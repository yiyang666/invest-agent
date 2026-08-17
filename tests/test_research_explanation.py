import copy
import json
from pathlib import Path
import unittest

from invest_agent.decision.explanation import (
    build_grounded_explanation,
    render_grounded_explanation_markdown,
    run_explanation_acceptance,
    validate_grounded_explanation,
)


ROOT = Path(__file__).resolve().parents[1]


class ResearchExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (ROOT / "config/research_explanation_acceptance_v1.json").read_text(
                encoding="utf-8"
            )
        )
        cls.report = json.loads(
            (ROOT / cls.config["source_report"]["path"]).read_text(encoding="utf-8")
        )

    def test_builds_grounded_explanation_with_complete_coverage(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        validation = validate_grounded_explanation(
            result, report=self.report, config=self.config
        )

        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["sections"], 6)
        self.assertEqual(validation["claims_covered"], 5)
        self.assertEqual(validation["strategies_covered"], 4)
        self.assertEqual(result["execution"]["orders"], [])

    def test_same_source_produces_identical_explanation(self) -> None:
        first = build_grounded_explanation(self.config, workspace_root=ROOT)
        second = build_grounded_explanation(self.config, workspace_root=ROOT)
        self.assertEqual(first, second)

    def test_rejects_unsupported_numeric_claim(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        result["sections"][0]["assertions"][0]["text"] += " 预计上涨10%。"
        with self.assertRaisesRegex(ValueError, "unsupported numeric token"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

    def test_rejects_changed_evidence_number(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        assertion = result["sections"][1]["assertions"][0]
        assertion["text"] = assertion["text"].replace("81.35%", "82.00%")
        assertion["numeric_facts"][1]["display"] = "82.00%"
        with self.assertRaisesRegex(ValueError, "numeric fact display mismatch"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

    def test_rejects_missing_counter_evidence_or_claim(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        result["acknowledged_counter_evidence"].pop()
        with self.assertRaisesRegex(ValueError, "counter-evidence coverage"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        for section in result["sections"]:
            for assertion in section["assertions"]:
                assertion["claim_ids"] = [
                    item for item in assertion["claim_ids"] if item != "oos_not_started"
                ]
        with self.assertRaisesRegex(ValueError, "claim coverage"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

    def test_rejects_action_language_and_execution(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        result["sections"][0]["assertions"][0]["text"] += " 建议立即加仓。"
        with self.assertRaisesRegex(ValueError, "prohibited action language"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        result["execution"]["real_trading_enabled"] = True
        with self.assertRaisesRegex(ValueError, "execution contract"):
            validate_grounded_explanation(result, report=self.report, config=self.config)

    def test_acceptance_matrix_passes_all_fault_injections(self) -> None:
        result = run_explanation_acceptance(self.config, workspace_root=ROOT)
        self.assertEqual(result["summary"]["total"], 14)
        self.assertEqual(result["summary"]["passed"], 14)
        self.assertEqual(result["summary"]["failed"], 0)
        self.assertEqual(result["summary"]["acceptance_gate"], "passed")

    def test_markdown_keeps_research_boundary(self) -> None:
        result = build_grounded_explanation(self.config, workspace_root=ROOT)
        markdown = render_grounded_explanation_markdown(result)
        self.assertIn("仅研究、不可执行", markdown)
        self.assertIn("反证与不确定性", markdown)
        self.assertNotIn("建议立即加仓", markdown)


if __name__ == "__main__":
    unittest.main()
