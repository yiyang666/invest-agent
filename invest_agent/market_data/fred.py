"""Raw-first FRED download adapter for reviewed personal-research series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Mapping
from urllib.parse import urlencode

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import MarketDataBatch, MarketNumericObservation


PROVIDER_ID = "fred_download"
FRED_GRAPH_CSV_ENDPOINT = "https://fred.stlouisfed.org/graph/fredgraph.csv"

_NORMALIZED_SCHEMA = {
    "adapter": "fred_graph_csv_v1",
    "fields": [
        "series_id",
        "observation_date",
        "value",
        "unit",
        "frequency",
        "label",
        "first_seen_at",
        "visibility_status",
        "attributes",
    ],
    "latest_row_visibility": "strict_point_in_time_at_retrieval",
    "older_row_visibility": "historical_visibility_assumed",
}
FRED_NORMALIZED_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(_NORMALIZED_SCHEMA, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class FredSeriesPolicy:
    source_series_id: str
    local_series_id: str
    label: str
    unit: str
    frequency: str
    source_owner: str
    citation_url: str
    usage_scope: str


@dataclass(frozen=True)
class RawFredResponse:
    series_id: str
    fetched_at: datetime
    payload: bytes
    content_type: str
    request_url: str
    start_date: date | None


def load_fred_policy(path: str | Path, series_id: str) -> FredSeriesPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_policy = payload.get("fred_policy")
    if not isinstance(raw_policy, Mapping) or raw_policy.get("enabled") is not True:
        raise ValueError("FRED collection is not enabled in market-data config")
    raw_series = raw_policy.get("series")
    if not isinstance(raw_series, Mapping) or series_id not in raw_series:
        raise ValueError(f"FRED series is not allowlisted: {series_id}")
    item = raw_series[series_id]
    if not isinstance(item, Mapping):
        raise ValueError(f"Invalid FRED series policy: {series_id}")
    required = (
        "local_series_id",
        "label",
        "unit",
        "frequency",
        "source_owner",
        "citation_url",
        "usage_scope",
    )
    if any(not isinstance(item.get(key), str) or not item[key] for key in required):
        raise ValueError(f"Incomplete FRED series policy: {series_id}")
    if item["usage_scope"] != "personal_research_local_only":
        raise ValueError(f"Unsupported FRED usage scope: {item['usage_scope']}")
    return FredSeriesPolicy(
        source_series_id=series_id,
        local_series_id=str(item["local_series_id"]),
        label=str(item["label"]),
        unit=str(item["unit"]),
        frequency=str(item["frequency"]),
        source_owner=str(item["source_owner"]),
        citation_url=str(item["citation_url"]),
        usage_scope=str(item["usage_scope"]),
    )


class FredCsvClient:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        series_id: str,
        fetched_at: datetime,
        start_date: date | None = None,
    ) -> RawFredResponse:
        params: dict[str, str] = {"id": series_id}
        if start_date is not None:
            params["cosd"] = start_date.isoformat()
        request_url = f"{FRED_GRAPH_CSV_ENDPOINT}?{urlencode(params)}"
        # requests/http1 stalls against this endpoint on the project host while
        # the system curl/http2 transport is stable.  Keep the transport fixed:
        # one direct request, no cookies, retries, fallback host, or proxy rotation.
        completed = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "--location",
                "--fail-with-body",
                "--silent",
                "--show-error",
                "--max-time",
                str(self.timeout_seconds),
                "--url",
                request_url,
            ],
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(f"FRED curl request failed ({completed.returncode}): {message}")
        return RawFredResponse(
            series_id=series_id,
            fetched_at=fetched_at,
            payload=completed.stdout,
            content_type="application/csv",
            request_url=request_url,
            start_date=start_date,
        )


def _decimal(value: str, *, series_id: str, observation_date: date) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"Invalid FRED value for {series_id} on {observation_date}: {value!r}"
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"Non-finite FRED value for {series_id} on {observation_date}")
    return parsed


def normalize_fred_csv(
    *,
    batch_id: str,
    payload: bytes,
    policy: FredSeriesPolicy,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    if fetched_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("FRED timestamps require timezone")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("FRED CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or len(reader.fieldnames) != 2:
        raise ValueError("FRED CSV must contain exactly date and value columns")
    date_column, value_column = reader.fieldnames
    if date_column not in {"observation_date", "DATE"}:
        raise ValueError(f"Unexpected FRED date column: {date_column}")
    if value_column != policy.source_series_id:
        raise ValueError(
            f"Unexpected FRED value column: {value_column}; expected {policy.source_series_id}"
        )

    parsed_rows: list[tuple[date, Decimal]] = []
    skipped_missing = 0
    skipped_future = 0
    for row in reader:
        raw_date = row.get(date_column)
        raw_value = row.get(value_column)
        if not raw_date:
            raise ValueError("FRED CSV contains a row without observation date")
        observed = date.fromisoformat(raw_date)
        if observed > as_of.date():
            skipped_future += 1
            continue
        if raw_value is None or raw_value.strip() in {"", "."}:
            skipped_missing += 1
            continue
        parsed_rows.append(
            (observed, _decimal(raw_value.strip(), series_id=value_column, observation_date=observed))
        )
    if not parsed_rows:
        raise ValueError(f"FRED returned no usable observations for {policy.source_series_id}")
    parsed_rows.sort(key=lambda item: item[0])
    latest_date = parsed_rows[-1][0]

    observations = tuple(
        MarketNumericObservation(
            series_id=policy.local_series_id,
            observation_date=observed,
            value=value,
            unit=policy.unit,
            frequency=policy.frequency,
            label=policy.label,
            first_seen_at=fetched_at if observed == latest_date else None,
            visibility_status=(
                VisibilityStatus.STRICT_POINT_IN_TIME
                if observed == latest_date
                else VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED
            ),
            attributes={
                "source_series_id": policy.source_series_id,
                "source_owner": policy.source_owner,
                "citation_url": policy.citation_url,
                "usage_scope": policy.usage_scope,
                "observation_semantics": "provider_observation_date",
            },
        )
        for observed, value in parsed_rows
    )
    issues = [
        QualityIssue(
            "fred_historical_release_time_assumed",
            "Older FRED observations lack original release timestamps and remain research-only",
            QualitySeverity.WARNING,
        ),
        QualityIssue(
            "fred_personal_research_scope",
            "FRED series is retained locally for personal research with source citation; redistribution is not allowed",
            QualitySeverity.WARNING,
        ),
    ]
    if skipped_missing:
        issues.append(
            QualityIssue(
                "fred_missing_values_skipped",
                f"Skipped {skipped_missing} missing FRED values",
                QualitySeverity.WARNING,
            )
        )
    if skipped_future:
        issues.append(
            QualityIssue(
                "fred_future_rows_skipped",
                f"Skipped {skipped_future} observations after as_of",
                QualitySeverity.WARNING,
            )
        )
    return MarketDataBatch(
        provider_id=PROVIDER_ID,
        batch_id=batch_id,
        tool_name=f"fred:{policy.source_series_id}",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments={"series_id": policy.source_series_id},
        raw_content_sha256=raw_content_sha256,
        schema_sha256=FRED_NORMALIZED_SCHEMA_SHA256,
        numeric_observations=observations,
        quality_issues=tuple(issues),
    )
