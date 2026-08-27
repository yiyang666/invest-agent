---
name: purchase/order-status
---

# 申购订单状态判定（thsfund / references/purchase/order-status）

> 公共约定见 SKILL.md §2。业务流程以 `./purchase.md` 为准。

订单状态必须组合使用 `/order/v2/ai/detail` 返回的 `data.confirmFlag` 和 `data.checkFlag` 判断。接口层 `status_code` 只表示详情接口调用成功，不得作为订单成功或失败的判断依据。

## 状态判断优先级

严格按下列顺序命中第一条符合的规则并停止判断：

| 优先级 | 条件 | 订单状态 | 展示要求 |
|---:|---|---|---|
| 1 | `confirmFlag=3` | 订单成功 | 展示成功结果 |
| 2 | `checkFlag=0` 且 `confirmFlag=0` | 订单成功 | 展示成功结果 |
| 3 | `confirmFlag=0` 且 `checkFlag!=0` | 订单处理中 | 提示用户稍后重新查询结果 |
| 4 | `confirmFlag=6` 且 `checkFlag!=0` | 订单失败 | 只展示失败原因；优先 `failMsg.thsMessage`，为空时用 `failMsg.message` |
| 5 | `confirmFlag=4` | 基金公司确认失败 | 展示基金公司确认失败 |
| 6 | `confirmFlag=2` | 基金公司部分确认成功，部分失败 | 展示部分确认结果 |
| 7 | `confirmFlag=1` | 已撤单 | 展示已撤单结果 |

未命中上述规则时，不得推断订单成功；提示：`暂时无法确认订单状态，请稍后重新查询`。

## 失败原因

当 `confirmFlag=6` 且 `checkFlag!=0` 时：

1. 优先使用 `failMsg.thsMessage`。
2. `failMsg.thsMessage` 为空时使用 `failMsg.message`。
3. 不展示 `failMsg.code` 或任何错误码。

## 展示约束

面向用户展示订单详情时：

- 不展示 `confirmFlag`、`checkFlag` 的字段名、字段值或“状态标识”行。
- 不展示 `failMsg.code` 或任何错误码。
- 只展示中文状态描述和用户可理解的失败原因。
- `acceptTime` 为空时展示 `-`。
- 结果展示模板见 `references/purchase/display-templates.md`。


---
---

## 需 init 错误路由（→ SKILL.md §0.5）

`aijijin` CLI 调用失败后，按 `error.code` 精确路由：

- `CredentialsNotFoundError`（首次安装）/ `DeviceNotRegisteredError` (1407) / `InitError` (1301/1302/1303) → **§0.5.2**（需扫码的完整 init）
- `DeviceAuthorizationError` (1406) → **§0.5.3**（仅 App 授权，无扫码）

模型在 references 主体流程内遇到上述错误时，立即跳出到对应小节；不要在本文档内自行重试或绕过。各错误的触发条件与用户提示语统一见 SKILL.md §0.5 / §0.5。
