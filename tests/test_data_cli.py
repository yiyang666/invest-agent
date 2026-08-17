from contextlib import redirect_stdout
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from invest_agent.data.cli import main
from invest_agent.data.contracts import DataBatch, FundNavRecord, RawNavPayload


class DataCliTests(unittest.TestCase):
    def test_ingest_csv_archives_and_publishes_partial_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "nav.csv"
            csv_path.write_text(
                "fund_code,nav_date,unit_nav,accumulated_nav\n"
                "000001,2026-08-14,1.2345,2.0000\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "ingest-csv",
                        "--csv",
                        str(csv_path),
                        "--fund-code",
                        "000001",
                        "--start-date",
                        "2026-08-01",
                        "--end-date",
                        "2026-08-15",
                        "--as-of",
                        "2026-08-15T20:00:00+08:00",
                        "--db",
                        str(root / "private" / "fund.sqlite3"),
                        "--raw-root",
                        str(root / "raw"),
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["quality_status"], "partial")
            self.assertEqual(payload["published_records"], 1)
            self.assertIn(
                "historical_visibility_assumed",
                {issue["code"] for issue in payload["quality_issues"]},
            )
            self.assertEqual(len(list((root / "raw" / "local_csv_nav").glob("*.payload.gz"))), 1)

    def test_remote_payload_is_archived_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_root = root / "raw"
            observed_at = datetime(2026, 8, 15, 20, tzinfo=ZoneInfo("Asia/Shanghai"))

            class FakeProvider:
                provider_id = "akshare_eastmoney"

                def __init__(self, **_kwargs) -> None:
                    pass

                def fetch_raw(self, _request):
                    return RawNavPayload(
                        provider_id=self.provider_id,
                        batch_id="akem-test-batch",
                        fetched_at=observed_at,
                        payload=b'{"raw":"exact"}\n',
                        content_type="application/json",
                        provenance="akshare test fixture",
                        source_domain="fund.eastmoney.com",
                        request_parameters={"fund_codes": "000001"},
                    )

                def normalize(self, raw, request, *, raw_content_sha256=None):
                    archived = raw_root / self.provider_id / f"{raw.batch_id}.payload.gz"
                    if not archived.exists():
                        raise AssertionError("normalization ran before raw archival")
                    return DataBatch(
                        provider_id=self.provider_id,
                        batch_id=raw.batch_id,
                        fetched_at=raw.fetched_at,
                        as_of=request.as_of,
                        records=(
                            FundNavRecord(
                                fund_code="000001",
                                nav_date=date(2026, 8, 15),
                                unit_nav=Decimal("1.23"),
                            ),
                        ),
                        provenance=raw.provenance,
                        source_domain=raw.source_domain,
                        request_parameters=raw.request_parameters,
                        raw_content_sha256=raw_content_sha256,
                    )

            output = StringIO()
            with patch("invest_agent.data.cli.AkshareEastmoneyNavProvider", FakeProvider):
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "collect-akshare-eastmoney",
                            "--fund-code",
                            "000001",
                            "--start-date",
                            "2026-08-01",
                            "--end-date",
                            "2026-08-15",
                            "--as-of",
                            "2026-08-15T20:00:00+08:00",
                            "--db",
                            str(root / "private" / "fund.sqlite3"),
                            "--raw-root",
                            str(raw_root),
                        ]
                    )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["published_records"], 1)
            self.assertTrue(
                (raw_root / "akshare_eastmoney" / "akem-test-batch.payload.gz").exists()
            )


if __name__ == "__main__":
    unittest.main()
