"""Point-in-time event studies for frozen monthly traffic-light signals."""

from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint


RATE = Decimal("0.000000000001")


def _rate(value: Decimal) -> str:
    return str(value.quantize(RATE, rounding=ROUND_HALF_UP))


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _point_at_or_before(
    points: Sequence[NavPoint], target: date, maximum_staleness_days: int
) -> NavPoint | None:
    dates = [point.nav_date for point in points]
    index = bisect_right(dates, target) - 1
    if index < 0:
        return None
    point = points[index]
    if (target - point.nav_date).days > maximum_staleness_days:
        return None
    return point


def _series_binding(points: Sequence[NavPoint]) -> str:
    return _canonical_sha256(
        [
            {
                "date": point.nav_date.isoformat(),
                "nav": str(point.nav),
                "visibility_status": point.visibility_status,
                "batch_id": point.batch_id,
                "content_sha256": point.content_sha256,
            }
            for point in points
        ]
    )


def analyze_traffic_light_forward_returns(
    *,
    signal_ledger: Sequence[Mapping[str, object]],
    theme_series: Mapping[str, tuple[str, Sequence[NavPoint]]],
    defensive_series: Mapping[str, Sequence[NavPoint]],
    defensive_weights: Mapping[str, Decimal],
    evaluation_end_date: date,
    horizons_months: Sequence[int] = (1, 3, 6),
    maximum_staleness_days: int = 10,
) -> dict[str, object]:
    """Evaluate already-frozen signals using forward returns unavailable to the signal."""

    if not signal_ledger or not theme_series:
        raise ValueError("signal ledger and theme series are required")
    if not defensive_series or set(defensive_series) != set(defensive_weights):
        raise ValueError("defensive series and weights must use identical sleeves")
    if sum(defensive_weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("defensive weights must sum to one")
    if (
        not horizons_months
        or len(set(horizons_months)) != len(horizons_months)
        or any(months <= 0 for months in horizons_months)
        or maximum_staleness_days <= 0
    ):
        raise ValueError("horizons and staleness must be unique positive values")

    expected_funds = {sleeve: fund for sleeve, (fund, _points) in theme_series.items()}
    events: list[dict[str, object]] = []
    ineligible_counts: dict[str, int] = {sleeve: 0 for sleeve in theme_series}
    for signal in signal_ledger:
        cutoff = date.fromisoformat(str(signal.get("signal_cutoff_date", "")))
        evaluations = signal.get("satellite_evaluations")
        if not isinstance(evaluations, list):
            raise ValueError("every signal requires satellite evaluations")
        by_sleeve = {
            str(item.get("sleeve")): item
            for item in evaluations
            if isinstance(item, Mapping)
        }
        if set(by_sleeve) != set(theme_series):
            raise ValueError("signal themes and event-study themes diverged")
        for sleeve, (fund_code, points) in sorted(theme_series.items()):
            evaluation = by_sleeve[sleeve]
            if str(evaluation.get("fund_code")) != fund_code or expected_funds[sleeve] != fund_code:
                raise ValueError("signal fund and event-study fund diverged")
            state = str(evaluation.get("state", ""))
            if state == "ineligible":
                ineligible_counts[sleeve] += 1
                continue
            if state not in {"green", "yellow", "red"}:
                raise ValueError("unsupported traffic-light state")
            start = _point_at_or_before(points, cutoff, maximum_staleness_days)
            if start is None or start.nav_date.isoformat() != evaluation.get("effective_nav_date"):
                raise ValueError("signal start point cannot be reproduced")
            if evaluation.get("features") is None:
                raise ValueError("eligible signal must retain frozen features")

            defensive_starts: dict[str, NavPoint] = {}
            for defensive_sleeve, defensive_points in defensive_series.items():
                point = _point_at_or_before(
                    defensive_points, cutoff, maximum_staleness_days
                )
                if point is None:
                    raise ValueError("defensive start point is unavailable")
                defensive_starts[defensive_sleeve] = point

            for horizon in sorted(horizons_months):
                target = _add_months(cutoff, horizon)
                if target > evaluation_end_date:
                    continue
                end = _point_at_or_before(points, target, maximum_staleness_days)
                if end is None or end.nav_date <= start.nav_date:
                    continue
                theme_return = end.nav / start.nav - Decimal("1")
                defensive_return = Decimal("0")
                defensive_end_dates: dict[str, str] = {}
                complete = True
                for defensive_sleeve, defensive_points in defensive_series.items():
                    defensive_end = _point_at_or_before(
                        defensive_points, target, maximum_staleness_days
                    )
                    if defensive_end is None or defensive_end.nav_date <= defensive_starts[defensive_sleeve].nav_date:
                        complete = False
                        break
                    defensive_return += defensive_weights[defensive_sleeve] * (
                        defensive_end.nav / defensive_starts[defensive_sleeve].nav
                        - Decimal("1")
                    )
                    defensive_end_dates[defensive_sleeve] = defensive_end.nav_date.isoformat()
                if not complete:
                    continue
                events.append(
                    {
                        "sleeve": sleeve,
                        "fund_code": fund_code,
                        "state": state,
                        "signal_cutoff_date": cutoff.isoformat(),
                        "signal_nav_date": start.nav_date.isoformat(),
                        "horizon_months": horizon,
                        "target_date": target.isoformat(),
                        "theme_end_nav_date": end.nav_date.isoformat(),
                        "defensive_end_nav_dates": defensive_end_dates,
                        "theme_forward_return": _rate(theme_return),
                        "defensive_forward_return": _rate(defensive_return),
                        "theme_minus_defensive_return": _rate(
                            theme_return - defensive_return
                        ),
                    }
                )

    grouped: dict[tuple[str, str, int], list[Mapping[str, object]]] = {}
    for event in events:
        key = (
            str(event["sleeve"]),
            str(event["state"]),
            int(event["horizon_months"]),
        )
        grouped.setdefault(key, []).append(event)
    summaries: list[dict[str, object]] = []
    for (sleeve, state, horizon), rows in sorted(grouped.items()):
        relative = sorted(
            Decimal(str(row["theme_minus_defensive_return"])) for row in rows
        )
        theme_returns = [Decimal(str(row["theme_forward_return"])) for row in rows]
        defensive_returns = [
            Decimal(str(row["defensive_forward_return"])) for row in rows
        ]
        midpoint = len(relative) // 2
        median = (
            relative[midpoint]
            if len(relative) % 2
            else (relative[midpoint - 1] + relative[midpoint]) / Decimal("2")
        )
        underperformed = sum(value < 0 for value in relative)
        summaries.append(
            {
                "sleeve": sleeve,
                "state": state,
                "horizon_months": horizon,
                "complete_events": len(rows),
                "mean_theme_forward_return": _rate(
                    sum(theme_returns, Decimal("0")) / Decimal(len(rows))
                ),
                "mean_defensive_forward_return": _rate(
                    sum(defensive_returns, Decimal("0")) / Decimal(len(rows))
                ),
                "mean_theme_minus_defensive_return": _rate(
                    sum(relative, Decimal("0")) / Decimal(len(rows))
                ),
                "median_theme_minus_defensive_return": _rate(median),
                "theme_underperformed_defensive_count": underperformed,
                "theme_underperformed_defensive_rate": _rate(
                    Decimal(underperformed) / Decimal(len(rows))
                ),
            }
        )

    result: dict[str, object] = {
        "engine_version": "traffic_light_forward_event_study_v1",
        "mode": "research_only",
        "classification": "historical_signal_diagnostic_not_trading_backtest",
        "evaluation_end_date": evaluation_end_date.isoformat(),
        "horizons_months": sorted(horizons_months),
        "maximum_staleness_days": maximum_staleness_days,
        "defensive_weights": {
            sleeve: str(weight) for sleeve, weight in sorted(defensive_weights.items())
        },
        "events": events,
        "summaries": summaries,
        "ineligible_signal_months": ineligible_counts,
        "data_bindings": {
            "themes": {
                sleeve: {"fund_code": fund, "series_sha256": _series_binding(points)}
                for sleeve, (fund, points) in sorted(theme_series.items())
            },
            "defensive": {
                sleeve: _series_binding(points)
                for sleeve, points in sorted(defensive_series.items())
            },
        },
        "interpretation": {
            "future_returns_used_for_evaluation_only": True,
            "ineligible_signals_excluded_from_state_return_statistics": True,
            "defensive_leg_is_static_two_asset_weighted_return": True,
            "automatic_parameter_selection": False,
        },
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result
