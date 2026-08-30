"""Allowlisted Guchacha tools and arguments for deterministic collection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ToolPolicy:
    enabled_tools: frozenset[str]
    allowed_market_series_datasets: frozenset[str]
    allowed_market_series_keys: Mapping[str, frozenset[str]]
    allowed_macro_indicators: frozenset[str]

    def validate(self, tool: str, arguments: Mapping[str, object]) -> None:
        if tool not in self.enabled_tools:
            raise ValueError(f"Guchacha tool is not allowlisted: {tool}")
        if tool == "get_market_series":
            dataset = arguments.get("dataset")
            if dataset not in self.allowed_market_series_datasets:
                raise ValueError(f"Market-series dataset is not allowlisted: {dataset}")
            key = arguments.get("key")
            if key is not None and key not in self.allowed_market_series_keys.get(str(dataset), frozenset()):
                raise ValueError(f"Market-series key is not allowlisted: {dataset}/{key}")
        if tool == "get_macro":
            indicator = arguments.get("indicator")
            if indicator not in self.allowed_macro_indicators:
                raise ValueError(f"Macro indicator is not allowlisted: {indicator}")


def load_tool_policy(path: str | Path) -> ToolPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("market-data sync config must use schema_version 1")
    raw = payload.get("tool_policy")
    if not isinstance(raw, dict):
        raise ValueError("market-data sync config requires tool_policy")
    enabled = raw.get("enabled_tools")
    market = raw.get("allowed_market_series_datasets")
    market_keys = raw.get("allowed_market_series_keys")
    macro = raw.get("allowed_macro_indicators")
    if not all(isinstance(value, list) and value for value in (enabled, market, macro)):
        raise ValueError("market-data tool allowlists must be non-empty arrays")
    if not isinstance(market_keys, dict) or set(market_keys) != set(market):
        raise ValueError("market-data key allowlists must cover every enabled dataset")
    return ToolPolicy(
        enabled_tools=frozenset(str(value) for value in enabled),
        allowed_market_series_datasets=frozenset(str(value) for value in market),
        allowed_market_series_keys={
            str(dataset): frozenset(str(value) for value in values)
            for dataset, values in market_keys.items()
            if isinstance(values, list)
        },
        allowed_macro_indicators=frozenset(str(value) for value in macro),
    )
