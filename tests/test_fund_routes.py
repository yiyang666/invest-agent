from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.domain.fund_routes import load_purchase_route_pool
from invest_agent.strategies.dca import (
    InstrumentRoute,
    build_monthly_allocation,
    generate_simulated_subscriptions,
)


ROOT = Path(__file__).resolve().parents[1]


class PurchaseRoutePoolTests(unittest.TestCase):
    def test_project_pool_admits_verified_us_broad_share_class_routes(self) -> None:
        pool = load_purchase_route_pool(ROOT / "config/qdii_purchase_route_pool_v1.json")

        self.assertEqual(
            pool.active_fund_codes(),
            (
                "000043",
                "016452",
                "017641",
                "017894",
                "019305",
                "020712",
                "096001",
                "100055",
                "519981",
                "539003",
            ),
        )
        broad_routes = pool.routes_for_sleeve("us_broad_core")
        self.assertEqual(
            tuple(route.fund_code for route in broad_routes),
            ("017641", "019305", "096001", "519981"),
        )
        cifm_routes = broad_routes[:2]
        self.assertEqual(
            tuple(route.daily_cap_cny for route in cifm_routes),
            (Decimal("10"), Decimal("10")),
        )
        self.assertEqual(
            tuple(route.purchase_fee_rate for route in cifm_routes),
            (Decimal("0.0012"), Decimal("0")),
        )
        self.assertEqual(
            {route.shared_daily_cap_group for route in cifm_routes},
            {"cifm_sp500_a_c_combined_daily_purchase"},
        )
        self.assertEqual(
            {route.shared_daily_cap_cny for route in cifm_routes}, {Decimal("10")}
        )
        self.assertEqual(broad_routes[2].purchase_fee_rate, Decimal("0.0015"))
        self.assertEqual(broad_routes[3].minimum_order_cny, Decimal("1"))
        self.assertEqual(broad_routes[3].daily_cap_cny, Decimal("10"))
        self.assertEqual(broad_routes[3].purchase_fee_rate, Decimal("0.0014"))
        route = pool.routes_for_sleeve("us_growth")[0]
        self.assertEqual(route.daily_cap_cny, Decimal("100"))
        uk_route = pool.routes_for_sleeve("uk_broad_core")[0]
        self.assertEqual(uk_route.fund_code, "539003")
        self.assertEqual(uk_route.daily_cap_cny, Decimal("5000"))
        self.assertEqual(uk_route.purchase_fee_rate, Decimal("0.0012"))
        self.assertEqual(
            pool.routes_for_sleeve("us_growth")[0].purchase_fee_rate,
            Decimal("0.0015"),
        )
        nasdaq_route = pool.routes_for_sleeve("us_nasdaq_core")[0]
        self.assertEqual(nasdaq_route.fund_code, "016452")
        self.assertEqual(nasdaq_route.minimum_order_cny, Decimal("1"))
        self.assertEqual(nasdaq_route.daily_cap_cny, Decimal("10"))
        self.assertEqual(nasdaq_route.purchase_fee_rate, Decimal("0.0012"))
        japan_route = pool.routes_for_sleeve("japan_broad_core")[0]
        self.assertEqual(japan_route.fund_code, "020712")
        self.assertEqual(japan_route.minimum_order_cny, Decimal("1"))
        self.assertEqual(japan_route.daily_cap_cny, Decimal("10"))
        self.assertEqual(japan_route.purchase_fee_rate, Decimal("0.0006"))
        technology_route = pool.routes_for_sleeve("global_technology_satellite")[0]
        self.assertEqual(technology_route.fund_code, "100055")
        self.assertEqual(technology_route.minimum_order_cny, Decimal("100"))
        self.assertEqual(technology_route.daily_cap_cny, Decimal("1000"))
        self.assertEqual(technology_route.purchase_fee_rate, Decimal("0.0015"))
        biotechnology_route = pool.routes_for_sleeve("biotechnology_satellite")[0]
        self.assertEqual(biotechnology_route.fund_code, "017894")
        self.assertEqual(biotechnology_route.minimum_order_cny, Decimal("1"))
        self.assertEqual(biotechnology_route.daily_cap_cny, Decimal("100"))
        self.assertEqual(biotechnology_route.purchase_fee_rate, Decimal("0.0012"))
        self.assertTrue(pool.cross_day_queue)
        self.assertTrue(pool.cross_month_queue)

    def test_suspended_route_cannot_enter_purchase_candidates(self) -> None:
        payload = json.loads(
            (ROOT / "config/qdii_purchase_route_pool_v1.json").read_text(encoding="utf-8")
        )
        payload["purchase_candidates"][0]["availability"] = "suspended"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "open or limited"):
                load_purchase_route_pool(path)

    def test_purchase_fee_must_be_explicit_and_bounded(self) -> None:
        payload = json.loads(
            (ROOT / "config/qdii_purchase_route_pool_v1.json").read_text(encoding="utf-8")
        )
        payload["purchase_candidates"][0]["purchase_fee_rate"] = "1.01"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "between zero and one"):
                load_purchase_route_pool(path)

    def test_shared_daily_cap_group_requires_consistent_amount(self) -> None:
        payload = json.loads(
            (ROOT / "config/qdii_purchase_route_pool_v1.json").read_text(encoding="utf-8")
        )
        payload["purchase_candidates"][1]["shared_daily_cap_cny"] = "20"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pool.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "one consistent cap"):
                load_purchase_route_pool(path)

    def test_multiple_limited_routes_combine_capacity_across_days(self) -> None:
        plan = build_monthly_allocation(
            planned_date=date(2026, 8, 3),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal("600"),
            current_sleeve_values_cny={"us_growth": Decimal("0")},
            target_weights={"us_growth": Decimal("1")},
            strategy_spec_sha256="a" * 64,
        )
        dates = (date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5))
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "us_growth", "000043", 1, Decimal("1"), Decimal("100"), dates, "cap-100"
                ),
                InstrumentRoute(
                    "us_growth", "000044", 2, Decimal("1"), Decimal("50"), dates, "cap-50"
                ),
            ),
        )

        self.assertEqual(
            sum((item.gross_amount_cny for item in result.subscriptions), Decimal("0")),
            Decimal("450.00"),
        )
        self.assertEqual(result.unfilled_by_sleeve_cny["us_growth"], Decimal("150.00"))

    def test_us_broad_share_classes_share_cap_and_leave_monthly_shortfall(self) -> None:
        plan = build_monthly_allocation(
            planned_date=date(2026, 8, 3),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal("300"),
            current_sleeve_values_cny={"us_broad_core": Decimal("0")},
            target_weights={"us_broad_core": Decimal("1")},
            strategy_spec_sha256="b" * 64,
        )
        trading_days = tuple(
            date(2026, 8, 3) + timedelta(days=offset)
            for offset in range(26)
            if (date(2026, 8, 3) + timedelta(days=offset)).weekday() < 5
        )
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "us_broad_core",
                    "017641",
                    1,
                    Decimal("10"),
                    Decimal("10"),
                    trading_days,
                    "verified-a-cap-10",
                    shared_daily_cap_group="cifm-sp500",
                    shared_daily_cap_cny=Decimal("10"),
                ),
                InstrumentRoute(
                    "us_broad_core",
                    "019305",
                    2,
                    Decimal("10"),
                    Decimal("10"),
                    trading_days,
                    "verified-c-cap-10",
                    shared_daily_cap_group="cifm-sp500",
                    shared_daily_cap_cny=Decimal("10"),
                ),
            ),
        )

        self.assertEqual(
            sum((item.gross_amount_cny for item in result.subscriptions), Decimal("0")),
            Decimal("200.00"),
        )
        self.assertEqual(
            result.unfilled_by_sleeve_cny["us_broad_core"], Decimal("100.00")
        )
        self.assertEqual({item.fund_code for item in result.subscriptions}, {"017641"})
        self.assertIn("unfilled_month_end:us_broad_core", result.issues)

    def test_equal_weight_route_fills_us_broad_monthly_capacity_gap(self) -> None:
        plan = build_monthly_allocation(
            planned_date=date(2026, 8, 3),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal("300"),
            current_sleeve_values_cny={"us_broad_core": Decimal("0")},
            target_weights={"us_broad_core": Decimal("1")},
            strategy_spec_sha256="c" * 64,
        )
        trading_days = tuple(
            date(2026, 8, 3) + timedelta(days=offset)
            for offset in range(26)
            if (date(2026, 8, 3) + timedelta(days=offset)).weekday() < 5
        )
        shared_fields = {
            "shared_daily_cap_group": "cifm-sp500",
            "shared_daily_cap_cny": Decimal("10"),
        }
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "us_broad_core",
                    "017641",
                    1,
                    Decimal("10"),
                    Decimal("10"),
                    trading_days,
                    "cifm-a",
                    **shared_fields,
                ),
                InstrumentRoute(
                    "us_broad_core",
                    "019305",
                    2,
                    Decimal("10"),
                    Decimal("10"),
                    trading_days,
                    "cifm-c",
                    **shared_fields,
                ),
                InstrumentRoute(
                    "us_broad_core",
                    "096001",
                    3,
                    Decimal("10"),
                    Decimal("10"),
                    trading_days,
                    "dcfund-equal-weight",
                ),
                InstrumentRoute(
                    "us_broad_core",
                    "519981",
                    4,
                    Decimal("1"),
                    Decimal("10"),
                    trading_days,
                    "cxfund-sp100-backup",
                ),
            ),
        )

        totals: dict[str, Decimal] = {}
        for item in result.subscriptions:
            totals[item.fund_code] = (
                totals.get(item.fund_code, Decimal("0")) + item.gross_amount_cny
            )
        self.assertEqual(
            totals,
            {"017641": Decimal("200.00"), "096001": Decimal("100.00")},
        )
        self.assertEqual(
            result.unfilled_by_sleeve_cny["us_broad_core"], Decimal("0.00")
        )

    def test_independent_routes_fill_current_month_before_primary_cross_month_queue(self) -> None:
        plan = build_monthly_allocation(
            planned_date=date(2026, 8, 3),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal("300"),
            current_sleeve_values_cny={"us_broad_core": Decimal("0")},
            target_weights={"us_broad_core": Decimal("1")},
            strategy_spec_sha256="d" * 64,
        )
        august_days = tuple(
            date(2026, 8, 3) + timedelta(days=offset)
            for offset in range(29)
            if (date(2026, 8, 3) + timedelta(days=offset)).weekday() < 5
        )
        september_days = tuple(
            date(2026, 9, 1) + timedelta(days=offset)
            for offset in range(30)
            if (date(2026, 9, 1) + timedelta(days=offset)).weekday() < 5
        )
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "us_broad_core",
                    "017641",
                    1,
                    Decimal("10"),
                    Decimal("10"),
                    august_days + september_days,
                    "primary",
                ),
                InstrumentRoute(
                    "us_broad_core",
                    "096001",
                    2,
                    Decimal("10"),
                    Decimal("10"),
                    august_days + september_days,
                    "independent-supplement",
                ),
            ),
            execution_end_date=date(2026, 9, 30),
            unfilled_issue_prefix="unfilled_scenario_end",
        )

        totals: dict[str, Decimal] = {}
        for item in result.subscriptions:
            totals[item.fund_code] = totals.get(item.fund_code, Decimal("0")) + item.gross_amount_cny
        self.assertEqual(
            totals,
            {"017641": Decimal("210.00"), "096001": Decimal("90.00")},
        )
        self.assertTrue(
            all(item.simulated_submit_date.month == 8 for item in result.subscriptions)
        )


if __name__ == "__main__":
    unittest.main()
