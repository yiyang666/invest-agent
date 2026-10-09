from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.decision.brief_cli import build_monthly_brief


class MonthlyBriefTests(unittest.TestCase):
    def test_current_month_verified_report_renders_four_decisions(self) -> None:
        report = {
            "as_of": "2026-10-09T23:00:00+08:00",
            "execution": {"orders": []},
            "executive_summary": {"status": "review_required", "headline": "维持观察", "evidence_confidence": "limited"},
            "portfolio_state": {"total_assets_cny": "6000", "cash_cny": "1000", "single_fund_limit_breaches": [{"fund_code": "000001", "actual_pct": 30.0, "limit_pct": 25.0}]},
            "decision_summary": {"monthly_action_status": "no_schedule", "current_risk_response": "检查集中度", "additional_budget_status": "not_adopted", "baseline": "631"},
            "uncertainties": ["交易规则待刷新"],
            "next_review": {"date": "2026-11-01"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "report.json"
            raw = json.dumps(report).encode()
            path.write_bytes(raw)
            manifest = {"as_of": report["as_of"], "summary": {"publication_gate": "passed"}, "artifacts": {"research_report_json": {"path": "report.json", "sha256": hashlib.sha256(raw).hexdigest()}}}
            brief = build_monthly_brief(manifest, root=root, today=date(2026, 10, 9))
            for heading in ("现状", "风险", "行动草案", "理由与下次复核"):
                self.assertIn(heading, brief)
            self.assertIn("30.0%", brief)
            with self.assertRaisesRegex(ValueError, "current month"):
                build_monthly_brief(manifest, root=root, today=date(2026, 11, 1))
            path.write_text("tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                build_monthly_brief(manifest, root=root, today=date(2026, 10, 9))


if __name__ == "__main__":
    unittest.main()
