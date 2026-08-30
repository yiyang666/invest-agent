"""Raw-first all-A-share breadth adapter for Guchacha's public dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import MarketDataBatch, MarketNumericObservation


PROVIDER_ID = "guchacha_mcp"
SHANGHAI = ZoneInfo("Asia/Shanghai")

_NORMALIZED_SCHEMA = {
    "adapter": "guchacha_public_dashboard_breadth_v1",
    "source_fields": [
        "total",
        "advancers",
        "decliners",
        "unchanged",
        "average_return_pct",
        "last_sync",
    ],
    "derived_series": [
        "listed_count",
        "advancers",
        "decliners",
        "unchanged",
        "classified_count",
        "unclassified_count",
        "classified_coverage_pct",
        "advance_share_pct",
        "decline_share_pct",
        "advance_decline_ratio",
        "average_return_pct",
    ],
    "universe": "provider-reported all A shares",
    "visibility": "strict_point_in_time_at_first_collection",
}
GUCHACHA_BREADTH_NORMALIZED_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(_NORMALIZED_SCHEMA, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class GuchachaBreadthPolicy:
    endpoint: str
    source_owner: str
    upstream_label: str
    usage_scope: str
    universe: str
    minimum_final_time: time


@dataclass(frozen=True)
class RawGuchachaBreadthResponse:
    fetched_at: datetime
    payload: bytes
    content_type: str
    request_url: str


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.tokens.append(value)


def load_guchacha_breadth_policy(path: str | Path) -> GuchachaBreadthPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("guchacha_breadth_policy")
    if not isinstance(raw, Mapping) or raw.get("enabled") is not True:
        raise ValueError("Guchacha breadth collection is not enabled in market-data config")
    required = ("endpoint", "source_owner", "upstream_label", "usage_scope", "universe")
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise ValueError("Guchacha breadth policy is incomplete")
    if raw["usage_scope"] != "personal_research_local_only":
        raise ValueError(f"Unsupported Guchacha breadth usage scope: {raw['usage_scope']}")
    try:
        minimum_final_time = time.fromisoformat(str(raw.get("minimum_final_time", "15:00:00")))
    except ValueError as exc:
        raise ValueError("Guchacha breadth minimum_final_time must be an ISO time") from exc
    return GuchachaBreadthPolicy(
        endpoint=str(raw["endpoint"]),
        source_owner=str(raw["source_owner"]),
        upstream_label=str(raw["upstream_label"]),
        usage_scope=str(raw["usage_scope"]),
        universe=str(raw["universe"]),
        minimum_final_time=minimum_final_time,
    )


class GuchachaBreadthClient:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        policy: GuchachaBreadthPolicy,
        fetched_at: datetime,
    ) -> RawGuchachaBreadthResponse:
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
                "--user-agent",
                "Mozilla/5.0",
                "--url",
                policy.endpoint,
            ],
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds + 5,
        )
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(
                f"Guchacha dashboard request failed ({completed.returncode}): {message}"
            )
        return RawGuchachaBreadthResponse(
            fetched_at=fetched_at,
            payload=completed.stdout,
            content_type="text/html; charset=utf-8",
            request_url=policy.endpoint,
        )


def _token_after(tokens: list[str], label: str) -> str:
    positions = [index for index, token in enumerate(tokens) if token == label]
    if len(positions) != 1 or positions[0] + 1 >= len(tokens):
        raise ValueError(f"Guchacha dashboard label changed or is ambiguous: {label}")
    return tokens[positions[0] + 1]


def _integer(value: str, *, field: str) -> int:
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"Invalid Guchacha dashboard {field}: {value!r}")
    return int(value)


def _series(
    *,
    suffix: str,
    observed: datetime,
    value: Decimal,
    unit: str,
    label: str,
    fetched_at: datetime,
    attributes: Mapping[str, object],
) -> MarketNumericObservation:
    return MarketNumericObservation(
        series_id=f"breadth:china:all_a:guchacha:{suffix}",
        observation_date=observed.date(),
        value=value,
        unit=unit,
        frequency="business_daily",
        label=label,
        first_seen_at=fetched_at,
        visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
        attributes=attributes,
    )


def normalize_guchacha_breadth(
    *,
    batch_id: str,
    payload: bytes,
    policy: GuchachaBreadthPolicy,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    if fetched_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("Guchacha breadth timestamps require timezone")
    html = payload.decode("utf-8")
    parser = _VisibleTextParser()
    parser.feed(html)
    tokens = parser.tokens
    if policy.upstream_label not in tokens:
        raise ValueError("Guchacha dashboard upstream label changed")

    total = _integer(_token_after(tokens, "A股总数"), field="total")
    advancers = _integer(_token_after(tokens, "上涨家数"), field="advancers")
    decliners = _integer(_token_after(tokens, "下跌家数"), field="decliners")
    average_text = _token_after(tokens, "平均涨跌幅")
    if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?%", average_text) is None:
        raise ValueError(f"Invalid Guchacha dashboard average return: {average_text!r}")
    average_return = Decimal(average_text.rstrip("%"))

    flat_matches = [re.fullmatch(r"平盘\s+([0-9]+)", token) for token in tokens]
    flat_values = [match.group(1) for match in flat_matches if match is not None]
    if len(flat_values) != 1:
        raise ValueError("Guchacha dashboard flat-count field changed or is ambiguous")
    unchanged = int(flat_values[0])

    success_matches = re.findall(r"([0-9]+)/([0-9]+)\s*成功", " ".join(tokens))
    if len(success_matches) != 1:
        raise ValueError("Guchacha dashboard sync-success field changed or is ambiguous")
    sync_total, sync_success = (int(value) for value in success_matches[0])
    if sync_total != total or sync_success != total:
        raise ValueError(
            f"Guchacha dashboard sync is incomplete: {sync_success}/{sync_total}, total={total}"
        )

    timestamps = {
        datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(SHANGHAI)
        for value in re.findall(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z",
            html,
        )
    }
    if not timestamps:
        raise ValueError("Guchacha dashboard has no last-sync timestamp")
    last_sync = max(timestamps)
    if last_sync > as_of:
        raise ValueError("Guchacha dashboard last sync is after as_of")
    if last_sync.timetz().replace(tzinfo=None) < policy.minimum_final_time:
        raise ValueError(
            f"Guchacha dashboard snapshot is not final: {last_sync.time().isoformat()}"
        )

    classified = advancers + decliners + unchanged
    if total <= 0 or classified <= 0 or classified > total:
        raise ValueError(
            f"Invalid Guchacha breadth counts: total={total}, classified={classified}"
        )
    unclassified = total - classified
    classified_decimal = Decimal(classified)
    total_decimal = Decimal(total)
    advance_share = Decimal(advancers) / classified_decimal * Decimal("100")
    decline_share = Decimal(decliners) / classified_decimal * Decimal("100")
    advance_decline_ratio = Decimal(advancers) / Decimal(max(decliners, 1))
    classified_coverage = classified_decimal / total_decimal * Decimal("100")
    common_attributes = {
        "source_owner": policy.source_owner,
        "source_page": policy.endpoint,
        "reported_upstream": policy.upstream_label,
        "usage_scope": policy.usage_scope,
        "universe": policy.universe,
        "denominator": "advancers + decliners + unchanged",
        "provider_reported_total": total,
        "provider_classified_count": classified,
        "provider_unclassified_count": unclassified,
        "provider_last_sync": last_sync.isoformat(),
        "history_semantics": "strict observations accumulate from first successful local collection",
    }
    observations = (
        _series(suffix="listed_count", observed=last_sync, value=total_decimal, unit="count", label="全A股总数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="advancers", observed=last_sync, value=Decimal(advancers), unit="count", label="全A股上涨家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="decliners", observed=last_sync, value=Decimal(decliners), unit="count", label="全A股下跌家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="unchanged", observed=last_sync, value=Decimal(unchanged), unit="count", label="全A股平盘家数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="classified_count", observed=last_sync, value=classified_decimal, unit="count", label="全A股宽度有效分类数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="unclassified_count", observed=last_sync, value=Decimal(unclassified), unit="count", label="全A股未分类证券数", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="classified_coverage_pct", observed=last_sync, value=classified_coverage, unit="%", label="全A股宽度分类覆盖率", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="advance_share_pct", observed=last_sync, value=advance_share, unit="%", label="全A股上涨家数占比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="decline_share_pct", observed=last_sync, value=decline_share, unit="%", label="全A股下跌家数占比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="advance_decline_ratio", observed=last_sync, value=advance_decline_ratio, unit="ratio", label="全A股涨跌家数比", fetched_at=fetched_at, attributes=common_attributes),
        _series(suffix="average_return_pct", observed=last_sync, value=average_return, unit="%", label="全A股平均涨跌幅", fetched_at=fetched_at, attributes=common_attributes),
    )
    issues = [
        QualityIssue(
            "guchacha_aggregated_breadth",
            "Guchacha publishes aggregate breadth without constituent-level rows; retained as research-only provider evidence",
            QualitySeverity.WARNING,
        ),
        QualityIssue(
            "guchacha_breadth_history_starts_at_first_collection",
            "Strict point-in-time breadth history starts with the first successful local collection",
            QualitySeverity.WARNING,
        ),
    ]
    if unclassified:
        issues.append(
            QualityIssue(
                "guchacha_unclassified_securities",
                f"Provider total exceeds up/down/flat classifications by {unclassified}; classified count is the percentage denominator",
                QualitySeverity.WARNING,
            )
        )
    return MarketDataBatch(
        provider_id=PROVIDER_ID,
        batch_id=batch_id,
        tool_name="guchacha_public_dashboard_breadth",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments={
            "endpoint": policy.endpoint,
            "transport": "public_server_rendered_html",
            "universe": policy.universe,
        },
        raw_content_sha256=raw_content_sha256,
        schema_sha256=GUCHACHA_BREADTH_NORMALIZED_SCHEMA_SHA256,
        numeric_observations=observations,
        quality_issues=tuple(issues),
    )
