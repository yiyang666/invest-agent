from datetime import date, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from invest_agent.approval.contracts import (
    ApprovalGate,
    ApprovalPolicy,
    OrderAction,
    OrderIntent,
)
from invest_agent.execution.mock import MockExecutionGateway, MockOutcome
from invest_agent.approval.sqlite_gate import SqliteApprovalGate


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.fromisoformat("2026-08-25T09:00:00+08:00")


def _intent(**changes: object) -> OrderIntent:
    values = {
        "fund_code": "019861",
        "fund_name": "鹏华上证科创100ETF联接A",
        "action": OrderAction.PURCHASE,
        "amount_cny": Decimal("200.00"),
        "shares": None,
        "order_reference": None,
        "scheduled_date": date(2026, 9, 1),
        "source_evidence_sha256": "b" * 64,
        "route_rule_version": "mock_route_v1",
        "account_alias": "fund_account_primary",
        "estimated_fee_cny": Decimal("0.30"),
        "expected_result": "submit_subscription_for_confirmation",
    }
    values.update(changes)
    return OrderIntent(**values)


class Phase6MockApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        payload = json.loads(
            (ROOT / "config/phase6_mock_approval_policy_v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.policy = ApprovalPolicy.from_mapping(payload)
        self.gate = ApprovalGate(self.policy)

    def test_policy_keeps_real_execution_disabled(self) -> None:
        self.assertEqual(self.policy.mode, "mock_only")
        self.assertEqual(self.policy.approval_expiry_minutes, 15)
        self.assertFalse(self.policy.real_trading_enabled)
        self.assertFalse(self.policy.real_adapter_allowed)

    def test_exact_approval_is_consumed_once(self) -> None:
        intent = _intent()
        approval = self.gate.record_explicit_approval(intent, approved_at=NOW)

        consumed = self.gate.consume(intent, approval, now=NOW + timedelta(minutes=1))
        self.assertEqual(consumed.intent_sha256, intent.sha256)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.gate.consume(intent, approval, now=NOW + timedelta(minutes=2))

    def test_key_change_and_expiry_fail_closed(self) -> None:
        intent = _intent()
        changes = (
            {"fund_code": "110020"},
            {"fund_name": "另一只基金"},
            {"amount_cny": Decimal("201.00")},
            {"scheduled_date": date(2026, 9, 2)},
            {"source_evidence_sha256": "c" * 64},
            {"route_rule_version": "mock_route_v2"},
            {"account_alias": "fund_account_secondary"},
            {"estimated_fee_cny": Decimal("0.31")},
            {"expected_result": "different_expected_result"},
        )
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                approved_at = NOW + timedelta(seconds=index)
                approval = self.gate.record_explicit_approval(
                    intent, approved_at=approved_at
                )
                with self.assertRaisesRegex(ValueError, "does not match"):
                    self.gate.consume(
                        _intent(**change),
                        approval,
                        now=approved_at + timedelta(minutes=1),
                    )

        approval = self.gate.record_explicit_approval(intent, approved_at=NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.gate.consume(intent, approval, now=NOW + timedelta(minutes=16))

    def test_unknown_state_blocks_retry_until_reconciled(self) -> None:
        intent = _intent()
        gateway = MockExecutionGateway(self.gate)
        first = self.gate.record_explicit_approval(intent, approved_at=NOW)
        result = gateway.submit(
            intent,
            first,
            now=NOW + timedelta(minutes=1),
            outcome=MockOutcome.UNKNOWN,
        )
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["real_trading_enabled"])

        second = self.gate.record_explicit_approval(
            intent, approved_at=NOW + timedelta(minutes=2)
        )
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            gateway.submit(
                intent,
                second,
                now=NOW + timedelta(minutes=3),
                outcome=MockOutcome.SUBMITTED,
            )

        reconciled = gateway.reconcile(intent, status=MockOutcome.FAILED)
        self.assertEqual(reconciled["status"], "failed")
        self.assertTrue(reconciled["reconciled"])

    def test_sensitive_account_alias_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "account alias"):
            _intent(account_alias="6222021234567890")

    def test_sqlite_approval_survives_restart_and_contains_no_account_data(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "approvals.sqlite3"
            first_process = SqliteApprovalGate(self.policy, database)
            approval = first_process.record_explicit_approval(intent, approved_at=NOW)

            restarted_process = SqliteApprovalGate(self.policy, database)
            restarted_process.consume(
                intent, approval, now=NOW + timedelta(minutes=1)
            )
            with self.assertRaisesRegex(ValueError, "already consumed"):
                SqliteApprovalGate(self.policy, database).consume(
                    intent, approval, now=NOW + timedelta(minutes=2)
                )

            stored = database.read_bytes()
            self.assertNotIn(intent.account_alias.encode(), stored)
            self.assertNotIn(intent.fund_code.encode(), stored)
            with sqlite3.connect(database) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(mock_approvals)")
                }
            self.assertNotIn("account_alias", columns)
            self.assertNotIn("fund_code", columns)

    def test_sqlite_concurrent_consume_has_exactly_one_winner(self) -> None:
        intent = _intent()
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "approvals.sqlite3"
            gate = SqliteApprovalGate(self.policy, database)
            approval = gate.record_explicit_approval(intent, approved_at=NOW)

            def consume_once() -> str:
                try:
                    SqliteApprovalGate(self.policy, database).consume(
                        intent, approval, now=NOW + timedelta(minutes=1)
                    )
                    return "consumed"
                except ValueError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: consume_once(), range(2)))

            self.assertEqual(results.count("consumed"), 1)
            self.assertEqual(results.count("approval already consumed"), 1)


if __name__ == "__main__":
    unittest.main()
