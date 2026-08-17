"""Historical drawdown event panel built only from validated local fund NAVs."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Mapping, Sequence

from .fund import NavPoint, load_nav_series


ENGINE_VERSION = "drawdown_event_panel_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_drawdown_event_panel_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("drawdown event panel specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("drawdown event panel must use schema_version 1")
    if spec.get("classification") != "historical_diagnostic_only":
        raise ValueError("drawdown event panel must remain historical_diagnostic_only")
    if spec.get("nav_field") != "accumulated_nav":
        raise ValueError("drawdown event panel requires accumulated_nav")
    if int(spec.get("rolling_peak_observations", 0)) < 2:
        raise ValueError("rolling_peak_observations must be at least 2")
    if int(spec.get("minimum_observations", 0)) < int(
        spec["rolling_peak_observations"]
    ) + 1:
        raise ValueError("minimum_observations must exceed rolling window")
    if int(spec.get("per_fund_minimum_episode_separation_observations", 0)) <= 0:
        raise ValueError("episode separation must be positive")
    if int(spec.get("cross_fund_cluster_days", 0)) <= 0:
        raise ValueError("cross-fund cluster days must be positive")
    thresholds = spec.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        raise ValueError("thresholds must be a non-empty list")
    values = [Decimal(str(value)) for value in thresholds]
    if not all(Decimal("0") > left > right for left, right in zip(values, values[1:])):
        raise ValueError("thresholds must be strictly decreasing below zero")
    horizons = spec.get("forward_horizon_months")
    if not isinstance(horizons, list) or not horizons or any(int(value) <= 0 for value in horizons):
        raise ValueError("forward horizons must be positive months")
    funds = spec.get("funds")
    if not isinstance(funds, list) or not funds:
        raise ValueError("funds must be a non-empty list")
    codes = [str(item.get("fund_code", "")) for item in funds if isinstance(item, Mapping)]
    if len(codes) != len(funds) or len(set(codes)) != len(codes):
        raise ValueError("fund codes must be present and unique")
    if {str(item.get("group")) for item in funds} != {
        "broad_control",
        "growth_technology",
    }:
        raise ValueError("panel must contain broad_control and growth_technology groups")


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _drawdown_path(
    points: Sequence[NavPoint], *, rolling_peak_observations: int | None
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    dates = [point.nav_date for point in points]
    review_date = date(points[0].nav_date.year, points[0].nav_date.month, 1)
    if review_date <= points[0].nav_date:
        review_date = _add_months(review_date, 1)
    final_review = _add_months(date(points[-1].nav_date.year, points[-1].nav_date.month, 1), 1)
    previous_index = -1
    while review_date <= final_review:
        index = bisect_right(dates, review_date - timedelta(days=1)) - 1
        if index < 0 or index == previous_index:
            review_date = _add_months(review_date, 1)
            continue
        point = points[index]
        start = 0 if rolling_peak_observations is None else max(
            0, index - rolling_peak_observations + 1
        )
        peak_index = max(
            range(start, index + 1), key=lambda candidate: (points[candidate].nav, candidate)
        )
        peak = points[peak_index]
        rows.append(
            {
                "index": index,
                "review_date": review_date,
                "nav_date": point.nav_date,
                "nav": point.nav,
                "peak_date": peak.nav_date,
                "peak_nav": peak.nav,
                "drawdown": point.nav / peak.nav - Decimal("1"),
            }
        )
        previous_index = index
        review_date = _add_months(review_date, 1)
    return tuple(rows)


def _future_result(
    points: Sequence[NavPoint],
    *,
    start_index: int,
    horizon_months: int,
    maximum_gap_days: int,
) -> dict[str, object]:
    start = points[start_index]
    target = _add_months(start.nav_date, horizon_months)
    dates = [point.nav_date for point in points]
    future_index = bisect_left(dates, target, lo=start_index + 1)
    if future_index >= len(points) or (points[future_index].nav_date - target).days > maximum_gap_days:
        return {
            "status": "incomplete",
            "target_date": target.isoformat(),
            "nav_date": None,
            "return": None,
        }
    future = points[future_index]
    return {
        "status": "complete",
        "target_date": target.isoformat(),
        "nav_date": future.nav_date.isoformat(),
        "return": str(future.nav / start.nav - Decimal("1")),
    }


def _episodes(
    points: Sequence[NavPoint],
    path: Sequence[Mapping[str, object]],
    *,
    threshold: Decimal,
    horizons: Sequence[int],
    maximum_gap_days: int,
    minimum_index: int,
    minimum_episode_separation_observations: int,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    previously_active = False
    last_accepted_index: int | None = None
    for row in path:
        index = int(row["index"])
        if index < minimum_index:
            continue
        drawdown = Decimal(str(row["drawdown"]))
        active = drawdown <= threshold
        separated = (
            last_accepted_index is None
            or index - last_accepted_index >= minimum_episode_separation_observations
        )
        if active and not previously_active and separated:
            peak_nav = Decimal(str(row["peak_nav"]))
            recovery = next(
                (point for point in points[index + 1 :] if point.nav >= peak_nav), None
            )
            result.append(
                {
                    "start_date": str(row["review_date"]),
                    "start_nav_date": str(row["nav_date"]),
                    "start_drawdown": str(drawdown),
                    "peak_date": str(row["peak_date"]),
                    "recovery_date": None if recovery is None else recovery.nav_date.isoformat(),
                    "recovery_days": None
                    if recovery is None
                    else (recovery.nav_date - points[index].nav_date).days,
                    "forward": {
                        f"{horizon}_months": _future_result(
                            points,
                            start_index=index,
                            horizon_months=horizon,
                            maximum_gap_days=maximum_gap_days,
                        )
                        for horizon in horizons
                    },
                }
            )
            last_accepted_index = index
        previously_active = active
    return result


def _cluster_events(
    events: Sequence[Mapping[str, object]], *, cluster_days: int
) -> list[dict[str, object]]:
    ordered = sorted(events, key=lambda item: (str(item["start_date"]), str(item["fund_code"])))
    clusters: list[list[Mapping[str, object]]] = []
    for event in ordered:
        event_date = date.fromisoformat(str(event["start_date"]))
        if not clusters:
            clusters.append([event])
            continue
        last_date = max(date.fromisoformat(str(item["start_date"])) for item in clusters[-1])
        if event_date - last_date <= timedelta(days=cluster_days):
            clusters[-1].append(event)
        else:
            clusters.append([event])
    result: list[dict[str, object]] = []
    for index, members in enumerate(clusters, start=1):
        forward: dict[str, object] = {}
        horizons = sorted(next(iter(members))["forward"])
        for horizon in horizons:
            values = [
                Decimal(str(item["forward"][horizon]["return"]))
                for item in members
                if item["forward"][horizon]["return"] is not None
            ]
            forward[horizon] = {
                "complete_member_count": len(values),
                "median_member_return": None if not values else str(median(values)),
            }
        result.append(
            {
                "cluster_id": f"event_{index:03d}",
                "start_date": min(str(item["start_date"]) for item in members),
                "end_date": max(str(item["start_date"]) for item in members),
                "fund_codes": sorted({str(item["fund_code"]) for item in members}),
                "member_count": len(members),
                "forward": forward,
            }
        )
    return result


def _cluster_summary(clusters: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not clusters:
        return {"date_cluster_count": 0, "forward": {}}
    horizons = sorted(clusters[0]["forward"])
    forward: dict[str, object] = {}
    for horizon in horizons:
        values = [
            Decimal(str(cluster["forward"][horizon]["median_member_return"]))
            for cluster in clusters
            if cluster["forward"][horizon]["median_member_return"] is not None
        ]
        forward[horizon] = {
            "complete_cluster_count": len(values),
            "positive_cluster_count": sum(value > 0 for value in values),
            "positive_rate": None
            if not values
            else str(Decimal(sum(value > 0 for value in values)) / Decimal(len(values))),
            "median_return": None if not values else str(median(values)),
            "minimum_return": None if not values else str(min(values)),
            "maximum_return": None if not values else str(max(values)),
        }
    return {"date_cluster_count": len(clusters), "forward": forward}


def calculate_drawdown_event_panel(
    database: str | Path, *, spec: Mapping[str, object]
) -> dict[str, object]:
    _validate_spec(spec)
    provider_id = str(spec["provider_id"])
    as_of = date.fromisoformat(str(spec["as_of"]))
    rolling = int(spec["rolling_peak_observations"])
    minimum = int(spec["minimum_observations"])
    thresholds = tuple(Decimal(str(value)) for value in spec["thresholds"])
    horizons = tuple(int(value) for value in spec["forward_horizon_months"])
    maximum_gap = int(spec["maximum_forward_nav_gap_days"])
    minimum_episode_separation = int(
        spec["per_fund_minimum_episode_separation_observations"]
    )
    cluster_days = int(spec["cross_fund_cluster_days"])
    fund_rows: list[dict[str, object]] = []
    all_events: list[dict[str, object]] = []
    for fund in spec["funds"]:
        assert isinstance(fund, Mapping)
        code = str(fund["fund_code"])
        points = load_nav_series(
            database,
            fund_code=code,
            provider_id=provider_id,
            as_of=as_of,
            nav_field="accumulated_nav",
        )
        methods = {
            "rolling_peak_126": _drawdown_path(
                points, rolling_peak_observations=rolling
            ),
            "expanding_peak": _drawdown_path(points, rolling_peak_observations=None),
        }
        coverage: dict[str, object] = {}
        for method, path in methods.items():
            threshold_rows: dict[str, object] = {}
            minimum_index = minimum - 1 if method == "rolling_peak_126" else 1
            for threshold in thresholds:
                episodes = _episodes(
                    points,
                    path,
                    threshold=threshold,
                    horizons=horizons,
                    maximum_gap_days=maximum_gap,
                    minimum_index=minimum_index,
                    minimum_episode_separation_observations=minimum_episode_separation,
                )
                for episode in episodes:
                    episode.update(
                        {
                            "fund_code": code,
                            "group": fund["group"],
                            "method": method,
                            "threshold": str(threshold),
                        }
                    )
                all_events.extend(episodes)
                threshold_rows[str(threshold)] = {
                    "episode_count": len(episodes),
                    "episodes": episodes,
                }
            coverage[method] = threshold_rows
        fund_rows.append(
            {
                "fund_code": code,
                "role": fund["role"],
                "group": fund["group"],
                "active_management": bool(fund["active_management"]),
                "data": {
                    "first_nav_date": points[0].nav_date.isoformat(),
                    "last_nav_date": points[-1].nav_date.isoformat(),
                    "observations": len(points),
                    "visibility_statuses": sorted({point.visibility_status for point in points}),
                    "batch_ids": sorted({point.batch_id for point in points}),
                    "batch_content_sha256": sorted({point.content_sha256 for point in points}),
                },
                "coverage": coverage,
            }
        )
    grouped: dict[str, object] = {}
    for group in ("broad_control", "growth_technology"):
        grouped[group] = {}
        for method in ("rolling_peak_126", "expanding_peak"):
            grouped[group][method] = {}
            for threshold in thresholds:
                selected = [
                    event
                    for event in all_events
                    if event["group"] == group
                    and event["method"] == method
                    and event["threshold"] == str(threshold)
                ]
                clusters = _cluster_events(selected, cluster_days=cluster_days)
                grouped[group][method][str(threshold)] = {
                    "raw_fund_episode_count": len(selected),
                    "cluster_summary": _cluster_summary(clusters),
                    "clusters": clusters,
                }
    payload: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "panel_id": spec["panel_id"],
        "classification": spec["classification"],
        "provider_id": provider_id,
        "nav_field": "accumulated_nav",
        "as_of": as_of.isoformat(),
        "parameters": {
            "rolling_peak_observations": rolling,
            "minimum_observations": minimum,
            "thresholds": [str(value) for value in thresholds],
            "forward_horizon_months": list(horizons),
            "maximum_forward_nav_gap_days": maximum_gap,
            "per_fund_minimum_episode_separation_observations": minimum_episode_separation,
            "cross_fund_cluster_days": cluster_days,
        },
        "fund_results": fund_rows,
        "group_results": grouped,
        "interpretation_rules": spec["interpretation_rules"],
        "limitations": [
            "历史回填缺少严格公告可见时间，只能用于研究。",
            "基金成立与本地数据起点不同，面板存在不等长历史和幸存者偏差。",
            "前瞻收益未模拟申购费用、限额、确认日或现金占用，不等于可执行策略回测。",
            "主动基金包含基金经理和风格漂移，不可视为稳定指数替代。"
        ],
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["report_sha256"] = _canonical_sha256(payload)
    return payload
