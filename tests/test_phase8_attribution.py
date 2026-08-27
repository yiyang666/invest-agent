from pathlib import Path
import json
import tempfile
import unittest

from invest_agent.attribution import (
    attribute_sleeves_brinson,
    calculate_cashflow_attribution,
    compare_cashflow_matched_paths,
    evaluate_strategy_lifecycle,
    load_attribution_policy,
    sequential_ablation_waterfall,
)
from invest_agent.attribution.cli import main


ROOT = Path(__file__).resolve().parents[1]
HASH_A = "a" * 64


def _candidate_periods() -> list[dict[str, object]]:
    return [
        {
            "period_start": "2026-01-01",
            "period_end": "2026-02-01",
            "start_value_cny": "1000",
            "end_value_cny": "1650",
            "external_flows": [{"amount_cny": "500", "remaining_weight": "1"}],
            "fees_cny": "5",
            "cash_drag_cost_cny": "2",
        },
        {
            "period_start": "2026-02-01",
            "period_end": "2026-03-01",
            "start_value_cny": "1650",
            "end_value_cny": "1815",
            "external_flows": [],
            "fees_cny": "0",
            "cash_drag_cost_cny": "0",
        },
    ]


def _benchmark_periods() -> list[dict[str, object]]:
    return [
        {
            "period_start": "2026-01-01",
            "period_end": "2026-02-01",
            "start_value_cny": "1000",
            "end_value_cny": "1575",
            "external_flows": [{"amount_cny": "500", "remaining_weight": "1"}],
        },
        {
            "period_start": "2026-02-01",
            "period_end": "2026-03-01",
            "start_value_cny": "1575",
            "end_value_cny": "1653.75",
            "external_flows": [],
        },
    ]


class Phase8AttributionTests(unittest.TestCase):
    def test_phase8_closure_is_research_only_and_fully_audited(self) -> None:
        audit = json.loads(
            (ROOT / "config/phase8_closure_audit_v1.json").read_text(
                encoding="utf-8"
            )
        )
        policy = load_attribution_policy(
            ROOT / "config/phase8_attribution_policy_v1.json"
        )
        self.assertEqual(audit["status"], "closed")
        self.assertTrue(all(audit["checks"].values()))
        self.assertEqual(
            audit["operational_evidence_state"],
            "accumulating_forward_monthly_observations",
        )
        self.assertFalse(policy["safety"]["order_generation_allowed"])
        self.assertFalse(policy["safety"]["real_trading_enabled"])

    def test_cashflow_attribution_separates_principal_and_links_returns(self) -> None:
        result = calculate_cashflow_attribution(_candidate_periods())
        self.assertEqual(result["summary"]["net_external_flow_cny"], "500.00")
        self.assertEqual(result["summary"]["investment_pnl_cny"], "315.00")
        self.assertEqual(result["summary"]["final_value_cny"], "1815.00")
        self.assertEqual(result["summary"]["linked_twr"], "0.210000000000")
        self.assertEqual(result["summary"]["observed_fees_cny"], "5.00")
        self.assertTrue(result["identity_reconciled"])
        self.assertEqual(
            result["report_sha256"],
            calculate_cashflow_attribution(_candidate_periods())["report_sha256"],
        )

    def test_matched_comparison_rejects_cashflow_or_date_divergence(self) -> None:
        result = compare_cashflow_matched_paths(
            _candidate_periods(), _benchmark_periods()
        )
        self.assertEqual(result["benchmark"]["summary"]["linked_twr"], "0.102500000000")
        self.assertEqual(result["difference"]["linked_twr"], "0.107500000000")
        changed = _benchmark_periods()
        changed[0]["external_flows"] = [
            {"amount_cny": "501", "remaining_weight": "1"}
        ]
        with self.assertRaisesRegex(ValueError, "timelines diverged"):
            compare_cashflow_matched_paths(_candidate_periods(), changed)

    def test_period_continuity_and_nonpositive_denominator_fail_closed(self) -> None:
        broken = _candidate_periods()
        broken[1]["start_value_cny"] = "1649"
        with self.assertRaisesRegex(ValueError, "contiguous"):
            calculate_cashflow_attribution(broken)
        with self.assertRaisesRegex(ValueError, "denominator"):
            calculate_cashflow_attribution(
                [
                    {
                        "period_start": "2026-01-01",
                        "period_end": "2026-02-01",
                        "start_value_cny": "100",
                        "end_value_cny": "0",
                        "external_flows": [
                            {"amount_cny": "-100", "remaining_weight": "1"}
                        ],
                    }
                ]
            )

    def test_sleeve_attribution_reconciles_and_keeps_cash_explicit(self) -> None:
        portfolio = {
            "equity": {"weight": "0.6", "return": "0.10"},
            "bond": {"weight": "0.3", "return": "0.02"},
            "cash": {"weight": "0.1", "return": "0"},
        }
        benchmark = {
            "equity": {"weight": "0.5", "return": "0.08"},
            "bond": {"weight": "0.4", "return": "0.03"},
            "cash": {"weight": "0.1", "return": "0"},
        }
        result = attribute_sleeves_brinson(portfolio, benchmark)
        self.assertEqual(result["summary"]["portfolio_return"], "0.066000000000")
        self.assertEqual(result["summary"]["benchmark_return"], "0.052000000000")
        self.assertEqual(result["summary"]["active_return"], "0.014000000000")
        self.assertEqual(result["summary"]["residual"], "0E-12")
        self.assertTrue(result["cash_sleeve_must_remain_explicit"])

    def test_sequential_waterfall_labels_policy_and_signal_without_overclaiming(self) -> None:
        result = sequential_ablation_waterfall(
            [
                {"stage_id": "631", "claim_scope": "baseline", "metric_value": "100", "evidence_sha256": HASH_A},
                {"stage_id": "424_dca", "claim_scope": "total_policy_outcome", "metric_value": "110", "evidence_sha256": HASH_A},
                {"stage_id": "traffic_light", "claim_scope": "signal_effect", "metric_value": "108", "evidence_sha256": HASH_A},
                {"stage_id": "full", "claim_scope": "combined_strategy_effect", "metric_value": "120", "evidence_sha256": HASH_A},
            ]
        )
        self.assertEqual(result["total_difference"], "20.00")
        self.assertEqual(
            [row["incremental_contribution"] for row in result["contributions"]],
            ["10.00", "-2.00", "12.00"],
        )
        self.assertEqual(
            result["contributions"][0]["claim_scope"], "total_policy_outcome"
        )
        self.assertFalse(result["interpretation"]["automatic_winner_selection"])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            sequential_ablation_waterfall(
                [
                    {"stage_id": "base", "claim_scope": "baseline", "metric_value": "1", "evidence_sha256": "z" * 64},
                    {"stage_id": "next", "claim_scope": "signal_effect", "metric_value": "2", "evidence_sha256": HASH_A},
                ]
            )

    def test_lifecycle_priorities_are_fail_closed_and_never_automatic(self) -> None:
        policy = load_attribution_policy(
            ROOT / "config/phase8_attribution_policy_v1.json"
        )
        base = {
            "strategy_id": "candidate",
            "strategy_version": "1.0.0",
            "benchmark": "dca_baseline@1.5.0",
            "forward_observation_months": 6,
            "data_fresh": True,
            "deterministic_rerun_match": True,
            "matched_cashflow_comparator": True,
            "risk_veto": False,
            "unresolved_violations": [],
            "expected_spec_sha256": HASH_A,
            "observed_spec_sha256": HASH_A,
            "maximum_target_weight_drift_pp": "0",
            "active_return_pct": "1",
            "maximum_drawdown_worsening_pp": "0",
        }
        observed = evaluate_strategy_lifecycle(base, policy)
        self.assertEqual(observed["state"], "continue_forward_observation")
        self.assertFalse(observed["automatic_promotion"])
        vetoed = evaluate_strategy_lifecycle({**base, "risk_veto": True}, policy)
        self.assertEqual(vetoed["state"], "risk_veto")
        retiring = evaluate_strategy_lifecycle(
            {
                **base,
                "forward_observation_months": 24,
                "active_return_pct": "-2.01",
            },
            policy,
        )
        self.assertEqual(retiring["state"], "retirement_review")
        self.assertFalse(retiring["automatic_retirement"])

    def test_cli_writes_reusable_waterfall_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "input.json"
            output = Path(temp) / "output.json"
            source.write_text(
                '{"stages":[{"stage_id":"base","claim_scope":"baseline","metric_value":"10","evidence_sha256":"'
                + HASH_A
                + '"},{"stage_id":"candidate","claim_scope":"signal_effect","metric_value":"12","evidence_sha256":"'
                + HASH_A
                + '"}]}',
                encoding="utf-8",
            )
            self.assertEqual(
                main(["waterfall", "--input", str(source), "--output", str(output)]),
                0,
            )
            self.assertIn('"total_difference": "2.00"', output.read_text())


if __name__ == "__main__":
    unittest.main()
