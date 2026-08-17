from copy import deepcopy
from decimal import Decimal
import unittest

from invest_agent.backtest.sensitivity import (
    _issue_summary,
    build_sensitivity_scenarios,
)


class SensitivityMatrixTests(unittest.TestCase):
    def _base(self):
        return {
            "scenario_id": "base",
            "monthly_contribution_cny": "3000",
            "calendar_day": 5,
            "routes": [
                {"sleeve": "core", "fund_code": "000001", "rule_version": "v1"},
                {
                    "sleeve": "defensive",
                    "fund_code": "070009",
                    "role": "old",
                    "rule_version": "v1",
                },
            ],
        }

    def _spec(self):
        return {
            "schema_version": 1,
            "matrix_id": "matrix",
            "monthly_contribution_cny": ["3000", "5000"],
            "calendar_days": [5, 25],
            "defensive_routes": [
                {"fund_code": "006932", "role": "policy bond"},
                {"fund_code": "070009", "role": "short bond"},
            ],
        }

    def test_builds_cartesian_product_without_mutating_base(self) -> None:
        base = self._base()
        original = deepcopy(base)

        scenarios = build_sensitivity_scenarios(base, self._spec())

        self.assertEqual(len(scenarios), 8)
        self.assertEqual(base, original)
        self.assertEqual(len({item["scenario_id"] for item in scenarios}), 8)
        self.assertEqual(
            {
                (
                    item["monthly_contribution_cny"],
                    item["calendar_day"],
                    next(route["fund_code"] for route in item["routes"] if route["sleeve"] == "defensive"),
                )
                for item in scenarios
            },
            {
                (amount, day, defense)
                for amount in ("3000", "5000")
                for day in (5, 25)
                for defense in ("006932", "070009")
            },
        )

    def test_summarizes_month_end_issues_and_cash(self) -> None:
        summary = _issue_summary(
            [
                {"issues": ["unfilled_month_end:us_growth"], "cash_remaining_cny": "10.25", "cross_month_scheduled_cny": "50"},
                {"issues": [], "cash_remaining_cny": "0.05"},
                {"issues": ["unfilled_month_end:us_growth"], "cash_remaining_cny": "8.00"},
            ]
        )

        self.assertEqual(summary["issue_months"], 2)
        self.assertEqual(summary["issue_counts"], {"unfilled_month_end:us_growth": 2})
        self.assertEqual(summary["maximum_month_end_cash_cny"], "10.25")
        self.assertEqual(Decimal(summary["total_month_end_cash_cny"]), Decimal("18.30"))
        self.assertEqual(summary["cross_month_queue_months"], 1)
        self.assertEqual(summary["total_cross_month_scheduled_cny"], "50.00")

    def test_builds_uniform_purchase_fee_axis_without_mutating_base(self) -> None:
        base = self._base()
        base["routes"][0]["purchase_fee_rate"] = "0"
        base["routes"][1]["purchase_fee_rate"] = "0"
        spec = self._spec()
        spec["monthly_contribution_cny"] = ["3000"]
        spec["calendar_days"] = [1]
        spec["defensive_routes"] = [spec["defensive_routes"][0]]
        spec["uniform_purchase_fee_rates"] = ["0", "0.0015", "0.015"]

        scenarios = build_sensitivity_scenarios(base, spec)

        self.assertEqual(len(scenarios), 3)
        self.assertEqual(base["routes"][0]["purchase_fee_rate"], "0")
        self.assertEqual(
            [item["uniform_purchase_fee_rate"] for item in scenarios],
            ["0", "0.0015", "0.015"],
        )
        self.assertTrue(
            all(
                route["purchase_fee_rate"] == scenario["uniform_purchase_fee_rate"]
                for scenario in scenarios
                for route in scenario["routes"]
            )
        )
        self.assertEqual(len({item["scenario_id"] for item in scenarios}), 3)

    def test_rejects_invalid_uniform_purchase_fee_axis(self) -> None:
        spec = self._spec()
        spec["uniform_purchase_fee_rates"] = ["-0.01"]

        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            build_sensitivity_scenarios(self._base(), spec)


if __name__ == "__main__":
    unittest.main()
