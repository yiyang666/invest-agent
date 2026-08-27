"""CLI for the offline, research-only event engine."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from .subscription_engine import (
    CashContribution,
    SubscriptionExecution,
    run_subscription_events,
)
from .valuation_engine import (
    CashDistribution,
    DailyUnitNav,
    ReinvestedDistribution,
    run_portfolio_valuation_events,
)
from .local_research import load_research_scenario, run_local_dca_research
from .sensitivity import load_sensitivity_spec, run_sensitivity_matrix
from .rolling import load_rolling_spec, run_rolling_windows
from .candidate_compare import load_comparison_spec, run_candidate_comparison
from .pair_compare import run_pair_cashflow_comparison
from .same_universe_compare import run_same_universe_comparison
from .trend_robustness import load_trend_robustness_spec, run_trend_robustness
from .drawdown_compare import run_drawdown_dual_benchmark
from .drawdown_robustness import (
    load_drawdown_robustness_spec,
    run_drawdown_robustness,
)
from .sleeve_drawdown_compare import run_sleeve_drawdown_compare
from invest_agent.risk.stress import load_stress_spec


DEFAULT_SPEC = Path("strategies/specs/dca_baseline_v1.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline off-exchange fund event simulations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subscriptions = subparsers.add_parser("subscriptions")
    subscriptions.add_argument("--input", type=Path, required=True)
    subscriptions.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    subscriptions.add_argument("--output", type=Path)
    valuations = subparsers.add_parser("valuations")
    valuations.add_argument("--input", type=Path, required=True)
    valuations.add_argument("--output", type=Path)
    local_dca = subparsers.add_parser("local-dca-research")
    local_dca.add_argument("--scenario", type=Path, required=True)
    local_dca.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    local_dca.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    local_dca.add_argument("--output", type=Path)
    sensitivity = subparsers.add_parser("sensitivity-matrix")
    sensitivity.add_argument("--scenario", type=Path, required=True)
    sensitivity.add_argument("--matrix", type=Path, required=True)
    sensitivity.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    sensitivity.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    sensitivity.add_argument("--output", type=Path)
    rolling = subparsers.add_parser("rolling-windows")
    rolling.add_argument("--scenario", type=Path, required=True)
    rolling.add_argument("--rolling", type=Path, required=True)
    rolling.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    rolling.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    rolling.add_argument("--output", type=Path)
    candidates = subparsers.add_parser("candidate-compare")
    candidates.add_argument("--scenario", type=Path, required=True)
    candidates.add_argument("--comparison", type=Path, required=True)
    candidates.add_argument("--rolling", type=Path, required=True)
    candidates.add_argument("--stress-spec", type=Path, required=True)
    candidates.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    candidates.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    candidates.add_argument("--output", type=Path)
    trend_robustness = subparsers.add_parser("trend-robustness")
    trend_robustness.add_argument("--scenario", type=Path, required=True)
    trend_robustness.add_argument("--robustness", type=Path, required=True)
    trend_robustness.add_argument("--stress-spec", type=Path, required=True)
    trend_robustness.add_argument("--benchmark-scenario", type=Path, required=True)
    trend_robustness.add_argument("--benchmark-spec", type=Path, required=True)
    trend_robustness.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    trend_robustness.add_argument("--spec", type=Path, required=True)
    trend_robustness.add_argument("--output", type=Path)
    drawdown_compare = subparsers.add_parser("drawdown-compare")
    drawdown_compare.add_argument("--scenario", type=Path, required=True)
    drawdown_compare.add_argument("--benchmark-scenario", type=Path, required=True)
    drawdown_compare.add_argument("--benchmark-spec", type=Path, required=True)
    drawdown_compare.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    drawdown_compare.add_argument("--spec", type=Path, required=True)
    drawdown_compare.add_argument("--output", type=Path)
    drawdown_robustness = subparsers.add_parser("drawdown-robustness")
    drawdown_robustness.add_argument("--scenario", type=Path, required=True)
    drawdown_robustness.add_argument("--robustness", type=Path, required=True)
    drawdown_robustness.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    drawdown_robustness.add_argument("--spec", type=Path, required=True)
    drawdown_robustness.add_argument("--output", type=Path)
    sleeve_drawdown = subparsers.add_parser("sleeve-drawdown-compare")
    sleeve_drawdown.add_argument("--scenario", type=Path, required=True)
    sleeve_drawdown.add_argument("--benchmark-scenario", type=Path, required=True)
    sleeve_drawdown.add_argument("--benchmark-spec", type=Path, required=True)
    sleeve_drawdown.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    sleeve_drawdown.add_argument("--spec", type=Path, required=True)
    sleeve_drawdown.add_argument("--output", type=Path)
    pair_compare = subparsers.add_parser("pair-cashflow-compare")
    pair_compare.add_argument("--candidate-scenario", type=Path, required=True)
    pair_compare.add_argument("--benchmark-scenario", type=Path, required=True)
    pair_compare.add_argument("--candidate-spec", type=Path, required=True)
    pair_compare.add_argument("--benchmark-spec", type=Path, required=True)
    pair_compare.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    pair_compare.add_argument("--output", type=Path)
    same_universe = subparsers.add_parser("same-universe-compare")
    same_universe.add_argument("--candidate-scenario", type=Path, required=True)
    same_universe.add_argument("--control-scenario", type=Path, required=True)
    same_universe.add_argument("--spec", type=Path, required=True)
    same_universe.add_argument("--protocol", type=Path, required=True)
    same_universe.add_argument("--db", type=Path, default=Path("data/private/invest_agent.sqlite3"))
    same_universe.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "same-universe-compare":
            spec_bytes = args.spec.read_bytes()
            protocol_bytes = args.protocol.read_bytes()
            payload = run_same_universe_comparison(
                database=args.db,
                candidate_scenario=load_research_scenario(args.candidate_scenario),
                control_scenario=load_research_scenario(args.control_scenario),
                strategy_spec=json.loads(spec_bytes),
                strategy_spec_bytes=spec_bytes,
                comparison_protocol=json.loads(protocol_bytes),
                comparison_protocol_bytes=protocol_bytes,
            )
        elif args.command == "pair-cashflow-compare":
            candidate_spec_bytes = args.candidate_spec.read_bytes()
            benchmark_spec_bytes = args.benchmark_spec.read_bytes()
            payload = run_pair_cashflow_comparison(
                database=args.db,
                candidate_scenario=load_research_scenario(args.candidate_scenario),
                candidate_spec=json.loads(candidate_spec_bytes),
                candidate_spec_bytes=candidate_spec_bytes,
                benchmark_scenario=load_research_scenario(args.benchmark_scenario),
                benchmark_spec=json.loads(benchmark_spec_bytes),
                benchmark_spec_bytes=benchmark_spec_bytes,
            )
        elif args.command == "sleeve-drawdown-compare":
            spec_bytes = args.spec.read_bytes()
            benchmark_spec_bytes = args.benchmark_spec.read_bytes()
            payload = run_sleeve_drawdown_compare(
                database=args.db,
                base_drawdown_scenario=load_research_scenario(args.scenario),
                sleeve_strategy_spec=json.loads(spec_bytes),
                sleeve_strategy_spec_bytes=spec_bytes,
                benchmark_scenario=load_research_scenario(args.benchmark_scenario),
                benchmark_spec=json.loads(benchmark_spec_bytes),
                benchmark_spec_bytes=benchmark_spec_bytes,
            )
        elif args.command == "drawdown-robustness":
            spec_bytes = args.spec.read_bytes()
            payload = run_drawdown_robustness(
                database=args.db,
                drawdown_scenario=load_research_scenario(args.scenario),
                robustness_spec=load_drawdown_robustness_spec(args.robustness),
                strategy_spec=json.loads(spec_bytes),
                strategy_spec_bytes=spec_bytes,
            )
        elif args.command == "drawdown-compare":
            spec_bytes = args.spec.read_bytes()
            benchmark_spec_bytes = args.benchmark_spec.read_bytes()
            payload = run_drawdown_dual_benchmark(
                database=args.db,
                drawdown_scenario=load_research_scenario(args.scenario),
                drawdown_strategy_spec=json.loads(spec_bytes),
                drawdown_strategy_spec_bytes=spec_bytes,
                fixed_benchmark_scenario=load_research_scenario(
                    args.benchmark_scenario
                ),
                fixed_benchmark_strategy_spec=json.loads(benchmark_spec_bytes),
                fixed_benchmark_strategy_spec_bytes=benchmark_spec_bytes,
            )
        elif args.command == "trend-robustness":
            spec_bytes = args.spec.read_bytes()
            benchmark_spec_bytes = args.benchmark_spec.read_bytes()
            payload = run_trend_robustness(
                database=args.db,
                trend_scenario=load_research_scenario(args.scenario),
                robustness_spec=load_trend_robustness_spec(args.robustness),
                trend_strategy_spec=json.loads(spec_bytes),
                trend_strategy_spec_bytes=spec_bytes,
                benchmark_scenario=load_research_scenario(args.benchmark_scenario),
                benchmark_strategy_spec=json.loads(benchmark_spec_bytes),
                benchmark_strategy_spec_bytes=benchmark_spec_bytes,
                stress_spec=load_stress_spec(args.stress_spec),
            )
        elif args.command == "candidate-compare":
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            payload = run_candidate_comparison(
                database=args.db,
                candidate_scenario=load_research_scenario(args.scenario),
                comparison_spec=load_comparison_spec(args.comparison),
                rolling_spec=load_rolling_spec(args.rolling),
                stress_spec=load_stress_spec(args.stress_spec),
                strategy_spec=spec,
                strategy_spec_bytes=spec_bytes,
            )
        elif args.command == "rolling-windows":
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            payload = run_rolling_windows(
                database=args.db,
                base_scenario=load_research_scenario(args.scenario),
                rolling_spec=load_rolling_spec(args.rolling),
                strategy_spec=spec,
                strategy_spec_bytes=spec_bytes,
            )
        elif args.command == "sensitivity-matrix":
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            payload = run_sensitivity_matrix(
                database=args.db,
                base_scenario=load_research_scenario(args.scenario),
                matrix_spec=load_sensitivity_spec(args.matrix),
                strategy_spec=spec,
                strategy_spec_bytes=spec_bytes,
            )
        elif args.command == "local-dca-research":
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            payload = run_local_dca_research(
                database=args.db,
                scenario=load_research_scenario(args.scenario),
                strategy_spec=spec,
                strategy_spec_bytes=spec_bytes,
            )
        else:
            request = json.loads(args.input.read_text(encoding="utf-8"))
        if args.command == "subscriptions":
            spec_bytes = args.spec.read_bytes()
            spec = json.loads(spec_bytes)
            contributions = tuple(
                CashContribution(
                    contribution_id=item["contribution_id"],
                    contribution_date=date.fromisoformat(item["contribution_date"]),
                    amount_cny=Decimal(str(item["amount_cny"])),
                )
                for item in request["contributions"]
            )
            subscriptions = tuple(
                SubscriptionExecution(
                    simulation_id=item["simulation_id"],
                    fund_code=item["fund_code"],
                    signal_date=date.fromisoformat(item["signal_date"]),
                    submit_date=date.fromisoformat(item["submit_date"]),
                    execution_nav_date=date.fromisoformat(item["execution_nav_date"]),
                    nav_visible_date=date.fromisoformat(item["nav_visible_date"]),
                    confirmation_date=date.fromisoformat(item["confirmation_date"]),
                    gross_amount_cny=Decimal(str(item["gross_amount_cny"])),
                    execution_nav=Decimal(str(item["execution_nav"])),
                    nav_field=item["nav_field"],
                    purchase_fee_model=item["purchase_fee_model"],
                    purchase_fee_rate=Decimal(str(item["purchase_fee_rate"])),
                    share_precision=int(item["share_precision"]),
                    share_rounding_mode=item["share_rounding_mode"],
                    rule_version=item["rule_version"],
                    rule_effective_date=date.fromisoformat(item["rule_effective_date"]),
                    nav_batch_sha256=item["nav_batch_sha256"],
                    visibility_status=item["visibility_status"],
                )
                for item in request["subscriptions"]
            )
            payload = run_subscription_events(
                end_date=date.fromisoformat(request["end_date"]),
                initial_cash_cny=Decimal(str(request["initial_cash_cny"])),
                contributions=contributions,
                subscriptions=subscriptions,
                strategy_id=spec["strategy_id"],
                strategy_version=spec["strategy_version"],
                strategy_spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
            )
        elif args.command == "valuations":
            nav_observations = tuple(
                DailyUnitNav(
                    fund_code=item["fund_code"],
                    nav_date=date.fromisoformat(item["nav_date"]),
                    visible_date=date.fromisoformat(item["visible_date"]),
                    unit_nav=Decimal(str(item["unit_nav"])),
                    nav_batch_sha256=item["nav_batch_sha256"],
                    visibility_status=item["visibility_status"],
                )
                for item in request.get("nav_observations", [])
            )
            cash_distributions = tuple(
                CashDistribution(
                    distribution_id=item["distribution_id"],
                    fund_code=item["fund_code"],
                    entitlement_date=date.fromisoformat(item["entitlement_date"]),
                    payment_date=date.fromisoformat(item["payment_date"]),
                    cash_per_share=Decimal(str(item["cash_per_share"])),
                    cash_precision=int(item["cash_precision"]),
                    cash_rounding_mode=item["cash_rounding_mode"],
                    source_batch_sha256=item["source_batch_sha256"],
                    visibility_status=item["visibility_status"],
                )
                for item in request.get("cash_distributions", [])
            )
            reinvested_distributions = tuple(
                ReinvestedDistribution(
                    distribution_id=item["distribution_id"],
                    fund_code=item["fund_code"],
                    entitlement_date=date.fromisoformat(item["entitlement_date"]),
                    reinvestment_nav_date=date.fromisoformat(item["reinvestment_nav_date"]),
                    nav_visible_date=date.fromisoformat(item["nav_visible_date"]),
                    confirmation_date=date.fromisoformat(item["confirmation_date"]),
                    cash_per_share=Decimal(str(item["cash_per_share"])),
                    reinvestment_nav=Decimal(str(item["reinvestment_nav"])),
                    cash_precision=int(item["cash_precision"]),
                    cash_rounding_mode=item["cash_rounding_mode"],
                    share_precision=int(item["share_precision"]),
                    share_rounding_mode=item["share_rounding_mode"],
                    source_batch_sha256=item["source_batch_sha256"],
                    visibility_status=item["visibility_status"],
                )
                for item in request.get("reinvested_distributions", [])
            )
            payload = run_portfolio_valuation_events(
                end_date=date.fromisoformat(request["end_date"]),
                subscription_result=request["subscription_result"],
                nav_observations=nav_observations,
                cash_distributions=cash_distributions,
                reinvested_distributions=reinvested_distributions,
            )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
