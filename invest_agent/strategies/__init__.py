"""Deterministic, versioned strategy calculations with no execution access."""

from .dca import (
    InstrumentRoute,
    build_monthly_allocation,
    generate_simulated_subscriptions,
)
from .trend_rs import (
    build_new_money_trend_signal,
    calculate_new_money_trend_signal,
    evaluate_trend_candidate,
)
from .drawdown_add import (
    build_drawdown_budget_signal,
    build_drawdown_monthly_allocation,
    calculate_drawdown_budget_signal,
    summarize_drawdown_tier_coverage,
)

__all__ = [
    "InstrumentRoute",
    "build_monthly_allocation",
    "generate_simulated_subscriptions",
    "build_new_money_trend_signal",
    "calculate_new_money_trend_signal",
    "build_drawdown_budget_signal",
    "build_drawdown_monthly_allocation",
    "calculate_drawdown_budget_signal",
    "summarize_drawdown_tier_coverage",
    "evaluate_trend_candidate",
]
