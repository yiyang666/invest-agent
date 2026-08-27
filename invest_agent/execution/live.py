"""Persistent controlled-live approval and execution journal.

This module never calls a broker or fund CLI. It atomically gates a separately
reviewed submission and stores digests only, so sensitive order fields remain in
memory and an interrupted submission cannot be repeated before reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from invest_agent.approval.contracts import OrderAction, OrderIntent, SHA256_RE, _aware


SCHEMA_VERSION = "phase7_controlled_live_sqlite_v1"
LIVE_STATUSES = (
    "submitting",
    "submitted",
    "failed",
    "unknown",
    "reconciled_success",
    "reconciled_failed",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LivePurchaseEnvelope:
    intent: OrderIntent
    payment_method: str
    payment_account_masked: str
    transaction_account_binding_sha256: str
    agreement_record_sha256: str
    expected_confirmation: str

    def __post_init__(self) -> None:
        if self.intent.action is not OrderAction.PURCHASE:
            raise ValueError("controlled live envelope supports purchase only")
        if self.payment_method not in ("wallet", "bank_card"):
            raise ValueError("payment method must be wallet or bank_card")
        if not self.payment_account_masked.strip() or "*" not in self.payment_account_masked:
            raise ValueError("payment account must be a masked display value")
        if not SHA256_RE.fullmatch(self.transaction_account_binding_sha256):
            raise ValueError("transaction account binding must be a SHA-256 digest")
        if not SHA256_RE.fullmatch(self.agreement_record_sha256):
            raise ValueError("agreement record binding must be a SHA-256 digest")
        if not self.expected_confirmation.strip():
            raise ValueError("expected confirmation is required")

    @classmethod
    def bind_runtime_secrets(
        cls,
        *,
        intent: OrderIntent,
        payment_method: str,
        payment_account_masked: str,
        transaction_account_id: str,
        agreement_record: str,
        expected_confirmation: str,
    ) -> "LivePurchaseEnvelope":
        if not transaction_account_id.strip() or not agreement_record.strip():
            raise ValueError("runtime account and agreement bindings are required")
        return cls(
            intent=intent,
            payment_method=payment_method,
            payment_account_masked=payment_account_masked,
            transaction_account_binding_sha256=_digest(transaction_account_id),
            agreement_record_sha256=_digest(agreement_record),
            expected_confirmation=expected_confirmation,
        )

    @property
    def sha256(self) -> str:
        payload = {
            "intent_sha256": self.intent.sha256,
            "payment_method": self.payment_method,
            "payment_account_masked": self.payment_account_masked,
            "transaction_account_binding_sha256": self.transaction_account_binding_sha256,
            "agreement_record_sha256": self.agreement_record_sha256,
            "expected_confirmation": self.expected_confirmation,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @property
    def confirmation_phrase(self) -> str:
        amount = self.intent.amount_cny.quantize(Decimal("0.01"))
        fee = self.intent.estimated_fee_cny.quantize(Decimal("0.01"))
        method = "钱包" if self.payment_method == "wallet" else "银行卡"
        return (
            f"确认申购{self.intent.fund_code} {amount}元，使用"
            f"{self.payment_account_masked}（{method}），预计费用{fee}元"
        )


@dataclass(frozen=True)
class LiveApproval:
    approval_id: str
    envelope_sha256: str
    approved_at: datetime
    expires_at: datetime


class PersistentControlledLiveGateway:
    """Digest-only journal around a separately executed exact purchase."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        expiry_minutes: int = 15,
        single_purchase_cap_cny: Decimal = Decimal("5000.00"),
        daily_cap_cny: Decimal = Decimal("5000.00"),
        monthly_cap_cny: Decimal = Decimal("5000.00"),
    ) -> None:
        if expiry_minutes != 15:
            raise ValueError("live approval expiry must remain exactly 15 minutes")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.expiry_minutes = expiry_minutes
        self.single_purchase_cap_cny = single_purchase_cap_cny
        self.daily_cap_cny = daily_cap_cny
        self.monthly_cap_cny = monthly_cap_cny
        if min(single_purchase_cap_cny, daily_cap_cny, monthly_cap_cny) <= 0:
            raise ValueError("controlled live caps must be positive")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        statuses = ",".join(f"'{status}'" for status in LIVE_STATUSES)
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS live_schema_metadata (
                    schema_version TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS live_approvals (
                    approval_id TEXT PRIMARY KEY,
                    envelope_sha256 TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS live_executions (
                    envelope_sha256 TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ({statuses})),
                    attempt_count INTEGER NOT NULL CHECK (attempt_count = 1),
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reconciled_at TEXT,
                    external_order_sha256 TEXT,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    trading_day TEXT NOT NULL,
                    trading_month TEXT NOT NULL
                );
                """
            )
            versions = connection.execute(
                "SELECT schema_version FROM live_schema_metadata"
            ).fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO live_schema_metadata(schema_version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif [row["schema_version"] for row in versions] != [SCHEMA_VERSION]:
                raise ValueError("unsupported controlled live database schema")

    def record_explicit_approval(
        self,
        envelope: LivePurchaseEnvelope,
        *,
        exact_confirmation: str,
        approved_at: datetime,
    ) -> LiveApproval:
        _aware(approved_at, "approved_at")
        if exact_confirmation != envelope.confirmation_phrase:
            raise ValueError("confirmation does not match the exact live order summary")
        expires_at = approved_at + timedelta(minutes=self.expiry_minutes)
        approval_id = "live_approval_" + _digest(
            envelope.sha256 + approved_at.isoformat()
        )[:20]
        approval = LiveApproval(approval_id, envelope.sha256, approved_at, expires_at)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO live_approvals(
                    approval_id, envelope_sha256, approved_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    approval.approval_id,
                    approval.envelope_sha256,
                    approval.approved_at.isoformat(),
                    approval.expires_at.isoformat(),
                ),
            )
        return approval

    def begin_submission(
        self,
        envelope: LivePurchaseEnvelope,
        approval: LiveApproval,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        _aware(now, "now")
        amount = envelope.intent.amount_cny
        if amount is None or amount > self.single_purchase_cap_cny:
            raise ValueError("live purchase exceeds the single-order hard cap")
        amount_cents = int((amount * 100).to_integral_exact())
        trading_day = now.date().isoformat()
        trading_month = trading_day[:7]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM live_approvals WHERE approval_id = ?",
                (approval.approval_id,),
            ).fetchone()
            if row is None or (
                str(row["envelope_sha256"]) != envelope.sha256
                or approval.envelope_sha256 != envelope.sha256
                or str(row["approved_at"]) != approval.approved_at.isoformat()
                or str(row["expires_at"]) != approval.expires_at.isoformat()
            ):
                raise ValueError("live approval does not match the exact envelope")
            if row["consumed_at"] is not None:
                raise ValueError("live approval already consumed")
            if now < approval.approved_at or now > approval.expires_at:
                raise ValueError("live approval is not currently valid")
            if connection.execute(
                "SELECT 1 FROM live_executions WHERE envelope_sha256 = ?",
                (envelope.sha256,),
            ).fetchone() is not None:
                raise ValueError("live envelope already has an execution record")
            day_total = connection.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) FROM live_executions WHERE trading_day = ?",
                (trading_day,),
            ).fetchone()[0]
            month_total = connection.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) FROM live_executions WHERE trading_month = ?",
                (trading_month,),
            ).fetchone()[0]
            if day_total + amount_cents > int(self.daily_cap_cny * 100):
                raise ValueError("live purchase exceeds the daily hard cap")
            if month_total + amount_cents > int(self.monthly_cap_cny * 100):
                raise ValueError("live purchase exceeds the monthly hard cap")
            updated = connection.execute(
                "UPDATE live_approvals SET consumed_at = ? WHERE approval_id = ? AND consumed_at IS NULL",
                (now.isoformat(), approval.approval_id),
            ).rowcount
            if updated != 1:
                raise ValueError("live approval already consumed")
            connection.execute(
                """
                INSERT INTO live_executions(
                    envelope_sha256, approval_id, status, attempt_count,
                    started_at, updated_at, reconciled_at, external_order_sha256
                    , amount_cents, trading_day, trading_month
                ) VALUES (?, ?, 'submitting', 1, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    envelope.sha256,
                    approval.approval_id,
                    now.isoformat(),
                    now.isoformat(),
                    amount_cents,
                    trading_day,
                    trading_month,
                ),
            )
            connection.commit()
            return self.status(envelope)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_submission(
        self,
        envelope: LivePurchaseEnvelope,
        *,
        now: datetime,
        outcome: str,
        external_order_id: str | None = None,
    ) -> dict[str, Any]:
        _aware(now, "now")
        if outcome not in ("submitted", "failed", "unknown"):
            raise ValueError("live submission outcome is invalid")
        if outcome == "submitted" and not external_order_id:
            raise ValueError("submitted live order requires an external order ID")
        order_digest = _digest(external_order_id) if external_order_id else None
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE live_executions
                SET status = ?, updated_at = ?, external_order_sha256 = ?
                WHERE envelope_sha256 = ? AND status = 'submitting'
                """,
                (outcome, now.isoformat(), order_digest, envelope.sha256),
            ).rowcount
            if updated != 1:
                raise ValueError("live submission is not in submitting state")
        return self.status(envelope)

    def recover_incomplete(self, *, now: datetime) -> int:
        _aware(now, "now")
        with self._connect() as connection:
            return connection.execute(
                """
                UPDATE live_executions SET status = 'unknown', updated_at = ?
                WHERE status = 'submitting'
                """,
                (now.isoformat(),),
            ).rowcount

    def reconcile(
        self,
        envelope: LivePurchaseEnvelope,
        *,
        now: datetime,
        success: bool,
        external_order_id: str,
    ) -> dict[str, Any]:
        _aware(now, "now")
        if not external_order_id.strip():
            raise ValueError("reconciliation requires the exact external order ID")
        order_digest = _digest(external_order_id)
        status = "reconciled_success" if success else "reconciled_failed"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM live_executions WHERE envelope_sha256 = ?",
                (envelope.sha256,),
            ).fetchone()
            if row is None or row["status"] not in ("submitted", "unknown"):
                raise ValueError("live execution is not eligible for reconciliation")
            stored = row["external_order_sha256"]
            if stored is not None and str(stored) != order_digest:
                raise ValueError("external order ID does not match submitted order")
            connection.execute(
                """
                UPDATE live_executions
                SET status = ?, updated_at = ?, reconciled_at = ?, external_order_sha256 = ?
                WHERE envelope_sha256 = ?
                """,
                (status, now.isoformat(), now.isoformat(), order_digest, envelope.sha256),
            )
        return self.status(envelope)

    def status(self, envelope: LivePurchaseEnvelope) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM live_executions WHERE envelope_sha256 = ?",
                (envelope.sha256,),
            ).fetchone()
        if row is None:
            return None
        return {
            "envelope_sha256": str(row["envelope_sha256"]),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "started_at": str(row["started_at"]),
            "updated_at": str(row["updated_at"]),
            "reconciled": row["reconciled_at"] is not None,
            "external_order_bound": row["external_order_sha256"] is not None,
            "mode": "controlled_live",
            "automatic_retry_used": False,
            "sensitive_order_fields_persisted": False,
        }
