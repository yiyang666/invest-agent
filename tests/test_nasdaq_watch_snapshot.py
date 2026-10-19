import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/fund_watch_catalog_v1.json"
SNAPSHOT_PATH = ROOT / "config/aijijin_nasdaq_watch_snapshot_2026_10_15.json"


class NasdaqWatchSnapshotTests(unittest.TestCase):
    def test_dividend_funds_follow_latest_core_classification(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        entries = {item["fund_code"]: item for item in catalog["classifications"]}

        self.assertEqual(entries["008163"]["fund_type"], "标普红利低波50")
        self.assertEqual(entries["009051"]["fund_type"], "中证红利")
        for code in ("008114", "008163", "009051"):
            self.assertEqual(entries[code]["position_bucket"], "核心仓")

    def test_requested_nasdaq_funds_are_in_watch_catalog(self) -> None:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        entries = {item["fund_code"]: item for item in catalog["classifications"]}

        for code in ("019441", "019172", "016452", "019736"):
            self.assertIn(code, entries)
            self.assertEqual(entries[code]["sleeve"], "us_nasdaq_core")
            self.assertEqual(entries[code]["fund_type"], "纳斯达克100")
            self.assertEqual(entries[code]["position_bucket"], "核心仓")

    def test_snapshot_is_sanitized_read_only_and_not_an_execution_route(self) -> None:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        routes = {item["fund_code"]: item for item in payload["routes"]}

        self.assertEqual(payload["mode"], "sanitized_read_only_research_evidence")
        self.assertEqual(set(routes), {"019441", "019172", "016452", "019736"})
        self.assertFalse(payload["privacy_filter"]["raw_protected_response_persisted"])
        self.assertFalse(payload["research_semantics"]["automatic_purchase_route_admission"])
        self.assertFalse(payload["research_semantics"]["real_order_submission_allowed"])
        self.assertFalse(payload["research_semantics"]["a_c_shared_limit_assumed"])

        for route in routes.values():
            self.assertEqual(route["purchase_status"], "limited")
            self.assertEqual(route["minimum_purchase_cny"], "1")
            self.assertEqual(route["minimum_additional_purchase_cny"], "1")
            self.assertEqual(route["maximum_single_purchase_cny"], "10")
            self.assertEqual(route["observed_remaining_daily_capacity_cny"], "10")

        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for forbidden in (
            "bankaccount",
            "transactionaccountid",
            "custid",
            "access_token",
            "refresh_token",
            "secret_key",
            "private_key",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
