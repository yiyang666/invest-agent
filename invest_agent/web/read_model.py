"""Join local evidence for display. No model, remote API, or trade calls."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from invest_agent.data.sync import load_sync_config, resolve_sync_funds
from invest_agent.decision.registry import load_strategy_registry, validate_strategy_registry

TZ = ZoneInfo("Asia/Shanghai")
SLEEVES = {
    "domestic_broad_core": "国内宽基", "domestic_growth": "国内成长",
    "defensive": "防御资产", "sh_hk_sz_passive_technology_satellite": "科技卫星",
    "us_broad_core": "美国宽基", "uk_broad_core": "英国宽基", "us_growth": "美国成长",
    "us_nasdaq_core": "纳斯达克", "japan_broad_core": "日本宽基",
    "global_technology_satellite": "全球科技", "biotechnology_satellite": "生物科技",
    "legacy_active_other": "存量主动基金", "legacy_theme_other": "存量主题基金",
    "us_broad_sp500": "美国宽基", "us_broad_sp500_equal_weight": "美国等权宽基",
    "us_large_sp100_equal_weight": "美国大盘等权", "us_growth_active": "美国成长",
    "us_nasdaq_style_active": "美国成长", "global_growth_nasdaq_style_active": "全球成长",
    "us_information_technology": "美国信息科技", "global_technology_internet_active": "全球互联网",
    "uk_broad_ftse100": "英国宽基", "europe_equity_active": "欧洲股票",
    "germany_broad_dax": "德国宽基", "japan_broad_nikkei225": "日本宽基",
    "japan_equity_active": "日本股票", "global_equity_active": "全球股票",
    "gold_precious_metals": "黄金贵金属", "crude_oil": "原油",
    "global_natural_resources": "自然资源", "us_biotechnology": "生物科技",
    "us_biotechnology_alternative": "生物科技", "global_healthcare": "全球医疗",
}
STRATEGIES = {
    "dca_baseline": "631 长期定投", "new_money_trend_rs": "趋势与相对强弱",
    "drawdown_budget_add": "回撤预算加仓", "sleeve_drawdown_recovery": "袖套回撤修复",
    "hierarchical_risk_budget_valuation": "分层风险预算与估值",
}
JOB_NAMES = {
    "portfolio_daily": "每日持仓快照", "fund_data_daily": "基金净值",
    "market_daily_series": "每日市场背景", "market_weekly_context": "每周估值与拥挤度",
    "market_monthly_context": "每月宏观与权重", "market_quarterly_context": "每季宏观",
    "market_context_bootstrap": "历史数据初始化",
}


class Dashboard:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.database = self.root / "data/private/invest_agent.sqlite3"

    def json(self, name: str, default=None):
        path = self.root / name
        return json.loads(path.read_text()) if path.is_file() else default

    def rows(self, sql: str, params=()):
        if not self.database.is_file():
            return []
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                return [dict(row) for row in connection.execute(sql, params)]
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return []
                raise

    def portfolios(self):
        records = self.rows("SELECT snapshot_id, captured_at, payload_json FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 400")
        days = {}
        for row in records:
            day = row["captured_at"][:10]
            if day not in days:
                payload = json.loads(row["payload_json"])
                days[day] = {"id": row["snapshot_id"], **payload}
        return list(days.values())

    def funds(self, now: datetime):
        config = load_sync_config(self.root / "config/fund_data_sync_v1.json")
        universe = resolve_sync_funds(config, workspace_root=self.root)
        pool = self.json("config/qdii_purchase_route_pool_v1.json", {})
        research = self.json("config/global_qdii_research_pool_v1.json", {})
        monthly = self.json("config/monthly_decision_pack_v1.json", {})
        catalog = self.json("config/fund_watch_catalog_v1.json", {})
        names, types, labels, tags, label_sources = {}, {}, {}, {}, {}
        for row in self.rows("SELECT fund_code,fund_name,fund_type FROM fund_metadata_observations ORDER BY source_observed_at"):
            names[row["fund_code"]] = row["fund_name"]
            types[row["fund_code"]] = row["fund_type"]
        for row in catalog.get("classifications", []):
            code = row["fund_code"]
            names[code] = row["fund_name"]
            labels[code] = row["category"]
            label_sources[code] = "监控基金目录"
        for row in research.get("funds", []):
            code = row["fund_code"]
            names[code] = row.get("fund_name")
            labels[code] = SLEEVES.get(row.get("sleeve"), row.get("sleeve", "待分类"))
            label_sources[code] = "全球研究池"
            tags[code] = row.get("tags", [])
        routes = {r["fund_code"]: r for r in pool.get("purchase_candidates", [])}
        for code, row in routes.items():
            names[code] = row.get("fund_name")
            labels[code] = SLEEVES.get(row.get("sleeve"), row.get("sleeve", "待分类"))
            label_sources[code] = "购买路由池"
        for code, sleeve in monthly.get("current_position_role_mapping", {}).items():
            labels[code] = SLEEVES.get(sleeve, sleeve)
            label_sources[code] = "已核验持仓分类"
        for sleeve, code in monthly.get("target_routes", {}).items():
            labels[code] = SLEEVES.get(sleeve, sleeve)
            label_sources[code] = "631 目标配置"
        for snap in reversed(self.portfolios()):
            for row in snap["positions"]:
                if row.get("fund_name"):
                    names[row["fund_code"]] = row["fund_name"]
        # Names from reviewed product snapshots; this does not create a classification.
        def collect_names(value):
            if isinstance(value, dict):
                if value.get("fund_code") and value.get("fund_name"):
                    names.setdefault(value["fund_code"], value["fund_name"])
                for child in value.values():
                    collect_names(child)
            elif isinstance(value, list):
                for child in value:
                    collect_names(child)
        collect_names(self.json("config/aijijin_research_route_snapshot_v1.json", {}))
        latest = {r["fund_code"]: r for r in self.rows("""
            SELECT * FROM (SELECT n.fund_code,n.nav_date,n.unit_nav,n.source_observed_at,
              b.fetched_at,b.quality_status, ROW_NUMBER() OVER (
              PARTITION BY n.fund_code ORDER BY n.nav_date DESC,b.fetched_at DESC,n.batch_id DESC) AS rn
            FROM fund_nav_observations n JOIN data_batches b USING(batch_id)
            WHERE n.provider_id='akshare_eastmoney' AND b.quality_status IN ('pass','partial')) WHERE rn=1""")}
        funds = []
        for fund in universe:
            code = fund.fund_code
            nav = latest.get(code, {})
            nav_date = nav.get("nav_date")
            lag = (now.date() - datetime.fromisoformat(nav_date).date()).days if nav_date else None
            funds.append({
                "code": code, "name": names.get(code) or f"基金 {code}",
                "category": labels.get(code, "待分类"), "category_source": label_sources.get(code),
                "fund_type": types.get(code) or ("QDII" if "QDII" in tags.get(code, []) else "未录入"),
                "tags": tags.get(code, []), "held": "current_portfolio_position" in fund.reasons,
                "role": "目标配置" if label_sources.get(code) == "631 目标配置" else "研究观察",
                "nav": nav.get("unit_nav"), "nav_date": nav_date,
                "collected_at": nav.get("fetched_at"), "lag_days": lag,
                "freshness": "missing" if lag is None else "fresh" if 0 <= lag <= config["collection"]["maximum_nav_lag_calendar_days"] else "stale",
                "source": "东方财富 / 本地校验仓", "quality": nav.get("quality_status"),
                "route_as_of": routes.get(code, {}).get("source_checked_at"),
                "reasons": list(fund.reasons),
            })
        return funds

    def series(self, code: str):
        return self.rows("""SELECT nav_date,unit_nav FROM (
            SELECT n.nav_date,n.unit_nav, ROW_NUMBER() OVER (
              PARTITION BY n.nav_date ORDER BY b.fetched_at DESC,n.batch_id DESC) AS rn
            FROM fund_nav_observations n JOIN data_batches b USING(batch_id)
            WHERE n.fund_code=? AND n.provider_id='akshare_eastmoney'
              AND b.quality_status IN ('pass','partial')) WHERE rn=1 ORDER BY nav_date DESC LIMIT 180""", (code,))[::-1]

    def strategies(self):
        registry = load_strategy_registry(self.root / "strategies/registry.json")
        try:
            validate_strategy_registry(registry, workspace_root=self.root)
            registry_valid = True
        except ValueError:
            registry_valid = False
        registrations = {(r["strategy_id"], r["strategy_version"]): r for r in registry["strategies"]}
        items = []
        for path in sorted((self.root / "strategies/specs").glob("*.json")):
            spec = json.loads(path.read_text())
            key = (spec.get("strategy_id"), spec.get("strategy_version"))
            registration = registrations.get(key)
            items.append({"id": path.stem, "name": STRATEGIES.get(key[0], key[0]),
                          "strategy_id": key[0], "version": key[1],
                          "purpose": spec.get("purpose", ""), "registered": registration is not None,
                          "is_benchmark": bool(registration and registration.get("decision_permissions", {}).get("target_allocation_authority")),
                          "mode": spec.get("mode"), "status": spec.get("status"),
                          "registration": registration, "spec": spec,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        items.sort(key=lambda x: (not x["is_benchmark"], not x["registered"], x["name"], x["version"] or ""))
        return {"items": items, "registry_valid": registry_valid,
                "execution_status": "未登记实盘执行策略", "benchmark": "dca_baseline@1.5.0"}

    def maintenance(self):
        state = self.json("data/private/automation/data-maintenance-state.json", {})
        jobs = [{"id": key, "name": JOB_NAMES.get(key, key), **value}
                for key, value in state.get("jobs", {}).items()]
        if not any(j["id"] == "portfolio_daily" for j in jobs):
            jobs.insert(0, {"id": "portfolio_daily", "name": "每日持仓快照", "last_status": "not_run"})
        reports = []
        for path in sorted((self.root / "data/private/automation").glob("data-maintenance-2*.json"), reverse=True)[:14]:
            data = json.loads(path.read_text())
            report = {k: data.get(k) for k in ("run_at", "status", "due_job_count", "completed_job_count", "failed_job_count")}
            report["jobs"] = []
            for job in data.get("jobs", []):
                row = {"name": JOB_NAMES.get(job["job_id"], job["job_id"]), "status": job["status"]}
                for result in job.get("results", []):
                    summary = result.get("result", {})
                    if isinstance(summary, dict) and isinstance(summary.get("summary"), dict):
                        row["summary"] = {k: summary["summary"].get(k) for k in ("fund_count", "published", "rejected", "errors", "stale")}
                report["jobs"].append(row)
            reports.append(report)
        return {"jobs": jobs, "reports": reports}

    def payload(self):
        now = datetime.now(TZ)
        return {"generated_at": now.isoformat(), "database_available": self.database.is_file(),
                "funds": self.funds(now), "portfolios": self.portfolios(),
                "strategies": self.strategies(), "maintenance": self.maintenance(),
                "local_only": True, "readonly": True}
