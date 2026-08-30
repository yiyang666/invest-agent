from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.market_data.fred import FredSeriesPolicy, normalize_fred_csv
from invest_agent.market_data.fund_proxy import (
    build_fund_proxy_payload,
    load_fund_proxy_specs,
    normalize_fund_proxy_payload,
)
from invest_agent.market_data.guchacha_breadth import (
    load_guchacha_breadth_policy,
    normalize_guchacha_breadth,
)
from invest_agent.market_data.sse_breadth import (
    SseBreadthPolicy,
    load_sse_breadth_policy,
    normalize_sse_breadth,
)
from invest_agent.market_data.store import MarketDataStore


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReviewedMarketSourceTests(unittest.TestCase):
    @staticmethod
    def _guchacha_dashboard_payload(*, synced_at: str = "2026-08-28T07:31:23.000Z") -> bytes:
        return f"""
        <html><body>
          <div>A股总数</div><div>5554</div><div>东方财富全市场</div>
          <div>上涨家数</div><div>2796</div><div>平盘 142</div>
          <div>下跌家数</div><div>2270</div>
          <div>平均涨跌幅</div><div>+0.21%</div>
          <p>数据最近同步：（5554/5554 成功）</p>
          <script type="application/json">[{{"created_at":1}},"{synced_at}"]</script>
        </body></html>
        """.encode("utf-8")

    @staticmethod
    def _sse_payload(*, market_time: int = 162906) -> bytes:
        rows = []
        for index in range(1500):
            if index < 800:
                change_rate = 1.0
            elif index < 1450:
                change_rate = -1.0
            else:
                change_rate = 0.0
            rows.append(
                [
                    f"{600000 + index:06d}",
                    f"样本{index}",
                    10.0 + change_rate / 10,
                    10.0,
                    change_rate,
                    change_rate / 10,
                    "E110    ",
                ]
            )
        rows.append(["900901", "B股样本", 0.7, 0.69, 1.45, 0.01, "E110    "])
        return json.dumps(
            {
                "date": 20260828,
                "time": market_time,
                "total": len(rows),
                "begin": 0,
                "end": len(rows),
                "list": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def test_fred_latest_is_strict_and_older_history_is_assumed(self) -> None:
        fetched = datetime(2026, 8, 29, 10, tzinfo=SHANGHAI)
        payload = b"observation_date,NFCI\n2026-08-14,-0.55\n2026-08-21,-0.50\n"
        policy = FredSeriesPolicy(
            source_series_id="NFCI",
            local_series_id="financial_conditions:us:NFCI",
            label="NFCI",
            unit="index",
            frequency="weekly",
            source_owner="Federal Reserve Bank of Chicago",
            citation_url="https://fred.stlouisfed.org/series/NFCI",
            usage_scope="personal_research_local_only",
        )
        batch = normalize_fred_csv(
            batch_id="fred-fixture",
            payload=payload,
            policy=policy,
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual(len(batch.numeric_observations), 2)
        self.assertEqual(
            batch.numeric_observations[0].visibility_status.value,
            "historical_visibility_assumed",
        )
        self.assertIsNone(batch.numeric_observations[0].first_seen_at)
        self.assertEqual(
            batch.numeric_observations[1].visibility_status.value,
            "strict_point_in_time",
        )
        self.assertEqual(batch.numeric_observations[1].first_seen_at, fetched)
        self.assertEqual(batch.numeric_observations[1].attributes["usage_scope"], "personal_research_local_only")

    def test_fred_schema_drift_fails_closed(self) -> None:
        fetched = datetime(2026, 8, 29, 10, tzinfo=SHANGHAI)
        policy = FredSeriesPolicy(
            source_series_id="NFCI",
            local_series_id="financial_conditions:us:NFCI",
            label="NFCI",
            unit="index",
            frequency="weekly",
            source_owner="Federal Reserve Bank of Chicago",
            citation_url="https://fred.stlouisfed.org/series/NFCI",
            usage_scope="personal_research_local_only",
        )
        payload = b"DATE,WRONG\n2026-08-21,-0.5\n"
        with self.assertRaisesRegex(ValueError, "Unexpected FRED value column"):
            normalize_fred_csv(
                batch_id="fred-bad",
                payload=payload,
                policy=policy,
                fetched_at=fetched,
                as_of=fetched,
                raw_content_sha256=hashlib.sha256(payload).hexdigest(),
            )

    def test_validated_fund_nav_proxy_is_derived_and_labelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "invest.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE data_batches(
                    batch_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    quality_status TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );
                CREATE TABLE fund_nav_observations(
                    batch_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    fund_code TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    unit_nav TEXT,
                    accumulated_nav TEXT,
                    announcement_at TEXT,
                    first_seen_at TEXT,
                    visibility_status TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO data_batches VALUES (?, ?, ?, ?, ?)",
                ("source-one", "akshare_eastmoney", "a" * 64, "partial", "2026-08-29T09:00:00+08:00"),
            )
            connection.executemany(
                "INSERT INTO fund_nav_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("source-one", "akshare_eastmoney", "110020", "2026-08-27", "2.0", "2.0", None, None, "historical_visibility_assumed"),
                    ("source-one", "akshare_eastmoney", "110020", "2026-08-28", "2.1", "2.1", None, None, "historical_visibility_assumed"),
                ],
            )
            connection.commit()
            connection.close()
            as_of = datetime(2026, 8, 29, 10, tzinfo=SHANGHAI)
            specs = (
                {
                    "region": "china",
                    "fund_code": "110020",
                    "fund_name": "易方达沪深300ETF联接A",
                    "provider_id": "akshare_eastmoney",
                    "nav_field": "accumulated_nav",
                    "local_series_id": "fund_proxy:china:110020:accumulated_nav",
                    "proxy_role": "沪深300",
                },
            )
            payload = build_fund_proxy_payload(
                database, specs=specs, as_of=as_of, history=False
            )
            batch = normalize_fund_proxy_payload(
                batch_id="fund-proxy-fixture",
                payload=payload,
                fetched_at=as_of,
                as_of=as_of,
                raw_content_sha256=hashlib.sha256(payload).hexdigest(),
            )
            result = MarketDataStore(database).publish(batch)

        self.assertEqual(result.published_numeric_records, 1)
        self.assertEqual(result.quality_report.status.value, "partial")
        observation = batch.numeric_observations[0]
        self.assertEqual(observation.observation_date.isoformat(), "2026-08-28")
        self.assertEqual(observation.attributes["source_batch_id"], "source-one")
        self.assertIn("fund_tracking_error", observation.attributes["proxy_limitations"])

    def test_project_proxy_policy_uses_five_explicit_regions(self) -> None:
        specs = load_fund_proxy_specs(ROOT / "config/market_data_sync_v1.json")
        self.assertEqual(
            {spec["region"] for spec in specs},
            {"china", "hong_kong", "united_states", "europe", "japan"},
        )

    def test_sse_breadth_is_strict_partial_and_excludes_b_shares(self) -> None:
        fetched = datetime(2026, 8, 29, 10, tzinfo=SHANGHAI)
        payload = self._sse_payload()
        policy = load_sse_breadth_policy(ROOT / "config/market_data_sync_v1.json")
        batch = normalize_sse_breadth(
            batch_id="sse-breadth-fixture",
            payload=payload,
            policy=policy,
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256=hashlib.sha256(payload).hexdigest(),
        )
        observations = {item.series_id: item for item in batch.numeric_observations}
        self.assertEqual(len(observations), 9)
        self.assertEqual(observations["breadth:china:sse:advancers"].value, 800)
        self.assertEqual(observations["breadth:china:sse:decliners"].value, 650)
        self.assertEqual(observations["breadth:china:sse:unchanged"].value, 50)
        self.assertEqual(observations["breadth:china:sse:valid_count"].value, 1500)
        self.assertEqual(
            observations["breadth:china:sse:advance_share_pct"].visibility_status.value,
            "strict_point_in_time",
        )
        self.assertEqual(
            observations["breadth:china:sse:advance_share_pct"].attributes[
                "excluded_b_share_count"
            ],
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = MarketDataStore(Path(directory) / "market.sqlite3").publish(batch)
        self.assertEqual(result.quality_report.status.value, "partial")
        self.assertEqual(result.published_numeric_records, 9)

    def test_sse_breadth_rejects_intraday_snapshot(self) -> None:
        fetched = datetime(2026, 8, 28, 14, 59, tzinfo=SHANGHAI)
        payload = self._sse_payload(market_time=145900)
        policy = SseBreadthPolicy(
            endpoint="https://example.invalid/equity",
            source_page="https://example.invalid/page",
            source_owner="SSE",
            usage_scope="personal_research_local_only",
            universe="equity_excluding_b_shares",
            max_records=4000,
            minimum_final_time=datetime.strptime("15:05:00", "%H:%M:%S").time(),
        )
        with self.assertRaisesRegex(ValueError, "not final"):
            normalize_sse_breadth(
                batch_id="sse-intraday",
                payload=payload,
                policy=policy,
                fetched_at=fetched,
                as_of=fetched,
                raw_content_sha256=hashlib.sha256(payload).hexdigest(),
            )

    def test_guchacha_dashboard_breadth_uses_classified_denominator(self) -> None:
        fetched = datetime(2026, 8, 30, 13, tzinfo=SHANGHAI)
        payload = self._guchacha_dashboard_payload()
        policy = load_guchacha_breadth_policy(ROOT / "config/market_data_sync_v1.json")
        batch = normalize_guchacha_breadth(
            batch_id="gcc-breadth-fixture",
            payload=payload,
            policy=policy,
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256=hashlib.sha256(payload).hexdigest(),
        )
        observations = {item.series_id: item for item in batch.numeric_observations}
        self.assertEqual(len(observations), 11)
        self.assertEqual(
            observations["breadth:china:all_a:guchacha:classified_count"].value,
            5208,
        )
        self.assertEqual(
            observations["breadth:china:all_a:guchacha:unclassified_count"].value,
            346,
        )
        self.assertAlmostEqual(
            float(observations["breadth:china:all_a:guchacha:advance_share_pct"].value),
            2796 / 5208 * 100,
        )
        self.assertEqual(
            observations["breadth:china:all_a:guchacha:advance_share_pct"].observation_date.isoformat(),
            "2026-08-28",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = MarketDataStore(Path(directory) / "market.sqlite3").publish(batch)
        self.assertEqual(result.quality_report.status.value, "partial")
        self.assertEqual(result.published_numeric_records, 11)

    def test_guchacha_dashboard_breadth_rejects_incomplete_sync(self) -> None:
        fetched = datetime(2026, 8, 30, 13, tzinfo=SHANGHAI)
        payload = self._guchacha_dashboard_payload().replace(b"5554/5554", b"5554/5500")
        policy = load_guchacha_breadth_policy(ROOT / "config/market_data_sync_v1.json")
        with self.assertRaisesRegex(ValueError, "sync is incomplete"):
            normalize_guchacha_breadth(
                batch_id="gcc-breadth-incomplete",
                payload=payload,
                policy=policy,
                fetched_at=fetched,
                as_of=fetched,
                raw_content_sha256=hashlib.sha256(payload).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
