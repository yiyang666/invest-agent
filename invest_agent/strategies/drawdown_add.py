"""Deterministic research-only signal for monthly drawdown-budget additions."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint, load_nav_series
from invest_agent.strategies.dca import (
    MonthlyAllocationPlan,
    SleeveAllocation,
    build_monthly_allocation,
)


MODEL_VERSION = "drawdown_budget_add_signal_v1"
BASE_CONTRIBUTION_CNY = Decimal("3000")
MAXIMUM_CONTRIBUTION_CNY = Decimal("5000")
STRATEGY_ID = "drawdown_budget_add"
STRATEGY_VERSION = "1.0.0"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _base_only_signal(
    *,
    review_date: date,
    cutoff_date: date,
    reason: str,
    quality: Mapping[str, object],
    signal_parameters: Mapping[str, object],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": cutoff_date.isoformat(),
        "signal_nav_field": "accumulated_nav",
        "signal_status": "base_only",
        "reason": reason,
        "signal_parameters": dict(signal_parameters),
        "composite": None,
        "budget": {
            "base_contribution_cny": "3000.00",
            "additional_budget_cny": "0.00",
            "total_contribution_cny": "3000.00",
            "additional_destinations_cny": {
                "domestic_broad_core": "0.00",
                "us_broad_core": "0.00",
            },
        },
        "risk_veto": False,
        "quality": dict(quality),
        "selling_allowed": False,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "drawdownadd_" + _canonical_sha256(payload)[:20]
    return payload


def build_drawdown_budget_signal(
    *,
    review_date: date,
    fund_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    weights: Mapping[str, Decimal],
    rolling_peak_observations: int = 126,
    minimum_composite_observations: int = 127,
    maximum_staleness_days: int = 10,
    first_threshold: Decimal = Decimal("-0.05"),
    second_threshold: Decimal = Decimal("-0.10"),
    third_threshold: Decimal = Decimal("-0.15"),
    risk_veto_threshold: Decimal = Decimal("-0.20"),
) -> dict[str, object]:
    cutoff = review_date - timedelta(days=1)
    sleeves = set(fund_series)
    normalized_weights = {sleeve: Decimal(value) for sleeve, value in weights.items()}
    if sleeves != set(normalized_weights) or not sleeves:
        raise ValueError("fund series and weights must have identical non-empty sleeves")
    if any(value < 0 for value in normalized_weights.values()):
        raise ValueError("composite weights cannot be negative")
    if sum(normalized_weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("composite weights must sum exactly to 1")
    if rolling_peak_observations < 2:
        raise ValueError("rolling peak observations must be at least 2")
    if minimum_composite_observations < rolling_peak_observations + 1:
        raise ValueError("minimum observations must exceed the rolling peak window")
    if maximum_staleness_days <= 0:
        raise ValueError("maximum staleness must be positive")
    thresholds = tuple(
        Decimal(value)
        for value in (
            first_threshold,
            second_threshold,
            third_threshold,
            risk_veto_threshold,
        )
    )
    if not (
        Decimal("0")
        > thresholds[0]
        > thresholds[1]
        > thresholds[2]
        > thresholds[3]
    ):
        raise ValueError("drawdown thresholds must be strictly decreasing below zero")
    signal_parameters: dict[str, object] = {
        "rolling_peak_observations": rolling_peak_observations,
        "minimum_composite_observations": minimum_composite_observations,
        "maximum_staleness_days": maximum_staleness_days,
        "first_threshold": str(thresholds[0]),
        "second_threshold": str(thresholds[1]),
        "third_threshold": str(thresholds[2]),
        "risk_veto_threshold": str(thresholds[3]),
    }

    visible_by_sleeve: dict[str, tuple[NavPoint, ...]] = {
        sleeve: tuple(point for point in points if point.nav_date <= cutoff)
        for sleeve, (_fund_code, points) in fund_series.items()
    }
    if any(not points for points in visible_by_sleeve.values()):
        return _base_only_signal(
            review_date=review_date,
            cutoff_date=cutoff,
            reason="missing_fund_history_at_or_before_cutoff",
            quality={
                "research_only": True,
                "common_observations": 0,
                "historical_visibility_assumed": 0,
            },
            signal_parameters=signal_parameters,
        )
    common_dates = set(point.nav_date for point in next(iter(visible_by_sleeve.values())))
    for points in visible_by_sleeve.values():
        common_dates.intersection_update(point.nav_date for point in points)
    ordered_dates = sorted(common_dates)
    if len(ordered_dates) < minimum_composite_observations:
        return _base_only_signal(
            review_date=review_date,
            cutoff_date=cutoff,
            reason="insufficient_exact_common_date_observations",
            quality={
                "research_only": True,
                "common_observations": len(ordered_dates),
                "latest_common_nav_date": (
                    ordered_dates[-1].isoformat() if ordered_dates else None
                ),
                "historical_visibility_assumed": sum(
                    point.visibility_status == "historical_visibility_assumed"
                    for points in visible_by_sleeve.values()
                    for point in points
                    if point.nav_date in common_dates
                ),
            },
            signal_parameters=signal_parameters,
        )
    latest_common_date = ordered_dates[-1]
    staleness = (cutoff - latest_common_date).days
    if staleness > maximum_staleness_days:
        return _base_only_signal(
            review_date=review_date,
            cutoff_date=cutoff,
            reason="latest_exact_common_date_is_stale",
            quality={
                "research_only": True,
                "common_observations": len(ordered_dates),
                "latest_common_nav_date": latest_common_date.isoformat(),
                "staleness_calendar_days": staleness,
            },
            signal_parameters=signal_parameters,
        )

    nav_by_sleeve_date = {
        sleeve: {point.nav_date: point for point in points}
        for sleeve, points in visible_by_sleeve.items()
    }
    levels: list[tuple[date, Decimal]] = [(ordered_dates[0], Decimal("1"))]
    previous = ordered_dates[0]
    for current in ordered_dates[1:]:
        weighted_return = sum(
            (
                normalized_weights[sleeve]
                * (
                    nav_by_sleeve_date[sleeve][current].nav
                    / nav_by_sleeve_date[sleeve][previous].nav
                    - Decimal("1")
                )
                for sleeve in sorted(sleeves)
            ),
            Decimal("0"),
        )
        levels.append((current, levels[-1][1] * (Decimal("1") + weighted_return)))
        previous = current
    rolling = levels[-rolling_peak_observations:]
    peak_date, peak_level = max(rolling, key=lambda item: (item[1], item[0]))
    latest_level = levels[-1][1]
    drawdown = latest_level / peak_level - Decimal("1")

    risk_veto = drawdown <= thresholds[3]
    if risk_veto:
        additional = Decimal("0")
        tier = "risk_veto_at_or_below_20pct"
    elif drawdown <= thresholds[2]:
        additional = Decimal("2000")
        tier = "drawdown_at_or_below_15pct"
    elif drawdown <= thresholds[1]:
        additional = Decimal("1000")
        tier = "drawdown_at_or_below_10pct"
    elif drawdown <= thresholds[0]:
        additional = Decimal("500")
        tier = "drawdown_at_or_below_5pct"
    else:
        additional = Decimal("0")
        tier = "normal_drawdown_above_5pct"
    total = BASE_CONTRIBUTION_CNY + additional
    if total > MAXIMUM_CONTRIBUTION_CNY:
        raise AssertionError("drawdown signal exceeded monthly contribution cap")
    used_points = [
        nav_by_sleeve_date[sleeve][nav_date]
        for sleeve in sorted(sleeves)
        for nav_date in ordered_dates
    ]
    payload: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": cutoff.isoformat(),
        "signal_nav_field": "accumulated_nav",
        "signal_status": "risk_veto" if risk_veto else "eligible",
        "reason": tier,
        "signal_parameters": signal_parameters,
        "composite": {
            "method": "chain_fixed_631_weighted_returns_on_exact_common_nav_dates",
            "first_common_nav_date": ordered_dates[0].isoformat(),
            "latest_common_nav_date": latest_common_date.isoformat(),
            "common_observations": len(ordered_dates),
            "rolling_peak_observations": rolling_peak_observations,
            "latest_level": str(latest_level),
            "rolling_peak_level": str(peak_level),
            "rolling_peak_date": peak_date.isoformat(),
            "drawdown": str(drawdown),
            "weights": {
                sleeve: str(normalized_weights[sleeve]) for sleeve in sorted(sleeves)
            },
        },
        "budget": {
            "base_contribution_cny": f"{BASE_CONTRIBUTION_CNY:.2f}",
            "additional_budget_cny": f"{additional:.2f}",
            "total_contribution_cny": f"{total:.2f}",
            "additional_destinations_cny": {
                "domestic_broad_core": f"{additional / Decimal('2'):.2f}",
                "us_broad_core": f"{additional / Decimal('2'):.2f}",
            },
        },
        "risk_veto": risk_veto,
        "quality": {
            "research_only": any(
                point.visibility_status != "strict_point_in_time" for point in used_points
            ),
            "staleness_calendar_days": staleness,
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed"
                for point in used_points
            ),
            "fund_codes": {
                sleeve: fund_series[sleeve][0] for sleeve in sorted(sleeves)
            },
            "batch_ids": sorted({point.batch_id for point in used_points}),
            "batch_content_sha256": sorted(
                {point.content_sha256 for point in used_points}
            ),
        },
        "selling_allowed": False,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "drawdownadd_" + _canonical_sha256(payload)[:20]
    return payload


def calculate_drawdown_budget_signal(
    database: str | Path,
    *,
    review_date: date,
    funds: Mapping[str, str],
    weights: Mapping[str, Decimal],
    provider_id: str = "akshare_eastmoney",
) -> dict[str, object]:
    cutoff = review_date - timedelta(days=1)
    series = {
        sleeve: (
            fund_code,
            load_nav_series(
                database,
                fund_code=fund_code,
                provider_id=provider_id,
                as_of=cutoff,
                nav_field="accumulated_nav",
            ),
        )
        for sleeve, fund_code in funds.items()
    }
    result = build_drawdown_budget_signal(
        review_date=review_date,
        fund_series=series,
        weights=weights,
    )
    result["provider_id"] = provider_id
    return result


def build_drawdown_monthly_allocation(
    *,
    planned_date: date,
    pre_contribution_portfolio_value_cny: Decimal,
    current_sleeve_values_cny: Mapping[str, Decimal],
    target_weights: Mapping[str, Decimal],
    signal: Mapping[str, object],
    strategy_spec_sha256: str,
) -> MonthlyAllocationPlan:
    """Combine the normal 631 allocation with signal-capped broad-index additions."""
    budget = signal.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("drawdown signal budget is required")
    base = Decimal(str(budget["base_contribution_cny"]))
    additional = Decimal(str(budget["additional_budget_cny"]))
    total = Decimal(str(budget["total_contribution_cny"]))
    if base != BASE_CONTRIBUTION_CNY or base + additional != total:
        raise ValueError("drawdown signal contribution breakdown is inconsistent")
    if additional < 0 or additional > Decimal("2000") or total > MAXIMUM_CONTRIBUTION_CNY:
        raise ValueError("drawdown signal exceeds the approved monthly budget")
    destinations = budget.get("additional_destinations_cny")
    if not isinstance(destinations, Mapping):
        raise ValueError("drawdown signal destinations are required")
    expected_destinations = {"domestic_broad_core", "us_broad_core"}
    if set(destinations) != expected_destinations:
        raise ValueError("additional budget may route only to the two broad cores")
    destination_amounts = {
        sleeve: Decimal(str(value)) for sleeve, value in destinations.items()
    }
    if any(value < 0 for value in destination_amounts.values()) or sum(
        destination_amounts.values(), Decimal("0")
    ) != additional:
        raise ValueError("additional destination amounts must reconcile")
    if bool(signal.get("risk_veto")) and additional != 0:
        raise ValueError("risk-vetoed signal cannot contain additional budget")

    base_plan = build_monthly_allocation(
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_contribution_portfolio_value_cny,
        monthly_contribution_cny=base,
        current_sleeve_values_cny=current_sleeve_values_cny,
        target_weights=target_weights,
        strategy_spec_sha256=strategy_spec_sha256,
    )
    additions = {sleeve: Decimal("0") for sleeve in target_weights}
    additions.update(destination_amounts)
    allocations = tuple(
        SleeveAllocation(
            sleeve=item.sleeve,
            current_value_cny=item.current_value_cny,
            target_weight=item.target_weight,
            post_contribution_target_cny=(
                item.post_contribution_target_cny + additions[item.sleeve]
            ),
            positive_gap_cny=item.positive_gap_cny + additions[item.sleeve],
            allocated_cny=item.allocated_cny + additions[item.sleeve],
        )
        for item in base_plan.allocations
    )
    return MonthlyAllocationPlan(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        strategy_spec_sha256=strategy_spec_sha256,
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=base_plan.pre_contribution_portfolio_value_cny,
        monthly_contribution_cny=total,
        allocations=allocations,
        unallocated_cash_cny=base_plan.unallocated_cash_cny,
    )


def summarize_drawdown_tier_coverage(
    signals: Sequence[Mapping[str, object]],
    *,
    thresholds: Sequence[tuple[str, Decimal]] | None = None,
) -> dict[str, object]:
    """Report observed tier coverage without promoting sparse history to validation."""
    evaluated_thresholds = thresholds or (
        ("5pct", Decimal("-0.05")),
        ("10pct", Decimal("-0.10")),
        ("15pct", Decimal("-0.15")),
        ("20pct_veto", Decimal("-0.20")),
    )
    rows: dict[str, object] = {}
    for tier_id, threshold in evaluated_thresholds:
        active: list[bool] = []
        levels: list[Decimal | None] = []
        for signal in signals:
            composite = signal.get("composite")
            if not isinstance(composite, Mapping):
                active.append(False)
                levels.append(None)
                continue
            drawdown = Decimal(str(composite["drawdown"]))
            active.append(drawdown <= threshold)
            levels.append(Decimal(str(composite["latest_level"])))
        episode_starts = [
            index
            for index, is_active in enumerate(active)
            if is_active and (index == 0 or not active[index - 1])
        ]
        forward: dict[str, object] = {}
        for horizon in (3, 6, 12):
            outcomes: list[str] = []
            for index in episode_starts:
                future_index = index + horizon
                if future_index >= len(levels):
                    continue
                start_level = levels[index]
                future_level = levels[future_index]
                if start_level is None or future_level is None:
                    continue
                outcomes.append(str(future_level / start_level - Decimal("1")))
            forward[f"{horizon}_months"] = {
                "complete_episode_count": len(outcomes),
                "composite_forward_returns": outcomes,
            }
        trigger_months = sum(active)
        episode_count = len(episode_starts)
        if trigger_months == 0:
            status = "historically_unobserved"
        elif episode_count < 3:
            status = "observed_insufficient_for_validation"
        else:
            status = "historical_diagnostic_only"
        rows[tier_id] = {
            "drawdown_threshold": str(threshold),
            "trigger_months": trigger_months,
            "independent_episode_count": episode_count,
            "evidence_status": status,
            "forward_outcomes": forward,
        }
    return {
        "method": "monthly_threshold_crossings_on_signal_composite",
        "historical_visibility_limit": "historical_visibility_assumed",
        "tiers": rows,
    }
