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


ROOT = Path(__file__).resolve().parents[1]
SPEC_HASH = "a" * 64
NAV_HASH = "b" * 64


class SubscriptionBacktestTests(unittest.TestCase):
    def _execution(self, amount: str = "1000") -> SubscriptionExecution:
        return SubscriptionExecution(
            simulation_id="sim_001",
            fund_code="000001",
            signal_date=date(2026, 5, 5),
            submit_date=date(2026, 5, 5),
            execution_nav_date=date(2026, 5, 5),
            nav_visible_date=date(2026, 5, 6),
            confirmation_date=date(2026, 5, 7),
            gross_amount_cny=Decimal(amount),
            execution_nav=Decimal("1.23"),
            nav_field="unit_nav",
            purchase_fee_model="proportional_front_end",
            purchase_fee_rate=Decimal("0.01"),
            share_precision=2,
            share_rounding_mode="down",
            rule_version="fund-rule-v1",
            rule_effective_date=date(2026, 1, 1),
            nav_batch_sha256=NAV_HASH,
            visibility_status="strict_point_in_time",
        )

    def _run(self, *, end_date: date, initial_cash: str = "0") -> dict[str, object]:
        return run_subscription_events(
            end_date=end_date,
            initial_cash_cny=Decimal(initial_cash),
            contributions=(
                CashContribution("cash_001", date(2026, 5, 5), Decimal("1000")),
            ),
            subscriptions=(self._execution(),),
            strategy_id="dca_baseline",
            strategy_version="1.0.0",
            strategy_spec_sha256=SPEC_HASH,
        )

    def test_cash_moves_to_pending_then_confirmed_shares(self) -> None:
        result = self._run(end_date=date(2026, 5, 7))

        self.assertEqual(
            [item["event_type"] for item in result["ledger"]],
            ["cash_contributed", "subscription_submitted", "subscription_confirmed"],
        )
        self.assertEqual(result["final_state"]["available_cash_cny"], "0.00")
        self.assertEqual(result["final_state"]["pending_subscription_cash_cny"], "0.00")
        self.assertEqual(result["final_state"]["available_shares"]["000001"], "804.95")
        self.assertEqual(result["summary"]["cash_reconciliation_difference_cny"], "0.00")
        self.assertEqual(result["bindings"]["nav_batch_sha256"], [NAV_HASH])
        self.assertFalse(result["external_side_effects"])
        self.assertFalse(result["real_order_submission_available"])

    def test_future_nav_is_not_exposed_before_confirmation(self) -> None:
        result = self._run(end_date=date(2026, 5, 6))

        self.assertEqual(result["final_state"]["pending_subscription_cash_cny"], "1000.00")
        self.assertEqual(result["final_state"]["available_shares"], {})
        self.assertEqual(result["bindings"]["nav_batch_sha256"], [])
        self.assertNotIn("execution_nav", json.dumps(result["ledger"], sort_keys=True))

    def test_insufficient_cash_rejects_without_negative_balance(self) -> None:
        result = run_subscription_events(
            end_date=date(2026, 5, 7),
            initial_cash_cny=Decimal("100"),
            contributions=(),
            subscriptions=(self._execution(),),
            strategy_id="dca_baseline",
            strategy_version="1.0.0",
            strategy_spec_sha256=SPEC_HASH,
        )

        self.assertEqual(result["summary"]["rejected_subscriptions"], 1)
        self.assertEqual(result["final_state"]["available_cash_cny"], "100.00")
        self.assertEqual(result["final_state"]["pending_subscription_cash_cny"], "0.00")
        self.assertEqual(result["final_state"]["available_shares"], {})

    def test_same_inputs_produce_identical_ledger(self) -> None:
        first = self._run(end_date=date(2026, 5, 7))
        second = self._run(end_date=date(2026, 5, 7))
        self.assertEqual(first, second)

    def test_rejects_impossible_visibility_order(self) -> None:
        invalid = SubscriptionExecution(
            **{
                **self._execution().__dict__,
                "nav_visible_date": date(2026, 5, 8),
            }
        )
        with self.assertRaisesRegex(ValueError, "date"):
            run_subscription_events(
                end_date=date(2026, 5, 8),
                initial_cash_cny=Decimal("1000"),
                contributions=(),
                subscriptions=(invalid,),
                strategy_id="dca_baseline",
                strategy_version="1.0.0",
                strategy_spec_sha256=SPEC_HASH,
            )

    def test_cli_writes_research_only_artifact(self) -> None:
        request = {
            "end_date": "2026-05-07",
            "initial_cash_cny": "0",
            "contributions": [
                {
                    "contribution_id": "cash_001",
                    "contribution_date": "2026-05-05",
                    "amount_cny": "1000"
                }
            ],
            "subscriptions": [
                {
                    "simulation_id": "sim_001",
                    "fund_code": "000001",
                    "signal_date": "2026-05-05",
                    "submit_date": "2026-05-05",
                    "execution_nav_date": "2026-05-05",
                    "nav_visible_date": "2026-05-06",
                    "confirmation_date": "2026-05-07",
                    "gross_amount_cny": "1000",
                    "execution_nav": "1.23",
                    "nav_field": "unit_nav",
                    "purchase_fee_model": "proportional_front_end",
                    "purchase_fee_rate": "0.01",
                    "share_precision": 2,
                    "share_rounding_mode": "down",
                    "rule_version": "fund-rule-v1",
                    "rule_effective_date": "2026-01-01",
                    "nav_batch_sha256": NAV_HASH,
                    "visibility_status": "strict_point_in_time"
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            output_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            status = backtest_cli_main(
                [
                    "subscriptions",
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
        self.assertEqual(result["mode"], "research_only")
        self.assertEqual(result["quality"]["redemption_events_implemented"], False)


if __name__ == "__main__":
    unittest.main()
