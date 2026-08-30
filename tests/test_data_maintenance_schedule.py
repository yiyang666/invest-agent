from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from invest_agent.automation.maintenance_cli import _bootstrap_job, _run_job, main
from invest_agent.automation.schedule import due_jobs, load_schedule_jobs


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")


class DataMaintenanceScheduleTests(unittest.TestCase):
    def _jobs(self):
        return load_schedule_jobs(
            fund_config_path=ROOT / "config/fund_data_sync_v1.json",
            market_config_path=ROOT / "config/market_data_sync_v1.json",
        )

    def test_one_registry_contains_all_cadences_and_existing_fund_sync(self) -> None:
        jobs = self._jobs()
        self.assertEqual(
            {job["job_id"] for job in jobs},
            {
                "fund_data_daily",
                "market_daily_series",
                "market_weekly_context",
                "market_monthly_context",
                "market_quarterly_context",
            },
        )
        self.assertEqual(
            {job["cadence"] for job in jobs},
            {"business_daily", "weekly", "monthly", "quarterly"},
        )

    def test_weekly_and_daily_are_due_once_in_their_period(self) -> None:
        jobs = self._jobs()
        as_of = datetime(2026, 8, 28, 23, 55, tzinfo=TZ)
        state = {
            "schema_version": 1,
            "jobs": {
                "market_monthly_context": {"last_success_period": "2026-08"},
                "market_quarterly_context": {"last_success_period": "2026-Q3"},
            },
        }
        due = due_jobs(jobs, state, as_of)
        self.assertEqual(
            {job["job_id"] for job in due},
            {"fund_data_daily", "market_daily_series", "market_weekly_context"},
        )
        for job in due:
            state["jobs"][job["job_id"]] = {"last_success_period": job["due_period"]}
        self.assertEqual(due_jobs(jobs, state, as_of), ())

    def test_monthly_and_quarterly_catch_up_after_nominal_day(self) -> None:
        jobs = self._jobs()
        as_of = datetime(2026, 8, 5, 23, 55, tzinfo=TZ)
        due = due_jobs(jobs, {"schema_version": 1, "jobs": {}}, as_of)
        ids = {job["job_id"] for job in due}
        self.assertIn("market_monthly_context", ids)
        self.assertIn("market_quarterly_context", ids)

    def test_plan_is_read_only_and_emits_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "plan",
                        "--workspace-root",
                        str(ROOT),
                        "--state",
                        str(Path(directory) / "missing-state.json"),
                        "--as-of",
                        "2026-08-28T23:55:00+08:00",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(len(payload["all_jobs"]), 5)
            self.assertGreaterEqual(len(payload["due_jobs"]), 3)
            self.assertFalse((Path(directory) / "missing-state.json").exists())

    def test_bootstrap_is_one_manual_market_job_with_history_backfills(self) -> None:
        job = _bootstrap_job(ROOT / "config/market_data_sync_v1.json")
        self.assertEqual(job["job_id"], "market_context_bootstrap")
        commands = job["commands"]
        self.assertIsInstance(commands, list)
        self.assertEqual(len(commands), 37)
        self.assertEqual(commands[0], {"operation": "audit_schema"})
        self.assertIn(
            {"operation": "publish_fund_proxies", "arguments": {"history": True}},
            commands,
        )
        self.assertIn(
            {"operation": "collect_fred", "arguments": {"series": "NFCI", "lookback_days": 0}},
            commands,
        )
        self.assertIn({"operation": "collect_guchacha_breadth"}, commands)
        self.assertTrue(
            any(
                command.get("tool") == "get_market_series"
                and command.get("arguments", {}).get("limit") == 500
                for command in commands
            )
        )
        self.assertTrue(
            any(
                command.get("tool") == "get_macro"
                and command.get("arguments", {}).get("indicator") == "gdp"
                for command in commands
            )
        )

    def test_schema_audit_failure_stops_remaining_market_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "market.json"
            config.write_text(
                json.dumps(
                    {
                        "database_path": "data/private/test.sqlite3",
                        "raw_root": "data/raw",
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "job_id": "market_test",
                "kind": "market_data_sync",
                "config_path": str(config),
                "commands": [
                    {"operation": "audit_schema"},
                    {"tool": "list_datasets", "arguments": {}},
                ],
            }
            with patch(
                "invest_agent.automation.maintenance_cli._run_json_command",
                return_value=(2, {"status": "schema_drift"}, ""),
            ) as mocked:
                result = _run_job(
                    job,
                    root=root,
                    as_of=datetime(2026, 8, 28, 23, 55, tzinfo=TZ),
                )
            self.assertEqual(mocked.call_count, 1)
            self.assertEqual(result["commands"], 1)
            self.assertEqual(result["failures"], 1)
            self.assertEqual(result["status"], "failed")

    def test_sse_breadth_operation_routes_to_reviewed_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "market.json"
            config.write_text(
                json.dumps(
                    {
                        "database_path": "data/private/test.sqlite3",
                        "raw_root": "data/raw",
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "job_id": "market_sse_test",
                "kind": "market_data_sync",
                "config_path": str(config),
                "commands": [{"operation": "collect_sse_breadth"}],
            }
            with patch(
                "invest_agent.automation.maintenance_cli._run_json_command",
                return_value=(0, {"status": "published"}, ""),
            ) as mocked:
                result = _run_job(
                    job,
                    root=root,
                    as_of=datetime(2026, 8, 28, 23, 55, tzinfo=TZ),
                )
            argv = mocked.call_args.args[0]
            self.assertIn("collect-sse-breadth", argv)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["results"][0]["command"], "sse_public_eod_breadth")

    def test_guchacha_breadth_operation_routes_to_reviewed_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "market.json"
            config.write_text(
                json.dumps(
                    {
                        "database_path": "data/private/test.sqlite3",
                        "raw_root": "data/raw",
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "job_id": "market_guchacha_breadth_test",
                "kind": "market_data_sync",
                "config_path": str(config),
                "commands": [{"operation": "collect_guchacha_breadth"}],
            }
            with patch(
                "invest_agent.automation.maintenance_cli._run_json_command",
                return_value=(0, {"status": "published"}, ""),
            ) as mocked:
                result = _run_job(
                    job,
                    root=root,
                    as_of=datetime(2026, 8, 28, 23, 55, tzinfo=TZ),
                )
            argv = mocked.call_args.args[0]
            self.assertIn("collect-guchacha-breadth", argv)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(
                result["results"][0]["command"],
                "guchacha_public_dashboard_breadth",
            )


if __name__ == "__main__":
    unittest.main()
