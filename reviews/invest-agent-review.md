# Invest-Agent 仓库评审报告

作者: GitHub Copilot
日期: 2026-08-27

## 一、概览与结论

Invest-Agent 是一个面向个人场外基金投研与辅助决策的系统，定位清晰：把数据采集、校验、确定性计算、回测与独立风控连成一条可审计、可回放的链路，并在人工确认下执行受控申购。该项目在产品定位、架构边界与安全约束上做了非常严格的工程化设计，适合用于个人研究台、策略验证与受控半自动化执行（逐笔受控申购）。如果把目标限定为“研究 + 建议 + 受控人工执行”，项目价值高且可落地；如果目标是“无人值守自动交易”，则需显著补强合规与安全审计。

总体评级（专业角度）: 具备高研究价值 + 中等到高工程成熟度，但在外部依赖审计、合规证明与可操作化监控上仍有可交付的工作。

## 二、我已审阅的关键文件（证据）

- README.md — 项目定位、功能清单、运行指引和安全边界（逐笔受控、dry-run）
- AGENTS.md — Agent 行为规范与项目级安全/架构约束
- docs/architecture.md — 数据流、分层职责、核心对象与执行状态机
- docs/decisions/README.md — ADR 总览（关于交易、Skill 隔离、数据门等多个关键决策）

这些文档一致地表达同一套安全第一、分层明确、确定性优先的设计原则，这是项目最大的强项。

## 三、核心优势（为什么有价值）

1. 明确的安全与控制边界：逐笔受控申购、一次性确认失效逻辑、未知状态阻断、禁止自动重试等，降低误操作与自动化风险。
2. 可复现的研究与回放能力：数据仓、回测门禁、证据哈希与Manifest为策略验证提供可审计链路。
3. 模块化边界清晰：skills/adapters、strategies、backtest、risk、decision、execution 的分层便于代码审查与责任隔离。
4. 工程实践意识强：环境文件、受审查的 SDK、测试套（231 项测试）以及 ADR 流程表明项目作者重视长期可维护性。

## 四、主要风险与问题

1. 合规与法律风险（高优先级）
   - 绑定第三方（同花顺/爱基金/券商）与真实下单会触发金融监管、KYC/AML、支付与责任分配问题。若对外提供服务，须尽早咨询法律与合规专家。

2. 依赖与供应链安全（中高）
   - 采用第三方 Skill 与 SDK（例如 aijijin-sdk）时，需要固定版本与校验哈希并加入 CI 的 SCA（pip-audit/safety）。文档有提及，但需要在 CI 强制执行并记录审批链。

3. 凭证与密钥管理（中）
   - README 指出凭证不入仓，但需在代码/CI 层面强制阻断（pre-commit + CI scan）并提供示例 secret 管理流程（GitHub Secrets、KMS、OS keyring）。

4. 运行与生产安全（中）
   - 逐笔申购逻辑要在网络超时、未知状态、竞态条件下保证幂等与安全。需要明确 order intent 的幂等 key、重放策略与回滚 SOP。

5. 可观测性与对账（中）
   - 虽有对账说明，但应完善监控告警阈值（订单对账失败率、未知状态占比、数据质量门禁触发率）与告警运行手册。

## 五、具体改进建议（可操作）

优先级分为 P0（立即）、P1（短期）、P2（中期）。

P0 — 安全与合规
- 在 CI 中加入 secrets 检测（detect-secrets/git-secrets）与依赖审计（pip-audit/safety），阻止高危依赖合并。
- 在 README/OP 或首次运行脚本中强制 EXECUTION_MODE 环境变量（advisory_only|controlled_live|production），并在代码路径中断言模式。
- 明确法律/合规负责人与审查点（例如：当��入真实支付或对外服务前需法律确认）。

P1 — 灾难恢复与幂等
- 为 OrderIntent 引入 idempotency_key，记录在 ApprovalRecord 与 ExecutionRecord 中。实现幂等提交与重复确认检测。
- 把对账逻辑自动化：实现每日/每小时 reconcile job，生成对账报告并在异常时自动创建 issue 或发送告警。

P1 — 测试与数据
- 增强回测可复现性（数据包版本化，DVC 或数据包哈希），并把回测报告作为 CI artifact。
- 将策略单元测试（纯函数）与 integration 测试（mock 外部接口）分开，并在 PR 中强制运行。

P2 — 可解释性与用户体验
- 每个建议/订单都输出“解释包”：触发因子、历史样本、置信度、回测摘要与替代假设。
- 在 CLI/WEB 中把确认快照做成可导出（PDF/HTML）并保存哈希以便留证。

## 六、代码与工程实践建议（技术细节）

1. 强制类型与静态检查：mypy + pydantic schema 用于核心边界对象（OrderIntent, PortfolioSnapshot 等）。
2. 格式化与 lint：black + ruff/flake8，CI 中运行并在 PR 上阻止失败。 
3. 依赖锁定：采用 requirements.txt + pip-tools 或 poetry/pyproject，记录依赖哈希并在 docs/ 里保存审核记录。
4. Secrets 与凭证管理：示例：GitHub Actions secrets、本地使用 OS keyring，并在 docs/integrations/* 里写清楚获取与配置步骤。
5. 审计日志：实现 append-only 审计表（数据库或受限对象存储），记录 approve/submit/confirm 操作的请求体、快照哈希 与 用户 ID（脱敏）。

## 七、文档与社区建议

- 补充 SECURITY.md、CONTRIBUTING.md、CODE_OF_CONDUCT（若考虑开源社区参与）。
- 在 README 中增加“如何在沙箱运行一步步演示”短教程（含最小数据、mock keys）。
- 在 docs/decisions/ 下，把每个关键 ADR 链接到实现文件（例如：ADR-0002 -> .agents/skills readme 或对应目录）。

## 八、建议的下一步工作清单

1. P0 安全加固：CI 秘密扫描 + 依赖审计（1–3 天）。
2. 订单幂等与对账自动化（1–2 周）。
3. 把 ApprovalRecord 与 ExecutionRecord 的 schema 用 pydantic 明确，并添加单元测试（1 周）。
4. 法律合规咨询（并行进行）。
5. 用户确认快照快速组件（CLI 模板 + HTML/PDF 导出）并在回测报告中包含（2 周）。

## 九、如何把这个 MR/评审保存为 PDF

既然你更愿意在仓库中查看，我已经将评审保存为 Markdown 文件：

- 路径: reviews/invest-agent-review.md

你可以在仓库中直接查看或下载该文件。

---

如果你希望我做更深入的代码级审计（逐文件检查、PR 草案、或生成修复补丁），我可以继续：
- 选项 A：逐文件审查（我会对每个 top-level 目录中的代表文件给出更细致的建议）
- 选项 B：生成可合并 PR（例如 CI 改造、pre-commit、依赖审计工作流）

请选择你下一步想要的操作。
