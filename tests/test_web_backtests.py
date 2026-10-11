from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invest_agent.data.store import FundDataStore
from invest_agent.web.backtest_service import BacktestService
from invest_agent.web.server import make_handler
from tests.test_fund_data_store import valid_batch
from tests import test_local_web as web_tests


def publish(root, rising=False, weekdays=False):
    db = Path(root)/'data/private/invest_agent.sqlite3'
    store = FundDataStore(db)
    batch = valid_batch()
    for code in ['000001', '000002']:
        records = tuple(replace(batch.records[0], fund_code=code,
                                nav_date=date(2026,1,1)+timedelta(days=i),
                                unit_nav=Decimal('1')+(Decimal(i)/1000 if rising and code=='000001' else 0),
                                accumulated_nav=Decimal('1')+(Decimal(i)/1000 if rising and code=='000001' else 0))
                        for i in range(90) if not weekdays or (date(2026,1,1)+timedelta(days=i)).weekday()<5)
        store.publish_nav_batch(replace(batch, batch_id=code, records=records))
    return db


def config(mode='fixed_weights', fee='0'):
    return {'start_date':'2026-01-01', 'end_date':'2026-03-31', 'monthly_amount':'100',
            'calendar_day':5, 'rebalance':mode,
            'funds':[{'code':'000001', 'weight':'50', 'fee_pct':fee},
                     {'code':'000002', 'weight':'50', 'fee_pct':fee}]}


class WebBacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = publish(self.root)
        self.service = BacktestService(self.root)

    def test_constant_nav_reconciles_contributions_wealth_cash_and_xirr(self):
        before = self.db.read_bytes()
        for mode in ['fixed_weights', 'revalidated_target_gap']:
            experiment = self.service.run(config(mode))
            profile = experiment['result']['profile_results'][0]
            metrics = profile['metrics']
            self.assertEqual(metrics['contributions_cny'], '300.00')
            self.assertEqual(metrics['final_value_cny'], '300.00')
            self.assertEqual(metrics['maximum_drawdown_pct'], 0)
            self.assertAlmostEqual(metrics['xirr_pct'], 0, places=5)
            self.assertTrue(all(p['value']==1 for p in metrics['unitized_path']))
            final = profile['valuation_result']['daily_valuations'][-1]
            self.assertEqual(sum(Decimal(p['market_value_cny']) for p in final['positions']), Decimal('300'))
        self.assertEqual(before, self.db.read_bytes())
        self.assertFalse(self.service.experiments.exists())

    def test_fees_reduce_assets_and_are_not_double_counted(self):
        profile = self.service.run(config(fee='1'))['result']['profile_results'][0]
        metrics = profile['metrics']
        self.assertEqual(metrics['purchase_fees_cny'], '2.97')
        self.assertEqual(metrics['final_value_cny'], '297.00')
        self.assertLess(metrics['xirr_pct'], 0)
        self.assertLess(metrics['maximum_drawdown_pct'], 0)
        summary=profile['subscription_result']['summary']
        self.assertEqual(Decimal(summary['purchase_fees_cny'])+Decimal(summary['share_rounding_adjustment_cny']),Decimal('3'))

    def test_parameters_and_history_coverage_rejected(self):
        invalid = []
        for key, value in [('monthly_amount','NaN'),('monthly_amount','100.001'),('calendar_day',29),
                           ('calendar_day',True),('rebalance','sell'),('start_date','2025-12-31'),
                           ('end_date','2026-04-01')]:
            item=config();item[key]=value;invalid.append(item)
        item=config();item['funds'][1]['weight']='49';invalid.append(item)
        item=config();item['funds'][1]['code']='000001';invalid.append(item)
        item=config();item['funds'][0]['fee_pct']='Infinity';invalid.append(item)
        item=config();item['funds'][0]['code']='999999';invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.service.run(item)

    def test_fund_count_accepts_ten_and_rejects_eleven(self):
        catalog = [{'code': f'{i:06d}', 'name': str(i),
                    'start_date': '2026-01-01', 'end_date': '2026-03-31'}
                   for i in range(1, 12)]
        ten = config()
        ten['funds'] = [{'code': f'{i:06d}', 'weight': '10', 'fee_pct': '0'}
                        for i in range(1, 11)]
        with patch.object(self.service, 'catalog', return_value=catalog):
            self.assertEqual(len(self.service.validate(ten)['funds']), 10)
            eleven = deepcopy(ten)
            eleven['funds'].append({'code': '000011', 'weight': '0.01', 'fee_pct': '0'})
            with self.assertRaisesRegex(ValueError, '1–10'):
                self.service.validate(eleven)

    def test_saved_results_are_server_computed_and_survive_restart(self):
        experiment=self.service.run(config())
        self.service.save({'run_id':experiment['id'],'name':'均衡实验','result':{'forged':True}})
        restarted=BacktestService(self.root)
        self.assertEqual(restarted.history()[0]['name'],'均衡实验')
        self.assertEqual(restarted.history(experiment['id'])['result'],experiment['result'])
        with self.assertRaises(ValueError):
            restarted.save({'run_id':'bad','name':'伪造'})
        with self.assertRaises(ValueError):
            restarted.history('../secrets')

    def test_delete_removes_only_the_selected_saved_experiment(self):
        first=self.service.run(config())
        self.service.save({'run_id':first['id'],'name':'待删除'})
        second=self.service.run(config('revalidated_target_gap'))
        self.service.save({'run_id':second['id'],'name':'保留'})

        self.assertEqual(self.service.delete(first['id']),
                         {'status':'deleted','id':first['id']})
        self.assertIsNone(self.service.history(first['id']))
        self.assertEqual(self.service.history(second['id'])['name'],'保留')
        with self.assertRaisesRegex(ValueError,'未找到'):
            self.service.delete(first['id'])
        with self.assertRaisesRegex(ValueError,'编号无效'):
            self.service.delete('../private')

    def test_fixed_weights_vs_low_weight_repair_and_no_future_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(tmp, rising=True)
            service=BacktestService(tmp)
            fixed=service.run(config())['result']['profile_results'][0]
            gap=service.run(config('revalidated_target_gap'))['result']['profile_results'][0]
            # Same initial plan despite subsequent rising NAV; later contributions repair low weight.
            def orders(profile, month):
                return {r['fund_code']:Decimal(r['gross_amount_cny'])
                        for r in profile['subscription_result']['ledger']
                        if r['event_type']=='subscription_confirmed' and r['submit_date'].startswith(month)}
            self.assertEqual(orders(fixed,'2026-01'),orders(gap,'2026-01'))
            self.assertEqual(orders(fixed,'2026-03')['000002'],50)
            self.assertGreater(orders(gap,'2026-03')['000002'],50)
            for r in gap['subscription_result']['ledger']:
                if r['event_type']=='subscription_confirmed':
                    self.assertLessEqual(r['signal_date'],r['submit_date'])
                    self.assertEqual(r['execution_nav_date'],r['submit_date'])
                    self.assertGreater(r['nav_visible_date'],r['submit_date'])
                    self.assertEqual(r['confirmation_date'],r['nav_visible_date'])

    def test_independently_rounded_positions_do_not_create_negative_cash(self):
        from invest_agent.backtest.local_research import _current_position_cash_state
        state={'daily_valuations':[{'total_value_cny':'2.01','available_cash_cny':'0',
                'pending_subscription_cash_cny':'0','distribution_receivable_cny':'0',
                'positions':[{'fund_code':c,'market_value_cny':'1.01'} for c in ['000001','000002']]}]}
        total,positions,cash,pending=_current_position_cash_state(
            state,fund_to_sleeve={'000001':'a','000002':'b'},sleeves=['a','b'])
        self.assertEqual(cash,0)
        self.assertEqual(total,sum(positions.values())+cash+pending)
        state['daily_valuations'][0]['total_value_cny']='2.03'
        total,positions,cash,pending=_current_position_cash_state(
            state,fund_to_sleeve={'000001':'a','000002':'b'},sleeves=['a','b'])
        self.assertEqual(cash,0)  # positive rounding residual is not spendable cash either
        self.assertEqual(total,sum(positions.values())+cash+pending)
        state['daily_valuations'][0]['total_value_cny']='1.00'
        with self.assertRaises(ValueError):
            _current_position_cash_state(state,fund_to_sleeve={'000001':'a','000002':'b'},sleeves=['a','b'])

    def test_weekend_plan_defers_to_nav_day_and_confirmation_is_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish(tmp, weekdays=True)
            cfg=config();cfg['calendar_day']=3
            p=BacktestService(tmp).run(cfg)['result']['profile_results'][0]
            orders=[r for r in p['subscription_result']['ledger'] if r['event_type']=='subscription_confirmed']
            first=orders[0]
            self.assertEqual(first['signal_date'],'2026-01-03')  # Saturday
            self.assertEqual(first['submit_date'],'2026-01-05')
            self.assertEqual(first['execution_nav_date'],'2026-01-05')
            self.assertEqual(first['confirmation_date'],'2026-01-06')

    def test_http_run_save_and_origin_boundary(self):
        helper=web_tests.WebBoundaryTests();helper.setUp()
        handler=make_handler(self.root,helper.credential_store,helper.aijijin_onboarding)
        def request(path, **kw):return helper.request(path,handler=handler,**kw)
        self.assertIn(b'200 OK',request('/backtests.js'))
        self.assertIn(b'200 OK',request('/api/backtests/catalog'))
        self.assertIn(b'403',request('/api/backtests/run',origin='https://evil.example',method='POST',body=json.dumps(config()).encode(),content_type='application/json'))
        response=request('/api/backtests/run',method='POST',body=json.dumps(config()).encode(),content_type='application/json')
        self.assertIn(b'200 OK',response)
        experiment=json.loads(response.split(b'\r\n\r\n',1)[1])
        response=request('/api/backtests/save',method='POST',body=json.dumps({'run_id':experiment['id'],'name':'HTTP实验'}).encode(),content_type='application/json')
        self.assertIn(b'200 OK',response)
        self.assertIn(b'403',request('/api/backtests?id='+experiment['id'],origin='https://evil.example',method='DELETE'))
        response=request('/api/backtests?id='+experiment['id'],method='DELETE')
        self.assertIn(b'200 OK',response)
        self.assertIn(b'"status": "deleted"',response)
        self.assertIn(b'404',request('/api/backtests?id='+experiment['id']))
        self.assertIn(b'400',request('/api/backtests?id=bad',method='DELETE'))
        self.assertIn(b'400',request('/api/backtests/run',method='POST',body=b'{}',content_type='application/json'))
        self.assertIn(b'404',request('/api/backtests?id='+'a'*32))
