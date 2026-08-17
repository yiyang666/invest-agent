"""Deterministic rolling-window orchestration for local DCA research."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Mapping

from .local_research import run_local_dca_research


ENGINE_VERSION = "local_dca_rolling_windows_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def load_rolling_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("rolling-window specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("rolling-window specification must use schema_version 1")
    if not str(spec.get("rolling_id", "")).strip():
        raise ValueError("rolling_id is required")
    first_start = date.fromisoformat(str(spec.get("first_start_date")))
    last_end = date.fromisoformat(str(spec.get("last_end_date")))
    if first_start.day != 1:
        raise ValueError("first_start_date must be the first day of a month")
    if last_end <= first_start:
        raise ValueError("last_end_date must be after first_start_date")
    window_months = int(spec.get("window_months", 0))
    step_months = int(spec.get("step_months", 0))
    if window_months < 2 or step_months < 1:
        raise ValueError("window_months must be at least 2 and step_months positive")
    rates = spec.get("uniform_purchase_fee_rates")
    if not isinstance(rates, list) or not rates:
        raise ValueError("uniform_purchase_fee_rates must be a non-empty list")
    if len({str(Decimal(str(value))) for value in rates}) != len(rates):
        raise ValueError("uniform purchase fee rates must be unique")
    for value in rates:
        rate = Decimal(str(value))
        if rate < 0 or rate > 1:
            raise ValueError("uniform purchase fee rates must be between 0 and 1")


def build_rolling_scenarios(
    base_scenario: Mapping[str, object],
    spec: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    _validate_spec(spec)
    base_period = base_scenario.get("period")
    if not isinstance(base_period, Mapping):
        raise ValueError("base scenario period is required")
    base_start = date.fromisoformat(str(base_period["start_date"]))
    base_end = date.fromisoformat(str(base_period["end_date"]))
    first_start = date.fromisoformat(str(spec["first_start_date"]))
    last_end = min(date.fromisoformat(str(spec["last_end_date"])), base_end)
    if first_start < base_start:
        raise ValueError("rolling first_start_date cannot precede base scenario")
    window_months = int(spec["window_months"])
    step_months = int(spec["step_months"])
    scenarios: list[dict[str, object]] = []
    start = first_start
    while True:
        end = _add_months(start, window_months) - timedelta(days=1)
        if end > last_end:
            break
        for fee_value in spec["uniform_purchase_fee_rates"]:
            fee_rate = Decimal(str(fee_value))
            fee_tag = str(fee_rate).replace("-", "m").replace(".", "p")
            scenario = deepcopy(dict(base_scenario))
            scenario["scenario_id"] = (
                f"{spec['rolling_id']}_{start:%Y%m}_{end:%Y%m}_fee{fee_tag}"
            )
            scenario["purpose"] = (
                f"Rolling window {start.isoformat()} through {end.isoformat()} with "
                f"uniform purchase fee {fee_rate}."
            )
            scenario["period"] = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
            scenario["uniform_purchase_fee_rate"] = str(fee_rate)
            routes = scenario.get("routes")
            if not isinstance(routes, list):
                raise ValueError("base scenario routes are required")
            for route in routes:
                if not isinstance(route, dict):
                    raise ValueError("base scenario route must be an object")
                route["purchase_fee_rate"] = str(fee_rate)
                route["rule_version"] = f"{route['rule_version']}_rolling_fee_{fee_tag}"
            scenarios.append(scenario)
        start = _add_months(start, step_months)
    if not scenarios:
        raise ValueError("rolling specification produces no complete windows")
    return tuple(scenarios)


def _metric_summary(rows: list[Mapping[str, object]], key: str) -> dict[str, float | None]:
    values = [float(row["metrics"][key]) for row in rows if row["metrics"].get(key) is not None]
    if not values:
        return {"minimum": None, "median": None, "maximum": None}
    return {"minimum": min(values), "median": median(values), "maximum": max(values)}


def run_rolling_windows(
    *,
    database: str | Path,
    base_scenario: Mapping[str, object],
    rolling_spec: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
) -> dict[str, object]:
    scenarios = build_rolling_scenarios(base_scenario, rolling_spec)
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        full_result = run_local_dca_research(
            database=database,
            scenario=scenario,
            strategy_spec=strategy_spec,
            strategy_spec_bytes=strategy_spec_bytes,
        )
        profiles = full_result["profile_results"]
        if not isinstance(profiles, list) or len(profiles) != 1:
            raise ValueError("rolling windows require exactly one allocation profile")
        profile = profiles[0]
        assert isinstance(profile, Mapping)
        period = scenario["period"]
        assert isinstance(period, Mapping)
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "scenario_sha256": full_result["scenario_sha256"],
                "full_result_sha256": _canonical_sha256(full_result),
                "start_date": period["start_date"],
                "end_date": period["end_date"],
                "uniform_purchase_fee_rate": scenario["uniform_purchase_fee_rate"],
                "metrics": profile["metrics"],
                "data_bindings": full_result["data_bindings"],
                "official_rule_gate": full_result["official_rule_gate"],
            }
        )
    rows.sort(key=lambda row: (str(row["uniform_purchase_fee_rate"]), str(row["start_date"])))
    summaries: dict[str, object] = {}
    for rate in sorted({str(row["uniform_purchase_fee_rate"]) for row in rows}, key=Decimal):
        selected = [row for row in rows if row["uniform_purchase_fee_rate"] == rate]
        summaries[rate] = {
            "window_count": len(selected),
            "annualized_return_pct": _metric_summary(selected, "annualized_return_pct"),
            "xirr_pct": _metric_summary(selected, "xirr_pct"),
            "maximum_drawdown_pct": _metric_summary(selected, "maximum_drawdown_pct"),
            "average_cash_drag_pct": _metric_summary(selected, "average_cash_drag_pct"),
        }
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "rolling_id": rolling_spec["rolling_id"],
        "rolling_spec_sha256": _canonical_sha256(rolling_spec),
        "base_scenario_sha256": _canonical_sha256(base_scenario),
        "cell_count": len(rows),
        "rows": rows,
        "summaries_by_fee_rate": summaries,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
