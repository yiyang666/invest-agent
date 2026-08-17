"""Reproducible comparison of allocation repair candidates."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping

from invest_agent.risk.stress import run_portfolio_stress

from .local_research import run_local_dca_research
from .rolling import run_rolling_windows


ENGINE_VERSION = "allocation_candidate_compare_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_comparison_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("candidate comparison specification must be an object")
    _validate_comparison_spec(payload)
    return payload


def _validate_comparison_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("candidate comparison specification must use schema_version 1")
    if not str(spec.get("comparison_id", "")).strip():
        raise ValueError("comparison_id is required")
    profile_ids = spec.get("expected_profile_ids")
    if not isinstance(profile_ids, list) or not profile_ids:
        raise ValueError("expected_profile_ids must be a non-empty list")
    if len({str(value) for value in profile_ids}) != len(profile_ids):
        raise ValueError("expected profile IDs must be unique")
    factors = spec.get("stress_severity_factors")
    if not isinstance(factors, list) or not factors:
        raise ValueError("stress_severity_factors must be a non-empty list")
    if len({str(Decimal(str(value))) for value in factors}) != len(factors):
        raise ValueError("stress severity factors must be unique")
    if any(Decimal(str(value)) <= 0 for value in factors):
        raise ValueError("stress severity factors must be positive")


def scale_stress_spec(
    stress_spec: Mapping[str, object], factor: Decimal
) -> dict[str, object]:
    if factor <= 0:
        raise ValueError("stress severity factor must be positive")
    scaled = deepcopy(dict(stress_spec))
    factor_tag = str(factor).replace(".", "p")
    scaled["stress_id"] = f"{stress_spec['stress_id']}_severity_{factor_tag}"
    scenarios = scaled.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("stress scenarios are required")
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("stress scenario must be an object")
        for step in scenario["steps"]:
            returns = step["sleeve_returns"]
            step["sleeve_returns"] = {
                sleeve: str(Decimal(str(value)) * factor)
                for sleeve, value in returns.items()
            }
    return scaled


def run_candidate_comparison(
    *,
    database: str | Path,
    candidate_scenario: Mapping[str, object],
    comparison_spec: Mapping[str, object],
    rolling_spec: Mapping[str, object],
    stress_spec: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
) -> dict[str, object]:
    _validate_comparison_spec(comparison_spec)
    profiles = candidate_scenario.get("allocation_profiles")
    if not isinstance(profiles, list):
        raise ValueError("candidate scenario allocation profiles are required")
    profile_ids = [str(profile["id"]) for profile in profiles]
    expected = [str(value) for value in comparison_spec["expected_profile_ids"]]
    if profile_ids != expected:
        raise ValueError("candidate scenario profiles must match expected_profile_ids in order")

    historical = run_local_dca_research(
        database=database,
        scenario=candidate_scenario,
        strategy_spec=strategy_spec,
        strategy_spec_bytes=strategy_spec_bytes,
    )
    historical_by_id = {
        str(profile["allocation_profile_id"]): profile
        for profile in historical["profile_results"]
    }
    rows: list[dict[str, object]] = []
    for profile in profiles:
        assert isinstance(profile, Mapping)
        profile_id = str(profile["id"])
        single_scenario = deepcopy(dict(candidate_scenario))
        single_scenario["allocation_profiles"] = [deepcopy(dict(profile))]
        rolling = run_rolling_windows(
            database=database,
            base_scenario=single_scenario,
            rolling_spec=rolling_spec,
            strategy_spec=strategy_spec,
            strategy_spec_bytes=strategy_spec_bytes,
        )
        fee_rates = rolling["summaries_by_fee_rate"]
        if not isinstance(fee_rates, Mapping) or len(fee_rates) != 1:
            raise ValueError("candidate rolling specification must contain exactly one fee rate")
        rolling_summary = next(iter(fee_rates.values()))
        stress_results: list[dict[str, object]] = []
        for factor_value in comparison_spec["stress_severity_factors"]:
            factor = Decimal(str(factor_value))
            stress = run_portfolio_stress(
                base_scenario=single_scenario,
                stress_spec=scale_stress_spec(stress_spec, factor),
            )
            scenario_rows = stress["rows"]
            worst = min(float(item["maximum_drawdown_pct"]) for item in scenario_rows)
            stress_results.append(
                {
                    "severity_factor": str(factor),
                    "worst_maximum_drawdown_pct": worst,
                    "risk_veto": stress["summary"]["risk_veto"],
                    "stress_limit_failure_count": stress["summary"]["stress_limit_failure_count"],
                    "scenario_drawdowns_pct": {
                        str(item["scenario_id"]): item["maximum_drawdown_pct"]
                        for item in scenario_rows
                    },
                }
            )
        historical_profile = historical_by_id[profile_id]
        rows.append(
            {
                "allocation_profile_id": profile_id,
                "weights": profile["weights"],
                "historical_metrics": historical_profile["metrics"],
                "rolling_summary": rolling_summary,
                "stress_robustness": stress_results,
            }
        )
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "comparison_id": comparison_spec["comparison_id"],
        "comparison_spec_sha256": _canonical_sha256(comparison_spec),
        "candidate_scenario_sha256": _canonical_sha256(candidate_scenario),
        "rolling_spec_sha256": _canonical_sha256(rolling_spec),
        "stress_spec_sha256": _canonical_sha256(stress_spec),
        "data_bindings": historical["data_bindings"],
        "official_rule_gate": historical["official_rule_gate"],
        "rows": rows,
        "selection": {
            "automatic_winner_selection": False,
            "selected_profile_id": None,
            "requires_user_policy_change": True,
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
