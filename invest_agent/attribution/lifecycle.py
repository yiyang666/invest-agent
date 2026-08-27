"""Versioned, fail-closed strategy lifecycle evaluation."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Mapping


def load_attribution_policy(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("attribution policy must be a schema version 1 object")
    if payload.get("status") != "active":
        raise ValueError("attribution policy must be active")
    lifecycle = payload.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise ValueError("attribution lifecycle policy is required")
    if lifecycle.get("automatic_promotion_allowed") is not False or lifecycle.get(
        "automatic_retirement_allowed"
    ) is not False:
        raise ValueError("strategy lifecycle cannot be automatic")
    return payload


def evaluate_strategy_lifecycle(
    evidence: Mapping[str, object], policy: Mapping[str, object]
) -> dict[str, object]:
    """Return a review state; never promote or retire a strategy automatically."""

    lifecycle = policy.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise ValueError("lifecycle policy is required")
    strategy_id = str(evidence.get("strategy_id", ""))
    strategy_version = str(evidence.get("strategy_version", ""))
    if not strategy_id or not strategy_version:
        raise ValueError("strategy identity is required")
    if evidence.get("benchmark") != policy.get("benchmark"):
        raise ValueError("lifecycle evidence uses the wrong benchmark")
    months = int(evidence.get("forward_observation_months", -1))
    if months < 0:
        raise ValueError("forward observation months cannot be negative")
    violations = evidence.get("unresolved_violations", [])
    if not isinstance(violations, list):
        raise ValueError("unresolved violations must be a list")

    blockers: list[str] = []
    if evidence.get("data_fresh") is not True:
        blockers.append("stale_or_unverified_data")
    if evidence.get("deterministic_rerun_match") is not True:
        blockers.append("deterministic_rerun_failed")
    if evidence.get("matched_cashflow_comparator") is not True:
        blockers.append("matched_cashflow_comparator_missing")
    if violations:
        blockers.append("unresolved_evidence_violations")

    risk_veto = evidence.get("risk_veto") is True
    expected_hash = str(evidence.get("expected_spec_sha256", ""))
    observed_hash = str(evidence.get("observed_spec_sha256", ""))
    hash_drift = len(expected_hash) != 64 or expected_hash != observed_hash
    drift = Decimal(str(evidence.get("maximum_target_weight_drift_pp", "0")))
    drift_limit = Decimal(str(lifecycle["maximum_target_weight_drift_pp"]))
    active_return = Decimal(str(evidence.get("active_return_pct", "0")))
    return_floor = Decimal(str(lifecycle["active_return_floor_pct"]))
    drawdown_worsening = Decimal(
        str(evidence.get("maximum_drawdown_worsening_pp", "0"))
    )
    drawdown_limit = Decimal(str(lifecycle["maximum_drawdown_worsening_pp"]))
    observation_min = int(lifecycle["minimum_forward_observation_months"])
    retirement_min = int(lifecycle["retirement_review_minimum_months"])

    if risk_veto:
        state = "risk_veto"
        reason = "independent risk layer vetoed the strategy"
    elif blockers:
        state = "blocked_evidence"
        reason = ",".join(blockers)
    elif hash_drift or drift > drift_limit:
        state = "revision_required"
        reason = "strategy version or target weights drifted beyond the frozen contract"
    elif months < observation_min:
        state = "continue_forward_observation"
        reason = "minimum forward observation window is not complete"
    elif months >= retirement_min and (
        active_return < return_floor or drawdown_worsening > drawdown_limit
    ):
        state = "retirement_review"
        reason = "precommitted underperformance or drawdown review threshold was crossed"
    else:
        state = "eligible_for_human_review"
        reason = "minimum evidence gates passed; no automatic promotion is allowed"

    return {
        "policy_id": policy.get("policy_id"),
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "state": state,
        "reason": reason,
        "evidence": {
            "forward_observation_months": months,
            "active_return_pct": str(active_return),
            "maximum_drawdown_worsening_pp": str(drawdown_worsening),
            "maximum_target_weight_drift_pp": str(drift),
            "blockers": blockers,
            "spec_hash_drift": hash_drift,
        },
        "automatic_promotion": False,
        "automatic_retirement": False,
        "order_generation_available": False,
    }
