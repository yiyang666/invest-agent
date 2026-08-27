"""Capture a public-safe portfolio snapshot through the aijijin CLI.

The raw CLI response remains in memory and is never written to disk. Only the
redacted PortfolioSnapshot payload is persisted under data/private/.
"""

from __future__ import annotations

import json
from datetime import datetime
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
        return Decimal(str(value))
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
    executable = Path(sys.executable).parent / "aijijin"
    result = subprocess.run(
        [str(executable), "holding", "overview"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError("The read-only holding overview request failed")
    envelope = json.loads(result.stdout)
    if envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
        raise RuntimeError("The read-only holding overview returned an invalid response")
    return envelope


def build_snapshot(envelope: dict[str, object]) -> PortfolioSnapshot:
    payload = envelope["data"]
    assert isinstance(payload, dict)
    issues: list[QualityIssue] = []
    now = datetime.now(ZoneInfo("Asia/Shanghai"))

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
            )
        )

    return PortfolioSnapshot(
        as_of=now,
        source="thsfund_read_only",
        batch_id=f"thsfund-{now.strftime('%Y%m%dT%H%M%S%z')}",
        cash=cash,
        positions=tuple(positions),
        source_issues=tuple(issues),
    )


def persist(snapshot: PortfolioSnapshot) -> Path:
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
    try:
        envelope = fetch_overview()
        snapshot = build_snapshot(envelope)
        target = persist(snapshot)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(target)
    update = envelope.get("update")
    if isinstance(update, dict) and update.get("latestVersion"):
        print(
            "A thsfund update is available: "
            f"{update['latestVersion']} (upgrade remains disabled until explicitly approved)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
