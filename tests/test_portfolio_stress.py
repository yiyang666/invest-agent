from copy import deepcopy
import unittest

from invest_agent.risk.stress import run_portfolio_stress


class PortfolioStressTests(unittest.TestCase):
    def _base(self):
        return {
            "allocation_profiles": [
                {"id": "target", "weights": {"equity": "0.70", "defensive": "0.30"}}
            ]
        }

    def _spec(self):
        return {
            "schema_version": 1,
            "stress_id": "stress",
            "probability_model": "none_non_probabilistic_scenarios",
            "target_drawdown_pct": "15",
            "stress_limit_drawdown_pct": "20",
            "scenarios": [
                {
                    "scenario_id": "correction",
                    "steps": [
                        {
                            "step_id": "shock",
                            "sleeve_returns": {"equity": "-0.20", "defensive": "0.05"},
                        }
                    ],
                }
            ],
        }

    def test_calculates_weighted_shock_and_target_classification(self) -> None:
        result = run_portfolio_stress(base_scenario=self._base(), stress_spec=self._spec())
        row = result["rows"][0]

        self.assertAlmostEqual(row["terminal_return_pct"], -12.5)
        self.assertAlmostEqual(row["maximum_drawdown_pct"], -12.5)
        self.assertEqual(row["classification"], "pass_within_target")
        self.assertFalse(row["risk_veto"])

    def test_compounds_multi_step_path_and_vetoes_stress_breach(self) -> None:
        spec = self._spec()
        spec["scenarios"][0]["steps"] = [
            {"step_id": "one", "sleeve_returns": {"equity": "-0.20", "defensive": "-0.05"}},
            {"step_id": "two", "sleeve_returns": {"equity": "-0.20", "defensive": "-0.05"}},
        ]

        result = run_portfolio_stress(base_scenario=self._base(), stress_spec=spec)
        row = result["rows"][0]

        self.assertLess(row["maximum_drawdown_pct"], -20)
        self.assertEqual(row["classification"], "fail_stress_limit")
        self.assertTrue(result["summary"]["risk_veto"])

    def test_rejects_missing_sleeve_shock(self) -> None:
        spec = deepcopy(self._spec())
        spec["scenarios"][0]["steps"][0]["sleeve_returns"].pop("defensive")

        with self.assertRaisesRegex(ValueError, "exactly match"):
            run_portfolio_stress(base_scenario=self._base(), stress_spec=spec)


if __name__ == "__main__":
    unittest.main()
