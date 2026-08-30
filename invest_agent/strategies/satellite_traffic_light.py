"""Deterministic buy-only traffic-light targets for thematic fund sleeves."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint

from .dca import MonthlyAllocationPlan, SleeveAllocation


MODEL_VERSION = "satellite_traffic_light_signal_v1"
STRATEGY_ID = "hierarchical_risk_budget_valuation"
STRATEGY_VERSION = "0.2.0"
CENT = Decimal("0.01")
ONE = Decimal("1")
STATE_MULTIPLIERS = {
    "green": Decimal("1"),
    "yellow": Decimal("0.5"),
    "red": Decimal("0"),
    "ineligible": Decimal("0"),
}


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _floor_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_DOWN)


def evaluate_satellite_traffic_light(
    *,
    sleeve: str,
    fund_code: str,
    points: Sequence[NavPoint],
    cutoff_date: date,
    long_average_observations: int = 200,
    momentum_lookback_observations: int = 126,
    minimum_observations: int = 201,
    maximum_staleness_days: int = 10,
    factor_mode: str = "dual",
) -> dict[str, object]:
    if factor_mode not in {"dual", "long_average_only", "momentum_only"}:
        raise ValueError("unsupported traffic-light factor mode")
    required = max(long_average_observations, momentum_lookback_observations + 1)
    if min(
        long_average_observations,
        momentum_lookback_observations,
        minimum_observations,
        maximum_staleness_days,
    ) <= 0:
        raise ValueError("traffic-light windows and staleness must be positive")
    if minimum_observations < required:
        raise ValueError("minimum observations do not cover signal windows")
    visible = tuple(point for point in points if point.nav_date <= cutoff_date)
    reasons: list[str] = []
    if not visible:
        reasons.append("no_observation_at_or_before_cutoff")
        return {
            "sleeve": sleeve,
            "fund_code": fund_code,
            "state": "ineligible",
            "multiplier": "0",
            "reasons": reasons,
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
    if reasons:
        return {
            "sleeve": sleeve,
            "fund_code": fund_code,
            "state": "ineligible",
            "multiplier": "0",
            "reasons": reasons,
            "effective_nav_date": latest.nav_date.isoformat(),
            "features": None,
            "quality": {
                "observations_at_cutoff": len(visible),
                "staleness_calendar_days": staleness,
                "research_only": any(
                    point.visibility_status != "strict_point_in_time"
                    for point in visible
                ),
            },
        }

    long_average = sum(
        (point.nav for point in visible[-long_average_observations:]), Decimal("0")
    ) / Decimal(long_average_observations)
    momentum_start = visible[-(momentum_lookback_observations + 1)]
    momentum_return = latest.nav / momentum_start.nav - ONE
    long_trend_positive = latest.nav > long_average
    momentum_positive = momentum_return > 0
    if factor_mode == "dual":
        condition_count = int(long_trend_positive) + int(momentum_positive)
        state = (
            "green"
            if condition_count == 2
            else "yellow"
            if condition_count == 1
            else "red"
        )
    else:
        selected_positive = (
            long_trend_positive
            if factor_mode == "long_average_only"
            else momentum_positive
        )
        state = "green" if selected_positive else "red"
    reasons = []
    reasons.append(
        "nav_above_long_average" if long_trend_positive else "nav_not_above_long_average"
    )
    reasons.append(
        "momentum_positive" if momentum_positive else "momentum_not_positive"
    )
    result = {
        "sleeve": sleeve,
        "fund_code": fund_code,
        "state": state,
        "multiplier": str(STATE_MULTIPLIERS[state]),
        "reasons": reasons,
        "effective_nav_date": latest.nav_date.isoformat(),
        "features": {
            "latest_nav": str(latest.nav),
            "long_average": str(long_average),
            "long_average_gap": str(latest.nav / long_average - ONE),
            "momentum_return": str(momentum_return),
            "momentum_start_nav_date": momentum_start.nav_date.isoformat(),
            "long_trend_positive": long_trend_positive,
            "momentum_positive": momentum_positive,
        },
        "quality": {
            "observations_at_cutoff": len(visible),
            "staleness_calendar_days": staleness,
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed"
                for point in visible
            ),
            "batch_ids": sorted({point.batch_id for point in visible}),
            "batch_content_sha256": sorted(
                {point.content_sha256 for point in visible}
            ),
            "research_only": any(
                point.visibility_status != "strict_point_in_time"
                for point in visible
            ),
        },
    }
    if factor_mode != "dual":
        result["factor_mode"] = factor_mode
    return result


def build_satellite_traffic_light_signal(
    *,
    review_date: date,
    satellite_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    strategic_weights: Mapping[str, Decimal],
    controlled_satellite_sleeves: Sequence[str] | None = None,
    released_weight_destination: str = "cash",
    long_average_observations: int = 200,
    momentum_lookback_observations: int = 126,
    minimum_observations: int = 201,
    maximum_staleness_days: int = 10,
    factor_mode: str = "dual",
    strategic_floor_multiplier: Decimal | str = Decimal("0"),
) -> dict[str, object]:
    if released_weight_destination not in {"cash", "bond_gold_2_to_1"}:
        raise ValueError("unsupported released-weight destination")
    floor_multiplier = Decimal(str(strategic_floor_multiplier))
    if floor_multiplier < 0 or floor_multiplier > ONE:
        raise ValueError("strategic floor multiplier must be between zero and one")
    weights = {sleeve: Decimal(value) for sleeve, value in strategic_weights.items()}
    if not weights or any(weight < 0 for weight in weights.values()):
        raise ValueError("strategic weights must be non-negative")
    if abs(sum(weights.values(), Decimal("0")) - ONE) > Decimal("0.00000001"):
        raise ValueError("strategic weights must sum to one")
    if set(satellite_series) - set(weights):
        raise ValueError("all satellite sleeves must exist in strategic weights")
    controlled = (
        set(satellite_series)
        if controlled_satellite_sleeves is None
        else {str(sleeve) for sleeve in controlled_satellite_sleeves}
    )
    if not controlled or controlled - set(satellite_series):
        raise ValueError("controlled satellite sleeves must be a non-empty signal subset")
    if released_weight_destination == "bond_gold_2_to_1" and not {
        "defensive_bond",
        "gold_stabilizer",
    }.issubset(weights):
        raise ValueError("bond/gold parking requires both defensive sleeves")

    cutoff = review_date - timedelta(days=1)
    raw_evaluations = [
        evaluate_satellite_traffic_light(
            sleeve=sleeve,
            fund_code=fund_code,
            points=points,
            cutoff_date=cutoff,
            long_average_observations=long_average_observations,
            momentum_lookback_observations=momentum_lookback_observations,
            minimum_observations=minimum_observations,
            maximum_staleness_days=maximum_staleness_days,
            factor_mode=factor_mode,
        )
        for sleeve, (fund_code, points) in sorted(satellite_series.items())
    ]
    evaluations = [
        {
            **item,
            "controlled": str(item["sleeve"]) in controlled,
            "applied_multiplier": (
                str(
                    floor_multiplier
                    + (ONE - floor_multiplier) * Decimal(str(item["multiplier"]))
                )
                if str(item["sleeve"]) in controlled
                else "1"
            ),
        }
        for item in raw_evaluations
    ]
    effective = dict(weights)
    released = Decimal("0")
    for item in evaluations:
        sleeve = str(item["sleeve"])
        multiplier = Decimal(str(item["applied_multiplier"]))
        scaled = weights[sleeve] * multiplier
        released += weights[sleeve] - scaled
        effective[sleeve] = scaled
    if released_weight_destination == "bond_gold_2_to_1":
        bond_addition = released * Decimal("2") / Decimal("3")
        effective["defensive_bond"] += bond_addition
        effective["gold_stabilizer"] += released - bond_addition
    effective_sum = sum(effective.values(), Decimal("0"))
    cash_target = ONE - effective_sum
    if cash_target < Decimal("-0.00000001"):
        raise ValueError("effective targets cannot exceed the full portfolio")
    cash_target = max(Decimal("0"), cash_target)
    signal_parameters: dict[str, object] = {
        "long_average_observations": long_average_observations,
        "momentum_lookback_observations": momentum_lookback_observations,
        "minimum_observations": minimum_observations,
        "maximum_staleness_days": maximum_staleness_days,
    }
    if factor_mode != "dual":
        signal_parameters["factor_mode"] = factor_mode
    if floor_multiplier != 0:
        signal_parameters["strategic_floor_multiplier"] = str(floor_multiplier)
    if factor_mode != "dual" and floor_multiplier != 0:
        model_version = "satellite_traffic_light_single_factor_strategic_floor_v1"
    elif factor_mode != "dual":
        model_version = "satellite_traffic_light_single_factor_ablation_v1"
    elif floor_multiplier != 0:
        model_version = "satellite_traffic_light_strategic_floor_v1"
    else:
        model_version = MODEL_VERSION
    payload: dict[str, object] = {
        "model_version": model_version,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": cutoff.isoformat(),
        "signal_nav_field": "accumulated_nav",
        "signal_parameters": signal_parameters,
        "controlled_satellite_sleeves": sorted(controlled),
        "satellite_evaluations": evaluations,
        "state_counts": {
            state: sum(item["state"] == state for item in evaluations)
            for state in ("green", "yellow", "red", "ineligible")
        },
        "reason": "monthly_satellite_traffic_light_state_scaling",
        "strategic_target_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(weights.items())
        },
        "effective_target_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(effective.items())
        },
        "released_weight": str(released),
        "released_weight_destination": released_weight_destination,
        "cash_target_weight": str(cash_target),
        "renormalized_after_state_scaling": False,
        "deep_drawdown_recovery_lock_applied": False,
        "selling_allowed": False,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "satlight_" + _canonical_sha256(payload)[:20]
    return payload


def build_state_adjusted_target_gap_allocation(
    *,
    planned_date: date,
    pre_contribution_portfolio_value_cny: Decimal,
    monthly_contribution_cny: Decimal,
    current_position_values_cny: Mapping[str, Decimal],
    prior_available_cash_cny: Decimal,
    nondeployable_cash_cny: Decimal,
    effective_target_weights: Mapping[str, Decimal],
    strategy_spec_sha256: str,
) -> MonthlyAllocationPlan:
    pre_value = _money(pre_contribution_portfolio_value_cny)
    contribution = _money(monthly_contribution_cny)
    positions = {
        sleeve: _money(value) for sleeve, value in current_position_values_cny.items()
    }
    prior_cash = _money(prior_available_cash_cny)
    nondeployable_cash = _money(nondeployable_cash_cny)
    weights = {
        sleeve: Decimal(value) for sleeve, value in effective_target_weights.items()
    }
    if pre_value < 0 or contribution <= 0:
        raise ValueError("portfolio value must be non-negative and contribution positive")
    if set(positions) != set(weights) or not weights:
        raise ValueError("positions and effective target weights must use identical sleeves")
    if any(value < 0 for value in (*positions.values(), prior_cash, nondeployable_cash)):
        raise ValueError("positions and cash cannot be negative")
    target_sum = sum(weights.values(), Decimal("0"))
    if any(weight < 0 for weight in weights.values()) or target_sum > ONE + Decimal(
        "0.00000001"
    ):
        raise ValueError("effective target weights must be non-negative and sum to at most one")
    if abs(
        sum(positions.values(), Decimal("0")) + prior_cash + nondeployable_cash - pre_value
    ) > CENT:
        raise ValueError("positions and cash must reconcile to pre-contribution value")
    if len(strategy_spec_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in strategy_spec_sha256
    ):
        raise ValueError("strategy_spec_sha256 must be a lowercase SHA-256 hex digest")

    post_value = pre_value + contribution
    cash_target_weight = max(Decimal("0"), ONE - target_sum)
    cash_target_value = _money(post_value * cash_target_weight)
    available_after_contribution = prior_cash + contribution
    required_available_cash_reserve = max(
        Decimal("0"), cash_target_value - nondeployable_cash
    )
    purchase_budget = max(
        Decimal("0"), available_after_contribution - required_available_cash_reserve
    )
    raw: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for sleeve in sorted(weights):
        target_value = _money(post_value * weights[sleeve])
        gap = max(Decimal("0"), target_value - positions[sleeve])
        raw.append((sleeve, target_value, gap, weights[sleeve]))
    total_gap = sum((item[2] for item in raw), Decimal("0"))
    planned_total = min(purchase_budget, total_gap)
    basis = total_gap if total_gap > 0 else ONE
    allocations = tuple(
        SleeveAllocation(
            sleeve=sleeve,
            current_value_cny=positions[sleeve],
            target_weight=weight,
            post_contribution_target_cny=target_value,
            positive_gap_cny=gap,
            allocated_cny=_floor_money(
                planned_total * gap / basis if total_gap > 0 else Decimal("0")
            ),
        )
        for sleeve, target_value, gap, weight in raw
    )
    allocated = sum((item.allocated_cny for item in allocations), Decimal("0"))
    if allocated > available_after_contribution:
        raise AssertionError("planned purchases cannot exceed available cash")
    return MonthlyAllocationPlan(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        strategy_spec_sha256=strategy_spec_sha256,
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_value,
        monthly_contribution_cny=contribution,
        allocations=allocations,
        unallocated_cash_cny=available_after_contribution - allocated,
        prior_available_cash_cny=prior_cash,
        nondeployable_cash_cny=nondeployable_cash,
    )
