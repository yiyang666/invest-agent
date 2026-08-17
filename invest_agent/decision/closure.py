"""Deterministic Phase 4 engineering-closure audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .registry import load_strategy_registry, validate_strategy_registry


def _resolve(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    path = (root / relative).resolve()
    workspace = root.resolve()
    if path != workspace and workspace not in path.parents:
        raise ValueError(f"{field} escapes workspace root")
    if not path.is_file():
        raise ValueError(f"{field} does not exist: {relative}")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _check(name: str, passed: bool, evidence: object) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "evidence": evidence}


def classify_phase4_closure(
    checks: Sequence[Mapping[str, Any]], *, production_blockers: Sequence[str]
) -> dict[str, str]:
    engineering_pass = bool(checks) and all(item.get("passed") is True for item in checks)
    return {
        "engineering_research_status": (
            "closed" if engineering_pass else "not_closed"
        ),
        "production_readiness": (
            "blocked" if production_blockers or not engineering_pass else "eligible_for_separate_review"
        ),
        "phase5_entry": (
            "allowed_research_reporting_only" if engineering_pass else "blocked"
        ),
    }


def run_phase4_closure_audit(
    config: Mapping[str, Any], *, workspace_root: Path
) -> dict[str, Any]:
    if config.get("schema_version") != 1:
        raise ValueError("Phase 4 closure config schema_version must be 1")
    safety = config.get("safety")
    if not isinstance(safety, Mapping) or safety != {
        "research_only": True,
        "advisory_only": True,
        "network_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
    }:
        raise ValueError("Phase 4 closure safety contract is not exact")

    checks: list[dict[str, Any]] = []
    artifact_hashes: dict[str, str] = {}
    for item in config["required_artifacts"]:
        path = _resolve(workspace_root, item["path"], "required_artifact.path")
        actual = _sha256(path)
        artifact_hashes[str(item["path"])] = actual
        checks.append(
            _check(
                "artifact_hash:" + str(item["path"]),
                actual == item["sha256"],
                {"expected": item["sha256"], "actual": actual},
            )
        )

    expected = config["expected"]
    policy_path = _resolve(workspace_root, "config/investment_policy.yaml", "policy")
    policy_text = policy_path.read_text(encoding="utf-8")
    checks.extend(
        [
            _check(
                "policy_allocation_anchor",
                f"allocation_anchor: {expected['allocation_anchor']}" in policy_text,
                expected["allocation_anchor"],
            ),
            _check(
                "policy_comparison_benchmark",
                f"comparison_benchmark: {expected['comparison_benchmark']}" in policy_text,
                expected["comparison_benchmark"],
            ),
            _check(
                "policy_real_trading_disabled",
                "real_trading_enabled: false" in policy_text,
                "false",
            ),
        ]
    )

    registry_path = _resolve(workspace_root, "strategies/registry.json", "registry")
    registry = load_strategy_registry(registry_path)
    registry_index = validate_strategy_registry(registry, workspace_root=workspace_root)
    authorities = [
        item
        for item in registry["strategies"]
        if item["decision_permissions"]["target_allocation_authority"] is True
    ]
    checks.extend(
        [
            _check(
                "registry_schema",
                registry["schema_version"] == expected["registry_schema_version"],
                registry["schema_version"],
            ),
            _check(
                "registered_strategy_count",
                len(registry_index) == expected["registered_strategies"],
                len(registry_index),
            ),
            _check(
                "single_baseline_authority",
                len(authorities) == 1
                and authorities[0]["strategy_id"] == "dca_baseline"
                and authorities[0]["strategy_version"] == "1.4.0",
                [f"{item['strategy_id']}@{item['strategy_version']}" for item in authorities],
            ),
            _check(
                "all_strategies_research_only",
                all(item["mode"] == "research_only" for item in registry["strategies"]),
                [item["mode"] for item in registry["strategies"]],
            ),
            _check(
                "no_strategy_production_approved",
                all(item["gates"]["production_approved"] is False for item in registry["strategies"]),
                False,
            ),
            _check(
                "no_strategy_execution_enabled",
                all(item["gates"]["execution_enabled"] is False for item in registry["strategies"]),
                False,
            ),
            _check(
                "anchor_is_not_execution_schedule",
                registry["terminology"]["allocation_anchor_631"]["mandatory_execution_schedule"] is False,
                registry["terminology"]["allocation_anchor_631"],
            ),
            _check(
                "tactical_overlay_requires_fair_comparator_and_risk_veto",
                registry["terminology"]["tactical_overlay"]["requires_matched_cashflow_comparator"] is True
                and registry["terminology"]["tactical_overlay"]["risk_layer_has_veto"] is True,
                registry["terminology"]["tactical_overlay"],
            ),
        ]
    )

    scenario = _json(
        _resolve(workspace_root, "config/backtest_research_scenario_v8.json", "scenario")
    )
    checks.append(
        _check(
            "official_product_rule_gate_remains_blocked",
            scenario["official_rule_gate"]["status"] == expected["official_rule_gate"],
            scenario["official_rule_gate"],
        )
    )
    product_statuses: dict[str, str] = {}
    for relative in config["product_rule_snapshots"]:
        payload = _json(_resolve(workspace_root, relative, "product_rule_snapshot"))
        product_statuses[str(relative)] = str(payload.get("status"))
    checks.append(
        _check(
            "product_rule_snapshots_not_misrepresented_as_verified",
            all(value == "provisional" for value in product_statuses.values()),
            product_statuses,
        )
    )

    oos = _json(
        _resolve(workspace_root, "config/oos_evaluation_protocol_v4.json", "oos_protocol")
    )
    checks.extend(
        [
            _check(
                "oos_is_frozen_not_started",
                oos["status"] == "frozen_waiting_for_future_observations"
                and oos["current_result"]["status"] == "not_started_future_data_unavailable",
                oos["current_result"],
            ),
            _check(
                "oos_gate_not_falsely_passed",
                oos["current_result"]["gate_passed"] is expected["oos_gate_passed"],
                oos["current_result"]["gate_passed"],
            ),
            _check(
                "oos_clock_preserved_only_for_semantic_clarification",
                oos["supersedes"]["change_class"]
                == "semantic_clarification_no_strategy_parameter_change"
                and oos["supersedes"]["clock_reset_required"] is False,
                oos["supersedes"],
            ),
        ]
    )

    pack = _json(
        _resolve(
            workspace_root,
            "data/private/reports/phase-4-monthly-research-decision-pack-s4-2-2026-08-16.json",
            "decision_pack",
        )
    )
    checks.extend(
        [
            _check(
                "decision_pack_has_one_baseline_authority",
                sum(
                    item["target_allocation_authority"] is True
                    for item in pack["strategy_decision_input"]["accepted_signals"]
                )
                == 1,
                pack["strategy_decision_input"]["registry"],
            ),
            _check(
                "decision_pack_non_executable",
                pack["execution"]["orders"] == []
                and pack["execution"]["order_intent_generation_allowed"] is False
                and pack["execution"]["real_trading_enabled"] is False,
                pack["execution"],
            ),
        ]
    )
    replay = _json(
        _resolve(
            workspace_root,
            "data/private/reports/phase-4-decision-replay-acceptance-s4-3-2026-08-16.json",
            "decision_replay",
        )
    )
    checks.extend(
        [
            _check(
                "decision_replay_all_passed",
                replay["summary"]["acceptance_gate"] == "passed"
                and replay["summary"]["passed"] == expected["decision_replay_scenarios"]
                and replay["summary"]["failed"] == 0,
                replay["summary"],
            ),
            _check(
                "decision_replay_non_executable",
                replay["execution"]["orders"] == []
                and replay["execution"]["real_trading_enabled"] is False,
                replay["execution"],
            ),
        ]
    )

    production_blockers = [
        "official_channel_fees_limits_confirmation_and_qdii_calendars_incomplete",
        "product_rule_snapshots_are_provisional",
        "strict_point_in_time_prospective_oos_has_zero_complete_months",
        "historical_backfill_visibility_is_assumed",
        "current_account_concentration_requires_risk_review",
        "redemption_share_conversion_and_settlement_paths_are_incomplete",
    ]
    classification = classify_phase4_closure(checks, production_blockers=production_blockers)
    return {
        "schema_version": 1,
        "audit_id": str(config["audit_id"]),
        "as_of": str(config["as_of"]),
        "mode": "research_only",
        "checks": checks,
        "summary": {
            "total_checks": len(checks),
            "passed": sum(item["passed"] for item in checks),
            "failed": sum(not item["passed"] for item in checks),
            **classification,
        },
        "capabilities_ready_for_phase5": [
            "read_only_redacted_portfolio_snapshot",
            "validated_local_fund_data_and_provenance",
            "deterministic_metrics_market_state_and_risk",
            "versioned_strategy_specs_and_registry",
            "off_exchange_subscription_valuation_fee_and_distribution_research_backtest",
            "fair_fixed_matched_cashflow_and_rule_replacement_comparators",
            "monthly_research_decision_pack",
            "fail_closed_freshness_registry_risk_and_execution_gates",
            "reproducible_private_reports_and_hashes",
        ],
        "production_blockers": production_blockers,
        "phase5_scope": {
            "allowed": [
                "explain_registered_evidence",
                "summarize_policy_holdings_market_state_strategy_conflicts_and_uncertainty",
                "produce_replayable_non_executable_research_reports",
            ],
            "prohibited": [
                "generate_or_submit_real_orders",
                "claim_strategy_is_production_validated",
                "treat_historical_visibility_assumed_as_strict_point_in_time",
                "let_tactical_overlay_silently_redefine_allocation_anchor",
            ],
        },
        "execution": {
            "network_used": False,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
        "artifact_hashes": artifact_hashes,
    }
