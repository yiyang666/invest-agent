"""Command-line entry points for reusable Phase 8 attribution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .engine import (
    attribute_sleeves_brinson,
    calculate_cashflow_attribution,
    compare_cashflow_matched_paths,
    sequential_ablation_waterfall,
)
from .lifecycle import evaluate_strategy_lifecycle, load_attribution_policy


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
    for name in ("cashflow", "matched", "sleeves", "waterfall", "lifecycle"):
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
    else:
        policy = load_attribution_policy(payload["policy_path"])
        result = evaluate_strategy_lifecycle(payload["evidence"], policy)
    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
