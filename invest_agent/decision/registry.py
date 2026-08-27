"""Deterministic strategy-registry validation and decision-input assembly.

This module deliberately has no dependency on execution adapters. It converts
versioned research signals into a common envelope and rejects signals that are
unregistered, stale, vetoed, or fail their declared data-quality contract.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_SIGNAL_FIELDS = {
    "strategy_id",
    "strategy_version",
    "signal_as_of",
    "data_cutoff",
    "valid_until",
    "data_quality",
    "risk",
    "evidence",
    "payload",
}


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 date-time string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 date-time string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _workspace_file(workspace_root: Path, relative_path: object, field: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError(f"{field} must be a non-empty relative path")
    candidate = (workspace_root / relative_path).resolve()
    root = workspace_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{field} escapes workspace root")
    if not candidate.is_file():
        raise ValueError(f"{field} does not exist: {relative_path}")
    return candidate


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_strategy_registry(path: Path) -> dict[str, Any]:
    """Load a registry JSON file without mutating or resolving any strategy."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("strategy registry must be a JSON object")
    return payload


def validate_strategy_registry(
    registry: Mapping[str, Any], *, workspace_root: Path
) -> dict[tuple[str, str], Mapping[str, Any]]:
    """Validate safety invariants, exact versions, and evidence file hashes."""

    if registry.get("schema_version") != 3:
        raise ValueError("strategy registry schema_version must be 3")
    if registry.get("mode") != "research_only":
        raise ValueError("strategy registry must remain research_only")
    _parse_datetime(registry.get("as_of"), "registry.as_of")

    terminology = registry.get("terminology")
    if not isinstance(terminology, Mapping):
        raise ValueError("strategy registry terminology must be an object")
    anchor = terminology.get("allocation_anchor_631")
    benchmark_entries = [
        (key, value)
        for key, value in terminology.items()
        if isinstance(key, str) and key.startswith("dca_baseline@")
    ]
    benchmark = benchmark_entries[0][1] if len(benchmark_entries) == 1 else None
    overlay = terminology.get("tactical_overlay")
    if not all(isinstance(item, Mapping) for item in (anchor, benchmark, overlay)):
        raise ValueError("strategy registry terminology entries are incomplete")
    if anchor.get("mandatory_execution_schedule") is not False:
        raise ValueError("631 allocation anchor cannot mandate one execution schedule")
    if benchmark.get("kind") != "comparison_benchmark_strategy":
        raise ValueError("dca_baseline must remain the comparison benchmark strategy")
    if overlay.get("may_deviate_from_631_during_execution") is not True:
        raise ValueError("tactical overlays must explicitly allow bounded execution deviation")
    if overlay.get("requires_matched_cashflow_comparator") is not True:
        raise ValueError("tactical overlays require a matched-cashflow comparator")
    if overlay.get("risk_layer_has_veto") is not True:
        raise ValueError("risk layer must retain veto over tactical overlays")

    contract = registry.get("decision_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("decision_contract must be an object")
    if contract.get("unregistered_strategy") != "reject":
        raise ValueError("unregistered strategies must be rejected")
    if contract.get("version_match") != "exact":
        raise ValueError("strategy version matching must be exact")
    if contract.get("spec_hash_match_required") is not True:
        raise ValueError("strategy spec hash verification must be required")
    if contract.get("order_intent_generation_allowed") is not False:
        raise ValueError("registry cannot enable order-intent generation")
    if set(contract.get("required_signal_fields", [])) != REQUIRED_SIGNAL_FIELDS:
        raise ValueError("required signal fields do not match the decision contract")

    allowed_quality = contract.get("accepted_data_quality_statuses")
    if not isinstance(allowed_quality, list) or not allowed_quality:
        raise ValueError("accepted_data_quality_statuses must be a non-empty list")

    entries = registry.get("strategies")
    if not isinstance(entries, list) or not entries:
        raise ValueError("strategy registry must contain strategies")

    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    target_authorities = 0
    for item in entries:
        if not isinstance(item, Mapping):
            raise ValueError("strategy registry entries must be objects")
        strategy_id = item.get("strategy_id")
        version = item.get("strategy_version")
        if not isinstance(strategy_id, str) or not isinstance(version, str):
            raise ValueError("strategy identity must contain string id and version")
        key = (strategy_id, version)
        if key in index:
            raise ValueError(f"duplicate strategy registration: {strategy_id}@{version}")
        if item.get("mode") != "research_only":
            raise ValueError(f"{strategy_id}@{version} must remain research_only")

        gates = item.get("gates")
        permissions = item.get("decision_permissions")
        if not isinstance(gates, Mapping) or not isinstance(permissions, Mapping):
            raise ValueError(f"{strategy_id}@{version} is missing gates or permissions")
        if gates.get("production_approved") is not False:
            raise ValueError(f"{strategy_id}@{version} cannot be production approved")
        if gates.get("execution_enabled") is not False:
            raise ValueError(f"{strategy_id}@{version} cannot enable execution")
        if permissions.get("order_intent_generation_allowed") is not False:
            raise ValueError(f"{strategy_id}@{version} cannot generate order intents")
        if permissions.get("target_allocation_authority") is True:
            target_authorities += 1
            if item.get("role") != "benchmark_and_personal_target":
                raise ValueError("only the benchmark may be target-allocation authority")

        spec = item.get("spec")
        evidence = item.get("evidence")
        if not isinstance(spec, Mapping) or not isinstance(evidence, Mapping):
            raise ValueError(f"{strategy_id}@{version} is missing spec or evidence")
        spec_path = _workspace_file(workspace_root, spec.get("path"), "spec.path")
        expected_spec_hash = spec.get("sha256")
        if not isinstance(expected_spec_hash, str) or not SHA256_RE.fullmatch(expected_spec_hash):
            raise ValueError(f"{strategy_id}@{version} has an invalid spec sha256")
        if _file_sha256(spec_path) != expected_spec_hash:
            raise ValueError(f"{strategy_id}@{version} spec hash mismatch")
        spec_payload = json.loads(spec_path.read_text(encoding="utf-8"))
        if (spec_payload.get("strategy_id"), spec_payload.get("strategy_version")) != key:
            raise ValueError(f"{strategy_id}@{version} spec identity mismatch")

        report_path = _workspace_file(
            workspace_root, evidence.get("primary_report_path"), "evidence.primary_report_path"
        )
        expected_report_hash = evidence.get("primary_report_sha256")
        if not isinstance(expected_report_hash, str) or not SHA256_RE.fullmatch(expected_report_hash):
            raise ValueError(f"{strategy_id}@{version} has an invalid report sha256")
        if _file_sha256(report_path) != expected_report_hash:
            raise ValueError(f"{strategy_id}@{version} evidence report hash mismatch")
        index[key] = item

    if target_authorities != 1:
        raise ValueError("registry must contain exactly one target-allocation authority")
    authority = next(
        item
        for item in entries
        if item["decision_permissions"]["target_allocation_authority"] is True
    )
    expected_benchmark_key = (
        f"{authority['strategy_id']}@{authority['strategy_version']}"
    )
    if benchmark_entries[0][0] != expected_benchmark_key:
        raise ValueError("terminology benchmark must match target-allocation authority")
    return index


def _reject(signal: object, reason: str) -> dict[str, Any]:
    identity = signal if isinstance(signal, Mapping) else {}
    return {
        "strategy_id": identity.get("strategy_id"),
        "strategy_version": identity.get("strategy_version"),
        "reason": reason,
    }


def build_decision_input(
    registry: Mapping[str, Any],
    *,
    workspace_root: Path,
    as_of: str,
    signals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic advisory envelope from registry-authorized signals."""

    index = validate_strategy_registry(registry, workspace_root=workspace_root)
    decision_time = _parse_datetime(as_of, "as_of")
    registry_time = _parse_datetime(registry.get("as_of"), "registry.as_of")
    if registry_time > decision_time:
        raise ValueError("strategy registry cannot be newer than the decision input")
    contract = registry["decision_contract"]
    allowed_quality = set(contract["accepted_data_quality_statuses"])
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for original in signals:
        signal = deepcopy(dict(original))
        missing = sorted(REQUIRED_SIGNAL_FIELDS - set(signal))
        if missing:
            rejected.append(_reject(signal, f"missing_required_fields:{','.join(missing)}"))
            continue
        key = (signal.get("strategy_id"), signal.get("strategy_version"))
        registration = index.get(key)
        if registration is None:
            rejected.append(_reject(signal, "unregistered_or_version_mismatch"))
            continue
        permissions = registration["decision_permissions"]
        if permissions.get("advisory_signal_allowed") is not True:
            rejected.append(_reject(signal, "advisory_signal_not_allowed"))
            continue
        try:
            signal_time = _parse_datetime(signal["signal_as_of"], "signal_as_of")
            data_cutoff = _parse_datetime(signal["data_cutoff"], "data_cutoff")
            valid_until = _parse_datetime(signal["valid_until"], "valid_until")
        except ValueError as exc:
            rejected.append(_reject(signal, f"invalid_time:{exc}"))
            continue
        if data_cutoff > signal_time:
            rejected.append(_reject(signal, "data_cutoff_after_signal"))
            continue
        if signal_time > decision_time:
            rejected.append(_reject(signal, "signal_from_future"))
            continue
        if valid_until < decision_time:
            rejected.append(_reject(signal, "signal_expired"))
            continue

        data_quality = signal["data_quality"]
        if not isinstance(data_quality, Mapping):
            rejected.append(_reject(signal, "invalid_data_quality"))
            continue
        if data_quality.get("status") not in allowed_quality:
            rejected.append(_reject(signal, "data_quality_not_allowed"))
            continue
        risk = signal["risk"]
        if not isinstance(risk, Mapping) or not isinstance(risk.get("veto"), bool):
            rejected.append(_reject(signal, "invalid_risk_assessment"))
            continue
        if risk["veto"]:
            rejected.append(_reject(signal, "risk_veto"))
            continue
        evidence = signal["evidence"]
        artifact_hash = evidence.get("artifact_sha256") if isinstance(evidence, Mapping) else None
        if not isinstance(artifact_hash, str) or not SHA256_RE.fullmatch(artifact_hash):
            rejected.append(_reject(signal, "invalid_evidence_hash"))
            continue

        accepted.append(
            {
                **signal,
                "registry_role": registration["role"],
                "registry_evidence_status": registration["evidence"]["status"],
                "target_allocation_authority": permissions["target_allocation_authority"],
                "execution_enabled": False,
            }
        )

    accepted.sort(key=lambda item: (item["strategy_id"], item["strategy_version"]))
    rejected.sort(key=lambda item: (str(item["strategy_id"]), str(item["strategy_version"]), item["reason"]))
    registry_bytes = json.dumps(
        registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "as_of": as_of,
        "mode": "research_only",
        "registry": {
            "registry_id": registry["registry_id"],
            "as_of": registry["as_of"],
            "sha256": hashlib.sha256(registry_bytes).hexdigest(),
        },
        "accepted_signals": accepted,
        "rejected_signals": rejected,
        "execution": {
            "advisory_only": True,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
        },
    }
