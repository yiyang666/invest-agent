"""Subscription event skeleton for research-only off-exchange fund backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import re
from typing import Sequence

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


CENT = Decimal("0.01")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def _text(value: Decimal) -> str:
    return format(value, "f")


def _money_text(value: Decimal) -> str:
    return f"{value:.2f}"


@dataclass(frozen=True)
class CashContribution:
    contribution_id: str
    contribution_date: date
    amount_cny: Decimal


@dataclass(frozen=True)
class SubscriptionExecution:
    simulation_id: str
    fund_code: str
    signal_date: date
    submit_date: date
    execution_nav_date: date
    nav_visible_date: date
    confirmation_date: date
    gross_amount_cny: Decimal
    execution_nav: Decimal
    nav_field: str
    purchase_fee_model: str
    purchase_fee_rate: Decimal
    share_precision: int
    share_rounding_mode: str
    rule_version: str
    rule_effective_date: date
    nav_batch_sha256: str
    visibility_status: str


def _validate_inputs(
    *,
    initial_cash_cny: Decimal,
    strategy_spec_sha256: str,
    contributions: Sequence[CashContribution],
    subscriptions: Sequence[SubscriptionExecution],
) -> None:
    if initial_cash_cny < 0:
        raise ValueError("initial cash cannot be negative")
    if SHA256_PATTERN.fullmatch(strategy_spec_sha256) is None:
        raise ValueError("strategy_spec_sha256 must be a lowercase SHA-256 digest")
    contribution_ids: set[str] = set()
    for item in contributions:
        if not item.contribution_id or item.contribution_id in contribution_ids:
            raise ValueError("contribution IDs must be non-empty and unique")
        contribution_ids.add(item.contribution_id)
        if _money(item.amount_cny) <= 0:
            raise ValueError("contribution amount must be positive")
    simulation_ids: set[str] = set()
    for item in subscriptions:
        if not item.simulation_id or item.simulation_id in simulation_ids:
            raise ValueError("simulation IDs must be non-empty and unique")
        simulation_ids.add(item.simulation_id)
        if FUND_CODE_PATTERN.fullmatch(item.fund_code) is None:
            raise ValueError("fund codes must contain six digits")
        if _money(item.gross_amount_cny) <= 0 or item.execution_nav <= 0:
            raise ValueError("gross amount and execution NAV must be positive")
        if item.purchase_fee_rate < 0:
            raise ValueError("purchase fee rate cannot be negative")
        if item.nav_field != "unit_nav":
            raise ValueError("subscription execution must use unit_nav")
        if item.purchase_fee_model != "proportional_front_end":
            raise ValueError("unsupported purchase fee model")
        if not 0 <= item.share_precision <= 8:
            raise ValueError("share precision must be between 0 and 8")
        if item.share_rounding_mode not in {"down", "half_up"}:
            raise ValueError("unsupported share rounding mode")
        if not item.rule_version:
            raise ValueError("rule version is required")
        if SHA256_PATTERN.fullmatch(item.nav_batch_sha256) is None:
            raise ValueError("nav_batch_sha256 must be a lowercase SHA-256 digest")
        if item.visibility_status not in {
            "strict_point_in_time",
            "historical_visibility_assumed",
        }:
            raise ValueError("unsupported visibility status")
        if not (
            item.signal_date
            <= item.submit_date
            <= item.execution_nav_date
            <= item.nav_visible_date
            <= item.confirmation_date
        ):
            raise ValueError("subscription dates violate signal/submit/NAV/visibility/confirmation order")
        if item.rule_effective_date > item.submit_date:
            raise ValueError("product rule cannot become effective after submission")


def run_subscription_events(
    *,
    end_date: date,
    initial_cash_cny: Decimal,
    contributions: Sequence[CashContribution],
    subscriptions: Sequence[SubscriptionExecution],
    strategy_id: str,
    strategy_version: str,
    strategy_spec_sha256: str,
) -> dict[str, object]:
    """Run contribution, submission, and confirmation events through ``end_date``."""
    _validate_inputs(
        initial_cash_cny=initial_cash_cny,
        strategy_spec_sha256=strategy_spec_sha256,
        contributions=contributions,
        subscriptions=subscriptions,
    )
    initial_cash = _money(initial_cash_cny)
    available_cash = initial_cash
    pending: dict[str, SubscriptionExecution] = {}
    lots: list[dict[str, object]] = []
    available_shares: dict[str, Decimal] = {}
    ledger: list[dict[str, object]] = []
    total_contributions = Decimal("0")
    confirmed_gross = Decimal("0")
    total_fees = Decimal("0")
    total_rounding_adjustment = Decimal("0")
    rejected = 0
    used_nav_hashes: set[str] = set()
    used_rule_versions: set[str] = set()
    assumed_visibility_confirmations = 0

    scheduled: list[tuple[date, int, str, str, object]] = []
    for item in contributions:
        if item.contribution_date <= end_date:
            scheduled.append(
                (item.contribution_date, 0, item.contribution_id, "contribution", item)
            )
    for item in subscriptions:
        if item.submit_date <= end_date:
            scheduled.append((item.submit_date, 1, item.simulation_id, "submit", item))
        if item.confirmation_date <= end_date:
            scheduled.append((item.confirmation_date, 2, item.simulation_id, "confirm", item))
    scheduled.sort(key=lambda item: (item[0], item[1], item[2]))

    for event_date, _priority, event_id, event_type, payload in scheduled:
        if event_type == "contribution":
            contribution = payload
            assert isinstance(contribution, CashContribution)
            amount = _money(contribution.amount_cny)
            available_cash += amount
            total_contributions += amount
            ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "cash_contributed",
                    "event_id": event_id,
                    "amount_cny": _money_text(amount),
                    "available_cash_after_cny": _money_text(available_cash),
                }
            )
            continue

        execution = payload
        assert isinstance(execution, SubscriptionExecution)
        gross = _money(execution.gross_amount_cny)
        if event_type == "submit":
            used_rule_versions.add(execution.rule_version)
            if available_cash < gross:
                rejected += 1
                ledger.append(
                    {
                        "event_date": event_date.isoformat(),
                        "event_type": "subscription_rejected",
                        "event_id": event_id,
                        "fund_code": execution.fund_code,
                        "gross_amount_cny": _money_text(gross),
                        "reason": "insufficient_available_cash",
                        "available_cash_after_cny": _money_text(available_cash),
                    }
                )
                continue
            available_cash -= gross
            pending[event_id] = execution
            ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "subscription_submitted",
                    "event_id": event_id,
                    "fund_code": execution.fund_code,
                    "gross_amount_cny": _money_text(gross),
                    "rule_version": execution.rule_version,
                    "available_cash_after_cny": _money_text(available_cash),
                    "pending_subscription_cash_after_cny": _money_text(
                        sum(
                            (_money(item.gross_amount_cny) for item in pending.values()),
                            Decimal("0"),
                        )
                    ),
                }
            )
            continue

        if event_id not in pending:
            ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "subscription_confirmation_skipped",
                    "event_id": event_id,
                    "fund_code": execution.fund_code,
                    "reason": "submission_not_pending",
                }
            )
            continue
        pending.pop(event_id)
        net_subscription = gross / (Decimal("1") + execution.purchase_fee_rate)
        purchase_fee = gross - net_subscription
        share_quantum = Decimal("1").scaleb(-execution.share_precision)
        rounding_mode = (
            ROUND_DOWN if execution.share_rounding_mode == "down" else ROUND_HALF_UP
        )
        confirmed_shares = (net_subscription / execution.execution_nav).quantize(
            share_quantum, rounding=rounding_mode
        )
        rounding_adjustment = net_subscription - confirmed_shares * execution.execution_nav
        available_shares[execution.fund_code] = (
            available_shares.get(execution.fund_code, Decimal("0")) + confirmed_shares
        )
        confirmed_gross += gross
        total_fees += purchase_fee
        total_rounding_adjustment += rounding_adjustment
        used_nav_hashes.add(execution.nav_batch_sha256)
        if execution.visibility_status == "historical_visibility_assumed":
            assumed_visibility_confirmations += 1
        lot = {
            "lot_id": event_id,
            "fund_code": execution.fund_code,
            "confirmation_date": event_date.isoformat(),
            "shares": _text(confirmed_shares),
            "gross_cost_cny": _money_text(gross),
            "rule_version": execution.rule_version,
        }
        lots.append(lot)
        ledger.append(
            {
                "event_date": event_date.isoformat(),
                "event_type": "subscription_confirmed",
                "event_id": event_id,
                "fund_code": execution.fund_code,
                "signal_date": execution.signal_date.isoformat(),
                "submit_date": execution.submit_date.isoformat(),
                "execution_nav_date": execution.execution_nav_date.isoformat(),
                "nav_visible_date": execution.nav_visible_date.isoformat(),
                "confirmation_date": execution.confirmation_date.isoformat(),
                "execution_nav": _text(execution.execution_nav),
                "gross_amount_cny": _money_text(gross),
                "net_subscription_cny": _text(net_subscription),
                "purchase_fee_cny": _text(purchase_fee),
                "share_rounding_adjustment_cny": _text(rounding_adjustment),
                "confirmed_shares": _text(confirmed_shares),
                "share_rounding_mode": execution.share_rounding_mode,
                "purchase_fee_model": execution.purchase_fee_model,
                "rule_version": execution.rule_version,
                "nav_batch_sha256": execution.nav_batch_sha256,
                "visibility_status": execution.visibility_status,
            }
        )

    pending_cash = sum(
        (_money(item.gross_amount_cny) for item in pending.values()), Decimal("0")
    )
    cash_sources = initial_cash + total_contributions
    cash_uses = available_cash + pending_cash + confirmed_gross
    return {
        "engine_version": "subscription_events_v1",
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_spec_sha256": strategy_spec_sha256,
        "end_date": end_date.isoformat(),
        "mode": "research_only",
        "ledger": ledger,
        "final_state": {
            "available_cash_cny": _money_text(available_cash),
            "pending_subscription_cash_cny": _money_text(pending_cash),
            "pending_subscriptions": [
                {
                    "event_id": key,
                    "fund_code": value.fund_code,
                    "submit_date": value.submit_date.isoformat(),
                    "gross_amount_cny": _money_text(_money(value.gross_amount_cny)),
                    "expected_confirmation_date": value.confirmation_date.isoformat(),
                }
                for key, value in sorted(pending.items())
            ],
            "available_shares": {
                key: _text(value) for key, value in sorted(available_shares.items())
            },
            "share_lots": lots,
            "pending_redemptions": [],
        },
        "summary": {
            "initial_cash_cny": _money_text(initial_cash),
            "contributions_cny": _money_text(total_contributions),
            "confirmed_gross_subscriptions_cny": _money_text(confirmed_gross),
            "purchase_fees_cny": _text(total_fees),
            "share_rounding_adjustment_cny": _text(total_rounding_adjustment),
            "rejected_subscriptions": rejected,
            "pending_subscriptions": len(pending),
            "cash_reconciliation_difference_cny": _money_text(cash_sources - cash_uses),
        },
        "bindings": {
            "nav_batch_sha256": sorted(used_nav_hashes),
            "product_rule_versions": sorted(used_rule_versions),
        },
        "quality": {
            "historical_visibility_assumed_confirmations": assumed_visibility_confirmations,
            "future_nav_values_hidden_until_confirmation": True,
            "no_forward_fill": True,
            "research_only": True,
            "redemption_events_implemented": False,
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
