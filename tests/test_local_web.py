from copy import deepcopy
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from invest_agent.data.portfolio_store import clean_snapshot, save_snapshot
from invest_agent.web.read_model import Dashboard
from invest_agent.web.server import make_handler
from invest_agent.automation.maintenance_cli import _run_job


def sample():
    return {'as_of':'2026-10-09T22:00:00+08:00','quality_status':'pass',
            'cash':'10','total_assets':'110','token':'do-not-store',
            'positions':[{'fund_code':'110020','fund_name':'Test','shares':'20',
                          'market_value':'100','weight':'0.909','accountId':'do-not-store'}]}


class PortfolioStoreTests(unittest.TestCase):
    def test_allowlist_unknown_dates_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'private/db.sqlite3'
            a=save_snapshot(db,sample()); self.assertEqual(a,save_snapshot(db,sample()))
            with sqlite3.connect(db) as con:
                rows=con.execute('SELECT payload_json FROM portfolio_snapshots').fetchall()
            self.assertEqual(len(rows),1)
            self.assertNotIn('do-not-store',rows[0][0])
            p=json.loads(rows[0][0])['positions'][0]
            self.assertIsNone(p['nav_date']); self.assertIsNone(p['latest_income'])

    def test_reject_failed_nonfinite_and_unreconciled(self):
        for field,value in [('quality_status','fail'),('cash','NaN'),('total_assets','999')]:
            p=sample();p[field]=value
            with self.assertRaises(ValueError): clean_snapshot(p)
        p=sample();p['positions']*=2
        with self.assertRaises(ValueError): clean_snapshot(p)

    def test_read_model_uses_latest_capture_per_day_and_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            model=Dashboard(Path(tmp)); self.assertEqual(model.portfolios(),[])
            self.assertFalse(model.database.exists())
            first=sample();save_snapshot(model.database,first)
            later=deepcopy(first);later['as_of']='2026-10-09T23:00:00+08:00'
            save_snapshot(model.database,later)
            self.assertEqual([p['as_of'] for p in model.portfolios()],[later['as_of']])
            with self.assertRaises(sqlite3.OperationalError):
                model.rows('DELETE FROM portfolio_snapshots')

    def test_maintenance_does_not_log_account_payload(self):
        with patch('invest_agent.automation.maintenance_cli._run_json_command',return_value=(0,sample(),'private raw error')) as call:
            r=_run_job({'kind':'portfolio_snapshot','job_id':'portfolio_daily'},root=Path('.'),as_of=datetime.now())
        self.assertEqual(r['status'],'complete')
        self.assertNotIn('do-not-store',json.dumps(r))
        self.assertNotIn('private raw error',json.dumps(r))
        self.assertIn('scripts/capture_portfolio_snapshot.py',call.call_args.args[0])


class FakeSocket:
    def __init__(self,request): self.request=BytesIO(request);self.response=bytearray()
    def makefile(self,*_): return self.request
    def sendall(self,data): self.response.extend(data)


class WebBoundaryTests(unittest.TestCase):
    def request(self,path,host='127.0.0.1:8765',origin=None,method='GET'):
        req=f'{method} {path} HTTP/1.1\r\nHost: {host}\r\n'
        if origin: req+=f'Origin: {origin}\r\n'
        req+='Connection: close\r\n\r\n'
        sock=FakeSocket(req.encode())
        make_handler(Path('.'))(sock,('127.0.0.1',1234),None)
        return bytes(sock.response)

    def test_static_and_blocked_origins_paths_writes(self):
        self.assertIn(b'200 OK',self.request('/'))
        self.assertIn(b'403',self.request('/',host='attacker.example'))
        self.assertIn(b'403',self.request('/',origin='https://attacker.example'))
        self.assertIn(b'404',self.request('/data/private/invest_agent.sqlite3'))
        self.assertIn(b'404',self.request('/../README.md'))
        self.assertIn(b'400',self.request('/api/nav?code=bad'))
        self.assertIn(b'501',self.request('/api/dashboard',method='POST'))

    def test_api_uses_local_model_and_hides_exceptions(self):
        with patch.object(Dashboard,'payload',return_value={'local_only':True}):
            self.assertIn(b'"local_only": true',self.request('/api/dashboard'))
        with patch.object(Dashboard,'payload',side_effect=ValueError('secret /private/path')):
            response=self.request('/api/dashboard')
            self.assertIn(b'503',response);self.assertNotIn(b'secret',response)


class FundWatchCatalogTests(unittest.TestCase):
    def test_every_currently_monitored_fund_has_a_display_classification(self):
        payload = Dashboard(Path('.')).payload()
        missing_types = [fund['code'] for fund in payload['funds'] if fund['fund_type'] == '待分类']
        missing_buckets = [fund['code'] for fund in payload['funds'] if fund['position_bucket'] == '待归属']
        self.assertEqual(missing_types, [])
        self.assertEqual(missing_buckets, [])

    def test_held_share_classes_have_expected_broad_market_classification(self):
        funds = {fund['code']: fund for fund in Dashboard(Path('.')).payload()['funds']}
        self.assertEqual((funds['000071']['fund_type'], funds['000071']['position_bucket']), ('恒生指数', '核心仓'))
        self.assertEqual((funds['008401']['fund_type'], funds['008401']['position_bucket']), ('标普500等权', '核心仓'))
        self.assertEqual((funds['008706']['fund_type'], funds['008706']['position_bucket']), ('富时100', '核心仓'))

    def test_active_japan_fund_is_core_active_enhancement(self):
        funds = {fund['code']: fund for fund in Dashboard(Path('.')).payload()['funds']}
        self.assertEqual((funds['007280']['fund_type'], funds['007280']['position_bucket']), ('日本主动增强', '核心仓'))

    def test_selected_backtests_are_exposed_with_research_gate(self):
        strategies = Dashboard(Path('.')).payload()['strategies']['items']
        baseline = next(item for item in strategies if item['strategy_id'] == 'dca_baseline' and item['version'] == '1.5.0')
        self.assertEqual(baseline['backtest']['mode'], 'research_only')
        self.assertEqual(baseline['backtest']['gate']['status'], 'blocked')
        self.assertIsNotNone(baseline['backtest']['metrics']['annualized_return_pct'])

    def test_nav_api_returns_full_published_history(self):
        series = Dashboard(Path('.')).series('000071')
        self.assertGreater(len(series), 180)
        self.assertLess(series[0]['nav_date'], series[-1]['nav_date'])
        self.assertIn('accumulated_nav', series[0])
        self.assertIsNotNone(series[0]['accumulated_nav'])

    def test_latest_fund_income_excludes_cash_and_reports_missing_date(self):
        snapshot = sample()
        snapshot['positions'][0]['latest_income'] = '12.5'
        summary = Dashboard(Path('.')).latest_fund_income([snapshot])
        self.assertEqual(summary['value'], 12.5)
        self.assertEqual(summary['source'], 'channel')
        self.assertTrue(summary['excludes_cash'])
        self.assertEqual(summary['date_status'], 'unreported')

    def test_latest_fund_income_reports_unified_channel_date(self):
        # 渠道给出统一收益日期时，总览直接展示该日期，不做快照差额降级
        snapshot = sample()
        snapshot['positions'][0].update(latest_income='12.5', income_date='2026-10-08')
        summary = Dashboard(Path('.')).latest_fund_income([snapshot])
        self.assertEqual(summary['source'], 'channel')
        self.assertEqual(summary['date_status'], 'reported')
        self.assertEqual(summary['income_date'], '2026-10-08')


if __name__=='__main__': unittest.main()
