import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_SCRIPT = ROOT / ".agents/skills/design-fund-strategy/scripts/discovery.py"
PROPOSAL_SCRIPT = ROOT / ".agents/skills/design-fund-strategy/scripts/proposal.py"
DISCOVERY_REPORT = ROOT / "strategies/proposals/hierarchical_risk_budget_valuation_discovery_v0_2_0.toml"
PROPOSAL = ROOT / "strategies/proposals/hierarchical_risk_budget_valuation_v0_2_0.toml"


def _load_discovery_module():
    spec = importlib.util.spec_from_file_location("design_fund_strategy_discovery", DISCOVERY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load discovery validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesignFundStrategySkillGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.discovery_module = _load_discovery_module()
        cls.report = tomllib.loads(DISCOVERY_REPORT.read_text(encoding="utf-8"))

    def test_research_ready_report_allows_exploratory_implementation_only(self) -> None:
        result = self.discovery_module.validate_report(
            self.report, require_research_ready=True
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["ready_for_implementation"])
        self.assertFalse(result["ready_for_promotion"])
        self.assertEqual(result["evidence_class"], "short_window_only")

    def test_research_ready_does_not_satisfy_complete_promotion_gate(self) -> None:
        result = self.discovery_module.validate_report(
            self.report, require_complete=True
        )
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["ready_for_promotion"])
        self.assertIn("discovery report must be complete", result["errors"])

    def test_research_gate_still_blocks_unknown_fund_identity(self) -> None:
        report = copy.deepcopy(self.report)
        report["funds"][0]["identity_status"] = "unknown"
        result = self.discovery_module.validate_report(
            report, require_research_ready=True
        )
        self.assertEqual(result["status"], "error")
        self.assertTrue(
            any("identity must be verified" in error for error in result["errors"])
        )

    def test_frozen_proposal_is_backtest_ready_but_not_promotion_ready(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROPOSAL_SCRIPT),
                "validate",
                str(PROPOSAL),
                "--workspace-root",
                str(ROOT),
                "--require-frozen",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ready_for_backtest"])
        self.assertFalse(result["ready_for_promotion"])


if __name__ == "__main__":
    unittest.main()
