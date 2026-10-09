"""Reviewed Jin10 MCP adapter for high-value market-context evidence only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import requests

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import MarketDataBatch, MarketNumericObservation
from .mcp_client import PROTOCOL_VERSION, RawToolResponse, decode_jsonrpc, extract_tool_result


PROVIDER_ID = "jin10_mcp"
REVIEWED_ENDPOINT = "https://mcp.jin10.com/mcp"
_LOCAL_SCHEMA = {
    "get_quote": ["status", "message", "data.code", "data.name", "data.close", "data.time", "data.ups_percent"],
    "list_calendar": ["status", "message", "data[].title", "data[].pub_time", "data[].actual", "data[].consensus", "data[].previous", "data[].revised", "data[].star", "data[].affect_txt"],
}
APPROVED_RESPONSE_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(_LOCAL_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


@dataclass(frozen=True)
class Jin10QuoteSpec:
    code: str
    label: str
    region: str
    price_unit: str


@dataclass(frozen=True)
class Jin10CalendarSpec:
    title_pattern: str
    series_id: str
    label: str
    unit: str
    frequency: str


@dataclass(frozen=True)
class Jin10Policy:
    endpoint: str
    credential_environment_variable: str
    enabled_tools: frozenset[str]
    interactive_tools: frozenset[str]
    quote_specs: Mapping[str, Jin10QuoteSpec]
    calendar_specs: Sequence[Jin10CalendarSpec]
    maximum_quote_age_hours: int

    def validate_tool(self, tool: str) -> None:
        if tool not in self.enabled_tools:
            raise ValueError(f"Jin10 collection tool is not allowlisted: {tool}")

    def quote_spec(self, code: str) -> Jin10QuoteSpec:
        self.validate_tool("get_quote")
        try:
            return self.quote_specs[code]
        except KeyError as exc:
            raise ValueError(f"Jin10 quote code is not allowlisted: {code}") from exc


def load_jin10_policy(path: str | Path) -> Jin10Policy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("jin10_policy")
    if not isinstance(raw, Mapping):
        raise ValueError("market-data sync config requires jin10_policy")
    endpoint = str(raw.get("endpoint", ""))
    if endpoint != REVIEWED_ENDPOINT:
        raise ValueError("Jin10 endpoint must use the reviewed HTTPS origin")
    credential = str(raw.get("credential_environment_variable", ""))
    if credential != "JIN10_MCP_TOKEN":
        raise ValueError("Jin10 credential must use JIN10_MCP_TOKEN")
    enabled = raw.get("enabled_tools")
    interactive = raw.get("interactive_tools")
    quotes = raw.get("quote_codes")
    calendar = raw.get("calendar_indicators")
    if not isinstance(enabled, list) or set(enabled) != {"get_quote", "list_calendar"}:
        raise ValueError("Jin10 collection allowlist must contain only get_quote and list_calendar")
    if not isinstance(interactive, list) or set(interactive) != {"search_flash", "search_news", "get_news"}:
        raise ValueError("Jin10 interactive allowlist must contain only reviewed search tools")
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("Jin10 quote_codes must be a non-empty array")
    quote_specs: dict[str, Jin10QuoteSpec] = {}
    for item in quotes:
        if not isinstance(item, Mapping):
            raise ValueError("Jin10 quote spec must be an object")
        spec = Jin10QuoteSpec(
            code=str(item.get("code", "")),
            label=str(item.get("label", "")),
            region=str(item.get("region", "")),
            price_unit=str(item.get("price_unit", "")),
        )
        if not all((spec.code, spec.label, spec.region, spec.price_unit)):
            raise ValueError("Jin10 quote spec has missing fields")
        if spec.code in quote_specs:
            raise ValueError(f"Duplicate Jin10 quote code: {spec.code}")
        quote_specs[spec.code] = spec
    if not isinstance(calendar, list) or not calendar:
        raise ValueError("Jin10 calendar_indicators must be a non-empty array")
    calendar_specs: list[Jin10CalendarSpec] = []
    for item in calendar:
        if not isinstance(item, Mapping):
            raise ValueError("Jin10 calendar spec must be an object")
        spec = Jin10CalendarSpec(
            title_pattern=str(item.get("title_pattern", "")),
            series_id=str(item.get("series_id", "")),
            label=str(item.get("label", "")),
            unit=str(item.get("unit", "")),
            frequency=str(item.get("frequency", "")),
        )
        if not all((spec.title_pattern, spec.series_id, spec.label, spec.unit, spec.frequency)):
            raise ValueError("Jin10 calendar spec has missing fields")
        re.compile(spec.title_pattern)
        calendar_specs.append(spec)
    max_age = raw.get("maximum_quote_age_hours", 96)
    if not isinstance(max_age, int) or max_age <= 0:
        raise ValueError("maximum_quote_age_hours must be a positive integer")
    return Jin10Policy(
        endpoint=endpoint,
        credential_environment_variable=credential,
        enabled_tools=frozenset(str(value) for value in enabled),
        interactive_tools=frozenset(str(value) for value in interactive),
        quote_specs=quote_specs,
        calendar_specs=tuple(calendar_specs),
        maximum_quote_age_hours=max_age,
    )


class Jin10McpClient:
    """Minimal Streamable HTTP client; secrets never enter payloads or logs."""

    def __init__(
        self,
        *,
        endpoint: str = REVIEWED_ENDPOINT,
        token_environment_variable: str = "JIN10_MCP_TOKEN",
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        if endpoint != REVIEWED_ENDPOINT:
            raise ValueError("Jin10 endpoint must use the reviewed HTTPS origin")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.token_environment_variable = token_environment_variable
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._request_id = 0
        self._session_id: str | None = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self.token_environment_variable)
        if not token:
            raise ValueError(
                "Required credential environment variable is missing: "
                f"{self.token_environment_variable}"
            )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, message: Mapping[str, object]):
        response = self.session.post(
            self.endpoint,
            headers=self._headers(),
            json=message,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if response.headers.get("Mcp-Session-Id"):
            self._session_id = response.headers["Mcp-Session-Id"]
        return response

    def initialize(self) -> None:
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "invest-agent-jin10", "version": "1.0.0"},
                },
            }
        )
        message = decode_jsonrpc(
            response.content,
            response.headers.get("Content-Type", "application/json"),
        )
        if "error" in message or not isinstance(message.get("result"), Mapping):
            raise ValueError("Jin10 MCP initialize failed")
        notification = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        if notification.status_code not in {200, 202, 204}:
            raise ValueError("Jin10 MCP initialized notification failed")

    def call_tool(self, tool_name: str, arguments: Mapping[str, object]) -> RawToolResponse:
        if self._request_id == 0:
            self.initialize()
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": dict(arguments)},
            }
        )
        return RawToolResponse(
            tool_name=tool_name,
            arguments=dict(arguments),
            fetched_at=datetime.now(ZoneInfo("Asia/Shanghai")),
            payload=response.content,
            content_type=response.headers.get("Content-Type", "application/json"),
        )


def decode_jin10_tool_payload(payload: bytes, content_type: str) -> Mapping[str, object]:
    result = extract_tool_result(decode_jsonrpc(payload, content_type))
    if not isinstance(result, Mapping):
        raise ValueError("Jin10 tool result must be an object")
    status = result.get("status")
    if status != 200:
        raise ValueError(f"Jin10 tool failed with status {status}: {result.get('message', '')}")
    return result


def _decimal(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing Jin10 numeric field: {field}")
    cleaned = str(value).strip().replace(",", "").removesuffix("%")
    try:
        parsed = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid Jin10 numeric field {field}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Non-finite Jin10 numeric field: {field}")
    return parsed


def _aware_datetime(value: object, *, default_timezone: ZoneInfo) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Jin10 timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=default_timezone)
    return parsed


def _numeric(
    *,
    series_id: str,
    observation_date,
    value: object,
    unit: str,
    frequency: str,
    label: str,
    fetched_at: datetime,
    published_date=None,
    attributes: Mapping[str, object] | None = None,
) -> MarketNumericObservation:
    return MarketNumericObservation(
        series_id=series_id,
        observation_date=observation_date,
        value=_decimal(value, series_id),
        unit=unit,
        frequency=frequency,
        label=label,
        published_date=published_date,
        first_seen_at=fetched_at,
        visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
        attributes=attributes or {},
    )


def normalize_jin10_quote(
    *,
    batch_id: str,
    arguments: Mapping[str, object],
    result: Mapping[str, object],
    policy: Jin10Policy,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    data = result.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("Jin10 quote response has no data object")
    requested = str(arguments.get("code", ""))
    spec = policy.quote_spec(requested)
    if data.get("code") != requested:
        raise ValueError("Jin10 quote response code does not match the request")
    observed_at = _aware_datetime(data.get("time"), default_timezone=ZoneInfo("Asia/Shanghai"))
    if observed_at > as_of + timedelta(minutes=5):
        raise ValueError("Jin10 quote timestamp is after as_of")
    issues = [
        QualityIssue(
            "jin10_quote_timestamp_semantics_unverified",
            "Provider quote timestamps are preserved but exchange-timezone semantics are not documented",
            QualitySeverity.WARNING,
        )
    ]
    age = as_of.astimezone(observed_at.tzinfo) - observed_at
    if age > timedelta(hours=policy.maximum_quote_age_hours):
        issues.append(
            QualityIssue(
                "stale_jin10_quote",
                f"Jin10 quote {requested} is {age.total_seconds() / 3600:.1f} hours old",
                QualitySeverity.WARNING,
            )
        )
    attrs = {
        "code": requested,
        "provider_name": data.get("name"),
        "region": spec.region,
        "provider_time": observed_at.isoformat(),
        "volume_excluded_reason": "provider_unit_not_documented",
    }
    records = [
        _numeric(
            series_id=f"jin10_quote:{requested}:close",
            observation_date=observed_at.date(),
            value=data.get("close"),
            unit=spec.price_unit,
            frequency="snapshot",
            label=f"{spec.label}收盘/最新价",
            fetched_at=fetched_at,
            attributes=attrs,
        )
    ]
    if data.get("ups_percent") is not None:
        records.append(
            _numeric(
                series_id=f"jin10_quote:{requested}:change_pct",
                observation_date=observed_at.date(),
                value=data.get("ups_percent"),
                unit="%",
                frequency="snapshot",
                label=f"{spec.label}涨跌幅",
                fetched_at=fetched_at,
                attributes=attrs,
            )
        )
    return MarketDataBatch(
        provider_id=PROVIDER_ID,
        batch_id=batch_id,
        tool_name="get_quote",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments=dict(arguments),
        raw_content_sha256=raw_content_sha256,
        schema_sha256=APPROVED_RESPONSE_SCHEMA_SHA256,
        numeric_observations=tuple(records),
        quality_issues=tuple(issues),
    )


def normalize_jin10_calendar(
    *,
    batch_id: str,
    result: Mapping[str, object],
    policy: Jin10Policy,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    data = result.get("data")
    if not isinstance(data, list):
        raise ValueError("Jin10 calendar response has no data array")
    records: list[MarketNumericObservation] = []
    matched_events = 0
    for raw in data:
        if not isinstance(raw, Mapping):
            raise ValueError("Jin10 calendar item must be an object")
        title = str(raw.get("title", ""))
        spec = next(
            (candidate for candidate in policy.calendar_specs if re.fullmatch(candidate.title_pattern, title)),
            None,
        )
        if spec is None:
            continue
        matched_events += 1
        if raw.get("actual") in (None, ""):
            continue
        published_at = _aware_datetime(raw.get("pub_time"), default_timezone=ZoneInfo("Asia/Shanghai"))
        if published_at > as_of + timedelta(minutes=5):
            continue
        attrs = {
            "title": title,
            "scheduled_at": published_at.isoformat(),
            "consensus_text": raw.get("consensus"),
            "previous_text": raw.get("previous"),
            "revised_text": raw.get("revised"),
            "importance": raw.get("star"),
            "provider_impact_text": raw.get("affect_txt"),
            "observation_date_basis": "release_date",
        }
        records.append(
            _numeric(
                series_id=spec.series_id,
                observation_date=published_at.date(),
                value=raw.get("actual"),
                unit=spec.unit,
                frequency=spec.frequency,
                label=spec.label,
                fetched_at=fetched_at,
                published_date=published_at.date(),
                attributes=attrs,
            )
        )
        if raw.get("consensus") not in (None, ""):
            surprise = _decimal(raw.get("actual"), "actual") - _decimal(raw.get("consensus"), "consensus")
            records.append(
                _numeric(
                    series_id=f"{spec.series_id}:surprise",
                    observation_date=published_at.date(),
                    value=surprise,
                    unit=spec.unit,
                    frequency=spec.frequency,
                    label=f"{spec.label}实际-预期",
                    fetched_at=fetched_at,
                    published_date=published_at.date(),
                    attributes=attrs,
                )
            )
    issues = (
        QualityIssue(
            "jin10_calendar_current_week_only",
            f"Jin10 MCP exposed the current natural week; {matched_events} reviewed events matched out of {len(data)} items",
            QualitySeverity.WARNING,
        ),
    )
    return MarketDataBatch(
        provider_id=PROVIDER_ID,
        batch_id=batch_id,
        tool_name="list_calendar",
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments={},
        raw_content_sha256=raw_content_sha256,
        schema_sha256=APPROVED_RESPONSE_SCHEMA_SHA256,
        numeric_observations=tuple(records),
        quality_issues=issues,
    )
