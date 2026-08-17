"""Multi-comparator execution research for sleeve-level drawdown additions."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .local_research import run_local_dca_research


ENGINE_VERSION = "sleeve_drawdown_execution_compare_v1"
CENT = Decimal("0.01")


def _sha(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _profiles(result: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    rows = result["profile_results"]
    assert isinstance(rows, list)
    return {str(row["allocation_profile_id"]): row for row in rows}


def _even_schedule(dates: list[str], total: Decimal) -> dict[str, str]:
    each = (total / Decimal(len(dates))).quantize(CENT, rounding=ROUND_DOWN)
    values = {item: each for item in dates}
    remainder = total - each * len(dates)
    for item in dates:
        if remainder < CENT:
            break
        values[item] += CENT
        remainder -= CENT
    return {key: f"{value:.2f}" for key, value in values.items()}


def run_sleeve_drawdown_compare(
    *,
    database: str | Path,
    base_drawdown_scenario: Mapping[str, object],
    sleeve_strategy_spec: Mapping[str, object],
    sleeve_strategy_spec_bytes: bytes,
    benchmark_scenario: Mapping[str, object],
    benchmark_spec: Mapping[str, object],
    benchmark_spec_bytes: bytes,
) -> dict[str, object]:
    scenario = deepcopy(dict(base_drawdown_scenario))
    scenario["scenario_id"] = "phase4_s3_6_sleeve_drawdown_execution_v1"
    engine = scenario["allocation_engine"]
    assert isinstance(engine, dict)
    engine.clear()
    engine.update(
        {
            "mode": "sleeve_drawdown_recovery",
            "signal_nav_field": "accumulated_nav",
            "composite_funds": {route["sleeve"]: route["fund_code"] for route in scenario["routes"]},
            "composite_weights": deepcopy(dict(scenario["allocation_profiles"][0]["weights"])),
            "candidate_funds": {
                "us_growth": "539001",
                "domestic_growth": "019861",
                "sh_hk_sz_passive_technology_satellite": "160646",
            },
        }
    )
    weights = deepcopy(dict(scenario["allocation_profiles"][0]["weights"]))
    scenario["allocation_profiles"] = [
        {"id": "sleeve_drawdown_with_broad_fallback", "allocation_variant": "sleeve_with_broad_fallback", "weights": weights},
        {"id": "matched_cashflow_631_target_gap", "allocation_variant": "matched_cashflow_631", "weights": deepcopy(weights)},
        {"id": "matched_cashflow_broad_core_route", "allocation_variant": "broad_core", "weights": deepcopy(weights)},
    ]
    main = run_local_dca_research(
        database=database,
        scenario=scenario,
        strategy_spec=sleeve_strategy_spec,
        strategy_spec_bytes=sleeve_strategy_spec_bytes,
    )
    main_profiles = _profiles(main)
    strategy = main_profiles["sleeve_drawdown_with_broad_fallback"]
    reconciliation = strategy["monthly_reconciliation"]
    assert isinstance(reconciliation, list)
    dates = [str(row["planned_date"]) for row in reconciliation]
    total = sum((Decimal(str(row["contribution_cny"])) for row in reconciliation), Decimal("0"))
    fixed = run_local_dca_research(
        database=database,
        scenario=benchmark_scenario,
        strategy_spec=benchmark_spec,
        strategy_spec_bytes=benchmark_spec_bytes,
    )
    even_scenario = deepcopy(dict(benchmark_scenario))
    even_scenario["scenario_id"] = "phase4_s3_6_same_total_capital_evenly_timed_631"
    even_scenario["monthly_contribution_schedule_cny"] = _even_schedule(dates, total)
    even = run_local_dca_research(
        database=database,
        scenario=even_scenario,
        strategy_spec=benchmark_spec,
        strategy_spec_bytes=benchmark_spec_bytes,
    )
    fixed_profile = next(iter(_profiles(fixed).values()))
    even_profile = next(iter(_profiles(even).values()))
    output_profiles = {
        "sleeve_drawdown": strategy["metrics"],
        "matched_cashflow_631": main_profiles["matched_cashflow_631_target_gap"]["metrics"],
        "matched_cashflow_broad_core": main_profiles["matched_cashflow_broad_core_route"]["metrics"],
        "same_total_capital_evenly_timed_631": even_profile["metrics"],
        "fixed_3000_631": fixed_profile["metrics"],
    }
    strategy_final = Decimal(str(output_profiles["sleeve_drawdown"]["final_value_cny"]))
    comparisons = {
        key: f"{strategy_final - Decimal(str(metrics['final_value_cny'])):.2f}"
        for key, metrics in output_profiles.items()
        if key != "sleeve_drawdown"
    }
    ledger = strategy["monthly_signal_ledger"]
    assert isinstance(ledger, list)
    trigger_counts: dict[str, int] = {}
    for signal in ledger:
        reason = str(signal["reason"])
        trigger_counts[reason] = trigger_counts.get(reason, 0) + 1
    incremental_totals: dict[str, Decimal] = {}
    for row in reconciliation:
        routing = row.get("incremental_routing_cny")
        if not isinstance(routing, Mapping):
            continue
        for sleeve, amount in routing.items():
            incremental_totals[str(sleeve)] = incremental_totals.get(str(sleeve), Decimal("0")) + Decimal(str(amount))
    payload: dict[str, object] = {
        "engine_version": ENGINE_VERSION,
        "mode": "research_only",
        "profiles": output_profiles,
        "final_value_differences_sleeve_minus_comparator_cny": comparisons,
        "cashflow": {"months": len(dates), "strategy_total_contributions_cny": f"{total:.2f}", "even_schedule": even_scenario["monthly_contribution_schedule_cny"]},
        "signal_summary": {
            "reason_month_counts": dict(sorted(trigger_counts.items())),
            "incremental_routing_totals_cny": {key: f"{value:.2f}" for key, value in sorted(incremental_totals.items())},
            "ledger": ledger,
        },
        "full_result_sha256": {"main": _sha(main), "fixed": _sha(fixed), "even": _sha(even)},
        "data_bindings": main["data_bindings"],
        "official_rule_gate": main["official_rule_gate"],
        "strategy_spec_sha256": hashlib.sha256(sleeve_strategy_spec_bytes).hexdigest(),
        "external_side_effects": False,
        "real_order_submission_available": False,
    }
    payload["report_sha256"] = _sha(payload)
    return payload
