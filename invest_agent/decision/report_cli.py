"""CLI for deterministic Phase 5 research reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .reporting import build_research_report, render_research_report_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-bound research report")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = build_research_report(config, workspace_root=args.workspace_root)
        markdown = render_research_report_markdown(report)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(markdown, encoding="utf-8")
    args.json_output.chmod(0o600)
    args.markdown_output.chmod(0o600)
    print(
        json.dumps(
            {
                "status": "pass",
                "report_id": report["report_id"],
                "evidence_claims": len(report["evidence_ledger"]),
                "external_market_news_used": False,
                "orders": 0,
                "real_trading_enabled": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
