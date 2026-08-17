"""Grounded natural-language explanations and deterministic acceptance gates."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .reporting import _canonical_sha256, _pointer


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _load_source_report(
    config: Mapping[str, Any],
    workspace_root: Path,
    source_report_override: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    source = config.get("source_report")
    if not isinstance(source, Mapping):
        raise ValueError("explanation config requires source_report")
    if source_report_override is None:
        path = _resolve(workspace_root, source.get("path"), "source_report.path")
        actual = _sha256(path)
        if actual != source.get("sha256"):
            raise ValueError("source_report sha256 mismatch")
        report = _load_json(path)
    else:
        report = source_report_override.get("payload")
        actual = source_report_override.get("sha256")
        if not isinstance(report, Mapping) or not isinstance(actual, str):
            raise ValueError("source_report_override requires payload and sha256")
        if actual != source.get("sha256"):
            raise ValueError("source_report_override sha256 mismatch")
        if _canonical_sha256(report) != source_report_override.get("canonical_sha256"):
            raise ValueError("source_report_override canonical sha256 mismatch")
    if report.get("mode") != "research_only" or report.get("execution") != {
        "advisory_only": True,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }:
        raise ValueError("source report is not safely non-executable")
    return report, str(source["path"]), actual


def _citation(report: Mapping[str, Any], pointer: str) -> dict[str, str]:
    value = _pointer(report, pointer)
    return {"json_pointer": pointer, "value_sha256": _canonical_sha256(value)}


def _format_fact(value: object, display_format: str) -> str:
    if display_format in {"string", "date"}:
        return str(value)
    if display_format == "percent_2":
        return f"{float(value):.2f}%"
    if display_format == "cny_2":
        return f"{float(value):,.2f}元"
    raise ValueError(f"unsupported numeric fact format: {display_format}")


def _numeric_fact(
    report: Mapping[str, Any], *, fact_id: str, pointer: str, display_format: str
) -> dict[str, str]:
    value = _pointer(report, pointer)
    return {
        "fact_id": fact_id,
        "source_pointer": pointer,
        "source_value_sha256": _canonical_sha256(value),
        "display_format": display_format,
        "display": _format_fact(value, display_format),
    }


def _assertion(
    report: Mapping[str, Any],
    *,
    assertion_id: str,
    text: str,
    claim_ids: list[str],
    citation_pointers: list[str],
    numeric_facts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "text": text,
        "claim_ids": claim_ids,
        "citations": [_citation(report, pointer) for pointer in citation_pointers],
        "numeric_facts": numeric_facts or [],
    }


def _strategy_index(report: Mapping[str, Any], strategy_id: str) -> int:
    for index, item in enumerate(report["strategy_views"]):
        if item.get("strategy_id") == strategy_id:
            return index
    raise ValueError(f"source report missing strategy: {strategy_id}")


def _validate_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("explanation config schema_version must be 1")
    expected_safety = {
        "research_only": True,
        "advisory_only": True,
        "external_market_news_allowed": False,
        "price_prediction_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
    }
    if config.get("safety") != expected_safety:
        raise ValueError("explanation safety contract is not exact")


def build_grounded_explanation(
    config: Mapping[str, Any],
    *,
    workspace_root: Path,
    source_report_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical explanation candidate from a locked research report."""

    _validate_config(config)
    report, report_path, report_hash = _load_source_report(
        config, workspace_root, source_report_override
    )
    drawdown_index = _strategy_index(report, "drawdown_budget_add")
    trend_index = _strategy_index(report, "new_money_trend_rs")
    sleeve_index = _strategy_index(report, "sleeve_drawdown_recovery")

    concentration_facts = [
        _numeric_fact(
            report,
            fact_id="largest_fund_code",
            pointer="/portfolio_state/largest_fund_code",
            display_format="string",
        ),
        _numeric_fact(
            report,
            fact_id="largest_fund_weight_pct",
            pointer="/portfolio_state/largest_fund_weight_pct",
            display_format="percent_2",
        ),
        _numeric_fact(
            report,
            fact_id="single_fund_limit_pct",
            pointer="/portfolio_state/single_fund_limit_breaches/0/limit_pct",
            display_format="percent_2",
        ),
    ]
    risk_facts = [
        _numeric_fact(
            report,
            fact_id="annualized_volatility_pct",
            pointer="/portfolio_state/risk_proxy/annualized_volatility_pct",
            display_format="percent_2",
        ),
        _numeric_fact(
            report,
            fact_id="maximum_drawdown_pct",
            pointer="/portfolio_state/risk_proxy/max_drawdown_pct",
            display_format="percent_2",
        ),
        _numeric_fact(
            report,
            fact_id="current_drawdown_pct",
            pointer="/portfolio_state/risk_proxy/current_drawdown_pct",
            display_format="percent_2",
        ),
    ]
    strategy_facts = [
        _numeric_fact(
            report,
            fact_id="trend_selected_fund",
            pointer=f"/strategy_views/{trend_index}/selected_fund_codes/0",
            display_format="string",
        ),
        _numeric_fact(
            report,
            fact_id="drawdown_additional_budget",
            pointer=f"/strategy_views/{drawdown_index}/proposed_additional_budget_cny",
            display_format="cny_2",
        ),
        _numeric_fact(
            report,
            fact_id="sleeve_additional_budget",
            pointer=f"/strategy_views/{sleeve_index}/proposed_additional_budget_cny",
            display_format="cny_2",
        ),
    ]
    review_fact = _numeric_fact(
        report,
        fact_id="next_review_date",
        pointer="/next_review/date",
        display_format="date",
    )

    sections = [
        {
            "section_id": "executive_summary",
            "title": "结论先行",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="summary_posture",
                    text="当前组合应先控制集中风险；保留631作为比较基准，不在月中补造计划，也不采纳额外预算。",
                    claim_ids=["portfolio_concentration", "baseline_authority", "no_mid_cycle_schedule"],
                    citation_pointers=["/executive_summary", "/decision_summary"],
                )
            ],
        },
        {
            "section_id": "portfolio_risk",
            "title": "组合与风险",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="concentration_fact",
                    text=(
                        f"最大持仓{concentration_facts[0]['display']}占{concentration_facts[1]['display']}，"
                        f"超过{concentration_facts[2]['display']}的单基金政策上限。"
                    ),
                    claim_ids=["portfolio_concentration"],
                    citation_pointers=["/portfolio_state/single_fund_limit_breaches"],
                    numeric_facts=concentration_facts,
                ),
                _assertion(
                    report,
                    assertion_id="risk_proxy_fact",
                    text=(
                        f"当前权重风险代理的年化波动为{risk_facts[0]['display']}，"
                        f"最大回撤为{risk_facts[1]['display']}，当前回撤为{risk_facts[2]['display']}；"
                        "它不是账户真实收益路径。"
                    ),
                    claim_ids=["portfolio_risk_proxy"],
                    citation_pointers=["/portfolio_state/risk_proxy"],
                    numeric_facts=risk_facts,
                ),
            ],
        },
        {
            "section_id": "strategy_interpretation",
            "title": "策略解释",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="strategy_statuses",
                    text=(
                        f"趋势对照仅选中{strategy_facts[0]['display']}，但尚未晋级；"
                        f"组合回撤与袖套回撤分别触发{strategy_facts[1]['display']}和"
                        f"{strategy_facts[2]['display']}研究预算，均未采纳。"
                    ),
                    claim_ids=["baseline_authority", "no_mid_cycle_schedule"],
                    citation_pointers=["/strategy_views", "/decision_summary"],
                    numeric_facts=strategy_facts,
                )
            ],
        },
        {
            "section_id": "counter_evidence",
            "title": "反证与不确定性",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="mandatory_limits",
                    text="前瞻样本外尚无完整月份，历史净值可见时点仍是假设，风险代理也不等于账户真实路径；因此不能把研究触发描述为生产验证或操作指令。",
                    claim_ids=["oos_not_started", "portfolio_risk_proxy"],
                    citation_pointers=["/counter_evidence", "/uncertainties"],
                )
            ],
        },
        {
            "section_id": "next_review",
            "title": "下一次复核",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="next_review_date",
                    text=f"下一次有效复核日期为{review_fact['display']}；届时必须刷新只读快照和本地数据，并重新运行全部门禁。",
                    claim_ids=["no_mid_cycle_schedule"],
                    citation_pointers=["/next_review"],
                    numeric_facts=[review_fact],
                )
            ],
        },
        {
            "section_id": "safety_boundary",
            "title": "安全边界",
            "assertions": [
                _assertion(
                    report,
                    assertion_id="non_executable",
                    text="本解释不使用外部消息预测涨跌，不修改上游数值，不生成订单，也不能覆盖注册表或风险否决。",
                    claim_ids=[],
                    citation_pointers=["/scope", "/llm_contract", "/execution"],
                )
            ],
        },
    ]

    explanation = {
        "schema_version": 1,
        "explanation_id": str(config["explanation_id"]),
        "as_of": str(report["as_of"]),
        "mode": "research_only",
        "source_report": {
            "path": report_path,
            "sha256": report_hash,
            "report_id": report["report_id"],
            "evidence_bundle_sha256": report["reproducibility"]["evidence_bundle_sha256"],
        },
        "scope": {
            "external_market_news_used": False,
            "market_direction_prediction": False,
            "new_numeric_calculation": False,
            "explanation_only": True,
        },
        "sections": sections,
        "covered_strategy_ids": sorted(item["strategy_id"] for item in report["strategy_views"]),
        "acknowledged_counter_evidence": list(report["counter_evidence"]),
        "acknowledged_uncertainties": list(report["uncertainties"]),
        "execution": {
            "advisory_only": True,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
    explanation["reproducibility"] = {
        "content_sha256": _canonical_sha256(explanation),
        "network_used": False,
    }
    validate_grounded_explanation(explanation, report=report, config=config)
    return explanation


def validate_grounded_explanation(
    explanation: Mapping[str, Any], *, report: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    """Reject unsupported numbers, missing evidence, unsafe language, or execution drift."""

    _validate_config(config)
    if explanation.get("schema_version") != 1 or explanation.get("mode") != "research_only":
        raise ValueError("explanation must be schema v1 research_only")
    source = explanation.get("source_report")
    expected_source = config["source_report"]
    if not isinstance(source, Mapping) or (
        source.get("path") != expected_source["path"]
        or source.get("sha256") != expected_source["sha256"]
        or source.get("report_id") != report.get("report_id")
        or source.get("evidence_bundle_sha256")
        != report.get("reproducibility", {}).get("evidence_bundle_sha256")
    ):
        raise ValueError("explanation source report reference mismatch")
    if explanation.get("scope") != {
        "external_market_news_used": False,
        "market_direction_prediction": False,
        "new_numeric_calculation": False,
        "explanation_only": True,
    }:
        raise ValueError("explanation scope is not exact")
    if explanation.get("execution") != {
        "advisory_only": True,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }:
        raise ValueError("explanation execution contract is not exact")

    sections = explanation.get("sections")
    if not isinstance(sections, list):
        raise ValueError("explanation sections must be a list")
    actual_sections = [item.get("section_id") for item in sections if isinstance(item, Mapping)]
    if actual_sections != list(config["required_section_ids"]):
        raise ValueError("explanation section coverage mismatch")

    claim_ids = {str(item["claim_id"]) for item in report["evidence_ledger"]}
    covered_claims: set[str] = set()
    assertion_ids: set[str] = set()
    assertion_count = 0
    for section in sections:
        assertions = section.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            raise ValueError("every explanation section requires assertions")
        for assertion in assertions:
            if not isinstance(assertion, Mapping):
                raise ValueError("assertion must be an object")
            assertion_count += 1
            assertion_id = str(assertion.get("assertion_id", ""))
            if not assertion_id or assertion_id in assertion_ids:
                raise ValueError("assertion ids must be unique and non-empty")
            assertion_ids.add(assertion_id)
            text = assertion.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("assertion text must be non-empty")
            if any(phrase in text for phrase in config["prohibited_phrases"]):
                raise ValueError("explanation contains prohibited action language")

            assertion_claims = assertion.get("claim_ids")
            if not isinstance(assertion_claims, list) or any(
                str(item) not in claim_ids for item in assertion_claims
            ):
                raise ValueError("assertion references unknown evidence claim")
            covered_claims.update(str(item) for item in assertion_claims)

            citations = assertion.get("citations")
            if not isinstance(citations, list) or not citations:
                raise ValueError("assertion requires at least one citation")
            for citation in citations:
                pointer = str(citation["json_pointer"])
                actual_hash = _canonical_sha256(_pointer(report, pointer))
                if citation.get("value_sha256") != actual_hash:
                    raise ValueError("citation value hash mismatch")

            residual = text
            numeric_facts = assertion.get("numeric_facts")
            if not isinstance(numeric_facts, list):
                raise ValueError("numeric_facts must be a list")
            seen_fact_ids: set[str] = set()
            for fact in numeric_facts:
                fact_id = str(fact.get("fact_id", ""))
                if not fact_id or fact_id in seen_fact_ids:
                    raise ValueError("numeric fact ids must be unique per assertion")
                seen_fact_ids.add(fact_id)
                pointer = str(fact["source_pointer"])
                value = _pointer(report, pointer)
                if fact.get("source_value_sha256") != _canonical_sha256(value):
                    raise ValueError("numeric fact source hash mismatch")
                expected_display = _format_fact(value, str(fact["display_format"]))
                if fact.get("display") != expected_display:
                    raise ValueError("numeric fact display mismatch")
                if expected_display not in residual:
                    raise ValueError("declared numeric fact is absent from assertion text")
                residual = residual.replace(expected_display, "")
            for literal in config["allowed_numeric_literals"]:
                residual = residual.replace(str(literal), "")
            if re.search(r"\d", residual):
                raise ValueError("assertion contains unsupported numeric token")

    if covered_claims != claim_ids:
        raise ValueError("required evidence claim coverage mismatch")
    if explanation.get("covered_strategy_ids") != sorted(config["required_strategy_ids"]):
        raise ValueError("strategy coverage mismatch")
    if explanation.get("covered_strategy_ids") != sorted(
        item["strategy_id"] for item in report["strategy_views"]
    ):
        raise ValueError("strategy coverage does not match source report")
    if explanation.get("acknowledged_counter_evidence") != report.get("counter_evidence"):
        raise ValueError("counter-evidence coverage mismatch")
    if explanation.get("acknowledged_uncertainties") != report.get("uncertainties"):
        raise ValueError("uncertainty coverage mismatch")
    return {
        "status": "passed",
        "sections": len(sections),
        "assertions": assertion_count,
        "claims_covered": len(covered_claims),
        "strategies_covered": len(explanation["covered_strategy_ids"]),
        "external_market_news_used": False,
        "orders": 0,
    }


def render_grounded_explanation_markdown(explanation: Mapping[str, Any]) -> str:
    if explanation.get("execution") != {
        "advisory_only": True,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }:
        raise ValueError("cannot render unsafe explanation")
    lines = [
        "# 月度投资研究解释",
        "",
        f"> 数据时点：{explanation['as_of']}｜仅研究、不可执行",
        "",
    ]
    for section in explanation["sections"]:
        lines.extend([f"## {section['title']}", ""])
        for assertion in section["assertions"]:
            claims = "、".join(assertion["claim_ids"]) or "结构化安全契约"
            lines.extend([str(assertion["text"]), "", f"证据：`{claims}`", ""])
    return "\n".join(lines)


def run_explanation_acceptance(
    config: Mapping[str, Any],
    *,
    workspace_root: Path,
    source_report_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run fixed fault injections against the explanation consistency gate."""

    _validate_config(config)
    report, report_path, report_hash = _load_source_report(
        config, workspace_root, source_report_override
    )
    canonical = build_grounded_explanation(
        config,
        workspace_root=workspace_root,
        source_report_override=source_report_override,
    )

    def mutate_source_hash(item: dict[str, Any]) -> None:
        item["source_report"]["sha256"] = "0" * 64

    def mutate_missing_section(item: dict[str, Any]) -> None:
        item["sections"].pop()

    def mutate_duplicate_assertion(item: dict[str, Any]) -> None:
        item["sections"][1]["assertions"][1]["assertion_id"] = item["sections"][1]["assertions"][0]["assertion_id"]

    def mutate_citation_hash(item: dict[str, Any]) -> None:
        item["sections"][0]["assertions"][0]["citations"][0]["value_sha256"] = "0" * 64

    def mutate_numeric_display(item: dict[str, Any]) -> None:
        assertion = item["sections"][1]["assertions"][0]
        assertion["text"] = assertion["text"].replace("81.35%", "82.00%")
        assertion["numeric_facts"][1]["display"] = "82.00%"

    def mutate_unsupported_number(item: dict[str, Any]) -> None:
        item["sections"][0]["assertions"][0]["text"] += " 预计上涨10%。"

    def mutate_missing_claim(item: dict[str, Any]) -> None:
        for section in item["sections"]:
            for assertion in section["assertions"]:
                assertion["claim_ids"] = [
                    claim for claim in assertion["claim_ids"] if claim != "oos_not_started"
                ]

    def mutate_missing_counter(item: dict[str, Any]) -> None:
        item["acknowledged_counter_evidence"].pop()

    def mutate_missing_uncertainty(item: dict[str, Any]) -> None:
        item["acknowledged_uncertainties"].pop()

    def mutate_missing_strategy(item: dict[str, Any]) -> None:
        item["covered_strategy_ids"].pop()

    def mutate_action_language(item: dict[str, Any]) -> None:
        item["sections"][0]["assertions"][0]["text"] += " 建议立即加仓。"

    def mutate_external_news(item: dict[str, Any]) -> None:
        item["scope"]["external_market_news_used"] = True

    def mutate_execution(item: dict[str, Any]) -> None:
        item["execution"]["real_trading_enabled"] = True

    scenarios = [
        ("control", None, None),
        ("source_hash_drift", mutate_source_hash, "source report reference"),
        ("missing_section", mutate_missing_section, "section coverage"),
        ("duplicate_assertion", mutate_duplicate_assertion, "assertion ids"),
        ("citation_hash_drift", mutate_citation_hash, "citation value hash"),
        ("numeric_value_tamper", mutate_numeric_display, "numeric fact display"),
        ("unsupported_number", mutate_unsupported_number, "unsupported numeric token"),
        ("missing_claim", mutate_missing_claim, "claim coverage"),
        ("missing_counter_evidence", mutate_missing_counter, "counter-evidence"),
        ("missing_uncertainty", mutate_missing_uncertainty, "uncertainty coverage"),
        ("missing_strategy", mutate_missing_strategy, "strategy coverage"),
        ("prohibited_action_language", mutate_action_language, "prohibited action language"),
        ("external_news_enabled", mutate_external_news, "scope is not exact"),
        ("execution_enabled", mutate_execution, "execution contract"),
    ]
    results: list[dict[str, Any]] = []
    for scenario_id, mutator, expected_error in scenarios:
        candidate = copy.deepcopy(canonical)
        if mutator is not None:
            mutator(candidate)
        try:
            details = validate_grounded_explanation(candidate, report=report, config=config)
            observed = "passed"
            error = None
        except ValueError as exc:
            details = None
            observed = "rejected"
            error = str(exc)
        expected = "passed" if mutator is None else "rejected"
        passed = observed == expected and (
            expected_error is None or (error is not None and expected_error in error)
        )
        results.append(
            {
                "scenario_id": scenario_id,
                "expected": expected,
                "observed": observed,
                "expected_error_contains": expected_error,
                "error": error,
                "details": details,
                "passed": passed,
            }
        )
    return {
        "schema_version": 1,
        "acceptance_id": str(config["acceptance_id"]),
        "as_of": str(canonical["as_of"]),
        "mode": "research_only",
        "source_report": {"path": report_path, "sha256": report_hash, "report_id": report["report_id"]},
        "canonical_explanation_sha256": _canonical_sha256(canonical),
        "scenarios": results,
        "summary": {
            "total": len(results),
            "passed": sum(item["passed"] for item in results),
            "failed": sum(not item["passed"] for item in results),
            "acceptance_gate": "passed" if all(item["passed"] for item in results) else "failed",
        },
        "execution": {
            "network_used": False,
            "external_market_news_used": False,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
