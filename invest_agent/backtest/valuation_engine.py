"""Daily unit-NAV valuation and explicit distribution events for research backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import re
from typing import Mapping, Sequence

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CENT = Decimal("0.01")


def _text(value: Decimal) -> str:
    return format(value, "f")


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):.2f}"


def _round(value: Decimal, precision: int, mode: str) -> Decimal:
    if not 0 <= precision <= 8:
        raise ValueError("precision must be between 0 and 8")
    if mode not in {"down", "half_up"}:
        raise ValueError("rounding mode must be down or half_up")
    quantum = Decimal("1").scaleb(-precision)
    rounding = ROUND_DOWN if mode == "down" else ROUND_HALF_UP
    return value.quantize(quantum, rounding=rounding)


@dataclass(frozen=True)
class DailyUnitNav:
    fund_code: str
    nav_date: date
    visible_date: date
    unit_nav: Decimal
    nav_batch_sha256: str
    visibility_status: str


@dataclass(frozen=True)
class CashDistribution:
    distribution_id: str
    fund_code: str
    entitlement_date: date
    payment_date: date
    cash_per_share: Decimal
    cash_precision: int
    cash_rounding_mode: str
    source_batch_sha256: str
    visibility_status: str


@dataclass(frozen=True)
class ReinvestedDistribution:
    distribution_id: str
    fund_code: str
    entitlement_date: date
    reinvestment_nav_date: date
    nav_visible_date: date
    confirmation_date: date
    cash_per_share: Decimal
    reinvestment_nav: Decimal
    cash_precision: int
    cash_rounding_mode: str
    share_precision: int
    share_rounding_mode: str
    source_batch_sha256: str
    visibility_status: str


def _validate_hash(value: str, field_name: str) -> None:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_visibility(value: str) -> None:
    if value not in {"strict_point_in_time", "historical_visibility_assumed"}:
        raise ValueError("unsupported visibility status")


def _validate_inputs(
    *,
    end_date: date,
    subscription_result: Mapping[str, object],
    nav_observations: Sequence[DailyUnitNav],
    cash_distributions: Sequence[CashDistribution],
    reinvested_distributions: Sequence[ReinvestedDistribution],
) -> None:
    if subscription_result.get("mode") != "research_only":
        raise ValueError("valuation accepts only research_only subscription results")
    subscription_end = date.fromisoformat(str(subscription_result["end_date"]))
    if subscription_end < end_date:
        raise ValueError("subscription result does not cover valuation end_date")
    if not isinstance(subscription_result.get("ledger"), list):
        raise ValueError("subscription result ledger is required")

    seen_nav: set[tuple[str, date]] = set()
    for item in nav_observations:
        if FUND_CODE_PATTERN.fullmatch(item.fund_code) is None:
            raise ValueError("fund codes must contain six digits")
        if item.unit_nav <= 0 or not item.unit_nav.is_finite():
            raise ValueError("unit NAV must be positive and finite")
        if item.nav_date > item.visible_date:
            raise ValueError("NAV cannot be visible before its nav_date")
        key = (item.fund_code, item.visible_date)
        if key in seen_nav:
            raise ValueError("only one visible NAV per fund and date is allowed")
        seen_nav.add(key)
        _validate_hash(item.nav_batch_sha256, "nav_batch_sha256")
        _validate_visibility(item.visibility_status)

    seen_distributions: set[str] = set()
    for item in cash_distributions:
        if not item.distribution_id or item.distribution_id in seen_distributions:
            raise ValueError("distribution IDs must be non-empty and unique")
        seen_distributions.add(item.distribution_id)
        if FUND_CODE_PATTERN.fullmatch(item.fund_code) is None:
            raise ValueError("fund codes must contain six digits")
        if item.cash_per_share <= 0 or not item.cash_per_share.is_finite():
            raise ValueError("cash_per_share must be positive and finite")
        if item.payment_date < item.entitlement_date:
            raise ValueError("cash distribution payment precedes entitlement")
        _round(Decimal("1"), item.cash_precision, item.cash_rounding_mode)
        _validate_hash(item.source_batch_sha256, "source_batch_sha256")
        _validate_visibility(item.visibility_status)

    for item in reinvested_distributions:
        if not item.distribution_id or item.distribution_id in seen_distributions:
            raise ValueError("distribution IDs must be non-empty and unique")
        seen_distributions.add(item.distribution_id)
        if FUND_CODE_PATTERN.fullmatch(item.fund_code) is None:
            raise ValueError("fund codes must contain six digits")
        if item.cash_per_share <= 0 or item.reinvestment_nav <= 0:
            raise ValueError("distribution cash and reinvestment NAV must be positive")
        if not (
            item.entitlement_date
            <= item.reinvestment_nav_date
            <= item.nav_visible_date
            <= item.confirmation_date
        ):
            raise ValueError("reinvestment dates violate entitlement/NAV/visibility/confirmation order")
        _round(Decimal("1"), item.cash_precision, item.cash_rounding_mode)
        _round(Decimal("1"), item.share_precision, item.share_rounding_mode)
        _validate_hash(item.source_batch_sha256, "source_batch_sha256")
        _validate_visibility(item.visibility_status)


def run_portfolio_valuation_events(
    *,
    end_date: date,
    subscription_result: Mapping[str, object],
    nav_observations: Sequence[DailyUnitNav],
    cash_distributions: Sequence[CashDistribution] = (),
    reinvested_distributions: Sequence[ReinvestedDistribution] = (),
) -> dict[str, object]:
    """Replay subscription state, distributions, and visible unit NAVs without filling gaps."""

    _validate_inputs(
        end_date=end_date,
        subscription_result=subscription_result,
        nav_observations=nav_observations,
        cash_distributions=cash_distributions,
        reinvested_distributions=reinvested_distributions,
    )
    summary = subscription_result["summary"]
    assert isinstance(summary, Mapping)
    available_cash = Decimal(str(summary["initial_cash_cny"]))
    pending_subscription_cash = Decimal("0")
    available_shares: dict[str, Decimal] = {}
    receivables: dict[str, Decimal] = {}
    event_ledger: list[dict[str, object]] = []
    daily_valuations: list[dict[str, object]] = []
    skipped_valuations: list[dict[str, object]] = []
    used_nav_hashes: set[str] = set()
    used_distribution_hashes: set[str] = set()
    assumed_visibility_events = 0

    scheduled: list[tuple[date, int, str, str, object]] = []
    source_ledger = subscription_result["ledger"]
    assert isinstance(source_ledger, list)
    source_priorities = {
        "cash_contributed": 0,
        "subscription_submitted": 1,
        "subscription_confirmed": 2,
    }
    for index, item in enumerate(source_ledger):
        if not isinstance(item, Mapping):
            raise ValueError("subscription ledger entries must be objects")
        event_type = str(item.get("event_type"))
        if event_type not in source_priorities:
            continue
        event_date = date.fromisoformat(str(item["event_date"]))
        if event_date <= end_date:
            scheduled.append(
                (
                    event_date,
                    source_priorities[event_type],
                    f"subscription-{index:08d}",
                    event_type,
                    item,
                )
            )
    for item in cash_distributions:
        if item.entitlement_date <= end_date:
            scheduled.append(
                (item.entitlement_date, 3, item.distribution_id, "cash_entitlement", item)
            )
        if item.payment_date <= end_date:
            scheduled.append((item.payment_date, 4, item.distribution_id, "cash_payment", item))
    for item in reinvested_distributions:
        if item.entitlement_date <= end_date:
            scheduled.append(
                (item.entitlement_date, 3, item.distribution_id, "reinvest_entitlement", item)
            )
        if item.confirmation_date <= end_date:
            scheduled.append(
                (item.confirmation_date, 4, item.distribution_id, "reinvest_confirmation", item)
            )

    navs_by_visible_date: dict[date, dict[str, DailyUnitNav]] = {}
    for item in nav_observations:
        if item.visible_date <= end_date:
            navs_by_visible_date.setdefault(item.visible_date, {})[item.fund_code] = item
    for visible_date in navs_by_visible_date:
        scheduled.append((visible_date, 9, visible_date.isoformat(), "valuation", navs_by_visible_date[visible_date]))
    scheduled.sort(key=lambda item: (item[0], item[1], item[2]))

    for event_date, _priority, event_id, event_type, payload in scheduled:
        if event_type == "cash_contributed":
            assert isinstance(payload, Mapping)
            available_cash += Decimal(str(payload["amount_cny"]))
            continue
        if event_type == "subscription_submitted":
            assert isinstance(payload, Mapping)
            gross = Decimal(str(payload["gross_amount_cny"]))
            available_cash -= gross
            pending_subscription_cash += gross
            continue
        if event_type == "subscription_confirmed":
            assert isinstance(payload, Mapping)
            gross = Decimal(str(payload["gross_amount_cny"]))
            pending_subscription_cash -= gross
            code = str(payload["fund_code"])
            available_shares[code] = available_shares.get(code, Decimal("0")) + Decimal(
                str(payload["confirmed_shares"])
            )
            continue
        if event_type in {"cash_entitlement", "reinvest_entitlement"}:
            item = payload
            assert isinstance(item, (CashDistribution, ReinvestedDistribution))
            eligible_shares = available_shares.get(item.fund_code, Decimal("0"))
            amount = _round(
                eligible_shares * item.cash_per_share,
                item.cash_precision,
                item.cash_rounding_mode,
            )
            receivables[event_id] = amount
            used_distribution_hashes.add(item.source_batch_sha256)
            if item.visibility_status == "historical_visibility_assumed":
                assumed_visibility_events += 1
            event_ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "distribution_entitlement_recorded",
                    "distribution_id": event_id,
                    "fund_code": item.fund_code,
                    "eligible_shares": _text(eligible_shares),
                    "cash_per_share": _text(item.cash_per_share),
                    "receivable_cny": _text(amount),
                    "election": "cash" if event_type == "cash_entitlement" else "reinvest",
                    "visibility_status": item.visibility_status,
                }
            )
            continue
        if event_type == "cash_payment":
            item = payload
            assert isinstance(item, CashDistribution)
            amount = receivables.pop(event_id, Decimal("0"))
            available_cash += amount
            event_ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "cash_distribution_paid",
                    "distribution_id": event_id,
                    "fund_code": item.fund_code,
                    "amount_cny": _text(amount),
                    "available_cash_after_cny": _money(available_cash),
                }
            )
            continue
        if event_type == "reinvest_confirmation":
            item = payload
            assert isinstance(item, ReinvestedDistribution)
            amount = receivables.pop(event_id, Decimal("0"))
            shares = _round(
                amount / item.reinvestment_nav,
                item.share_precision,
                item.share_rounding_mode,
            )
            residual = amount - shares * item.reinvestment_nav
            available_shares[item.fund_code] = (
                available_shares.get(item.fund_code, Decimal("0")) + shares
            )
            available_cash += residual
            event_ledger.append(
                {
                    "event_date": event_date.isoformat(),
                    "event_type": "distribution_reinvested",
                    "distribution_id": event_id,
                    "fund_code": item.fund_code,
                    "reinvestment_nav_date": item.reinvestment_nav_date.isoformat(),
                    "nav_visible_date": item.nav_visible_date.isoformat(),
                    "reinvestment_nav": _text(item.reinvestment_nav),
                    "confirmed_shares": _text(shares),
                    "rounding_residual_cny": _text(residual),
                    "source_batch_sha256": item.source_batch_sha256,
                }
            )
            continue

        assert event_type == "valuation" and isinstance(payload, dict)
        held_codes = sorted(code for code, shares in available_shares.items() if shares > 0)
        missing = [code for code in held_codes if code not in payload]
        if missing:
            skipped_valuations.append(
                {
                    "visible_date": event_date.isoformat(),
                    "reason": "missing_exact_visible_unit_nav",
                    "fund_codes": missing,
                }
            )
            continue
        positions: list[dict[str, object]] = []
        invested_value = Decimal("0")
        for code in held_codes:
            point = payload[code]
            market_value = available_shares[code] * point.unit_nav
            invested_value += market_value
            used_nav_hashes.add(point.nav_batch_sha256)
            if point.visibility_status == "historical_visibility_assumed":
                assumed_visibility_events += 1
            positions.append(
                {
                    "fund_code": code,
                    "shares": _text(available_shares[code]),
                    "nav_date": point.nav_date.isoformat(),
                    "unit_nav": _text(point.unit_nav),
                    "market_value_cny": _money(market_value),
                    "nav_batch_sha256": point.nav_batch_sha256,
                    "visibility_status": point.visibility_status,
                }
            )
        receivable_total = sum(receivables.values(), Decimal("0"))
        total = available_cash + pending_subscription_cash + receivable_total + invested_value
        daily_valuations.append(
            {
                "visible_date": event_date.isoformat(),
                "available_cash_cny": _money(available_cash),
                "pending_subscription_cash_cny": _money(pending_subscription_cash),
                "distribution_receivable_cny": _money(receivable_total),
                "invested_value_cny": _money(invested_value),
                "total_value_cny": _money(total),
                "positions": positions,
            }
        )

    return {
        "engine_version": "portfolio_valuation_events_v1",
        "mode": "research_only",
        "end_date": end_date.isoformat(),
        "distribution_ledger": event_ledger,
        "daily_valuations": daily_valuations,
        "skipped_valuations": skipped_valuations,
        "final_state": {
            "available_cash_cny": _money(available_cash),
            "pending_subscription_cash_cny": _money(pending_subscription_cash),
            "distribution_receivable_cny": _money(sum(receivables.values(), Decimal("0"))),
            "available_shares": {
                code: _text(shares) for code, shares in sorted(available_shares.items())
            },
        },
        "bindings": {
            "nav_batch_sha256": sorted(used_nav_hashes),
            "distribution_batch_sha256": sorted(used_distribution_hashes),
        },
        "quality": {
            "historical_visibility_assumed_events": assumed_visibility_events,
            "no_forward_fill": True,
            "unit_nav_only": True,
            "explicit_distributions_only": True,
            "research_only": True,
        },
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
