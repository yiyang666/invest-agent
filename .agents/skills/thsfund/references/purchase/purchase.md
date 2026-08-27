---
name: purchase
---

# 基金申购（thsfund / references/purchase/purchase）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或低于 0.2.0 时停下并展示安装命令，等用户完成后再继续。

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
| 禁止把购买意图当作风险二次确认 | 风险不匹配时必须单独确认 |
| 禁止把购买意图当作协议已阅读 | 协议确认必须由用户回复 `已阅读` |
| 禁止未记录协议阅读就签约检查 | 用户回复 `已阅读` 后必须先调用协议阅读记录接口 |
| 禁止钱包余额不足时拦截钱包支付 | 钱包支付由 `/ai/buy` 接口自行完成充值并购买 |
| 禁止只凭接口 `status_code=0000` 判定订单成功 | 订单成功必须按订单详情状态字段判定 |
| 禁止展示底层状态码 | 面向用户只展示中文状态和可读原因 |
| 禁止自行调用任何 token 接口 | Work Token 由 CLI 负责（详见 SKILL.md §2 第 3 条），skill 不需要也无法访问 |
| 禁止在申购失败时自动重试 | 网络超时、连接中断、5xx 或提交结果不明确时不得自动重试；先查询订单状态并向用户说明 |

## 本轮状态变量

执行过程中持续维护以下状态，后续接口必须使用本轮状态值，不得混用历史值：

| 变量 | 来源 | 用途 |
|---|---|---|
| `fundCode` | 用户指定/确认 | 所有申购相关接口必须使用该基金代码 |
| `fundName` | init | 展示和风险提示 |
| `amount` | 用户输入 | 金额校验、签约检查、下单 |
| `fundRiskLevel` | init | 风险等级校验 |
| `clientRiskLevel` | init 的 `ov_clientriskrate` | 风险等级校验 |
| `riskConfirmed` | 用户本轮回复 | 风险不匹配时是否已单独确认 |
| `selectedPayType` | 用户选择 | 钱包/银行卡，决定 `buyType` |
| `selectedTransAccountId` | 用户选择账户 | 签约检查和下单 |
| `selectedWalletAvailableVol` | init 中用户所选 `fundtzeroList` 账户的 `availableVol` | 判断钱包余额是否足额以及是否跳过签约检查；选择银行卡时为空 |
| `confirmedAgreements` | 本轮协议确认展示内容 | 协议阅读记录 |
| `appSheetSerialNo` | `/ai/buy` 返回 | 订单详情查询 |

## 基金锁定约束

1. 一旦确定用户要申购的 `fundCode`，整个流程中所有 API 调用都必须使用该 `fundCode`。
2. 不得因网络或临时错误自动重试申购；按 CLI 重试规则处理（CLI 仅在 HTTP 401 时刷新 Work Token 并重试一次，其他场景不重试）。
3. 业务错误（基金不存在、限额、状态不允许等）不得通过更换基金重试，必须将原始错误信息返回给用户。
4. 不得自行推测替代基金。

## 流程总览

按 Step 1 → Step 10 顺序执行；Work Token / `--dry-run` / 退出码语义等全局约定见 SKILL.md §2，不在本文档重复。

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

如果校验不通过，禁止继续金额校验、支付方式选择、协议确认、签约检查、下单等任何后续步骤。

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

- 必须在本次交易 init 之后、签约检查和下单之前，针对风险提示单独获得用户确认。
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

`availableVol` 只用于展示、钱包余额是否足额判断和 Step 8 是否跳过签约检查判断，不参与支付方式可选性、金额校验或下单资格判断。

用户选择钱包后，无论余额是否小于申购金额，都必须继续协议确认和 `/ai/buy` 下单。不得提示钱包余额不足，不得要求改选支付方式，不得自动切换银行卡，不得额外调用充值接口。

当用户选择钱包账户时，必须记录所选 `fundtzeroList` 账户的 `availableVol` 作为本轮 `selectedWalletAvailableVol`，供 Step 8 判断是否需要签约检查。

## Step 6：

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

用户选择支付方式后，必须查询并展示本轮交易需确认的协议。用户明确回复 `已阅读` 后，必须先调用协议阅读记录接口；记录成功后才能进入签约检查。

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

处理规则：

- 用户未明确回复 `已阅读` 前，禁止调用协议阅读记录接口。
- 协议阅读记录必须在用户回复 `已阅读` 后、签约检查或签约检查跳过判断之前调用。
- 协议阅读记录成功后，继续 Step 8 签约检查或签约检查跳过判断。
- 如协议阅读记录失败，展示失败原因并停止后续流程，不得继续签约检查、签约检查跳过判断或下单。

## Step 8：签约检查

签约检查前必须先判断是否满足跳过条件：

- 若用户选择钱包支付（`selectedPayType=钱包`），且所选钱包账户的 `selectedWalletAvailableVol >= amount`，则跳过签约检查接口，直接进入 Step 9 提交订单。
- 上述比较必须使用本轮用户所选钱包账户的 `availableVol` 和本轮申购金额 `amount`，按数值比较，不得使用展示字符串或历史账户余额。
- 仅当“钱包支付 + 所选钱包余额足额”两个条件同时满足时才能跳过签约检查；银行卡支付、钱包余额不足或无法可靠判断余额是否足额时，都必须继续调用签约检查接口。

需要签约检查时，调用：

```bash
aijijin fund check-before-trade \
  --amount "$amount" \
  --fund-code "$fundCode" \
  --transaction-account-id "$selectedTransAccountId"
```

若响应中 `signContract=false`，继续提交订单。

若 `signContract=true`，按 `references/purchase/display-templates.md` 的“签约提醒”模板展示。只有用户明确回复 `y` 时，才能继续提交订单；用户回复其他内容或尚未回复时，停留在本步骤，不得调用 `/ai/buy`。

## Step 9：提交订单

调用：

```bash
aijijin fund buy \
  --buy-type "$buyType" \
  --fund-code "$fundCode" \
  --amount "$amount" \
  --transaction-account-id "$selectedTransAccountId"
```

如需协议留痕校验（合规要求每笔申购记录到具体的协议版本号），追加：

```bash
aijijin fund buy \
  --buy-type "$buyType" \
  --fund-code "$fundCode" \
  --amount "$amount" \
  --transaction-account-id "$selectedTransAccountId" \
  --agreement-record "$agreementRecordId"
```

`$agreementRecordId` 取自 Step 7 协议阅读记录接口 `aijijin fund trade-record` 的服务端响应（用于唯一标识本次交易对应的协议记录）。该参数可选；不传时不发送 `extAttr` 字段，服务端按无协议留痕处理。CLI 内部会把 `--agreement-record <value>` 包装为 `extAttr: {AgreementRecord: <value>}`。

请求中的 `buyType` 必须使用 Step 6 根据用户选择确定的值：钱包 `1`，银行卡 `0`。

钱包支付时必须始终使用 `buyType=1` 和用户所选钱包的 `transActionAccountId`。

`/ai/buy` 返回 `appSheetSerialNo` 后，必须进入 Step 10 查询订单详情。`/ai/buy` 的 `ok: true` 只表示提交接口成功，不代表订单最终成功。

CLI 仅会在服务端明确返回 HTTP 401 时刷新 Work Token 并重试一次；网络超时、连接中断、5xx 或 `ok: false` 时，不得自动重试申购命令。

## Step 10：查询订单详情并展示结果

申购提交并取得 `appSheetSerialNo` 后，必须直接调用：

```bash
aijijin trade detail --order-id "$appSheetSerialNo"
```

订单状态必须按 `references/purchase/order-status.md` 的状态判断优先级判定，不得只因为详情接口返回 `ok: true` 就展示订单成功。

展示结果时使用 `references/purchase/display-templates.md` 的申购结果模板。

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
