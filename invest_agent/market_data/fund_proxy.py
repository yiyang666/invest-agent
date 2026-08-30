"""Bridge validated local fund NAVs into explicitly labelled market proxies."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Sequence

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import MarketDataBatch, MarketNumericObservation


_NORMALIZED_SCHEMA = {
    "adapter": "validated_fund_nav_proxy_v1",
    "fields": [
        "fund_code",
        "region",
        "nav_date",
        "nav",
        "source_batch_id",
        "source_content_sha256",
        "visibility_status",
        "first_seen_at",
    ],
}
FUND_PROXY_NORMALIZED_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(_NORMALIZED_SCHEMA, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def load_fund_proxy_specs(path: str | Path) -> tuple[dict[str, str], ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_policy = payload.get("fund_proxy_policy")
    if not isinstance(raw_policy, Mapping) or raw_policy.get("enabled") is not True:
        raise ValueError("Fund-proxy publishing is not enabled in market-data config")
    raw_proxies = raw_policy.get("proxies")
    if not isinstance(raw_proxies, list) or not raw_proxies:
        raise ValueError("Fund-proxy policy requires a non-empty proxies list")
    required = {
        "region",
        "fund_code",
        "fund_name",
        "provider_id",
        "nav_field",
        "local_series_id",
        "proxy_role",
    }
    specs: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_proxies:
        if not isinstance(raw, Mapping) or any(not isinstance(raw.get(key), str) for key in required):
            raise ValueError("Fund-proxy policy contains an incomplete proxy")
        spec = {key: str(raw[key]) for key in required}
        if spec["nav_field"] not in {"unit_nav", "accumulated_nav"}:
            raise ValueError(f"Unsupported fund proxy NAV field: {spec['nav_field']}")
        if spec["local_series_id"] in seen:
            raise ValueError(f"Duplicate fund proxy series: {spec['local_series_id']}")
        seen.add(spec["local_series_id"])
        specs.append(spec)
    return tuple(specs)


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_rows(
    database: str | Path,
    *,
    spec: Mapping[str, str],
    as_of: datetime,
    history: bool,
) -> list[dict[str, object]]:
    nav_field = spec["nav_field"]
    connection = _connect_read_only(database)
    try:
        rows = connection.execute(
            f"""
            WITH ranked AS (
                SELECT observation.nav_date, observation.{nav_field} AS nav,
                       observation.visibility_status, observation.first_seen_at,
                       observation.announcement_at, observation.batch_id,
                       batch.content_sha256, batch.published_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY observation.nav_date
                           ORDER BY batch.published_at DESC, observation.batch_id DESC
                       ) AS rank_number
                FROM fund_nav_observations AS observation
                JOIN data_batches AS batch ON batch.batch_id = observation.batch_id
                WHERE observation.fund_code = ?
                  AND observation.provider_id = ?
                  AND observation.nav_date <= ?
                  AND observation.{nav_field} IS NOT NULL
                  AND batch.quality_status IN ('pass', 'partial')
            )
            SELECT nav_date, nav, visibility_status, first_seen_at, announcement_at,
                   batch_id, content_sha256
            FROM ranked
            WHERE rank_number = 1
            ORDER BY nav_date
            """,
            (spec["fund_code"], spec["provider_id"], as_of.date().isoformat()),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError(f"No validated NAV data for proxy fund {spec['fund_code']}")
    selected = rows if history else rows[-1:]
    return [dict(row) for row in selected]


def build_fund_proxy_payload(
    database: str | Path,
    *,
    specs: Sequence[Mapping[str, str]],
    as_of: datetime,
    history: bool,
) -> bytes:
    proxies: list[dict[str, object]] = []
    for spec in specs:
        proxies.append(
            {
                "spec": dict(sorted(spec.items())),
                "rows": _source_rows(database, spec=spec, as_of=as_of, history=history),
            }
        )
    payload = {
        "schema_version": 1,
        "kind": "derived_from_validated_local_fund_store",
        "history": history,
        "as_of": as_of.isoformat(),
        "proxies": proxies,
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _published_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return date.fromisoformat(str(value)[:10])


def normalize_fund_proxy_payload(
    *,
    batch_id: str,
    payload: bytes,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    decoded = json.loads(payload)
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
        raise ValueError("Unsupported fund-proxy payload schema")
    raw_proxies = decoded.get("proxies")
    if not isinstance(raw_proxies, list) or not raw_proxies:
        raise ValueError("Fund-proxy payload has no proxies")
    observations: list[MarketNumericObservation] = []
    providers: set[str] = set()
    regions: set[str] = set()
    for raw_proxy in raw_proxies:
        if not isinstance(raw_proxy, Mapping):
            raise ValueError("Fund-proxy entry must be an object")
        spec = raw_proxy.get("spec")
        rows = raw_proxy.get("rows")
        if not isinstance(spec, Mapping) or not isinstance(rows, list) or not rows:
            raise ValueError("Fund-proxy entry requires spec and rows")
        provider_id = str(spec.get("provider_id", ""))
        providers.add(provider_id)
        regions.add(str(spec.get("region", "")))
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("Fund-proxy row must be an object")
            visibility = VisibilityStatus(str(raw.get("visibility_status")))
            first_seen = None
            if visibility is VisibilityStatus.STRICT_POINT_IN_TIME:
                if raw.get("first_seen_at") is None:
                    raise ValueError("Strict fund-proxy row lacks first_seen_at")
                first_seen = datetime.fromisoformat(str(raw["first_seen_at"]))
                if first_seen.tzinfo is None:
                    raise ValueError("Fund-proxy first_seen_at requires timezone")
            observations.append(
                MarketNumericObservation(
                    series_id=str(spec.get("local_series_id")),
                    observation_date=date.fromisoformat(str(raw.get("nav_date"))),
                    value=Decimal(str(raw.get("nav"))),
                    unit="CNY_nav",
                    frequency="fund_nav_day",
                    label=f"{spec.get('fund_name')}（{spec.get('proxy_role')}代理）",
                    published_date=_published_date(raw.get("announcement_at")),
                    first_seen_at=first_seen,
                    visibility_status=visibility,
                    attributes={
                        "region": spec.get("region"),
                        "fund_code": spec.get("fund_code"),
                        "fund_name": spec.get("fund_name"),
                        "nav_field": spec.get("nav_field"),
                        "proxy_role": spec.get("proxy_role"),
                        "source_provider_id": provider_id,
                        "source_batch_id": raw.get("batch_id"),
                        "source_content_sha256": raw.get("content_sha256"),
                        "proxy_limitations": [
                            "fund_tracking_error",
                            "fund_fees",
                            "fund_nav_publication_lag",
                            "qdii_fx_effect_when_applicable",
                        ],
                    },
                )
            )
    if len(providers) != 1 or not next(iter(providers)):
        raise ValueError("One fund-proxy batch must use exactly one source provider")
    issues = (
        QualityIssue(
            "fund_nav_used_as_market_proxy",
            "Investable fund NAVs are explicit market proxies, not official index levels",
            QualitySeverity.WARNING,
        ),
        QualityIssue(
            "fund_proxy_scope_limitations",
            "Proxy trends include tracking error, fees, NAV lag and QDII currency effects",
            QualitySeverity.WARNING,
        ),
    )
    return MarketDataBatch(
        provider_id=next(iter(providers)),
        batch_id=batch_id,
        tool_name="validated_fund_nav_proxy",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments={
            "history": bool(decoded.get("history")),
            "regions": sorted(regions),
        },
        raw_content_sha256=raw_content_sha256,
        schema_sha256=FUND_PROXY_NORMALIZED_SCHEMA_SHA256,
        numeric_observations=tuple(observations),
        quality_issues=issues,
    )
