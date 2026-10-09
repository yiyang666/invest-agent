"""Incremental, raw-first fund-data synchronization orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from invest_agent.data.archive import archive_raw_payload
from invest_agent.data.contracts import NavRequest
from invest_agent.data.providers import AkshareEastmoneyNavProvider
from invest_agent.data.store import FundDataStore
from invest_agent.domain.fund_routes import load_purchase_route_pool
from invest_agent.domain.portfolio import FUND_CODE_PATTERN


@dataclass(frozen=True)
class SyncFund:
    fund_code: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IncrementalRequest:
    fund_code: str
    start_date: date
    end_date: date
    last_nav_before: date | None
    reasons: tuple[str, ...]
    history_mode: str


ProviderFactory = Callable[[float, int], Any]


def _resolve(root: Path, relative: object, *, field: str, must_exist: bool = True) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    result = (root / relative).resolve()
    workspace = root.resolve()
    if result != workspace and workspace not in result.parents:
        raise ValueError(f"{field} escapes workspace root")
    if must_exist and not result.exists():
        raise ValueError(f"{field} does not exist: {relative}")
    return result


def load_sync_config(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("fund data sync config schema_version must be 1")
    if payload.get("mode") != "collection_only":
        raise ValueError("fund data sync must remain collection_only")
    safety = payload.get("safety")
    if not isinstance(safety, Mapping) or safety != {
        "trading_allowed": False,
        "strategy_inference_allowed": False,
        "publish_failed_batches": False,
        "fallback_to_mock_data": False,
    }:
        raise ValueError("fund data sync safety contract is not exact")
    return payload


def _latest_snapshot(directory: Path) -> Mapping[str, Any] | None:
    if not directory.is_dir():
        return None
    candidates: list[tuple[datetime, Mapping[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            timestamp = datetime.fromisoformat(str(payload["as_of"]))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            continue
        if isinstance(payload, Mapping):
            candidates.append((timestamp, payload))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def resolve_sync_funds(config: Mapping[str, Any], *, workspace_root: Path) -> tuple[SyncFund, ...]:
    sources = config.get("fund_sources")
    if not isinstance(sources, Mapping):
        raise ValueError("fund data sync config requires fund_sources")
    reasons: dict[str, set[str]] = {}

    watch_catalog_path = sources.get("watch_catalog_path")
    if watch_catalog_path is not None:
        path = _resolve(
            workspace_root,
            watch_catalog_path,
            field="watch_catalog_path",
        )
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(catalog, Mapping) or catalog.get("schema_version") != 1:
            raise ValueError("fund watch catalog schema_version must be 1")
        classifications = catalog.get("classifications")
        if not isinstance(classifications, list):
            raise ValueError("fund watch catalog classifications must be a list")
        seen_catalog_codes: set[str] = set()
        for item in classifications:
            if not isinstance(item, Mapping):
                raise ValueError("fund watch catalog entries must be objects")
            code = str(item.get("fund_code", ""))
            if FUND_CODE_PATTERN.fullmatch(code) is None or code in seen_catalog_codes:
                raise ValueError(f"invalid or duplicate fund watch catalog code: {code!r}")
            if not all(isinstance(item.get(field), str) and item[field].strip()
                       for field in ("fund_name", "sleeve", "fund_type", "position_bucket", "basis")):
                raise ValueError(f"fund watch catalog entry is incomplete: {code}")
            if item["position_bucket"] not in {"核心仓", "防御仓", "卫星仓"}:
                raise ValueError(f"invalid fund watch position bucket: {code}")
            seen_catalog_codes.add(code)
            reasons.setdefault(code, set()).add("fund_watch_catalog")

    static_funds = sources.get("static_funds", [])
    if not isinstance(static_funds, list):
        raise ValueError("static_funds must be a list")
    for item in static_funds:
        if not isinstance(item, Mapping):
            raise ValueError("static fund entries must be objects")
        code = str(item.get("fund_code", ""))
        if FUND_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(f"static fund code must contain six digits: {code!r}")
        reasons.setdefault(code, set()).add(str(item.get("reason", "static_watchlist")))

    research_candidates = sources.get("research_candidate_funds", [])
    if not isinstance(research_candidates, list):
        raise ValueError("research_candidate_funds must be a list")
    for item in research_candidates:
        if not isinstance(item, Mapping):
            raise ValueError("research candidate entries must be objects")
        code = str(item.get("fund_code", ""))
        if FUND_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(
                f"research candidate fund code must contain six digits: {code!r}"
            )
        if item.get("current_channel_availability") not in {"open", "limited"}:
            raise ValueError(
                "research candidate funds require current-channel open or limited status"
            )
        reasons.setdefault(code, set()).add("tradable_research_candidate")

    research_pool_path = sources.get("global_qdii_research_pool_path")
    if research_pool_path is not None:
        path = _resolve(
            workspace_root,
            research_pool_path,
            field="global_qdii_research_pool_path",
        )
        research_pool = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(research_pool, Mapping) or research_pool.get("schema_version") != 1:
            raise ValueError("global QDII research pool schema_version must be 1")
        if research_pool.get("mode") != "research_only":
            raise ValueError("global QDII pool must remain research_only")
        pool_funds = research_pool.get("funds")
        if not isinstance(pool_funds, list):
            raise ValueError("global QDII research pool funds must be a list")
        if research_pool.get("candidate_count") != len(pool_funds):
            raise ValueError("global QDII candidate_count must match funds length")
        seen_pool_codes: set[str] = set()
        for item in pool_funds:
            if not isinstance(item, Mapping):
                raise ValueError("global QDII research pool entries must be objects")
            code = str(item.get("fund_code", ""))
            if FUND_CODE_PATTERN.fullmatch(code) is None:
                raise ValueError(
                    f"global QDII fund code must contain six digits: {code!r}"
                )
            if code in seen_pool_codes:
                raise ValueError(f"duplicate global QDII research fund: {code}")
            seen_pool_codes.add(code)
            if item.get("purchase_confirmed") is not True:
                raise ValueError(
                    f"global QDII research fund must be purchase-confirmed: {code}"
                )
            if item.get("availability") not in {"open", "limited"}:
                raise ValueError(
                    f"global QDII research fund must be open or limited: {code}"
                )
            tags = item.get("tags")
            if not isinstance(tags, list) or "QDII" not in tags:
                raise ValueError(f"global QDII research fund requires QDII tag: {code}")
            if not ({"LOF", "非LOF"} & set(map(str, tags))):
                raise ValueError(f"global QDII research fund requires LOF tag: {code}")
            reasons.setdefault(code, set()).add("global_qdii_research_pool")

    pool_path = _resolve(
        workspace_root,
        sources.get("purchase_route_pool_path"),
        field="purchase_route_pool_path",
    )
    pool = load_purchase_route_pool(pool_path)
    for code in pool.active_fund_codes():
        reasons.setdefault(code, set()).add("active_purchase_candidate")

    if sources.get("include_blocked_reference_routes") is not False:
        raise ValueError("daily sync must not include blocked/reference routes by default")

    snapshot_config = sources.get("latest_portfolio_snapshot")
    if not isinstance(snapshot_config, Mapping):
        raise ValueError("latest_portfolio_snapshot config is required")
    if snapshot_config.get("enabled") is True:
        directory = _resolve(
            workspace_root,
            snapshot_config.get("directory"),
            field="latest_portfolio_snapshot.directory",
            must_exist=False,
        )
        snapshot = _latest_snapshot(directory)
        if snapshot is not None:
            positions = snapshot.get("positions", [])
            if not isinstance(positions, list):
                raise ValueError("latest portfolio snapshot positions must be a list")
            for item in positions:
                if not isinstance(item, Mapping):
                    continue
                code = str(item.get("fund_code", ""))
                if FUND_CODE_PATTERN.fullmatch(code):
                    reasons.setdefault(code, set()).add("current_portfolio_position")

    return tuple(
        SyncFund(code, tuple(sorted(code_reasons)))
        for code, code_reasons in sorted(reasons.items())
    )


def build_incremental_requests(
    config: Mapping[str, Any],
    *,
    workspace_root: Path,
    as_of: datetime,
) -> tuple[IncrementalRequest, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("sync as_of must include a timezone")
    collection = config.get("collection")
    if not isinstance(collection, Mapping):
        raise ValueError("fund data sync config requires collection")
    provider_id = str(collection.get("provider_id", ""))
    if provider_id != "akshare_eastmoney":
        raise ValueError("only the reviewed akshare_eastmoney provider is supported")
    lookback_days = int(collection.get("overlap_calendar_days", 0))
    bootstrap_days = int(collection.get("bootstrap_calendar_days", 0))
    if collection.get("bootstrap_mode") != "bounded_recent_history":
        raise ValueError("bootstrap_mode must be bounded_recent_history")
    if lookback_days < 0 or bootstrap_days < 1:
        raise ValueError("overlap days must be non-negative and bootstrap days positive")
    database = _resolve(
        workspace_root,
        config.get("database_path"),
        field="database_path",
        must_exist=False,
    )
    store = FundDataStore(database)
    requests: list[IncrementalRequest] = []
    for fund in resolve_sync_funds(config, workspace_root=workspace_root):
        latest_text = store.latest_nav_date(
            fund_code=fund.fund_code, provider_id=provider_id
        )
        latest = date.fromisoformat(latest_text) if latest_text else None
        if latest is not None and latest > as_of.date():
            raise ValueError(f"stored NAV date is newer than sync as_of for {fund.fund_code}")
        if latest is not None:
            start = latest - timedelta(days=lookback_days)
            history_mode = "incremental_overlap"
        else:
            start = as_of.date() - timedelta(days=bootstrap_days)
            history_mode = "bounded_recent_bootstrap"
        requests.append(
            IncrementalRequest(
                fund_code=fund.fund_code,
                start_date=start,
                end_date=as_of.date(),
                last_nav_before=latest,
                reasons=fund.reasons,
                history_mode=history_mode,
            )
        )
    return tuple(requests)


def sync_plan_payload(
    config: Mapping[str, Any], *, workspace_root: Path, as_of: datetime
) -> dict[str, Any]:
    requests = build_incremental_requests(config, workspace_root=workspace_root, as_of=as_of)
    return {
        "schema_version": 1,
        "sync_id": str(config.get("sync_id", "")),
        "as_of": as_of.isoformat(),
        "mode": "collection_only",
        "requests": [
            {
                "fund_code": item.fund_code,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
                "last_nav_before": (
                    item.last_nav_before.isoformat() if item.last_nav_before else None
                ),
                "reasons": list(item.reasons),
                "history_mode": item.history_mode,
            }
            for item in requests
        ],
        "network_used": False,
        "trading_used": False,
    }


def _default_provider_factory(timeout_seconds: float, max_attempts: int) -> Any:
    return AkshareEastmoneyNavProvider(
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )


def summarize_sync_results(results: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
    """Keep collection success separate from the age of the published NAV."""
    published = sum(item["collection_status"] == "published" for item in results)
    summary = {
        "fund_count": len(results),
        "published": published,
        "rejected": sum(item["collection_status"] == "rejected" for item in results),
        "errors": sum(item["collection_status"] == "error" for item in results),
        "stale": sum(item.get("freshness") == "stale" for item in results),
        "fresh": sum(item.get("freshness") == "fresh" for item in results),
    }
    if results and published == len(results):
        status = "complete"
    elif published > 0:
        status = "degraded"
    else:
        status = "failed"
    return status, summary


def run_incremental_sync(
    config: Mapping[str, Any],
    *,
    workspace_root: Path,
    as_of: datetime,
    provider_factory: ProviderFactory = _default_provider_factory,
) -> dict[str, Any]:
    requests = build_incremental_requests(config, workspace_root=workspace_root, as_of=as_of)
    collection = config["collection"]
    assert isinstance(collection, Mapping)
    timeout_seconds = float(collection.get("timeout_seconds", 20))
    max_attempts = int(collection.get("max_attempts", 2))
    if timeout_seconds <= 0 or max_attempts < 1:
        raise ValueError("timeout_seconds and max_attempts must be positive")
    maximum_lag = int(collection.get("maximum_nav_lag_calendar_days", 5))
    if maximum_lag < 0:
        raise ValueError("maximum_nav_lag_calendar_days cannot be negative")
    database = _resolve(
        workspace_root, config.get("database_path"), field="database_path", must_exist=False
    )
    raw_root = _resolve(
        workspace_root, config.get("raw_root"), field="raw_root", must_exist=False
    )
    store = FundDataStore(database)
    provider = provider_factory(timeout_seconds, max_attempts)
    results: list[dict[str, Any]] = []
    for item in requests:
        try:
            request = NavRequest(
                fund_codes=(item.fund_code,),
                start_date=item.start_date,
                end_date=item.end_date,
                as_of=as_of,
            )
            raw_batch = provider.fetch_raw(request)
            archived = archive_raw_payload(
                root=raw_root,
                provider_id=raw_batch.provider_id,
                batch_id=raw_batch.batch_id,
                payload=raw_batch.payload,
                content_type=raw_batch.content_type,
                observed_at=raw_batch.fetched_at,
                request_parameters=raw_batch.request_parameters,
            )
            batch = provider.normalize(
                raw_batch,
                request,
                raw_content_sha256=archived.content_sha256,
            )
            published = store.publish_nav_batch(batch)
            latest_text = store.latest_nav_date(
                fund_code=item.fund_code,
                provider_id=str(collection["provider_id"]),
            )
            latest = date.fromisoformat(latest_text) if latest_text else None
            lag = (as_of.date() - latest).days if latest else None
            results.append(
                {
                    "fund_code": item.fund_code,
                    "reasons": list(item.reasons),
                    "request_start_date": item.start_date.isoformat(),
                    "request_end_date": item.end_date.isoformat(),
                    "last_nav_before": (
                        item.last_nav_before.isoformat() if item.last_nav_before else None
                    ),
                    "history_mode": item.history_mode,
                    "latest_nav_after": latest.isoformat() if latest else None,
                    "nav_lag_calendar_days": lag,
                    "freshness": (
                        "missing"
                        if lag is None
                        else "fresh" if lag <= maximum_lag else "stale"
                    ),
                    "batch_id": published.batch_id,
                    "content_sha256": published.content_sha256,
                    "quality_status": published.quality_report.status.value,
                    "published_records": published.published_records,
                    "quality_issues": [
                        issue.to_dict() for issue in published.quality_report.issues
                    ],
                    "collection_status": (
                        "published" if published.quality_report.can_publish else "rejected"
                    ),
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            results.append(
                {
                    "fund_code": item.fund_code,
                    "reasons": list(item.reasons),
                    "request_start_date": item.start_date.isoformat(),
                    "request_end_date": item.end_date.isoformat(),
                    "last_nav_before": (
                        item.last_nav_before.isoformat() if item.last_nav_before else None
                    ),
                    "history_mode": item.history_mode,
                    "latest_nav_after": None,
                    "freshness": "unknown",
                    "collection_status": "error",
                    "error": str(exc),
                }
            )

    status, summary = summarize_sync_results(results)
    config_hash = hashlib.sha256(
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "schema_version": 1,
        "sync_id": str(config.get("sync_id", "")),
        "run_id": f"fund-sync-{as_of:%Y%m%dT%H%M%S%z}",
        "as_of": as_of.isoformat(),
        "mode": "collection_only",
        "config_sha256": config_hash,
        "status": status,
        "summary": summary,
        "warnings": (["stale_nav_observations_present"] if summary["stale"] else []),
        "results": results,
        "network_used": True,
        "trading_used": False,
        "strategy_inference_used": False,
    }
