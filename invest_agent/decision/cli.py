"""CLI for validating the registry and assembling research-only decision input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .registry import build_decision_input, load_strategy_registry, validate_strategy_registry


DEFAULT_REGISTRY = Path("strategies/registry.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate research strategy decision inputs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-registry")
    validate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    validate.add_argument("--workspace-root", type=Path, default=Path("."))
    build = subparsers.add_parser("build")
    build.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    build.add_argument("--workspace-root", type=Path, default=Path("."))
    build.add_argument("--signals", type=Path, required=True)
    build.add_argument("--as-of", required=True)
    build.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_strategy_registry(args.registry)
        if args.command == "validate-registry":
            index = validate_strategy_registry(registry, workspace_root=args.workspace_root)
            payload = {
                "status": "pass",
                "registry_id": registry["registry_id"],
                "registered_strategy_count": len(index),
                "mode": registry["mode"],
                "execution_enabled": False,
            }
        else:
            signals = json.loads(args.signals.read_text(encoding="utf-8"))
            if not isinstance(signals, list):
                raise ValueError("signals input must be a JSON array")
            payload = build_decision_input(
                registry,
                workspace_root=args.workspace_root,
                as_of=args.as_of,
                signals=signals,
            )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if getattr(args, "output", None):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
