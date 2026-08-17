"""CLI for grounded explanation generation and acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .explanation import (
    build_grounded_explanation,
    render_grounded_explanation_markdown,
    run_explanation_acceptance,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and validate a grounded explanation")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--acceptance-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        explanation = build_grounded_explanation(config, workspace_root=args.workspace_root)
        markdown = render_grounded_explanation_markdown(explanation)
        acceptance = run_explanation_acceptance(config, workspace_root=args.workspace_root)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1

    for path in (args.json_output, args.markdown_output, args.acceptance_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(explanation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown, encoding="utf-8")
    args.acceptance_output.write_text(
        json.dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for path in (args.json_output, args.markdown_output, args.acceptance_output):
        path.chmod(0o600)
    print(
        json.dumps(
            {
                "status": acceptance["summary"]["acceptance_gate"],
                "explanation_id": explanation["explanation_id"],
                "scenarios_passed": acceptance["summary"]["passed"],
                "scenarios_total": acceptance["summary"]["total"],
                "external_market_news_used": False,
                "orders": 0,
                "real_trading_enabled": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if acceptance["summary"]["acceptance_gate"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
