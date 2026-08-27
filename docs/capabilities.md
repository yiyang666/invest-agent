# 系统能力与接口目录

本文件是新会话确认能力边界的权威入口。它回答三个问题：系统已经会什么、Agent应优先调用什么、哪些能力尚未接通。聊天中的临时描述不能覆盖这里、`AGENTS.md`、投资政策或代码测试。

## 1. 能力是怎样串起来的

```text
公开基金源 / 爱基金只读账户 / 已审查MCP发现
                    ↓
          原始归档、脱敏、质量门禁
                    ↓
              本地SQLite校验仓
                    ↓
       指标 → 策略 → 回测 → 独立风控
                    ↓
       决策报告 → 人工批准 → 受控申购
                    ↓
              对账 → 四层归因
```

- **CLI / Python模块**是确定性能力：负责取数、计算、校验、状态机和产物哈希。
- **项目级 Skill**是Agent操作规程：负责把自然语言意图路由到正确CLI，并约束来源、安全和解释方式。
- **MCP**是外部能力入口：当前只用于受控发现；远端返回不能越过本地数据门禁直接进入策略或交易。
- **LLM/Agent**负责编排、解释、发现缺口和呈现冲突；不在提示词里临时计算金额、改策略数字或绕过风控。

## 2. 功能层接口

| 层 | Python入口 | CLI / 脚本入口 | 主要输入与输出 | 网络或外部副作用 |
|---|---|---|---|---|
| 账户快照 | `invest_agent.domain.portfolio` | `scripts/capture_portfolio_snapshot.py` | 爱基金只读账户 → 脱敏`PortfolioSnapshot` | 只读账户；输出仅进`data/private/` |
| 数据采集 | `invest_agent.data` | `python -m invest_agent.data.cli` | 公开响应/授权CSV → 原始批次、质量报告、本地SQLite | 采集命令可联网；不运行策略或交易 |
| 数据同步 | `invest_agent.data.sync` | `python -m invest_agent.data.sync_cli`、`scripts/run_scheduled_fund_data_sync.sh` | 配置基金全集 → 增量请求、发布/拒绝报告 | 可联网；失败不发布、不补模拟值 |
| 指标 | `invest_agent.metrics` | `python -m invest_agent.metrics.cli` | 本地净值/快照 → 收益、波动、回撤、相关性、市场状态 | 纯本地只读 |
| 策略 | `invest_agent.strategies` | `python -m invest_agent.strategies.cli` | 版本化规格+时点数据 → 定投计划或研究信号 | 纯本地；不生成真实订单 |
| 回测 | `invest_agent.backtest` | `python -m invest_agent.backtest.cli` | 场景、规格、本地数据 → 账本、路径指标、对比报告 | 纯本地；`research_only` |
| 风控 | `invest_agent.risk` | `python -m invest_agent.risk.cli` | 组合/情景 → 压力损失、门禁、否决 | 纯本地；拥有否决权 |
| 决策 | `invest_agent.decision` | `python -m invest_agent.decision.*_cli` | 注册策略、快照、证据 → 月度包、报告、解释、Manifest | 纯本地；默认零订单 |
| 批准与执行 | `invest_agent.approval`、`invest_agent.execution` | `python -m invest_agent.execution.mock_cli`；受审查`aijijin` CLI | 精确摘要 → 一次性批准、提交状态与对账 | mock无网络；真实申购逐笔确认，不能自动提交 |
| 归因 | `invest_agent.attribution` | `python -m invest_agent.attribution.cli` | 现金流/组合/袖套/消融证据 → 归因与生命周期状态 | 纯本地；不生成订单 |

### 2.1 数据CLI

`invest_agent.data.cli`：

- `init-store`：创建本地SQLite结构；
- `ingest-csv`：归档并导入授权净值CSV；
- `collect-akshare-eastmoney`：东财历史净值与分红；
- `collect-akshare-ths-metadata`：同花顺基金元数据；
- `collect-akshare-ths-daily`：同花顺指定日期净值抽检；
- `collect-penghua-official`：鹏华基金公司公开净值；
- `replay-akshare-ths-daily`、`replay-eastmoney-distributions`：从不可变原始批次重放；
- `crosscheck-nav`：跨来源冲突检查，不静默选值；
- `inspect`：检查批次和质量状态。

`invest_agent.data.sync_cli`负责基金全集增量同步，支持`--dry-run`。基金全集由政策基金、当前可购路由、研究候选和真实持仓合并；不支持交易的基金不会进入购买候选，但已有持仓仍会更新净值。

### 2.2 指标CLI

`invest_agent.metrics.cli`子命令：

- `fund`：区间收益、年化收益/波动、最大回撤及修复；
- `correlation`、`rolling-correlation`：共同交易日相关性；
- `portfolio`：当前权重组合风险代理与集中度；
- `market-state`：只使用截止日以前数据的可解释市场状态；
- `drawdown-event-panel`：多基金回撤事件面板。

指标只读本地校验仓，不在计算时调用网页、AKShare、MCP或账户API。

### 2.3 策略与回测CLI

`invest_agent.strategies.cli`当前提供：

- `dca-plan`：目标缺口定投、三次扣款、限额多日/跨月队列；
- `trend-rs-signal`：新增资金趋势与相对强弱研究信号；
- `drawdown-add-signal`：回撤预算加仓研究信号。

`invest_agent.backtest.cli`当前提供：

- 场外语义：`subscriptions`、`valuations`、`local-dca-research`；
- 稳健性：`sensitivity-matrix`、`rolling-windows`、`candidate-compare`；
- 既有策略比较：`trend-robustness`、`drawdown-compare`、`drawdown-robustness`、`sleeve-drawdown-compare`；
- 公平比较：`pair-cashflow-compare`、`same-universe-compare`。

引擎显式模拟申购费、T+确认、在途现金、份额、单位净值估值、分红、限额、共享额度、多路由和跨月队列。它没有通用高频、杠杆、衍生品或任意卖出择时引擎。

### 2.4 风控、决策与报告CLI

- `invest_agent.risk.cli stress`：确定性压力情景与组合风险否决；
- `invest_agent.decision.cli validate-registry/build`：验证策略注册表并组合时点信号；
- `monthly_cli`：生成非执行月度决策包；
- `report_cli`：生成证据绑定的JSON/Markdown研究报告；
- `explanation_cli`：生成并验收不篡改数字的解释；
- `pipeline_cli`：按“决策包→报告→解释→验收→Manifest”运行离线流水线；
- `replay_cli`、`closure_cli`：安全故障重放与阶段审计。

### 2.5 批准、交易和规则核验

项目批准/执行层实现了15分钟一次性精确批准、关键字段变化失效、SQLite原子额度门禁、`submitting/unknown/reconciled`状态机和未知状态先对账。

爱基金`aijijin` CLI提供：

- 账户与订单只读：`holding`、`trade`；
- 当前规则只读：`fund fee-rule`、`fund subscribe-init`、`fund redeem-render`；
- 外部写操作：`fund buy`、`fund redeem`及撤单命令。

当前项目只开放逐笔受控申购。赎回、撤单、无人值守提交和异常自动重试仍关闭；真实写操作不能由研究Skill、策略、回测或MCP触发。

### 2.6 归因CLI

`invest_agent.attribution.cli`子命令：

- `cashflow`：本金、投资损益、费用、现金拖累、Modified Dietz与链接TWR；
- `matched`：候选与631的同期间、同现金流比较；
- `sleeves`：单期Brinson配置、选择、交互效应；
- `waterfall`：运行前冻结的策略消融瀑布；
- `lifecycle`：继续观察、阻断、修订、风险否决或人工评审。

资产池或长期权重变化只能标记为`total_policy_outcome`；只有同资产、同路线和同现金流控制下的增量才可标记为`signal_effect`。

## 3. 项目级 Skills

| Skill | 何时触发 | 首选能力 | 硬边界 |
|---|---|---|---|
| `fund-data-collect` | 补数、同步、检查批次或数据质量 | 数据采集/同步CLI | 原始先归档；失败不补模拟数据；不交易 |
| `fund-metric-calc` | 收益、波动、回撤、相关性、持仓风险 | 指标CLI | 只读本地校验仓，不现场联网 |
| `design-fund-strategy` | 新策略、新基金、新袖套、对比631、策略回测 | 提案→发现→规格→测试→回测→归因 | `research_only`；结果后改参必须新版本 |
| `verify-fund-trading-rules` | 可购/限额/申赎费/到账/赎回档位 | 爱基金CLI只读预检优先 | 不用买入试单取数；不保存账户原始响应 |
| `thsfund` | 持仓、钱包、订单、申购/赎回/撤单意图 | 受审查`aijijin` CLI | 写操作逐笔确认；项目政策优先；禁止自动升级 |

Skill位于`.agents/skills/`。第三方`thsfund`与SDK固定为已审查的0.2.0版本；项目自建Skill可根据真实跑偏案例继续迭代。

## 4. 当前项目MCP

项目配置文件`.codex/config.toml`当前只登记一个MCP：

| MCP | 已允许工具 | 状态 | 当前用途 |
|---|---|---|---|
| 股叉叉 `guchacha` | `list_datasets`、`get_index_valuation`、`get_index_weight`、`get_index_forward_pe` | 已注册、Bearer环境变量认证、每次调用提示批准 | `discovery_only`指数估值/权重发现 |

MCP返回必须经过原始归档、schema/日期/单位/完整性检查、第二来源复核和单独晋级，才能进入本地校验仓。当前尚未接入数据采集适配器、指标、回测、策略或交易。新会话若没有显示这些工具，应先重新加载项目或检查`codex mcp list`，不能假装已经调用。

Codex自身提供的浏览器、网页、文档等通用工具属于运行环境能力，不等于本仓库已接入的投资数据MCP。

## 5. 当前没有的能力

- 免费实时基金持仓穿透或基金经理实时调仓；
- 全渠道、全历史的精确费率、限额和到账规则；
- 新闻驱动的短线预测器；
- 任意卖出择时、场内高频、杠杆或衍生品交易；
- 无人值守真实交易；
- 已满12个月的严格前瞻策略晋级证据；
- `xalpha`、`cjquant`的当前运行时集成。它们曾作为方案参考，但现有回测由项目自己的场外事件引擎承担。

## 6. 新会话复用协议

新Agent开始工作时应先阅读`AGENTS.md`规定的权威文件，并做到：

1. 先在本目录确认已有CLI/Skill/MCP，不重复造轮子；
2. 数据不足时走`fund-data-collect`，计算时只读本地校验仓；
3. 动态交易规则走`verify-fund-trading-rules`，爱基金CLI优先；
4. 新策略走`design-fund-strategy`，冻结规则后再回测；
5. 策略结果走`invest_agent.attribution`，不得把本金或资产池变化冒充alpha；
6. 明确报告数据截止日、证据等级、尚未接通的能力和是否产生外部副作用。

如果Agent声称“系统没有这个能力”，应先让它检查本文件和对应`--help`；如果它要改用网页手工计算，也应先解释为什么现有确定性接口不适用。
