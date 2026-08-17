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

已具备：真实只读账户、本地数据、风险指标、研究回测、策略注册、证据报告和费用感知的调整草案。

尚未具备：

- 全基金、全历史的严格公告时点；
- 全渠道历史费率、动态限额和精确到账规则；
- 至少12个月的前瞻样本外证据；
- Phase 6订单摘要、批准状态机和mock执行；
- 真实申购、赎回、撤单与长期归因。

因此当前可以生成手工执行清单，但不能代用户提交真实交易。
