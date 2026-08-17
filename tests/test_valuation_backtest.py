from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.backtest.cli import main as backtest_cli_main
from invest_agent.backtest.subscription_engine import (
    CashContribution,
    SubscriptionExecution,
    run_subscription_events,
)
from invest_agent.backtest.valuation_engine import (
    CashDistribution,
    DailyUnitNav,
    ReinvestedDistribution,
    run_portfolio_valuation_events,
)


SPEC_HASH = "a" * 64
NAV_HASH = "b" * 64
DIST_HASH = "c" * 64


def subscription_result() -> dict[str, object]:
    return run_subscription_events(
        end_date=date(2026, 5, 31),
        initial_cash_cny=Decimal("0"),
        contributions=(CashContribution("cash", date(2026, 5, 5), Decimal("1000")),),
        subscriptions=(
            SubscriptionExecution(
                simulation_id="sub",
                fund_code="000001",
                signal_date=date(2026, 5, 5),
                submit_date=date(2026, 5, 5),
                execution_nav_date=date(2026, 5, 5),
                nav_visible_date=date(2026, 5, 6),
                confirmation_date=date(2026, 5, 7),
                gross_amount_cny=Decimal("1000"),
                execution_nav=Decimal("1"),
                nav_field="unit_nav",
                purchase_fee_model="proportional_front_end",
                purchase_fee_rate=Decimal("0"),
                share_precision=2,
                share_rounding_mode="down",
                rule_version="rule-v1",
                rule_effective_date=date(2026, 1, 1),
                nav_batch_sha256=NAV_HASH,
                visibility_status="strict_point_in_time",
            ),
        ),
        strategy_id="dca_baseline",
        strategy_version="1.0.0",
        strategy_spec_sha256=SPEC_HASH,
    )


class ValuationBacktestTests(unittest.TestCase):
    def test_cash_distribution_offsets_ex_dividend_nav_drop(self) -> None:
        result = run_portfolio_valuation_events(
            end_date=date(2026, 5, 31),
            subscription_result=subscription_result(),
            nav_observations=(
                DailyUnitNav(
                    "000001",
                    date(2026, 5, 8),
                    date(2026, 5, 9),
                    Decimal("1"),
                    NAV_HASH,
                    "strict_point_in_time",
                ),
                DailyUnitNav(
                    "000001",
                    date(2026, 5, 19),
                    date(2026, 5, 20),
                    Decimal("0.9"),
                    NAV_HASH,
                    "strict_point_in_time",
                ),
            ),
            cash_distributions=(
                CashDistribution(
                    "dist",
                    "000001",
                    date(2026, 5, 19),
                    date(2026, 5, 20),
                    Decimal("0.1"),
                    2,
                    "half_up",
                    DIST_HASH,
                    "strict_point_in_time",
                ),
            ),
        )

        self.assertEqual(result["daily_valuations"][0]["total_value_cny"], "1000.00")
        self.assertEqual(result["daily_valuations"][1]["invested_value_cny"], "900.00")
        self.assertEqual(result["daily_valuations"][1]["available_cash_cny"], "100.00")
        self.assertEqual(result["daily_valuations"][1]["total_value_cny"], "1000.00")

    def test_missing_exact_nav_is_skipped_without_forward_fill(self) -> None:
        result = run_portfolio_valuation_events(
            end_date=date(2026, 5, 31),
            subscription_result=subscription_result(),
            nav_observations=(
                DailyUnitNav(
                    "000002",
                    date(2026, 5, 20),
                    date(2026, 5, 21),
                    Decimal("1"),
                    NAV_HASH,
                    "strict_point_in_time",
                ),
            ),
        )

        self.assertEqual(result["daily_valuations"], [])
        self.assertEqual(
            result["skipped_valuations"][0]["reason"],
            "missing_exact_visible_unit_nav",
        )
        self.assertTrue(result["quality"]["no_forward_fill"])

    def test_reinvestment_adds_shares_only_on_confirmation(self) -> None:
        result = run_portfolio_valuation_events(
            end_date=date(2026, 5, 31),
            subscription_result=subscription_result(),
            nav_observations=(
                DailyUnitNav(
                    "000001",
                    date(2026, 5, 21),
                    date(2026, 5, 22),
                    Decimal("0.9"),
                    NAV_HASH,
                    "strict_point_in_time",
                ),
            ),
            reinvested_distributions=(
                ReinvestedDistribution(
                    "dist-reinvest",
                    "000001",
                    date(2026, 5, 19),
                    date(2026, 5, 19),
                    date(2026, 5, 20),
                    date(2026, 5, 21),
                    Decimal("0.1"),
                    Decimal("0.9"),
                    2,
                    "half_up",
                    2,
                    "down",
                    DIST_HASH,
                    "strict_point_in_time",
                ),
            ),
        )

        self.assertEqual(result["final_state"]["available_shares"]["000001"], "1111.11")
        self.assertEqual(
            [item["event_type"] for item in result["distribution_ledger"]],
            ["distribution_entitlement_recorded", "distribution_reinvested"],
        )
        self.assertEqual(result["distribution_ledger"][1]["rounding_residual_cny"], "0.001")

    def test_cli_writes_research_only_valuation_artifact(self) -> None:
        request = {
            "end_date": "2026-05-31",
            "subscription_result": subscription_result(),
            "nav_observations": [
                {
                    "fund_code": "000001",
                    "nav_date": "2026-05-08",
                    "visible_date": "2026-05-09",
                    "unit_nav": "1",
                    "nav_batch_sha256": NAV_HASH,
                    "visibility_status": "strict_point_in_time",
                }
            ],
            "cash_distributions": [],
            "reinvested_distributions": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            output_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            status = backtest_cli_main(
                ["valuations", "--input", str(request_path), "--output", str(output_path)]
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(result["engine_version"], "portfolio_valuation_events_v1")
        self.assertFalse(result["external_side_effects"])


if __name__ == "__main__":
    unittest.main()
