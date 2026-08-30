"""Raw-first Shanghai Stock Exchange end-of-day breadth adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
from typing import Mapping, Sequence
from urllib.parse import urlencode

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import MarketDataBatch, MarketNumericObservation


PROVIDER_ID = "sse_public_market"

_SELECT_FIELDS = (
    "code",
    "name",
    "last",
    "prev_close",
    "chg_rate",
    "change",
    "tradephase",
)
_NORMALIZED_SCHEMA = {
    "adapter": "sse_public_eod_breadth_v1",
    "source_fields": list(_SELECT_FIELDS),
    "derived_series": [
        "advancers",
        "decliners",
        "unchanged",
        "valid_count",
        "advance_share_pct",
        "decline_share_pct",
        "advance_decline_ratio",
        "equal_weight_return_pct",
        "median_return_pct",
    ],
    "universe": "SSE A shares including STAR Market; B shares excluded",
    "visibility": "strict_point_in_time_at_first_collection",
}
SSE_BREADTH_NORMALIZED_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(_NORMALIZED_SCHEMA, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class SseBreadthPolicy:
    endpoint: str
    source_page: str
    source_owner: str
    usage_scope: str
    universe: str
    max_records: int
    minimum_final_time: time


@dataclass(frozen=True)
class RawSseBreadthResponse:
    fetched_at: datetime
    payload: bytes
    content_type: str
    request_url: str


def load_sse_breadth_policy(path: str | Path) -> SseBreadthPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("sse_breadth_policy")
    if not isinstance(raw, Mapping) or raw.get("enabled") is not True:
        raise ValueError("SSE breadth collection is not enabled in market-data config")
    required = ("endpoint", "source_page", "source_owner", "usage_scope", "universe")
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise ValueError("SSE breadth policy is incomplete")
    if raw["usage_scope"] != "personal_research_local_only":
        raise ValueError(f"Unsupported SSE breadth usage scope: {raw['usage_scope']}")
    if raw["universe"] != "equity_excluding_b_shares":
        raise ValueError(f"Unsupported SSE breadth universe: {raw['universe']}")
    max_records = raw.get("max_records", 4000)
    if not isinstance(max_records, int) or not 2000 <= max_records <= 10000:
        raise ValueError("SSE breadth max_records must be an integer from 2000 to 10000")
    try:
        minimum_final_time = time.fromisoformat(str(raw.get("minimum_final_time", "15:00:00")))
    except ValueError as exc:
        raise ValueError("SSE breadth minimum_final_time must be an ISO time") from exc
    return SseBreadthPolicy(
        endpoint=str(raw["endpoint"]),
        source_page=str(raw["source_page"]),
        source_owner=str(raw["source_owner"]),
        usage_scope=str(raw["usage_scope"]),
        universe=str(raw["universe"]),
        max_records=max_records,
        minimum_final_time=minimum_final_time,
    )


class SseBreadthClient:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        policy: SseBreadthPolicy,
        fetched_at: datetime,
    ) -> RawSseBreadthResponse:
        params = {
            "select": ",".join(_SELECT_FIELDS),
            "begin": "0",
            "end": str(policy.max_records),
        }
        request_url = f"{policy.endpoint}?{urlencode(params)}"
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
                "--header",
                f"Referer: {policy.source_page}",
                "--url",
                request_url,
            ],
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(f"SSE breadth curl request failed ({completed.returncode}): {message}")
        return RawSseBreadthResponse(
            fetched_at=fetched_at,
            payload=completed.stdout,
            content_type="application/json",
            request_url=request_url,
        )


def _decimal(value: object, *, field: str, code: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing SSE {field} for {code}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid SSE {field} for {code}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Non-finite SSE {field} for {code}")
    return parsed


def _series(
    *,
    suffix: str,
    observed: date,
    value: Decimal,
    unit: str,
    label: str,
    fetched_at: datetime,
    attributes: Mapping[str, object],
) -> MarketNumericObservation:
    return MarketNumericObservation(
        series_id=f"breadth:china:sse:{suffix}",
        observation_date=observed,
        value=value,
        unit=unit,
        frequency="business_daily",
        label=label,
        first_seen_at=fetched_at,
        visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
        attributes=attributes,
    )


def normalize_sse_breadth(
    *,
    batch_id: str,
    payload: bytes,
    policy: SseBreadthPolicy,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    if fetched_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("SSE breadth timestamps require timezone")
    decoded = json.loads(payload)
    if not isinstance(decoded, Mapping):
        raise ValueError("SSE breadth response must be an object")
    raw_date = decoded.get("date")
    raw_time = decoded.get("time")
    raw_total = decoded.get("total")
    raw_rows = decoded.get("list")
    if not isinstance(raw_date, int) or not isinstance(raw_time, int):
        raise ValueError("SSE breadth response lacks integer date/time")
    if not isinstance(raw_total, int) or not isinstance(raw_rows, list):
        raise ValueError("SSE breadth response lacks total/list")
    observed = datetime.strptime(str(raw_date), "%Y%m%d").date()
    market_clock = f"{raw_time:06d}"
    market_time = time.fromisoformat(
        f"{market_clock[:2]}:{market_clock[2:4]}:{market_clock[4:]}"
    )
    if observed > as_of.date():
        raise ValueError("SSE breadth observation is after as_of")
    if market_time < policy.minimum_final_time:
        raise ValueError(
            f"SSE breadth snapshot is not final: {market_time.isoformat()} < {policy.minimum_final_time.isoformat()}"
        )
    if raw_total > policy.max_records or len(raw_rows) != raw_total:
        raise ValueError(
            f"SSE breadth response is truncated: total={raw_total}, rows={len(raw_rows)}, max={policy.max_records}"
        )

    returns: list[Decimal] = []
    excluded_b_shares = 0
    excluded_non_trading = 0
    for raw_row in raw_rows:
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)):
            raise ValueError("SSE breadth row must be an array")
        if len(raw_row) != len(_SELECT_FIELDS):
            raise ValueError(f"SSE breadth row width changed: {len(raw_row)}")
        code = str(raw_row[0])
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"Invalid SSE security code: {code!r}")
        if code.startswith("900"):
            excluded_b_shares += 1
            continue
        try:
            last = _decimal(raw_row[2], field="last", code=code)
            previous_close = _decimal(raw_row[3], field="prev_close", code=code)
            change_rate = _decimal(raw_row[4], field="chg_rate", code=code)
        except ValueError:
            excluded_non_trading += 1
            continue
        if last <= 0 or previous_close <= 0:
            excluded_non_trading += 1
            continue
        returns.append(change_rate)
    if len(returns) < 1500:
        raise ValueError(f"SSE breadth universe is unexpectedly small: {len(returns)}")

    advancers = sum(value > 0 for value in returns)
    decliners = sum(value < 0 for value in returns)
    unchanged = len(returns) - advancers - decliners
    count = Decimal(len(returns))
    advance_share = Decimal(advancers) / count * Decimal("100")
    decline_share = Decimal(decliners) / count * Decimal("100")
    advance_decline_ratio = Decimal(advancers) / Decimal(max(decliners, 1))
    equal_weight_return = sum(returns, Decimal("0")) / count
    median_return = Decimal(str(statistics.median(returns)))
    common_attributes = {
        "exchange": "SSE",
        "source_owner": policy.source_owner,
        "source_page": policy.source_page,
        "usage_scope": policy.usage_scope,
        "universe": "SSE A shares including STAR Market; B shares excluded",
        "raw_security_count": raw_total,
        "valid_a_share_count": len(returns),
        "excluded_b_share_count": excluded_b_shares,
        "market_snapshot_time": market_time.isoformat(),
        "coverage_limitation": "Shanghai exchange only; Shenzhen and Beijing exchanges are not included",
        "history_semantics": "strict observations accumulate from first successful local collection",
    }
    if excluded_non_trading:
        common_attributes["excluded_non_trading_count"] = excluded_non_trading
    observations = (
        _series(suffix="advancers", observed=observed, value=Decimal(advancers), unit="count", label="沪市A股上涨家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="decliners", observed=observed, value=Decimal(decliners), unit="count", label="沪市A股下跌家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="unchanged", observed=observed, value=Decimal(unchanged), unit="count", label="沪市A股平盘家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="valid_count", observed=observed, value=count, unit="count", label="沪市A股宽度有效样本数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="advance_share_pct", observed=observed, value=advance_share, unit="%", label="沪市A股上涨家数占比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="decline_share_pct", observed=observed, value=decline_share, unit="%", label="沪市A股下跌家数占比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="advance_decline_ratio", observed=observed, value=advance_decline_ratio, unit="ratio", label="沪市A股涨跌家数比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="equal_weight_return_pct", observed=observed, value=equal_weight_return, unit="%", label="沪市A股等权平均涨跌幅", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="median_return_pct", observed=observed, value=median_return, unit="%", label="沪市A股涨跌幅中位数", fetched_at=fetched_at, attributes=common_attributes),
    )
    issues = [
        QualityIssue(
            "sse_only_partial_china_breadth",
            "Breadth covers Shanghai A shares only; Shenzhen and Beijing are not included",
            QualitySeverity.WARNING,
        ),
        QualityIssue(
            "sse_breadth_history_starts_at_first_collection",
            "Strict point-in-time breadth history starts with the first successful local collection",
            QualitySeverity.WARNING,
        ),
    ]
    if excluded_non_trading:
        issues.append(
            QualityIssue(
                "sse_non_trading_rows_excluded",
                f"Excluded {excluded_non_trading} SSE rows without a usable close/previous close",
                QualitySeverity.WARNING,
            )
        )
    return MarketDataBatch(
        provider_id=PROVIDER_ID,
        batch_id=batch_id,
        tool_name="sse_public_eod_breadth",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments={
            "endpoint": policy.endpoint,
            "universe": policy.universe,
            "max_records": policy.max_records,
        },
        raw_content_sha256=raw_content_sha256,
        schema_sha256=SSE_BREADTH_NORMALIZED_SCHEMA_SHA256,
        numeric_observations=observations,
        quality_issues=tuple(issues),
    )
