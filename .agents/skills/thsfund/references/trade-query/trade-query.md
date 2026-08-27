---
name: trade-query
---

# 基金交易记录查询（thsfund / references/trade-query/trade-query）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或低于 0.2.0 时停下并展示安装命令，等用户完成后再继续。

基金交易记录查询与分析，包含：**分页拉取交易列表 → 必要时按订单号补全详情 → 汇总买卖/分红/转换/持仓盈亏**。

所有受保护数据均通过 `aijijin` CLI 命令读取。

---

## CLI 命令参数参考

本 Skill 只使用下面两条 CLI 命令。参数、取值、校验规则与响应字段统一见 [`cli.md`](cli.md)，本节只补 trade-query 动作特有的调用要点与字段语义。

读服务端字段时要再往下取一层 `data.<field>`（例如 `data.appSheetSerialNo`、`data.lastAcceptTime`）；`trade list` 的订单列表位于更深一层 `data.data[]`（即响应 envelope → `data` → `data.data[]` 三层嵌套）。

`--format table` 会从响应 `data` 字段渲染为 ASCII 文本表格（不是 Markdown）；本 skill 默认建议直接读 JSON 后用 Markdown 表格渲染给用户，避免 Windows 等宽字体下空格对齐错乱。

`--start-date` 和 `--end-date` 在正常调用中应显式传入；服务端在缺省这两个字段时默认查询最近 30 天，但 CLI 将其定义为必填。

---

## 分页逻辑（重点）

1. 首页使用 `offset = 1`，不要传 `--last-accept-time` 和 `--last-order-id`。
2. 下一页将 `offset` 加 1，并同时传入当前页最后一条数据的 `lastAcceptTime` 和 `lastAppSheetSerialNo`。
3. `offset != 1` 时两个游标参数缺一不可，否则会产生重复或缺失数据。
4. 每次请求成功后应立即保存当前页，再继续请求下一页。
5. CLI Schema 强制该约束：缺少游标会触发退出码 `2`（校验失败），而不是静默回退到首页。
6. 详情按需查询：仅当某条记录满足「列表字段补全规则」时，才用其 `appSheetSerialNo` 调用 `aijijin trade detail --order-id <订单号>`。

---

## 响应关键字段

`trade list` 的订单数组在响应第三层 `data.data[]`，每条记录字段如下。

### 交易列表 data.data[]

| 字段名 | 说明 | 可选值 |
|--------|------|--------|
| firstBusinessType | 一级交易类型 | buy（买入）, aip（定投）, sell（卖出）, change（转换）, dividend（分红）, other（其它） |
| firstBusinessTypeMsg | 一级交易类型描述 | 买入, 定投, 卖出, 转换, 分红, 其它 |
| subBusinessType | 二级交易类型 | card_subscription（普通申购∈买入）, card_reservation（普通认购∈买入）, wallet_subscription（钱包申购∈买入）, wallet_reservation（钱包认购∈买入）, demand_subscription（活期申购∈买入）, demand_reservation（活期认购∈买入）, award（活动奖励∈买入）, refund（基金退款∈买入）, income（基金收益∈买入）, fund_sold（基金卖出（到活期）∈买入）, card_aip（普通定投∈定投）, wallet_aip（钱包定投∈定投）, demand_aip（活期定投∈定投）, redemption_to_card（赎至银行卡∈卖出）, forced_redemption（强行赎回∈卖出）, redemption_to_wallet（赎至钱包∈卖出）, redemption_to_demand（赎至活期∈卖出）, fast_redemption（快速取现∈卖出）, redemption_for_buying（（从活期赎回）用于买基金∈卖出）, dividend_reinvestment（红利再投∈分红）, cash_dividend（现金分红∈分红）, normal_change（普通转换∈转换）, forced_increase（强行增加∈其它）, forced_decrease（强行减少∈其它） |
| subBusinessTypeMsg | 二级交易类型描述 | 普通申购, 普通认购, 钱包申购, 钱包认购, 活期申购, 活期认购, 活动奖励, 基金退款, 基金收益, 基金卖出, 普通定投, 钱包定投, 活期定投, 赎至银行卡, 强行赎回, 赎至钱包, 赎至活期, 快速取现, 用于买基金, 红利再投, 现金分红, 普通转换, 强行增加, 强行减少 |
| businessCode | 业务代码 | - |
| totalFee | 交易申请金额，精确到小数点后2位 | - |
| failFee | 交易失败金额，精确到小数点后2位 | - |
| totalFundUnits | 交易申请份额，小数位精度2 | - |
| failFundUnits | 交易失败份额，小数位精度2 | - |
| acceptTime | 交易发起时间，格式 yyyy-MM-dd HH:mm:ss | - |
| appSheetSerialNo | 交易申请单号 | - |
| transactionAccountId | 交易账号 | - |
| confirmFlag | 交易状态 | 0：待确认；1：已撤单；2：部分确认成功；3：确认成功；4：确认失败/认购接受失败；5：认购交易接受；6：订单作废 |
| payFlag | 支付状态 | 0：支付成功；1：支付失败；2：支付超时 |
| endFlag | 是否是最终状态 | 0：进行中状态；1：最终状态 |
| processStatus | 进行中状态 | 0：等待支付结果；1：预计XX-XX确认；2：交易失败 预计XX-XX回款；3：预计XX-XX回款；4：已撤单 预计XX-XX回款；5：待基金成立；6：进行中；7：提交申请中 |
| finalStatus | 最终状态 | 0：确认成功；1：成功；2：部分成功；3：部分成功 有退款；4：交易失败 有退款；5：已撤单；6：交易失败 |
| changeFundCode | 转换专用，转换的目标基金代码 | - |
| changeFundName | 转换专用，转换的目标基金名称 | - |
| fundCode | 基金代码，产品为个基时才有 | - |
| fundName | 基金名称，产品为个基时才有 | - |
| groupId | 组合id，产品为组合时才有 | - |
| groupName | 组合名称，产品为组合时才有 | - |
| status | 组合状态，产品为组合时才有，用于传递给组合交易记录详情页 | 0：组合子订单有未确认或作废的；1：组合已撤单 |
| toAccountTime | 预计回款时间，格式 yyyy-MM-dd HH:mm:ss | - |
| exceptConfirmTime | 预计确认时间，格式 yyyy-MM-dd HH:mm:ss | - |
| transactionCfmDate | 交易确认日，格式 yyyy-MM-dd | - |
| fixedIncomeFlag | 是否是固收 | 0：不是，1：是 |
| taCode | TA代码 | - |
| goldFlag | 是否是黄金宝交易 | - |
| goldConversionNum | 黄金宝转换系数，精确到小数点后两位 | - |
| ndConfirmedamount | 确认金额/转出确认金额 | - |
| ndConfirmedvol | 确认份额 | - |
| vcCanceltime | 撤单时间 | - |
| vcTransactioncfmdate | 确认日期 | - |
| vcCustid | 投资人id | - |
| vcSence | 业务场景 | GOLD（黄金宝），FIRMOFFER（实盘大赛） |

### 交易详情 data.*

| 字段 | 说明 |
|------|------|
| appSheetSerialNo | 订单号 |
| fundCode | 基金代码 |
| fundName | 基金名称 |
| businessCode | 业务代码 |
| businessName | 业务名称 |
| subBusinessTypeMsg | 子业务描述 |
| applicationAmount | 申请金额 |
| walletPayAmount | 钱包实际支付金额；存在时优先作为买入 `amount` |
| cardPayAmount | 银行卡实际支付金额；钱包支付金额为 0 时使用 |
| refundAmount | 认购部分确认或未确认时返还的资金 |
| actualFee | 实际费用 |
| confirmFlag | 确认状态 |
| cancelFlag | 撤单标志 (0可撤/1不可撤) |
| feeSource | 资金来源 (0银行卡/1活期/2钱包) |
| bankAccount | 银行卡尾号 |
| bankName | 银行名称 |
| acceptTime | 受理时间 |
| exceptCfmDate | 预计确认时间 |
| ftoAccountTime | 银行卡到账时间 |
| ftoscAccountTime | 钱包到账时间 |
| incomeDate | 收益日期 |
| targetFundCode / targetFundName | `redemption_for_buying` 对应的购买基金代码/名称 |

### 基金转换详情字段

基金转换记录必须查询详情，使用确认字段进行分析：

| 字段 | 说明 |
|------|------|
| outFundCode / outFundName | 转出基金代码/名称 |
| outConfirmedVol | 确认转出份额 |
| outNav | 转出确认净值 |
| inFundCode / inFundName | 转入基金代码/名称 |
| inConfirmedVol | 确认转入份额 |
| inNav | 转入确认净值 |
| charge | 转换费用 |
| navDate | 转换净值日期 |

### 列表字段补全规则

按以下规则使用 `appSheetSerialNo` 查询详情后再保存和分析：

- 买入、卖出记录的金额或份额缺少任意一个时，必须查询详情。
- `confirmFlag=1` 表示已撤单，`confirmFlag=4` 表示确认失败，`confirmFlag=6` 表示交易作废；这三类记录不需要补全数据，即使字段为空也不要查询详情。
- `subBusinessType` 包含 `reservation` 或描述包含“认购”时必须查询详情，即使列表金额和份额非空。
- 基金转换记录必须查询详情，以获取确认转出/转入份额和净值。
- `subBusinessType=redemption_for_buying` 时必须查询详情，以获取 `targetFundCode/targetFundName`。
- `amount`：优先读取详情的 `amount`，并兼容 `applicationAmount`、`confirmAmount` 等金额字段。
- 买入详情的 `walletPayAmount > 0` 时优先使用；否则使用 `cardPayAmount > 0`，再回退到申请金额。
- 认购详情存在 `refundAmount` 时，净支付金额和 FIFO 成本为 `实际支付金额 - refundAmount`，最低为 0。
- 费用可能有折扣或补贴，仅单独统计，不得再次叠加到 FIFO 成本。
- 保存并汇总 `refundAmount`，用于核对部分确认和全额返还记录。
- `shares`：优先读取详情的 `shares`，并兼容 `totalFundUnits`、`confirmShare` 等份额字段。
- 详情中的空值不得覆盖交易列表已有的非空字段。
- 每条详情查询成功后立即持久化，单条失败时保留列表数据，后续运行继续补全。

### 分红与基金转换分析规则

- `businessCode=098` 表示货币基金快速取现，按卖出处理；单位净值固定为 1，卖出金额等于卖出份额。
- `cash_dividend` 按现金分红金额计入已实现收益。
- `dividend_reinvestment` 按新增份额计入持仓，不增加现金投入成本。
- `firstBusinessType=other` 且 `shares > 0` 时，按份额分红/红利再投处理。
- 基金转换不是卖出：按 FIFO 扣减转出份额，将对应持仓成本迁移到 `inFundCode` 的 `inConfirmedVol`。
- 转换不确认买卖盈亏；目标基金后续卖出时按迁移后的单位成本计算。
- 转换详情不完整时暂不扣减转出份额，避免持仓成本丢失。
- `redemption_for_buying` 表示赎回货币基金用于购买目标基金；目标基金已有独立买入记录，不得合成额外买入。
- 使用相同 `acceptTime`、目标基金代码和金额匹配对应买入记录。
- 货币基金按真实赎回扣减 FIFO 份额，目标基金按真实买入记录建仓。
- 将配对的赎回与买入标记为内部资金划转，并从 `external_sell_amount`、`external_buy_amount` 中剔除。

### 单位净值与估值（描述性参考、严禁 CLI 调用）

> **严格禁止**：以下 NAV 接口**不在 `aijijin` CLI 暴露**，模型**不得**通过 `curl` 或 `python -c` 直接调用。任何 NAV 数据需求必须由用户自行通过其它渠道获取；本 skill 只在用户已经提供 NAV 时才引用，且绝不主动发起该请求。

参考地址（描述性，禁止调用）：`https://fund.10jqka.com.cn/quotation/fund_detail/v2/getNavData?fundCode=<fundCode>&range=now&type=unit`

- 从响应 `data.unit[]` 读取 `date` 和 `value`。
- 分红日或估值日无净值时，向前取最近一个净值。
- 红利再投估算金额 = 新增份额 × 分红日单位净值。
- 剩余持仓市值 = 剩余份额 × 估值日单位净值。
- 未实现盈亏 = 剩余持仓市值 - 剩余成本。
- 估算总盈亏 = FIFO 已实现盈亏 + 现金分红 + 未实现盈亏；不要重复加入红利再投估算金额。

---

## 调用示例

> **日期格式硬约束**：`--start-date` 与 `--end-date` 必须为 `YYYYMMDD` 形式的 8 位日期字符串（如 `20260811`）；CLI 在收到 `2026-08-11` 这类带分隔符的格式时会返回 `VALIDATION_ERROR: must be a yyyyMMdd date`（退出码 2）。模型在构造请求时必须把日期拼接成 `YYYYMMDD`，**不要**依赖 shell 自动化转换。

### 查询交易列表首页

```bash
aijijin trade list \
  --offset 1 \
  --limit 200 \
  --start-date "20260811" \
  --end-date "20260811" \
  --business-code all \
  --product-type all \
  --no-query-processing
```

### 使用游标查询下一页

```bash
aijijin trade list \
  --offset "$offset" \
  --limit 200 \
  --start-date "20260811" \
  --end-date "20260811" \
  --business-code all \
  --product-type all \
  --no-query-processing \
  --last-accept-time "$lastAcceptTime" \
  --last-order-id "$lastAppSheetSerialNo"
```

### 查询交易详情

```bash
aijijin trade detail --order-id "$appSheetSerialNo"
```

---

## 完整交互流程

```
1. 收集参数 → 用户/上下文提供 startDate、endDate
2. 首页查询 → aijijin trade list --offset 1（不带游标）
3. 解析 data.data[] → 提取每条记录的 appSheetSerialNo 与关键字段
4. 判断是否需要补全 → 根据列表字段补全规则决定是否调用详情
5. 详情补全 → aijijin trade detail --order-id <订单号>
6. 分页推进 → 取最后一行的 acceptTime、appSheetSerialNo 作为下一页游标
7. 汇总分析 → 按分析规则合并买卖/分红/转换/未实现盈亏
8. 返回结果 → 输出汇总的 Markdown 表格与中文状态
```

---

## 注意事项

1. **Work Token**：见 SKILL.md §2 第 3 条。
2. **校验失败会立即退出**：`--offset != 1` 但缺少游标时 CLI 返回退出码 `2`，不要试图补全参数后继续旧请求。
3. **保存当前页**：每页返回后立即持久化，再发起下一页请求，避免网络中断导致数据丢失。
4. **详情失败不影响列表**：单条 `aijijin trade detail` 失败时保留列表已有字段，后续运行继续补全即可。
5. **NAV 仅供分析**：NAV 说明是描述性参考，没有对应 CLI 子命令；如需 NAV 数据用于持仓市值或未实现盈亏分析，需通过其它渠道查询。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
