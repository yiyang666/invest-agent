from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.backtest.product_rules import (
    load_execution_rule_snapshot,
    resolve_subscription_rule,
)


class ProductRuleTests(unittest.TestCase):
    def _snapshot(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "snapshot_id": "rules-test-v1",
            "as_of": "2026-08-15T20:00:00+08:00",
            "status": "verified",
            "funds": [
                {
                    "fund_code": "000001",
                    "fund_name": "测试基金A",
                    "versions": [
                        {
                            "rule_version": "000001-2026-v1",
                            "effective_from": "2026-01-01",
                            "effective_to": None,
                            "authority": "official_verified",
                            "source_documents": [
                                {
                                    "url": "https://example.test/rule.pdf",
                                    "content_sha256": "a" * 64,
                                    "retrieved_at": "2026-08-15T19:00:00+08:00"
                                }
                            ],
                            "subscription": {
                                "minimum_purchase_cny": "1",
                                "daily_purchase_limit": {
                                    "kind": "amount",
                                    "amount_cny": "10000"
                                },
                                "cutoff_time": "15:00:00",
                                "confirmation_business_days": 1,
                                "nav_visibility_business_days": 1,
                                "purchase_fee_tiers": [
                                    {
                                        "minimum_cny_inclusive": "0",
                                        "maximum_cny_exclusive": "1000000",
                                        "kind": "rate",
                                        "rate": "0.012"
                                    },
                                    {
                                        "minimum_cny_inclusive": "1000000",
                                        "maximum_cny_exclusive": None,
                                        "kind": "fixed",
                                        "fixed_fee_cny": "1000"
                                    }
                                ],
                                "share_precision": 2,
                                "share_rounding_mode": "down",
                                "calendar_id": "cn_fund_open_day"
                            },
                            "unknown_required_fields": []
                        }
                    ]
                }
            ]
        }

    def test_loads_and_resolves_exact_effective_rule(self) -> None:
        snapshot = self._snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            loaded = load_execution_rule_snapshot(path)
        result = resolve_subscription_rule(
            loaded,
            fund_code="000001",
            submit_date=date(2026, 8, 15),
            gross_amount_cny=Decimal("3000"),
        )

        self.assertEqual(result.rule_version, "000001-2026-v1")
        self.assertEqual(result.purchase_fee_model, "proportional_front_end")
        self.assertEqual(result.purchase_fee_rate, Decimal("0.012"))
        self.assertEqual(result.daily_purchase_limit_cny, Decimal("10000"))
        self.assertEqual(result.source_content_sha256, ("a" * 64,))

    def test_unknown_required_field_blocks_even_experimental_resolution(self) -> None:
        snapshot = self._snapshot()
        snapshot["status"] = "provisional"
        snapshot["funds"][0]["versions"][0]["authority"] = "reference_only"
        snapshot["funds"][0]["versions"][0]["unknown_required_fields"] = [
            "cutoff_time"
        ]

        with self.assertRaisesRegex(ValueError, "remain unknown"):
            resolve_subscription_rule(
                snapshot,
                fund_code="000001",
                submit_date=date(2026, 8, 15),
                gross_amount_cny=Decimal("3000"),
                require_official_verified=False,
            )

    def test_reference_rule_cannot_pass_official_gate(self) -> None:
        snapshot = self._snapshot()
        snapshot["status"] = "provisional"
        snapshot["funds"][0]["versions"][0]["authority"] = "reference_only"

        with self.assertRaisesRegex(ValueError, "official verified"):
            resolve_subscription_rule(
                snapshot,
                fund_code="000001",
                submit_date=date(2026, 8, 15),
                gross_amount_cny=Decimal("3000"),
            )

    def test_loader_rejects_overlapping_rule_versions(self) -> None:
        snapshot = self._snapshot()
        overlap = deepcopy(snapshot["funds"][0]["versions"][0])
        overlap["rule_version"] = "000001-overlap"
        overlap["effective_from"] = "2026-08-01"
        snapshot["funds"][0]["versions"][0]["effective_to"] = "2026-08-31"
        snapshot["funds"][0]["versions"].append(overlap)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "overlapping"):
                load_execution_rule_snapshot(path)

    def test_amount_must_match_one_fee_tier_and_minimum(self) -> None:
        snapshot = self._snapshot()
        with self.assertRaisesRegex(ValueError, "below"):
            resolve_subscription_rule(
                snapshot,
                fund_code="000001",
                submit_date=date(2026, 8, 15),
                gross_amount_cny=Decimal("0.50"),
            )


if __name__ == "__main__":
    unittest.main()
