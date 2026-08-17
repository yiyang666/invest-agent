"""Deterministic sensitivity-matrix orchestration for local DCA research."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from .local_research import run_local_dca_research


ENGINE_VERSION = "local_dca_sensitivity_matrix_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_sensitivity_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sensitivity matrix specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("sensitivity matrix specification must use schema_version 1")
    if not str(spec.get("matrix_id", "")).strip():
        raise ValueError("matrix_id is required")
    amounts = spec.get("monthly_contribution_cny")
    days = spec.get("calendar_days")
    defenses = spec.get("defensive_routes")
    if not isinstance(amounts, list) or not amounts:
        raise ValueError("monthly_contribution_cny must be a non-empty list")
    if not isinstance(days, list) or not days:
        raise ValueError("calendar_days must be a non-empty list")
    if not isinstance(defenses, list) or not defenses:
        raise ValueError("defensive_routes must be a non-empty list")
    if len({str(value) for value in amounts}) != len(amounts):
        raise ValueError("monthly contribution values must be unique")
    if len({int(value) for value in days}) != len(days):
        raise ValueError("calendar day values must be unique")
    for amount in amounts:
        if Decimal(str(amount)) <= 0:
            raise ValueError("monthly contribution values must be positive")
    for day in days:
        if not 1 <= int(day) <= 28:
            raise ValueError("calendar days must be between 1 and 28")
    fee_rates = spec.get("uniform_purchase_fee_rates")
    if fee_rates is not None:
        if not isinstance(fee_rates, list) or not fee_rates:
            raise ValueError("uniform_purchase_fee_rates must be a non-empty list")
        if len({str(Decimal(str(value))) for value in fee_rates}) != len(fee_rates):
            raise ValueError("uniform purchase fee rates must be unique")
        for value in fee_rates:
            rate = Decimal(str(value))
            if rate < 0 or rate > 1:
                raise ValueError("uniform purchase fee rates must be between 0 and 1")
    codes: set[str] = set()
    for route in defenses:
        if not isinstance(route, Mapping):
            raise ValueError("defensive route must be an object")
        code = str(route.get("fund_code", ""))
        if len(code) != 6 or not code.isdigit() or code in codes:
            raise ValueError("defensive fund codes must be unique six-digit values")
        codes.add(code)
        if not str(route.get("role", "")).strip():
            raise ValueError("defensive route role is required")


def _deep_merge(target: dict[str, object], overrides: Mapping[str, object]) -> None:
    for key, value in overrides.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = deepcopy(value)


def build_sensitivity_scenarios(
    base_scenario: Mapping[str, object],
    spec: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    _validate_spec(spec)
    routes = base_scenario.get("routes")
    if not isinstance(routes, list):
        raise ValueError("base scenario routes are required")
    defensive_indexes = [
        index
        for index, route in enumerate(routes)
        if isinstance(route, Mapping) and route.get("sleeve") == "defensive"
    ]
    if len(defensive_indexes) != 1:
        raise ValueError("base scenario must contain exactly one defensive route")
    defensive_index = defensive_indexes[0]
    scenarios: list[dict[str, object]] = []
    fee_rates = spec.get("uniform_purchase_fee_rates", [None])
    assert isinstance(fee_rates, list)
    for amount in spec["monthly_contribution_cny"]:
        for day in spec["calendar_days"]:
            for defense in spec["defensive_routes"]:
                for fee_rate_value in fee_rates:
                    fee_rate = (
                        None
                        if fee_rate_value is None
                        else Decimal(str(fee_rate_value))
                    )
                    scenario = deepcopy(dict(base_scenario))
                    overrides = spec.get("scenario_overrides", {})
                    if not isinstance(overrides, Mapping):
                        raise ValueError("scenario_overrides must be an object")
                    _deep_merge(scenario, overrides)
                    code = str(defense["fund_code"])
                    fee_suffix = ""
                    if fee_rate is not None:
                        fee_tag = str(fee_rate).replace("-", "m").replace(".", "p")
                        fee_suffix = f"_fee{fee_tag}"
                    scenario["scenario_id"] = (
                        f"{spec['matrix_id']}_c{Decimal(str(amount)):f}_d{int(day):02d}_def{code}"
                        f"{fee_suffix}"
                    )
                    scenario["purpose"] = (
                        f"Sensitivity cell: monthly contribution {amount}, calendar day {int(day)}, "
                        f"defensive route {code}, uniform purchase fee "
                        f"{fee_rate if fee_rate is not None else 'base route values'}."
                    )
                    scenario["monthly_contribution_cny"] = str(amount)
                    scenario["calendar_day"] = int(day)
                    scenario_routes = scenario["routes"]
                    assert isinstance(scenario_routes, list)
                    defensive_route = scenario_routes[defensive_index]
                    assert isinstance(defensive_route, dict)
                    defensive_route["fund_code"] = code
                    defensive_route["role"] = str(defense["role"])
                    defensive_route["rule_version"] = str(
                        defense.get("rule_version", defensive_route["rule_version"])
                    )
                    if fee_rate is not None:
                        scenario["uniform_purchase_fee_rate"] = str(fee_rate)
                        for route in scenario_routes:
                            assert isinstance(route, dict)
                            route["purchase_fee_rate"] = str(fee_rate)
                            route["rule_version"] = (
                                f"{route['rule_version']}_uniform_fee_{fee_tag}"
                            )
                    scenarios.append(scenario)
    return tuple(scenarios)


def _issue_summary(monthly_reconciliation: Sequence[Mapping[str, object]]) -> dict[str, object]:
    issue_counts: dict[str, int] = {}
    issue_months = 0
    cash_values: list[Decimal] = []
    cross_month_values: list[Decimal] = []
    for month in monthly_reconciliation:
        issues = month.get("issues", [])
        if not isinstance(issues, list):
            raise ValueError("monthly reconciliation issues must be a list")
        if issues:
            issue_months += 1
        for issue in issues:
            key = str(issue)
            issue_counts[key] = issue_counts.get(key, 0) + 1
        cash_values.append(Decimal(str(month["cash_remaining_cny"])))
        cross_month_values.append(Decimal(str(month.get("cross_month_scheduled_cny", "0"))))
    return {
        "issue_months": issue_months,
        "issue_counts": dict(sorted(issue_counts.items())),
        "maximum_month_end_cash_cny": f"{max(cash_values, default=Decimal('0')):.2f}",
        "total_month_end_cash_cny": f"{sum(cash_values, Decimal('0')):.2f}",
        "cross_month_queue_months": sum(value > 0 for value in cross_month_values),
        "total_cross_month_scheduled_cny": f"{sum(cross_month_values, Decimal('0')):.2f}",
    }


def run_sensitivity_matrix(
    *,
    database: str | Path,
    base_scenario: Mapping[str, object],
    matrix_spec: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
) -> dict[str, object]:
    scenarios = build_sensitivity_scenarios(base_scenario, matrix_spec)
    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        full_result = run_local_dca_research(
            database=database,
            scenario=scenario,
            strategy_spec=strategy_spec,
            strategy_spec_bytes=strategy_spec_bytes,
        )
        profile_results = full_result["profile_results"]
        if not isinstance(profile_results, list) or len(profile_results) != 1:
            raise ValueError("sensitivity matrix requires exactly one allocation profile")
        profile = profile_results[0]
        if not isinstance(profile, Mapping):
            raise ValueError("profile result must be an object")
        route = next(
            item
            for item in scenario["routes"]
            if isinstance(item, Mapping) and item.get("sleeve") == "defensive"
        )
        reconciliation = profile["monthly_reconciliation"]
        assert isinstance(reconciliation, list)
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "scenario_sha256": full_result["scenario_sha256"],
                "full_result_sha256": _canonical_sha256(full_result),
                "monthly_contribution_cny": str(scenario["monthly_contribution_cny"]),
                "calendar_day": scenario["calendar_day"],
                "uniform_purchase_fee_rate": scenario.get("uniform_purchase_fee_rate"),
                "defensive_fund_code": route["fund_code"],
                "metrics": profile["metrics"],
                "reconciliation_summary": _issue_summary(reconciliation),
                "data_bindings": full_result["data_bindings"],
                "official_rule_gate": full_result["official_rule_gate"],
            }
        )
    rows.sort(
        key=lambda row: (
            Decimal(str(row["monthly_contribution_cny"])),
            int(row["calendar_day"]),
            str(row["defensive_fund_code"]),
            Decimal(str(row["uniform_purchase_fee_rate"] or "0")),
        )
    )
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "matrix_id": matrix_spec["matrix_id"],
        "matrix_spec_sha256": _canonical_sha256(matrix_spec),
        "base_scenario_sha256": _canonical_sha256(base_scenario),
        "cell_count": len(rows),
        "rows": rows,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
