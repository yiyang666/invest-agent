---
name: purchase/api-reference
---

# 基金申购 CLI Reference（thsfund / references/purchase/api-reference）

> 公共约定（CLI 信封 / 退出码 / Work Token 管理 / JSON 合并 / `--dry-run`）见 SKILL.md §2。业务流程以 `./purchase.md` 为准。

本文件集中维护申购动作（`aijijin fund ...`）的 CLI 命令、参数、响应字段与重试规则。所有接口调用均通过 `aijijin` CLI 完成，禁止直接使用 `curl` 或手动注入 `Authorization` 头。

## 调用约定

通用语法：

```bash
aijijin <group> <command> [--<named-flag> <value>]... [--json-file <path>|--json '<...>'|--stdin] [--dry-run]
```

具体字段合并规则、`--dry-run` 输出格式、退出码与 Work Token 管理见 SKILL.md §2。

---

## CLI 命令清单

### `aijijin fund subscribe-init`

基金申购初始化。返回基金基础信息、风险等级、支付方式、阶梯费率判断依据等。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--fund-code` | 是 | 6 位数字字符串 | 形如 `000001` | `fundCode` | 基金代码 |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "subscribe_init", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。所有服务端字段都要再往下读一层 `data.<field>`。

响应字段：

| 字段 | 说明 |
|---|---|
| `data.custId` | 客户 ID |
| `data.paramOpenFundAccBean.fundCode` | 基金代码 |
| `data.paramOpenFundAccBean.fundName` | 基金名称 |
| `data.minBuy` | 最小起购金额（新申购） |
| `data.paramOpenFundAccBean.minAddBuy` | 最小追加金额 |
| `data.maxBuy` | 单笔最大购买金额 |
| `data.fundRiskLevel` | 产品风险等级（1-5） |
| `data.ov_clientriskrate` | 客户风险等级（1-5） |
| `data.ov_flag` | 风险评测状态标志 |
| `data.bankBuyDiscount` | 银行卡购买折扣 |
| `data.moneyToStockBuyDiscount` | 钱包购买折扣 |
| `data.moneytostockTzeroFlag` | 是否支持钱包支付（0=不支持，仅银行卡；1=支持，钱包+银行卡） |
| `data.fundtzeroList` | 钱包账户列表 |
| `data.bankCardSplitListResult` | 银行卡账户列表 |
| `data.subOrAddResult` | 已申购过该基金的账户（追加申购判断） |
| `data.paramOpenFundAccBean.isRollingHold` | 是否滚动持有（1=是，0=否） |
| `data.paramOpenFundAccBean.hasLockPeriod` | 是否有封闭锁定期（1=是，0=否） |
| `data.paramOpenFundAccBean.hasRedeemDate` | 是否有赎回日期（1=是，0=否） |
| `data.paramOpenFundAccBean.buyUrl` | 购买 URL（`ren`=新基金） |
| `data.paramOpenFundAccBean.appkday` | 申请日（T 日，YYYYMMDD） |
| `data.paramOpenFundAccBean.confirmDay` | 预计确认日（T+1，YYYY-MM-DD） |
| `data.accountValidateResult.validateCode` | 个人信息校验结果码 |
| `data.accountValidateResult.validateMessage` | 个人信息校验提示信息 |

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败（如基金不存在）；`5` 网络/服务器/响应异常。

---

### `aijijin fund fee-rule`

查询基金阶梯费率与持有期间费用。公开接口，不需要 Work Token。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--fund-code` | 是 | 6 位数字字符串 | 形如 `000001` | `fundCode` | 基金代码 |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "fee_rule", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

响应字段：

| 字段 | 说明 |
|---|---|
| `data.rateInfo.glf` | 管理费 |
| `data.rateInfo.tgf` | 托管费 |
| `data.rateInfo.fwf` | 销售服务费 |
| `data.rateInfo.sg.qd[].money` | 申购金额区间 |
| `data.rateInfo.sg.qd[].rate` | 原始申购费率 |
| `data.rateInfo.sh` | 赎回费率 |

退出码：`0` 成功；`2` 输入校验失败；`4` 业务失败（如基金代码无效）；`5` 网络/服务器/响应异常。

---

### `aijijin fund trade-treaty`

查询本轮交易需确认的协议。公开接口，不需要 Work Token。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--fund-code` | 是 | 6 位数字字符串 | 形如 `000001` | `fundCode` | 基金代码 |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "trade_treaty", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

响应字段：

| 字段 | 说明 |
|---|---|
| `data.tradeInitTreaty` | 本轮需展示并确认的基金协议列表 |
| `data.tradeInitTreaty[].title` | 协议名称 |
| `data.tradeInitTreaty[].jumpAction` | 协议跳转链接（按解析规则提取 `url=` 后 URL 或直接使用 `http(s)://...`） |

退出码：`0` 成功；`2` 输入校验失败；`4` 业务失败；`5` 网络/服务器/响应异常。

---

### `aijijin fund trade-record`

记录用户已阅读的协议。必须在用户回复 `已阅读` 之后、签约检查之前调用。

无命名选项。通过 `--json-file`、`--stdin` 或 `--json` 之一提供完整请求体：

```json
{
  "agreements": [
    {"title": "协议标题", "agreementUrl": "https://example.com/a.pdf"}
  ],
  "sourceType": "BUY"
}
```

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `agreements` | 是 | 数组，至少 1 项 | 已阅读协议列表 |
| `agreements[].title` | 是 | 字符串 | 协议标题 |
| `agreements[].agreementUrl` | 是 | URL（http/https） | 协议 URL，必须是 HTTP(S) URL |
| `sourceType` | 是 | 枚举 `BUY` | 固定为 `BUY` |

CLI 输入选项：

| 选项 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `--json-file` | 否 | 文件路径 | 基础请求体所在文件；与 `--stdin` 互斥；可与 `--json` 共存并被覆盖 |
| `--stdin` | 否 | 标志 | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | 仅校验，不发起网络请求；输出 `{"endpoint": "trade_record", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

退出码：`0` 成功且 `ok: true`；`2` 输入校验失败（如 `sourceType` 非 `BUY`、`agreementUrl` 非 http(s) URL、数组为空等）；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

---

### `aijijin fund check-before-trade`

申购前的签约检查。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--amount` | 是 | 正十进制字符串（无指数） | 形如 `100.00` | `applicationAmount` | 申购金额 |
| `--fund-code` | 是 | 6 位数字字符串 | 形如 `000001` | `fundCode` | 基金代码 |
| `--transaction-account-id` | 是 | 非空字符串 | — | `transactionAccountId` | 交易账户 ID |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "check_before_trade", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

响应字段：

| 字段 | 说明 |
|---|---|
| `data.signContract` | 是否需要签约（`true` / `false`） |

退出码：`0` 成功；`2` 输入校验失败（如金额非正十进制、基金代码格式错误等）；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

CLI 不会对超时、连接错误、5xx、响应异常或业务错误进行自动重试；只有 HTTP 401 会自动刷新 Work Token 并重放一次。

---

### `aijijin fund buy`

提交基金申购订单。

CLI 不会对超时、连接错误、5xx、响应异常或业务错误进行自动重试；只有 HTTP 401 会自动刷新 Work Token 并重放一次。

参数：

| 选项 | 必填 | 类型 | 取值 | 服务端字段 | 说明 |
|---|---|---|---|---|---|
| `--buy-type` | 是 | 枚举 | `0` / `1` | `buyType` | 0 = 银行卡，1 = 钱包 |
| `--fund-code` | 是 | 6 位数字字符串 | — | `fundCode` | 基金代码 |
| `--amount` | 是 | 正十进制字符串（无指数） | — | `money` | 申购金额 |
| `--transaction-account-id` | 是 | 字符串 | — | `transactionAccountId` | 交易账户 ID |
| `--agreement-record` | 否 | 非空字符串 | — | `extAttr.AgreementRecord` | 协议留痕校验。CLI 内部包装为 `extAttr: {AgreementRecord: <value>}`；不传时不发送 `extAttr` 字段。`/ai/buy` 是 JSON 编码，`extAttr` 作为嵌套对象直接透传。 |
| `--json-file` | 否 | 文件路径 | — | — | 基础请求体；与 `--stdin` 互斥；可与 `--json` 和命名选项共存并被覆盖 |
| `--stdin` | 否 | 标志 | — | — | 从 stdin 读取请求体；与 `--json-file` 互斥 |
| `--json` | 否 | 字符串 | — | — | 内联 JSON 字符串；可补充或覆盖基础请求体 |
| `--dry-run` | 否 | 标志 | — | — | 仅校验，不发起网络请求；输出 `{"endpoint": "buy", "request": {...}}` |

成功输出：UTF-8 JSON `{"ok": true, "data": <server-response>}`，exit 0。

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

响应字段：

| 字段 | 说明 |
|---|---|
| `data.appSheetSerialNo` | 订单号，用于后续订单详情查询 |

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

退出码 0 且 `ok: true` 只表示提交接口成功，不等同于最终订单成功。必须继续调用订单详情确认订单状态。

---

### `aijijin trade detail`

查询订单详情。提交申购取得 `appSheetSerialNo` 后必须调用。参数、取值与完整字段表见 [`../trade-query/cli.md`](../trade-query/cli.md)；本节只补申购结果展示时实际读哪些字段。

调用示例：

```bash
aijijin trade detail --order-id "$appSheetSerialNo"
```

申购结果展示时实际读取的字段（其它字段见共享参考）：

| 字段 | 说明 |
|---|---|
| `data.acceptTime` | 申请下单时间，格式 `yyyy-MM-dd HH:mm:ss` |
| `data.fundCode` / `data.fundName` | 基金代码 / 名称 |
| `data.exceptCfmDate` | 预计确认时间 |
| `data.applicationAmount` | 申请金额 |
| `data.feeSource` | 资金来源（0=银行卡/1=活期/2=钱包） |
| `data.bankName` / `data.bankAccount` | 银行名称 / 银行卡尾号 |
| `data.confirmFlag` | 订单状态标识，**仅内部判断用，禁止展示给用户** |
| `data.checkFlag` | 订单检查标识，**仅内部判断用，禁止展示给用户** |
| `data.failMsg.thsMessage` | 失败原因优先展示字段 |
| `data.failMsg.message` | 失败原因兜底展示字段 |


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
