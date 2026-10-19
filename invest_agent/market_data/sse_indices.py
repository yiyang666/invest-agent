"""Reviewed SSE public index quotes for a private end-of-day dashboard."""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
import subprocess
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import VisibilityStatus

from .contracts import MarketDataBatch, MarketNumericObservation


PROVIDER_ID = "sse_public_market"
SOURCE_PAGE = "https://www.sse.com.cn/market/sseindex/quotation/index.shtml"
ENDPOINT = "https://yunhq.sse.com.cn:32042/v1/csip/list/self/000001_000680"
CALLBACK = "quoteCallback"
FIELDS = "prev_close,open,high,low,last,chg_rate,code,name"
INDEX_NAMES = {"000001": "上证指数", "000680": "科创综指"}
SCHEMA_HASH = hashlib.sha256(b"sse_index_quotes_v1:date,time,prev_close,last,chg_rate,code,name").hexdigest()


def fetch_sse_index_quotes(*, timeout_seconds: float = 20.0) -> tuple[bytes, str]:
    # The exchange's public index page uses this fixed endpoint and field list.
    url = f"{ENDPOINT}?{urlencode({'callback': CALLBACK, 'select': FIELDS})}"
    completed = subprocess.run(
        ["curl", "--noproxy", "*", "--location", "--fail-with-body", "--silent",
         "--show-error", "--max-time", str(timeout_seconds), "--header",
         f"Referer: {SOURCE_PAGE}", "--url", url],
        capture_output=True, check=False, timeout=timeout_seconds + 5,
    )
    if completed.returncode:
        raise OSError(f"SSE index quote request failed ({completed.returncode})")
    return completed.stdout, url


def _number(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"SSE index {field} missing")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"SSE index {field} invalid") from exc
    if not result.is_finite():
        raise ValueError(f"SSE index {field} non-finite")
    return result


def normalize_sse_index_quotes(
    *, batch_id: str, payload: bytes, fetched_at: datetime, as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    if fetched_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("SSE index quote timestamps require timezone")
    prefix = f"{CALLBACK}(".encode()
    if not payload.startswith(prefix) or not payload.rstrip().endswith(b")"):
        raise ValueError("SSE index quote response is not the expected JSONP")
    raw = json.loads(payload[len(prefix):payload.rfind(b")")])
    if not isinstance(raw, dict) or not isinstance(raw.get("date"), int) or not isinstance(raw.get("time"), int):
        raise ValueError("SSE index quote date/time missing")
    if raw.get("total") != len(INDEX_NAMES) or not isinstance(raw.get("list"), list) or len(raw["list"]) != len(INDEX_NAMES):
        raise ValueError("SSE index quote coverage incomplete")
    observed = datetime.strptime(str(raw["date"]), "%Y%m%d").date()
    hhmmss = f"{raw['time']:06d}"
    observed_at = datetime.combine(observed, time.fromisoformat(f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}"), ZoneInfo("Asia/Shanghai"))
    if observed_at > as_of or observed_at.time() < time(15, 0):
        raise ValueError("SSE index quote is future or not final")
    observations = []
    seen = set()
    for row in raw["list"]:
        if not isinstance(row, list) or len(row) != 8:
            raise ValueError("SSE index quote row shape changed")
        code, name = row[6], row[7]
        if code not in INDEX_NAMES or name != INDEX_NAMES[code] or code in seen:
            raise ValueError("SSE index quote identity mismatch")
        seen.add(code)
        previous = _number(row[0], "previous close")
        close = _number(row[4], "close")
        change_pct = _number(row[5], "change pct")
        if previous <= 0 or close <= 0 or not -100 < change_pct < 100:
            raise ValueError("SSE index quote value out of range")
        if abs((close / previous - 1) * 100 - change_pct) > Decimal("0.05"):
            raise ValueError("SSE index quote change does not match previous close")
        attributes = {"source_page": SOURCE_PAGE, "source_owner": "Shanghai Stock Exchange",
                      "usage_scope": "personal_research_local_only", "market_snapshot_time": observed_at.isoformat()}
        for field, value, unit, label in (
            ("close", close, "index_point", "收盘点位"),
            ("change_pct", change_pct, "%", "涨跌幅"),
        ):
            observations.append(MarketNumericObservation(
                series_id=f"index_quote:sse:{code}:{field}", observation_date=observed,
                value=value, unit=unit, frequency="business_daily", label=f"{name}{label}",
                first_seen_at=fetched_at, visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
                attributes=attributes,
            ))
    return MarketDataBatch(
        provider_id=PROVIDER_ID, batch_id=batch_id, tool_name="sse_public_eod_index_quotes",
        fetched_at=fetched_at, as_of=as_of,
        request_arguments={"endpoint": ENDPOINT, "source_page": SOURCE_PAGE, "codes": sorted(INDEX_NAMES)},
        raw_content_sha256=raw_content_sha256, schema_sha256=SCHEMA_HASH,
        numeric_observations=tuple(observations), quality_issues=(),
    )
