"""Explainable fund market-state labels based only on trailing local NAV data."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
import math
from pathlib import Path
from statistics import stdev
from typing import Mapping, Sequence

from .fund import NavPoint, _returns, load_nav_series


DEFAULT_THRESHOLDS = {
    "stress_drawdown_pct": -15.0,
    "stress_volatility_pct": 40.0,
    "constructive_max_drawdown_pct": -10.0,
}
MARKET_STATE_MODEL_VERSION = "fund_regime_v1"


def _window_return(points: Sequence[NavPoint], observations: int) -> float:
    return float(points[-1].nav / points[-1 - observations].nav - Decimal("1")) * 100


def classify_market_state(
    *,
    short_return_pct: float,
    medium_return_pct: float,
    slow_average_gap_pct: float,
    annualized_volatility_pct: float,
    current_drawdown_pct: float,
) -> tuple[str, list[str]]:
    """Apply fixed, ordered rules; this label is evidence, not a trade signal."""
    if current_drawdown_pct <= DEFAULT_THRESHOLDS["stress_drawdown_pct"]:
        return "stressed", [
            "current_drawdown_at_or_below_stress_threshold",
        ]
    if (
        annualized_volatility_pct >= DEFAULT_THRESHOLDS["stress_volatility_pct"]
        and short_return_pct < 0
    ):
        return "stressed", [
            "high_recent_volatility",
            "negative_short_return",
        ]
    if (
        slow_average_gap_pct > 0
        and medium_return_pct > 0
        and current_drawdown_pct > DEFAULT_THRESHOLDS["constructive_max_drawdown_pct"]
    ):
        evidence = [
            "nav_above_slow_average",
            "positive_medium_return",
            "drawdown_better_than_constructive_threshold",
        ]
        if short_return_pct < 0:
            evidence.append("short_pullback_inside_constructive_state")
        return "constructive", evidence
    if slow_average_gap_pct < 0 and medium_return_pct < 0:
        return "weak", [
            "nav_below_slow_average",
            "negative_medium_return",
        ]
    return "mixed", ["trend_and_risk_evidence_disagree"]


def calculate_fund_market_state(
    database: str | Path,
    *,
    fund_code: str,
    as_of: date,
    provider_id: str = "akshare_eastmoney",
    nav_field: str = "accumulated_nav",
    short_window: int = 20,
    medium_window: int = 60,
    slow_window: int = 120,
    volatility_window: int = 20,
) -> dict[str, object]:
    windows = (short_window, medium_window, slow_window, volatility_window)
    if any(window < 2 for window in windows):
        raise ValueError("all market-state windows must be at least 2")
    points = load_nav_series(
        database,
        fund_code=fund_code,
        provider_id=provider_id,
        as_of=as_of,
        nav_field=nav_field,
    )
    required = max(short_window, medium_window, slow_window, volatility_window) + 1
    if len(points) < required:
        raise ValueError(
            f"At least {required} observations are required for market state of {fund_code}"
        )

    recent_returns = _returns(points)[-volatility_window:]
    slow_average = sum(
        (point.nav for point in points[-slow_window:]), Decimal("0")
    ) / Decimal(slow_window)
    running_peak = max(point.nav for point in points)
    features = {
        "short_return_pct": _window_return(points, short_window),
        "medium_return_pct": _window_return(points, medium_window),
        "slow_average_gap_pct": float(
            (points[-1].nav / slow_average - Decimal("1")) * Decimal("100")
        ),
        "annualized_volatility_pct": stdev(recent_returns) * math.sqrt(252) * 100,
        "current_drawdown_pct": float(
            (points[-1].nav / running_peak - Decimal("1")) * Decimal("100")
        ),
    }
    state, evidence = classify_market_state(**features)
    gaps = [
        (current.nav_date - previous.nav_date).days
        for previous, current in zip(points, points[1:])
    ]
    return {
        "fund_code": fund_code,
        "model_version": MARKET_STATE_MODEL_VERSION,
        "state": state,
        "evidence": evidence,
        "features": features,
        "thresholds": DEFAULT_THRESHOLDS,
        "windows_observations": {
            "short_return": short_window,
            "medium_return": medium_window,
            "slow_average": slow_window,
            "recent_volatility": volatility_window,
        },
        "provider_id": provider_id,
        "nav_field": nav_field,
        "requested_as_of": as_of.isoformat(),
        "effective_nav_date": points[-1].nav_date.isoformat(),
        "source_range": {
            "start_date": points[0].nav_date.isoformat(),
            "end_date": points[-1].nav_date.isoformat(),
            "observations": len(points),
            "max_gap_days": max(gaps),
        },
        "quality": {
            "nav_date_cutoff_applied": True,
            "trailing_windows_only": True,
            "no_forward_fill": True,
            "historical_visibility_assumed": sum(
                point.visibility_status == "historical_visibility_assumed" for point in points
            ),
            "batch_ids": sorted({point.batch_id for point in points}),
            "batch_content_sha256": sorted({point.content_sha256 for point in points}),
            "research_only": any(
                point.visibility_status != "strict_point_in_time" for point in points
            ),
        },
    }


def calculate_market_state_snapshot(
    database: str | Path,
    *,
    fund_codes: Sequence[str],
    as_of: date,
    weights: Mapping[str, Decimal] | None = None,
    cash_weight: Decimal = Decimal("0"),
    provider_id: str = "akshare_eastmoney",
) -> dict[str, object]:
    codes = tuple(dict.fromkeys(fund_codes))
    if not codes:
        raise ValueError("At least one fund code is required")
    if weights is not None and set(weights) != set(codes):
        raise ValueError("weights must match fund_codes exactly")
    states = [
        calculate_fund_market_state(
            database,
            fund_code=code,
            as_of=as_of,
            provider_id=provider_id,
        )
        for code in codes
    ]
    counts = Counter(str(item["state"]) for item in states)
    weighted_exposure = None
    if weights is not None:
        if cash_weight < 0 or any(weight < 0 for weight in weights.values()):
            raise ValueError("weights cannot be negative")
        total = cash_weight + sum(weights.values(), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.000001"):
            raise ValueError("fund and cash weights must sum to 1")
        weighted_exposure = {
            state: float(
                sum(
                    (weights[str(item["fund_code"])] for item in states if item["state"] == state),
                    Decimal("0"),
                )
                * Decimal("100")
            )
            for state in ("constructive", "mixed", "weak", "stressed")
        }
    return {
        "as_of": as_of.isoformat(),
        "model_version": MARKET_STATE_MODEL_VERSION,
        "provider_id": provider_id,
        "states": states,
        "state_counts": dict(sorted(counts.items())),
        "weighted_fund_exposure_pct": weighted_exposure,
        "cash_weight_pct": float(cash_weight * Decimal("100")),
        "classification_is_not_strategy_signal": True,
        "research_only": any(bool(item["quality"]["research_only"]) for item in states),
    }
