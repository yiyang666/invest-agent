"""Command-line entry points for reusable Phase 8 attribution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from datetime import date
from decimal import Decimal

from invest_agent.metrics.fund import load_nav_series

from .engine import (
    attribute_sleeves_brinson,
    calculate_cashflow_attribution,
    compare_cashflow_matched_paths,
    sequential_ablation_waterfall,
)
from .lifecycle import evaluate_strategy_lifecycle, load_attribution_policy
from .research_audit import attribute_buy_only_sleeve_pnl
from .signal_events import analyze_traffic_light_forward_returns


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _emit(payload: object, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 8 deterministic attribution")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "cashflow",
        "matched",
        "sleeves",
        "waterfall",
        "lifecycle",
        "buy-only-sleeves",
        "traffic-light-events",
    ):
        command = sub.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = _load(args.input)
    if not isinstance(payload, dict):
        raise ValueError("attribution input must be one JSON object")
    if args.command == "cashflow":
        result = calculate_cashflow_attribution(payload["periods"])
    elif args.command == "matched":
        result = compare_cashflow_matched_paths(
            payload["candidate_periods"], payload["benchmark_periods"]
        )
    elif args.command == "sleeves":
        result = attribute_sleeves_brinson(payload["portfolio"], payload["benchmark"])
    elif args.command == "waterfall":
        result = sequential_ablation_waterfall(
            payload["stages"],
            metric_name=str(payload.get("metric_name", "final_value_cny")),
            unit=str(payload.get("unit", "CNY")),
        )
    elif args.command == "lifecycle":
        policy = load_attribution_policy(payload["policy_path"])
        result = evaluate_strategy_lifecycle(payload["evidence"], policy)
    elif args.command == "buy-only-sleeves":
        backtest_result = payload.get("backtest_result")
        if backtest_result is None:
            backtest_result = _load(Path(str(payload["backtest_result_path"])))
        routes = payload.get("routes")
        if routes is None:
            route_document = _load(Path(str(payload["routes_path"])))
            if not isinstance(route_document, dict):
                raise ValueError("routes document must be one JSON object")
            routes = route_document["routes"]
        result = attribute_buy_only_sleeve_pnl(
            backtest_result=backtest_result,
            routes=routes,
            sleeve_groups=payload.get("sleeve_groups"),
        )
    else:
        signal_result = _load(Path(str(payload["signal_result_path"])))
        if not isinstance(signal_result, dict):
            raise ValueError("signal result must be one JSON object")
        profiles = signal_result.get("profile_results")
        if not isinstance(profiles, list) or len(profiles) != 1:
            raise ValueError("traffic-light event study requires one profile")
        profile = profiles[0]
        if not isinstance(profile, dict):
            raise ValueError("signal profile must be one object")
        provider = str(payload.get("provider_id", "akshare_eastmoney"))
        database = Path(str(payload["database_path"]))
        end_date = date.fromisoformat(str(payload["evaluation_end_date"]))
        theme_funds = payload["theme_funds"]
        defensive_funds = payload["defensive_funds"]
        if not isinstance(theme_funds, dict) or not isinstance(defensive_funds, dict):
            raise ValueError("theme and defensive fund maps are required")
        result = analyze_traffic_light_forward_returns(
            signal_ledger=profile["monthly_signal_ledger"],
            theme_series={
                str(sleeve): (
                    str(fund),
                    load_nav_series(
                        database,
                        fund_code=str(fund),
                        provider_id=provider,
                        as_of=end_date,
                        nav_field="accumulated_nav",
                    ),
                )
                for sleeve, fund in theme_funds.items()
            },
            defensive_series={
                str(sleeve): load_nav_series(
                    database,
                    fund_code=str(settings["fund_code"]),
                    provider_id=provider,
                    as_of=end_date,
                    nav_field="accumulated_nav",
                )
                for sleeve, settings in defensive_funds.items()
            },
            defensive_weights={
                str(sleeve): Decimal(str(settings["weight"]))
                for sleeve, settings in defensive_funds.items()
            },
            evaluation_end_date=end_date,
            horizons_months=[int(value) for value in payload["horizons_months"]],
            maximum_staleness_days=int(payload["maximum_staleness_days"]),
        )
    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
