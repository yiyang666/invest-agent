"""CLI for deterministic strategy simulations; contains no execution adapter."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .dca import InstrumentRoute, build_monthly_allocation, generate_simulated_subscriptions
from .trend_rs import calculate_new_money_trend_signal
from .drawdown_add import calculate_drawdown_budget_signal


DEFAULT_SPEC = Path("strategies/specs/dca_baseline_v1.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run simulation-only strategy calculations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    dca = subparsers.add_parser("dca-plan")
    dca.add_argument("--input", type=Path, required=True)
    dca.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    dca.add_argument("--output", type=Path)
    trend = subparsers.add_parser("trend-rs-signal")
    trend.add_argument("--review-date", type=date.fromisoformat, required=True)
    trend.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    trend.add_argument("--output", type=Path)
    drawdown = subparsers.add_parser("drawdown-add-signal")
    drawdown.add_argument("--review-date", type=date.fromisoformat, required=True)
    drawdown.add_argument(
        "--db", type=Path, default=Path("data/private/invest_agent.sqlite3")
    )
    drawdown.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "drawdown-add-signal":
            payload = calculate_drawdown_budget_signal(
                args.db,
                review_date=args.review_date,
                funds={
                    "domestic_broad_core": "110020",
                    "us_broad_core": "050025",
                    "us_growth": "539001",
                    "domestic_growth": "019861",
                    "defensive": "006932",
                    "sh_hk_sz_passive_technology_satellite": "160646",
                },
                weights={
                    "domestic_broad_core": Decimal("0.20"),
                    "us_broad_core": Decimal("0.20"),
                    "us_growth": Decimal("0.10"),
                    "domestic_growth": Decimal("0.10"),
                    "defensive": Decimal("0.30"),
                    "sh_hk_sz_passive_technology_satellite": Decimal("0.10"),
                },
            )
        elif args.command == "trend-rs-signal":
            payload = calculate_new_money_trend_signal(
                args.db,
                review_date=args.review_date,
                candidates={
                    "us_growth": "539001",
                    "domestic_growth": "019861",
                    "sh_hk_sz_passive_technology_satellite": "160646",
                },
            )
        else:
            request = json.loads(args.input.read_text(encoding="utf-8"))
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            if spec.get("strategy_id") != "dca_baseline" or spec.get("strategy_version") != "1.0.0":
                raise ValueError("dca-plan only accepts dca_baseline@1.0.0")
            plan = build_monthly_allocation(
                planned_date=date.fromisoformat(request["planned_date"]),
                pre_contribution_portfolio_value_cny=Decimal(
                    str(request["pre_contribution_portfolio_value_cny"])
                ),
                monthly_contribution_cny=Decimal(str(request["monthly_contribution_cny"])),
                current_sleeve_values_cny={
                    key: Decimal(str(value))
                    for key, value in request["current_sleeve_values_cny"].items()
                },
                target_weights={
                    key: Decimal(str(value))
                    for key, value in request["target_weights"].items()
                },
                strategy_spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
            )
            routes = tuple(
                InstrumentRoute(
                    sleeve=item["sleeve"],
                    fund_code=item["fund_code"],
                    priority=int(item["priority"]),
                    minimum_order_cny=Decimal(str(item["minimum_order_cny"])),
                    daily_cap_cny=(
                        None
                        if item.get("daily_cap_cny") is None
                        else Decimal(str(item["daily_cap_cny"]))
                    ),
                    eligible_dates=tuple(
                        date.fromisoformat(value) for value in item["eligible_dates"]
                    ),
                    rule_version=item["rule_version"],
                )
                for item in request["routes"]
            )
            payload = generate_simulated_subscriptions(plan, routes=routes).to_dict()
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
