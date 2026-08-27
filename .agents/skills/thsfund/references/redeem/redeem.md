---
name: redeem
---

# 基金赎回（thsfund / references/redeem/redeem）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或低于 0.2.0 时停下并展示安装命令，等用户完成后再继续。

基金赎回完整流程，包含：**持仓查询 → 账户确认 → 赎回初始化 → 赎回方式选择 → 份额输入 → 手续费计算 → 预估到账 → 提交赎回 → 结果查询**。

所有受保护接口均通过 `aijijin` CLI 调用。

---

## 基金锁定约束（强制执行）

**本 Skill 在处理赎回请求时，必须严格遵守以下约束，禁止违反：**

1. **基金代码锁定**：一旦确定用户要赎回的基金代码 `fundCode` 和交易账户 `transactionAccountId`，整个流程中所有 CLI 调用（holding list、fund redeem-render、fund redeem）都必须使用该 `fundCode` 和 `transactionAccountId`。
2. **CLI 自行处理凭据刷新**：CLI 仅会在服务端明确返回 HTTP 401 时自动刷新凭据并重试一次。
3. **网络/5xx/结果不明确时不得自动重试**：网络超时、连接中断、5xx 或提交结果不明确时，不得自动重试赎回；先查询订单状态并向用户说明。
4. **禁止更换基金**：当 API 返回业务错误时（如份额不足、状态不允许等），**禁止** AI 自动更换为持仓中的其他基金代码或账户重试，必须将原始错误信息原样返回给用户。
5. **禁止推测替代方案**：不允许 AI 自行推测持仓中其他可赎回的基金来"曲线救国"，不允许在用户未明确指定的情况下对其他基金进行任何赎回操作。

---

## CLI 命令规范

本 Skill 使用的全部命令均为 `aijijin` CLI 命令。CLI 信封、退出码语义、Work Token 自动管理、保护接口列表见 SKILL.md §2。

读服务端字段时要再往下取一层 `data.<field>`（例如 `data.fundInfo.nav`、`data.stepRates`、`data.shareList`）。

`aijijin fund redeem` 是一次性提交命令：返回 `ok: true` 只表示提交接口成功，不等于赎回订单最终成功。提交结果不明确时，必须通过 `aijijin trade list` / `aijijin trade detail` 查询订单实际状态再向用户说明，由用户决定是否再次提交，**严禁自动重新提交赎回**。

---

### `aijijin holding list`

按份额类别查询基金持仓列表。`fund-holding-list` skill 会在 01–07 之间循环合并查询；提交赎回流程需要锁定具体份额时，可直接调用本命令。如需其他份额类别，将 `01` 替换为 `02`–`07` 之一并独立调用一次。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--share-category` | 是 | 枚举字符串 | `01` / `02` / `03` / `04` / `05` / `06` / `07` | `shareCategory` | 份额类别（详见 `fund-holding-list` skill） |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "holding_list", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

---

### `aijijin fund redeem-render`

赎回初始化（渲染）：拉取单位净值、最大/最小赎回份额、是否可钱包赎回、阶梯费率档位、钱包账户信息、持有份额列表等元数据，供前端展示与用户确认。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--fund-code` | 是 | 6 位数字字符串 | — | `fundCode` | 基金代码 |
| `--transaction-account-id` | 是 | 字符串 | — | `transactionAccountId` | 交易账户 ID |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "redeem_render", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

---

### `aijijin fund redeem`

提交基金赎回订单。一次性的提交命令，CLI 不会对超时、连接错误、5xx、响应异常或业务错误进行自动重试；只有 HTTP 401 会自动刷新凭据并重放一次。提交结果不明确时，必须先查询订单状态再说明，不得自动重试赎回。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--fund-code` | 是 | 6 位数字字符串 | — | `fundCode` | 基金代码 |
| `--redemption-type` | 是 | 枚举 | `0` / `1` | `redemptionType` | `0` = 银行卡，`1` = 钱包 |
| `--share-vol` | 是 | 正十进制字符串（无指数） | — | `shareVol` | 赎回份额 |
| `--transaction-account-id` | 是 | 字符串 | — | `transActionAccountId`（注意：服务端字段名驼峰首字母大写为 `trans**A**ctionAccountId`，CLI 自动映射） | 交易账户 ID |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "redeem", "request": {...}}` |

**特别注意**：

- `--redemption-type` 是枚举：仅 `0`（银行卡）或 `1`（钱包）；其他值会触发退出码 `2`。
- `--share-vol` 必须是正十进制数字字符串（不得带指数符号如 `1e3`），保留两位以内小数。
- `--transaction-account-id` 在 CLI 命名上是 snake-case，但服务端实际字段名为 `transActionAccountId`（中间大写 A）；CLI 自动完成映射，skill 不得手工拼接 `transActionAccountId`。

成功输出：`{"ok": true, "data": <server response>}`，exit 0。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

---

### 辅助命令（可选）

提交结果不明确时，应使用交易记录查询命令复核订单实际状态：

```bash
aijijin trade list --offset 1 --limit 200 --start-date "$startDate" --end-date "$endDate" --business-code all --product-type all --no-query-processing
aijijin trade detail --order-id "$appSheetSerialNo"
```

---

## Step 1: 持仓查询

### CLI 命令调用

```bash
aijijin holding list --share-category 01
```

### 找到目标基金

- **`combineFlag = 0`**（单账户）：`transAccIdList` 即为唯一交易账户
- **`combineFlag = 1`**（多账户）：`transAccIdList` 包含多个账户ID

从 `fundPositonDetailList` 获取每个账户的：
- `transactionAccountId`：交易账户ID
- `bankName`：银行名称
- `bankAccount`：银行卡号（脱敏）
- `availableVol`：可用份额

---

## Step 2: 账户确认

展示账户选项供用户选择：

```
请选择赎回账户：
1. xx银行(尾号1212) — 可用份额 1,221.22 份
2. yy银行(尾号2233) — 可用份额 32.73 份
```

**禁止用序号如"账户1"代替银行名称展示，必须从字段读取后拼接！**

---

## Step 3: 赎回初始化

### CLI 命令调用

```bash
aijijin fund redeem-render \
  --fund-code "$fundCode" \
  --transaction-account-id "$transactionAccountId"
```

**参数说明**：
- `fundCode`：基金代码（对应服务端 `fundCode`）
- `transactionAccountId`：交易账号（CLI 内部映射到服务端 `transactionAccountId`）

### 关键响应字段

| 字段 | 说明 |
|------|------|
| data.fundInfo.nav | 单位净值 |
| data.fundInfo.maxRedemptionVol | 最大赎回份额 |
| data.fundInfo.minRedemptionVol | 最小赎回份额 |
| data.fundInfo.canRedeemToWallet | 是否可钱包赎回（1=可） |
| data.stepRates | 费率档位列表 |
| data.walletInfo | 钱包账户信息 |
| data.shareList | 持有份额列表 |

### 费率档位结构

| 字段 | 说明 |
|------|------|
| lwLimit | 持有天数下限 |
| upLimit | 持有天数上限（null 表示无上限） |
| rate | 费率（百分比） |

---

## Step 4: 赎回方式redemptionType选择

| 赎回方式 | 标识 | 说明 |
|----------|------|------|
| 钱包赎回 | `1` | **优先推荐**，到账最快，需 `canRedeemToWallet=1` |
| 银行卡赎回 | `0` | 到银行卡 |

**自动选择逻辑**：如果 `canRedeemToWallet=1` 且 `walletInfo.usable=1`，**自动选择钱包赎回**。

---

## Step 5: 份额输入与校验

1. **换算份额**：赎回金额 ÷ 净值 = 赎回份额
2. **可用份额检查**：份额 <= 可用份额
3. **最大/最小份额检查**
4. **保留余额检查**：赎回后剩余份额 >= `minAccountBalance`

---

## Step 6: 手续费与预估到账计算

### 有持有时间列表（先进先出）

```
1. 按持有天数倒序排列 shareHoldTimeList
2. 遍历持有时间列表，为每笔份额匹配对应费率
3. 手续费 = Σ(份额 × 费率 × 净值)
```

### 无持有时间列表

直接按 `stepRates` 匹配适用费率。

### 预估到账

```
预估到账 = 赎回金额 - 手续费
```

---

## Step 7: 用户确认（强制）

在调用提交赎回接口之前，必须向用户完整展示以下信息并获得明确确认：

| 字段 | 值 |
|---|---|
| 基金代码 / 名称 | <fundCode> / <fundName> |
| 赎回账户 | <银行名称(尾号XXXX)> |
| 赎回方式 | <钱包赎回 / 银行卡赎回> |
| 赎回份额 | <shareVol> 份 |
| 手续费 | <手续费> 元 |
| 预估到账金额 | <预估到账金额> 元（最终实际到账以基金公司确认为准） |
| 预估到账时间 | <预估到账时间> |

**强制要求**：

- 等待用户对本轮所有金额、份额、账户、方式、手续费信息逐一确认。
- 用户必须明确回复"确认赎回"或同义表达（例如"提交"、"继续"）才能进入提交步骤。
- 不得把"我要赎回"、"帮我赎回"等申购意图当作本轮的金额、份额、手续费确认。
- 未收到用户明确确认前，禁止调用 `aijijin fund redeem`。
- 如果用户对其中任何字段提出异议（金额不对、份额错误、账户不对等），必须先回到对应步骤修正，再重新走完整确认流程。

---

## Step 8: 提交赎回

### CLI 命令调用

```bash
aijijin fund redeem \
  --fund-code "$fundCode" \
  --redemption-type "$redemptionType" \
  --share-vol "$shareVol" \
  --transaction-account-id "$transactionAccountId"
```

**提交语义**：`redeem` 是一次性提交命令。CLI 不会对网络超时、连接错误、5xx、响应格式异常或业务错误进行自动重试；当服务端明确返回 HTTP 401 时，CLI 会自动刷新凭据并重试一次。提交结果不明确（超时、连接中断、5xx、响应异常）时，必须先通过 `aijijin trade list` / `aijijin trade detail` 查询订单实际状态，并明确告知用户，由用户决定是否再次提交，**严禁自动重新提交赎回**。

### 关键响应字段

- `status_code`: "0000" 表示成功
- `data.appSheetSerialNo`: 单据编号

### 赎回结果展示

赎回提交后直接根据 `status_code` 判断最终状态并返回结果（无需轮询）。

**成功**（status_code=0000）：展示基金信息、赎回份额、赎回方式、手续费、预估到账金额与到账时间、单据编号。

```
====================================================================================================
                              基金赎回结果
====================================================================================================
基金代码：<fundCode>
基金名称：<fundName>
赎回账户：<银行名称(尾号XXXX)>
赎回份额：<shareVol> 份
赎回方式：<钱包赎回/银行卡赎回>
手续费：<手续费> 元
预估到账金额：<预估到账金额> 元（最终实际到账以基金公司确认为准）
预估到账时间：<预估到账时间>
单据编号：<appSheetSerialNo>
交易状态：✅ 成功
====================================================================================================
```

**注意**：赎回结果中**不展示赎回金额**；预估到账金额后**必须**追加"最终实际到账以基金公司确认为准"的提示。

**失败**（status_code 非 0000）：展示失败原因与单据编号。

```
====================================================================================================
                              基金赎回结果
====================================================================================================
交易状态：❌ 失败
失败原因：<失败原因>
单据编号：<appSheetSerialNo>
====================================================================================================
```

---

## 完整交互流程

```
1. 持仓查询 → aijijin holding list --share-category 01
2. 找到基金 → 定位目标基金
3. 展示账户 → 银行(尾号XXXX)选项
4. 用户选择 → 记录 transactionAccountId
5. 赎回初始化 → aijijin fund redeem-render
6. 赎回方式 → 优先推荐钱包赎回
7. 输入份额 → 用户输入 shareVol
8. 换算金额 → 份额×净值
9. 计算手续费 → 按持有时间或档位计算
10. 计算预估到账 → 金额-手续费
11. 用户确认 → 展示所有费用/份额/预估到账信息，等待用户明确回复"确认赎回"
12. 提交赎回 → aijijin fund redeem
13. 返回结果 → 告知成功/失败；若结果不明确，先查订单状态再说明
```

---

## 注意事项

1. **持有时间列表可能为 null**（如货币基金）
2. **赎回无需单独查询结果**：赎回提交成功（status_code=0000）即表示成功，直接返回结果给用户
3. **结果不明确时必须查询订单**：提交结果不明确（超时、连接中断、5xx、响应异常）时，必须先用 `aijijin trade list` / `aijijin trade detail` 查询实际订单状态再向用户说明，**不得自动重新提交赎回**
4. **撤单规则**：赎回提交后并非不可撤销——在当前交易日 15:00 之前可正常撤单，15:00 之后进入确认流程不可撤单。向用户说明赎回状态时，应表述为"在当前交易日 15:00 之前可撤单"，禁止表述为"提交后即不可撤销"。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
