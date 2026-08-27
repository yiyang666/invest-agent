---
name: overview
---

# 持仓总览（thsfund / references/overview/overview）

> 公共约定见 SKILL.md §2，全局禁止事项见 §3。

> **前置自检**：执行任何 `aijijin` 命令前，先按 SKILL.md §0 完成 SDK 自检；缺失或低于 0.2.0 时停下并展示安装命令，等用户完成后再继续。

用于生成"基金持仓 + 钱包持仓"的总览结果。所有数据拼装、合并、排序、Top5/Bottom5 计算和卡号打码都已由 SDK 在 `aijijin holding overview` 内部完成，模型只负责按本文档的展示格式渲染 `data.*` 字段。

---

## 1. 执行要点

`aijijin holding overview` 的成功输出顶层是 CLI 信封，**真正的聚合数据要再往下取一层 `data.*`**：

| 路径 | 内容 |
|---|---|
| `data.wallet` | 钱包首页（`ok` 标志 + `data` 内容 + `bank_total` 累加） |
| `data.fundApi.okCategories` / `data.fundApi.failedCategories` | 7 个 shareCategory 的成功 / 失败列表 |
| `data.fundSummary` | 基金总金额 / 持有收益 / 日收益 / 基金个数 |
| `data.funds` | 按 `totalAmount` 降序的最终展示记录 |
| `data.topProfitFunds` / `data.topLossFunds` | 最赚钱 / 最亏损榜单（最多 5 条） |

`overview` 命令一次内部串行调用 8 个接口，任一业务失败都会让 CLI 退出码非 0（退出码语义详见 SKILL.md §2 第 1 条）。

### 部分失败处理

- `data.wallet.ok` 为 `false` 或缺 `data`：单独提示用户"钱包数据获取失败"，但仍展示基金部分。
- `data.fundApi.failedCategories` 非空：继续展示已成功的类别，并在最终输出追加一行 `基金数据获取失败（XX）`，XX 是逗号拼接的失败 shareCategory。
- 7 个 shareCategory 全部失败（`data.fundApi.okCategories` 为空）：仅展示钱包持仓并提示"基金数据获取失败"。
- 钱包 + 全部 shareCategory 都失败（CLI 退出码 `3` 或 `5`）：返回错误提示，请用户检查 `aijijin-sdk` 配置。

### 展示硬约束

- 钱包持仓不要展示本文档未要求的收益率字段，例如七日年化收益率、万份收益率等。
- 银行卡号必须按打码后的 `<maskedBankAccount>` 展示（SDK 已处理）。
- 严格按本文档第 7 节的"展示格式"输出对应内容；不要自行改成其他结构，也不要遗漏要求展示的字段。
- 输出使用 Markdown 表格，不使用空格对齐长表格。

### 执行前检查清单

- 已重新调用 `aijijin holding overview` 发起实时请求，而不是复用旧的 JSON 文件或缓存结果。
- 已读取 `data.fundApi.okCategories` 与 `data.fundApi.failedCategories` 判断各 shareCategory 状态。
- 已读取 `data.wallet.ok` 判断钱包数据是否可用。
- 基金持仓按 `data.funds` 顺序展示（SDK 已按 `totalAmount` 从大到小排序）。
- "最赚钱的5只基金"只展示 `data.topProfitFunds`；为空时省略整个榜单。
- "最亏损的5只基金"只展示 `data.topLossFunds`；为空时省略整个榜单。

---

## 2. 执行流程

```bash
aijijin holding overview > overview.json
```

一次调用即拿到所有需要的数据。响应格式：

```json
{
  "ok": true,
  "data": {
    "wallet": {
      "ok": true,
      "data": { "custId": "...", "bankAccountShareList": [...] },
      "bank_total": "300.75"
    },
    "fundApi": {
      "okCategories": ["01", "02", ...],
      "failedCategories": ["03"]
    },
    "fundSummary": {
      "fundCount": "5",
      "totalAmount": "12345.67",
      "holdIncome": "234.56",
      "newestIncome": "12.34"
    },
    "funds": [
      {
        "fundCode": "000001",
        "fundName": "...",
        "totalAmount": "5000.00",
        "holdIncome": "100.00",
        "holdIncomeRate": "2.00%",
        "holdVol": "10000.00",
        "newestIncome": "5.00",
        "duplicateRecordCount": 1,
        "displayFieldsSource": "api_record"
      }
    ],
    "topProfitFunds": [ ...最多 5 条... ],
    "topLossFunds": [ ...最多 5 条... ]
  }
}
```

退出码：`0` 成功（即使部分 shareCategory 失败也已落入 `fundApi.failedCategories`）；`3` 凭据/认证失败；`5` 网络/服务器异常。退出码非 `0` 时顶层 `ok` 为 `false`，CLI envelope 形如 `{"ok": false, "error": {...}}`，按本文档第 8 节处理。

---

## 3. 凭据管理说明

Work Token 见 SKILL.md §2 第 3 条；本 skill 不需要也无法访问 Work Token（如 `aijijin auth work-token` 命令仅供查看，skill 不调用）。

---

## 4. CLI 命令规范

### 4.1 `aijijin holding overview`

聚合查询钱包首页 + 7 个 shareCategory 持仓 + 累加/排序/榜单计算。SDK 内部一次性完成所有调用，对外返回结构化 JSON。

CLI 自动管理 Work Token；socket 之外不需要任何凭据。

成功输出：

```json
{"ok": true, "data": <aggregated overview payload>}
```

> 顶层可附 `update` 字段（受保护接口 + 未 dismiss 时由 CLI 透传），详见 SKILL.md §2 第 8 条。

退出码：`0` 成功；`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

调用示例：

```bash
aijijin holding overview > overview.json
```

即使某个 shareCategory 在内部失败，CLI 仍然退出码 `0`，把失败信息落到 `data.fundApi.failedCategories` 字段。

### 4.2 JSON 编码契约

CLI 默认 JSON 输出在 stdout 边界统一使用 UTF-8 字节，调用方不需要设置 `PYTHONIOENCODING` 或手工转换文件编码。

---

## 5. 响应字段说明

### 5.1 钱包首页字段（`data.wallet.data`）

| 字段 | 说明 |
|------|------|
| `custId` | 客户号 |
| `yesterdayIncome` | 昨日日期（如 `07-21`，表示 7 月 21 日） |
| `profits` | 昨日收益金额，单位：元 |
| `holdProfits` | 持有收益，累计 |
| `fundCode` | 基金代码 |
| `fundName` | 基金名称 |
| `avaiableVol` | 可用份额 |
| `freezeMoney` | 冻结份额 |
| `convertFreezeMoney` | 转换冻结份额 |
| `usableCashOutVol` | 可用可取份额 |
| `usableUnCashOutVol` | 可用不可取份额 |
| `buyStatus` | 转入状态（`1`=可转入，`0`=不可转入） |

`data.wallet.bank_total` 是 SDK 累加 `bankAccountShareList[].totalShare` 后的字符串（两位小数）。

### 5.2 银行卡份额列表字段（`bankAccountShareList`）

| 字段 | 说明 |
|------|------|
| `bankName` | 银行名称 |
| `bankAccount` | 银行卡号，需打码展示，保留前 4 位 + 后 4 位 |
| `totalShare` | 该卡下钱包总份额 |

### 5.3 基金持仓字段（`data.funds[]`，每条已格式化）

| 字段 | 说明 |
|------|------|
| `fundCode` | 基金代码 |
| `fundName` | 基金名称 |
| `totalAmount` | 总金额/持有成本，单位：元；基金持仓顶部"总金额"取 `data.fundSummary.totalAmount` 之和，这里是单条 |
| `holdVol` | 持有份额；已格式化为两位小数字符串，或 "待确认" |
| `newestIncome` | 日收益，单位：元 |
| `holdIncome` | 持有收益，单位：元 |
| `holdIncomeRate` | 持有收益率，已格式化为 `XX.XX%` 或 `-` |
| `duplicateRecordCount` | SDK 合并时遇到同 `fundCode` 多条记录时的数量 |
| `displayFieldsSource` | `api_combined_record` / `api_record`，标记明细字段是来自合并记录还是单条 |

### 5.4 基金汇总（`data.fundSummary`）

| 字段 | 说明 |
|------|------|
| `fundCount` | 基金个数 |
| `totalAmount` | 所有最终展示记录的 `totalAmount` 之和 |
| `holdIncome` | 所有最终展示记录的 `holdIncome` 之和 |
| `newestIncome` | 所有最终展示记录的 `newestIncome` 之和 |

### 5.5 源状态（`data.fundApi`）

| 字段 | 说明 |
|------|------|
| `okCategories` | 成功的 shareCategory 列表 |
| `failedCategories` | 失败的 shareCategory 列表 |

---

## 6. 数据处理规则

以下规则已由 SDK 的 `aijijin_sdk.holdings.aggregate` 模块实现并自动应用到 `data.*` 字段。模型**不要**在回复里重新实现这些规则，只需读取 SDK 输出。

### 6.1 基金最终展示记录选择（SDK 已做）

1. 从 7 个 shareCategory 响应中取出 `fundPositonCombinedList`，合并到候选数组。
2. 同一 `fundCode` 出现多条记录时，优先使用接口提供的合并基金记录（`combineFlag` ∈ {`1`,`Y`,`true`,`True`}）。
3. 最终展示记录一旦确定，明细中的总金额、持有收益、持有收益率、持有份额、日收益直接取该记录的对应字段。
4. 如果接口没有提供可识别的合并基金记录，才按 `fundCode` 兜底合并（保留 `totalAmount` 最大的记录）。

### 6.2 基金汇总计算（SDK 已做）

- `data.fundSummary.totalAmount` = Σ 最终展示记录的 `totalAmount`
- `data.fundSummary.holdIncome` = Σ 最终展示记录的 `holdIncome`
- `data.fundSummary.newestIncome` = Σ 最终展示记录的 `newestIncome`

### 6.3 持有收益率

- `data.funds[i].holdIncomeRate` 直接来自最终展示记录的 `holdIncomeRate`。
- 模型不要用 `holdIncome / totalAmount` 覆盖 SDK 已格式化的百分比。
- 缺失 `holdIncomeRate` 时 SDK 输出 `-`。

### 6.4 排序、打码和特殊状态

1. `data.funds` 已按 `totalAmount` 从大到小排序。
2. `data.topProfitFunds` 只包含 `holdIncome>0` 的基金，按 `holdIncome` 从高到低选最多 5 条；空时省略榜单。
3. `data.topLossFunds` 只包含 `holdIncome<0` 的基金，按 `holdIncome` 从低到高选最多 5 条；空时省略榜单。
4. 银行卡号打码展示，保留前 4 位 + 后 4 位，格式示例：`1234****5678`；接口只返回后 4 位时展示 `****<末4位>`。
5. `data.funds[i].holdVol` 已是 "待确认"（当原始 `holdVol=0` 且 `totalAmount>0`）或两位小数字符串。
6. 钱包的 `walletFinancialList`（理财通列表）不展示给用户。

---

## 7. 展示格式

按以下结构输出。字段为空时保留结构，并用接口实际可用值或合理空值填充；不要额外展示钱包收益率字段。

优先使用 Markdown 表格展示明细，避免中文基金名称过长时导致空格对齐错乱。数值列保持两位小数；基金持仓按 `data.funds` 顺序排列（SDK 已按 `totalAmount` 从大到小排列）。

输出时直接渲染 Markdown，不要把整段结果包进代码块；这样表格会被客户端正确渲染，用户更容易阅读。

```markdown
# 我的总持仓（基金 + 钱包）

## 钱包持仓

| 基金代码 | 基金名称 | 总金额 | 昨日收益 | 累计收益 |
|---|---|---:|---:|---:|
| <wallet.data.fundCode> | <wallet.data.fundName> | <wallet.data.avaiableVol> | <wallet.data.profits> | <wallet.data.holdProfits> |

- 昨日日期: <wallet.data.yesterdayIncome>
- 冻结份额: <wallet.data.freezeMoney>
- 转换冻结份额: <wallet.data.convertFreezeMoney>
- 可用可取份额: <wallet.data.usableCashOutVol>
- 可用不可取份额: <wallet.data.usableUnCashOutVol>
- 转入状态: <wallet.data.buyStatus>（1=可转入，0=不可转入）

### 钱包持仓明细

| 银行名称 | 卡号 | 持有金额 |
|---|---|---:|
| <bankName> | <maskedBankAccount> | <totalShare> |

## 基金持仓

- 总金额: <fundSummary.totalAmount> 元
- 持有收益: <fundSummary.holdIncome> 元
- 日收益: <fundSummary.newestIncome> 元

| 基金代码 | 基金名称 | 总金额 | 持有收益 | 持有收益率 | 持有份额 | 日收益 |
|---|---|---:|---:|---:|---:|---:|
| <funds[i].fundCode> | <funds[i].fundName> | <funds[i].totalAmount> | <funds[i].holdIncome> | <funds[i].holdIncomeRate> | <funds[i].holdVol> | <funds[i].newestIncome> |

### 最赚钱的5只基金

| 基金代码 | 基金名称 | 持有收益 | 持有收益率 |
|---|---|---:|---:|
| <topProfitFunds[i].fundCode> | <topProfitFunds[i].fundName> | <topProfitFunds[i].holdIncome> | <topProfitFunds[i].holdIncomeRate> |

### 最亏损的5只基金

| 基金代码 | 基金名称 | 持有收益 | 持有收益率 |
|---|---|---:|---:|
| <topLossFunds[i].fundCode> | <topLossFunds[i].fundName> | <topLossFunds[i].holdIncome> | <topLossFunds[i].holdIncomeRate> |
```

### 展示细节

- 不要使用依赖空格宽度对齐的长文本表格；中文、英文和数字混排时容易错位。
- 银行卡号按打码后的 `<maskedBankAccount>` 展示。
- 基金名称不要为了对齐而截断；Markdown 表格允许长名称自然换行。
- "最赚钱" / "最亏损" 榜单在 `topProfitFunds` / `topLossFunds` 为空时整段省略。

---

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| CLI 退出码 `3`（凭据/认证失败，如 RT 过期） | 返回错误提示，请用户执行 `aijijin_sdk.set_refresh_token(...)` 后重试 |
| CLI 退出码 `5`（网络/服务器异常） | 返回错误提示，请用户检查网络或稍后重试 |
| CLI 退出码 `0` 但 `data.wallet.ok` 为 `false` | 钱包数据获取失败：单独展示基金部分并提示用户 |
| CLI 退出码 `0` 且 `data.wallet.ok` 为 `true`，但 `data.fundApi.failedCategories` 非空 | 展示钱包 + 已成功的基金类别，追加一行 `基金数据获取失败（XX）`，XX 为逗号拼接的失败 shareCategory |
| `data.fundApi.okCategories` 为空（即 7 个 shareCategory 全部失败） | 仅展示钱包持仓，并提示 `基金数据获取失败` |
| 钱包和 7 个 shareCategory 全部失败 | 返回错误提示，请用户检查 `aijijin-sdk` 配置是否正确 |

CLI 退出码对照：`2` 输入校验失败；`3` 凭据/认证失败；`4` 业务失败；`5` 网络/服务器/响应异常。

即使部分源失败，也要保留已成功获取的数据展示，不要因为部分失败而丢弃全部结果。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
