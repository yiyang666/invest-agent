"""Compare one research strategy with fixed and matched-cashflow 631 paths."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from .local_research import run_local_dca_research


ENGINE_VERSION = "pair_cashflow_compare_v1"
FIXED_631_MONTHLY_CNY = Decimal("3000")


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _single_profile(result: Mapping[str, object]) -> Mapping[str, object]:
    profiles = result.get("profile_results")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("pair comparison requires exactly one profile per scenario")
    profile = profiles[0]
    if not isinstance(profile, Mapping):
        raise ValueError("profile result must be an object")
    return profile


def _cashflow_schedule(
    reconciliation: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    schedule: dict[str, str] = {}
    for row in reconciliation:
        planned_date = str(row.get("planned_date", ""))
        amount = Decimal(str(row.get("contribution_cny", "0")))
        if not planned_date or planned_date in schedule:
            raise ValueError("candidate reconciliation dates must be non-empty and unique")
        if amount <= 0:
            raise ValueError("candidate monthly contributions must be positive")
        schedule[planned_date] = f"{amount:.2f}"
    if not schedule:
        raise ValueError("candidate reconciliation must contain monthly cashflows")
    return schedule


def build_benchmark_scenarios(
    *,
    candidate_scenario: Mapping[str, object],
    benchmark_scenario: Mapping[str, object],
    candidate_schedule: Mapping[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    """Align benchmark window and contribution dates without changing its routes."""
    candidate_period = candidate_scenario.get("period")
    if not isinstance(candidate_period, Mapping):
        raise ValueError("candidate period is required")
    fixed = deepcopy(dict(benchmark_scenario))
    fixed["scenario_id"] = (
        f"{benchmark_scenario['scenario_id']}__fixed_3000_common_window"
    )
    fixed["period"] = deepcopy(dict(candidate_period))
    fixed["calendar_day"] = int(candidate_scenario["calendar_day"])
    fixed["monthly_contribution_cny"] = f"{FIXED_631_MONTHLY_CNY:.2f}"
    fixed.pop("monthly_contribution_schedule_cny", None)

    matched = deepcopy(fixed)
    matched["scenario_id"] = (
        f"{benchmark_scenario['scenario_id']}__matched_cashflow_common_window"
    )
    matched["monthly_contribution_schedule_cny"] = dict(candidate_schedule)
    return fixed, matched


def run_pair_cashflow_comparison(
    *,
    database: str | Path,
    candidate_scenario: Mapping[str, object],
    candidate_spec: Mapping[str, object],
    candidate_spec_bytes: bytes,
    benchmark_scenario: Mapping[str, object],
    benchmark_spec: Mapping[str, object],
    benchmark_spec_bytes: bytes,
) -> dict[str, object]:
    candidate_result = run_local_dca_research(
        database=database,
        scenario=candidate_scenario,
        strategy_spec=candidate_spec,
        strategy_spec_bytes=candidate_spec_bytes,
    )
    candidate_profile = _single_profile(candidate_result)
    reconciliation = candidate_profile.get("monthly_reconciliation")
    if not isinstance(reconciliation, list) or not all(
        isinstance(row, Mapping) for row in reconciliation
    ):
        raise ValueError("candidate monthly reconciliation is required")
    candidate_schedule = _cashflow_schedule(reconciliation)
    fixed_scenario, matched_scenario = build_benchmark_scenarios(
        candidate_scenario=candidate_scenario,
        benchmark_scenario=benchmark_scenario,
        candidate_schedule=candidate_schedule,
    )
    fixed_result = run_local_dca_research(
        database=database,
        scenario=fixed_scenario,
        strategy_spec=benchmark_spec,
        strategy_spec_bytes=benchmark_spec_bytes,
    )
    matched_result = run_local_dca_research(
        database=database,
        scenario=matched_scenario,
        strategy_spec=benchmark_spec,
        strategy_spec_bytes=benchmark_spec_bytes,
    )
    fixed_profile = _single_profile(fixed_result)
    matched_profile = _single_profile(matched_result)
    matched_reconciliation = matched_profile.get("monthly_reconciliation")
    if not isinstance(matched_reconciliation, list) or not all(
        isinstance(row, Mapping) for row in matched_reconciliation
    ):
        raise ValueError("matched benchmark reconciliation is required")
    matched_schedule = _cashflow_schedule(matched_reconciliation)
    cashflows_match = candidate_schedule == matched_schedule
    if not cashflows_match:
        raise ValueError("candidate and matched 631 monthly cashflows diverged")

    candidate_metrics = candidate_profile["metrics"]
    fixed_metrics = fixed_profile["metrics"]
    matched_metrics = matched_profile["metrics"]
    assert isinstance(candidate_metrics, Mapping)
    assert isinstance(fixed_metrics, Mapping)
    assert isinstance(matched_metrics, Mapping)
    candidate_final = Decimal(str(candidate_metrics["final_value_cny"]))

    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "classification": "exploratory_counterfactual_execution",
        "research_stage": candidate_scenario.get("research_stage"),
        "research_evidence_level": candidate_scenario.get(
            "research_evidence_level", "research_ready"
        ),
        "candidate": {
            "scenario_id": candidate_result["scenario_id"],
            "strategy": candidate_result["strategy"],
            "metrics": candidate_metrics,
        },
        "comparators": {
            "fixed_3000_631": {
                "scenario_id": fixed_result["scenario_id"],
                "strategy": fixed_result["strategy"],
                "metrics": fixed_metrics,
            },
            "matched_cashflow_631": {
                "scenario_id": matched_result["scenario_id"],
                "strategy": matched_result["strategy"],
                "metrics": matched_metrics,
            },
        },
        "candidate_final_value_difference_cny": {
            "vs_fixed_3000_631": f"{candidate_final - Decimal(str(fixed_metrics['final_value_cny'])):.2f}",
            "vs_matched_cashflow_631": f"{candidate_final - Decimal(str(matched_metrics['final_value_cny'])):.2f}",
        },
        "cashflow_check": {
            "exact_monthly_match": cashflows_match,
            "months": len(candidate_schedule),
            "candidate_total_cny": f"{sum((Decimal(value) for value in candidate_schedule.values()), Decimal('0')):.2f}",
            "matched_631_total_cny": f"{sum((Decimal(value) for value in matched_schedule.values()), Decimal('0')):.2f}",
            "monthly_schedule": candidate_schedule,
        },
        "assumption_labels": {
            "candidate_historical_truth_claimed": False,
            "benchmark_historical_truth_claimed": False,
            "same_external_cashflow_does_not_mean_same_fund_or_route_rules": True,
            "automatic_winner_selection": False,
        },
        "official_rule_gates": {
            "candidate": candidate_result["official_rule_gate"],
            "benchmark": matched_result["official_rule_gate"],
        },
        "data_bindings": {
            "candidate": candidate_result["data_bindings"],
            "benchmark": matched_result["data_bindings"],
        },
        "full_result_sha256": {
            "candidate": _canonical_sha256(candidate_result),
            "fixed_3000_631": _canonical_sha256(fixed_result),
            "matched_cashflow_631": _canonical_sha256(matched_result),
        },
        "scenario_sha256": {
            "candidate": _canonical_sha256(candidate_scenario),
            "fixed_3000_631": _canonical_sha256(fixed_scenario),
            "matched_cashflow_631": _canonical_sha256(matched_scenario),
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result
