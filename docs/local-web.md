# 本机投资工作台

macOS 可双击仓库根目录的 `启动投资看板.command`，它会启动服务并打开浏览器。保持弹出的终端窗口运行即可。

也可在仓库根目录运行：

```sh
scripts/run_local_web.sh
```

打开 http://127.0.0.1:8765 。终端保持运行，Ctrl+C 停止；端口冲突可加 `--port 8766`。使用项目现有 `.conda-env`，无新增依赖。Web 只监听本机，不能从其他设备直接访问。

页面包括投资总览（最新基金收益以及基金、基金类型和仓位归属饼图）、监控基金（搜索、双分类、净值与采集质量、可切换区间的完整净值历史、每日净值涨跌幅及区间位置）、每日持仓（日期选择、金额隐藏）、策略库（登记与历史版本、规则详情、明确绑定的本地回测摘要）、数据更新（维护作业状态与运行记录）。每 60 秒读取一次本地视图，后端缓存 15 秒；“刷新”仅刷新页面数据，不触发账户查询。

## 数据来源

- 净值和持仓：`data/private/invest_agent.sqlite3`，Web 只读。
- 分类与监控集合：`config/fund_watch_catalog_v1.json` 提供稳定的监控分类，研究池、购买路由池和已登记月度持仓分类可提供更具体的既有分类；未登记的基金显示“待分类”。
- 策略：`strategies/registry.json` 与 `strategies/specs/`，检验登记证据，不把研究状态当成实盘执行。
- 更新记录：统一维护任务私有状态与报告。

持仓展示采集时刻的渠道快照。渠道给出净值日期、收益日期时分别展示，否则显示“未提供”，不会自动标成上一交易日。钱包或基金分类采集不完整时提示部分数据。浏览页面不调用外部数据源或模型，不生成订单。

## 日常维护

晚间总控在同一固定会话分两次触发：22:35 运行 22:30 到期的 `portfolio_daily` 只读持仓快照，23:35 再运行 23:30 到期的基金净值和市场数据。两次仍共用统一CLI、状态、锁和运行记录。手工运行也应使用统一入口：

```sh
scripts/run_data_maintenance_cli.sh plan
scripts/run_data_maintenance_cli.sh run-job --job-id portfolio_daily
```

旧的脱敏持仓 JSON 可一次性导入（失败门禁记录自动拒绝）：

```sh
.conda-env/bin/python -c 'from pathlib import Path; from invest_agent.data.portfolio_store import import_snapshots; print(import_snapshots(Path("data/private/invest_agent.sqlite3"),Path("data/private/portfolio_snapshots")))'
```

Web 不负责调度唤醒；晚间任务仍依赖现有 Codex 自动化运行环境和 CLI 登录状态。登录失效或采集失败会保留历史快照并显示更新作业失败。测试用例只使用临时合成数据，不把个人持仓提交到 Git。
