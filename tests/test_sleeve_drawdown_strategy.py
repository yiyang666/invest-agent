from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.dca import build_monthly_allocation
from invest_agent.strategies.sleeve_drawdown import (
    build_sleeve_drawdown_allocation,
    build_sleeve_drawdown_signal,
)


WEIGHTS = {
    "domestic_broad_core": Decimal("0.20"), "us_broad_core": Decimal("0.20"),
    "us_growth": Decimal("0.10"), "domestic_growth": Decimal("0.10"),
    "defensive": Decimal("0.30"), "sh_hk_sz_passive_technology_satellite": Decimal("0.10"),
}


def _series(terminal: str):
    start = date(2026, 3, 14)
    points = tuple(
        NavPoint(start + timedelta(days=i), Decimal(terminal) if i == 139 else Decimal("1"), "historical_visibility_assumed", "batch", "hash")
        for i in range(140)
    )
    return points


class SleeveDrawdownStrategyTests(unittest.TestCase):
    def test_portfolio_twenty_pct_drawdown_vetoes_sleeve_addition(self) -> None:
        portfolio = {sleeve: (f"{index:06d}", _series("0.79")) for index, sleeve in enumerate(WEIGHTS, 1)}
        candidates = {sleeve: portfolio[sleeve] for sleeve in ("us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite")}

        signal = build_sleeve_drawdown_signal(
            review_date=date(2026, 8, 1), candidate_series=candidates,
            portfolio_series=portfolio, portfolio_weights=WEIGHTS,
        )

        self.assertTrue(signal["portfolio_risk_veto"])
        self.assertEqual(signal["budget"]["additional_budget_cny"], "0.00")

    def test_theme_cap_redirects_excess_addition_to_broad_cores(self) -> None:
        current = {sleeve: weight * Decimal("100000") for sleeve, weight in WEIGHTS.items()}
        signal = {"budget": {"additional_budget_cny": "2000.00"}, "triggered_sleeves": ["domestic_growth"]}
        base = build_monthly_allocation(
            planned_date=date(2026, 8, 1), pre_contribution_portfolio_value_cny=Decimal("100000"),
            monthly_contribution_cny=Decimal("3000"), current_sleeve_values_cny=current,
            target_weights=WEIGHTS, strategy_spec_sha256="a" * 64,
        )
        plan = build_sleeve_drawdown_allocation(
            planned_date=date(2026, 8, 1), pre_contribution_portfolio_value_cny=Decimal("100000"),
            current_sleeve_values_cny=current, target_weights=WEIGHTS, signal=signal,
            strategy_spec_sha256="a" * 64, variant="sleeve_with_broad_fallback",
        )
        base_amounts = {item.sleeve: item.allocated_cny for item in base.allocations}
        amounts = {item.sleeve: item.allocated_cny for item in plan.allocations}
        extra_theme = sum(amounts[s] - base_amounts[s] for s in ("us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite"))
        extra_broad = sum(amounts[s] - base_amounts[s] for s in ("domestic_broad_core", "us_broad_core"))

        self.assertEqual(extra_theme, Decimal("600.00"))
        self.assertEqual(extra_broad, Decimal("1400.00"))
        self.assertEqual(plan.monthly_contribution_cny, Decimal("5000"))


if __name__ == "__main__":
    unittest.main()
