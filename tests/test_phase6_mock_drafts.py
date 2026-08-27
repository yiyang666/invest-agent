from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest

from invest_agent.approval.drafts import (
    build_mock_purchase_drafts,
    render_mock_approval_summary,
)
from invest_agent.domain.fund_routes import load_purchase_route_pool


ROOT = Path(__file__).resolve().parents[1]


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _eligible_pack() -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "research_only",
        "execution": {
            "orders": [],
            "real_trading_enabled": False,
            "order_intent_generation_allowed": False,
        },
        "current_portfolio": {
            "risk_posture": "within_current_research_limits",
            "target_gap_preview": {
                "preview_only": False,
                "scheduled_purchase_dates": ["2026-09-01"],
            },
        },
        "research_conclusion": {"monthly_action_status": "mock_schedule_frozen"},
        "strategy_decision_input": {
            "accepted_signals": [
                {
                    "strategy_id": "dca_baseline",
                    "strategy_version": "1.5.0",
                    "target_allocation_authority": True,
                }
            ]
        },
    }


def _simulation_plan(*, fund_code: str = "539003") -> dict[str, object]:
    return {
        "allocation_plan": {
            "strategy_id": "dca_baseline",
            "strategy_version": "1.5.0",
        },
        "simulated_subscriptions": [
            {
                "simulation_id": "sim_20260901_539003_001",
                "action": "subscribe",
                "sleeve": "uk_broad_core",
                "fund_code": fund_code,
                "simulated_submit_date": "2026-09-01",
                "gross_amount_cny": "100.00",
                "rule_version": "current_channel_2026_08_25_v1",
                "submitted": False,
                "account": None,
                "payment_method": None,
            }
        ],
        "issues": [],
        "external_side_effects": False,
        "real_order_submission_available": False,
    }


class Phase6MockDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.routes = load_purchase_route_pool(
            ROOT / "config/qdii_purchase_route_pool_v1.json"
        )

    def test_frozen_risk_valid_plan_becomes_mock_draft(self) -> None:
        pack = _eligible_pack()
        bundle = build_mock_purchase_drafts(
            decision_pack=pack,
            source_pack_sha256=_sha256(pack),
            simulated_subscription_plan=_simulation_plan(),
            purchase_route_pool=self.routes,
            account_alias="fund_account_primary",
        )

        self.assertEqual(len(bundle.drafts), 1)
        intent = bundle.drafts[0].intent
        self.assertEqual(intent.fund_code, "539003")
        self.assertEqual(intent.fund_name, "建信富时100指数(QDII)A人民币")
        self.assertEqual(intent.amount_cny, Decimal("100.00"))
        self.assertEqual(intent.estimated_fee_cny, Decimal("0.12"))
        self.assertFalse(bundle.to_dict()["real_trading_enabled"])
        summary = render_mock_approval_summary(bundle.drafts[0])
        self.assertIn("仅mock", summary)
        self.assertIn("真实交易：关闭", summary)

    def test_current_diagnostic_contract_is_rejected_with_zero_drafts(self) -> None:
        pack = _eligible_pack()
        pack["current_portfolio"]["risk_posture"] = (
            "review_required_no_risk_increasing_action"
        )
        pack["current_portfolio"]["target_gap_preview"] = {
            "preview_only": True,
            "scheduled_purchase_dates": [],
        }
        pack["research_conclusion"]["monthly_action_status"] = (
            "no_new_schedule_generated_mid_cycle"
        )

        with self.assertRaisesRegex(ValueError, "risk posture"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256=_sha256(pack),
                simulated_subscription_plan=_simulation_plan(),
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )

    def test_unvalidated_route_and_tampered_pack_fail_closed(self) -> None:
        pack = _eligible_pack()
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256="a" * 64,
                simulated_subscription_plan=_simulation_plan(),
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )

        unknown_fee_route = replace(
            next(route for route in self.routes.routes if route.fund_code == "539003"),
            purchase_fee_rate=None,
        )
        unknown_fee_pool = replace(self.routes, routes=(unknown_fee_route,))
        with self.assertRaisesRegex(ValueError, "purchase fee is unknown"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256=_sha256(pack),
                simulated_subscription_plan=_simulation_plan(),
                purchase_route_pool=unknown_fee_pool,
                account_alias="fund_account_primary",
            )

        plan = _simulation_plan(fund_code="110020")
        with self.assertRaisesRegex(ValueError, "not an active purchase candidate"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256=_sha256(pack),
                simulated_subscription_plan=plan,
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )

    def test_preview_or_unresolved_issue_cannot_be_drafted(self) -> None:
        pack = _eligible_pack()
        preview_pack = deepcopy(pack)
        preview_pack["current_portfolio"]["target_gap_preview"]["preview_only"] = True
        with self.assertRaisesRegex(ValueError, "diagnostic preview"):
            build_mock_purchase_drafts(
                decision_pack=preview_pack,
                source_pack_sha256=_sha256(preview_pack),
                simulated_subscription_plan=_simulation_plan(),
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )

        plan = _simulation_plan()
        plan["issues"] = ["route_limit_needs_review"]
        with self.assertRaisesRegex(ValueError, "unresolved issues"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256=_sha256(pack),
                simulated_subscription_plan=plan,
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )

    def test_route_daily_cap_is_revalidated_during_conversion(self) -> None:
        pack = _eligible_pack()
        plan = _simulation_plan()
        plan["simulated_subscriptions"][0]["gross_amount_cny"] = "5000.01"
        with self.assertRaisesRegex(ValueError, "route daily cap"):
            build_mock_purchase_drafts(
                decision_pack=pack,
                source_pack_sha256=_sha256(pack),
                simulated_subscription_plan=plan,
                purchase_route_pool=self.routes,
                account_alias="fund_account_primary",
            )


if __name__ == "__main__":
    unittest.main()
