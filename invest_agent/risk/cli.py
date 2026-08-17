"""CLI for deterministic, research-only risk calculations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from invest_agent.backtest.local_research import load_research_scenario

from .stress import load_stress_spec, run_portfolio_stress


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run independent portfolio risk checks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    stress = subparsers.add_parser("stress")
    stress.add_argument("--scenario", type=Path, required=True)
    stress.add_argument("--stress-spec", type=Path, required=True)
    stress.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_portfolio_stress(
            base_scenario=load_research_scenario(args.scenario),
            stress_spec=load_stress_spec(args.stress_spec),
        )
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
