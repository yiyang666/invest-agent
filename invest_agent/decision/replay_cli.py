"""CLI for deterministic decision-package failure injection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .replay import run_decision_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay decision safety failures")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = run_decision_replay(config, workspace_root=args.workspace_root)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"]["acceptance_gate"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
