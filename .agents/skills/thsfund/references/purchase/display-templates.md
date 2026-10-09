---
name: purchase/display-templates
---

# 基金申购展示模板（thsfund / references/purchase/display-templates）

> 公共约定见 SKILL.md §2。业务流程以 `./purchase.md` 为准。

本文件维护申购动作（`thsfund` 的 purchase 链路）的用户可见展示格式。输出要简洁、稳定、适合终端阅读；优先使用 Markdown 表格和短段落，避免超宽 ASCII 分隔线。

## 通用排版规则

- 对结构化信息使用 Markdown 表格。
- 每个阶段使用二级或三级标题，不使用超长等号分隔线。
- 金额统一展示为 `¥金额` 或 `金额 元`，同一张表内保持一致。
- 账号只展示银行名和尾号，不展示完整卡号。
- 风险提示、协议确认等需要用户确认的节点，最后一行必须明确告诉用户可回复的内容。
- 不展示底层状态字段名和值，例如 `confirmFlag`、`checkFlag`、`failMsg.code`。

## 1. 基金申购信息

```markdown
## 基金申购信息

| 项目 | 内容 |
|---|---|
| 基金代码 | <fundCode> |
| 基金名称 | <fundName> |
| 申购金额 | <amount> 元 |
| 起购金额 | <minBuy> 元 |
| 追加金额 | <minAddBuy> 元 |
| 单笔最大购买 | <maxBuy> 元 |
| 产品风险等级 | R<fundRiskLevel> |
| 客户风险等级 | C<ov_clientriskrate> |

### 阶梯费率

| 申购金额区间 | 原始费率 |
|---|---:|
| <money1> | <rate1> |
| <money2> | <rate2> |

### 购买折扣

| 支付方式 | 折扣 |
|---|---|
| 银行卡 | <银行卡折扣描述> |
| 钱包 | <钱包折扣描述> |

### 持有期间费用

| 费用类型 | 费率 |
|---|---:|
| 管理费 | <glf> |
| 托管费 | <tgf> |
| 销售服务费 | <fwf> |
```

如有封闭期，在持有期间费用后追加：

```markdown
### 封闭期提示

⚠️ <封闭期提示文案>
```

## 2. 风险二次确认

```markdown
## 风险提示

该产品「<fundName>（<fundCode>）」的风险等级（R<fundRiskLevel>）高于您自身的风险等级（C<ov_clientriskrate>）。

购买该产品/服务，可能导致您承担超出自身承受能力的损失和不利后果。请您认真了解产品风险特征，慎重购买。

如需继续本次申购，请回复：`确认继续`  
如不继续，请回复：`不继续`
```

## 3. 支付方式选择

### 钱包 + 银行卡

```markdown
## 请选择支付方式

### 钱包账户

| 编号 | 账户 | 可用余额 |
|---|---|---:|
| 钱包1 | <bankName>-<tailNo> | ¥<availableVol> |
| 钱包2 | <bankName>-<tailNo> | ¥<availableVol> |

### 银行卡

| 编号 | 账户 | 单笔限额 | 单日限额 |
|---|---|---:|---:|
| 银行卡1 | <bankName>-<tailNo> | ¥<maxPurchaseOfOne> | ¥<maxPurchaseOfDay> |
| 银行卡2 | <bankName>-<tailNo> | ¥<maxPurchaseOfOne> | ¥<maxPurchaseOfDay> |

请回复编号选择支付方式，例如：`钱包1` 或 `银行卡1`。
```

### 仅银行卡

```markdown
## 请选择支付方式

### 银行卡

| 编号 | 账户 | 单笔限额 | 单日限额 |
|---|---|---:|---:|
| 银行卡1 | <bankName>-<tailNo> | ¥<maxPurchaseOfOne> | ¥<maxPurchaseOfDay> |
| 银行卡2 | <bankName>-<tailNo> | ¥<maxPurchaseOfOne> | ¥<maxPurchaseOfDay> |

请回复编号选择支付方式，例如：`银行卡1`。
```

## 4. 分仓选择

仅在所选交易账户以 `600` 开头时使用：

```markdown
## 请选择买入归属

| 编号 | 分仓 | 当前持仓 | 类型 |
|---|---|---:|---|
| 分仓1 | <subBusinessUserName1> | <响应提供则展示，否则 -> | <响应提供则展示，否则 -> |
| 分仓2 | <subBusinessUserName2> | <响应提供则展示，否则 -> | <响应提供则展示，否则 -> |
| 普通 | 普通持仓（默认） | - | 普通持仓 |
| 新建 | 新建一个独立分仓 | - | - |

请回复编号或分仓名；未指定分仓时默认归入普通持仓。
```

用户选择新建时：

```markdown
请输入新分仓名称。名称可包含汉字、数字、字母和空格；同一交易账户下不能重名。
```

## 5. 协议确认

```markdown
## 协议确认

请仔细阅读、了解并确认以下协议：

- [<协议1名称>](<协议1URL>)
- [<协议2名称>](<协议2URL>)
- [<协议3名称>](<协议3URL>)

<高风险产品附加文案，如适用：且已阅读产品详情页，知晓本产品为高风险。>

您可以点击上述链接查看协议详细内容。

如已阅读并同意继续交易，请回复：`已阅读`
```

模板中不得放示例真实 URL，实际输出只能使用本轮协议查询接口返回并解析后的 URL。

## 6. 最终买入确认

```markdown
## 请确认买入

| 项目 | 内容 |
|---|---|
| 基金 | <fundName>（<fundCode>） |
| 金额 | <amount> 元 |
| 支付账户 | <钱包/银行卡> - <bankName>-<tailNo> |
| 关联分仓 | <selectedStrategyName；未选时为普通持仓> |

确认以上信息并提交订单，请回复：`确认买入`  
取消本次交易，请回复：`取消`
```

## 7. 申购结果：订单成功

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 基金代码 | <fundCode> |
| 基金名称 | <fundName> |
| 购买金额 | <amount> 元 |
| 支付方式 | <钱包/银行卡> - <bankName>-<tailNo> |
| 关联分仓 | <selectedStrategyName> |
| 申请下单时间 | <acceptTime> |
| 交易状态 | ✅ 订单成功 |
| 预计确认时间 | <exceptCfmDate> |
| 预计可查看收益时间 | <incomeDate> |
| 订单号 | <appSheetSerialNo> |
```

## 8. 申购结果：处理中

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 申请下单时间 | <acceptTime 或 -> |
| 关联分仓 | <selectedStrategyName> |
| 交易状态 | ⏳ 订单处理中 |
| 提示 | 请稍后重新查询结果 |
| 订单号 | <appSheetSerialNo> |
```

## 9. 申购结果：失败

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 申请下单时间 | <acceptTime 或 -> |
| 关联分仓 | <selectedStrategyName> |
| 交易状态 | ❌ 订单失败 |
| 失败原因 | <failMsg.thsMessage 或 failMsg.message> |
| 订单号 | <appSheetSerialNo> |
```

## 10. 申购结果：已撤单

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 申请下单时间 | <acceptTime 或 -> |
| 关联分仓 | <selectedStrategyName> |
| 交易状态 | ↩️ 已撤单 |
| 订单号 | <appSheetSerialNo> |
```

## 11. 申购结果：基金公司确认失败

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 申请下单时间 | <acceptTime 或 -> |
| 关联分仓 | <selectedStrategyName> |
| 交易状态 | ❌ 基金公司确认失败 |
| 订单号 | <appSheetSerialNo> |
```

## 12. 申购结果：部分确认成功，部分失败

```markdown
## 基金申购结果

| 项目 | 内容 |
|---|---|
| 申请下单时间 | <acceptTime 或 -> |
| 关联分仓 | <selectedStrategyName> |
| 交易状态 | ⚠️ 基金公司部分确认成功，部分失败 |
| 已确认金额 | <confirmedAmount 或 -> |
| 已确认份额 | <confirmedVol 或 -> |
| 订单号 | <appSheetSerialNo> |
```


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
