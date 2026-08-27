from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import DataBatch, FundNavRecord, VisibilityStatus
from invest_agent.data.store import FundDataStore
from invest_agent.data.sync import (
    build_incremental_requests,
    load_sync_config,
    resolve_sync_funds,
    sync_plan_payload,
)


ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


class FundDataSyncTests(unittest.TestCase):
    def _config(self, database_path: str, *, snapshots: bool = False) -> dict:
        config = load_sync_config(ROOT / "config/fund_data_sync_v1.json")
        config["database_path"] = database_path
        config["fund_sources"] = dict(config["fund_sources"])
        config["fund_sources"]["latest_portfolio_snapshot"] = {
            "enabled": snapshots,
            "directory": "data/private/portfolio_snapshots",
            "include_positions_even_when_purchase_blocked": True,
        }
        return config

    def test_active_universe_excludes_blocked_reference_routes(self) -> None:
        config = self._config("data/private/test-unused.sqlite3")
        funds = resolve_sync_funds(config, workspace_root=ROOT)
        codes = {item.fund_code for item in funds}

        self.assertIn("000043", codes)
        self.assertIn("539003", codes)
        self.assertIn("096001", codes)
        self.assertIn("006282", codes)
        self.assertTrue(
            {"017436", "017437", "080006", "016452", "016453", "017641", "019305", "519981"}
            <= codes
        )
        self.assertNotIn("968173", codes)
        self.assertNotIn("021000", codes)
        self.assertNotIn("050025", codes)
        self.assertNotIn("539001", codes)

    def test_held_fund_remains_in_universe_when_purchase_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            snapshot_directory = Path(directory) / "snapshots"
            snapshot_directory.mkdir()
            (snapshot_directory / "held.json").write_text(
                json.dumps(
                    {
                        "as_of": "2026-08-25T08:00:00+08:00",
                        "positions": [{"fund_code": "539001"}],
                    }
                ),
                encoding="utf-8",
            )
            config = self._config("data/private/test-unused.sqlite3", snapshots=True)
            config["fund_sources"]["latest_portfolio_snapshot"]["directory"] = str(
                snapshot_directory.relative_to(ROOT)
            )

            funds = {item.fund_code: item.reasons for item in resolve_sync_funds(config, workspace_root=ROOT)}

            self.assertIn("539001", funds)
            self.assertIn("current_portfolio_position", funds["539001"])

    def test_new_fund_uses_fixed_three_year_bootstrap_without_contract_audit(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            database = Path(directory) / "fund.sqlite3"
            config = self._config(str(database.relative_to(ROOT)))
            plan = sync_plan_payload(
                config,
                workspace_root=ROOT,
                as_of=datetime(2026, 8, 25, 23, 15, tzinfo=SHANGHAI),
            )

            self.assertNotIn("contract_audit", plan)
            request = next(
                item for item in plan["requests"] if item["fund_code"] == "006282"
            )
            self.assertEqual(request["history_mode"], "bounded_recent_bootstrap")
            self.assertEqual(request["start_date"], "2023-08-26")

    def test_incremental_plan_overlaps_last_published_nav(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            database = Path(directory) / "fund.sqlite3"
            relative_database = str(database.relative_to(ROOT))
            store = FundDataStore(database)
            observed = datetime(2026, 8, 20, 23, tzinfo=SHANGHAI)
            store.publish_nav_batch(
                DataBatch(
                    provider_id="akshare_eastmoney",
                    batch_id="sync-seed",
                    fetched_at=observed,
                    as_of=observed,
                    records=(
                        FundNavRecord(
                            fund_code="000043",
                            nav_date=date(2026, 8, 20),
                            unit_nav=Decimal("1.25"),
                            accumulated_nav=Decimal("1.25"),
                            source_observed_at=observed,
                            first_seen_at=observed,
                            visibility_status=VisibilityStatus.STRICT_POINT_IN_TIME,
                        ),
                    ),
                    provenance="test",
                    source_domain="example.invalid",
                    request_parameters={"fund_code": "000043"},
                    raw_content_sha256="a" * 64,
                )
            )
            config = self._config(relative_database)
            requests = build_incremental_requests(
                config,
                workspace_root=ROOT,
                as_of=datetime(2026, 8, 24, 23, 15, tzinfo=SHANGHAI),
            )
            request = next(item for item in requests if item.fund_code == "000043")

            self.assertEqual(request.last_nav_before, date(2026, 8, 20))
            self.assertEqual(request.start_date, date(2026, 8, 17))
            self.assertEqual(request.end_date, date(2026, 8, 24))

    def test_existing_fund_uses_overlap_without_contract_boundary_lookup(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            database = Path(directory) / "fund.sqlite3"
            relative_database = str(database.relative_to(ROOT))
            store = FundDataStore(database)
            observed = datetime(2026, 8, 24, 23, tzinfo=SHANGHAI)
            store.publish_nav_batch(
                DataBatch(
                    provider_id="akshare_eastmoney",
                    batch_id="contract-boundary-seed",
                    fetched_at=observed,
                    as_of=observed,
                    records=(
                        FundNavRecord(
                            fund_code="539003",
                            nav_date=date(2019, 12, 31),
                            unit_nav=Decimal("1.00"),
                            accumulated_nav=Decimal("1.00"),
                            source_observed_at=observed,
                        ),
                        FundNavRecord(
                            fund_code="539003",
                            nav_date=date(2020, 1, 10),
                            unit_nav=Decimal("1.01"),
                            accumulated_nav=Decimal("1.01"),
                            source_observed_at=observed,
                        ),
                        FundNavRecord(
                            fund_code="539003",
                            nav_date=date(2026, 8, 21),
                            unit_nav=Decimal("1.50"),
                            accumulated_nav=Decimal("1.50"),
                            source_observed_at=observed,
                        ),
                    ),
                    provenance="test",
                    source_domain="example.invalid",
                    request_parameters={"fund_codes": "539003", "start_date": "2019-12-31"},
                    raw_content_sha256="b" * 64,
                )
            )
            config = self._config(relative_database)
            requests = build_incremental_requests(
                config,
                workspace_root=ROOT,
                as_of=datetime(2026, 8, 24, 23, 15, tzinfo=SHANGHAI),
            )
            request = next(item for item in requests if item.fund_code == "539003")

            self.assertEqual(request.start_date, date(2026, 8, 18))
            self.assertEqual(request.history_mode, "incremental_overlap")


if __name__ == "__main__":
    unittest.main()
