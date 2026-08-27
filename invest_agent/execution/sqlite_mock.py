"""Restart-safe, network-free mock execution journal."""

from __future__ import annotations

from datetime import datetime
import sqlite3
from typing import Any

from invest_agent.approval.contracts import ApprovalRecord, OrderIntent, _aware
from invest_agent.approval.sqlite_gate import SqliteApprovalGate

from .mock import MockOutcome


SCHEMA_VERSION = "phase6_mock_execution_sqlite_v1"


class SimulatedProcessCrash(RuntimeError):
    """Fault-injection signal used only by mock tests and rehearsals."""


class PersistentMockExecutionGateway:
    """Persist mock states without storing order, account, or credential plaintext."""

    def __init__(self, approval_gate: SqliteApprovalGate) -> None:
        self.approval_gate = approval_gate
        self.database_path = approval_gate.database_path
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
                CREATE TABLE IF NOT EXISTS mock_execution_schema_metadata (
                    schema_version TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS mock_executions (
                    intent_sha256 TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('submitting', 'submitted', 'failed', 'unknown')
                    ),
                    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reconciled_at TEXT,
                    mode TEXT NOT NULL CHECK (mode = 'mock_only'),
                    real_trading_enabled INTEGER NOT NULL CHECK (real_trading_enabled = 0)
                );
                CREATE TABLE IF NOT EXISTS mock_execution_events (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_sha256 TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('submitting', 'submitted', 'failed', 'unknown')
                    ),
                    event_at TEXT NOT NULL,
                    reconciled INTEGER NOT NULL CHECK (reconciled IN (0, 1)),
                    mode TEXT NOT NULL CHECK (mode = 'mock_only')
                );
                """
            )
            versions = connection.execute(
                "SELECT schema_version FROM mock_execution_schema_metadata"
            ).fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO mock_execution_schema_metadata(schema_version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif [row["schema_version"] for row in versions] != [SCHEMA_VERSION]:
                raise ValueError("unsupported mock execution database schema")

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        intent_sha256: str,
        approval_id: str,
        status: str,
        event_at: datetime,
        reconciled: bool,
    ) -> None:
        connection.execute(
            """
            INSERT INTO mock_execution_events(
                intent_sha256, approval_id, status, event_at, reconciled, mode
            ) VALUES (?, ?, ?, ?, ?, 'mock_only')
            """,
            (
                intent_sha256,
                approval_id,
                status,
                event_at.isoformat(),
                int(reconciled),
            ),
        )

    @staticmethod
    def _result(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "intent_id": str(row["intent_id"]),
            "intent_sha256": str(row["intent_sha256"]),
            "status": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "started_at": str(row["started_at"]),
            "updated_at": str(row["updated_at"]),
            "reconciled": row["reconciled_at"] is not None,
            "mode": "mock_only",
            "network_used": False,
            "external_side_effects": False,
            "automatic_retry_used": False,
            "real_trading_enabled": False,
        }

    def status(self, intent: OrderIntent) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
        return None if row is None else self._result(row)

    def begin_submission(
        self,
        intent: OrderIntent,
        approval: ApprovalRecord,
        *,
        now: datetime,
        fault_after_approval_consume: bool = False,
    ) -> dict[str, Any]:
        """Atomically consume approval and write SUBMITTING before any side effect."""

        _aware(now, "now")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            if existing is not None and existing["status"] in ("submitting", "unknown"):
                raise ValueError("incomplete mock submission requires reconciliation")
            if existing is not None and existing["status"] == "submitted":
                raise ValueError("submitted mock intent cannot be submitted again")
            self.approval_gate.consume_in_transaction(
                connection, intent, approval, now=now
            )
            if fault_after_approval_consume:
                raise SimulatedProcessCrash("simulated crash before atomic commit")
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO mock_executions(
                        intent_sha256, intent_id, approval_id, status, attempt_count,
                        started_at, updated_at, reconciled_at, mode,
                        real_trading_enabled
                    ) VALUES (?, ?, ?, 'submitting', 1, ?, ?, NULL, 'mock_only', 0)
                    """,
                    (
                        intent.sha256,
                        intent.intent_id,
                        approval.approval_id,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE mock_executions
                    SET approval_id = ?, status = 'submitting',
                        attempt_count = attempt_count + 1, started_at = ?,
                        updated_at = ?, reconciled_at = NULL
                    WHERE intent_sha256 = ? AND status = 'failed'
                    """,
                    (
                        approval.approval_id,
                        now.isoformat(),
                        now.isoformat(),
                        intent.sha256,
                    ),
                )
            self._append_event(
                connection,
                intent_sha256=intent.sha256,
                approval_id=approval.approval_id,
                status="submitting",
                event_at=now,
                reconciled=False,
            )
            row = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            connection.commit()
            return self._result(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_submission(
        self, intent: OrderIntent, *, now: datetime, outcome: MockOutcome
    ) -> dict[str, Any]:
        _aware(now, "now")
        if outcome not in (MockOutcome.SUBMITTED, MockOutcome.FAILED, MockOutcome.UNKNOWN):
            raise ValueError("mock outcome is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            if row is None or row["status"] != "submitting":
                raise ValueError("mock submission is not in submitting state")
            connection.execute(
                """
                UPDATE mock_executions SET status = ?, updated_at = ?
                WHERE intent_sha256 = ? AND status = 'submitting'
                """,
                (outcome.value, now.isoformat(), intent.sha256),
            )
            self._append_event(
                connection,
                intent_sha256=intent.sha256,
                approval_id=str(row["approval_id"]),
                status=outcome.value,
                event_at=now,
                reconciled=False,
            )
            result = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            connection.commit()
            return self._result(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def submit(
        self,
        intent: OrderIntent,
        approval: ApprovalRecord,
        *,
        now: datetime,
        outcome: MockOutcome,
        crash_after_begin: bool = False,
    ) -> dict[str, Any]:
        self.begin_submission(intent, approval, now=now)
        if crash_after_begin:
            raise SimulatedProcessCrash("simulated crash after submitting commit")
        return self.complete_submission(intent, now=now, outcome=outcome)

    def recover_incomplete(self, *, now: datetime) -> int:
        """Convert every restart-visible SUBMITTING record to UNKNOWN."""

        _aware(now, "now")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM mock_executions WHERE status = 'submitting'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE mock_executions SET status = 'unknown', updated_at = ?
                    WHERE intent_sha256 = ? AND status = 'submitting'
                    """,
                    (now.isoformat(), row["intent_sha256"]),
                )
                self._append_event(
                    connection,
                    intent_sha256=str(row["intent_sha256"]),
                    approval_id=str(row["approval_id"]),
                    status="unknown",
                    event_at=now,
                    reconciled=False,
                )
            connection.commit()
            return len(rows)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reconcile(
        self, intent: OrderIntent, *, now: datetime, status: MockOutcome
    ) -> dict[str, Any]:
        _aware(now, "now")
        if status not in (MockOutcome.SUBMITTED, MockOutcome.FAILED):
            raise ValueError("reconciliation must resolve to submitted or failed")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            if row is None or row["status"] != "unknown":
                raise ValueError("only an unknown mock intent can be reconciled")
            connection.execute(
                """
                UPDATE mock_executions
                SET status = ?, updated_at = ?, reconciled_at = ?
                WHERE intent_sha256 = ? AND status = 'unknown'
                """,
                (status.value, now.isoformat(), now.isoformat(), intent.sha256),
            )
            self._append_event(
                connection,
                intent_sha256=intent.sha256,
                approval_id=str(row["approval_id"]),
                status=status.value,
                event_at=now,
                reconciled=True,
            )
            result = connection.execute(
                "SELECT * FROM mock_executions WHERE intent_sha256 = ?",
                (intent.sha256,),
            ).fetchone()
            connection.commit()
            return self._result(result)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
