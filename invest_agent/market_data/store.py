"""SQLite isolation tables for validated external market-context data."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .contracts import MarketDataBatch
from .normalize import normalized_batch_sha256
from .quality import MarketBatchQualityReport, evaluate_market_batch


MARKET_SCHEMA_VERSION = 1


class ImmutableMarketBatchConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketPublishResult:
    batch_id: str
    content_sha256: str
    quality_report: MarketBatchQualityReport
    published_numeric_records: int
    published_weight_records: int
    published_catalog_records: int
    idempotent_replay: bool = False


class MarketDataStore:
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
                CREATE TABLE IF NOT EXISTS market_schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_data_batches (
                    batch_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    raw_content_sha256 TEXT NOT NULL,
                    schema_sha256 TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    quality_status TEXT NOT NULL CHECK (quality_status IN ('pass', 'partial', 'fail')),
                    quality_issues_json TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS market_numeric_observations (
                    batch_id TEXT NOT NULL REFERENCES market_data_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    value TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    label TEXT NOT NULL,
                    published_date TEXT,
                    first_seen_at TEXT,
                    visibility_status TEXT NOT NULL CHECK (
                        visibility_status IN ('strict_point_in_time', 'historical_visibility_assumed')
                    ),
                    attributes_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, series_id, observation_date)
                );

                CREATE TABLE IF NOT EXISTS market_index_weight_observations (
                    batch_id TEXT NOT NULL REFERENCES market_data_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    index_code TEXT NOT NULL,
                    index_name TEXT NOT NULL,
                    weight_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    weight_pct TEXT NOT NULL,
                    is_estimated INTEGER NOT NULL CHECK (is_estimated IN (0, 1)),
                    first_seen_at TEXT NOT NULL,
                    industry TEXT,
                    contribution_pct_points TEXT,
                    attributes_json TEXT NOT NULL,
                    PRIMARY KEY (batch_id, index_code, weight_date, stock_code)
                );

                CREATE TABLE IF NOT EXISTS market_dataset_catalog_observations (
                    batch_id TEXT NOT NULL REFERENCES market_data_batches(batch_id),
                    provider_id TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    last_updated TEXT NOT NULL,
                    PRIMARY KEY (batch_id, dataset_name)
                );

                CREATE INDEX IF NOT EXISTS idx_market_numeric_series_date
                    ON market_numeric_observations(series_id, observation_date);
                CREATE INDEX IF NOT EXISTS idx_market_weight_index_date
                    ON market_index_weight_observations(index_code, weight_date);
                CREATE INDEX IF NOT EXISTS idx_market_batch_tool_time
                    ON market_data_batches(tool_name, fetched_at);
                """
            )
            existing = connection.execute(
                "SELECT value FROM market_schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if existing is not None and int(existing["value"]) != MARKET_SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported market schema version: {existing['value']}")
            connection.execute(
                """
                INSERT INTO market_schema_metadata(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(MARKET_SCHEMA_VERSION),),
            )
        self.path.chmod(0o600)

    def publish(self, batch: MarketDataBatch) -> MarketPublishResult:
        self.initialize()
        report = evaluate_market_batch(batch)
        content_hash = normalized_batch_sha256(batch)
        issues_json = json.dumps([i.to_dict() for i in report.issues], ensure_ascii=False, sort_keys=True)
        request_json = json.dumps(batch.request_arguments, ensure_ascii=False, sort_keys=True)
        with self.session() as connection:
            existing = connection.execute(
                "SELECT content_sha256 FROM market_data_batches WHERE batch_id = ?",
                (batch.batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["content_sha256"] != content_hash:
                    raise ImmutableMarketBatchConflict(
                        f"Market batch ID {batch.batch_id} already exists with different content"
                    )
                counts = self._counts(connection, batch.batch_id)
                return MarketPublishResult(
                    batch_id=batch.batch_id,
                    content_sha256=content_hash,
                    quality_report=report,
                    published_numeric_records=counts[0],
                    published_weight_records=counts[1],
                    published_catalog_records=counts[2],
                    idempotent_replay=True,
                )
            connection.execute(
                """
                INSERT INTO market_data_batches(
                    batch_id, provider_id, tool_name, fetched_at, as_of, request_json,
                    raw_content_sha256, schema_sha256, content_sha256, quality_status,
                    quality_issues_json, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.provider_id,
                    batch.tool_name,
                    batch.fetched_at.isoformat(),
                    batch.as_of.isoformat(),
                    request_json,
                    batch.raw_content_sha256,
                    batch.schema_sha256,
                    content_hash,
                    report.status.value,
                    issues_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if report.can_publish:
                connection.executemany(
                    """
                    INSERT INTO market_numeric_observations(
                        batch_id, provider_id, series_id, observation_date, value, unit,
                        frequency, label, published_date, first_seen_at, visibility_status,
                        attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            r.series_id,
                            r.observation_date.isoformat(),
                            str(r.value),
                            r.unit,
                            r.frequency,
                            r.label,
                            r.published_date.isoformat() if r.published_date else None,
                            r.first_seen_at.isoformat() if r.first_seen_at else None,
                            r.visibility_status.value,
                            json.dumps(r.attributes, ensure_ascii=False, sort_keys=True),
                        )
                        for r in batch.numeric_observations
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO market_index_weight_observations(
                        batch_id, provider_id, index_code, index_name, weight_date,
                        stock_code, stock_name, weight_pct, is_estimated, first_seen_at,
                        industry, contribution_pct_points, attributes_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            r.index_code,
                            r.index_name,
                            r.weight_date.isoformat(),
                            r.stock_code,
                            r.stock_name,
                            str(r.weight_pct),
                            int(r.is_estimated),
                            r.first_seen_at.isoformat(),
                            r.industry,
                            str(r.contribution_pct_points) if r.contribution_pct_points is not None else None,
                            json.dumps(r.attributes, ensure_ascii=False, sort_keys=True),
                        )
                        for r in batch.index_weights
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO market_dataset_catalog_observations(
                        batch_id, provider_id, dataset_name, tool_name, description, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            batch.batch_id,
                            batch.provider_id,
                            r.dataset_name,
                            r.tool_name,
                            r.description,
                            r.last_updated.isoformat(),
                        )
                        for r in batch.catalog_observations
                    ],
                )
            counts = self._counts(connection, batch.batch_id)
        return MarketPublishResult(
            batch_id=batch.batch_id,
            content_sha256=content_hash,
            quality_report=report,
            published_numeric_records=counts[0],
            published_weight_records=counts[1],
            published_catalog_records=counts[2],
        )

    @staticmethod
    def _counts(connection: sqlite3.Connection, batch_id: str) -> tuple[int, int, int]:
        tables = (
            "market_numeric_observations",
            "market_index_weight_observations",
            "market_dataset_catalog_observations",
        )
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE batch_id = ?", (batch_id,)).fetchone()["count"])
            for table in tables
        )  # type: ignore[return-value]

    def iter_batches(self):
        self.initialize()
        connection = self.connect()
        try:
            return connection.execute(
                """
                SELECT batch_id, tool_name, fetched_at, as_of, quality_status,
                       raw_content_sha256, content_sha256
                FROM market_data_batches ORDER BY fetched_at, batch_id
                """
            ).fetchall()
        finally:
            connection.close()

    def series_summary(self):
        self.initialize()
        connection = self.connect()
        try:
            return connection.execute(
                """
                SELECT series_id, unit, frequency, COUNT(*) AS versions,
                       MIN(observation_date) AS start_date,
                       MAX(observation_date) AS end_date,
                       MAX(first_seen_at) AS latest_first_seen_at
                FROM market_numeric_observations
                GROUP BY series_id, unit, frequency
                ORDER BY series_id
                """
            ).fetchall()
        finally:
            connection.close()
