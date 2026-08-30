"""Deterministic, local-only global market-state assessment."""

from .engine import build_global_market_state_snapshot, load_regime_config

__all__ = ["build_global_market_state_snapshot", "load_regime_config"]
