"""Frozen-parameter robustness checks for the new-money trend strategy."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from invest_agent.risk.stress import run_portfolio_stress

from .local_research import run_local_dca_research


ENGINE_VERSION = "new_money_trend_rs_robustness_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_trend_robustness_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trend robustness specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("trend robustness specification must use schema_version 1")
    if not str(spec.get("robustness_id", "")):
        raise ValueError("robustness_id is required")
    grid = spec.get("economic_grid")
    if not isinstance(grid, Mapping):
        raise ValueError("economic_grid is required")
    amounts = grid.get("monthly_contribution_cny")
    fees = grid.get("uniform_purchase_fee_rates")
    if not isinstance(amounts, list) or not amounts or not isinstance(fees, list) or not fees:
        raise ValueError("economic grid amounts and fees must be non-empty lists")
    if any(Decimal(str(value)) <= 0 for value in amounts):
        raise ValueError("economic grid amounts must be positive")
    if any(not Decimal("0") <= Decimal(str(value)) <= Decimal("1") for value in fees):
        raise ValueError("economic grid fee rates must be between 0 and 1")
    variants = spec.get("signal_variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError("signal_variants must be a non-empty list")
    ids = [str(item.get("variant_id", "")) for item in variants if isinstance(item, Mapping)]
    if len(ids) != len(variants) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("signal variant IDs must be non-empty and unique")
    if str(spec.get("primary_variant_id")) not in ids:
        raise ValueError("primary_variant_id must name a signal variant")
    for item in variants:
        assert isinstance(item, Mapping)
        if not 1 <= int(item.get("calendar_day", 0)) <= 28:
            raise ValueError("signal variant calendar_day must be between 1 and 28")
        parameters = item.get("signal_parameters")
        if not isinstance(parameters, Mapping) or not parameters:
            raise ValueError("every signal variant requires signal_parameters")
        if any(int(value) <= 0 for value in parameters.values()):
            raise ValueError("signal variant parameters must be positive")


def _set_uniform_fee(scenario: dict[str, object], fee_rate: Decimal) -> None:
    routes = scenario["routes"]
    assert isinstance(routes, list)
    fee_tag = str(fee_rate).replace(".", "p")
    for route in routes:
        assert isinstance(route, dict)
        route["purchase_fee_rate"] = str(fee_rate)
        route["rule_version"] = f"{route['rule_version']}_robust_fee_{fee_tag}"


def _selection_summary(ledger: Sequence[Mapping[str, object]]) -> dict[str, object]:
    combination_counts: dict[str, int] = {}
    fund_counts: dict[str, int] = {}
    selected_slots = 0
    for signal in ledger:
        codes = [str(value) for value in signal["selected_fund_codes"]]
        key = "+".join(codes) if codes else "none"
        combination_counts[key] = combination_counts.get(key, 0) + 1
        selected_slots += len(codes)
        for code in codes:
            fund_counts[code] = fund_counts.get(code, 0) + 1
    return {
        "signal_months": len(ledger),
        "selected_slots": selected_slots,
        "combination_month_counts": dict(sorted(combination_counts.items())),
        "fund_selected_month_counts": dict(sorted(fund_counts.items())),
    }


def _result_row(
    scenario: Mapping[str, object], full_result: Mapping[str, object]
) -> dict[str, object]:
    profiles = full_result["profile_results"]
    assert isinstance(profiles, list) and len(profiles) == 1
    profile = profiles[0]
    assert isinstance(profile, Mapping)
    ledger = profile["monthly_signal_ledger"]
    assert isinstance(ledger, list)
    return {
        "scenario_id": scenario["scenario_id"],
        "scenario_sha256": full_result["scenario_sha256"],
        "full_result_sha256": _canonical_sha256(full_result),
        "monthly_contribution_cny": str(scenario["monthly_contribution_cny"]),
        "calendar_day": scenario["calendar_day"],
        "metrics": profile["metrics"],
        "selection_summary": _selection_summary(ledger),
    }


def _realized_weight_scenario(
    scenario: Mapping[str, object],
    full_result: Mapping[str, object],
    *,
    fallback_sleeve: str = "defensive",
) -> tuple[dict[str, object], dict[str, str]]:
    routes = scenario["routes"]
    assert isinstance(routes, list)
    fund_to_sleeve = {
        str(item["fund_code"]): str(item["sleeve"])
        for item in routes
        if isinstance(item, Mapping)
    }
    profiles = full_result["profile_results"]
    assert isinstance(profiles, list) and len(profiles) == 1
    profile = profiles[0]
    assert isinstance(profile, Mapping)
    valuation = profile["valuation_result"]
    assert isinstance(valuation, Mapping)
    daily = valuation["daily_valuations"]
    assert isinstance(daily, list) and daily
    last = daily[-1]
    assert isinstance(last, Mapping)
    amounts = {sleeve: Decimal("0") for sleeve in fund_to_sleeve.values()}
    positions = last["positions"]
    assert isinstance(positions, list)
    for position in positions:
        assert isinstance(position, Mapping)
        amounts[fund_to_sleeve[str(position["fund_code"])]] += Decimal(
            str(position["market_value_cny"])
        )
    amounts[fallback_sleeve] += sum(
        (
            Decimal(str(last[field]))
            for field in (
                "available_cash_cny",
                "pending_subscription_cash_cny",
                "distribution_receivable_cny",
            )
        ),
        Decimal("0"),
    )
    total = Decimal(str(last["total_value_cny"]))
    ordered = sorted(amounts)
    weights: dict[str, Decimal] = {}
    for sleeve in ordered:
        if sleeve != fallback_sleeve:
            weights[sleeve] = amounts[sleeve] / total
    weights[fallback_sleeve] = Decimal("1") - sum(weights.values(), Decimal("0"))
    stress_scenario = deepcopy(dict(scenario))
    stress_profiles = stress_scenario["allocation_profiles"]
    assert isinstance(stress_profiles, list) and len(stress_profiles) == 1
    assert isinstance(stress_profiles[0], dict)
    stress_profiles[0]["weights"] = {
        sleeve: str(weights[sleeve]) for sleeve in sorted(weights)
    }
    return stress_scenario, {
        sleeve: str(weights[sleeve]) for sleeve in sorted(weights)
    }


def run_trend_robustness(
    *,
    database: str | Path,
    trend_scenario: Mapping[str, object],
    robustness_spec: Mapping[str, object],
    trend_strategy_spec: Mapping[str, object],
    trend_strategy_spec_bytes: bytes,
    benchmark_scenario: Mapping[str, object],
    benchmark_strategy_spec: Mapping[str, object],
    benchmark_strategy_spec_bytes: bytes,
    stress_spec: Mapping[str, object],
) -> dict[str, object]:
    _validate_spec(robustness_spec)
    grid = robustness_spec["economic_grid"]
    assert isinstance(grid, Mapping)
    economic_rows: list[dict[str, object]] = []
    for amount in grid["monthly_contribution_cny"]:
        for raw_fee in grid["uniform_purchase_fee_rates"]:
            fee = Decimal(str(raw_fee))
            scenario = deepcopy(dict(trend_scenario))
            scenario["scenario_id"] = (
                f"{robustness_spec['robustness_id']}_economic_c{amount}_fee{str(fee).replace('.', 'p')}"
            )
            scenario["monthly_contribution_cny"] = str(amount)
            _set_uniform_fee(scenario, fee)
            result = run_local_dca_research(
                database=database,
                scenario=scenario,
                strategy_spec=trend_strategy_spec,
                strategy_spec_bytes=trend_strategy_spec_bytes,
            )
            row = _result_row(scenario, result)
            row["uniform_purchase_fee_rate"] = str(fee)
            economic_rows.append(row)

    signal_rows: list[dict[str, object]] = []
    primary_result: dict[str, object] | None = None
    primary_scenario: dict[str, object] | None = None
    for variant in robustness_spec["signal_variants"]:
        assert isinstance(variant, Mapping)
        scenario = deepcopy(dict(trend_scenario))
        scenario["scenario_id"] = (
            f"{robustness_spec['robustness_id']}_signal_{variant['variant_id']}"
        )
        scenario["calendar_day"] = int(variant["calendar_day"])
        allocation_engine = scenario["allocation_engine"]
        assert isinstance(allocation_engine, dict)
        allocation_engine["signal_parameters"] = deepcopy(
            dict(variant["signal_parameters"])
        )
        result = run_local_dca_research(
            database=database,
            scenario=scenario,
            strategy_spec=trend_strategy_spec,
            strategy_spec_bytes=trend_strategy_spec_bytes,
        )
        row = _result_row(scenario, result)
        row["variant_id"] = variant["variant_id"]
        row["signal_parameters"] = variant["signal_parameters"]
        signal_rows.append(row)
        if variant["variant_id"] == robustness_spec["primary_variant_id"]:
            primary_result = result
            primary_scenario = scenario
    assert primary_result is not None and primary_scenario is not None

    benchmark_result = run_local_dca_research(
        database=database,
        scenario=benchmark_scenario,
        strategy_spec=benchmark_strategy_spec,
        strategy_spec_bytes=benchmark_strategy_spec_bytes,
    )
    trend_stress_scenario, trend_weights = _realized_weight_scenario(
        primary_scenario, primary_result
    )
    benchmark_stress_scenario, benchmark_weights = _realized_weight_scenario(
        benchmark_scenario, benchmark_result
    )
    stress = {
        "basis": "historical_end_realized_weights_cash_attributed_to_defensive",
        "trend_realized_weights": trend_weights,
        "benchmark_realized_weights": benchmark_weights,
        "trend": run_portfolio_stress(
            base_scenario=trend_stress_scenario, stress_spec=stress_spec
        ),
        "benchmark": run_portfolio_stress(
            base_scenario=benchmark_stress_scenario, stress_spec=stress_spec
        ),
    }
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "robustness_id": robustness_spec["robustness_id"],
        "robustness_spec_sha256": _canonical_sha256(robustness_spec),
        "trend_scenario_sha256": _canonical_sha256(trend_scenario),
        "benchmark_scenario_sha256": _canonical_sha256(benchmark_scenario),
        "economic_grid_rows": economic_rows,
        "signal_variant_rows": signal_rows,
        "stress_comparison": stress,
        "data_bindings": primary_result["data_bindings"],
        "official_rule_gate": primary_result["official_rule_gate"],
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
