from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.drawdown_add import (
    build_drawdown_budget_signal,
    build_drawdown_monthly_allocation,
    summarize_drawdown_tier_coverage,
)


SLEEVES = (
    "domestic_broad_core",
    "us_broad_core",
    "us_growth",
    "domestic_growth",
    "defensive",
    "sh_hk_sz_passive_technology_satellite",
)
WEIGHTS = {
    "domestic_broad_core": Decimal("0.20"),
    "us_broad_core": Decimal("0.20"),
    "us_growth": Decimal("0.10"),
    "domestic_growth": Decimal("0.10"),
    "defensive": Decimal("0.30"),
    "sh_hk_sz_passive_technology_satellite": Decimal("0.10"),
}


def _fund_series(*, terminal_value: str, observations: int = 140):
    end = date(2026, 7, 31)
    start = end - timedelta(days=observations - 1)
    result = {}
    for index, sleeve in enumerate(SLEEVES, start=1):
        points = tuple(
            NavPoint(
                nav_date=start + timedelta(days=offset),
                nav=(
                    Decimal(terminal_value)
                    if offset == observations - 1
                    else Decimal("1")
                ),
                visibility_status="historical_visibility_assumed",
                batch_id=f"batch-{index}",
                content_sha256=f"hash-{index}",
            )
            for offset in range(observations)
        )
        result[sleeve] = (f"{index:06d}", points)
    return result


class DrawdownBudgetAddStrategyTests(unittest.TestCase):
    def test_unobserved_tier_is_not_marked_passed(self) -> None:
        signals = [
            build_drawdown_budget_signal(
                review_date=date(2026, 8, 1),
                fund_series=_fund_series(terminal_value="0.94"),
                weights=WEIGHTS,
            )
        ]

        coverage = summarize_drawdown_tier_coverage(signals)

        self.assertEqual(coverage["tiers"]["5pct"]["trigger_months"], 1)
        self.assertEqual(
            coverage["tiers"]["15pct"]["evidence_status"],
            "historically_unobserved",
        )
        self.assertNotIn(
            "passed", coverage["tiers"]["15pct"]["evidence_status"]
        )

    def test_monthly_allocation_adds_only_to_broad_cores(self) -> None:
        signal = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1),
            fund_series=_fund_series(terminal_value="0.84"),
            weights=WEIGHTS,
        )
        current = {sleeve: Decimal("0") for sleeve in SLEEVES}

        plan = build_drawdown_monthly_allocation(
            planned_date=date(2026, 8, 1),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            current_sleeve_values_cny=current,
            target_weights=WEIGHTS,
            signal=signal,
            strategy_spec_sha256="a" * 64,
        )

        amounts = {item.sleeve: item.allocated_cny for item in plan.allocations}
        self.assertEqual(plan.monthly_contribution_cny, Decimal("5000.00"))
        self.assertEqual(amounts["domestic_broad_core"], Decimal("1600.00"))
        self.assertEqual(amounts["us_broad_core"], Decimal("1600.00"))
        self.assertEqual(amounts["defensive"], Decimal("900.00"))
        self.assertEqual(amounts["us_growth"], Decimal("300.00"))
    def test_drawdown_tiers_are_budget_bounded(self) -> None:
        cases = (
            ("0.94", "500.00", "3500.00", False),
            ("0.89", "1000.00", "4000.00", False),
            ("0.84", "2000.00", "5000.00", False),
            ("0.79", "0.00", "3000.00", True),
        )
        for terminal, additional, total, veto in cases:
            with self.subTest(terminal=terminal):
                result = build_drawdown_budget_signal(
                    review_date=date(2026, 8, 1),
                    fund_series=_fund_series(terminal_value=terminal),
                    weights=WEIGHTS,
                )
                self.assertEqual(result["budget"]["additional_budget_cny"], additional)
                self.assertEqual(result["budget"]["total_contribution_cny"], total)
                self.assertEqual(result["risk_veto"], veto)

    def test_neighbor_thresholds_are_applied_and_audited(self) -> None:
        result = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1),
            fund_series=_fund_series(terminal_value="0.955"),
            weights=WEIGHTS,
            first_threshold=Decimal("-0.04"),
            second_threshold=Decimal("-0.09"),
            third_threshold=Decimal("-0.14"),
        )

        self.assertEqual(result["budget"]["additional_budget_cny"], "500.00")
        self.assertEqual(result["signal_parameters"]["first_threshold"], "-0.04")

    def test_invalid_threshold_order_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly decreasing"):
            build_drawdown_budget_signal(
                review_date=date(2026, 8, 1),
                fund_series=_fund_series(terminal_value="0.90"),
                weights=WEIGHTS,
                first_threshold=Decimal("-0.10"),
                second_threshold=Decimal("-0.05"),
            )

    def test_future_nav_cannot_change_current_signal(self) -> None:
        series = _fund_series(terminal_value="0.94")
        base = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1), fund_series=series, weights=WEIGHTS
        )
        amended = {}
        for sleeve, (code, points) in series.items():
            future = NavPoint(
                nav_date=date(2026, 8, 1),
                nav=Decimal("0.20"),
                visibility_status="strict_point_in_time",
                batch_id="future",
                content_sha256="future",
            )
            amended[sleeve] = (code, (*points, future))

        with_future = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1), fund_series=amended, weights=WEIGHTS
        )

        self.assertEqual(base["signal_id"], with_future["signal_id"])
        self.assertEqual(base["composite"], with_future["composite"])

    def test_missing_latest_date_is_not_forward_filled(self) -> None:
        series = _fund_series(terminal_value="0.94")
        sleeve = "us_growth"
        code, points = series[sleeve]
        series[sleeve] = (code, points[:-1])

        result = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1), fund_series=series, weights=WEIGHTS
        )

        self.assertEqual(result["composite"]["latest_common_nav_date"], "2026-07-30")
        self.assertEqual(result["budget"]["additional_budget_cny"], "0.00")

    def test_insufficient_common_history_returns_base_only(self) -> None:
        result = build_drawdown_budget_signal(
            review_date=date(2026, 8, 1),
            fund_series=_fund_series(terminal_value="0.80", observations=100),
            weights=WEIGHTS,
        )

        self.assertEqual(result["signal_status"], "base_only")
        self.assertEqual(result["reason"], "insufficient_exact_common_date_observations")
        self.assertEqual(result["budget"]["total_contribution_cny"], "3000.00")


if __name__ == "__main__":
    unittest.main()
