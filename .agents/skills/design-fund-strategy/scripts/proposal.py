"""Create, validate, and compile deterministic fund-strategy proposals."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any, Mapping

from discovery import validate_report as validate_discovery_report


STRATEGY_ID_RE = re.compile(r"^[a-z][a-z0-9_]+$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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
PLACEHOLDER_TEXTS = {
    "replace_me",
    "DECIDE_BEFORE_BACKTEST",
    "用一句可证伪的话说明为什么该策略可能优于相同现金流的631。",
}
REQUIRED_SECTIONS = (
    "capital",
    "stable",
    "flexible",
    "data",
    "discovery",
    "risk",
    "comparators",
    "acceptance",
    "signals",
    "actions",
)


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("proposal must be a TOML object")
    return payload


def _policy_scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*([^#\s]+)", text)
    if match is None:
        raise ValueError(f"investment policy is missing {key}")
    return match.group(1).strip('"\'')


def _project_context(root: Path) -> dict[str, Any]:
    policy_path = root / "config/investment_policy.yaml"
    registry_path = root / "strategies/registry.json"
    policy_text = policy_path.read_text(encoding="utf-8")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    benchmark_keys = sorted(
        key
        for key in registry.get("terminology", {})
        if isinstance(key, str) and key.startswith("dca_baseline@")
    )
    if len(benchmark_keys) != 1:
        raise ValueError("strategy registry must expose exactly one dca_baseline benchmark")
    return {
        "workspace_root": root,
        "benchmark": _policy_scalar(policy_text, "comparison_benchmark"),
        "registry_benchmark": benchmark_keys[0],
        "committed_cny": int(_policy_scalar(policy_text, "stable_base_contribution_cny")),
        "hard_cap_cny": int(_policy_scalar(policy_text, "total_monthly_hard_cap_cny")),
        "single_fund_pct": float(_policy_scalar(policy_text, "max_single_fund_weight_pct")),
        "theme_pct": float(_policy_scalar(policy_text, "max_theme_weight_pct")),
        "target_drawdown_pct": float(_policy_scalar(policy_text, "target_max_portfolio_drawdown_pct")),
        "stress_drawdown_pct": float(_policy_scalar(policy_text, "stress_limit_portfolio_drawdown_pct")),
        "max_rebalances": int(_policy_scalar(policy_text, "max_active_rebalances_per_month")),
    }


def _number(value: object, field: str, errors: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field} must be a number fixed before backtest")
        return None
    return float(value)


def _required_text(payload: Mapping[str, Any], field: str, errors: list[str]) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or value in PLACEHOLDER_TEXTS:
        errors.append(f"{field} must be completed")
        return ""
    return value.strip()


def validate_proposal(
    proposal: Mapping[str, Any], *, context: Mapping[str, Any], require_frozen: bool = False
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for section in REQUIRED_SECTIONS:
        if section not in proposal:
            errors.append(f"missing section: {section}")

    if proposal.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    strategy_id = _required_text(proposal, "strategy_id", errors)
    version = _required_text(proposal, "strategy_version", errors)
    _required_text(proposal, "title", errors)
    _required_text(proposal, "hypothesis", errors)
    if strategy_id and STRATEGY_ID_RE.fullmatch(strategy_id) is None:
        errors.append("strategy_id must use snake_case and start with a letter")
    if version and SEMVER_RE.fullmatch(version) is None:
        errors.append("strategy_version must use semantic versioning")
    if proposal.get("mode") != "research_only":
        errors.append("mode must remain research_only")
    if proposal.get("allocation_anchor") != "allocation_anchor_631":
        errors.append("allocation_anchor must remain allocation_anchor_631")
    benchmark = proposal.get("comparison_benchmark")
    if benchmark != context["benchmark"] or benchmark != context["registry_benchmark"]:
        errors.append("comparison_benchmark must match the current policy and registry benchmark")
    if proposal.get("scope") != "new_money_only":
        errors.append("current deterministic engine supports custom strategies only as new_money_only")
    if proposal.get("allow_selling") is not False:
        errors.append("selling is not supported by the current custom-strategy research path")
    if proposal.get("allow_leverage") is not False:
        errors.append("leverage is prohibited")

    capital = proposal.get("capital", {})
    stable = proposal.get("stable", {})
    flexible = proposal.get("flexible", {})
    if isinstance(capital, Mapping) and isinstance(stable, Mapping) and isinstance(flexible, Mapping):
        committed = _number(capital.get("monthly_committed_cny"), "capital.monthly_committed_cny", errors)
        discretionary = _number(capital.get("monthly_discretionary_max_cny"), "capital.monthly_discretionary_max_cny", errors)
        hard_cap = _number(capital.get("monthly_hard_cap_cny"), "capital.monthly_hard_cap_cny", errors)
        stable_base = _number(stable.get("base_amount_cny"), "stable.base_amount_cny", errors)
        flexible_base = _number(flexible.get("base_amount_cny"), "flexible.base_amount_cny", errors)
        flexible_extra = _number(flexible.get("discretionary_max_cny"), "flexible.discretionary_max_cny", errors)
        if committed is not None and committed != context["committed_cny"]:
            errors.append("committed monthly cash must match investment policy")
        if hard_cap is not None and hard_cap != context["hard_cap_cny"]:
            errors.append("monthly hard cap must match investment policy")
        if None not in (stable_base, flexible_base, committed) and stable_base + flexible_base != committed:
            errors.append("stable and flexible base amounts must sum to committed monthly cash")
        if None not in (discretionary, flexible_extra) and flexible_extra > discretionary:
            errors.append("flexible discretionary budget exceeds the declared discretionary budget")
        if None not in (committed, discretionary, hard_cap) and committed + discretionary > hard_cap:
            errors.append("committed plus discretionary cash exceeds the monthly hard cap")

    risk = proposal.get("risk", {})
    if isinstance(risk, Mapping):
        expected_risk = {
            "maximum_single_fund_weight_pct": context["single_fund_pct"],
            "maximum_theme_weight_pct": context["theme_pct"],
            "portfolio_drawdown_review_pct": context["target_drawdown_pct"],
            "portfolio_drawdown_veto_pct": context["stress_drawdown_pct"],
            "maximum_active_rebalances_per_month": context["max_rebalances"],
        }
        for field, expected in expected_risk.items():
            value = _number(risk.get(field), f"risk.{field}", errors)
            if value is not None and value != float(expected):
                errors.append(f"risk.{field} must match investment policy ({expected})")

    data = proposal.get("data", {})
    if isinstance(data, Mapping):
        if data.get("execution_nav_field") != "unit_nav":
            errors.append("data.execution_nav_field must be unit_nav")
        if data.get("forward_fill") is not False:
            errors.append("data.forward_fill must be false")
        if data.get("missing_signal_action") not in {"no_signal", "ineligible", "hold_cash"}:
            errors.append("data.missing_signal_action must fail closed")

    discovery = proposal.get("discovery", {})
    new_fund_routes: list[dict[str, str]] = []
    new_sleeves: list[str] = []
    novel_data_inputs: list[str] = []
    discovery_status: object = None
    if isinstance(discovery, Mapping):
        discovery_status = discovery.get("status")
        if discovery_status not in {
            "not_required", "pending", "research_ready", "complete", "blocked"
        }:
            errors.append("discovery.status is invalid")
        raw_routes = discovery.get("new_fund_routes")
        if not isinstance(raw_routes, list):
            errors.append("discovery.new_fund_routes must be a list")
        else:
            seen_codes: set[str] = set()
            for index, route in enumerate(raw_routes):
                if not isinstance(route, Mapping):
                    errors.append(f"discovery.new_fund_routes[{index}] must be a table")
                    continue
                fund_code = str(route.get("fund_code", ""))
                sleeve_id = str(route.get("sleeve_id", ""))
                if re.fullmatch(r"[0-9]{6}", fund_code) is None:
                    errors.append(
                        f"discovery.new_fund_routes[{index}].fund_code must contain six digits"
                    )
                elif fund_code in seen_codes:
                    errors.append(f"duplicate discovery fund code: {fund_code}")
                else:
                    seen_codes.add(fund_code)
                if STRATEGY_ID_RE.fullmatch(sleeve_id) is None:
                    errors.append(
                        f"discovery.new_fund_routes[{index}].sleeve_id must use snake_case"
                    )
                if re.fullmatch(r"[0-9]{6}", fund_code) and STRATEGY_ID_RE.fullmatch(
                    sleeve_id
                ):
                    new_fund_routes.append(
                        {"fund_code": fund_code, "sleeve_id": sleeve_id}
                    )
        raw_sleeves = discovery.get("new_sleeves")
        if not isinstance(raw_sleeves, list):
            errors.append("discovery.new_sleeves must be a list")
        else:
            for value in raw_sleeves:
                sleeve_id = str(value)
                if STRATEGY_ID_RE.fullmatch(sleeve_id) is None:
                    errors.append("all discovery.new_sleeves values must use snake_case")
                else:
                    new_sleeves.append(sleeve_id)
            if len(set(new_sleeves)) != len(new_sleeves):
                errors.append("discovery.new_sleeves must be unique")
        raw_inputs = discovery.get("novel_data_inputs")
        if not isinstance(raw_inputs, list):
            errors.append("discovery.novel_data_inputs must be a list")
        else:
            novel_data_inputs = [str(value).strip() for value in raw_inputs]
            if any(not value for value in novel_data_inputs):
                errors.append("discovery.novel_data_inputs cannot contain empty values")
            if any(STRATEGY_ID_RE.fullmatch(value) is None for value in novel_data_inputs):
                errors.append("all discovery.novel_data_inputs values must use snake_case")
            if len(set(novel_data_inputs)) != len(novel_data_inputs):
                errors.append("discovery.novel_data_inputs must be unique")
    else:
        errors.append("discovery must be a table")

    comparators = proposal.get("comparators", {})
    if isinstance(comparators, Mapping):
        expected_prefix = str(context["benchmark"]) + ":"
        if not str(comparators.get("fixed_cashflow", "")).startswith(expected_prefix):
            errors.append("comparators.fixed_cashflow must use the current benchmark")
        if not str(comparators.get("matched_cashflow", "")).startswith(expected_prefix):
            errors.append("comparators.matched_cashflow must use the current benchmark")
        if comparators.get("require_identical_monthly_cashflows") is not True:
            errors.append("matched comparator must require identical monthly cashflows")

    acceptance = proposal.get("acceptance", {})
    if isinstance(acceptance, Mapping):
        if acceptance.get("primary_return_metric") not in {"xirr_pct", "annualized_return_pct"}:
            errors.append("acceptance.primary_return_metric is unsupported")
        _number(acceptance.get("minimum_return_delta_pp"), "acceptance.minimum_return_delta_pp", errors)
        worsening = _number(
            acceptance.get("maximum_drawdown_worsening_pp"),
            "acceptance.maximum_drawdown_worsening_pp",
            errors,
        )
        if worsening is not None and worsening < 0:
            errors.append("maximum drawdown worsening tolerance cannot be negative")
        if acceptance.get("automatic_winner_selection") is not False:
            errors.append("automatic winner selection must remain false")
        if acceptance.get("parameter_changes_create_new_version") is not True:
            errors.append("parameter changes must create a new version")

    signals = proposal.get("signals", [])
    signal_ids: set[str] = set()
    primitives: set[str] = set()
    if not isinstance(signals, list) or not signals:
        errors.append("at least one signal is required")
    else:
        for index, signal in enumerate(signals):
            if not isinstance(signal, Mapping):
                errors.append(f"signals[{index}] must be a table")
                continue
            signal_id = _required_text(signal, "signal_id", errors)
            if signal_id in signal_ids:
                errors.append(f"duplicate signal_id: {signal_id}")
            signal_ids.add(signal_id)
            primitive = _required_text(signal, "primitive", errors)
            if primitive:
                primitives.add(primitive)
            _number(signal.get("threshold"), f"signals[{index}].threshold", errors)
            if signal.get("on_missing") not in {"no_signal", "ineligible", "hold_cash"}:
                errors.append(f"signals[{index}].on_missing must fail closed")

    actions = proposal.get("actions", [])
    if not isinstance(actions, list) or not actions:
        errors.append("at least one action is required")
    else:
        action_ids: set[str] = set()
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                errors.append(f"actions[{index}] must be a table")
                continue
            action_id = _required_text(action, "action_id", errors)
            if action_id in action_ids:
                errors.append(f"duplicate action_id: {action_id}")
            action_ids.add(action_id)
            when_signal = action.get("when_signal")
            if when_signal != "always" and when_signal not in signal_ids:
                errors.append(f"actions[{index}] references an unknown signal")
            if action.get("action_type") != "allocate_new_cash":
                errors.append(f"actions[{index}] only allocate_new_cash is supported")
            sleeves = action.get("target_sleeves")
            weights = action.get("target_weights")
            if not isinstance(sleeves, list) or not sleeves:
                errors.append(f"actions[{index}].target_sleeves must be non-empty")
            if not isinstance(weights, list) or not weights or len(weights) != len(sleeves or []):
                errors.append(f"actions[{index}].target_weights must align with target_sleeves")
            elif any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in weights):
                errors.append(f"actions[{index}].target_weights must be numeric")
            elif abs(sum(float(value) for value in weights) - 1.0) > 1e-9:
                errors.append(f"actions[{index}].target_weights must sum to 1")

    status = proposal.get("status")
    if status not in {"draft", "frozen"}:
        errors.append("status must be draft or frozen")
    if status == "draft":
        warnings.append("proposal is complete only after the user freezes it before performance runs")
    if require_frozen and status != "frozen":
        errors.append("proposal must be frozen before compile or backtest")

    novel = sorted(primitives - SUPPORTED_PRIMITIVES)
    reusable = sorted(primitives & SUPPORTED_PRIMITIVES)
    if novel:
        warnings.append("novel signal primitives require new deterministic code and tests")
    discovery_reasons: list[str] = []
    discovery_reasons.extend(
        f"new_fund:{item['fund_code']}" for item in new_fund_routes
    )
    discovery_reasons.extend(
        f"new_sleeve:{item}" for item in sorted(set(new_sleeves))
    )
    discovery_reasons.extend(
        f"novel_data:{item}" for item in sorted(set(novel_data_inputs))
    )
    discovery_reasons.extend(f"novel_primitive:{item}" for item in novel)
    discovery_required = bool(discovery_reasons)
    research_discovery_ready = discovery_status in {"research_ready", "complete"}
    if discovery_required and not research_discovery_ready:
        warnings.append(
            "discovery path must reach research_ready before exploratory implementation or complete before promotion"
        )
    elif discovery_required and discovery_status == "research_ready":
        warnings.append(
            "discovery is research_ready: compile and backtest are exploratory only; promotion remains blocked"
        )
    if not discovery_required and discovery_status not in {
        "not_required", "research_ready", "complete"
    }:
        warnings.append("discovery status is set although no dependency requires discovery")
    if require_frozen and discovery_required and not research_discovery_ready:
        errors.append(
            "discovery.status must be research_ready or complete before research-only compile or backtest"
        )
    if discovery_required and research_discovery_ready and isinstance(discovery, Mapping):
        report_path_value = discovery.get("report_path")
        expected_report_sha = str(discovery.get("report_sha256", ""))
        if not isinstance(report_path_value, str) or not report_path_value.strip():
            errors.append("discovery.report_path is required when discovery is research-ready")
        elif SHA256_RE.fullmatch(expected_report_sha) is None:
            errors.append("discovery.report_sha256 must be a lowercase SHA-256 digest")
        else:
            root = Path(context["workspace_root"]).resolve()
            report_path = (root / report_path_value).resolve()
            if report_path == root or root not in report_path.parents:
                errors.append("discovery.report_path must stay within the workspace")
            elif not report_path.is_file():
                errors.append("discovery.report_path does not exist")
            else:
                actual_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
                if actual_sha != expected_report_sha:
                    errors.append("discovery report SHA-256 mismatch")
                else:
                    discovery_report = _load_toml(report_path)
                    discovery_validation = (
                        validate_discovery_report(discovery_report, require_complete=True)
                        if discovery_status == "complete"
                        else validate_discovery_report(
                            discovery_report, require_research_ready=True
                        )
                    )
                    if discovery_validation["status"] != "ok":
                        errors.extend(
                            f"discovery report: {item}"
                            for item in discovery_validation["errors"]
                        )
                    if (
                        discovery_report.get("strategy_id") != strategy_id
                        or discovery_report.get("strategy_version") != version
                    ):
                        errors.append("discovery report strategy identity mismatch")
    workflow_path = "discovery_path" if discovery_required else "fast_path"
    if novel:
        implementation_class = "new_primitives_and_compositor_required"
    elif discovery_required:
        implementation_class = "discovery_then_compositor_required"
    else:
        implementation_class = "thin_compositor_required"
    return {
        "status": "ok" if not errors else "error",
        "strategy": f"{strategy_id}@{version}" if strategy_id and version else None,
        "proposal_status": status,
        "errors": errors,
        "warnings": warnings,
        "reusable_primitives": reusable,
        "novel_primitives": novel,
        "workflow_path": workflow_path,
        "discovery_required": discovery_required,
        "discovery_reasons": sorted(discovery_reasons),
        "implementation_class": implementation_class,
        "ready_for_backtest": (
            not errors
            and status == "frozen"
            and (not discovery_required or research_discovery_ready)
        ),
        "ready_for_promotion": (
            not errors
            and status == "frozen"
            and (not discovery_required or discovery_status == "complete")
        ),
    }


def _default_proposal_path(root: Path, strategy_id: str, version: str) -> Path:
    return root / "strategies/proposals" / f"{strategy_id}_v{version.replace('.', '_')}.toml"


def command_new(args: argparse.Namespace) -> dict[str, Any]:
    if STRATEGY_ID_RE.fullmatch(args.strategy_id) is None:
        raise ValueError("strategy-id must use snake_case and start with a letter")
    if SEMVER_RE.fullmatch(args.version) is None:
        raise ValueError("version must use semantic versioning")
    root = args.workspace_root.resolve()
    output = args.output or _default_proposal_path(root, args.strategy_id, args.version)
    output = output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing proposal: {output}")
    template = (_skill_root() / "assets/strategy-proposal.template.toml").read_text(
        encoding="utf-8"
    )
    template = template.replace('strategy_id = "replace_me"', f'strategy_id = "{args.strategy_id}"')
    template = template.replace('strategy_version = "0.1.0"', f'strategy_version = "{args.version}"')
    template = template.replace('title = "replace_me"', f'title = {json.dumps(args.title, ensure_ascii=False)}')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template, encoding="utf-8")
    return {"status": "created", "path": str(output), "next": "edit_then_validate"}


def command_validate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = args.workspace_root.resolve()
    proposal = _load_toml(args.proposal.resolve())
    report = validate_proposal(
        proposal, context=_project_context(root), require_frozen=args.require_frozen
    )
    return report, 0 if report["status"] == "ok" else 1


def _compiled_spec(proposal: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    blockers = ["prospective_out_of_sample_observation_incomplete"]
    if report["novel_primitives"]:
        blockers.append("novel_signal_primitives_not_implemented")
    if report["discovery_required"]:
        if proposal["discovery"]["status"] == "research_ready":
            blockers.extend(
                [
                    "promotion_discovery_incomplete",
                    "counterfactual_execution_assumptions_require_sensitivity",
                ]
            )
        else:
            blockers.append("new_fund_sleeve_or_data_integration_pending")
    blockers.append("strategy_compositor_and_backtest_scenario_not_implemented")
    return {
        "$schema": "../../schemas/strategy_spec.schema.json",
        "schema_version": 1,
        "strategy_id": proposal["strategy_id"],
        "strategy_version": proposal["strategy_version"],
        "status": "specified",
        "mode": "research_only",
        "research_evidence_level": proposal["discovery"]["status"],
        "purpose": proposal["hypothesis"],
        "inputs": {
            "allocation_anchor": proposal["allocation_anchor"],
            "comparison_benchmark": proposal["comparison_benchmark"],
            "scope": proposal["scope"],
            "capital": deepcopy(proposal["capital"]),
            "data": deepcopy(proposal["data"]),
            "discovery": deepcopy(proposal["discovery"]),
        },
        "rules": {
            "stable": deepcopy(proposal["stable"]),
            "flexible": deepcopy(proposal["flexible"]),
            "signals": deepcopy(proposal["signals"]),
            "actions": deepcopy(proposal["actions"]),
            "risk": deepcopy(proposal["risk"]),
            "acceptance": deepcopy(proposal["acceptance"]),
            "execution": {"selling_allowed": False, "leverage_allowed": False},
        },
        "prohibitions": [
            "use_future_or_same_day_unpublished_data",
            "forward_fill_missing_nav",
            "change_parameters_after_viewing_backtest_results",
            "borrow_cash_or_use_leverage",
            "sell_existing_holdings",
            "submit_real_orders",
        ],
        "backtest_scenarios": {
            "required_comparators": deepcopy(proposal["comparators"]),
            "implementation_status": "pending",
        },
        "required_outputs": [
            "annualized_return",
            "xirr",
            "annualized_volatility",
            "maximum_drawdown_and_recovery",
            "purchase_fees",
            "cash_drag",
            "rolling_windows",
            "stress_results",
            "data_rule_strategy_and_code_hashes",
        ],
        "acceptance_tests": [
            "candidate_and_matched_631_have_identical_monthly_cashflows",
            "signals_use_only_data_visible_at_evaluation_time",
            "same_inputs_produce_byte_equivalent_results",
            "no_negative_cash_no_sell_no_leverage",
            "policy_concentration_and_drawdown_vetoes_are_enforced",
        ],
        "known_blockers": blockers,
    }


def command_compile(args: argparse.Namespace) -> dict[str, Any]:
    root = args.workspace_root.resolve()
    proposal = _load_toml(args.proposal.resolve())
    report = validate_proposal(proposal, context=_project_context(root), require_frozen=True)
    if report["status"] != "ok":
        raise ValueError("proposal is not ready: " + "; ".join(report["errors"]))
    version_tag = str(proposal["strategy_version"]).replace(".", "_")
    output = args.output or (
        root / "strategies/specs" / f"{proposal['strategy_id']}_v{version_tag}.json"
    )
    output = output.resolve()
    if output.exists():
        raise ValueError(f"refusing to overwrite existing strategy spec: {output}")
    spec = _compiled_spec(proposal, report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "compiled",
        "path": str(output),
        "implementation_class": report["implementation_class"],
        "workflow_path": report["workflow_path"],
        "discovery_reasons": report["discovery_reasons"],
        "novel_primitives": report["novel_primitives"],
        "next": "implement_tests_scenario_and_comparator_before_registry",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    new = subparsers.add_parser("new", help="create one editable TOML proposal")
    new.add_argument("--strategy-id", required=True)
    new.add_argument("--title", required=True)
    new.add_argument("--version", default="0.1.0")
    new.add_argument("--workspace-root", type=Path, default=Path("."))
    new.add_argument("--output", type=Path)
    validate = subparsers.add_parser("validate", help="validate proposal and project policy")
    validate.add_argument("proposal", type=Path)
    validate.add_argument("--workspace-root", type=Path, default=Path("."))
    validate.add_argument("--require-frozen", action="store_true")
    compile_parser = subparsers.add_parser("compile", help="compile a frozen proposal to a spec")
    compile_parser.add_argument("proposal", type=Path)
    compile_parser.add_argument("--workspace-root", type=Path, default=Path("."))
    compile_parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "new":
            payload = command_new(args)
            code = 0
        elif args.command == "validate":
            payload, code = command_validate(args)
        else:
            payload = command_compile(args)
            code = 0
    except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        payload = {"status": "error", "message": str(exc)}
        code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
