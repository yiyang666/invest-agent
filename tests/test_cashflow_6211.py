from datetime import date, timedelta
from decimal import Decimal as D
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.cashflow_6211 import (
    build_broker_core_allocation, evaluate_satellite_profit_taking,
    satellite_rsi_targets, satellite_trough_recovery_targets,
)


class Cashflow6211Tests(unittest.TestCase):
    def plan(self, **overrides):
        weights = {'core': D('.6'), 'bond': D('.2'), 'gold': D('.1'), 'satellite': D('.1')}
        args = dict(planned_date=date(2026, 1, 1), pre_contribution_portfolio_value_cny=D(0),
                    monthly_contribution_cny=D(5000), current_position_values_cny={k: D(0) for k in weights},
                    prior_available_cash_cny=D(0), nondeployable_cash_cny=D(0),
                    effective_target_weights=weights, strategic_weights=weights,
                    core_sleeves=['core'], broker_core_amount_cny=D(3000), strategy_spec_sha256='a'*64)
        args.update(overrides)
        return build_broker_core_allocation(**args)

    def test_budget_is_split_between_exogenous_core_and_managed_pool(self):
        p = self.plan()
        amounts = {a.sleeve: a.allocated_cny for a in p.allocations}
        self.assertEqual(amounts, {'core': D(3000), 'bond': D(1000), 'gold': D(500), 'satellite': D(500)})
        self.assertEqual(p.unallocated_cash_cny, 0)

    def test_old_cash_does_not_expand_management_budget(self):
        p = self.plan(pre_contribution_portfolio_value_cny=D(5000), prior_available_cash_cny=D(2000),
                      current_position_values_cny={'core': D(3000), 'bond': D(0), 'gold': D(0), 'satellite': D(0)})
        amounts = {a.sleeve: a.allocated_cny for a in p.allocations}
        self.assertEqual(amounts, {'core': D(3000), 'bond': D(1000), 'gold': D(500), 'satellite': D(500)})
        self.assertEqual(p.unallocated_cash_cny, 2000)

    def test_pending_is_not_spendable_and_core_actual_may_vary(self):
        p = self.plan(pre_contribution_portfolio_value_cny=D(5000), prior_available_cash_cny=D(500),
                      nondeployable_cash_cny=D(1500), broker_core_amount_cny=D(3500),
                      current_position_values_cny={'core': D(3000), 'bond': D(0), 'gold': D(0), 'satellite': D(0)})
        a = {a.sleeve: a.allocated_cny for a in p.allocations}
        self.assertEqual(a['core'], 3500)
        self.assertEqual(sum(a.values()), 5000)
        self.assertEqual(p.unallocated_cash_cny, 500)

    def test_managed_pool_does_not_add_to_underweight_core(self):
        p = self.plan(pre_contribution_portfolio_value_cny=D(10000), prior_available_cash_cny=D(2000),
                      current_position_values_cny={'core': D(1000), 'bond': D(4000), 'gold': D(2000), 'satellite': D(1000)})
        self.assertEqual(next(a.allocated_cny for a in p.allocations if a.sleeve == 'core'), 3000)

    def test_inactive_satellite_budget_remains_wallet_cash(self):
        targets = {'core': D('.6'), 'bond': D('.2'), 'gold': D('.1'), 'satellite': D(0)}
        p = self.plan(pre_contribution_portfolio_value_cny=D(5000), prior_available_cash_cny=D(2000),
                      current_position_values_cny={'core': D(3000), 'bond': D(0), 'gold': D(0), 'satellite': D(0)},
                      effective_target_weights=targets)
        self.assertEqual(next(a.allocated_cny for a in p.allocations if a.sleeve == 'satellite'), 0)
        self.assertGreaterEqual(p.unallocated_cash_cny, 2000)

    def test_rejects_invalid_money_and_double_budgets(self):
        for overrides in ({'broker_core_amount_cny': D(5001)}, {'monthly_contribution_cny': D(6000)},
                          {'prior_available_cash_cny': D('NaN')}, {'pre_contribution_portfolio_value_cny': D(1)}):
            with self.assertRaises(ValueError):
                self.plan(**overrides)

    def test_zero_management_budget_never_uses_carry_cash(self):
        p = self.plan(monthly_contribution_cny=D(3000))
        self.assertEqual(sum(a.allocated_cny for a in p.allocations if a.sleeve != 'core'), 0)

    def points(self):
        # Long positive trend with a short pullback: low RSI above MA200.
        values = [1 + n*.01 for n in range(210)] + [3.09 - n*.005 for n in range(1, 15)]
        return [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(v)),
                         batch_id='fixture', content_sha256='a'*64,
                         visibility_status='historical_visibility_assumed') for n, v in enumerate(values)]

    def test_rsi_signal_uses_no_same_day_or_future_nav(self):
        points = self.points()
        review = points[-2].nav_date
        kwargs = dict(review_date=review, strategic_weights={'core': D('.9'), 'satellite': D('.1')})
        baseline = satellite_rsi_targets(**kwargs, satellite_series={'satellite': points})
        changed = list(points)
        changed[-2:] = [NavPoint(nav_date=p.nav_date, nav=D(1000), batch_id=p.batch_id,
                                content_sha256=p.content_sha256, visibility_status=p.visibility_status)
                        for p in points[-2:]]
        self.assertEqual(baseline, satellite_rsi_targets(**kwargs, satellite_series={'satellite': changed}))
        self.assertTrue(baseline['evaluations'][0]['eligible'])
        self.assertLess(date.fromisoformat(baseline['evaluations'][0]['effective_nav_date']), review)

    def test_missing_or_stale_signals_fail_closed(self):
        p = self.points()
        for series in (p[:10], p):
            result = satellite_rsi_targets(review_date=date(2026, 1, 1),
                                          strategic_weights={'core': D('.9'), 'satellite': D('.1')},
                                          satellite_series={'satellite': series})
            self.assertEqual(D(result['effective_target_weights']['satellite']), 0)

    def test_trough_recovery_triggers_on_rebound_from_oversold_trough(self):
        # 40 baseline + fall to 1.50 (-25% drawdown) + rebound to 1.56 (+4% rebound) + padding
        values = [2.0] * 40 + [2.0 - n * 0.02 for n in range(26)] + [1.50 + n * 0.012 for n in range(1, 6)] + [1.56] * 5
        series = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                           batch_id='fixture', content_sha256='b'*64,
                           visibility_status='historical_visibility_assumed') for n, v in enumerate(values)]
        review = series[-2].nav_date
        res = satellite_trough_recovery_targets(
            review_date=review,
            strategic_weights={'core': D('.9'), 'satellite': D('.1')},
            satellite_series={'satellite': series},
            trough_window_days=40,
            tranche_weight=D('.05'),
        )
        self.assertTrue(res['evaluations'][0]['eligible'])
        self.assertEqual(res['evaluations'][0]['reason'], 'trough_recovery_confirmed')
        self.assertEqual(D(res['effective_target_weights']['satellite']), D('.05'))

        # Future mutation does not leak back
        mutated = list(series)
        mutated[-2:] = [NavPoint(nav_date=p.nav_date, nav=D(999), batch_id=p.batch_id,
                                 content_sha256=p.content_sha256, visibility_status=p.visibility_status)
                        for p in series[-2:]]
        res_mut = satellite_trough_recovery_targets(
            review_date=review,
            strategic_weights={'core': D('.9'), 'satellite': D('.1')},
            satellite_series={'satellite': mutated},
            trough_window_days=40,
            tranche_weight=D('.05'),
        )
        self.assertEqual(res, res_mut)

    def test_trough_recovery_rejects_falling_knife_and_overchased_runup(self):
        # Monotonically falling: 30 baseline + peak 2.0 to 1.10 (current is at 1.10, rebound = 0)
        falling = [2.0] * 30 + [2.0 - n * 0.015 for n in range(60)]
        s_falling = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                              batch_id='fixture', content_sha256='c'*64,
                              visibility_status='historical_visibility_assumed') for n, v in enumerate(falling)]
        review_f = s_falling[-1].nav_date + timedelta(days=2)
        res_f = satellite_trough_recovery_targets(
            review_date=review_f,
            strategic_weights={'core': D('.9'), 'satellite': D('.1')},
            satellite_series={'satellite': s_falling},
            trough_window_days=40,
        )
        self.assertFalse(res_f['evaluations'][0]['eligible'])
        self.assertEqual(res_f['evaluations'][0]['reason'], 'rebound_insufficient_or_falling')
        self.assertEqual(D(res_f['effective_target_weights']['satellite']), 0)

        # Huge runup (> 25% rebound): 30 baseline + trough 1.0, rebounded to 1.30 (+30%)
        runup = [1.5] * 35 + [1.5 - n * 0.05 for n in range(11)] + [1.0 + n * 0.02 for n in range(1, 16)]
        s_runup = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                            batch_id='fixture', content_sha256='d'*64,
                            visibility_status='historical_visibility_assumed') for n, v in enumerate(runup)]
        review_r = s_runup[-1].nav_date + timedelta(days=2)
        res_r = satellite_trough_recovery_targets(
            review_date=review_r,
            strategic_weights={'core': D('.9'), 'satellite': D('.1')},
            satellite_series={'satellite': s_runup},
            trough_window_days=40,
        )
        self.assertFalse(res_r['evaluations'][0]['eligible'])
        self.assertEqual(res_r['evaluations'][0]['reason'], 'overbought_or_excessive_runup')
        self.assertEqual(D(res_r['effective_target_weights']['satellite']), 0)

    def test_bond_reservoir_parks_unused_satellite_weight_and_eliminates_cash_drag(self):
        falling = [2.0] * 30 + [2.0 - n * 0.015 for n in range(60)]
        s_falling = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                              batch_id='fixture', content_sha256='e'*64,
                              visibility_status='historical_visibility_assumed') for n, v in enumerate(falling)]
        review = s_falling[-1].nav_date + timedelta(days=2)
        strategic = {'core': D('.6'), 'bond': D('.2'), 'gold': D('.1'), 'satellite': D('.1')}
        res = satellite_trough_recovery_targets(
            review_date=review,
            strategic_weights=strategic,
            satellite_series={'satellite': s_falling},
            trough_window_days=40,
            released_weight_destination='bond',
        )
        eff = {k: D(v) for k, v in res['effective_target_weights'].items()}
        self.assertEqual(eff['satellite'], D(0))
        self.assertEqual(eff['bond'], D('.3'))
        self.assertEqual(sum(eff.values()), D('1.0'))

        # With waterfall allocation and zero satellite target, budget fills gold first then bond reservoir
        plan = self.plan(
            pre_contribution_portfolio_value_cny=D(5000),
            current_position_values_cny={'core': D(5000), 'bond': D(0), 'gold': D(0), 'satellite': D(0)},
            effective_target_weights=eff,
            allocation_priority='waterfall',
        )
        amounts = {a.sleeve: a.allocated_cny for a in plan.allocations}
        self.assertEqual(amounts['satellite'], D(0))
        self.assertEqual(amounts['gold'], D(1000))
        self.assertEqual(amounts['bond'], D(1000))
        self.assertEqual(plan.unallocated_cash_cny, D(0))

    def test_waterfall_priority_concentrates_budget_into_triggered_satellite(self):
        # Post value = 10000 + 5000 = 15000. Satellite target 10% = 1500.
        # Managed budget = 2000. Waterfall puts 1500 into satellite first, then 500 into gold!
        p = self.plan(
            pre_contribution_portfolio_value_cny=D(10000),
            current_position_values_cny={'core': D(10000), 'bond': D(0), 'gold': D(0), 'satellite': D(0)},
            allocation_priority='waterfall',
        )
        amounts = {a.sleeve: a.allocated_cny for a in p.allocations}
        self.assertEqual(amounts['core'], D(3000))
        self.assertEqual(amounts['satellite'], D(1500))
        self.assertEqual(amounts['gold'], D(500))
        self.assertEqual(amounts['bond'], D(0))

    def test_satellite_profit_taking_respects_90_day_fee_gate_and_overheating(self):
        locked = evaluate_satellite_profit_taking(holding_days=45, cumulative_return_pct=35.0, current_rsi_13=78.0)
        self.assertFalse(locked['should_harvest'])
        self.assertEqual(locked['action'], 'hold_within_fee_lock_period')

        ready = evaluate_satellite_profit_taking(holding_days=120, cumulative_return_pct=26.5, current_rsi_13=74.2)
        self.assertTrue(ready['should_harvest'])
        self.assertEqual(ready['action'], 'harvest_half_to_bond_reservoir')
        self.assertEqual(ready['recommended_redeem_fraction'], '0.50')
        self.assertEqual(ready['destination_fund_code'], '006932')

    def test_stepwise_optimizations_odds_ranking_erp_gold_and_trailing_lock(self):
        # Single triggered satellite sleeve with small static weight (0.0333) gets boosted up to tranche_weight (0.06)
        # when conviction_ranking=True, funded out of the bond reservoir.
        vals = [1.5] * 35 + [1.5 - n * 0.04 for n in range(1, 11)] + [1.10, 1.12, 1.14, 1.15, 1.16]
        s_rec = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                          batch_id='fixture', content_sha256='f'*64,
                          visibility_status='historical_visibility_assumed') for n, v in enumerate(vals)]
        review = s_rec[-1].nav_date + timedelta(days=2)
        strategic = {'core': D('.6'), 'bond': D('.2'), 'gold': D('.1'), 'sat_a': D('.05'), 'sat_b': D('.05')}
        falling = [2.0] * 30 + [2.0 - n * 0.015 for n in range(25)]
        s_fall = [NavPoint(nav_date=date(2025, 1, 1) + timedelta(days=n), nav=D(str(round(v, 4))),
                           batch_id='fixture', content_sha256='e'*64,
                           visibility_status='historical_visibility_assumed') for n, v in enumerate(falling)]
        res = satellite_trough_recovery_targets(
            review_date=review,
            strategic_weights=strategic,
            satellite_series={'sat_a': s_rec, 'sat_b': s_fall},
            trough_window_days=35,
            tranche_weight=D('0.08'),
            max_satellite_weight=D('0.10'),
            released_weight_destination='bond',
            conviction_ranking=True,
            top_k_satellites=1,
            enable_erp_gate=True,
            erp_pct=6.15,
        )
        eff = {k: D(v) for k, v in res['effective_target_weights'].items()}
        self.assertEqual(res['erp_regime'], 'high_odds_equity_cheap')
        self.assertEqual(eff['sat_a'], D('0.0800'))
        self.assertEqual(eff['sat_b'], D('0'))
        self.assertEqual(eff['bond'], D('0.2200'))
        self.assertEqual(sum(eff.values()), D('1.0000'))


if __name__ == '__main__':
    unittest.main()


