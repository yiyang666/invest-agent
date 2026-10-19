"""Offline, frozen 6211 candidate matrix; never imports a trading adapter.

Run: .conda-env/bin/python scripts/research_cashflow_6211.py
Private results are under data/reports/cashflow_6211_2026_10_12/.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest_agent.backtest.local_research import run_local_dca_research
from invest_agent.attribution.engine import compare_cashflow_matched_paths
from invest_agent.risk.stress import run_portfolio_stress

OUT = ROOT / 'data/reports/cashflow_6211_2026_10_12'
SPEC_PATH = ROOT / 'strategies/specs/cashflow_6211_v0_2_0.json'
BASE = ROOT / 'config/backtest_cashflow_6211_v0_2_0.json'
BENCH = ROOT / 'config/backtest_research_scenario_v11.json'
SATELLITES = ['sat_technology', 'sat_semiconductor', 'sat_robotics']
LABELS = {'A': '五类核心，无红利，卫星10%', 'B': '核心内红利5%，卫星10%',
          'C': '核心内红利5%，卫星5%，现金5%', 'D': '核心内红利5%，RSI筛选卫星新增',
          'E': '核心内红利5%，先低位后回升卫星动态(0~10%)',
          'F': 'v0.3 锁定核心10%红利+两阶段卫星+短债蓄水池(以债养战)+瀑布流',
          'G1': 'v0.4-Opt1 (+卫星多维赔率与筹码出清竞价打分器)',
          'G12': 'v0.4-Opt1+2 (+沪深300 ERP股债性价比总闸门 & 黄金200日均线状态机)',
          'G123': 'v0.4-Opt1+2+3 (+移动跟踪止盈保护与末期追高熔断)',
          'dividend10': '核心内红利10%，卫星10%', 'satellite0': '核心内红利5%，卫星0%，现金10%'}


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def candidate(key='B', *, budget=2000, start='2024-01-01', fee='.0015', rsi=30):
    s = json.loads(BASE.read_text())
    s['period']['start_date'] = start
    s['monthly_contribution_cny'] = str(3000 + budget)
    s['scenario_id'] += f'_{key}_budget{budget}_start{start}_fee{fee}_rsi{rsi}'
    s['allocation_profiles'][0]['id'] = key
    w = s['allocation_profiles'][0]['weights']
    dividend = D(0) if key == 'A' else D('.10') if key in {'dividend10', 'F', 'G1', 'G12', 'G123'} else D('.05')
    w['dividend'] = str(dividend)
    for sleeve, inside in zip(['sp500', 'csi300', 'us_growth', 'sp100', 'ftse100'], ['.3','.2','.3','.1','.1']):
        w[sleeve] = str((D('.6') - dividend) * D(inside))
    e = s['allocation_engine']
    if key in {'C', 'satellite0'}:
        e['satellite_multiplier'] = '.5' if key == 'C' else '0'
    if key == 'D':
        e['satellite_mode'] = 'rsi_low_trend'
        e['signal_parameters'] = {'rsi_low': rsi}
    if key in {'E', 'F', 'G1', 'G12', 'G123'}:
        e['satellite_mode'] = 'trough_recovery'
        e['satellite_multiplier'] = '.5'
        e['signal_parameters'] = {
            'trough_window_days': 60,
            'trough_rsi_low': 35.0,
            'trough_drawdown_min': 0.15,
            'recovery_rebound_min': 0.03,
            'tranche_weight': '0.06' if key in {'G1', 'G12', 'G123'} else '0.05',
            'max_satellite_weight': '0.10',
            'baseline_satellite_weight': '0.00',
            'released_weight_destination': 'wallet_cash' if key == 'E' else 'bond',
            'conviction_ranking': key in {'G1', 'G12', 'G123'},
            'top_k_satellites': 2,
            'enable_erp_gate': key in {'G12', 'G123'},
            'enable_gold_regime': key in {'G12', 'G123'},
            'enable_trailing_profit_lock': key == 'G123',
            'profit_lock_runup_threshold': 0.18,
            'profit_lock_pullback_threshold': 0.03,
        }
        if key in {'F', 'G1', 'G12', 'G123'}:
            e['allocation_priority'] = 'waterfall'
    for route in s['routes']:
        route['purchase_fee_rate'] = fee
    return s


def benchmark(budget):
    s = json.loads(BENCH.read_text())
    s['period'] = {'start_date':'2024-01-01', 'end_date':'2026-09-30'}
    s['monthly_contribution_cny'] = str(3000 + budget)
    s['scenario_id'] += f'_6211_matched_{budget}'
    return s


def compact(result, s):
    p = result['profile_results'][0]
    rows = p['valuation_result']['daily_valuations']
    core = set(s.get('allocation_engine', {}).get('core_sleeves', []))
    routes = {r['fund_code']:r['sleeve'] for r in s['routes']}
    def weight(row, sleeves):
        return sum(D(v['market_value_cny']) for v in row['positions']
                   if routes[v['fund_code']] in sleeves) / D(row['total_value_cny']) * 100
    individual_max = max((D(v['market_value_cny']) / D(row['total_value_cny']) * 100
                          for row in rows for v in row['positions']), default=D(0))
    summary = p['subscription_result']['summary']
    if D(summary['cash_reconciliation_difference_cny']) != 0:
        raise AssertionError('cash reconciliation failed')
    expected = len(p['monthly_reconciliation']) * D(s['monthly_contribution_cny'])
    if D(p['metrics']['contributions_cny']) != expected:
        raise AssertionError('external cashflow mismatch')
    if any(row['planned_date'] > p['metrics']['end_date'] for row in p['monthly_reconciliation']):
        raise AssertionError('terminal valuation predates external contribution')
    end = rows[-1]
    return {'metrics': p['metrics'], 'scenario_sha256': result['scenario_sha256'],
            'result_sha256':sha(result), 'final_core_weight_pct': float(weight(end, core)) if core else None,
            'final_satellite_weight_pct': float(weight(end, set(SATELLITES))) if core else None,
            'maximum_single_fund_weight_pct':float(individual_max),
            'concentration_review':individual_max > 25,
            'final_available_cash_cny':end['available_cash_cny'],
            'cash_reconciliation_difference_cny':summary['cash_reconciliation_difference_cny'],
            'unfilled_issues': sum(len(row['issues']) for row in p['monthly_reconciliation']),
            'pending_subscriptions':summary['pending_subscriptions'],
            'signal_eligible_fund_months':sum(sum(e['eligible'] for e in row['evaluations'])
                                             for row in p['monthly_signal_ledger'])}


def matched(left, right):
    lp, rp = left['profile_results'][0], right['profile_results'][0]
    a = {r['visible_date']:r['total_value_cny'] for r in lp['valuation_result']['daily_valuations']}
    b = {r['visible_date']:r['total_value_cny'] for r in rp['valuation_result']['daily_valuations']}
    dates = sorted(a.keys() & b.keys())
    fa = [(r['planned_date'], r['contribution_cny']) for r in lp['monthly_reconciliation']]
    fb = [(r['planned_date'], r['contribution_cny']) for r in rp['monthly_reconciliation']]
    if fa != fb:
        raise AssertionError('cannot compare unmatched external cashflows')
    origin = (date.fromisoformat(fa[0][0]) - timedelta(days=1)).isoformat()
    def periods(values):
        prior, value, rows = origin, '0', []
        for day in dates:
            start, end = date.fromisoformat(prior), date.fromisoformat(day)
            flows = [{'amount_cny':amount, 'remaining_weight':str(D((end-date.fromisoformat(when)).days)/D((end-start).days))}
                     for when, amount in fa if prior < when <= day]
            rows.append({'period_start':prior, 'period_end':day, 'start_value_cny':value,
                         'end_value_cny':values[day], 'external_flows':flows})
            prior, value = day, values[day]
        return rows
    return compare_cashflow_matched_paths(periods(a), periods(b))


def stress(s):
    weights = {k:D(v) for k,v in s['allocation_profiles'][0]['weights'].items()}
    multiplier = D(s['allocation_engine'].get('satellite_multiplier', '1'))
    for k in SATELLITES:
        weights[k] *= multiplier
    weights['cash'] = 1 - sum(weights.values())
    stresses = []
    for name in ['equity_correction','technology_crash','stocks_and_bonds_fall_together','slow_equity_bear']:
        returns = {k:D(0) for k in weights}
        for k in weights:
            if k in {'bond','gold','cash'}:
                continue
            if name == 'equity_correction':
                returns[k] = D('-.25') if k in SATELLITES else D('-.20') if k=='us_growth' else D('-.15')
            elif name == 'technology_crash':
                returns[k] = D('-.50') if k in SATELLITES else D('-.45') if k=='us_growth' else D('-.10') if k=='csi300' else D('-.15')
            elif name == 'stocks_and_bonds_fall_together':
                returns[k] = D('-.20')
            else:
                returns[k] = D('-.10') if k in SATELLITES else D('-.08') if k=='us_growth' else D('-.06')
        returns['bond'] = D('-.07') if name=='stocks_and_bonds_fall_together' else D('.005') if name=='slow_equity_bear' else D('.01') if name=='equity_correction' else D(0)
        returns['gold'] = D('-.10') if name=='stocks_and_bonds_fall_together' else D('.005') if name=='slow_equity_bear' else D('.10') if name=='technology_crash' else D('.02')
        stresses.append({'scenario_id':name, 'steps':[{'step_id':str(n), 'sleeve_returns':{k:str(v) for k,v in returns.items()}}
                                                    for n in range(5 if name=='slow_equity_bear' else 1)]})
    return run_portfolio_stress(base_scenario={'allocation_profiles':[{'weights':{k:str(v) for k,v in weights.items()}}]},
        stress_spec={'schema_version':1,'stress_id':'6211_frozen_synthetic_v0_2_0','probability_model':'none_non_probabilistic_scenarios',
                     'target_drawdown_pct':'15','stress_limit_drawdown_pct':'20','scenarios':stresses})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    spec_bytes = SPEC_PATH.read_bytes()
    spec = json.loads(spec_bytes)
    (OUT/'strategy_spec.json').write_bytes(spec_bytes)
    catalog = {'main':{}, 'budgets':{}, 'rolling':{}, 'sensitivity':{}, 'stress':{}}
    with TemporaryDirectory(prefix='6211-offline-') as temp:
        snapshot = Path(temp)/'nav.sqlite3'
        source = sqlite3.connect(f'file:{ROOT / "data/private/invest_agent.sqlite3"}?mode=ro',uri=True)
        destination = sqlite3.connect(snapshot)
        source.backup(destination)
        source.close(); destination.close()
        catalog['database_snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        def run(name, s, *, benchmark_spec=False, archive=False):
            raw = (ROOT/'strategies/specs/dca_baseline_v1_5.json').read_bytes() if benchmark_spec else spec_bytes
            result = run_local_dca_research(database=snapshot,scenario=s,strategy_spec=json.loads(raw),strategy_spec_bytes=raw)
            c = compact(result,s)
            if archive:
                (OUT/f'{name}.json').write_text(json.dumps({'scenario':s,'result':result},ensure_ascii=False,indent=2)+'\n')
            print(name, f"XIRR={c['metrics']['xirr_pct']:.3f} DD={c['metrics']['maximum_drawdown_pct']:.3f}", flush=True)
            return result,c
        main_results = {}
        for key in ['A','B','C','D','E']:
            result, c = run(key,candidate(key),archive=True)
            catalog['main'][key]=c
            main_results[key]=result
            catalog['stress'][key]=stress(candidate(key))
        matched_bench, c = run('matched_631_5000',benchmark(2000),benchmark_spec=True,archive=True)
        catalog['main']['matched_631_5000']=c
        _, c = run('fixed_631_3000',benchmark(0),benchmark_spec=True,archive=True)
        catalog['main']['fixed_631_3000']=c
        catalog['attribution']={key:matched(result,matched_bench) for key,result in main_results.items()}
        catalog['rsi_increment_attribution']=matched(main_results['D'],main_results['B'])
        catalog['trough_recovery_increment_attribution']=matched(main_results['E'],main_results['B'])
        for budget in [0,1000]:
            _, b = run(f'benchmark_budget{budget}',benchmark(budget),benchmark_spec=True)
            catalog['budgets'][f'benchmark_{budget}']=b
            for key in ['A','B','C','D','E']:
                _, c=run(f'{key}_budget{budget}',candidate(key,budget=budget))
                catalog['budgets'][f'{key}_{budget}']=c
        for start in ['2024-07-01','2025-01-01','2025-07-01','2026-01-01']:
            catalog['rolling'][start]={}
            for key in ['A','B','C','D','E']:
                _, c=run(f'{key}_start{start}',candidate(key,start=start))
                catalog['rolling'][start][key]=c
        for key in ['dividend10','satellite0']:
            _, c=run(key,candidate(key))
            catalog['sensitivity'][key]=c
        for fee in ['0','.015']:
            for key in ['B','C','D','E']:
                _, c=run(f'{key}_fee{fee}',candidate(key,fee=fee))
                catalog['sensitivity'][f'{key}_fee{fee}']=c
        for rsi in [25,35]:
            _, c=run(f'D_rsi{rsi}',candidate('D',rsi=rsi))
            catalog['sensitivity'][f'D_rsi{rsi}']=c
        for key in ['B','C']:
            s=candidate(key)
            s['calendar_day']=15
            s['scenario_id']+='_review_day15'
            _,c=run(f'{key}_review_day15',s)
            catalog['sensitivity'][f'{key}_review_day15']=c
            s=candidate(key)
            s['subscription_assumption'].pop('unlimited_fund_tranche_calendar_days')
            s['scenario_id']+='_single_tranche'
            _,c=run(f'{key}_single_tranche',s)
            catalog['sensitivity'][f'{key}_single_tranche']=c
        repeat=run_local_dca_research(database=snapshot,scenario=candidate('D'),strategy_spec=spec,strategy_spec_bytes=spec_bytes)
        if sha(repeat)!=sha(main_results['D']):
            raise AssertionError('deterministic replay mismatch')
        catalog['deterministic_replay']='passed_D_byte_equivalent'
    catalog['labels']=LABELS
    files=[BASE,SPEC_PATH,ROOT/'strategies/proposals/cashflow_6211_protocol_v0_2_0.md',
           ROOT/'invest_agent/backtest/local_research.py',ROOT/'invest_agent/strategies/cashflow_6211.py',Path(__file__)]
    catalog['artifact_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    catalog['mode']='research_only'
    catalog['external_side_effects']=False
    catalog['redemption_tested']=False
    (OUT/'summary.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    print('saved',OUT/'summary.json',flush=True)


if __name__ == '__main__':
    main()
