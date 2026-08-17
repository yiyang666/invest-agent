from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import DataBatch, FundNavRecord
from invest_agent.data.store import FundDataStore
from invest_agent.metrics.fund import (
    calculate_fund_metrics,
    calculate_return_correlation,
    calculate_rolling_correlation,
)
from invest_agent.metrics.portfolio import calculate_portfolio_risk


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FundMetricTests(unittest.TestCase):
    def _database(self, directory: str) -> Path:
        path = Path(directory) / "fund.sqlite3"
        observed_at = datetime(2026, 1, 5, 20, tzinfo=SHANGHAI)
        rows = []
        values = {
            "000001": ("100", "110", "99", "118.8"),
            "000002": ("100", "120", "96", "134.4"),
        }
        dates = (
            date(2026, 1, 2),
            date(2026, 1, 3),
            date(2026, 1, 4),
            date(2026, 1, 5),
        )
        for code, navs in values.items():
            rows.extend(
                FundNavRecord(code, nav_date, Decimal(nav), Decimal(nav))
                for nav_date, nav in zip(dates, navs)
            )
        batch = DataBatch(
            provider_id="akshare_eastmoney",
            batch_id="metric-fixture",
            fetched_at=observed_at,
            as_of=observed_at,
            records=tuple(rows),
            provenance="test fixture",
            source_domain="example.test",
            request_parameters={"fund_codes": "000001,000002"},
            raw_content_sha256="a" * 64,
        )
        FundDataStore(path).publish_nav_batch(batch)
        return path

    def test_calculates_return_and_drawdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = calculate_fund_metrics(
                self._database(directory), fund_code="000001"
            )

        self.assertAlmostEqual(result["total_return_pct"], 18.8)
        self.assertAlmostEqual(result["max_drawdown_pct"], -10.0)
        self.assertEqual(result["max_drawdown_peak_date"], "2026-01-03")
        self.assertEqual(result["max_drawdown_trough_date"], "2026-01-04")
        self.assertEqual(result["max_drawdown_recovery_date"], "2026-01-05")
        self.assertTrue(result["quality"]["research_only"])

    def test_calculates_pairwise_return_correlation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = calculate_return_correlation(
                self._database(directory),
                fund_codes=("000001", "000002"),
                minimum_overlap=3,
            )

        self.assertAlmostEqual(result["matrix"]["000001"]["000002"], 1.0)
        self.assertEqual(result["overlap_observations"]["000001:000002"], 3)

    def test_calculates_rolling_return_correlation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = calculate_rolling_correlation(
                self._database(directory),
                left_fund_code="000001",
                right_fund_code="000002",
                window=3,
            )

        self.assertEqual(result["common_return_observations"], 3)
        self.assertAlmostEqual(result["summary"]["latest"], 1.0)
        self.assertEqual(len(result["rolling_points"]), 1)
        self.assertTrue(result["quality"]["no_forward_fill"])

    def test_calculates_constant_current_weight_portfolio_risk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = calculate_portfolio_risk(
                self._database(directory),
                weights={"000001": Decimal("0.5"), "000002": Decimal("0.5")},
                minimum_overlap=3,
            )

        self.assertAlmostEqual(result["total_return_pct"], 27.075)
        self.assertAlmostEqual(result["max_drawdown_pct"], -15.0)
        self.assertEqual(result["common_return_observations"], 3)
        self.assertEqual(len(result["concentration"]["single_fund_limit_breaches"]), 2)
        self.assertTrue(result["quality"]["hypothetical_not_actual_portfolio_history"])

    def test_rejects_portfolio_weights_that_do_not_sum_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = self._database(directory)
            with self.assertRaisesRegex(ValueError, "sum to 1"):
                calculate_portfolio_risk(
                    database,
                    weights={"000001": Decimal("0.5")},
                )


if __name__ == "__main__":
    unittest.main()
