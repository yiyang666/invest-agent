"""CLI for incremental public fund-data synchronization."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from invest_agent.data.sync import load_sync_config, run_incremental_sync, sync_plan_payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synchronize the configured fund universe")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    parser.add_argument("--as-of")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed


def _default_output(root: Path, run_as_of: datetime) -> Path:
    return root / "data" / "private" / "sync" / f"fund-data-sync-{run_as_of:%Y%m%dT%H%M%S%z}.json"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.workspace_root.resolve()
    try:
        config = load_sync_config(args.config)
        run_as_of = _as_of(args.as_of)
        lock_path = root / "data" / "private" / "sync" / ".fund-data-sync.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.parent.chmod(0o700)
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another fund data sync is already running") from exc
            payload = (
                sync_plan_payload(config, workspace_root=root, as_of=run_as_of)
                if args.dry_run
                else run_incremental_sync(config, workspace_root=root, as_of=run_as_of)
            )
        output = args.output or _default_output(root, run_as_of)
        if not output.is_absolute():
            output = root / output
        output = output.resolve()
        if output != root and root not in output.parents:
            raise ValueError("output path escapes workspace root")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.parent.chmod(0o700)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        output.chmod(0o600)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(output)
    if args.dry_run:
        return 0
    return 0 if payload["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
