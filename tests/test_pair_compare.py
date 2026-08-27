from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from invest_agent.backtest.pair_compare import (
    build_benchmark_scenarios,
    run_pair_cashflow_comparison,
)


class PairCashflowComparisonTests(unittest.TestCase):
    def _candidate(self) -> dict[str, object]:
        return {
            "scenario_id": "candidate-a1",
            "research_stage": "A1_fixed_strategic_target_gap",
            "research_evidence_level": "research_ready",
            "period": {"start_date": "2026-01-01", "end_date": "2026-02-28"},
            "calendar_day": 1,
        }

    def _benchmark(self) -> dict[str, object]:
        return {
            "scenario_id": "631-v1-5",
            "period": {"start_date": "2024-01-01", "end_date": "2026-12-31"},
            "calendar_day": 5,
            "monthly_contribution_cny": "5000",
            "routes": [{"fund_code": "000001"}],
        }

    def test_build_benchmark_scenarios_aligns_window_without_mutating_input(self) -> None:
        candidate = self._candidate()
        benchmark = self._benchmark()
        original = deepcopy(benchmark)
        schedule = {"2026-01-01": "3000.00", "2026-02-01": "4000.00"}

        fixed, matched = build_benchmark_scenarios(
            candidate_scenario=candidate,
            benchmark_scenario=benchmark,
            candidate_schedule=schedule,
        )

        self.assertEqual(benchmark, original)
        self.assertEqual(fixed["period"], candidate["period"])
        self.assertEqual(fixed["calendar_day"], 1)
        self.assertEqual(fixed["monthly_contribution_cny"], "3000.00")
        self.assertNotIn("monthly_contribution_schedule_cny", fixed)
        self.assertEqual(matched["monthly_contribution_schedule_cny"], schedule)
        self.assertEqual(matched["routes"], benchmark["routes"])

    def test_comparison_enforces_exact_monthly_cashflow_match(self) -> None:
        candidate = self._candidate()
        benchmark = self._benchmark()

        def fake_run(*, scenario, strategy_spec, **_kwargs):
            is_candidate = scenario["scenario_id"] == "candidate-a1"
            schedule = scenario.get(
                "monthly_contribution_schedule_cny",
                {"2026-01-01": "3000.00", "2026-02-01": "3000.00"},
            )
            final_value = "6500.00" if is_candidate else "6400.00"
            return {
                "scenario_id": scenario["scenario_id"],
                "strategy": {
                    "strategy_id": strategy_spec["strategy_id"],
                    "strategy_version": strategy_spec["strategy_version"],
                },
                "profile_results": [
                    {
                        "metrics": {"final_value_cny": final_value},
                        "monthly_reconciliation": [
                            {"planned_date": when, "contribution_cny": amount}
                            for when, amount in schedule.items()
                        ],
                    }
                ],
                "official_rule_gate": {"status": "blocked"},
                "data_bindings": {"provider_id": "test"},
            }

        with patch(
            "invest_agent.backtest.pair_compare.run_local_dca_research",
            side_effect=fake_run,
        ):
            result = run_pair_cashflow_comparison(
                database=Path("unused.sqlite3"),
                candidate_scenario=candidate,
                candidate_spec={"strategy_id": "candidate", "strategy_version": "0.2.0"},
                candidate_spec_bytes=b"candidate",
                benchmark_scenario=benchmark,
                benchmark_spec={"strategy_id": "dca_baseline", "strategy_version": "1.5.0"},
                benchmark_spec_bytes=b"benchmark",
            )

        self.assertTrue(result["cashflow_check"]["exact_monthly_match"])
        self.assertEqual(result["cashflow_check"]["candidate_total_cny"], "6000.00")
        self.assertEqual(
            result["candidate_final_value_difference_cny"]["vs_matched_cashflow_631"],
            "100.00",
        )
        self.assertFalse(result["real_order_submission_available"])


if __name__ == "__main__":
    unittest.main()
