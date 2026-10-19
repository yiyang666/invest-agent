"""Synchronize public channel fee and redemption rules for all watched funds via aijijin CLI."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def _parse_pct(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "--" or "元" in text:
        return None
    if text.endswith("%"):
        try:
            return round(float(text[:-1]) / 100.0, 6)
        except ValueError:
            return None
    try:
        return round(float(text), 6)
    except ValueError:
        return None


def _to_days(value: str, unit: str) -> int:
    num = int(value)
    if unit == "年":
        return num * 365
    if unit == "月" or unit == "个月":
        return num * 30
    return num


def _parse_redemption_tier(item: dict) -> dict:
    label = str(item.get("time", "")).strip()
    rate_str = str(item.get("rate", "")).strip()
    rate = _parse_pct(rate_str)
    min_days = 0
    max_days: int | None = None

    m_below = re.search(r"^(\d+)(天|个月|月|年)以下", label)
    m_range = re.search(r"^(\d+)(天|个月|月|年).*?-(\d+)(天|个月|月|年)", label)
    m_above = re.search(r"^(\d+)(天|个月|月|年)以上", label)
    if m_below:
        min_days = 0
        max_days = _to_days(m_below.group(1), m_below.group(2))
    elif m_range:
        min_days = _to_days(m_range.group(1), m_range.group(2))
        max_days = _to_days(m_range.group(3), m_range.group(4))
    elif m_above:
        min_days = _to_days(m_above.group(1), m_above.group(2))
        max_days = None

    return {
        "label": label,
        "rate_display": rate_str,
        "rate": rate if rate is not None else 0.0,
        "minimum_holding_days_inclusive": min_days,
        "maximum_holding_days_exclusive": max_days,
    }


def fetch_fee_rule(aijijin_bin: Path, fund_code: str) -> dict:
    proc = subprocess.run(
        [str(aijijin_bin), "fund", "fee-rule", "--fund-code", fund_code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"aijijin fee-rule failed for {fund_code}: exit={proc.returncode}")
    outer = json.loads(proc.stdout)
    if not outer.get("ok"):
        raise RuntimeError(f"aijijin fee-rule error for {fund_code}: {outer.get('error')}")
    inner = outer.get("data", {}).get("data", {})
    rate_info = inner.get("rateInfo", {})
    trade_rule = inner.get("tradeRule", {})
    fund_info = inner.get("fundinfo", {})

    sg_qd = rate_info.get("sg", {}).get("qd", []) or []
    first_sg = sg_qd[0] if sg_qd else {}
    raw_sg_rate = _parse_pct(first_sg.get("rate"))
    disc_sg_rate = _parse_pct(first_sg.get("irate"))
    discount_val = _parse_pct(rate_info.get("discount"))
    if disc_sg_rate is None and raw_sg_rate is not None and discount_val is not None:
        disc_sg_rate = round(raw_sg_rate * discount_val, 6)

    sh_list = [_parse_redemption_tier(x) for x in (rate_info.get("sh") or [])]

    zero_fee_days: int | None = None
    min_terminal_rate: float | None = None
    min_terminal_days: int | None = None
    for tier in sh_list:
        r = tier["rate"]
        d = tier["minimum_holding_days_inclusive"]
        if r == 0.0 and zero_fee_days is None:
            zero_fee_days = d
        if min_terminal_rate is None or r < min_terminal_rate:
            min_terminal_rate = r
            min_terminal_days = d

    buy_steps = {x.get("title"): x.get("showtime") for x in (trade_rule.get("tradeRuleBuy") or [])}
    sell_steps = {x.get("title"): x.get("showtime") for x in (trade_rule.get("tradeRuleSale") or [])}

    return {
        "fund_code": fund_code,
        "minimum_purchase_cny": fund_info.get("zdsg"),
        "channel_fund_type": fund_info.get("type"),
        "statutory_first_tier_purchase_fee_rate": raw_sg_rate,
        "discounted_first_tier_purchase_fee_rate": disc_sg_rate,
        "channel_purchase_discount": rate_info.get("discount"),
        "management_fee_rate_display": rate_info.get("glf"),
        "custodian_fee_rate_display": rate_info.get("tgf"),
        "sales_service_fee_rate_display": rate_info.get("fwf"),
        "redemption_fee_tiers": sh_list,
        "zero_redemption_fee_min_holding_days": zero_fee_days,
        "lowest_redemption_fee_rate": min_terminal_rate,
        "lowest_redemption_fee_min_holding_days": min_terminal_days,
        "buy_confirm_timing": buy_steps.get("确认份额"),
        "sell_confirm_timing": sell_steps.get("确认份额"),
        "sell_arrival_timing": sell_steps.get("资金到账"),
    }


def main() -> int:
    catalog_path = ROOT / "config/fund_watch_catalog_v1.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    aijijin_bin = ROOT / ".conda-env/bin/aijijin"
    now_iso = datetime.now(TZ).isoformat(timespec="seconds")

    rules = []
    for item in catalog.get("classifications", []):
        code = item["fund_code"]
        fetched = fetch_fee_rule(aijijin_bin, code)
        rules.append(
            {
                "fund_code": code,
                "fund_name": item["fund_name"],
                "position_bucket": item["position_bucket"],
                "sleeve": item["sleeve"],
                "fund_type": item["fund_type"],
                **fetched,
                "observed_at": now_iso,
            }
        )
        print(
            f"[{code}] {item['fund_name']}: purchase={fetched['discounted_first_tier_purchase_fee_rate']} "
            f"zero_redeem_days={fetched['zero_redemption_fee_min_holding_days']} "
            f"lowest_redeem={fetched['lowest_redemption_fee_rate']}@{fetched['lowest_redemption_fee_min_holding_days']}d",
            flush=True,
        )

    out_payload = {
        "schema_version": 1,
        "catalog_id": "fund_fee_rules_v1",
        "observed_at": now_iso,
        "channel": "aijijin_thsfund_current_channel",
        "mode": "sanitized_read_only_fee_rule_evidence",
        "source_command": "aijijin fund fee-rule --fund-code <code>",
        "privacy_filter": {
            "raw_protected_response_persisted": False,
            "account_fields_persisted": False,
            "customer_fields_persisted": False,
        },
        "funds": rules,
    }
    out_path = ROOT / "config/fund_fee_rules_v1.json"
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(rules)} fund fee rules to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
