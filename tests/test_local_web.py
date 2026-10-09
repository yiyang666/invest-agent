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


if __name__=='__main__': unittest.main()
