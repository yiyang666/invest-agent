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
        self.assertEqual(
            snapshot.to_public_dict()["positions"][0]["status"],
            "pending_confirmation",
        )


if __name__ == "__main__":
    unittest.main()
