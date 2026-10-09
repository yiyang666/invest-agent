"""One-page, read-only view of a validated monthly research report."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


LABELS = {
    "review_required_no_risk_increasing_action": "需复核风险，暂不增加风险敞口",
    "current_portfolio_risk_is_a_constant_weight_historical_proxy_not_actual_account_path": "组合风险是当前权重的历史代理，并非账户真实收益路径",
    "026211_has_short_history_and_high_recent_volatility": "026211 历史较短，近期波动较高",
    "exact_channel_fees_limits_and_qdii_calendar_are_not_fully_verified": "渠道费率、限额和 QDII 日历尚未完整核验",
    "no_new_schedule_generated_mid_cycle": "月中诊断，不生成新的定投日程",
    "do_not_add_to_legacy_overweight_positions_use_future_new_money_only_to_fill_631_gaps": "暂停加仓已超配的旧持仓，未来新增资金优先补足 631 缺口",
    "not_adopted_current_concentration_and_short_evidence_require_review": "暂不启用额外预算，先复核集中度和证据",
    "retain_dca_baseline_631": "继续以 631 为比较基准",
    "limited_research_evidence": "研究证据有限",
}


def _label(value: object) -> str:
    text = str(value)
    return LABELS.get(text, text)


def build_monthly_brief(manifest: Mapping[str, Any], *, root: Path, today: date) -> str:
    if manifest.get("summary", {}).get("publication_gate") != "passed":
        raise ValueError("monthly pipeline has not passed")
    report_date = date.fromisoformat(str(manifest["as_of"])[:10])
    if (report_date.year, report_date.month) != (today.year, today.month):
        raise ValueError("monthly report is not from the current month")
    item = manifest["artifacts"]["research_report_json"]
    path = (root / item["path"]).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("report path escapes workspace")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("monthly report hash mismatch")
    report = json.loads(raw)
    if report.get("as_of") != manifest["as_of"]:
        raise ValueError("monthly report date mismatch")
    if report.get("execution", {}).get("orders") != []:
        raise ValueError("brief requires a zero-order research report")
    state = report["portfolio_state"]
    decision = report["decision_summary"]
    executive = report["executive_summary"]
    breaches = state.get("single_fund_limit_breaches", [])
    risks = (
        [f"{x['fund_code']} 占比 {x['actual_pct']:.1f}%，超过单基金 {x['limit_pct']:.1f}% 限制" for x in breaches]
        if breaches else ["未发现单基金集中度超限；仍需查看完整风险报告"]
    )
    uncertainties = report.get("uncertainties", [])
    lines = [
        f"# {report_date:%Y年%m月} 投资决策一页纸",
        "",
        f"证据日期：{report_date.isoformat()}｜状态：{_label(executive['status'])}｜仅供研究与人工判断",
        "",
        "## 现状",
        "",
        f"- 组合资产：{state['total_assets_cny']} 元；现金：{state['cash_cny']} 元。",
        f"- {executive['headline']}",
        "",
        "## 风险",
        "",
        *[f"- {risk}" for risk in risks],
        *[f"- 待核实：{_label(value)}" for value in uncertainties],
        "",
        "## 行动草案",
        "",
        f"- 月度状态：{_label(decision['monthly_action_status'])}。",
        f"- 当前风险处理：{_label(decision['current_risk_response'])}。",
        f"- 额外预算：{_label(decision['additional_budget_status'])}。",
        "",
        "## 理由与下次复核",
        "",
        f"- 基准：{_label(decision['baseline'])}；证据等级：{_label(executive['evidence_confidence'])}。",
        f"- 下次复核：{report['next_review']['date']}。",
        "- 本页不构成订单；详细证据见对应月度报告。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the current month's research decision on one page")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=Path("."))
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        brief = build_monthly_brief(
            json.loads(args.manifest.read_text(encoding="utf-8")),
            root=args.workspace_root,
            today=args.today,
        )
        if args.output:
            path = args.output.resolve()
            if args.workspace_root.resolve() not in path.parents:
                raise ValueError("output path escapes workspace")
            if path.exists():
                raise ValueError("refusing to overwrite existing brief")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(brief, encoding="utf-8")
        else:
            print(brief, end="")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
