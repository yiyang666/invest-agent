"""Command-line entry point for local fund-data collection and inspection."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from invest_agent.data.archive import archive_raw_payload, load_raw_payload
from invest_agent.data.contracts import FundMetadataRequest, NavRequest, RawNavPayload
from invest_agent.data.providers import (
    AkshareEastmoneyNavProvider,
    AkshareThsDailyNavProvider,
    AkshareThsFundMetadataProvider,
    LocalCsvNavProvider,
    PenghuaOfficialNavProvider,
)
from invest_agent.data.store import FundDataStore
from invest_agent.data.providers.akshare_eastmoney import (
    AKSHARE_VERSION as EASTMONEY_AKSHARE_VERSION,
    SOURCE_DOMAIN as EASTMONEY_SOURCE_DOMAIN,
)
from invest_agent.data.providers.akshare_ths import (
    AKSHARE_VERSION as THS_AKSHARE_VERSION,
    SOURCE_DOMAIN as THS_SOURCE_DOMAIN,
)


DEFAULT_DATABASE = Path("data/private/invest_agent.sqlite3")
DEFAULT_RAW_ROOT = Path("data/raw/fund_batches")


def _parse_as_of(value: str | None) -> datetime:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset, for example +08:00")
    return parsed


def _result_payload(result: object) -> dict[str, object]:
    quality_report = result.quality_report
    return {
        "batch_id": result.batch_id,
        "content_sha256": result.content_sha256,
        "quality_status": quality_report.status.value,
        "published_records": result.published_records,
        "idempotent_replay": result.idempotent_replay,
        "quality_issues": [issue.to_dict() for issue in quality_report.issues],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and validate local fund data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_store = subparsers.add_parser("init-store", help="Create the local SQLite schema")
    init_store.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    ingest_csv = subparsers.add_parser("ingest-csv", help="Archive and ingest an authorized CSV export")
    ingest_csv.add_argument("--csv", type=Path, required=True)
    ingest_csv.add_argument("--fund-code", action="append", required=True)
    ingest_csv.add_argument("--start-date", type=date.fromisoformat, required=True)
    ingest_csv.add_argument("--end-date", type=date.fromisoformat, required=True)
    ingest_csv.add_argument("--as-of")
    ingest_csv.add_argument("--source-label", default="authorized_export")
    ingest_csv.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    ingest_csv.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    collect_eastmoney = subparsers.add_parser(
        "collect-akshare-eastmoney",
        help="Archive, normalize, and ingest Eastmoney fund NAV history",
    )
    collect_eastmoney.add_argument("--fund-code", action="append", required=True)
    collect_eastmoney.add_argument("--start-date", type=date.fromisoformat, required=True)
    collect_eastmoney.add_argument("--end-date", type=date.fromisoformat, required=True)
    collect_eastmoney.add_argument("--as-of")
    collect_eastmoney.add_argument("--timeout-seconds", type=float, default=20.0)
    collect_eastmoney.add_argument("--max-attempts", type=int, default=2)
    collect_eastmoney.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    collect_eastmoney.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    collect_penghua = subparsers.add_parser(
        "collect-penghua-official",
        help="Archive, normalize, and ingest official Penghua fund NAV history",
    )
    collect_penghua.add_argument("--fund-code", action="append", required=True)
    collect_penghua.add_argument("--start-date", type=date.fromisoformat, required=True)
    collect_penghua.add_argument("--end-date", type=date.fromisoformat, required=True)
    collect_penghua.add_argument("--as-of")
    collect_penghua.add_argument("--timeout-seconds", type=float, default=20.0)
    collect_penghua.add_argument("--max-attempts", type=int, default=2)
    collect_penghua.add_argument("--max-pages", type=int, default=100)
    collect_penghua.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    collect_penghua.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    collect_ths_metadata = subparsers.add_parser(
        "collect-akshare-ths-metadata",
        help="Archive, normalize, and ingest THS fund metadata",
    )
    collect_ths_metadata.add_argument("--fund-code", action="append", required=True)
    collect_ths_metadata.add_argument("--as-of")
    collect_ths_metadata.add_argument("--timeout-seconds", type=float, default=20.0)
    collect_ths_metadata.add_argument("--max-attempts", type=int, default=2)
    collect_ths_metadata.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    collect_ths_metadata.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    collect_ths_daily = subparsers.add_parser(
        "collect-akshare-ths-daily",
        help="Archive, normalize, and ingest explicit-date THS daily NAV snapshots",
    )
    collect_ths_daily.add_argument("--fund-code", action="append", required=True)
    collect_ths_daily.add_argument("--start-date", type=date.fromisoformat, required=True)
    collect_ths_daily.add_argument("--end-date", type=date.fromisoformat, required=True)
    collect_ths_daily.add_argument("--as-of")
    collect_ths_daily.add_argument("--timeout-seconds", type=float, default=20.0)
    collect_ths_daily.add_argument("--max-attempts", type=int, default=2)
    collect_ths_daily.add_argument("--max-query-dates", type=int, default=31)
    collect_ths_daily.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    collect_ths_daily.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    replay_ths_daily = subparsers.add_parser(
        "replay-akshare-ths-daily",
        help="Re-normalize and publish an already archived THS daily NAV batch",
    )
    replay_ths_daily.add_argument("--batch-id", required=True)
    replay_ths_daily.add_argument("--as-of", required=True)
    replay_ths_daily.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    replay_ths_daily.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)

    replay_eastmoney_distributions = subparsers.add_parser(
        "replay-akshare-eastmoney-distributions",
        help="Extract and publish cash distributions from an archived Eastmoney NAV batch",
    )
    replay_eastmoney_distributions.add_argument("--batch-id", required=True)
    replay_eastmoney_distributions.add_argument("--as-of", required=True)
    replay_eastmoney_distributions.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    replay_eastmoney_distributions.add_argument(
        "--raw-root", type=Path, default=DEFAULT_RAW_ROOT
    )

    inspect_store = subparsers.add_parser("inspect", help="List immutable batch summaries")
    inspect_store.add_argument("--db", type=Path, default=DEFAULT_DATABASE)

    crosscheck = subparsers.add_parser(
        "crosscheck-nav", help="Compare two stored NAV sources without selecting a winner"
    )
    crosscheck.add_argument("--left-provider", default="akshare_eastmoney")
    crosscheck.add_argument("--right-provider", default="akshare_ths_daily")
    crosscheck.add_argument("--fund-code", action="append", default=[])
    crosscheck.add_argument("--start-date", type=date.fromisoformat)
    crosscheck.add_argument("--end-date", type=date.fromisoformat)
    crosscheck.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = FundDataStore(args.db)

    if args.command == "init-store":
        store.initialize()
        print(json.dumps({"database": str(args.db), "status": "initialized"}, ensure_ascii=False))
        return 0

    if args.command == "inspect":
        rows = [dict(row) for row in store.iter_batches()]
        distribution_rows = [dict(row) for row in store.iter_distribution_batches()]
        print(
            json.dumps(
                {
                    "database": str(args.db),
                    "batches": rows,
                    "distribution_batches": distribution_rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "crosscheck-nav":
        report = store.compare_nav_sources(
            left_provider=args.left_provider,
            right_provider=args.right_provider,
            fund_codes=tuple(args.fund_code),
            start_date=args.start_date.isoformat() if args.start_date else None,
            end_date=args.end_date.isoformat() if args.end_date else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    try:
        as_of = _parse_as_of(args.as_of)
        if args.command == "replay-akshare-eastmoney-distributions":
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id="akshare_eastmoney",
                batch_id=args.batch_id,
            )
            parameters = archived.request_parameters
            request = NavRequest(
                fund_codes=tuple(parameters["fund_codes"].split(",")),
                start_date=date.fromisoformat(parameters["start_date"]),
                end_date=date.fromisoformat(parameters["end_date"]),
                as_of=as_of,
            )
            raw_batch = RawNavPayload(
                provider_id=archived.provider_id,
                batch_id=archived.batch_id,
                fetched_at=archived.observed_at,
                payload=archived.payload,
                content_type=archived.content_type,
                provenance=(
                    f"akshare=={EASTMONEY_AKSHARE_VERSION}:"
                    "fund_open_fund_info_em-compatible"
                ),
                source_domain=EASTMONEY_SOURCE_DOMAIN,
                request_parameters=archived.request_parameters,
            )
            provider = AkshareEastmoneyNavProvider()
            batch = provider.normalize_distributions(
                raw_batch,
                request,
                raw_content_sha256=archived.content_sha256,
            )
            result = store.publish_distribution_batch(batch)
        elif args.command == "replay-akshare-ths-daily":
            archived = load_raw_payload(
                root=args.raw_root,
                provider_id="akshare_ths_daily",
                batch_id=args.batch_id,
            )
            parameters = archived.request_parameters
            request = NavRequest(
                fund_codes=tuple(parameters["fund_codes"].split(",")),
                start_date=date.fromisoformat(parameters["start_date"]),
                end_date=date.fromisoformat(parameters["end_date"]),
                as_of=as_of,
            )
            raw_batch = RawNavPayload(
                provider_id=archived.provider_id,
                batch_id=archived.batch_id,
                fetched_at=archived.observed_at,
                payload=archived.payload,
                content_type=archived.content_type,
                provenance=(
                    f"akshare=={THS_AKSHARE_VERSION}:"
                    "fund_etf_category_ths(symbol='')-compatible"
                ),
                source_domain=THS_SOURCE_DOMAIN,
                request_parameters=archived.request_parameters,
            )
            provider = AkshareThsDailyNavProvider()
            batch = provider.normalize(
                raw_batch,
                request,
                raw_content_sha256=archived.content_sha256,
            )
            result = store.publish_nav_batch(batch)
        elif args.command == "collect-akshare-ths-metadata":
            metadata_request = FundMetadataRequest(
                fund_codes=tuple(args.fund_code),
                as_of=as_of,
            )
            metadata_provider = AkshareThsFundMetadataProvider(
                timeout_seconds=args.timeout_seconds,
                max_attempts=args.max_attempts,
            )
            raw_batch = metadata_provider.fetch_raw(metadata_request)
            raw = archive_raw_payload(
                root=args.raw_root,
                provider_id=raw_batch.provider_id,
                batch_id=raw_batch.batch_id,
                payload=raw_batch.payload,
                content_type=raw_batch.content_type,
                observed_at=raw_batch.fetched_at,
                request_parameters=raw_batch.request_parameters,
            )
            batch = metadata_provider.normalize(
                raw_batch,
                metadata_request,
                raw_content_sha256=raw.content_sha256,
            )
            result = store.publish_fund_metadata_batch(batch)
        else:
            request = NavRequest(
                fund_codes=tuple(args.fund_code),
                start_date=args.start_date,
                end_date=args.end_date,
                as_of=as_of,
            )
            if args.command == "ingest-csv":
                provider = LocalCsvNavProvider(args.csv, source_label=args.source_label)
            elif args.command == "collect-akshare-eastmoney":
                provider = AkshareEastmoneyNavProvider(
                    timeout_seconds=args.timeout_seconds,
                    max_attempts=args.max_attempts,
                )
            elif args.command == "collect-akshare-ths-daily":
                provider = AkshareThsDailyNavProvider(
                    timeout_seconds=args.timeout_seconds,
                    max_attempts=args.max_attempts,
                    max_query_dates=args.max_query_dates,
                )
            elif args.command == "collect-penghua-official":
                provider = PenghuaOfficialNavProvider(
                    timeout_seconds=args.timeout_seconds,
                    max_attempts=args.max_attempts,
                    max_pages=args.max_pages,
                )
            else:  # guarded by argparse; keeps type narrowing explicit
                raise ValueError(f"Unsupported command: {args.command}")

            raw_batch = provider.fetch_raw(request)
            raw = archive_raw_payload(
                root=args.raw_root,
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
                raw_content_sha256=raw.content_sha256,
            )
            result = store.publish_nav_batch(batch)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
    return 0 if result.quality_report.can_publish else 2


if __name__ == "__main__":
    raise SystemExit(main())
