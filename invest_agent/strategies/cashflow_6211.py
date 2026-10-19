"""Research-only broker core DCA plus cash-constrained managed sleeves.

The broker budget is a scenario input, never a prediction of actual debits.
There are no broker clients or order submission capabilities here.
"""
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Mapping, Sequence

from invest_agent.metrics.fund import NavPoint
from invest_agent.metrics.rsi import simple_rsi
from .dca import MonthlyAllocationPlan, SleeveAllocation

CENT = Decimal('0.01')
ZERO = Decimal('0')


def satellite_rsi_targets(*, review_date: date, strategic_weights: Mapping[str, Decimal],
                          satellite_series: Mapping[str, Sequence[NavPoint]],
                          rsi_low: float = 30, average_period: int = 200,
                          maximum_staleness_days: int = 10) -> dict:
    """Use only NAV published under the next-observation visibility assumption.

    A low simple RSI AND a positive slow trend permit new money. No sale or
    retroactive change to existing positions occurs when eligibility is lost.
    Released targets remain wallet cash, not a secretly increased bond target.
    """
    if not 0 <= rsi_low <= 100 or average_period < 14 or maximum_staleness_days < 1:
        raise ValueError('invalid RSI signal parameters')
    weights = dict(strategic_weights)
    evaluations = []
    for sleeve, points in sorted(satellite_series.items()):
        if sleeve not in weights:
            raise ValueError('satellite sleeve is not in targets')
        # A NAV is only usable after the following NAV date, strictly before
        # review; same-day unpublished NAV must not participate in the signal.
        visible = [p for p, following in zip(points, points[1:])
                   if following.nav_date < review_date]
        eligible = False
        rsi = None
        reason = 'insufficient_or_stale_history'
        if (len(visible) >= average_period
                and (review_date - visible[-1].nav_date).days <= maximum_staleness_days):
            rsi = simple_rsi([float(p.nav) for p in visible[-14:]], 13)[-1]
            average = sum((p.nav for p in visible[-average_period:]), ZERO) / average_period
            eligible = rsi <= rsi_low and visible[-1].nav > average
            reason = 'low_rsi_positive_trend' if eligible else 'entry_condition_absent'
        weights[sleeve] = strategic_weights[sleeve] if eligible else ZERO
        evaluations.append({'sleeve': sleeve, 'eligible': eligible, 'reason': reason,
                            'rsi_13': rsi, 'effective_nav_date':
                            visible[-1].nav_date.isoformat() if visible else None,
                            'nav_batch_sha256': sorted({p.content_sha256 for p in visible[-average_period:]})})
    return {'review_date': review_date.isoformat(), 'evaluations': evaluations,
            'effective_target_weights': {k: str(v) for k, v in weights.items()},
            'released_weight_destination': 'wallet_cash', 'selling_allowed': False,
            'external_side_effects': False}


def satellite_trough_recovery_targets(
    *, review_date: date, strategic_weights: Mapping[str, Decimal],
    satellite_series: Mapping[str, Sequence[NavPoint]],
    trough_window_days: int = 60, trough_rsi_low: float = 35.0,
    trough_drawdown_min: float = 0.15, recovery_rebound_min: float = 0.03,
    momentum_confirm: bool = True, overbought_cutoff: float = 0.25,
    tranche_weight: Decimal = Decimal('0.05'),
    max_satellite_weight: Decimal = Decimal('0.10'),
    baseline_satellite_weight: Decimal = Decimal('0'),
    maximum_staleness_days: int = 10,
    released_weight_destination: str = 'wallet_cash',
    bond_sleeve: str = 'bond',
    gold_sleeve: str = 'gold',
    industry_crowding_by_sleeve: Mapping[str, float] | None = None,
    max_crowding_percentile: float = 75.0,
    conviction_ranking: bool = False,
    top_k_satellites: int = 2,
    enable_erp_gate: bool = False,
    erp_pct: float | None = None,
    enable_gold_regime: bool = False,
    gold_series: Sequence[NavPoint] | None = None,
    enable_trailing_profit_lock: bool = False,
    profit_lock_runup_threshold: float = 0.18,
    profit_lock_pullback_threshold: float = 0.03,
) -> dict:
    """Two-stage satellite signal with optional stepwise optimizations (v0.4):

    - Opt 1 (conviction_ranking): Computes deterministic composite odds_score (0..100)
      from drawdown depth (40%), RSI/crowding exhaustion (35%), and right-side recovery
      quality (25%), concentrating up to tranche_weight / max_satellite_weight on Top-K
      highest-conviction sleeves instead of clamping to static per-sleeve shares.
    - Opt 2 (enable_erp_gate & enable_gold_regime): Adjusts Stage-2 rebound threshold
      and satellite ceiling using point-in-time CSI 300 Equity Risk Premium (ERP), and
      modulates Gold target weight using its 200-day moving average trend & RSI13.
    - Opt 3 (enable_trailing_profit_lock): Blocks late-stage chasing when a sleeve has
      already rallied >= profit_lock_runup_threshold or started pulling back from a
      surge, locking released weight into the short-bond reservoir ('以债养战').
    """
    tranche_weight = Decimal(str(tranche_weight))
    max_satellite_weight = Decimal(str(max_satellite_weight))
    baseline_satellite_weight = Decimal(str(baseline_satellite_weight))
    if trough_window_days < 20 or not 0 <= trough_rsi_low <= 100 or recovery_rebound_min < 0:
        raise ValueError('invalid trough recovery parameters')
    if tranche_weight < 0 or max_satellite_weight < 0 or baseline_satellite_weight < 0:
        raise ValueError('weights must be non-negative')
    if max_satellite_weight > Decimal('0.20'):
        raise ValueError('satellite cap cannot exceed 0.20')
    if released_weight_destination not in {'wallet_cash', 'bond'}:
        raise ValueError('released_weight_destination must be wallet_cash or bond')

    effective_rebound_min = float(recovery_rebound_min)
    effective_max_sat = max_satellite_weight
    erp_regime = 'neutral'
    if enable_erp_gate and erp_pct is not None:
        if erp_pct >= 5.80:
            erp_regime = 'high_odds_equity_cheap'
            effective_rebound_min = max(0.02, float(recovery_rebound_min) - 0.01)
            effective_max_sat = min(Decimal('0.12'), max_satellite_weight + Decimal('0.02'))
        elif erp_pct <= 5.25:
            erp_regime = 'low_odds_equity_expensive'
            effective_rebound_min = float(recovery_rebound_min) + 0.01
            effective_max_sat = max(Decimal('0.05'), max_satellite_weight - Decimal('0.02'))

    weights = dict(strategic_weights)
    evaluations = []
    for sleeve, points in sorted(satellite_series.items()):
        if sleeve not in weights:
            raise ValueError('satellite sleeve is not in targets')
        # Only use strictly visible NAV before review date
        visible = [p for p, following in zip(points, points[1:])
                   if following.nav_date < review_date]
        eligible = False
        stage1_trough = False
        trailing_profit_lock = False
        reason = 'insufficient_or_stale_history'
        min_rsi = None
        current_rsi = None
        rebound_pct = None
        dd_from_peak = None
        odds_score = 0.0
        crowding_pct = (
            float(industry_crowding_by_sleeve[sleeve])
            if industry_crowding_by_sleeve and sleeve in industry_crowding_by_sleeve
            and industry_crowding_by_sleeve[sleeve] is not None
            else None
        )
        if (len(visible) >= trough_window_days + 14
                and (review_date - visible[-1].nav_date).days <= maximum_staleness_days):
            window = visible[-trough_window_days:]
            navs = [float(p.nav) for p in window]
            trough_nav = min(navs)
            peak_nav = max(navs)
            current_nav = float(visible[-1].nav)
            dd_from_peak = (peak_nav - trough_nav) / peak_nav if peak_nav > 0 else 0.0
            rebound_pct = (current_nav - trough_nav) / trough_nav if trough_nav > 0 else 0.0

            rsi_vals = simple_rsi([float(p.nav) for p in visible[-(trough_window_days + 14):]], 13)
            valid_rsis = [r for r in rsi_vals[-trough_window_days:] if r is not None]
            min_rsi = min(valid_rsis) if valid_rsis else 50.0
            current_rsi = valid_rsis[-1] if valid_rsis else 50.0

            # Deterministic composite odds & exhaustion score (0..100)
            dd_score = min(100.0, (dd_from_peak / 0.35) * 100.0)
            crowd_penalty = 0.30 * ((crowding_pct if crowding_pct is not None else 50.0) / 100.0)
            exh_score = min(100.0, max(0.0, (40.0 - min_rsi) / 35.0 * 100.0)) * (1.0 - crowd_penalty)
            if rebound_pct <= 0.12:
                rec_score = min(100.0, max(0.0, (rebound_pct / 0.10) * 100.0))
            else:
                rec_score = max(15.0, 100.0 - (rebound_pct - 0.12) * 500.0)
            odds_score = round(0.40 * dd_score + 0.35 * exh_score + 0.25 * rec_score, 2)

            is_trough = (min_rsi <= trough_rsi_low) or (dd_from_peak >= trough_drawdown_min)
            stage1_trough = bool(is_trough)
            is_rebound = rebound_pct >= effective_rebound_min
            effective_cutoff = min(overbought_cutoff, profit_lock_runup_threshold) if enable_trailing_profit_lock else overbought_cutoff
            not_overchased = (rebound_pct <= effective_cutoff) and (current_rsi <= 70.0)
            crowding_ok = crowding_pct is None or crowding_pct <= max_crowding_percentile
            if momentum_confirm and len(visible) >= 10:
                ma5 = sum(float(p.nav) for p in visible[-5:]) / 5.0
                ma10 = sum(float(p.nav) for p in visible[-10:]) / 10.0
                momentum_ok = (current_nav >= ma5) or (ma5 >= ma10) or (current_rsi >= min_rsi + 5.0)
            else:
                momentum_ok = True

            if enable_trailing_profit_lock and len(visible) >= 20:
                recent_20_high = max(float(p.nav) for p in visible[-20:])
                pullback_20 = (recent_20_high - current_nav) / recent_20_high if recent_20_high > 0 else 0.0
                if (rebound_pct >= profit_lock_runup_threshold) or (
                    rebound_pct >= 0.14 and pullback_20 >= profit_lock_pullback_threshold
                ):
                    trailing_profit_lock = True
                    not_overchased = False

            eligible = bool(is_trough and is_rebound and not_overchased and momentum_ok and crowding_ok)
            if eligible:
                reason = 'trough_recovery_confirmed'
            elif trailing_profit_lock:
                reason = 'trailing_profit_lock_active'
            elif not is_trough:
                reason = 'no_recent_trough_or_oversold'
            elif not is_rebound:
                reason = 'rebound_insufficient_or_falling'
            elif not not_overchased:
                reason = 'overbought_or_excessive_runup'
            elif not crowding_ok:
                reason = 'industry_crowding_too_high'
            else:
                reason = 'momentum_unconfirmed'

        evaluations.append({
            'sleeve': sleeve, 'eligible': eligible, 'stage1_trough': stage1_trough,
            'trailing_profit_lock': trailing_profit_lock,
            'odds_score': odds_score,
            'reason': reason, 'min_rsi': min_rsi, 'current_rsi': current_rsi,
            'rebound_pct': rebound_pct, 'dd_from_peak': dd_from_peak,
            'crowding_pct': crowding_pct,
            'effective_nav_date': visible[-1].nav_date.isoformat() if visible else None,
            'nav_batch_sha256': sorted({p.content_sha256 for p in visible[-trough_window_days:]}),
        })

    eligible_evals = [e for e in evaluations if e['eligible']]
    sat_count = len(satellite_series)
    strategic_sat_total = sum((strategic_weights[s] for s in satellite_series), ZERO)
    if eligible_evals:
        if conviction_ranking:
            ranked = sorted(eligible_evals, key=lambda item: (-float(item['odds_score']), str(item['sleeve'])))
            winners = ranked[:max(1, int(top_k_satellites))]
            score_sum = sum(max(10.0, float(w['odds_score'])) for w in winners)
            desired_total = min(effective_max_sat, tranche_weight * Decimal(len(winners)))
            allocated = {
                str(w['sleeve']): (
                    desired_total * Decimal(str(max(10.0, float(w['odds_score'])) / score_sum))
                ).quantize(Decimal('0.0001'))
                for w in winners
            }
        else:
            allocated = {str(e['sleeve']): min(strategic_weights[str(e['sleeve'])], tranche_weight) for e in eligible_evals}
            total_alloc = sum(allocated.values())
            if total_alloc > effective_max_sat and total_alloc > 0:
                scale = effective_max_sat / total_alloc
                allocated = {s: (w * scale).quantize(Decimal('0.0001')) for s, w in allocated.items()}
        for s in satellite_series:
            weights[s] = allocated.get(s, ZERO)
    else:
        base_per_sleeve = (baseline_satellite_weight / sat_count).quantize(Decimal('0.0001')) if sat_count else ZERO
        for s in satellite_series:
            weights[s] = base_per_sleeve

    effective_sat_total = sum((weights[s] for s in satellite_series), ZERO)
    released_weight = max(ZERO, strategic_sat_total - effective_sat_total)
    extra_sat_weight = max(ZERO, effective_sat_total - strategic_sat_total)
    if released_weight_destination == 'bond' and bond_sleeve in weights:
        weights[bond_sleeve] = max(ZERO, weights[bond_sleeve] + released_weight - extra_sat_weight)

    gold_regime_state = 'static'
    if enable_gold_regime and gold_series and gold_sleeve in weights and bond_sleeve in weights:
        vis_gold = [p for p, following in zip(gold_series, gold_series[1:]) if following.nav_date < review_date]
        if len(vis_gold) >= 200:
            g_navs = [float(p.nav) for p in vis_gold[-220:]]
            g_cur = g_navs[-1]
            g_ma200 = sum(g_navs[-200:]) / 200.0
            g_rsis = [r for r in simple_rsi(g_navs[-30:], 13) if r is not None]
            g_rsi = g_rsis[-1] if g_rsis else 50.0
            base_gold = strategic_weights[gold_sleeve]
            if g_rsi >= 75.0:
                gold_regime_state = 'overbought_pause'
                delta = base_gold * Decimal('0.50')
                weights[gold_sleeve] = (base_gold - delta).quantize(Decimal('0.0001'))
                weights[bond_sleeve] = (weights[bond_sleeve] + delta).quantize(Decimal('0.0001'))
            elif g_cur >= g_ma200 and g_rsi <= 55.0:
                gold_regime_state = 'uptrend_dip_accumulate'
                boost = min(Decimal('0.0200'), weights[bond_sleeve])
                weights[gold_sleeve] = (base_gold + boost).quantize(Decimal('0.0001'))
                weights[bond_sleeve] = (weights[bond_sleeve] - boost).quantize(Decimal('0.0001'))
            elif g_cur < g_ma200:
                gold_regime_state = 'below_ma200_cautious'
                trim = (base_gold * Decimal('0.25')).quantize(Decimal('0.0001'))
                weights[gold_sleeve] = base_gold - trim
                weights[bond_sleeve] = weights[bond_sleeve] + trim
            else:
                gold_regime_state = 'uptrend_hold'

    return {
        'review_date': review_date.isoformat(),
        'evaluations': evaluations,
        'erp_regime': erp_regime,
        'erp_pct': round(erp_pct, 3) if erp_pct is not None else None,
        'gold_regime': gold_regime_state,
        'effective_target_weights': {k: str(v) for k, v in weights.items()},
        'released_weight_destination': released_weight_destination,
        'released_satellite_weight': str(released_weight),
        'selling_allowed': False,
        'external_side_effects': False,
    }


def evaluate_satellite_profit_taking(
    *, holding_days: int, cumulative_return_pct: float, current_rsi_13: float,
    crowding_percentile: float | None = None,
    min_free_redemption_days: int = 90,
    profit_trigger_pct: float = 20.0,
    rsi_overbought_threshold: float = 70.0,
    crowding_extreme_threshold: float = 85.0,
) -> dict:
    """Deterministic profit-harvesting rule for high-beta satellite positions.

    Requires holding >= min_free_redemption_days (0% redemption fee gate) AND
    cumulative return >= profit_trigger_pct AND (RSI13 >= overbought OR crowding >= extreme).
    Recommends harvesting 50% back into the 006932 short-bond reservoir.
    """
    fee_free_eligible = holding_days >= min_free_redemption_days
    profit_met = cumulative_return_pct >= profit_trigger_pct
    overheated = (current_rsi_13 >= rsi_overbought_threshold) or (
        crowding_percentile is not None and crowding_percentile >= crowding_extreme_threshold
    )
    should_harvest = bool(fee_free_eligible and profit_met and overheated)
    if should_harvest:
        action = 'harvest_half_to_bond_reservoir'
        fraction = Decimal('0.50')
    elif not fee_free_eligible:
        action = 'hold_within_fee_lock_period'
        fraction = ZERO
    elif not profit_met:
        action = 'hold_below_profit_target'
        fraction = ZERO
    else:
        action = 'hold_trend_not_overheated'
        fraction = ZERO
    return {
        'should_harvest': should_harvest,
        'action': action,
        'recommended_redeem_fraction': str(fraction),
        'destination_fund_code': '006932',
        'holding_days': holding_days,
        'cumulative_return_pct': round(cumulative_return_pct, 2),
        'current_rsi_13': round(current_rsi_13, 2),
        'crowding_percentile': crowding_percentile,
    }


def build_broker_core_allocation(*, planned_date: date,
                                pre_contribution_portfolio_value_cny: Decimal,
                                monthly_contribution_cny: Decimal,
                                current_position_values_cny: Mapping[str, Decimal],
                                prior_available_cash_cny: Decimal,
                                nondeployable_cash_cny: Decimal,
                                effective_target_weights: Mapping[str, Decimal],
                                strategic_weights: Mapping[str, Decimal],
                                core_sleeves: Sequence[str], broker_core_amount_cny: Decimal,
                                strategy_spec_sha256: str,
                                allocation_priority: str = 'proportional_gap',
                                satellite_sleeves: Sequence[str] = (),
                                gold_sleeves: Sequence[str] = ('gold',),
                                bond_sleeves: Sequence[str] = ('bond',)) -> MonthlyAllocationPlan:
    """Protect broker core budget; only allocate remaining cash to managed gaps.

    Core purchases are exogenous fixed proportions in this mechanism study.
    Managed purchases never become additional core buys, even if core is low.
    Supports 'proportional_gap' (legacy) and 'waterfall' (Satellite -> Gold -> Bond reservoir).
    """
    values = dict(current_position_values_cny)
    targets = dict(effective_target_weights)
    core = set(core_sleeves)
    numbers = [*values.values(), *targets.values(), *strategic_weights.values(),
               pre_contribution_portfolio_value_cny, monthly_contribution_cny,
               prior_available_cash_cny, nondeployable_cash_cny, broker_core_amount_cny]
    if any(not v.is_finite() or v < 0 for v in numbers):
        raise ValueError('amounts and weights must be finite and non-negative')
    if not core or not core <= values.keys() or values.keys() != targets.keys() or targets.keys() != strategic_weights.keys():
        raise ValueError('core and target sleeves must match positions')
    if abs(sum(strategic_weights.values()) - 1) > Decimal('0.00000001') or sum(targets.values()) > Decimal('1.00000001'):
        raise ValueError('strategic weights must sum to one; effective targets at most one')
    if monthly_contribution_cny <= 0 or monthly_contribution_cny > 5000:
        raise ValueError('monthly wallet contribution must be in (0, 5000]')
    if broker_core_amount_cny > monthly_contribution_cny:
        raise ValueError('scenario core budget cannot exceed monthly wallet contribution')
    managed_budget = monthly_contribution_cny - broker_core_amount_cny
    if managed_budget > 2000:
        raise ValueError('managed monthly budget cannot exceed 2000')
    if any(targets[k] != strategic_weights[k] for k in core):
        raise ValueError('managed signals cannot change broker core targets')
    if abs(sum(values.values()) + prior_available_cash_cny + nondeployable_cash_cny
           - pre_contribution_portfolio_value_cny) > CENT:
        raise ValueError('positions and cash do not reconcile')
    if len(strategy_spec_sha256) != 64 or any(c not in '0123456789abcdef' for c in strategy_spec_sha256):
        raise ValueError('invalid strategy spec hash')
    if allocation_priority not in {'proportional_gap', 'waterfall'}:
        raise ValueError('allocation_priority must be proportional_gap or waterfall')
    money = lambda value: value.quantize(CENT, rounding=ROUND_HALF_UP)
    floor = lambda value: value.quantize(CENT, rounding=ROUND_DOWN)
    post = pre_contribution_portfolio_value_cny + monthly_contribution_cny
    core_weight = sum(strategic_weights[k] for k in core)
    if core_weight <= 0:
        raise ValueError('core target must be positive')
    amounts = {k: ZERO for k in targets}
    for k in sorted(core):
        amounts[k] = floor(broker_core_amount_cny * strategic_weights[k] / core_weight)
    # Reconcile cents without leaking extra purchases into the core bucket.
    amounts[sorted(core)[0]] += broker_core_amount_cny - sum(amounts.values())
    gaps = {k: max(ZERO, money(post * targets[k]) - values[k]) for k in targets}
    cash_target = money(post * max(ZERO, 1 - sum(targets.values())))
    reserve = max(ZERO, cash_target - nondeployable_cash_cny)
    remaining = min(managed_budget, max(ZERO, prior_available_cash_cny
                    + monthly_contribution_cny - broker_core_amount_cny - reserve))
    managed = sorted(targets.keys() - core)
    total_gap = sum(gaps[k] for k in managed)
    purchase = min(remaining, total_gap)
    if total_gap:
        if allocation_priority == 'waterfall':
            sat_set = set(satellite_sleeves) if satellite_sleeves else (set(managed) - set(gold_sleeves) - set(bond_sleeves))
            gold_set = set(gold_sleeves) & set(managed)
            bond_set = set(bond_sleeves) & set(managed)
            other_set = set(managed) - sat_set - gold_set - bond_set
            budget_left = purchase
            for tier in (sorted(sat_set), sorted(gold_set), sorted(bond_set), sorted(other_set)):
                tier_gap = sum((gaps[k] for k in tier), ZERO)
                if tier_gap <= 0 or budget_left <= 0:
                    continue
                tier_buy = min(budget_left, tier_gap)
                for k in tier:
                    amounts[k] = floor(tier_buy * gaps[k] / tier_gap)
                budget_left -= sum((amounts[k] for k in tier), ZERO)
        else:
            for k in managed:
                amounts[k] = floor(purchase * gaps[k] / total_gap)
    allocated = sum(amounts.values())
    if allocated > prior_available_cash_cny + monthly_contribution_cny:
        raise AssertionError('purchases exceed available cash')
    return MonthlyAllocationPlan(
        strategy_id='cashflow_6211', strategy_version='0.3.0',
        strategy_spec_sha256=strategy_spec_sha256, planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_contribution_portfolio_value_cny,
        monthly_contribution_cny=monthly_contribution_cny,
        allocations=tuple(SleeveAllocation(k, values[k], targets[k], money(post * targets[k]),
                                          gaps[k], amounts[k]) for k in sorted(targets)),
        unallocated_cash_cny=prior_available_cash_cny + monthly_contribution_cny - allocated,
        prior_available_cash_cny=prior_available_cash_cny,
        nondeployable_cash_cny=nondeployable_cash_cny)

