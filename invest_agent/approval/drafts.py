"""Fail-closed conversion from frozen research schedules to mock order drafts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Mapping

from invest_agent.domain.fund_routes import PurchaseRoutePool

from .contracts import OrderAction, OrderIntent, SHA256_RE


CENT = Decimal("0.01")
ALLOWED_RISK_POSTURES = {"within_current_research_limits"}


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


@dataclass(frozen=True)
class MockOrderDraft:
    simulation_id: str
    sleeve: str
    intent: OrderIntent

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "sleeve": self.sleeve,
            "intent_id": self.intent.intent_id,
            "intent_sha256": self.intent.sha256,
            "order": self.intent.to_dict(),
            "mode": "mock_only",
            "submittable": False,
            "real_trading_enabled": False,
        }


@dataclass(frozen=True)
class MockDraftBundle:
    source_pack_sha256: str
    purchase_route_pool_id: str
    drafts: tuple[MockOrderDraft, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "source_pack_sha256": self.source_pack_sha256,
            "purchase_route_pool_id": self.purchase_route_pool_id,
            "drafts": [item.to_dict() for item in self.drafts],
            "mode": "mock_only",
            "network_used": False,
            "external_side_effects": False,
            "real_trading_enabled": False,
        }
        payload["bundle_sha256"] = _canonical_sha256(payload)
        return payload


def build_mock_purchase_drafts(
    *,
    decision_pack: Mapping[str, Any],
    source_pack_sha256: str,
    simulated_subscription_plan: Mapping[str, Any],
    purchase_route_pool: PurchaseRoutePool,
    account_alias: str,
) -> MockDraftBundle:
    """Convert only an eligible frozen plan; never submit or contact a provider."""

    if not SHA256_RE.fullmatch(source_pack_sha256):
        raise ValueError("source pack sha256 is invalid")
    if source_pack_sha256 != _canonical_sha256(decision_pack):
        raise ValueError("source pack sha256 does not match decision pack")
    if decision_pack.get("mode") != "research_only":
        raise ValueError("source decision pack must remain research_only")
    execution = _mapping(decision_pack.get("execution"), "execution")
    if execution.get("orders") != [] or execution.get("real_trading_enabled") is not False:
        raise ValueError("source decision pack must contain zero real orders")
    if execution.get("order_intent_generation_allowed") is not False:
        raise ValueError("source decision pack execution contract is invalid")

    portfolio = _mapping(decision_pack.get("current_portfolio"), "current_portfolio")
    if portfolio.get("risk_posture") not in ALLOWED_RISK_POSTURES:
        raise ValueError("risk posture does not allow mock draft generation")
    preview = _mapping(portfolio.get("target_gap_preview"), "target_gap_preview")
    if preview.get("preview_only") is not False:
        raise ValueError("diagnostic preview cannot generate mock order drafts")
    scheduled_dates = preview.get("scheduled_purchase_dates")
    if not isinstance(scheduled_dates, list) or not scheduled_dates:
        raise ValueError("frozen schedule requires purchase dates")
    scheduled_date_set = {date.fromisoformat(str(item)) for item in scheduled_dates}

    conclusion = _mapping(decision_pack.get("research_conclusion"), "research_conclusion")
    if conclusion.get("monthly_action_status") != "mock_schedule_frozen":
        raise ValueError("monthly plan is not frozen for mock drafting")

    if simulated_subscription_plan.get("external_side_effects") is not False or (
        simulated_subscription_plan.get("real_order_submission_available") is not False
    ):
        raise ValueError("subscription plan must remain simulation-only")
    if simulated_subscription_plan.get("issues") not in ([], ()):
        raise ValueError("subscription plan with unresolved issues cannot be drafted")
    allocation = _mapping(
        simulated_subscription_plan.get("allocation_plan"), "allocation_plan"
    )
    authorities = [
        item
        for item in _mapping(
            decision_pack.get("strategy_decision_input"), "strategy_decision_input"
        ).get("accepted_signals", [])
        if isinstance(item, Mapping) and item.get("target_allocation_authority") is True
    ]
    if len(authorities) != 1 or authorities[0].get("strategy_id") != "dca_baseline":
        raise ValueError("one dca_baseline target authority is required")
    if allocation.get("strategy_id") != "dca_baseline" or allocation.get(
        "strategy_version"
    ) != authorities[0].get("strategy_version"):
        raise ValueError("subscription plan strategy does not match target authority")

    active_routes = {route.fund_code: route for route in purchase_route_pool.routes}
    subscriptions = simulated_subscription_plan.get("simulated_subscriptions")
    if not isinstance(subscriptions, list) or not subscriptions:
        raise ValueError("subscription plan contains no subscriptions")
    drafts: list[MockOrderDraft] = []
    seen_simulations: set[str] = set()
    route_daily_totals: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    shared_daily_totals: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    for item in subscriptions:
        subscription = _mapping(item, "simulated_subscription")
        simulation_id = str(subscription.get("simulation_id", ""))
        if not simulation_id or simulation_id in seen_simulations:
            raise ValueError("simulation IDs must be present and unique")
        seen_simulations.add(simulation_id)
        if subscription.get("submitted") is not False:
            raise ValueError("a simulated subscription cannot claim submission")
        if subscription.get("action") != "subscribe":
            raise ValueError("mock purchase drafts require subscribe simulations")
        if subscription.get("account") is not None or subscription.get(
            "payment_method"
        ) is not None:
            raise ValueError("source simulation cannot contain account credentials")
        fund_code = str(subscription.get("fund_code", ""))
        route = active_routes.get(fund_code)
        if route is None or not route.is_purchase_candidate:
            raise ValueError(f"fund route is not an active purchase candidate: {fund_code}")
        if route.purchase_fee_rate is None:
            raise ValueError(f"purchase fee is unknown for route: {fund_code}")
        sleeve = str(subscription.get("sleeve", ""))
        if sleeve != route.sleeve:
            raise ValueError("subscription sleeve does not match purchase route")
        submit_date = date.fromisoformat(str(subscription.get("simulated_submit_date")))
        if submit_date not in scheduled_date_set:
            raise ValueError("subscription date is outside the frozen schedule")
        gross_amount = Decimal(str(subscription.get("gross_amount_cny")))
        if not gross_amount.is_finite() or gross_amount <= 0:
            raise ValueError("subscription amount must be a positive finite amount")
        if gross_amount != gross_amount.quantize(CENT):
            raise ValueError("subscription amount must use cent precision")
        if gross_amount < route.minimum_order_cny:
            raise ValueError("subscription amount is below the route minimum")
        route_daily_key = (fund_code, submit_date)
        route_daily_totals[route_daily_key] += gross_amount
        if route.daily_cap_cny is not None and (
            route_daily_totals[route_daily_key] > route.daily_cap_cny
        ):
            raise ValueError("subscription plan exceeds the route daily cap")
        if route.shared_daily_cap_group is not None:
            shared_daily_key = (route.shared_daily_cap_group, submit_date)
            shared_daily_totals[shared_daily_key] += gross_amount
            if shared_daily_totals[shared_daily_key] > route.shared_daily_cap_cny:
                raise ValueError("subscription plan exceeds the shared daily cap")
        fee = (gross_amount * route.purchase_fee_rate).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        rule_version = str(subscription.get("rule_version", "")).strip()
        if not rule_version:
            raise ValueError("subscription route rule version is required")
        evidence_sha256 = _canonical_sha256(
            {
                "source_pack_sha256": source_pack_sha256,
                "purchase_route_pool_id": purchase_route_pool.pool_id,
                "purchase_route_pool_as_of": purchase_route_pool.as_of.isoformat(),
                "subscription": dict(subscription),
            }
        )
        intent = OrderIntent(
            fund_code=fund_code,
            fund_name=route.fund_name,
            action=OrderAction.PURCHASE,
            amount_cny=gross_amount,
            shares=None,
            order_reference=None,
            scheduled_date=submit_date,
            source_evidence_sha256=evidence_sha256,
            route_rule_version=(
                f"{purchase_route_pool.pool_id}:{fund_code}:"
                f"{route.rule_effective_at.isoformat()}"
            ),
            account_alias=account_alias,
            estimated_fee_cny=fee,
            expected_result="mock_subscription_pending_fund_confirmation",
        )
        drafts.append(MockOrderDraft(simulation_id, sleeve, intent))
    return MockDraftBundle(
        source_pack_sha256=source_pack_sha256,
        purchase_route_pool_id=purchase_route_pool.pool_id,
        drafts=tuple(drafts),
    )


def render_mock_approval_summary(draft: MockOrderDraft) -> str:
    order = draft.intent.to_dict()
    return "\n".join(
        (
            "## 模拟订单批准摘要",
            "",
            "> 仅mock，不会连接交易接口或产生真实订单。",
            "",
            f"- 基金：{order['fund_name']}（{order['fund_code']}）",
            f"- 动作：{order['action']}",
            f"- 金额：{order['amount_cny']}元",
            f"- 模拟日期：{order['scheduled_date']}",
            f"- 账户别名：{order['account_alias']}",
            f"- 预计费用：{order['estimated_fee_cny']}元",
            f"- 预期结果：{order['expected_result']}",
            f"- 路由规则：{order['route_rule_version']}",
            f"- 来源证据：{order['source_evidence_sha256']}",
            f"- 精确摘要：{draft.intent.sha256}",
            "- 真实交易：关闭",
        )
    )
