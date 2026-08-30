"""Fail-closed quality gates for market-context batches."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Sequence

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity, QualityStatus

from .contracts import MarketDataBatch


@dataclass(frozen=True)
class MarketBatchQualityReport:
    status: QualityStatus
    issues: Sequence[QualityIssue]

    @property
    def can_publish(self) -> bool:
        return self.status is not QualityStatus.FAIL


def evaluate_market_batch(batch: MarketDataBatch) -> MarketBatchQualityReport:
    issues = list(batch.quality_issues)
    if not batch.provider_id or not batch.batch_id or not batch.tool_name:
        issues.append(QualityIssue("missing_batch_identity", "Market batch identity is required", QualitySeverity.ERROR))
    if batch.fetched_at.tzinfo is None or batch.as_of.tzinfo is None:
        issues.append(QualityIssue("naive_market_timestamp", "Market batch timestamps require timezone", QualitySeverity.ERROR))
    if re.fullmatch(r"[0-9a-f]{64}", batch.raw_content_sha256) is None:
        issues.append(QualityIssue("invalid_market_raw_hash", "raw_content_sha256 must be lowercase SHA-256", QualitySeverity.ERROR))
    if re.fullmatch(r"[0-9a-f]{64}", batch.schema_sha256) is None:
        issues.append(QualityIssue("invalid_market_schema_hash", "schema_sha256 must be lowercase SHA-256", QualitySeverity.ERROR))
    if not (batch.numeric_observations or batch.index_weights or batch.catalog_observations):
        issues.append(QualityIssue("empty_market_batch", "No publishable market observations were normalized", QualitySeverity.ERROR))

    seen_numeric: set[tuple[str, object]] = set()
    assumed = 0
    for record in batch.numeric_observations:
        key = (record.series_id, record.observation_date)
        if key in seen_numeric:
            issues.append(QualityIssue("duplicate_market_observation", f"Duplicate market observation: {key}", QualitySeverity.ERROR))
        seen_numeric.add(key)
        if not record.series_id or not record.unit or not record.frequency or not record.label:
            issues.append(QualityIssue("incomplete_market_observation", f"Incomplete market observation: {key}", QualitySeverity.ERROR))
        if not record.value.is_finite():
            issues.append(QualityIssue("nonfinite_market_value", f"Non-finite market value: {key}", QualitySeverity.ERROR))
        if record.observation_date > batch.as_of.date():
            issues.append(QualityIssue("future_market_observation", f"Market observation is after as_of: {key}", QualitySeverity.ERROR))
        if record.published_date is not None and record.published_date > batch.as_of.date():
            issues.append(QualityIssue("future_market_publication", f"Market publication is after as_of: {key}", QualitySeverity.ERROR))
        if record.visibility_status is VisibilityStatus.STRICT_POINT_IN_TIME and record.first_seen_at is None:
            issues.append(QualityIssue("strict_market_value_missing_first_seen", f"Strict market observation lacks first_seen_at: {key}", QualitySeverity.ERROR))
        if record.visibility_status is VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED:
            assumed += 1

    seen_weights: set[tuple[str, object, str]] = set()
    for record in batch.index_weights:
        key = (record.index_code, record.weight_date, record.stock_code)
        if key in seen_weights:
            issues.append(QualityIssue("duplicate_index_weight", f"Duplicate index weight: {key}", QualitySeverity.ERROR))
        seen_weights.add(key)
        if record.weight_date > batch.as_of.date():
            issues.append(QualityIssue("future_index_weight", f"Index weight is after as_of: {key}", QualitySeverity.ERROR))
        if record.weight_pct < Decimal("0") or record.weight_pct > Decimal("100"):
            issues.append(QualityIssue("invalid_index_weight", f"Index weight is outside 0-100: {key}", QualitySeverity.ERROR))

    for record in batch.catalog_observations:
        if record.last_updated > batch.as_of.date():
            issues.append(QualityIssue("future_dataset_update", f"Dataset update is after as_of: {record.dataset_name}", QualitySeverity.ERROR))

    if assumed:
        issues.append(
            QualityIssue(
                "historical_visibility_assumed",
                f"{assumed} historical market observations lack original publication timestamps",
                QualitySeverity.WARNING,
            )
        )
    status = QualityStatus.FAIL if any(i.severity is QualitySeverity.ERROR for i in issues) else (QualityStatus.PARTIAL if issues else QualityStatus.PASS)
    return MarketBatchQualityReport(status=status, issues=tuple(issues))
