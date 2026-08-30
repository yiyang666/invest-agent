# 运行手册与能力边界

## 运行环境

- 项目环境：`.conda-env`，Python 3.12；
- 爱基金 SDK：`aijijin-sdk==0.2.0`；
- AKShare：固定为1.18.91；
- 本地数据库：`data/private/invest_agent.sqlite3`；
- 私有快照、报告和原始批次均被 Git 忽略。

## 账户刷新

```bash
.conda-env/bin/python scripts/capture_portfolio_snapshot.py
```

命令通过爱基金只读接口实时取数，原始响应只在内存处理，仅持久化脱敏 `PortfolioSnapshot`。交易记录和赎回预览也只能通过受审查的 `aijijin` CLI 查询。

## 基金数据

```bash
# 预览当次基金全集、区间与来源（不联网）
.conda-env/bin/python -m invest_agent.data.sync_cli \
  --config config/fund_data_sync_v1.json --workspace-root . --dry-run

# 一键增量同步
.conda-env/bin/python -m invest_agent.data.sync_cli \
  --config config/fund_data_sync_v1.json --workspace-root .

# 检查已有批次
.conda-env/bin/python -m invest_agent.data.cli inspect

# 东财历史净值
.conda-env/bin/python -m invest_agent.data.cli collect-akshare-eastmoney \
  --fund-code 110020 --start-date 2009-08-26 --end-date 2026-08-14 \
  --as-of 2026-08-16T23:30:00+08:00

# 同花顺主数据与指定日抽检
.conda-env/bin/python -m invest_agent.data.cli collect-akshare-ths-metadata \
  --fund-code 110020 --as-of 2026-08-16T23:30:00+08:00
.conda-env/bin/python -m invest_agent.data.cli collect-akshare-ths-daily \
  --fund-code 110020 --start-date 2026-08-14 --end-date 2026-08-14 \
  --as-of 2026-08-16T23:30:00+08:00
```

远端采集只负责生成批次；指标、回测和报告不得在运行时绕过本地仓实时取网页数据。

增量同步的基金全集由四部分组成：政策中的非QDII基金、当前购买路由池、当前渠道可交易的研究候选、最新真实持仓。购买路由池和研究候选只接受 `open/limited`；暂停、不支持、关闭或未知路由不会进入候选。若这些基金仍在真实持仓中，系统仍会更新其净值用于风险监控。研究候选只获得数据，不会自动晋级为购买路线。每只基金先归档原始响应，再标准化并通过质量门禁；单只失败会使运行降级或失败，不会用模拟数据补洞。

基金单域命令可继续手工执行；自动维护统一由下文的多频率入口负责。进程锁会阻止重叠运行；任务只更新数据，不自动运行策略、报告或交易。

QDII当前路由事实位于 `config/qdii_purchase_route_pool_v1.json`。同一袖套可以登记多个已核验路由；各路由每日限额分别消耗，未完成预算跨日、跨月继续排队。系统不会因为某条路线不可买而自行换基金。

## 市场背景数据

股叉叉只补充指数估值/权重、利率、汇率、杠杆、宏观与行业拥挤度。它不替代基金净值，也不是天气预言机。

```bash
# 初始化隔离表、查看已发布批次和序列
.conda-env/bin/python -m invest_agent.market_data.cli init-store
.conda-env/bin/python -m invest_agent.market_data.cli inspect

# 单次证据采集：先归档，再标准化和发布
.conda-env/bin/python -m invest_agent.market_data.cli collect \
  --tool get_index_valuation \
  --arguments '{"index_code":"000300","metric":"pe_ttm","window":"since","history":true}'

.conda-env/bin/python -m invest_agent.market_data.cli publish-fund-proxies --history

.conda-env/bin/python -m invest_agent.market_data.cli collect-fred \
  --series NFCI --lookback-days 120

.conda-env/bin/python -m invest_agent.market_data.cli collect-guchacha-breadth

# 一次性历史回填（包含schema审计、基金代理、NFCI、全A聚合宽度及已审查市场命令）
.conda-env/bin/python -m invest_agent.automation.maintenance_cli run-bootstrap
```

`probe`只用于schema排查，归档但不发布；进入策略、风险、报告、归因或调仓建议的值必须由`collect`、`publish-fund-proxies`、`collect-fred`、`collect-guchacha-breadth`或定时维护写入本地市场数据表。基金代理不是官方指数；FRED与股叉叉聚合宽度仅限个人本地研究并要求引用。`collect-sse-breadth`保留为手工校验，不在默认计划中。精确允许列表见`config/market_data_sync_v1.json`。

## 可观察的日/周/月/季统一数据维护

系统不为四种频率创建四个会话。一个名为`Invest Agent 数据更新总控`的Codex heartbeat在工作日23:55返回固定维护会话，只依次运行`plan`和`run-due`；确定性代码根据本地状态判断应运行哪些作业：

- 工作日：基金净值、股叉叉全A股聚合宽度、国债收益率、外汇和两融；
- 每周：数据目录、指数估值、前瞻PE和行业拥挤度；
- 每月：指数权重及CPI/PPI/PMI/非农/巴菲特指标；
- 每季：GDP。

```bash
# 只读预览当前到期作业
.conda-env/bin/python -m invest_agent.automation.maintenance_cli plan

# 手工运行到期作业
.conda-env/bin/python -m invest_agent.automation.maintenance_cli run-due

# 诊断单个作业
.conda-env/bin/python -m invest_agent.automation.maintenance_cli \
  run-job --job-id market_weekly_context
```

Codex任务的版本化提示词位于`config/codex_data_update_automation_v1.md`。Agent只展示计划和解释汇总报告；共享锁、频率状态、采集、门禁与写库均由`maintenance_cli`完成。任务状态和历史在Codex的Scheduled页面及固定会话查看，本地状态位于`data/private/automation/data-maintenance-state.json`。

“今天该跑什么”不是Agent判断：CLI以`Asia/Shanghai`当前时间（或显式`--as-of`）检查每个作业的频率、目标星期/日期/时间，生成日、ISO周、月或季度周期键，再与状态文件中的`last_success_period`比较。只有已到时且本周期尚未成功的作业进入`due_jobs`。当前heartbeat是周一至周五；基金净值和高频市场序列均按工作日运行，月/季任务在标称日期落到周末时于下个工作日补跑。市场序列每次读取最近窗口，能补入期间遗漏的观察，但补采不能伪装成当日首次观测；完全错过的周频快照也不能在下周一还原。

`scripts/run_scheduled_data_maintenance.sh`及`config/launchd/com.example.invest-agent.data-maintenance.plist.example`保留为未来零LLM备用，当前不得加载。若未来切换到launchd，先暂停Codex数据总控，再完成Keychain凭证与本地任务验收；两个触发器不能并行。

检查本地备用任务是否误加载：

```bash
launchctl print gui/$(id -u)/com.ethan.invest-agent.data-maintenance
tail -n 50 data/private/automation/data-maintenance.stderr.log
```

本地项目的Codex定时任务要求电脑开机且应用运行。远端失败会记录并失败关闭，不发布模拟数据、不触发策略或交易，也不自动重试含糊失败。任务停用不会影响CLI手工运行。

## 指标与体检

```bash
# 单基金指标
.conda-env/bin/python -m invest_agent.metrics.cli fund \
  --fund-code 110020 --as-of 2026-08-16

# 当前组合风险
.conda-env/bin/python -m invest_agent.metrics.cli portfolio \
  --snapshot data/private/portfolio_snapshots/<snapshot>.json \
  --as-of 2026-08-16

# 持仓基金状态
.conda-env/bin/python -m invest_agent.metrics.cli market-state \
  --snapshot data/private/portfolio_snapshots/<snapshot>.json \
  --as-of 2026-08-16
```

历史净值缺少可信公告时点时，结果固定标记 `historical_visibility_assumed / research_only`。组合风险是“当前权重倒放”的代理，不是账户真实历史收益。

## 月度研究流水线

```bash
.conda-env/bin/python -m invest_agent.decision.pipeline_cli \
  --config config/monthly_research_pipeline_v1.json \
  --workspace-root .
```

流水线依次生成决策包、证据报告、自然语言解释和一致性验收，最后才发布Manifest。任一阶段失败则不发布；它不联网、不自动定时、不生成订单。

每次新运行必须使用新鲜快照、最新本地数据、新月份运行ID和新的不可变输出路径。快照或市场数据超过3个日历日、策略哈希漂移或唯一631权威失效时，流水线失败关闭。

## 当前能力边界

已具备：真实只读账户、本地基金与市场数据、统一多频率维护、风险指标、研究回测、策略注册、证据报告、QDII可购路由容量、费用感知的调整草案、持久化逐笔批准与受控申购，以及确定性归因和策略生命周期评价。

尚未具备：

- 全基金、全历史的严格公告时点；
- 全渠道历史费率、动态限额和精确到账规则；
- 至少12个月的前瞻样本外证据；
- 可用于策略晋级的至少12个月真实前瞻证据；
- 自动化真实交易、赎回和撤单；
- 足够长跨度的真实账户归因观察。

因此当前可以生成建议，并在逐笔精确确认后受控申购；不能自动提交，也不能把尚未积累的真实观察期当作现成证据。

## Phase 6 mock审批门

`config/phase6_mock_approval_policy_v1.json` 固定15分钟一次性批准、关键字段变化失效和未知状态先对账。只有 `mock_schedule_frozen`、风险通过且具有明确日期的计划才能转换；月中诊断预览不能生成草案。批准记录写入 `data/private/` 下的SQLite库时只保存摘要与时间戳，并通过事务保证并发一次性消费。实现位于 `invest_agent/approval/` 与 `invest_agent/execution/`，mock命令不调用网络或爱基金交易命令。当前没有真实提交入口；任何mock结果都不能当作真实订单。

持久化交互演练：

```bash
.conda-env/bin/python -m invest_agent.execution.mock_cli \
  --db data/private/mock-execution.sqlite3 \
  rehearse --intent data/private/mock-intent.json --outcome submitted
```

若进程在 `submitting` 后中断，先执行 `recover`，再通过 `status` 检查；状态会变成 `unknown`，只能使用 `reconcile --status submitted|failed` 明确收口。上述命令均为mock且不联网。

## Phase 7逐笔受控申购

`config/phase7_controlled_live_policy_draft_v1.json` 当前为 `active_controlled_live`，仅允许逐笔申购。长期单笔、单日和单月绝对上限均不超过5000元及当月剩余额度；真实约束以已批准调度计划为准。首笔500元试运行已经完成。`PersistentControlledLiveGateway`负责15分钟一次性批准、原子额度门禁、提交中状态和未知对账，但不自动调用交易CLI。

实盘SQLite库必须位于`data/private/`。数据库保存摘要、必要的额度累计值、时间和外部订单号哈希，不保存基金、账户、协议编号、外部订单号或凭证明文。进程启动后先运行不完整状态恢复；任何`unknown`必须用精确订单号完成只读查询和对账，禁止再次提交。

首笔006932已完成基金公司确认和持仓核验。动态交易规则查询优先使用爱基金CLI当轮只读预检：申购使用`subscribe-init`/`fee-rule`，赎回使用`redeem-render`。原始响应可能包含完整交易账户，禁止保存；仅可通过`normalize_redeem_preview`保留基金、净值、持有批次、阶梯费率、份额限制和到账时间等脱敏事实。运行赎回预览不等于开放赎回，`fund redeem`仍被政策禁止。

## Phase 8归因与策略生命周期

通用入口接受JSON输入并输出带确定性哈希的JSON结果：

```bash
# 本金/损益/费用/现金拖累与Modified Dietz/TWR
.conda-env/bin/python -m invest_agent.attribution.cli \
  cashflow --input data/private/cashflow-attribution-input.json \
  --output data/private/cashflow-attribution-result.json

# 候选与631同现金流比较
.conda-env/bin/python -m invest_agent.attribution.cli \
  matched --input data/private/matched-attribution-input.json \
  --output data/private/matched-attribution-result.json

# sleeves / waterfall / lifecycle 使用相同的 --input / --output 形式
```

输入字段和解释边界见`docs/attribution.md`。`waterfall`的阶段顺序必须在运行前冻结并绑定证据SHA-256；资产池或长期权重变化使用`total_policy_outcome`，不能标成`signal_effect`。`lifecycle`只返回继续观察、阻断、修订或人工评审状态，不自动晋级、退役或生成订单。
