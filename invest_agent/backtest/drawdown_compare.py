"""Fair fixed- and matched-cashflow comparison for drawdown-budget research."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .local_research import run_local_dca_research


ENGINE_VERSION = "drawdown_budget_dual_benchmark_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metric_deltas(
    left: Mapping[str, object], right: Mapping[str, object]
) -> dict[str, object]:
    keys = (
        "annualized_return_pct",
        "xirr_pct",
        "maximum_drawdown_pct",
        "annualized_volatility_pct",
        "time_weighted_return_pct",
        "average_cash_drag_pct",
    )
    return {
        key: float(left[key]) - float(right[key])
        for key in keys
        if left.get(key) is not None and right.get(key) is not None
    }


def run_drawdown_dual_benchmark(
    *,
    database: str | Path,
    drawdown_scenario: Mapping[str, object],
    drawdown_strategy_spec: Mapping[str, object],
    drawdown_strategy_spec_bytes: bytes,
    fixed_benchmark_scenario: Mapping[str, object],
    fixed_benchmark_strategy_spec: Mapping[str, object],
    fixed_benchmark_strategy_spec_bytes: bytes,
) -> dict[str, object]:
    drawdown_result = run_local_dca_research(
        database=database,
        scenario=drawdown_scenario,
        strategy_spec=drawdown_strategy_spec,
        strategy_spec_bytes=drawdown_strategy_spec_bytes,
    )
    fixed_result = run_local_dca_research(
        database=database,
        scenario=fixed_benchmark_scenario,
        strategy_spec=fixed_benchmark_strategy_spec,
        strategy_spec_bytes=fixed_benchmark_strategy_spec_bytes,
    )
    profiles = drawdown_result["profile_results"]
    assert isinstance(profiles, list)
    by_id = {str(profile["allocation_profile_id"]): profile for profile in profiles}
    strategy = by_id["drawdown_add_broad_cores"]
    matched = by_id["matched_cashflow_631_target_gap"]
    fixed_profiles = fixed_result["profile_results"]
    assert isinstance(fixed_profiles, list) and len(fixed_profiles) == 1
    fixed = fixed_profiles[0]
    strategy_cashflows = [
        str(item["contribution_cny"]) for item in strategy["monthly_reconciliation"]
    ]
    matched_cashflows = [
        str(item["contribution_cny"]) for item in matched["monthly_reconciliation"]
    ]
    if strategy_cashflows != matched_cashflows:
        raise ValueError("matched benchmark monthly cashflows are not identical")
    strategy_metrics = strategy["metrics"]
    matched_metrics = matched["metrics"]
    fixed_metrics = fixed["metrics"]
    assert isinstance(strategy_metrics, Mapping)
    assert isinstance(matched_metrics, Mapping)
    assert isinstance(fixed_metrics, Mapping)
    strategy_contributions = Decimal(str(strategy_metrics["contributions_cny"]))
    fixed_contributions = Decimal(str(fixed_metrics["contributions_cny"]))
    strategy_final = Decimal(str(strategy_metrics["final_value_cny"]))
    fixed_final = Decimal(str(fixed_metrics["final_value_cny"]))
    matched_final = Decimal(str(matched_metrics["final_value_cny"]))
    extra_contributions = strategy_contributions - fixed_contributions
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "drawdown_scenario_sha256": drawdown_result["scenario_sha256"],
        "fixed_benchmark_scenario_sha256": fixed_result["scenario_sha256"],
        "strategy_spec_sha256": drawdown_result["strategy"]["strategy_spec_sha256"],
        "fixed_benchmark_spec_sha256": fixed_result["strategy"]["strategy_spec_sha256"],
        "profiles": {
            "drawdown_add": strategy_metrics,
            "matched_cashflow_631": matched_metrics,
            "fixed_3000_631": fixed_metrics,
        },
        "cashflow_fairness": {
            "monthly_cashflows_identical_to_matched_benchmark": True,
            "drawdown_contributions_cny": f"{strategy_contributions:.2f}",
            "matched_contributions_cny": str(matched_metrics["contributions_cny"]),
            "fixed_contributions_cny": f"{fixed_contributions:.2f}",
            "extra_contributions_vs_fixed_cny": f"{extra_contributions:.2f}",
            "final_value_gain_vs_fixed_cny": f"{strategy_final - fixed_final:.2f}",
            "final_value_gain_net_of_extra_contributions_cny": f"{strategy_final - fixed_final - extra_contributions:.2f}",
            "final_value_difference_vs_matched_cashflow_cny": f"{strategy_final - matched_final:.2f}",
        },
        "rate_metric_deltas": {
            "drawdown_minus_fixed": _metric_deltas(strategy_metrics, fixed_metrics),
            "drawdown_minus_matched": _metric_deltas(strategy_metrics, matched_metrics),
        },
        "tier_coverage": strategy["drawdown_tier_coverage"],
        "drawdown_full_result_sha256": _canonical_sha256(drawdown_result),
        "fixed_full_result_sha256": _canonical_sha256(fixed_result),
        "data_bindings": {
            "drawdown": drawdown_result["data_bindings"],
            "fixed": fixed_result["data_bindings"],
        },
        "official_rule_gate": drawdown_result["official_rule_gate"],
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
