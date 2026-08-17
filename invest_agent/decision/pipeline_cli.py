"""CLI for the offline, manifest-last monthly research pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import (
    ARTIFACT_KEYS,
    _json_bytes,
    _resolve_output,
    build_monthly_research_pipeline,
    write_immutable,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline monthly research pipeline")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        manifest, artifacts = build_monthly_research_pipeline(
            config, workspace_root=args.workspace_root
        )
        for key in ARTIFACT_KEYS:
            path = _resolve_output(
                args.workspace_root, config["artifact_paths"][key], f"artifact_paths.{key}"
            )
            write_immutable(path, artifacts[key])
        manifest_path = _resolve_output(
            args.workspace_root, config["manifest_path"], "manifest_path"
        )
        write_immutable(manifest_path, _json_bytes(manifest))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "pipeline_id": manifest["pipeline_id"],
                "run_id": manifest["run_id"],
                "stages_passed": manifest["summary"]["stages_passed"],
                "artifacts": len(manifest["artifacts"]),
                "network_used": False,
                "automatic_schedule_used": False,
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
