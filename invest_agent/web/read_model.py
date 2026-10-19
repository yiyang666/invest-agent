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
from invest_agent.metrics.rsi import simple_rsi

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
    "dividend_stabilizer", "dividend_low_volatility_stabilizer",
}
DEFENSIVE_SLEEVES = {"defensive", "gold_stabilizer", "gold_precious_metals", "bond_defensive"}


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
MARKET_INDEX_SPECS = (
    ("000001", "上证指数", "中国", ("sse",)),
    ("399006", "创业板指", "中国", ("cnindex",)),
    ("000680", "科创综指", "中国", ("sse",)),
    ("000300", "沪深300", "中国", ("jin10",)),
    ("HSI", "恒生指数", "中国香港", ("jin10",)),
    ("SPX", "标普500", "美国", ("jin10",)),
    ("IXIC", "纳斯达克综合指数", "美国", ("fred",)),
    ("NDX", "纳斯达克100", "美国", ("fred",)),
    ("FTSE", "富时100", "英国", ("jin10",)),
    ("GDAXI", "德国DAX", "德国", ("jin10",)),
    ("N225", "日经225", "日本", ("jin10",)),
)
MARKET_SOURCE_LABELS = {
    "jin10": "金十 / 本地校验仓", "cnindex": "国证指数 / 本地校验仓",
    "sse": "上交所 / 本地校验仓", "fred": "Nasdaq 经 FRED / 本地校验仓",
}


class Dashboard:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.database = self.root / "data/private/invest_agent.sqlite3"

    def json(self, name: str, default=None):
        path = self.root / name
        return json.loads(path.read_text()) if path.is_file() else default

    def source_revision(self) -> str:
        """Return a cheap version for every local input used by the dashboard."""
        paths = [
            self.database,
            Path(str(self.database) + "-wal"),
            self.root / "data/private/web_core_dca.sqlite3",
            Path(__file__),
        ]
        paths.extend((self.root / "config").glob("*.json"))
        paths.append(self.root / "strategies/registry.json")
        paths.extend((self.root / "strategies/specs").glob("*.json"))
        paths.extend((self.root / "data/private/automation").glob("*.json"))
        paths.extend((self.root / "data/private/reports").glob("*.json"))
        digest = hashlib.sha256()
        for path in sorted(set(paths), key=lambda value: str(value)):
            try:
                stat = path.stat()
                rel = path.relative_to(self.root) if path.is_relative_to(self.root) else path.name
                marker = f"{rel}:{stat.st_mtime_ns}:{stat.st_size}\n"
            except FileNotFoundError:
                rel = path.relative_to(self.root) if path.is_relative_to(self.root) else path.name
                marker = f"{rel}:missing\n"
            digest.update(marker.encode())
        return digest.hexdigest()[:16]

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
        # 只汇总渠道快照里的逐基金收益；禁止用相邻快照市值差推算
        if not portfolios:
            return None
        snapshot = portfolios[0]
        rows = [row for row in snapshot.get("positions", []) if row.get("latest_income") is not None]
        if not rows:
            return None
        dates = sorted({row["income_date"] for row in rows if row.get("income_date")})
        return {"value": sum(float(row["latest_income"]) for row in rows),
                "position_count": len(rows), "total_position_count": len(snapshot.get("positions", [])),
                "income_date": dates[0] if len(dates) == 1 else None,
                "date_status": "reported" if len(dates) == 1 else "mixed" if dates else "unreported",
                "source": "channel", "snapshot_as_of": snapshot.get("as_of"), "excludes_cash": True}

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
        # 展示和 RSI 使用累计净值，避免分红把单位净值整体下移造成假信号。
        rows = self.rows("""SELECT nav_date,unit_nav,accumulated_nav FROM (
            SELECT n.nav_date,n.unit_nav,n.accumulated_nav, ROW_NUMBER() OVER (
              PARTITION BY n.nav_date ORDER BY b.fetched_at DESC,n.batch_id DESC) AS rn
            FROM fund_nav_observations n JOIN data_batches b USING(batch_id)
            WHERE n.fund_code=? AND n.provider_id='akshare_eastmoney'
              AND b.quality_status IN ('pass','partial')) WHERE rn=1 ORDER BY nav_date""", (code,))
        nav_values = [float(row.get("accumulated_nav") or row["unit_nav"]) for row in rows]
        rsi_values = simple_rsi(nav_values, period=13)
        for row, nav_value, rsi in zip(rows, nav_values, rsi_values):
            row["display_nav"] = nav_value
            row["rsi_13"] = None if rsi is None else round(rsi, 4)
        return rows

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

    def market_indices(self):
        rows = self.rows("""
            WITH revisions AS (
              SELECT o.series_id,o.observation_date,o.value,o.unit,o.first_seen_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY o.series_id,o.observation_date
                       ORDER BY o.first_seen_at DESC,o.batch_id DESC
                     ) AS version_rank
              FROM market_numeric_observations o
              JOIN market_data_batches b USING(batch_id)
              WHERE (o.series_id LIKE 'jin10_quote:%' OR o.series_id LIKE 'index_quote:%')
                AND b.quality_status IN ('pass','partial')
            ), ranked AS (
              SELECT series_id,observation_date,value,unit,first_seen_at,
                     ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY observation_date DESC) AS rn
              FROM revisions WHERE version_rank=1
            )
            SELECT series_id,observation_date,value,unit,first_seen_at,rn
            FROM ranked WHERE rn<=2 ORDER BY series_id,rn
        """)
        latest = {}
        for row in rows:
            latest.setdefault(row["series_id"], []).append(row)
        result = []
        for code, label, region, sources in MARKET_INDEX_SPECS:
            candidates = []
            for source in sources:
                prefix = f"jin10_quote:{code}" if source == "jin10" else f"index_quote:{source}:{code}"
                closes = latest.get(f"{prefix}:close", [])
                if not closes:
                    continue
                close = closes[0]
                changes = latest.get(f"{prefix}:change_pct", [])
                if changes and changes[0]["observation_date"] == close["observation_date"]:
                    value = float(close["value"])
                    pct = float(changes[0]["value"])
                    previous = value / (1 + pct / 100) if pct != -100 else None
                    change = None if previous is None else value - previous
                elif source == "fred" and len(closes) > 1:
                    value = float(close["value"])
                    previous = float(closes[1]["value"])
                    if previous <= 0:
                        continue
                    change, pct = value - previous, (value / previous - 1) * 100
                else:
                    continue
                candidates.append({
                    "code": code, "label": label, "region": region,
                    "value": value, "change": change, "change_pct": pct,
                    "observation_date": close["observation_date"],
                    "collected_at": close["first_seen_at"],
                    "source": MARKET_SOURCE_LABELS[source],
                })
            if candidates:
                result.append(max(candidates, key=lambda item: item["observation_date"]))
        return result

    def satellite_industries(self):
        """Show the 31-industry radar, candidate routes, and deterministic 6211 signal states."""
        config = self.json("config/satellite_industry_watchlist_v1.json", {})
        industries = config.get("industries", [])
        rows = self.rows("""
            WITH ranked AS (
              SELECT o.series_id, o.observation_date, o.value, o.first_seen_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY o.series_id
                       ORDER BY o.observation_date DESC, o.first_seen_at DESC, o.batch_id DESC
                     ) AS rn
              FROM market_numeric_observations o
              JOIN market_data_batches b USING(batch_id)
              WHERE (
                o.series_id LIKE 'industry_crowding:%'
                OR o.series_id LIKE 'index_valuation:%'
                OR o.series_id = 'market_series:bond_yield:CN_10Y'
              )
                AND b.quality_status IN ('pass', 'partial')
            )
            SELECT series_id, observation_date, value, first_seen_at
            FROM ranked WHERE rn = 1
        """) if industries else []
        latest = {row["series_id"]: row for row in rows}

        def eval_fund_signal(code: str) -> dict:
            pts = self.series(code)
            if len(pts) < 20:
                return {"fund_code": code, "status": "pending_bootstrap", "observations": len(pts)}
            window = pts[-60:] if len(pts) >= 60 else pts
            navs = [float(p["display_nav"]) for p in window]
            rsis = [float(p["rsi_13"]) for p in window if p.get("rsi_13") is not None]
            cur_nav = navs[-1]
            peak_60 = max(navs)
            peak_idx = navs.index(peak_60)
            post_peak_navs = navs[peak_idx:]
            trough_after_peak = min(post_peak_navs)
            all_navs = [float(p["display_nav"]) for p in pts]
            all_peak = max(all_navs)
            ma_200 = sum(all_navs[-200:]) / 200.0 if len(all_navs) >= 200 else sum(all_navs) / len(all_navs)
            above_ma200 = cur_nav >= ma_200
            dd_60 = (cur_nav / peak_60 - 1.0) * 100.0 if peak_60 > 0 else 0.0
            max_dd_60 = (trough_after_peak / peak_60 - 1.0) * 100.0 if peak_60 > 0 else 0.0
            total_dd = (cur_nav / all_peak - 1.0) * 100.0 if all_peak > 0 else 0.0
            rebound_60 = (cur_nav / trough_after_peak - 1.0) * 100.0 if trough_after_peak > 0 else 0.0
            post_peak_rsis = [
                float(p["rsi_13"]) for p in window[peak_idx:] if p.get("rsi_13") is not None
            ]
            cur_rsi = rsis[-1] if rsis else 50.0
            min_rsi = min(post_peak_rsis) if post_peak_rsis else cur_rsi

            # Composite Odds & Exhaustion Score (0..100)
            dd_ref = max(abs(max_dd_60), abs(total_dd) * 0.8) / 100.0
            dd_score = min(100.0, (dd_ref / 0.35) * 100.0)
            exh_score = min(100.0, max(0.0, (40.0 - min_rsi) / 35.0 * 100.0))
            reb_ratio = rebound_60 / 100.0
            if reb_ratio <= 0.12:
                rec_score = min(100.0, max(0.0, (reb_ratio / 0.10) * 100.0))
            else:
                rec_score = max(15.0, 100.0 - (reb_ratio - 0.12) * 500.0)
            odds_score = round(0.40 * dd_score + 0.35 * exh_score + 0.25 * rec_score, 1)

            trough_60_global = min(navs)
            runup_60 = (peak_60 / trough_60_global - 1.0) * 100.0 if trough_60_global > 0 else 0.0
            trailing_profit_lock = bool(runup_60 >= 18.0 and abs(dd_60) >= 3.0)

            is_trough = (abs(max_dd_60) >= 15.0 or abs(total_dd) >= 20.0) and (min_rsi <= 30.0)
            is_rebound = rebound_60 >= 3.0 and cur_rsi <= 70.0 and rebound_60 <= 18.0 and not trailing_profit_lock
            if is_trough and is_rebound:
                stage = "stage2_entry_confirmed"
            elif is_trough:
                stage = "stage1_trough_watch"
            elif trailing_profit_lock or cur_rsi >= 70.0:
                stage = "overbought_alert"
            else:
                stage = "neutral"
            return {
                "fund_code": code,
                "status": "ready",
                "observations": len(pts),
                "nav_date": pts[-1]["nav_date"],
                "nav": round(cur_nav, 4),
                "ma_200": round(ma_200, 4),
                "above_ma200": above_ma200,
                "rsi_13": round(cur_rsi, 1),
                "min_rsi_60d": round(min_rsi, 1),
                "dd_60d_pct": round(dd_60, 1),
                "total_dd_pct": round(total_dd, 1),
                "rebound_from_trough_pct": round(rebound_60, 1),
                "odds_score": odds_score,
                "trailing_profit_lock": trailing_profit_lock,
                "stage": stage,
            }

        fee_catalog = self.json("config/fund_fee_rules_v1.json", {})
        fee_by_code = {item["fund_code"]: item for item in fee_catalog.get("funds", [])}

        route_signals = {}
        for code in ("006932", "000216"):
            route_signals[code] = eval_fund_signal(code)
        for route in config.get("candidate_routes", []):
            code = route.get("fund_code")
            if code:
                route_signals[code] = eval_fund_signal(code)

        pe_300_raw = latest.get("index_valuation:000300:pe_ttm", {}).get("value")
        cn_10y_raw = latest.get("market_series:bond_yield:CN_10Y", {}).get("value")
        pe_300 = float(pe_300_raw) if pe_300_raw is not None else None
        cn_10y = float(cn_10y_raw) if cn_10y_raw is not None else None
        erp_pct = round(100.0 / pe_300 - cn_10y, 2) if (pe_300 and pe_300 > 0 and cn_10y is not None) else None

        return {
            "status": config.get("status", "unavailable"),
            "source": "股叉叉 / 本地校验仓",
            "focus_themes": [
                {key: theme[key] for key in ("id", "name", "industry_codes", "local_proxy_funds", "proxy_scope", "route_status")}
                for theme in config.get("focus_themes", [])
            ],
            "candidate_routes": [
                {
                    **{key: route[key] for key in ("fund_code", "fund_name", "theme", "evidence_status", "limitation")},
                    "signal": route_signals.get(route["fund_code"], {}),
                    "fee_rule": fee_by_code.get(route["fund_code"], {}),
                }
                for route in config.get("candidate_routes", [])
            ],
            "defensive_signals": {
                "bond_006932": {**route_signals.get("006932", {}), "fee_rule": fee_by_code.get("006932", {})},
                "gold_000216": {**route_signals.get("000216", {}), "fee_rule": fee_by_code.get("000216", {})},
            },
            "valuation_highlights": {
                "000300_pe_ttm": pe_300_raw,
                "cn_10y_yield": cn_10y_raw,
                "csi300_erp_pct": erp_pct,
                "399975_pe_pct": latest.get("index_valuation:399975:pe_pct", {}).get("value"),
                "399975_pb_pct": latest.get("index_valuation:399975:pb_pct", {}).get("value"),
                "000300_pb_pct": latest.get("index_valuation:000300:pb_pct", {}).get("value"),
                "399006_pe_pct": latest.get("index_valuation:399006:pe_pct", {}).get("value"),
                "H30184_pe_pct": latest.get("index_valuation:H30184:pe_pct", {}).get("value"),
            },
            "items": [
                {
                    "code": item["code"], "name": item["name"],
                    "exposure_group": item["exposure_group"],
                    "route_status": item["route_status"],
                    "proxy_funds": item.get("proxy_funds", []),
                    "crowding": latest.get(f"industry_crowding:{item['code']}:composite", {}).get("value"),
                    "momentum_pct": latest.get(f"industry_crowding:{item['code']}:momentum_pct", {}).get("value"),
                    "turnover_pct": latest.get(f"industry_crowding:{item['code']}:turnover_pct", {}).get("value"),
                    "ta_share_pct": latest.get(f"industry_crowding:{item['code']}:ta_share_pct", {}).get("value"),
                    "observation_date": latest.get(f"industry_crowding:{item['code']}:composite", {}).get("observation_date"),
                }
                for item in industries
            ],
        }

    def trade_reconciliation(self, portfolios, now: datetime):
        month_prefix = now.strftime("%Y-%m")
        latest_snap = portfolios[0] if portfolios else {}
        raw_tr = None
        for snap in portfolios:
            tr = snap.get("trade_reconciliation")
            if isinstance(tr, dict) and tr.get("month") == month_prefix:
                raw_tr = tr
                break
        if raw_tr is None and portfolios and isinstance(portfolios[0].get("trade_reconciliation"), dict):
            raw_tr = portfolios[0]["trade_reconciliation"]

        if isinstance(raw_tr, dict):
            return {
                "status": "connected",
                "snapshot_as_of": latest_snap.get("as_of"),
                "wallet_cash_cny": latest_snap.get("cash", "0.00"),
                **raw_tr,
            }
        return {
            "status": "pending_capture",
            "month": month_prefix,
            "snapshot_as_of": latest_snap.get("as_of"),
            "wallet_cash_cny": latest_snap.get("cash", "0.00"),
            "active_order_count": 0,
            "pending_order_count": 0,
            "confirmed_order_count": 0,
            "aip_cny": "0.00",
            "buy_cny": "0.00",
            "sell_cny": "0.00",
            "pending_cny": "0.00",
            "by_fund": [],
            "recent_orders": [],
        }

    def payload(self):
        now = datetime.now(TZ)
        portfolios = self.portfolios()
        return {"generated_at": now.isoformat(), "database_available": self.database.is_file(),
                "funds": self.funds(now), "portfolios": portfolios,
                "latest_fund_income": self.latest_fund_income(portfolios),
                "trade_reconciliation": self.trade_reconciliation(portfolios, now),
                "market_indices": self.market_indices(),
                "satellite_industries": self.satellite_industries(),
                "strategies": self.strategies(), "maintenance": self.maintenance(),
                "local_only": True, "readonly": True,
                "manual_market_refresh_enabled": True,
                "trading_write_disabled": True}
