"""Private, append-only storage for allowlisted portfolio snapshots."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def _number(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("snapshot numbers must be finite")
    return str(parsed)


def clean_snapshot(payload: dict) -> dict:
    """Only explicit public-safe fields can reach SQLite or the Web API."""
    captured = datetime.fromisoformat(payload["as_of"])
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("snapshot needs an aware capture time")
    if payload.get("quality_status") not in {"pass", "partial"}:
        raise ValueError("failed portfolio snapshots cannot be published")
    captured = captured.astimezone(ZoneInfo("Asia/Shanghai"))
    positions = []
    seen = set()
    for item in payload["positions"]:
        code = str(item["fund_code"])
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            raise ValueError("invalid or duplicate snapshot fund")
        seen.add(code)
        if item.get("fund_name") is not None and not isinstance(item["fund_name"], str):
            raise ValueError("fund name must be text")
        if item.get("status", "confirmed") not in {"confirmed", "pending_confirmation"}:
            raise ValueError("invalid position status")
        row = {"fund_code": code, "fund_name": item.get("fund_name"),
               "status": item.get("status", "confirmed")}
        for field in ("shares", "market_value", "weight"):
            row[field] = _number(item[field])
            if Decimal(row[field]) < 0:
                raise ValueError("negative portfolio amount")
        for field in ("holding_income", "latest_income"):
            row[field] = _number(item.get(field), optional=True)
        for field in ("nav_date", "income_date"):
            row[field] = date.fromisoformat(item[field]).isoformat() if item.get(field) else None
        positions.append(row)
    cash = _number(payload["cash"])
    total = _number(payload["total_assets"])
    if Decimal(cash) < 0 or Decimal(total) < 0:
        raise ValueError("negative snapshot total")
    if abs(Decimal(total) - Decimal(cash) - sum(Decimal(p["market_value"]) for p in positions)) > Decimal("0.01"):
        raise ValueError("snapshot values do not reconcile")
    return {
        "as_of": captured.isoformat(), "source": "thsfund_read_only",
        "quality_status": payload["quality_status"],
        "cash": cash, "total_assets": total,
        "total_fund_value": str(sum(Decimal(p["market_value"]) for p in positions)),
        "positions": positions,
        "quality_issues": [{"code": str(x.get("code", "unknown")), "severity": str(x.get("severity", "warning"))}
                           for x in payload.get("quality_issues", [])],
    }


def save_snapshot(database: Path, payload: dict) -> str:
    clean = clean_snapshot(payload)
    raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    database.parent.mkdir(parents=True, exist_ok=True)
    database.parent.chmod(0o700)
    with sqlite3.connect(database, timeout=15) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            snapshot_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
            quality_status TEXT NOT NULL, payload_json TEXT NOT NULL)""")
        connection.execute("INSERT OR IGNORE INTO portfolio_snapshots VALUES (?,?,?,?)",
                           (digest, clean["as_of"], clean["quality_status"], raw))
    database.chmod(0o600)
    return digest


def import_snapshots(database: Path, directory: Path) -> dict:
    imported = rejected = 0
    for path in sorted(directory.glob("*.json")):
        try:
            save_snapshot(database, json.loads(path.read_text()))
            imported += 1
        except (ValueError, KeyError, TypeError, InvalidOperation):
            rejected += 1
    return {"imported": imported, "rejected": rejected}
