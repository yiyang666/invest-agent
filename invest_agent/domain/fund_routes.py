"""Validated purchase-route pools separated from research benchmarks.

The pool describes implementation capacity only.  It does not select sleeves,
change target weights, or authorize orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


class PurchaseAvailability(str, Enum):
    OPEN = "open"
    LIMITED = "limited"


@dataclass(frozen=True)
class PurchaseRoute:
    sleeve: str
    fund_code: str
    fund_name: str
    priority: int
    availability: PurchaseAvailability
    minimum_order_cny: Decimal
    daily_cap_cny: Decimal | None
    purchase_fee_rate: Decimal | None
    shared_daily_cap_group: str | None
    shared_daily_cap_cny: Decimal | None
    channel: str
    rule_effective_at: datetime
    rule_source: str

    @property
    def is_purchase_candidate(self) -> bool:
        return self.availability in {
            PurchaseAvailability.OPEN,
            PurchaseAvailability.LIMITED,
        }


@dataclass(frozen=True)
class PurchaseRoutePool:
    pool_id: str
    as_of: datetime
    routes: tuple[PurchaseRoute, ...]
    blocked_or_reference_routes: tuple[Mapping[str, Any], ...]
    cross_day_queue: bool
    cross_month_queue: bool

    def active_fund_codes(self) -> tuple[str, ...]:
        return tuple(sorted({route.fund_code for route in self.routes}))

    def routes_for_sleeve(self, sleeve: str) -> tuple[PurchaseRoute, ...]:
        return tuple(
            sorted(
                (route for route in self.routes if route.sleeve == sleeve),
                key=lambda route: (route.priority, route.fund_code),
            )
        )


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _nonnegative_decimal(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if parsed < 0 or parsed > 1:
        raise ValueError(f"{field} must be between zero and one")
    return parsed


def _timestamp(value: object, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def load_purchase_route_pool(path: str | Path) -> PurchaseRoutePool:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("purchase route pool schema_version must be 1")
    if payload.get("mode") != "advisory_only":
        raise ValueError("purchase route pool must remain advisory_only")

    rules = payload.get("rules")
    if not isinstance(rules, Mapping):
        raise ValueError("purchase route pool requires rules")
    if rules.get("automatic_fund_substitution") is not False:
        raise ValueError("purchase route pool cannot enable automatic substitution")
    if rules.get("cross_day_queue") is not True or rules.get("cross_month_queue") is not True:
        raise ValueError("limited routes must retain cross-day and cross-month queues")

    routes_payload = payload.get("purchase_candidates")
    if not isinstance(routes_payload, list):
        raise ValueError("purchase_candidates must be a list")
    routes: list[PurchaseRoute] = []
    seen_funds: set[str] = set()
    seen_priorities: set[tuple[str, int]] = set()
    shared_caps: dict[str, Decimal] = {}
    for item in routes_payload:
        if not isinstance(item, Mapping):
            raise ValueError("purchase candidate entries must be objects")
        fund_code = str(item.get("fund_code", ""))
        fund_name = str(item.get("fund_name", "")).strip()
        sleeve = str(item.get("sleeve", "")).strip()
        if FUND_CODE_PATTERN.fullmatch(fund_code) is None:
            raise ValueError(f"purchase route fund code must contain six digits: {fund_code!r}")
        if not fund_name:
            raise ValueError("purchase route fund name is required")
        if not sleeve:
            raise ValueError("purchase route sleeve is required")
        try:
            availability = PurchaseAvailability(str(item.get("availability", "")))
        except ValueError as exc:
            raise ValueError(
                "purchase candidates may only use open or limited availability"
            ) from exc
        priority = int(item.get("priority", 0))
        if priority < 1:
            raise ValueError("purchase route priority must be positive")
        if fund_code in seen_funds:
            raise ValueError(f"purchase route fund code is duplicated: {fund_code}")
        if (sleeve, priority) in seen_priorities:
            raise ValueError(f"purchase route priority is duplicated for sleeve {sleeve}")
        seen_funds.add(fund_code)
        seen_priorities.add((sleeve, priority))

        minimum = _decimal(item.get("minimum_order_cny"), field="minimum_order_cny")
        cap_value = item.get("daily_cap_cny")
        cap = None if cap_value is None else _decimal(cap_value, field="daily_cap_cny")
        if availability is PurchaseAvailability.LIMITED and cap is None:
            raise ValueError("limited purchase routes require daily_cap_cny")
        if availability is PurchaseAvailability.OPEN and cap is not None:
            raise ValueError("open purchase routes must not declare daily_cap_cny")
        fee_value = item.get("purchase_fee_rate")
        fee_rate = (
            None
            if fee_value is None
            else _nonnegative_decimal(fee_value, field="purchase_fee_rate")
        )
        shared_group_value = item.get("shared_daily_cap_group")
        shared_group = (
            None if shared_group_value is None else str(shared_group_value).strip()
        )
        shared_cap_value = item.get("shared_daily_cap_cny")
        shared_cap = (
            None
            if shared_cap_value is None
            else _decimal(shared_cap_value, field="shared_daily_cap_cny")
        )
        if bool(shared_group) != (shared_cap is not None):
            raise ValueError(
                "shared daily cap group and amount must be declared together"
            )
        if shared_group is not None:
            prior_cap = shared_caps.setdefault(shared_group, shared_cap)
            if prior_cap != shared_cap:
                raise ValueError("shared daily cap group must use one consistent cap")
        channel = str(item.get("channel", "")).strip()
        source = str(item.get("rule_source", "")).strip()
        if not channel or not source:
            raise ValueError("purchase routes require channel and rule_source")
        routes.append(
            PurchaseRoute(
                sleeve=sleeve,
                fund_code=fund_code,
                fund_name=fund_name,
                priority=priority,
                availability=availability,
                minimum_order_cny=minimum,
                daily_cap_cny=cap,
                purchase_fee_rate=fee_rate,
                shared_daily_cap_group=shared_group,
                shared_daily_cap_cny=shared_cap,
                channel=channel,
                rule_effective_at=_timestamp(
                    item.get("rule_effective_at"), field="rule_effective_at"
                ),
                rule_source=source,
            )
        )

    blocked = payload.get("blocked_or_reference_routes", [])
    if not isinstance(blocked, list):
        raise ValueError("blocked_or_reference_routes must be a list")
    for item in blocked:
        if not isinstance(item, Mapping):
            raise ValueError("blocked route entries must be objects")
        code = str(item.get("fund_code", ""))
        if FUND_CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(f"blocked route fund code must contain six digits: {code!r}")
        if item.get("purchase_candidate") is not False:
            raise ValueError("blocked/reference routes cannot be purchase candidates")
        if code in seen_funds:
            raise ValueError(f"a route cannot be both active and blocked: {code}")

    return PurchaseRoutePool(
        pool_id=str(payload.get("pool_id", "")),
        as_of=_timestamp(payload.get("as_of"), field="as_of"),
        routes=tuple(routes),
        blocked_or_reference_routes=tuple(blocked),
        cross_day_queue=True,
        cross_month_queue=True,
    )
