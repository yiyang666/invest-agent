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

from invest_agent.market_data.cli import (
    APPROVED_CANONICAL_TOOLS_SCHEMA_SHA256,
    canonical_tools_schema_sha256,
    main,
)
from invest_agent.market_data.mcp_client import RawToolResponse, decode_jsonrpc, extract_tool_result
from invest_agent.market_data.normalize import normalize_guchacha_result
from invest_agent.market_data.policy import load_tool_policy
from invest_agent.market_data.store import MarketDataStore


ROOT = Path(__file__).resolve().parents[1]


class GuchachaMarketDataTests(unittest.TestCase):
    def test_tools_schema_hash_is_canonical_and_order_sensitive(self) -> None:
        message = {
            "result": {
                "tools": [
                    {"name": "one", "inputSchema": {"type": "object"}},
                    {"name": "two", "inputSchema": {"type": "object"}},
                ]
            }
        }
        digest, names = canonical_tools_schema_sha256(message)
        self.assertEqual(names, ("one", "two"))
        self.assertEqual(len(digest), len(APPROVED_CANONICAL_TOOLS_SCHEMA_SHA256))
        reversed_digest, _ = canonical_tools_schema_sha256(
            {"result": {"tools": list(reversed(message["result"]["tools"]))}}
        )
        self.assertNotEqual(digest, reversed_digest)

    def test_policy_rejects_non_allowlisted_tools_and_subdatasets(self) -> None:
        policy = load_tool_policy(ROOT / "config/market_data_sync_v1.json")
        policy.validate("get_market_series", {"dataset": "forex", "limit": 10})
        with self.assertRaises(ValueError):
            policy.validate("get_watchlist", {})
        with self.assertRaises(ValueError):
            policy.validate("get_market_series", {"dataset": "private_credit"})
        with self.assertRaises(ValueError):
            policy.validate("get_market_series", {"dataset": "forex", "key": "bitcoin"})
        with self.assertRaises(ValueError):
            policy.validate("get_macro", {"indicator": "korea_margin"})

    def test_decodes_sse_and_nested_text_payload(self) -> None:
        payload = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"content":'
            '[{"type":"text","text":"{\\"value\\":1}"}]}}\n\n'
        ).encode()
        message = decode_jsonrpc(payload, "text/event-stream")
        self.assertEqual(extract_tool_result(message), {"value": 1})

    def test_probe_archives_before_decoding_and_never_stores_token(self) -> None:
        observed_at = datetime(2026, 8, 28, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": '{"datasets":[]}'}]},
        }

        class FakeClient:
            def __init__(self, **_kwargs) -> None:
                pass

            def call_tool(self, tool, arguments):
                return RawToolResponse(
                    tool_name=tool,
                    arguments=arguments,
                    fetched_at=observed_at,
                    payload=json.dumps(response).encode(),
                    content_type="application/json",
                )

        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            secret = "gcc_fixture_secret_must_not_persist"
            with patch.dict(os.environ, {"GUCHACHA_MCP_TOKEN": secret}):
                with patch("invest_agent.market_data.cli.GuchachaMcpClient", FakeClient):
                    with redirect_stdout(output):
                        code = main(
                            [
                                "probe",
                                "--tool",
                                "list_datasets",
                                "--config",
                                str(ROOT / "config/market_data_sync_v1.json"),
                                "--raw-root",
                                directory,
                                "--as-of",
                                "2026-08-28T12:00:00+08:00",
                            ]
                        )
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            metadata = next(Path(directory).rglob("*.metadata.json")).read_text()
            archived = next(Path(directory).rglob("*.payload.gz")).read_bytes()
            self.assertEqual(result["status"], "archived_discovery_only")
            self.assertNotIn(secret, metadata)
            self.assertNotIn(secret.encode(), archived)

    def test_market_series_history_publishes_with_visibility_warning(self) -> None:
        fetched = datetime(2026, 8, 28, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        batch = normalize_guchacha_result(
            batch_id="gcc-market-series-fixture",
            tool_name="get_market_series",
            arguments={"dataset": "forex", "key": "usdcny", "limit": 2},
            result={
                "dataset": "forex",
                "key": "usdcny",
                "label": "美元/人民币（在岸）",
                "unit": "人民币",
                "latest": {"date": "2026-08-27", "value": 6.72},
                "points": 2,
                "series": [
                    {"date": "2026-08-26", "value": 6.71},
                    {"date": "2026-08-27", "value": 6.72},
                ],
            },
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256="a" * 64,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = MarketDataStore(Path(directory) / "market.sqlite3")
            result = store.publish(batch)
            self.assertEqual(result.quality_report.status.value, "partial")
            self.assertEqual(result.published_numeric_records, 2)
            self.assertIn(
                "historical_visibility_assumed",
                {issue.code for issue in result.quality_report.issues},
            )
            replay = store.publish(batch)
            self.assertTrue(replay.idempotent_replay)

    def test_macro_skips_unreleased_null_and_publishes_known_history(self) -> None:
        fetched = datetime(2026, 8, 28, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        batch = normalize_guchacha_result(
            batch_id="gcc-macro-fixture",
            tool_name="get_macro",
            arguments={"indicator": "nonfarm", "limit": 2},
            result={
                "indicator": "nonfarm",
                "series": [
                    {
                        "series": "nfp_change",
                        "description": "美国非农新增就业，单位 千人",
                        "latest": {
                            "date": "2026-08-01",
                            "publish_date": "2026-09-04",
                            "value": None,
                        },
                        "data": [
                            {
                                "date": "2026-07-01",
                                "time_label": "2026年07月",
                                "publish_date": "2026-08-07",
                                "value": 73,
                            },
                            {
                                "date": "2026-08-01",
                                "time_label": "2026年08月",
                                "publish_date": "2026-09-04",
                                "value": None,
                            },
                        ],
                    }
                ],
            },
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256="b" * 64,
        )
        self.assertEqual(len(batch.numeric_observations), 1)
        self.assertEqual(batch.numeric_observations[0].unit, "千人")
        self.assertIn("null_macro_value_skipped", {issue.code for issue in batch.quality_issues})

    def test_estimated_index_weight_remains_explicit(self) -> None:
        fetched = datetime(2026, 8, 28, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        batch = normalize_guchacha_result(
            batch_id="gcc-weight-fixture",
            tool_name="get_index_weight",
            arguments={"index_code": "000300", "limit": 1},
            result={
                "index_code": "000300",
                "index_name": "沪深300",
                "weight_date": "2026-08-27",
                "is_estimated": True,
                "constituents": [
                    {
                        "stock_code": "600519",
                        "stock_name": "贵州茅台",
                        "industry": "食品饮料",
                        "weight_pct": 3.1,
                        "contribution_pct_points": -0.01,
                    }
                ],
            },
            fetched_at=fetched,
            as_of=fetched,
            raw_content_sha256="c" * 64,
        )
        self.assertTrue(batch.index_weights[0].is_estimated)
        with tempfile.TemporaryDirectory() as directory:
            result = MarketDataStore(Path(directory) / "market.sqlite3").publish(batch)
            self.assertEqual(result.quality_report.status.value, "partial")
            self.assertEqual(result.published_weight_records, 1)


if __name__ == "__main__":
    unittest.main()
