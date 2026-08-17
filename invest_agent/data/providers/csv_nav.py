"""Deterministic adapter for official NAV exports in CSV format."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path
from uuid import uuid4

from invest_agent.data.contracts import (
    DataBatch,
    FundNavRecord,
    NavRequest,
    ProviderCapabilities,
    RawNavPayload,
    VisibilityStatus,
)
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity


class LocalCsvNavProvider:
    """Load NAV data exported from an authorized source; never scrapes pages."""

    provider_id = "local_csv_nav"
    capabilities = ProviderCapabilities(fund_nav_history=True, local_api=True)
    required_columns = {"fund_code", "nav_date", "unit_nav"}

    def __init__(self, path: str | Path, source_label: str = "authorized_export") -> None:
        self.path = Path(path)
        self.source_label = source_label

    def fetch_raw(self, request: NavRequest) -> RawNavPayload:
        now = datetime.now(timezone.utc)
        return RawNavPayload(
            provider_id=self.provider_id,
            batch_id=f"csv-{uuid4().hex}",
            fetched_at=now,
            payload=self.path.read_bytes(),
            content_type="text/csv",
            provenance=f"{self.source_label}:{self.path.name}",
            source_domain="local_file",
            request_parameters={
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
                "fund_codes": ",".join(sorted(set(request.fund_codes))),
            },
        )

    def normalize(
        self,
        raw: RawNavPayload,
        request: NavRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> DataBatch:
        if raw.provider_id != self.provider_id:
            raise ValueError(f"Unexpected raw provider: {raw.provider_id}")

        issues: list[QualityIssue] = []
        records: list[FundNavRecord] = []
        seen: set[tuple[str, date]] = set()
        requested_codes = set(request.fund_codes)

        try:
            text = raw.payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            issues.append(
                QualityIssue(
                    "invalid_csv_encoding",
                    f"CSV payload is not UTF-8: {exc}",
                    QualitySeverity.ERROR,
                )
            )
            text = ""

        with StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or ())
            missing = self.required_columns - columns
            if missing:
                issues.append(
                    QualityIssue(
                        "missing_csv_columns",
                        f"CSV is missing required columns: {sorted(missing)}",
                        QualitySeverity.ERROR,
                    )
                )

            for line_number, row in enumerate(reader, start=2) if not missing else ():
                try:
                    fund_code = row["fund_code"].strip()
                    nav_date = date.fromisoformat(row["nav_date"].strip())
                    unit_nav = Decimal(row["unit_nav"].strip())
                    accumulated_raw = (row.get("accumulated_nav") or "").strip()
                    accumulated_nav = Decimal(accumulated_raw) if accumulated_raw else None
                    announcement_raw = (row.get("announcement_at") or "").strip()
                    announcement_at = (
                        self._parse_timestamp(announcement_raw, request.as_of)
                        if announcement_raw
                        else None
                    )
                except (KeyError, ValueError, InvalidOperation) as exc:
                    issues.append(
                        QualityIssue(
                            "invalid_nav_row",
                            f"Line {line_number} cannot be parsed: {exc}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue

                if fund_code not in requested_codes:
                    continue
                if nav_date > request.as_of.date():
                    issues.append(
                        QualityIssue(
                            "future_nav_observation",
                            f"Ignored {fund_code} NAV dated after as_of: {nav_date}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                if not request.start_date <= nav_date <= request.end_date:
                    continue
                if unit_nav <= 0:
                    issues.append(
                        QualityIssue(
                            "nonpositive_unit_nav",
                            f"Ignored nonpositive NAV for {fund_code} on {nav_date}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                key = (fund_code, nav_date)
                if key in seen:
                    issues.append(
                        QualityIssue(
                            "duplicate_nav_observation",
                            f"Ignored duplicate NAV for {fund_code} on {nav_date}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                seen.add(key)
                records.append(
                    FundNavRecord(
                        fund_code=fund_code,
                        nav_date=nav_date,
                        unit_nav=unit_nav,
                        accumulated_nav=accumulated_nav,
                        announcement_at=announcement_at,
                        source_observed_at=raw.fetched_at,
                        first_seen_at=raw.fetched_at,
                        visibility_status=(
                            VisibilityStatus.STRICT_POINT_IN_TIME
                            if announcement_at
                            else VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED
                        ),
                    )
                )

        records.sort(key=lambda record: (record.fund_code, record.nav_date))
        return DataBatch(
            provider_id=self.provider_id,
            batch_id=raw.batch_id,
            fetched_at=raw.fetched_at,
            as_of=request.as_of,
            records=tuple(records),
            provenance=raw.provenance,
            quality_issues=tuple(issues),
            source_domain=raw.source_domain,
            request_parameters=raw.request_parameters,
            raw_content_sha256=raw_content_sha256,
        )

    def fetch_nav(self, request: NavRequest) -> DataBatch:
        """Compatibility helper; production CLI archives ``fetch_raw`` first."""

        raw = self.fetch_raw(request)
        return self.normalize(raw, request)

    @staticmethod
    def _parse_timestamp(value: str, as_of: datetime) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None and as_of.tzinfo is not None:
            parsed = parsed.replace(tzinfo=as_of.tzinfo)
        return parsed
