"""Provider-neutral contracts for external market-context batches."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Sequence

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue


@dataclass(frozen=True)
class MarketNumericObservation:
    series_id: str
    observation_date: date
    value: Decimal
    unit: str
    frequency: str
    label: str
    published_date: date | None = None
    first_seen_at: datetime | None = None
    visibility_status: VisibilityStatus = VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexWeightObservation:
    index_code: str
    index_name: str
    weight_date: date
    stock_code: str
    stock_name: str
    weight_pct: Decimal
    is_estimated: bool
    first_seen_at: datetime
    industry: str | None = None
    contribution_pct_points: Decimal | None = None
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetCatalogObservation:
    dataset_name: str
    tool_name: str
    description: str
    last_updated: date


@dataclass(frozen=True)
class MarketDataBatch:
    provider_id: str
    batch_id: str
    tool_name: str
    fetched_at: datetime
    as_of: datetime
    request_arguments: Mapping[str, object]
    raw_content_sha256: str
    schema_sha256: str
    numeric_observations: Sequence[MarketNumericObservation] = field(default_factory=tuple)
    index_weights: Sequence[IndexWeightObservation] = field(default_factory=tuple)
    catalog_observations: Sequence[DatasetCatalogObservation] = field(default_factory=tuple)
    quality_issues: Sequence[QualityIssue] = field(default_factory=tuple)
