from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.trend_rs import (
    build_direct_new_money_allocation,
    build_new_money_trend_signal,
)


def _series(code: str, *, daily_growth: str, days: int = 220, end: date = date(2026, 7, 31)):
    growth = Decimal(daily_growth)
    start = end - timedelta(days=days - 1)
    value = Decimal("1")
    points = []
    for index in range(days):
        points.append(
            NavPoint(
                nav_date=start + timedelta(days=index),
                nav=value,
                visibility_status="historical_visibility_assumed",
                batch_id=f"batch-{code}",
                content_sha256=f"hash-{code}",
            )
        )
        value *= Decimal("1") + growth
    return tuple(points)


class TrendRelativeStrengthStrategyTests(unittest.TestCase):
    def test_direct_allocation_uses_new_money_weights_without_rebalancing(self) -> None:
        plan = build_direct_new_money_allocation(
            planned_date=date(2026, 8, 1),
            pre_contribution_portfolio_value_cny=Decimal("1000.01"),
            monthly_contribution_cny=Decimal("100.01"),
            current_sleeve_values_cny={
                "defensive": Decimal("100.01"),
                "growth": Decimal("900.00"),
            },
            new_money_weights={
                "defensive": Decimal("0.85"),
                "growth": Decimal("0.15"),
            },
            strategy_spec_sha256="a" * 64,
        )

        allocations = {item.sleeve: item for item in plan.allocations}
        self.assertEqual(allocations["defensive"].allocated_cny, Decimal("85.01"))
        self.assertEqual(allocations["growth"].allocated_cny, Decimal("15.00"))
        self.assertEqual(
            allocations["growth"].post_contribution_target_cny, Decimal("915.00")
        )
        self.assertEqual(plan.unallocated_cash_cny, Decimal("0.00"))

    def _candidates(self):
        return {
            "us_growth": ("539001", _series("539001", daily_growth="0.002")),
            "domestic_growth": ("019861", _series("019861", daily_growth="0.001")),
            "sh_hk_sz_passive_technology_satellite": (
                "160646",
                _series("160646", daily_growth="-0.001"),
            ),
        }

    def test_selects_top_two_eligible_candidates_and_caps_each_at_15pct(self) -> None:
        result = build_new_money_trend_signal(
            review_date=date(2026, 8, 1),
            candidate_series=self._candidates(),
        )

        self.assertEqual(result["selected_fund_codes"], ["539001", "019861"])
        self.assertEqual(result["new_money_weights"]["us_growth"], "0.15")
        self.assertEqual(result["new_money_weights"]["domestic_growth"], "0.15")
        self.assertEqual(result["new_money_weights"]["defensive"], "0.30")
        self.assertFalse(result["selling_allowed"])

    def test_unfilled_slots_return_to_defensive(self) -> None:
        candidates = self._candidates()
        candidates["domestic_growth"] = (
            "019861",
            _series("019861", daily_growth="-0.001"),
        )

        result = build_new_money_trend_signal(
            review_date=date(2026, 8, 1),
            candidate_series=candidates,
        )

        self.assertEqual(result["selected_fund_codes"], ["539001"])
        self.assertEqual(result["new_money_weights"]["defensive"], "0.45")

    def test_future_point_cannot_change_signal(self) -> None:
        candidates = self._candidates()
        base = build_new_money_trend_signal(
            review_date=date(2026, 8, 1), candidate_series=candidates
        )
        code, points = candidates["sh_hk_sz_passive_technology_satellite"]
        future = NavPoint(
            nav_date=date(2026, 8, 2),
            nav=Decimal("999"),
            visibility_status="strict_point_in_time",
            batch_id="future",
            content_sha256="future",
        )
        candidates["sh_hk_sz_passive_technology_satellite"] = (code, (*points, future))

        with_future = build_new_money_trend_signal(
            review_date=date(2026, 8, 1), candidate_series=candidates
        )

        self.assertEqual(base["selected_fund_codes"], with_future["selected_fund_codes"])
        self.assertEqual(base["new_money_weights"], with_future["new_money_weights"])

    def test_equal_scores_use_fund_code_tie_break(self) -> None:
        same = _series("same", daily_growth="0.001")
        candidates = {
            "third": ("300000", same),
            "first": ("100000", same),
            "second": ("200000", same),
        }

        result = build_new_money_trend_signal(
            review_date=date(2026, 8, 1), candidate_series=candidates
        )

        self.assertEqual(result["selected_fund_codes"], ["100000", "200000"])

    def test_signal_parameters_are_explicit_and_change_the_window(self) -> None:
        candidates = self._candidates()
        result = build_new_money_trend_signal(
            review_date=date(2026, 8, 1),
            candidate_series=candidates,
            medium_return_days=84,
            slow_return_days=168,
            slow_average_observations=120,
            minimum_observations=121,
        )

        self.assertEqual(result["signal_parameters"]["medium_return_days"], 84)
        evaluation = result["candidate_evaluations"][0]
        self.assertEqual(
            evaluation["features"]["medium_start_nav_date"], "2026-05-08"
        )


if __name__ == "__main__":
    unittest.main()
