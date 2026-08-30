"""Single deterministic entry point for fund and market-data schedules."""

from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping
from zoneinfo import ZoneInfo

from .schedule import cadence_period, due_jobs, load_schedule_jobs


DEFAULT_FUND_CONFIG = Path("config/fund_data_sync_v1.json")
DEFAULT_MARKET_CONFIG = Path("config/market_data_sync_v1.json")
DEFAULT_STATE = Path("data/private/automation/data-maintenance-state.json")
DEFAULT_OUTPUT_DIR = Path("data/private/automation")


def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include timezone")
    return parsed


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": 1, "jobs": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("jobs"), dict):
        raise ValueError("maintenance state must use schema_version 1")
    return payload


def _write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _run_json_command(argv: list[str], root: Path) -> tuple[int, object, str]:
    completed = subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = completed.stdout.strip()
    parsed: object = stdout
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            candidate = Path(stdout.splitlines()[-1])
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.is_file():
                try:
                    parsed = json.loads(candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    parsed = {"report_path": str(candidate)}
    return completed.returncode, parsed, completed.stderr.strip()


def _run_job(
    job: Mapping[str, object], *, root: Path, as_of: datetime
) -> dict[str, object]:
    kind = job.get("kind")
    results: list[dict[str, object]] = []
    if kind == "fund_data_sync":
        argv = [
            sys.executable,
            "-m",
            "invest_agent.data.sync_cli",
            "--config",
            str(job["config_path"]),
            "--workspace-root",
            str(root),
            "--as-of",
            as_of.isoformat(),
        ]
        code, payload, stderr = _run_json_command(argv, root)
        results.append({"command": "fund_data_sync", "exit_code": code, "result": payload, "stderr": stderr})
    elif kind == "market_data_sync":
        commands = job.get("commands")
        if not isinstance(commands, list):
            raise ValueError(f"Market job has no commands: {job.get('job_id')}")
        market_config = json.loads((root / str(job["config_path"])).read_text(encoding="utf-8"))
        database = str(market_config["database_path"])
        raw_root = str(market_config["raw_root"])
        for raw in commands:
            if not isinstance(raw, Mapping):
                raise ValueError("Market command must be one object")
            operation = raw.get("operation")
            arguments = raw.get("arguments", {})
            if operation == "audit_schema":
                tool = "audit_schema"
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "audit-schema",
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--raw-root",
                    raw_root,
                ]
            elif operation == "publish_fund_proxies":
                if not isinstance(arguments, Mapping):
                    raise ValueError("publish_fund_proxies arguments must be an object")
                tool = "publish_fund_proxies"
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "publish-fund-proxies",
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--db",
                    database,
                    "--fund-db",
                    database,
                    "--raw-root",
                    raw_root,
                ]
                if arguments.get("history") is True:
                    argv.append("--history")
            elif operation == "collect_fred":
                if not isinstance(arguments, Mapping):
                    raise ValueError("collect_fred arguments must be an object")
                series = arguments.get("series")
                lookback_days = arguments.get("lookback_days", 120)
                if not isinstance(series, str) or not series:
                    raise ValueError("collect_fred requires a series")
                if not isinstance(lookback_days, int) or lookback_days < 0:
                    raise ValueError("collect_fred lookback_days must be a non-negative integer")
                tool = f"fred:{series}"
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "collect-fred",
                    "--series",
                    series,
                    "--lookback-days",
                    str(lookback_days),
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--db",
                    database,
                    "--raw-root",
                    raw_root,
                ]
            elif operation == "collect_sse_breadth":
                if arguments not in ({}, None):
                    raise ValueError("collect_sse_breadth does not accept arguments")
                tool = "sse_public_eod_breadth"
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "collect-sse-breadth",
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--db",
                    database,
                    "--raw-root",
                    raw_root,
                ]
            elif operation == "collect_guchacha_breadth":
                if arguments not in ({}, None):
                    raise ValueError("collect_guchacha_breadth does not accept arguments")
                tool = "guchacha_public_dashboard_breadth"
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "collect-guchacha-breadth",
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--db",
                    database,
                    "--raw-root",
                    raw_root,
                ]
            elif operation is None:
                tool = str(raw.get("tool", ""))
                argv = [
                    sys.executable,
                    "-m",
                    "invest_agent.market_data.cli",
                    "collect",
                    "--tool",
                    tool,
                    "--arguments",
                    json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "--as-of",
                    as_of.isoformat(),
                    "--config",
                    str(job["config_path"]),
                    "--db",
                    database,
                    "--raw-root",
                    raw_root,
                ]
            else:
                raise ValueError(f"Unknown market maintenance operation: {operation}")
            code, payload, stderr = _run_json_command(argv, root)
            results.append({"command": tool, "arguments": arguments, "exit_code": code, "result": payload, "stderr": stderr})
            if operation == "audit_schema" and code != 0:
                break
    else:
        raise ValueError(f"Unknown maintenance job kind: {kind}")
    failures = sum(int(item["exit_code"]) != 0 for item in results)
    return {
        "job_id": job["job_id"],
        "kind": kind,
        "due_period": job.get("due_period"),
        "status": "complete" if failures == 0 else "failed",
        "commands": len(results),
        "failures": failures,
        "results": results,
    }


def _bootstrap_job(market_config_path: Path) -> dict[str, object]:
    payload = json.loads(market_config_path.read_text(encoding="utf-8"))
    commands = payload.get("bootstrap_commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("market sync config requires non-empty bootstrap_commands")
    return {
        "job_id": "market_context_bootstrap",
        "kind": "market_data_sync",
        "cadence": "manual",
        "config_path": str(market_config_path),
        "commands": commands,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run local data maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run-due", "run-job", "run-bootstrap"):
        command = subparsers.add_parser(name)
        command.add_argument("--workspace-root", type=Path, default=Path("."))
        command.add_argument("--fund-config", type=Path, default=DEFAULT_FUND_CONFIG)
        command.add_argument("--market-config", type=Path, default=DEFAULT_MARKET_CONFIG)
        command.add_argument("--state", type=Path, default=DEFAULT_STATE)
        command.add_argument("--as-of")
        if name == "run-due":
            command.add_argument("--output", type=Path)
        if name == "run-job":
            command.add_argument("--job-id", required=True)
            command.add_argument("--output", type=Path)
        if name == "run-bootstrap":
            command.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.workspace_root.resolve()
    try:
        as_of = _as_of(args.as_of)
        fund_config = args.fund_config if args.fund_config.is_absolute() else root / args.fund_config
        market_config = args.market_config if args.market_config.is_absolute() else root / args.market_config
        state_path = args.state if args.state.is_absolute() else root / args.state
        jobs = load_schedule_jobs(fund_config_path=fund_config, market_config_path=market_config)
        state = _load_state(state_path)
        due = due_jobs(jobs, state, as_of)
        if args.command == "plan":
            print(json.dumps({"as_of": as_of.isoformat(), "due_jobs": due, "all_jobs": jobs}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run-job":
            selected = [dict(job) for job in jobs if job["job_id"] == args.job_id]
            if not selected:
                raise ValueError(f"Unknown maintenance job: {args.job_id}")
            period = cadence_period(selected[0], as_of) or f"manual-{as_of:%Y%m%dT%H%M%S}"
            selected[0]["due_period"] = period
            due = tuple(selected)
        if args.command == "run-bootstrap":
            selected = _bootstrap_job(market_config)
            selected["due_period"] = f"manual-{as_of:%Y%m%dT%H%M%S}"
            due = (selected,)

        lock_path = state_path.parent / ".data-maintenance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.parent.chmod(0o700)
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another data maintenance run is active") from exc
            job_results = []
            state_jobs = state["jobs"]
            assert isinstance(state_jobs, dict)
            for job in due:
                result = _run_job(job, root=root, as_of=as_of)
                job_results.append(result)
                job_state = {
                    "last_attempt_at": as_of.isoformat(),
                    "last_status": result["status"],
                    "last_attempt_period": job.get("due_period"),
                }
                if result["status"] == "complete":
                    job_state["last_success_at"] = as_of.isoformat()
                    job_state["last_success_period"] = job.get("due_period")
                else:
                    prior = state_jobs.get(str(job["job_id"]))
                    if isinstance(prior, Mapping):
                        for key in ("last_success_at", "last_success_period"):
                            if key in prior:
                                job_state[key] = prior[key]
                state_jobs[str(job["job_id"])] = job_state
            _write_private_json(state_path, state)
        failures = sum(result["status"] != "complete" for result in job_results)
        payload = {
            "schema_version": 1,
            "run_at": as_of.isoformat(),
            "status": "complete" if failures == 0 else "partial",
            "due_job_count": len(due),
            "completed_job_count": len(job_results) - failures,
            "failed_job_count": failures,
            "jobs": job_results,
            "trading_attempted": False,
            "strategy_inference_attempted": False,
        }
        output = args.output
        if output is None:
            output = DEFAULT_OUTPUT_DIR / f"data-maintenance-{as_of:%Y%m%dT%H%M%S%z}.json"
        if not output.is_absolute():
            output = root / output
        _write_private_json(output, payload)
        print(output)
        return 0 if failures == 0 else 2
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
