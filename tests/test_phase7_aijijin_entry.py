from decimal import Decimal
import json
from pathlib import Path
import subprocess
import unittest

from invest_agent.execution.aijijin import (
    AijijinCliAdapter,
    load_controlled_live_policy,
    normalize_redeem_preview,
    validate_endpoint_overrides,
)

from tests.test_phase6_mock_approval import _intent


ROOT = Path(__file__).resolve().parents[1]


class Phase7AijijinEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_controlled_live_policy(
            ROOT / "config/phase7_controlled_live_policy_draft_v1.json"
        )

    def test_policy_enables_purchase_only_but_adapter_has_no_automatic_submit(self) -> None:
        self.assertTrue(self.policy.real_trading_enabled)
        self.assertEqual(self.policy.allowed_mutations, ("purchase",))
        adapter = AijijinCliAdapter(
            self.policy,
            executable="aijijin",
            environment={},
            runner=lambda *args, **kwargs: None,
        )
        with self.assertRaisesRegex(PermissionError, "not implemented"):
            adapter.submit_purchase()

    def test_unknown_or_insecure_endpoint_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unapproved"):
            validate_endpoint_overrides(
                {"AIJIJIN_GATEWAY_URL": "https://evil.example"},
                approved_hosts=self.policy.approved_hosts,
            )
        with self.assertRaisesRegex(ValueError, "unapproved"):
            validate_endpoint_overrides(
                {"AIJIJIN_API_BASE_URL": "http://fund.10jqka.com.cn"},
                approved_hosts=self.policy.approved_hosts,
            )

    def test_dry_run_binds_but_does_not_return_transaction_account(self) -> None:
        seen: list[str] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            seen.extend(command)
            payload = {
                "ok": True,
                "data": {
                    "endpoint": "buy",
                    "request": {
                        "buyType": "1",
                        "fundCode": "019861",
                        "money": "200.00",
                        "transactionAccountId": "secret-account-id",
                    },
                },
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        adapter = AijijinCliAdapter(
            self.policy,
            executable="aijijin",
            environment={},
            runner=runner,
        )
        result = adapter.dry_run_purchase(
            _intent(amount_cny=Decimal("200.00")),
            buy_type=1,
            transaction_account_id="secret-account-id",
        )
        self.assertEqual(seen[-1], "--dry-run")
        self.assertNotIn("secret-account-id", json.dumps(result))
        self.assertTrue(result["transaction_account_bound"])
        self.assertFalse(result["real_trading_enabled"])

    def test_proposed_single_order_cap_is_enforced_even_in_dry_run(self) -> None:
        adapter = AijijinCliAdapter(
            self.policy,
            executable="aijijin",
            environment={},
            runner=lambda *args, **kwargs: None,
        )
        with self.assertRaisesRegex(ValueError, "hard cap"):
            adapter.dry_run_purchase(
                _intent(amount_cny=Decimal("5000.01")),
                buy_type=1,
                transaction_account_id="secret-account-id",
            )

    def test_redeem_preview_is_redacted_and_calculates_current_lot_fee(self) -> None:
        raw = {
            "ok": True,
            "data": {
                "data": {
                    "bankInfo": {"bankAccount": "full-secret-account"},
                    "shareHoldTimeList": [{"vol": "441.89", "day": 2}],
                    "shareList": [
                        {
                            "toDepositTime": "2026.08.28 17:00",
                            "toBankTime": "2026.08.31 21:00",
                        }
                    ],
                    "fundInfo": {
                        "fundCode": "006932",
                        "fundName": "平安0-3年期政策性金融债债券A",
                        "nav": "1.13130000",
                        "navDate": "20260826",
                        "minRedemptionVol": "5.00",
                        "minAccountBalance": "5.00",
                        "canRedeemToWallet": "1",
                        "stepRates": [
                            {"lwLimit": "0", "upLimit": "7", "rate": "0.0150", "containsLwLimit": False},
                            {"lwLimit": "7", "upLimit": "30", "rate": "0.0010", "containsLwLimit": True},
                            {"lwLimit": "30", "upLimit": None, "rate": "0.0000", "containsLwLimit": True},
                        ],
                    },
                }
            },
        }
        result = normalize_redeem_preview(raw, expected_fund_code="006932")
        self.assertEqual(result["estimated_full_redemption_fee_cny"], "7.50")
        self.assertEqual(result["holding_lots"][0]["rate_fraction"], "0.0150")
        self.assertTrue(result["can_redeem_to_wallet"])
        self.assertFalse(result["submission_performed"])
        self.assertNotIn("full-secret-account", json.dumps(result, ensure_ascii=False))

    def test_redeem_preview_rejects_fund_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            normalize_redeem_preview(
                {
                    "ok": True,
                    "data": {
                        "data": {
                            "shareHoldTimeList": [],
                            "fundInfo": {
                                "fundCode": "000001",
                                "nav": "1",
                                "stepRates": [{"lwLimit": "0", "upLimit": None, "rate": "0"}],
                            },
                        }
                    },
                },
                expected_fund_code="006932",
            )


if __name__ == "__main__":
    unittest.main()
