"""Deterministic monthly new-money trend and relative-strength signal."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint, load_nav_series
from invest_agent.strategies.dca import MonthlyAllocationPlan, SleeveAllocation


MODEL_VERSION = "new_money_trend_rs_signal_v1"
STRATEGY_ID = "new_money_trend_rs"
STRATEGY_VERSION = "1.0.0"
CENT = Decimal("0.01")


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _point_at_or_before(points: Sequence[NavPoint], target: date) -> NavPoint | None:
    dates = [point.nav_date for point in points]
    index = bisect_right(dates, target) - 1
    return None if index < 0 else points[index]


def evaluate_trend_candidate(
    *,
    sleeve: str,
    fund_code: str,
    points: Sequence[NavPoint],
    cutoff_date: date,
    minimum_observations: int = 127,
    medium_return_days: int = 91,
    slow_return_days: int = 182,
    slow_average_observations: int = 126,
    maximum_staleness_days: int = 10,
) -> dict[str, object]:
    if minimum_observations < slow_average_observations + 1:
        raise ValueError("minimum_observations must cover the slow average plus one")
    if min(medium_return_days, slow_return_days, maximum_staleness_days) <= 0:
        raise ValueError("return windows and maximum staleness must be positive")
    visible = tuple(point for point in points if point.nav_date <= cutoff_date)
    reasons: list[str] = []
    if not visible:
        reasons.append("no_observation_at_or_before_cutoff")
        return {
            "sleeve": sleeve,
            "fund_code": fund_code,
            "eligible": False,
            "reasons": reasons,
            "score": None,
            "effective_nav_date": None,
            "features": None,
            "quality": {"observations_at_cutoff": 0, "research_only": True},
        }
    latest = visible[-1]
    staleness = (cutoff_date - latest.nav_date).days
    if staleness > maximum_staleness_days:
        reasons.append("latest_observation_is_stale")
    if len(visible) < minimum_observations:
        reasons.append("insufficient_observations")
    medium_start = _point_at_or_before(
        visible, latest.nav_date - timedelta(days=medium_return_days)
    )
    slow_start = _point_at_or_before(
        visible, latest.nav_date - timedelta(days=slow_return_days)
    )
    if medium_start is None:
        reasons.append("missing_medium_return_start")
    if slow_start is None:
        reasons.append("missing_slow_return_start")
    if len(visible) < slow_average_observations:
        reasons.append("missing_slow_average_history")
    if reasons:
        return {
            "sleeve": sleeve,
            "fund_code": fund_code,
            "eligible": False,
            "reasons": reasons,
            "score": None,
            "effective_nav_date": latest.nav_date.isoformat(),
            "features": None,
            "quality": {
                "observations_at_cutoff": len(visible),
                "staleness_calendar_days": staleness,
                "research_only": any(point.visibility_status != "strict_point_in_time" for point in visible),
            },
        }
    assert medium_start is not None and slow_start is not None
    medium_return = latest.nav / medium_start.nav - Decimal("1")
    slow_return = latest.nav / slow_start.nav - Decimal("1")
    slow_average = sum(
        (point.nav for point in visible[-slow_average_observations:]), Decimal("0")
    ) / Decimal(slow_average_observations)
    slow_average_gap = latest.nav / slow_average - Decimal("1")
    if slow_return <= 0:
        reasons.append("slow_return_is_not_positive")
    if slow_average_gap <= 0:
        reasons.append("nav_is_not_above_slow_average")
    score = (medium_return + slow_return) / Decimal("2")
    return {
        "sleeve": sleeve,
        "fund_code": fund_code,
        "eligible": not reasons,
        "reasons": reasons or ["positive_slow_return_and_above_slow_average"],
        "score": str(score),
        "effective_nav_date": latest.nav_date.isoformat(),
        "features": {
            "medium_return": str(medium_return),
            "slow_return": str(slow_return),
            "slow_average_gap": str(slow_average_gap),
            "medium_start_nav_date": medium_start.nav_date.isoformat(),
            "slow_start_nav_date": slow_start.nav_date.isoformat(),
        },
        "quality": {
            "observations_at_cutoff": len(visible),
            "staleness_calendar_days": staleness,
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed" for point in visible
            ),
            "batch_ids": sorted({point.batch_id for point in visible}),
            "batch_content_sha256": sorted({point.content_sha256 for point in visible}),
            "research_only": any(point.visibility_status != "strict_point_in_time" for point in visible),
        },
    }


def build_new_money_trend_signal(
    *,
    review_date: date,
    candidate_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    minimum_observations: int = 127,
    medium_return_days: int = 91,
    slow_return_days: int = 182,
    slow_average_observations: int = 126,
    maximum_staleness_days: int = 10,
) -> dict[str, object]:
    cutoff = review_date - timedelta(days=1)
    evaluations = [
        evaluate_trend_candidate(
            sleeve=sleeve,
            fund_code=fund_code,
            points=points,
            cutoff_date=cutoff,
            minimum_observations=minimum_observations,
            medium_return_days=medium_return_days,
            slow_return_days=slow_return_days,
            slow_average_observations=slow_average_observations,
            maximum_staleness_days=maximum_staleness_days,
        )
        for sleeve, (fund_code, points) in sorted(candidate_series.items())
    ]
    eligible = [item for item in evaluations if item["eligible"]]
    eligible.sort(key=lambda item: (-Decimal(str(item["score"])), str(item["fund_code"])))
    selected = eligible[:2]
    allocations = {
        "domestic_broad_core": Decimal("0.20"),
        "us_broad_core": Decimal("0.20"),
        "defensive": Decimal("0.30"),
        **{sleeve: Decimal("0") for sleeve in candidate_series},
    }
    for item in selected:
        allocations[str(item["sleeve"])] += Decimal("0.15")
    allocations["defensive"] += Decimal("0.15") * (2 - len(selected))
    if sum(allocations.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("new-money trend allocation must sum exactly to 1")
    payload = {
        "model_version": MODEL_VERSION,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": cutoff.isoformat(),
        "signal_nav_field": "accumulated_nav",
        "signal_parameters": {
            "minimum_observations": minimum_observations,
            "medium_return_days": medium_return_days,
            "slow_return_days": slow_return_days,
            "slow_average_observations": slow_average_observations,
            "maximum_staleness_days": maximum_staleness_days,
        },
        "candidate_evaluations": evaluations,
        "selected_fund_codes": [str(item["fund_code"]) for item in selected],
        "selected_sleeves": [str(item["sleeve"]) for item in selected],
        "new_money_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(allocations.items())
        },
        "selling_allowed": False,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "trendrs_" + _canonical_sha256(payload)[:20]
    return payload


def build_direct_new_money_allocation(
    *,
    planned_date: date,
    pre_contribution_portfolio_value_cny: Decimal,
    monthly_contribution_cny: Decimal,
    current_sleeve_values_cny: Mapping[str, Decimal],
    new_money_weights: Mapping[str, Decimal],
    strategy_spec_sha256: str,
    remainder_sleeve: str = "defensive",
) -> MonthlyAllocationPlan:
    """Allocate only the new contribution by signal weights; never rebalance holdings."""
    pre_value = Decimal(pre_contribution_portfolio_value_cny).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    contribution = Decimal(monthly_contribution_cny).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    current = {
        sleeve: Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
        for sleeve, value in current_sleeve_values_cny.items()
    }
    weights = {sleeve: Decimal(value) for sleeve, value in new_money_weights.items()}
    if contribution <= 0 or pre_value < 0:
        raise ValueError("portfolio value must be non-negative and contribution positive")
    if set(current) != set(weights) or not weights:
        raise ValueError("current sleeves and new-money weights must have identical keys")
    if remainder_sleeve not in weights:
        raise ValueError("remainder sleeve must exist in new-money weights")
    if any(value < 0 for value in current.values()) or any(
        value < 0 for value in weights.values()
    ):
        raise ValueError("current values and new-money weights cannot be negative")
    if abs(sum(current.values(), Decimal("0")) - pre_value) > CENT:
        raise ValueError("current sleeve values must reconcile to portfolio value")
    if abs(sum(weights.values(), Decimal("0")) - Decimal("1")) > Decimal(
        "0.00000001"
    ):
        raise ValueError("new-money weights must sum to 1")
    if len(strategy_spec_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in strategy_spec_sha256
    ):
        raise ValueError("strategy_spec_sha256 must be a lowercase SHA-256 hex digest")

    allocated = {
        sleeve: (contribution * weight).quantize(CENT, rounding=ROUND_DOWN)
        for sleeve, weight in weights.items()
    }
    allocated[remainder_sleeve] += contribution - sum(
        allocated.values(), Decimal("0")
    )
    allocations = tuple(
        SleeveAllocation(
            sleeve=sleeve,
            current_value_cny=current[sleeve],
            target_weight=weights[sleeve],
            post_contribution_target_cny=current[sleeve] + allocated[sleeve],
            positive_gap_cny=allocated[sleeve],
            allocated_cny=allocated[sleeve],
        )
        for sleeve in sorted(weights)
    )
    return MonthlyAllocationPlan(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        strategy_spec_sha256=strategy_spec_sha256,
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_value,
        monthly_contribution_cny=contribution,
        allocations=allocations,
        unallocated_cash_cny=Decimal("0.00"),
    )


def calculate_new_money_trend_signal(
    database: str | Path,
    *,
    review_date: date,
    candidates: Mapping[str, str],
    provider_id: str = "akshare_eastmoney",
) -> dict[str, object]:
    series = {
        sleeve: (
            fund_code,
            load_nav_series(
                database,
                fund_code=fund_code,
                provider_id=provider_id,
                as_of=review_date - timedelta(days=1),
                nav_field="accumulated_nav",
            ),
        )
        for sleeve, fund_code in candidates.items()
    }
    result = build_new_money_trend_signal(
        review_date=review_date,
        candidate_series=series,
    )
    result["provider_id"] = provider_id
    return result
