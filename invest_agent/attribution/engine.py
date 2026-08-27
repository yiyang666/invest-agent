"""Cash-flow, sleeve, benchmark and sequential strategy attribution."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Mapping, Sequence


ENGINE_VERSION = "phase8_attribution_v1"
MONEY = Decimal("0.01")
RATE = Decimal("0.000000000001")
ALLOWED_CLAIM_SCOPES = {
    "baseline",
    "total_policy_outcome",
    "execution_effect",
    "signal_effect",
    "risk_overlay_effect",
    "combined_strategy_effect",
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _decimal(value: object, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be decimal-compatible") from exc


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _money(value: Decimal) -> str:
    return str(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def _rate(value: Decimal) -> str:
    return str(value.quantize(RATE, rounding=ROUND_HALF_UP))


def calculate_cashflow_attribution(
    periods: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Calculate Modified Dietz periods and geometrically linked portfolio return."""

    if not periods:
        raise ValueError("cashflow attribution requires at least one period")
    rows: list[dict[str, object]] = []
    linked_growth = Decimal("1")
    total_flow = Decimal("0")
    total_pnl = Decimal("0")
    total_fees = Decimal("0")
    total_cash_drag_cost = Decimal("0")
    prior_end_date: date | None = None
    prior_end_value: Decimal | None = None

    for raw in periods:
        start_date = date.fromisoformat(str(raw.get("period_start", "")))
        end_date = date.fromisoformat(str(raw.get("period_end", "")))
        if end_date <= start_date:
            raise ValueError("attribution period end must be after start")
        start_value = _decimal(raw.get("start_value_cny"), "start_value_cny")
        end_value = _decimal(raw.get("end_value_cny"), "end_value_cny")
        fees = _decimal(raw.get("fees_cny", "0"), "fees_cny")
        cash_drag = _decimal(
            raw.get("cash_drag_cost_cny", "0"), "cash_drag_cost_cny"
        )
        if min(start_value, end_value, fees, cash_drag) < 0:
            raise ValueError("values and observed costs cannot be negative")
        if prior_end_date is not None and (
            start_date != prior_end_date or start_value != prior_end_value
        ):
            raise ValueError("attribution periods must be contiguous and value-reconciled")

        raw_flows = raw.get("external_flows", [])
        if not isinstance(raw_flows, list):
            raise ValueError("external_flows must be a list")
        net_flow = Decimal("0")
        weighted_flow = Decimal("0")
        normalized_flows: list[dict[str, str]] = []
        for flow in raw_flows:
            if not isinstance(flow, Mapping):
                raise ValueError("each external flow must be an object")
            amount = _decimal(flow.get("amount_cny"), "external flow amount")
            weight = _decimal(flow.get("remaining_weight"), "remaining_weight")
            if weight < 0 or weight > 1:
                raise ValueError("remaining_weight must be between 0 and 1")
            net_flow += amount
            weighted_flow += amount * weight
            normalized_flows.append(
                {"amount_cny": _money(amount), "remaining_weight": _rate(weight)}
            )
        denominator = start_value + weighted_flow
        if denominator <= 0:
            raise ValueError("Modified Dietz denominator must be positive")
        investment_pnl = end_value - start_value - net_flow
        period_return = investment_pnl / denominator
        if period_return <= Decimal("-1"):
            raise ValueError("period return cannot be at or below -100%")
        linked_growth *= Decimal("1") + period_return

        rows.append(
            {
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "start_value_cny": _money(start_value),
                "end_value_cny": _money(end_value),
                "external_flows": normalized_flows,
                "net_external_flow_cny": _money(net_flow),
                "weighted_capital_cny": _money(denominator),
                "investment_pnl_cny": _money(investment_pnl),
                "fees_cny": _money(fees),
                "cash_drag_cost_cny": _money(cash_drag),
                "pnl_before_observed_costs_cny": _money(
                    investment_pnl + fees + cash_drag
                ),
                "modified_dietz_return": _rate(period_return),
            }
        )
        total_flow += net_flow
        total_pnl += investment_pnl
        total_fees += fees
        total_cash_drag_cost += cash_drag
        prior_end_date = end_date
        prior_end_value = end_value

    first_value = _decimal(periods[0].get("start_value_cny"), "start_value_cny")
    final_value = _decimal(periods[-1].get("end_value_cny"), "end_value_cny")
    if first_value + total_flow + total_pnl != final_value:
        raise ValueError("cashflow attribution identity does not reconcile")
    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "method": "modified_dietz_then_geometric_link",
        "periods": rows,
        "summary": {
            "initial_value_cny": _money(first_value),
            "net_external_flow_cny": _money(total_flow),
            "investment_pnl_cny": _money(total_pnl),
            "observed_fees_cny": _money(total_fees),
            "observed_cash_drag_cost_cny": _money(total_cash_drag_cost),
            "final_value_cny": _money(final_value),
            "linked_twr": _rate(linked_growth - Decimal("1")),
        },
        "identity_reconciled": True,
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result


def compare_cashflow_matched_paths(
    candidate_periods: Sequence[Mapping[str, object]],
    benchmark_periods: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Compare paths only when every period and external cash flow is identical."""

    if len(candidate_periods) != len(benchmark_periods):
        raise ValueError("candidate and benchmark period counts differ")
    for candidate, benchmark in zip(candidate_periods, benchmark_periods, strict=True):
        for key in ("period_start", "period_end", "external_flows"):
            if candidate.get(key) != benchmark.get(key):
                raise ValueError("candidate and benchmark cashflow timelines diverged")
    candidate_result = calculate_cashflow_attribution(candidate_periods)
    benchmark_result = calculate_cashflow_attribution(benchmark_periods)
    candidate_summary = candidate_result["summary"]
    benchmark_summary = benchmark_result["summary"]
    assert isinstance(candidate_summary, Mapping)
    assert isinstance(benchmark_summary, Mapping)
    candidate_twr = _decimal(candidate_summary["linked_twr"], "candidate TWR")
    benchmark_twr = _decimal(benchmark_summary["linked_twr"], "benchmark TWR")
    candidate_final = _decimal(candidate_summary["final_value_cny"], "candidate final")
    benchmark_final = _decimal(benchmark_summary["final_value_cny"], "benchmark final")
    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "method": "matched_cashflow_path_comparison",
        "candidate": candidate_result,
        "benchmark": benchmark_result,
        "difference": {
            "linked_twr": _rate(candidate_twr - benchmark_twr),
            "linked_twr_percentage_points": _rate(
                (candidate_twr - benchmark_twr) * Decimal("100")
            ),
            "final_value_cny": _money(candidate_final - benchmark_final),
        },
        "fairness": {
            "periods_identical": True,
            "valuation_dates_identical": True,
            "external_cashflows_identical": True,
            "claim_scope": "strategy_relative_result_not_causal_signal_proof",
        },
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result


def attribute_sleeves_brinson(
    portfolio: Mapping[str, Mapping[str, object]],
    benchmark: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Decompose active return into allocation, selection and interaction effects."""

    if not portfolio or set(portfolio) != set(benchmark):
        raise ValueError("portfolio and benchmark sleeves must be identical and non-empty")
    portfolio_weights = {
        sleeve: _decimal(values.get("weight"), f"{sleeve} portfolio weight")
        for sleeve, values in portfolio.items()
    }
    benchmark_weights = {
        sleeve: _decimal(values.get("weight"), f"{sleeve} benchmark weight")
        for sleeve, values in benchmark.items()
    }
    if abs(sum(portfolio_weights.values()) - Decimal("1")) > Decimal("0.00000001"):
        raise ValueError("portfolio sleeve weights must sum to 1")
    if abs(sum(benchmark_weights.values()) - Decimal("1")) > Decimal("0.00000001"):
        raise ValueError("benchmark sleeve weights must sum to 1")

    portfolio_returns = {
        sleeve: _decimal(values.get("return"), f"{sleeve} portfolio return")
        for sleeve, values in portfolio.items()
    }
    benchmark_returns = {
        sleeve: _decimal(values.get("return"), f"{sleeve} benchmark return")
        for sleeve, values in benchmark.items()
    }
    benchmark_total = sum(
        benchmark_weights[s] * benchmark_returns[s] for s in benchmark
    )
    portfolio_total = sum(
        portfolio_weights[s] * portfolio_returns[s] for s in portfolio
    )
    rows: list[dict[str, str]] = []
    allocation_total = Decimal("0")
    selection_total = Decimal("0")
    interaction_total = Decimal("0")
    for sleeve in sorted(portfolio):
        wp = portfolio_weights[sleeve]
        wb = benchmark_weights[sleeve]
        rp = portfolio_returns[sleeve]
        rb = benchmark_returns[sleeve]
        allocation = (wp - wb) * (rb - benchmark_total)
        selection = wb * (rp - rb)
        interaction = (wp - wb) * (rp - rb)
        allocation_total += allocation
        selection_total += selection
        interaction_total += interaction
        rows.append(
            {
                "sleeve": sleeve,
                "portfolio_weight": _rate(wp),
                "benchmark_weight": _rate(wb),
                "portfolio_return": _rate(rp),
                "benchmark_return": _rate(rb),
                "allocation_effect": _rate(allocation),
                "selection_effect": _rate(selection),
                "interaction_effect": _rate(interaction),
                "total_effect": _rate(allocation + selection + interaction),
            }
        )
    active_return = portfolio_total - benchmark_total
    attributed = allocation_total + selection_total + interaction_total
    residual = active_return - attributed
    if abs(residual) > Decimal("0.000000000001"):
        raise ValueError("sleeve attribution does not reconcile")
    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "method": "brinson_fachler_allocation_selection_interaction",
        "sleeves": rows,
        "summary": {
            "portfolio_return": _rate(portfolio_total),
            "benchmark_return": _rate(benchmark_total),
            "active_return": _rate(active_return),
            "allocation_effect": _rate(allocation_total),
            "selection_effect": _rate(selection_total),
            "interaction_effect": _rate(interaction_total),
            "residual": _rate(residual),
        },
        "cash_sleeve_must_remain_explicit": True,
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result


def sequential_ablation_waterfall(
    stages: Sequence[Mapping[str, object]],
    *,
    metric_name: str = "final_value_cny",
    unit: str = "CNY",
) -> dict[str, object]:
    """Attribute an ordered, precommitted experiment chain by adjacent differences."""

    if len(stages) < 2:
        raise ValueError("sequential attribution requires at least two stages")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, stage in enumerate(stages):
        stage_id = str(stage.get("stage_id", ""))
        scope = str(stage.get("claim_scope", ""))
        if not stage_id or stage_id in seen:
            raise ValueError("stage IDs must be non-empty and unique")
        if scope not in ALLOWED_CLAIM_SCOPES:
            raise ValueError("stage claim scope is unsupported")
        if index == 0 and scope != "baseline":
            raise ValueError("the first stage must be the baseline")
        seen.add(stage_id)
        normalized.append(
            {
                "stage_id": stage_id,
                "claim_scope": scope,
                "metric_value": str(_decimal(stage.get("metric_value"), "metric_value")),
                "evidence_sha256": str(stage.get("evidence_sha256", "")),
            }
        )
        if SHA256_PATTERN.fullmatch(normalized[-1]["evidence_sha256"]) is None:
            raise ValueError("every stage requires a SHA-256 evidence binding")

    contributions: list[dict[str, str]] = []
    for previous, current in zip(normalized, normalized[1:]):
        delta = _decimal(current["metric_value"], "current metric") - _decimal(
            previous["metric_value"], "previous metric"
        )
        contributions.append(
            {
                "from_stage": previous["stage_id"],
                "to_stage": current["stage_id"],
                "claim_scope": current["claim_scope"],
                "incremental_contribution": _money(delta) if unit == "CNY" else _rate(delta),
            }
        )
    first = _decimal(normalized[0]["metric_value"], "first metric")
    last = _decimal(normalized[-1]["metric_value"], "last metric")
    total = sum(
        (_decimal(row["incremental_contribution"], "contribution") for row in contributions),
        Decimal("0"),
    )
    expected = last - first
    tolerance = MONEY if unit == "CNY" else RATE
    if abs(total - expected) > tolerance:
        raise ValueError("sequential attribution does not telescope")
    result: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "method": "precommitted_sequential_ablation_waterfall",
        "metric_name": metric_name,
        "unit": unit,
        "stages": normalized,
        "contributions": contributions,
        "total_difference": _money(expected) if unit == "CNY" else _rate(expected),
        "interpretation": {
            "adjacent_difference_only": True,
            "causal_claim_requires_frozen_same_universe_control": True,
            "automatic_winner_selection": False,
        },
        "external_side_effects": False,
    }
    result["report_sha256"] = _canonical_sha256(result)
    return result
