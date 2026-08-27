"""Generate and validate discovery reports for unknown strategy dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Mapping


ID_RE = re.compile(r"^[a-z][a-z0-9_]+$")
FUND_RE = re.compile(r"^[0-9]{6}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
PLACEHOLDERS = {"", "COMPLETE_DURING_DISCOVERY", "UNDECIDED", "unknown"}
SUPPORTED_PRIMITIVES = {
    "calendar_dca",
    "target_gap",
    "absolute_trend",
    "moving_average",
    "relative_strength",
    "portfolio_drawdown",
    "tiered_drawdown_budget",
    "sleeve_drawdown",
}


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("TOML document must be an object")
    return payload


def _q(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _source_block(source_id: str, authority: str, supports: list[str]) -> str:
    source_type = (
        "official_fund_or_index_document"
        if authority == "official"
        else "independent_secondary_validation"
    )
    return "\n".join(
        [
            "[[sources]]",
            f"source_id = {_q(source_id)}",
            f"authority = {_q(authority)}",
            f"source_type = {_q(source_type)}",
            'location_or_archive = "COMPLETE_DURING_DISCOVERY"',
            'retrieved_at = "COMPLETE_DURING_DISCOVERY"',
            'content_sha256 = "COMPLETE_DURING_DISCOVERY"',
            f"supports = {_q(supports)}",
        ]
    )


def _fund_block(fund_code: str, sleeve_id: str, source_ids: list[str]) -> str:
    return "\n".join(
        [
            "[[funds]]",
            f"fund_code = {_q(fund_code)}",
            f"sleeve_id = {_q(sleeve_id)}",
            'fund_name = "COMPLETE_DURING_DISCOVERY"',
            'share_class = "COMPLETE_DURING_DISCOVERY"',
            'tracking_exposure = "COMPLETE_DURING_DISCOVERY"',
            'currency = "COMPLETE_DURING_DISCOVERY"',
            'currency_hedged = "UNDECIDED"',
            'identity_status = "unknown"',
            'purchase_status = "unknown"',
            'contract_status = "unknown"',
            'execution_rule_status = "unknown"',
            'local_nav_status = "unknown"',
            'local_nav_start_date = ""',
            'local_nav_end_date = ""',
            'visibility_status = "unknown"',
            'evidence_plan = "UNDECIDED"',
            'proxy_id = ""',
            'basis_risks = []',
            f"source_ids = {_q(source_ids)}",
        ]
    )


def _sleeve_block(sleeve_id: str, source_ids: list[str]) -> str:
    return "\n".join(
        [
            "[[sleeves]]",
            f"sleeve_id = {_q(sleeve_id)}",
            'definition_status = "draft"',
            'asset_class = "COMPLETE_DURING_DISCOVERY"',
            'region = "COMPLETE_DURING_DISCOVERY"',
            'economic_exposure = "COMPLETE_DURING_DISCOVERY"',
            'benchmark_index = "COMPLETE_DURING_DISCOVERY"',
            'currency = "COMPLETE_DURING_DISCOVERY"',
            'currency_hedged = "UNDECIDED"',
            'strategy_role = "UNDECIDED"',
            'maximum_strategy_weight_pct = 0',
            'maximum_portfolio_weight_pct = 0',
            'overlap_assessment = "COMPLETE_DURING_DISCOVERY"',
            'stress_mapping_status = "pending"',
            f"source_ids = {_q(source_ids)}",
        ]
    )


def _primitive_block(primitive: str) -> str:
    return "\n".join(
        [
            "[[primitives]]",
            f"primitive = {_q(primitive)}",
            'data_contract_status = "pending"',
            'implementation_status = "pending"',
            'test_status = "pending"',
        ]
    )


def _data_input_block(input_id: str, source_ids: list[str]) -> str:
    return "\n".join(
        [
            "[[data_inputs]]",
            f"input_id = {_q(input_id)}",
            'source_status = "pending"',
            'local_store_status = "pending"',
            'point_in_time_status = "unknown"',
            'missing_data_action = "block"',
            f"source_ids = {_q(source_ids)}",
        ]
    )


def _proposal_dependencies(proposal: Mapping[str, Any]) -> dict[str, Any]:
    discovery = proposal.get("discovery")
    if not isinstance(discovery, Mapping):
        raise ValueError("proposal is missing discovery table")
    routes = discovery.get("new_fund_routes", [])
    sleeves = discovery.get("new_sleeves", [])
    inputs = discovery.get("novel_data_inputs", [])
    signals = proposal.get("signals", [])
    if not isinstance(routes, list) or not isinstance(sleeves, list) or not isinstance(inputs, list):
        raise ValueError("proposal discovery dependencies must be lists")
    primitives = sorted(
        {
            str(item.get("primitive"))
            for item in signals
            if isinstance(item, Mapping)
            and str(item.get("primitive")) not in SUPPORTED_PRIMITIVES
        }
    )
    normalized_routes = []
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("new fund routes must be inline tables")
        normalized_routes.append(
            {"fund_code": str(route.get("fund_code", "")), "sleeve_id": str(route.get("sleeve_id", ""))}
        )
    return {
        "strategy_id": str(proposal.get("strategy_id", "")),
        "strategy_version": str(proposal.get("strategy_version", "")),
        "routes": normalized_routes,
        "sleeves": sorted({str(value) for value in sleeves}),
        "inputs": sorted({str(value) for value in inputs}),
        "primitives": primitives,
    }


def render_report(proposal_path: Path, proposal: Mapping[str, Any]) -> str:
    dependencies = _proposal_dependencies(proposal)
    if not any(dependencies[key] for key in ("routes", "sleeves", "inputs", "primitives")):
        raise ValueError("proposal has no dependency requiring discovery")
    source_blocks: list[str] = []
    fund_blocks: list[str] = []
    sleeve_blocks: list[str] = []
    known_source_ids: set[str] = set()
    for route in dependencies["routes"]:
        code = route["fund_code"]
        official = f"fund_{code}_official"
        secondary = f"fund_{code}_secondary"
        source_blocks.extend(
            [
                _source_block(official, "official", [f"fund:{code}:identity", f"fund:{code}:contract"]),
                _source_block(secondary, "secondary", [f"fund:{code}:identity", f"fund:{code}:data"]),
            ]
        )
        known_source_ids.update({official, secondary})
        fund_blocks.append(_fund_block(code, route["sleeve_id"], [official, secondary]))
    for sleeve_id in dependencies["sleeves"]:
        source_id = f"sleeve_{sleeve_id}_official"
        if source_id not in known_source_ids:
            source_blocks.append(
                _source_block(source_id, "official", [f"sleeve:{sleeve_id}:definition"])
            )
            known_source_ids.add(source_id)
        sleeve_blocks.append(_sleeve_block(sleeve_id, [source_id]))
    data_input_source_ids: dict[str, list[str]] = {}
    for input_id in dependencies["inputs"]:
        if ID_RE.fullmatch(input_id) is None:
            raise ValueError("novel data input IDs must use snake_case")
        official = f"data_{input_id}_official"
        secondary = f"data_{input_id}_secondary"
        source_blocks.extend(
            [
                _source_block(official, "official", [f"data:{input_id}:definition"]),
                _source_block(secondary, "secondary", [f"data:{input_id}:validation"]),
            ]
        )
        data_input_source_ids[input_id] = [official, secondary]
    template = (_skill_root() / "assets/strategy-discovery.template.toml").read_text(
        encoding="utf-8"
    )
    replacements = {
        "{{STRATEGY_ID}}": dependencies["strategy_id"],
        "{{STRATEGY_VERSION}}": dependencies["strategy_version"],
        "{{GENERATED_FROM}}": str(proposal_path),
        "{{SOURCE_BLOCKS}}": "\n\n".join(source_blocks),
        "{{FUND_BLOCKS}}": "\n\n".join(fund_blocks),
        "{{SLEEVE_BLOCKS}}": "\n\n".join(sleeve_blocks),
        "{{PRIMITIVE_BLOCKS}}": "\n\n".join(
            _primitive_block(item) for item in dependencies["primitives"]
        ),
        "{{DATA_INPUT_BLOCKS}}": "\n\n".join(
            _data_input_block(item, data_input_source_ids[item])
            for item in dependencies["inputs"]
        ),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def _filled_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() not in PLACEHOLDERS


def validate_report(
    report: Mapping[str, Any],
    *,
    require_complete: bool = False,
    require_research_ready: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    status = report.get("status")
    if require_complete and require_research_ready:
        errors.append("choose either complete or research-ready validation, not both")
    strict = status == "complete" or require_complete
    research_gate = strict or status == "research_ready" or require_research_ready
    if report.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    strategy_id = str(report.get("strategy_id", ""))
    version = str(report.get("strategy_version", ""))
    if ID_RE.fullmatch(strategy_id) is None:
        errors.append("strategy_id must use snake_case")
    if SEMVER_RE.fullmatch(version) is None:
        errors.append("strategy_version must use semantic versioning")
    if status not in {"draft", "research_ready", "complete", "blocked"}:
        errors.append("status must be draft, research_ready, complete, or blocked")
    if require_complete and status != "complete":
        errors.append("discovery report must be complete")
    if require_research_ready and status not in {"research_ready", "complete"}:
        errors.append("discovery report must be research_ready or complete")

    research = report.get("research")
    if not isinstance(research, Mapping):
        errors.append("research table is required")
        research = {}
    if research.get("remote_data_direct_backtest") is not False:
        errors.append("remote data cannot directly serve backtests")
    if research.get("third_party_install_required") is True and research.get(
        "third_party_install_approved"
    ) is not True:
        errors.append("third-party installation requires explicit approval")
    if strict and research.get("network_research_status") not in {"not_required", "complete"}:
        errors.append("network research must be complete or explicitly not required")
    elif research_gate and research.get("network_research_status") not in {
        "not_required", "partial", "complete"
    }:
        errors.append("research-ready discovery requires completed or explicit partial research")

    decisions = report.get("decisions")
    if not isinstance(decisions, Mapping):
        errors.append("decisions table is required")
        decisions = {}
    unresolved = decisions.get("unresolved")
    if not isinstance(unresolved, list):
        errors.append("decisions.unresolved must be a list")
        unresolved = []
    if research_gate and unresolved:
        errors.append("discovery report has unresolved user decisions")

    sources = report.get("sources", [])
    if not isinstance(sources, list):
        errors.append("sources must be a list")
        sources = []
    source_index: dict[str, Mapping[str, Any]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            errors.append(f"sources[{index}] must be a table")
            continue
        source_id = str(source.get("source_id", ""))
        if ID_RE.fullmatch(source_id) is None or source_id in source_index:
            errors.append(f"sources[{index}].source_id is invalid or duplicated")
            continue
        source_index[source_id] = source
        if source.get("authority") not in {"official", "secondary"}:
            errors.append(f"sources[{index}].authority is invalid")
        if research_gate:
            if not _filled_text(source.get("location_or_archive")):
                errors.append(f"sources[{index}] location/archive is incomplete")
            if not _filled_text(source.get("retrieved_at")):
                errors.append(f"sources[{index}] retrieved_at is incomplete")
            if SHA_RE.fullmatch(str(source.get("content_sha256", ""))) is None:
                errors.append(f"sources[{index}] requires archived content SHA-256")

    evidence_classes: list[str] = []
    funds = report.get("funds", [])
    if not isinstance(funds, list):
        errors.append("funds must be a list")
        funds = []
    seen_funds: set[str] = set()
    for index, fund in enumerate(funds):
        if not isinstance(fund, Mapping):
            errors.append(f"funds[{index}] must be a table")
            continue
        code = str(fund.get("fund_code", ""))
        if FUND_RE.fullmatch(code) is None or code in seen_funds:
            errors.append(f"funds[{index}].fund_code is invalid or duplicated")
        seen_funds.add(code)
        if ID_RE.fullmatch(str(fund.get("sleeve_id", ""))) is None:
            errors.append(f"funds[{index}].sleeve_id must use snake_case")
        plan = str(fund.get("evidence_plan", ""))
        if research_gate:
            for field in ("fund_name", "share_class", "tracking_exposure", "currency"):
                if not _filled_text(fund.get(field)):
                    errors.append(f"funds[{index}].{field} is incomplete")
            if not isinstance(fund.get("currency_hedged"), bool):
                errors.append(f"funds[{index}].currency_hedged must be true or false")
            if fund.get("identity_status") != "verified":
                errors.append(f"funds[{index}] identity must be verified")
            if fund.get("purchase_status") not in {
                "open", "limited", "suspended", "closed", "unsupported"
            }:
                errors.append(f"funds[{index}] purchase status is unresolved")
            if fund.get("contract_status") not in {"verified", "research_assumption"}:
                errors.append(f"funds[{index}] contract status is unresolved")
            if fund.get("execution_rule_status") not in {"verified", "counterfactual_only"}:
                errors.append(f"funds[{index}] execution rules are unresolved")
            nav_status = fund.get("local_nav_status")
            if nav_status not in {"complete", "partial", "missing"}:
                errors.append(f"funds[{index}] local NAV status is unresolved")
            if fund.get("visibility_status") not in {
                "strict_point_in_time", "historical_visibility_assumed", "unavailable"
            }:
                errors.append(f"funds[{index}] visibility status is unresolved")
            if plan not in {
                "fund_history", "short_fund_history", "index_proxy", "older_fund_proxy", "forward_only"
            }:
                errors.append(f"funds[{index}] evidence plan is unresolved")
            if nav_status in {"complete", "partial"}:
                if DATE_RE.fullmatch(str(fund.get("local_nav_start_date", ""))) is None:
                    errors.append(f"funds[{index}] local NAV start date is required")
                if DATE_RE.fullmatch(str(fund.get("local_nav_end_date", ""))) is None:
                    errors.append(f"funds[{index}] local NAV end date is required")
            if plan in {"index_proxy", "older_fund_proxy"}:
                if not _filled_text(fund.get("proxy_id")):
                    errors.append(f"funds[{index}] proxy_id is required")
                if not isinstance(fund.get("basis_risks"), list) or not fund.get("basis_risks"):
                    errors.append(f"funds[{index}] proxy basis risks are required")
            source_ids = fund.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                errors.append(f"funds[{index}] source_ids are required")
            else:
                linked = [source_index.get(str(item)) for item in source_ids]
                if any(item is None for item in linked):
                    errors.append(f"funds[{index}] references an unknown source")
                if strict:
                    authorities = {str(item.get("authority")) for item in linked if item}
                    if authorities != {"official", "secondary"}:
                        errors.append(f"funds[{index}] requires official and secondary evidence")
        if plan in {"index_proxy", "older_fund_proxy"}:
            evidence_classes.append("proxy_research_only")
        elif plan == "forward_only":
            evidence_classes.append("forward_observation_only")
        elif plan == "short_fund_history":
            evidence_classes.append("short_window_only")
        elif plan == "fund_history":
            evidence_classes.append("fund_history_ready")

    sleeves = report.get("sleeves", [])
    if not isinstance(sleeves, list):
        errors.append("sleeves must be a list")
        sleeves = []
    seen_sleeves: set[str] = set()
    for index, sleeve in enumerate(sleeves):
        if not isinstance(sleeve, Mapping):
            errors.append(f"sleeves[{index}] must be a table")
            continue
        sleeve_id = str(sleeve.get("sleeve_id", ""))
        if ID_RE.fullmatch(sleeve_id) is None or sleeve_id in seen_sleeves:
            errors.append(f"sleeves[{index}].sleeve_id is invalid or duplicated")
        seen_sleeves.add(sleeve_id)
        if research_gate:
            if sleeve.get("definition_status") != "frozen":
                errors.append(f"sleeves[{index}] definition must be frozen")
            for field in (
                "asset_class", "region", "economic_exposure", "benchmark_index", "currency", "overlap_assessment"
            ):
                if not _filled_text(sleeve.get(field)):
                    errors.append(f"sleeves[{index}].{field} is incomplete")
            if not isinstance(sleeve.get("currency_hedged"), bool):
                errors.append(f"sleeves[{index}].currency_hedged must be true or false")
            if sleeve.get("strategy_role") not in {"tactical_overlay", "policy_candidate"}:
                errors.append(f"sleeves[{index}] strategy role is unresolved")
            strategy_weight = sleeve.get("maximum_strategy_weight_pct")
            portfolio_weight = sleeve.get("maximum_portfolio_weight_pct")
            if (
                isinstance(strategy_weight, bool)
                or not isinstance(strategy_weight, (int, float))
                or isinstance(portfolio_weight, bool)
                or not isinstance(portfolio_weight, (int, float))
                or not 0 < float(strategy_weight) <= float(portfolio_weight) <= 100
            ):
                errors.append(f"sleeves[{index}] weight caps are invalid")
            stress_status = sleeve.get("stress_mapping_status")
            if strict and stress_status != "specified":
                errors.append(f"sleeves[{index}] stress mapping is incomplete")
            elif not strict and stress_status not in {
                "specified", "deferred_for_exploratory_backtest"
            }:
                errors.append(f"sleeves[{index}] stress mapping status is unresolved")
            source_ids = sleeve.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                errors.append(f"sleeves[{index}] source_ids are required")
            elif not any(
                source_index.get(str(item), {}).get("authority") == "official"
                for item in source_ids
            ):
                errors.append(f"sleeves[{index}] requires an official definition source")

    primitives = report.get("primitives", [])
    if not isinstance(primitives, list):
        errors.append("primitives must be a list")
        primitives = []
    for index, primitive in enumerate(primitives):
        if not isinstance(primitive, Mapping) or not _filled_text(primitive.get("primitive")):
            errors.append(f"primitives[{index}] is invalid")
        elif research_gate and primitive.get("data_contract_status") != "specified":
            errors.append(f"primitives[{index}] data contract is incomplete")

    inputs = report.get("data_inputs", [])
    if not isinstance(inputs, list):
        errors.append("data_inputs must be a list")
        inputs = []
    for index, item in enumerate(inputs):
        if not isinstance(item, Mapping) or not _filled_text(item.get("input_id")):
            errors.append(f"data_inputs[{index}] is invalid")
            continue
        if research_gate:
            if strict and item.get("source_status") != "verified":
                errors.append(f"data_inputs[{index}] source is not verified")
            elif not strict and item.get("source_status") not in {
                "verified", "deferred_for_stage"
            }:
                errors.append(f"data_inputs[{index}] source status is unresolved")
            if strict and item.get("local_store_status") != "ready":
                errors.append(f"data_inputs[{index}] is not ready in local store")
            elif not strict and item.get("local_store_status") not in {
                "ready", "deferred_for_stage"
            }:
                errors.append(f"data_inputs[{index}] local-store status is unresolved")
            point_in_time = item.get("point_in_time_status")
            if strict and point_in_time not in {
                "strict_point_in_time", "historical_visibility_assumed"
            }:
                errors.append(f"data_inputs[{index}] point-in-time status is unresolved")
            elif not strict and point_in_time not in {
                "strict_point_in_time", "historical_visibility_assumed", "deferred_for_stage"
            }:
                errors.append(f"data_inputs[{index}] point-in-time status is unresolved")
            if item.get("missing_data_action") not in {
                "block", "no_signal", "hold_cash", "variant_excluded"
            }:
                errors.append(f"data_inputs[{index}] missing-data behavior is unsafe")
            source_ids = item.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids:
                errors.append(f"data_inputs[{index}] source_ids are required")
            else:
                linked = [source_index.get(str(source_id)) for source_id in source_ids]
                if any(source is None for source in linked):
                    errors.append(f"data_inputs[{index}] references an unknown source")
                if strict:
                    authorities = {str(source.get("authority")) for source in linked if source}
                    if authorities != {"official", "secondary"}:
                        errors.append(
                            f"data_inputs[{index}] requires official and secondary evidence"
                        )

    if status == "draft":
        warnings.append("continue autonomous discovery before requesting user decisions")
    if status == "research_ready":
        warnings.append(
            "exploratory implementation only; counterfactual execution assumptions and deferred inputs must remain labeled"
        )
    if status == "blocked":
        warnings.append("discovery is blocked; keep the strategy out of implementation")
    evidence_class = "undetermined"
    if evidence_classes:
        evidence_class = "fund_history_ready"
        for candidate in (
            "forward_observation_only", "proxy_research_only", "short_window_only"
        ):
            if candidate in evidence_classes:
                evidence_class = candidate
                break
    return {
        "status": "ok" if not errors else "error",
        "strategy": f"{strategy_id}@{version}" if strategy_id and version else None,
        "discovery_status": status,
        "errors": errors,
        "warnings": warnings,
        "unresolved_user_decisions": unresolved,
        "evidence_class": evidence_class,
        "ready_for_implementation": not errors and status in {"research_ready", "complete"},
        "ready_for_promotion": not errors and status == "complete",
    }


def command_new(args: argparse.Namespace) -> dict[str, Any]:
    proposal_path = args.proposal.resolve()
    proposal = _load(proposal_path)
    output = args.output
    if output is None:
        version_tag = str(proposal.get("strategy_version", "")).replace(".", "_")
        output = (
            args.workspace_root.resolve()
            / "strategies/proposals"
            / f"{proposal.get('strategy_id')}_discovery_v{version_tag}.toml"
        )
    output = output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing discovery report: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(proposal_path, proposal), encoding="utf-8")
    return {"status": "created", "path": str(output), "next": "research_fill_validate"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    new = subparsers.add_parser("new", help="create a discovery report from one proposal")
    new.add_argument("--proposal", type=Path, required=True)
    new.add_argument("--workspace-root", type=Path, default=Path("."))
    new.add_argument("--output", type=Path)
    validate = subparsers.add_parser("validate", help="validate one discovery report")
    validate.add_argument("report", type=Path)
    validate.add_argument("--workspace-root", type=Path, default=Path("."))
    validate.add_argument("--require-complete", action="store_true")
    validate.add_argument("--require-research-ready", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "new":
            payload = command_new(args)
            code = 0
        else:
            root = args.workspace_root.resolve()
            if not (root / "config/investment_policy.yaml").is_file():
                raise ValueError("workspace root is missing investment policy")
            report_path = args.report.resolve()
            payload = validate_report(
                _load(report_path),
                require_complete=args.require_complete,
                require_research_ready=args.require_research_ready,
            )
            payload["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
            code = 0 if payload["status"] == "ok" else 1
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        payload = {"status": "error", "message": str(exc)}
        code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
