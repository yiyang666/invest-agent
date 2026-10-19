"""Check deterministic 6211 strategy signals for daily automation notification.

Outputs machine-readable JSON and human-friendly Markdown cards.
Can be invoked directly by Codex daily maintenance or external schedulers.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest_agent.web.read_model import Dashboard


def check_daily_signals(root: Path = ROOT) -> dict:
    dashboard = Dashboard(root)
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.date()

    # 1. Evaluate satellite candidate signals & odds ranking
    satellite_data = dashboard.satellite_industries()
    routes = satellite_data.get("candidate_routes", [])
    val_highlights = satellite_data.get("valuation_highlights", {})

    # Sort routes by composite odds score descending
    def get_score(r: dict) -> float:
        sig = r.get("signal", {})
        return float(sig.get("odds_score") or -1.0)

    routes_sorted = sorted(routes, key=get_score, reverse=True)

    stage2_buy = [r for r in routes_sorted if r.get("signal", {}).get("stage") == "stage2_entry_confirmed"]
    stage1_watch = [r for r in routes_sorted if r.get("signal", {}).get("stage") == "stage1_trough_watch"]
    overbought_sell = [r for r in routes_sorted if r.get("signal", {}).get("stage") == "overbought_alert"]

    # 2. Check A-share Core Deep-Value Boost signal (ERP >= 6.15%)
    erp_pct = float(val_highlights.get("csi300_erp_pct") or 0.0)
    core_boost_ready = erp_pct >= 6.15

    # 3. Check Month-End Window (25~28th)
    is_month_end_window = 25 <= today.day <= 28
    is_month_intra_window = today.day <= 24

    # 4. Synthesize action cards
    action_required = False
    action_type = "none"
    alerts: list[dict] = []

    if stage2_buy:
        action_required = True
        action_type = "satellite_buy_stage2"
        top2 = stage2_buy[:2]
        for idx, r in enumerate(top2, 1):
            sig = r.get("signal", {})
            alerts.append({
                "category": "买入信号 · 卫星二阶段企稳",
                "priority": f"第1优先级 (竞价排名 #{idx})",
                "fund_code": r["fund_code"],
                "fund_name": r["fund_name"],
                "theme": r.get("theme", ""),
                "action": "从活期宝钱包 T+0 直购",
                "suggested_amount_cny": 1000.0,
                "odds_score": sig.get("odds_score"),
                "reason": (
                    f"60日回撤 {sig.get('dd_60d_pct')}%，谷底RSI {sig.get('min_rsi_60d')} 出清，"
                    f"右侧反弹 +{sig.get('rebound_from_trough_pct')}% 企稳确认！赔率打分 {sig.get('odds_score')} 分"
                ),
            })

    if overbought_sell:
        action_required = True
        action_type = "satellite_profit_lock"
        for r in overbought_sell:
            sig = r.get("signal", {})
            alerts.append({
                "category": "止盈信号 · 卫星高位收割",
                "priority": "止盈风控",
                "fund_code": r["fund_code"],
                "fund_name": r["fund_name"],
                "theme": r.get("theme", ""),
                "action": "赎回 50% 份额归集至短债/活期宝",
                "odds_score": sig.get("odds_score"),
                "reason": (
                    f"触发过热或高位回撤预警（当前RSI {sig.get('rsi_13')}，距谷底反弹 +{sig.get('rebound_from_trough_pct')}%）。"
                    "请核验持有天数是否满足免赎期（≥7天或≥30天），锁定利润！"
                ),
            })

    if core_boost_ready and not action_required:
        action_required = True
        action_type = "core_deep_value_boost"
        alerts.append({
            "category": "买入信号 · A股核心大底暴击",
            "priority": "第1.5优先级 (极值低估)",
            "fund_code": "110020 / 009051",
            "fund_name": "易方达沪深300(60%) + 华宝红利(40%)",
            "action": "从40%机动池发车一次性申购",
            "suggested_amount_cny": 1000.0,
            "reason": (
                f"沪深300 ERP 达 {erp_pct}% ≥ 6.15% 极值大底区，美股QDII额度受限保持日定投，"
                "A股核心无额度限制，建议动用机动资金暴击加码！"
            ),
        })

    if is_month_end_window and not action_required:
        action_required = True
        action_type = "month_end_collection"
        alerts.append({
            "category": "月度例行 · 月末缓冲归集窗口 (25~28日)",
            "priority": "月末归集",
            "action": "钱包常留 ¥1,200 活期宝缓冲垫，多余资金配置黄金(000216)与短债(006932)",
            "reason": "当月核心定投扣款已基本完成，全月仅此一次检查钱包结余并归集短债，自然躺满30天免赎期。",
        })

    # Generate Feishu / Notification Markdown Card
    card_lines = []
    if action_required:
        card_lines.append(f"🚨 **【6211 投资策略 · 交易信号预警】** ({today})")
        card_lines.append("")
        for a in alerts:
            card_lines.append(f"**[{a['category']}]** · {a.get('fund_code', '')} {a.get('fund_name', '')}")
            card_lines.append(f"• **建议操作**：{a['action']}" + (f" (建议 ¥{a['suggested_amount_cny']})" if "suggested_amount_cny" in a else ""))
            card_lines.append(f"• **触发依据**：{a['reason']}")
            card_lines.append("")
        card_lines.append("⚠️ **执行提示**：遵循《AGENTS.md》安全门禁，系统绝不自动扣款。请在会话中回复确认或在同花顺App手动执行。")
    else:
        window_name = "月末归集窗口 (25~28日)" if is_month_end_window else "月内机动待命期 (1~24日)"
        card_lines.append(f"✅ **【6211 每日巡检正常 · 无需人工操作】** ({today})")
        card_lines.append(f"• **当前阶段**：{window_name}")
        card_lines.append(f"• **卫星观察池**：共 {len(routes)} 只候选（{len(stage1_watch)} 只入坑观察中，0 只触发右侧反弹）")
        top_name = f"{routes_sorted[0]['fund_code']} {routes_sorted[0]['fund_name']}" if routes_sorted else "无"
        top_score = routes_sorted[0].get("signal", {}).get("odds_score") if routes_sorted else "—"
        card_lines.append(f"• **赔率榜首**：{top_name}（{top_score}分，等企稳）")
        card_lines.append(f"• **大势估值**：沪深300 ERP {erp_pct}%（定投实际扣款情况以渠道记录为准）")
        card_lines.append("• **资金状态**：请核对活期宝可用余额；本巡检不确认扣款或资金到账。")

    markdown_message = "\n".join(card_lines)

    return {
        "as_of": now.isoformat(),
        "date": today.isoformat(),
        "action_required": action_required,
        "action_type": action_type,
        "alerts": alerts,
        "stage2_count": len(stage2_buy),
        "stage1_count": len(stage1_watch),
        "overbought_count": len(overbought_sell),
        "erp_pct": erp_pct,
        "markdown_message": markdown_message,
    }


def main():
    parser = argparse.ArgumentParser(description="Check 6211 daily signals")
    parser.add_argument("--json", action="store_true", help="Print json output only")
    parser.add_argument("--notify-only-on-action", action="store_true", help="Exit with code 0 without output if no action required")
    args = parser.parse_args()

    res = check_daily_signals()

    if args.notify_only_on_action and not res["action_required"]:
        sys.exit(0)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["markdown_message"])


if __name__ == "__main__":
    main()
