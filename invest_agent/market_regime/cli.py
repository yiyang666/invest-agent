"""CLI for deterministic local global market-state snapshots."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from .engine import build_global_market_state_snapshot, load_regime_config


DEFAULT_DATABASE = Path("data/private/invest_agent.sqlite3")
DEFAULT_CONFIG = Path("config/global_market_regime_v1.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _as_of(value: str) -> datetime:
    if len(value) == 10:
        parsed_date = date.fromisoformat(value)
        return datetime.combine(parsed_date, time.max, tzinfo=SHANGHAI)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed_date = date.fromisoformat(value)
        return datetime.combine(parsed_date, time.max, tzinfo=SHANGHAI)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of datetime must include a timezone offset")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assess global market state from validated local evidence"
    )
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_global_market_state_snapshot(
            args.db,
            config=load_regime_config(args.config),
            as_of=_as_of(args.as_of),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
