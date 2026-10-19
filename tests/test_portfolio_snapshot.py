from datetime import datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from invest_agent.domain.portfolio import (
    PortfolioSnapshot,
    PositionStatus,
    PositionSnapshot,
    QualityStatus,
    ensure_public_payload,
)


class PortfolioSnapshotTests(unittest.TestCase):
    def make_snapshot(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            as_of=datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            source="test",
            batch_id="batch-1",
            cash=Decimal("50"),
            positions=(
                PositionSnapshot("000001", Decimal("10"), Decimal("100")),
                PositionSnapshot("000002", Decimal("5"), Decimal("50")),
            ),
        )

    def test_totals_weights_and_public_payload(self) -> None:
        snapshot = self.make_snapshot()
        self.assertEqual(snapshot.total_fund_value, Decimal("150"))
        self.assertEqual(snapshot.total_assets, Decimal("200"))
        self.assertEqual(snapshot.cash_weight, Decimal("0.25"))
        self.assertEqual(snapshot.weights()["000001"], Decimal("0.5"))
        self.assertEqual(snapshot.quality_status, QualityStatus.PASS)
        self.assertNotIn("account_id", snapshot.to_public_dict())

    def test_duplicate_position_fails_quality(self) -> None:
        snapshot = PortfolioSnapshot(
            as_of=self.make_snapshot().as_of,
            source="test",
            batch_id="batch-2",
            cash=Decimal("0"),
            positions=(
                PositionSnapshot("000001", Decimal("1"), Decimal("1")),
                PositionSnapshot("000001", Decimal("1"), Decimal("1")),
            ),
        )
        self.assertEqual(snapshot.quality_status, QualityStatus.FAIL)

    def test_sensitive_keys_are_rejected_recursively(self) -> None:
        with self.assertRaises(ValueError):
            ensure_public_payload({"nested": {"bankAccount": "should-not-exist"}})

    def test_pending_confirmation_is_valid_without_confirmed_shares(self) -> None:
        snapshot = PortfolioSnapshot(
            as_of=self.make_snapshot().as_of,
            source="test",
            batch_id="batch-pending",
            cash=Decimal("0"),
            positions=(
                PositionSnapshot(
                    "539001",
                    Decimal("0"),
                    Decimal("100"),
                    status=PositionStatus.PENDING_CONFIRMATION,
                ),
            ),
        )

        self.assertEqual(snapshot.quality_status, QualityStatus.PASS)
    def test_trade_reconciliation_inclusion(self) -> None:
        tr = {
            "month": "2026-10",
            "start_date": "2026-10-01",
            "end_date": "2026-10-18",
            "source": "thsfund_trade_list_read_only",
            "active_order_count": 50,
            "pending_order_count": 16,
            "confirmed_order_count": 34,
            "aip_cny": "900.00",
            "buy_cny": "1001.20",
            "sell_cny": "0.00",
            "pending_cny": "310.00",
            "by_fund": [
                {
                    "fund_code": "110020",
                    "fund_name": "易方达沪深300A",
                    "aip_cny": "0.00",
                    "buy_cny": "1001.20",
                    "sell_cny": "0.00",
                    "pending_cny": "0.00",
                    "order_count": 2,
                    "latest_accept_date": "2026-10-15",
                    "latest_status": "confirmed",
                }
            ],
            "recent_orders": [],
        }
        snapshot = PortfolioSnapshot(
            as_of=self.make_snapshot().as_of,
            source="test",
            batch_id="batch-recon",
            cash=Decimal("0"),
            positions=(
                PositionSnapshot("110020", Decimal("10"), Decimal("100")),
            ),
            trade_reconciliation=tr,
        )
        self.assertEqual(snapshot.quality_status, QualityStatus.PASS)
        payload = snapshot.to_public_dict()
        self.assertIn("trade_reconciliation", payload)
        self.assertEqual(payload["trade_reconciliation"]["month"], "2026-10")
        self.assertEqual(payload["trade_reconciliation"]["active_order_count"], 50)


if __name__ == "__main__":
    unittest.main()
