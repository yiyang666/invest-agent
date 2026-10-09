---
name: trade-revoke
---

# 基金撤单（thsfund / references/trade-revoke/trade-revoke）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或版本不是 0.2.1 时停下并展示重装命令，等用户完成后再继续。

基金撤单完整流程，包含：**订单详情查询 → 可撤单判断 → 用户最终确认 → 执行撤单 → 必要时复核详情**。

所有受保护接口均通过 `aijijin` CLI 调用。

---

## 全局禁止事项

通用禁止项（自动更换订单号、跳过可撤单判断、跳过用户最终确认、失败时自动重试、Work Token 直连等）见 SKILL.md §3。撤单动作专属约束：

- **禁止发送额外字段**：服务端撤单请求只接受 `revokeAppSheetNo` 和 `transActionAccountId`；不得注入 `operator`、`revokeType` 或其他字段。

---

## 本轮状态变量

执行过程中持续维护以下状态，后续接口必须使用本轮状态值，不得混用历史值：

| 变量 | 来源 | 用途 |
|---|---|---|
| `appSheetSerialNo` | 用户指定/确认 | 详情查询与撤单必须使用该订单号 |
| `transactionAccountId` | 本轮订单详情响应的 `data.transactionAccountId` | 撤单时映射为 `transActionAccountId` |
| `cancelFlag` | 详情查询 | `0` 表示可撤单，`1` 表示不可撤单 |
| `confirmFlag` | 详情查询 | 撤单前的当前状态字段 |

---

## 订单锁定约束（强制执行）

**本 Skill 在处理撤单请求时，必须严格遵守以下约束，禁止违反：**

1. **订单号锁定**：一旦确定用户要撤销的订单号 `appSheetSerialNo`，所有后续 CLI 调用（`trade detail`、`trade revoke`）都必须使用该 `appSheetSerialNo`。
2. **交易账户锁定**：订单详情成功后，必须从本轮响应读取非空 `data.transactionAccountId`。撤单只能使用该值，禁止让用户另行输入、从历史订单复用或自行猜测；字段缺失或为空时停止，不得调用 `trade revoke`。
3. **撤单请求禁止携带非文档字段**：仅可使用 `--order-id`、`--transaction-account-id`；其余字段一律不得手工拼接或映射。
4. **禁止更换订单**：当 CLI 返回业务错误时，**禁止** AI 自动从交易记录中挑选其他订单号进行重试，必须将原始错误信息（`error.code`、`error.message`）原样返回给用户。
5. **禁止推测替代方案**：不允许 AI 自行从交易记录中推测可撤单订单来"曲线救国"，不允许在用户未明确指定的情况下对其他订单进行任何撤单操作。
6. **撤单结果不明确时必须重新查询详情**：网络超时、连接中断、5xx 或响应格式异常时，禁止自动重试撤单；先调用 `aijijin trade detail --order-id "$appSheetSerialNo"` 复核当前订单状态，并将结果告知用户。

---

## CLI 命令规范

读服务端字段时要再往下取一层 `data.<field>`（例如 `data.cancelFlag`、`data.confirmFlag`、`data.appSheetSerialNo`、`data.transactionAccountId`）。

`aijijin trade revoke` 是提交型命令：返回 `ok: true` 只表示提交成功，不等于订单最终被撤销。提交结果不明确时，必须先调用 `aijijin trade detail --order-id "$appSheetSerialNo"` 复核实际订单状态。

### `aijijin trade detail`

查询单个订单的详情，用于撤单前的可撤单判断与信息展示。参数、取值与响应字段见 [`../trade-query/cli.md`](../trade-query/cli.md)；本文只补撤单动作对响应字段的关注点。

注意：服务端请求内容只包含 `appSheetSerialNo` 一个字段。

调用示例：

```bash
aijijin trade detail --order-id "$appSheetSerialNo"
```

### `aijijin trade revoke`

提交订单撤单。一次性的提交命令，CLI 不会对超时、连接错误、5xx、响应异常或业务错误进行自动重试；只有 HTTP 401 会自动重新获取 Work Token 并重放一次。提交结果不明确时，必须先查询订单状态再说明，不得自动重新提交。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--order-id` | 是 | 字符串 | — | `revokeAppSheetNo` | 待撤单的单据编号 |
| `--transaction-account-id` | 是 | 非空字符串 | 来自本轮订单详情 | `transActionAccountId` | 详情响应中的 `transactionAccountId` |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "revoke", "request": {...}}` |

注意：服务端请求内容只包含 `revokeAppSheetNo` 和 `transActionAccountId` 两个字段。CLI 不接受也不会发送 `operator`、`revokeType` 或其他字段。

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

调用示例：

```bash
aijijin trade revoke \
  --order-id "$appSheetSerialNo" \
  --transaction-account-id "$transactionAccountId"
```

### 关键响应字段（`trade detail` 与 `trade revoke` 的 `data` 字段）

| 字段 | 说明 |
|------|------|
| data.appSheetSerialNo | 订单号 |
| data.fundCode | 基金代码 |
| data.fundName | 基金名称 |
| data.businessCode | 业务代码：022=申购, 023=赎回 |
| data.productType | 产品类型：0101=普通基金, 0102=养老基金 |
| data.cancelFlag | 撤单标志：'0'=可撤单, '1'=不可撤单 |
| data.confirmFlag | 确认状态：'0'=未确认, '1'=已撤单, '3'=确认成功, '4'=确认失败, '6'=作废 |
| data.transactionAccountId | 交易账号ID |
| data.feeSource | 资金来源：'0'=银行卡, '1'=活期, '2'=钱包 |
| data.applicationAmount | 申请金额 |
| data.walletPayAmount | 钱包支付金额 |
| data.cardPayAmount | 银行卡支付金额 |
| data.bankAccount | 银行卡账号（后4位） |
| data.bankName | 银行名称 |
| data.acceptTime | 受理时间 |
| data.exceptCfmDate | 预期确认日期 |

### 可撤单判断

- `cancelFlag = '0'`: 可撤单，向用户展示完整订单信息并请其确认后撤单。
- `cancelFlag = '1'`: 不可撤单，**禁止** 调用 `aijijin trade revoke`，向用户说明该订单状态不可撤销。

---

## 完整交互流程

```
1. 收集参数 → 用户提供订单号 appSheetSerialNo（与查询阶段确认）
2. 订单详情查询 → aijijin trade detail --order-id "$appSheetSerialNo"；保存非空 data.transactionAccountId，否则停止
3. 可撤单判断 → 检查 cancelFlag：'0'=可撤单，'1'=不可撤单
4. 订单展示 → 向用户展示订单关键信息（基金代码、名称、金额、受理时间等）
5. 最终确认 → 用户明确确认后继续；用户未确认则终止流程
6. 执行撤单 → aijijin trade revoke --order-id "$appSheetSerialNo" --transaction-account-id "$transactionAccountId"
7. 结果判定 → exit 0 且 ok:true 视为成功；其他退出码或重试请求必须先复核详情
8. 复核（如需）→ 撤单结果不明确时再次 aijijin trade detail --order-id "$appSheetSerialNo"，将真实状态告知用户
```

---

## 注意事项

1. **Work Token**：见 SKILL.md §2 第 3 条。
2. **撤单为提交型命令**：网络超时、连接中断、5xx 或响应格式异常时禁止自动重试撤单；先调用 `aijijin trade detail` 复核订单实际状态，再决定是否再次撤单。
3. **不可自动更换订单**：CLI 业务错误（退出码 `4`）时必须原样返回错误信息，禁止挑选其他订单替代。
4. **不可显示底层状态码**：面向用户只展示中文状态和可读原因。
5. **交易账户必须来自详情**：撤单命令的 `--transaction-account-id` 必须取本轮订单详情的 `data.transactionAccountId`；缺失或为空时停止。
6. **不得发送额外字段**：撤单请求只包含 `revokeAppSheetNo` 和 `transActionAccountId`；禁止传入 `operator`、`revokeType` 或其他字段。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
