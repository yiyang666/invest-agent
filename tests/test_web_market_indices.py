import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.market_data.normalize import normalize_guchacha_result
from invest_agent.market_data.cnindex_indices import normalize_cnindex_index_quotes
from invest_agent.market_data.sse_indices import normalize_sse_index_quotes
from invest_agent.market_data.store import MarketDataStore
from invest_agent.web.read_model import Dashboard


NOW = datetime(2026, 10, 15, 23, 40, tzinfo=ZoneInfo("Asia/Shanghai"))


class MarketIndexTests(unittest.TestCase):
    def test_sse_quotes_publish_and_render_without_remote_reads(self):
        body = {"date": 20261015, "time": 161445, "total": 2, "list": [
            [3767.2271, 3710.6954, 3724.5611, 3674.8167, 3707.4760, -1.59, "000001", "上证指数"],
            [1631.9331, 1576.3994, 1601.0864, 1566.1715, 1586.8782, -2.76, "000680", "科创综指"],
        ]}
        payload = ("quoteCallback(" + json.dumps(body) + ")").encode()
        batch = normalize_sse_index_quotes(batch_id="sse-test", payload=payload,
            fetched_at=NOW, as_of=NOW, raw_content_sha256="a" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = MarketDataStore(root / "data/private/invest_agent.sqlite3")
            self.assertTrue(store.publish(batch).quality_report.can_publish)
            rows = {item["code"]: item for item in Dashboard(root).market_indices()}
            self.assertEqual(set(rows), {"000001", "000680"})
            self.assertEqual(rows["000001"]["observation_date"], "2026-10-15")
            self.assertAlmostEqual(rows["000001"]["change_pct"], -1.59)
        body["list"][1][6] = "000688"
        with self.assertRaisesRegex(ValueError, "identity"):
            normalize_sse_index_quotes(batch_id="invalid", payload=("quoteCallback(" + json.dumps(body) + ")").encode(),
                fetched_at=NOW, as_of=NOW, raw_content_sha256="a" * 64)

    def test_cnindex_daily_quote_publish_and_reject_bad_change(self):
        row = [1791907200000, 2866.763, 2962.7297, 2943.0084, 2863.7106,
               2866.763, -91.5816, -0.0309570427, 0, 0, None]
        body = {"code": 200, "data": {"indexCode": "399006", "indexName": "创业板指",
            "item": ["timestamp", "current", "high", "open", "low", "close", "chg",
                     "percent", "amount", "volume", "avg"], "data": [row]}}
        batch = normalize_cnindex_index_quotes(batch_id="cnindex-test", payload=json.dumps(body).encode(),
            fetched_at=NOW, as_of=NOW, raw_content_sha256="c" * 64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            MarketDataStore(root / "data/private/invest_agent.sqlite3").publish(batch)
            rows = {item["code"]: item for item in Dashboard(root).market_indices()}
            self.assertAlmostEqual(rows["399006"]["value"], 2866.763)
            self.assertEqual(rows["399006"]["observation_date"], "2026-10-14")
        row[7] = -0.11231
        with self.assertRaisesRegex(ValueError, "disagrees"):
            normalize_cnindex_index_quotes(batch_id="invalid", payload=json.dumps(body).encode(),
                fetched_at=NOW, as_of=NOW, raw_content_sha256="c" * 64)

    def test_guchacha_valuation_change_is_not_a_daily_quote(self):
        batch = normalize_guchacha_result(batch_id="gcc-index-test", tool_name="get_index_valuation",
            arguments={}, result={"indexes": [
                {"index_code": "399006", "name": "创业板指", "snapshot_date": "2026-10-15",
                 "level": 3000, "change_pct": 1.25, "pe_ttm": 40},
                {"index_code": "NDX", "name": "纳斯达克100", "snapshot_date": "2026-10-14",
                 "level": 30000, "change_pct": -2.0, "pe_ttm": 30},
            ]}, fetched_at=NOW, as_of=NOW, raw_content_sha256="b" * 64)
        ids = {row.series_id for row in batch.numeric_observations}
        self.assertNotIn("index_quote:guchacha:NDX:close", ids)
        self.assertIn("index_valuation:NDX:pe_ttm", ids)


if __name__ == "__main__":
    unittest.main()
