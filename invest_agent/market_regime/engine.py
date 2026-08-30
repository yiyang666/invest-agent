"""Build a point-in-time-aware global market-state snapshot from local evidence.

This module deliberately separates market observation from investment action.  It
can describe whether available evidence is supportive, balanced, restrictive, or
insufficient; it cannot predict returns, size orders, or bypass the risk layer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
MODEL_VERSION = "global_market_regime_v1"
_STATE_SCORE = {"supportive": 1.0, "balanced": 0.0, "restrictive": -1.0}


def load_regime_config(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Global market-regime config must be a JSON object")
    _validate_config(payload)
    return payload


def _validate_config(config: Mapping[str, object]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported global market-regime config schema")
    if not isinstance(config.get("model_id"), str) or not config["model_id"]:
        raise ValueError("model_id is required")
    axes = config.get("axes")
    if not isinstance(axes, list) or not axes:
        raise ValueError("axes must be a non-empty array")
    seen_axes: set[str] = set()
    seen_features: set[str] = set()
    total_axis_weight = Decimal("0")
    for axis in axes:
        if not isinstance(axis, dict):
            raise ValueError("Each axis must be an object")
        axis_id = str(axis.get("axis_id", ""))
        if not axis_id or axis_id in seen_axes:
            raise ValueError(f"Invalid or duplicate axis_id: {axis_id!r}")
        seen_axes.add(axis_id)
        weight = Decimal(str(axis.get("weight", 0)))
        if weight <= 0:
            raise ValueError(f"Axis weight must be positive: {axis_id}")
        total_axis_weight += weight
        features = axis.get("features")
        if not isinstance(features, list) or not features:
            raise ValueError(f"Axis {axis_id} must declare expected features")
        for feature in features:
            if not isinstance(feature, dict):
                raise ValueError(f"Axis {axis_id} has a non-object feature")
            feature_id = str(feature.get("feature_id", ""))
            if not feature_id or feature_id in seen_features:
                raise ValueError(f"Invalid or duplicate feature_id: {feature_id!r}")
            seen_features.add(feature_id)
            if Decimal(str(feature.get("weight", 0))) <= 0:
                raise ValueError(f"Feature weight must be positive: {feature_id}")
            calculation = feature.get("calculation")
            if calculation not in {
                "not_connected",
                "latest_threshold",
                "spread_threshold",
                "trailing_percentile_threshold",
                "trailing_return_threshold",
                "moving_average_gap_threshold",
                "drawdown_threshold",
                "panel_average_drawdown_threshold",
                "cross_section_median_threshold",
            }:
                raise ValueError(f"Unsupported calculation for {feature_id}: {calculation}")
    if abs(total_axis_weight - Decimal("1")) > Decimal("0.000001"):
        raise ValueError("Axis weights must sum to 1")


def _as_of_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of datetime must include a timezone")
        return value
    return datetime.combine(value, time.max, tzinfo=SHANGHAI)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Stored market timestamp is missing timezone: {value}")
    return parsed


def _load_series_rows(
    database: str | Path,
    *,
    series_id: str,
    as_of: datetime,
    allow_historical_visibility_assumed: bool,
) -> list[dict[str, object]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT o.series_id, o.observation_date, o.value, o.unit, o.frequency,
                   o.published_date, o.first_seen_at, o.visibility_status,
                   o.batch_id, o.provider_id, b.fetched_at, b.content_sha256,
                   b.quality_status
            FROM market_numeric_observations AS o
            JOIN market_data_batches AS b ON b.batch_id = o.batch_id
            WHERE o.series_id = ?
              AND o.observation_date <= ?
              AND (o.published_date IS NULL OR o.published_date <= ?)
              AND b.quality_status != 'fail'
            ORDER BY o.observation_date, b.fetched_at, o.batch_id
            """,
            (series_id, as_of.date().isoformat(), as_of.date().isoformat()),
        ).fetchall()
    finally:
        connection.close()

    eligible: list[dict[str, object]] = []
    for raw in rows:
        row = dict(raw)
        visibility = str(row["visibility_status"])
        first_seen = _parse_datetime(
            str(row["first_seen_at"]) if row["first_seen_at"] is not None else None
        )
        if visibility == "strict_point_in_time":
            if first_seen is None or first_seen > as_of:
                continue
        elif not allow_historical_visibility_assumed:
            continue
        row["parsed_first_seen_at"] = first_seen
        row["parsed_fetched_at"] = _parse_datetime(str(row["fetched_at"]))
        eligible.append(row)
    return eligible


def _deduplicate_series(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    by_date: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["observation_date"])].append(row)
    points: list[dict[str, object]] = []
    for observation_date, versions in sorted(by_date.items()):
        selected = max(
            versions,
            key=lambda item: (
                item["parsed_first_seen_at"] or item["parsed_fetched_at"],
                str(item["batch_id"]),
            ),
        )
        point = dict(selected)
        distinct_values = sorted({str(item["value"]) for item in versions})
        provider_values = {
            (str(item["provider_id"]), str(item["value"])) for item in versions
        }
        point["version_count"] = len(versions)
        point["revised_values"] = distinct_values
        point["provider_value_conflict"] = (
            len({provider for provider, _ in provider_values}) > 1
            and len({value for _, value in provider_values}) > 1
        )
        point["observation_date"] = observation_date
        point["value_decimal"] = Decimal(str(point["value"]))
        points.append(point)
    return points


def _bands(feature: Mapping[str, object], value: Decimal) -> tuple[str, float]:
    bands = feature.get("bands")
    if not isinstance(bands, Mapping):
        raise ValueError(f"Feature {feature.get('feature_id')} requires bands")
    low_max = Decimal(str(bands.get("low_max")))
    high_min = Decimal(str(bands.get("high_min")))
    if low_max >= high_min:
        raise ValueError(f"Feature {feature.get('feature_id')} has overlapping bands")
    if value <= low_max:
        state = str(bands.get("low_state"))
    elif value >= high_min:
        state = str(bands.get("high_state"))
    else:
        state = str(bands.get("middle_state", "balanced"))
    if state not in _STATE_SCORE:
        raise ValueError(f"Unknown feature state: {state}")
    return state, _STATE_SCORE[state]


def _evidence_from_points(points: Sequence[Mapping[str, object]]) -> dict[str, object]:
    batch_ids = sorted({str(point["batch_id"]) for point in points})
    hashes = sorted({str(point["content_sha256"]) for point in points})
    providers = sorted({str(point["provider_id"]) for point in points})
    visibility = sorted({str(point["visibility_status"]) for point in points})
    return {
        "batch_ids": batch_ids,
        "content_sha256": hashes,
        "provider_ids": providers,
        "visibility_statuses": visibility,
        "strict_point_in_time": visibility == ["strict_point_in_time"],
    }


def _missing_feature(feature: Mapping[str, object], reason: str) -> dict[str, object]:
    return {
        "feature_id": str(feature["feature_id"]),
        "region": str(feature.get("region", "global")),
        "weight": float(Decimal(str(feature["weight"]))),
        "status": "missing",
        "reason": reason,
        "state": "unknown",
        "score": None,
        "value": None,
        "unit": None,
        "observation_date": None,
        "evidence": None,
    }


def _series_for_feature(
    database: str | Path,
    *,
    series_id: str,
    as_of: datetime,
    allow_historical_visibility_assumed: bool,
) -> list[dict[str, object]]:
    return _deduplicate_series(
        _load_series_rows(
            database,
            series_id=series_id,
            as_of=as_of,
            allow_historical_visibility_assumed=allow_historical_visibility_assumed,
        )
    )


def _series_ids_for_prefix(
    database: str | Path,
    *,
    prefix: str,
    as_of: datetime,
    suffix: str | None = None,
) -> list[str]:
    if not prefix:
        raise ValueError("series_id_prefix must be a non-empty literal prefix")
    escaped_prefix = (
        prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT series_id
            FROM market_numeric_observations
            WHERE series_id LIKE ? ESCAPE '\\'
              AND observation_date <= ?
            ORDER BY series_id
            """,
            (f"{escaped_prefix}%", as_of.date().isoformat()),
        ).fetchall()
    finally:
        connection.close()
    series_ids = [str(row[0]) for row in rows]
    return [series_id for series_id in series_ids if suffix is None or series_id.endswith(suffix)]


def _calculate_feature(
    database: str | Path,
    *,
    feature: Mapping[str, object],
    as_of: datetime,
    allow_historical_visibility_assumed: bool,
) -> dict[str, object]:
    if feature["calculation"] == "not_connected":
        return _missing_feature(feature, str(feature.get("missing_reason", "not_connected")))

    calculation = str(feature["calculation"])
    if calculation == "cross_section_median_threshold":
        prefix = feature.get("series_id_prefix")
        if not isinstance(prefix, str):
            raise ValueError(
                f"Feature {feature['feature_id']} requires series_id_prefix"
            )
        suffix = feature.get("series_id_suffix")
        if suffix is not None and not isinstance(suffix, str):
            raise ValueError(
                f"Feature {feature['feature_id']} has an invalid series_id_suffix"
            )
        series_ids = _series_ids_for_prefix(
            database,
            prefix=prefix,
            suffix=suffix,
            as_of=as_of,
        )
        minimum_series = int(feature.get("minimum_series", 20))
        if len(series_ids) < minimum_series:
            return _missing_feature(feature, "insufficient_cross_section")
    else:
        series_ids = feature.get("series_ids")
        if not isinstance(series_ids, list) or not series_ids:
            raise ValueError(f"Feature {feature['feature_id']} requires series_ids")
    series = [
        _series_for_feature(
            database,
            series_id=str(series_id),
            as_of=as_of,
            allow_historical_visibility_assumed=allow_historical_visibility_assumed,
        )
        for series_id in series_ids
    ]
    if any(not points for points in series):
        return _missing_feature(feature, "series_unavailable_as_of")
    if any(
        bool(point.get("provider_value_conflict"))
        for points in series
        for point in points
    ):
        return _missing_feature(feature, "conflicting_provider_values")

    latest_points = [points[-1] for points in series]
    newest_date = max(date.fromisoformat(str(point["observation_date"])) for point in latest_points)
    max_age_days = int(feature.get("max_age_days", 7))
    if (as_of.date() - newest_date).days > max_age_days:
        return _missing_feature(feature, "stale_observation")
    if len(latest_points) > 1:
        oldest_date = min(date.fromisoformat(str(point["observation_date"])) for point in latest_points)
        if (newest_date - oldest_date).days > int(feature.get("max_alignment_days", 7)):
            return _missing_feature(feature, "series_dates_not_aligned")

    calculation_details: dict[str, object] = {"calculation": calculation}
    if feature.get("proxy_note"):
        calculation_details["proxy_note"] = str(feature["proxy_note"])
    used_points: list[Mapping[str, object]]
    if calculation == "latest_threshold":
        value = latest_points[0]["value_decimal"]
        used_points = [latest_points[0]]
    elif calculation == "spread_threshold":
        if len(latest_points) != 2:
            raise ValueError(f"Spread feature {feature['feature_id']} requires two series")
        value = latest_points[0]["value_decimal"] - latest_points[1]["value_decimal"]
        used_points = latest_points
        calculation_details["formula"] = f"{series_ids[0]} - {series_ids[1]}"
    elif calculation == "trailing_percentile_threshold":
        points = series[0]
        minimum = int(feature.get("minimum_history_points", 60))
        if len(points) < minimum:
            return _missing_feature(feature, "insufficient_history")
        window = int(feature.get("history_observations", len(points)))
        used_points = points[-window:]
        current = used_points[-1]["value_decimal"]
        value = Decimal(
            sum(point["value_decimal"] <= current for point in used_points)
        ) / Decimal(len(used_points)) * Decimal("100")
        calculation_details["history_points"] = len(used_points)
        calculation_details["raw_latest_value"] = float(current)
    elif calculation == "trailing_return_threshold":
        points = series[0]
        lookback = int(feature.get("lookback_observations", 20))
        if len(points) <= lookback:
            return _missing_feature(feature, "insufficient_history")
        used_points = points[-(lookback + 1):]
        value = (used_points[-1]["value_decimal"] / used_points[0]["value_decimal"] - 1) * 100
        calculation_details["lookback_observations"] = lookback
    elif calculation == "moving_average_gap_threshold":
        points = series[0]
        window = int(feature.get("window_observations", 120))
        if len(points) < window:
            return _missing_feature(feature, "insufficient_history")
        used_points = points[-window:]
        average = sum((point["value_decimal"] for point in used_points), Decimal("0")) / Decimal(window)
        value = (used_points[-1]["value_decimal"] / average - 1) * 100
        calculation_details["window_observations"] = window
    elif calculation == "drawdown_threshold":
        points = series[0]
        window = int(feature.get("window_observations", 252))
        if len(points) < window:
            return _missing_feature(feature, "insufficient_history")
        used_points = points[-window:]
        peak = max(point["value_decimal"] for point in used_points)
        value = (used_points[-1]["value_decimal"] / peak - 1) * 100
        calculation_details["window_observations"] = window
    elif calculation == "panel_average_drawdown_threshold":
        window = int(feature.get("window_observations", 252))
        if any(len(points) < window for points in series):
            return _missing_feature(feature, "insufficient_history")
        panel_windows = [points[-window:] for points in series]
        component_drawdowns: dict[str, Decimal] = {}
        used_points = []
        for series_id, points in zip(series_ids, panel_windows):
            peak = max(point["value_decimal"] for point in points)
            component_drawdowns[str(series_id)] = (
                points[-1]["value_decimal"] / peak - 1
            ) * 100
            used_points.extend(points)
        value = sum(component_drawdowns.values(), Decimal("0")) / Decimal(
            len(component_drawdowns)
        )
        calculation_details["window_observations"] = window
        calculation_details["component_drawdowns_pct"] = {
            key: float(component_drawdowns[key]) for key in sorted(component_drawdowns)
        }
    elif calculation == "cross_section_median_threshold":
        used_points = latest_points
        value = Decimal(str(statistics.median(point["value_decimal"] for point in used_points)))
        calculation_details["series_count"] = len(used_points)
        calculation_details["series_id_prefix"] = str(feature["series_id_prefix"])
        if feature.get("series_id_suffix"):
            calculation_details["series_id_suffix"] = str(feature["series_id_suffix"])
    else:  # protected by config validation
        raise ValueError(f"Unsupported calculation: {calculation}")

    state, score = _bands(feature, value)
    evidence = _evidence_from_points(used_points)
    return {
        "feature_id": str(feature["feature_id"]),
        "region": str(feature.get("region", "global")),
        "weight": float(Decimal(str(feature["weight"]))),
        "status": "available",
        "reason": None,
        "state": state,
        "score": score,
        "value": float(value),
        "unit": str(feature.get("output_unit", latest_points[0]["unit"])),
        "observation_date": newest_date.isoformat(),
        "calculation_details": calculation_details,
        "evidence": evidence,
    }


def _state_for_score(score: float) -> str:
    if score >= 0.25:
        return "supportive"
    if score <= -0.25:
        return "restrictive"
    return "balanced"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_global_market_state_snapshot(
    database: str | Path,
    *,
    config: Mapping[str, object],
    as_of: date | datetime,
) -> dict[str, object]:
    """Build a research-only snapshot without any strategy or execution output."""
    _validate_config(config)
    effective_as_of = _as_of_datetime(as_of)
    allow_assumed = bool(config.get("allow_historical_visibility_assumed", True))
    axis_results: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []
    all_batch_ids: set[str] = set()
    all_hashes: set[str] = set()
    all_providers: set[str] = set()
    available_feature_weight = Decimal("0")
    strict_feature_weight = Decimal("0")

    for raw_axis in config["axes"]:  # type: ignore[index]
        axis = raw_axis
        axis_weight = Decimal(str(axis["weight"]))
        features = [
            _calculate_feature(
                database,
                feature=feature,
                as_of=effective_as_of,
                allow_historical_visibility_assumed=allow_assumed,
            )
            for feature in axis["features"]
        ]
        expected_weight = sum(
            (Decimal(str(feature["weight"])) for feature in axis["features"]),
            Decimal("0"),
        )
        available = [feature for feature in features if feature["status"] == "available"]
        covered_weight = sum(
            (Decimal(str(feature["weight"])) for feature in available), Decimal("0")
        )
        coverage = covered_weight / expected_weight
        if available:
            axis_score = sum(
                Decimal(str(feature["score"])) * Decimal(str(feature["weight"]))
                for feature in available
            ) / covered_weight
            axis_state = _state_for_score(float(axis_score))
        else:
            axis_score = None
            axis_state = "unknown"
        for feature in features:
            if feature["status"] != "available":
                missing.append(
                    {
                        "axis_id": str(axis["axis_id"]),
                        "feature_id": str(feature["feature_id"]),
                        "reason": str(feature["reason"]),
                    }
                )
                continue
            feature_weight = axis_weight * Decimal(str(feature["weight"])) / expected_weight
            available_feature_weight += feature_weight
            evidence = feature["evidence"]
            if evidence["strict_point_in_time"]:
                strict_feature_weight += feature_weight
            all_batch_ids.update(evidence["batch_ids"])
            all_hashes.update(evidence["content_sha256"])
            all_providers.update(evidence["provider_ids"])
        axis_results.append(
            {
                "axis_id": str(axis["axis_id"]),
                "label": str(axis.get("label", axis["axis_id"])),
                "weight": float(axis_weight),
                "coverage_pct": float(coverage * 100),
                "state": axis_state,
                "score": float(axis_score) if axis_score is not None else None,
                "features": features,
            }
        )

    total_axis_weight = sum(
        (Decimal(str(axis["weight"])) for axis in config["axes"]), Decimal("0")  # type: ignore[index]
    )
    coverage = available_feature_weight / total_axis_weight
    weighted_scores = [
        (
            Decimal(str(axis["score"])),
            Decimal(str(axis["weight"])) * Decimal(str(axis["coverage_pct"])) / 100,
        )
        for axis in axis_results
        if axis["score"] is not None and axis["coverage_pct"] > 0
    ]
    score_denominator = sum((weight for _, weight in weighted_scores), Decimal("0"))
    observed_score = (
        sum((score * weight for score, weight in weighted_scores), Decimal("0"))
        / score_denominator
        if score_denominator
        else None
    )
    minimum_coverage = Decimal(str(config.get("minimum_coverage_for_state", "0.60")))
    overall_state = (
        _state_for_score(float(observed_score))
        if observed_score is not None and coverage >= minimum_coverage
        else "insufficient_evidence"
    )
    strict_ratio = (
        strict_feature_weight / available_feature_weight
        if available_feature_weight
        else Decimal("0")
    )
    diversity_target = max(1, int(config.get("source_diversity_target", 2)))
    diversity_ratio = min(Decimal(len(all_providers)) / Decimal(diversity_target), Decimal("1"))
    confidence = coverage * (Decimal("0.5") + strict_ratio / 2) * (
        Decimal("0.5") + diversity_ratio / 2
    )
    confidence_label = "high" if confidence >= Decimal("0.70") else (
        "medium" if confidence >= Decimal("0.40") else "low"
    )

    config_hash = _canonical_sha256(config)
    conflicts = [item for item in missing if item["reason"] == "conflicting_provider_values"]
    body: dict[str, object] = {
        "schema_version": 1,
        "model_id": str(config["model_id"]),
        "model_version": MODEL_VERSION,
        "as_of": effective_as_of.isoformat(),
        "mode": "research_only",
        "overall_state": overall_state,
        "observed_composite_score": float(observed_score) if observed_score is not None else None,
        "axes": axis_results,
        "confidence": {
            "score_pct": float(confidence * 100),
            "label": confidence_label,
            "coverage_pct": float(coverage * 100),
            "strict_point_in_time_pct": float(strict_ratio * 100),
            "independent_provider_count": len(all_providers),
            "source_diversity_target": diversity_target,
            "formal_decision_eligible": False,
        },
        "missing_capabilities": missing,
        "conflicts": conflicts,
        "guardrails": {
            "market_direction_prediction_allowed": False,
            "strategy_signal_generation_allowed": False,
            "order_intent_generation_allowed": False,
            "real_trading_allowed": False,
        },
        "reproducibility": {
            "config_sha256": config_hash,
            "batch_ids": sorted(all_batch_ids),
            "content_sha256": sorted(all_hashes),
            "provider_ids": sorted(all_providers),
            "local_data_only": True,
        },
    }
    body["snapshot_id"] = f"gms-{effective_as_of:%Y%m%d}-{_canonical_sha256(body)[:16]}"
    return body
