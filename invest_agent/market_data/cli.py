"""CLI for reviewed Guchacha probes and future validated collection."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import requests

from invest_agent.data.archive import archive_raw_payload, load_raw_payload

from .fred import (
    FRED_GRAPH_CSV_ENDPOINT,
    PROVIDER_ID as FRED_PROVIDER_ID,
    FredCsvClient,
    load_fred_policy,
    normalize_fred_csv,
)
from .fund_proxy import (
    build_fund_proxy_payload,
    load_fund_proxy_specs,
    normalize_fund_proxy_payload,
)
from .guchacha_breadth import (
    PROVIDER_ID as GUCHACHA_BREADTH_PROVIDER_ID,
    GuchachaBreadthClient,
    load_guchacha_breadth_policy,
    normalize_guchacha_breadth,
)
from .mcp_client import GuchachaMcpClient, PROVIDER_ID, decode_jsonrpc, extract_tool_result
from .normalize import normalize_guchacha_result
from .policy import load_tool_policy
from .sse_breadth import (
    PROVIDER_ID as SSE_BREADTH_PROVIDER_ID,
    SseBreadthClient,
    load_sse_breadth_policy,
    normalize_sse_breadth,
)
from .store import MarketDataStore


DEFAULT_CONFIG = Path("config/market_data_sync_v1.json")
DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_DATABASE = Path("data/private/invest_agent.sqlite3")
APPROVED_CANONICAL_TOOLS_SCHEMA_SHA256 = (
    "a0a5d1885e3f49434a23508d9d9942f117fea18ccbc53feeb391672d43d306b0"
)


def _as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed


def _arguments(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("--arguments must be one JSON object")
    return payload


def _safe_tool(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", value):
        raise ValueError("tool name contains unsafe characters")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect reviewed market-context data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("probe", "Archive one allowlisted MCP response without publishing it"),
        ("collect", "Archive, normalize, validate, and publish one MCP response"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--tool", required=True)
        command.add_argument("--arguments", default="{}")
        command.add_argument("--as-of")
        command.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        command.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
        command.add_argument("--timeout-seconds", type=float, default=30.0)
        if name == "collect":
            command.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    audit = subparsers.add_parser(
        "audit-schema", help="Archive tools/list and fail if the reviewed schema changed"
    )
    audit.add_argument("--as-of")
    audit.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    audit.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    audit.add_argument("--timeout-seconds", type=float, default=30.0)

    replay = subparsers.add_parser("replay", help="Re-normalize an immutable raw MCP batch")
    replay.add_argument("--batch-id", required=True)
    replay.add_argument("--as-of", required=True)
    replay.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    replay.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    fred = subparsers.add_parser(
        "collect-fred", help="Archive and publish one reviewed FRED CSV series"
    )
    fred.add_argument("--series", required=True)
    fred.add_argument("--lookback-days", type=int, default=120)
    fred.add_argument("--as-of")
    fred.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    fred.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    fred.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    fred.add_argument("--timeout-seconds", type=float, default=20.0)

    replay_fred = subparsers.add_parser(
        "replay-fred", help="Re-normalize an immutable raw FRED CSV batch"
    )
    replay_fred.add_argument("--batch-id", required=True)
    replay_fred.add_argument("--as-of", required=True)
    replay_fred.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay_fred.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    replay_fred.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    proxy = subparsers.add_parser(
        "publish-fund-proxies",
        help="Archive and publish reviewed fund-NAV market proxies",
    )
    proxy.add_argument("--history", action="store_true")
    proxy.add_argument("--as-of")
    proxy.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    proxy.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    proxy.add_argument("--fund-db", type=Path, default=DEFAULT_DATABASE)
    proxy.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    replay_proxy = subparsers.add_parser(
        "replay-fund-proxies",
        help="Re-normalize an immutable fund-proxy batch",
    )
    replay_proxy.add_argument("--batch-id", required=True)
    replay_proxy.add_argument("--as-of", required=True)
    replay_proxy.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay_proxy.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    replay_proxy.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    sse_breadth = subparsers.add_parser(
        "collect-sse-breadth",
        help="Archive and publish the reviewed SSE end-of-day breadth snapshot",
    )
    sse_breadth.add_argument("--as-of")
    sse_breadth.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sse_breadth.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    sse_breadth.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    sse_breadth.add_argument("--timeout-seconds", type=float, default=20.0)

    replay_sse_breadth = subparsers.add_parser(
        "replay-sse-breadth",
        help="Re-normalize an immutable SSE breadth batch",
    )
    replay_sse_breadth.add_argument("--batch-id", required=True)
    replay_sse_breadth.add_argument("--as-of", required=True)
    replay_sse_breadth.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay_sse_breadth.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    replay_sse_breadth.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    guchacha_breadth = subparsers.add_parser(
        "collect-guchacha-breadth",
        help="Archive and publish Guchacha's public all-A-share breadth summary",
    )
    guchacha_breadth.add_argument("--as-of")
    guchacha_breadth.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    guchacha_breadth.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    guchacha_breadth.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    guchacha_breadth.add_argument("--timeout-seconds", type=float, default=20.0)

    replay_guchacha_breadth = subparsers.add_parser(
        "replay-guchacha-breadth",
        help="Re-normalize an immutable Guchacha dashboard breadth batch",
    )
    replay_guchacha_breadth.add_argument("--batch-id", required=True)
    replay_guchacha_breadth.add_argument("--as-of", required=True)
    replay_guchacha_breadth.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    replay_guchacha_breadth.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    replay_guchacha_breadth.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    init_store = subparsers.add_parser("init-store", help="Create isolated market-data tables")
    init_store.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    inspect = subparsers.add_parser("inspect", help="Inspect market batches and series coverage")
    inspect.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    return parser


def canonical_tools_schema_sha256(message: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    result = message.get("result")
    if not isinstance(result, dict):
        raise ValueError("MCP tools/list response has no result object")
    tools = result.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("MCP tools/list response has no tools")
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError("MCP tools/list contains an invalid tool definition")
        names.append(tool["name"])
    canonical = json.dumps(
        tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), tuple(names)


def _published_summary(result, *, source: str) -> tuple[dict[str, object], int]:
    summary = {
        "status": "published" if result.quality_report.can_publish else "rejected",
        "batch_id": result.batch_id,
        "source": source,
        "content_sha256": result.content_sha256,
        "quality_status": result.quality_report.status.value,
        "quality_issues": [issue.to_dict() for issue in result.quality_report.issues],
        "published_numeric_records": result.published_numeric_records,
        "published_weight_records": result.published_weight_records,
        "published_catalog_records": result.published_catalog_records,
        "idempotent_replay": result.idempotent_replay,
    }
    return summary, 0 if result.quality_report.can_publish else 2


def _publish_payload(
    *,
    batch_id: str,
    tool: str,
    arguments: dict[str, object],
    payload: bytes,
    content_type: str,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
    db: Path,
) -> tuple[dict[str, object], int]:
    message = decode_jsonrpc(payload, content_type)
    result = extract_tool_result(message)
    batch = normalize_guchacha_result(
        batch_id=batch_id,
        tool_name=tool,
        arguments=arguments,
        result=result,
        fetched_at=fetched_at,
        as_of=as_of,
        raw_content_sha256=raw_content_sha256,
    )
    published = MarketDataStore(db).publish(batch)
    summary: dict[str, object] = {
        "status": "published" if published.quality_report.can_publish else "rejected",
        "batch_id": published.batch_id,
        "tool": tool,
        "content_sha256": published.content_sha256,
        "quality_status": published.quality_report.status.value,
        "quality_issues": [issue.to_dict() for issue in published.quality_report.issues],
        "published_numeric_records": published.published_numeric_records,
        "published_weight_records": published.published_weight_records,
        "published_catalog_records": published.published_catalog_records,
        "idempotent_replay": published.idempotent_replay,
    }
    return summary, 0 if published.quality_report.can_publish else 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init-store":
            MarketDataStore(args.db).initialize()
            print(json.dumps({"status": "initialized", "database": str(args.db)}, ensure_ascii=False))
            return 0
        if args.command == "inspect":
            store = MarketDataStore(args.db)
            print(
                json.dumps(
                    {
                        "database": str(args.db),
                        "batches": [dict(row) for row in store.iter_batches()],
                        "series": [dict(row) for row in store.series_summary()],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "collect-fred":
            as_of = _as_of(args.as_of)
            if args.lookback_days < 0:
                raise ValueError("--lookback-days cannot be negative")
            policy = load_fred_policy(args.config, args.series)
            start_date = (
                None if args.lookback_days == 0 else as_of.date() - timedelta(days=args.lookback_days)
            )
            raw = FredCsvClient(timeout_seconds=args.timeout_seconds).fetch(
                series_id=policy.source_series_id,
                fetched_at=datetime.now(ZoneInfo("Asia/Shanghai")),
                start_date=start_date,
            )
            suffix = hashlib.sha256(raw.payload).hexdigest()[:12]
            stamp = raw.fetched_at.strftime("%Y%m%dT%H%M%S")
            batch_id = f"fred-{stamp}-{policy.source_series_id.lower()}-{suffix}"
            receipt = archive_raw_payload(
                root=args.raw_root,
                provider_id=FRED_PROVIDER_ID,
                batch_id=batch_id,
                payload=raw.payload,
                content_type=raw.content_type,
                observed_at=raw.fetched_at,
                request_parameters={
                    "endpoint": FRED_GRAPH_CSV_ENDPOINT,
                    "series_id": policy.source_series_id,
                    "start_date": start_date.isoformat() if start_date else "",
                    "as_of": as_of.isoformat(),
                },
            )
            batch = normalize_fred_csv(
                batch_id=batch_id,
                payload=raw.payload,
                policy=policy,
                fetched_at=raw.fetched_at,
                as_of=as_of,
                raw_content_sha256=receipt.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source=policy.source_series_id
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "replay-fred":
            as_of = _as_of(args.as_of)
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id=FRED_PROVIDER_ID,
                batch_id=args.batch_id,
            )
            series_id = archived.request_parameters.get("series_id", "")
            policy = load_fred_policy(args.config, series_id)
            batch = normalize_fred_csv(
                batch_id=archived.batch_id,
                payload=archived.payload,
                policy=policy,
                fetched_at=archived.observed_at,
                as_of=as_of,
                raw_content_sha256=archived.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source=series_id
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "publish-fund-proxies":
            as_of = _as_of(args.as_of)
            fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            specs = load_fund_proxy_specs(args.config)
            payload = build_fund_proxy_payload(
                args.fund_db,
                specs=specs,
                as_of=as_of,
                history=args.history,
            )
            suffix = hashlib.sha256(payload).hexdigest()[:12]
            stamp = fetched_at.strftime("%Y%m%dT%H%M%S")
            batch_id = f"fund-proxy-{stamp}-{suffix}"
            source_providers = sorted({spec["provider_id"] for spec in specs})
            if len(source_providers) != 1:
                raise ValueError("Configured fund proxies must use one source provider per batch")
            receipt = archive_raw_payload(
                root=args.raw_root,
                provider_id=source_providers[0],
                batch_id=batch_id,
                payload=payload,
                content_type="application/json",
                observed_at=fetched_at,
                request_parameters={
                    "operation": "validated_fund_nav_proxy",
                    "history": str(bool(args.history)).lower(),
                    "as_of": as_of.isoformat(),
                },
            )
            batch = normalize_fund_proxy_payload(
                batch_id=batch_id,
                payload=payload,
                fetched_at=fetched_at,
                as_of=as_of,
                raw_content_sha256=receipt.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="validated_fund_nav_proxy"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "replay-fund-proxies":
            as_of = _as_of(args.as_of)
            batch_id = args.batch_id
            providers = sorted(
                {spec["provider_id"] for spec in load_fund_proxy_specs(args.config)}
            )
            if len(providers) != 1:
                raise ValueError("Configured fund proxies must use one source provider per batch")
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id=providers[0],
                batch_id=batch_id,
            )
            batch = normalize_fund_proxy_payload(
                batch_id=batch_id,
                payload=archived.payload,
                fetched_at=archived.observed_at,
                as_of=as_of,
                raw_content_sha256=archived.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="validated_fund_nav_proxy"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "collect-sse-breadth":
            as_of = _as_of(args.as_of)
            fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            policy = load_sse_breadth_policy(args.config)
            raw = SseBreadthClient(timeout_seconds=args.timeout_seconds).fetch(
                policy=policy,
                fetched_at=fetched_at,
            )
            suffix = hashlib.sha256(raw.payload).hexdigest()[:12]
            stamp = raw.fetched_at.strftime("%Y%m%dT%H%M%S")
            batch_id = f"sse-breadth-{stamp}-{suffix}"
            receipt = archive_raw_payload(
                root=args.raw_root,
                provider_id=SSE_BREADTH_PROVIDER_ID,
                batch_id=batch_id,
                payload=raw.payload,
                content_type=raw.content_type,
                observed_at=raw.fetched_at,
                request_parameters={
                    "endpoint": policy.endpoint,
                    "source_page": policy.source_page,
                    "universe": policy.universe,
                    "as_of": as_of.isoformat(),
                },
            )
            batch = normalize_sse_breadth(
                batch_id=batch_id,
                payload=raw.payload,
                policy=policy,
                fetched_at=raw.fetched_at,
                as_of=as_of,
                raw_content_sha256=receipt.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="SSE public market"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "replay-sse-breadth":
            as_of = _as_of(args.as_of)
            policy = load_sse_breadth_policy(args.config)
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id=SSE_BREADTH_PROVIDER_ID,
                batch_id=args.batch_id,
            )
            batch = normalize_sse_breadth(
                batch_id=archived.batch_id,
                payload=archived.payload,
                policy=policy,
                fetched_at=archived.observed_at,
                as_of=as_of,
                raw_content_sha256=archived.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="SSE public market"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "collect-guchacha-breadth":
            as_of = _as_of(args.as_of)
            fetched_at = datetime.now(ZoneInfo("Asia/Shanghai"))
            policy = load_guchacha_breadth_policy(args.config)
            raw = GuchachaBreadthClient(timeout_seconds=args.timeout_seconds).fetch(
                policy=policy,
                fetched_at=fetched_at,
            )
            suffix = hashlib.sha256(raw.payload).hexdigest()[:12]
            stamp = raw.fetched_at.strftime("%Y%m%dT%H%M%S")
            batch_id = f"gcc-breadth-{stamp}-{suffix}"
            receipt = archive_raw_payload(
                root=args.raw_root,
                provider_id=GUCHACHA_BREADTH_PROVIDER_ID,
                batch_id=batch_id,
                payload=raw.payload,
                content_type=raw.content_type,
                observed_at=raw.fetched_at,
                request_parameters={
                    "endpoint": policy.endpoint,
                    "transport": "public_server_rendered_html",
                    "universe": policy.universe,
                    "as_of": as_of.isoformat(),
                },
            )
            batch = normalize_guchacha_breadth(
                batch_id=batch_id,
                payload=raw.payload,
                policy=policy,
                fetched_at=raw.fetched_at,
                as_of=as_of,
                raw_content_sha256=receipt.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="Guchacha public dashboard"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "replay-guchacha-breadth":
            as_of = _as_of(args.as_of)
            policy = load_guchacha_breadth_policy(args.config)
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id=GUCHACHA_BREADTH_PROVIDER_ID,
                batch_id=args.batch_id,
            )
            batch = normalize_guchacha_breadth(
                batch_id=archived.batch_id,
                payload=archived.payload,
                policy=policy,
                fetched_at=archived.observed_at,
                as_of=as_of,
                raw_content_sha256=archived.content_sha256,
            )
            summary, code = _published_summary(
                MarketDataStore(args.db).publish(batch), source="Guchacha public dashboard"
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code
        if args.command == "replay":
            as_of = _as_of(args.as_of)
            archived = load_raw_payload(
                root=args.raw_root, provider_id=PROVIDER_ID, batch_id=args.batch_id
            )
            method = archived.request_parameters.get("jsonrpc_method", "")
            if not method.startswith("tools/call:"):
                raise ValueError("Archived batch is not a Guchacha tool call")
            tool = _safe_tool(method.split(":", 1)[1])
            arguments = _arguments(archived.request_parameters.get("arguments_json", "{}"))
            load_tool_policy(args.config).validate(tool, arguments)
            summary, code = _publish_payload(
                batch_id=archived.batch_id,
                tool=tool,
                arguments=arguments,
                payload=archived.payload,
                content_type=archived.content_type,
                fetched_at=archived.observed_at,
                as_of=as_of,
                raw_content_sha256=archived.content_sha256,
                db=args.db,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return code

        if args.command == "audit-schema":
            policy = load_tool_policy(args.config)
            as_of = _as_of(args.as_of)
            raw = GuchachaMcpClient(timeout_seconds=args.timeout_seconds).list_tools()
            suffix = hashlib.sha256(raw.payload).hexdigest()[:12]
            stamp = raw.fetched_at.strftime("%Y%m%dT%H%M%S")
            batch_id = f"gcc-{stamp}-tools-list-{suffix}"
            receipt = archive_raw_payload(
                root=args.raw_root,
                provider_id=PROVIDER_ID,
                batch_id=batch_id,
                payload=raw.payload,
                content_type=raw.content_type,
                observed_at=raw.fetched_at,
                request_parameters={
                    "endpoint": "https://guchacha.com/mcp",
                    "jsonrpc_method": "tools/list",
                    "protocol_version": "2025-06-18",
                    "as_of": as_of.isoformat(),
                },
            )
            message = decode_jsonrpc(raw.payload, raw.content_type)
            schema_hash, tool_names = canonical_tools_schema_sha256(message)
            missing_enabled = sorted(set(policy.enabled_tools) - set(tool_names))
            approved = (
                schema_hash == APPROVED_CANONICAL_TOOLS_SCHEMA_SHA256
                and not missing_enabled
            )
            print(
                json.dumps(
                    {
                        "status": "schema_approved" if approved else "schema_drift",
                        "batch_id": batch_id,
                        "raw_content_sha256": receipt.content_sha256,
                        "canonical_schema_sha256": schema_hash,
                        "tool_count": len(tool_names),
                        "missing_enabled_tools": missing_enabled,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0 if approved else 2

        tool = _safe_tool(args.tool)
        arguments = _arguments(args.arguments)
        policy = load_tool_policy(args.config)
        policy.validate(tool, arguments)
        as_of = _as_of(args.as_of)
        client = GuchachaMcpClient(timeout_seconds=args.timeout_seconds)
        raw = client.call_tool(tool, arguments)
        suffix = hashlib.sha256(raw.payload).hexdigest()[:12]
        stamp = raw.fetched_at.strftime("%Y%m%dT%H%M%S")
        batch_id = f"gcc-{stamp}-{tool.replace('_', '-')}-{suffix}"
        receipt = archive_raw_payload(
            root=args.raw_root,
            provider_id=PROVIDER_ID,
            batch_id=batch_id,
            payload=raw.payload,
            content_type=raw.content_type,
            observed_at=raw.fetched_at,
            request_parameters={
                "endpoint": "https://guchacha.com/mcp",
                "jsonrpc_method": f"tools/call:{tool}",
                "protocol_version": "2025-06-18",
                "arguments_json": json.dumps(
                    arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                "as_of": as_of.isoformat(),
            },
        )
        if args.command == "probe":
            message = decode_jsonrpc(raw.payload, raw.content_type)
            result = extract_tool_result(message)
            summary = {
                "status": "archived_discovery_only",
                "batch_id": batch_id,
                "tool": tool,
                "content_sha256": receipt.content_sha256,
                "observed_at": raw.fetched_at.isoformat(),
                "as_of": as_of.isoformat(),
                "result_type": type(result).__name__,
                "published_records": 0,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        summary, code = _publish_payload(
            batch_id=batch_id,
            tool=tool,
            arguments=arguments,
            payload=raw.payload,
            content_type=raw.content_type,
            fetched_at=raw.fetched_at,
            as_of=as_of,
            raw_content_sha256=receipt.content_sha256,
            db=args.db,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return code
    except (OSError, ValueError, json.JSONDecodeError, requests.RequestException) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
