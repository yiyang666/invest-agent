"""Offline monthly research pipeline with a manifest-last publication gate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .explanation import (
    build_grounded_explanation,
    render_grounded_explanation_markdown,
    run_explanation_acceptance,
)
from .monthly import build_monthly_research_decision_pack
from .reporting import (
    _canonical_sha256,
    build_research_report,
    render_research_report_markdown,
)


ARTIFACT_KEYS = (
    "decision_pack_json",
    "research_report_json",
    "research_report_markdown",
    "grounded_explanation_json",
    "grounded_explanation_markdown",
    "explanation_acceptance_json",
)


def _resolve_existing(root: Path, relative: object, field: str) -> Path:
    path = _resolve_output(root, relative, field)
    if not path.is_file():
        raise ValueError(f"{field} does not exist: {relative}")
    return path


def _resolve_output(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    path = (root / relative).resolve()
    workspace = root.resolve()
    if path != workspace and workspace not in path.parents:
        raise ValueError(f"{field} escapes workspace root")
    return path


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bytes_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _load_locked_config(
    root: Path, item: Mapping[str, Any], field: str
) -> tuple[dict[str, Any], str]:
    path = _resolve_existing(root, item.get("path"), f"{field}.path")
    actual = _file_sha256(path)
    if actual != item.get("sha256"):
        raise ValueError(f"{field} sha256 mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return payload, actual


def _validate_pipeline_config(config: Mapping[str, Any], workspace_root: Path) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("monthly research pipeline config schema_version must be 1")
    expected_safety = {
        "research_only": True,
        "advisory_only": True,
        "network_allowed": False,
        "automatic_schedule_enabled": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
    }
    if config.get("safety") != expected_safety:
        raise ValueError("monthly research pipeline safety contract is not exact")
    paths = config.get("artifact_paths")
    if not isinstance(paths, Mapping) or set(paths) != set(ARTIFACT_KEYS):
        raise ValueError("monthly research pipeline artifact path set is not exact")
    resolved = [
        _resolve_output(workspace_root, paths[key], f"artifact_paths.{key}")
        for key in ARTIFACT_KEYS
    ]
    manifest = _resolve_output(
        workspace_root, config.get("manifest_path"), "manifest_path"
    )
    if len(set(resolved + [manifest])) != len(resolved) + 1:
        raise ValueError("pipeline artifact paths must be unique")


def build_monthly_research_pipeline(
    config: Mapping[str, Any], *, workspace_root: Path
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build every stage in memory; the CLI publishes the manifest last."""

    _validate_pipeline_config(config, workspace_root)
    configs = config.get("source_configs")
    if not isinstance(configs, Mapping):
        raise ValueError("monthly research pipeline requires source_configs")
    monthly_config, monthly_config_hash = _load_locked_config(
        workspace_root, configs["monthly_decision"], "monthly_decision"
    )
    report_template, report_config_hash = _load_locked_config(
        workspace_root, configs["research_report"], "research_report"
    )
    explanation_template, explanation_config_hash = _load_locked_config(
        workspace_root, configs["research_explanation"], "research_explanation"
    )
    if str(monthly_config.get("as_of")) != str(config.get("as_of")):
        raise ValueError("pipeline as_of must match monthly decision config")

    paths = {key: str(config["artifact_paths"][key]) for key in ARTIFACT_KEYS}
    pack = build_monthly_research_decision_pack(
        monthly_config, workspace_root=workspace_root
    )
    pack_bytes = _json_bytes(pack)
    pack_hash = _bytes_sha256(pack_bytes)
    pack_override = {
        "payload": pack,
        "sha256": pack_hash,
        "canonical_sha256": _canonical_sha256(pack),
    }

    report_config = copy.deepcopy(report_template)
    report_config["sources"]["decision_pack"] = {
        "path": paths["decision_pack_json"],
        "sha256": pack_hash,
    }
    report = build_research_report(
        report_config,
        workspace_root=workspace_root,
        decision_pack_override=pack_override,
    )
    report_bytes = _json_bytes(report)
    report_hash = _bytes_sha256(report_bytes)
    report_markdown = render_research_report_markdown(report).encode("utf-8")
    report_override = {
        "payload": report,
        "sha256": report_hash,
        "canonical_sha256": _canonical_sha256(report),
    }

    explanation_config = copy.deepcopy(explanation_template)
    explanation_config["source_report"] = {
        "path": paths["research_report_json"],
        "sha256": report_hash,
    }
    explanation = build_grounded_explanation(
        explanation_config,
        workspace_root=workspace_root,
        source_report_override=report_override,
    )
    explanation_bytes = _json_bytes(explanation)
    explanation_markdown = render_grounded_explanation_markdown(explanation).encode(
        "utf-8"
    )
    acceptance = run_explanation_acceptance(
        explanation_config,
        workspace_root=workspace_root,
        source_report_override=report_override,
    )
    if acceptance["summary"]["acceptance_gate"] != "passed":
        raise ValueError("grounded explanation acceptance gate failed")
    acceptance_bytes = _json_bytes(acceptance)

    artifact_bytes = {
        "decision_pack_json": pack_bytes,
        "research_report_json": report_bytes,
        "research_report_markdown": report_markdown,
        "grounded_explanation_json": explanation_bytes,
        "grounded_explanation_markdown": explanation_markdown,
        "explanation_acceptance_json": acceptance_bytes,
    }
    artifact_records = {
        key: {
            "path": paths[key],
            "sha256": _bytes_sha256(artifact_bytes[key]),
            "bytes": len(artifact_bytes[key]),
        }
        for key in ARTIFACT_KEYS
    }
    stages = [
        {
            "stage_id": "monthly_decision_pack",
            "status": "passed",
            "inputs": [str(configs["monthly_decision"]["path"])],
            "outputs": ["decision_pack_json"],
        },
        {
            "stage_id": "evidence_bound_report",
            "status": "passed",
            "inputs": ["decision_pack_json", str(configs["research_report"]["path"])],
            "outputs": ["research_report_json", "research_report_markdown"],
        },
        {
            "stage_id": "grounded_explanation",
            "status": "passed",
            "inputs": ["research_report_json", str(configs["research_explanation"]["path"])],
            "outputs": ["grounded_explanation_json", "grounded_explanation_markdown"],
        },
        {
            "stage_id": "explanation_acceptance",
            "status": "passed",
            "inputs": ["research_report_json", "grounded_explanation_json"],
            "outputs": ["explanation_acceptance_json"],
        },
    ]
    manifest = {
        "schema_version": 1,
        "pipeline_id": str(config["pipeline_id"]),
        "run_id": str(config["run_id"]),
        "as_of": str(config["as_of"]),
        "mode": "research_only",
        "publication_semantics": "manifest_last_all_stages_must_pass",
        "source_configs": {
            "monthly_decision": {
                "path": str(configs["monthly_decision"]["path"]),
                "sha256": monthly_config_hash,
            },
            "research_report": {
                "path": str(configs["research_report"]["path"]),
                "sha256": report_config_hash,
            },
            "research_explanation": {
                "path": str(configs["research_explanation"]["path"]),
                "sha256": explanation_config_hash,
            },
        },
        "stages": stages,
        "artifacts": artifact_records,
        "summary": {
            "stages_total": len(stages),
            "stages_passed": len(stages),
            "stages_failed": 0,
            "publication_gate": "passed",
            "monthly_portfolio_review_generated": True,
            "new_real_time_investment_decision": False,
        },
        "execution": {
            "network_used": False,
            "automatic_schedule_used": False,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
    manifest["reproducibility"] = {
        "pipeline_payload_sha256": _canonical_sha256(
            {
                "source_configs": manifest["source_configs"],
                "stages": stages,
                "artifacts": artifact_records,
                "execution": manifest["execution"],
            }
        )
    }
    return manifest, artifact_bytes


def write_immutable(path: Path, content: bytes) -> None:
    """Write once or accept byte-identical reruns; reject silent overwrites."""

    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError(f"refusing to overwrite changed artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)
