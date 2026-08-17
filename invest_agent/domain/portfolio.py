"""Redacted, deterministic portfolio snapshot models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Mapping, Sequence


FUND_CODE_PATTERN = re.compile(r"^\d{6}$")
_FORBIDDEN_PUBLIC_KEYS = {
    "accountid",
    "accountnumber",
    "bankaccount",
    "bankcard",
    "customerid",
    "custid",
    "deviceid",
    "idcard",
    "inittoken",
    "password",
    "phone",
    "secret",
    "token",
}


class QualitySeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class QualityStatus(str, Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: QualitySeverity

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
        }


@dataclass(frozen=True)
class PositionSnapshot:
    fund_code: str
    shares: Decimal
    market_value: Decimal
    fund_name: str | None = None
    nav_date: date | None = None

    def validation_issues(self) -> tuple[QualityIssue, ...]:
        issues: list[QualityIssue] = []
        if not FUND_CODE_PATTERN.fullmatch(self.fund_code):
            issues.append(
                QualityIssue(
                    "invalid_fund_code",
                    f"Fund code must contain six digits: {self.fund_code!r}",
                    QualitySeverity.ERROR,
                )
            )
        if self.shares < 0:
            issues.append(
                QualityIssue(
                    "negative_shares",
                    f"Shares cannot be negative for {self.fund_code}",
                    QualitySeverity.ERROR,
                )
            )
        if self.market_value < 0:
            issues.append(
                QualityIssue(
                    "negative_market_value",
                    f"Market value cannot be negative for {self.fund_code}",
                    QualitySeverity.ERROR,
                )
            )
        return tuple(issues)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """A public-safe snapshot with no account or customer identifiers."""

    as_of: datetime
    source: str
    batch_id: str
    cash: Decimal
    positions: Sequence[PositionSnapshot]
    source_issues: Sequence[QualityIssue] = field(default_factory=tuple)

    @property
    def total_fund_value(self) -> Decimal:
        return sum((position.market_value for position in self.positions), Decimal("0"))

    @property
    def total_assets(self) -> Decimal:
        return self.cash + self.total_fund_value

    def validation_issues(self) -> tuple[QualityIssue, ...]:
        issues = list(self.source_issues)
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            issues.append(
                QualityIssue(
                    "naive_as_of",
                    "Snapshot timestamp must include a timezone",
                    QualitySeverity.ERROR,
                )
            )
        if not self.source.strip():
            issues.append(
                QualityIssue("missing_source", "Snapshot source is required", QualitySeverity.ERROR)
            )
        if not self.batch_id.strip():
            issues.append(
                QualityIssue("missing_batch_id", "Snapshot batch_id is required", QualitySeverity.ERROR)
            )
        if self.cash < 0:
            issues.append(
                QualityIssue("negative_cash", "Cash cannot be negative", QualitySeverity.ERROR)
            )

        seen_codes: set[str] = set()
        for position in self.positions:
            issues.extend(position.validation_issues())
            if position.fund_code in seen_codes:
                issues.append(
                    QualityIssue(
                        "duplicate_fund_code",
                        f"Duplicate fund code: {position.fund_code}",
                        QualitySeverity.ERROR,
                    )
                )
            seen_codes.add(position.fund_code)

        if not self.positions:
            issues.append(
                QualityIssue(
                    "empty_positions",
                    "No fund positions were returned",
                    QualitySeverity.WARNING,
                )
            )
        return tuple(issues)

    @property
    def quality_status(self) -> QualityStatus:
        issues = self.validation_issues()
        if any(issue.severity is QualitySeverity.ERROR for issue in issues):
            return QualityStatus.FAIL
        if issues:
            return QualityStatus.PARTIAL
        return QualityStatus.PASS

    def weights(self) -> dict[str, Decimal]:
        if self.total_assets <= 0:
            return {position.fund_code: Decimal("0") for position in self.positions}
        return {
            position.fund_code: position.market_value / self.total_assets
            for position in self.positions
        }

    @property
    def cash_weight(self) -> Decimal:
        if self.total_assets <= 0:
            return Decimal("0")
        return self.cash / self.total_assets

    def to_public_dict(self) -> dict[str, Any]:
        weights = self.weights()
        payload: dict[str, Any] = {
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "batch_id": self.batch_id,
            "quality_status": self.quality_status.value,
            "cash": str(self.cash),
            "cash_weight": str(self.cash_weight),
            "total_fund_value": str(self.total_fund_value),
            "total_assets": str(self.total_assets),
            "positions": [
                {
                    "fund_code": position.fund_code,
                    "fund_name": position.fund_name,
                    "shares": str(position.shares),
                    "market_value": str(position.market_value),
                    "weight": str(weights[position.fund_code]),
                    "nav_date": position.nav_date.isoformat() if position.nav_date else None,
                }
                for position in self.positions
            ],
            "quality_issues": [issue.to_dict() for issue in self.validation_issues()],
        }
        ensure_public_payload(payload)
        return payload


def ensure_public_payload(value: Any) -> None:
    """Reject common credential and private-identifier keys recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in _FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"Sensitive key is not allowed in a public snapshot: {key}")
            ensure_public_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            ensure_public_payload(child)
