from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from invest_agent.execution.mock_cli import main

from tests.test_phase6_mock_approval import _intent


ROOT = Path(__file__).resolve().parents[1]
AT = datetime.fromisoformat("2026-08-25T11:00:00+08:00").isoformat()


class Phase6MockCliTests(unittest.TestCase):
    def test_exact_confirmation_runs_persistent_mock_rehearsal(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            intent_path = Path(temp_dir) / "intent.json"
            database = Path(temp_dir) / "mock.sqlite3"
            intent_path.write_text(json.dumps(intent.to_dict()), encoding="utf-8")
            output = StringIO()
            with patch("builtins.input", return_value=f"APPROVE MOCK {intent.sha256}"), redirect_stdout(output):
                status = main(
                    [
                        "--policy",
                        str(ROOT / "config/phase6_mock_approval_policy_v1.json"),
                        "--db",
                        str(database),
                        "--at",
                        AT,
                        "rehearse",
                        "--intent",
                        str(intent_path),
                        "--outcome",
                        "submitted",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertIn("真实交易：关闭", output.getvalue())
            self.assertIn('"status": "submitted"', output.getvalue())

            status_output = StringIO()
            with redirect_stdout(status_output):
                status = main(
                    [
                        "--policy",
                        str(ROOT / "config/phase6_mock_approval_policy_v1.json"),
                        "--db",
                        str(database),
                        "--at",
                        AT,
                        "status",
                        "--intent",
                        str(intent_path),
                    ]
                )
            self.assertEqual(status, 0)
            self.assertIn('"status": "submitted"', status_output.getvalue())

    def test_inexact_confirmation_creates_no_execution_state(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            intent_path = Path(temp_dir) / "intent.json"
            database = Path(temp_dir) / "mock.sqlite3"
            intent_path.write_text(json.dumps(intent.to_dict()), encoding="utf-8")
            with patch("builtins.input", return_value="yes"), redirect_stdout(StringIO()):
                status = main(
                    [
                        "--policy",
                        str(ROOT / "config/phase6_mock_approval_policy_v1.json"),
                        "--db",
                        str(database),
                        "--at",
                        AT,
                        "rehearse",
                        "--intent",
                        str(intent_path),
                        "--outcome",
                        "submitted",
                    ]
                )
            self.assertEqual(status, 1)
            with sqlite3.connect(database) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM mock_executions"
                ).fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
