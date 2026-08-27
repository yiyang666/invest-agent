"""Fund return, volatility, drawdown, and correlation calculations."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import math
from pathlib import Path
import sqlite3
from statistics import StatisticsError, correlation, mean, stdev
from typing import Sequence

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


@dataclass(frozen=True)
class NavPoint:
    nav_date: date
    nav: Decimal
    visibility_status: str
    batch_id: str
    content_sha256: str


def _connect(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_nav_series(
    database: str | Path,
    *,
    fund_code: str,
    provider_id: str = "akshare_eastmoney",
    as_of: date | None = None,
    nav_field: str = "accumulated_nav",
) -> tuple[NavPoint, ...]:
    if FUND_CODE_PATTERN.fullmatch(fund_code) is None:
        raise ValueError("fund_code must contain six digits")
    if nav_field not in {"unit_nav", "accumulated_nav"}:
        raise ValueError("nav_field must be unit_nav or accumulated_nav")
    clauses = [
        "observation.provider_id = ?",
        "batch.quality_status IN ('pass', 'partial')",
        f"observation.{nav_field} IS NOT NULL",
    ]
    parameters: list[object] = [provider_id]
    if as_of is not None:
        clauses.append("observation.nav_date <= ?")
        parameters.append(as_of.isoformat())
    connection = _connect(database)
    try:
        boundary_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'fund_history_boundaries'
            """
        ).fetchone()
        if boundary_table is not None:
            boundary = connection.execute(
                """
                SELECT current_contract_start_date
                FROM fund_history_boundaries
                WHERE fund_code = ?
                """,
                (fund_code,),
            ).fetchone()
            if boundary is not None:
                clauses.append("observation.nav_date >= ?")
                parameters.append(boundary["current_contract_start_date"])
        where_sql = " AND ".join(clauses)
        rows = connection.execute(
            f"""
            WITH ranked AS (
                SELECT observation.fund_code, observation.nav_date,
                       observation.{nav_field} AS nav,
                       observation.visibility_status, observation.batch_id,
                       batch.content_sha256, batch.published_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY observation.fund_code, observation.nav_date
                           ORDER BY batch.published_at DESC, observation.batch_id DESC
                       ) AS rank_number
                FROM fund_nav_observations AS observation
                JOIN data_batches AS batch ON batch.batch_id = observation.batch_id
                WHERE {where_sql}
            )
            SELECT nav_date, nav, visibility_status, batch_id, content_sha256
            FROM ranked
            WHERE fund_code = ? AND rank_number = 1
            ORDER BY nav_date
            """,
            (*parameters, fund_code),
        ).fetchall()
    finally:
        connection.close()
    points = tuple(
        NavPoint(
            nav_date=date.fromisoformat(row["nav_date"]),
            nav=Decimal(row["nav"]),
            visibility_status=row["visibility_status"],
            batch_id=row["batch_id"],
            content_sha256=row["content_sha256"],
        )
        for row in rows
    )
    if len(points) < 2:
        raise ValueError(f"At least two validated NAV observations are required for {fund_code}")
    return points


def _returns(points: Sequence[NavPoint]) -> list[float]:
    return [float(current.nav / previous.nav - Decimal("1")) for previous, current in zip(points, points[1:])]


def _trailing_return(points: Sequence[NavPoint], days: int) -> float | None:
    target = points[-1].nav_date - timedelta(days=days)
    dates = [point.nav_date for point in points]
    index = bisect_right(dates, target) - 1
    if index < 0:
        return None
    return float(points[-1].nav / points[index].nav - Decimal("1"))


def _drawdown(points: Sequence[NavPoint]) -> dict[str, object]:
    peak_index = 0
    worst_peak = 0
    worst_trough = 0
    worst = Decimal("0")
    for index, point in enumerate(points):
        if point.nav > points[peak_index].nav:
            peak_index = index
        drawdown = point.nav / points[peak_index].nav - Decimal("1")
        if drawdown < worst:
            worst = drawdown
            worst_peak = peak_index
            worst_trough = index
    recovery_date = None
    if worst < 0:
        peak_nav = points[worst_peak].nav
        for point in points[worst_trough + 1 :]:
            if point.nav >= peak_nav:
                recovery_date = point.nav_date.isoformat()
                break
    running_peak = max(point.nav for point in points)
    return {
        "max_drawdown_pct": float(worst * Decimal("100")),
        "max_drawdown_peak_date": points[worst_peak].nav_date.isoformat(),
        "max_drawdown_trough_date": points[worst_trough].nav_date.isoformat(),
        "max_drawdown_recovery_date": recovery_date,
        "max_drawdown_duration_days": 0
        if worst == 0
        else (
            (date.fromisoformat(recovery_date) if recovery_date else points[-1].nav_date)
            - points[worst_peak].nav_date
        ).days,
        "current_drawdown_pct": float(
            (points[-1].nav / running_peak - Decimal("1")) * Decimal("100")
        ),
    }


def _worst_rolling_return(points: Sequence[NavPoint], days: int) -> float | None:
    dates = [point.nav_date for point in points]
    worst: Decimal | None = None
    for end_index, end_point in enumerate(points):
        target = end_point.nav_date - timedelta(days=days)
        start_index = bisect_right(dates, target, hi=end_index) - 1
        if start_index < 0:
            continue
        value = end_point.nav / points[start_index].nav - Decimal("1")
        worst = value if worst is None or value < worst else worst
    return None if worst is None else float(worst * Decimal("100"))


def calculate_fund_metrics(
    database: str | Path,
    *,
    fund_code: str,
    provider_id: str = "akshare_eastmoney",
    as_of: date | None = None,
    nav_field: str = "accumulated_nav",
) -> dict[str, object]:
    points = load_nav_series(
        database,
        fund_code=fund_code,
        provider_id=provider_id,
        as_of=as_of,
        nav_field=nav_field,
    )
    returns = _returns(points)
    elapsed_days = (points[-1].nav_date - points[0].nav_date).days
    total_return = float(points[-1].nav / points[0].nav - Decimal("1"))
    annualized_return = (
        (float(points[-1].nav / points[0].nav) ** (365.25 / elapsed_days) - 1)
        if elapsed_days > 0
        else None
    )
    annualized_volatility = stdev(returns) * math.sqrt(252) if len(returns) >= 2 else None
    gaps = [
        (current.nav_date - previous.nav_date).days
        for previous, current in zip(points, points[1:])
    ]
    result: dict[str, object] = {
        "fund_code": fund_code,
        "provider_id": provider_id,
        "nav_field": nav_field,
        "start_date": points[0].nav_date.isoformat(),
        "end_date": points[-1].nav_date.isoformat(),
        "observations": len(points),
        "elapsed_days": elapsed_days,
        "total_return_pct": total_return * 100,
        "annualized_return_pct": None if annualized_return is None else annualized_return * 100,
        "annualized_volatility_pct": (
            None if annualized_volatility is None else annualized_volatility * 100
        ),
        "worst_rolling_1y_return_pct": _worst_rolling_return(points, 365),
        "trailing_return_pct": {
            "1m": None if (value := _trailing_return(points, 30)) is None else value * 100,
            "3m": None if (value := _trailing_return(points, 91)) is None else value * 100,
            "6m": None if (value := _trailing_return(points, 182)) is None else value * 100,
            "1y": None if (value := _trailing_return(points, 365)) is None else value * 100,
            "3y": None if (value := _trailing_return(points, 1096)) is None else value * 100,
        },
        "max_gap_days": max(gaps),
        "quality": {
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed" for point in points
            ),
            "batch_ids": sorted({point.batch_id for point in points}),
            "batch_content_sha256": sorted({point.content_sha256 for point in points}),
            "research_only": any(
                point.visibility_status != "strict_point_in_time" for point in points
            ),
            "short_history": elapsed_days < 365,
            "annualization_reliable": elapsed_days >= 365,
        },
    }
    result.update(_drawdown(points))
    return result


def _dated_returns(points: Sequence[NavPoint]) -> dict[date, float]:
    return {
        current.nav_date: float(current.nav / previous.nav - Decimal("1"))
        for previous, current in zip(points, points[1:])
    }


def calculate_return_correlation(
    database: str | Path,
    *,
    fund_codes: Sequence[str],
    provider_id: str = "akshare_eastmoney",
    as_of: date | None = None,
    nav_field: str = "accumulated_nav",
    minimum_overlap: int = 20,
) -> dict[str, object]:
    codes = tuple(dict.fromkeys(fund_codes))
    if len(codes) < 2:
        raise ValueError("At least two distinct fund codes are required")
    series = {
        code: _dated_returns(
            load_nav_series(
                database,
                fund_code=code,
                provider_id=provider_id,
                as_of=as_of,
                nav_field=nav_field,
            )
        )
        for code in codes
    }
    matrix: dict[str, dict[str, float | None]] = {code: {} for code in codes}
    overlaps: dict[str, int] = {}
    for left_index, left in enumerate(codes):
        for right_index, right in enumerate(codes):
            if left_index == right_index:
                matrix[left][right] = 1.0
                continue
            if right_index < left_index:
                matrix[left][right] = matrix[right][left]
                continue
            dates = sorted(set(series[left]) & set(series[right]))
            overlaps[f"{left}:{right}"] = len(dates)
            if len(dates) < minimum_overlap:
                value = None
            else:
                left_values = [series[left][item] for item in dates]
                right_values = [series[right][item] for item in dates]
                try:
                    value = correlation(left_values, right_values)
                except StatisticsError:
                    value = None
            matrix[left][right] = value
    return {
        "provider_id": provider_id,
        "nav_field": nav_field,
        "minimum_overlap": minimum_overlap,
        "matrix": matrix,
        "overlap_observations": overlaps,
        "research_only": True,
    }


def calculate_rolling_correlation(
    database: str | Path,
    *,
    left_fund_code: str,
    right_fund_code: str,
    window: int = 60,
    provider_id: str = "akshare_eastmoney",
    as_of: date | None = None,
    nav_field: str = "accumulated_nav",
    minimum_overlap: int | None = None,
) -> dict[str, object]:
    """Calculate a trailing observation-window correlation without filling gaps."""
    if left_fund_code == right_fund_code:
        raise ValueError("Two distinct fund codes are required")
    if window < 2:
        raise ValueError("window must be at least 2")
    minimum = window if minimum_overlap is None else minimum_overlap
    if minimum < 2 or minimum > window:
        raise ValueError("minimum_overlap must be between 2 and window")
    series = {}
    points_by_code: dict[str, tuple[NavPoint, ...]] = {}
    quality_points: list[NavPoint] = []
    for code in (left_fund_code, right_fund_code):
        points = load_nav_series(
            database,
            fund_code=code,
            provider_id=provider_id,
            as_of=as_of,
            nav_field=nav_field,
        )
        quality_points.extend(points)
        points_by_code[code] = points
        series[code] = _dated_returns(points)
    common_dates = sorted(set(series[left_fund_code]) & set(series[right_fund_code]))
    values: list[dict[str, object]] = []
    for end_index in range(minimum - 1, len(common_dates)):
        start_index = max(0, end_index - window + 1)
        dates = common_dates[start_index : end_index + 1]
        if len(dates) < minimum:
            continue
        left_values = [series[left_fund_code][item] for item in dates]
        right_values = [series[right_fund_code][item] for item in dates]
        try:
            value = correlation(left_values, right_values)
        except StatisticsError:
            value = None
        values.append(
            {
                "end_date": dates[-1].isoformat(),
                "start_date": dates[0].isoformat(),
                "observations": len(dates),
                "correlation": value,
            }
        )
    numeric = [float(item["correlation"]) for item in values if item["correlation"] is not None]
    return {
        "left_fund_code": left_fund_code,
        "right_fund_code": right_fund_code,
        "provider_id": provider_id,
        "nav_field": nav_field,
        "window_observations": window,
        "minimum_overlap": minimum,
        "common_return_observations": len(common_dates),
        "rolling_points": values,
        "summary": {
            "latest": numeric[-1] if numeric else None,
            "minimum": min(numeric) if numeric else None,
            "maximum": max(numeric) if numeric else None,
            "mean": mean(numeric) if numeric else None,
        },
        "quality": {
            "no_forward_fill": True,
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
                for point in quality_points
            ),
            "batch_ids": sorted({point.batch_id for point in quality_points}),
            "batch_content_sha256": sorted(
                {point.content_sha256 for point in quality_points}
            ),
            "research_only": any(
                point.visibility_status != "strict_point_in_time"
                for point in quality_points
            ),
        },
    }
