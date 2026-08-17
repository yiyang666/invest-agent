from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import NavRequest
from invest_agent.data.providers import LocalCsvNavProvider


class LocalCsvNavProviderTests(unittest.TestCase):
    def test_filters_as_of_and_reports_future_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nav.csv"
            path.write_text(
                "fund_code,nav_date,unit_nav,accumulated_nav\n"
                "000001,2026-08-14,1.2345,2.0000\n"
                "000001,2026-08-16,9.9999,9.9999\n",
                encoding="utf-8",
            )
            request = NavRequest(
                fund_codes=("000001",),
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
                as_of=datetime(2026, 8, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            batch = LocalCsvNavProvider(path).fetch_nav(request)

            self.assertEqual(len(batch.records), 1)
            self.assertEqual(batch.records[0].nav_date, date(2026, 8, 14))
            self.assertEqual(batch.quality_issues[0].code, "future_nav_observation")


if __name__ == "__main__":
    unittest.main()
