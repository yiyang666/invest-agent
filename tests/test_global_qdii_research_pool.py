import json
from pathlib import Path
import unittest

from invest_agent.data.sync import load_sync_config, resolve_sync_funds


ROOT = Path(__file__).resolve().parents[1]
POOL_PATH = ROOT / "config/global_qdii_research_pool_v1.json"


class GlobalQdiiResearchPoolTests(unittest.TestCase):
    def test_pool_contains_twenty_unique_purchase_confirmed_tagged_funds(self) -> None:
        payload = json.loads(POOL_PATH.read_text(encoding="utf-8"))
        funds = payload["funds"]

        self.assertEqual(payload["mode"], "research_only")
        self.assertEqual(payload["candidate_count"], 20)
        self.assertEqual(len(funds), 20)
        self.assertEqual(len({item["fund_code"] for item in funds}), 20)
        self.assertTrue(all(item["purchase_confirmed"] is True for item in funds))
        self.assertTrue(all(item["availability"] in {"open", "limited"} for item in funds))
        self.assertTrue(all("QDII" in item["tags"] for item in funds))
        self.assertTrue(
            all({"LOF", "非LOF"} & set(item["tags"]) for item in funds)
        )
        self.assertFalse(payload["rules"]["real_order_submission_allowed"])

    def test_sync_universe_includes_every_global_qdii_research_fund(self) -> None:
        config = load_sync_config(ROOT / "config/fund_data_sync_v1.json")
        resolved = {
            item.fund_code: item.reasons
            for item in resolve_sync_funds(config, workspace_root=ROOT)
        }
        payload = json.loads(POOL_PATH.read_text(encoding="utf-8"))

        for item in payload["funds"]:
            self.assertIn(item["fund_code"], resolved)
            self.assertIn("global_qdii_research_pool", resolved[item["fund_code"]])


if __name__ == "__main__":
    unittest.main()
