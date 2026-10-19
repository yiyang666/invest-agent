"""Reviewed public daily ChiNext price-index close for private local display."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import subprocess
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import VisibilityStatus

from .contracts import MarketDataBatch, MarketNumericObservation


PROVIDER_ID = "cnindex_public_market"
SOURCE_PAGE = "https://www.cnindex.com.cn/module/index-detail.html?activeName=2&indexCode=399006"
ENDPOINT = "https://hq.cnindex.com.cn/market/market/getIndexDailyData"
INDEX_CODE = "399006"
INDEX_NAME = "创业板指"
SCHEMA_HASH = hashlib.sha256(b"cnindex_399006_daily_v1:timestamp,close,chg,percent").hexdigest()
TZ = ZoneInfo("Asia/Shanghai")


def fetch_cnindex_index_quotes(*, as_of: datetime, timeout_seconds: float = 20.0) -> tuple[bytes, str]:
    start = (as_of.astimezone(TZ).date() - timedelta(days=14)).isoformat()
    end = as_of.astimezone(TZ).date().isoformat()
    url = f"{ENDPOINT}?{urlencode({'indexCode': INDEX_CODE, 'startDate': start, 'endDate': end})}"
    completed = subprocess.run(
        ["curl", "--noproxy", "*", "--location", "--fail-with-body", "--silent",
         "--show-error", "--max-time", str(timeout_seconds), "--url", url],
        capture_output=True, check=False, timeout=timeout_seconds + 5,
    )
    if completed.returncode:
        raise OSError(f"CNIndex daily quote request failed ({completed.returncode})")
    return completed.stdout, url


def _decimal(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"CNIndex {field} missing")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CNIndex {field} invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"CNIndex {field} non-finite")
    return parsed


def normalize_cnindex_index_quotes(
    *, batch_id: str, payload: bytes, fetched_at: datetime, as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    if fetched_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("CNIndex timestamps require timezone")
    raw = json.loads(payload)
    if not isinstance(raw, dict) or raw.get("code") != 200:
        raise ValueError("CNIndex response is not successful")
    body = raw.get("data")
    if not isinstance(body, dict) or body.get("indexCode") != INDEX_CODE or body.get("indexName") != INDEX_NAME:
        raise ValueError("CNIndex index identity mismatch")
    fields = body.get("item")
    rows = body.get("data")
    if fields != ["timestamp", "current", "high", "open", "low", "close", "chg", "percent", "amount", "volume", "avg"]:
        raise ValueError("CNIndex daily quote schema changed")
    if not isinstance(rows, list) or not rows:
        raise ValueError("CNIndex daily quote has no rows")
    observations = []
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(fields) or not isinstance(row[0], int):
            raise ValueError("CNIndex daily quote row shape changed")
        observed_at = datetime.fromtimestamp(row[0] / 1000, timezone.utc).astimezone(TZ)
        if observed_at.date() > as_of.astimezone(TZ).date() or observed_at.date() in seen:
            raise ValueError("CNIndex daily quote duplicate or future date")
        seen.add(observed_at.date())
        close = _decimal(row[5], "close")
        change = _decimal(row[6], "change")
        pct = _decimal(row[7], "percent") * 100
        previous = close - change
        if close <= 0 or previous <= 0 or not -100 < pct < 100:
            raise ValueError("CNIndex daily quote value out of range")
        if abs((change / previous) * 100 - pct) > Decimal("0.05"):
            raise ValueError("CNIndex daily quote change disagrees with percent")
        attributes = {"source_page": SOURCE_PAGE, "source_owner": "Shenzhen Securities Information Co., Ltd.",
                      "usage_scope": "personal_research_local_only"}
        for field, value, unit, label in (
            ("close", close, "index_point", "收盘点位"),
            ("change_pct", pct, "%", "涨跌幅"),
        ):
            observations.append(MarketNumericObservation(
                series_id=f"index_quote:cnindex:{INDEX_CODE}:{field}",
                observation_date=observed_at.date(), value=value, unit=unit,
                frequency="business_daily", label=f"{INDEX_NAME}{label}",
                first_seen_at=fetched_at, visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
                attributes=attributes,
            ))
    return MarketDataBatch(
        provider_id=PROVIDER_ID, batch_id=batch_id, tool_name="cnindex_public_daily_index_quotes",
        fetched_at=fetched_at, as_of=as_of,
        request_arguments={"endpoint": ENDPOINT, "source_page": SOURCE_PAGE, "index_code": INDEX_CODE},
        raw_content_sha256=raw_content_sha256, schema_sha256=SCHEMA_HASH,
        numeric_observations=tuple(observations), quality_issues=(),
    )
