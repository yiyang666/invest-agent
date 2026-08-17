# Personal AI Investment OS

面向个人场外基金的本地投研与辅助决策系统。目标是用可复现的数据、策略和风控减少追涨杀跌，而不是让大模型自由交易。

```text
只读账户 + 本地基金数据
        ↓
指标 / 策略 / 回测
        ↓
独立风控与证据报告
        ↓
人工决策；真实交易逐笔确认
```

## 当前状态

- Phase 0–5 的研究系统已完成：政策、只读账户、数据仓、指标、回测、策略注册表和月度报告流水线可用。
- 爱基金已项目级安装并认证；账户查询只读，真实交易关闭。
- 真实持仓快照和账户报告只保存到 Git 忽略的 `data/private/`。
- 当前唯一配置锚点是 631：60%长期资产、30%防御、10%科技卫星。
- 四个策略已登记，但只有 `dca_baseline@1.4.0` 是目标配置参考；其他策略仍是研究对照或候选。
- Phase 6–8 尚未完成：人工审批模拟、受控实盘和长期归因仍待建设。

## 已有能力

- 读取并脱敏保存爱基金持仓、钱包和交易事实；
- 通过 AKShare 东财/同花顺及基金公司公开源采集数据，原始批次不可变留痕；
- 在本地 SQLite 数据仓完成质量门禁、指标、相关性、回撤和市场状态计算；
- 模拟场外基金申购确认、在途现金、单位净值估值、分红和限购队列；
- 对策略做费用、窗口、压力和反事实比较；
- 生成证据约束、可回放、零订单的月度研究报告。

## 安全边界

系统默认 `advisory_only`。申购、赎回和撤单不会因“按策略执行”之类的泛化意图自动发生。未来即使启用实盘，每笔订单仍必须展示基金、动作、金额或份额、账户、费用及预期结果，并取得当次明确确认；状态未知时先对账，禁止自动重试。

## 常用入口

```bash
# 全量测试
.conda-env/bin/python -m unittest discover -s tests -v

# 查看本地数据批次
.conda-env/bin/python -m invest_agent.data.cli inspect

# 运行月度研究流水线
.conda-env/bin/python -m invest_agent.decision.pipeline_cli \
  --config config/monthly_research_pipeline_v1.json \
  --workspace-root .
```

真实数据采集、持仓刷新和指标命令见[运行手册](docs/operations.md)。

## 文档

- [系统架构](docs/architecture.md)
- [当前路线图](docs/roadmap.md)
- [投资策略与631基准](docs/strategy.md)
- [运行手册与能力边界](docs/operations.md)
- [数据层方案](docs/data/phase-2-data-plan.md)
- [场外基金回测契约](docs/backtest/off-exchange-fund-backtest-contract.md)
- [架构决策摘要](docs/decisions/README.md)
- [爱基金安全审查](docs/integrations/thsfund-review.md)

策略的机器可读规格位于 `strategies/specs/`，生命周期和证据入口以 `strategies/registry.json` 为准；聊天记录和旧阶段文档不再作为系统真相。
