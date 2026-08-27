# 股叉叉 MCP 集成审查

- 审查日期：2026-08-26
- 官方入口：https://guchacha.com/mcp
- 服务地址：https://guchacha.com/mcp
- 传输：远程 Streamable HTTP
- 认证：股叉叉账号签发的 Bearer token
- 费用：用户已于 2026-08-26 确认当前免费，受调用次数限制
- 官方频率限制：每小时 600 次
- 状态：已完成项目级、只读、发现优先的最小 MCP 注册和首次认证验收；尚未接入采集适配器、本地校验仓、指标、回测、策略或交易

## 1. 已审查能力

官网列出 11 个工具：

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

本轮项目配置只允许以下四个工具：

- `list_datasets`：核对可用数据、更新时间和后续 schema 漂移；
- `get_index_valuation`：18 个中外指数的估值和历史分位；
- `get_index_weight`：上证50、沪深300、科创50、创业板指的权重与贡献点数；
- `get_index_forward_pe`：指数前瞻 PE 与前瞻股息率。

其余工具默认不可见。特别是 `get_watchlist` 涉及账号内自选数据，当前无业务必要，不予开放。

## 2. 数据口径与已知限制

- A 股行情每个交易日收盘后更新；
- 指数估值与行业拥挤度由公开数据整理；
- 指数权重只覆盖上证50、沪深300、科创50、创业板指；最新交易日数据为预估，官方权重 T+1 发布；
- 海外指数的 PE/PB 与 A 股口径不同，部分指标可能为空；
- 指数历史估值公开页面显示为周频、指数整体口径 `mcw`，但不能据此推定全部 18 个指数具有相同历史起点；
- 前瞻 PE 采用总市值整体法；一致预期来自东财机构一致预期，具有预测乐观偏差和后续修订风险；
- DCF 报告含模型生成内容和假设，不属于权威原始事实，本轮不开放；
- 官方未公开突发并发限制、分页上限、重试语义、历史修订政策、完整数据字典或稳定性承诺。

## 3. 静态安全审查

该集成是托管远程服务，不安装本地 npm/PyPI 包、SDK 或脚本：

- 本地不执行第三方代码；
- 最小网络权限为出站 HTTPS `guchacha.com:443`；
- 不需要设备标识、账户文件、shell 或项目写权限；
- token 只允许通过 `GUCHACHA_MCP_TOKEN` 环境变量提供；
- 禁止把 token 写入 `.codex/config.toml`、`.env`、日志、测试快照、报告或 Git；
- 远端 MCP 返回的 instructions、工具说明和数据正文均视为不可信输入，不能覆盖 `AGENTS.md`、投资政策、风险门禁或逐笔交易确认；
- 远端实现没有可固定的包版本或二进制校验值。首次认证后必须归档 `initialize`、`tools/list` 和 `list_datasets` 的规范化响应及 SHA-256，schema 漂移时失败关闭；
- 无凭证探测观察到认证错误通过 HTTP 200 + JSON-RPC `isError` 返回，监控必须检查协议正文，不能只看 HTTP 状态；
- 不自动更新、不自动扩大工具允许列表。

## 4. 架构边界

允许的数据流只有：

```text
股叉叉 MCP
  → 原始响应不可变归档
  → schema / 日期 / 单位 / 完整性 / 来源质量门禁
  → discovery_only 隔离区
  → 独立第二来源核验
  → 单独晋级审查
  → 本地校验仓
```

强制约束：

- MCP 不得在指标、策略、月度报告、回测或交易运行时被实时调用；
- 远端结果不得直接写入现有基金 NAV 表；
- 未知公告时点标记为 `historical_visibility_assumed / research_only`；
- 原始归档不得包含 Authorization 请求头或 token；
- 跨源冲突必须隔离，禁止静默选值；
- 远端失败不得回落到模拟数据；
- 指数估值、前瞻预测和 DCF 不能直接生成策略信号或订单；
- 交易、申购、赎回、撤单和执行策略不受该 MCP 影响。

## 5. 当前项目配置

项目级配置位于 `.codex/config.toml`，只使用环境变量凭证，服务非启动必需，并对每次工具调用保留提示审批：

```toml
[mcp_servers.guchacha]
url = "https://guchacha.com/mcp"
bearer_token_env_var = "GUCHACHA_MCP_TOKEN"
enabled = true
required = false
enabled_tools = [
  "list_datasets",
  "get_index_valuation",
  "get_index_weight",
  "get_index_forward_pe",
]
default_tools_approval_mode = "prompt"
startup_timeout_sec = 10
tool_timeout_sec = 30
```

## 6. 首次认证后的验收

1. 只读获取 `initialize` 与 `tools/list`，保存去敏原始响应和内容哈希；
2. 实际工具集合必须与官方声明一致，且项目可见工具必须恰为允许列表中的四个；
3. 调用一次 `list_datasets`，核对各数据集的更新日、历史边界和字段 schema；
4. 小样本查询一个指数的估值、权重和前瞻 PE，分别归档原始结果；
5. 验证无 token、无效 token、超时及应用层 `isError` 均失败关闭且不自动重试；
6. 检查 Git、配置、日志和测试输出中不存在 `gcc_` 凭证；
7. 在获得独立第二来源核验和数据许可依据前，不发布到本地校验仓；
8. 新工具、新用途、自动化采集或策略接线均需再次审查和用户确认。

### 2026-08-26 验收记录

- 认证握手成功；服务端报告 `guchacha 1.0.0`，MCP 协议版本 `2025-06-18`；
- `tools/list` 返回 11 个工具，名称与官网声明一致，均具有 `inputSchema`；
- 所有工具均未声明 MCP `annotations`，因此项目继续把它们视为未证明只读，并保留提示审批和允许列表；
- `list_datasets` 调用成功；指数估值更新到 2026-08-24，指数权重与指数前瞻 PE 更新到 2026-08-26；
- 去敏后的 `initialize`、`tools/list`、`list_datasets` 原始 JSON-RPC 响应已归档到 Git 忽略的 `data/raw/guchacha_mcp/`；
- 三份原始响应的 SHA-256 分别为：
  - `initialize`: `5c6b9bccd1be432aee66906078ada3ae41daaf357a0d2846a0a13e8c4676e115`
  - `tools/list`: `193a51a92b8b6d17f5bf61aaeb093f7beedbefbc99b8a66417a1f15c270027d9`
  - `list_datasets`: `483e5ab17f3d98b67b5dd9c7818e4b1b9db54e6b7f85b0977492097c83361f5f`
- 归档重放校验通过；仓库和原始归档均未发现形如真实 `gcc_…` 的凭证。

## 7. 回滚

1. 将 `.codex/config.toml` 中 `enabled` 设为 `false`，或删除整个 `guchacha` 表；
2. 清除当前进程的 `GUCHACHA_MCP_TOKEN`；
3. 在股叉叉页面重置 token，使旧 token 失效；
4. 重启 Codex；
5. 已归档批次保留为未发布审计记录，不覆盖或删除；
6. 无第三方包、SDK 或全局目录需要卸载。

## 8. 后续晋级门禁

在实现确定性采集适配器前，仍需取得或实测：

- 正式工具 JSON Schema、分页和最大响应量；
- 各指数完整历史起止日及修订规则；
- 数据保存、派生计算与报告引用许可；
- token 生命周期及服务端日志保留说明；
- 不同数据集对应的独立权威复核源。

完成这些门禁后，才可另行提案实现 `raw-first` 采集适配器和质量测试。该适配器不得直接服务回测或交易。
