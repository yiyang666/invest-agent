import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "config/aijijin_research_route_snapshot_v1.json"


class AijijinResearchRouteSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    def test_snapshot_is_read_only_sanitized_and_has_expected_routes(self) -> None:
        self.assertEqual(self.payload["mode"], "sanitized_read_only_research_evidence")
        self.assertFalse(self.payload["research_semantics"]["real_order_submission_allowed"])
        self.assertFalse(self.payload["privacy_filter"]["raw_protected_response_persisted"])
        self.assertFalse(self.payload["privacy_filter"]["account_fields_persisted"])
        self.assertFalse(self.payload["privacy_filter"]["customer_fields_persisted"])
        self.assertEqual(
            {route["fund_code"] for route in self.payload["routes"]},
            {"000216", "007300", "008114", "014880", "016452", "017894", "020712", "100055"},
        )

    def test_route_payload_has_only_research_rule_fields(self) -> None:
        allowed_route_fields = {
            "fund_code",
            "fund_name",
            "purchase_status",
            "minimum_purchase_cny",
            "daily_purchase_limit_cny",
            "observed_remaining_daily_capacity_cny",
            "fund_risk_level",
            "statutory_first_tier_purchase_fee_rate",
            "bank_channel_purchase_fee_rate",
            "wallet_channel_purchase_fee_rate",
            "redemption_fee_tiers",
            "buy_confirmation_business_days",
            "sell_confirmation_business_days",
            "sell_cash_arrival_business_days",
        }
        for route in self.payload["routes"]:
            self.assertEqual(set(route), allowed_route_fields)
            self.assertIn(route["purchase_status"], {"open", "limited"})

        serialized = json.dumps(self.payload, ensure_ascii=False).lower()
        for forbidden in (
            "account_no",
            "account_id",
            "customer_id",
            "bank_card",
            "payment_account",
            "access_token",
            "refresh_token",
            "secret_key",
            "private_key",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_observed_limits_and_fees_match_normalized_current_channel_rules(self) -> None:
        routes = {route["fund_code"]: route for route in self.payload["routes"]}
        expected = {
            "016452": ("1", "10", "0.0012"),
            "020712": ("1", "10", "0.0006"),
            "100055": ("100", "1000", "0.0015"),
            "007300": ("100", None, "0.0008"),
            "014880": ("100", None, "0.001"),
            "017894": ("1", "100", "0.0012"),
            "000216": ("100", None, "0.0006"),
            "008114": ("100", None, "0.001"),
        }
        for code, (minimum, limit, fee) in expected.items():
            self.assertEqual(routes[code]["minimum_purchase_cny"], minimum)
            self.assertEqual(routes[code]["daily_purchase_limit_cny"], limit)
            self.assertEqual(routes[code]["bank_channel_purchase_fee_rate"], fee)


if __name__ == "__main__":
    unittest.main()
