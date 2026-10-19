import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/fund_watch_catalog_v1.json"
FEE_RULES_PATH = ROOT / "config/fund_fee_rules_v1.json"


class FundFeeRulesCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.fee_rules = json.loads(FEE_RULES_PATH.read_text(encoding="utf-8"))

    def test_all_watched_funds_have_registered_channel_fee_rules(self) -> None:
        watched_codes = {item["fund_code"] for item in self.catalog["classifications"]}
        registered_codes = {item["fund_code"] for item in self.fee_rules["funds"]}
        self.assertTrue(watched_codes.issubset(registered_codes))

    def test_short_bond_006932_has_exact_7_to_30_day_redemption_fee_tier(self) -> None:
        by_code = {item["fund_code"]: item for item in self.fee_rules["funds"]}
        bond = by_code["006932"]
        self.assertEqual(bond["discounted_first_tier_purchase_fee_rate"], 0.0003)
        self.assertEqual(bond["zero_redemption_fee_min_holding_days"], 30)
        tiers = bond["redemption_fee_tiers"]
        self.assertEqual(len(tiers), 3)
        self.assertEqual(tiers[0]["minimum_holding_days_inclusive"], 0)
        self.assertEqual(tiers[0]["maximum_holding_days_exclusive"], 7)
        self.assertEqual(tiers[0]["rate"], 0.015)
        self.assertEqual(tiers[1]["minimum_holding_days_inclusive"], 7)
        self.assertEqual(tiers[1]["maximum_holding_days_exclusive"], 30)
        self.assertEqual(tiers[1]["rate"], 0.001)
        self.assertEqual(tiers[2]["minimum_holding_days_inclusive"], 30)
        self.assertIsNone(tiers[2]["maximum_holding_days_exclusive"])
        self.assertEqual(tiers[2]["rate"], 0.0)

    def test_no_sensitive_account_or_token_fields_persisted(self) -> None:
        serialized = json.dumps(self.fee_rules, ensure_ascii=False).lower()
        for forbidden in (
            "bankaccount",
            "transactionaccountid",
            "custid",
            "worktoken",
            "refreshtoken",
            "secret_key",
            "private_key",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
