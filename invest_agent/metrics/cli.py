"""CLI for local-only fund metrics."""

from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from invest_agent.metrics.fund import (
    calculate_fund_metrics,
    calculate_return_correlation,
    calculate_rolling_correlation,
)
from invest_agent.metrics.portfolio import calculate_portfolio_risk
from invest_agent.metrics.market_state import calculate_market_state_snapshot
from invest_agent.metrics.drawdown_events import (
    calculate_drawdown_event_panel,
    load_drawdown_event_panel_spec,
)


DEFAULT_DATABASE = Path("data/private/invest_agent.sqlite3")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate fund metrics from the local store")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fund = subparsers.add_parser("fund")
    fund.add_argument("--fund-code", action="append", required=True)
    fund.add_argument("--provider-id", default="akshare_eastmoney")
    fund.add_argument("--as-of", type=date.fromisoformat)
    fund.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    fund.add_argument("--output", type=Path)
    matrix = subparsers.add_parser("correlation")
    matrix.add_argument("--fund-code", action="append", required=True)
    matrix.add_argument("--provider-id", default="akshare_eastmoney")
    matrix.add_argument("--as-of", type=date.fromisoformat)
    matrix.add_argument("--minimum-overlap", type=int, default=20)
    matrix.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    matrix.add_argument("--output", type=Path)
    rolling = subparsers.add_parser("rolling-correlation")
    rolling.add_argument("--fund-code", action="append", required=True)
    rolling.add_argument("--window", type=int, default=60)
    rolling.add_argument("--minimum-overlap", type=int)
    rolling.add_argument("--provider-id", default="akshare_eastmoney")
    rolling.add_argument("--as-of", type=date.fromisoformat)
    rolling.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    rolling.add_argument("--output", type=Path)
    portfolio = subparsers.add_parser("portfolio")
    portfolio.add_argument("--snapshot", type=Path, required=True)
    portfolio.add_argument("--provider-id", default="akshare_eastmoney")
    portfolio.add_argument("--as-of", type=date.fromisoformat)
    portfolio.add_argument("--minimum-overlap", type=int, default=20)
    portfolio.add_argument("--max-single-fund-weight-pct", type=Decimal, default=Decimal("25"))
    portfolio.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    portfolio.add_argument("--output", type=Path)
    market_state = subparsers.add_parser("market-state")
    source = market_state.add_mutually_exclusive_group(required=True)
    source.add_argument("--fund-code", action="append")
    source.add_argument("--snapshot", type=Path)
    market_state.add_argument("--provider-id", default="akshare_eastmoney")
    market_state.add_argument("--as-of", type=date.fromisoformat, required=True)
    market_state.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    market_state.add_argument("--output", type=Path)
    drawdown_panel = subparsers.add_parser("drawdown-event-panel")
    drawdown_panel.add_argument("--spec", type=Path, required=True)
    drawdown_panel.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    drawdown_panel.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "drawdown-event-panel":
            payload = calculate_drawdown_event_panel(
                args.db,
                spec=load_drawdown_event_panel_spec(args.spec),
            )
        elif args.command == "fund":
            payload: dict[str, object] = {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "metrics": [
                    calculate_fund_metrics(
                        args.db,
                        fund_code=code,
                        provider_id=args.provider_id,
                        as_of=args.as_of,
                    )
                    for code in args.fund_code
                ],
            }
        elif args.command == "correlation":
            payload = {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "correlation": calculate_return_correlation(
                    args.db,
                    fund_codes=args.fund_code,
                    provider_id=args.provider_id,
                    as_of=args.as_of,
                    minimum_overlap=args.minimum_overlap,
                ),
            }
        elif args.command == "rolling-correlation":
            if len(args.fund_code) != 2:
                raise ValueError("rolling-correlation requires exactly two --fund-code values")
            payload = {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "rolling_correlation": calculate_rolling_correlation(
                    args.db,
                    left_fund_code=args.fund_code[0],
                    right_fund_code=args.fund_code[1],
                    window=args.window,
                    minimum_overlap=args.minimum_overlap,
                    provider_id=args.provider_id,
                    as_of=args.as_of,
                ),
            }
        elif args.command == "portfolio":
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            positions = snapshot.get("positions")
            if not isinstance(positions, list):
                raise ValueError("snapshot positions must be a list")
            weights = {
                str(item["fund_code"]): Decimal(str(item["weight"])) for item in positions
            }
            payload = {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "snapshot_as_of": snapshot.get("as_of"),
                "portfolio_risk": calculate_portfolio_risk(
                    args.db,
                    weights=weights,
                    cash_weight=Decimal(str(snapshot.get("cash_weight", 0))),
                    provider_id=args.provider_id,
                    as_of=args.as_of,
                    minimum_overlap=args.minimum_overlap,
                    max_single_fund_weight_pct=args.max_single_fund_weight_pct,
                ),
            }
        else:
            weights = None
            cash_weight = Decimal("0")
            codes = args.fund_code
            snapshot_as_of = None
            if args.snapshot is not None:
                snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
                positions = snapshot.get("positions")
                if not isinstance(positions, list):
                    raise ValueError("snapshot positions must be a list")
                weights = {
                    str(item["fund_code"]): Decimal(str(item["weight"]))
                    for item in positions
                }
                codes = list(weights)
                cash_weight = Decimal(str(snapshot.get("cash_weight", 0)))
                snapshot_as_of = snapshot.get("as_of")
            payload = {
                "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "snapshot_as_of": snapshot_as_of,
                "market_state": calculate_market_state_snapshot(
                    args.db,
                    fund_codes=codes,
                    weights=weights,
                    cash_weight=cash_weight,
                    provider_id=args.provider_id,
                    as_of=args.as_of,
                ),
            }
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
