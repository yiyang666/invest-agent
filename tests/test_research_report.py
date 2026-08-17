import copy
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.decision.reporting import (
    _pointer,
    build_research_report,
    render_research_report_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


class ResearchReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (ROOT / "config/research_report_v1.json").read_text(encoding="utf-8")
        )

    def test_builds_evidence_bound_non_executable_report(self) -> None:
        report = build_research_report(self.config, workspace_root=ROOT)

        self.assertEqual(report["mode"], "research_only")
        self.assertFalse(report["scope"]["external_market_news_used"])
        self.assertFalse(report["scope"]["market_direction_prediction"])
        self.assertEqual(len(report["strategy_views"]), 4)
        self.assertEqual(
            sum(item["target_allocation_authority"] for item in report["strategy_views"]),
            1,
        )
        self.assertEqual(report["execution"]["orders"], [])
        self.assertFalse(report["execution"]["real_trading_enabled"])
        self.assertEqual(report["next_review"]["date"], "2026-09-01")
        authority_claim = next(
            item for item in report["evidence_ledger"] if item["claim_id"] == "baseline_authority"
        )
        self.assertIn("/accepted_signals/", authority_claim["source"]["json_pointer"])

    def test_same_locked_inputs_produce_identical_report(self) -> None:
        first = build_research_report(self.config, workspace_root=ROOT)
        second = build_research_report(self.config, workspace_root=ROOT)

        self.assertEqual(first, second)
        self.assertEqual(
            first["reproducibility"]["evidence_bundle_sha256"],
            second["reproducibility"]["evidence_bundle_sha256"],
        )

    def test_source_hash_drift_fails_closed(self) -> None:
        config = copy.deepcopy(self.config)
        config["sources"]["decision_pack"]["sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            build_research_report(config, workspace_root=ROOT)

    def test_safety_cannot_enable_news_prediction_or_orders(self) -> None:
        for key in (
            "external_market_news_allowed",
            "price_prediction_allowed",
            "llm_numeric_calculation_allowed",
            "order_intent_generation_allowed",
            "real_trading_enabled",
        ):
            config = copy.deepcopy(self.config)
            config["safety"][key] = True
            with self.assertRaisesRegex(ValueError, "safety contract"):
                build_research_report(config, workspace_root=ROOT)

    def test_json_pointer_rejects_missing_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            _pointer({"known": 1}, "/missing")

    def test_renderer_rejects_execution_tampering(self) -> None:
        report = build_research_report(self.config, workspace_root=ROOT)
        report["execution"]["real_trading_enabled"] = True

        with self.assertRaisesRegex(ValueError, "cannot render"):
            render_research_report_markdown(report)

    def test_renderer_contains_limits_without_external_news(self) -> None:
        report = build_research_report(self.config, workspace_root=ROOT)
        markdown = render_research_report_markdown(report)

        self.assertIn("没有使用外部新闻", markdown)
        self.assertIn("不是账户真实收益记录", markdown)
        self.assertIn("订单数组为空", markdown)
        self.assertNotIn("建议买入", markdown)


if __name__ == "__main__":
    unittest.main()
