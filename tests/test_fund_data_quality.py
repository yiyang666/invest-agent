from datetime import date, datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import DataBatch, FundNavRecord, VisibilityStatus
from invest_agent.data.quality import evaluate_nav_batch
from invest_agent.domain.portfolio import QualityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FundDataQualityTests(unittest.TestCase):
    def test_strict_visibility_rejects_data_seen_after_as_of(self) -> None:
        batch = DataBatch(
            provider_id="test",
            batch_id="strict-future",
            fetched_at=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
            as_of=datetime(2026, 8, 15, 18, tzinfo=SHANGHAI),
            records=(
                FundNavRecord(
                    fund_code="000001",
                    nav_date=date(2026, 8, 15),
                    unit_nav=Decimal("1.23"),
                    first_seen_at=datetime(2026, 8, 15, 19, tzinfo=SHANGHAI),
                    visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
                ),
            ),
            provenance="test fixture",
            source_domain="example.test",
            request_parameters={"fund_code": "000001"},
            raw_content_sha256="a" * 64,
        )

        report = evaluate_nav_batch(batch)

        self.assertEqual(report.status, QualityStatus.FAIL)
        self.assertIn("future_visible_observation", {issue.code for issue in report.issues})

    def test_suspicious_jump_warns_but_can_publish(self) -> None:
        batch = DataBatch(
            provider_id="test",
            batch_id="jump-warning",
            fetched_at=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
            as_of=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
            records=(
                FundNavRecord("000001", date(2026, 8, 14), Decimal("1.00")),
                FundNavRecord("000001", date(2026, 8, 15), Decimal("1.60")),
            ),
            provenance="test fixture",
            source_domain="example.test",
            request_parameters={"fund_code": "000001"},
            raw_content_sha256="a" * 64,
        )

        report = evaluate_nav_batch(batch)

        self.assertEqual(report.status, QualityStatus.PARTIAL)
        self.assertTrue(report.can_publish)
        self.assertIn("suspicious_nav_jump", {issue.code for issue in report.issues})

    def test_out_of_order_dates_fail_publication(self) -> None:
        batch = DataBatch(
            provider_id="test",
            batch_id="out-of-order",
            fetched_at=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
            as_of=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
            records=(
                FundNavRecord("000001", date(2026, 8, 15), Decimal("1.01")),
                FundNavRecord("000001", date(2026, 8, 14), Decimal("1.00")),
            ),
            provenance="test fixture",
            source_domain="example.test",
            request_parameters={"fund_code": "000001"},
            raw_content_sha256="a" * 64,
        )

        report = evaluate_nav_batch(batch)

        self.assertEqual(report.status, QualityStatus.FAIL)
        self.assertIn("out_of_order_nav_observation", {issue.code for issue in report.issues})


if __name__ == "__main__":
    unittest.main()
