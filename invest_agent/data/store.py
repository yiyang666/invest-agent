"""SQLite-backed immutable store for validated fund-data batches."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Sequence

from invest_agent.data.contracts import (
    DataBatch,
    FundDistributionBatch,
    FundDistributionRecord,
    FundMetadataBatch,
    FundMetadataRecord,
    FundNavRecord,
)
from invest_agent.data.quality import (
    BatchQualityReport,
    evaluate_distribution_batch,
    evaluate_fund_metadata_batch,
    evaluate_nav_batch,
)


SCHEMA_VERSION = 4


class ImmutableBatchConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    batch_id: str
    content_sha256: str
    quality_report: BatchQualityReport
    published_records: int
    idempotent_replay: bool = False


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _record_payload(record: FundNavRecord) -> dict[str, Any]:
    return {
        "fund_code": record.fund_code,
        "nav_date": record.nav_date.isoformat(),
        "unit_nav": str(record.unit_nav),
        "accumulated_nav": str(record.accumulated_nav) if record.accumulated_nav is not None else None,
        "announcement_at": _timestamp(record.announcement_at),
        "source_observed_at": _timestamp(record.source_observed_at),
        "first_seen_at": _timestamp(record.first_seen_at),
        "visibility_status": record.visibility_status.value,
    }


def _metadata_record_payload(record: FundMetadataRecord) -> dict[str, Any]:
    return {
        "fund_code": record.fund_code,
        "fund_name": record.fund_name,
        "fund_type": record.fund_type,
        "fund_manager": record.fund_manager,
        "management_company": record.management_company,
        "custodian": record.custodian,
        "establishment_date": (
            record.establishment_date.isoformat() if record.establishment_date else None
        ),
        "raw_fields": dict(sorted(record.raw_fields.items())),
        "source_observed_at": _timestamp(record.source_observed_at),
    }


def _distribution_record_payload(record: FundDistributionRecord) -> dict[str, Any]:
    return {
        "fund_code": record.fund_code,
        "ex_date": record.ex_date.isoformat(),
        "cash_per_share": str(record.cash_per_share),
        "source_text": record.source_text,
        "announcement_at": _timestamp(record.announcement_at),
        "source_observed_at": _timestamp(record.source_observed_at),
        "first_seen_at": _timestamp(record.first_seen_at),
        "visibility_status": record.visibility_status.value,
    }


def batch_content_sha256(batch: DataBatch, report: BatchQualityReport) -> str:
    """Hash all material inputs so a batch ID can never be silently reused."""

    payload = {
        "provider_id": batch.provider_id,
        "batch_id": batch.batch_id,
        "fetched_at": batch.fetched_at.isoformat(),
        "as_of": batch.as_of.isoformat(),
        "provenance": batch.provenance,
        "source_domain": batch.source_domain,
        "request_parameters": dict(sorted(batch.request_parameters.items())),
        "raw_content_sha256": batch.raw_content_sha256,
        "records": [_record_payload(record) for record in batch.records],
        "quality_issues": [issue.to_dict() for issue in report.issues],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def metadata_batch_content_sha256(
    batch: FundMetadataBatch, report: BatchQualityReport
) -> str:
    payload = {
        "batch_kind": "fund_metadata",
        "provider_id": batch.provider_id,
        "batch_id": batch.batch_id,
        "fetched_at": batch.fetched_at.isoformat(),
        "as_of": batch.as_of.isoformat(),
        "provenance": batch.provenance,
        "source_domain": batch.source_domain,
        "request_parameters": dict(sorted(batch.request_parameters.items())),
        "raw_content_sha256": batch.raw_content_sha256,
        "records": [_metadata_record_payload(record) for record in batch.records],
        "quality_issues": [issue.to_dict() for issue in report.issues],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def distribution_batch_content_sha256(
    batch: FundDistributionBatch, report: BatchQualityReport
) -> str:
    payload = {
        "batch_kind": "fund_distribution",
        "provider_id": batch.provider_id,
        "batch_id": batch.batch_id,
        "source_nav_batch_id": batch.source_nav_batch_id,
        "fetched_at": batch.fetched_at.isoformat(),
        "as_of": batch.as_of.isoformat(),
        "provenance": batch.provenance,
        "source_domain": batch.source_domain,
        "request_parameters": dict(sorted(batch.request_parameters.items())),
        "raw_content_sha256": batch.raw_content_sha256,
        "records": [_distribution_record_payload(record) for record in batch.records],
        "quality_issues": [issue.to_dict() for issue in report.issues],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FundDataStore:
    """Persist validated records while retaining rejected batch audit metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS data_batches (
                    batch_id TEXT PRIMARY KEY,
                    batch_kind TEXT NOT NULL DEFAULT 'fund_nav' CHECK (
                        batch_kind IN ('fund_nav', 'fund_metadata')
                    ),
                    provider_id TEXT NOT NULL,
                    source_domain TEXT,
                    fetched_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    raw_content_sha256 TEXT,
                    content_sha256 TEXT NOT NULL,
                    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'partial', 'fail')),
                    quality_issues_json TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fund_nav_observations (
                    batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    fund_code TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    unit_nav TEXT NOT NULL,
                    accumulated_nav TEXT,
                    announcement_at TEXT,
                    source_observed_at TEXT,
                    first_seen_at TEXT,
                    visibility_status TEXT NOT NULL CHECK (
                        visibility_status IN ('strict_point_in_time', 'historical_visibility_assumed')
                    ),
                    PRIMARY KEY (batch_id, fund_code, nav_date)
                );

                CREATE TABLE IF NOT EXISTS fund_metadata_observations (
                    batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    fund_code TEXT NOT NULL,
                    fund_name TEXT,
                    fund_type TEXT,
                    fund_manager TEXT,
                    management_company TEXT,
                    custodian TEXT,
                    establishment_date TEXT,
                    raw_fields_json TEXT NOT NULL,
                    source_observed_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, fund_code)
                );

                CREATE TABLE IF NOT EXISTS fund_nav_source_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    fund_code TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    field_name TEXT NOT NULL CHECK (
                        field_name IN ('unit_nav', 'accumulated_nav')
                    ),
                    left_provider TEXT NOT NULL,
                    left_batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    left_value TEXT,
                    right_provider TEXT NOT NULL,
                    right_batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    right_value TEXT,
                    detected_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
                    resolution_json TEXT
                );

                CREATE TABLE IF NOT EXISTS fund_distribution_batches (
                    batch_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    source_nav_batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    source_domain TEXT,
                    fetched_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    raw_content_sha256 TEXT,
                    content_sha256 TEXT NOT NULL,
                    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'partial', 'fail')),
                    quality_issues_json TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fund_distribution_observations (
                    batch_id TEXT NOT NULL REFERENCES fund_distribution_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    fund_code TEXT NOT NULL,
                    ex_date TEXT NOT NULL,
                    cash_per_share TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    announcement_at TEXT,
                    source_observed_at TEXT,
                    first_seen_at TEXT,
                    visibility_status TEXT NOT NULL CHECK (
                        visibility_status IN ('strict_point_in_time', 'historical_visibility_assumed')
                    ),
                    PRIMARY KEY (batch_id, fund_code, ex_date)
                );

                CREATE TABLE IF NOT EXISTS fund_history_boundaries (
                    fund_code TEXT PRIMARY KEY,
                    current_contract_start_date TEXT NOT NULL,
                    boundary_kind TEXT NOT NULL,
                    current_identity TEXT NOT NULL,
                    source_reference TEXT NOT NULL,
                    source_checked_at TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL,
                    activated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fund_history_backfills (
                    fund_code TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    current_contract_start_date TEXT NOT NULL,
                    batch_id TEXT NOT NULL REFERENCES data_batches(batch_id),
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (fund_code, provider_id, current_contract_start_date)
                );

                CREATE INDEX IF NOT EXISTS idx_nav_fund_date
                    ON fund_nav_observations(fund_code, nav_date);
                CREATE INDEX IF NOT EXISTS idx_metadata_fund
                    ON fund_metadata_observations(fund_code, source_observed_at);
                CREATE INDEX IF NOT EXISTS idx_nav_conflict_fund_date
                    ON fund_nav_source_conflicts(fund_code, nav_date);
                CREATE INDEX IF NOT EXISTS idx_batch_provider
                    ON data_batches(provider_id, fetched_at);
                CREATE INDEX IF NOT EXISTS idx_distribution_fund_date
                    ON fund_distribution_observations(fund_code, ex_date);
                """
            )
            existing = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            existing_version = int(existing["value"]) if existing is not None else None
            if existing_version is not None and existing_version not in {1, 2, 3, SCHEMA_VERSION}:
                raise RuntimeError(
                    f"Unsupported data-store schema version: {existing['value']}"
                )
            batch_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(data_batches)")
            }
            if "batch_kind" not in batch_columns:
                connection.execute(
                    "ALTER TABLE data_batches ADD COLUMN batch_kind TEXT NOT NULL DEFAULT 'fund_nav'"
                )
            connection.execute(
                """
                INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
        self.path.chmod(0o600)

    def publish_nav_batch(self, batch: DataBatch) -> PublishResult:
        self.initialize()
        report = evaluate_nav_batch(batch)
        content_hash = batch_content_sha256(batch, report)
        issues_json = json.dumps(
            [issue.to_dict() for issue in report.issues],
            ensure_ascii=False,
            sort_keys=True,
        )
        request_json = json.dumps(
            dict(sorted(batch.request_parameters.items())),
            ensure_ascii=False,
            sort_keys=True,
        )

        with self.session() as connection:
            existing = connection.execute(
                "SELECT content_sha256, quality_status FROM data_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != content_hash:
                    raise ImmutableBatchConflict(
                        f"Batch ID {batch.batch_id} already exists with different content"
                    )
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_nav_observations WHERE batch_id = ?",
                    (batch.batch_id,),
                ).fetchone()["count"]
                return PublishResult(
                    batch_id=batch.batch_id,
                    content_sha256=content_hash,
                    quality_report=report,
                    published_records=int(count),
                    idempotent_replay=True,
                )

            connection.execute(
                """
                INSERT INTO data_batches(
                    batch_id, batch_kind, provider_id, source_domain, fetched_at, as_of,
                    provenance, request_json, raw_content_sha256, content_sha256,
                    quality_status, quality_issues_json, published_at
                ) VALUES (?, 'fund_nav', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.provider_id,
                    batch.source_domain,
                    batch.fetched_at.isoformat(),
                    batch.as_of.isoformat(),
                    batch.provenance,
                    request_json,
                    batch.raw_content_sha256,
                    content_hash,
                    report.status.value,
                    issues_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            published_records = 0
            if report.can_publish:
                connection.executemany(
                    """
                    INSERT INTO fund_nav_observations(
                        batch_id, provider_id, fund_code, nav_date, unit_nav,
                        accumulated_nav, announcement_at, source_observed_at,
                        first_seen_at, visibility_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            record.fund_code,
                            record.nav_date.isoformat(),
                            str(record.unit_nav),
                            str(record.accumulated_nav) if record.accumulated_nav is not None else None,
                            _timestamp(record.announcement_at),
                            _timestamp(record.source_observed_at),
                            _timestamp(record.first_seen_at),
                            record.visibility_status.value,
                        )
                        for record in batch.records
                    ],
                )
                published_records = len(batch.records)
                self._record_cross_source_conflicts(
                    connection,
                    batch_id=batch.batch_id,
                    provider_id=batch.provider_id,
                    records=batch.records,
                )

        return PublishResult(
            batch_id=batch.batch_id,
            content_sha256=content_hash,
            quality_report=report,
            published_records=published_records,
        )

    @staticmethod
    def _record_cross_source_conflicts(
        connection: sqlite3.Connection,
        *,
        batch_id: str,
        provider_id: str,
        records: Sequence[FundNavRecord],
        tolerance: Decimal = Decimal("0.0000005"),
    ) -> None:
        detected_at = datetime.now(timezone.utc).isoformat()
        for record in records:
            others = connection.execute(
                """
                SELECT observation.batch_id, observation.provider_id,
                       observation.unit_nav, observation.accumulated_nav
                FROM fund_nav_observations AS observation
                JOIN data_batches AS batch ON batch.batch_id = observation.batch_id
                WHERE observation.fund_code = ? AND observation.nav_date = ?
                  AND observation.provider_id <> ? AND batch.quality_status <> 'fail'
                """,
                (record.fund_code, record.nav_date.isoformat(), provider_id),
            ).fetchall()
            for other in others:
                pairs = (
                    ("unit_nav", str(record.unit_nav), other["unit_nav"]),
                    (
                        "accumulated_nav",
                        str(record.accumulated_nav) if record.accumulated_nav is not None else None,
                        other["accumulated_nav"],
                    ),
                )
                for field_name, current_value, other_value in pairs:
                    if current_value is None or other_value is None:
                        continue
                    if abs(Decimal(current_value) - Decimal(other_value)) <= tolerance:
                        continue
                    sides = sorted(
                        (
                            (provider_id, batch_id, current_value),
                            (other["provider_id"], other["batch_id"], other_value),
                        ),
                        key=lambda item: (item[0], item[1]),
                    )
                    identity = "|".join(
                        (
                            record.fund_code,
                            record.nav_date.isoformat(),
                            field_name,
                            sides[0][0],
                            sides[0][1],
                            sides[1][0],
                            sides[1][1],
                        )
                    )
                    conflict_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO fund_nav_source_conflicts(
                            conflict_id, fund_code, nav_date, field_name,
                            left_provider, left_batch_id, left_value,
                            right_provider, right_batch_id, right_value,
                            detected_at, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                        """,
                        (
                            conflict_id,
                            record.fund_code,
                            record.nav_date.isoformat(),
                            field_name,
                            sides[0][0],
                            sides[0][1],
                            sides[0][2],
                            sides[1][0],
                            sides[1][1],
                            sides[1][2],
                            detected_at,
                        ),
                    )

    def publish_fund_metadata_batch(self, batch: FundMetadataBatch) -> PublishResult:
        self.initialize()
        report = evaluate_fund_metadata_batch(batch)
        content_hash = metadata_batch_content_sha256(batch, report)
        issues_json = json.dumps(
            [issue.to_dict() for issue in report.issues], ensure_ascii=False, sort_keys=True
        )
        request_json = json.dumps(
            dict(sorted(batch.request_parameters.items())), ensure_ascii=False, sort_keys=True
        )

        with self.session() as connection:
            existing = connection.execute(
                "SELECT content_sha256 FROM data_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != content_hash:
                    raise ImmutableBatchConflict(
                        f"Batch ID {batch.batch_id} already exists with different content"
                    )
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_metadata_observations WHERE batch_id = ?",
                    (batch.batch_id,),
                ).fetchone()["count"]
                return PublishResult(
                    batch_id=batch.batch_id,
                    content_sha256=content_hash,
                    quality_report=report,
                    published_records=int(count),
                    idempotent_replay=True,
                )

            connection.execute(
                """
                INSERT INTO data_batches(
                    batch_id, batch_kind, provider_id, source_domain, fetched_at, as_of,
                    provenance, request_json, raw_content_sha256, content_sha256,
                    quality_status, quality_issues_json, published_at
                ) VALUES (?, 'fund_metadata', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.provider_id,
                    batch.source_domain,
                    batch.fetched_at.isoformat(),
                    batch.as_of.isoformat(),
                    batch.provenance,
                    request_json,
                    batch.raw_content_sha256,
                    content_hash,
                    report.status.value,
                    issues_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            published_records = 0
            if report.can_publish:
                connection.executemany(
                    """
                    INSERT INTO fund_metadata_observations(
                        batch_id, provider_id, fund_code, fund_name, fund_type,
                        fund_manager, management_company, custodian,
                        establishment_date, raw_fields_json, source_observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            record.fund_code,
                            record.fund_name,
                            record.fund_type,
                            record.fund_manager,
                            record.management_company,
                            record.custodian,
                            record.establishment_date.isoformat()
                            if record.establishment_date
                            else None,
                            json.dumps(
                                dict(sorted(record.raw_fields.items())),
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            _timestamp(record.source_observed_at),
                        )
                        for record in batch.records
                    ],
                )
                published_records = len(batch.records)

        return PublishResult(
            batch_id=batch.batch_id,
            content_sha256=content_hash,
            quality_report=report,
            published_records=published_records,
        )

    def publish_distribution_batch(self, batch: FundDistributionBatch) -> PublishResult:
        self.initialize()
        report = evaluate_distribution_batch(batch)
        content_hash = distribution_batch_content_sha256(batch, report)
        issues_json = json.dumps(
            [issue.to_dict() for issue in report.issues], ensure_ascii=False, sort_keys=True
        )
        request_json = json.dumps(
            dict(sorted(batch.request_parameters.items())), ensure_ascii=False, sort_keys=True
        )
        with self.session() as connection:
            existing = connection.execute(
                "SELECT content_sha256 FROM fund_distribution_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != content_hash:
                    raise ImmutableBatchConflict(
                        f"Distribution batch ID {batch.batch_id} already exists with different content"
                    )
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_distribution_observations WHERE batch_id = ?",
                    (batch.batch_id,),
                ).fetchone()["count"]
                return PublishResult(
                    batch_id=batch.batch_id,
                    content_sha256=content_hash,
                    quality_report=report,
                    published_records=int(count),
                    idempotent_replay=True,
                )
            source = connection.execute(
                "SELECT batch_id FROM data_batches WHERE batch_id = ?",
                (batch.source_nav_batch_id,),
            ).fetchone()
            if source is None:
                raise ValueError(
                    f"Source NAV batch is not present in the local store: {batch.source_nav_batch_id}"
                )
            connection.execute(
                """
                INSERT INTO fund_distribution_batches(
                    batch_id, provider_id, source_nav_batch_id, source_domain,
                    fetched_at, as_of, provenance, request_json, raw_content_sha256,
                    content_sha256, quality_status, quality_issues_json, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.provider_id,
                    batch.source_nav_batch_id,
                    batch.source_domain,
                    batch.fetched_at.isoformat(),
                    batch.as_of.isoformat(),
                    batch.provenance,
                    request_json,
                    batch.raw_content_sha256,
                    content_hash,
                    report.status.value,
                    issues_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            published_records = 0
            if report.can_publish:
                connection.executemany(
                    """
                    INSERT INTO fund_distribution_observations(
                        batch_id, provider_id, fund_code, ex_date, cash_per_share,
                        source_text, announcement_at, source_observed_at,
                        first_seen_at, visibility_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            record.fund_code,
                            record.ex_date.isoformat(),
                            str(record.cash_per_share),
                            record.source_text,
                            _timestamp(record.announcement_at),
                            _timestamp(record.source_observed_at),
                            _timestamp(record.first_seen_at),
                            record.visibility_status.value,
                        )
                        for record in batch.records
                    ],
                )
                published_records = len(batch.records)
        return PublishResult(
            batch_id=batch.batch_id,
            content_sha256=content_hash,
            quality_report=report,
            published_records=published_records,
        )

    def iter_batches(self) -> Iterator[sqlite3.Row]:
        self.initialize()
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT batch_id, batch_kind, provider_id, source_domain, fetched_at, as_of,
                       content_sha256, quality_status
                FROM data_batches
                ORDER BY fetched_at, batch_id
                """
            ).fetchall()
        yield from rows

    def count_nav_observations(self, *, batch_id: str | None = None) -> int:
        self.initialize()
        with self.session() as connection:
            if batch_id is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_nav_observations"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_nav_observations WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
        return int(row["count"])

    def count_fund_metadata_observations(self, *, batch_id: str | None = None) -> int:
        self.initialize()
        with self.session() as connection:
            if batch_id is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_metadata_observations"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_metadata_observations WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
        return int(row["count"])

    def count_distribution_observations(self, *, batch_id: str | None = None) -> int:
        self.initialize()
        with self.session() as connection:
            if batch_id is None:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_distribution_observations"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM fund_distribution_observations WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
        return int(row["count"])

    def latest_nav_date(self, *, fund_code: str, provider_id: str) -> str | None:
        """Return the newest published NAV date for one provider and fund."""

        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT MAX(observation.nav_date) AS latest_nav_date
                FROM fund_nav_observations AS observation
                JOIN data_batches AS batch ON batch.batch_id = observation.batch_id
                WHERE observation.fund_code = ?
                  AND observation.provider_id = ?
                  AND batch.quality_status <> 'fail'
                """,
                (fund_code, provider_id),
            ).fetchone()
        value = row["latest_nav_date"]
        return str(value) if value is not None else None

    def activate_history_boundary(
        self,
        *,
        fund_code: str,
        current_contract_start_date: str,
        boundary_kind: str,
        current_identity: str,
        source_reference: str,
        source_checked_at: str,
        config_sha256: str,
    ) -> None:
        """Mirror a verified current-contract boundary into the serving store."""

        self.initialize()
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO fund_history_boundaries(
                    fund_code, current_contract_start_date, boundary_kind,
                    current_identity, source_reference, source_checked_at,
                    config_sha256, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fund_code) DO UPDATE SET
                    current_contract_start_date = excluded.current_contract_start_date,
                    boundary_kind = excluded.boundary_kind,
                    current_identity = excluded.current_identity,
                    source_reference = excluded.source_reference,
                    source_checked_at = excluded.source_checked_at,
                    config_sha256 = excluded.config_sha256,
                    activated_at = excluded.activated_at
                """,
                (
                    fund_code,
                    current_contract_start_date,
                    boundary_kind,
                    current_identity,
                    source_reference,
                    source_checked_at,
                    config_sha256,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def history_backfill_completed(
        self,
        *,
        fund_code: str,
        provider_id: str,
        current_contract_start_date: str,
    ) -> bool:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM fund_history_backfills
                WHERE fund_code = ? AND provider_id = ?
                  AND current_contract_start_date = ?
                """,
                (fund_code, provider_id, current_contract_start_date),
            ).fetchone()
        return row is not None

    def mark_history_backfill_completed(
        self,
        *,
        fund_code: str,
        provider_id: str,
        current_contract_start_date: str,
        batch_id: str,
    ) -> None:
        self.initialize()
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO fund_history_backfills(
                    fund_code, provider_id, current_contract_start_date,
                    batch_id, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(fund_code, provider_id, current_contract_start_date)
                DO UPDATE SET batch_id = excluded.batch_id,
                              completed_at = excluded.completed_at
                """,
                (
                    fund_code,
                    provider_id,
                    current_contract_start_date,
                    batch_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def iter_distribution_batches(self) -> Iterator[sqlite3.Row]:
        self.initialize()
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT batch_id, provider_id, source_nav_batch_id, source_domain,
                       fetched_at, as_of, content_sha256, quality_status
                FROM fund_distribution_batches
                ORDER BY fetched_at, batch_id
                """
            ).fetchall()
        yield from rows

    def compare_nav_sources(
        self,
        *,
        left_provider: str,
        right_provider: str,
        fund_codes: Sequence[str] = (),
        start_date: str | None = None,
        end_date: str | None = None,
        tolerance: Decimal = Decimal("0.0000005"),
    ) -> dict[str, object]:
        """Compare latest published observations per provider without selecting a winner."""

        self.initialize()
        clauses = ["batch.quality_status <> 'fail'", "observation.provider_id IN (?, ?)"]
        parameters: list[object] = [left_provider, right_provider]
        if fund_codes:
            placeholders = ",".join("?" for _ in fund_codes)
            clauses.append(f"observation.fund_code IN ({placeholders})")
            parameters.extend(fund_codes)
        if start_date:
            clauses.append("observation.nav_date >= ?")
            parameters.append(start_date)
        if end_date:
            clauses.append("observation.nav_date <= ?")
            parameters.append(end_date)
        where_sql = " AND ".join(clauses)
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT observation.provider_id, observation.fund_code,
                       observation.nav_date, observation.unit_nav,
                       observation.accumulated_nav, observation.batch_id,
                       batch.published_at
                FROM fund_nav_observations AS observation
                JOIN data_batches AS batch ON batch.batch_id = observation.batch_id
                WHERE {where_sql}
                ORDER BY batch.published_at DESC, observation.batch_id DESC
                """,
                parameters,
            ).fetchall()

        latest: dict[tuple[str, str, str], sqlite3.Row] = {}
        keys: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["provider_id"], row["fund_code"], row["nav_date"])
            latest.setdefault(key, row)
            keys.add((row["fund_code"], row["nav_date"]))

        samples: list[dict[str, object]] = []
        counts = {"matched": 0, "conflict": 0, "missing_left": 0, "missing_right": 0}
        for fund_code, nav_date in sorted(keys):
            left = latest.get((left_provider, fund_code, nav_date))
            right = latest.get((right_provider, fund_code, nav_date))
            if left is None:
                status = "missing_left"
            elif right is None:
                status = "missing_right"
            else:
                unit_match = abs(Decimal(left["unit_nav"]) - Decimal(right["unit_nav"])) <= tolerance
                left_acc = left["accumulated_nav"]
                right_acc = right["accumulated_nav"]
                accumulated_match = (
                    left_acc is None
                    or right_acc is None
                    or abs(Decimal(left_acc) - Decimal(right_acc)) <= tolerance
                )
                status = "matched" if unit_match and accumulated_match else "conflict"
            counts[status] += 1
            samples.append(
                {
                    "fund_code": fund_code,
                    "nav_date": nav_date,
                    "status": status,
                    "left": None
                    if left is None
                    else {
                        "batch_id": left["batch_id"],
                        "unit_nav": left["unit_nav"],
                        "accumulated_nav": left["accumulated_nav"],
                    },
                    "right": None
                    if right is None
                    else {
                        "batch_id": right["batch_id"],
                        "unit_nav": right["unit_nav"],
                        "accumulated_nav": right["accumulated_nav"],
                    },
                }
            )
        return {
            "left_provider": left_provider,
            "right_provider": right_provider,
            "tolerance": str(tolerance),
            "counts": counts,
            "samples": samples,
        }

    def count_open_nav_conflicts(self) -> int:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM fund_nav_source_conflicts WHERE status = 'open'"
            ).fetchone()
        return int(row["count"])
