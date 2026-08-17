from decimal import Decimal
from datetime import date, datetime
import unittest

from invest_agent.decision.monthly import (
    _current_sleeves,
    _require_target_authority,
    _signal_envelope,
    _validate_market_context,
    _validate_snapshot_context,
)


class MonthlyDecisionPackTests(unittest.TestCase):
    def test_current_position_mapping_reconciles_cash_legacy_and_target_sleeves(self) -> None:
        snapshot = {
            "cash": "100.00",
            "total_assets": "400.00",
            "positions": [
                {"fund_code": "000043", "market_value": "120.00"},
                {"fund_code": "014408", "market_value": "180.00"},
            ],
        }

        result = _current_sleeves(
            snapshot,
            {"000043": "us_growth", "014408": "legacy_active_other"},
        )

        self.assertEqual(result["us_growth"], Decimal("120.00"))
        self.assertEqual(result["legacy_active_other"], Decimal("180.00"))
        self.assertEqual(result["unassigned_cash"], Decimal("100.00"))
        self.assertEqual(sum(result.values(), Decimal("0")), Decimal("400.00"))

    def test_unreviewed_position_role_is_rejected(self) -> None:
        snapshot = {
            "cash": "0",
            "total_assets": "1",
            "positions": [{"fund_code": "999999", "market_value": "1"}],
        }

        with self.assertRaisesRegex(ValueError, "no reviewed role mapping"):
            _current_sleeves(snapshot, {})

    def test_signal_envelope_is_research_evidence_not_execution(self) -> None:
        result = _signal_envelope(
            strategy_id="dca_baseline",
            version="1.4.0",
            review_date=date(2026, 8, 1),
            valid_until="2026-08-31T23:59:59+08:00",
            payload={"real_order_submission_available": False},
            veto=False,
            risk_reasons=[],
        )

        self.assertEqual(result["data_cutoff"][:10], "2026-07-31")
        self.assertEqual(result["data_quality"]["status"], "historical_visibility_assumed")
        self.assertFalse(result["payload"]["real_order_submission_available"])

    def test_stale_and_future_snapshots_are_blocked(self) -> None:
        as_of = datetime.fromisoformat("2026-08-16T23:59:59+08:00")
        with self.assertRaisesRegex(ValueError, "snapshot is stale"):
            _validate_snapshot_context(
                {"quality_status": "pass", "as_of": "2026-08-10T12:00:00+08:00"},
                as_of=as_of,
                maximum_age_calendar_days=3,
            )
        with self.assertRaisesRegex(ValueError, "snapshot cannot be newer"):
            _validate_snapshot_context(
                {"quality_status": "pass", "as_of": "2026-08-17T12:00:00+08:00"},
                as_of=as_of,
                maximum_age_calendar_days=3,
            )

    def test_stale_and_future_market_data_are_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "market data is stale"):
            _validate_market_context(
                date(2026, 8, 10),
                decision_date=date(2026, 8, 16),
                maximum_age_calendar_days=3,
            )
        with self.assertRaisesRegex(ValueError, "market data as_of cannot be in the future"):
            _validate_market_context(
                date(2026, 8, 17),
                decision_date=date(2026, 8, 16),
                maximum_age_calendar_days=3,
            )

    def test_missing_or_competing_target_authority_stops_pack(self) -> None:
        baseline = {
            "strategy_id": "dca_baseline",
            "strategy_version": "1.4.0",
            "target_allocation_authority": True,
        }
        _require_target_authority({"accepted_signals": [baseline]})
        with self.assertRaisesRegex(ValueError, "target authority is required"):
            _require_target_authority({"accepted_signals": []})
        with self.assertRaisesRegex(ValueError, "target authority is required"):
            _require_target_authority({"accepted_signals": [baseline, dict(baseline)]})


if __name__ == "__main__":
    unittest.main()
