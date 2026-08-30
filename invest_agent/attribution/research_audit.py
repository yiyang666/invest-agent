"""Deterministic terminal P&L audit for buy-only research backtests."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Mapping, Sequence


MONEY = Decimal("0.01")


def _decimal(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be decimal-compatible") from exc


def _money(value: Decimal) -> str:
    return str(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _single_profile(result: Mapping[str, object]) -> Mapping[str, object]:
    profiles = result.get("profile_results")
    if not isinstance(profiles, list) or len(profiles) != 1:
        raise ValueError("buy-only sleeve audit requires exactly one profile result")
    profile = profiles[0]
    if not isinstance(profile, Mapping):
        raise ValueError("profile result must be an object")
    return profile


def attribute_buy_only_sleeve_pnl(
    *,
    backtest_result: Mapping[str, object],
    routes: Sequence[Mapping[str, object]],
    sleeve_groups: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Reconcile terminal value to external principal plus sleeve holding P&L.

    This audit intentionally reports money contribution, not a time-weighted sleeve
    return or a multi-period Brinson result. It is valid only for buy-only paths with
    no pending subscriptions, redemptions, or distribution receivables at the end.
    """

    if backtest_result.get("mode") != "research_only":
        raise ValueError("buy-only sleeve audit accepts research-only results")
    profile = _single_profile(backtest_result)
    subscription = profile.get("subscription_result")
    valuation = profile.get("valuation_result")
    metrics = profile.get("metrics")
    if not all(isinstance(item, Mapping) for item in (subscription, valuation, metrics)):
        raise ValueError("subscription, valuation, and metrics are required")
    assert isinstance(subscription, Mapping)
    assert isinstance(valuation, Mapping)
    assert isinstance(metrics, Mapping)

    fund_to_sleeve: dict[str, str] = {}
    for route in routes:
        fund_code = str(route.get("fund_code", ""))
        sleeve = str(route.get("sleeve", ""))
        if not fund_code or not sleeve:
            raise ValueError("every route requires fund_code and sleeve")
        if fund_code in fund_to_sleeve and fund_to_sleeve[fund_code] != sleeve:
            raise ValueError("one fund cannot map to multiple sleeves")
        fund_to_sleeve[fund_code] = sleeve
    if not fund_to_sleeve:
        raise ValueError("at least one route is required")

    subscription_state = subscription.get("final_state")
    if not isinstance(subscription_state, Mapping):
        raise ValueError("subscription final state is required")
    if subscription_state.get("pending_subscriptions") or subscription_state.get(
        "pending_redemptions"
    ):
        raise ValueError("pending subscriptions or redemptions make the audit ineligible")
    if _decimal(
        subscription_state.get("pending_subscription_cash_cny", "0"),
        "pending subscription cash",
    ) != 0:
        raise ValueError("pending subscription cash must be zero")

    ledger = subscription.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("subscription ledger is required")
    gross_by_sleeve: dict[str, Decimal] = {}
    fees_by_sleeve: dict[str, Decimal] = {}
    external_contributions = Decimal("0")
    confirmed_event_ids: set[str] = set()
    for event in ledger:
        if not isinstance(event, Mapping):
            raise ValueError("ledger event must be an object")
        event_type = str(event.get("event_type", ""))
        if "redemption" in event_type:
            raise ValueError("redemption events are not supported by the buy-only audit")
        if event_type == "cash_contributed":
            external_contributions += _decimal(event.get("amount_cny"), "contribution")
        if event_type != "subscription_confirmed":
            continue
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in confirmed_event_ids:
            raise ValueError("confirmed subscription event IDs must be unique")
        confirmed_event_ids.add(event_id)
        fund_code = str(event.get("fund_code", ""))
        if fund_code not in fund_to_sleeve:
            raise ValueError(f"confirmed fund {fund_code} is missing from routes")
        sleeve = fund_to_sleeve[fund_code]
        gross_by_sleeve[sleeve] = gross_by_sleeve.get(sleeve, Decimal("0")) + _decimal(
            event.get("gross_amount_cny"), "gross subscription"
        )
        fees_by_sleeve[sleeve] = fees_by_sleeve.get(sleeve, Decimal("0")) + _decimal(
            event.get("purchase_fee_cny", "0"), "purchase fee"
        )

    valuations = valuation.get("daily_valuations")
    if not isinstance(valuations, list) or not valuations:
        raise ValueError("daily valuations are required")
    final_valuation = valuations[-1]
    if not isinstance(final_valuation, Mapping):
        raise ValueError("final valuation must be an object")
    if _decimal(
        final_valuation.get("pending_subscription_cash_cny", "0"),
        "final pending subscription cash",
    ) != 0 or _decimal(
        final_valuation.get("distribution_receivable_cny", "0"),
        "final distribution receivable",
    ) != 0:
        raise ValueError("pending cash or receivables make the audit ineligible")

    market_by_sleeve: dict[str, Decimal] = {}
    positions = final_valuation.get("positions")
    if not isinstance(positions, list):
        raise ValueError("final positions are required")
    for position in positions:
        if not isinstance(position, Mapping):
            raise ValueError("position must be an object")
        fund_code = str(position.get("fund_code", ""))
        if fund_code not in fund_to_sleeve:
            raise ValueError(f"position fund {fund_code} is missing from routes")
        sleeve = fund_to_sleeve[fund_code]
        market_by_sleeve[sleeve] = market_by_sleeve.get(sleeve, Decimal("0")) + _decimal(
            position.get("market_value_cny"), "market value"
        )

    distributions_by_sleeve: dict[str, Decimal] = {}
    distribution_ledger = valuation.get("distribution_ledger", [])
    if not isinstance(distribution_ledger, list):
        raise ValueError("distribution ledger must be a list")
    for event in distribution_ledger:
        if not isinstance(event, Mapping) or event.get("event_type") != "cash_distribution_paid":
            continue
        fund_code = str(event.get("fund_code", ""))
        if fund_code not in fund_to_sleeve:
            raise ValueError(f"distribution fund {fund_code} is missing from routes")
        sleeve = fund_to_sleeve[fund_code]
        distributions_by_sleeve[sleeve] = distributions_by_sleeve.get(
            sleeve, Decimal("0")
        ) + _decimal(event.get("amount_cny"), "cash distribution")

    sleeves = sorted(set(fund_to_sleeve.values()))
    rows: list[dict[str, object]] = []
    group_totals: dict[str, dict[str, Decimal]] = {}
    total_pnl = Decimal("0")
    total_gross = Decimal("0")
    total_fees = Decimal("0")
    total_distributions = Decimal("0")
    total_market = Decimal("0")
    for sleeve in sleeves:
        gross = gross_by_sleeve.get(sleeve, Decimal("0"))
        fees = fees_by_sleeve.get(sleeve, Decimal("0"))
        distributions = distributions_by_sleeve.get(sleeve, Decimal("0"))
        market = market_by_sleeve.get(sleeve, Decimal("0"))
        pnl = market + distributions - gross
        group = str(sleeve_groups.get(sleeve, "ungrouped")) if sleeve_groups else "ungrouped"
        rows.append(
            {
                "sleeve": sleeve,
                "group": group,
                "gross_subscriptions_cny": _money(gross),
                "purchase_fees_cny": _money(fees),
                "cash_distributions_cny": _money(distributions),
                "ending_market_value_cny": _money(market),
                "holding_pnl_cny": _money(pnl),
            }
        )
        totals = group_totals.setdefault(
            group,
            {"gross": Decimal("0"), "fees": Decimal("0"), "distributions": Decimal("0"), "market": Decimal("0"), "pnl": Decimal("0")},
        )
        totals["gross"] += gross
        totals["fees"] += fees
        totals["distributions"] += distributions
        totals["market"] += market
        totals["pnl"] += pnl
        total_gross += gross
        total_fees += fees
        total_distributions += distributions
        total_market += market
        total_pnl += pnl

    ending_cash = _decimal(final_valuation.get("available_cash_cny"), "ending cash")
    reported_invested = _decimal(
        final_valuation.get("invested_value_cny", total_market),
        "reported invested value",
    )
    valuation_rounding_residual = reported_invested - total_market
    final_value = _decimal(final_valuation.get("total_value_cny"), "final value")
    reported_final = _decimal(metrics.get("final_value_cny"), "reported final value")
    reported_fees = _decimal(metrics.get("purchase_fees_cny"), "reported fees")
    if abs(final_value - reported_final) > MONEY:
        raise ValueError("final valuation and reported metrics diverged")
    if abs(total_fees - reported_fees) > MONEY:
        raise ValueError("ledger fees and reported fees diverged")
    if abs(reported_invested + ending_cash - final_value) > MONEY:
        raise ValueError("ending market value and cash do not reconcile")
    if abs(
        external_contributions + total_pnl + valuation_rounding_residual - final_value
    ) > MONEY:
        raise ValueError("external principal plus sleeve P&L does not reconcile")
    expected_cash = external_contributions + total_distributions - total_gross
    if abs(expected_cash - ending_cash) > MONEY:
        raise ValueError("cash conservation does not reconcile")

    result: dict[str, object] = {
        "engine_version": "buy_only_sleeve_pnl_audit_v1",
        "method": "terminal_holding_pnl_with_explicit_cash_conservation",
        "scenario_id": backtest_result.get("scenario_id"),
        "classification": "research_only_terminal_money_contribution_not_time_weighted_return",
        "evidence_bindings": {
            "backtest_result_sha256": _canonical_sha256(backtest_result),
            "routes_sha256": _canonical_sha256(list(routes)),
        },
        "sleeves": rows,
        "groups": [
            {
                "group": group,
                "gross_subscriptions_cny": _money(values["gross"]),
                "purchase_fees_cny": _money(values["fees"]),
                "cash_distributions_cny": _money(values["distributions"]),
                "ending_market_value_cny": _money(values["market"]),
                "holding_pnl_cny": _money(values["pnl"]),
            }
            for group, values in sorted(group_totals.items())
        ],
        "summary": {
            "external_contributions_cny": _money(external_contributions),
            "gross_subscriptions_cny": _money(total_gross),
            "purchase_fees_cny": _money(total_fees),
            "cash_distributions_cny": _money(total_distributions),
            "ending_market_value_cny": _money(total_market),
            "reported_invested_value_cny": _money(reported_invested),
            "valuation_rounding_residual_cny": _money(valuation_rounding_residual),
            "ending_cash_cny": _money(ending_cash),
            "holding_pnl_cny": _money(total_pnl),
            "final_value_cny": _money(final_value),
        },
        "identity_checks": {
            "external_principal_plus_holding_pnl_and_rounding_residual_equals_final_value": True,
            "gross_subscriptions_plus_ending_cash_equals_external_principal_plus_distributions": True,
            "reported_invested_value_plus_cash_equals_final_value": True,
            "no_pending_subscriptions_or_redemptions": True,
        },
        "limitations": {
            "buy_only_paths_only": True,
            "not_multi_period_brinson": True,
            "not_time_weighted_sleeve_return": True,
            "money_contribution_depends_on_cashflow_timing": True,
        },
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result
