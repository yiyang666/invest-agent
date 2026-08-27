"""Guarded aijijin CLI boundary and redacted read-only trade-rule parsing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

from invest_agent.approval.contracts import OrderAction, OrderIntent


OVERRIDE_VARIABLES = ("AIJIJIN_GATEWAY_URL", "AIJIJIN_API_BASE_URL")


def normalize_redeem_preview(
    envelope: Mapping[str, object], *, expected_fund_code: str
) -> dict[str, object]:
    """Return only non-sensitive, deterministic facts from ``redeem-render``.

    The raw response may contain a full bank account and transaction metadata, so
    callers must never persist the envelope itself. Rates are returned as decimal
    fractions exactly as supplied by the CLI (``0.015`` means 1.5%).
    """

    if envelope.get("ok") is not True or not isinstance(envelope.get("data"), Mapping):
        raise ValueError("redeem preview returned an invalid envelope")
    outer = envelope["data"]
    payload = outer.get("data")
    if not isinstance(payload, Mapping):
        raise ValueError("redeem preview payload is missing")
    fund = payload.get("fundInfo")
    lots = payload.get("shareHoldTimeList")
    if not isinstance(fund, Mapping) or not isinstance(lots, list):
        raise ValueError("redeem preview fund or holding data is missing")
    if str(fund.get("fundCode", "")) != expected_fund_code:
        raise ValueError("redeem preview fund binding mismatch")

    nav = Decimal(str(fund.get("nav")))
    tiers_raw = fund.get("stepRates")
    if nav <= 0 or not isinstance(tiers_raw, list) or not tiers_raw:
        raise ValueError("redeem preview NAV or fee tiers are invalid")

    tiers: list[dict[str, object]] = []
    for item in tiers_raw:
        if not isinstance(item, Mapping):
            raise ValueError("redeem preview fee tier is invalid")
        lower = Decimal(str(item.get("lwLimit")))
        upper_value = item.get("upLimit")
        rate = Decimal(str(item.get("rate")))
        if lower < 0 or rate < 0:
            raise ValueError("redeem preview fee tier cannot be negative")
        tiers.append(
            {
                "holding_days_lower": str(lower.normalize()),
                "holding_days_upper": (
                    None
                    if upper_value is None
                    else str(Decimal(str(upper_value)).normalize())
                ),
                "lower_exclusive": item.get("containsLwLimit") is True,
                "rate_fraction": str(rate),
            }
        )

    holding_lots: list[dict[str, str]] = []
    estimated_full_redemption_fee = Decimal("0")
    for lot in lots:
        if not isinstance(lot, Mapping):
            raise ValueError("redeem preview holding lot is invalid")
        units = Decimal(str(lot.get("vol")))
        days = Decimal(str(lot.get("day")))
        if units < 0 or days < 0:
            raise ValueError("redeem preview holding lot cannot be negative")
        matching = [
            tier
            for tier in tiers
            if days >= Decimal(str(tier["holding_days_lower"]))
            and (
                tier["holding_days_upper"] is None
                or days < Decimal(str(tier["holding_days_upper"]))
            )
        ]
        if len(matching) != 1:
            raise ValueError("redeem preview holding lot does not match one fee tier")
        rate = Decimal(str(matching[0]["rate_fraction"]))
        estimated_full_redemption_fee += units * nav * rate
        holding_lots.append(
            {
                "units": str(units),
                "holding_days": str(days.normalize()),
                "rate_fraction": str(rate),
            }
        )

    share_list = payload.get("shareList")
    settlement = share_list[0] if isinstance(share_list, list) and share_list else {}
    if not isinstance(settlement, Mapping):
        settlement = {}
    return {
        "source": "aijijin_cli_redeem_render",
        "fund_code": expected_fund_code,
        "fund_name": str(fund.get("fundName", "")),
        "nav": str(nav),
        "nav_date": str(fund.get("navDate", "")),
        "available_units": str(sum(Decimal(lot["units"]) for lot in holding_lots)),
        "minimum_redemption_units": str(fund.get("minRedemptionVol", "")),
        "minimum_remaining_units": str(fund.get("minAccountBalance", "")),
        "can_redeem_to_wallet": str(fund.get("canRedeemToWallet", "")) == "1",
        "fee_tiers": tiers,
        "holding_lots": holding_lots,
        "estimated_full_redemption_fee_cny": str(
            estimated_full_redemption_fee.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        ),
        "estimated_wallet_arrival": str(settlement.get("toDepositTime", "")),
        "estimated_bank_arrival": str(settlement.get("toBankTime", "")),
        "sensitive_account_data_persisted": False,
        "submission_performed": False,
    }


@dataclass(frozen=True)
class ControlledLivePolicy:
    policy_id: str
    status: str
    real_trading_enabled: bool
    allowed_mutations: tuple[str, ...]
    approved_hosts: tuple[str, ...]
    single_purchase_hard_cap_cny: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "ControlledLivePolicy":
        if payload.get("schema_version") != 1:
            raise ValueError("controlled live policy schema_version must be 1")
        environment = payload.get("environment")
        limits = payload.get("proposed_hard_limits_cny")
        if not isinstance(environment, Mapping) or not isinstance(limits, Mapping):
            raise ValueError("controlled live policy is incomplete")
        policy = cls(
            policy_id=str(payload.get("policy_id", "")),
            status=str(payload.get("status", "")),
            real_trading_enabled=payload.get("real_trading_enabled") is True,
            allowed_mutations=tuple(
                str(item) for item in payload.get("allowed_mutations", [])
            ),
            approved_hosts=tuple(
                str(item) for item in environment.get("approved_hosts", [])
            ),
            single_purchase_hard_cap_cny=str(limits.get("single_purchase", "")),
        )
        if policy.status not in (
            "draft_disabled",
            "active_pilot",
            "active_controlled_live",
        ):
            raise ValueError("Phase 7 policy status is unsupported")
        if policy.status == "draft_disabled" and (
            policy.real_trading_enabled or policy.allowed_mutations
        ):
            raise ValueError("a disabled Phase 7 policy cannot enable mutations")
        if policy.status in ("active_pilot", "active_controlled_live") and (
            not policy.real_trading_enabled
            or policy.allowed_mutations != ("purchase",)
        ):
            raise ValueError("the active pilot may enable purchase only")
        if not policy.approved_hosts or not policy.policy_id:
            raise ValueError("controlled live policy identity and hosts are required")
        if Decimal(policy.single_purchase_hard_cap_cny) <= 0:
            raise ValueError("single purchase hard cap must be positive")
        return policy


def load_controlled_live_policy(path: str | Path) -> ControlledLivePolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("controlled live policy must contain one object")
    return ControlledLivePolicy.from_mapping(payload)


def validate_endpoint_overrides(
    environment: Mapping[str, str], *, approved_hosts: Sequence[str]
) -> None:
    approved = set(approved_hosts)
    for name in OVERRIDE_VARIABLES:
        value = environment.get(name)
        if value is None:
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.hostname not in approved:
            raise ValueError(f"unapproved aijijin endpoint override: {name}")


class AijijinCliAdapter:
    """Validate the real CLI shape without exposing a live submission method."""

    def __init__(
        self,
        policy: ControlledLivePolicy,
        *,
        executable: str | Path,
        environment: Mapping[str, str],
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        validate_endpoint_overrides(environment, approved_hosts=policy.approved_hosts)
        self.policy = policy
        self.executable = str(executable)
        self._environment = dict(environment)
        self._runner = runner

    @staticmethod
    def _purchase_args(
        intent: OrderIntent,
        *,
        buy_type: int,
        transaction_account_id: str,
        agreement_record: str | None,
    ) -> list[str]:
        if intent.action is not OrderAction.PURCHASE or intent.amount_cny is None:
            raise ValueError("aijijin purchase adapter requires a purchase intent")
        if buy_type not in (0, 1):
            raise ValueError("buy type must be 0 for bank card or 1 for wallet")
        if not transaction_account_id.strip():
            raise ValueError("transaction account ID is required in memory")
        args = [
            "fund",
            "buy",
            "--buy-type",
            str(buy_type),
            "--fund-code",
            intent.fund_code,
            "--amount",
            str(intent.amount_cny.quantize(Decimal("0.01"))),
            "--transaction-account-id",
            transaction_account_id,
        ]
        if agreement_record is not None:
            if not agreement_record.strip():
                raise ValueError("agreement record cannot be blank")
            args.extend(("--agreement-record", agreement_record))
        return args

    def dry_run_purchase(
        self,
        intent: OrderIntent,
        *,
        buy_type: int,
        transaction_account_id: str,
        agreement_record: str | None = None,
    ) -> dict[str, object]:
        if intent.amount_cny is None or intent.amount_cny > Decimal(
            self.policy.single_purchase_hard_cap_cny
        ):
            raise ValueError("purchase amount exceeds the proposed single-order hard cap")
        command = [
            self.executable,
            *self._purchase_args(
                intent,
                buy_type=buy_type,
                transaction_account_id=transaction_account_id,
                agreement_record=agreement_record,
            ),
            "--dry-run",
        ]
        completed = self._runner(
            command,
            check=False,
            capture_output=True,
            env=self._environment,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise ValueError("aijijin purchase dry-run failed")
        payload = json.loads(completed.stdout)
        if payload.get("ok") is not True or not isinstance(payload.get("data"), Mapping):
            raise ValueError("aijijin purchase dry-run returned an invalid envelope")
        data = payload["data"]
        request = data.get("request")
        if data.get("endpoint") != "buy" or not isinstance(request, Mapping):
            raise ValueError("aijijin purchase dry-run endpoint mismatch")
        if request.get("transactionAccountId") != transaction_account_id:
            raise ValueError("aijijin purchase dry-run account binding mismatch")
        if (
            request.get("fundCode") != intent.fund_code
            or request.get("money") != str(intent.amount_cny.quantize(Decimal("0.01")))
            or request.get("buyType") != str(buy_type)
        ):
            raise ValueError("aijijin purchase dry-run intent binding mismatch")
        return {
            "status": "validated_dry_run_only",
            "endpoint": "buy",
            "fund_code": str(request.get("fundCode")),
            "amount_cny": str(request.get("money")),
            "buy_type": str(request.get("buyType")),
            "transaction_account_bound": True,
            "transaction_account_persisted": False,
            "network_used": False,
            "real_trading_enabled": False,
        }

    def submit_purchase(self, *_: object, **__: object) -> None:
        raise PermissionError(
            "automated live submission is not implemented; use the reviewed exact-order CLI flow"
        )
