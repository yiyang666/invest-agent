"""Research-only technology/growth sleeve drawdown signal and allocation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint
from .dca import MonthlyAllocationPlan, SleeveAllocation, build_monthly_allocation
from .drawdown_add import build_drawdown_budget_signal


BASE = Decimal("3000")
MAXIMUM = Decimal("5000")
CENT = Decimal("0.01")
MODEL_VERSION = "sleeve_drawdown_recovery_signal_v1"


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _single_drawdown(points: Sequence[NavPoint], cutoff: date, window: int, minimum: int) -> dict[str, object] | None:
    visible = tuple(point for point in points if point.nav_date <= cutoff)
    if len(visible) < minimum:
        return None
    rolling = visible[-window:]
    peak = max(rolling, key=lambda point: (point.nav, point.nav_date))
    latest = visible[-1]
    return {
        "latest_nav_date": latest.nav_date.isoformat(),
        "peak_date": peak.nav_date.isoformat(),
        "drawdown": str(latest.nav / peak.nav - Decimal("1")),
        "observations": len(visible),
        "batch_content_sha256": sorted({point.content_sha256 for point in visible}),
        "research_only": any(point.visibility_status != "strict_point_in_time" for point in visible),
    }


def build_sleeve_drawdown_signal(
    *,
    review_date: date,
    candidate_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    portfolio_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    portfolio_weights: Mapping[str, Decimal],
    rolling_peak_observations: int = 126,
    minimum_observations: int = 127,
) -> dict[str, object]:
    cutoff = review_date - timedelta(days=1)
    candidate_rows = {
        sleeve: _single_drawdown(points, cutoff, rolling_peak_observations, minimum_observations)
        for sleeve, (_code, points) in candidate_series.items()
    }
    portfolio = build_drawdown_budget_signal(
        review_date=review_date,
        fund_series=portfolio_series,
        weights=portfolio_weights,
        rolling_peak_observations=rolling_peak_observations,
        minimum_composite_observations=minimum_observations,
    )
    composite = portfolio.get("composite")
    portfolio_veto = isinstance(composite, Mapping) and Decimal(str(composite["drawdown"])) <= Decimal("-0.20")
    usable = {sleeve: row for sleeve, row in candidate_rows.items() if row is not None}
    if len(usable) != len(candidate_rows) or not isinstance(composite, Mapping):
        additional, tier, triggered = Decimal("0"), "base_only_insufficient_signal_data", []
    else:
        deepest = min(Decimal(str(row["drawdown"])) for row in usable.values())
        triggered = sorted(sleeve for sleeve, row in usable.items() if Decimal(str(row["drawdown"])) <= Decimal("-0.10"))
        if portfolio_veto:
            additional, tier = Decimal("0"), "portfolio_drawdown_risk_veto"
        elif deepest <= Decimal("-0.20"):
            additional, tier = Decimal("2000"), "sleeve_drawdown_at_or_below_20pct"
        elif deepest <= Decimal("-0.15"):
            additional, tier = Decimal("1000"), "sleeve_drawdown_at_or_below_15pct"
        elif deepest <= Decimal("-0.10"):
            additional, tier = Decimal("500"), "sleeve_drawdown_at_or_below_10pct"
        else:
            additional, tier, triggered = Decimal("0"), "normal_sleeve_drawdown_above_10pct", []
    payload: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": cutoff.isoformat(),
        "reason": tier,
        "portfolio_drawdown": None if not isinstance(composite, Mapping) else composite["drawdown"],
        "portfolio_risk_veto": portfolio_veto,
        "candidate_drawdowns": candidate_rows,
        "triggered_sleeves": triggered,
        "budget": {"base_contribution_cny": "3000.00", "additional_budget_cny": f"{additional:.2f}", "total_contribution_cny": f"{BASE + additional:.2f}"},
        "selling_allowed": False,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "sleevedd_" + _sha(payload)[:20]
    return payload


def _cent_split(total: Decimal, keys: Sequence[str]) -> dict[str, Decimal]:
    if not keys:
        return {}
    each = (total / Decimal(len(keys))).quantize(CENT, rounding=ROUND_DOWN)
    result = {key: each for key in sorted(keys)}
    remainder = total - sum(result.values(), Decimal("0"))
    for key in sorted(keys):
        if remainder < CENT:
            break
        result[key] += CENT
        remainder -= CENT
    return result


def build_sleeve_drawdown_allocation(
    *,
    planned_date: date,
    pre_contribution_portfolio_value_cny: Decimal,
    current_sleeve_values_cny: Mapping[str, Decimal],
    target_weights: Mapping[str, Decimal],
    signal: Mapping[str, object],
    strategy_spec_sha256: str,
    variant: str,
) -> MonthlyAllocationPlan:
    budget = signal["budget"]
    assert isinstance(budget, Mapping)
    additional = Decimal(str(budget["additional_budget_cny"]))
    total = BASE + additional
    base_plan = build_monthly_allocation(
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_contribution_portfolio_value_cny,
        monthly_contribution_cny=BASE,
        current_sleeve_values_cny=current_sleeve_values_cny,
        target_weights=target_weights,
        strategy_spec_sha256=strategy_spec_sha256,
    )
    additions = {sleeve: Decimal("0") for sleeve in target_weights}
    if variant == "broad_core":
        additions.update(_cent_split(additional, ("domestic_broad_core", "us_broad_core")))
    elif variant == "sleeve_with_broad_fallback":
        theme = ("us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite")
        triggered = tuple(str(value) for value in signal["triggered_sleeves"])
        post_total = pre_contribution_portfolio_value_cny + total
        base_by_sleeve = {item.sleeve: item.allocated_cny for item in base_plan.allocations}
        theme_after_base = sum((current_sleeve_values_cny[s] + base_by_sleeve[s] for s in theme), Decimal("0"))
        theme_capacity = max(Decimal("0"), Decimal("0.30") * post_total - theme_after_base)
        sleeve_total = min(additional, theme_capacity).quantize(CENT, rounding=ROUND_DOWN)
        sleeve_split = _cent_split(sleeve_total, triggered)
        for sleeve, amount in sleeve_split.items():
            single_capacity = max(Decimal("0"), Decimal("0.25") * post_total - current_sleeve_values_cny[sleeve] - base_by_sleeve[sleeve])
            accepted = min(amount, single_capacity).quantize(CENT, rounding=ROUND_DOWN)
            additions[sleeve] += accepted
        accepted_total = sum(additions.values(), Decimal("0"))
        additions.update({key: additions[key] + value for key, value in _cent_split(additional - accepted_total, ("domestic_broad_core", "us_broad_core")).items()})
    else:
        raise ValueError("unsupported sleeve drawdown allocation variant")
    allocations = tuple(
        SleeveAllocation(
            sleeve=item.sleeve,
            current_value_cny=item.current_value_cny,
            target_weight=item.target_weight,
            post_contribution_target_cny=item.post_contribution_target_cny + additions[item.sleeve],
            positive_gap_cny=item.positive_gap_cny + additions[item.sleeve],
            allocated_cny=item.allocated_cny + additions[item.sleeve],
        )
        for item in base_plan.allocations
    )
    return MonthlyAllocationPlan(
        strategy_id="sleeve_drawdown_recovery",
        strategy_version="1.0.0",
        strategy_spec_sha256=strategy_spec_sha256,
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_contribution_portfolio_value_cny,
        monthly_contribution_cny=total,
        allocations=allocations,
        unallocated_cash_cny=base_plan.unallocated_cash_cny,
    )
