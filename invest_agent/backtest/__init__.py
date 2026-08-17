"""Deterministic, offline event simulation for off-exchange funds."""

from .subscription_engine import (
    CashContribution,
    SubscriptionExecution,
    run_subscription_events,
)
from .valuation_engine import (
    CashDistribution,
    DailyUnitNav,
    ReinvestedDistribution,
    run_portfolio_valuation_events,
)
from .local_research import calculate_path_metrics, load_research_scenario, run_local_dca_research
from .trend_robustness import load_trend_robustness_spec, run_trend_robustness
from .drawdown_compare import run_drawdown_dual_benchmark
from .sensitivity import (
    build_sensitivity_scenarios,
    load_sensitivity_spec,
    run_sensitivity_matrix,
)
from .rolling import build_rolling_scenarios, load_rolling_spec, run_rolling_windows
from .candidate_compare import load_comparison_spec, run_candidate_comparison

__all__ = [
    "CashContribution",
    "CashDistribution",
    "DailyUnitNav",
    "ReinvestedDistribution",
    "SubscriptionExecution",
    "run_portfolio_valuation_events",
    "run_subscription_events",
    "calculate_path_metrics",
    "load_research_scenario",
    "run_local_dca_research",
    "load_trend_robustness_spec",
    "run_trend_robustness",
    "run_drawdown_dual_benchmark",
    "build_sensitivity_scenarios",
    "load_sensitivity_spec",
    "run_sensitivity_matrix",
    "build_rolling_scenarios",
    "load_rolling_spec",
    "run_rolling_windows",
    "load_comparison_spec",
    "run_candidate_comparison",
]
