---
name: design-fund-strategy
description: Design, discover dependencies for, specify, implement, backtest, and evaluate deterministic user-level off-exchange fund portfolio strategies against the authoritative 631 v1.5 benchmark in this repository. Use when the user mentions 自定义策略、组合策略、稳定加灵活、择机投入、挑战或对比631、策略回测、策略因子、用户级策略、新基金、新袖套、新指数或修改策略规则，也用于继续编辑本 Skill 生成的策略提案或发现报告。不要用于直接买卖基金或把研究结果自动升级为实盘策略。
---

# Design Fund Strategy

把想法持久化为可编辑提案，用项目内确定性代码完成发现、实现、回测和评估。始终保留 `allocation_anchor_631` 与 `dca_baseline@1.5.0` 的锚点/比较权威，除非用户另行明确要求政策级变更。

## 核心原则

- 减少用户决策检查点，不限制内部工具调用、联网研究、补数据、编码或测试次数。
- 保持 `research_only`；不得提交申购、赎回、撤单或调用真实执行模块。经用户授权且项目已静态审查时，可用只读渠道接口核验基金费率、限额、确认日和预计到账日；只保留白名单化后的基金规则，不保存账户、支付方式、客户风险或原始受保护响应。
- 不把历史胜出解释为未来收益预测。
- 不在看到回测结果后静默改参；结果后改参必须产生新版本并重置前瞻观察。
- 只让本地校验数据仓给指标和回测供数。网页与远程接口只用于发现、采集和核验。
- 风险层保留否决权。用户可以接受收益/回撤权衡，但不能绕过杠杆、金额、集中度、数据质量与确认边界。

## 用户决策检查点

默认最多安排三个用户决策检查点；信息完整时合并，安全地可自行发现的事实不要询问。

1. **意图冻结**：集中确认现金流、规则、接受边界，以及新袖套的策略角色。
2. **发现分叉**：仅在代理数据、长期配置变更、付费源、第三方依赖或无法唯一解释的规则出现时集中确认一次。
3. **结果裁决**：让用户选择保留研究候选、创建新版本迭代或放弃。

一个检查点之间可以包含多次本地查询、联网、数据归档、代码修改和测试。不要为了追求固定消息数跳过证据门。

## 1. 创建并校验提案

1. 读取 `AGENTS.md`、`config/investment_policy.yaml`、`docs/strategy.md`、`strategies/registry.json` 和 [capability-map.md](references/capability-map.md)。
2. 把用户已表达的内容直接写入提案，不重复确认。
3. 创建提案：

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/proposal.py new \
  --strategy-id <snake_case_id> --title '<中文标题>' --workspace-root .
```

4. 根据用户想法补全提案。使用新基金时写入 `discovery.new_fund_routes`；使用新袖套时写入 `discovery.new_sleeves`；使用项目未接入的数据写入 `discovery.novel_data_inputs`。
5. 校验：

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/proposal.py validate \
  strategies/proposals/<proposal>.toml --workspace-root .
```

校验报告输出 `workflow_path`：

- `fast_path`：基金、袖套、数据与信号原语都已存在，直接进入实现。
- `discovery_path`：存在新基金、新袖套、新数据或新信号原语，先完成发现报告。

提案必须在性能运行前冻结现金流、信号观察窗、阈值、动作、缺失数据行为、比较尺和接受边界。用 `apply_patch` 把 `status` 改为 `frozen`；不得由脚本替用户选择阈值。

## 2. 探索路径

只在 `workflow_path=discovery_path` 时执行。

### 创建发现报告

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/discovery.py new \
  --proposal strategies/proposals/<proposal>.toml --workspace-root .
```

自主完成能安全发现的事实，持续更新同一份报告。不要把研究过程拆成零碎聊天。

### 新基金数据门

采用两级门禁，避免把“先看策略机制是否值得研究”和“准备进入正式建议”混为一谈。

**探索回测门（`research_ready`）**只要求：

1. 基金代码、名称、份额类别和袖套经济暴露能够唯一对应；
2. 有可用的本基金净值窗口，短历史必须显式标为 `short_fund_history`；
3. 分红、缺值和可见性有确定性处理；
4. 当前开放/限额、首档申购费和确认时序已从管理人资料或已审查的只读渠道快照取得，缺少的执行细节可使用版本化的保守反事实假设；
5. 合同变更、历史限额、精确份额舍入和主动基金风格漂移在探索阶段作为披露项，不因其本身阻断机制回测；基金身份断裂、错误份额类别、不可用净值或未来数据仍然阻断。

**晋级门（`complete`）**才要求补齐有效日期化产品规则、关键合同边界、历史渠道规则敏感性和拟用于实际建议所需的全部执行字段。探索胜出不自动满足晋级门。

发现顺序：

1. 先查本地仓；缺数据时使用 `fund-data-collect` 的受控采集流程。
2. 当前爱基金渠道规则优先使用已审查的 `thsfund` 只读 `fee-rule`；确需核验开放状态、最低金额或限额时使用只读 `subscribe-init` 并在命令内过滤敏感字段。禁止用 `fund buy`、`fund redeem` 或撤单“试单取数”。
3. 产品与指数事实可能变化时联网；探索门至少要求身份可解释，晋级门再要求管理人/指数公司和独立来源的完整交叉验证。
4. 公开远程响应先归档；受保护只读响应不得原样落盘，只能保存无账户字段的规范化快照。回测只读本地快照，不实时访问远端。
5. 不安装新 SDK、第三方 Skill 或数据源依赖；确有必要时先静态审查并请求用户批准。

按证据强度选择且明确标记：

- `fund_history`：使用基金自身已校验净值；
- `short_fund_history`：只做短窗研究并进入前瞻观察；
- `index_proxy`：研究经济暴露，不冒充基金费用、跟踪误差或交易路径；
- `older_fund_proxy`：记录份额、汇率、费率、跟踪误差与渠道规则的基差风险；
- `forward_only`：无可靠代理时不补造历史收益，只做压力和前瞻观察。

### 新袖套定义门

不要把基金代码直接当袖套。为袖套冻结经济暴露契约：资产类别、地区、指数/风格、币种、是否对冲、组合角色、权重上限、与既有袖套的重叠和压力映射。

- `tactical_overlay`：631长期锚点不变，只允许有边界的新增资金偏移。
- `policy_candidate`：拟改变长期地域或资产配置；另行准备政策与 ADR 候选，不能在策略中静默改锚。

### 新规则门

- 已有原语：复用函数并新增薄组合器和测试。
- 新原语：先定义数据契约，再写纯确定性函数、边界测试和防未来数据测试。
- 无法唯一确定化的语言，例如“行情不错时买”，进入意图冻结检查点，不得自行发明含义。

### 完成发现

机制探索先验证 `research_ready`：

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/discovery.py validate \
  strategies/proposals/<discovery>.toml --workspace-root . --require-research-ready
```

只有报告为 `research_ready` 或 `complete` 且验证通过，才能编译 `research_only` 提案并运行带有 `exploratory` 标签的回测。`research_ready` 结果不得注册为实际建议策略，也不得声称产品规则已完整复现。

准备晋级时再运行完整门：

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/discovery.py validate \
  strategies/proposals/<discovery>.toml --workspace-root . --require-complete
```

把提案中的 `discovery.status` 设为报告的同级状态（`research_ready` 或 `complete`），并写入验证输出的 `report_path` 与 `report_sha256`。提案编译器会重新校验报告内容、策略身份和哈希。`counterfactual_execution`、`proxy_research_only`、`short_window_only` 和 `forward_observation_only` 都是合法探索结论，但不能伪装为完整历史验证。

## 3. 确定性实现与回测

1. 编译冻结提案：

```bash
.conda-env/bin/python .agents/skills/design-fund-strategy/scripts/proposal.py compile \
  strategies/proposals/<proposal>.toml --workspace-root .
```

2. 先写或补规格，再写测试和回测实现；不得先跑结果再反推规则。
3. 创建独立候选场景和对比配置，不改写 631 v1.5 规格或既有结果。
4. 按消融阶段逐步运行；`research_ready` 可先运行只依赖现有本地数据的阶段，未接入因子对应的变体必须排除而不是填造。完整候选至少运行：主历史窗口、相同月度现金流631、固定每月3000元631、滚动窗口、参数敏感性、费用/限购、压力测试和确定性复跑。
5. 动态使用机动资金时，逐月现金流必须与 matched-cashflow 631 完全相同；否则只报告资金差异，不声称策略增益。
6. `research_ready` 的产物统一标记 `exploratory / counterfactual_execution`。只有发现达到 `complete`、规格/实现/测试/证据齐全后，才提出注册为 `research_candidate`；不要自动改注册表、目标权威或月度建议入口。

## 4. 固定记分卡

同时展示候选、固定3000元631和 matched-cashflow 631：

- 年化收益、XIRR、期末价值；
- 年化波动、最大回撤、修复日期/时长；
- 申购费、现金拖累、拒绝/延期金额；
- 最差滚动窗口、参数/费用敏感性与压力结果；
- 数据时点、样本长度、代理等级、路由可实施性和已知缺口。

使用客观标签，不自动选赢家：

- `dominates_precommitted_metrics`
- `return_drawdown_tradeoff`
- `underperforms_matched_631`
- `insufficient_evidence`
- `risk_veto`

用户看完结果后提高回撤容忍度时，可记录为个人偏好，但本次不得改判为预先通过；验证新边界必须创建新版本。

## 5. 能力沉淀判断

- 仅服务一个策略的组合逻辑：留在该策略模块。
- 能被两个以上策略复用、数据契约稳定且可独立测试的指标/信号：提升为通用原语。
- 新资产类别、数据源、回测语义或安全边界：进入基础设施并视影响新增 ADR。
- “研究候选入库”与“接管月度建议”是两次不同变更；新策略需单独适配 decision/reporting。
- 当前受控申购草案仍要求 `dca_baseline` 目标权威，本 Skill 不改变该条件。

## 资源

- [strategy-proposal.template.toml](assets/strategy-proposal.template.toml)：主提案模板。
- [strategy-discovery.template.toml](assets/strategy-discovery.template.toml)：探索路径发现报告模板。
- [proposal.py](scripts/proposal.py)：创建、路径分流、校验与规格编译。
- [discovery.py](scripts/discovery.py)：生成和验证新基金/袖套/数据/原语发现报告。
- [capability-map.md](references/capability-map.md)：通用能力、硬编码边界和沉淀规则。
