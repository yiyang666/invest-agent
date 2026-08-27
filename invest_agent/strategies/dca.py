"""Pure calculations for dca_baseline@1.0.0; never submits real orders."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import hashlib
import json
from typing import Mapping, Sequence

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


CENT = Decimal("0.01")
ONE = Decimal("1")
STRATEGY_ID = "dca_baseline"
STRATEGY_VERSION = "1.0.0"


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _floor_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_DOWN)


def _money_text(value: Decimal) -> str:
    return f"{value:.2f}"


@dataclass(frozen=True)
class SleeveAllocation:
    sleeve: str
    current_value_cny: Decimal
    target_weight: Decimal
    post_contribution_target_cny: Decimal
    positive_gap_cny: Decimal
    allocated_cny: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "sleeve": self.sleeve,
            "current_value_cny": _money_text(self.current_value_cny),
            "target_weight": str(self.target_weight),
            "post_contribution_target_cny": _money_text(
                self.post_contribution_target_cny
            ),
            "positive_gap_cny": _money_text(self.positive_gap_cny),
            "allocated_cny": _money_text(self.allocated_cny),
        }


@dataclass(frozen=True)
class MonthlyAllocationPlan:
    strategy_id: str
    strategy_version: str
    strategy_spec_sha256: str
    planned_date: date
    pre_contribution_portfolio_value_cny: Decimal
    monthly_contribution_cny: Decimal
    allocations: tuple[SleeveAllocation, ...]
    unallocated_cash_cny: Decimal
    prior_available_cash_cny: Decimal = Decimal("0")
    nondeployable_cash_cny: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_spec_sha256": self.strategy_spec_sha256,
            "planned_date": self.planned_date.isoformat(),
            "pre_contribution_portfolio_value_cny": _money_text(
                self.pre_contribution_portfolio_value_cny
            ),
            "monthly_contribution_cny": _money_text(self.monthly_contribution_cny),
            "allocations": [item.to_dict() for item in self.allocations],
            "unallocated_cash_cny": _money_text(self.unallocated_cash_cny),
            "prior_available_cash_cny": _money_text(
                self.prior_available_cash_cny
            ),
            "nondeployable_cash_cny": _money_text(self.nondeployable_cash_cny),
            "mode": "simulation_only",
        }


@dataclass(frozen=True)
class InstrumentRoute:
    sleeve: str
    fund_code: str
    priority: int
    minimum_order_cny: Decimal
    daily_cap_cny: Decimal | None
    eligible_dates: tuple[date, ...]
    rule_version: str
    reserved_by_date_cny: Mapping[date, Decimal] = field(default_factory=dict)
    allocation_mode: str = "as_soon_as_possible"
    planned_tranche_count: int = 1
    shared_daily_cap_group: str | None = None
    shared_daily_cap_cny: Decimal | None = None


@dataclass(frozen=True)
class SimulatedSubscription:
    simulation_id: str
    sleeve: str
    fund_code: str
    planned_date: date
    simulated_submit_date: date
    gross_amount_cny: Decimal
    rule_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "simulation_id": self.simulation_id,
            "action": "subscribe",
            "sleeve": self.sleeve,
            "fund_code": self.fund_code,
            "planned_date": self.planned_date.isoformat(),
            "simulated_submit_date": self.simulated_submit_date.isoformat(),
            "gross_amount_cny": _money_text(self.gross_amount_cny),
            "rule_version": self.rule_version,
            "submitted": False,
            "account": None,
            "payment_method": None,
        }


@dataclass(frozen=True)
class SimulatedSubscriptionPlan:
    allocation_plan: MonthlyAllocationPlan
    subscriptions: tuple[SimulatedSubscription, ...]
    unfilled_by_sleeve_cny: Mapping[str, Decimal]
    contribution_cash_remaining_cny: Decimal
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "allocation_plan": self.allocation_plan.to_dict(),
            "simulated_subscriptions": [item.to_dict() for item in self.subscriptions],
            "unfilled_by_sleeve_cny": {
                key: _money_text(value)
                for key, value in sorted(self.unfilled_by_sleeve_cny.items())
            },
            "contribution_cash_remaining_cny": _money_text(
                self.contribution_cash_remaining_cny
            ),
            "issues": list(self.issues),
            "external_side_effects": False,
            "real_order_submission_available": False,
        }


def build_monthly_allocation(
    *,
    planned_date: date,
    pre_contribution_portfolio_value_cny: Decimal,
    monthly_contribution_cny: Decimal,
    current_sleeve_values_cny: Mapping[str, Decimal],
    target_weights: Mapping[str, Decimal],
    strategy_spec_sha256: str,
) -> MonthlyAllocationPlan:
    """Allocate new cash across positive target gaps, rounded down to cents."""
    pre_value = _money(pre_contribution_portfolio_value_cny)
    contribution = _money(monthly_contribution_cny)
    current = {key: _money(value) for key, value in current_sleeve_values_cny.items()}
    targets = {key: Decimal(value) for key, value in target_weights.items()}
    if contribution <= 0 or pre_value < 0:
        raise ValueError("portfolio value must be non-negative and contribution positive")
    if set(current) != set(targets) or not targets:
        raise ValueError("current sleeve values and target weights must have identical keys")
    if any(value < 0 for value in current.values()):
        raise ValueError("current sleeve values cannot be negative")
    if any(value < 0 for value in targets.values()):
        raise ValueError("target weights cannot be negative")
    if abs(sum(targets.values(), Decimal("0")) - ONE) > Decimal("0.00000001"):
        raise ValueError("target weights must sum to 1")
    if abs(sum(current.values(), Decimal("0")) - pre_value) > CENT:
        raise ValueError("current sleeve values must reconcile to portfolio value")
    if len(strategy_spec_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in strategy_spec_sha256
    ):
        raise ValueError("strategy_spec_sha256 must be a lowercase SHA-256 hex digest")

    post_value = pre_value + contribution
    raw: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for sleeve in sorted(targets):
        target_value = _money(post_value * targets[sleeve])
        gap = max(Decimal("0"), target_value - current[sleeve])
        raw.append((sleeve, target_value, gap, targets[sleeve]))
    total_gap = sum((item[2] for item in raw), Decimal("0"))
    basis = total_gap if total_gap > 0 else ONE
    allocations = tuple(
        SleeveAllocation(
            sleeve=sleeve,
            current_value_cny=current[sleeve],
            target_weight=weight,
            post_contribution_target_cny=target_value,
            positive_gap_cny=gap,
            allocated_cny=_floor_money(
                contribution * (gap / basis if total_gap > 0 else weight)
            ),
        )
        for sleeve, target_value, gap, weight in raw
    )
    allocated = sum((item.allocated_cny for item in allocations), Decimal("0"))
    if allocated > contribution:
        raise AssertionError("allocated cash cannot exceed monthly contribution")
    return MonthlyAllocationPlan(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        strategy_spec_sha256=strategy_spec_sha256,
        planned_date=planned_date,
        pre_contribution_portfolio_value_cny=pre_value,
        monthly_contribution_cny=contribution,
        allocations=allocations,
        unallocated_cash_cny=contribution - allocated,
    )


def _simulation_id(
    plan: MonthlyAllocationPlan,
    route: InstrumentRoute,
    submit_date: date,
    amount: Decimal,
    sequence: int,
) -> str:
    payload = json.dumps(
        {
            "strategy": f"{plan.strategy_id}@{plan.strategy_version}",
            "spec": plan.strategy_spec_sha256,
            "fund_code": route.fund_code,
            "submit_date": submit_date.isoformat(),
            "amount": _money_text(amount),
            "sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sim_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def generate_simulated_subscriptions(
    plan: MonthlyAllocationPlan,
    *,
    routes: Sequence[InstrumentRoute],
    execution_end_date: date | None = None,
    unfilled_issue_prefix: str = "unfilled_month_end",
) -> SimulatedSubscriptionPlan:
    """Route allocations through explicit dates/caps and emit simulation-only events."""
    seen_funds: set[str] = set()
    priorities: set[tuple[str, int]] = set()
    shared_caps: dict[str, Decimal] = {}
    shared_reserved_by_date: dict[tuple[str, date], Decimal] = {}
    for route in routes:
        if route.fund_code in seen_funds:
            raise ValueError("a fund code can appear in only one route")
        if FUND_CODE_PATTERN.fullmatch(route.fund_code) is None:
            raise ValueError("route fund codes must contain six digits")
        if (route.sleeve, route.priority) in priorities:
            raise ValueError("route priorities must be unique within each sleeve")
        seen_funds.add(route.fund_code)
        priorities.add((route.sleeve, route.priority))
        if route.minimum_order_cny <= 0:
            raise ValueError("minimum order must be positive")
        if route.daily_cap_cny is not None and route.daily_cap_cny <= 0:
            raise ValueError("daily cap must be positive when supplied")
        if bool(route.shared_daily_cap_group) != (
            route.shared_daily_cap_cny is not None
        ):
            raise ValueError(
                "shared daily cap group and amount must be declared together"
            )
        if (
            route.shared_daily_cap_cny is not None
            and route.shared_daily_cap_cny <= 0
        ):
            raise ValueError("shared daily cap must be positive when supplied")
        if route.shared_daily_cap_group is not None:
            group_cap = _money(route.shared_daily_cap_cny)
            prior_cap = shared_caps.setdefault(route.shared_daily_cap_group, group_cap)
            if prior_cap != group_cap:
                raise ValueError("shared daily cap group must use one consistent cap")
        if not route.rule_version:
            raise ValueError("every route requires a rule version")
        if route.allocation_mode not in {"as_soon_as_possible", "equal_tranches"}:
            raise ValueError(f"unsupported allocation mode: {route.allocation_mode}")
        if route.planned_tranche_count < 1:
            raise ValueError("planned_tranche_count must be positive")
        if route.allocation_mode == "equal_tranches" and route.daily_cap_cny is not None:
            raise ValueError("equal-tranche routes cannot also use a daily cap")
        for reserved_date, reserved_amount in route.reserved_by_date_cny.items():
            if reserved_amount < 0:
                raise ValueError("reserved daily capacity cannot be negative")
            if route.daily_cap_cny is not None and reserved_amount > route.daily_cap_cny:
                raise ValueError(
                    f"reserved daily capacity exceeds cap for {route.fund_code} on {reserved_date}"
                )
            if route.shared_daily_cap_group is not None:
                shared_key = (route.shared_daily_cap_group, reserved_date)
                shared_reserved_by_date[shared_key] = (
                    shared_reserved_by_date.get(shared_key, Decimal("0"))
                    + _money(reserved_amount)
                )
                if shared_reserved_by_date[shared_key] > _money(
                    route.shared_daily_cap_cny
                ):
                    raise ValueError(
                        "reserved daily capacity exceeds the shared daily cap"
                    )

    month_end = date(
        plan.planned_date.year,
        plan.planned_date.month,
        monthrange(plan.planned_date.year, plan.planned_date.month)[1],
    )
    routing_end = execution_end_date or month_end
    if routing_end < plan.planned_date:
        raise ValueError("execution_end_date cannot be before planned_date")
    grouped: dict[str, list[InstrumentRoute]] = {}
    for route in routes:
        grouped.setdefault(route.sleeve, []).append(route)
    subscriptions: list[SimulatedSubscription] = []
    unfilled: dict[str, Decimal] = {}
    issues: list[str] = []
    sequence = 0
    shared_capacity_used: dict[tuple[str, date], Decimal] = {}
    for allocation in plan.allocations:
        remaining = allocation.allocated_cny
        sleeve_routes = sorted(
            grouped.get(allocation.sleeve, []),
            key=lambda item: (item.priority, item.fund_code),
        )
        if remaining > 0 and not sleeve_routes:
            issues.append(f"missing_route:{allocation.sleeve}")
        if sleeve_routes and all(
            route.allocation_mode == "as_soon_as_possible"
            for route in sleeve_routes
        ):
            dated_capacity = sorted(
                (
                    submit_date.year,
                    submit_date.month,
                    route.priority,
                    submit_date,
                    route.fund_code,
                    route,
                )
                for route in sleeve_routes
                for submit_date in set(route.eligible_dates)
                if plan.planned_date <= submit_date <= routing_end
            )
            for _year, _month, _priority, submit_date, _fund_code, route in dated_capacity:
                minimum = _money(route.minimum_order_cny)
                if remaining < minimum:
                    break
                cap = (
                    None
                    if route.daily_cap_cny is None
                    else _money(route.daily_cap_cny)
                )
                if cap is None:
                    available_capacity = remaining
                else:
                    reserved = _money(
                        Decimal(
                            route.reserved_by_date_cny.get(
                                submit_date, Decimal("0")
                            )
                        )
                    )
                    available_capacity = max(Decimal("0"), cap - reserved)
                shared_key = None
                if route.shared_daily_cap_group is not None:
                    shared_key = (route.shared_daily_cap_group, submit_date)
                    shared_remaining = max(
                        Decimal("0"),
                        _money(route.shared_daily_cap_cny)
                        - shared_reserved_by_date.get(shared_key, Decimal("0"))
                        - shared_capacity_used.get(shared_key, Decimal("0")),
                    )
                    available_capacity = min(available_capacity, shared_remaining)
                amount = _floor_money(min(remaining, available_capacity))
                if amount < minimum:
                    continue
                sequence += 1
                subscriptions.append(
                    SimulatedSubscription(
                        simulation_id=_simulation_id(
                            plan, route, submit_date, amount, sequence
                        ),
                        sleeve=allocation.sleeve,
                        fund_code=route.fund_code,
                        planned_date=plan.planned_date,
                        simulated_submit_date=submit_date,
                        gross_amount_cny=amount,
                        rule_version=route.rule_version,
                    )
                )
                remaining -= amount
                if shared_key is not None:
                    shared_capacity_used[shared_key] = (
                        shared_capacity_used.get(shared_key, Decimal("0")) + amount
                    )
                if remaining == 0:
                    break
            unfilled[allocation.sleeve] = remaining
            if remaining > 0:
                issues.append(f"{unfilled_issue_prefix}:{allocation.sleeve}")
            continue
        for route in sleeve_routes:
            minimum = _money(route.minimum_order_cny)
            cap = None if route.daily_cap_cny is None else _money(route.daily_cap_cny)
            if route.allocation_mode == "equal_tranches":
                eligible_dates = sorted(
                    item
                    for item in route.eligible_dates
                    if plan.planned_date <= item <= routing_end
                )
                original_remaining = remaining
                tranche_amount = _floor_money(
                    original_remaining / Decimal(route.planned_tranche_count)
                )
                for tranche_index, submit_date in enumerate(eligible_dates, start=1):
                    amount = tranche_amount
                    if tranche_index == route.planned_tranche_count:
                        amount = original_remaining - tranche_amount * Decimal(
                            route.planned_tranche_count - 1
                        )
                    amount = _floor_money(amount)
                    if amount < minimum or remaining < minimum:
                        continue
                    amount = min(amount, remaining)
                    sequence += 1
                    subscriptions.append(
                        SimulatedSubscription(
                            simulation_id=_simulation_id(
                                plan, route, submit_date, amount, sequence
                            ),
                            sleeve=allocation.sleeve,
                            fund_code=route.fund_code,
                            planned_date=plan.planned_date,
                            simulated_submit_date=submit_date,
                            gross_amount_cny=amount,
                            rule_version=route.rule_version,
                        )
                    )
                    remaining -= amount
                if remaining == 0:
                    break
                continue
            eligible_dates = sorted(
                {
                    item
                    for item in route.eligible_dates
                    if plan.planned_date <= item <= routing_end
                }
            )
            for submit_date in eligible_dates:
                if remaining < minimum:
                    break
                if cap is None:
                    available_capacity = remaining
                else:
                    reserved = _money(
                        Decimal(route.reserved_by_date_cny.get(submit_date, Decimal("0")))
                    )
                    available_capacity = max(Decimal("0"), cap - reserved)
                shared_key = None
                if route.shared_daily_cap_group is not None:
                    shared_key = (route.shared_daily_cap_group, submit_date)
                    shared_remaining = max(
                        Decimal("0"),
                        _money(route.shared_daily_cap_cny)
                        - shared_reserved_by_date.get(shared_key, Decimal("0"))
                        - shared_capacity_used.get(shared_key, Decimal("0")),
                    )
                    available_capacity = min(available_capacity, shared_remaining)
                amount = min(remaining, available_capacity)
                amount = _floor_money(amount)
                if amount < minimum:
                    continue
                sequence += 1
                subscriptions.append(
                    SimulatedSubscription(
                        simulation_id=_simulation_id(
                            plan, route, submit_date, amount, sequence
                        ),
                        sleeve=allocation.sleeve,
                        fund_code=route.fund_code,
                        planned_date=plan.planned_date,
                        simulated_submit_date=submit_date,
                        gross_amount_cny=amount,
                        rule_version=route.rule_version,
                    )
                )
                remaining -= amount
                if shared_key is not None:
                    shared_capacity_used[shared_key] = (
                        shared_capacity_used.get(shared_key, Decimal("0")) + amount
                    )
                if remaining == 0:
                    break
            if remaining == 0:
                break
        unfilled[allocation.sleeve] = remaining
        if remaining > 0:
            issues.append(f"{unfilled_issue_prefix}:{allocation.sleeve}")
    contribution_cash_remaining = plan.unallocated_cash_cny + sum(
        unfilled.values(), Decimal("0")
    )
    return SimulatedSubscriptionPlan(
        allocation_plan=plan,
        subscriptions=tuple(subscriptions),
        unfilled_by_sleeve_cny=unfilled,
        contribution_cash_remaining_cny=contribution_cash_remaining,
        issues=tuple(issues),
    )
