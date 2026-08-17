from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.backtest.local_research import (
    _current_values,
    calculate_path_metrics,
    load_research_scenario,
)
from invest_agent.strategies.dca import build_monthly_allocation
from invest_agent.backtest.subscription_engine import CashContribution


class LocalResearchBacktestTests(unittest.TestCase):
    def _scenario(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scenario_id": "test-v1",
            "classification": "counterfactual_research_assumption",
            "provider_id": "test",
            "period": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
            "monthly_contribution_cny": "100",
            "calendar_day": 5,
            "allocation_profiles": [
                {"id": "profile", "weights": {"core": "1"}}
            ],
            "routes": [
                {
                    "sleeve": "core",
                    "fund_code": "000001",
                    "minimum_order_cny": "10",
                    "daily_cap_cny": None,
                    "purchase_fee_rate": "0",
                    "share_precision": 2,
                    "share_rounding_mode": "down",
                    "rule_version": "research-v1",
                    "rule_effective_date": "2026-01-01",
                    "assumption_authority": "counterfactual_research_assumption",
                }
            ],
            "distribution_assumption": {
                "election": "cash",
                "payment_date_rule": "next_fund_nav_date",
                "cash_precision": 2,
                "cash_rounding_mode": "half_up",
            },
            "official_rule_gate": {"status": "blocked", "reason": "test"},
        }

    def test_scenario_requires_official_gate_to_remain_blocked(self) -> None:
        scenario = deepcopy(self._scenario())
        scenario["official_rule_gate"]["status"] = "passed"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(scenario), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "official rule gate blocked"):
                load_research_scenario(path)

    def test_trend_scenario_requires_accumulated_nav_and_matching_routes(self) -> None:
        scenario = deepcopy(self._scenario())
        scenario["allocation_engine"] = {
            "mode": "new_money_trend_rs",
            "signal_nav_field": "unit_nav",
            "tactical_candidates": {"core": "000001"},
            "fallback_sleeve": "core",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(scenario), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fallback"):
                load_research_scenario(path)

    def test_path_metrics_remove_external_contribution_from_twr(self) -> None:
        valuations = {
            "daily_valuations": [
                {
                    "visible_date": "2026-01-05",
                    "available_cash_cny": "0",
                    "pending_subscription_cash_cny": "0",
                    "distribution_receivable_cny": "0",
                    "invested_value_cny": "100",
                    "total_value_cny": "100",
                },
                {
                    "visible_date": "2026-02-05",
                    "available_cash_cny": "0",
                    "pending_subscription_cash_cny": "0",
                    "distribution_receivable_cny": "0",
                    "invested_value_cny": "200",
                    "total_value_cny": "200",
                },
                {
                    "visible_date": "2026-03-05",
                    "available_cash_cny": "0",
                    "pending_subscription_cash_cny": "0",
                    "distribution_receivable_cny": "0",
                    "invested_value_cny": "220",
                    "total_value_cny": "220",
                },
            ],
            "skipped_valuations": [],
        }
        subscriptions = {
            "summary": {
                "contributions_cny": "200.00",
                "purchase_fees_cny": "0.00",
                "rejected_subscriptions": 0,
            }
        }
        contributions = (
            CashContribution("first", date(2026, 1, 5), Decimal("100")),
            CashContribution("second", date(2026, 2, 5), Decimal("100")),
        )

        result = calculate_path_metrics(valuations, subscriptions, contributions)

        self.assertAlmostEqual(result["time_weighted_return_pct"], 10.0)
        self.assertAlmostEqual(result["maximum_drawdown_pct"], 0.0)
        self.assertAlmostEqual(result["average_invested_ratio_pct"], 100.0)
        self.assertEqual(result["final_value_cny"], "220.00")

    def test_six_sleeve_cash_attribution_reconciles_after_cent_rounding(self) -> None:
        sleeves = tuple(f"sleeve_{index}" for index in range(6))
        fund_to_sleeve = {
            f"{index:06d}": sleeve for index, sleeve in enumerate(sleeves, start=1)
        }
        target_weights = {
            sleeve: Decimal("0.1666666667") for sleeve in sleeves
        }
        valuations = {
            "daily_valuations": [
                {
                    "total_value_cny": "6.03",
                    "available_cash_cny": "0.03",
                    "pending_subscription_cash_cny": "0",
                    "distribution_receivable_cny": "0",
                    "positions": [
                        {"fund_code": code, "market_value_cny": "1.00"}
                        for code in fund_to_sleeve
                    ],
                }
            ]
        }

        total, current_values = _current_values(
            valuations,
            fund_to_sleeve=fund_to_sleeve,
            target_weights=target_weights,
        )

        self.assertEqual(sum(current_values.values(), Decimal("0")), total)
        self.assertTrue(all(value == value.quantize(Decimal("0.01")) for value in current_values.values()))
        build_monthly_allocation(
            planned_date=date(2026, 4, 5),
            pre_contribution_portfolio_value_cny=total,
            monthly_contribution_cny=Decimal("100"),
            current_sleeve_values_cny=current_values,
            target_weights=target_weights,
            strategy_spec_sha256="a" * 64,
        )

    def test_reserved_future_subscription_cash_stays_with_its_sleeve(self) -> None:
        valuations = {
            "daily_valuations": [
                {
                    "total_value_cny": "100.00",
                    "available_cash_cny": "40.00",
                    "pending_subscription_cash_cny": "0",
                    "distribution_receivable_cny": "0",
                    "positions": [
                        {"fund_code": "000001", "market_value_cny": "60.00"}
                    ],
                }
            ]
        }

        total, values = _current_values(
            valuations,
            fund_to_sleeve={"000001": "core"},
            target_weights={"core": Decimal("0.60"), "overseas": Decimal("0.40")},
            reserved_cash_by_sleeve={"overseas": Decimal("40.00")},
        )

        self.assertEqual(total, Decimal("100.00"))
        self.assertEqual(values, {"core": Decimal("60.00"), "overseas": Decimal("40.00")})


if __name__ == "__main__":
    unittest.main()
