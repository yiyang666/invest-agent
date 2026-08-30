# Invest Agent 数据更新总控提示词

这是 Invest Agent 的固定可观察数据更新会话。仅在 `/Users/ethan/Documents/workspace/Invest Agent` 主目录和当前会话中工作，使用项目级 `$market-data-collect`；不要创建或切换 worktree。

本任务只负责触发和观察确定性数据维护。日、周、月、季频率、到期周期、防重复状态、进程锁、采集命令、质量门禁和数据库写入全部由 `invest_agent.automation.maintenance_cli` 决定；Agent不得自行判断后绕过调度器逐项调用网页、MCP、AKShare或底层采集器。

严格依次执行：

1. `.conda-env/bin/python -m invest_agent.automation.maintenance_cli plan --workspace-root .`
2. 读取并简短说明本次 `due_jobs`；这一步只读，不得据此修改配置。
3. `.conda-env/bin/python -m invest_agent.automation.maintenance_cli run-due --workspace-root .`
4. 读取命令输出的汇总报告和 `data/private/automation/data-maintenance-state.json`，完成结果对账。

不得绕过统一维护CLI直接运行基金单域同步，不得调用或加载launchd，不得在任务级自动重试。某项失败时保留其他独立作业的结果，原样报告失败命令、错误和是否需要人工处理。

只允许更新公开基金数据和股叉叉允许列表中的市场背景数据，并写入项目既有原始归档及 `data/private/invest_agent.sqlite3`。禁止刷新爱基金账户、读取或展示凭证、运行指标/策略/回测/风控/月报/归因、生成订单或执行交易；禁止修改基金池、购买路由、投资政策、策略配置和调度配置；禁止用模拟、旧数据或网页即时结果冒充成功。

每次在本固定会话简短汇报：

- 运行时间与总体状态；
- 本次到期及未到期的日/周/月/季作业；
- 各作业命令数、成功数、失败数与质量警告；
- 基金数、发布/拒绝/错误/陈旧数（如本次运行基金同步）；
- 市场数据新增批次和主要数据族（如本次运行市场采集）；
- 汇总报告路径、状态文件路径及是否需要人工处理；
- 明确说明未运行策略、未生成订单、未发生交易。
