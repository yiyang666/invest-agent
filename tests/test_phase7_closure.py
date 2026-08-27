import json
from pathlib import Path
import unittest

from invest_agent.execution.aijijin import load_controlled_live_policy


ROOT = Path(__file__).resolve().parents[1]


class Phase7ClosureTests(unittest.TestCase):
    def test_closure_audit_passes_without_enabling_automatic_mutations(self) -> None:
        audit = json.loads(
            (ROOT / "config/phase7_closure_audit_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(audit["status"], "closed")
        self.assertTrue(all(audit["checks"].values()))
        self.assertTrue(audit["checks"]["automatic_submission_disabled"])
        self.assertTrue(audit["checks"]["redeem_disabled"])
        self.assertTrue(audit["checks"]["revoke_disabled"])

    def test_authoritative_policy_is_controlled_purchase_only(self) -> None:
        policy = load_controlled_live_policy(
            ROOT / "config/phase7_controlled_live_policy_draft_v1.json"
        )
        self.assertEqual(policy.status, "active_controlled_live")
        self.assertEqual(policy.allowed_mutations, ("purchase",))
        self.assertTrue(policy.real_trading_enabled)


if __name__ == "__main__":
    unittest.main()
