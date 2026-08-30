from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.satellite_risk_budget import (
    build_satellite_risk_budget_signal,
)


SATELLITES = (
    "global_technology_satellite",
    "semiconductor_satellite",
    "robotics_satellite",
    "biotechnology_satellite",
)


def _points(scale: int, *, count: int = 253) -> tuple[NavPoint, ...]:
    cutoff = date(2025, 12, 31)
    start = cutoff - timedelta(days=count - 1)
    nav = Decimal("1")
    result = []
    for index in range(count):
        if index:
            shock = Decimal(((index % 5) - 2) * scale) / Decimal("1000")
            nav *= Decimal("1") + shock
        result.append(
            NavPoint(
                nav_date=start + timedelta(days=index),
                nav=nav,
                visibility_status="historical_visibility_assumed",
                batch_id=f"batch-{scale}",
                content_sha256=str(scale) * 64,
            )
        )
    return tuple(result)


def _strategic_weights() -> dict[str, Decimal]:
    return {
        "core": Decimal("0.40"),
        **{sleeve: Decimal("0.05") for sleeve in SATELLITES},
        "defensive_bond": Decimal("0.20"),
        "gold_stabilizer": Decimal("0.10"),
        "dividend": Decimal("0.10"),
    }


class SatelliteRiskBudgetTests(unittest.TestCase):
    def test_equal_risk_is_projected_to_the_frozen_group_cap(self) -> None:
        common = _points(2)
        signal = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series={
                sleeve: (f"00000{index}", common)
                for index, sleeve in enumerate(SATELLITES, start=1)
            },
            strategic_weights=_strategic_weights(),
        )
        shares = {
            sleeve: Decimal(value)
            for sleeve, value in signal["satellite_shares"].items()
        }

        self.assertEqual(sum(shares.values(), Decimal("0")), Decimal("1"))
        self.assertEqual(shares["biotechnology_satellite"], Decimal("0.30"))
        self.assertEqual(
            sum(
                shares[sleeve]
                for sleeve in SATELLITES
                if sleeve != "biotechnology_satellite"
            ),
            Decimal("0.70"),
        )
        self.assertEqual(signal["weight_source"], "bounded_inverse_volatility")

    def test_lower_volatility_receives_more_weight_within_bounds(self) -> None:
        scales = {
            "global_technology_satellite": 1,
            "semiconductor_satellite": 2,
            "robotics_satellite": 4,
            "biotechnology_satellite": 3,
        }
        signal = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series={
                sleeve: (f"00000{index}", _points(scales[sleeve]))
                for index, sleeve in enumerate(SATELLITES, start=1)
            },
            strategic_weights=_strategic_weights(),
        )
        shares = {
            sleeve: Decimal(value)
            for sleeve, value in signal["satellite_shares"].items()
        }

        self.assertGreater(
            shares["global_technology_satellite"],
            shares["robotics_satellite"],
        )
        self.assertTrue(all(Decimal("0.15") <= value <= Decimal("0.35") for value in shares.values()))

    def test_missing_history_uses_constraint_neutral_fallback(self) -> None:
        signal = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series={
                sleeve: (
                    f"00000{index}",
                    _points(2, count=100) if sleeve == "robotics_satellite" else _points(2),
                )
                for index, sleeve in enumerate(SATELLITES, start=1)
            },
            strategic_weights=_strategic_weights(),
        )

        self.assertEqual(signal["weight_source"], "constraint_neutral_fallback")
        self.assertEqual(
            Decimal(signal["satellite_shares"]["biotechnology_satellite"]),
            Decimal("0.30"),
        )

    def test_future_points_cannot_change_the_quarterly_weights(self) -> None:
        common = _points(2)
        series = {
            sleeve: (f"00000{index}", common)
            for index, sleeve in enumerate(SATELLITES, start=1)
        }
        before = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series=series,
            strategic_weights=_strategic_weights(),
        )
        future = NavPoint(
            nav_date=date(2026, 1, 2),
            nav=Decimal("100"),
            visibility_status="historical_visibility_assumed",
            batch_id="future",
            content_sha256="f" * 64,
        )
        changed = dict(series)
        changed["robotics_satellite"] = (
            series["robotics_satellite"][0],
            (*common, future),
        )
        after = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series=changed,
            strategic_weights=_strategic_weights(),
        )

        self.assertEqual(before, after)

    def test_weights_are_frozen_inside_each_calendar_quarter(self) -> None:
        common = _points(2)
        series = {
            sleeve: (f"00000{index}", common)
            for index, sleeve in enumerate(SATELLITES, start=1)
        }
        january = build_satellite_risk_budget_signal(
            review_date=date(2026, 1, 1),
            satellite_series=series,
            strategic_weights=_strategic_weights(),
        )
        march = build_satellite_risk_budget_signal(
            review_date=date(2026, 3, 1),
            satellite_series=series,
            strategic_weights=_strategic_weights(),
        )

        self.assertEqual(january["effective_quarter_start"], "2026-01-01")
        self.assertEqual(january["signal_cutoff_date"], "2025-12-31")
        self.assertEqual(january["satellite_shares"], march["satellite_shares"])
        self.assertEqual(
            january["effective_target_weights"], march["effective_target_weights"]
        )


if __name__ == "__main__":
    unittest.main()
