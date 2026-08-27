---
name: trade-query/cli
---

# 交易 CLI 共享参考（thsfund / references/trade-query/cli）

> 公共约定见 SKILL.md §2。本文件集中维护 `aijijin trade list` / `aijijin trade detail` 的参数、响应字段与重试规则；`trade-query.md` / `../trade-revoke/trade-revoke.md` / `../purchase/api-reference.md` 等文档共用本节定义，避免字段名错位。

---

## `aijijin trade list`

查询订单列表（支持游标分页）。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--offset` | 是 | 整数 | 1..2147483647 | `offset` | 页码；第一页传 1，第二页起需提供游标字段 |
| `--limit` | 否 | 整数 | 1..200（默认 20） | `limit` | 单页数量 |
| `--start-date` | 是 | 8 位日期字符串 | `YYYYMMDD` | `startDate` | 起始日期（包含） |
| `--end-date` | 是 | 8 位日期字符串 | `YYYYMMDD` | `endDate` | 结束日期（包含） |
| `--business-code` | 否 | 枚举 | `buy` / `sell` / `aip` / `change` / `dividend` / `other` / `all`（默认 `all`） | `opBusinessCode` | 业务类型过滤 |
| `--product-type` | 否 | 枚举 | `demand` / `time` / `normal_fund` / `advanced_financing` / `pension` / `all`（默认 `all`） | `opProductType` | 产品类型过滤 |
| `--query-processing` / `--no-query-processing` | 是（二选一） | 布尔 | `True` / `False` | `queryProcessing` | 是否查询处理中订单 |
| `--last-accept-time` | 条件必填 | 字符串 | 非空 | `lastAcceptTime` | 上一页最后一条订单的接受时间（取自 `acceptTime`）；`--offset > 1` 时必填 |
| `--last-order-id` | 条件必填 | 字符串 | 非空 | `lastAppSheetSerialNo` | 上一页最后一条订单的 ID（取自 `appSheetSerialNo`）；`--offset > 1` 时必填 |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "order_list", "request": {...}}` |

校验规则：

- `--start-date` 必须不晚于 `--end-date`，且都必须是真实存在的日期。
- 第一页（`--offset = 1`）禁止提供 `--last-accept-time` 和 `--last-order-id`。
- 第二页及后续（`--offset > 1`）必须同时提供 `--last-accept-time` 和 `--last-order-id`。
- 未知字段一律拒绝。

成功响应字段布局：`{"ok": true, "data": {"data": [<订单列表>]}}` — 订单数组在 `data.data[]`；常见游标字段 `data.lastAcceptTime` 与 `data.lastAppSheetSerialNo` 用于构造下一页请求。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

退出码语义同 SKILL.md §2 第 1 条。

---

## `aijijin trade detail`

按订单号查询单笔交易详情。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--order-id` | 是 | 字符串 | 非空 | `appSheetSerialNo` | 订单号（交易申请单号），取自列表结果的 `appSheetSerialNo` |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "order_detail", "request": {...}}` |

校验规则：`--order-id` 必须为非空字符串；未知字段一律拒绝。

成功响应字段布局：`{"ok": true, "data": <订单详情对象>}`。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

---

## 通用响应字段（`data.data[]` 订单 / `data` 详情共享）

> 以下字段在 `trade list` 和 `trade detail` 之间大部分重叠；具体差异由各动作文档（`trade-query.md`、`../trade-revoke/trade-revoke.md`、`../purchase/api-reference.md`）补充。

| 字段 | 说明 |
|---|---|
| `appSheetSerialNo` | 交易申请单号；详情/撤单的主键 |
| `fundCode` / `fundName` | 基金代码 / 名称 |
| `businessCode` / `businessName` | 业务代码 / 业务名称 |
| `subBusinessType` / `subBusinessTypeMsg` | 二级交易类型及描述 |
| `firstBusinessType` / `firstBusinessTypeMsg` | 一级交易类型及描述 |
| `applicationAmount` | 申请金额 |
| `walletPayAmount` | 钱包实际支付金额；存在时优先作为买入 `amount` |
| `cardPayAmount` | 银行卡实际支付金额；钱包支付金额为 0 时使用 |
| `totalFee` / `failFee` | 交易申请 / 失败金额 |
| `totalFundUnits` / `failFundUnits` | 交易申请 / 失败份额 |
| `acceptTime` | 交易发起时间，格式 `yyyy-MM-dd HH:mm:ss` |
| `confirmFlag` | 交易状态：`0`待确认 / `1`已撤单 / `2`部分确认 / `3`确认成功 / `4`确认失败 / `5`认购接受 / `6`订单作废 |
| `payFlag` | 支付状态：`0`成功 / `1`失败 / `2`超时 |
| `endFlag` | 是否最终状态：`0`进行中 / `1`最终 |
| `processStatus` / `finalStatus` | 处理中 / 最终状态细分 |
| `cancelFlag` | 撤单标志（详情）：`0`可撤 / `1`不可撤 |
| `transactionAccountId` | 交易账号 ID |
| `feeSource` | 资金来源：`0`银行卡 / `1`活期 / `2`钱包 |
| `bankName` / `bankAccount` | 银行名称 / 银行卡尾号 |
| `exceptCfmDate` / `exceptConfirmTime` | 预计确认时间 |
| `transactionCfmDate` / `vcTransactioncfmdate` | 交易确认日 |
| `toAccountTime` | 预计回款时间 |
| `ndConfirmedamount` / `ndConfirmedvol` | 确认金额 / 确认份额 |
| `vcCanceltime` | 撤单时间 |
| `failMsg.thsMessage` | 失败原因（优先展示字段） |
| `failMsg.message` | 失败原因（兜底展示字段） |
| `failMsg.code` | 失败错误码（**禁止展示给用户**） |