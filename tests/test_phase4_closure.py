import unittest

from invest_agent.decision.closure import classify_phase4_closure


class Phase4ClosureTests(unittest.TestCase):
    def test_engineering_can_close_while_production_remains_blocked(self) -> None:
        result = classify_phase4_closure(
            [{"passed": True}, {"passed": True}],
            production_blockers=["prospective_oos_incomplete"],
        )

        self.assertEqual(result["engineering_research_status"], "closed")
        self.assertEqual(result["production_readiness"], "blocked")
        self.assertEqual(result["phase5_entry"], "allowed_research_reporting_only")

    def test_failed_engineering_check_blocks_phase5(self) -> None:
        result = classify_phase4_closure(
            [{"passed": True}, {"passed": False}],
            production_blockers=[],
        )

        self.assertEqual(result["engineering_research_status"], "not_closed")
        self.assertEqual(result["phase5_entry"], "blocked")


if __name__ == "__main__":
    unittest.main()
