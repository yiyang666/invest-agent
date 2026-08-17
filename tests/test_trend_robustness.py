from copy import deepcopy
from decimal import Decimal
import unittest

from invest_agent.backtest.trend_robustness import (
    _realized_weight_scenario,
    _selection_summary,
)


class TrendRobustnessTests(unittest.TestCase):
    def test_selection_summary_counts_slots_and_funds(self) -> None:
        summary = _selection_summary(
            [
                {"selected_fund_codes": ["000001", "000002"]},
                {"selected_fund_codes": ["000001"]},
                {"selected_fund_codes": []},
            ]
        )

        self.assertEqual(summary["signal_months"], 3)
        self.assertEqual(summary["selected_slots"], 3)
        self.assertEqual(summary["fund_selected_month_counts"]["000001"], 2)

    def test_realized_weights_reconcile_and_attribute_cash_to_defensive(self) -> None:
        scenario = {
            "routes": [
                {"fund_code": "000001", "sleeve": "core"},
                {"fund_code": "000002", "sleeve": "defensive"},
            ],
            "allocation_profiles": [
                {"id": "profile", "weights": {"core": "0.5", "defensive": "0.5"}}
            ],
        }
        original = deepcopy(scenario)
        result = {
            "profile_results": [
                {
                    "valuation_result": {
                        "daily_valuations": [
                            {
                                "positions": [
                                    {"fund_code": "000001", "market_value_cny": "60"},
                                    {"fund_code": "000002", "market_value_cny": "30"},
                                ],
                                "available_cash_cny": "10",
                                "pending_subscription_cash_cny": "0",
                                "distribution_receivable_cny": "0",
                                "total_value_cny": "100",
                            }
                        ]
                    }
                }
            ]
        }

        stress_scenario, weights = _realized_weight_scenario(scenario, result)

        self.assertEqual(scenario, original)
        self.assertEqual(Decimal(weights["core"]), Decimal("0.6"))
        self.assertEqual(Decimal(weights["defensive"]), Decimal("0.4"))
        stress_weights = stress_scenario["allocation_profiles"][0]["weights"]
        self.assertEqual(sum(map(Decimal, stress_weights.values())), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
