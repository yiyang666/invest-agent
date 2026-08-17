"""Robustness and safety checks for the research-only drawdown budget strategy."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint, load_nav_series
from invest_agent.strategies.drawdown_add import (
    build_drawdown_budget_signal,
    summarize_drawdown_tier_coverage,
)

from .local_research import run_local_dca_research


ENGINE_VERSION = "drawdown_budget_robustness_v1"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_drawdown_robustness_spec(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("drawdown robustness specification must be an object")
    _validate_spec(payload)
    return payload


def _validate_spec(spec: Mapping[str, object]) -> None:
    if spec.get("schema_version") != 1:
        raise ValueError("drawdown robustness specification must use schema_version 1")
    variants = spec.get("signal_variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError("signal_variants must be a non-empty list")
    ids = [str(item.get("variant_id", "")) for item in variants if isinstance(item, Mapping)]
    if len(ids) != len(variants) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("signal variant IDs must be non-empty and unique")
    if spec.get("primary_variant_id") not in ids:
        raise ValueError("primary_variant_id must identify one variant")
    synthetic = spec.get("synthetic_drawdowns")
    if not isinstance(synthetic, list) or not synthetic:
        raise ValueError("synthetic_drawdowns must be a non-empty list")
    proxy = spec.get("long_history_proxy")
    if not isinstance(proxy, Mapping) or not isinstance(proxy.get("funds"), Mapping):
        raise ValueError("long_history_proxy funds are required")
    if not isinstance(proxy.get("raw_weights"), Mapping):
        raise ValueError("long_history_proxy raw_weights are required")
    if set(proxy["funds"]) != set(proxy["raw_weights"]):
        raise ValueError("proxy funds and weights must use identical sleeves")


def _signal_kwargs(raw: Mapping[str, object]) -> dict[str, object]:
    integer_parameters = {
        "rolling_peak_observations",
        "minimum_composite_observations",
        "maximum_staleness_days",
    }
    return {
        str(key): int(value) if str(key) in integer_parameters else Decimal(str(value))
        for key, value in raw.items()
    }


def _synthetic_series(
    sleeves: Sequence[str], *, terminal_level: Decimal, observations: int = 140
) -> dict[str, tuple[str, tuple[NavPoint, ...]]]:
    end = date(2026, 7, 31)
    start = end - timedelta(days=observations - 1)
    result: dict[str, tuple[str, tuple[NavPoint, ...]]] = {}
    for index, sleeve in enumerate(sorted(sleeves), start=1):
        points = tuple(
            NavPoint(
                nav_date=start + timedelta(days=offset),
                nav=terminal_level if offset == observations - 1 else Decimal("1"),
                visibility_status="synthetic_safety_test",
                batch_id=f"synthetic-{index}",
                content_sha256=f"synthetic-{index}",
            )
            for offset in range(observations)
        )
        result[sleeve] = (f"{index:06d}", points)
    return result


def _synthetic_checks(
    spec: Mapping[str, object], scenario: Mapping[str, object]
) -> list[dict[str, object]]:
    engine = scenario["allocation_engine"]
    assert isinstance(engine, Mapping)
    weights = {key: Decimal(str(value)) for key, value in engine["composite_weights"].items()}
    rows: list[dict[str, object]] = []
    for raw_drawdown in spec["synthetic_drawdowns"]:
        drawdown = Decimal(str(raw_drawdown))
        signal = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1),
            fund_series=_synthetic_series(
                tuple(weights), terminal_level=Decimal("1") + drawdown
            ),
            weights=weights,
        )
        budget = signal["budget"]
        assert isinstance(budget, Mapping)
        destinations = budget["additional_destinations_cny"]
        assert isinstance(destinations, Mapping)
        additional = Decimal(str(budget["additional_budget_cny"]))
        total = Decimal(str(budget["total_contribution_cny"]))
        checks = {
            "monthly_total_at_or_below_5000": total <= Decimal("5000"),
            "additional_budget_at_or_below_2000": Decimal("0") <= additional <= Decimal("2000"),
            "additional_routes_only_to_broad_cores": set(destinations)
            == {"domestic_broad_core", "us_broad_core"},
            "risk_veto_removes_extra_at_or_below_20pct": (
                drawdown > Decimal("-0.20")
                or (bool(signal["risk_veto"]) and additional == 0)
            ),
            "selling_disabled": signal["selling_allowed"] is False,
        }
        rows.append(
            {
                "synthetic_drawdown": str(drawdown),
                "signal_status": signal["signal_status"],
                "reason": signal["reason"],
                "budget": budget,
                "checks": checks,
                "all_safety_checks_passed": all(checks.values()),
            }
        )
    return rows


def _variant_row(
    scenario: Mapping[str, object], result: Mapping[str, object], variant: Mapping[str, object]
) -> dict[str, object]:
    profiles = result["profile_results"]
    assert isinstance(profiles, list)
    by_id = {str(item["allocation_profile_id"]): item for item in profiles}
    strategy = by_id["drawdown_add_broad_cores"]
    matched = by_id["matched_cashflow_631_target_gap"]
    strategy_metrics = strategy["metrics"]
    matched_metrics = matched["metrics"]
    assert isinstance(strategy_metrics, Mapping) and isinstance(matched_metrics, Mapping)
    ledger = strategy["monthly_signal_ledger"]
    assert isinstance(ledger, list)
    parameters = variant["signal_parameters"]
    assert isinstance(parameters, Mapping)
    parameterized_coverage = summarize_drawdown_tier_coverage(
        ledger,
        thresholds=(
            ("tier_1", Decimal(str(parameters["first_threshold"]))),
            ("tier_2", Decimal(str(parameters["second_threshold"]))),
            ("tier_3", Decimal(str(parameters["third_threshold"]))),
            ("risk_veto", Decimal(str(parameters["risk_veto_threshold"]))),
        ),
    )
    return {
        "variant_id": variant["variant_id"],
        "is_primary": bool(variant.get("is_primary", False)),
        "signal_parameters": variant["signal_parameters"],
        "scenario_sha256": result["scenario_sha256"],
        "full_result_sha256": _canonical_sha256(result),
        "drawdown_add_metrics": strategy_metrics,
        "matched_cashflow_metrics": matched_metrics,
        "final_value_difference_vs_matched_cny": str(
            Decimal(str(strategy_metrics["final_value_cny"]))
            - Decimal(str(matched_metrics["final_value_cny"]))
        ),
        "tier_coverage": parameterized_coverage,
    }


def _month_starts(start: date, end: date) -> tuple[date, ...]:
    current = date(start.year, start.month, 1)
    result: list[date] = []
    while current <= end:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return tuple(result)


def _proxy_check(
    database: str | Path, spec: Mapping[str, object], provider_id: str
) -> dict[str, object]:
    proxy = spec["long_history_proxy"]
    assert isinstance(proxy, Mapping)
    funds = {str(key): str(value) for key, value in proxy["funds"].items()}
    raw_weights = {str(key): Decimal(str(value)) for key, value in proxy["raw_weights"].items()}
    total_raw = sum(raw_weights.values(), Decimal("0"))
    ordered = sorted(raw_weights)
    weights = {key: raw_weights[key] / total_raw for key in ordered[:-1]}
    weights[ordered[-1]] = Decimal("1") - sum(weights.values(), Decimal("0"))
    end = date.fromisoformat(str(proxy["end_date"]))
    series = {
        sleeve: (
            code,
            load_nav_series(
                database,
                fund_code=code,
                provider_id=provider_id,
                as_of=end,
                nav_field="accumulated_nav",
            ),
        )
        for sleeve, code in funds.items()
    }
    start = date.fromisoformat(str(proxy["review_start_date"]))
    signals = [
        build_drawdown_budget_signal(
            review_date=review_date,
            fund_series=series,
            weights=weights,
        )
        for review_date in _month_starts(start, end)
    ]
    usable = [signal for signal in signals if signal["composite"] is not None]
    bindings = {
        sleeve: {
            "fund_code": code,
            "first_nav_date": points[0].nav_date.isoformat(),
            "last_nav_date": points[-1].nav_date.isoformat(),
            "observations": len(points),
            "batch_content_sha256": sorted({point.content_sha256 for point in points}),
        }
        for sleeve, (code, points) in series.items()
    }
    return {
        "proxy_id": proxy["proxy_id"],
        "classification": "historical_diagnostic_only",
        "review_start_date": start.isoformat(),
        "review_end_date": end.isoformat(),
        "review_months": len(signals),
        "usable_signal_months": len(usable),
        "normalized_weights": {key: str(weights[key]) for key in sorted(weights)},
        "tier_coverage": summarize_drawdown_tier_coverage(signals),
        "basis_risk": proxy["basis_risk"],
        "data_bindings": bindings,
    }


def run_drawdown_robustness(
    *,
    database: str | Path,
    drawdown_scenario: Mapping[str, object],
    robustness_spec: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
) -> dict[str, object]:
    _validate_spec(robustness_spec)
    rows: list[dict[str, object]] = []
    for variant in robustness_spec["signal_variants"]:
        assert isinstance(variant, Mapping)
        scenario = deepcopy(dict(drawdown_scenario))
        scenario["scenario_id"] = f"{robustness_spec['robustness_id']}_{variant['variant_id']}"
        engine = scenario["allocation_engine"]
        assert isinstance(engine, dict)
        engine["signal_parameters"] = deepcopy(dict(variant["signal_parameters"]))
        result = run_local_dca_research(
            database=database,
            scenario=scenario,
            strategy_spec=strategy_spec,
            strategy_spec_bytes=strategy_spec_bytes,
        )
        rows.append(_variant_row(scenario, result, variant))
    provider_id = str(drawdown_scenario["provider_id"])
    payload: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "robustness_id": robustness_spec["robustness_id"],
        "primary_variant_id": robustness_spec["primary_variant_id"],
        "synthetic_safety": {
            "scope": "control-flow_and_budget_invariants_only",
            "economic_return_validation": False,
            "rows": _synthetic_checks(robustness_spec, drawdown_scenario),
        },
        "neighbor_sensitivity": {
            "winner_selection_allowed": False,
            "rows": rows,
        },
        "long_history_proxy": _proxy_check(database, robustness_spec, provider_id),
        "interpretation": robustness_spec["interpretation_rules"],
        "strategy_spec_sha256": hashlib.sha256(strategy_spec_bytes).hexdigest(),
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["report_sha256"] = _canonical_sha256(payload)
    return payload
