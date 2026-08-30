"""Quarterly bounded inverse-volatility weights for thematic sleeves."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import math
from statistics import stdev
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint


MODEL_VERSION = "satellite_quarterly_bounded_inverse_volatility_v1"
ONE = Decimal("1")
DEFAULT_CONCENTRATION_GROUP = (
    "global_technology_satellite",
    "semiconductor_satellite",
    "robotics_satellite",
)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _quarter_start(review_date: date) -> date:
    month = ((review_date.month - 1) // 3) * 3 + 1
    return date(review_date.year, month, 1)


def _bounded_proportional_weights(
    scores: Mapping[str, Decimal],
    *,
    lower: Mapping[str, Decimal],
    upper: Mapping[str, Decimal],
    total: Decimal,
) -> dict[str, Decimal]:
    sleeves = tuple(sorted(scores))
    if not sleeves or set(lower) != set(sleeves) or set(upper) != set(sleeves):
        raise ValueError("scores and bounds must use identical non-empty sleeves")
    if any(scores[sleeve] <= 0 for sleeve in sleeves):
        raise ValueError("risk-budget scores must be positive")
    if any(
        lower[sleeve] < 0 or upper[sleeve] < lower[sleeve]
        for sleeve in sleeves
    ):
        raise ValueError("risk-budget bounds are invalid")
    if sum(lower.values(), Decimal("0")) > total or sum(
        upper.values(), Decimal("0")
    ) < total:
        raise ValueError("risk-budget bounds cannot satisfy the requested total")

    def clipped(scale: Decimal) -> dict[str, Decimal]:
        return {
            sleeve: min(
                upper[sleeve], max(lower[sleeve], scale * scores[sleeve])
            )
            for sleeve in sleeves
        }

    low_scale = Decimal("0")
    high_scale = Decimal("1")
    while sum(clipped(high_scale).values(), Decimal("0")) < total:
        high_scale *= 2
    for _ in range(96):
        midpoint = (low_scale + high_scale) / 2
        if sum(clipped(midpoint).values(), Decimal("0")) < total:
            low_scale = midpoint
        else:
            high_scale = midpoint
    result = clipped((low_scale + high_scale) / 2)
    residual = total - sum(result.values(), Decimal("0"))
    if residual > 0:
        for sleeve in sleeves:
            addition = min(residual, upper[sleeve] - result[sleeve])
            result[sleeve] += addition
            residual -= addition
            if residual == 0:
                break
    elif residual < 0:
        for sleeve in sleeves:
            reduction = min(-residual, result[sleeve] - lower[sleeve])
            result[sleeve] -= reduction
            residual += reduction
            if residual == 0:
                break
    if residual != 0:
        raise ValueError("risk-budget projection did not reconcile")
    return result


def _project_with_group_cap(
    scores: Mapping[str, Decimal],
    *,
    minimum_share: Decimal,
    maximum_share: Decimal,
    concentration_group: Sequence[str],
    concentration_group_cap: Decimal,
) -> dict[str, Decimal]:
    sleeves = set(scores)
    group = set(concentration_group)
    outside = sleeves - group
    if not group or not outside or group - sleeves:
        raise ValueError("concentration group must be a proper sleeve subset")
    lower = {sleeve: minimum_share for sleeve in sleeves}
    upper = {sleeve: maximum_share for sleeve in sleeves}
    unconstrained = _bounded_proportional_weights(
        scores, lower=lower, upper=upper, total=ONE
    )
    if sum((unconstrained[sleeve] for sleeve in group), Decimal("0")) <= concentration_group_cap:
        return unconstrained

    group_scores = {sleeve: scores[sleeve] for sleeve in group}
    outside_scores = {sleeve: scores[sleeve] for sleeve in outside}
    group_weights = _bounded_proportional_weights(
        group_scores,
        lower={sleeve: lower[sleeve] for sleeve in group},
        upper={sleeve: upper[sleeve] for sleeve in group},
        total=concentration_group_cap,
    )
    outside_weights = _bounded_proportional_weights(
        outside_scores,
        lower={sleeve: lower[sleeve] for sleeve in outside},
        upper={sleeve: upper[sleeve] for sleeve in outside},
        total=ONE - concentration_group_cap,
    )
    return {**group_weights, **outside_weights}


def build_satellite_risk_budget_signal(
    *,
    review_date: date,
    satellite_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    strategic_weights: Mapping[str, Decimal],
    lookback_return_observations: int = 252,
    minimum_observations: int = 253,
    maximum_staleness_days: int = 10,
    minimum_theme_share: Decimal | str = Decimal("0.15"),
    maximum_theme_share: Decimal | str = Decimal("0.35"),
    concentration_group_sleeves: Sequence[str] = DEFAULT_CONCENTRATION_GROUP,
    concentration_group_cap: Decimal | str = Decimal("0.70"),
) -> dict[str, object]:
    if not satellite_series:
        raise ValueError("satellite series cannot be empty")
    weights = {sleeve: Decimal(value) for sleeve, value in strategic_weights.items()}
    satellites = set(satellite_series)
    if satellites - set(weights):
        raise ValueError("all satellite sleeves must exist in strategic weights")
    if abs(sum(weights.values(), Decimal("0")) - ONE) > Decimal("0.00000001"):
        raise ValueError("strategic weights must sum to one")
    if min(lookback_return_observations, minimum_observations, maximum_staleness_days) <= 0:
        raise ValueError("risk-budget observations and staleness must be positive")
    if minimum_observations < lookback_return_observations + 1:
        raise ValueError("minimum observations must cover all return observations")
    minimum_share = Decimal(str(minimum_theme_share))
    maximum_share = Decimal(str(maximum_theme_share))
    group_cap = Decimal(str(concentration_group_cap))
    if not 0 <= minimum_share <= maximum_share <= ONE:
        raise ValueError("theme share bounds must be between zero and one")
    if not 0 < group_cap < ONE:
        raise ValueError("concentration group cap must be between zero and one")

    effective_quarter_start = _quarter_start(review_date)
    cutoff = effective_quarter_start - timedelta(days=1)
    evaluations: list[dict[str, object]] = []
    scores: dict[str, Decimal] = {}
    all_eligible = True
    for sleeve, (fund_code, points) in sorted(satellite_series.items()):
        visible = tuple(point for point in points if point.nav_date <= cutoff)
        reasons: list[str] = []
        if not visible:
            reasons.append("no_observation_at_or_before_cutoff")
            latest_date = None
            staleness = None
        else:
            latest_date = visible[-1].nav_date
            staleness = (cutoff - latest_date).days
            if staleness > maximum_staleness_days:
                reasons.append("latest_observation_is_stale")
            if len(visible) < minimum_observations:
                reasons.append("insufficient_observations")
        annualized_volatility = None
        if not reasons:
            window = visible[-(lookback_return_observations + 1) :]
            returns = [
                float(current.nav / previous.nav - ONE)
                for previous, current in zip(window, window[1:])
            ]
            annualized_volatility = stdev(returns) * math.sqrt(252)
            if annualized_volatility <= 0:
                reasons.append("nonpositive_volatility")
            else:
                scores[sleeve] = ONE / Decimal(str(annualized_volatility))
        eligible = not reasons
        all_eligible = all_eligible and eligible
        evaluations.append(
            {
                "sleeve": sleeve,
                "fund_code": fund_code,
                "eligible": eligible,
                "reasons": reasons or ["eligible"],
                "effective_nav_date": None if latest_date is None else latest_date.isoformat(),
                "annualized_volatility": (
                    None if annualized_volatility is None else str(annualized_volatility)
                ),
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
        )

    if all_eligible:
        weight_source = "bounded_inverse_volatility"
    else:
        weight_source = "constraint_neutral_fallback"
        scores = {sleeve: ONE for sleeve in satellites}
    satellite_shares = _project_with_group_cap(
        scores,
        minimum_share=minimum_share,
        maximum_share=maximum_share,
        concentration_group=concentration_group_sleeves,
        concentration_group_cap=group_cap,
    )
    satellite_total_weight = sum((weights[sleeve] for sleeve in satellites), Decimal("0"))
    effective = dict(weights)
    for sleeve in satellites:
        effective[sleeve] = satellite_total_weight * satellite_shares[sleeve]
    if abs(sum(effective.values(), Decimal("0")) - ONE) > Decimal("0.00000001"):
        raise ValueError("effective risk-budget weights must sum to one")

    payload: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "mode": "research_only",
        "review_date": review_date.isoformat(),
        "effective_quarter_start": effective_quarter_start.isoformat(),
        "signal_cutoff_date": cutoff.isoformat(),
        "signal_nav_field": "accumulated_nav",
        "signal_parameters": {
            "lookback_return_observations": lookback_return_observations,
            "minimum_observations": minimum_observations,
            "maximum_staleness_days": maximum_staleness_days,
            "minimum_theme_share": str(minimum_share),
            "maximum_theme_share": str(maximum_share),
            "concentration_group_sleeves": sorted(concentration_group_sleeves),
            "concentration_group_cap": str(group_cap),
        },
        "weight_source": weight_source,
        "satellite_evaluations": evaluations,
        "satellite_total_weight": str(satellite_total_weight),
        "satellite_shares": {
            sleeve: str(weight) for sleeve, weight in sorted(satellite_shares.items())
        },
        "strategic_target_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(weights.items())
        },
        "effective_target_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(effective.items())
        },
        "selling_allowed": False,
        "released_weight": "0",
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["signal_id"] = "satrisk_" + _canonical_sha256(payload)[:20]
    return payload
