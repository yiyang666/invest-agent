"""Deterministic failure-injection replay for monthly decision safety gates."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .monthly import _current_sleeves, _validate_market_context, _validate_snapshot_context
from .registry import REQUIRED_SIGNAL_FIELDS, build_decision_input, load_strategy_registry, validate_strategy_registry


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    result = (root / relative).resolve()
    workspace = root.resolve()
    if result != workspace and workspace not in result.parents:
        raise ValueError(f"{field} escapes workspace root")
    if not result.is_file():
        raise ValueError(f"{field} does not exist: {relative}")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _raw_signals(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    accepted = pack["strategy_decision_input"]["accepted_signals"]
    return [
        {key: deepcopy(item[key]) for key in REQUIRED_SIGNAL_FIELDS}
        for item in accepted
    ]


def _scenario(name: str, expected: str, passed: bool, observed: str) -> dict[str, Any]:
    return {
        "scenario": name,
        "expected": expected,
        "observed": observed,
        "passed": passed,
    }


def _expect_block(name: str, message: str, action: Callable[[], object]) -> dict[str, Any]:
    try:
        action()
    except ValueError as exc:
        observed = str(exc)
        return _scenario(name, f"block:{message}", message in observed, f"blocked:{observed}")
    return _scenario(name, f"block:{message}", False, "not_blocked")


def _expect_signal_rejection(
    *,
    name: str,
    reason: str,
    registry: Mapping[str, Any],
    workspace_root: Path,
    as_of: str,
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    result = build_decision_input(
        registry,
        workspace_root=workspace_root,
        as_of=as_of,
        signals=signals,
    )
    reasons = [str(item["reason"]) for item in result["rejected_signals"]]
    return _scenario(
        name,
        f"reject_signal:{reason}",
        reason in reasons and not result["execution"]["real_trading_enabled"],
        "rejected:" + ",".join(sorted(reasons)),
    )


def run_decision_replay(
    config: Mapping[str, Any], *, workspace_root: Path
) -> dict[str, Any]:
    if config.get("schema_version") != 1:
        raise ValueError("replay config schema_version must be 1")
    safety = config.get("safety")
    if not isinstance(safety, Mapping) or safety != {
        "research_only": True,
        "mutate_copies_only": True,
        "network_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
    }:
        raise ValueError("replay safety contract is not exact")

    pack_path = _resolve(workspace_root, config["source_pack_path"], "source_pack_path")
    registry_path = _resolve(workspace_root, config["registry_path"], "registry_path")
    pack = _load_json(pack_path)
    registry = load_strategy_registry(registry_path)
    as_of = str(config["as_of"])
    as_of_dt = datetime.fromisoformat(as_of)
    base_signals = _raw_signals(pack)
    rows: list[dict[str, Any]] = []

    control_safe = (
        pack["mode"] == "research_only"
        and pack["execution"]["orders"] == []
        and pack["execution"]["real_trading_enabled"] is False
        and len(pack["strategy_decision_input"]["accepted_signals"]) == 4
    )
    rows.append(
        _scenario(
            "control_pack_safe",
            "pass_with_four_research_signals_and_zero_orders",
            control_safe,
            "safe" if control_safe else "unsafe_control_pack",
        )
    )

    rows.append(
        _expect_block(
            "snapshot_quality_fail",
            "snapshot must pass",
            lambda: _validate_snapshot_context(
                {"quality_status": "fail", "as_of": "2026-08-15T12:00:00+08:00"},
                as_of=as_of_dt,
                maximum_age_calendar_days=3,
            ),
        )
    )
    rows.append(
        _expect_block(
            "snapshot_stale",
            "snapshot is stale",
            lambda: _validate_snapshot_context(
                {"quality_status": "pass", "as_of": "2026-08-10T12:00:00+08:00"},
                as_of=as_of_dt,
                maximum_age_calendar_days=3,
            ),
        )
    )
    rows.append(
        _expect_block(
            "snapshot_from_future",
            "snapshot cannot be newer",
            lambda: _validate_snapshot_context(
                {"quality_status": "pass", "as_of": "2026-08-17T12:00:00+08:00"},
                as_of=as_of_dt,
                maximum_age_calendar_days=3,
            ),
        )
    )
    rows.append(
        _expect_block(
            "market_data_stale",
            "market data is stale",
            lambda: _validate_market_context(
                date(2026, 8, 10),
                decision_date=as_of_dt.date(),
                maximum_age_calendar_days=3,
            ),
        )
    )
    rows.append(
        _expect_block(
            "unreviewed_position_role",
            "no reviewed role mapping",
            lambda: _current_sleeves(
                {
                    "cash": "0",
                    "total_assets": "1",
                    "positions": [{"fund_code": "999999", "market_value": "1"}],
                },
                {},
            ),
        )
    )

    mutations: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
        ("unregistered_strategy", "unregistered_or_version_mismatch", lambda item: item.update(strategy_id="unknown_strategy")),
        ("strategy_version_mismatch", "unregistered_or_version_mismatch", lambda item: item.update(strategy_version="9.9.9")),
        ("expired_signal", "signal_expired", lambda item: item.update(valid_until="2026-08-15T23:59:59+08:00")),
        ("future_signal", "signal_from_future", lambda item: item.update(signal_as_of="2026-08-17T00:00:00+08:00")),
        ("data_quality_fail", "data_quality_not_allowed", lambda item: item.update(data_quality={"status": "fail", "issues": ["injected"]})),
        ("risk_veto", "risk_veto", lambda item: item.update(risk={"veto": True, "reasons": ["injected"]})),
        ("invalid_evidence_hash", "invalid_evidence_hash", lambda item: item.update(evidence={"artifact_sha256": "bad"})),
    ]
    for name, reason, mutate in mutations:
        signals = deepcopy(base_signals)
        mutate(signals[0])
        rows.append(
            _expect_signal_rejection(
                name=name,
                reason=reason,
                registry=registry,
                workspace_root=workspace_root,
                as_of=as_of,
                signals=signals,
            )
        )

    drifted = deepcopy(registry)
    drifted["strategies"][0]["spec"]["sha256"] = "0" * 64
    rows.append(
        _expect_block(
            "spec_hash_drift",
            "spec hash mismatch",
            lambda: validate_strategy_registry(drifted, workspace_root=workspace_root),
        )
    )
    execution_enabled = deepcopy(registry)
    execution_enabled["strategies"][0]["gates"]["execution_enabled"] = True
    rows.append(
        _expect_block(
            "execution_flag_tampering",
            "cannot enable execution",
            lambda: validate_strategy_registry(execution_enabled, workspace_root=workspace_root),
        )
    )
    future_registry = deepcopy(registry)
    future_registry["as_of"] = "2026-08-17T00:00:00+08:00"
    rows.append(
        _expect_block(
            "future_registry",
            "registry cannot be newer",
            lambda: build_decision_input(
                future_registry,
                workspace_root=workspace_root,
                as_of=as_of,
                signals=base_signals,
            ),
        )
    )

    accepted = pack["strategy_decision_input"]["accepted_signals"]
    authorities = [
        item for item in accepted if item["target_allocation_authority"] is True
    ]
    conflict_safe = (
        len(accepted) > 1
        and len(authorities) == 1
        and authorities[0]["strategy_id"] == "dca_baseline"
        and pack["research_conclusion"]["baseline"] == "retain_dca_baseline_631"
    )
    rows.append(
        _scenario(
            "strategy_conflict_preserves_single_baseline_authority",
            "one_dca_baseline_authority",
            conflict_safe,
            f"authorities:{len(authorities)}",
        )
    )
    execution_safe = pack["execution"] == {
        "advisory_only": True,
        "selling_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }
    rows.append(
        _scenario(
            "output_execution_invariants",
            "exact_non_executable_output",
            execution_safe,
            "safe" if execution_safe else "execution_contract_changed",
        )
    )

    expected = list(config["expected_scenarios"])
    actual = [row["scenario"] for row in rows]
    scenario_set_exact = expected == actual
    passed = sum(bool(row["passed"]) for row in rows)
    return {
        "schema_version": 1,
        "replay_id": str(config["replay_id"]),
        "as_of": as_of,
        "mode": "research_only",
        "source_pack": {"path": str(config["source_pack_path"]), "sha256": _file_sha256(pack_path)},
        "registry": {"path": str(config["registry_path"]), "sha256": _file_sha256(registry_path)},
        "summary": {
            "expected_scenarios": len(expected),
            "actual_scenarios": len(rows),
            "passed": passed,
            "failed": len(rows) - passed,
            "scenario_order_and_set_exact": scenario_set_exact,
            "acceptance_gate": "passed" if passed == len(rows) and scenario_set_exact else "failed",
        },
        "scenarios": rows,
        "execution": {
            "network_used": False,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
