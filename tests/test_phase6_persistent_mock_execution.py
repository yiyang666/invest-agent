from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from invest_agent.approval.contracts import ApprovalPolicy
from invest_agent.approval.sqlite_gate import SqliteApprovalGate
from invest_agent.execution.mock import MockOutcome
from invest_agent.execution.sqlite_mock import (
    PersistentMockExecutionGateway,
    SimulatedProcessCrash,
)

from tests.test_phase6_mock_approval import _intent


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-08-25T10:00:00+08:00")


def _policy() -> ApprovalPolicy:
    payload = json.loads(
        (ROOT / "config/phase6_mock_approval_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    return ApprovalPolicy.from_mapping(payload)


class Phase6PersistentMockExecutionTests(unittest.TestCase):
    def test_submitted_state_survives_restart_and_blocks_duplicate(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mock.sqlite3"
            gate = SqliteApprovalGate(_policy(), database)
            gateway = PersistentMockExecutionGateway(gate)
            approval = gate.record_explicit_approval(intent, approved_at=NOW)
            result = gateway.submit(
                intent,
                approval,
                now=NOW + timedelta(minutes=1),
                outcome=MockOutcome.SUBMITTED,
            )
            self.assertEqual(result["status"], "submitted")

            restarted_gate = SqliteApprovalGate(_policy(), database)
            restarted = PersistentMockExecutionGateway(restarted_gate)
            self.assertEqual(restarted.status(intent)["status"], "submitted")
            second = restarted_gate.record_explicit_approval(
                intent, approved_at=NOW + timedelta(minutes=2)
            )
            with self.assertRaisesRegex(ValueError, "cannot be submitted again"):
                restarted.submit(
                    intent,
                    second,
                    now=NOW + timedelta(minutes=3),
                    outcome=MockOutcome.SUBMITTED,
                )

    def test_crash_before_atomic_commit_rolls_back_approval_and_state(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mock.sqlite3"
            gate = SqliteApprovalGate(_policy(), database)
            gateway = PersistentMockExecutionGateway(gate)
            approval = gate.record_explicit_approval(intent, approved_at=NOW)
            with self.assertRaises(SimulatedProcessCrash):
                gateway.begin_submission(
                    intent,
                    approval,
                    now=NOW + timedelta(minutes=1),
                    fault_after_approval_consume=True,
                )
            self.assertIsNone(gateway.status(intent))

            result = gateway.submit(
                intent,
                approval,
                now=NOW + timedelta(minutes=2),
                outcome=MockOutcome.FAILED,
            )
            self.assertEqual(result["status"], "failed")

    def test_crash_after_begin_recovers_to_unknown_and_requires_reconciliation(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mock.sqlite3"
            gate = SqliteApprovalGate(_policy(), database)
            gateway = PersistentMockExecutionGateway(gate)
            approval = gate.record_explicit_approval(intent, approved_at=NOW)
            with self.assertRaises(SimulatedProcessCrash):
                gateway.submit(
                    intent,
                    approval,
                    now=NOW + timedelta(minutes=1),
                    outcome=MockOutcome.SUBMITTED,
                    crash_after_begin=True,
                )
            self.assertEqual(gateway.status(intent)["status"], "submitting")

            restarted_gate = SqliteApprovalGate(_policy(), database)
            restarted = PersistentMockExecutionGateway(restarted_gate)
            self.assertEqual(
                restarted.recover_incomplete(now=NOW + timedelta(minutes=2)), 1
            )
            self.assertEqual(restarted.status(intent)["status"], "unknown")
            second = restarted_gate.record_explicit_approval(
                intent, approved_at=NOW + timedelta(minutes=3)
            )
            with self.assertRaisesRegex(ValueError, "requires reconciliation"):
                restarted.submit(
                    intent,
                    second,
                    now=NOW + timedelta(minutes=4),
                    outcome=MockOutcome.SUBMITTED,
                )

            reconciled = restarted.reconcile(
                intent,
                now=NOW + timedelta(minutes=5),
                status=MockOutcome.FAILED,
            )
            self.assertEqual(reconciled["status"], "failed")
            self.assertTrue(reconciled["reconciled"])

    def test_concurrent_begin_has_one_winner_and_journal_has_no_plaintext(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "mock.sqlite3"
            gate = SqliteApprovalGate(_policy(), database)
            approval = gate.record_explicit_approval(intent, approved_at=NOW)

            def begin_once() -> str:
                try:
                    PersistentMockExecutionGateway(
                        SqliteApprovalGate(_policy(), database)
                    ).begin_submission(
                        intent, approval, now=NOW + timedelta(minutes=1)
                    )
                    return "begun"
                except ValueError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: begin_once(), range(2)))
            self.assertEqual(results.count("begun"), 1)
            self.assertEqual(
                results.count("incomplete mock submission requires reconciliation"), 1
            )

            stored = database.read_bytes()
            self.assertNotIn(intent.account_alias.encode(), stored)
            self.assertNotIn(intent.fund_code.encode(), stored)
            with sqlite3.connect(database) as connection:
                events = connection.execute(
                    "SELECT status FROM mock_execution_events ORDER BY event_sequence"
                ).fetchall()
            self.assertEqual(events, [("submitting",)])


if __name__ == "__main__":
    unittest.main()
