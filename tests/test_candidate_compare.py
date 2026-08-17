from decimal import Decimal
import unittest

from invest_agent.backtest.candidate_compare import scale_stress_spec


class CandidateCompareTests(unittest.TestCase):
    def test_scales_every_sleeve_return_without_mutating_source(self) -> None:
        source = {
            "stress_id": "base",
            "scenarios": [
                {
                    "scenario_id": "one",
                    "steps": [
                        {"step_id": "shock", "sleeve_returns": {"equity": "-0.20", "bond": "0.01"}}
                    ],
                }
            ],
        }

        scaled = scale_stress_spec(source, Decimal("1.10"))

        self.assertEqual(source["scenarios"][0]["steps"][0]["sleeve_returns"]["equity"], "-0.20")
        self.assertEqual(scaled["scenarios"][0]["steps"][0]["sleeve_returns"]["equity"], "-0.2200")
        self.assertEqual(scaled["scenarios"][0]["steps"][0]["sleeve_returns"]["bond"], "0.0110")

    def test_rejects_nonpositive_stress_factor(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            scale_stress_spec({"stress_id": "base", "scenarios": []}, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
