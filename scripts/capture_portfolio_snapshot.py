"""Capture a public-safe portfolio snapshot through the aijijin CLI.

The raw CLI response remains in memory and is never written to disk. Only the
redacted PortfolioSnapshot payload is persisted under data/private/.
"""

from __future__ import annotations

import json
import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from invest_agent.domain.portfolio import (
    PortfolioSnapshot,
    PositionStatus,
    PositionSnapshot,
    QualityIssue,
    QualitySeverity,
)
from invest_agent.data.portfolio_store import save_snapshot
from invest_agent.execution.aijijin import validate_endpoint_overrides
import os
from importlib.metadata import version


SNAPSHOT_DIRECTORY = PROJECT_ROOT / "data" / "private" / "portfolio_snapshots"
PENDING_SHARE_MARKERS = {"", "-", "--", "待确认", "确认中", "处理中", "none", "null"}


def decimal_or_issue(
    value: object,
    *,
    field: str,
    fund_code: str | None,
    issues: list[QualityIssue],
) -> Decimal:
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise ValueError("nonfinite")
        return parsed
    except (InvalidOperation, ValueError):
        subject = f" for {fund_code}" if fund_code else ""
        issues.append(
            QualityIssue(
                "invalid_decimal",
                f"Could not parse {field}{subject}",
                QualitySeverity.ERROR,
            )
        )
        return Decimal("0")


def holding_shares_and_status(
    value: object,
    *,
    fund_code: str,
    market_value: Decimal,
    issues: list[QualityIssue],
) -> tuple[Decimal, PositionStatus]:
    text = "" if value is None else str(value).strip()
    try:
        shares = Decimal(text)
        if not shares.is_finite():
            raise ValueError("nonfinite")
    except (InvalidOperation, ValueError):
        if text.lower() in PENDING_SHARE_MARKERS and market_value > 0:
            return Decimal("0"), PositionStatus.PENDING_CONFIRMATION
        issues.append(
            QualityIssue(
                "invalid_decimal",
                f"Could not parse holding shares for {fund_code}",
                QualitySeverity.ERROR,
            )
        )
        return Decimal("0"), PositionStatus.CONFIRMED
    if shares == 0 and market_value > 0:
        return shares, PositionStatus.PENDING_CONFIRMATION
    return shares, PositionStatus.CONFIRMED


def fetch_overview() -> dict[str, object]:
    if version("aijijin-sdk") != "0.2.1":
        raise RuntimeError("Read-only portfolio capture requires reviewed aijijin-sdk 0.2.1")
    validate_endpoint_overrides(os.environ, approved_hosts=("trade.5ifund.com", "fund.10jqka.com.cn"))
    executable = Path(sys.executable).parent / "aijijin"
    result = subprocess.run(
        [str(executable), "holding", "overview"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("The read-only holding overview request failed")
    envelope = json.loads(result.stdout)
    if envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
        raise RuntimeError("The read-only holding overview returned an invalid response")
    return envelope


def fetch_monthly_trades(now: datetime) -> list[dict[str, object]]:
    if version("aijijin-sdk") != "0.2.1":
        raise RuntimeError("Read-only trade capture requires reviewed aijijin-sdk 0.2.1")
    validate_endpoint_overrides(os.environ, approved_hosts=("trade.5ifund.com", "fund.10jqka.com.cn"))
    executable = Path(sys.executable).parent / "aijijin"
    start_date = now.replace(day=1).strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")
    all_records: list[dict[str, object]] = []
    last_accept_time: str | None = None
    last_order_id: str | None = None

    for page in range(1, 4):
        cmd = [
            str(executable),
            "trade",
            "list",
            "--offset",
            str(page),
            "--limit",
            "200",
            "--start-date",
            start_date,
            "--end-date",
            end_date,
            "--business-code",
            "all",
            "--product-type",
            "all",
            "--no-query-processing",
        ]
        if page > 1:
            if not last_accept_time or not last_order_id:
                break
            cmd.extend(["--last-accept-time", last_accept_time, "--last-order-id", last_order_id])
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError("The read-only trade list request failed")
        envelope = json.loads(result.stdout)
        if envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
            raise RuntimeError("The read-only trade list returned an invalid response")
        outer = envelope["data"]
        page_items = outer.get("data") if isinstance(outer.get("data"), list) else []
        if not page_items:
            break
        valid_items = [item for item in page_items if isinstance(item, dict)]
        all_records.extend(valid_items)
        if len(valid_items) < 200:
            break
        tail = valid_items[-1]
        last_accept_time = str(outer.get("lastAcceptTime") or tail.get("acceptTime") or "").strip() or None
        last_order_id = str(outer.get("lastAppSheetSerialNo") or tail.get("appSheetSerialNo") or "").strip() or None
    return all_records


def summarize_monthly_trades(
    records: list[dict[str, object]], *, now: datetime
) -> dict[str, object]:
    month_prefix = now.strftime("%Y-%m")
    aip_total = Decimal("0")
    buy_total = Decimal("0")
    sell_total = Decimal("0")
    pending_total = Decimal("0")
    active_count = 0
    pending_count = 0
    confirmed_count = 0
    by_fund: dict[str, dict[str, object]] = {}
    recent_orders: list[dict[str, object]] = []

    internal_subtypes = {
        "redemption_for_buying",
        "income",
        "award",
        "refund",
        "fund_sold",
    }
    type_labels = {
        "aip": "定投扣款",
        "buy": "主动申购",
        "sell": "赎回",
        "change": "基金转换",
        "dividend": "分红",
    }

    for item in records:
        if not isinstance(item, dict):
            continue
        fund_code = str(item.get("fundCode", "")).strip()
        if len(fund_code) != 6 or not fund_code.isdigit():
            continue
        confirm_flag = str(item.get("confirmFlag", "")).strip()
        pay_flag = str(item.get("payFlag", "0")).strip()
        end_flag = str(item.get("endFlag", "")).strip()
        if confirm_flag in {"1", "4", "6"} or pay_flag in {"1", "2"}:
            continue
        first_type = str(item.get("firstBusinessType", "")).strip().lower()
        sub_type = str(item.get("subBusinessType", "")).strip().lower()
        sub_type_msg = str(item.get("subBusinessTypeMsg") or "").strip()
        business_code = str(item.get("businessCode", "")).strip()
        if sub_type in internal_subtypes or business_code == "098":
            continue
        if first_type == "aip" or "aip" in sub_type or "定投" in sub_type_msg:
            effective_type = "aip"
        else:
            effective_type = first_type
        accept_time = str(item.get("acceptTime", "")).strip()
        accept_date = accept_time[:10] if len(accept_time) >= 10 else now.strftime("%Y-%m-%d")
        if not accept_date.startswith(month_prefix):
            continue

        gross = (
            optional_decimal(item.get("totalFee"))
            or optional_decimal(item.get("ndConfirmedamount"))
            or Decimal("0")
        )
        fail = optional_decimal(item.get("failFee")) or Decimal("0")
        net_amount = max(Decimal("0"), gross - fail).quantize(Decimal("0.01"))
        units_gross = (
            optional_decimal(item.get("ndConfirmedvol"))
            or optional_decimal(item.get("totalFundUnits"))
            or Decimal("0")
        )
        units_fail = optional_decimal(item.get("failFundUnits")) or Decimal("0")
        net_shares = max(Decimal("0"), units_gross - units_fail).quantize(Decimal("0.01"))

        is_pending = confirm_flag == "0" or end_flag == "0"
        status = "pending_confirmation" if is_pending else "confirmed"
        active_count += 1
        if is_pending:
            pending_count += 1
            if effective_type in {"aip", "buy"}:
                pending_total += net_amount
        else:
            confirmed_count += 1

        if effective_type == "aip":
            aip_total += net_amount
        elif effective_type == "buy":
            buy_total += net_amount
        elif effective_type == "sell":
            sell_total += net_amount

        fund_name = str(item.get("fundName", "")).strip() or None
        entry = by_fund.setdefault(
            fund_code,
            {
                "fund_code": fund_code,
                "fund_name": fund_name,
                "aip_cny": Decimal("0"),
                "buy_cny": Decimal("0"),
                "sell_cny": Decimal("0"),
                "pending_cny": Decimal("0"),
                "order_count": 0,
                "latest_accept_date": accept_date,
                "latest_status": status,
            },
        )
        if not entry["fund_name"] and fund_name:
            entry["fund_name"] = fund_name
        entry["order_count"] = int(entry["order_count"]) + 1
        if effective_type == "aip":
            entry["aip_cny"] = Decimal(str(entry["aip_cny"])) + net_amount
        elif effective_type == "buy":
            entry["buy_cny"] = Decimal(str(entry["buy_cny"])) + net_amount
        elif effective_type == "sell":
            entry["sell_cny"] = Decimal(str(entry["sell_cny"])) + net_amount
        if is_pending and effective_type in {"aip", "buy"}:
            entry["pending_cny"] = Decimal(str(entry["pending_cny"])) + net_amount
        if accept_date >= str(entry["latest_accept_date"]):
            entry["latest_accept_date"] = accept_date
            entry["latest_status"] = status

        if len(recent_orders) < 100:
            cfm_date_raw = (
                item.get("transactionCfmDate")
                or item.get("vcTransactioncfmdate")
                or item.get("exceptConfirmTime")
            )
            cfm_date = optional_date(cfm_date_raw)
            recent_orders.append(
                {
                    "accept_date": accept_date,
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "trade_type": effective_type or "other",
                    "trade_type_label": type_labels.get(
                        effective_type, str(item.get("firstBusinessTypeMsg") or "其他")
                    ),
                    "sub_type_label": sub_type_msg,
                    "amount_cny": str(net_amount),
                    "shares": str(net_shares) if net_shares > 0 else None,
                    "status": status,
                    "confirm_date": cfm_date.isoformat() if cfm_date else None,
                }
            )

    serialized_funds = [
        {
            "fund_code": f_code,
            "fund_name": data["fund_name"],
            "aip_cny": str(Decimal(str(data["aip_cny"])).quantize(Decimal("0.01"))),
            "buy_cny": str(Decimal(str(data["buy_cny"])).quantize(Decimal("0.01"))),
            "sell_cny": str(Decimal(str(data["sell_cny"])).quantize(Decimal("0.01"))),
            "pending_cny": str(Decimal(str(data["pending_cny"])).quantize(Decimal("0.01"))),
            "order_count": data["order_count"],
            "latest_accept_date": data["latest_accept_date"],
            "latest_status": data["latest_status"],
        }
        for f_code, data in sorted(by_fund.items())
    ]

    return {
        "month": month_prefix,
        "start_date": now.replace(day=1).strftime("%Y-%m-%d"),
        "end_date": now.strftime("%Y-%m-%d"),
        "source": "thsfund_trade_list_read_only",
        "active_order_count": active_count,
        "pending_order_count": pending_count,
        "confirmed_order_count": confirmed_count,
        "aip_cny": str(aip_total.quantize(Decimal("0.01"))),
        "buy_cny": str(buy_total.quantize(Decimal("0.01"))),
        "sell_cny": str(sell_total.quantize(Decimal("0.01"))),
        "pending_cny": str(pending_total.quantize(Decimal("0.01"))),
        "by_fund": serialized_funds,
        "recent_orders": recent_orders,
    }


def optional_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def optional_date(value: object) -> date | None:
    try:
        text = str(value)
        return datetime.strptime(text, "%Y%m%d").date() if len(text) == 8 else date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_snapshot(
    envelope: dict[str, object],
    *,
    trade_records: list[dict[str, object]] | None = None,
    now: datetime | None = None,
) -> PortfolioSnapshot:
    payload = envelope["data"]
    assert isinstance(payload, dict)
    issues: list[QualityIssue] = []
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))

    wallet = payload.get("wallet")
    if not isinstance(wallet, dict) or wallet.get("ok") is not True:
        issues.append(
            QualityIssue(
                "wallet_unavailable",
                "Wallet data was unavailable",
                QualitySeverity.WARNING,
            )
        )
        cash = Decimal("0")
    else:
        cash = decimal_or_issue(
            wallet.get("bank_total", "0"), field="wallet total", fund_code=None, issues=issues
        )

    fund_api = payload.get("fundApi")
    if isinstance(fund_api, dict) and fund_api.get("failedCategories"):
        issues.append(
            QualityIssue(
                "partial_fund_categories",
                "One or more fund categories were unavailable",
                QualitySeverity.WARNING,
            )
        )

    positions: list[PositionSnapshot] = []
    funds = payload.get("funds")
    if not isinstance(funds, list):
        funds = []
        issues.append(
            QualityIssue(
                "funds_unavailable",
                "Fund position records were unavailable",
                QualitySeverity.ERROR,
            )
        )

    for fund in funds:
        if not isinstance(fund, dict):
            issues.append(
                QualityIssue(
                    "invalid_fund_record",
                    "Ignored a malformed fund position",
                    QualitySeverity.ERROR,
                )
            )
            continue
        fund_code = str(fund.get("fundCode", ""))
        market_value = decimal_or_issue(
            fund.get("totalAmount"), field="market value", fund_code=fund_code, issues=issues
        )
        shares, position_status = holding_shares_and_status(
            fund.get("holdVol"),
            fund_code=fund_code,
            market_value=market_value,
            issues=issues,
        )
        positions.append(
            PositionSnapshot(
                fund_code=fund_code,
                fund_name=str(fund.get("fundName")) if fund.get("fundName") else None,
                shares=shares,
                market_value=market_value,
                status=position_status,
                nav_date=optional_date(fund.get("navDate")),
                holding_income=optional_decimal(fund.get("holdIncome")),
                latest_income=optional_decimal(fund.get("newestIncome")),
                income_date=optional_date(fund.get("newestIncomeDate")),
            )
        )

    trade_reconciliation = (
        summarize_monthly_trades(trade_records, now=now)
        if trade_records is not None
        else None
    )

    return PortfolioSnapshot(
        as_of=now,
        source="thsfund_read_only",
        batch_id=f"thsfund-{now.strftime('%Y%m%dT%H%M%S%z')}",
        cash=cash,
        positions=tuple(positions),
        source_issues=tuple(issues),
        trade_reconciliation=trade_reconciliation,
    )


def persist(snapshot: PortfolioSnapshot, *, database: Path | None = None) -> Path:
    if snapshot.quality_status.value == "fail":
        raise ValueError("Portfolio snapshot failed its quality gate")
    save_snapshot(database or PROJECT_ROOT / "data/private/invest_agent.sqlite3", snapshot.to_public_dict())
    SNAPSHOT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIRECTORY.chmod(0o700)
    target = SNAPSHOT_DIRECTORY / f"{snapshot.batch_id}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(snapshot.to_public_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    target.chmod(0o600)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one redacted, read-only portfolio snapshot")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data/private/invest_agent.sqlite3")
    args = parser.parse_args()
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        envelope = fetch_overview()
        try:
            trade_records: list[dict[str, object]] | None = fetch_monthly_trades(now)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
            trade_records = None
        snapshot = build_snapshot(envelope, trade_records=trade_records, now=now)
        target = persist(snapshot, database=args.database)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        print("Read-only portfolio capture failed; no raw response was saved.", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
