# 归因与策略生命周期契约

## 目的

归因回答“结果从哪里来”，不负责预测市场或生成订单。所有金额和效应由确定性代码计算，LLM只解释已锁定结果。

Phase 8工程状态为`closed`；真实前瞻证据状态为持续积累。通用Python入口是`invest_agent.attribution`，命令行入口是`python -m invest_agent.attribution.cli`。

## 四层归因

1. **现金流层**：期末价值拆为期初价值、外部净投入和投资损益；申购/赎回费单列。外部投入不是收益。
2. **组合层**：每期使用Modified Dietz处理期内现金流，再几何链接为TWR；候选策略只有与631逐期现金流、估值日期一致时才能声称策略相对结果。
3. **袖套层**：使用Brinson-Fachler式配置、选择和交互效应。现金必须作为显式袖套，不能重新归一化后消失。
4. **策略层**：按运行前冻结的消融阶梯逐级相减。改变资产池或长期权重的阶段是总政策结果；只有同资产、同路线、同现金流控制下的增量才可解释为规则或信号贡献。

## 现金流时点

每笔外部现金流必须提供`remaining_weight`：现金进入期初为1，期末为0，期中按剩余期间比例取0到1。单期收益为：

`(期末价值 - 期初价值 - 外部净现金流) / (期初价值 + Σ现金流×remaining_weight)`

分母非正、日期重复、期间倒序或跨路径现金流不一致均失败关闭。

## 生命周期

- 少于12个完整前瞻月：只能继续观察；历史回填不能补足。
- 风险否决、数据不新鲜、现金流不匹配或确定性复跑失败：阻断评价。
- 权重漂移超过政策阈值或规格/实现哈希漂移：要求新版本复核。
- 满24个月且持续显著落后或回撤恶化：进入退役评审，不自动退役。
- 系统永不自动晋级策略；晋级、升级和退役都需要版本化证据与人工裁决。

机器可读阈值见`config/phase8_attribution_policy_v1.json`。

## 可复用命令契约

所有命令接受一个JSON对象；`--output`省略时写标准输出。

| 命令 | 主要输入 | 输出 |
|---|---|---|
| `cashflow` | `periods[]`，每期含期初/期末价值和外部现金流 | 本金、损益、费用、现金拖累、逐期Modified Dietz与链接TWR |
| `matched` | `candidate_periods[]`、`benchmark_periods[]` | 只有期间和外部现金流完全一致才产生相对结果 |
| `sleeves` | `portfolio`、`benchmark`的同名袖套权重与收益 | 单期配置、选择、交互和残差 |
| `waterfall` | 至少两个预冻结`stages[]` | 相邻实验阶段的增量贡献及总差异 |
| `lifecycle` | `policy_path`与策略`evidence` | 继续观察、证据阻断、修订、风险否决或人工评审状态 |

`waterfall`每个阶段必须提供唯一`stage_id`、`metric_value`、64位小写SHA-256证据哈希及以下`claim_scope`之一：

- `baseline`：首个基准阶段；
- `total_policy_outcome`：资产池、长期权重或政策骨架改变；
- `execution_effect`：相同策略下费用、限额、成交或现金队列影响；
- `signal_effect`：同资产、同路线、同现金流控制下的冻结信号增量；
- `risk_overlay_effect`：独立风控覆盖的增量；
- `combined_strategy_effect`：多个已说明模块的合并结果。

袖套Brinson当前按单个评价期计算。跨月时逐月保存结果，禁止直接相加冒充多期贡献；需要多期链接时应先新增并验证明确的链接算法。

## 最小调用示例

```json
{
  "stages": [
    {
      "stage_id": "631",
      "claim_scope": "baseline",
      "metric_value": "100000",
      "evidence_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    {
      "stage_id": "candidate_policy",
      "claim_scope": "total_policy_outcome",
      "metric_value": "103500",
      "evidence_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
  ],
  "metric_name": "final_value_cny",
  "unit": "CNY"
}
```

```bash
.conda-env/bin/python -m invest_agent.attribution.cli \
  waterfall --input data/private/attribution-input.json \
  --output data/private/attribution-result.json
```
