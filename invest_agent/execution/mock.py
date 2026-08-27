"""Network-free mock execution state machine for approval-gate verification."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from invest_agent.approval.contracts import ApprovalGate, ApprovalRecord, OrderIntent


class MockOutcome(str, Enum):
    SUBMITTED = "submitted"
    FAILED = "failed"
    UNKNOWN = "unknown"


class MockExecutionGateway:
    def __init__(self, approval_gate: ApprovalGate) -> None:
        self.approval_gate = approval_gate
        self._statuses: dict[str, MockOutcome] = {}

    def _result(
        self, intent: OrderIntent, *, status: MockOutcome, reconciled: bool
    ) -> dict[str, Any]:
        return {
            "intent_id": intent.intent_id,
            "intent_sha256": intent.sha256,
            "status": status.value,
            "reconciled": reconciled,
            "mode": "mock_only",
            "network_used": False,
            "external_side_effects": False,
            "automatic_retry_used": False,
            "real_trading_enabled": False,
        }

    def submit(
        self,
        intent: OrderIntent,
        approval: ApprovalRecord,
        *,
        now: datetime,
        outcome: MockOutcome,
    ) -> dict[str, Any]:
        if not isinstance(outcome, MockOutcome):
            raise ValueError("mock outcome is invalid")
        if self._statuses.get(intent.sha256) is MockOutcome.UNKNOWN:
            raise ValueError("unknown state requires reconciliation before retry")
        if self._statuses.get(intent.sha256) is MockOutcome.SUBMITTED:
            raise ValueError("submitted mock intent cannot be submitted again")
        self.approval_gate.consume(intent, approval, now=now)
        self._statuses[intent.sha256] = outcome
        return self._result(intent, status=outcome, reconciled=False)

    def reconcile(
        self, intent: OrderIntent, *, status: MockOutcome
    ) -> dict[str, Any]:
        if self._statuses.get(intent.sha256) is not MockOutcome.UNKNOWN:
            raise ValueError("only an unknown mock intent can be reconciled")
        if status not in (MockOutcome.SUBMITTED, MockOutcome.FAILED):
            raise ValueError("reconciliation must resolve to submitted or failed")
        self._statuses[intent.sha256] = status
        return self._result(intent, status=status, reconciled=True)
