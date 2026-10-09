from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from invest_agent.market_data.jin10 import (
    load_jin10_policy,
    normalize_jin10_calendar,
    normalize_jin10_quote,
)
from invest_agent.market_data.cli import main
from invest_agent.market_data.mcp_client import RawToolResponse
from invest_agent.market_data.store import MarketDataStore


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")


class Jin10MarketDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_jin10_policy(ROOT / "config/market_data_sync_v1.json")
        self.fetched = datetime(2026, 9, 13, 22, 30, tzinfo=TZ)

    def test_policy_excludes_minute_kline_and_broad_news_feeds(self) -> None:
        self.assertEqual(self.policy.enabled_tools, {"get_quote", "list_calendar"})
        self.assertEqual(
            self.policy.interactive_tools,
            {"search_flash", "search_news", "get_news"},
        )
        with self.assertRaises(ValueError):
            self.policy.validate_tool("get_kline")
        with self.assertRaises(ValueError):
            self.policy.quote_spec("DJI")

    def test_quote_normalization_keeps_only_price_and_change(self) -> None:
        batch = normalize_jin10_quote(
            batch_id="jin10-quote-fixture",
            arguments={"code": "SPX"},
            result={
                "status": 200,
                "message": "",
                "data": {
                    "code": "SPX",
                    "name": "标普500指数",
                    "close": "7656.85",
                    "ups_percent": "0.86",
                    "volume": 4647,
                    "time": "2026-09-12T04:23:02+08:00",
                },
            },
            policy=self.policy,
            fetched_at=self.fetched,
            as_of=self.fetched,
            raw_content_sha256="a" * 64,
        )
        self.assertEqual(
            {record.series_id for record in batch.numeric_observations},
            {"jin10_quote:SPX:close", "jin10_quote:SPX:change_pct"},
        )
        self.assertTrue(
            all("volume" not in record.series_id for record in batch.numeric_observations)
        )
        self.assertEqual(
            batch.numeric_observations[0].attributes["volume_excluded_reason"],
            "provider_unit_not_documented",
        )
        with tempfile.TemporaryDirectory() as directory:
            result = MarketDataStore(Path(directory) / "market.sqlite3").publish(batch)
            self.assertEqual(result.quality_report.status.value, "partial")
            self.assertEqual(result.published_numeric_records, 2)

    def test_calendar_publishes_only_reviewed_releases_and_surprises(self) -> None:
        batch = normalize_jin10_calendar(
            batch_id="jin10-calendar-fixture",
            result={
                "status": 200,
                "message": "OK",
                "data": [
                    {
                        "title": "欧元区第二季度GDP季率终值",
                        "pub_time": "2026-09-07 17:00",
                        "actual": "0.6",
                        "consensus": "0.4",
                        "previous": "0.4",
                        "revised": None,
                        "star": 2,
                        "affect_txt": "利多",
                    },
                    {
                        "title": "日本7月领先指标初值",
                        "pub_time": "2026-09-07 13:00",
                        "actual": "117.9",
                        "consensus": "118",
                        "previous": "116.5",
                        "revised": None,
                        "star": 1,
                        "affect_txt": "利空",
                    },
                    {
                        "title": "美国8月NFIB小型企业信心指数",
                        "pub_time": "2026-09-08 18:00",
                        "actual": "98.7",
                        "consensus": None,
                        "previous": "99.8",
                        "revised": None,
                        "star": 3,
                        "affect_txt": "利多",
                    },
                ],
            },
            policy=self.policy,
            fetched_at=self.fetched,
            as_of=self.fetched,
            raw_content_sha256="b" * 64,
        )
        ids = {record.series_id for record in batch.numeric_observations}
        self.assertEqual(
            ids,
            {
                "jin10_macro:euro_area:gdp_qoq_release",
                "jin10_macro:euro_area:gdp_qoq_release:surprise",
                "jin10_macro:japan:leading_index_release",
                "jin10_macro:japan:leading_index_release:surprise",
            },
        )
        euro_surprise = next(
            record
            for record in batch.numeric_observations
            if record.series_id.endswith("gdp_qoq_release:surprise")
        )
        self.assertEqual(str(euro_surprise.value), "0.2")
        self.assertEqual(euro_surprise.attributes["observation_date_basis"], "release_date")

    def test_future_calendar_release_is_not_published(self) -> None:
        batch = normalize_jin10_calendar(
            batch_id="jin10-calendar-future-fixture",
            result={
                "status": 200,
                "message": "OK",
                "data": [
                    {
                        "title": "日本8月核心CPI年率",
                        "pub_time": "2026-09-18 07:30",
                        "actual": "3.0",
                        "consensus": "2.8",
                        "previous": "2.7",
                        "revised": None,
                        "star": 3,
                        "affect_txt": "利空",
                    }
                ],
            },
            policy=self.policy,
            fetched_at=self.fetched,
            as_of=self.fetched,
            raw_content_sha256="c" * 64,
        )
        self.assertEqual(batch.numeric_observations, ())

    def test_quote_cli_archives_before_publish_without_persisting_secret(self) -> None:
        response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "structuredContent": {
                    "status": 200,
                    "message": "",
                    "data": {
                        "code": "SPX",
                        "name": "标普500指数",
                        "close": "7656.85",
                        "ups_percent": "0.86",
                        "time": "2026-09-12T04:23:02+08:00",
                    },
                }
            },
        }

        class FakeClient:
            def __init__(self, **_kwargs) -> None:
                pass

            def call_tool(self, tool, arguments):
                return RawToolResponse(
                    tool_name=tool,
                    arguments=arguments,
                    fetched_at=self_fetched,
                    payload=json.dumps(response, ensure_ascii=False).encode(),
                    content_type="application/json",
                )

        self_fetched = self.fetched
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = json.loads(
                (ROOT / "config/market_data_sync_v1.json").read_text(encoding="utf-8")
            )
            config["jin10_policy"]["quote_codes"] = [
                next(
                    item
                    for item in config["jin10_policy"]["quote_codes"]
                    if item["code"] == "SPX"
                )
            ]
            config_path = root / "market.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            secret = "jin10_fixture_secret_must_not_persist"
            with patch.dict(os.environ, {"JIN10_MCP_TOKEN": secret}):
                with patch("invest_agent.market_data.cli.Jin10McpClient", FakeClient):
                    with redirect_stdout(output):
                        code = main(
                            [
                                "collect-jin10-quotes",
                                "--config",
                                str(config_path),
                                "--raw-root",
                                str(root / "raw"),
                                "--db",
                                str(root / "market.sqlite3"),
                                "--as-of",
                                self.fetched.isoformat(),
                            ]
                        )
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["requested_quotes"], 1)
            files = list((root / "raw").rglob("*"))
            persisted = b"".join(path.read_bytes() for path in files if path.is_file())
            self.assertNotIn(secret.encode(), persisted)


if __name__ == "__main__":
    unittest.main()
