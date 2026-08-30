from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import VisibilityStatus
from invest_agent.market_data.contracts import MarketDataBatch, MarketNumericObservation
from invest_agent.market_data.store import MarketDataStore
from invest_agent.market_regime.engine import build_global_market_state_snapshot


SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def _config(*, calculation: str = "latest_threshold", max_age_days: int = 30):
    feature = {
        "feature_id": "test_feature",
        "region": "global",
        "weight": 1,
        "calculation": calculation,
        "series_ids": ["test:series"],
        "max_age_days": max_age_days,
        "bands": {
            "low_max": 40,
            "high_min": 60,
            "low_state": "restrictive",
            "middle_state": "balanced",
            "high_state": "supportive",
        },
    }
    if calculation == "trailing_percentile_threshold":
        feature.update({"minimum_history_points": 2, "history_observations": 10})
    return {
        "schema_version": 1,
        "model_id": "test_global_market_state@1",
        "minimum_coverage_for_state": 0.6,
        "source_diversity_target": 2,
        "allow_historical_visibility_assumed": True,
        "axes": [
            {
                "axis_id": "test_axis",
                "label": "Test axis",
                "weight": 1,
                "features": [feature],
            }
        ],
    }


class GlobalMarketRegimeTests(unittest.TestCase):
    def test_project_v0_5_connects_hong_kong_and_two_us_labor_features(self) -> None:
        config = json.loads(
            (ROOT / "config/global_market_regime_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["model_id"], "global_market_state_snapshot@0.5.0")
        axes = {axis["axis_id"]: axis for axis in config["axes"]}
        trend = {feature["feature_id"]: feature for feature in axes["trend"]["features"]}
        self.assertEqual(
            trend["hong_kong_equity_trend"]["series_ids"],
            ["fund_proxy:hong_kong:000071:accumulated_nav"],
        )
        macro_ids = {
            feature["feature_id"] for feature in axes["macro_growth"]["features"]
        }
        self.assertIn("united_states_payroll_cycle", macro_ids)
        self.assertIn("united_states_unemployment_cycle", macro_ids)
        for axis in config["axes"]:
            self.assertAlmostEqual(
                sum(float(feature["weight"]) for feature in axis["features"]), 1.0
            )

    def _publish(
        self,
        database: Path,
        *,
        batch_id: str,
        fetched_at: datetime,
        observation_date: date,
        value: str,
        visibility: VisibilityStatus = VisibilityStatus.STRICT_POINT_IN_TIME,
        first_seen_at: datetime | None = None,
        provider_id: str = "provider_one",
        series_id: str = "test:series",
    ) -> None:
        MarketDataStore(database).publish(
            MarketDataBatch(
                provider_id=provider_id,
                batch_id=batch_id,
                tool_name="fixture",
                fetched_at=fetched_at,
                as_of=fetched_at,
                request_arguments={},
                raw_content_sha256="a" * 64,
                schema_sha256="b" * 64,
                numeric_observations=(
                    MarketNumericObservation(
                        series_id=series_id,
                        observation_date=observation_date,
                        value=Decimal(value),
                        unit="index",
                        frequency="daily",
                        label="fixture",
                        first_seen_at=(first_seen_at or fetched_at)
                        if visibility is VisibilityStatus.STRICT_POINT_IN_TIME
                        else None,
                        visibility_status=visibility,
                    ),
                ),
            )
        )

    def test_strict_first_seen_cutoff_blocks_later_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            self._publish(
                database,
                batch_id="visible",
                fetched_at=datetime(2026, 8, 20, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 8, 20),
                value="45",
            )
            self._publish(
                database,
                batch_id="not-yet-seen",
                fetched_at=datetime(2026, 8, 25, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 8, 20),
                value="80",
            )
            result = build_global_market_state_snapshot(
                database,
                config=_config(),
                as_of=datetime(2026, 8, 21, 23, tzinfo=SHANGHAI),
            )

        feature = result["axes"][0]["features"][0]
        self.assertEqual(feature["value"], 45.0)
        self.assertEqual(feature["state"], "balanced")
        self.assertEqual(feature["evidence"]["batch_ids"], ["visible"])

    def test_missing_expected_feature_produces_insufficient_evidence(self) -> None:
        config = _config()
        config["axes"][0]["features"].append(
            {
                "feature_id": "not_connected",
                "region": "global",
                "weight": 4,
                "calculation": "not_connected",
                "missing_reason": "provider_not_connected",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            self._publish(
                database,
                batch_id="one",
                fetched_at=datetime(2026, 8, 20, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 8, 20),
                value="80",
            )
            result = build_global_market_state_snapshot(
                database, config=config, as_of=date(2026, 8, 21)
            )

        self.assertEqual(result["overall_state"], "insufficient_evidence")
        self.assertAlmostEqual(result["confidence"]["coverage_pct"], 20.0)
        self.assertFalse(result["guardrails"]["order_intent_generation_allowed"])
        self.assertEqual(result["missing_capabilities"][0]["feature_id"], "not_connected")

    def test_assumed_history_is_visible_but_never_point_in_time_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            self._publish(
                database,
                batch_id="historical-one",
                fetched_at=datetime(2026, 8, 20, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 8, 18),
                value="30",
                visibility=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
            )
            self._publish(
                database,
                batch_id="historical-two",
                fetched_at=datetime(2026, 8, 20, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 8, 19),
                value="70",
                visibility=VisibilityStatus.HISTORICAL_VISIBILITY_ASSUMED,
            )
            result = build_global_market_state_snapshot(
                database,
                config=_config(calculation="trailing_percentile_threshold"),
                as_of=date(2026, 8, 21),
            )

        self.assertEqual(result["confidence"]["strict_point_in_time_pct"], 0.0)
        self.assertFalse(result["confidence"]["formal_decision_eligible"])
        feature = result["axes"][0]["features"][0]
        self.assertIn("historical_visibility_assumed", feature["evidence"]["visibility_statuses"])

    def test_stale_feature_is_not_silently_scored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            self._publish(
                database,
                batch_id="stale",
                fetched_at=datetime(2026, 7, 1, 20, tzinfo=SHANGHAI),
                observation_date=date(2026, 7, 1),
                value="80",
            )
            result = build_global_market_state_snapshot(
                database,
                config=_config(max_age_days=7),
                as_of=date(2026, 8, 21),
            )

        self.assertEqual(result["axes"][0]["features"][0]["reason"], "stale_observation")
        self.assertIsNone(result["observed_composite_score"])
        self.assertEqual(result["overall_state"], "insufficient_evidence")

    def test_cross_provider_disagreement_is_not_silently_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            observed = datetime(2026, 8, 20, 20, tzinfo=SHANGHAI)
            self._publish(
                database,
                batch_id="provider-one",
                fetched_at=observed,
                observation_date=date(2026, 8, 20),
                value="45",
                provider_id="provider_one",
            )
            self._publish(
                database,
                batch_id="provider-two",
                fetched_at=observed,
                observation_date=date(2026, 8, 20),
                value="80",
                provider_id="provider_two",
            )
            result = build_global_market_state_snapshot(
                database, config=_config(), as_of=date(2026, 8, 21)
            )

        feature = result["axes"][0]["features"][0]
        self.assertEqual(feature["reason"], "conflicting_provider_values")
        self.assertEqual(result["conflicts"][0]["feature_id"], "test_feature")
        self.assertEqual(result["overall_state"], "insufficient_evidence")

    def test_panel_average_drawdown_uses_each_series_without_lookahead(self) -> None:
        config = _config()
        feature = config["axes"][0]["features"][0]
        feature.update(
            {
                "calculation": "panel_average_drawdown_threshold",
                "series_ids": ["panel:a", "panel:b"],
                "window_observations": 3,
                "bands": {
                    "low_max": -15,
                    "high_min": -5,
                    "low_state": "restrictive",
                    "middle_state": "balanced",
                    "high_state": "supportive",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            fetched = datetime(2026, 8, 20, 20, tzinfo=SHANGHAI)
            for series_id, values in (("panel:a", (100, 120, 108)), ("panel:b", (100, 110, 88))):
                for day, value in enumerate(values, start=18):
                    self._publish(
                        database,
                        batch_id=f"{series_id}-{day}",
                        fetched_at=fetched,
                        observation_date=date(2026, 8, day),
                        value=str(value),
                        series_id=series_id,
                    )
            result = build_global_market_state_snapshot(
                database,
                config=config,
                as_of=datetime(2026, 8, 21, 23, tzinfo=SHANGHAI),
            )
        feature_result = result["axes"][0]["features"][0]
        self.assertAlmostEqual(feature_result["value"], -15.0)
        self.assertEqual(feature_result["state"], "restrictive")
        self.assertEqual(
            feature_result["calculation_details"]["component_drawdowns_pct"],
            {"panel:a": -10.0, "panel:b": -20.0},
        )

    def test_cross_section_median_uses_latest_visible_values(self) -> None:
        config = _config()
        feature = config["axes"][0]["features"][0]
        feature.pop("series_ids")
        feature.update(
            {
                "calculation": "cross_section_median_threshold",
                "series_id_prefix": "cross:industry:",
                "series_id_suffix": ":composite",
                "minimum_series": 3,
                "max_alignment_days": 0,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "market.sqlite3"
            fetched = datetime(2026, 8, 20, 20, tzinfo=SHANGHAI)
            for suffix, value in (("a", "20"), ("b", "50"), ("c", "90")):
                self._publish(
                    database,
                    batch_id=f"cross-{suffix}",
                    fetched_at=fetched,
                    observation_date=date(2026, 8, 20),
                    value=value,
                    series_id=f"cross:industry:{suffix}:composite",
                )
            self._publish(
                database,
                batch_id="cross-unrelated",
                fetched_at=fetched,
                observation_date=date(2026, 8, 20),
                value="1",
                series_id="cross:industry:a:turnover",
            )
            result = build_global_market_state_snapshot(
                database,
                config=config,
                as_of=datetime(2026, 8, 21, 23, tzinfo=SHANGHAI),
            )
        feature_result = result["axes"][0]["features"][0]
        self.assertEqual(feature_result["value"], 50.0)
        self.assertEqual(feature_result["state"], "balanced")
        self.assertEqual(feature_result["calculation_details"]["series_count"], 3)


if __name__ == "__main__":
    unittest.main()
