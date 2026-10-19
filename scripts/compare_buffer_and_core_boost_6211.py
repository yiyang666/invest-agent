"""Backtest ablation study for:
1) Wallet Liquidity Buffer (活期宝常设机动缓冲垫 ¥1,200 vs 月末全买光 Buffer=0)
2) Core Valuation Dynamic Adjustment (传统手动改定投 vs 方案C：定投底座不动 + 机动盘核心大底暴击增强)
3) Merged Scheme 4 & 5 Profit-Taking + Exact Channel Fees
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal as D
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest_agent.metrics.fund import load_nav_series
from invest_agent.metrics.rsi import simple_rsi
from invest_agent.strategies.cashflow_6211 import satellite_trough_recovery_targets


def calc_xirr(flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    days = [(d - t0).days / 365.0 for d, _ in flows]
    amts = [a for _, a in flows]
    r = 0.10
    for _ in range(100):
        f_val = sum(a / ((1.0 + r) ** t) for a, t in zip(amts, days))
        df_val = sum(-t * a / ((1.0 + r) ** (t + 1.0)) for a, t in zip(amts, days))
        if abs(df_val) < 1e-12:
            break
        nr = max(-0.95, r - f_val / df_val)
        if abs(nr - r) < 1e-9:
            return nr
        r = nr
    return r


@dataclass
class Lot:
    fund_code: str
    confirm_date: date
    shares: float
    cost_nav: float


def load_fee_rules() -> dict[str, dict]:
    p = ROOT / "config/fund_fee_rules_v1.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return {item["fund_code"]: item for item in data["funds"]}


def redemption_fee_rate(fee_rules: dict[str, dict], fund_code: str, holding_days: int) -> float:
    rule = fee_rules.get(fund_code)
    if not rule or not rule.get("redemption_fee_tiers"):
        return 0.015 if holding_days < 7 else (0.005 if holding_days < 365 else 0.0)
    for tier in rule["redemption_fee_tiers"]:
        lo = int(tier["minimum_holding_days_inclusive"])
        hi = tier["maximum_holding_days_exclusive"]
        if holding_days >= lo and (hi is None or holding_days < int(hi)):
            return float(tier["rate"])
    return float(rule["redemption_fee_tiers"][-1]["rate"])


def purchase_fee_rate(fee_rules: dict[str, dict], fund_code: str) -> float:
    rule = fee_rules.get(fund_code)
    if rule and rule.get("discounted_first_tier_purchase_fee_rate") is not None:
        return float(rule["discounted_first_tier_purchase_fee_rate"])
    return 0.0012


def run_ablation(
    *,
    label: str,
    wallet_buffer_cny: float = 0.0,
    core_mode: str = "fixed",  # 'fixed' | 'manual_pe_scale' | 'managed_deep_value_boost'
    wallet_annual_yield: float = 0.016,
) -> dict:
    db = ROOT / "data/private/invest_agent.sqlite3"
    base_scen = json.loads((ROOT / "config/backtest_cashflow_6211_v0_2_0.json").read_text(encoding="utf-8"))
    fee_rules = load_fee_rules()

    start_date = date(2024, 1, 1)
    end_date = date(2026, 9, 30)

    sleeve_to_fund = {r["sleeve"]: r["fund_code"] for r in base_scen["routes"]}
    core_sleeves = ["sp500", "csi300", "us_growth", "sp100", "ftse100", "dividend"]
    sat_sleeves = ["sat_technology", "sat_semiconductor", "sat_robotics"]

    strategic_weights = {
        "sp500": D("0.15"),
        "csi300": D("0.10"),
        "us_growth": D("0.15"),
        "sp100": D("0.05"),
        "ftse100": D("0.05"),
        "dividend": D("0.10"),
        "bond": D("0.20"),
        "gold": D("0.10"),
        "sat_technology": D("0.03333334"),
        "sat_semiconductor": D("0.03333333"),
        "sat_robotics": D("0.03333333"),
    }

    unit_series = {}
    acc_series = {}
    acc_with_rsi = {}
    for sleeve, code in sleeve_to_fund.items():
        u_pts = load_nav_series(db, fund_code=code, provider_id="akshare_eastmoney", as_of=end_date, nav_field="unit_nav")
        a_pts = load_nav_series(db, fund_code=code, provider_id="akshare_eastmoney", as_of=end_date, nav_field="accumulated_nav")
        unit_series[code] = u_pts
        acc_series[sleeve] = a_pts
        vals = [float(p.nav) for p in a_pts]
        rsis = simple_rsi(vals, period=13)
        acc_with_rsi[sleeve] = [(p.nav_date, float(p.nav), r) for p, r in zip(a_pts, rsis)]

    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        erp_pe_history = [
            (str(d), float(v))
            for d, v in conn.execute(
                "SELECT observation_date, value FROM market_numeric_observations "
                "WHERE series_id='index_valuation:000300:pe_ttm' ORDER BY observation_date"
            ).fetchall()
        ]
        erp_y10_history = [
            (str(d), float(v))
            for d, v in conn.execute(
                "SELECT observation_date, value FROM market_numeric_observations "
                "WHERE series_id='market_series:bond_yield:CN_10Y' ORDER BY observation_date"
            ).fetchall()
        ]

    all_trading_days = sorted(
        {
            p.nav_date
            for code, pts in unit_series.items()
            for p in pts
            if start_date <= p.nav_date <= end_date
        }
    )

    def latest_nav(code: str, d: date) -> float:
        pts = unit_series[code]
        dates = [p.nav_date for p in pts]
        idx = bisect_left(dates, d)
        if idx < len(dates) and dates[idx] == d:
            return float(pts[idx].nav)
        if idx > 0:
            return float(pts[idx - 1].nav)
        return float(pts[0].nav)

    def sleeve_window_stats(sleeve: str, d: date, window_days: int = 60) -> dict:
        pts = [x for x in acc_with_rsi[sleeve] if x[0] < d]
        if len(pts) < 20:
            return {"ready": False}
        win = pts[-window_days:]
        navs = [x[1] for x in win]
        rsis = [x[2] for x in win if x[2] is not None]
        cur = navs[-1]
        peak = max(navs)
        trough = min(navs)
        peak_idx = navs.index(peak)
        post_peak = navs[peak_idx:]
        trough_after_peak = min(post_peak)
        dd_from_peak = (cur / peak - 1.0) if peak > 0 else 0.0
        max_dd = (trough_after_peak / peak - 1.0) if peak > 0 else 0.0
        rebound = (cur / trough_after_peak - 1.0) if trough_after_peak > 0 else 0.0
        runup = (cur / trough - 1.0) if trough > 0 else 0.0
        cur_rsi = float(rsis[-1]) if rsis else 50.0
        min_rsi = float(min(rsis)) if rsis else 50.0
        return {
            "ready": True,
            "dd_from_peak": dd_from_peak,
            "max_dd": max_dd,
            "rebound": rebound,
            "runup": runup,
            "cur_rsi": cur_rsi,
            "min_rsi": min_rsi,
        }

    lots_by_sleeve: dict[str, list[Lot]] = {s: [] for s in strategic_weights}
    wallet_managed_cash = 0.0
    wallet_core_cash = 0.0
    total_purchase_fees = 0.0
    total_redemption_fees = 0.0
    intramonth_sat_triggers = 0
    core_boost_triggers = 0
    core_boost_total_cny = 0.0
    profit_lock_redemptions = 0
    bond_switch_redemptions = 0
    manual_plan_edits = 0
    cashflows: list[tuple[date, float]] = []
    daily_totals: list[tuple[date, float]] = []

    def buy_sleeve(sleeve: str, d: date, gross_cny: float) -> None:
        nonlocal total_purchase_fees
        if gross_cny < 1.0:
            return
        code = sleeve_to_fund[sleeve]
        fee_r = purchase_fee_rate(fee_rules, code)
        fee = gross_cny * fee_r
        net = gross_cny - fee
        nav = latest_nav(code, d)
        shares = math.floor((net / nav) * 100.0) / 100.0
        total_purchase_fees += fee
        lots_by_sleeve[sleeve].append(Lot(fund_code=code, confirm_date=d + timedelta(days=1), shares=shares, cost_nav=nav))

    def redeem_lots(
        sleeve: str,
        d: date,
        target_cny: float,
        *,
        max_fee_rate: float = 0.0005,
        min_hold_days: int = 7,
        min_net_gain_pct: float | None = None,
    ) -> float:
        nonlocal total_redemption_fees
        code = sleeve_to_fund[sleeve]
        nav = latest_nav(code, d)
        remaining_lots: list[Lot] = []
        net_proceeds = 0.0
        for lot in lots_by_sleeve[sleeve]:
            hold_days = (d - lot.confirm_date).days
            fee_r = redemption_fee_rate(fee_rules, code, max(0, hold_days))
            gross_val = lot.shares * nav
            net_gain = (nav * (1.0 - fee_r)) / lot.cost_nav - 1.0
            eligible = (
                hold_days >= min_hold_days
                and fee_r <= max_fee_rate
                and (min_net_gain_pct is None or net_gain >= min_net_gain_pct)
            )
            if eligible and net_proceeds < target_cny and gross_val > 0:
                need_ratio = min(1.0, (target_cny - net_proceeds) / (gross_val * (1.0 - fee_r)))
                redeem_shares = round(lot.shares * need_ratio, 2)
                if redeem_shares > 0:
                    g = redeem_shares * nav
                    fee = g * fee_r
                    total_redemption_fees += fee
                    net_proceeds += g - fee
                    left_shares = round(lot.shares - redeem_shares, 2)
                    if left_shares > 0.001:
                        remaining_lots.append(Lot(fund_code=code, confirm_date=lot.confirm_date, shares=left_shares, cost_nav=lot.cost_nav))
                else:
                    remaining_lots.append(lot)
            else:
                remaining_lots.append(lot)
        lots_by_sleeve[sleeve] = remaining_lots
        return net_proceeds

    def current_erp(d: date) -> tuple[float | None, float | None]:
        vis_pe = [v for dt_str, v in erp_pe_history if dt_str < d.isoformat()]
        if not vis_pe or vis_pe[-1] <= 0:
            return None, None
        pe = vis_pe[-1]
        vis_y10 = [v for dt_str, v in erp_y10_history if dt_str < d.isoformat()]
        y10 = vis_y10[-1] if vis_y10 else 2.35
        return 100.0 / pe - y10, pe

    def eval_signal_on(d: date) -> dict:
        erp_val, _ = current_erp(d)
        return satellite_trough_recovery_targets(
            review_date=d,
            strategic_weights=strategic_weights,
            satellite_series={s: acc_series[s] for s in sat_sleeves},
            gold_series=acc_series["gold"],
            erp_pct=erp_val,
            trough_window_days=60,
            trough_rsi_low=35.0,
            trough_drawdown_min=0.15,
            recovery_rebound_min=0.03,
            tranche_weight="0.06",
            max_satellite_weight="0.10",
            baseline_satellite_weight="0.00",
            released_weight_destination="bond",
            conviction_ranking=True,
            top_k_satellites=2,
            enable_erp_gate=True,
            enable_gold_regime=True,
            enable_trailing_profit_lock=True,
        )

    current_month: tuple[int, int] | None = None
    month_core_tranches_done: set[int] = set()
    month_swept = False
    month_sat_triggered_sleeves: set[str] = set()
    month_core_boost_done = False
    last_cn_boost_date: date | None = None
    last_us_boost_date: date | None = None

    for d in all_trading_days:
        ym = (d.year, d.month)
        if ym != current_month:
            current_month = ym
            month_core_tranches_done = set()
            month_swept = False
            month_sat_triggered_sleeves = set()
            month_core_boost_done = False
            wallet_core_cash += 3000.0
            wallet_managed_cash += 2000.0
            cashflows.append((d, -5000.0))

        # Daily wallet money-market interest on idle cash (~1.6% annualized)
        if wallet_managed_cash > 0:
            wallet_managed_cash *= (1.0 + wallet_annual_yield / 252.0)
        if wallet_core_cash > 0:
            wallet_core_cash *= (1.0 + wallet_annual_yield / 252.0)

        # Core DCA tranches on [5, 15, 25]
        for t_day in (5, 15, 25):
            if d.day >= t_day and t_day not in month_core_tranches_done:
                month_core_tranches_done.add(t_day)
                tranche_core = 1000.0
                erp_val, _ = current_erp(d)

                if core_mode == "manual_pe_scale":
                    if t_day == 5:
                        manual_plan_edits += 6
                    for s in core_sleeves:
                        w_inside = float(strategic_weights[s]) / 0.60
                        base_amt = tranche_core * w_inside
                        st = sleeve_window_stats(s, d)
                        mult = 1.0
                        if s in {"sp500", "us_growth", "sp100"}:
                            if st.get("ready") and (st["cur_rsi"] > 62 or st["runup"] > 0.08):
                                mult = 0.65
                            elif st.get("ready") and st["max_dd"] <= -0.10:
                                mult = 1.30
                        elif s in {"csi300", "dividend"}:
                            if erp_val is not None and erp_val >= 6.10:
                                mult = 1.35
                            elif erp_val is not None and erp_val <= 5.30:
                                mult = 0.70
                        actual_amt = base_amt * mult
                        if actual_amt <= wallet_core_cash:
                            wallet_core_cash -= actual_amt
                        else:
                            diff = actual_amt - wallet_core_cash
                            wallet_core_cash = 0.0
                            wallet_managed_cash = max(0.0, wallet_managed_cash - diff)
                        buy_sleeve(s, d, actual_amt)
                    if t_day == 25 and wallet_core_cash > 5.0:
                        wallet_managed_cash += wallet_core_cash
                        wallet_core_cash = 0.0
                else:
                    spend = min(wallet_core_cash, tranche_core)
                    wallet_core_cash = max(0.0, wallet_core_cash - spend)
                    for s in core_sleeves:
                        w_inside = float(strategic_weights[s]) / 0.60
                        buy_sleeve(s, d, spend * w_inside)

        # Intra-month evaluation of Managed Pool (40%)
        sig = eval_signal_on(d)
        eff_w = {k: float(v) for k, v in sig["effective_target_weights"].items()}
        pos_vals = {
            s: sum(lot.shares * latest_nav(sleeve_to_fund[s], d) for lot in lots_by_sleeve[s])
            for s in strategic_weights
        }
        total_val = sum(pos_vals.values()) + wallet_core_cash + wallet_managed_cash

        # Scheme 4 & 5 Merged Two-Tier Profit Lock on Satellite sleeves
        for s in sat_sleeves:
            if pos_vals[s] <= 200.0:
                continue
            st = sleeve_window_stats(s, d, window_days=60)
            if not st.get("ready"):
                continue
            # Tier 1 (Scheme 5): 0%/<=0.05% fee tier (held >=30d), 60d runup >= 18% and RSI >= 70
            if st["runup"] >= 0.18 and st["cur_rsi"] >= 70.0:
                proceeds = redeem_lots(s, d, pos_vals[s] * 0.50, max_fee_rate=0.0005, min_hold_days=30, min_net_gain_pct=0.10)
                if proceeds > 10.0:
                    profit_lock_redemptions += 1
                    wallet_managed_cash += proceeds
                    continue
            # Tier 2 (Scheme 4): extreme surge >= 25% and RSI >= 75 even in 0.50% fee tier
            if st["runup"] >= 0.25 and st["cur_rsi"] >= 75.0:
                proceeds = redeem_lots(s, d, pos_vals[s] * 0.50, max_fee_rate=0.005, min_hold_days=7, min_net_gain_pct=0.14)
                if proceeds > 10.0:
                    profit_lock_redemptions += 1
                    wallet_managed_cash += proceeds

        # Priority 1: Check Satellite Stage-2 entry triggers (at most 1 tranche per newly triggered satellite sleeve per month, ¥1000 per sleeve)
        new_sats = [
            s
            for s in sat_sleeves
            if float(eff_w.get(s, 0.0)) > 0.0 and s not in month_sat_triggered_sleeves
        ]
        if new_sats:
            sat_cap_room = max(0.0, total_val * 0.10 - sum(pos_vals[s] for s in sat_sleeves))
            desired = min(sat_cap_room, 1000.0 * len(new_sats))
            if desired >= 200.0:
                avail = min(wallet_managed_cash, desired)
                extra_from_bond = 0.0
                if desired > avail + 100.0 and pos_vals["bond"] >= 200.0:
                    extra_from_bond = redeem_lots("bond", d, desired - avail, max_fee_rate=0.0, min_hold_days=30)
                    if extra_from_bond > 0:
                        bond_switch_redemptions += 1
                deploy_total = avail + extra_from_bond
                if deploy_total >= 100.0:
                    intramonth_sat_triggers += 1
                    wallet_managed_cash -= avail
                    for s in new_sats:
                        month_sat_triggered_sleeves.add(s)
                        buy_sleeve(s, d, deploy_total / len(new_sats))

        # Priority 1.5 (Scheme C): Managed-Pool Core Deep-Value Boost (定投底座不动 + 机动盘承担核心大底暴击增强, 60d cooldown per market)
        if core_mode == "managed_deep_value_boost" and not month_core_boost_done:
            erp_val, _ = current_erp(d)
            csi_st = sleeve_window_stats("csi300", d, window_days=60)
            sp_st = sleeve_window_stats("sp500", d, window_days=60)

            boost_targets: list[tuple[str, float]] = []
            boost_market = ""
            # 1) Domestic extreme deep value + trough stabilization (ERP >= 6.15% & drawdown >= 7.5% & rebound 2%~8%)
            if (
                (last_cn_boost_date is None or (d - last_cn_boost_date).days >= 60)
                and erp_val is not None
                and erp_val >= 6.15
                and csi_st.get("ready")
                and csi_st["max_dd"] <= -0.075
                and csi_st["min_rsi"] <= 36.0
                and 0.02 <= csi_st["rebound"] <= 0.08
            ):
                boost_targets.append(("csi300", 600.0))
                boost_targets.append(("dividend", 400.0))
                boost_market = "cn"

            # 2) Global US core rare correction trough stabilization (60d drawdown >= 9.5% & min RSI <= 34 & rebound 2%~8%)
            elif (
                (last_us_boost_date is None or (d - last_us_boost_date).days >= 60)
                and sp_st.get("ready")
                and sp_st["max_dd"] <= -0.095
                and sp_st["min_rsi"] <= 34.0
                and 0.02 <= sp_st["rebound"] <= 0.08
            ):
                boost_targets.append(("sp500", 500.0))
                boost_targets.append(("us_growth", 500.0))
                boost_market = "us"

            if boost_targets:
                need_boost = sum(amt for _, amt in boost_targets)
                avail_w = min(wallet_managed_cash, need_boost)
                from_bond = 0.0
                if avail_w < need_boost and pos_vals["bond"] >= 200.0:
                    from_bond = redeem_lots("bond", d, need_boost - avail_w, max_fee_rate=0.0, min_hold_days=30)
                    if from_bond > 0:
                        bond_switch_redemptions += 1
                total_boost = avail_w + from_bond
                if total_boost >= 200.0:
                    wallet_managed_cash -= avail_w
                    month_core_boost_done = True
                    core_boost_triggers += 1
                    core_boost_total_cny += total_boost
                    if boost_market == "cn":
                        last_cn_boost_date = d
                    elif boost_market == "us":
                        last_us_boost_date = d
                    for slv, amt in boost_targets:
                        buy_sleeve(slv, d, total_boost * (amt / need_boost))

        # Month-end single sweep on or after 25th (respecting wallet_buffer_cny!)
        if d.day >= 25 and not month_swept:
            month_swept = True
            g_gap = max(0.0, total_val * eff_w["gold"] - pos_vals["gold"])
            if g_gap >= 100.0 and float(eff_w["gold"]) > 0.0 and wallet_managed_cash > 200.0:
                g_amt = min(wallet_managed_cash * 0.30, g_gap, 550.0)
                if g_amt >= 100.0:
                    wallet_managed_cash -= g_amt
                    buy_sleeve("gold", d, g_amt)
            # Only sweep excess above wallet_buffer_cny into 006932 short bond!
            excess_for_bond = wallet_managed_cash - wallet_buffer_cny
            if excess_for_bond >= 100.0:
                buy_sleeve("bond", d, excess_for_bond)
                wallet_managed_cash -= excess_for_bond

        eod_val = wallet_core_cash + wallet_managed_cash + sum(
            lot.shares * latest_nav(sleeve_to_fund[s], d)
            for s in strategic_weights
            for lot in lots_by_sleeve[s]
        )
        daily_totals.append((d, eod_val))

    final_date, final_val = daily_totals[-1]
    xirr_flows = list(cashflows) + [(final_date, final_val)]
    xirr_val = calc_xirr(xirr_flows) * 100.0

    peak = 0.0
    max_dd = 0.0
    daily_rets = []
    flows_by_day = {}
    for d, amt in cashflows:
        flows_by_day[d] = flows_by_day.get(d, 0.0) + (-amt)

    twr = 1.0
    for i, (d, val) in enumerate(daily_totals):
        if i > 0:
            prev_val = daily_totals[i - 1][1]
            ext = flows_by_day.get(d, 0.0)
            denom = prev_val + ext
            r = (val - denom) / denom if denom > 0 else 0.0
            daily_rets.append(r)
            twr *= (1.0 + r)
        peak = max(peak, twr)
        dd = (twr / peak - 1.0) * 100.0
        max_dd = min(max_dd, dd)

    mean_r = sum(daily_rets) / len(daily_rets)
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1))
    vol_pct = std_r * math.sqrt(252) * 100.0
    sharpe = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0.0
    ann_twr = (twr ** (252.0 / len(daily_rets)) - 1.0) * 100.0
    calmar = ann_twr / abs(max_dd) if max_dd < 0 else 0.0

    return {
        "label": label,
        "final_value_cny": round(final_val, 2),
        "xirr_pct": round(xirr_val, 3),
        "twr_cumulative_pct": round((twr - 1.0) * 100.0, 2),
        "annualized_vol_pct": round(vol_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "total_purchase_fees_cny": round(total_purchase_fees, 2),
        "total_redemption_fees_cny": round(total_redemption_fees, 2),
        "bond_switch_redemptions": bond_switch_redemptions,
        "core_boost_triggers": core_boost_triggers,
        "core_boost_total_cny": round(core_boost_total_cny, 2),
        "manual_plan_edits": manual_plan_edits,
    }


def main() -> None:
    configs = [
        {
            "label": "1. 基准 v1.0 (月末全买光短债 Buffer=0 + 固定定投)",
            "wallet_buffer_cny": 0.0,
            "core_mode": "fixed",
        },
        {
            "label": "2. +活期宝缓冲垫 (Buffer=¥1,200 常留钱包 + 固定定投)",
            "wallet_buffer_cny": 1200.0,
            "core_mode": "fixed",
        },
        {
            "label": "3. 对照：传统手动改定投 (每月按PE/RSI改计划 Buffer=¥1,200)",
            "wallet_buffer_cny": 1200.0,
            "core_mode": "manual_pe_scale",
        },
        {
            "label": "4. +核心大底暴击增强 (方案C: 定投不动+机动盘抄底核心 Buffer=0)",
            "wallet_buffer_cny": 0.0,
            "core_mode": "managed_deep_value_boost",
        },
        {
            "label": "5. ★终极收敛 v1.1 (Buffer=¥1,200 + 机动盘核心大底暴击 + 二合一止盈)",
            "wallet_buffer_cny": 1200.0,
            "core_mode": "managed_deep_value_boost",
        },
    ]
    print(
        f"{'回测方案':<58} | {'XIRR%':>7} | {'TWR%':>7} | {'MaxDD%':>7} | {'Sharpe':>6} | {'Calmar':>6} | {'赎债次数':>6} | {'大底加码':>6} | {'改计划数':>6} | {'期末终值(元)':>11}"
    )
    print("-" * 150)
    for cfg in configs:
        res = run_ablation(**cfg)
        print(
            f"{res['label']:<58} | {res['xirr_pct']:>7.3f} | {res['twr_cumulative_pct']:>7.2f} | "
            f"{res['max_drawdown_pct']:>7.2f} | {res['sharpe']:>6.3f} | {res['calmar']:>6.3f} | "
            f"{res['bond_switch_redemptions']:>6d} | {res['core_boost_triggers']:>6d} | "
            f"{res['manual_plan_edits']:>6d} | {res['final_value_cny']:>11.2f}"
        )


if __name__ == "__main__":
    main()
