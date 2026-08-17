from copy import deepcopy
import unittest

from invest_agent.backtest.rolling import build_rolling_scenarios


class RollingBacktestTests(unittest.TestCase):
    def _base(self):
        return {
            "scenario_id": "base",
            "period": {"start_date": "2024-01-01", "end_date": "2026-07-31"},
            "routes": [
                {"fund_code": "000001", "purchase_fee_rate": "0", "rule_version": "v1"}
            ],
        }

    def _spec(self):
        return {
            "schema_version": 1,
            "rolling_id": "rolling",
            "first_start_date": "2024-01-01",
            "last_end_date": "2026-07-31",
            "window_months": 12,
            "step_months": 3,
            "uniform_purchase_fee_rates": ["0", "0.0015"],
        }

    def test_builds_complete_rolling_windows_and_fee_axis(self) -> None:
        base = self._base()
        original = deepcopy(base)

        scenarios = build_rolling_scenarios(base, self._spec())

        self.assertEqual(len(scenarios), 14)
        self.assertEqual(base, original)
        self.assertEqual(scenarios[0]["period"]["end_date"], "2024-12-31")
        self.assertEqual(scenarios[-1]["period"]["start_date"], "2025-07-01")
        self.assertEqual(scenarios[-1]["period"]["end_date"], "2026-06-30")
        self.assertEqual(
            {item["uniform_purchase_fee_rate"] for item in scenarios},
            {"0", "0.0015"},
        )

    def test_rejects_window_before_base_period(self) -> None:
        spec = self._spec()
        spec["first_start_date"] = "2023-10-01"

        with self.assertRaisesRegex(ValueError, "cannot precede"):
            build_rolling_scenarios(self._base(), spec)


if __name__ == "__main__":
    unittest.main()
