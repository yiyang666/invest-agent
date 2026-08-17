from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import DataBatch, FundNavRecord
from invest_agent.data.store import FundDataStore
from invest_agent.metrics.market_state import (
    calculate_fund_market_state,
    calculate_market_state_snapshot,
)
from invest_agent.metrics.fund import calculate_fund_metrics, calculate_rolling_correlation
from invest_agent.metrics.portfolio import calculate_portfolio_risk


SHANGHAI = ZoneInfo("Asia/Shanghai")


class MarketStateTests(unittest.TestCase):
    def _database(self, directory: str) -> tuple[Path, tuple[date, ...]]:
        path = Path(directory) / "market-state.sqlite3"
        dates = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(150))
        rows = []
        for index, nav_date in enumerate(dates):
            constructive = Decimal(100 + index)
            stressed = (
                Decimal(100 + index)
                if index < 120
                else Decimal(220 - (index - 119) * 3)
            )
            rows.append(FundNavRecord("000001", nav_date, constructive, constructive))
            rows.append(FundNavRecord("000002", nav_date, stressed, stressed))
        observed_at = datetime(2026, 6, 1, 20, tzinfo=SHANGHAI)
        FundDataStore(path).publish_nav_batch(
            DataBatch(
                provider_id="akshare_eastmoney",
                batch_id="market-state-fixture",
                fetched_at=observed_at,
                as_of=observed_at,
                records=tuple(rows),
                provenance="test fixture",
                source_domain="example.test",
                request_parameters={"fund_codes": "000001,000002"},
                raw_content_sha256="b" * 64,
            )
        )
        return path, dates

    def test_constructive_state_has_explicit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, dates = self._database(directory)
            result = calculate_fund_market_state(
                database, fund_code="000001", as_of=dates[-1]
            )

        self.assertEqual(result["state"], "constructive")
        self.assertIn("nav_above_slow_average", result["evidence"])
        self.assertTrue(result["quality"]["trailing_windows_only"])

    def test_historical_cutoff_excludes_later_nav_observations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, dates = self._database(directory)
            before_decline = calculate_fund_market_state(
                database, fund_code="000002", as_of=dates[120]
            )
            after_decline = calculate_fund_market_state(
                database, fund_code="000002", as_of=dates[-1]
            )

        self.assertEqual(before_decline["effective_nav_date"], dates[120].isoformat())
        self.assertEqual(before_decline["state"], "constructive")
        self.assertEqual(after_decline["state"], "stressed")
        self.assertTrue(before_decline["quality"]["nav_date_cutoff_applied"])

    def test_snapshot_reports_weighted_state_exposure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, dates = self._database(directory)
            result = calculate_market_state_snapshot(
                database,
                fund_codes=("000001", "000002"),
                as_of=dates[-1],
                weights={"000001": Decimal("0.4"), "000002": Decimal("0.5")},
                cash_weight=Decimal("0.1"),
            )

        self.assertAlmostEqual(result["weighted_fund_exposure_pct"]["constructive"], 40.0)
        self.assertAlmostEqual(result["weighted_fund_exposure_pct"]["stressed"], 50.0)
        self.assertTrue(result["classification_is_not_strategy_signal"])

    def test_all_phase_three_calculators_honor_nav_date_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, dates = self._database(directory)
            cutoff = dates[120]
            fund = calculate_fund_metrics(database, fund_code="000002", as_of=cutoff)
            rolling = calculate_rolling_correlation(
                database,
                left_fund_code="000001",
                right_fund_code="000002",
                as_of=cutoff,
                window=20,
            )
            portfolio = calculate_portfolio_risk(
                database,
                weights={"000001": Decimal("0.5"), "000002": Decimal("0.5")},
                as_of=cutoff,
            )

        self.assertEqual(fund["end_date"], cutoff.isoformat())
        self.assertLessEqual(rolling["rolling_points"][-1]["end_date"], cutoff.isoformat())
        self.assertLessEqual(portfolio["return_end_date"], cutoff.isoformat())


if __name__ == "__main__":
    unittest.main()
