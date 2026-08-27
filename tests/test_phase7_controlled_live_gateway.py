from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest

from invest_agent.execution.live import (
    LivePurchaseEnvelope,
    PersistentControlledLiveGateway,
)
from tests.test_phase6_mock_approval import _intent


UTC = timezone.utc


def _envelope(*, amount: Decimal = Decimal("200.00")) -> LivePurchaseEnvelope:
    return LivePurchaseEnvelope.bind_runtime_secrets(
        intent=_intent(amount_cny=amount),
        payment_method="wallet",
        payment_account_masked="中国银行****2996",
        transaction_account_id="runtime-account-secret",
        agreement_record="runtime-agreement-secret",
        expected_confirmation="预计下一工作日确认",
    )


class Phase7ControlledLiveGatewayTests(unittest.TestCase):
    def test_exact_confirmation_is_one_time_and_reconciles(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "live.sqlite3"
            gateway = PersistentControlledLiveGateway(database)
            envelope = _envelope()
            approved_at = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
            approval = gateway.record_explicit_approval(
                envelope,
                exact_confirmation=envelope.confirmation_phrase,
                approved_at=approved_at,
            )
            started = gateway.begin_submission(
                envelope, approval, now=approved_at + timedelta(minutes=1)
            )
            self.assertEqual(started["status"], "submitting")
            with self.assertRaisesRegex(ValueError, "already"):
                gateway.begin_submission(
                    envelope, approval, now=approved_at + timedelta(minutes=2)
                )
            submitted = gateway.complete_submission(
                envelope,
                now=approved_at + timedelta(minutes=2),
                outcome="submitted",
                external_order_id="external-order-123",
            )
            self.assertTrue(submitted["external_order_bound"])
            reconciled = gateway.reconcile(
                envelope,
                now=approved_at + timedelta(days=1),
                success=True,
                external_order_id="external-order-123",
            )
            self.assertEqual(reconciled["status"], "reconciled_success")
            self.assertTrue(reconciled["reconciled"])

    def test_confirmation_change_expiry_and_forged_times_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gateway = PersistentControlledLiveGateway(Path(temp) / "live.sqlite3")
            envelope = _envelope()
            approved_at = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
            with self.assertRaisesRegex(ValueError, "exact live order"):
                gateway.record_explicit_approval(
                    envelope,
                    exact_confirmation=envelope.confirmation_phrase + "改",
                    approved_at=approved_at,
                )
            approval = gateway.record_explicit_approval(
                envelope,
                exact_confirmation=envelope.confirmation_phrase,
                approved_at=approved_at,
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                gateway.begin_submission(
                    envelope,
                    replace(approval, expires_at=approved_at + timedelta(days=1)),
                    now=approved_at + timedelta(minutes=1),
                )
            with self.assertRaisesRegex(ValueError, "not currently valid"):
                gateway.begin_submission(
                    envelope, approval, now=approved_at + timedelta(minutes=16)
                )

    def test_restart_recovers_submitting_to_unknown_and_blocks_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "live.sqlite3"
            envelope = _envelope()
            now = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
            first = PersistentControlledLiveGateway(database)
            approval = first.record_explicit_approval(
                envelope,
                exact_confirmation=envelope.confirmation_phrase,
                approved_at=now,
            )
            first.begin_submission(envelope, approval, now=now + timedelta(minutes=1))
            restarted = PersistentControlledLiveGateway(database)
            self.assertEqual(
                restarted.recover_incomplete(now=now + timedelta(minutes=2)), 1
            )
            self.assertEqual(restarted.status(envelope)["status"], "unknown")
            with self.assertRaisesRegex(ValueError, "already"):
                restarted.begin_submission(
                    envelope, approval, now=now + timedelta(minutes=3)
                )
            reconciled = restarted.reconcile(
                envelope,
                now=now + timedelta(minutes=4),
                success=False,
                external_order_id="checked-order-id",
            )
            self.assertEqual(reconciled["status"], "reconciled_failed")

    def test_database_contains_no_order_account_agreement_or_external_id_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "live.sqlite3"
            gateway = PersistentControlledLiveGateway(database)
            envelope = _envelope()
            now = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
            approval = gateway.record_explicit_approval(
                envelope,
                exact_confirmation=envelope.confirmation_phrase,
                approved_at=now,
            )
            gateway.begin_submission(envelope, approval, now=now + timedelta(minutes=1))
            gateway.complete_submission(
                envelope,
                now=now + timedelta(minutes=2),
                outcome="submitted",
                external_order_id="external-order-123",
            )
            raw = database.read_bytes()
            for secret in (
                b"019861",
                b"runtime-account-secret",
                b"runtime-agreement-secret",
                b"external-order-123",
                "中国银行".encode(),
            ):
                self.assertNotIn(secret, raw)
            with sqlite3.connect(database) as connection:
                attempts = connection.execute(
                    "SELECT attempt_count FROM live_executions"
                ).fetchone()[0]
            self.assertEqual(attempts, 1)

    def test_single_daily_and_monthly_caps_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gateway = PersistentControlledLiveGateway(
                Path(temp) / "live.sqlite3",
                single_purchase_cap_cny=Decimal("300.00"),
                daily_cap_cny=Decimal("300.00"),
                monthly_cap_cny=Decimal("300.00"),
            )
            now = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)
            too_large = _envelope(amount=Decimal("300.01"))
            approval = gateway.record_explicit_approval(
                too_large,
                exact_confirmation=too_large.confirmation_phrase,
                approved_at=now,
            )
            with self.assertRaisesRegex(ValueError, "single-order"):
                gateway.begin_submission(
                    too_large, approval, now=now + timedelta(minutes=1)
                )

            first = _envelope(amount=Decimal("200.00"))
            first_approval = gateway.record_explicit_approval(
                first,
                exact_confirmation=first.confirmation_phrase,
                approved_at=now,
            )
            gateway.begin_submission(first, first_approval, now=now + timedelta(minutes=1))
            gateway.complete_submission(
                first,
                now=now + timedelta(minutes=2),
                outcome="failed",
            )
            second = _envelope(amount=Decimal("150.00"))
            second_approval = gateway.record_explicit_approval(
                second,
                exact_confirmation=second.confirmation_phrase,
                approved_at=now + timedelta(minutes=3),
            )
            with self.assertRaisesRegex(ValueError, "daily"):
                gateway.begin_submission(
                    second, second_approval, now=now + timedelta(minutes=4)
                )


if __name__ == "__main__":
    unittest.main()
