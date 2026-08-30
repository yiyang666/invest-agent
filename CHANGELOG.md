# 版本记录（Changelog）

版本事实以本文件、仓库根目录的 `VERSION` 与 Git 标签（`v*`）为准。每个版本记录发布时间、主要工作与遗留工作；聊天记录和模型记忆不是版本真相。

## V1.0.1 — 2026-08-30

**主要工作**

- 市场数据链：`invest_agent.market_data` 原始先归档/标准化/质量门禁/可重放的隔离市场数据仓；接入股叉叉估值/权重/前瞻PE/拥挤度/受限宏观序列、FRED NFCI、已校验基金净值趋势代理、全A聚合宽度，上交所沪市A股宽度保留为手工校验。
- 全球市场状态：`invest_agent.market_regime` 七轴 `GlobalMarketStateSnapshot` v0.5，显式报告覆盖率/置信度/来源数与缺失能力，覆盖不足失败关闭，保持 `research_only`。
- 统一维护：`invest_agent.automation maintenance_cli` 将基金与市场数据合并为日/周/月/季任务注册表，单一 Codex heartbeat 触发与解释；launchd 仅保留为未启用备用。
- 归因扩展：`buy-only-sleeves` 现金守恒审计、`traffic-light-events` 前瞻事件评价、研究审计模块。
- 卫星策略研究：`satellite_risk_budget` 与 `satellite_traffic_light` 0.2.0，分层卫星策略回测/归因协议、事件研究及证据文档。
- 项目级 Skills：新增 `market-context-research` 与 `market-data-collect`；ADR-0029/0030/0031 及架构、能力目录、路线图、操作手册同步更新。

**遗留工作**

- `GlobalMarketStateSnapshot` 剩余缺口：日本估值、欧洲/日本增长周期、全球宽度、跨市场资金流。
- 严格时点历史与前瞻样本外证据需按月积累；市场状态尚未达到正式决策资格（≥80% 覆盖、全部严格时点、两个独立来源）。
- 分层卫星策略仅 `research_only`，未晋级 631 或生产执行。
- 赎回、撤单、无人值守交易与自动重试继续关闭。

## V1.0.0 — 2026-08-27

**主要工作**

- Phase 0–5 研究闭环收口：投资政策、安全边界与架构冻结；爱基金只读账户与本地基金数据仓；指标；场外基金回测、631 基准与策略注册表；风险门禁与月度决策包。
- Phase 6 人工审批与模拟执行：15 分钟一次性批准、SQLite 原子额度门禁、`submitting/unknown/reconciled` 状态机与未知状态先对账。
- Phase 7 受控实盘：首笔逐笔申购 006932（500 元）完成确认，并与实时持仓双重对账；赎回/撤单保持关闭。
- Phase 8 归因与生命周期：现金流、组合、袖套、顺序消融四层归因及人工评审状态。

**遗留工作**

- 严格前瞻样本外（OOS）证据不足 12 个月，策略晋级门未开放。
- 市场数据链与全球市场状态尚未接入（由 V1.0.1 补齐）。
- 赎回、撤单与无人值守执行仍关闭。

## 初始提交 — 2026-08-17（版本化之前）

**主要工作**

- Phase 0–5 研究地基：数据采集/同步、指标、研究回测、631 基准、策略注册表与月度流水线雏形。
- 项目级 Skill：`fund-data-collect`、`fund-metric-calc`、`design-fund-strategy`、`verify-fund-trading-rules`、`thsfund`。

**遗留工作**

- 尚未版本化；自 V1.0.0 起正式记录版本。
