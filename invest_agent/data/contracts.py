"""Provider-neutral contracts for reproducible fund data batches."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence

from invest_agent.domain.portfolio import QualityIssue


@dataclass(frozen=True)
class ProviderCapabilities:
    fund_nav_history: bool
    fund_master: bool = False
    fees_and_settlement: bool = False
    purchase_limits: bool = False
    local_api: bool = False
    commercial_entitlement_required: bool = False


class VisibilityStatus(str, Enum):
    """How confidently an observation can be placed on a historical timeline."""

    STRICT_POINT_IN_TIME = "strict_point_in_time"
    HISTORICAL_VISIBILITY_ASSUMED = "historical_visibility_assumed"


@dataclass(frozen=True)
class NavRequest:
    fund_codes: Sequence[str]
    start_date: date
    end_date: date
    as_of: datetime


@dataclass(frozen=True)
class FundMetadataRequest:
    fund_codes: Sequence[str]
    as_of: datetime


@dataclass(frozen=True)
class RawNavPayload:
    """Exact provider payload plus transport metadata, before normalization."""

    provider_id: str
    batch_id: str
    fetched_at: datetime
    payload: bytes
    content_type: str
    provenance: str
    source_domain: str
    request_parameters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RawFundMetadataPayload:
    """Exact fund-master payload plus transport metadata, before normalization."""

    provider_id: str
    batch_id: str
    fetched_at: datetime
    payload: bytes
    content_type: str
    provenance: str
    source_domain: str
    request_parameters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FundNavRecord:
    fund_code: str
    nav_date: date
    unit_nav: Decimal
    accumulated_nav: Decimal | None = None
    announcement_at: datetime | None = None
    source_observed_at: datetime | None = None
    first_seen_at: datetime | None = None
    visibility_status: VisibilityStatus = VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED


@dataclass(frozen=True)
class FundDistributionRecord:
    fund_code: str
    ex_date: date
    cash_per_share: Decimal
    source_text: str
    announcement_at: datetime | None = None
    source_observed_at: datetime | None = None
    first_seen_at: datetime | None = None
    visibility_status: VisibilityStatus = VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED


@dataclass(frozen=True)
class DataBatch:
    provider_id: str
    batch_id: str
    fetched_at: datetime
    as_of: datetime
    records: Sequence[FundNavRecord]
    provenance: str
    quality_issues: Sequence[QualityIssue] = field(default_factory=tuple)
    source_domain: str | None = None
    request_parameters: Mapping[str, str] = field(default_factory=dict)
    raw_content_sha256: str | None = None


@dataclass(frozen=True)
class FundDistributionBatch:
    provider_id: str
    batch_id: str
    source_nav_batch_id: str
    fetched_at: datetime
    as_of: datetime
    records: Sequence[FundDistributionRecord]
    provenance: str
    quality_issues: Sequence[QualityIssue] = field(default_factory=tuple)
    source_domain: str | None = None
    request_parameters: Mapping[str, str] = field(default_factory=dict)
    raw_content_sha256: str | None = None


@dataclass(frozen=True)
class FundMetadataRecord:
    fund_code: str
    fund_name: str | None = None
    fund_type: str | None = None
    fund_manager: str | None = None
    management_company: str | None = None
    custodian: str | None = None
    establishment_date: date | None = None
    raw_fields: Mapping[str, str] = field(default_factory=dict)
    source_observed_at: datetime | None = None


@dataclass(frozen=True)
class FundMetadataBatch:
    provider_id: str
    batch_id: str
    fetched_at: datetime
    as_of: datetime
    records: Sequence[FundMetadataRecord]
    provenance: str
    quality_issues: Sequence[QualityIssue] = field(default_factory=tuple)
    source_domain: str | None = None
    request_parameters: Mapping[str, str] = field(default_factory=dict)
    raw_content_sha256: str | None = None


class FundNavProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def fetch_nav(self, request: NavRequest) -> DataBatch:
        """Return only observations observable at request.as_of."""


class RawFundNavProvider(Protocol):
    """Two-phase provider that permits raw archival before parsing."""

    provider_id: str
    capabilities: ProviderCapabilities

    def fetch_raw(self, request: NavRequest) -> RawNavPayload:
        """Fetch bytes without normalizing them."""

    def normalize(
        self,
        raw: RawNavPayload,
        request: NavRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> DataBatch:
        """Normalize an already archived payload into provider-neutral records."""


class RawFundMetadataProvider(Protocol):
    """Two-phase provider for reproducible fund-master collection."""

    provider_id: str
    capabilities: ProviderCapabilities

    def fetch_raw(self, request: FundMetadataRequest) -> RawFundMetadataPayload:
        """Fetch bytes without normalizing them."""

    def normalize(
        self,
        raw: RawFundMetadataPayload,
        request: FundMetadataRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> FundMetadataBatch:
        """Normalize an already archived payload into fund-master records."""
