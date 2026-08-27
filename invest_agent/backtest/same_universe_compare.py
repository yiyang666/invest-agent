"""Compare one candidate with a frozen control that uses the same fund universe."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from .local_research import run_local_dca_research


ENGINE_VERSION = "same_universe_cashflow_compare_v1"
FROZEN_PROTOCOL_STATUS = "frozen_before_same_universe_performance_run"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _single_profile(result: Mapping[str, object]) -> Mapping[str, object]:
    profiles = result.get("profile_results")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("same-universe comparison requires one profile per scenario")
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
            raise ValueError("monthly reconciliation dates must be non-empty and unique")
        if amount <= 0:
            raise ValueError("monthly contributions must be positive")
        schedule[planned_date] = f"{amount:.2f}"
    if not schedule:
        raise ValueError("monthly reconciliation must contain cashflows")
    return schedule


def _single_weight_map(scenario: Mapping[str, object]) -> dict[str, str]:
    profiles = scenario.get("allocation_profiles")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("same-universe scenarios must each have one allocation profile")
    profile = profiles[0]
    if not isinstance(profile, Mapping) or not isinstance(profile.get("weights"), Mapping):
        raise ValueError("same-universe scenario weights are required")
    return {
        str(sleeve): str(Decimal(str(weight)))
        for sleeve, weight in profile["weights"].items()
    }


def _validate_protocol(
    *,
    protocol: Mapping[str, object],
    candidate_scenario: Mapping[str, object],
    control_scenario: Mapping[str, object],
    strategy_spec: Mapping[str, object],
) -> None:
    if protocol.get("schema_version") != 1:
        raise ValueError("same-universe protocol must use schema_version 1")
    if protocol.get("status") != FROZEN_PROTOCOL_STATUS:
        raise ValueError("same-universe protocol must be frozen before performance run")
    if protocol.get("strategy_id") != strategy_spec.get("strategy_id") or protocol.get(
        "strategy_version"
    ) != strategy_spec.get("strategy_version"):
        raise ValueError("comparison protocol and strategy spec identity diverged")
    control_contract = protocol.get("primary_same_universe_control")
    if not isinstance(control_contract, Mapping) or control_contract.get(
        "scenario_id"
    ) != control_scenario.get("scenario_id"):
        raise ValueError("control scenario does not match the frozen protocol")
    candidates = protocol.get("allowed_candidate_scenario_ids")
    if not isinstance(candidates, list) or candidate_scenario.get("scenario_id") not in candidates:
        raise ValueError("candidate scenario is not registered in the frozen protocol")
    invariants = protocol.get("required_invariants")
    if not isinstance(invariants, Mapping):
        raise ValueError("comparison protocol invariants are required")
    required_true = {
        "identical_route_payloads",
        "identical_strategic_sleeve_weights",
        "identical_provider",
        "identical_period",
        "identical_calendar_day",
        "identical_monthly_external_cashflows",
        "same_strategy_spec_bytes",
        "no_real_orders",
    }
    if any(invariants.get(key) is not True for key in required_true):
        raise ValueError("all same-universe boolean invariants must be enabled")
    if invariants.get("same_initial_state") != "zero_holdings_zero_cash":
        raise ValueError("same-universe control must freeze the zero-state initial condition")


def _validate_same_universe(
    candidate_scenario: Mapping[str, object], control_scenario: Mapping[str, object]
) -> None:
    if candidate_scenario.get("routes") != control_scenario.get("routes"):
        raise ValueError("candidate and control route payloads must be identical")
    if _single_weight_map(candidate_scenario) != _single_weight_map(control_scenario):
        raise ValueError("candidate and control strategic sleeve weights must be identical")
    for field in ("provider_id", "period", "calendar_day"):
        if candidate_scenario.get(field) != control_scenario.get(field):
            raise ValueError(f"candidate and control {field} must be identical")


def _metric_delta(
    candidate: Mapping[str, object], control: Mapping[str, object], key: str
) -> str:
    return f"{Decimal(str(candidate[key])) - Decimal(str(control[key])):.12f}"


def run_same_universe_comparison(
    *,
    database: str | Path,
    candidate_scenario: Mapping[str, object],
    control_scenario: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
    comparison_protocol: Mapping[str, object],
    comparison_protocol_bytes: bytes,
) -> dict[str, object]:
    """Run a causal-control comparison while enforcing the frozen universe invariants."""
    _validate_protocol(
        protocol=comparison_protocol,
        candidate_scenario=candidate_scenario,
        control_scenario=control_scenario,
        strategy_spec=strategy_spec,
    )
    _validate_same_universe(candidate_scenario, control_scenario)

    candidate_result = run_local_dca_research(
        database=database,
        scenario=candidate_scenario,
        strategy_spec=strategy_spec,
        strategy_spec_bytes=strategy_spec_bytes,
    )
    candidate_profile = _single_profile(candidate_result)
    candidate_reconciliation = candidate_profile.get("monthly_reconciliation")
    if not isinstance(candidate_reconciliation, list) or not all(
        isinstance(row, Mapping) for row in candidate_reconciliation
    ):
        raise ValueError("candidate monthly reconciliation is required")
    candidate_schedule = _cashflow_schedule(candidate_reconciliation)

    matched_control_scenario = deepcopy(dict(control_scenario))
    matched_control_scenario["monthly_contribution_schedule_cny"] = candidate_schedule
    control_result = run_local_dca_research(
        database=database,
        scenario=matched_control_scenario,
        strategy_spec=strategy_spec,
        strategy_spec_bytes=strategy_spec_bytes,
    )
    control_profile = _single_profile(control_result)
    control_reconciliation = control_profile.get("monthly_reconciliation")
    if not isinstance(control_reconciliation, list) or not all(
        isinstance(row, Mapping) for row in control_reconciliation
    ):
        raise ValueError("control monthly reconciliation is required")
    control_schedule = _cashflow_schedule(control_reconciliation)
    if candidate_schedule != control_schedule:
        raise ValueError("candidate and same-universe control monthly cashflows diverged")

    candidate_metrics = candidate_profile.get("metrics")
    control_metrics = control_profile.get("metrics")
    if not isinstance(candidate_metrics, Mapping) or not isinstance(
        control_metrics, Mapping
    ):
        raise ValueError("candidate and control metrics are required")
    candidate_final = Decimal(str(candidate_metrics["final_value_cny"]))
    control_final = Decimal(str(control_metrics["final_value_cny"]))
    total = sum(
        (Decimal(value) for value in candidate_schedule.values()), Decimal("0")
    )

    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "classification": "exploratory_counterfactual_same_universe_ablation",
        "research_stage": candidate_scenario.get("research_stage"),
        "research_evidence_level": candidate_scenario.get(
            "research_evidence_level", "research_ready"
        ),
        "comparison_protocol": {
            "protocol_id": comparison_protocol["protocol_id"],
            "sha256": hashlib.sha256(comparison_protocol_bytes).hexdigest(),
            "status": comparison_protocol["status"],
        },
        "candidate": {
            "scenario_id": candidate_result["scenario_id"],
            "strategy": candidate_result["strategy"],
            "allocation_engine_mode": candidate_profile["allocation_engine_mode"],
            "metrics": candidate_metrics,
        },
        "primary_same_universe_control": {
            "scenario_id": control_result["scenario_id"],
            "strategy": control_result["strategy"],
            "allocation_engine_mode": control_profile["allocation_engine_mode"],
            "metrics": control_metrics,
        },
        "candidate_minus_control": {
            "final_value_cny": f"{candidate_final - control_final:.2f}",
            "xirr_delta_pp": _metric_delta(candidate_metrics, control_metrics, "xirr_pct"),
            "annualized_return_delta_pp": _metric_delta(
                candidate_metrics, control_metrics, "annualized_return_pct"
            ),
            "annualized_volatility_delta_pp": _metric_delta(
                candidate_metrics, control_metrics, "annualized_volatility_pct"
            ),
            "maximum_drawdown_improvement_pp": _metric_delta(
                candidate_metrics, control_metrics, "maximum_drawdown_pct"
            ),
            "average_cash_drag_delta_pp": _metric_delta(
                candidate_metrics, control_metrics, "average_cash_drag_pct"
            ),
            "purchase_fees_cny": _metric_delta(
                candidate_metrics, control_metrics, "purchase_fees_cny"
            ),
        },
        "invariant_checks": {
            "identical_route_payloads": True,
            "identical_strategic_sleeve_weights": True,
            "identical_provider": True,
            "identical_period": True,
            "identical_calendar_day": True,
            "identical_monthly_external_cashflows": True,
            "same_initial_state": "zero_holdings_zero_cash",
            "same_strategy_spec_bytes": True,
            "months": len(candidate_schedule),
            "external_cashflow_total_cny": f"{total:.2f}",
        },
        "interpretation": {
            "asset_selection_effect_held_constant": True,
            "candidate_rule_increment_isolated_from_631_asset_mix": True,
            "external_631_anchor_still_required_separately": True,
            "automatic_winner_selection": False,
        },
        "official_rule_gates": {
            "candidate": candidate_result["official_rule_gate"],
            "control": control_result["official_rule_gate"],
        },
        "data_bindings": {
            "candidate": candidate_result["data_bindings"],
            "control": control_result["data_bindings"],
        },
        "full_result_sha256": {
            "candidate": _canonical_sha256(candidate_result),
            "control": _canonical_sha256(control_result),
        },
        "scenario_sha256": {
            "candidate": _canonical_sha256(candidate_scenario),
            "control": _canonical_sha256(matched_control_scenario),
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result
