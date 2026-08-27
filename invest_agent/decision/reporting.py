"""Deterministic, evidence-bound research reporting for Phase 5."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .registry import load_strategy_registry, validate_strategy_registry


EXPECTED_STRATEGY_IDS = {
    "dca_baseline",
    "new_money_trend_rs",
    "drawdown_budget_add",
    "sleeve_drawdown_recovery",
}

STRATEGY_LABELS = {
    "dca_baseline": "631定投比较基准",
    "new_money_trend_rs": "新增资金趋势对照",
    "drawdown_budget_add": "组合回撤预算对照",
    "sleeve_drawdown_recovery": "科技成长袖套回撤候选",
}

SLEEVE_LABELS = {
    "defensive": "防御",
    "domestic_broad_core": "国内宽基",
    "us_broad_core": "美国宽基",
    "uk_broad_core": "英国宽基",
    "domestic_growth": "国内成长",
    "us_growth": "海外成长",
    "sh_hk_sz_passive_technology_satellite": "沪港深科技卫星",
}


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


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _load_locked_json(
    root: Path, artifact: Mapping[str, Any], field: str
) -> tuple[Path, dict[str, Any], str]:
    path = _resolve(root, artifact.get("path"), f"{field}.path")
    actual = _sha256(path)
    if actual != artifact.get("sha256"):
        raise ValueError(f"{field} sha256 mismatch")
    return path, _load_json(path), actual


def _load_locked_text(
    root: Path, artifact: Mapping[str, Any], field: str
) -> tuple[Path, str, str]:
    path = _resolve(root, artifact.get("path"), f"{field}.path")
    actual = _sha256(path)
    if actual != artifact.get("sha256"):
        raise ValueError(f"{field} sha256 mismatch")
    return path, path.read_text(encoding="utf-8"), actual


def _pointer(document: object, pointer: str) -> object:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with /")
    current = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise ValueError(f"JSON pointer does not exist: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"JSON pointer does not exist: {pointer}") from exc
        else:
            raise ValueError(f"JSON pointer does not exist: {pointer}")
    return current


def _claim(
    *,
    claim_id: str,
    category: str,
    statement: str,
    source_artifact: str,
    source_pointer: str,
    source_document: Mapping[str, Any],
    evidence_status: str,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    value = _pointer(source_document, source_pointer)
    return {
        "claim_id": claim_id,
        "category": category,
        "statement": statement,
        "source": {
            "artifact": source_artifact,
            "json_pointer": source_pointer,
            "value_sha256": _canonical_sha256(value),
        },
        "evidence_status": evidence_status,
        "limitations": limitations or [],
    }


def _validate_pack(pack: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if pack.get("schema_version") != 1 or pack.get("mode") != "research_only":
        raise ValueError("source decision pack must be schema v1 research_only")
    execution = pack.get("execution")
    expected_execution = {
        "advisory_only": True,
        "selling_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }
    if execution != expected_execution:
        raise ValueError("source decision pack execution contract is not exact")
    snapshot = pack.get("source_snapshot")
    if not isinstance(snapshot, Mapping) or snapshot.get("quality_status") != "pass":
        raise ValueError("source decision pack snapshot must pass quality gate")
    decision_input = pack.get("strategy_decision_input")
    if not isinstance(decision_input, Mapping):
        raise ValueError("source decision pack requires strategy_decision_input")
    accepted = decision_input.get("accepted_signals")
    if not isinstance(accepted, list):
        raise ValueError("accepted_signals must be a list")
    identities = {
        str(item.get("strategy_id"))
        for item in accepted
        if isinstance(item, Mapping)
    }
    if identities != EXPECTED_STRATEGY_IDS:
        raise ValueError("source decision pack must contain the four frozen strategies")
    authorities = [
        item
        for item in accepted
        if isinstance(item, Mapping) and item.get("target_allocation_authority") is True
    ]
    if len(authorities) != 1 or authorities[0].get("strategy_id") != "dca_baseline":
        raise ValueError("source decision pack requires one dca_baseline authority")
    if any(item.get("execution_enabled") is not False for item in accepted):
        raise ValueError("accepted strategy signal cannot enable execution")
    return accepted


def _strategy_summary(signal: Mapping[str, Any]) -> dict[str, Any]:
    strategy_id = str(signal["strategy_id"])
    payload = signal.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError(f"strategy payload must be an object: {strategy_id}")
    base = {
        "strategy_id": strategy_id,
        "strategy_version": str(signal["strategy_version"]),
        "label": STRATEGY_LABELS[strategy_id],
        "registry_role": str(signal["registry_role"]),
        "evidence_status": str(signal["registry_evidence_status"]),
        "target_allocation_authority": bool(signal["target_allocation_authority"]),
        "risk_veto": bool(signal["risk"]["veto"]),
        "data_quality_status": str(signal["data_quality"]["status"]),
        "decision_use": "research_evidence_only",
    }
    if strategy_id == "dca_baseline":
        base.update(
            {
                "observed_signal": "retain_631_anchor_without_mid_cycle_schedule",
                "display_summary": "631仍是唯一目标配置参考；本包处于月中，没有生成新的扣款计划。",
                "proposed_additional_budget_cny": "0.00",
                "decision_use": "comparison_authority_no_executable_schedule",
            }
        )
    elif strategy_id == "new_money_trend_rs":
        selected = [str(item) for item in payload.get("selected_fund_codes", [])]
        base.update(
            {
                "observed_signal": "selected_funds:" + ",".join(selected),
                "display_summary": "趋势对照仅选中" + ("、".join(selected) or "无基金") + "，但尚未晋级，不能覆盖631。",
                "selected_fund_codes": selected,
                "proposed_additional_budget_cny": "0.00",
            }
        )
    elif strategy_id == "drawdown_budget_add":
        budget = payload.get("budget")
        if not isinstance(budget, Mapping):
            raise ValueError("drawdown budget signal requires budget")
        additional = str(budget["additional_budget_cny"])
        base.update(
            {
                "observed_signal": str(payload.get("reason")),
                "display_summary": f"组合回撤对照触发额外{additional}元研究预算，但证据不足且当前组合集中，未采纳。",
                "proposed_additional_budget_cny": additional,
            }
        )
    elif strategy_id == "sleeve_drawdown_recovery":
        budget = payload.get("budget")
        if not isinstance(budget, Mapping):
            raise ValueError("sleeve drawdown signal requires budget")
        additional = str(budget["additional_budget_cny"])
        sleeves = [str(item) for item in payload.get("triggered_sleeves", [])]
        labels = [SLEEVE_LABELS.get(item, item) for item in sleeves]
        base.update(
            {
                "observed_signal": str(payload.get("reason")),
                "display_summary": f"袖套候选在{'、'.join(labels)}触发额外{additional}元研究预算，但短样本尚不足以晋级，未采纳。",
                "triggered_sleeves": sleeves,
                "proposed_additional_budget_cny": additional,
            }
        )
    return base


def build_research_report(
    config: Mapping[str, Any],
    *,
    workspace_root: Path,
    decision_pack_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a replayable report whose claims remain bound to deterministic evidence."""

    if config.get("schema_version") != 1:
        raise ValueError("research report config schema_version must be 1")
    expected_safety = {
        "research_only": True,
        "advisory_only": True,
        "external_market_news_allowed": False,
        "price_prediction_allowed": False,
        "llm_numeric_calculation_allowed": False,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
    }
    if config.get("safety") != expected_safety:
        raise ValueError("research report safety contract is not exact")

    sources = config.get("sources")
    if not isinstance(sources, Mapping):
        raise ValueError("research report config requires sources")
    if decision_pack_override is None:
        _, pack, pack_hash = _load_locked_json(
            workspace_root, sources["decision_pack"], "decision_pack"
        )
    else:
        pack = decision_pack_override.get("payload")
        pack_hash = decision_pack_override.get("sha256")
        if not isinstance(pack, Mapping) or not isinstance(pack_hash, str):
            raise ValueError("decision_pack_override requires payload and sha256")
        if pack_hash != sources["decision_pack"].get("sha256"):
            raise ValueError("decision_pack_override sha256 mismatch")
        if _canonical_sha256(pack) != decision_pack_override.get("canonical_sha256"):
            raise ValueError("decision_pack_override canonical sha256 mismatch")
    _, policy_text, policy_hash = _load_locked_text(
        workspace_root, sources["investment_policy"], "investment_policy"
    )
    registry_path, registry, registry_hash = _load_locked_json(
        workspace_root, sources["strategy_registry"], "strategy_registry"
    )
    _, oos, oos_hash = _load_locked_json(
        workspace_root, sources["oos_protocol"], "oos_protocol"
    )

    accepted = _validate_pack(pack)
    validate_strategy_registry(registry, workspace_root=workspace_root)
    if registry != load_strategy_registry(registry_path):
        raise ValueError("strategy registry changed during report build")

    policy = config.get("policy_context")
    if not isinstance(policy, Mapping):
        raise ValueError("research report config requires policy_context")
    required_policy_lines = [
        f"allocation_anchor: {policy['allocation_anchor']}",
        f"comparison_benchmark: {policy['comparison_benchmark']}",
        f"target_max_portfolio_drawdown_pct: {policy['target_drawdown_pct']}",
        f"stress_limit_portfolio_drawdown_pct: {policy['stress_drawdown_pct']}",
        "require_human_approval: true",
        "approval_scope: exact_single_order",
        "auto_retry_mutations: false",
    ]
    if any(line not in policy_text for line in required_policy_lines):
        raise ValueError("investment policy does not match report policy context")

    current_result = oos.get("current_result")
    if not isinstance(current_result, Mapping) or current_result.get("gate_passed") is not False:
        raise ValueError("research report cannot claim an unverified OOS gate")

    current = pack["current_portfolio"]
    concentration = current["largest_position"]
    risk_proxy = current["risk_proxy"]
    market_state = current["market_state"]
    conclusion = pack["research_conclusion"]
    baseline_index = next(
        index
        for index, item in enumerate(accepted)
        if item["strategy_id"] == "dca_baseline"
    )
    strategy_views = [
        _strategy_summary(item)
        for item in sorted(
            accepted,
            key=lambda item: (
                0 if item["strategy_id"] == "dca_baseline" else 1,
                str(item["strategy_id"]),
            ),
        )
    ]

    decision_pack_ref = str(sources["decision_pack"]["path"])
    oos_ref = str(sources["oos_protocol"]["path"])
    claims = [
        _claim(
            claim_id="portfolio_concentration",
            category="risk",
            statement="当前最大单基金仓位超过政策上限，组合需要先做风险复核。",
            source_artifact=decision_pack_ref,
            source_pointer="/current_portfolio/largest_position/single_fund_limit_breaches",
            source_document=pack,
            evidence_status="observed_current_snapshot",
        ),
        _claim(
            claim_id="portfolio_risk_proxy",
            category="risk",
            statement="当前权重历史风险代理显示较高波动与回撤，但它不是账户真实收益路径。",
            source_artifact=decision_pack_ref,
            source_pointer="/current_portfolio/risk_proxy",
            source_document=pack,
            evidence_status="limited_hypothetical_proxy",
            limitations=["constant_weight_proxy", "short_common_history"],
        ),
        _claim(
            claim_id="baseline_authority",
            category="strategy",
            statement="631定投基准仍是唯一目标配置参考，研究候选不能覆盖它。",
            source_artifact=decision_pack_ref,
            source_pointer=f"/strategy_decision_input/accepted_signals/{baseline_index}/target_allocation_authority",
            source_document=pack,
            evidence_status="registered_authority",
        ),
        _claim(
            claim_id="no_mid_cycle_schedule",
            category="decision",
            statement="本报告不补造月中扣款计划，也不采用研究策略提出的额外预算。",
            source_artifact=decision_pack_ref,
            source_pointer="/research_conclusion",
            source_document=pack,
            evidence_status="deterministic_research_conclusion",
        ),
        _claim(
            claim_id="oos_not_started",
            category="uncertainty",
            statement="前瞻样本外尚无完整月份，任何策略都不能被描述为生产验证通过。",
            source_artifact=oos_ref,
            source_pointer="/current_result",
            source_document=oos,
            evidence_status="gate_blocked",
        ),
    ]

    report = {
        "schema_version": 1,
        "report_id": str(config["report_id"]),
        "report_kind": "evidence_bound_monthly_research_report",
        "as_of": str(pack["as_of"]),
        "mode": "research_only",
        "scope": {
            "purpose": "explain_existing_deterministic_evidence",
            "external_market_news_used": False,
            "market_direction_prediction": False,
            "new_strategy_signal_calculated": False,
            "llm_may_explain_only": True,
        },
        "sources": {
            "decision_pack": {"path": decision_pack_ref, "sha256": pack_hash, "pack_id": pack["pack_id"]},
            "investment_policy": {"path": str(sources["investment_policy"]["path"]), "sha256": policy_hash},
            "strategy_registry": {"path": str(sources["strategy_registry"]["path"]), "sha256": registry_hash, "registry_id": registry["registry_id"]},
            "oos_protocol": {"path": oos_ref, "sha256": oos_hash, "protocol_id": oos["protocol_id"]},
        },
        "policy_context": {
            "allocation_anchor": str(policy["allocation_anchor"]),
            "comparison_benchmark": str(policy["comparison_benchmark"]),
            "anchor_is_mandatory_execution_schedule": False,
            "target_drawdown_pct": str(policy["target_drawdown_pct"]),
            "stress_drawdown_pct": str(policy["stress_drawdown_pct"]),
        },
        "executive_summary": {
            "status": str(current["risk_posture"]),
            "headline": "当前组合先控制集中风险；保留631作为比较基准，不在月中补计划，不采纳额外预算。",
            "evidence_confidence": "limited_research_evidence",
            "production_validated": False,
        },
        "portfolio_state": {
            "total_assets_cny": str(current["total_assets_cny"]),
            "cash_cny": str(current["cash_cny"]),
            "largest_fund_code": str(concentration["largest_fund_code"]),
            "largest_fund_weight_pct": concentration["largest_fund_weight_pct"],
            "single_fund_limit_breaches": concentration["single_fund_limit_breaches"],
            "risk_proxy": {
                "annualized_volatility_pct": risk_proxy["annualized_volatility_pct"],
                "max_drawdown_pct": risk_proxy["max_drawdown_pct"],
                "current_drawdown_pct": risk_proxy["current_drawdown_pct"],
                "common_return_observations": risk_proxy["common_return_observations"],
                "is_actual_account_history": False,
            },
            "market_state_model": {
                "model_version": market_state["model_version"],
                "classification_is_not_strategy_signal": True,
                "state_counts": market_state["state_counts"],
                "weighted_fund_exposure_pct": market_state["weighted_fund_exposure_pct"],
            },
        },
        "strategy_views": strategy_views,
        "decision_summary": {
            "baseline": str(conclusion["baseline"]),
            "monthly_action_status": str(conclusion["monthly_action_status"]),
            "current_risk_response": str(conclusion["current_risk_response"]),
            "additional_budget_status": str(conclusion["additional_budget_status"]),
            "orders": [],
        },
        "conflicts": list(conclusion["strategy_conflicts"]),
        "counter_evidence": [
            "prospective_oos_has_zero_complete_months",
            "historical_nav_visibility_is_assumed_not_strict_point_in_time",
            "risk_proxy_is_not_actual_account_history",
            "exact_channel_fees_limits_and_qdii_calendar_are_not_fully_verified",
            "current_account_has_single_fund_concentration_breach",
        ],
        "uncertainties": list(conclusion["uncertainties"]),
        "evidence_ledger": claims,
        "next_review": {
            "date": str(conclusion["next_valid_decision_date"]),
            "required_inputs": list(conclusion["required_before_next_pack"]),
        },
        "llm_contract": {
            "allowed": [
                "translate_structured_evidence_into_plain_language",
                "explain_conflicts_counter_evidence_and_uncertainty",
                "cite_only_the_evidence_ledger_and_locked_sources",
            ],
            "prohibited": [
                "introduce_external_news_or_market_predictions",
                "change_numeric_values_or_strategy_status",
                "invent_missing_evidence",
                "turn_preview_amounts_into_orders",
                "override_risk_or_registry_gates",
            ],
        },
        "execution": {
            "advisory_only": True,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
    report["reproducibility"] = {
        "evidence_bundle_sha256": _canonical_sha256(
            {
                "sources": report["sources"],
                "policy_context": report["policy_context"],
                "portfolio_state": report["portfolio_state"],
                "strategy_views": report["strategy_views"],
                "decision_summary": report["decision_summary"],
                "evidence_ledger": report["evidence_ledger"],
            }
        ),
        "network_used": False,
    }
    return report


def render_research_report_markdown(report: Mapping[str, Any]) -> str:
    """Render only facts already present in the structured report."""

    if report.get("mode") != "research_only" or report.get("execution") != {
        "advisory_only": True,
        "order_intent_generation_allowed": False,
        "real_trading_enabled": False,
        "orders": [],
    }:
        raise ValueError("cannot render an executable or malformed research report")
    current = report["portfolio_state"]
    risk = current["risk_proxy"]
    executive = report["executive_summary"]
    lines = [
        "# 月度投资研究报告（Phase 5.1）",
        "",
        f"> 数据时点：{report['as_of']}｜模式：仅研究、不可执行",
        "",
        "## 结论先行",
        "",
        str(executive["headline"]),
        "",
        "本报告没有使用外部新闻，也不预测市场涨跌；订单数组为空。",
        "",
        "## 当前组合",
        "",
        f"- 总资产：{current['total_assets_cny']}元；现金：{current['cash_cny']}元。",
        f"- 最大持仓：{current['largest_fund_code']}，占比{current['largest_fund_weight_pct']:.2f}%。",
        f"- 当前权重风险代理：年化波动{risk['annualized_volatility_pct']:.2f}%，最大回撤{risk['max_drawdown_pct']:.2f}%，当前回撤{risk['current_drawdown_pct']:.2f}%。",
        "- 上述风险数字是假设当前权重不变的短样本历史代理，不是账户真实收益记录。",
        "",
        "## 四个策略怎么看",
        "",
        "| 策略 | 观察结果 | 系统处理 |",
        "|---|---|---|",
    ]
    for item in report["strategy_views"]:
        decision_use = (
            "保留为比较权威" if item["target_allocation_authority"] else "仅作研究证据"
        )
        lines.append(f"| {item['label']} | {item['display_summary']} | {decision_use} |")
    lines.extend(
        [
            "",
            "## 为什么没有采用额外预算",
            "",
            "- 当前账户存在明显单基金集中度超限。",
            "- 回撤策略的独立历史样本仍少，前瞻样本外为0个完整月。",
            "- 历史净值可见时间仍是假设口径，真实渠道费率、限额和QDII日历尚未完全核验。",
            "- 因此，策略触发只代表出现研究信号，不等于允许执行。",
            "",
            "## 下一次有效复核",
            "",
            f"日期：{report['next_review']['date']}。在此之前需要刷新只读持仓快照、使用新截止日前可见的本地数据，并重新运行注册表、数据质量和风险门禁。",
            "",
            "## 安全边界",
            "",
            "本报告仅解释锁定证据。LLM不得修改数值、补造消息、生成订单或绕过风险否决。",
            "",
        ]
    )
    return "\n".join(lines)
