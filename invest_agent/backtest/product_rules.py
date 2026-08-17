"""Load and resolve versioned fund execution rules without inventing defaults."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Mapping

from invest_agent.domain.portfolio import FUND_CODE_PATTERN


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ResolvedSubscriptionRule:
    snapshot_id: str
    fund_code: str
    fund_name: str
    rule_version: str
    authority: str
    minimum_purchase_cny: Decimal
    daily_purchase_limit_cny: Decimal | None
    cutoff_time: time
    confirmation_business_days: int
    nav_visibility_business_days: int
    purchase_fee_model: str
    purchase_fee_rate: Decimal | None
    fixed_purchase_fee_cny: Decimal | None
    share_precision: int
    share_rounding_mode: str
    calendar_id: str
    source_content_sha256: tuple[str, ...]


def load_execution_rule_snapshot(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("execution rule snapshot must use schema_version 1")
    if payload.get("status") not in {"provisional", "verified"}:
        raise ValueError("execution rule snapshot status is invalid")
    try:
        datetime.fromisoformat(str(payload["as_of"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("execution rule snapshot requires a valid as_of") from exc
    funds = payload.get("funds")
    if not isinstance(funds, list) or not funds:
        raise ValueError("execution rule snapshot requires funds")
    seen_codes: set[str] = set()
    for fund in funds:
        if not isinstance(fund, dict):
            raise ValueError("fund execution rule must be an object")
        code = str(fund.get("fund_code", ""))
        if FUND_CODE_PATTERN.fullmatch(code) is None or code in seen_codes:
            raise ValueError("fund codes must be unique six-digit values")
        seen_codes.add(code)
        versions = fund.get("versions")
        if not isinstance(versions, list) or not versions:
            raise ValueError(f"fund {code} requires at least one rule version")
        ranges: list[tuple[date, date | None, str]] = []
        seen_versions: set[str] = set()
        for version in versions:
            if not isinstance(version, dict):
                raise ValueError("rule version must be an object")
            version_id = str(version.get("rule_version", ""))
            if not version_id or version_id in seen_versions:
                raise ValueError(f"fund {code} rule versions must be non-empty and unique")
            seen_versions.add(version_id)
            start = date.fromisoformat(str(version["effective_from"]))
            end_raw = version.get("effective_to")
            end = None if end_raw is None else date.fromisoformat(str(end_raw))
            if end is not None and end < start:
                raise ValueError(f"fund {code} rule version ends before it starts")
            for prior_start, prior_end, _prior_id in ranges:
                if (prior_end is None or start <= prior_end) and (end is None or prior_start <= end):
                    raise ValueError(f"fund {code} has overlapping rule versions")
            ranges.append((start, end, version_id))
            if version.get("authority") not in {"reference_only", "official_verified"}:
                raise ValueError(f"fund {code} rule authority is invalid")
            documents = version.get("source_documents")
            if not isinstance(documents, list) or not documents:
                raise ValueError(f"fund {code} rule version requires source documents")
            for document in documents:
                if SHA256_PATTERN.fullmatch(str(document.get("content_sha256", ""))) is None:
                    raise ValueError("source document requires a SHA-256 digest")
    return payload


def _select_fee_tier(
    tiers: object, gross_amount_cny: Decimal
) -> tuple[str, Decimal | None, Decimal | None]:
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("purchase fee tiers are required")
    matches: list[Mapping[str, object]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ValueError("purchase fee tier must be an object")
        minimum = Decimal(str(tier["minimum_cny_inclusive"]))
        maximum_raw = tier.get("maximum_cny_exclusive")
        maximum = None if maximum_raw is None else Decimal(str(maximum_raw))
        if gross_amount_cny >= minimum and (maximum is None or gross_amount_cny < maximum):
            matches.append(tier)
    if len(matches) != 1:
        raise ValueError("gross amount must match exactly one purchase fee tier")
    tier = matches[0]
    kind = str(tier.get("kind"))
    if kind == "rate":
        return "proportional_front_end", Decimal(str(tier["rate"])), None
    if kind == "fixed":
        return "fixed", None, Decimal(str(tier["fixed_fee_cny"]))
    raise ValueError("purchase fee tier kind must be rate or fixed")


def resolve_subscription_rule(
    snapshot: Mapping[str, object],
    *,
    fund_code: str,
    submit_date: date,
    gross_amount_cny: Decimal,
    require_official_verified: bool = True,
) -> ResolvedSubscriptionRule:
    if gross_amount_cny <= 0:
        raise ValueError("gross amount must be positive")
    funds = snapshot.get("funds")
    if not isinstance(funds, list):
        raise ValueError("snapshot funds are missing")
    matches = [item for item in funds if item.get("fund_code") == fund_code]
    if len(matches) != 1:
        raise ValueError(f"exactly one fund rule is required for {fund_code}")
    fund = matches[0]
    versions = [
        item
        for item in fund["versions"]
        if date.fromisoformat(item["effective_from"]) <= submit_date
        and (
            item.get("effective_to") is None
            or submit_date <= date.fromisoformat(item["effective_to"])
        )
    ]
    if len(versions) != 1:
        raise ValueError(f"exactly one effective rule version is required for {fund_code}")
    version = versions[0]
    if require_official_verified and (
        snapshot.get("status") != "verified"
        or version.get("authority") != "official_verified"
    ):
        raise ValueError("official verified execution rules are required")
    unknown = version.get("unknown_required_fields")
    if not isinstance(unknown, list) or unknown:
        raise ValueError(f"required execution rule fields remain unknown: {unknown}")
    subscription = version.get("subscription")
    if not isinstance(subscription, dict):
        raise ValueError("subscription rule is missing")
    minimum = Decimal(str(subscription["minimum_purchase_cny"]))
    if minimum <= 0 or gross_amount_cny < minimum:
        raise ValueError("gross amount is below the resolved minimum purchase")
    limit = subscription["daily_purchase_limit"]
    if not isinstance(limit, dict):
        raise ValueError("daily purchase limit is invalid")
    limit_kind = limit.get("kind")
    if limit_kind == "unknown":
        raise ValueError("daily purchase limit is unknown")
    daily_limit = None
    if limit_kind == "amount":
        daily_limit = Decimal(str(limit.get("amount_cny")))
        if daily_limit <= 0:
            raise ValueError("daily purchase limit amount must be positive")
    elif limit_kind != "unlimited":
        raise ValueError("daily purchase limit kind is invalid")
    fee_model, fee_rate, fixed_fee = _select_fee_tier(
        subscription["purchase_fee_tiers"], gross_amount_cny
    )
    documents = version["source_documents"]
    return ResolvedSubscriptionRule(
        snapshot_id=str(snapshot["snapshot_id"]),
        fund_code=fund_code,
        fund_name=str(fund["fund_name"]),
        rule_version=str(version["rule_version"]),
        authority=str(version["authority"]),
        minimum_purchase_cny=minimum,
        daily_purchase_limit_cny=daily_limit,
        cutoff_time=time.fromisoformat(str(subscription["cutoff_time"])),
        confirmation_business_days=int(subscription["confirmation_business_days"]),
        nav_visibility_business_days=int(subscription["nav_visibility_business_days"]),
        purchase_fee_model=fee_model,
        purchase_fee_rate=fee_rate,
        fixed_purchase_fee_cny=fixed_fee,
        share_precision=int(subscription["share_precision"]),
        share_rounding_mode=str(subscription["share_rounding_mode"]),
        calendar_id=str(subscription["calendar_id"]),
        source_content_sha256=tuple(
            sorted(str(document["content_sha256"]) for document in documents)
        ),
    )
