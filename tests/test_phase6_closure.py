import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Phase6ClosureTests(unittest.TestCase):
    def test_closure_audit_is_complete_but_live_production_is_blocked(self) -> None:
        payload = json.loads(
            (ROOT / "config/phase6_closure_audit_v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["status"], "closed")
        disabled_safety_flags = {
            "network_used",
            "external_side_effects",
            "real_trading_enabled",
        }
        completed_checks = {
            key: value
            for key, value in payload["checks"].items()
            if key not in disabled_safety_flags
        }
        self.assertTrue(all(completed_checks.values()))
        self.assertTrue(
            all(payload["checks"][key] is False for key in disabled_safety_flags)
        )
        self.assertEqual(
            payload["production_state"],
            "blocked_pending_phase7_controlled_live_gate",
        )
        for group in payload["evidence"].values():
            for relative_path in group:
                self.assertTrue((ROOT / relative_path).is_file(), relative_path)


if __name__ == "__main__":
    unittest.main()
