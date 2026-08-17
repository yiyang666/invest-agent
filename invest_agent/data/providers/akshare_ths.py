"""Raw-first adapters for the reviewed AKShare/THS fund interfaces."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from importlib.metadata import PackageNotFoundError, version
import json
import re
import time
from typing import Callable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import (
    DataBatch,
    FundMetadataBatch,
    FundMetadataRecord,
    FundMetadataRequest,
    FundNavRecord,
    NavRequest,
    ProviderCapabilities,
    RawFundMetadataPayload,
    RawNavPayload,
    VisibilityStatus,
)
from invest_agent.domain.portfolio import FUND_CODE_PATTERN, QualityIssue, QualitySeverity


SHANGHAI = ZoneInfo("Asia/Shanghai")
AKSHARE_VERSION = "1.18.91"
SOURCE_DOMAIN = "fund.10jqka.com.cn"
METADATA_CONTENT_TYPE = "application/vnd.invest-agent.ths-fund-metadata-batch+json"
DAILY_NAV_CONTENT_TYPE = "application/vnd.invest-agent.ths-daily-nav-batch+json"


@dataclass(frozen=True)
class ThsHttpPayload:
    status_code: int
    body: bytes
    content_type: str = "text/html"
    resolved_url: str | None = None


HttpFetcher = Callable[[str, float], ThsHttpPayload]


def _default_fetcher(url: str, timeout_seconds: float) -> ThsHttpPayload:
    import requests

    response = requests.get(
        url,
        headers={
            "Accept": "text/html, application/json, text/javascript, */*;q=0.8",
            "User-Agent": "Invest-Agent/1.0 (+local personal research)",
        },
        timeout=(min(timeout_seconds, 5.0), timeout_seconds),
    )
    return ThsHttpPayload(
        status_code=response.status_code,
        body=response.content,
        content_type=response.headers.get("Content-Type", "application/octet-stream"),
        resolved_url=str(response.url),
    )


def _verify_akshare_version() -> None:
    try:
        installed = version("akshare")
    except PackageNotFoundError as exc:
        raise RuntimeError("akshare is not installed in the active Python environment") from exc
    if installed != AKSHARE_VERSION:
        raise RuntimeError(
            f"Reviewed akshare version is {AKSHARE_VERSION}, but active version is {installed}"
        )


def _validate_as_of(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")


def _validate_codes(codes: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(str(code).strip() for code in codes))
    if not normalized:
        raise ValueError("At least one fund code is required")
    invalid = [code for code in normalized if FUND_CODE_PATTERN.fullmatch(code) is None]
    if invalid:
        raise ValueError(f"Fund codes must contain six digits: {invalid}")
    return normalized


def _fetch_responses(
    *,
    keys: Sequence[str],
    url_for: Callable[[str], str],
    fetcher: HttpFetcher,
    timeout_seconds: float,
    max_attempts: int,
    retry_delay_seconds: float,
) -> list[dict[str, object]]:
    responses: list[dict[str, object]] = []
    for key in keys:
        url = url_for(key)
        response_entry: dict[str, object] = {"key": key, "requested_url": url}
        for attempt in range(1, max_attempts + 1):
            response_entry["attempts"] = attempt
            try:
                response = fetcher(url, timeout_seconds)
                response_entry.update(
                    {
                        "resolved_url": response.resolved_url,
                        "status_code": response.status_code,
                        "content_type": response.content_type,
                        "body_base64": base64.b64encode(response.body).decode("ascii"),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                if response.status_code < 500 or attempt == max_attempts:
                    break
            except Exception as exc:  # transport implementations expose heterogeneous errors
                response_entry.update(
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                if attempt == max_attempts:
                    break
            if retry_delay_seconds:
                time.sleep(retry_delay_seconds)
        responses.append(response_entry)
    return responses


class _FundInfoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._in_dialog = False
        self._dialog_ul_depth = 0
        self._in_li = False
        self._capture: str | None = None
        self._key_parts: list[str] = []
        self._value_parts: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        values = dict(attrs).get("class") or ""
        return set(values.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "ul" and "g-dialog" in self._classes(attrs) and not self._in_dialog:
            self._in_dialog = True
            self._dialog_ul_depth = 1
            return
        if not self._in_dialog:
            return
        if tag == "ul":
            self._dialog_ul_depth += 1
        elif tag == "li":
            self._in_li = True
            self._key_parts = []
            self._value_parts = []
        elif tag == "span" and self._in_li:
            classes = self._classes(attrs)
            if "key" in classes:
                self._capture = "key"
            elif "value" in classes:
                self._capture = "value"

    def handle_endtag(self, tag: str) -> None:
        if not self._in_dialog:
            return
        if tag == "span":
            self._capture = None
        elif tag == "li" and self._in_li:
            key = "".join(self._key_parts).strip()
            value = "".join(self._value_parts).strip()
            if key and value:
                self.fields[key] = value
            self._in_li = False
            self._capture = None
        elif tag == "ul":
            self._dialog_ul_depth -= 1
            if self._dialog_ul_depth == 0:
                self._in_dialog = False

    def handle_data(self, data: str) -> None:
        if self._capture == "key":
            self._key_parts.append(data)
        elif self._capture == "value":
            self._value_parts.append(data)


def _first_field(fields: Mapping[str, str], names: Sequence[str]) -> str | None:
    for name in names:
        value = fields.get(name)
        if value and value.strip() and value.strip() not in {"--", "-"}:
            return value.strip()
    return None


def _parse_source_date(value: str | None) -> date | None:
    if not value:
        return None
    matched = re.search(r"(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})", value)
    if matched is None:
        return None
    try:
        return date(*(int(part) for part in matched.groups()))
    except ValueError:
        return None


class AkshareThsFundMetadataProvider:
    """Collect the HTML used by AKShare ``fund_info_ths`` and retain every field."""

    provider_id = "akshare_ths_metadata"
    capabilities = ProviderCapabilities(fund_nav_history=False, fund_master=True)

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
        verify_akshare_install: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if verify_akshare_install:
            _verify_akshare_version()
        self._fetcher = fetcher or _default_fetcher
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    def fetch_raw(self, request: FundMetadataRequest) -> RawFundMetadataPayload:
        _validate_as_of(request.as_of)
        codes = _validate_codes(request.fund_codes)
        fetched_at = self._clock()
        _validate_as_of(fetched_at)
        responses = _fetch_responses(
            keys=codes,
            url_for=lambda code: f"https://{SOURCE_DOMAIN}/{code}/interduce.html",
            fetcher=self._fetcher,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_delay_seconds=self.retry_delay_seconds,
        )
        for response in responses:
            response["fund_code"] = response.pop("key")
        envelope = {
            "schema_version": 1,
            "adapter": "akshare_fund_info_ths_raw_v1",
            "akshare_version": AKSHARE_VERSION,
            "responses": responses,
        }
        payload = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return RawFundMetadataPayload(
            provider_id=self.provider_id,
            batch_id=f"akthsmeta-{fetched_at:%Y%m%dT%H%M%S}-{uuid4().hex[:12]}",
            fetched_at=fetched_at,
            payload=payload,
            content_type=METADATA_CONTENT_TYPE,
            provenance=f"akshare=={AKSHARE_VERSION}:fund_info_ths-compatible",
            source_domain=SOURCE_DOMAIN,
            request_parameters={
                "interface": "fund_info_ths",
                "akshare_version": AKSHARE_VERSION,
                "fund_codes": ",".join(codes),
            },
        )

    def normalize(
        self,
        raw: RawFundMetadataPayload,
        request: FundMetadataRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> FundMetadataBatch:
        if raw.provider_id != self.provider_id:
            raise ValueError(f"Unexpected raw provider: {raw.provider_id}")
        requested_codes = set(_validate_codes(request.fund_codes))
        issues: list[QualityIssue] = []
        records: list[FundMetadataRecord] = []
        try:
            envelope = json.loads(raw.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Raw THS metadata envelope cannot be decoded: {exc}") from exc
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported THS metadata envelope schema")

        seen_codes: set[str] = set()
        for response in envelope.get("responses", []):
            code = str(response.get("fund_code", ""))
            if code not in requested_codes:
                issues.append(QualityIssue("unexpected_fund_response", f"Unrequested THS fund: {code!r}", QualitySeverity.ERROR))
                continue
            seen_codes.add(code)
            if response.get("error_message"):
                issues.append(QualityIssue("upstream_fetch_failed", f"THS metadata request failed for {code}: {response['error_message']}", QualitySeverity.ERROR))
                continue
            if response.get("status_code") != 200:
                issues.append(QualityIssue("upstream_http_status", f"THS returned HTTP {response.get('status_code')} for {code}", QualitySeverity.ERROR))
                continue
            try:
                body = base64.b64decode(str(response.get("body_base64", "")), validate=True)
                text = body.decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                issues.append(QualityIssue("invalid_upstream_payload", f"THS metadata payload cannot be decoded for {code}: {exc}", QualitySeverity.ERROR))
                continue
            parser = _FundInfoParser()
            parser.feed(text)
            fields = parser.fields
            if not fields:
                issues.append(QualityIssue("metadata_structure_changed", f"No g-dialog metadata found for {code}", QualitySeverity.ERROR))
                continue
            source_code = _first_field(fields, ("基金代码", "代码"))
            if source_code and source_code != code:
                issues.append(QualityIssue("fund_code_mismatch", f"THS page for {code} reports code {source_code}", QualitySeverity.ERROR))
            records.append(
                FundMetadataRecord(
                    fund_code=code,
                    fund_name=_first_field(fields, ("基金简称", "基金名称", "基金全称")),
                    fund_type=_first_field(fields, ("基金类型", "投资类型")),
                    fund_manager=_first_field(fields, ("基金经理", "基金经理人")),
                    management_company=_first_field(fields, ("基金管理人", "管理人", "基金公司")),
                    custodian=_first_field(fields, ("基金托管人", "托管人")),
                    establishment_date=_parse_source_date(_first_field(fields, ("成立日期", "基金成立日"))),
                    raw_fields=dict(sorted(fields.items())),
                    source_observed_at=raw.fetched_at,
                )
            )

        for code in sorted(requested_codes - seen_codes):
            issues.append(QualityIssue("missing_fund_response", f"No THS metadata response for {code}", QualitySeverity.ERROR))
        records.sort(key=lambda item: item.fund_code)
        return FundMetadataBatch(
            provider_id=self.provider_id,
            batch_id=raw.batch_id,
            fetched_at=raw.fetched_at,
            as_of=request.as_of,
            records=tuple(records),
            provenance=raw.provenance,
            quality_issues=tuple(issues),
            source_domain=raw.source_domain,
            request_parameters=raw.request_parameters,
            raw_content_sha256=raw_content_sha256,
        )


def _dates_inclusive(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _decode_json_object(body: bytes) -> dict[str, object]:
    text = body.decode("utf-8")
    start = text.find("{")
    if start < 0:
        raise ValueError("JSONP response has no JSON object")
    value, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("JSONP payload root is not an object")
    return value


class AkshareThsDailyNavProvider:
    """Collect explicit-date snapshots behind AKShare ``fund_etf_category_ths``."""

    provider_id = "akshare_ths_daily"
    capabilities = ProviderCapabilities(fund_nav_history=False)

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
        max_query_dates: int = 31,
        verify_akshare_install: bool = True,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if max_query_dates < 1:
            raise ValueError("max_query_dates must be at least 1")
        if verify_akshare_install:
            _verify_akshare_version()
        self._fetcher = fetcher or _default_fetcher
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.max_query_dates = max_query_dates

    def _validate_request(self, request: NavRequest) -> tuple[tuple[str, ...], tuple[date, ...]]:
        _validate_as_of(request.as_of)
        codes = _validate_codes(request.fund_codes)
        if request.start_date > request.end_date:
            raise ValueError("start_date cannot be after end_date")
        if request.end_date > request.as_of.date():
            raise ValueError("end_date cannot be after as_of")
        dates = _dates_inclusive(request.start_date, request.end_date)
        if len(dates) > self.max_query_dates:
            raise ValueError(
                f"THS daily cross-check is limited to {self.max_query_dates} dates per batch"
            )
        return codes, dates

    def fetch_raw(self, request: NavRequest) -> RawNavPayload:
        codes, dates = self._validate_request(request)
        date_keys = tuple(item.isoformat() for item in dates)
        fetched_at = self._clock()
        _validate_as_of(fetched_at)
        responses = _fetch_responses(
            keys=date_keys,
            url_for=lambda value: (
                f"https://{SOURCE_DOMAIN}/data/Net/info/"
                f"all_rate_desc_{value}_0_1_9999_0_0_0_jsonp_g.html"
            ),
            fetcher=self._fetcher,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
            retry_delay_seconds=self.retry_delay_seconds,
        )
        for response in responses:
            response["query_date"] = response.pop("key")
        envelope = {
            "schema_version": 1,
            "adapter": "akshare_fund_etf_category_ths_all_raw_v1",
            "akshare_version": AKSHARE_VERSION,
            "responses": responses,
        }
        payload = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return RawNavPayload(
            provider_id=self.provider_id,
            batch_id=f"akthsdaily-{fetched_at:%Y%m%dT%H%M%S}-{uuid4().hex[:12]}",
            fetched_at=fetched_at,
            payload=payload,
            content_type=DAILY_NAV_CONTENT_TYPE,
            provenance=f"akshare=={AKSHARE_VERSION}:fund_etf_category_ths(symbol='')-compatible",
            source_domain=SOURCE_DOMAIN,
            request_parameters={
                "interface": "fund_etf_category_ths",
                "symbol": "",
                "akshare_version": AKSHARE_VERSION,
                "fund_codes": ",".join(codes),
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
            },
        )

    def normalize(
        self,
        raw: RawNavPayload,
        request: NavRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> DataBatch:
        if raw.provider_id != self.provider_id:
            raise ValueError(f"Unexpected raw provider: {raw.provider_id}")
        codes, _dates = self._validate_request(request)
        requested_codes = set(codes)
        issues: list[QualityIssue] = []
        records: list[FundNavRecord] = []
        try:
            envelope = json.loads(raw.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Raw THS daily envelope cannot be decoded: {exc}") from exc
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported THS daily envelope schema")

        for response in envelope.get("responses", []):
            query_date_text = str(response.get("query_date", ""))
            try:
                query_date = date.fromisoformat(query_date_text)
            except ValueError:
                issues.append(QualityIssue("invalid_query_date", f"Invalid THS query date: {query_date_text!r}", QualitySeverity.ERROR))
                continue
            if response.get("error_message"):
                issues.append(QualityIssue("upstream_fetch_failed", f"THS daily request failed for {query_date}: {response['error_message']}", QualitySeverity.ERROR))
                continue
            if response.get("status_code") != 200:
                issues.append(QualityIssue("upstream_http_status", f"THS returned HTTP {response.get('status_code')} for {query_date}", QualitySeverity.ERROR))
                continue
            try:
                body = base64.b64decode(str(response.get("body_base64", "")), validate=True)
                payload = _decode_json_object(body)
                rows = payload["data"]["data"]  # type: ignore[index]
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                issues.append(QualityIssue("invalid_upstream_payload", f"THS daily payload cannot be parsed for {query_date}: {exc}", QualitySeverity.ERROR))
                continue
            if isinstance(rows, Mapping):
                row_values = rows.values()
            elif isinstance(rows, list):
                row_values = rows
            else:
                issues.append(QualityIssue("invalid_nav_shape", f"THS daily rows have an unsupported shape for {query_date}", QualitySeverity.ERROR))
                continue

            found_codes: set[str] = set()
            for item in row_values:
                if not isinstance(item, Mapping):
                    continue
                code = str(item.get("code", "")).strip().zfill(6)
                if code not in requested_codes:
                    continue
                found_codes.add(code)
                unit_text = str(item.get("net", "")).strip()
                accumulated_text = str(item.get("totalnet", "")).strip()
                if unit_text in {"", "--", "-", "None", "null"}:
                    issues.append(QualityIssue("nav_unavailable_for_date", f"THS has no unit NAV for {code} on {query_date}", QualitySeverity.WARNING))
                    continue
                try:
                    unit_nav = Decimal(unit_text)
                    accumulated_nav = None if accumulated_text in {"", "--", "-", "None", "null"} else Decimal(accumulated_text)
                    if not unit_nav.is_finite() or (accumulated_nav is not None and not accumulated_nav.is_finite()):
                        raise ValueError("NAV is not finite")
                except (InvalidOperation, ValueError) as exc:
                    issues.append(QualityIssue("invalid_nav_row", f"Invalid THS NAV for {code} on {query_date}: {exc}", QualitySeverity.ERROR))
                    continue
                records.append(
                    FundNavRecord(
                        fund_code=code,
                        nav_date=query_date,
                        unit_nav=unit_nav,
                        accumulated_nav=accumulated_nav,
                        source_observed_at=raw.fetched_at,
                        first_seen_at=raw.fetched_at,
                        visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
                    )
                )
            for missing in sorted(requested_codes - found_codes):
                issues.append(QualityIssue("fund_missing_from_daily_snapshot", f"THS daily snapshot has no row for {missing} on {query_date}", QualitySeverity.WARNING))

        records.sort(key=lambda item: (item.fund_code, item.nav_date))
        return DataBatch(
            provider_id=self.provider_id,
            batch_id=raw.batch_id,
            fetched_at=raw.fetched_at,
            as_of=request.as_of,
            records=tuple(records),
            provenance=raw.provenance,
            quality_issues=tuple(issues),
            source_domain=raw.source_domain,
            request_parameters=raw.request_parameters,
            raw_content_sha256=raw_content_sha256,
        )
