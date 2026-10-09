"""Join local evidence for display. No model, remote API, or trade calls."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from invest_agent.data.sync import load_sync_config, resolve_sync_funds
from invest_agent.decision.registry import load_strategy_registry, validate_strategy_registry

TZ = ZoneInfo("Asia/Shanghai")
FUND_TYPES = {
    "domestic_broad_core": "沪深300", "domestic_growth": "科创100",
    "defensive": "政策性金融债", "sh_hk_sz_passive_technology_satellite": "沪港深科技龙头",
    "us_broad_core": "标普500", "uk_broad_core": "富时100", "us_growth": "美国主动成长",
    "us_nasdaq_core": "纳斯达克100", "japan_broad_core": "日经225",
    "global_technology_satellite": "全球科技互联网主动", "biotechnology_satellite": "纳斯达克生物科技",
    "legacy_active_other": "存量主动基金", "legacy_theme_other": "存量主题基金",
    "us_broad_sp500": "标普500", "us_broad_sp500_equal_weight": "标普500等权",
    "us_large_sp100_equal_weight": "标普100等权", "us_growth_active": "美国主动成长",
    "us_nasdaq_style_active": "纳指风格主动", "global_growth_nasdaq_style_active": "全球行业主动",
    "us_information_technology": "标普信息科技", "global_technology_internet_active": "全球科技互联网主动",
    "uk_broad_ftse100": "富时100", "europe_equity_active": "欧洲主动",
    "germany_broad_dax": "德国DAX", "japan_broad_nikkei225": "日经225",
    "japan_equity_active": "日本主动增强", "global_equity_active": "全球主动",
    "gold_precious_metals": "全球黄金主题", "crude_oil": "原油",
    "global_natural_resources": "全球自然资源主动", "us_biotechnology": "纳斯达克生物科技",
    "us_biotechnology_alternative": "纳斯达克生物科技", "global_healthcare": "标普全球1200医疗保健",
}
CORE_SLEEVES = {
    "domestic_broad_core", "domestic_growth", "us_broad_core", "uk_broad_core", "us_growth",
    "us_nasdaq_core", "japan_broad_core", "us_broad_sp500", "us_broad_sp500_equal_weight",
    "us_large_sp100_equal_weight", "us_growth_active", "us_nasdaq_style_active",
    "global_growth_nasdaq_style_active", "europe_equity_active", "germany_broad_dax",
    "japan_broad_nikkei225", "japan_equity_active", "global_equity_active",
}
DEFENSIVE_SLEEVES = {"defensive", "gold_stabilizer", "dividend_stabilizer"}


def position_bucket(sleeve: object) -> str:
    value = str(sleeve)
    if value in CORE_SLEEVES:
        return "核心仓"
    if value in DEFENSIVE_SLEEVES:
        return "防御仓"
    return "卫星仓"


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

    def latest_fund_income(self, portfolios):
        if not portfolios:
            return None
        snapshot = portfolios[0]
        rows = [row for row in snapshot.get("positions", []) if row.get("latest_income") is not None]
        dates = sorted({row["income_date"] for row in rows if row.get("income_date")})
        if rows and len(rows) == len(snapshot.get("positions", [])) and len(dates) == 1:
            return {"value": sum(float(row["latest_income"]) for row in rows),
                    "position_count": len(rows), "total_position_count": len(rows),
                    "income_date": dates[0], "date_status": "reported", "source": "channel",
                    "snapshot_as_of": snapshot.get("as_of"), "excludes_cash": True}
        if len(portfolios) >= 2:
            previous = portfolios[1]
            def signature(value):
                return sorted((row["fund_code"], str(row["shares"]), row.get("status", "confirmed"))
                              for row in value.get("positions", []))
            if signature(snapshot) == signature(previous):
                change = Decimal(str(snapshot["total_fund_value"])) - Decimal(str(previous["total_fund_value"]))
                return {"value": float(change), "position_count": len(snapshot.get("positions", [])),
                        "total_position_count": len(snapshot.get("positions", [])),
                        "income_date": str(snapshot["as_of"])[:10], "date_status": "derived",
                        "source": "snapshot_delta", "previous_snapshot_as_of": previous.get("as_of"),
                        "snapshot_as_of": snapshot.get("as_of"), "excludes_cash": True}
        if rows:
            return {"value": sum(float(row["latest_income"]) for row in rows),
                    "position_count": len(rows), "total_position_count": len(snapshot.get("positions", [])),
                    "income_date": None, "date_status": "mixed" if dates else "unreported",
                    "source": "channel_undated", "snapshot_as_of": snapshot.get("as_of"),
                    "excludes_cash": True}
        return None

    def funds(self, now: datetime):
        config = load_sync_config(self.root / "config/fund_data_sync_v1.json")
        universe = resolve_sync_funds(config, workspace_root=self.root)
        pool = self.json("config/qdii_purchase_route_pool_v1.json", {})
        research = self.json("config/global_qdii_research_pool_v1.json", {})
        monthly = self.json("config/monthly_decision_pack_v1.json", {})
        catalog = self.json("config/fund_watch_catalog_v1.json", {})
        names, registry_types, fund_types, buckets, tags = {}, {}, {}, {}, {}
        type_sources, bucket_sources = {}, {}
        for row in self.rows("SELECT fund_code,fund_name,fund_type FROM fund_metadata_observations ORDER BY source_observed_at"):
            names[row["fund_code"]] = row["fund_name"]
            registry_types[row["fund_code"]] = row["fund_type"]
        for row in research.get("funds", []):
            code = row["fund_code"]
            names[code] = row.get("fund_name")
            fund_types[code] = FUND_TYPES.get(row.get("sleeve"), str(row.get("sleeve", "待分类")))
            buckets[code] = position_bucket(row.get("sleeve"))
            type_sources[code] = bucket_sources[code] = "全球研究池"
            tags[code] = row.get("tags", [])
        routes = {r["fund_code"]: r for r in pool.get("purchase_candidates", [])}
        for code, row in routes.items():
            names[code] = row.get("fund_name")
            fund_types.setdefault(code, FUND_TYPES.get(row.get("sleeve"), str(row.get("sleeve", "待分类"))))
            buckets.setdefault(code, position_bucket(row.get("sleeve")))
            type_sources.setdefault(code, "购买路由池")
            bucket_sources.setdefault(code, "购买路由池")
        for code, sleeve in monthly.get("current_position_role_mapping", {}).items():
            buckets[code] = position_bucket(sleeve)
            bucket_sources[code] = "已核验持仓归属"
        for sleeve, code in monthly.get("target_routes", {}).items():
            fund_types.setdefault(code, FUND_TYPES.get(sleeve, sleeve))
            buckets[code] = position_bucket(sleeve)
            type_sources.setdefault(code, "631 目标配置")
            bucket_sources[code] = "631 目标配置"
        for row in catalog.get("classifications", []):
            code = row["fund_code"]
            names[code] = row["fund_name"]
            fund_types[code] = row["fund_type"]
            buckets[code] = row["position_bucket"]
            type_sources[code] = bucket_sources[code] = "监控基金目录"
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
                "fund_type": fund_types.get(code, registry_types.get(code) or "待分类"),
                "fund_type_source": type_sources.get(code),
                "position_bucket": buckets.get(code, "待归属"),
                "position_bucket_source": bucket_sources.get(code),
                "registry_type": registry_types.get(code) or ("QDII" if "QDII" in tags.get(code, []) else "未录入"),
                "tags": tags.get(code, []), "held": "current_portfolio_position" in fund.reasons,
                "target_route": code in set(monthly.get("target_routes", {}).values()),
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
              AND b.quality_status IN ('pass','partial')) WHERE rn=1 ORDER BY nav_date""", (code,))

    def backtests(self):
        catalog = self.json("config/web_backtest_catalog_v1.json", {"results": []})
        results = {}
        for entry in catalog.get("results", []):
            path = self.root / entry.get("report_path", "")
            if not path.is_file():
                continue
            report = json.loads(path.read_text())
            profiles = report.get("profile_results") or []
            if not profiles or not isinstance(profiles[0].get("metrics"), dict):
                continue
            metrics = profiles[0]["metrics"]
            key = (entry.get("strategy_id"), entry.get("strategy_version"))
            results[key] = {"label": entry.get("label"), "scenario_id": report.get("scenario_id"),
                "mode": report.get("mode"), "classification": report.get("classification"),
                "gate": report.get("official_rule_gate"), "profile_id": profiles[0].get("allocation_profile_id"),
                "metrics": {name: metrics.get(name) for name in ("start_date", "end_date",
                    "time_weighted_return_pct", "xirr_pct", "annualized_return_pct",
                    "annualized_volatility_pct", "maximum_drawdown_pct", "contributions_cny",
                    "final_value_cny", "purchase_fees_cny")}, "report_path": entry.get("report_path")}
        return results

    def strategies(self):
        registry = load_strategy_registry(self.root / "strategies/registry.json")
        try:
            validate_strategy_registry(registry, workspace_root=self.root)
            registry_valid = True
        except ValueError:
            registry_valid = False
        registrations = {(r["strategy_id"], r["strategy_version"]): r for r in registry["strategies"]}
        backtests = self.backtests()
        items = []
        for path in sorted((self.root / "strategies/specs").glob("*.json")):
            spec = json.loads(path.read_text())
            key = (spec.get("strategy_id"), spec.get("strategy_version"))
            registration = registrations.get(key)
            items.append({"id": path.stem, "name": STRATEGIES.get(key[0], key[0]),
                          "strategy_id": key[0], "version": key[1],
                          "purpose": spec.get("purpose", ""), "registered": registration is not None,
                          "is_benchmark": bool(registration and registration.get("decision_permissions", {}).get("target_allocation_authority")),
                          "mode": spec.get("mode"), "status": spec.get("status"), "backtest": backtests.get(key),
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
        portfolios = self.portfolios()
        return {"generated_at": now.isoformat(), "database_available": self.database.is_file(),
                "funds": self.funds(now), "portfolios": portfolios,
                "latest_fund_income": self.latest_fund_income(portfolios),
                "strategies": self.strategies(), "maintenance": self.maintenance(),
                "local_only": True, "readonly": True}
