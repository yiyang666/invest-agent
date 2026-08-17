"""Raw-first Eastmoney NAV adapter aligned with AKShare's reviewed interface."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
import json
import re
import time
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import (
    DataBatch,
    FundDistributionBatch,
    FundDistributionRecord,
    FundNavRecord,
    NavRequest,
    ProviderCapabilities,
    RawNavPayload,
    VisibilityStatus,
)
from invest_agent.domain.portfolio import FUND_CODE_PATTERN, QualityIssue, QualitySeverity


SHANGHAI = ZoneInfo("Asia/Shanghai")
AKSHARE_VERSION = "1.18.91"
SOURCE_DOMAIN = "fund.eastmoney.com"
RAW_CONTENT_TYPE = "application/vnd.invest-agent.eastmoney-fund-nav-batch+json"
CASH_DISTRIBUTION_PATTERN = re.compile(
    r"^分红：每份派现金(?P<amount>[0-9]+(?:\.[0-9]+)?)元$"
)


@dataclass(frozen=True)
class HttpPayload:
    status_code: int
    body: bytes
    content_type: str = "application/javascript"
    resolved_url: str | None = None


HttpFetcher = Callable[[str, float], HttpPayload]


def _default_fetcher(url: str, timeout_seconds: float) -> HttpPayload:
    # Imported lazily so deterministic unit tests do not require network packages.
    import requests

    response = requests.get(
        url,
        headers={
            "Accept": "text/javascript, application/javascript, */*;q=0.8",
            "User-Agent": "Invest-Agent/1.0 (+local personal research)",
        },
        timeout=(min(timeout_seconds, 5.0), timeout_seconds),
    )
    return HttpPayload(
        status_code=response.status_code,
        body=response.content,
        content_type=response.headers.get("Content-Type", "application/javascript"),
        resolved_url=str(response.url),
    )


class AkshareEastmoneyNavProvider:
    """Collect the raw endpoint used by ``fund_open_fund_info_em`` without JS eval.

    AKShare evaluates the remote JavaScript payload. This adapter archives the exact
    response bytes first and parses only the two JSON-compatible variable values
    needed for unit and accumulated NAV.
    """

    provider_id = "akshare_eastmoney"
    capabilities = ProviderCapabilities(fund_nav_history=True)

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
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
            self._verify_akshare_version()
        self._fetcher = fetcher or _default_fetcher
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds

    @staticmethod
    def _verify_akshare_version() -> None:
        try:
            installed = version("akshare")
        except PackageNotFoundError as exc:
            raise RuntimeError("akshare is not installed in the active Python environment") from exc
        if installed != AKSHARE_VERSION:
            raise RuntimeError(
                f"Reviewed akshare version is {AKSHARE_VERSION}, active version is {installed}"
            )

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

    def fetch_raw(self, request: NavRequest) -> RawNavPayload:
        codes = self._validate_request(request)
        fetched_at = datetime.now(SHANGHAI)
        responses: list[dict[str, object]] = []

        for code in codes:
            url = f"https://{SOURCE_DOMAIN}/pingzhongdata/{code}.js"
            response_entry: dict[str, object] = {
                "fund_code": code,
                "requested_url": url,
                "resolved_url": None,
                "status_code": None,
                "content_type": None,
                "body_base64": "",
                "error_type": None,
                "error_message": None,
                "attempts": 0,
            }
            for attempt in range(1, self.max_attempts + 1):
                response_entry["attempts"] = attempt
                try:
                    response = self._fetcher(url, self.timeout_seconds)
                    response_entry.update(
                        {
                            "resolved_url": response.resolved_url or url,
                            "status_code": response.status_code,
                            "content_type": response.content_type,
                            "body_base64": base64.b64encode(response.body).decode("ascii"),
                            "error_type": None,
                            "error_message": None,
                        }
                    )
                    if response.status_code < 500 or attempt == self.max_attempts:
                        break
                except Exception as exc:  # transport adapters expose heterogeneous errors
                    response_entry.update(
                        {
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        }
                    )
                    if attempt == self.max_attempts:
                        break
                if self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds)
            responses.append(response_entry)

        envelope = {
            "schema_version": 1,
            "adapter": "akshare_fund_open_fund_info_em_raw_v1",
            "akshare_version": AKSHARE_VERSION,
            "responses": responses,
        }
        payload = (
            json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        return RawNavPayload(
            provider_id=self.provider_id,
            batch_id=f"akem-{fetched_at:%Y%m%dT%H%M%S}-{uuid4().hex[:12]}",
            fetched_at=fetched_at,
            payload=payload,
            content_type=RAW_CONTENT_TYPE,
            provenance=f"akshare=={AKSHARE_VERSION}:fund_open_fund_info_em-compatible",
            source_domain=SOURCE_DOMAIN,
            request_parameters={
                "interface": "fund_open_fund_info_em",
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
        requested_codes = set(self._validate_request(request))
        issues: list[QualityIssue] = []
        records: list[FundNavRecord] = []

        try:
            envelope = json.loads(raw.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Raw Eastmoney envelope cannot be decoded: {exc}") from exc
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported Eastmoney raw envelope schema")

        seen_response_codes: set[str] = set()
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
            seen_response_codes.add(code)

            error_message = response.get("error_message")
            if error_message:
                issues.append(
                    QualityIssue(
                        "upstream_fetch_failed",
                        f"Eastmoney request failed for {code}: {error_message}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            status_code = response.get("status_code")
            if status_code != 200:
                issues.append(
                    QualityIssue(
                        "upstream_http_status",
                        f"Eastmoney returned HTTP {status_code} for {code}",
                        QualitySeverity.ERROR,
                    )
                )
                continue

            try:
                body = base64.b64decode(str(response.get("body_base64", "")), validate=True)
                text = body.decode("utf-8")
                unit_values = self._extract_js_value(text, "Data_netWorthTrend")
                accumulated_values = self._extract_js_value(text, "Data_ACWorthTrend")
                accumulated_by_date = self._accumulated_by_date(accumulated_values)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                issues.append(
                    QualityIssue(
                        "invalid_upstream_payload",
                        f"Eastmoney payload cannot be parsed for {code}: {exc}",
                        QualitySeverity.ERROR,
                    )
                )
                continue

            if not isinstance(unit_values, list):
                issues.append(
                    QualityIssue(
                        "invalid_nav_shape",
                        f"Data_netWorthTrend is not a list for {code}",
                        QualitySeverity.ERROR,
                    )
                )
                continue

            for item in unit_values:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("NAV row is not an object")
                    nav_date = self._date_from_milliseconds(item["x"])
                    unit_nav = Decimal(str(item["y"]))
                    if not unit_nav.is_finite():
                        raise ValueError("unit NAV is not finite")
                    accumulated_nav = accumulated_by_date.get(nav_date)
                except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                    issues.append(
                        QualityIssue(
                            "invalid_nav_row",
                            f"Ignored an invalid Eastmoney NAV row for {code}: {exc}",
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
                records.append(
                    FundNavRecord(
                        fund_code=code,
                        nav_date=nav_date,
                        unit_nav=unit_nav,
                        accumulated_nav=accumulated_nav,
                        announcement_at=None,
                        source_observed_at=raw.fetched_at,
                        first_seen_at=raw.fetched_at,
                        visibility_status=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
                    )
                )

        for missing_code in sorted(requested_codes - seen_response_codes):
            issues.append(
                QualityIssue(
                    "missing_fund_response",
                    f"Raw payload has no response for requested fund {missing_code}",
                    QualitySeverity.ERROR,
                )
            )

        records.sort(key=lambda record: (record.fund_code, record.nav_date))
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

    def fetch_nav(self, request: NavRequest) -> DataBatch:
        """Compatibility helper; production CLI archives ``fetch_raw`` first."""

        raw = self.fetch_raw(request)
        return self.normalize(raw, request)

    def normalize_distributions(
        self,
        raw: RawNavPayload,
        request: NavRequest,
        *,
        raw_content_sha256: str | None = None,
    ) -> FundDistributionBatch:
        """Extract explicit per-share cash distributions from an archived NAV payload."""

        if raw.provider_id != self.provider_id:
            raise ValueError(f"Unexpected raw provider: {raw.provider_id}")
        requested_codes = set(self._validate_request(request))
        issues: list[QualityIssue] = []
        records: list[FundDistributionRecord] = []
        try:
            envelope = json.loads(raw.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Raw Eastmoney envelope cannot be decoded: {exc}") from exc
        if envelope.get("schema_version") != 1:
            raise ValueError("Unsupported Eastmoney raw envelope schema")

        seen_response_codes: set[str] = set()
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
            seen_response_codes.add(code)
            if response.get("error_message"):
                issues.append(
                    QualityIssue(
                        "upstream_fetch_failed",
                        f"Eastmoney request failed for {code}: {response['error_message']}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            if response.get("status_code") != 200:
                issues.append(
                    QualityIssue(
                        "upstream_http_status",
                        f"Eastmoney returned HTTP {response.get('status_code')} for {code}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            try:
                body = base64.b64decode(str(response.get("body_base64", "")), validate=True)
                text = body.decode("utf-8")
                unit_values = self._extract_js_value(text, "Data_netWorthTrend")
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                issues.append(
                    QualityIssue(
                        "invalid_upstream_payload",
                        f"Eastmoney distribution payload cannot be parsed for {code}: {exc}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            if not isinstance(unit_values, list):
                issues.append(
                    QualityIssue(
                        "invalid_nav_shape",
                        f"Data_netWorthTrend is not a list for {code}",
                        QualitySeverity.ERROR,
                    )
                )
                continue
            for item in unit_values:
                if not isinstance(item, dict):
                    continue
                source_text = str(item.get("unitMoney") or "").strip()
                if not source_text:
                    continue
                try:
                    ex_date = self._date_from_milliseconds(item["x"])
                except (KeyError, ValueError, TypeError) as exc:
                    issues.append(
                        QualityIssue(
                            "invalid_distribution_date",
                            f"Ignored a distribution with invalid date for {code}: {exc}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                if not request.start_date <= ex_date <= request.end_date:
                    continue
                match = CASH_DISTRIBUTION_PATTERN.fullmatch(source_text)
                if match is None:
                    issues.append(
                        QualityIssue(
                            "unsupported_corporate_action_text",
                            f"Unsupported corporate action for {code} on {ex_date}: {source_text}",
                            QualitySeverity.ERROR,
                        )
                    )
                    continue
                cash_per_share = Decimal(match.group("amount"))
                records.append(
                    FundDistributionRecord(
                        fund_code=code,
                        ex_date=ex_date,
                        cash_per_share=cash_per_share,
                        source_text=source_text,
                        announcement_at=None,
                        source_observed_at=raw.fetched_at,
                        first_seen_at=raw.fetched_at,
                        visibility_status=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
                    )
                )

        for missing_code in sorted(requested_codes - seen_response_codes):
            issues.append(
                QualityIssue(
                    "missing_fund_response",
                    f"Raw payload has no response for requested fund {missing_code}",
                    QualitySeverity.ERROR,
                )
            )
        records.sort(key=lambda record: (record.fund_code, record.ex_date))
        parameters = dict(raw.request_parameters)
        parameters["source_nav_batch_id"] = raw.batch_id
        return FundDistributionBatch(
            provider_id="akshare_eastmoney_distribution",
            batch_id=f"{raw.batch_id}-cashdist-v1",
            source_nav_batch_id=raw.batch_id,
            fetched_at=raw.fetched_at,
            as_of=request.as_of,
            records=tuple(records),
            provenance=f"{raw.provenance}:Data_netWorthTrend.unitMoney-v1",
            quality_issues=tuple(issues),
            source_domain=raw.source_domain,
            request_parameters=parameters,
            raw_content_sha256=raw_content_sha256,
        )

    @staticmethod
    def _extract_js_value(text: str, variable_name: str) -> object:
        marker = re.search(rf"\bvar\s+{re.escape(variable_name)}\s*=\s*", text)
        if marker is None:
            raise ValueError(f"Missing JavaScript variable {variable_name}")
        value_text = text[marker.end() :].lstrip()
        value, _ = json.JSONDecoder().raw_decode(value_text)
        return value

    @classmethod
    def _accumulated_by_date(cls, values: object) -> dict[date, Decimal]:
        if not isinstance(values, list):
            raise ValueError("Data_ACWorthTrend is not a list")
        result: dict[date, Decimal] = {}
        for item in values:
            if not isinstance(item, list) or len(item) < 2 or item[1] is None:
                continue
            try:
                value = Decimal(str(item[1]))
                if value.is_finite():
                    result[cls._date_from_milliseconds(item[0])] = value
            except (ValueError, TypeError, InvalidOperation):
                continue
        return result

    @staticmethod
    def _date_from_milliseconds(value: object) -> date:
        milliseconds = int(value)
        return datetime.fromtimestamp(
            milliseconds / 1000,
            tz=timezone.utc,
        ).astimezone(SHANGHAI).date()
