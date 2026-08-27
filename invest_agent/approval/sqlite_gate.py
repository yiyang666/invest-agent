"""SQLite-backed one-time mock approvals that survive process restarts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from .contracts import (
    ApprovalPolicy,
    ApprovalRecord,
    OrderIntent,
    SHA256_RE,
    _aware,
    _canonical_sha256,
)


SCHEMA_VERSION = "phase6_mock_approval_sqlite_v1"


class SqliteApprovalGate:
    """Persist only digests and timestamps; never store accounts or credentials."""

    def __init__(self, policy: ApprovalPolicy, database_path: str | Path) -> None:
        self.policy = policy
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_schema_metadata (
                    schema_version TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS mock_approvals (
                    approval_id TEXT PRIMARY KEY,
                    intent_sha256 TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    mode TEXT NOT NULL CHECK (mode = 'mock_only'),
                    real_trading_enabled INTEGER NOT NULL CHECK (real_trading_enabled = 0)
                );
                """
            )
            versions = connection.execute(
                "SELECT schema_version FROM approval_schema_metadata"
            ).fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO approval_schema_metadata(schema_version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif [row["schema_version"] for row in versions] != [SCHEMA_VERSION]:
                raise ValueError("unsupported approval database schema")

    def record_explicit_approval(
        self, intent: OrderIntent, *, approved_at: datetime
    ) -> ApprovalRecord:
        _aware(approved_at, "approved_at")
        expires_at = approved_at + timedelta(minutes=self.policy.approval_expiry_minutes)
        approval_id = "approval_" + _canonical_sha256(
            {
                "intent_sha256": intent.sha256,
                "approved_at": approved_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "mode": self.policy.mode,
            }
        )[:20]
        record = ApprovalRecord(
            approval_id=approval_id,
            intent_sha256=intent.sha256,
            approved_at=approved_at,
            expires_at=expires_at,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO mock_approvals(
                    approval_id, intent_sha256, approved_at, expires_at,
                    consumed_at, mode, real_trading_enabled
                ) VALUES (?, ?, ?, ?, NULL, 'mock_only', 0)
                """,
                (
                    record.approval_id,
                    record.intent_sha256,
                    record.approved_at.isoformat(),
                    record.expires_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM mock_approvals WHERE approval_id = ?",
                (record.approval_id,),
            ).fetchone()
            if row is None or self._record_from_row(row) != record:
                raise ValueError("approval ID collision or stored approval mismatch")
        return record

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=str(row["approval_id"]),
            intent_sha256=str(row["intent_sha256"]),
            approved_at=datetime.fromisoformat(str(row["approved_at"])),
            expires_at=datetime.fromisoformat(str(row["expires_at"])),
            mode=str(row["mode"]),
            real_trading_enabled=bool(row["real_trading_enabled"]),
        )

    def consume(
        self, intent: OrderIntent, approval: ApprovalRecord, *, now: datetime
    ) -> ApprovalRecord:
        _aware(now, "now")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.consume_in_transaction(
                connection, intent, approval, now=now
            )
            connection.commit()
            return approval
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def consume_in_transaction(
        self,
        connection: sqlite3.Connection,
        intent: OrderIntent,
        approval: ApprovalRecord,
        *,
        now: datetime,
    ) -> ApprovalRecord:
        """Consume inside a caller-owned transaction for atomic execution journaling."""

        _aware(now, "now")
        row = connection.execute(
            "SELECT * FROM mock_approvals WHERE approval_id = ?",
            (approval.approval_id,),
        ).fetchone()
        if row is None or self._record_from_row(row) != approval:
            raise ValueError("approval was not issued by this persistent gate")
        if row["consumed_at"] is not None:
            raise ValueError("approval already consumed")
        if not SHA256_RE.fullmatch(approval.intent_sha256) or (
            approval.intent_sha256 != intent.sha256
        ):
            raise ValueError("approval does not match exact order intent")
        if now < approval.approved_at:
            raise ValueError("approval cannot be consumed before approval time")
        if now > approval.expires_at:
            raise ValueError("approval expired")
        updated = connection.execute(
            """
            UPDATE mock_approvals
            SET consumed_at = ?
            WHERE approval_id = ? AND consumed_at IS NULL
            """,
            (now.isoformat(), approval.approval_id),
        ).rowcount
        if updated != 1:
            raise ValueError("approval already consumed")
        return approval
