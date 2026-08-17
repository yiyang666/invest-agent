"""Deterministic quality gates for fund-data batches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Sequence

from invest_agent.data.contracts import (
    DataBatch,
    FundDistributionBatch,
    FundMetadataBatch,
    FundDistributionRecord,
    FundNavRecord,
    VisibilityStatus,
)
from invest_agent.domain.portfolio import (
    FUND_CODE_PATTERN,
    QualityIssue,
    QualitySeverity,
    QualityStatus,
)


@dataclass(frozen=True)
class BatchQualityReport:
    status: QualityStatus
    issues: Sequence[QualityIssue]

    @property
    def can_publish(self) -> bool:
        return self.status is not QualityStatus.FAIL


def _status_for(issues: Sequence[QualityIssue]) -> QualityStatus:
    if any(issue.severity is QualitySeverity.ERROR for issue in issues):
        return QualityStatus.FAIL
    if issues:
        return QualityStatus.PARTIAL
    return QualityStatus.PASS


def _batch_envelope_issues(
    batch: DataBatch | FundMetadataBatch | FundDistributionBatch,
) -> list[QualityIssue]:
    issues = list(batch.quality_issues)
    if not batch.provider_id.strip():
        issues.append(QualityIssue("missing_provider", "provider_id is required", QualitySeverity.ERROR))
    if not batch.batch_id.strip():
        issues.append(QualityIssue("missing_batch_id", "batch_id is required", QualitySeverity.ERROR))
    if not batch.provenance.strip():
        issues.append(QualityIssue("missing_provenance", "provenance is required", QualitySeverity.ERROR))
    if not (batch.source_domain or "").strip():
        issues.append(
            QualityIssue("missing_source_domain", "source_domain is required", QualitySeverity.ERROR)
        )
    if not batch.request_parameters:
        issues.append(
            QualityIssue(
                "missing_request_parameters",
                "request_parameters are required",
                QualitySeverity.ERROR,
            )
        )
    if not batch.raw_content_sha256:
        issues.append(
            QualityIssue(
                "missing_raw_content_sha256",
                "raw_content_sha256 is required before publication",
                QualitySeverity.ERROR,
            )
        )
    elif re.fullmatch(r"[0-9a-f]{64}", batch.raw_content_sha256) is None:
        issues.append(
            QualityIssue(
                "invalid_raw_content_sha256",
                "raw_content_sha256 must be 64 lowercase hexadecimal characters",
                QualitySeverity.ERROR,
            )
        )
    if not _is_timezone_aware(batch.fetched_at):
        issues.append(QualityIssue("naive_fetched_at", "fetched_at must include a timezone", QualitySeverity.ERROR))
    if not _is_timezone_aware(batch.as_of):
        issues.append(QualityIssue("naive_as_of", "as_of must include a timezone", QualitySeverity.ERROR))
    return issues


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def evaluate_nav_batch(
    batch: DataBatch,
    *,
    suspicious_daily_move: Decimal = Decimal("0.50"),
    long_gap_days: int = 14,
) -> BatchQualityReport:
    """Return a report without mutating or silently repairing the batch."""

    issues = _batch_envelope_issues(batch)
    if not batch.records:
        issues.append(QualityIssue("empty_nav_batch", "No NAV records passed provider filtering", QualitySeverity.ERROR))

    seen: set[tuple[str, object]] = set()
    by_fund: dict[str, list[FundNavRecord]] = {}
    last_input_date: dict[str, date] = {}
    assumed_visibility_count = 0
    for record in batch.records:
        if not FUND_CODE_PATTERN.fullmatch(record.fund_code):
            issues.append(
                QualityIssue(
                    "invalid_fund_code",
                    f"Fund code must contain six digits: {record.fund_code!r}",
                    QualitySeverity.ERROR,
                )
            )
        if record.unit_nav <= 0:
            issues.append(
                QualityIssue(
                    "nonpositive_unit_nav",
                    f"Unit NAV must be positive for {record.fund_code} on {record.nav_date}",
                    QualitySeverity.ERROR,
                )
            )
        if record.accumulated_nav is not None and record.accumulated_nav <= 0:
            issues.append(
                QualityIssue(
                    "nonpositive_accumulated_nav",
                    f"Accumulated NAV must be positive for {record.fund_code} on {record.nav_date}",
                    QualitySeverity.ERROR,
                )
            )
        if _is_timezone_aware(batch.as_of) and record.nav_date > batch.as_of.date():
            issues.append(
                QualityIssue(
                    "future_nav_observation",
                    f"NAV is after batch as_of for {record.fund_code}: {record.nav_date}",
                    QualitySeverity.ERROR,
                )
            )

        key = (record.fund_code, record.nav_date)
        if key in seen:
            issues.append(
                QualityIssue(
                    "duplicate_nav_observation",
                    f"Duplicate NAV for {record.fund_code} on {record.nav_date}",
                    QualitySeverity.ERROR,
                )
            )
        seen.add(key)

        previous_input_date = last_input_date.get(record.fund_code)
        if previous_input_date is not None and record.nav_date < previous_input_date:
            issues.append(
                QualityIssue(
                    "out_of_order_nav_observation",
                    f"NAV dates are out of order for {record.fund_code}: {record.nav_date}",
                    QualitySeverity.ERROR,
                )
            )
        last_input_date[record.fund_code] = record.nav_date

        for field_name, timestamp in (
            ("announcement_at", record.announcement_at),
            ("source_observed_at", record.source_observed_at),
            ("first_seen_at", record.first_seen_at),
        ):
            if timestamp is not None and not _is_timezone_aware(timestamp):
                issues.append(
                    QualityIssue(
                        f"naive_{field_name}",
                        f"{field_name} must include a timezone for {record.fund_code} on {record.nav_date}",
                        QualitySeverity.ERROR,
                    )
                )

        if record.visibility_status is VisibilityStatus.STRICT_POINT_IN_TIME:
            visible_at = record.announcement_at or record.first_seen_at
            if visible_at is None:
                issues.append(
                    QualityIssue(
                        "strict_visibility_without_timestamp",
                        f"Strict visibility requires announcement_at or first_seen_at for {record.fund_code}",
                        QualitySeverity.ERROR,
                    )
                )
            elif _is_timezone_aware(batch.as_of) and _is_timezone_aware(visible_at) and visible_at > batch.as_of:
                issues.append(
                    QualityIssue(
                        "future_visible_observation",
                        f"Observation was not visible by as_of for {record.fund_code} on {record.nav_date}",
                        QualitySeverity.ERROR,
                    )
                )
        else:
            assumed_visibility_count += 1

        by_fund.setdefault(record.fund_code, []).append(record)

    if assumed_visibility_count:
        issues.append(
            QualityIssue(
                "historical_visibility_assumed",
                f"{assumed_visibility_count} NAV observations lack strict historical visibility evidence",
                QualitySeverity.WARNING,
            )
        )

    for fund_code, records in by_fund.items():
        ordered = sorted(records, key=lambda item: item.nav_date)
        for previous, current in zip(ordered, ordered[1:]):
            gap = (current.nav_date - previous.nav_date).days
            if gap > long_gap_days:
                issues.append(
                    QualityIssue(
                        "long_nav_gap",
                        f"{fund_code} has a {gap}-day NAV gap ending {current.nav_date}",
                        QualitySeverity.WARNING,
                    )
                )
            move = abs(current.unit_nav / previous.unit_nav - Decimal("1")) if previous.unit_nav > 0 else Decimal("0")
            if move > suspicious_daily_move:
                issues.append(
                    QualityIssue(
                        "suspicious_nav_jump",
                        f"{fund_code} unit NAV moved {move:.2%} on {current.nav_date}; check split/dividend data",
                        QualitySeverity.WARNING,
                    )
                )

    return BatchQualityReport(status=_status_for(issues), issues=tuple(issues))


def evaluate_fund_metadata_batch(batch: FundMetadataBatch) -> BatchQualityReport:
    """Validate fund-master observations without inventing missing fields."""

    issues = _batch_envelope_issues(batch)
    if not batch.records:
        issues.append(
            QualityIssue("empty_fund_metadata_batch", "No fund metadata records were parsed", QualitySeverity.ERROR)
        )

    seen_codes: set[str] = set()
    for record in batch.records:
        if not FUND_CODE_PATTERN.fullmatch(record.fund_code):
            issues.append(
                QualityIssue(
                    "invalid_fund_code",
                    f"Fund code must contain six digits: {record.fund_code!r}",
                    QualitySeverity.ERROR,
                )
            )
        if record.fund_code in seen_codes:
            issues.append(
                QualityIssue(
                    "duplicate_fund_metadata",
                    f"Duplicate metadata record for {record.fund_code}",
                    QualitySeverity.ERROR,
                )
            )
        seen_codes.add(record.fund_code)
        if not (record.fund_name or "").strip():
            issues.append(
                QualityIssue(
                    "missing_fund_name",
                    f"Fund name is missing for {record.fund_code}",
                    QualitySeverity.WARNING,
                )
            )
        if not record.raw_fields:
            issues.append(
                QualityIssue(
                    "empty_fund_metadata_fields",
                    f"No source fields were retained for {record.fund_code}",
                    QualitySeverity.ERROR,
                )
            )
        if record.source_observed_at is None or not _is_timezone_aware(record.source_observed_at):
            issues.append(
                QualityIssue(
                    "invalid_source_observed_at",
                    f"source_observed_at must include a timezone for {record.fund_code}",
                    QualitySeverity.ERROR,
                )
            )

    return BatchQualityReport(status=_status_for(issues), issues=tuple(issues))


def evaluate_distribution_batch(batch: FundDistributionBatch) -> BatchQualityReport:
    """Validate explicit cash distributions extracted from an archived source batch."""

    issues = _batch_envelope_issues(batch)
    if not batch.source_nav_batch_id.strip():
        issues.append(
            QualityIssue(
                "missing_source_nav_batch",
                "source_nav_batch_id is required",
                QualitySeverity.ERROR,
            )
        )

    seen: set[tuple[str, date]] = set()
    assumed_visibility_count = 0
    for record in batch.records:
        if not FUND_CODE_PATTERN.fullmatch(record.fund_code):
            issues.append(
                QualityIssue(
                    "invalid_fund_code",
                    f"Fund code must contain six digits: {record.fund_code!r}",
                    QualitySeverity.ERROR,
                )
            )
        if record.cash_per_share <= 0 or not record.cash_per_share.is_finite():
            issues.append(
                QualityIssue(
                    "invalid_cash_distribution",
                    f"Cash per share must be positive for {record.fund_code} on {record.ex_date}",
                    QualitySeverity.ERROR,
                )
            )
        if record.ex_date > batch.as_of.date():
            issues.append(
                QualityIssue(
                    "future_distribution",
                    f"Distribution is after batch as_of for {record.fund_code}: {record.ex_date}",
                    QualitySeverity.ERROR,
                )
            )
        key = (record.fund_code, record.ex_date)
        if key in seen:
            issues.append(
                QualityIssue(
                    "duplicate_distribution",
                    f"Duplicate distribution for {record.fund_code} on {record.ex_date}",
                    QualitySeverity.ERROR,
                )
            )
        seen.add(key)
        if not record.source_text.strip():
            issues.append(
                QualityIssue(
                    "missing_distribution_source_text",
                    f"Distribution source text is missing for {record.fund_code}",
                    QualitySeverity.ERROR,
                )
            )
        for field_name, timestamp in (
            ("announcement_at", record.announcement_at),
            ("source_observed_at", record.source_observed_at),
            ("first_seen_at", record.first_seen_at),
        ):
            if timestamp is not None and not _is_timezone_aware(timestamp):
                issues.append(
                    QualityIssue(
                        f"naive_{field_name}",
                        f"{field_name} must include a timezone for {record.fund_code}",
                        QualitySeverity.ERROR,
                    )
                )
        if record.visibility_status is VisibilityStatus.STRICT_POINT_IN_TIME:
            visible_at = record.announcement_at or record.first_seen_at
            if visible_at is None:
                issues.append(
                    QualityIssue(
                        "strict_visibility_without_timestamp",
                        f"Strict distribution visibility requires a timestamp for {record.fund_code}",
                        QualitySeverity.ERROR,
                    )
                )
            elif visible_at > batch.as_of:
                issues.append(
                    QualityIssue(
                        "future_visible_distribution",
                        f"Distribution was not visible by as_of for {record.fund_code}",
                        QualitySeverity.ERROR,
                    )
                )
        else:
            assumed_visibility_count += 1

    if assumed_visibility_count:
        issues.append(
            QualityIssue(
                "historical_visibility_assumed",
                f"{assumed_visibility_count} distributions lack strict historical visibility evidence",
                QualitySeverity.WARNING,
            )
        )
    return BatchQualityReport(status=_status_for(issues), issues=tuple(issues))
