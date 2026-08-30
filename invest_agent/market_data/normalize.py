"""Normalize reviewed Guchacha tool payloads into bounded local contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Mapping, Sequence

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.domain.portfolio import QualityIssue, QualitySeverity

from .contracts import (
    DatasetCatalogObservation,
    IndexWeightObservation,
    MarketDataBatch,
    MarketNumericObservation,
)


APPROVED_TOOLS_SCHEMA_SHA256 = "193a51a92b8b6d17f5bf61aaeb093f7beedbefbc99b8a66417a1f15c270027d9"


_MACRO_UNITS = {
    "cpi_yoy": "%",
    "cpi_mom": "%",
    "ppi_yoy": "%",
    "pmi_make": "index",
    "pmi_nmake": "index",
    "gdp_yoy": "%",
    "gdp_cum": "亿元",
    "buffett": "%",
    "buffett_mc": "元",
    "buffett_gdp": "元",
    "nfp_change": "千人",
    "unemployment": "%",
}


def _decimal(value: object, field: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing numeric field: {field}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid numeric field {field}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Non-finite numeric field: {field}")
    return parsed


def _date(value: object, field: str) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing date field: {field}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Expected object for {field}")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ValueError(f"Expected array for {field}")
    return value


def _strict(first_seen: datetime) -> tuple[datetime, VisibilityStatus]:
    return first_seen, VisibilityStatus.STRICT_POINT_IN_TIME


def _assumed() -> tuple[None, VisibilityStatus]:
    return None, VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED


def _numeric(
    *,
    series_id: str,
    observation_date: date,
    value: object,
    unit: str,
    frequency: str,
    label: str,
    fetched_at: datetime,
    current: bool,
    published_date: date | None = None,
    attributes: Mapping[str, object] | None = None,
) -> MarketNumericObservation:
    first_seen, visibility = _strict(fetched_at) if current else _assumed()
    return MarketNumericObservation(
        series_id=series_id,
        observation_date=observation_date,
        value=_decimal(value, series_id),
        unit=unit,
        frequency=frequency,
        label=label,
        published_date=published_date,
        first_seen_at=first_seen,
        visibility_status=visibility,
        attributes=attributes or {},
    )


def _normalize_catalog(result: Mapping[str, object]) -> tuple[list[DatasetCatalogObservation], list[QualityIssue]]:
    records: list[DatasetCatalogObservation] = []
    for raw in _sequence(result.get("datasets"), "datasets"):
        item = _mapping(raw, "dataset")
        records.append(
            DatasetCatalogObservation(
                dataset_name=str(item.get("name", "")).strip(),
                tool_name=str(item.get("tool", "")).strip(),
                description=str(item.get("description", "")).strip(),
                last_updated=_date(item.get("last_updated"), "last_updated"),
            )
        )
    return records, []


def _normalize_index_valuation(
    result: Mapping[str, object], fetched_at: datetime
) -> tuple[list[MarketNumericObservation], list[QualityIssue]]:
    records: list[MarketNumericObservation] = []
    issues: list[QualityIssue] = []
    if isinstance(result.get("indexes"), list):
        for raw in _sequence(result.get("indexes"), "indexes"):
            item = _mapping(raw, "index")
            code = str(item.get("index_code", ""))
            name = str(item.get("name", code))
            observed = _date(item.get("snapshot_date"), "snapshot_date")
            for field, unit, label in (
                ("pe_ttm", "ratio", "PE-TTM"),
                ("pe_pct", "%", "PE历史分位"),
                ("pb", "ratio", "PB"),
                ("pb_pct", "%", "PB历史分位"),
                ("div_yield", "%", "股息率"),
            ):
                if item.get(field) is None:
                    continue
                records.append(
                    _numeric(
                        series_id=f"index_valuation:{code}:{field}",
                        observation_date=observed,
                        value=item[field],
                        unit=unit,
                        frequency="snapshot",
                        label=f"{name} {label}",
                        fetched_at=fetched_at,
                        current=True,
                        attributes={"area": item.get("area")},
                    )
                )
        return records, issues

    index = _mapping(result.get("index"), "index")
    valuation = _mapping(result.get("valuation"), "valuation")
    code = str(index.get("index_code", ""))
    name = str(index.get("name", code))
    observed = _date(result.get("snapshot_date"), "snapshot_date")
    history = result.get("history")
    history_metric = None
    if isinstance(history, Mapping):
        history_metric = str(history.get("metric", ""))
        history_series = _sequence(history.get("series"), "history.series")
        for raw in history_series:
            point = _mapping(raw, "history.point")
            point_date = _date(point.get("date"), "history.date")
            records.append(
                _numeric(
                    series_id=f"index_valuation:{code}:{history_metric}",
                    observation_date=point_date,
                    value=point.get("value"),
                    unit="%" if history_metric in {"dyr", "div_yield_pct"} else "ratio",
                    frequency="weekly",
                    label=f"{name} {history_metric}",
                    fetched_at=fetched_at,
                    current=point_date == observed,
                    attributes={"window": result.get("percentile_window_used")},
                )
            )
    current_fields = (
        ("pe_ttm", "ratio", "PE-TTM"),
        ("pe_percentile", "%", "PE历史分位"),
        ("pb", "ratio", "PB"),
        ("pb_percentile", "%", "PB历史分位"),
        ("ps_ttm", "ratio", "PS-TTM"),
        ("ps_percentile", "%", "PS历史分位"),
        ("div_yield_pct", "%", "股息率"),
        ("div_percentile", "%", "股息率历史分位"),
        ("roe", "%", "ROE"),
    )
    for field, unit, label in current_fields:
        if valuation.get(field) is None or field == history_metric:
            continue
        records.append(
            _numeric(
                series_id=f"index_valuation:{code}:{field}",
                observation_date=observed,
                value=valuation[field],
                unit=unit,
                frequency="snapshot",
                label=f"{name} {label}",
                fetched_at=fetched_at,
                current=True,
            )
        )
    return records, issues


def _normalize_forward_pe(
    result: Mapping[str, object], fetched_at: datetime
) -> tuple[list[MarketNumericObservation], list[QualityIssue]]:
    records: list[MarketNumericObservation] = []
    for raw in _sequence(result.get("indexes"), "indexes"):
        item = _mapping(raw, "forward index")
        code = str(item.get("index_code", ""))
        for raw_year in _sequence(item.get("years"), "years"):
            year = _mapping(raw_year, "forward year")
            forecast_year = int(year.get("year"))
            records.append(
                _numeric(
                    series_id=f"index_forward:{code}:pe:{forecast_year}",
                    observation_date=fetched_at.date(),
                    value=year.get("forward_pe"),
                    unit="ratio",
                    frequency="snapshot",
                    label=f"{code} {forecast_year}E前瞻PE",
                    fetched_at=fetched_at,
                    current=True,
                    attributes={
                        "forecast_year": forecast_year,
                        "covered_stocks": year.get("covered_stocks"),
                        "coverage_pct": year.get("coverage_pct"),
                        "observation_date_basis": "retrieval_date",
                    },
                )
            )
        if item.get("expected_growth_pct") is not None:
            records.append(
                _numeric(
                    series_id=f"index_forward:{code}:expected_growth_pct",
                    observation_date=fetched_at.date(),
                    value=item.get("expected_growth_pct"),
                    unit="%",
                    frequency="snapshot",
                    label=f"{code}预期盈利增速",
                    fetched_at=fetched_at,
                    current=True,
                    attributes={"observation_date_basis": "retrieval_date"},
                )
            )
    return records, [
        QualityIssue(
            "retrieval_date_used_for_forward_snapshot",
            "Forward estimates expose no provider snapshot date; retrieval date is retained explicitly",
            QualitySeverity.WARNING,
        )
    ]


def _normalize_index_weights(
    result: Mapping[str, object], fetched_at: datetime
) -> tuple[list[IndexWeightObservation], list[QualityIssue]]:
    code = str(result.get("index_code", ""))
    name = str(result.get("index_name", code))
    observed = _date(result.get("weight_date"), "weight_date")
    estimated = bool(result.get("is_estimated"))
    records: list[IndexWeightObservation] = []
    for raw in _sequence(result.get("constituents"), "constituents"):
        item = _mapping(raw, "constituent")
        contribution = item.get("contribution_pct_points")
        records.append(
            IndexWeightObservation(
                index_code=code,
                index_name=name,
                weight_date=observed,
                stock_code=str(item.get("stock_code", "")),
                stock_name=str(item.get("stock_name", "")),
                weight_pct=_decimal(item.get("weight_pct"), "weight_pct"),
                is_estimated=estimated,
                first_seen_at=fetched_at,
                industry=str(item.get("industry")) if item.get("industry") is not None else None,
                contribution_pct_points=(
                    _decimal(contribution, "contribution_pct_points")
                    if contribution is not None
                    else None
                ),
                attributes={
                    "weight_official": item.get("weight_official"),
                    "close_price": item.get("close_price"),
                    "change_rate_pct": item.get("change_rate_pct"),
                    "pe": item.get("pe"),
                    "roe": item.get("roe"),
                },
            )
        )
    issues = []
    if estimated:
        issues.append(QualityIssue("estimated_index_weight", "Latest index weight is an intraday estimate pending T+1 official replacement", QualitySeverity.WARNING))
    return records, issues


def _industry_current_records(
    item: Mapping[str, object], fetched_at: datetime
) -> list[MarketNumericObservation]:
    code = str(item.get("code", ""))
    name = str(item.get("name", code))
    observed = _date(item.get("snapshot_date"), "snapshot_date")
    records: list[MarketNumericObservation] = []
    for field, unit, label in (
        ("ta_share", "%", "成交占比"),
        ("ta_share_pct", "%", "成交占比历史分位"),
        ("turnover_ratio", "ratio", "换手率"),
        ("turnover_pct", "%", "换手历史分位"),
        ("momentum", "%", "近20周动量"),
        ("momentum_pct", "%", "动量历史分位"),
        ("over_alloc", "ratio", "成交超配"),
        ("over_alloc_pct", "%", "成交超配历史分位"),
        ("composite", "%", "综合拥挤度"),
    ):
        if item.get(field) is None:
            continue
        records.append(
            _numeric(
                series_id=f"industry_crowding:{code}:{field}",
                observation_date=observed,
                value=item[field],
                unit=unit,
                frequency="weekly_snapshot",
                label=f"{name} {label}",
                fetched_at=fetched_at,
                current=True,
                attributes={"industry_name": name},
            )
        )
    return records


def _normalize_industry_crowding(
    result: Mapping[str, object], fetched_at: datetime
) -> tuple[list[MarketNumericObservation], list[QualityIssue]]:
    records: list[MarketNumericObservation] = []
    if isinstance(result.get("industries"), list):
        for raw in _sequence(result.get("industries"), "industries"):
            records.extend(_industry_current_records(_mapping(raw, "industry"), fetched_at))
        return records, []
    industry = _mapping(result.get("industry"), "industry")
    records.extend(_industry_current_records(industry, fetched_at))
    history = result.get("history")
    if isinstance(history, Mapping):
        metric = str(history.get("metric", ""))
        code = str(industry.get("code", ""))
        name = str(industry.get("name", code))
        current_date = _date(industry.get("snapshot_date"), "snapshot_date")
        existing = {(item.series_id, item.observation_date) for item in records}
        for raw in _sequence(history.get("series"), "history.series"):
            point = _mapping(raw, "history point")
            observed = _date(point.get("date"), "history.date")
            key = (f"industry_crowding:{code}:ta_share", observed)
            if key in existing:
                continue
            records.append(
                _numeric(
                    series_id=key[0],
                    observation_date=observed,
                    value=point.get("share"),
                    unit="%",
                    frequency="weekly",
                    label=f"{name} 成交占比",
                    fetched_at=fetched_at,
                    current=observed == current_date,
                    attributes={"provider_history_metric": metric, "industry_name": name},
                )
            )
    return records, []


def _normalize_market_series(
    result: Mapping[str, object], fetched_at: datetime
) -> tuple[list[MarketNumericObservation], list[QualityIssue]]:
    if isinstance(result.get("available_keys"), list):
        return [], [QualityIssue("catalog_only_market_series", "Market-series key catalog has no observations", QualitySeverity.ERROR)]
    dataset = str(result.get("dataset", ""))
    key = str(result.get("key", ""))
    label = str(result.get("label", key))
    unit = str(result.get("unit", "")).strip()
    latest = _mapping(result.get("latest"), "latest")
    latest_date = _date(latest.get("date"), "latest.date")
    records = []
    for raw in _sequence(result.get("series"), "series"):
        point = _mapping(raw, "series point")
        observed = _date(point.get("date"), "series.date")
        records.append(
            _numeric(
                series_id=f"market_series:{dataset}:{key}",
                observation_date=observed,
                value=point.get("value"),
                unit=unit,
                frequency="daily",
                label=label,
                fetched_at=fetched_at,
                current=observed == latest_date,
            )
        )
    return records, []


def _normalize_macro(
    result: Mapping[str, object], fetched_at: datetime, as_of: datetime
) -> tuple[list[MarketNumericObservation], list[QualityIssue]]:
    indicator = str(result.get("indicator", ""))
    records: list[MarketNumericObservation] = []
    issues: list[QualityIssue] = []
    for raw_series in _sequence(result.get("series"), "series"):
        series = _mapping(raw_series, "macro series")
        key = str(series.get("series", ""))
        if key not in _MACRO_UNITS:
            raise ValueError(f"Unknown macro series unit mapping: {key}")
        latest = _mapping(series.get("latest"), "macro latest")
        latest_date = _date(latest.get("date"), "macro latest.date")
        for raw in _sequence(series.get("data"), "macro data"):
            point = _mapping(raw, "macro point")
            if point.get("value") is None:
                issues.append(QualityIssue("null_macro_value_skipped", f"Skipped unreleased or null macro point for {key}", QualitySeverity.WARNING))
                continue
            observed = _date(point.get("date"), "macro date")
            published = _date(point.get("publish_date"), "publish_date") if point.get("publish_date") else None
            if published is not None and published > as_of.date():
                issues.append(QualityIssue("future_macro_release_skipped", f"Skipped future macro release for {key}: {published}", QualitySeverity.WARNING))
                continue
            frequency = "quarterly" if indicator == "gdp" else ("daily" if indicator == "buffett" else "monthly")
            records.append(
                _numeric(
                    series_id=f"macro:{key}",
                    observation_date=observed,
                    value=point.get("value"),
                    unit=_MACRO_UNITS[key],
                    frequency=frequency,
                    label=str(series.get("description", key)),
                    fetched_at=fetched_at,
                    current=observed == latest_date,
                    published_date=published,
                    attributes={"indicator": indicator, "time_label": point.get("time_label")},
                )
            )
    return records, issues


def normalize_guchacha_result(
    *,
    batch_id: str,
    tool_name: str,
    arguments: Mapping[str, object],
    result: object,
    fetched_at: datetime,
    as_of: datetime,
    raw_content_sha256: str,
) -> MarketDataBatch:
    body = _mapping(result, "tool result")
    numeric: list[MarketNumericObservation] = []
    weights: list[IndexWeightObservation] = []
    catalog: list[DatasetCatalogObservation] = []
    issues: list[QualityIssue] = []
    if tool_name == "list_datasets":
        catalog, issues = _normalize_catalog(body)
    elif tool_name == "get_index_valuation":
        numeric, issues = _normalize_index_valuation(body, fetched_at)
    elif tool_name == "get_index_weight":
        weights, issues = _normalize_index_weights(body, fetched_at)
    elif tool_name == "get_index_forward_pe":
        numeric, issues = _normalize_forward_pe(body, fetched_at)
    elif tool_name == "get_industry_crowding":
        numeric, issues = _normalize_industry_crowding(body, fetched_at)
    elif tool_name == "get_market_series":
        numeric, issues = _normalize_market_series(body, fetched_at)
    elif tool_name == "get_macro":
        numeric, issues = _normalize_macro(body, fetched_at, as_of)
    else:
        raise ValueError(f"Unsupported Guchacha normalizer: {tool_name}")
    return MarketDataBatch(
        provider_id="guchacha_mcp",
        batch_id=batch_id,
        tool_name=tool_name,
        fetched_at=fetched_at,
        as_of=as_of,
        request_arguments=dict(arguments),
        raw_content_sha256=raw_content_sha256,
        schema_sha256=APPROVED_TOOLS_SCHEMA_SHA256,
        numeric_observations=tuple(numeric),
        index_weights=tuple(weights),
        catalog_observations=tuple(catalog),
        quality_issues=tuple(issues),
    )


def normalized_batch_sha256(batch: MarketDataBatch) -> str:
    payload = {
        "provider_id": batch.provider_id,
        "batch_id": batch.batch_id,
        "tool_name": batch.tool_name,
        "fetched_at": batch.fetched_at.isoformat(),
        "as_of": batch.as_of.isoformat(),
        "request_arguments": batch.request_arguments,
        "raw_content_sha256": batch.raw_content_sha256,
        "schema_sha256": batch.schema_sha256,
        "numeric": [
            {
                "series_id": r.series_id,
                "date": r.observation_date.isoformat(),
                "value": str(r.value),
                "unit": r.unit,
                "frequency": r.frequency,
                "label": r.label,
                "published_date": r.published_date.isoformat() if r.published_date else None,
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                "visibility": r.visibility_status.value,
                "attributes": r.attributes,
            }
            for r in batch.numeric_observations
        ],
        "weights": [
            {
                "index_code": r.index_code,
                "index_name": r.index_name,
                "weight_date": r.weight_date.isoformat(),
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "weight_pct": str(r.weight_pct),
                "is_estimated": r.is_estimated,
                "first_seen_at": r.first_seen_at.isoformat(),
                "industry": r.industry,
                "contribution_pct_points": str(r.contribution_pct_points) if r.contribution_pct_points is not None else None,
                "attributes": r.attributes,
            }
            for r in batch.index_weights
        ],
        "catalog": [
            {
                "dataset_name": r.dataset_name,
                "tool_name": r.tool_name,
                "description": r.description,
                "last_updated": r.last_updated.isoformat(),
            }
            for r in batch.catalog_observations
        ],
        "issues": [r.to_dict() for r in batch.quality_issues],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
