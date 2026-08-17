import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StrategySpecTests(unittest.TestCase):
    def test_dca_baseline_spec_has_safe_reproducible_invariants(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/dca_baseline_v1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_id"], "dca_baseline")
        self.assertEqual(spec["strategy_version"], "1.0.0")
        self.assertEqual(spec["status"], "implemented")
        self.assertEqual(spec["mode"], "research_only")
        self.assertFalse(spec["rules"]["signal"]["depends_on_nav_or_return"])
        self.assertFalse(spec["rules"]["buy_only_allocation"]["selling_allowed"])
        self.assertFalse(spec["rules"]["instrument_routing"]["automatic_fund_substitution"])
        self.assertIn("submit_real_orders", spec["prohibitions"])
        self.assertEqual(spec["inputs"]["contribution_cny"]["canonical"], 3000)
        self.assertEqual(
            [item["core_pct"] + item["defensive_pct"] + item["satellite_pct"] for item in spec["inputs"]["allocation_profiles"]],
            [100, 100, 100],
        )

    def test_registry_cannot_enable_unvalidated_strategy(self) -> None:
        registry = json.loads(
            (ROOT / "strategies/registry.json").read_text(encoding="utf-8")
        )
        item = registry["strategies"][0]

        self.assertEqual(item["lifecycle_status"], "implemented")
        self.assertFalse(item["gates"]["production_approved"])
        self.assertFalse(item["gates"]["execution_enabled"])
        self.assertNotEqual(item["gates"]["backtest"], "passed")

    def test_dca_v1_1_carries_limited_queue_without_enabling_execution(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/dca_baseline_v1_1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_version"], "1.1.0")
        self.assertTrue(spec["rules"]["cash_flow"]["carry_unfilled_order_across_month_end"])
        self.assertTrue(spec["rules"]["cash_flow"]["reserved_cash_counts_toward_its_sleeve"])
        self.assertFalse(spec["rules"]["buy_only_allocation"]["selling_allowed"])
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_dca_v1_2_combines_limited_queue_with_unlimited_tranches(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/dca_baseline_v1_2.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_version"], "1.2.0")
        self.assertEqual(spec["inputs"]["schedule"]["budget_freeze_calendar_day"], 1)
        self.assertEqual(
            spec["inputs"]["schedule"]["unlimited_fund_tranche_calendar_days"],
            [5, 15, 25],
        )
        self.assertTrue(spec["rules"]["cash_flow"]["carry_unfilled_order_across_month_end"])
        self.assertIn(
            "average_independent_backtest_metrics_as_if_they_were_one_portfolio",
            spec["prohibitions"],
        )
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_dca_v1_3_uses_confirmed_personal_policy_baseline(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/dca_baseline_v1_3.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_version"], "1.3.0")
        self.assertEqual(spec["inputs"]["allocation"], "20/20/10/10/35/5")
        self.assertEqual(spec["inputs"]["allocation_role"], "personal_policy_baseline")
        self.assertLess(
            abs(spec["rules"]["risk_boundary"]["standard_synthetic_stress_result_pct"]),
            spec["rules"]["risk_boundary"]["stress_limit_drawdown_pct"],
        )
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_dca_v1_4_reconfirms_631_as_shared_baseline_and_target(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/dca_baseline_v1_4.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_version"], "1.4.0")
        self.assertEqual(spec["inputs"]["allocation"], "20/20/10/10/30/10")
        self.assertEqual(
            spec["inputs"]["allocation_role"],
            "research_benchmark_and_personal_target",
        )
        self.assertEqual(spec["rules"]["risk_boundary"]["stress_limit_drawdown_pct"], 20)
        self.assertTrue(
            spec["rules"]["risk_boundary"]["baseline_remains_valid_when_stress_requires_review"]
        )
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_new_money_trend_rs_v1_is_bounded_and_has_no_sell_path(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/new_money_trend_rs_v1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(spec["strategy_version"], "1.0.0")
        self.assertEqual(spec["status"], "implemented")
        self.assertEqual(spec["inputs"]["signal_nav_field"], "accumulated_nav")
        self.assertEqual(spec["rules"]["new_money_allocation"]["eligible_slots"], 2)
        self.assertEqual(
            spec["rules"]["risk_boundary"]["total_growth_and_technology_new_money_cap"],
            "0.30",
        )
        self.assertFalse(spec["rules"]["execution"]["selling_allowed"])
        self.assertIn("change_parameters_after_viewing_backtest_results", spec["prohibitions"])
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_drawdown_budget_add_v1_is_cash_bounded_and_has_fair_comparator(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/drawdown_budget_add_v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(spec["status"], "implemented")
        self.assertEqual(spec["inputs"]["base_monthly_contribution_cny"], "3000")
        self.assertEqual(spec["inputs"]["maximum_monthly_contribution_cny"], "5000")
        self.assertEqual(
            spec["rules"]["risk_veto"]["drawdown_at_or_below"], "-0.20"
        )
        self.assertEqual(
            set(spec["rules"]["allocation"]["additional_budget_destinations"]),
            {"domestic_broad_core", "us_broad_core"},
        )
        self.assertIn(
            "matched_cashflow_631", spec["backtest_scenarios"]["required_comparators"]
        )
        self.assertIn("submit_real_orders", spec["prohibitions"])

    def test_sleeve_drawdown_candidate_cannot_bypass_portfolio_risk(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/sleeve_drawdown_recovery_candidate_v0.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(spec["status"], "hypothesis_only")
        self.assertFalse(spec["decision"]["implemented"])
        self.assertFalse(spec["decision"]["approved"])
        self.assertFalse(spec["decision"]["real_order_submission_available"])
        self.assertEqual(
            spec["portfolio_safety_gates"]["portfolio_drawdown_veto_at_or_below"],
            "-0.20",
        )
        self.assertEqual(spec["portfolio_safety_gates"]["maximum_theme_weight_pct"], 30)
        self.assertFalse(spec["portfolio_safety_gates"]["selling_allowed"])

    def test_sleeve_drawdown_v1_keeps_caps_and_execution_disabled(self) -> None:
        spec = json.loads(
            (ROOT / "strategies/specs/sleeve_drawdown_recovery_v1.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(spec["status"], "implemented")
        self.assertEqual(spec["mode"], "research_only")
        self.assertEqual(spec["rules"]["risk"]["maximum_theme_weight"], "0.30")
        self.assertEqual(spec["rules"]["risk"]["portfolio_drawdown_veto_at_or_below"], "-0.20")
        self.assertIn("submit_real_orders", spec["prohibitions"])


if __name__ == "__main__":
    unittest.main()
