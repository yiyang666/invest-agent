"""Deterministic, one-time approval contracts for exact mock order intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping


FUND_CODE_RE = re.compile(r"^\d{6}$")
SAFE_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ORDER_FIELDS = {
    "fund_code",
    "fund_name",
    "action",
    "amount_cny",
    "shares",
    "order_reference",
    "scheduled_date",
    "source_evidence_sha256",
    "route_rule_version",
    "account_alias",
    "estimated_fee_cny",
    "expected_result",
}


class OrderAction(str, Enum):
    PURCHASE = "purchase"
    REDEEM = "redeem"
    REVOKE = "revoke"


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ApprovalPolicy:
    mode: str
    approval_expiry_minutes: int
    one_time_approval: bool
    key_change_invalidates_approval: bool
    unknown_state_requires_reconciliation: bool
    real_adapter_allowed: bool
    network_allowed: bool
    automatic_retry_allowed: bool
    real_trading_enabled: bool

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ApprovalPolicy":
        if payload.get("schema_version") != 1:
            raise ValueError("approval policy schema_version must be 1")
        if set(payload.get("required_order_fields", [])) != REQUIRED_ORDER_FIELDS:
            raise ValueError("approval policy required order fields are not exact")
        safety = payload.get("safety")
        if not isinstance(safety, Mapping):
            raise ValueError("approval policy requires safety settings")
        policy = cls(
            mode=str(payload.get("mode")),
            approval_expiry_minutes=int(payload.get("approval_expiry_minutes", 0)),
            one_time_approval=payload.get("one_time_approval") is True,
            key_change_invalidates_approval=(
                payload.get("key_change_invalidates_approval") is True
            ),
            unknown_state_requires_reconciliation=(
                payload.get("unknown_state_requires_reconciliation") is True
            ),
            real_adapter_allowed=safety.get("real_adapter_allowed") is True,
            network_allowed=safety.get("network_allowed") is True,
            automatic_retry_allowed=safety.get("automatic_retry_allowed") is True,
            real_trading_enabled=safety.get("real_trading_enabled") is True,
        )
        if safety.get("advisory_only") is not True:
            raise ValueError("approval policy must remain advisory_only")
        if policy.mode != "mock_only":
            raise ValueError("Phase 6.1 approval policy must remain mock_only")
        if policy.approval_expiry_minutes != 15:
            raise ValueError("approval expiry must remain exactly 15 minutes")
        if not all(
            (
                policy.one_time_approval,
                policy.key_change_invalidates_approval,
                policy.unknown_state_requires_reconciliation,
            )
        ):
            raise ValueError("approval fail-closed invariants are incomplete")
        if any(
            (
                policy.real_adapter_allowed,
                policy.network_allowed,
                policy.automatic_retry_allowed,
                policy.real_trading_enabled,
            )
        ):
            raise ValueError("mock approval policy cannot enable execution capabilities")
        return policy


@dataclass(frozen=True)
class OrderIntent:
    fund_code: str
    fund_name: str
    action: OrderAction
    amount_cny: Decimal | None
    shares: Decimal | None
    order_reference: str | None
    scheduled_date: date | None
    source_evidence_sha256: str
    route_rule_version: str
    account_alias: str
    estimated_fee_cny: Decimal
    expected_result: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OrderIntent":
        try:
            action = OrderAction(str(payload["action"]))
            amount_value = payload.get("amount_cny")
            shares_value = payload.get("shares")
            scheduled_value = payload.get("scheduled_date")
            return cls(
                fund_code=str(payload["fund_code"]),
                fund_name=str(payload["fund_name"]),
                action=action,
                amount_cny=(
                    None if amount_value is None else Decimal(str(amount_value))
                ),
                shares=None if shares_value is None else Decimal(str(shares_value)),
                order_reference=(
                    None
                    if payload.get("order_reference") is None
                    else str(payload["order_reference"])
                ),
                scheduled_date=(
                    None
                    if scheduled_value is None
                    else date.fromisoformat(str(scheduled_value))
                ),
                source_evidence_sha256=str(payload["source_evidence_sha256"]),
                route_rule_version=str(payload["route_rule_version"]),
                account_alias=str(payload["account_alias"]),
                estimated_fee_cny=Decimal(str(payload["estimated_fee_cny"])),
                expected_result=str(payload["expected_result"]),
            )
        except KeyError as exc:
            raise ValueError(f"order intent is missing required field: {exc.args[0]}") from exc
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("order intent contains an invalid numeric value") from exc

    def __post_init__(self) -> None:
        if not FUND_CODE_RE.fullmatch(self.fund_code):
            raise ValueError("fund code must contain six digits")
        if not self.fund_name.strip():
            raise ValueError("fund name is required")
        if not isinstance(self.action, OrderAction):
            raise ValueError("order action must be an OrderAction")
        if not SAFE_ALIAS_RE.fullmatch(self.account_alias) or re.search(
            r"\d{6,}", self.account_alias
        ):
            raise ValueError("account alias must be non-sensitive and contain no account number")
        if not self.expected_result.strip():
            raise ValueError("expected result is required")
        if not SHA256_RE.fullmatch(self.source_evidence_sha256):
            raise ValueError("source evidence must be a lowercase SHA-256 digest")
        if not self.route_rule_version.strip():
            raise ValueError("route rule version is required")
        if self.estimated_fee_cny < 0:
            raise ValueError("estimated fee cannot be negative")
        if self.action is OrderAction.PURCHASE:
            if self.amount_cny is None or self.amount_cny <= 0 or self.shares is not None:
                raise ValueError("purchase requires positive amount and no shares")
            if self.order_reference is not None:
                raise ValueError("purchase cannot contain an order reference")
            if self.scheduled_date is None:
                raise ValueError("purchase requires a scheduled date")
        elif self.action is OrderAction.REDEEM:
            if self.shares is None or self.shares <= 0 or self.amount_cny is not None:
                raise ValueError("redeem requires positive shares and no amount")
            if self.order_reference is not None:
                raise ValueError("redeem cannot contain an order reference")
        elif self.action is OrderAction.REVOKE:
            if self.amount_cny is not None or self.shares is not None:
                raise ValueError("revoke cannot contain amount or shares")
            if not self.order_reference or not self.order_reference.strip():
                raise ValueError("revoke requires an exact order reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "action": self.action.value,
            "amount_cny": _money(self.amount_cny) if self.amount_cny is not None else None,
            "shares": str(self.shares.normalize()) if self.shares is not None else None,
            "order_reference": self.order_reference,
            "scheduled_date": (
                self.scheduled_date.isoformat() if self.scheduled_date else None
            ),
            "source_evidence_sha256": self.source_evidence_sha256,
            "route_rule_version": self.route_rule_version,
            "account_alias": self.account_alias,
            "estimated_fee_cny": _money(self.estimated_fee_cny),
            "expected_result": self.expected_result,
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())

    @property
    def intent_id(self) -> str:
        return "order_intent_" + self.sha256[:20]


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    intent_sha256: str
    approved_at: datetime
    expires_at: datetime
    mode: str = "mock_only"
    real_trading_enabled: bool = False


class ApprovalGate:
    """Issue and consume explicit mock approvals without any execution adapter."""

    def __init__(self, policy: ApprovalPolicy) -> None:
        self.policy = policy
        self._issued: dict[str, ApprovalRecord] = {}
        self._consumed: set[str] = set()

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
        self._issued[approval_id] = record
        return record

    def consume(
        self, intent: OrderIntent, approval: ApprovalRecord, *, now: datetime
    ) -> ApprovalRecord:
        _aware(now, "now")
        if self._issued.get(approval.approval_id) != approval:
            raise ValueError("approval was not issued by this gate")
        if approval.approval_id in self._consumed:
            raise ValueError("approval already consumed")
        if not SHA256_RE.fullmatch(approval.intent_sha256) or (
            approval.intent_sha256 != intent.sha256
        ):
            raise ValueError("approval does not match exact order intent")
        if now < approval.approved_at:
            raise ValueError("approval cannot be consumed before approval time")
        if now > approval.expires_at:
            raise ValueError("approval expired")
        self._consumed.add(approval.approval_id)
        return approval
