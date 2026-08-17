from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import (
    DataBatch,
    FundDistributionBatch,
    FundDistributionRecord,
    FundMetadataBatch,
    FundMetadataRecord,
    FundNavRecord,
    VisibilityStatus,
)
from invest_agent.data.store import FundDataStore, ImmutableBatchConflict
from invest_agent.domain.portfolio import QualityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")


def valid_batch() -> DataBatch:
    observed_at = datetime(2026, 8, 15, 20, tzinfo=SHANGHAI)
    return DataBatch(
        provider_id="akshare_eastmoney",
        batch_id="batch-001",
        fetched_at=observed_at,
        as_of=observed_at,
        records=(
            FundNavRecord(
                fund_code="000001",
                nav_date=date(2026, 8, 15),
                unit_nav=Decimal("1.2345"),
                accumulated_nav=Decimal("2.3456"),
                source_observed_at=observed_at,
                first_seen_at=observed_at,
                visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
            ),
        ),
        provenance="eastmoney:test",
        source_domain="eastmoney.com",
        request_parameters={"fund_code": "000001"},
        raw_content_sha256="a" * 64,
    )


class FundDataStoreTests(unittest.TestCase):
    def test_publish_is_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory) / "fund.sqlite3")
            batch = valid_batch()

            first = store.publish_nav_batch(batch)
            replay = store.publish_nav_batch(batch)

            self.assertEqual(first.quality_report.status, QualityStatus.PASS)
            self.assertEqual(first.published_records, 1)
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(store.count_nav_observations(batch_id=batch.batch_id), 1)

            changed = replace(
                batch,
                records=(replace(batch.records[0], unit_nav=Decimal("9.9999")),),
            )
            with self.assertRaises(ImmutableBatchConflict):
                store.publish_nav_batch(changed)

    def test_failed_batch_keeps_audit_row_but_publishes_no_nav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory) / "fund.sqlite3")
            batch = replace(
                valid_batch(),
                batch_id="batch-invalid",
                records=(replace(valid_batch().records[0], unit_nav=Decimal("0")),),
            )

            result = store.publish_nav_batch(batch)

            self.assertEqual(result.quality_report.status, QualityStatus.FAIL)
            self.assertEqual(result.published_records, 0)
            self.assertEqual(store.count_nav_observations(batch_id=batch.batch_id), 0)
            self.assertEqual(len(list(store.iter_batches())), 1)

    def test_publishes_metadata_and_preserves_raw_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory) / "fund.sqlite3")
            observed_at = datetime(2026, 8, 15, 20, tzinfo=SHANGHAI)
            batch = FundMetadataBatch(
                provider_id="akshare_ths_metadata",
                batch_id="metadata-001",
                fetched_at=observed_at,
                as_of=observed_at,
                records=(
                    FundMetadataRecord(
                        fund_code="000001",
                        fund_name="Test Fund",
                        raw_fields={"custom": "retained"},
                        source_observed_at=observed_at,
                    ),
                ),
                provenance="ths:test",
                source_domain="fund.10jqka.com.cn",
                request_parameters={"fund_codes": "000001"},
                raw_content_sha256="b" * 64,
            )

            first = store.publish_fund_metadata_batch(batch)
            replay = store.publish_fund_metadata_batch(batch)

            self.assertEqual(first.quality_report.status, QualityStatus.PASS)
            self.assertEqual(first.published_records, 1)
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(store.count_fund_metadata_observations(), 1)
            self.assertEqual(list(store.iter_batches())[0]["batch_kind"], "fund_metadata")

    def test_publishes_distribution_against_immutable_source_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory) / "fund.sqlite3")
            source = valid_batch()
            store.publish_nav_batch(source)
            observed_at = datetime(2026, 8, 15, 20, tzinfo=SHANGHAI)
            batch = FundDistributionBatch(
                provider_id="akshare_eastmoney_distribution",
                batch_id="batch-001-cashdist-v1",
                source_nav_batch_id=source.batch_id,
                fetched_at=observed_at,
                as_of=observed_at,
                records=(
                    FundDistributionRecord(
                        fund_code="000001",
                        ex_date=date(2026, 8, 15),
                        cash_per_share=Decimal("0.015"),
                        source_text="分红：每份派现金0.015元",
                        source_observed_at=observed_at,
                        first_seen_at=observed_at,
                        visibility_status=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
                    ),
                ),
                provenance="eastmoney:test:unitMoney",
                source_domain="fund.eastmoney.com",
                request_parameters={"source_nav_batch_id": source.batch_id},
                raw_content_sha256="a" * 64,
            )

            first = store.publish_distribution_batch(batch)
            replay = store.publish_distribution_batch(batch)

            self.assertEqual(first.quality_report.status, QualityStatus.PARTIAL)
            self.assertEqual(first.published_records, 1)
            self.assertTrue(replay.idempotent_replay)
            self.assertEqual(store.count_distribution_observations(), 1)
            self.assertEqual(
                list(store.iter_distribution_batches())[0]["source_nav_batch_id"],
                source.batch_id,
            )

    def test_cross_source_conflict_is_isolated_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FundDataStore(Path(directory) / "fund.sqlite3")
            left = valid_batch()
            right = replace(
                valid_batch(),
                provider_id="akshare_ths_daily",
                batch_id="batch-002",
                records=(
                    replace(
                        valid_batch().records[0],
                        unit_nav=Decimal("1.9999"),
                    ),
                ),
                provenance="ths:test",
                source_domain="fund.10jqka.com.cn",
                raw_content_sha256="b" * 64,
            )

            store.publish_nav_batch(left)
            store.publish_nav_batch(right)
            report = store.compare_nav_sources(
                left_provider="akshare_eastmoney",
                right_provider="akshare_ths_daily",
            )

            self.assertEqual(report["counts"]["conflict"], 1)
            self.assertEqual(store.count_open_nav_conflicts(), 1)


if __name__ == "__main__":
    unittest.main()
