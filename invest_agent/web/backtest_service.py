"""Offline experiment adapter. Never imports execution or channel adapters."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
import threading
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from invest_agent.backtest.local_research import run_local_dca_research
from invest_agent.metrics.fund import load_nav_series

PROVIDER = "akshare_eastmoney"
SPEC = {"strategy_id": "web_dca_experiment", "strategy_version": "1.0.0",
        "mode": "research_only", "selling_allowed": False}


def number(value, label, lower, upper):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是数字") from None
    if not result.is_finite() or not lower <= result <= upper:
        raise ValueError(f"{label}须在 {lower}–{upper} 之间")
    return result


class BacktestService:
    def __init__(self, root):
        self.root = Path(root)
        self.database = self.root / "data/private/invest_agent.sqlite3"
        self.experiments = self.root / "data/private/web_backtests.sqlite3"
        self.pending = OrderedDict()
        self.lock = threading.Lock()

    def catalog(self):
        if not self.database.exists():
            return []
        names = {}
        for path, key in [("config/fund_watch_catalog_v1.json", "classifications"),
                          ("config/global_qdii_research_pool_v1.json", "funds"),
                          ("config/qdii_purchase_route_pool_v1.json", "purchase_candidates")]:
            file = self.root / path
            if file.exists():
                for item in json.loads(file.read_text()).get(key, []):
                    names[item["fund_code"]] = item.get("fund_name", item.get("name", item["fund_code"]))
        with closing(sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)) as con:
            codes = [r[0] for r in con.execute(
                "SELECT DISTINCT fund_code FROM fund_nav_observations WHERE provider_id=?", (PROVIDER,))]
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='fund_metadata_observations'").fetchone():
                names.update(con.execute("SELECT fund_code,fund_name FROM fund_metadata_observations ORDER BY source_observed_at").fetchall())
        items = []
        for code in sorted(codes):
            try:
                points = load_nav_series(self.database, fund_code=code, provider_id=PROVIDER,
                                         nav_field="unit_nav", as_of=date.today())
            except ValueError:
                continue
            items.append({"code": code, "name": names.get(code) or code,
                          "start_date": points[0].nav_date.isoformat(),
                          "end_date": points[-1].nav_date.isoformat(), "observations": len(points)})
        return items

    def validate(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("实验配置须为对象")
        try:
            start, end = date.fromisoformat(payload["start_date"]), date.fromisoformat(payload["end_date"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("请填写有效起止日期") from None
        if not start < end <= date.today() or (end-start).days > 3660:
            raise ValueError("起止日期须先后有序、不得晚于今日，最长十年")
        monthly = number(payload.get("monthly_amount"), "每月投入", Decimal("0.01"), Decimal("1000000"))
        if monthly != monthly.quantize(Decimal("0.01")):
            raise ValueError("每月投入最多两位小数")
        day = payload.get("calendar_day")
        if type(day) is not int or not 1 <= day <= 28:
            raise ValueError("定投日期须为每月 1–28 日")
        mode = payload.get("rebalance")
        if mode not in {"fixed_weights", "revalidated_target_gap"}:
            raise ValueError("请选择支持的新增资金分配方式")
        funds = payload.get("funds")
        if not isinstance(funds, list) or not 1 <= len(funds) <= 8:
            raise ValueError("请选择 1–8 只基金")
        catalog = {f["code"]: f for f in self.catalog()}
        clean, seen = [], set()
        for item in funds:
            if not isinstance(item, dict):
                raise ValueError("基金配置无效")
            code = item.get("code")
            if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or code not in catalog:
                raise ValueError("基金不存在或没有通过校验的净值历史")
            if code in seen:
                raise ValueError("不能重复添加同一基金")
            seen.add(code)
            f = catalog[code]
            if start.isoformat() < f["start_date"] or end.isoformat() > f["end_date"]:
                raise ValueError(f"{code} 可用历史为 {f['start_date']} 至 {f['end_date']}，请调整日期")
            weight = number(item.get("weight"), "目标比例", Decimal("0.01"), Decimal("100"))
            fee = number(item.get("fee_pct"), "申购费率", Decimal("0"), Decimal("10"))
            if weight != weight.quantize(Decimal("0.01")) or fee != fee.quantize(Decimal("0.0001")):
                raise ValueError("比例最多两位小数，费率最多四位小数")
            clean.append({"code": code, "weight": str(weight), "fee_pct": str(fee)})
        if sum((Decimal(f["weight"]) for f in clean), Decimal(0)) != 100:
            raise ValueError("目标比例合计必须为 100%")
        return {"start_date": start.isoformat(), "end_date": end.isoformat(),
                "monthly_amount": str(monthly), "calendar_day": day, "rebalance": mode, "funds": clean}

    def run(self, payload):
        config = self.validate(payload)
        scenario = {"schema_version": 1, "scenario_id": "web-dca-v1",
                    "classification": "counterfactual_research_assumption", "provider_id": PROVIDER,
                    "period": {"start_date": config["start_date"], "end_date": config["end_date"]},
                    "monthly_contribution_cny": config["monthly_amount"], "calendar_day": config["calendar_day"],
                    "allocation_engine": {"mode": config["rebalance"]},
                    "allocation_profiles": [{"id": "experiment", "weights": {
                        f["code"]: str(Decimal(f["weight"])/100) for f in config["funds"]}}],
                    "routes": [{"sleeve": f["code"], "fund_code": f["code"], "minimum_order_cny": "1",
                                "daily_cap_cny": None, "purchase_fee_rate": str(Decimal(f["fee_pct"])/100),
                                "share_precision": 2, "share_rounding_mode": "down",
                                "rule_version": "web_research_v1", "rule_effective_date": config["start_date"],
                                "assumption_authority": "counterfactual_research_assumption"} for f in config["funds"]],
                    "nav_assumption": {"field": "unit_nav", "visibility_rule": "next_fund_nav_date", "forward_fill": False},
                    "subscription_assumption": {"precommitted_before_cutoff": True, "confirmation_rule": "nav_visible_date"},
                    "distribution_assumption": {"election": "cash", "payment_date_rule": "next_fund_nav_date",
                                                "cash_precision": 2, "cash_rounding_mode": "half_up"},
                    "official_rule_gate": {"status": "blocked", "reason": "Web 研究假设，未核验历史交易规则"}}
        # SQLite backup gives one consistent revision even if nightly maintenance publishes during a run.
        with TemporaryDirectory(prefix="invest-web-backtest-") as directory:
            snapshot = Path(directory) / "snapshot.sqlite3"
            with closing(sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)) as source, \
                    closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination)
            snapshot.chmod(0o600)
            result = run_local_dca_research(database=snapshot, scenario=scenario,
                                           strategy_spec=SPEC, strategy_spec_bytes=json.dumps(SPEC, sort_keys=True).encode())
        profile = result["profile_results"][0]
        metrics = profile["metrics"]
        # Do not compare end assets with contributions occurring after the final complete valuation.
        if any(r["planned_date"] > metrics["end_date"] for r in profile["monthly_reconciliation"]):
            raise ValueError("期末完整估值早于最后一次投入，请提前结束日期或更换基金组合")
        run_id = uuid4().hex
        experiment = {"id": run_id, "name": "未保存实验", "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                      "config": config, "scenario": scenario, "result": result}
        with self.lock:
            self.pending[run_id] = experiment
            while len(self.pending) > 16:
                self.pending.popitem(last=False)
        return experiment

    def save(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            raise ValueError("请填写实验名称")
        name = payload["name"].strip()
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError("实验名称须为 1–80 个字符")
        with self.lock:
            run_id = payload.get("run_id")
            if not isinstance(run_id, str):
                raise ValueError("实验编号无效")
            experiment = self.pending.get(run_id) or self.history(run_id)
            if experiment is None:
                raise ValueError("未找到本次运行结果；服务重启或过期后请重跑")
            saved = {**experiment, "name": name}
            self.experiments.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.experiments)) as con, con:
                con.execute("CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                con.execute("INSERT INTO experiments VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                            (saved["id"], json.dumps(saved, ensure_ascii=False, allow_nan=False)))
            self.experiments.chmod(0o600)
        return saved

    def history(self, experiment_id=None):
        if experiment_id is not None and not re.fullmatch(r"[a-f0-9]{32}", experiment_id):
            raise ValueError("实验编号无效")
        if not self.experiments.exists():
            return None if experiment_id else []
        with closing(sqlite3.connect(f"file:{self.experiments}?mode=ro", uri=True)) as con:
            if experiment_id:
                row = con.execute("SELECT payload FROM experiments WHERE id=?", (experiment_id,)).fetchone()
                return json.loads(row[0]) if row else None
            rows = con.execute("SELECT payload FROM experiments ORDER BY rowid DESC").fetchall()
        return [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "config": r["config"],
                 "metrics": {k: v for k, v in r["result"]["profile_results"][0]["metrics"].items()
                             if k != "unitized_path"}} for r in map(lambda row: json.loads(row[0]), rows)]

    def delete(self, experiment_id):
        if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", experiment_id):
            raise ValueError("实验编号无效")
        if not self.experiments.exists():
            raise ValueError("未找到这个历史实验")
        with self.lock:
            with closing(sqlite3.connect(self.experiments)) as con, con:
                deleted = con.execute(
                    "DELETE FROM experiments WHERE id = ?", (experiment_id,)
                ).rowcount
            self.pending.pop(experiment_id, None)
        if deleted != 1:
            raise ValueError("未找到这个历史实验")
        return {"status": "deleted", "id": experiment_id}
