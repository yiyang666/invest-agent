"""Hypothetical constant-weight portfolio risk from validated local NAV data."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import math
from pathlib import Path
from statistics import stdev
from typing import Mapping

from .fund import NavPoint, _dated_returns, _drawdown, _worst_rolling_return, load_nav_series


def calculate_portfolio_risk(
    database: str | Path,
    *,
    weights: Mapping[str, Decimal],
    cash_weight: Decimal = Decimal("0"),
    provider_id: str = "akshare_eastmoney",
    as_of: date | None = None,
    nav_field: str = "accumulated_nav",
    minimum_overlap: int = 20,
    max_single_fund_weight_pct: Decimal = Decimal("25"),
) -> dict[str, object]:
    """Model today's weights as constant through their common historical window."""
    if not weights:
        raise ValueError("At least one fund weight is required")
    if cash_weight < 0 or any(weight < 0 for weight in weights.values()):
        raise ValueError("weights cannot be negative")
    total_weight = cash_weight + sum(weights.values(), Decimal("0"))
    if abs(total_weight - Decimal("1")) > Decimal("0.000001"):
        raise ValueError("fund and cash weights must sum to 1")
    if minimum_overlap < 2:
        raise ValueError("minimum_overlap must be at least 2")

    returns_by_code: dict[str, dict[date, float]] = {}
    points_by_code: dict[str, tuple[NavPoint, ...]] = {}
    all_points: list[NavPoint] = []
    for code in weights:
        points = load_nav_series(
            database,
            fund_code=code,
            provider_id=provider_id,
            as_of=as_of,
            nav_field=nav_field,
        )
        all_points.extend(points)
        points_by_code[code] = points
        returns_by_code[code] = _dated_returns(points)
    common_dates = sorted(set.intersection(*(set(items) for items in returns_by_code.values())))
    if len(common_dates) < minimum_overlap:
        raise ValueError(
            f"Only {len(common_dates)} common return observations; {minimum_overlap} required"
        )

    portfolio_returns = [
        sum(float(weights[code]) * returns_by_code[code][item] for code in weights)
        for item in common_dates
    ]
    wealth = Decimal("1")
    wealth_points = [
        NavPoint(common_dates[0] - timedelta(days=1), wealth, "synthetic", "", "")
    ]
    for item, value in zip(common_dates, portfolio_returns):
        wealth *= Decimal(str(1 + value))
        wealth_points.append(NavPoint(item, wealth, "synthetic", "", ""))

    elapsed_days = (common_dates[-1] - common_dates[0]).days
    total_return = float(wealth - Decimal("1"))
    annualized_return = (
        (float(wealth) ** (365.25 / elapsed_days) - 1) if elapsed_days > 0 else None
    )
    volatility = stdev(portfolio_returns) * math.sqrt(252)
    largest_code, largest_weight = max(weights.items(), key=lambda item: item[1])
    hhi = cash_weight**2 + sum((weight**2 for weight in weights.values()), Decimal("0"))
    breaches = [
        {
            "rule": "max_single_fund_weight_pct",
            "fund_code": code,
            "actual_pct": float(weight * 100),
            "limit_pct": float(max_single_fund_weight_pct),
            "excess_pct_points": float(weight * 100 - max_single_fund_weight_pct),
        }
        for code, weight in sorted(weights.items())
        if weight * 100 > max_single_fund_weight_pct
    ]
    result: dict[str, object] = {
        "method": "hypothetical_constant_current_weights",
        "provider_id": provider_id,
        "nav_field": nav_field,
        "return_start_date": common_dates[0].isoformat(),
        "return_end_date": common_dates[-1].isoformat(),
        "common_return_observations": len(common_dates),
        "elapsed_days": elapsed_days,
        "weights": {code: float(weight) for code, weight in sorted(weights.items())},
        "cash_weight": float(cash_weight),
        "total_return_pct": total_return * 100,
        "annualized_return_pct": None if annualized_return is None else annualized_return * 100,
        "annualized_volatility_pct": volatility * 100,
        "worst_rolling_1y_return_pct": _worst_rolling_return(wealth_points, 365),
        "concentration": {
            "largest_fund_code": largest_code,
            "largest_fund_weight_pct": float(largest_weight * 100),
            "hhi_including_cash": float(hhi),
            "effective_positions_including_cash": float(Decimal("1") / hhi),
            "single_fund_limit_breaches": breaches,
        },
        "quality": {
            "hypothetical_not_actual_portfolio_history": True,
            "constant_weights_no_rebalancing": True,
            "cash_return_assumed_zero": True,
            "common_dates_only": True,
            "no_forward_fill": True,
            "short_history": elapsed_days < 365,
            "source_ranges": {
                code: {
                    "start_date": points[0].nav_date.isoformat(),
                    "end_date": points[-1].nav_date.isoformat(),
                    "observations": len(points),
                    "max_gap_days": max(
                        current.nav_date - previous.nav_date
                        for previous, current in zip(points, points[1:])
                    ).days,
                }
                for code, points in points_by_code.items()
            },
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed"
                for point in all_points
            ),
            "research_only": any(
                point.visibility_status != "strict_point_in_time" for point in all_points
            ),
            "batch_ids": sorted({point.batch_id for point in all_points}),
            "batch_content_sha256": sorted(
                {point.content_sha256 for point in all_points}
            ),
        },
    }
    result.update(_drawdown(wealth_points))
    return result
