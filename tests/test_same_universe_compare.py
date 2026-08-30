from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from invest_agent.backtest.local_research import load_research_scenario
from invest_agent.backtest.same_universe_compare import run_same_universe_comparison


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "config/backtest_hierarchical_424_dca_baseline_v1.json"
A1_PATH = ROOT / "config/backtest_hierarchical_a1_v1.json"
A2_PATH = ROOT / "config/backtest_hierarchical_a2_v1.json"
PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_comparison_protocol_v1.json"
PROTOCOL_V2_PATH = ROOT / "config/backtest_hierarchical_comparison_protocol_v2.json"
RELEASE_SENSITIVITY_PATH = ROOT / "config/backtest_hierarchical_a2_bond_gold_sensitivity_v1.json"
RELEASE_PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_release_destination_protocol_v1.json"
D1_PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_d1_single_theme_protocol_v1.json"
D1_SCENARIO_PATHS = (
    ROOT / "config/backtest_hierarchical_d1_global_tech_v1.json",
    ROOT / "config/backtest_hierarchical_d1_semiconductor_v1.json",
    ROOT / "config/backtest_hierarchical_d1_robotics_v1.json",
    ROOT / "config/backtest_hierarchical_d1_biotechnology_v1.json",
)
D3_PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_d3_factor_ablation_protocol_v1.json"
D3_SCENARIO_PATHS = (
    ROOT / "config/backtest_hierarchical_d3_long_average_only_v1.json",
    ROOT / "config/backtest_hierarchical_d3_momentum_only_v1.json",
)
D4_PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_d4_satellite_floor_protocol_v1.json"
D4_SCENARIO_PATH = ROOT / "config/backtest_hierarchical_d4_satellite_floor_v1.json"
A3A_PROTOCOL_PATH = ROOT / "config/backtest_hierarchical_a3a_satellite_inverse_volatility_protocol_v1.json"
A3A_SCENARIO_PATH = ROOT / "config/backtest_hierarchical_a3a_satellite_inverse_volatility_v1.json"


class SameUniverseComparisonTests(unittest.TestCase):
    def _scenario(self, scenario_id: str, mode: str) -> dict[str, object]:
        return {
            "scenario_id": scenario_id,
            "research_stage": "test",
            "research_evidence_level": "research_ready",
            "provider_id": "local",
            "period": {"start_date": "2026-01-01", "end_date": "2026-02-28"},
            "calendar_day": 1,
            "routes": [
                {
                    "sleeve": "core",
                    "fund_code": "000001",
                    "priority": 1,
                    "minimum_order_cny": "1",
                }
            ],
            "allocation_profiles": [
                {"id": scenario_id, "weights": {"core": "1.0"}}
            ],
            "allocation_engine": {"mode": mode},
        }

    def _protocol(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "protocol_id": "protocol-v1",
            "status": "frozen_before_same_universe_performance_run",
            "strategy_id": "candidate",
            "strategy_version": "0.2.0",
            "primary_same_universe_control": {"scenario_id": "control"},
            "allowed_candidate_scenario_ids": ["candidate-a1"],
            "required_invariants": {
                "identical_route_payloads": True,
                "identical_strategic_sleeve_weights": True,
                "identical_provider": True,
                "identical_period": True,
                "identical_calendar_day": True,
                "identical_monthly_external_cashflows": True,
                "same_initial_state": "zero_holdings_zero_cash",
                "same_strategy_spec_bytes": True,
                "no_real_orders": True,
            },
        }

    def test_comparison_enforces_same_universe_and_reports_rule_increment(self) -> None:
        candidate = self._scenario("candidate-a1", "revalidated_target_gap")
        control = self._scenario("control", "target_gap")
        protocol = self._protocol()

        def fake_run(*, scenario, **_kwargs):
            is_candidate = scenario["scenario_id"] == "candidate-a1"
            schedule = scenario.get(
                "monthly_contribution_schedule_cny",
                {"2026-01-01": "3000.00", "2026-02-01": "3000.00"},
            )
            metrics = {
                "final_value_cny": "6500.00" if is_candidate else "6400.00",
                "xirr_pct": 11.0 if is_candidate else 10.0,
                "annualized_return_pct": 10.5 if is_candidate else 10.0,
                "annualized_volatility_pct": 8.0 if is_candidate else 9.0,
                "maximum_drawdown_pct": -7.0 if is_candidate else -9.0,
                "average_cash_drag_pct": 4.0 if is_candidate else 5.0,
                "purchase_fees_cny": "10.00" if is_candidate else "12.00",
            }
            return {
                "scenario_id": scenario["scenario_id"],
                "strategy": {
                    "strategy_id": "candidate",
                    "strategy_version": "0.2.0",
                },
                "profile_results": [
                    {
                        "allocation_engine_mode": scenario["allocation_engine"]["mode"],
                        "metrics": metrics,
                        "monthly_reconciliation": [
                            {"planned_date": when, "contribution_cny": amount}
                            for when, amount in schedule.items()
                        ],
                    }
                ],
                "official_rule_gate": {"status": "blocked"},
                "data_bindings": {"provider_id": "test"},
            }

        protocol_bytes = json.dumps(protocol, sort_keys=True).encode("utf-8")
        with patch(
            "invest_agent.backtest.same_universe_compare.run_local_dca_research",
            side_effect=fake_run,
        ):
            result = run_same_universe_comparison(
                database=Path("unused.sqlite3"),
                candidate_scenario=candidate,
                control_scenario=control,
                strategy_spec={"strategy_id": "candidate", "strategy_version": "0.2.0"},
                strategy_spec_bytes=b"same-spec",
                comparison_protocol=protocol,
                comparison_protocol_bytes=protocol_bytes,
            )

        self.assertEqual(result["candidate_minus_control"]["final_value_cny"], "100.00")
        self.assertEqual(result["candidate_minus_control"]["xirr_delta_pp"], "1.000000000000")
        self.assertEqual(
            result["candidate_minus_control"]["maximum_drawdown_improvement_pp"],
            "2.000000000000",
        )
        self.assertTrue(
            result["invariant_checks"]["identical_monthly_external_cashflows"]
        )
        self.assertFalse(result["real_order_submission_available"])

    def test_route_drift_fails_before_performance_run(self) -> None:
        candidate = self._scenario("candidate-a1", "revalidated_target_gap")
        control = self._scenario("control", "target_gap")
        changed = deepcopy(control)
        changed["routes"][0]["fund_code"] = "000002"
        protocol = self._protocol()

        with self.assertRaisesRegex(ValueError, "route payloads"):
            run_same_universe_comparison(
                database=Path("unused.sqlite3"),
                candidate_scenario=candidate,
                control_scenario=changed,
                strategy_spec={"strategy_id": "candidate", "strategy_version": "0.2.0"},
                strategy_spec_bytes=b"same-spec",
                comparison_protocol=protocol,
                comparison_protocol_bytes=b"protocol",
            )

    def test_project_control_copies_631_execution_semantics_on_424_universe(self) -> None:
        control = load_research_scenario(CONTROL_PATH)
        a1 = load_research_scenario(A1_PATH)
        a2 = load_research_scenario(A2_PATH)
        protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))

        self.assertEqual(control["allocation_engine"]["mode"], "target_gap")
        self.assertTrue(
            control["subscription_assumption"]["carry_unfilled_across_month_end"]
        )
        self.assertEqual(
            control["subscription_assumption"]["unlimited_fund_tranche_calendar_days"],
            [5, 15, 25],
        )
        self.assertEqual(control["routes"], a1["routes"])
        self.assertEqual(control["routes"], a2["routes"])
        self.assertEqual(
            control["allocation_profiles"][0]["weights"],
            a1["allocation_profiles"][0]["weights"],
        )
        self.assertEqual(
            protocol["primary_same_universe_control"]["scenario_id"],
            control["scenario_id"],
        )
        self.assertEqual(
            protocol["excluded_controls"][0]["control_id"], "424_fixed_split_dca"
        )

    def test_v2_reclassifies_a1_as_primary_control_and_keeps_v1_as_sensitivity(self) -> None:
        protocol = json.loads(PROTOCOL_V2_PATH.read_text(encoding="utf-8"))
        a1 = load_research_scenario(A1_PATH)
        a2 = load_research_scenario(A2_PATH)

        self.assertEqual(
            protocol["primary_same_universe_control"]["scenario_id"],
            a1["scenario_id"],
        )
        self.assertEqual(
            protocol["allowed_candidate_scenario_ids"], [a2["scenario_id"]]
        )
        self.assertEqual(
            protocol["execution_sensitivity_controls"][0]["scenario_id"],
            "hierarchical_risk_budget_valuation_424_dca_baseline_v1",
        )
        self.assertEqual(
            protocol["excluded_controls"][0]["control_id"], "424_fixed_split_dca"
        )

    def test_release_destination_sensitivity_changes_only_the_overlay_destination(self) -> None:
        cash = load_research_scenario(A2_PATH)
        bond_gold = load_research_scenario(RELEASE_SENSITIVITY_PATH)
        protocol = json.loads(RELEASE_PROTOCOL_PATH.read_text(encoding="utf-8"))

        self.assertEqual(cash["routes"], bond_gold["routes"])
        self.assertEqual(cash["allocation_profiles"], bond_gold["allocation_profiles"])
        self.assertEqual(
            cash["allocation_engine"]["released_weight_destination"], "cash"
        )
        self.assertEqual(
            bond_gold["allocation_engine"]["released_weight_destination"],
            "bond_gold_2_to_1",
        )
        cash_engine = deepcopy(cash["allocation_engine"])
        bond_gold_engine = deepcopy(bond_gold["allocation_engine"])
        cash_engine.pop("released_weight_destination")
        bond_gold_engine.pop("released_weight_destination")
        self.assertEqual(cash_engine, bond_gold_engine)
        self.assertEqual(
            protocol["primary_same_universe_control"]["scenario_id"],
            cash["scenario_id"],
        )
        self.assertEqual(
            protocol["allowed_candidate_scenario_ids"], [bond_gold["scenario_id"]]
        )

    def test_d1_single_theme_protocol_freezes_one_controlled_sleeve_per_scenario(self) -> None:
        protocol = json.loads(D1_PROTOCOL_PATH.read_text(encoding="utf-8"))
        base = load_research_scenario(RELEASE_SENSITIVITY_PATH)
        observed_ids = []
        observed_sleeves = []
        for path in D1_SCENARIO_PATHS:
            scenario = load_research_scenario(path)
            observed_ids.append(scenario["scenario_id"])
            controlled = scenario["allocation_engine"][
                "controlled_satellite_sleeves"
            ]
            self.assertEqual(len(controlled), 1)
            observed_sleeves.extend(controlled)
            engine = deepcopy(scenario["allocation_engine"])
            engine.pop("controlled_satellite_sleeves")
            self.assertEqual(engine, base["allocation_engine"])
            self.assertEqual(scenario["routes"], base["routes"])
            self.assertEqual(
                scenario["allocation_profiles"], base["allocation_profiles"]
            )
        self.assertEqual(observed_ids, protocol["allowed_candidate_scenario_ids"])
        self.assertEqual(len(set(observed_sleeves)), 4)

    def test_d3_factor_ablation_changes_only_the_frozen_factor_mode(self) -> None:
        protocol = json.loads(D3_PROTOCOL_PATH.read_text(encoding="utf-8"))
        base = load_research_scenario(RELEASE_SENSITIVITY_PATH)
        observed_ids = []
        observed_modes = []
        for path in D3_SCENARIO_PATHS:
            scenario = load_research_scenario(path)
            observed_ids.append(scenario["scenario_id"])
            signal_parameters = scenario["allocation_engine"]["signal_parameters"]
            observed_modes.append(signal_parameters["factor_mode"])

            candidate_engine = deepcopy(scenario["allocation_engine"])
            base_engine = deepcopy(base["allocation_engine"])
            candidate_engine["signal_parameters"].pop("factor_mode")
            self.assertEqual(candidate_engine, base_engine)
            self.assertEqual(scenario["routes"], base["routes"])
            self.assertEqual(
                scenario["allocation_profiles"], base["allocation_profiles"]
            )
            self.assertEqual(scenario["period"], base["period"])
            self.assertEqual(
                scenario["monthly_contribution_cny"],
                base["monthly_contribution_cny"],
            )

        self.assertEqual(observed_ids, protocol["allowed_candidate_scenario_ids"])
        self.assertEqual(
            observed_modes, ["long_average_only", "momentum_only"]
        )
        self.assertEqual(
            protocol["only_allowed_difference"],
            "allocation_engine.signal_parameters.factor_mode",
        )

    def test_d4_strategic_floor_changes_only_the_frozen_floor_multiplier(self) -> None:
        protocol = json.loads(D4_PROTOCOL_PATH.read_text(encoding="utf-8"))
        base = load_research_scenario(RELEASE_SENSITIVITY_PATH)
        candidate = load_research_scenario(D4_SCENARIO_PATH)

        candidate_engine = deepcopy(candidate["allocation_engine"])
        self.assertEqual(
            candidate_engine["signal_parameters"].pop(
                "strategic_floor_multiplier"
            ),
            "0.5",
        )
        self.assertEqual(candidate_engine, base["allocation_engine"])
        self.assertEqual(candidate["routes"], base["routes"])
        self.assertEqual(candidate["allocation_profiles"], base["allocation_profiles"])
        self.assertEqual(candidate["period"], base["period"])
        self.assertEqual(
            protocol["allowed_candidate_scenario_ids"],
            [candidate["scenario_id"]],
        )

    def test_a3a_inverse_volatility_keeps_the_same_universe_and_total_satellite_target(self) -> None:
        protocol = json.loads(A3A_PROTOCOL_PATH.read_text(encoding="utf-8"))
        control = load_research_scenario(A1_PATH)
        candidate = load_research_scenario(A3A_SCENARIO_PATH)

        self.assertEqual(candidate["routes"], control["routes"])
        self.assertEqual(candidate["allocation_profiles"], control["allocation_profiles"])
        self.assertEqual(candidate["period"], control["period"])
        self.assertEqual(
            candidate["monthly_contribution_cny"],
            control["monthly_contribution_cny"],
        )
        weights = candidate["allocation_profiles"][0]["weights"]
        self.assertEqual(
            sum(
                Decimal(weights[sleeve])
                for sleeve in (
                    "global_technology_satellite",
                    "semiconductor_satellite",
                    "robotics_satellite",
                    "biotechnology_satellite",
                )
            ),
            Decimal("0.20"),
        )
        self.assertEqual(
            protocol["allowed_candidate_scenario_ids"],
            [candidate["scenario_id"]],
        )


if __name__ == "__main__":
    unittest.main()
