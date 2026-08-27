import json
from pathlib import Path
import unittest

from invest_agent.decision.registry import build_decision_input, validate_strategy_registry


ROOT = Path(__file__).resolve().parents[1]


def _signal(strategy_id: str = "dca_baseline", version: str = "1.5.0") -> dict:
    return {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "signal_as_of": "2026-08-25T08:00:00+08:00",
        "data_cutoff": "2026-08-24T23:59:59+08:00",
        "valid_until": "2026-08-31T23:59:59+08:00",
        "data_quality": {"status": "historical_visibility_assumed", "issues": []},
        "risk": {"veto": False, "reasons": []},
        "evidence": {"artifact_sha256": "a" * 64},
        "payload": {"signal": "base_plan"},
    }


class StrategyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = json.loads(
            (ROOT / "strategies/registry.json").read_text(encoding="utf-8")
        )

    def test_registry_has_four_exact_safe_entries_and_one_target_authority(self) -> None:
        index = validate_strategy_registry(self.registry, workspace_root=ROOT)

        self.assertEqual(self.registry["schema_version"], 3)
        self.assertEqual(len(index), 4)
        self.assertIn(("dca_baseline", "1.5.0"), index)
        self.assertIn(("sleeve_drawdown_recovery", "1.0.0"), index)
        self.assertEqual(
            sum(
                item["decision_permissions"]["target_allocation_authority"]
                for item in self.registry["strategies"]
            ),
            1,
        )
        self.assertTrue(
            all(not item["gates"]["execution_enabled"] for item in self.registry["strategies"])
        )

    def test_631_anchor_is_distinct_from_dca_benchmark_and_allows_bounded_overlays(self) -> None:
        terminology = self.registry["terminology"]

        self.assertFalse(
            terminology["allocation_anchor_631"]["mandatory_execution_schedule"]
        )
        self.assertEqual(
            terminology["dca_baseline@1.5.0"]["kind"],
            "comparison_benchmark_strategy",
        )
        self.assertTrue(
            terminology["tactical_overlay"]["may_deviate_from_631_during_execution"]
        )
        self.assertTrue(
            terminology["tactical_overlay"]["requires_matched_cashflow_comparator"]
        )

    def test_registered_current_signal_enters_advisory_input_only(self) -> None:
        result = build_decision_input(
            self.registry,
            workspace_root=ROOT,
            as_of="2026-08-25T23:59:59+08:00",
            signals=[_signal()],
        )

        self.assertEqual(len(result["accepted_signals"]), 1)
        self.assertTrue(result["accepted_signals"][0]["target_allocation_authority"])
        self.assertFalse(result["accepted_signals"][0]["execution_enabled"])
        self.assertFalse(result["execution"]["order_intent_generation_allowed"])
        self.assertFalse(result["execution"]["real_trading_enabled"])

    def test_unregistered_expired_and_risk_vetoed_signals_are_rejected(self) -> None:
        unregistered = _signal("unknown_strategy", "1.0.0")
        expired = _signal()
        expired["valid_until"] = "2026-08-24T23:59:59+08:00"
        vetoed = _signal("drawdown_budget_add", "1.0.0")
        vetoed["risk"] = {"veto": True, "reasons": ["portfolio_drawdown_limit"]}

        result = build_decision_input(
            self.registry,
            workspace_root=ROOT,
            as_of="2026-08-25T23:59:59+08:00",
            signals=[unregistered, expired, vetoed],
        )

        self.assertEqual(result["accepted_signals"], [])
        self.assertEqual(
            {item["reason"] for item in result["rejected_signals"]},
            {"unregistered_or_version_mismatch", "signal_expired", "risk_veto"},
        )

    def test_registry_cannot_enable_execution(self) -> None:
        self.registry["strategies"][0]["gates"]["execution_enabled"] = True

        with self.assertRaisesRegex(ValueError, "cannot enable execution"):
            validate_strategy_registry(self.registry, workspace_root=ROOT)

    def test_registry_rejects_spec_hash_drift(self) -> None:
        self.registry["strategies"][0]["spec"]["sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "spec hash mismatch"):
            validate_strategy_registry(self.registry, workspace_root=ROOT)

    def test_decision_input_rejects_future_registry(self) -> None:
        with self.assertRaisesRegex(ValueError, "registry cannot be newer"):
            build_decision_input(
                self.registry,
                workspace_root=ROOT,
                as_of="2026-08-25T12:00:00+08:00",
                signals=[_signal()],
            )


if __name__ == "__main__":
    unittest.main()
