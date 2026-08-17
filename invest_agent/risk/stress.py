"""Deterministic sleeve-level portfolio stress paths with independent vetoes."""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping


ENGINE_VERSION = "portfolio_sleeve_stress_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_stress_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stress specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("stress specification must use schema_version 1")
    if not str(spec.get("stress_id", "")).strip():
        raise ValueError("stress_id is required")
    if spec.get("probability_model") != "none_non_probabilistic_scenarios":
        raise ValueError("stress scenarios must not imply occurrence probabilities")
    target = Decimal(str(spec.get("target_drawdown_pct")))
    limit = Decimal(str(spec.get("stress_limit_drawdown_pct")))
    if target <= 0 or limit <= target or limit > 100:
        raise ValueError("drawdown limits must satisfy 0 < target < stress limit <= 100")
    scenarios = spec.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("at least one stress scenario is required")
    scenario_ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("stress scenario must be an object")
        scenario_id = str(scenario.get("scenario_id", ""))
        if not scenario_id or scenario_id in scenario_ids:
            raise ValueError("stress scenario IDs must be non-empty and unique")
        scenario_ids.add(scenario_id)
        steps = scenario.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("each stress scenario requires at least one step")
        step_ids: set[str] = set()
        for step in steps:
            if not isinstance(step, Mapping):
                raise ValueError("stress step must be an object")
            step_id = str(step.get("step_id", ""))
            if not step_id or step_id in step_ids:
                raise ValueError("stress step IDs must be non-empty and unique per scenario")
            step_ids.add(step_id)
            returns = step.get("sleeve_returns")
            if not isinstance(returns, Mapping) or not returns:
                raise ValueError("stress step sleeve_returns are required")
            for value in returns.values():
                if Decimal(str(value)) <= Decimal("-1"):
                    raise ValueError("stress sleeve return must be greater than -1")


def _extract_weights(base_scenario: Mapping[str, object]) -> dict[str, Decimal]:
    profiles = base_scenario.get("allocation_profiles")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("stress testing requires exactly one allocation profile")
    profile = profiles[0]
    if not isinstance(profile, Mapping):
        raise ValueError("allocation profile must be an object")
    raw_weights = profile.get("weights")
    if not isinstance(raw_weights, Mapping) or not raw_weights:
        raise ValueError("allocation profile weights are required")
    weights = {str(key): Decimal(str(value)) for key, value in raw_weights.items()}
    if any(value < 0 for value in weights.values()):
        raise ValueError("stress weights cannot be negative")
    if sum(weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("stress weights must sum exactly to 1")
    return weights


def run_portfolio_stress(
    *,
    base_scenario: Mapping[str, object],
    stress_spec: Mapping[str, object],
) -> dict[str, object]:
    _validate_spec(stress_spec)
    weights = _extract_weights(base_scenario)
    target = Decimal(str(stress_spec["target_drawdown_pct"]))
    limit = Decimal(str(stress_spec["stress_limit_drawdown_pct"]))
    rows: list[dict[str, object]] = []
    for raw_scenario in stress_spec["scenarios"]:
        assert isinstance(raw_scenario, Mapping)
        sleeve_values = dict(weights)
        peak = Decimal("1")
        worst_drawdown = Decimal("0")
        path: list[dict[str, object]] = []
        for raw_step in raw_scenario["steps"]:
            assert isinstance(raw_step, Mapping)
            raw_returns = raw_step["sleeve_returns"]
            assert isinstance(raw_returns, Mapping)
            if set(map(str, raw_returns.keys())) != set(weights):
                raise ValueError(
                    f"stress step {raw_step['step_id']} sleeves must exactly match allocation profile"
                )
            for sleeve in sleeve_values:
                sleeve_values[sleeve] *= Decimal("1") + Decimal(str(raw_returns[sleeve]))
            total = sum(sleeve_values.values(), Decimal("0"))
            peak = max(peak, total)
            drawdown_pct = (total / peak - Decimal("1")) * Decimal("100")
            worst_drawdown = min(worst_drawdown, drawdown_pct)
            path.append(
                {
                    "step_id": raw_step["step_id"],
                    "portfolio_value": str(total),
                    "drawdown_pct": float(drawdown_pct),
                }
            )
        terminal_value = sum(sleeve_values.values(), Decimal("0"))
        loss_magnitude = -worst_drawdown
        if loss_magnitude > limit:
            classification = "fail_stress_limit"
        elif loss_magnitude > target:
            classification = "review_target_breach"
        else:
            classification = "pass_within_target"
        rows.append(
            {
                "scenario_id": raw_scenario["scenario_id"],
                "label": raw_scenario.get("label", raw_scenario["scenario_id"]),
                "step_count": len(path),
                "terminal_return_pct": float((terminal_value - Decimal("1")) * Decimal("100")),
                "maximum_drawdown_pct": float(worst_drawdown),
                "classification": classification,
                "target_drawdown_breached": loss_magnitude > target,
                "stress_limit_breached": loss_magnitude > limit,
                "risk_veto": classification == "fail_stress_limit",
                "path": path,
            }
        )
    rows.sort(key=lambda row: str(row["scenario_id"]))
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "stress_id": stress_spec["stress_id"],
        "stress_spec_sha256": _canonical_sha256(stress_spec),
        "base_scenario_sha256": _canonical_sha256(base_scenario),
        "portfolio_dynamics": "buy_and_hold_no_rebalance_no_contributions",
        "initial_portfolio_value": "1",
        "target_drawdown_pct": str(target),
        "stress_limit_drawdown_pct": str(limit),
        "rows": rows,
        "summary": {
            "scenario_count": len(rows),
            "within_target_count": sum(row["classification"] == "pass_within_target" for row in rows),
            "target_review_count": sum(row["classification"] == "review_target_breach" for row in rows),
            "stress_limit_failure_count": sum(row["classification"] == "fail_stress_limit" for row in rows),
            "risk_veto": any(bool(row["risk_veto"]) for row in rows),
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
