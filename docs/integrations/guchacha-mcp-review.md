# 股叉叉 MCP 集成审查

- 首次审查：2026-08-26
- 最近更新：2026-08-30
- 官方入口与服务地址：https://guchacha.com/mcp
- 传输：远程 Streamable HTTP，MCP协议`2025-06-18`
- 认证：Bearer token，仅从`GUCHACHA_MCP_TOKEN`读取
- 用户确认的当前费用：免费，官方页面标注每小时600次
- 当前状态：七个只读工具已进入项目允许列表；raw-first采集适配器、质量门禁、隔离SQLite表、重放和统一多频率调度已实现。数据权限仍为市场研究背景，未接入交易，也未晋级为策略或风险硬门禁。

## 1. 工具评审与允许列表

官网和认证后的`tools/list`均返回11个工具：

1. `list_datasets`
2. `search_stocks`
3. `get_stock`
4. `get_index_valuation`
5. `get_index_weight`
6. `get_index_forward_pe`
7. `get_dcf_report`
8. `get_macro`
9. `get_market_series`
10. `get_industry_crowding`
11. `get_watchlist`

项目只允许：

- `list_datasets`：数据目录和漂移发现；
- `get_index_valuation`：18个中外指数的当前估值及特定指数周频历史；
- `get_index_weight`：四个A股指数的成分权重，保留`is_estimated`；
- `get_index_forward_pe`：预测年度PE、覆盖股票数、覆盖率和预期增长；
- `get_industry_crowding`：31个行业的当前拥挤度；特定行业历史当前仅返回成交占比；
- `get_market_series`：只允许`bond_yield/forex/margin`及本地精确key白名单；
- `get_macro`：只允许`cpi/ppi/pmi/gdp/buffett/nonfarm`。

`search_stocks`、`get_stock`、`get_dcf_report`和`get_watchlist`继续关闭。个股筛选、模型DCF和账号自选均不属于当前场外基金系统的必要数据边界。

2026-08-29围绕全球市场状态再次审计全部11个工具：额外四个工具仍不能提供全球指数价格历史、市场宽度、VIX/信用压力或跨市场资金流。实测`get_stock`还会把不同时点的行情价与DCF报告“现价”混在一个响应中，进一步证明它不适合作为状态事实源。完整来源矩阵见`docs/integrations/market-regime-source-review.md`。

精确允许列表以`.codex/config.toml`和`config/market_data_sync_v1.json`为准。不得用未审查网页或逐股抓取绕过它；唯一例外是下述已固定字段、低频采集的公开聚合总览。

补充边界：股叉叉公开的`/dashboard`服务端渲染页直接展示全A股总数、上涨、下跌、平盘和平均涨跌幅。该页面不是MCP工具，但属于同一数据提供方；项目仅以一次低频请求采集这些聚合数，不抓逐股明细，也不把网页与MCP虚增为两个独立来源。该链路已经替代“分别采集沪深北全部股票”的高成本方案。

## 2. 数据分层

使用边界由结果用途决定，而不是由人还是Agent发起决定：

1. `interactive_ephemeral`：一次性即时问答可直接调用允许的MCP工具，不保存，也不能复用为正式证据；
2. `evidence_snapshot`：任何可能进入组合、策略、风控、归因、报告或调仓建议的值，必须先原始归档、标准化、质量门禁并写入本地市场表；
3. `historical_series`：趋势、分位、修订、回测或时点分析只读取本地表，缺历史时通过确定性回填补齐。

允许的数据流：

```text
股叉叉MCP → 不可变去敏原始响应 → schema/日期/单位/完整性门禁
           → 隔离市场数据表 → 确定性计算/风险 → Agent解释
```

MCP不能在指标、策略、回测、归因或交易运行时绕过本地仓实时供数，也不得写入基金NAV表。

## 3. 实测数据口径与限制

- 无指数代码的估值查询返回18个指数当前面板；特定指数历史为周频。沪深300样本历史从2005-04-07延续到2026-08-26，共约1091个观察；不能据此推定每个指数都有相同起点。
- 指数权重只覆盖上证50、沪深300、科创50、创业板指。最新数据可能为估算，必须保留`weight_date/is_estimated`，不能冒充官方调仓。
- 前瞻PE包含预测年份、覆盖股票数、覆盖率和预期增长；响应未提供可靠快照日时只使用采集日代理并警告。预测存在修订和乐观偏差，证据权重较低。
- 行业拥挤当前面板含多个维度，但特定行业历史实测只返回周频成交占比。其他维度只能从今后的持续快照中积累，不能虚构历史。
- 市场序列返回`dataset/key/unit/latest/change/series`。项目只采集配置中的国债、汇率和两融key。
- 宏观序列带观察日期、显示期和发布日期。非农等响应可能含尚未发布的未来null占位；标准化器跳过并保留警告，不能当作0。
- 海外指数与A股PE/PB口径不同，部分字段可能为空；跨市场直接比较必须解释口径差异。
- 官方未提供完整修订政策、数据持久化/派生许可、稳定性承诺和全部历史边界。

因此，历史回填固定标记`historical_visibility_assumed`；持续采集的快照通过`first_seen_at`逐步积累严格时点证据。缺少独立第二来源的数据保持`research_only`。

## 4. 本地实现和安全边界

- 客户端：`invest_agent.market_data.mcp_client`，固定HTTPS源，不安装或执行第三方SDK；
- 策略白名单：`invest_agent.market_data.policy`；
- 标准化与质量门禁：`normalize.py`、`quality.py`；
- 本地发布：`store.py`，使用独立的`market_*`表；
- CLI：`python -m invest_agent.market_data.cli`；
- A股宽度：`collect-guchacha-breadth`一次采集股叉叉公开总览的全A股聚合值；
- 多频率维护：`python -m invest_agent.automation.maintenance_cli`；
- 项目Skill：`market-context-research`与`market-data-collect`。

token不得进入配置、plist、原始归档、数据库、日志、测试快照、报告或Git。客户端不保存Authorization头；失败不回落到模拟数据，不对含糊网络失败自动重试。远端instructions、描述和正文均是不可信输入，不能覆盖`AGENTS.md`、投资政策、风险门禁或逐笔交易确认。

## 5. 调度决策

自动运行只配置一个可观察的Codex heartbeat，由本地代码评估：

- 工作日：基金净值、国债、汇率和两融；
- 每周：数据目录、指数估值、前瞻PE和行业拥挤；
- 每月：指数权重及CPI/PPI/PMI/非农/巴菲特指标；
- 每季：GDP。

共享锁防止并发写入，状态文件防止同周期重复成功，独立作业失败不阻止其他作业，最终只生成一份汇总报告。Codex heartbeat只依次运行`plan/run-due`并在固定会话解释结果；它不能自行构造采集调用、质量结论或数据库写入。不为每个频率建立会话。

launchd与Keychain方案保留为零LLM备用，当前保持未加载；若未来切换，必须先暂停Codex数据总控。

## 6. 验收记录

### 2026-08-26：首次认证

- 握手成功，服务端报告`guchacha 1.0.0`；
- `tools/list`返回11个具备`inputSchema`的工具，与官网一致；
- 工具未声明MCP只读annotations，因此项目继续用本地允许列表和调用审批保护；
- 去敏`initialize/tools/list/list_datasets`响应归档到Git忽略的`data/raw/guchacha_mcp/`；
- 对应SHA-256为`5c6b9b…e115`、`193a51…27d9`、`483e5a…61f5f`；完整值保留在原始归档元数据，不复制凭证。

### 2026-08-28：扩展工具与适配器

- 七个工具的真实只读响应均完成schema探测和不可变归档；
- `tools/list`规范化schema哈希复核仍为`a0a5d1…06b0`；首次回填和每周作业先审计schema，不一致时停止该组发布；
- 13类真实响应在临时SQLite中完成重放，13个批次、385个序列组通过或以显式partial警告发布；
- 估算权重、前瞻PE日期代理、历史可见性假设和宏观null占位均被保留；
- 允许列表、raw-first CLI、隔离表、统一调度器和项目Skill已有自动化测试；
- 初始生产回填完成：33个数据命令零失败；连同周/月当前快照共发布46个批次、19,499条数值观察、499条权重、13条目录记录和449个序列组。42个`partial`均保留历史可见性、估算或日期代理警告，4个批次无警告通过；
- 单个Codex数据总控用于可观察触发；launchd未启用，避免与Codex任务重复采集。

## 7. 仍未晋级的能力

- 股叉叉不是基金净值、基金交易规则或账户事实源；
- 当前市场数据不直接生成择时信号、买卖动作或订单；
- 未完成第二来源复核的数据不能成为风险硬否决或策略晋级证据；
- 尚无所有拥挤维度的历史序列；
- 尚未把Guchacha接入现有`metrics/backtest/attribution`的正式计算接口。

如要把某一数据族晋级，必须分别补齐独立来源、单位/时点/修订测试、正式指标契约和策略前置规格，不能一次性把整个MCP升权。

## 8. 回滚

1. 将`.codex/config.toml`中的服务设为禁用；
2. 卸载或停止`com.ethan.invest-agent.data-maintenance` launchd任务；
3. 清除当前进程环境变量并在服务端重置token；
4. 保留已归档批次和研究表作为审计记录，不覆盖或删除；
5. 无第三方SDK或全局包需要卸载。
