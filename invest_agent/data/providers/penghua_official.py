"""Raw-first NAV adapter for Penghua Fund's official public product endpoint."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import time
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import (
    DataBatch,
    FundNavRecord,
    NavRequest,
    ProviderCapabilities,
    RawNavPayload,
    VisibilityStatus,
)
from invest_agent.domain.portfolio import FUND_CODE_PATTERN, QualityIssue, QualitySeverity


SHANGHAI = ZoneInfo("Asia/Shanghai")
SOURCE_DOMAIN = "www.phfund.com.cn"
SOURCE_URL = f"https://{SOURCE_DOMAIN}/web/fundDetail/getNavList"
RAW_CONTENT_TYPE = "application/vnd.invest-agent.penghua-official-nav-pages+json"
PAGE_SIZE = 100


@dataclass(frozen=True)
class PenghuaHttpPayload:
    status_code: int
    body: bytes
    content_type: str = "application/json"
    resolved_url: str | None = None


HttpFetcher = Callable[[str, dict[str, object], float], PenghuaHttpPayload]


def _default_fetcher(
    url: str, request_body: dict[str, object], timeout_seconds: float
) -> PenghuaHttpPayload:
    import requests

    response = requests.post(
        url,
        json=request_body,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Invest-Agent/1.0 (+local personal research)",
        },
        timeout=(min(timeout_seconds, 5.0), timeout_seconds),
    )
    return PenghuaHttpPayload(
        status_code=response.status_code,
        body=response.content,
        content_type=response.headers.get("Content-Type", "application/json"),
        resolved_url=str(response.url),
    )


class PenghuaOfficialNavProvider:
    """Collect official Penghua NAV pages and archive every exact response body."""

    provider_id = "penghua_official_nav"
    capabilities = ProviderCapabilities(fund_nav_history=True)

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 2,
        retry_delay_seconds: float = 0.5,
        max_pages: int = 100,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        self._fetcher = fetcher or _default_fetcher
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.max_pages = max_pages

    @staticmethod
    def _validate_request(request: NavRequest) -> tuple[str, ...]:
        if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
            raise ValueError("request.as_of must include a timezone")
        if request.start_date > request.end_date:
            raise ValueError("start_date cannot be after end_date")
        if request.end_date > request.as_of.date():
            raise ValueError("end_date cannot be after request.as_of")
        codes = tuple(sorted(set(request.fund_codes)))
        if not codes:
            raise ValueError("At least one fund code is required")
        invalid = [code for code in codes if not FUND_CODE_PATTERN.fullmatch(code)]
        if invalid:
            raise ValueError(f"Fund codes must contain six digits: {invalid}")
        return codes

    def _fetch_page(self, code: str, page_no: int, request: NavRequest) -> dict[str, object]:
        request_body: dict[str, object] = {
            "fundCode": code,
            "pageNo": page_no,
            "pageSize": PAGE_SIZE,
            "startDate": request.start_date.strftime("%Y%m%d"),
            "endDate": request.end_date.strftime("%Y%m%d"),
        }
        entry: dict[str, object] = {
            "fund_code": code,
            "page_no": page_no,
            "requested_url": SOURCE_URL,
            "request_body": request_body,
            "resolved_url": None,
            "status_code": None,
            "content_type": None,
            "body_base64": "",
            "error_type": None,
            "error_message": None,
            "attempts": 0,
        }
        for attempt in range(1, self.max_attempts + 1):
            entry["attempts"] = attempt
            try:
                response = self._fetcher(SOURCE_URL, request_body, self.timeout_seconds)
                entry.update(
                    {
                        "resolved_url": response.resolved_url or SOURCE_URL,
                        "status_code": response.status_code,
                        "content_type": response.content_type,
                        "body_base64": base64.b64encode(response.body).decode("ascii"),
                        "error_type": None,
                        "error_message": None,
                    }
                )
                if response.status_code < 500 or attempt == self.max_attempts:
                    break
            except Exception as exc:
                entry.update(
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    }
                )
                if attempt == self.max_attempts:
                    break
            if self.retry_delay_seconds:
                time.sleep(self.retry_delay_seconds)
        return entry

    @staticmethod
    def _declared_pages(entry: dict[str, object]) -> int:
        if entry.get("status_code") != 200 or entry.get("error_message"):
            return 1
        try:
            body = base64.b64decode(str(entry["body_base64"]), validate=True)
            payload = json.loads(body.decode("utf-8"))
            data = payload.get("data")
            if not isinstance(data, dict):
                return 1
            pages = int(data.get("pages", 1))
            return max(pages, 1)
        except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return 1

    def fetch_raw(self, request: NavRequest) -> RawNavPayload:
        codes = self._validate_request(request)
        fetched_at = datetime.now(SHANGHAI)
        responses: list[dict[str, object]] = []
        fetch_issues: list[str] = []

        for code in codes:
            first = self._fetch_page(code, 1, request)
            responses.append(first)
            declared_pages = self._declared_pages(first)
            if declared_pages > self.max_pages:
                fetch_issues.append(
                    f"{code} declared {declared_pages} pages, exceeding max_pages={self.max_pages}"
                )
                continue
            for page_no in range(2, declared_pages + 1):
                responses.append(self._fetch_page(code, page_no, request))

        envelope = {
            "schema_version": 1,
            "adapter": "penghua_official_get_nav_list_v1",
            "page_size": PAGE_SIZE,
            "fetch_issues": fetch_issues,
            "responses": responses,
        }
        payload = (
            json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        return RawNavPayload(
            provider_id=self.provider_id,
            batch_id=f"phofficial-{fetched_at:%Y%m%dT%H%M%S}-{uuid4().hex[:12]}",
            fetched_at=fetched_at,
            payload=payload,
            content_type=RAW_CONTENT_TYPE,
            provenance="Penghua Fund official public endpoint:getNavList-v1",
            source_domain=SOURCE_DOMAIN,
            request_parameters={
                "interface": "fundDetail/getNavList",
                "fund_codes": ",".join(codes),
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
                "page_size": str(PAGE_SIZE),
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
        requested_codes = set(self._validate_request(request))
        issues: list[QualityIssue] = []
        records_by_key: dict[tuple[str, object], FundNavRecord] = {}
        seen_codes: set[str] = set()

        try:
            envelope = json.loads(raw.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Raw Penghua envelope cannot be decoded: {exc}") from exc
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported Penghua raw envelope schema")

        for message in envelope.get("fetch_issues", []):
            issues.append(QualityIssue("pagination_limit_exceeded", str(message), QualitySeverity.ERROR))

        for response in envelope.get("responses", []):
            code = str(response.get("fund_code", ""))
            if code not in requested_codes:
                issues.append(
                    QualityIssue(
                        "unexpected_fund_response",
                        f"Raw payload contains an unrequested fund: {code!r}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            seen_codes.add(code)
            page_no = response.get("page_no")
            if response.get("error_message"):
                issues.append(
                    QualityIssue(
                        "upstream_fetch_failed",
                        f"Penghua request failed for {code} page {page_no}: {response['error_message']}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            if response.get("status_code") != 200:
                issues.append(
                    QualityIssue(
                        "upstream_http_status",
                        f"Penghua returned HTTP {response.get('status_code')} for {code} page {page_no}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            try:
                body = base64.b64decode(str(response.get("body_base64", "")), validate=True)
                payload = json.loads(body.decode("utf-8"))
                if payload.get("code") != "CM000000":
                    raise ValueError(f"business code is {payload.get('code')!r}")
                data = payload.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                    raise ValueError("data.records is not a list")
                rows = data["records"]
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                issues.append(
                    QualityIssue(
                        "invalid_upstream_payload",
                        f"Penghua payload cannot be parsed for {code} page {page_no}: {exc}",
                        QualitySeverity.ERROR,
                    )
                )
                continue

            for row in rows:
                try:
                    if not isinstance(row, dict):
                        raise ValueError("NAV row is not an object")
                    row_code = str(row.get("fundCode") or code)
                    if row_code != code:
                        raise ValueError(f"row fund code {row_code!r} does not match {code}")
                    nav_date = datetime.strptime(str(row["navDate"]), "%Y%m%d").date()
                    unit_nav = Decimal(str(row["unitNav"]))
                    accumulated_value = row.get("aggrUnitNav")
                    accumulated_nav = (
                        Decimal(str(accumulated_value))
                        if accumulated_value not in (None, "")
                        else None
                    )
                    if not unit_nav.is_finite() or (
                        accumulated_nav is not None and not accumulated_nav.is_finite()
                    ):
                        raise ValueError("NAV is not finite")
                except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                    issues.append(
                        QualityIssue(
                            "invalid_nav_row",
                            f"Ignored an invalid Penghua NAV row for {code}: {exc}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                if not request.start_date <= nav_date <= request.end_date:
                    continue
                if nav_date > request.as_of.date():
                    issues.append(
                        QualityIssue(
                            "future_nav_observation",
                            f"Ignored {code} NAV dated after as_of: {nav_date}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                key = (code, nav_date)
                if key in records_by_key:
                    issues.append(
                        QualityIssue(
                            "duplicate_nav_observation",
                            f"Duplicate Penghua NAV for {code} on {nav_date}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                records_by_key[key] = FundNavRecord(
                    fund_code=code,
                    nav_date=nav_date,
                    unit_nav=unit_nav,
                    accumulated_nav=accumulated_nav,
                    source_observed_at=raw.fetched_at,
                    first_seen_at=raw.fetched_at,
                    visibility_status=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
                )

        for code in sorted(requested_codes - seen_codes):
            issues.append(
                QualityIssue(
                    "missing_fund_response",
                    f"Raw payload has no response for requested fund {code}",
                    QualitySeverity.ERROR,
                )
            )

        records = tuple(sorted(records_by_key.values(), key=lambda item: (item.fund_code, item.nav_date)))
        return DataBatch(
            provider_id=self.provider_id,
            batch_id=raw.batch_id,
            fetched_at=raw.fetched_at,
            as_of=request.as_of,
            records=records,
            provenance=raw.provenance,
            quality_issues=tuple(issues),
            source_domain=raw.source_domain,
            request_parameters=raw.request_parameters,
            raw_content_sha256=raw_content_sha256,
        )

    def fetch_nav(self, request: NavRequest) -> DataBatch:
        raw = self.fetch_raw(request)
        return self.normalize(raw, request)
