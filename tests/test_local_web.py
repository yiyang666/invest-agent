from copy import deepcopy
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from invest_agent.aijijin_onboarding import AijijinOnboarding, AijijinOnboardingError
from invest_agent.credentials import CredentialStoreError, KeychainCredentialStore
from invest_agent.data.portfolio_store import clean_snapshot, save_snapshot
from invest_agent.web.read_model import Dashboard
from invest_agent.web.server import make_handler
from invest_agent.automation.maintenance_cli import _run_job


def sample():
    return {'as_of':'2026-10-09T22:00:00+08:00','quality_status':'pass',
            'cash':'10','total_assets':'110','token':'do-not-store',
            'positions':[{'fund_code':'110020','fund_name':'Test','shares':'20',
                          'market_value':'100','weight':'0.909','accountId':'do-not-store'}]}


class CredentialStoreTests(unittest.TestCase):
    def test_keychain_write_uses_stdin_and_never_process_arguments(self):
        store = KeychainCredentialStore.__new__(KeychainCredentialStore)
        store.account = 'fixture-user'
        secret = 'fixture-token-never-in-argv'
        completed = subprocess.CompletedProcess([], 0)
        with patch('invest_agent.credentials.subprocess.run', return_value=completed) as run:
            store.set('jin10', secret)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/security', '-i'])
        self.assertNotIn(secret, ' '.join(args[0]))
        self.assertIn(secret.encode().hex(), kwargs['input'])
        self.assertIs(kwargs['stdout'], subprocess.DEVNULL)
        self.assertIs(kwargs['stderr'], subprocess.DEVNULL)

    def test_locked_keychain_has_fixed_error_without_command_output(self):
        store = KeychainCredentialStore.__new__(KeychainCredentialStore)
        store.account = 'fixture-user'
        completed = subprocess.CompletedProcess([], 36, stdout='secret', stderr='private')
        with patch('invest_agent.credentials.subprocess.run', return_value=completed):
            with self.assertRaisesRegex(CredentialStoreError, 'keychain_locked'):
                store.set('guchacha', 'fixture-token')


class AijijinOnboardingTests(unittest.TestCase):
    def test_status_never_reads_or_returns_credential_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.agents/skills/thsfund').mkdir(parents=True)
            (root / '.agents/skills/thsfund/SKILL.md').write_text('fixture')
            (root / '.conda-env/bin').mkdir(parents=True)
            (root / '.conda-env/bin/aijijin').write_text('fixture')
            credential = root / 'private/credentials.json'
            credential.parent.mkdir()
            credential.write_text('never-return-this-secret')
            status = AijijinOnboarding(root, credential).status()
            self.assertTrue(status['configured'])
            self.assertNotIn('never-return-this-secret', json.dumps(status))

    def test_initialize_returns_only_reviewed_state(self):
        onboarding = AijijinOnboarding.__new__(AijijinOnboarding)
        onboarding.root = Path('.')
        onboarding.executable = Path('README.md')
        onboarding.skill_path = Path('README.md')
        onboarding.credential_path = Path('/fixture/credentials.json')
        completed = subprocess.CompletedProcess([], 0, stdout='{"data":{"confirmStatus":0},"secret":"never-return"}', stderr='private')
        with patch('invest_agent.aijijin_onboarding.subprocess.run', return_value=completed):
            self.assertEqual(onboarding.initialize('fixture-init-token'), {'state': 'pending_authorization'})


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


class FakeCredentialStore:
    def __init__(self):
        self.secrets = {}

    def configured(self, provider):
        return provider == 'jin10' or provider in self.secrets

    def set(self, provider, secret):
        self.secrets[provider] = secret


class FakeAijijinOnboarding:
    def __init__(self):
        self.tokens = []

    def status(self):
        return {'state': 'ready', 'configured': False, 'skill_installed': True, 'cli_installed': True, 'storage': None}

    def initialize(self, init_token):
        self.tokens.append(init_token)
        return {'state': 'pending_authorization'}


class WebBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.credential_store = FakeCredentialStore()
        self.aijijin_onboarding = FakeAijijinOnboarding()

    def request(self,path,host='127.0.0.1:8765',origin=None,method='GET',body=b'',content_type=None,handler=None):
        req=f'{method} {path} HTTP/1.1\r\nHost: {host}\r\n'
        if origin: req+=f'Origin: {origin}\r\n'
        if body: req+=f'Content-Length: {len(body)}\r\n'
        if content_type: req+=f'Content-Type: {content_type}\r\n'
        req+='Connection: close\r\n\r\n'
        sock=FakeSocket(req.encode()+body)
        (handler or make_handler(Path('.'), self.credential_store, self.aijijin_onboarding))(sock,('127.0.0.1',1234),None)
        return bytes(sock.response)

    def test_static_and_blocked_origins_paths_writes(self):
        self.assertIn(b'200 OK',self.request('/'))
        version = self.request('/api/dashboard-version')
        self.assertIn(b'200 OK', version)
        self.assertIn(b'"revision"', version)
        self.assertIn(b'403',self.request('/',host='attacker.example'))
        self.assertIn(b'403',self.request('/',origin='https://attacker.example'))
        self.assertIn(b'404',self.request('/data/private/invest_agent.sqlite3'))
        self.assertIn(b'404',self.request('/../README.md'))
        self.assertIn(b'400',self.request('/api/nav?code=bad'))
        self.assertIn(b'404',self.request('/api/dashboard',method='POST'))

    def test_market_refresh_is_exact_reviewed_action(self):
        completed = subprocess.CompletedProcess([], 0)
        with patch('invest_agent.web.server.subprocess.run', return_value=completed) as run:
            response = self.request('/api/market-refresh', method='POST')
        self.assertIn(b'200 OK', response)
        self.assertIn(b'"status":"complete"', response)
        args, kwargs = run.call_args
        self.assertTrue(args[0][-1].endswith('/scripts/refresh_market_quotes.sh'))
        self.assertFalse(kwargs['check'])
        self.assertNotIn('shell', kwargs)
        self.assertIn(b'413', self.request('/api/market-refresh', method='POST', body=b'{}'))

    def test_dashboard_reuses_unchanged_revision_and_manual_refresh_rebuilds(self):
        handler = make_handler(Path('.'), self.credential_store)
        with patch.object(Dashboard, 'source_revision', return_value='stable'), \
                patch.object(Dashboard, 'payload', return_value={'local_only': True}) as payload:
            self.assertIn(b'200 OK', self.request('/api/dashboard', handler=handler))
            self.assertIn(b'200 OK', self.request('/api/dashboard', handler=handler))
            self.assertEqual(payload.call_count, 1)
            self.assertIn(b'200 OK', self.request('/api/dashboard?refresh=1', handler=handler))
            self.assertEqual(payload.call_count, 2)

    def test_integration_status_and_save_never_echo_secret(self):
        status = self.request('/api/integrations')
        self.assertIn(b'200 OK', status)
        self.assertIn('股叉叉 MCP'.encode(), status)
        secret = 'fixture-token-never-echo'
        body = json.dumps({'provider': 'guchacha', 'token': secret}).encode()
        response = self.request(
            '/api/integrations', method='POST', body=body,
            content_type='application/json',
        )
        self.assertIn(b'200 OK', response)
        self.assertNotIn(secret.encode(), response)
        self.assertEqual(self.credential_store.secrets['guchacha'], secret)

    def test_aijijin_status_and_init_never_echo_one_time_token(self):
        status = self.request('/api/aijijin')
        self.assertIn(b'200 OK', status)
        secret = 'fixture-init-token-never-echo'
        body = json.dumps({'init_token': secret}).encode()
        response = self.request('/api/aijijin', method='POST', body=body, content_type='application/json')
        self.assertIn(b'200 OK', response)
        self.assertIn(b'pending_authorization', response)
        self.assertNotIn(secret.encode(), response)
        self.assertEqual(self.aijijin_onboarding.tokens, [secret])

    def test_integration_save_rejects_non_json_unknown_provider_and_short_token(self):
        self.assertIn(b'400', self.request('/api/integrations', method='POST', body=b'{}'))
        for payload in (
            {'provider': 'unknown', 'token': 'long-enough-token'},
            {'provider': 'jin10', 'token': 'short'},
        ):
            response = self.request(
                '/api/integrations', method='POST', body=json.dumps(payload).encode(),
                content_type='application/json',
            )
            self.assertIn(b'400', response)

    def test_market_refresh_reports_missing_credential_without_process_output(self):
        completed = subprocess.CompletedProcess([], 78, stdout='secret', stderr='private path')
        with patch('invest_agent.web.server.subprocess.run', return_value=completed):
            response = self.request('/api/market-refresh', method='POST')
        self.assertIn(b'503', response)
        self.assertIn(b'credential_unavailable', response)
        self.assertNotIn(b'secret', response)
        self.assertNotIn(b'private path', response)

    def test_api_uses_local_model_and_hides_exceptions(self):
        with patch.object(Dashboard,'payload',return_value={'local_only':True}):
            self.assertIn(b'"local_only": true',self.request('/api/dashboard'))
        with patch.object(Dashboard,'payload',side_effect=ValueError('secret /private/path')):
            response=self.request('/api/dashboard')
            self.assertIn(b'503',response);self.assertNotIn(b'secret',response)


class FundWatchCatalogTests(unittest.TestCase):
    def test_satellite_watchlist_keeps_original_industries_and_adds_agriculture(self):
        config = json.loads(Path('config/satellite_industry_watchlist_v1.json').read_text())
        codes = [item['code'] for item in config['industries']]
        self.assertEqual(len(codes), 31)
        self.assertEqual(len(set(codes)), 31)
        self.assertIn('110000', codes)
        self.assertIn('490000', codes)
        self.assertIn('330000', codes)
        agriculture = next(item for item in config['focus_themes'] if item['id'] == 'el_nino_agriculture')
        self.assertEqual(agriculture['industry_codes'], ['110000'])
        self.assertEqual(agriculture['local_proxy_funds'], ['016077'])
        route = next(item for item in config['candidate_routes'] if item['fund_code'] == '016077')
        self.assertIn('农业产业代理', route['theme'])
        route_codes = {item['fund_code'] for item in config['candidate_routes']}
        self.assertTrue({'007300', '014880', '016077', '004069', '007817', '012862', '012321', '003017', '009067', '005063', '001631'} <= route_codes)

    def test_satellite_watchlist_reads_only_published_local_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            (root / 'config/satellite_industry_watchlist_v1.json').write_text(json.dumps({
                'status': 'research_only_observation',
                'focus_themes': [{'id': 'ai_hardware', 'name': 'AI 硬件',
                                  'industry_codes': ['270000'], 'local_proxy_funds': ['007300'],
                                  'proxy_scope': '部分代理', 'route_status': 'partial_proxy_only'}],
                'industries': [{'code': '270000', 'name': '电子',
                                'exposure_group': 'technology', 'route_status': 'unreviewed'}],
            }))
            model = Dashboard(root)
            model.database.parent.mkdir(parents=True)
            with sqlite3.connect(model.database) as con:
                con.execute('CREATE TABLE market_data_batches (batch_id TEXT, quality_status TEXT)')
                con.execute('CREATE TABLE market_numeric_observations '
                            '(batch_id TEXT, series_id TEXT, observation_date TEXT, value REAL, first_seen_at TEXT)')
                con.executemany('INSERT INTO market_data_batches VALUES (?,?)',
                                [('good', 'pass'), ('bad', 'fail')])
                con.executemany('INSERT INTO market_numeric_observations VALUES (?,?,?,?,?)', [
                    ('good', 'industry_crowding:270000:composite', '2026-10-08', 59.82, '2026-10-09T23:00:00+08:00'),
                    ('bad', 'industry_crowding:270000:composite', '2026-10-15', 99, '2026-10-16T01:00:00+08:00'),
                ])
            watch = model.satellite_industries()
            item = watch['items'][0]
            self.assertEqual(item['crowding'], 59.82)
            self.assertEqual(item['observation_date'], '2026-10-08')
            self.assertEqual(item['route_status'], 'unreviewed')
            self.assertEqual(watch['focus_themes'][0]['local_proxy_funds'], ['007300'])

    def test_dashboard_revision_changes_only_when_local_inputs_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            model = Dashboard(root)
            initial = model.source_revision()
            self.assertEqual(initial, model.source_revision())
            (root / 'config/example.json').write_text('{}')
            self.assertNotEqual(initial, model.source_revision())

    def test_market_panel_uses_latest_reviewed_quote_values(self):
        model = Dashboard(Path('.'))
        rows = [
            {'series_id': 'jin10_quote:SPX:close', 'observation_date': '2026-10-09',
             'value': 6750.0, 'unit': 'index_point', 'first_seen_at': '2026-10-10T01:00:00+08:00'},
            {'series_id': 'jin10_quote:SPX:change_pct', 'observation_date': '2026-10-09',
             'value': 1.5, 'unit': '%', 'first_seen_at': '2026-10-10T01:00:00+08:00'},
        ]
        with patch.object(model, 'rows', return_value=rows):
            values = model.market_indices()
        self.assertEqual([item['code'] for item in values], ['SPX'])
        self.assertEqual(values[0]['value'], 6750.0)
        self.assertAlmostEqual(values[0]['change'], 6750.0 - 6750.0 / 1.015)
        self.assertEqual(values[0]['change_pct'], 1.5)

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

    def test_jiashi_us_growth_is_classified_as_active_nasdaq_style(self):
        funds = {fund['code']: fund for fund in Dashboard(Path('.')).payload()['funds']}
        self.assertEqual((funds['000043']['fund_type'], funds['000043']['position_bucket']), ('纳指风格主动', '核心仓'))

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
        self.assertEqual(series[0]['display_nav'], float(series[0]['accumulated_nav']))
        self.assertIsNone(series[0]['rsi_13'])
        self.assertIsNotNone(series[13]['rsi_13'])

    def test_nav_rsi_matches_reviewed_tonghuashun_examples(self):
        # These are reviewed historical observations, not the moving latest NAV.
        expected = {'110020': ('2026-10-09', 32.46),
                    '000043': ('2026-10-08', 58.97),
                    '018966': ('2026-10-08', 66.10),
                    '017641': ('2026-10-08', 56.62)}
        for code, (nav_date, value) in expected.items():
            with self.subTest(code=code):
                observation = next(row for row in Dashboard(Path('.')).series(code) if row['nav_date'] == nav_date)
                self.assertAlmostEqual(observation['rsi_13'], value, places=2)

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
