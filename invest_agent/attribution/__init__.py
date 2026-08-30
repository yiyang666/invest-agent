"""Deterministic portfolio attribution and strategy lifecycle primitives."""

from .engine import (
    attribute_sleeves_brinson,
    calculate_cashflow_attribution,
    compare_cashflow_matched_paths,
    sequential_ablation_waterfall,
)
from .lifecycle import evaluate_strategy_lifecycle, load_attribution_policy
from .research_audit import attribute_buy_only_sleeve_pnl
from .signal_events import analyze_traffic_light_forward_returns

__all__ = [
    "attribute_sleeves_brinson",
    "calculate_cashflow_attribution",
    "compare_cashflow_matched_paths",
    "sequential_ablation_waterfall",
    "evaluate_strategy_lifecycle",
    "load_attribution_policy",
    "attribute_buy_only_sleeve_pnl",
    "analyze_traffic_light_forward_returns",
]
