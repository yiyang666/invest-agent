from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.strategies.cli import main as strategy_cli_main
from invest_agent.strategies.dca import (
    InstrumentRoute,
    build_monthly_allocation,
    generate_simulated_subscriptions,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC_HASH = "a" * 64


class DcaStrategyTests(unittest.TestCase):
    def _plan(self, contribution: str = "250"):
        return build_monthly_allocation(
            planned_date=date(2026, 5, 5),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal(contribution),
            current_sleeve_values_cny={"core": Decimal("0")},
            target_weights={"core": Decimal("1")},
            strategy_spec_sha256=SPEC_HASH,
        )

    def test_allocates_only_to_positive_target_gaps(self) -> None:
        plan = build_monthly_allocation(
            planned_date=date(2026, 5, 5),
            pre_contribution_portfolio_value_cny=Decimal("6000"),
            monthly_contribution_cny=Decimal("3000"),
            current_sleeve_values_cny={
                "core": Decimal("0"),
                "defensive": Decimal("550"),
                "satellite": Decimal("5450"),
            },
            target_weights={
                "core": Decimal("0.60"),
                "defensive": Decimal("0.20"),
                "satellite": Decimal("0.20"),
            },
            strategy_spec_sha256=SPEC_HASH,
        )
        allocations = {item.sleeve: item.allocated_cny for item in plan.allocations}

        self.assertEqual(allocations["core"], Decimal("2436.09"))
        self.assertEqual(allocations["defensive"], Decimal("563.90"))
        self.assertEqual(allocations["satellite"], Decimal("0.00"))
        self.assertEqual(plan.unallocated_cash_cny, Decimal("0.01"))

    def test_splits_simulated_subscriptions_by_daily_cap(self) -> None:
        plan = self._plan()
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    sleeve="core",
                    fund_code="000001",
                    priority=1,
                    minimum_order_cny=Decimal("10"),
                    daily_cap_cny=Decimal("100"),
                    eligible_dates=(
                        date(2026, 5, 5),
                        date(2026, 5, 6),
                        date(2026, 5, 7),
                    ),
                    rule_version="rule-v1",
                ),
            ),
        )

        self.assertEqual(
            [item.gross_amount_cny for item in result.subscriptions],
            [Decimal("100.00"), Decimal("100.00"), Decimal("50.00")],
        )
        self.assertEqual(result.contribution_cash_remaining_cny, Decimal("0.00"))
        self.assertTrue(all(item.simulated_submit_date.month == 5 for item in result.subscriptions))
        self.assertTrue(all(item.to_dict()["submitted"] is False for item in result.subscriptions))

    def test_uses_next_route_after_first_route_monthly_capacity(self) -> None:
        plan = self._plan()
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "core",
                    "000001",
                    1,
                    Decimal("10"),
                    Decimal("100"),
                    (date(2026, 5, 5), date(2026, 5, 6)),
                    "rule-v1",
                ),
                InstrumentRoute(
                    "core",
                    "000002",
                    2,
                    Decimal("10"),
                    Decimal("50"),
                    (date(2026, 5, 5),),
                    "rule-v2",
                ),
            ),
        )

        self.assertEqual(
            [(item.fund_code, item.gross_amount_cny) for item in result.subscriptions],
            [
                ("000001", Decimal("100.00")),
                ("000001", Decimal("100.00")),
                ("000002", Decimal("50.00")),
            ],
        )

    def test_carries_limited_route_across_month_end_with_reserved_capacity(self) -> None:
        plan = self._plan()
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "core",
                    "000001",
                    1,
                    Decimal("10"),
                    Decimal("100"),
                    (
                        date(2026, 5, 29),
                        date(2026, 6, 1),
                        date(2026, 6, 2),
                    ),
                    "rule-v2",
                    {date(2026, 6, 1): Decimal("60")},
                ),
            ),
            execution_end_date=date(2026, 6, 2),
            unfilled_issue_prefix="unfilled_scenario_end",
        )

        self.assertEqual(
            [(item.simulated_submit_date, item.gross_amount_cny) for item in result.subscriptions],
            [
                (date(2026, 5, 29), Decimal("100.00")),
                (date(2026, 6, 1), Decimal("40.00")),
                (date(2026, 6, 2), Decimal("100.00")),
            ],
        )
        self.assertEqual(result.contribution_cash_remaining_cny, Decimal("10.00"))
        self.assertIn("unfilled_scenario_end:core", result.issues)

    def test_splits_unlimited_route_into_three_executable_tranches(self) -> None:
        plan = self._plan()
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "core",
                    "000001",
                    1,
                    Decimal("10"),
                    None,
                    (
                        date(2026, 5, 5),
                        date(2026, 5, 15),
                        date(2026, 5, 25),
                    ),
                    "rule-v3",
                    allocation_mode="equal_tranches",
                    planned_tranche_count=3,
                ),
            ),
        )

        self.assertEqual(
            [(item.simulated_submit_date, item.gross_amount_cny) for item in result.subscriptions],
            [
                (date(2026, 5, 5), Decimal("83.33")),
                (date(2026, 5, 15), Decimal("83.33")),
                (date(2026, 5, 25), Decimal("83.34")),
            ],
        )
        self.assertEqual(result.contribution_cash_remaining_cny, Decimal("0.00"))

    def test_missing_terminal_tranche_is_not_front_loaded(self) -> None:
        plan = self._plan()
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "core",
                    "000001",
                    1,
                    Decimal("10"),
                    None,
                    (date(2026, 5, 5),),
                    "rule-v3",
                    allocation_mode="equal_tranches",
                    planned_tranche_count=3,
                ),
            ),
        )

        self.assertEqual(
            [item.gross_amount_cny for item in result.subscriptions],
            [Decimal("83.33")],
        )
        self.assertEqual(result.contribution_cash_remaining_cny, Decimal("166.67"))

    def test_below_minimum_order_remains_cash_at_month_end(self) -> None:
        plan = self._plan("5")
        result = generate_simulated_subscriptions(
            plan,
            routes=(
                InstrumentRoute(
                    "core",
                    "000001",
                    1,
                    Decimal("10"),
                    None,
                    (date(2026, 5, 5),),
                    "rule-v1",
                ),
            ),
        )

        self.assertEqual(result.subscriptions, ())
        self.assertEqual(result.contribution_cash_remaining_cny, Decimal("5.00"))
        self.assertIn("unfilled_month_end:core", result.issues)

    def test_same_inputs_produce_identical_simulation_ids(self) -> None:
        plan = self._plan()
        routes = (
            InstrumentRoute(
                "core",
                "000001",
                1,
                Decimal("10"),
                None,
                (date(2026, 5, 5),),
                "rule-v1",
            ),
        )

        first = generate_simulated_subscriptions(plan, routes=routes).to_dict()
        second = generate_simulated_subscriptions(plan, routes=routes).to_dict()
        self.assertEqual(first, second)
        self.assertFalse(first["external_side_effects"])
        self.assertFalse(first["real_order_submission_available"])

    def test_cli_generates_private_simulation_artifact(self) -> None:
        request = {
            "planned_date": "2026-05-05",
            "pre_contribution_portfolio_value_cny": "0",
            "monthly_contribution_cny": "100",
            "current_sleeve_values_cny": {"core": "0"},
            "target_weights": {"core": "1"},
            "routes": [
                {
                    "sleeve": "core",
                    "fund_code": "000001",
                    "priority": 1,
                    "minimum_order_cny": "10",
                    "daily_cap_cny": "100",
                    "eligible_dates": ["2026-05-05"],
                    "rule_version": "rule-v1"
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            output_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            status = strategy_cli_main(
                [
                    "dca-plan",
                    "--input",
                    str(request_path),
                    "--spec",
                    str(ROOT / "strategies/specs/dca_baseline_v1.json"),
                    "--output",
                    str(output_path),
                ]
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(result["simulated_subscriptions"][0]["action"], "subscribe")
        self.assertFalse(result["simulated_subscriptions"][0]["submitted"])

    def test_rejects_non_reconciling_portfolio_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "reconcile"):
            build_monthly_allocation(
                planned_date=date(2026, 5, 5),
                pre_contribution_portfolio_value_cny=Decimal("100"),
                monthly_contribution_cny=Decimal("100"),
                current_sleeve_values_cny={"core": Decimal("90")},
                target_weights={"core": Decimal("1")},
                strategy_spec_sha256=SPEC_HASH,
            )


if __name__ == "__main__":
    unittest.main()
