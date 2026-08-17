"""Build a reproducible, non-executable monthly research decision package."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from invest_agent.metrics.fund import load_nav_series
from invest_agent.metrics.market_state import calculate_market_state_snapshot
from invest_agent.metrics.portfolio import calculate_portfolio_risk
from invest_agent.strategies.dca import build_monthly_allocation
from invest_agent.strategies.drawdown_add import calculate_drawdown_budget_signal
from invest_agent.strategies.sleeve_drawdown import build_sleeve_drawdown_signal
from invest_agent.strategies.trend_rs import calculate_new_money_trend_signal

from .registry import build_decision_input, load_strategy_registry


TZ = ZoneInfo("Asia/Shanghai")
TARGET_SLEEVES = (
    "defensive",
    "domestic_broad_core",
    "domestic_growth",
    "sh_hk_sz_passive_technology_satellite",
    "us_broad_core",
    "us_growth",
)


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _collect_named_values(value: object, key_name: str) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == key_name and isinstance(child, list):
                found.update(str(item) for item in child)
            found.update(_collect_named_values(child, key_name))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_named_values(child, key_name))
    return found


def _resolve(root: Path, relative: object, field: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{field} must be a non-empty relative path")
    result = (root / relative).resolve()
    workspace = root.resolve()
    if result != workspace and workspace not in result.parents:
        raise ValueError(f"{field} escapes workspace root")
    if not result.is_file():
        raise ValueError(f"{field} does not exist: {relative}")
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _validate_snapshot_context(
    snapshot: Mapping[str, Any], *, as_of: datetime, maximum_age_calendar_days: int
) -> datetime:
    if snapshot.get("quality_status") != "pass":
        raise ValueError("portfolio snapshot must pass its quality gate")
    if maximum_age_calendar_days < 0:
        raise ValueError("maximum snapshot age cannot be negative")
    snapshot_time = datetime.fromisoformat(str(snapshot["as_of"]))
    if snapshot_time.tzinfo is None:
        raise ValueError("portfolio snapshot as_of must include timezone")
    if snapshot_time > as_of:
        raise ValueError("portfolio snapshot cannot be newer than decision pack")
    age = (as_of.date() - snapshot_time.date()).days
    if age > maximum_age_calendar_days:
        raise ValueError("portfolio snapshot is stale")
    return snapshot_time


def _validate_market_context(
    market_as_of: date, *, decision_date: date, maximum_age_calendar_days: int
) -> None:
    if maximum_age_calendar_days < 0:
        raise ValueError("maximum market-data age cannot be negative")
    if market_as_of > decision_date:
        raise ValueError("market data as_of cannot be in the future")
    if (decision_date - market_as_of).days > maximum_age_calendar_days:
        raise ValueError("market data is stale")


def _signal_envelope(
    *,
    strategy_id: str,
    version: str,
    review_date: date,
    valid_until: str,
    payload: Mapping[str, Any],
    veto: bool,
    risk_reasons: list[str],
) -> dict[str, Any]:
    signal_at = datetime.combine(review_date, time.min, tzinfo=TZ)
    cutoff = datetime.combine(review_date - timedelta(days=1), time.max, tzinfo=TZ)
    return {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "signal_as_of": signal_at.isoformat(),
        "data_cutoff": cutoff.isoformat(),
        "valid_until": valid_until,
        "data_quality": {
            "status": "historical_visibility_assumed",
            "issues": ["backfilled_nav_has_no_trustworthy_historical_publication_time"],
        },
        "risk": {"veto": veto, "reasons": risk_reasons},
        "evidence": {"artifact_sha256": _canonical_sha256(payload)},
        "payload": dict(payload),
    }


def _require_target_authority(decision_input: Mapping[str, Any]) -> None:
    accepted = decision_input.get("accepted_signals")
    if not isinstance(accepted, list):
        raise ValueError("decision input accepted_signals must be a list")
    accepted_authorities = [
        item
        for item in accepted
        if isinstance(item, Mapping) and item.get("target_allocation_authority") is True
    ]
    if len(accepted_authorities) != 1 or (
        accepted_authorities[0].get("strategy_id"),
        accepted_authorities[0].get("strategy_version"),
    ) != ("dca_baseline", "1.4.0"):
        raise ValueError("accepted dca_baseline@1.4.0 target authority is required")


def _current_sleeves(
    snapshot: Mapping[str, Any], mapping: Mapping[str, str]
) -> dict[str, Decimal]:
    values = {sleeve: Decimal("0") for sleeve in TARGET_SLEEVES}
    values.update(
        {
            "legacy_active_other": Decimal("0"),
            "legacy_theme_other": Decimal("0"),
            "unassigned_cash": Decimal(str(snapshot["cash"])),
        }
    )
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        raise ValueError("snapshot positions must be a list")
    for item in positions:
        if not isinstance(item, Mapping):
            raise ValueError("snapshot position must be an object")
        code = str(item["fund_code"])
        sleeve = mapping.get(code)
        if sleeve is None:
            raise ValueError(f"current position has no reviewed role mapping: {code}")
        if sleeve not in values:
            raise ValueError(f"unsupported mapped sleeve for {code}: {sleeve}")
        values[sleeve] += Decimal(str(item["market_value"]))
    total = sum(values.values(), Decimal("0"))
    if abs(total - Decimal(str(snapshot["total_assets"]))) > Decimal("0.01"):
        raise ValueError("mapped sleeve values do not reconcile to snapshot total assets")
    return values


def build_monthly_research_decision_pack(
    config: Mapping[str, Any], *, workspace_root: Path
) -> dict[str, Any]:
    """Assemble current portfolio evidence and frozen monthly strategy signals."""

    if config.get("schema_version") != 1:
        raise ValueError("monthly decision config schema_version must be 1")
    safety = config.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(key) is not False
        for key in (
            "mid_cycle_pack_may_generate_new_schedule",
            "selling_allowed",
            "order_intent_generation_allowed",
            "real_trading_enabled",
        )
    ):
        raise ValueError("bootstrap decision pack safety flags must keep execution disabled")
    if safety.get("research_only") is not True or safety.get("advisory_only") is not True:
        raise ValueError("decision pack must remain research_only and advisory_only")

    as_of = datetime.fromisoformat(str(config["as_of"]))
    if as_of.tzinfo is None:
        raise ValueError("decision pack as_of must include timezone")
    review_date = date.fromisoformat(str(config["signal_review_date"]))
    if review_date.day != 1:
        raise ValueError("monthly signal review date must be the first calendar day")
    market_as_of = date.fromisoformat(str(config["market_data_as_of"]))
    quality_limits = config.get("quality_limits")
    if not isinstance(quality_limits, Mapping):
        raise ValueError("monthly decision config requires quality_limits")
    _validate_market_context(
        market_as_of,
        decision_date=as_of.date(),
        maximum_age_calendar_days=int(quality_limits["maximum_market_data_age_calendar_days"]),
    )

    database = _resolve(workspace_root, config["database_path"], "database_path")
    snapshot_path = _resolve(workspace_root, config["snapshot_path"], "snapshot_path")
    registry_path = _resolve(workspace_root, config["registry_path"], "registry_path")
    snapshot = _load_json(snapshot_path)
    snapshot_time = _validate_snapshot_context(
        snapshot,
        as_of=as_of,
        maximum_age_calendar_days=int(quality_limits["maximum_snapshot_age_calendar_days"]),
    )

    positions = snapshot["positions"]
    weights = {
        str(item["fund_code"]): Decimal(str(item["weight"])) for item in positions
    }
    cash_weight = Decimal(str(snapshot["cash_weight"]))
    provider = str(config["provider_id"])
    portfolio_risk = calculate_portfolio_risk(
        database,
        weights=weights,
        cash_weight=cash_weight,
        provider_id=provider,
        as_of=market_as_of,
        minimum_overlap=20,
        max_single_fund_weight_pct=Decimal(
            str(config["risk_limits"]["maximum_single_fund_weight_pct"])
        ),
    )
    market_state = calculate_market_state_snapshot(
        database,
        fund_codes=list(weights),
        weights=weights,
        cash_weight=cash_weight,
        provider_id=provider,
        as_of=market_as_of,
    )

    routes = {str(k): str(v) for k, v in config["target_routes"].items()}
    target_weights = {str(k): Decimal(str(v)) for k, v in config["target_weights"].items()}
    if set(routes) != set(TARGET_SLEEVES) or set(target_weights) != set(TARGET_SLEEVES):
        raise ValueError("target routes and weights must contain the six frozen 631 sleeves")
    if sum(target_weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("target weights must sum exactly to one")

    trend = calculate_new_money_trend_signal(
        database,
        review_date=review_date,
        candidates={sleeve: routes[sleeve] for sleeve in ("us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite")},
        provider_id=provider,
    )
    drawdown = calculate_drawdown_budget_signal(
        database,
        review_date=review_date,
        funds=routes,
        weights=target_weights,
        provider_id=provider,
    )
    portfolio_series = {
        sleeve: (
            code,
            load_nav_series(
                database,
                fund_code=code,
                provider_id=provider,
                as_of=review_date - timedelta(days=1),
                nav_field=str(config["nav_field"]),
            ),
        )
        for sleeve, code in routes.items()
    }
    candidate_series = {
        sleeve: portfolio_series[sleeve]
        for sleeve in ("us_growth", "domestic_growth", "sh_hk_sz_passive_technology_satellite")
    }
    sleeve_drawdown = build_sleeve_drawdown_signal(
        review_date=review_date,
        candidate_series=candidate_series,
        portfolio_series=portfolio_series,
        portfolio_weights=target_weights,
    )
    baseline = {
        "model_version": "dca_baseline_monthly_signal_v1_4",
        "review_date": review_date.isoformat(),
        "signal_cutoff_date": (review_date - timedelta(days=1)).isoformat(),
        "target_weights": {key: str(value) for key, value in sorted(target_weights.items())},
        "target_routes": dict(sorted(routes.items())),
        "monthly_contribution_cny": str(config["monthly_contribution_preview_cny"]),
        "depends_on_market_state": False,
        "new_schedule_generated": False,
        "reason": "bootstrap_pack_created_after_monthly_budget_freeze_date",
        "selling_allowed": False,
        "real_order_submission_available": False,
    }

    signals = [
        _signal_envelope(
            strategy_id="dca_baseline",
            version="1.4.0",
            review_date=review_date,
            valid_until=str(config["signal_valid_until"]),
            payload=baseline,
            veto=False,
            risk_reasons=["existing_concentration_requires_review_but_target_gap_new_money_does_not_add_to_legacy_overweight"],
        ),
        _signal_envelope(
            strategy_id="new_money_trend_rs",
            version="1.0.0",
            review_date=review_date,
            valid_until=str(config["signal_valid_until"]),
            payload=trend,
            veto=False,
            risk_reasons=["research_control_only_not_target_allocation_authority"],
        ),
        _signal_envelope(
            strategy_id="drawdown_budget_add",
            version="1.0.0",
            review_date=review_date,
            valid_until=str(config["signal_valid_until"]),
            payload=drawdown,
            veto=bool(drawdown["risk_veto"]),
            risk_reasons=[str(drawdown["reason"])],
        ),
        _signal_envelope(
            strategy_id="sleeve_drawdown_recovery",
            version="1.0.0",
            review_date=review_date,
            valid_until=str(config["signal_valid_until"]),
            payload=sleeve_drawdown,
            veto=bool(sleeve_drawdown["portfolio_risk_veto"]),
            risk_reasons=[str(sleeve_drawdown["reason"])],
        ),
    ]
    registry = load_strategy_registry(registry_path)
    decision_input = build_decision_input(
        registry,
        workspace_root=workspace_root,
        as_of=str(config["as_of"]),
        signals=signals,
    )
    _require_target_authority(decision_input)

    current_values = _current_sleeves(snapshot, config["current_position_role_mapping"])
    preview_targets = {sleeve: target_weights.get(sleeve, Decimal("0")) for sleeve in current_values}
    baseline_spec_hash = next(
        item["spec"]["sha256"]
        for item in registry["strategies"]
        if item["strategy_id"] == "dca_baseline" and item["strategy_version"] == "1.4.0"
    )
    preview = build_monthly_allocation(
        planned_date=as_of.date(),
        pre_contribution_portfolio_value_cny=Decimal(str(snapshot["total_assets"])),
        monthly_contribution_cny=Decimal(str(config["monthly_contribution_preview_cny"])),
        current_sleeve_values_cny=current_values,
        target_weights=preview_targets,
        strategy_spec_sha256=baseline_spec_hash,
    ).to_dict()
    preview["strategy_id"] = "dca_baseline"
    preview["strategy_version"] = "1.4.0"
    preview["preview_only"] = True
    preview["scheduled_purchase_dates"] = []
    preview["reason"] = "mid_cycle_diagnostic_only_not_a_monthly_schedule"

    single_breaches = portfolio_risk["concentration"]["single_fund_limit_breaches"]
    current_drawdown = Decimal(str(portfolio_risk["current_drawdown_pct"]))
    target_dd = -Decimal(str(config["risk_limits"]["target_drawdown_pct"]))
    stress_dd = -Decimal(str(config["risk_limits"]["stress_drawdown_pct"]))
    if current_drawdown <= stress_dd:
        risk_posture = "risk_veto"
    elif current_drawdown <= target_dd or single_breaches:
        risk_posture = "review_required_no_risk_increasing_action"
    else:
        risk_posture = "within_current_research_limits"

    all_evidence = [portfolio_risk, market_state, trend, drawdown, sleeve_drawdown]
    all_batch_ids = sorted(
        set().union(*(_collect_named_values(item, "batch_ids") for item in all_evidence))
    )
    all_batch_hashes = sorted(
        set().union(
            *(_collect_named_values(item, "batch_content_sha256") for item in all_evidence)
        )
    )
    return {
        "schema_version": 1,
        "pack_id": str(config["pack_id"]),
        "pack_kind": str(config["pack_kind"]),
        "as_of": str(config["as_of"]),
        "mode": "research_only",
        "source_snapshot": {
            "as_of": snapshot["as_of"],
            "batch_id": snapshot["batch_id"],
            "quality_status": snapshot["quality_status"],
            "file_sha256": _file_sha256(snapshot_path),
            "nav_dates_present": all(item.get("nav_date") is not None for item in positions),
        },
        "data_quality": {
            "provider_id": provider,
            "nav_field": str(config["nav_field"]),
            "market_data_as_of": market_as_of.isoformat(),
            "visibility_status": "historical_visibility_assumed",
            "research_only": True,
            "batch_ids": all_batch_ids,
            "batch_content_sha256": all_batch_hashes,
            "warnings": [
                "historical_nav_publication_time_is_assumed",
                "snapshot_positions_do_not_include_nav_dates",
                "snapshot_is_after_the_monthly_signal_review_date_and_was_not_used_to_generate_signals",
            ],
        },
        "current_portfolio": {
            "total_assets_cny": snapshot["total_assets"],
            "cash_cny": snapshot["cash"],
            "largest_position": portfolio_risk["concentration"],
            "risk_proxy": portfolio_risk,
            "market_state": market_state,
            "risk_posture": risk_posture,
            "target_gap_preview": preview,
        },
        "strategy_decision_input": decision_input,
        "research_conclusion": {
            "baseline": "retain_dca_baseline_631",
            "monthly_action_status": "no_new_schedule_generated_mid_cycle",
            "current_risk_response": "do_not_add_to_legacy_overweight_positions_use_future_new_money_only_to_fill_631_gaps",
            "additional_budget_status": "not_adopted_current_concentration_and_short_evidence_require_review",
            "strategy_conflicts": [
                "trend_control_selected_only_us_growth_and_redirected_one_tactical_slot_to_defensive",
                "portfolio_drawdown_control_indicated_500_cny_extra_broad_core_budget",
                "research_controls_do_not_override_the_631_target_authority",
            ],
            "next_valid_decision_date": "2026-09-01",
            "required_before_next_pack": [
                "refresh_read_only_portfolio_snapshot",
                "use_only_local_batches_visible_by_the_new_cutoff",
                "rerun_registry_data_quality_and_risk_gates",
            ],
            "uncertainties": [
                "current_portfolio_risk_is_a_constant_weight_historical_proxy_not_actual_account_path",
                "026211_has_short_history_and_high_recent_volatility",
                "exact_channel_fees_limits_and_qdii_calendar_are_not_fully_verified",
            ],
        },
        "execution": {
            "advisory_only": True,
            "selling_allowed": False,
            "order_intent_generation_allowed": False,
            "real_trading_enabled": False,
            "orders": [],
        },
    }
