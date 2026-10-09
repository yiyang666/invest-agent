---
name: purchase
---

# 基金申购（thsfund / references/purchase/purchase）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或版本不是 0.2.1 时停下并展示重装命令，等用户完成后再继续。

本 skill 用于执行基金申购真实交易流程。它不是投资建议或基金查询流程；一旦用户表达买入、申购、购买基金等交易意图，必须按本文档执行。

## 严格执行要求

无论用户使用什么模型、客户端或表达方式，只要命中本 Skill 的使用场景，都必须严格按照本 Skill 执行，并按本文档指定格式展示对应内容。

- 不得自行简化流程。
- 不得复用历史接口数据。
- 不得跳过任何用户确认节点。
- 不得把用户的购买意图当作风险确认、协议确认或支付方式选择。
- 不得在未完成前置步骤时调用后续交易接口。

申购链路的 API 字段、展示模板、订单状态判定按需读取 `references/purchase/` 子目录下的文档：

- API 字段 → `references/purchase/api-reference.md`
- 展示模板 → `references/purchase/display-templates.md`
- 订单状态判定 → `references/purchase/order-status.md`

读服务端字段时要再往下取一层：`data.<field>`（例如 `data.fundRiskLevel`、`data.paramOpenFundAccBean.fundCode`、`data.fundtzeroList[].transActionAccountId`、`data.accountValidateResult.validateCode`）。

`/ai/buy` 接口返回 `ok: true` 只表示提交成功，不等于订单最终成功——必须按 `references/purchase/order-status.md` 查询订单详情判定。

## 全局禁止事项

| 禁止事项 | 原因 |
|---|---|
| 禁止自动更换基金代码 | 真实交易必须锁定用户指定基金，避免买错产品 |
| 禁止使用其他 skill 的接口绕过失败 | 申购流程必须使用本文档指定接口 |
| 禁止替用户选择支付方式 | 支付账户必须由用户明确选择 |
| 禁止把非 `600` 开头账户用于虚拟分仓查询或创建 | 虚拟账户必须绑定 `600` 开头的交易账户 |
| 禁止在虚拟账户创建失败或结果不明确时自动重试 | 重试可能重复创建分仓；必须停止并说明结果不明确 |
| 禁止把购买意图当作风险二次确认 | 风险不匹配时必须单独确认 |
| 禁止把购买意图当作协议已阅读 | 协议确认必须由用户回复 `已阅读` |
| 禁止未记录协议阅读就下单 | 用户回复 `已阅读` 后必须先调用协议阅读记录接口 |
| 禁止钱包余额不足时拦截钱包支付 | 钱包支付由 `/ai/buy` 接口自行完成充值并购买 |
| 禁止只凭接口 `status_code=0000` 判定订单成功 | 订单成功必须按订单详情状态字段判定 |
| 禁止展示底层状态码 | 面向用户只展示中文状态和可读原因 |
| 禁止在申购失败时自动重试 | 网络超时、连接中断、5xx 或提交结果不明确时不得自动重试；先查询订单状态并向用户说明 |
| 禁止申购养老基金（productType=0105）/ 黄金宝（productType=0107） | 当前 skill 不支持这两种产品类型 |

## 本轮状态变量

执行过程中持续维护以下状态，后续接口必须使用本轮状态值，不得混用历史值：

| 变量 | 来源 | 用途 |
|---|---|---|
| `fundCode` | 用户指定/确认 | 所有申购相关接口必须使用该基金代码 |
| `fundName` | init | 展示和风险提示 |
| `amount` | 用户输入 | 金额校验、下单 |
| `fundRiskLevel` | init | 风险等级校验 |
| `clientRiskLevel` | init 的 `ov_clientriskrate` | 风险等级校验 |
| `riskConfirmed` | 用户本轮回复 | 风险不匹配时是否已单独确认 |
| `selectedPayType` | 用户选择 | 钱包/银行卡，决定 `buyType` |
| `selectedTransAccountId` | 用户选择账户 | 下单 |
| `selectedWalletAvailableVol` | init 中用户所选 `fundtzeroList` 账户的 `availableVol` | 判断钱包余额是否足额；选择银行卡时为空 |
| `selectedGeneralTradeId` | 用户选定支付账户的 `transActionAccountId` | `600` 开头时用于查询/创建虚拟账户；值必须与本轮支付账户一致 |
| `selectedTradeId` | 普通持仓取 `selectedTransAccountId`；已有分仓取列表项 `vcTransactionaccountid`；新建分仓取创建成功响应 | 下单必传的 `tradeId` |
| `selectedStrategyName` | 已有分仓取列表项 `subBusinessUserName`；新建分仓取创建名称 | 最终确认与结果展示；普通持仓固定为“普通持仓” |
| `confirmedAgreements` | 本轮协议确认展示内容 | 协议阅读记录 |
| `agreementRecordId` | Step 7 协议阅读记录成功响应 | Step 8 下单必传的协议记录号 |
| `appSheetSerialNo` | `/ai/buy` 返回 | 订单详情查询 |

## 基金锁定约束

1. 一旦确定用户要申购的 `fundCode`，整个流程中所有 API 调用都必须使用该 `fundCode`。
2. 不得因网络或临时错误自动重试申购；按 CLI 重试规则处理（CLI 仅在 HTTP 401 时刷新 Work Token 并重试一次，其他场景不重试）。
3. 业务错误（基金不存在、限额、状态不允许等）不得通过更换基金重试，必须将原始错误信息返回给用户。
4. 不得自行推测替代基金。

## 流程总览

按 Step 1 → Step 9 顺序执行；Step 5 选择支付账户后必须紧接 Step 5.1 处理分仓。Work Token / `--dry-run` / 退出码语义等全局约定见 SKILL.md §2，不在本文档重复。

## Step 0：CLI 自动管理 Work Token

- Work Token 与重试规则：见 SKILL.md §2 第 3、4 条。
- `--dry-run`：见 SKILL.md §2 第 7 条（仅在排查 Schema/拼写时使用，不要写进正常调用样例）。

## Step 1：收集基金代码和申购金额

- 如果用户只给出基金名称，先根据用户意图确认基金代码。
- 如果用户未提供金额，先询问申购金额，不得直接进入支付方式选择或下单。
- 基金代码确认后锁定为 `fundCode`。

## Step 2：申购初始化、费用查询与信息展示

1. 调用申购初始化命令：

   ```bash
   aijijin fund subscribe-init --fund-code "$fundCode"
   ```

   **产品类型阻断检查**：读取 init 响应 `data.paramOpenFundAccBean.productType`，按下表路由：

   | productType | 含义 | 处理方式 |
   |---|---|---|
   | `0105` | 养老基金 | **阻断申购**，停止流程，向用户告知「该基金属于养老基金，本 skill 当前不支持申购养老基金，请前往同花顺 App 完成」 |
   | `0107` | 黄金宝 | **阻断申购**，停止流程，向用户告知「该基金属于黄金宝，本 skill 当前不支持申购黄金宝，请前往同花顺 App 完成」 |
   | 其他 | 普通基金 | 继续后续步骤 |

   阻断后**不得**继续任何后续步骤（费用查询、信息展示、合规校验、风险测评、支付方式选择、协议确认、下单）。

2. 调用基金费用信息查询命令：

   ```bash
   aijijin fund fee-rule --fund-code "$fundCode"
   ```

3. 按 `references/purchase/display-templates.md` 的“基金申购信息”模板展示：
   - 基金代码、基金名称、申购金额
   - 起购金额、追加金额、单笔最大购买
   - 产品风险等级、客户风险等级
   - 阶梯费率
   - 银行卡/钱包购买折扣
   - 管理费、托管费、销售服务费
4. 判断是否存在封闭期；如存在，追加封闭期提示。

### 费用和折扣展示规则

- 申购金额区间使用费用查询接口 `data.rateInfo.sg.qd[].money`。
- 原始费率使用 `data.rateInfo.sg.qd[].rate`，不展示折后费率 `irate`。
- 折扣值转化：返回值 × 10 = 几折。
- 返回值 `1` 或原始申购费率为 `0` 时，展示“不打折/无需折扣”。
- 返回值 `0` 时，展示“0折（免手续费）”。

### 封闭期提示规则

| 类型 | 判断条件 | 提示文案 |
|---|---|---|
| Cycle（周期型） | `isRollingHold=0` + `hasRedeemDate=1` + `hasLockPeriod=0`，或 `isRollingHold=0` + `hasRedeemDate=1` + `hasLockPeriod=1`，或 `isRollingHold=1` + `hasRedeemDate=0` + `hasLockPeriod=1` | 本基金存在**封闭锁定期**，封闭阶段不支持转出，封闭结束可赎回，如未赎回则进入下一个封闭期，具体规则以官方公告及基金合同为准。 |
| New（新基金型） | `buyUrl='ren'` | 新基金成立后一般会有一个**最长不超过3个月**的封闭期，封闭阶段不支持转出，封闭结束可赎回，具体规则以官方公告及基金合同为准。 |
| Normal（普通型） | `hasLockPeriod=1` 且不满足上述 Cycle 条件 | 本基金存在**封闭锁定期**，封闭阶段不支持转出，封闭结束可赎回，具体规则以官方公告及基金合同为准。 |

## Step 3：合规校验

个人信息校验必须在风险等级校验之前完成。

个人信息校验的主数据源是 init 返回的 `data.accountValidateResult.validateCode`。`account-status` 仅作为可选补充，不可作为主依赖；返回退出码 4（业务失败）或 HTTP 404 时必须忽略并回退 init 字段。

| validateCode | 说明 | 处理方式 |
|---|---|---|
| `0000` | 通过 | 继续后续校验 |
| `0001` | 身份信息无效 | 阻止申购，提示完善身份信息 |
| `0002` | 身份证照片未上传或审核失败 | 阻止申购，提示上传身份证照片 |
| `0003` | 职业信息未完善 | 阻止申购，提示完善职业信息 |

阻止申购时展示：

```text
您的个人信息未完善，请到同花顺爱基金或者同花顺理财的个人中心补充对应信息。
```

如果校验不通过，禁止继续金额校验、支付方式选择、协议确认、下单等任何后续步骤。

## Step 4：风险测评与风险等级校验

### 风险测评状态

使用 init 返回的 `data.ov_flag`：

| ov_flag | 含义 | 处理方式 |
|---|---|---|
| `1` | 未做风险测评 | 终止流程 |
| `2` | 评测与基金不匹配 | 进入风险等级校验的二次确认场景，不重复提示两次 |
| `3` | 评测与基金匹配 | 继续风险等级校验 |

`ov_flag=1` 时展示：

```text
您尚未完成风险测评，为了不影响您的基金交易，请到同花顺理财或同花顺爱基金个人中心完成风险评测。
```

### 风险等级规则

风险等级数值：1=最低风险，5=最高风险。产品风险等级用 `R` 表示，客户风险等级用 `C` 表示。

| 客户风险等级 | 产品风险等级 | 处理方式 |
|---|---|---|
| C1 | R1 | 通过 |
| C1 | R2~R5 | 阻止申购 |
| C2~C5 | ≤ 客户等级 | 通过 |
| C2~C5 | > 客户等级 | 必须进行风险二次确认 |

### C1 阻止申购

```text
您的风险等级为 C1（最低），只能购买 R1 风险等级的产品。
当前基金「<fundName>（<fundCode>」）风险等级为 R<fundRiskLevel>，超出您的风险承受能力，无法购买。
```

### 风险二次确认

当客户非 C1 且产品风险高于客户等级时，按 `references/purchase/display-templates.md` 的“风险二次确认”模板展示。

强制要求：

- 必须在本次交易 init 之后、下单之前，针对风险提示单独获得用户确认。
- 不得把用户此前表达过购买意图、前一轮确认或当前购买指令当作本轮风险确认。
- 若风险确认被中断、拒绝或未完成，必须重新发起二次确认。
- 未收到明确的 `确认继续` 前，禁止进入支付方式选择之后的任何交易步骤。

## Step 5：支付方式选择

必须展示可用支付方式供用户选择，禁止自动选择。

### 支付方式判断

| moneytostockTzeroFlag | 含义 | 展示方式 |
|---|---|---|
| `0` | 不支持钱包支付 | 仅展示银行卡 |
| `1` | 支持钱包支付 | 分区展示钱包账户和银行卡 |

展示格式使用 `references/purchase/display-templates.md` 的“支付方式选择”模板。

### 账户字段

| 账户类型 | 字段 |
|---|---|
| 钱包账户 | `fundtzeroList[].bankName`、`bankAccount`、`availableVol`、`transActionAccountId` |
| 银行卡 | `bankCardSplitListResult[].bankName`、`bankAccount`、`maxPurchaseOfOne`、`maxPurchaseOfDay`、`transActionAccountId` |

银行卡必须同时展示单笔限额和单日限额；字段为空时展示 `-`。

### 用户选择解析规则

- 优先按编号匹配，例如 `钱包1`、`银行卡2`。
- 编号不明确时，可按银行名和尾号匹配。
- 如果匹配不唯一，必须要求用户重新选择，不得自动猜测。

### buyType 规则

| 用户选择 | buyType | 账户来源 |
|---|---:|---|
| 钱包账户 | `1` | `fundtzeroList` |
| 银行卡账户 | `0` | `bankCardSplitListResult` |

### 钱包余额处理

`availableVol` 只用于展示和钱包余额是否足额判断，不参与支付方式可选性、金额校验或下单资格判断。

用户选择钱包后，无论余额是否小于申购金额，都必须继续协议确认和 `/ai/buy` 下单。不得提示钱包余额不足，不得要求改选支付方式，不得自动切换银行卡，不得额外调用充值接口。

当用户选择钱包账户时，必须记录所选 `fundtzeroList` 账户的 `availableVol` 作为本轮 `selectedWalletAvailableVol`，仅用于钱包余额是否足额的展示与判断。

## Step 5.1：选择或创建虚拟分仓

虚拟账户用于分仓管理，一个虚拟账户对应一个持仓。该步骤必须在支付账户确定后执行，因为虚拟账户绑定到具体交易账户。

### 资格判断

将本轮所选支付账户的 `transActionAccountId` 同时记录为 `selectedGeneralTradeId`：

| 条件 | 动作 |
|---|---|
| `selectedGeneralTradeId` 匹配 `^600[0-9]+$` | 查询该交易账户下的虚拟账户 |
| 不匹配 | 不调用任何 `trade-account` 命令；设置 `selectedTradeId = selectedTransAccountId`、`selectedStrategyName=普通持仓`，继续 Step 6 |

禁止为了启用分仓而更换用户已选支付账户。

### 查询并展示已有分仓

```bash
aijijin trade-account list \
  --general-trade-id "$selectedGeneralTradeId"
```

只使用本次响应实际返回的虚拟账户。列表项字段按以下固定映射读取：

- `subBusinessUserName`：策略名称，用于展示并记录为 `selectedStrategyName`。
- `vcTransactionaccountid`：虚拟交易账户，用于记录为 `selectedTradeId`。

`subBusinessUserName` 为空或仅含空白的列表项不展示给用户；列表为空时不阻断流程，仍展示“普通持仓”和“新建一个独立分仓”。持仓金额、标签类型等字段仅在响应明确提供时展示，禁止猜测。按 `references/purchase/display-templates.md` 的“分仓选择”模板展示。

- 用户明确选择已有分仓：记录该项实际返回的 `vcTransactionaccountid` 与 `subBusinessUserName`。
- 用户选择普通持仓，或未主动指定任何分仓：设置 `selectedTradeId = selectedTransAccountId`、`selectedStrategyName=普通持仓`；当所选账户为 `600` 开头时，`selectedTradeId` 即该 `600` 账号。
- 用户输入无法唯一匹配多个分仓：要求重新选择，不得猜测。
- 查询失败：展示可读错误并停止本次申购，不得在无法确认分仓列表时创建或下单。

### 新建独立分仓

用户选择“新建一个独立分仓”后，询问分仓名。名称去除首尾空白后必须非空；允许汉字、数字、字母和内部空格。

创建前必须再次执行 `trade-account list`，用最新列表在当前 `selectedGeneralTradeId` 下校验重名：

- 若已有相同的 `subBusinessUserName`（与待创建 `strategyName` 比较去除首尾空白后的完整名称），不得重复创建；提示用户选择已有分仓或输入其他名称。
- 无重名时才执行：

  ```bash
  aijijin trade-account create \
    --general-trade-id "$selectedGeneralTradeId" \
    --strategy-name "$strategyName"
  ```

创建成功后，必须从本次响应读取新虚拟账户的 `tradeId`，记录为 `selectedTradeId`，并记录实际 `strategyName`。响应缺少非空 `tradeId` 时视为结果不明确，停止流程，不得下单。

`trade-account create` 是不可安全重放的外部写操作：包括 HTTP 401、网络超时、连接中断、5xx、响应异常或业务失败在内，CLI 与 skill 均禁止自动重试，也禁止转而用同名再次创建。

## Step 6：金额校验

### 申购类型判断

- 账户在 `subOrAddResult` 中：追加申购，金额需 `>= minAddBuy`。
- 账户不在 `subOrAddResult` 中：新申购，金额需 `>= minBuy`。

### 校验规则

1. 新申购金额必须 `>= minBuy`。
2. 追加申购金额必须 `>= minAddBuy`。
3. 金额必须 `<= maxBuy`。
4. 金额必须符合级差要求。

### 失败处理

- 金额不足：提示最低起购/追加金额，并要求用户重新输入金额。
- 超过上限：提示单笔最大购买金额，并要求用户重新输入金额。
- 级差不符：提示金额需符合级差要求，并要求用户重新输入金额。
- 金额未通过校验前，禁止展示支付方式或进入后续流程。

## Step 7：协议确认与协议阅读记录

用户选择支付方式后，必须查询并展示本轮交易需确认的协议。用户明确回复 `已阅读` 后，必须先调用协议阅读记录接口；记录成功后才能进入最终买入确认与下单步骤。

### 协议查询和展示

调用协议查询命令，使用返回的 `data.tradeInitTreaty` 作为本轮需展示并确认的基金协议列表：

```bash
aijijin fund trade-treaty --fund-code "$fundCode"
```

每个协议必须显示为 Markdown 链接：

```markdown
- [协议名称](协议真实URL)
```

链接解析规则见 `references/purchase/api-reference.md`。

展示格式使用 `references/purchase/display-templates.md` 的“协议确认”模板。

禁止在模板或实际输出中使用示例真实 URL；实际输出只能使用本轮协议查询接口返回并解析后的 URL。

### confirmedAgreements 规则

`agreements` 必须使用本轮【协议确认】阶段已展示给用户、且用户回复 `已阅读` 明确确认的协议列表。不得使用示例协议、历史协议或未向用户展示的协议。

每个协议对象格式：

```json
{
  "title": "<协议名称>",
  "agreementUrl": "<协议真实URL>"
}
```

规则：

- `title` 使用已展示给用户的协议名称。
- `agreementUrl` 使用已展示给用户的 Markdown 链接 URL。
- 如果本次交易需要记录固定协议，也必须先展示给用户并由用户回复 `已阅读` 确认，才能放入 `agreements`。
- 高风险产品附加文案属于页面提示，不是协议对象；除非有明确 URL 并作为协议链接展示，否则不要加入 `agreements`。
- 禁止把未展示、未确认的协议写入 `agreements`。

### 协议阅读记录接口

用户回复 `已阅读` 后，必须先把协议对象写入临时 JSON 文件（避免命令行转义问题），然后调用：

```bash
aijijin fund trade-record --json-file "$tradeRecordFile"
```

`$tradeRecordFile` 内容示例：

```json
{
  "agreements": [
    {
      "title": "<协议1名称>",
      "agreementUrl": "<协议1URL>"
    },
    {
      "title": "<协议2名称>",
      "agreementUrl": "<协议2URL>"
    }
  ],
  "sourceType": "BUY"
}
```

`agreements` 必须严格使用本轮 `confirmedAgreements`，不得加入示例协议或历史协议。

成功判定：

- CLI 退出码 0 且响应中顶层 `ok: true` 时，视为记录成功。
- 其他情况视为失败：展示响应中的 `message` 或 `error.msg`，并停止后续流程。
- 记录成功后必须从本次响应读取非空协议记录号，保存为 `agreementRecordId`；缺失时停止流程，不得进入下单。

处理规则：

- 用户未明确回复 `已阅读` 前，禁止调用协议阅读记录接口。
- 协议阅读记录必须在用户回复 `已阅读` 后、最终买入确认与下单之前调用。
- 协议阅读记录成功后，进入 Step 8 提交订单。
- 如协议阅读记录失败，展示失败原因并停止后续流程，不得继续下单。

## Step 8：提交订单

完成风险、支付方式、分仓、金额与协议步骤后，按 `references/purchase/display-templates.md` 的“最终买入确认”模板展示基金、金额、支付账户和本轮 `selectedStrategyName`。普通持仓显示“普通持仓”。

### 提交订单

统一调用以下命令，不按普通持仓或虚拟分仓拆分参数：

```bash
aijijin fund buy \
  --buy-type "$buyType" \
  --fund-code "$fundCode" \
  --amount "$amount" \
  --transaction-account-id "$selectedTransAccountId" \
  --trade-id "$selectedTradeId" \
  --agreement-record "$agreementRecordId"
```

`$agreementRecordId` 取自 Step 7 协议阅读记录接口 `aijijin fund trade-record` 的本次成功响应，用于唯一标识本次交易对应的协议记录。

请求中的 `buyType` 必须使用 Step 5 根据用户选择确定的值：钱包 `1`，银行卡 `0`。

`tradeId` 规则：

| 分仓归属 | `fund buy` 参数 |
|---|---|
| 普通持仓 | 传 `selectedTradeId = selectedTransAccountId`；所选账户为 `600` 开头时即传该 `600` 账号 |
| 已有虚拟分仓 | 传该列表项实际返回的 `vcTransactionaccountid` |
| 本轮新建虚拟分仓 | 传创建成功响应实际返回的 `--trade-id` |

禁止把 `subBusinessUserName` 或创建入参 `strategyName` 当作 `tradeId`，禁止沿用历史轮次或其他支付账户的虚拟交易账户，禁止手工通过 `--json` 注入未校验的分仓字段。

钱包支付时必须始终使用 `buyType=1` 和用户所选钱包的 `transActionAccountId`。

### 完整分仓下单示例

```bash
aijijin trade-account list \
  --general-trade-id "600110053853"

aijijin fund buy \
  --buy-type "0" \
  --fund-code "000083" \
  --amount "100.00" \
  --transaction-account-id "600110053853" \
  --trade-id "v00110054143" \
  --agreement-record "150772237510861572"
```

此示例中的虚拟交易账户和协议记录号只能来自本轮实际响应，不得复制示例值用于真实交易。若用户选择普通持仓，同一命令仍必须保留 `--trade-id`，其值改为所选普通交易账户 `600110053853`。

### 常见错误

| 错误 | 正确做法 |
|---|---|
| 支付账户未确定就查询分仓 | 先完成 Step 5，再用所选账户查询 |
| 用虚拟 `tradeId` 调用 `trade-account list/create` | 两个命令都传绑定它的 `600` 开头普通交易账户 |
| 用分仓名代替 `tradeId` | `subBusinessUserName` 只用于展示；下单使用响应实际返回的 `vcTransactionaccountid` |
| 普通持仓传空字符串 `--trade-id ""` 或省略参数 | 传本轮所选普通交易账户；`600` 账户即传对应的 `600` 账号 |
| 创建失败后用同名自动重试 | 停止并报告结果不明确，待人工确认后再继续 |

`/ai/buy` 返回 `appSheetSerialNo` 后，必须进入 Step 9 查询订单详情。`/ai/buy` 的 `ok: true` 只表示提交接口成功，不代表订单最终成功。

CLI 仅会在服务端明确返回 HTTP 401 时刷新 Work Token 并重试一次；网络超时、连接中断、5xx 或 `ok: false` 时，不得自动重试申购命令。

## Step 9：查询订单详情并展示结果

申购提交并取得 `appSheetSerialNo` 后，必须直接调用：

```bash
aijijin trade detail --order-id "$appSheetSerialNo"
```

订单状态必须按 `references/purchase/order-status.md` 的状态判断优先级判定，不得只因为详情接口返回 `ok: true` 就展示订单成功。

展示结果时使用 `references/purchase/display-templates.md` 的申购结果模板。

结果中的“关联分仓”使用本轮已提交的 `selectedStrategyName` 展示；普通持仓显示“普通持仓”。不得因为订单详情暂未回显分仓字段而改写本轮已提交的归属。

面向用户展示时：

- 不得展示 `confirmFlag` 或 `checkFlag` 的字段名、字段值及“状态标识”行。
- 不得展示 `failMsg.code` 或任何错误码。
- 订单失败时只展示可读失败原因，优先使用 `failMsg.thsMessage`，为空时使用 `failMsg.message`。
- 其他订单状态也只展示中文状态描述。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
