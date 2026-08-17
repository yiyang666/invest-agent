"""Offline assembly and research-only DCA portfolio experiments.

The module deliberately separates a complete counterfactual execution scenario
from verified historical channel rules.  A counterfactual run can compare
allocation profiles, but can never satisfy the official-rule publication gate.
"""

from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import stdev
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint, load_nav_series
from invest_agent.strategies.dca import (
    InstrumentRoute,
    build_monthly_allocation,
    generate_simulated_subscriptions,
)
from invest_agent.strategies.trend_rs import (
    build_direct_new_money_allocation,
    build_new_money_trend_signal,
)
from invest_agent.strategies.drawdown_add import (
    build_drawdown_budget_signal,
    build_drawdown_monthly_allocation,
    summarize_drawdown_tier_coverage,
)
from invest_agent.strategies.sleeve_drawdown import (
    build_sleeve_drawdown_allocation,
    build_sleeve_drawdown_signal,
)

from .subscription_engine import (
    CashContribution,
    SubscriptionExecution,
    run_subscription_events,
)
from .valuation_engine import (
    CashDistribution,
    DailyUnitNav,
    run_portfolio_valuation_events,
)


ENGINE_VERSION = "local_dca_research_v1"
RESEARCH_CLASSIFICATION = "counterfactual_research_assumption"
CENT = Decimal("0.01")


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_scenario(payload: Mapping[str, object]) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("research scenario must use schema_version 1")
    if payload.get("classification") != RESEARCH_CLASSIFICATION:
        raise ValueError("scenario must be explicitly classified as counterfactual research")
    gate = payload.get("official_rule_gate")
    if not isinstance(gate, Mapping) or gate.get("status") != "blocked":
        raise ValueError("counterfactual scenario must keep the official rule gate blocked")
    period = payload.get("period")
    if not isinstance(period, Mapping):
        raise ValueError("scenario period is required")
    start = date.fromisoformat(str(period["start_date"]))
    end = date.fromisoformat(str(period["end_date"]))
    if end <= start:
        raise ValueError("scenario end_date must be after start_date")
    amount = Decimal(str(payload.get("monthly_contribution_cny")))
    if amount <= 0:
        raise ValueError("monthly contribution must be positive")
    schedule = payload.get("monthly_contribution_schedule_cny")
    if schedule is not None:
        if not isinstance(schedule, Mapping) or not schedule:
            raise ValueError("monthly contribution schedule must be a non-empty object")
        for raw_date, raw_amount in schedule.items():
            date.fromisoformat(str(raw_date))
            if Decimal(str(raw_amount)) <= 0:
                raise ValueError("scheduled monthly contributions must be positive")
    calendar_day = int(payload.get("calendar_day", 0))
    if not 1 <= calendar_day <= 28:
        raise ValueError("calendar_day must be between 1 and 28")
    profiles = payload.get("allocation_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("at least one allocation profile is required")
    profile_ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise ValueError("allocation profile must be an object")
        profile_id = str(profile.get("id", ""))
        if not profile_id or profile_id in profile_ids:
            raise ValueError("allocation profile IDs must be non-empty and unique")
        profile_ids.add(profile_id)
        weights = profile.get("weights")
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("allocation profile weights are required")
        total = sum((Decimal(str(value)) for value in weights.values()), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.00000001"):
            raise ValueError("allocation profile weights must sum to 1")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("at least one route is required")
    sleeves: set[str] = set()
    funds: set[str] = set()
    for route in routes:
        if not isinstance(route, Mapping):
            raise ValueError("route must be an object")
        sleeve = str(route.get("sleeve", ""))
        fund_code = str(route.get("fund_code", ""))
        if not sleeve or sleeve in sleeves:
            raise ValueError("v1 requires exactly one unique route per sleeve")
        if len(fund_code) != 6 or not fund_code.isdigit() or fund_code in funds:
            raise ValueError("route fund codes must be unique six-digit values")
        sleeves.add(sleeve)
        funds.add(fund_code)
        if Decimal(str(route.get("minimum_order_cny"))) <= 0:
            raise ValueError("minimum order must be positive")
        cap = route.get("daily_cap_cny")
        if cap is not None and Decimal(str(cap)) <= 0:
            raise ValueError("daily cap must be positive")
        if Decimal(str(route.get("purchase_fee_rate"))) < 0:
            raise ValueError("purchase fee rate cannot be negative")
        if str(route.get("assumption_authority")) != RESEARCH_CLASSIFICATION:
            raise ValueError("every route must be a counterfactual research assumption")
    for profile in profiles:
        weights = profile["weights"]
        if set(weights) != sleeves:
            raise ValueError("profile sleeves must exactly match route sleeves")
    allocation_engine = payload.get("allocation_engine", {"mode": "target_gap"})
    if not isinstance(allocation_engine, Mapping):
        raise ValueError("allocation_engine must be an object")
    allocation_mode = str(allocation_engine.get("mode", "target_gap"))
    if allocation_mode not in {
        "target_gap",
        "new_money_trend_rs",
        "drawdown_budget_add",
        "sleeve_drawdown_recovery",
    }:
        raise ValueError("unsupported allocation engine mode")
    if allocation_mode == "new_money_trend_rs":
        candidates = allocation_engine.get("tactical_candidates")
        if not isinstance(candidates, Mapping) or not candidates:
            raise ValueError("trend allocation requires tactical candidates")
        route_by_sleeve = {
            str(route["sleeve"]): str(route["fund_code"]) for route in routes
        }
        if any(route_by_sleeve.get(str(sleeve)) != str(code) for sleeve, code in candidates.items()):
            raise ValueError("trend candidates must match configured sleeve routes")
        fallback = str(allocation_engine.get("fallback_sleeve", ""))
        if fallback not in sleeves or fallback in candidates:
            raise ValueError("trend fallback must be a non-candidate routed sleeve")
        if allocation_engine.get("signal_nav_field") != "accumulated_nav":
            raise ValueError("trend allocation requires accumulated_nav signals")
        signal_parameters = allocation_engine.get("signal_parameters", {})
        if not isinstance(signal_parameters, Mapping):
            raise ValueError("trend signal_parameters must be an object")
        allowed_parameters = {
            "minimum_observations",
            "medium_return_days",
            "slow_return_days",
            "slow_average_observations",
            "maximum_staleness_days",
        }
        if set(signal_parameters) - allowed_parameters:
            raise ValueError("trend signal_parameters contain unsupported keys")
        if any(int(value) <= 0 for value in signal_parameters.values()):
            raise ValueError("trend signal parameters must be positive integers")
    if allocation_mode == "drawdown_budget_add":
        composite_funds = allocation_engine.get("composite_funds")
        composite_weights = allocation_engine.get("composite_weights")
        if not isinstance(composite_funds, Mapping) or set(composite_funds) != sleeves:
            raise ValueError("drawdown composite funds must match all routed sleeves")
        if not isinstance(composite_weights, Mapping) or set(composite_weights) != sleeves:
            raise ValueError("drawdown composite weights must match all routed sleeves")
        route_by_sleeve = {
            str(route["sleeve"]): str(route["fund_code"]) for route in routes
        }
        if any(
            route_by_sleeve.get(str(sleeve)) != str(code)
            for sleeve, code in composite_funds.items()
        ):
            raise ValueError("drawdown composite funds must match configured routes")
        if sum(
            (Decimal(str(value)) for value in composite_weights.values()), Decimal("0")
        ) != Decimal("1"):
            raise ValueError("drawdown composite weights must sum exactly to 1")
        if allocation_engine.get("signal_nav_field") != "accumulated_nav":
            raise ValueError("drawdown allocation requires accumulated_nav signals")
        signal_parameters = allocation_engine.get("signal_parameters", {})
        if not isinstance(signal_parameters, Mapping):
            raise ValueError("drawdown signal_parameters must be an object")
        allowed_parameters = {
            "rolling_peak_observations",
            "minimum_composite_observations",
            "maximum_staleness_days",
            "first_threshold",
            "second_threshold",
            "third_threshold",
            "risk_veto_threshold",
        }
        if set(signal_parameters) - allowed_parameters:
            raise ValueError("drawdown signal_parameters contain unsupported keys")
        variants = {str(profile.get("allocation_variant", "")) for profile in profiles}
        if variants != {"drawdown_add", "matched_cashflow_631"}:
            raise ValueError("drawdown research requires strategy and matched-cashflow profiles")
    if allocation_mode == "sleeve_drawdown_recovery":
        composite_funds = allocation_engine.get("composite_funds")
        composite_weights = allocation_engine.get("composite_weights")
        candidate_funds = allocation_engine.get("candidate_funds")
        if not isinstance(composite_funds, Mapping) or set(composite_funds) != sleeves:
            raise ValueError("sleeve drawdown composite funds must match routed sleeves")
        if not isinstance(composite_weights, Mapping) or set(composite_weights) != sleeves:
            raise ValueError("sleeve drawdown composite weights must match routed sleeves")
        if not isinstance(candidate_funds, Mapping) or set(candidate_funds) != {
            "us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite"
        }:
            raise ValueError("sleeve drawdown candidate funds must match three growth sleeves")
        route_by_sleeve = {str(route["sleeve"]): str(route["fund_code"]) for route in routes}
        if any(route_by_sleeve.get(str(sleeve)) != str(code) for sleeve, code in composite_funds.items()):
            raise ValueError("sleeve drawdown composite funds must match routes")
        if any(route_by_sleeve.get(str(sleeve)) != str(code) for sleeve, code in candidate_funds.items()):
            raise ValueError("sleeve drawdown candidate funds must match routes")
        if sum((Decimal(str(value)) for value in composite_weights.values()), Decimal("0")) != Decimal("1"):
            raise ValueError("sleeve drawdown composite weights must sum exactly to 1")
        variants = {str(profile.get("allocation_variant", "")) for profile in profiles}
        if variants != {"sleeve_with_broad_fallback", "matched_cashflow_631", "broad_core"}:
            raise ValueError("sleeve drawdown requires three allocation comparators")
        if allocation_engine.get("signal_nav_field") != "accumulated_nav":
            raise ValueError("sleeve drawdown requires accumulated_nav signals")
    distribution = payload.get("distribution_assumption")
    if not isinstance(distribution, Mapping):
        raise ValueError("distribution assumption is required")
    if distribution.get("election") != "cash":
        raise ValueError("v1 local research pipeline supports cash distributions only")
    if distribution.get("payment_date_rule") != "next_fund_nav_date":
        raise ValueError("unsupported distribution payment-date assumption")


def load_research_scenario(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("research scenario must be a JSON object")
    _validate_scenario(payload)
    return payload


def _load_navs(
    database: str | Path,
    *,
    fund_codes: Sequence[str],
    provider_id: str,
    start_date: date,
    end_date: date,
) -> dict[str, tuple[NavPoint, ...]]:
    result: dict[str, tuple[NavPoint, ...]] = {}
    for code in fund_codes:
        all_points = load_nav_series(
            database,
            fund_code=code,
            provider_id=provider_id,
            as_of=end_date,
            nav_field="unit_nav",
        )
        # Retain one predecessor so the first in-window NAV can become visible
        # under the conservative next-observation visibility assumption.
        dates = [point.nav_date for point in all_points]
        index = max(0, bisect_right(dates, start_date) - 1)
        points = tuple(point for point in all_points[index:] if point.nav_date <= end_date)
        if len(points) < 2:
            raise ValueError(f"insufficient validated unit NAV observations for {code}")
        result[code] = points
    return result


def _visible_navs(
    series: Mapping[str, Sequence[NavPoint]],
    *,
    start_date: date,
    end_date: date,
) -> tuple[DailyUnitNav, ...]:
    observations: list[DailyUnitNav] = []
    for code, points in sorted(series.items()):
        for current, following in zip(points, points[1:]):
            if following.nav_date < start_date or following.nav_date > end_date:
                continue
            observations.append(
                DailyUnitNav(
                    fund_code=code,
                    nav_date=current.nav_date,
                    visible_date=following.nav_date,
                    unit_nav=current.nav,
                    nav_batch_sha256=current.content_sha256,
                    visibility_status=current.visibility_status,
                )
            )
    return tuple(observations)


def _load_cash_distributions(
    database: str | Path,
    *,
    series: Mapping[str, Sequence[NavPoint]],
    provider_id: str,
    start_date: date,
    end_date: date,
    cash_precision: int,
    cash_rounding_mode: str,
) -> tuple[CashDistribution, ...]:
    connection = _connect_read_only(database)
    try:
        placeholders = ",".join("?" for _ in series)
        rows = connection.execute(
            f"""
            WITH ranked AS (
                SELECT observation.fund_code, observation.ex_date,
                       observation.cash_per_share, observation.visibility_status,
                       batch.content_sha256, batch.published_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY observation.fund_code, observation.ex_date
                           ORDER BY batch.published_at DESC, observation.batch_id DESC
                       ) AS rank_number
                FROM fund_distribution_observations AS observation
                JOIN fund_distribution_batches AS batch
                  ON batch.batch_id = observation.batch_id
                WHERE observation.provider_id = ?
                  AND batch.quality_status IN ('pass', 'partial')
                  AND observation.fund_code IN ({placeholders})
                  AND observation.ex_date BETWEEN ? AND ?
            )
            SELECT fund_code, ex_date, cash_per_share, visibility_status,
                   content_sha256
            FROM ranked WHERE rank_number = 1
            ORDER BY fund_code, ex_date
            """,
            (provider_id, *series.keys(), start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
    finally:
        connection.close()
    results: list[CashDistribution] = []
    for row in rows:
        code = str(row["fund_code"])
        ex_date = date.fromisoformat(str(row["ex_date"]))
        next_dates = [point.nav_date for point in series[code] if point.nav_date > ex_date]
        if not next_dates or next_dates[0] > end_date:
            continue
        distribution_id = "dist_" + hashlib.sha256(
            f"{code}|{ex_date.isoformat()}|{row['cash_per_share']}".encode("utf-8")
        ).hexdigest()[:20]
        results.append(
            CashDistribution(
                distribution_id=distribution_id,
                fund_code=code,
                entitlement_date=ex_date,
                payment_date=next_dates[0],
                cash_per_share=Decimal(str(row["cash_per_share"])),
                cash_precision=cash_precision,
                cash_rounding_mode=cash_rounding_mode,
                source_batch_sha256=str(row["content_sha256"]),
                visibility_status=str(row["visibility_status"]),
            )
        )
    return tuple(results)


def _month_plans(start: date, end: date, calendar_day: int) -> tuple[date, ...]:
    cursor = date(start.year, start.month, 1)
    dates: list[date] = []
    while cursor <= end:
        planned = date(
            cursor.year,
            cursor.month,
            min(calendar_day, monthrange(cursor.year, cursor.month)[1]),
        )
        if start <= planned <= end:
            dates.append(planned)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return tuple(dates)


def _run_event_state(
    *,
    end_date: date,
    strategy_spec: Mapping[str, object],
    strategy_hash: str,
    contributions: Sequence[CashContribution],
    executions: Sequence[SubscriptionExecution],
    navs: Sequence[DailyUnitNav],
    distributions: Sequence[CashDistribution],
) -> tuple[dict[str, object], dict[str, object]]:
    subscriptions = run_subscription_events(
        end_date=end_date,
        initial_cash_cny=Decimal("0"),
        contributions=contributions,
        subscriptions=executions,
        strategy_id=str(strategy_spec["strategy_id"]),
        strategy_version=str(strategy_spec["strategy_version"]),
        strategy_spec_sha256=strategy_hash,
    )
    valuations = run_portfolio_valuation_events(
        end_date=end_date,
        subscription_result=subscriptions,
        nav_observations=navs,
        cash_distributions=distributions,
    )
    return subscriptions, valuations


def _current_values(
    valuations: Mapping[str, object],
    *,
    fund_to_sleeve: Mapping[str, str],
    target_weights: Mapping[str, Decimal],
    reserved_cash_by_sleeve: Mapping[str, Decimal] | None = None,
) -> tuple[Decimal, dict[str, Decimal]]:
    sleeves = tuple(sorted(target_weights))
    daily = valuations.get("daily_valuations")
    if not isinstance(daily, list) or not daily:
        return Decimal("0"), {sleeve: Decimal("0") for sleeve in sleeves}
    latest = daily[-1]
    if not isinstance(latest, Mapping):
        raise ValueError("daily valuation entry must be an object")
    values = {sleeve: Decimal("0") for sleeve in sleeves}
    for position in latest.get("positions", []):
        code = str(position["fund_code"])
        values[fund_to_sleeve[code]] += Decimal(str(position["market_value_cny"]))
    unassigned = (
        Decimal(str(latest["available_cash_cny"]))
        + Decimal(str(latest["pending_subscription_cash_cny"]))
        + Decimal(str(latest["distribution_receivable_cny"]))
    )
    reserved = {
        sleeve: Decimal(str(value))
        for sleeve, value in (reserved_cash_by_sleeve or {}).items()
    }
    if set(reserved) - set(sleeves):
        raise ValueError("reserved cash contains an unknown sleeve")
    reserved_total = sum(reserved.values(), Decimal("0"))
    if reserved_total < 0 or reserved_total > unassigned + CENT:
        raise ValueError("reserved future subscriptions cannot exceed available cash")
    for sleeve, amount in reserved.items():
        values[sleeve] += amount
    unassigned = max(Decimal("0"), unassigned - reserved_total)
    # Cash has no strategic sleeve in this buy-only baseline.  Attribute it
    # pro-rata for target-gap accounting so incidental cents and paid dividends
    # do not create a fake overweight in one named sleeve.
    for sleeve in sleeves:
        values[sleeve] += unassigned * target_weights[sleeve]
    # The allocation engine accepts monetary sleeve values at cent precision.
    # Round the complete vector here, then reconcile the rounding residue once;
    # otherwise six or more independently rounded sleeves can differ from the
    # cent-precision portfolio total by several cents and trip the safety gate.
    values = {
        sleeve: value.quantize(CENT, rounding=ROUND_HALF_UP)
        for sleeve, value in values.items()
    }
    total = Decimal(str(latest["total_value_cny"])).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    difference = total - sum(values.values(), Decimal("0"))
    values[sleeves[0]] += difference
    return total, values


def _xirr(contributions: Sequence[CashContribution], terminal_date: date, terminal: Decimal) -> float | None:
    if not contributions or terminal <= 0:
        return None
    start = min(item.contribution_date for item in contributions)
    flows = [(item.contribution_date, -float(item.amount_cny)) for item in contributions]
    flows.append((terminal_date, float(terminal)))

    def npv(rate: float) -> float:
        return sum(value / ((1.0 + rate) ** ((when - start).days / 365.25)) for when, value in flows)

    low = -0.9999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(160):
        middle = (low + high) / 2
        value = npv(middle)
        if abs(value) < 1e-9:
            return middle
        if low_value * value <= 0:
            high = middle
        else:
            low = middle
            low_value = value
    return (low + high) / 2


def calculate_path_metrics(
    valuations: Mapping[str, object],
    subscriptions: Mapping[str, object],
    contributions: Sequence[CashContribution],
) -> dict[str, object]:
    daily = valuations.get("daily_valuations")
    if not isinstance(daily, list) or len(daily) < 2:
        raise ValueError("at least two complete daily valuations are required")
    rows = [item for item in daily if isinstance(item, Mapping)]
    returns: list[float] = []
    wealth = 1.0
    wealth_path: list[tuple[date, float]] = [(date.fromisoformat(str(rows[0]["visible_date"])), wealth)]
    previous_date = wealth_path[0][0]
    previous_value = Decimal(str(rows[0]["total_value_cny"]))
    for row in rows[1:]:
        current_date = date.fromisoformat(str(row["visible_date"]))
        current_value = Decimal(str(row["total_value_cny"]))
        external_flow = sum(
            (
                item.amount_cny
                for item in contributions
                if previous_date < item.contribution_date <= current_date
            ),
            Decimal("0"),
        )
        if previous_value > 0:
            period_return = float((current_value - external_flow) / previous_value - Decimal("1"))
            returns.append(period_return)
            wealth *= 1.0 + period_return
            wealth_path.append((current_date, wealth))
        previous_date = current_date
        previous_value = current_value
    if not returns or len(wealth_path) < 2:
        raise ValueError("valuation path does not contain calculable returns")
    peak_value = wealth_path[0][1]
    peak_date = wealth_path[0][0]
    worst = 0.0
    worst_peak = peak_date
    worst_trough = peak_date
    for current_date, value in wealth_path:
        if value > peak_value:
            peak_value = value
            peak_date = current_date
        drawdown = value / peak_value - 1.0
        if drawdown < worst:
            worst = drawdown
            worst_peak = peak_date
            worst_trough = current_date
    recovery = None
    trough_seen = False
    peak_level = next(value for when, value in wealth_path if when == worst_peak)
    for current_date, value in wealth_path:
        if current_date == worst_trough:
            trough_seen = True
        if trough_seen and value >= peak_level:
            recovery = current_date
            break
    elapsed_days = (wealth_path[-1][0] - wealth_path[0][0]).days
    annualized = wealth ** (365.25 / elapsed_days) - 1 if elapsed_days > 0 else None
    observed_periods_per_year = (
        len(returns) * 365.25 / elapsed_days if elapsed_days > 0 else None
    )
    total_values = [Decimal(str(row["total_value_cny"])) for row in rows]
    invested_ratios = [
        Decimal(str(row["invested_value_cny"])) / total
        for row, total in zip(rows, total_values)
        if total > 0
    ]
    cash_ratios = [
        (
            Decimal(str(row["available_cash_cny"]))
            + Decimal(str(row["pending_subscription_cash_cny"]))
            + Decimal(str(row["distribution_receivable_cny"]))
        )
        / total
        for row, total in zip(rows, total_values)
        if total > 0
    ]
    final_value = total_values[-1]
    final_date = date.fromisoformat(str(rows[-1]["visible_date"]))
    summary = subscriptions["summary"]
    assert isinstance(summary, Mapping)
    xirr = _xirr(contributions, final_date, final_value)
    return {
        "start_date": wealth_path[0][0].isoformat(),
        "end_date": final_date.isoformat(),
        "valuation_observations": len(rows),
        "time_weighted_return_pct": (wealth - 1.0) * 100,
        "annualized_return_pct": None if annualized is None else annualized * 100,
        "annualized_volatility_pct": (
            stdev(returns) * math.sqrt(observed_periods_per_year) * 100
            if len(returns) >= 2 and observed_periods_per_year is not None
            else None
        ),
        "volatility_basis": "complete_observed_path_frequency",
        "observed_path_periods_per_year": observed_periods_per_year,
        "xirr_pct": None if xirr is None else xirr * 100,
        "maximum_drawdown_pct": worst * 100,
        "maximum_drawdown_peak_date": worst_peak.isoformat(),
        "maximum_drawdown_trough_date": worst_trough.isoformat(),
        "maximum_drawdown_recovery_date": None if recovery is None else recovery.isoformat(),
        "average_invested_ratio_pct": float(sum(invested_ratios, Decimal("0")) / len(invested_ratios) * 100),
        "average_cash_drag_pct": float(sum(cash_ratios, Decimal("0")) / len(cash_ratios) * 100),
        "contributions_cny": summary["contributions_cny"],
        "final_value_cny": f"{final_value:.2f}",
        "purchase_fees_cny": f"{Decimal(str(summary['purchase_fees_cny'])):.2f}",
        "rejected_subscriptions": summary["rejected_subscriptions"],
        "skipped_valuations": len(valuations.get("skipped_valuations", [])),
    }


def run_local_dca_research(
    *,
    database: str | Path,
    scenario: Mapping[str, object],
    strategy_spec: Mapping[str, object],
    strategy_spec_bytes: bytes,
) -> dict[str, object]:
    _validate_scenario(scenario)
    period = scenario["period"]
    assert isinstance(period, Mapping)
    start = date.fromisoformat(str(period["start_date"]))
    end = date.fromisoformat(str(period["end_date"]))
    provider_id = str(scenario["provider_id"])
    routes_payload = scenario["routes"]
    assert isinstance(routes_payload, list)
    fund_codes = tuple(str(item["fund_code"]) for item in routes_payload)
    series = _load_navs(
        database,
        fund_codes=fund_codes,
        provider_id=provider_id,
        start_date=start,
        end_date=end,
    )
    navs = _visible_navs(series, start_date=start, end_date=end)
    allocation_engine = scenario.get("allocation_engine", {"mode": "target_gap"})
    assert isinstance(allocation_engine, Mapping)
    allocation_mode = str(allocation_engine.get("mode", "target_gap"))
    trend_candidate_series: dict[str, tuple[str, Sequence[NavPoint]]] = {}
    drawdown_signal_series: dict[str, tuple[str, Sequence[NavPoint]]] = {}
    sleeve_candidate_series: dict[str, tuple[str, Sequence[NavPoint]]] = {}
    if allocation_mode == "new_money_trend_rs":
        tactical_candidates = allocation_engine["tactical_candidates"]
        assert isinstance(tactical_candidates, Mapping)
        trend_candidate_series = {
            str(sleeve): (
                str(fund_code),
                load_nav_series(
                    database,
                    fund_code=str(fund_code),
                    provider_id=provider_id,
                    as_of=end,
                    nav_field="accumulated_nav",
                ),
            )
            for sleeve, fund_code in tactical_candidates.items()
        }
    if allocation_mode == "drawdown_budget_add":
        composite_funds = allocation_engine["composite_funds"]
        assert isinstance(composite_funds, Mapping)
        drawdown_signal_series = {
            str(sleeve): (
                str(fund_code),
                load_nav_series(
                    database,
                    fund_code=str(fund_code),
                    provider_id=provider_id,
                    as_of=end,
                    nav_field="accumulated_nav",
                ),
            )
            for sleeve, fund_code in composite_funds.items()
        }
    if allocation_mode == "sleeve_drawdown_recovery":
        composite_funds = allocation_engine["composite_funds"]
        candidate_funds = allocation_engine["candidate_funds"]
        assert isinstance(composite_funds, Mapping) and isinstance(candidate_funds, Mapping)
        drawdown_signal_series = {
            str(sleeve): (str(code), load_nav_series(database, fund_code=str(code), provider_id=provider_id, as_of=end, nav_field="accumulated_nav"))
            for sleeve, code in composite_funds.items()
        }
        sleeve_candidate_series = {
            str(sleeve): drawdown_signal_series[str(sleeve)]
            for sleeve in candidate_funds
        }
    distribution_assumption = scenario["distribution_assumption"]
    assert isinstance(distribution_assumption, Mapping)
    distributions = _load_cash_distributions(
        database,
        series=series,
        provider_id=f"{provider_id}_distribution",
        start_date=start,
        end_date=end,
        cash_precision=int(distribution_assumption["cash_precision"]),
        cash_rounding_mode=str(distribution_assumption["cash_rounding_mode"]),
    )
    strategy_hash = hashlib.sha256(strategy_spec_bytes).hexdigest()
    scenario_hash = _canonical_sha256(scenario)
    planned_dates = _month_plans(start, end, int(scenario["calendar_day"]))
    route_by_sleeve = {str(item["sleeve"]): item for item in routes_payload}
    fund_to_sleeve = {
        str(item["fund_code"]): str(item["sleeve"]) for item in routes_payload
    }
    points_by_code_date = {
        code: {point.nav_date: point for point in points}
        for code, points in series.items()
    }
    visible_by_code_nav_date = {
        code: {current.nav_date: following.nav_date for current, following in zip(points, points[1:])}
        for code, points in series.items()
    }
    results: list[dict[str, object]] = []
    subscription_assumption = scenario.get("subscription_assumption", {})
    assert isinstance(subscription_assumption, Mapping)
    carry_across_month_end = bool(
        subscription_assumption.get("carry_unfilled_across_month_end", False)
    )
    unlimited_tranche_days = tuple(
        int(value)
        for value in subscription_assumption.get(
            "unlimited_fund_tranche_calendar_days", []
        )
    )
    if unlimited_tranche_days and (
        len(set(unlimited_tranche_days)) != len(unlimited_tranche_days)
        or any(day < 1 or day > 28 for day in unlimited_tranche_days)
    ):
        raise ValueError("unlimited fund tranche days must be unique values from 1 to 28")
    profiles = scenario["allocation_profiles"]
    assert isinstance(profiles, list)
    for profile in profiles:
        assert isinstance(profile, Mapping)
        profile_weights = {
            key: Decimal(str(value)) for key, value in profile["weights"].items()
        }
        sleeves = tuple(sorted(profile_weights))
        contributions: list[CashContribution] = []
        executions: list[SubscriptionExecution] = []
        monthly_reconciliation: list[dict[str, object]] = []
        monthly_signal_ledger: list[dict[str, object]] = []
        for sequence, planned in enumerate(planned_dates, start=1):
            signal: dict[str, object] | None = None
            if allocation_mode == "new_money_trend_rs":
                raw_signal_parameters = allocation_engine.get("signal_parameters", {})
                assert isinstance(raw_signal_parameters, Mapping)
                signal = build_new_money_trend_signal(
                    review_date=planned,
                    candidate_series=trend_candidate_series,
                    **{
                        str(key): int(value)
                        for key, value in raw_signal_parameters.items()
                    },
                )
                weights = {
                    key: Decimal(str(value))
                    for key, value in signal["new_money_weights"].items()
                }
                monthly_signal_ledger.append(signal)
            elif allocation_mode == "drawdown_budget_add":
                composite_weights = allocation_engine["composite_weights"]
                assert isinstance(composite_weights, Mapping)
                raw_signal_parameters = allocation_engine.get("signal_parameters", {})
                assert isinstance(raw_signal_parameters, Mapping)
                integer_parameters = {
                    "rolling_peak_observations",
                    "minimum_composite_observations",
                    "maximum_staleness_days",
                }
                signal = build_drawdown_budget_signal(
                    review_date=planned,
                    fund_series=drawdown_signal_series,
                    weights={
                        str(key): Decimal(str(value))
                        for key, value in composite_weights.items()
                    },
                    **{
                        str(key): (
                            int(value)
                            if str(key) in integer_parameters
                            else Decimal(str(value))
                        )
                        for key, value in raw_signal_parameters.items()
                    },
                )
                weights = profile_weights
                monthly_signal_ledger.append(signal)
            elif allocation_mode == "sleeve_drawdown_recovery":
                composite_weights = allocation_engine["composite_weights"]
                assert isinstance(composite_weights, Mapping)
                signal = build_sleeve_drawdown_signal(
                    review_date=planned,
                    candidate_series=sleeve_candidate_series,
                    portfolio_series=drawdown_signal_series,
                    portfolio_weights={str(key): Decimal(str(value)) for key, value in composite_weights.items()},
                )
                weights = profile_weights
                monthly_signal_ledger.append(signal)
            else:
                weights = profile_weights
            if executions or contributions:
                _prior_subscriptions, prior_valuations = _run_event_state(
                    end_date=planned - timedelta(days=1),
                    strategy_spec=strategy_spec,
                    strategy_hash=strategy_hash,
                    contributions=contributions,
                    executions=executions,
                    navs=navs,
                    distributions=distributions,
                )
                future_reserved_by_sleeve = {sleeve: Decimal("0") for sleeve in sleeves}
                for execution in executions:
                    if execution.submit_date >= planned:
                        future_reserved_by_sleeve[fund_to_sleeve[execution.fund_code]] += (
                            execution.gross_amount_cny
                        )
                pre_value, current_values = _current_values(
                    prior_valuations,
                    fund_to_sleeve=fund_to_sleeve,
                    target_weights=weights,
                    reserved_cash_by_sleeve=future_reserved_by_sleeve,
                )
            else:
                pre_value = Decimal("0")
                current_values = {sleeve: Decimal("0") for sleeve in sleeves}
            raw_schedule = scenario.get("monthly_contribution_schedule_cny")
            if raw_schedule is not None:
                assert isinstance(raw_schedule, Mapping)
                if planned.isoformat() not in raw_schedule:
                    raise ValueError("monthly contribution schedule must cover every planned date")
                contribution_amount = Decimal(str(raw_schedule[planned.isoformat()]))
            else:
                contribution_amount = Decimal(str(scenario["monthly_contribution_cny"]))
            if allocation_mode in {"drawdown_budget_add", "sleeve_drawdown_recovery"}:
                assert signal is not None
                signal_budget = signal["budget"]
                assert isinstance(signal_budget, Mapping)
                contribution_amount = Decimal(
                    str(signal_budget["total_contribution_cny"])
                )
            contribution = CashContribution(
                contribution_id=f"cash_{profile['id']}_{planned.isoformat()}",
                contribution_date=planned,
                amount_cny=contribution_amount,
            )
            contributions.append(contribution)
            incremental_routing: dict[str, str] | None = None
            if allocation_mode == "new_money_trend_rs":
                allocation = build_direct_new_money_allocation(
                    planned_date=planned,
                    pre_contribution_portfolio_value_cny=pre_value,
                    monthly_contribution_cny=contribution.amount_cny,
                    current_sleeve_values_cny=current_values,
                    new_money_weights=weights,
                    strategy_spec_sha256=strategy_hash,
                    remainder_sleeve=str(allocation_engine["fallback_sleeve"]),
                )
            elif allocation_mode == "drawdown_budget_add" and profile.get(
                "allocation_variant"
            ) == "drawdown_add":
                assert signal is not None
                allocation = build_drawdown_monthly_allocation(
                    planned_date=planned,
                    pre_contribution_portfolio_value_cny=pre_value,
                    current_sleeve_values_cny=current_values,
                    target_weights=weights,
                    signal=signal,
                    strategy_spec_sha256=strategy_hash,
                )
            elif allocation_mode == "sleeve_drawdown_recovery" and profile.get("allocation_variant") in {"sleeve_with_broad_fallback", "broad_core"}:
                assert signal is not None
                allocation = build_sleeve_drawdown_allocation(
                    planned_date=planned,
                    pre_contribution_portfolio_value_cny=pre_value,
                    current_sleeve_values_cny=current_values,
                    target_weights=weights,
                    signal=signal,
                    strategy_spec_sha256=strategy_hash,
                    variant=str(profile["allocation_variant"]),
                )
                base_reference = build_monthly_allocation(
                    planned_date=planned,
                    pre_contribution_portfolio_value_cny=pre_value,
                    monthly_contribution_cny=Decimal("3000"),
                    current_sleeve_values_cny=current_values,
                    target_weights=weights,
                    strategy_spec_sha256=strategy_hash,
                )
                base_by_sleeve = {item.sleeve: item.allocated_cny for item in base_reference.allocations}
                incremental_routing = {
                    item.sleeve: f"{item.allocated_cny - base_by_sleeve[item.sleeve]:.2f}"
                    for item in allocation.allocations
                    if item.allocated_cny != base_by_sleeve[item.sleeve]
                }
            else:
                allocation = build_monthly_allocation(
                    planned_date=planned,
                    pre_contribution_portfolio_value_cny=pre_value,
                    monthly_contribution_cny=contribution.amount_cny,
                    current_sleeve_values_cny=current_values,
                    target_weights=weights,
                    strategy_spec_sha256=strategy_hash,
                )
            instrument_routes: list[InstrumentRoute] = []
            routing_end = end if carry_across_month_end else date(
                planned.year,
                planned.month,
                monthrange(planned.year, planned.month)[1],
            )
            for sleeve in sleeves:
                item = route_by_sleeve[sleeve]
                code = str(item["fund_code"])
                all_eligible = tuple(
                    point.nav_date
                    for point in series[code]
                    if planned <= point.nav_date
                    <= routing_end
                    and point.nav_date in visible_by_code_nav_date[code]
                )
                cap_value = item.get("daily_cap_cny")
                if cap_value is None and unlimited_tranche_days:
                    eligible_list: list[date] = []
                    for tranche_day in unlimited_tranche_days:
                        target = date(
                            planned.year,
                            planned.month,
                            min(
                                tranche_day,
                                monthrange(planned.year, planned.month)[1],
                            ),
                        )
                        next_date = next(
                            (candidate for candidate in all_eligible if candidate >= target),
                            None,
                        )
                        if next_date is not None:
                            eligible_list.append(next_date)
                    eligible = tuple(eligible_list)
                    route_allocation_mode = "equal_tranches"
                    planned_tranche_count = len(unlimited_tranche_days)
                else:
                    eligible = all_eligible
                    route_allocation_mode = "as_soon_as_possible"
                    planned_tranche_count = 1
                reserved_by_date: dict[date, Decimal] = {}
                for execution in executions:
                    if execution.fund_code == code and execution.submit_date >= planned:
                        reserved_by_date[execution.submit_date] = (
                            reserved_by_date.get(execution.submit_date, Decimal("0"))
                            + execution.gross_amount_cny
                        )
                instrument_routes.append(
                    InstrumentRoute(
                        sleeve=sleeve,
                        fund_code=code,
                        priority=1,
                        minimum_order_cny=Decimal(str(item["minimum_order_cny"])),
                        daily_cap_cny=None if item.get("daily_cap_cny") is None else Decimal(str(item["daily_cap_cny"])),
                        eligible_dates=eligible,
                        rule_version=str(item["rule_version"]),
                        reserved_by_date_cny=reserved_by_date,
                        allocation_mode=route_allocation_mode,
                        planned_tranche_count=planned_tranche_count,
                    )
                )
            routed = generate_simulated_subscriptions(
                allocation,
                routes=instrument_routes,
                execution_end_date=routing_end,
                unfilled_issue_prefix=(
                    "unfilled_scenario_end"
                    if carry_across_month_end
                    else "unfilled_month_end"
                ),
            )
            gross_total = Decimal("0")
            cross_month_total = Decimal("0")
            latest_submit_date: date | None = None
            for simulated in routed.subscriptions:
                route = route_by_sleeve[simulated.sleeve]
                point = points_by_code_date[simulated.fund_code][simulated.simulated_submit_date]
                visible_date = visible_by_code_nav_date[simulated.fund_code][point.nav_date]
                executions.append(
                    SubscriptionExecution(
                        simulation_id=f"{profile['id']}_{sequence:03d}_{simulated.simulation_id}",
                        fund_code=simulated.fund_code,
                        signal_date=planned,
                        submit_date=simulated.simulated_submit_date,
                        execution_nav_date=point.nav_date,
                        nav_visible_date=visible_date,
                        confirmation_date=visible_date,
                        gross_amount_cny=simulated.gross_amount_cny,
                        execution_nav=point.nav,
                        nav_field="unit_nav",
                        purchase_fee_model="proportional_front_end",
                        purchase_fee_rate=Decimal(str(route["purchase_fee_rate"])),
                        share_precision=int(route["share_precision"]),
                        share_rounding_mode=str(route["share_rounding_mode"]),
                        rule_version=str(route["rule_version"]),
                        rule_effective_date=date.fromisoformat(str(route["rule_effective_date"])),
                        nav_batch_sha256=point.content_sha256,
                        visibility_status=point.visibility_status,
                    )
                )
                gross_total += simulated.gross_amount_cny
                if simulated.simulated_submit_date.month != planned.month:
                    cross_month_total += simulated.gross_amount_cny
                if latest_submit_date is None or simulated.simulated_submit_date > latest_submit_date:
                    latest_submit_date = simulated.simulated_submit_date
            monthly_reconciliation.append(
                {
                    "planned_date": planned.isoformat(),
                    "contribution_cny": f"{contribution.amount_cny:.2f}",
                    "simulated_gross_orders_cny": f"{gross_total:.2f}",
                    "cash_remaining_cny": f"{routed.contribution_cash_remaining_cny:.2f}",
                    "cross_month_scheduled_cny": f"{cross_month_total:.2f}",
                    "latest_submit_date": (
                        None if latest_submit_date is None else latest_submit_date.isoformat()
                    ),
                    "issues": list(routed.issues),
                    "signal_reason": None if signal is None else signal["reason"],
                    "base_contribution_cny": (
                        None
                        if signal is None or allocation_mode not in {"drawdown_budget_add", "sleeve_drawdown_recovery"}
                        else signal["budget"]["base_contribution_cny"]
                    ),
                    "additional_budget_cny": (
                        None
                        if signal is None or allocation_mode not in {"drawdown_budget_add", "sleeve_drawdown_recovery"}
                        else signal["budget"]["additional_budget_cny"]
                    ),
                    "incremental_routing_cny": incremental_routing,
                }
            )
        subscription_result, valuation_result = _run_event_state(
            end_date=end,
            strategy_spec=strategy_spec,
            strategy_hash=strategy_hash,
            contributions=contributions,
            executions=executions,
            navs=navs,
            distributions=distributions,
        )
        results.append(
            {
                "allocation_profile_id": profile["id"],
                "weights": {key: str(value) for key, value in profile_weights.items()},
                "allocation_engine_mode": allocation_mode,
                "monthly_signal_ledger": monthly_signal_ledger,
                "drawdown_tier_coverage": (
                    summarize_drawdown_tier_coverage(monthly_signal_ledger)
                    if allocation_mode == "drawdown_budget_add"
                    else None
                ),
                "metrics": calculate_path_metrics(
                    valuation_result, subscription_result, contributions
                ),
                "monthly_reconciliation": monthly_reconciliation,
                "subscription_result": subscription_result,
                "valuation_result": valuation_result,
            }
        )
    return {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "classification": RESEARCH_CLASSIFICATION,
        "scenario_id": scenario["scenario_id"],
        "scenario_sha256": scenario_hash,
        "strategy": {
            "strategy_id": strategy_spec["strategy_id"],
            "strategy_version": strategy_spec["strategy_version"],
            "strategy_spec_sha256": strategy_hash,
        },
        "official_rule_gate": scenario["official_rule_gate"],
        "assumption_summary": {
            "nav_visibility": "next_fund_nav_date",
            "subscription_confirmation": "nav_visible_date",
            "distribution_payment": "next_fund_nav_date",
            "purchase_fee_rates": {
                str(item["fund_code"]): str(item["purchase_fee_rate"])
                for item in routes_payload
            },
            "historical_truth_claimed": False,
            "carry_unfilled_across_month_end": carry_across_month_end,
            "unlimited_fund_tranche_calendar_days": list(unlimited_tranche_days),
            "allocation_engine_mode": allocation_mode,
        },
        "data_bindings": {
            "provider_id": provider_id,
            "fund_codes": list(fund_codes),
            "nav_batch_sha256": sorted(
                {point.content_sha256 for points in series.values() for point in points}
            ),
            "distribution_batch_sha256": sorted(
                {item.source_batch_sha256 for item in distributions}
            ),
            "distribution_events": len(distributions),
            "trend_signal_batch_sha256": sorted(
                {
                    point.content_sha256
                    for _code, points in trend_candidate_series.values()
                    for point in points
                }
            ),
            "drawdown_signal_batch_sha256": sorted(
                {
                    point.content_sha256
                    for _code, points in drawdown_signal_series.values()
                    for point in points
                }
            ),
        },
        "profile_results": results,
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
