---
name: thsfund
description: aijijin CLI 基金业务统一入口（ai基金/爱基金亦触发）。覆盖 5 类意图：(1) 持仓/钱包总览（"我的持仓/总持仓/总资产/账户里有哪些基金/今天赚了多少钱"），(2) 申购/买入（"买入/申购/购买基金/加仓/加投/订阅/subscribe"），(3) 赎回（"赎回/卖出/清仓"），(4) 交易记录查询（"交易记录/买卖记录/最近买了什么/查一下我的交易/订单查询"），(5) 撤单（"撤单/撤销订单/撤销这笔/revoke/cancel order"）。命中以上任意场景均触发本 skill；其他场景（费率查询、基金信息查询、NAV 估值、持仓之外的钱包查询）走其他 skill。本 skill 严格通过 `aijijin` CLI 执行，不调用任何 Work Token 接口、不使用 curl 直连保护接口、不通过 `python -c` 调用 SDK token 脚本。
---

# thsfund Skill

`thsfund` 是 `aijijin` CLI 基金业务的统一入口，覆盖 5 类意图：持仓总览、申购、赎回、交易记录查询、撤单。命中任一意图后，按下表进入对应 `references/` 子文件，按动作流程完整执行。

## 本项目覆盖条款

在Personal AI Investment OS仓库中，`AGENTS.md`、`config/investment_policy.yaml`和`docs/integrations/thsfund-review.md`优先于本Skill内的通用安装/升级说明：

- 只使用项目`.conda-env`和`.agents/skills/thsfund/`，不得写入全局Skill目录；
- 禁止自动下载、升级、覆盖或删除Skill/SDK，也不得执行下文面向上游通用环境的`~/.claude/skills/`升级步骤；
- 当前规则查询优先交给相邻`verify-fund-trading-rules`，禁止通过申购、赎回或撤单试单取数；
- 当前仅开放逐笔精确确认的受控申购；赎回、撤单、自动提交与异常自动重试关闭；
- 原始受保护响应、账户、支付方式和凭证不得落盘或进入Git。

## §0 前置条件：SDK 自检与安装引导

任何 `aijijin` 命令执行前都必须先完成本节自检；缺失或版本不足时**模型可在用户同意后替用户执行安装**（见 §0.3），未取得同意前不要执行 pip install。

### §0.1 最低版本 & 自检命令

合到一步完成：

- **最低版本**：`aijijin-sdk >= 0.2.0`。`0.1.x` 不包含 CLI 入口（`aijijin` 控制台脚本自 0.2.0 引入），调用必失败。
- **自检命令**：

  ```bash
  pip show aijijin-sdk
  ```

  解析 `Version:` 行；命令非 0 退出或 `Version` 缺失 ⇒ SDK 未安装；`Version` 解析后须 `>= 0.2.0` 才满足最低版本要求。

> SDK 装好后，`aijijin` CLI 入口随 pip 安装自动写入 Python 的 Scripts 目录；如遇 `pip show` 通过而 `aijijin` 调用报错「command not found」，停下向用户报告「Scripts 目录未在 PATH，请重新安装或将 Scripts 加入 PATH」。

### §0.2 决策矩阵

| `pip show` 结果 | 动作 |
|---|---|
| 未安装 / 解析失败 | 展示安装命令（见 §0.3），用 AskUserQuestion 询问用户是否同意安装；同意后执行 pip install 并重新自检；不同意则停下 |
| `Version < 0.2.0` | 展示升级命令（见 §0.3，`--force-reinstall`），用 AskUserQuestion 询问用户是否同意升级；同意后执行并重新自检；不同意则停下 |
| `Version >= 0.2.0` | 通过，进入 §1 路由 |

### §0.3 安装命令模板

skill 自带 `vendor/aijijin_sdk-0.2.0-py3-none-any.whl`，使用本地绝对路径安装，无需联网：

```bash
pip install "<skill_dir>\vendor\aijijin_sdk-0.2.0-py3-none-any.whl"
```

`<skill_dir>` 必须替换为本 skill 的实际目录。模型在向用户展示时把绝对路径写完整，不要让用户猜测。

- 升级场景建议加 `--force-reinstall`，避免被已存在的 0.1.x 残留干扰；命令示例：`pip install --force-reinstall "<skill_dir>\vendor\aijijin_sdk-0.2.0-py3-none-any.whl"`。

### §0.4 references/*.md 的引用约定

每个会调用 `aijijin` 的 `references/**/*.md`（共 8 个：入口文件 `overview/overview.md` / `purchase/purchase.md` / `redeem/redeem.md` / `trade-query/trade-query.md` / `trade-revoke/trade-revoke.md`，以及 `purchase/` 下 3 个子文件 `api-reference.md` / `display-templates.md` / `order-status.md`）首部都以一行 `前置自检` 指针回链到本节；模型在执行该文件任何 CLI 命令前必须再次确认 §0 已通过。

每个 `references/**/*.md` 的错误处理表都必须包含以下四行，按 `error.code` **精确路由**到 §0.5 对应小节：

| CLI 错误 | 触发条件 | 动作 |
|---|---|---|
| `CredentialsNotFoundError` | 凭证文件不存在（首次安装） | → §0.5.2 |
| `DeviceNotRegisteredError` (1407) | 设备尚未在 App 注册（无任何绑定记录） | → §0.5.2 |
| `DeviceAuthorizationError` (1406) | 设备已注册但用户在 App 未完成授权 | → §0.5.3 |
| `InitError` (1301/1302/1303) | initToken 不可用 | → §0.5.2 |

## §0.5 init 引导（设备注册 + 授权）

> **路径守卫**：仅支持**同花顺 App 扫码**；禁止引导用户"复制链接"或"在浏览器打开"

各 `references/**/*.md` 在 CLI 调用失败后按 `error.code` 进入 §0.5.2（首次注册）或 §0.5.3（仅授权）；两条路径完成 init 后由 §0.5.3 接管等待用户确认授权。

### §0.5.1 打开 QR 页

**GUI 场景（默认）**：模型在 §0.3 入口**主动**调用平台命令打开 QR 页，并引导用户操作。

**平台打开命令**：

| 平台 | GUI 检测 | 命令 |
|---|---|---|
| Windows | 视为 GUI | `start "" "<skill_dir>\assets\init.html"` |
| macOS | 视为 GUI | `open "<skill_dir>/assets/init.html"` |
| Linux | `$DISPLAY` 非空 | `xdg-open "<skill_dir>/assets/init.html"` |
| Linux headless / WSL 无 `$DISPLAY` / 无 `assets/` | — | 跳过打开，仅文字指引 |

**统一话术（GUI，已主动打开）**：

> 已为你打开同花顺基金Skill 设备注册页面（默认浏览器）。请打开同花顺 App 扫码进入 → 点击「复制提示词」按钮 → 完成风险评测答题（如系统提示需要）→ 把 initToken 粘贴到聊天里。

**统一话术（无 GUI / 无资产，仅 1407 / 1301-1303 路径）**：

> 请打开同花顺 App，扫码进入基金Skill页面（路径：同花顺理财 → 基金Skill），点击「复制提示词」按钮，完成风险评测答题（如系统提示需要），把 initToken 粘贴到聊天里。

### §0.5.2 设备注册

收到 initToken 后执行：

```bash
aijijin auth init "<initToken>"
```

按下表路由：

| 退出码 | `confirmStatus` | 动作 |
|---|---|---|
| 0 | `1` | 设备已绑定，直接执行本次意图在 `references/*` 中对应的 CLI 命令 |
| 0 | `0` | 进入 §0.5.3，等用户回 App 点「确认授权」后告知「继续」 |
| 2 | — | initToken 格式错误 → 提示「initToken 长度/字符异常，请检查粘贴是否完整后重新粘贴」 |
| 3 | — | initToken 凭据/认证失败 → 提示「initToken 已过期或被使用过，请重新扫码获取新 initToken」 |
| 4 | — | 业务失败 → 展示 `error.message`，停下 |
| 5 | — | 网络/服务端异常 → 展示 `error.message`，停下 |

`confirmStatus=0` 时模型保持在 §0.5.3 等待用户主动告知「继续」/「已完成」/「已授权」等同义表达

### §0.5.3 设备授权

设备**已注册**，不需要再次扫码。：

> 设备已注册，但同花顺 App 未完成最终授权。请打开同花顺 App → 基金Skill → 设备管理 → 点击「确认授权」按钮，完成后告诉我「继续」。

收到 "继续" 后进入后续步骤

## §1 意图路由表

模型按 description 命中意图后，按下表读取对应 references/ 子文件（按原 5 skill 意图分目录：`overview/` / `purchase/` / `redeem/` / `trade-query/` / `trade-revoke/`）：

| 用户意图（关键词） | 进入 references |
|---|---|
| 持仓 / 总持仓 / 总资产 / 我的基金 / 钱包 + 持仓 / 今天赚了多少 | `references/overview/overview.md` |
| 买入 / 申购 / 购买 / 加仓 / 加投 / subscribe | `references/purchase/purchase.md` |
| 赎回 / 卖出 / 清仓 / 把基金赎回来 | `references/redeem/redeem.md` |
| 交易记录 / 买卖记录 / 最近买了什么 / 查一下我的交易 / 订单查询 / 历史订单 | `references/trade-query/trade-query.md` |
| 撤单 / 撤销订单 / 撤销这笔 / revoke / cancel order | `references/trade-revoke/trade-revoke.md` |
| 安装 / 重装 / 更新 / 部署 / 升级爱基金 skill | `references/install/install.md` |

子文件首部统一以一行 `> 公共约定见 SKILL.md §2，全局禁止事项见 §3。` 引用本文，不重复整段。

## §2 公共约定

所有 `references/**/*.md` 共用以下契约：

1. **CLI 输出与退出码约定**：`aijijin` CLI 对所有命令统一输出 UTF-8 JSON，即使 Windows 本地代码页不是 UTF-8 也能直接 `json.loads` 解析。
   - 成功（退出码 0）：`{"ok": true, "data": <server-response>}`
   - 失败（退出码非 0）：`{"ok": false, "error": {"code": "...", "message": "...", "field": "..."}}`
   - 退出码语义：`0` 成功 / `2` 输入或校验失败 / `3` 凭据或认证失败 / `4` 业务失败 / `5` 网络或服务端异常。
2. **读字段一律取 `data.*`**：所有服务端字段都要再往下读一层 `data.<field>`（如 `data.fundRiskLevel`、`data.cancelFlag`）；`trade list` 的订单数组在 `data.data[]`（三层嵌套）。
3. **Work Token 由 CLI 自动管理**：skill 不需要也无法获取 Work Token，禁止调用任何 Work Token 或 Refresh Token 接口。
4. **CLI 重试规则**：CLI 仅在服务端明确返回 HTTP 401 时刷新凭据并重试一次；网络超时、连接中断、5xx 或响应异常时不会自动重试。
5. **用户询问规范**：所有需要用户确认 / 选择 / 输入的步骤，**优先使用 `AskUserQuestion` 工具**。若当前 Agent 不支持 `AskUserQuestion`，则降级为**直接用文字询问**并等待用户回复。本规范覆盖所有 `references/**/*.md` 中的「询问用户」「用户确认」「用户输入」「等用户」等步骤；各文件无需重复说明，统一引用本条。
6. **面向用户展示约束**：不得展示底层状态码、`confirmFlag`、`checkFlag`、`failMsg.code` 等字段名或字段值；面向用户只展示中文状态和可读原因。
7. **dry-run 行为**：所有 CLI 命令都支持 `--dry-run`，仅在排查拼写错误、字段合并结果或 Schema 报错时使用——它执行命名选项 + JSON 输入合并和 Schema 校验，但不读取 Work Token、不发起网络请求、不会产生交易，输出 `{"endpoint": <name>, "request": <merged-payload>}`。dry-run 不是流程的一部分，不写进正常调用样例。
8. **顶层 `update` 字段（版本更新通知）**：受保护接口（`fund buy` / `fund redeem` / `fund redeem-render`、`holding list` / `holding wallet-home` / `holding overview`、`trade list` / `trade detail` / `trade revoke`）的**成功响应**（`ok: true`）在以下三个条件**同时**满足时，CLI 会在 JSON 顶层附加一个 `update` 字段：
   - `getworktoken` 本次返回中包含 `update` 对象（服务端提示当前 CLI 已是最新或需要更新）；
   - 该 `update.latestVersion` 在本地尚未被 dismiss；
   - 本次调用不是 `--dry-run`。
   - 字段内容形如 `{"update": {"latestVersion": "0.3.0", "downloadUrl": "https://...", "changeLog": "..."}}`。格式为 Keep-a-Changelog Markdown（`## [版本] - 日期 \n 描述`）。`downloadUrl` 是**单 URL** 指向一个组合 zip 包（含 SDK + skill 整套）。
   - **触发时机**：业务意图的 CLI 命令成功返回（退出码 0）**之后**；自检阶段（§0.1 ~ §0.5）只判断"能不能干活"，**不触发**版本升级提示（会打断用户当前业务）。即使用户在升级提示后说"先不升级"，skill 也必须保留已完成业务的有效状态继续工作。
   - **展示要求**：
     * 提示语必须是"提醒/建议"语气，**不得**要求用户必须升级才能继续；
     * 必须展示 `data.update.latestVersion`（新版本号）；
     * 提示用户变更内容 `data.update.changeLog`；
     * 可附带 `data.update.downloadUrl` 作为升级包来源（zip URL，不是 SDK 单文件）。
   - **用户操作询问**：使用 `AskUserQuestion` 提供以下四个选项（顺序固定），按用户选择进入对应分支：
     1. **稍后再说（本次会话仍可能再提示）** —— 不调用任何 `dismiss-update`，保留当前会话内未来受保护接口再次提示的机会；
     2. **今天内不再提示（24h）** —— 执行：`aijijin auth dismiss-update --version <latestVersion> --mode today`；
     3. **此版本不再提示** —— 执行：`aijijin auth dismiss-update --version <latestVersion>`（不传 `--mode` 即默认"此版本不再提示"）；
     4. **立即下载并升级** —— 自动执行下方"升级动作"全流程（与 §0.4 "停下等用户完成"模式不同；用户已在 AskUserQuestion 中显式授权）；完成后提示用户 skill 文件已更新，下次业务起新会话生效（当前会话引用的 references 仍是旧版）。
   - **升级动作（用户选择"立即下载并升级"后自动执行）**：
     1. `curl -L -o <tmp> "<downloadUrl>"` 下载 zip 到临时目录；
     2. `unzip -o <tmp> -d ~/.claude/skills/` 解压覆盖 `thsfund/`（zip 根目录是 `thsfund/`，解压会整体覆盖现有 `thsfund/` 目录，旧 vendor whl 被新 vendor whl 自然替换）；
     3. `pip install --force-reinstall "<~/.claude/skills/thsfund/vendor/aijijin_sdk-X.Y.Z-py3-none-any.whl>"` 装新 SDK；
     4. 提示用户 skill 文件已更新，下次业务起新会话生效。
   - **升级范围**：SDK + skill 本身（`SKILL.md` / `references/` / `vendor/`）**必须一起升**，不允许只升其一（skill 改了引用/字段名但 SDK 没换版本会出现接口错位，反之亦然）。

## §3 全局禁止事项

| 禁止事项 | 原因 |
|---|---|
| 禁止自动更换基金代码 / 订单号 / 交易账户 | 真实交易必须锁定用户指定对象 |
| 禁止跳过可撤单判断、风险等级校验、协议确认、支付方式选择、用户最终确认等用户确认节点 | 监管与合规要求 |
| 禁止把用户的购买 / 赎回 / 撤单意图当作风险确认、协议确认、支付方式选择、撤单最终确认 | 不得用意图代替显式确认 |
| 禁止在申购 / 赎回 / 撤单提交失败或结果不明确时自动重试 | 必须先查订单状态再说明 |
| 禁止展示底层状态码、确认标志、失败错误码 | 面向用户只展示中文状态和可读原因 |
| 禁止自行调用任何 Work Token / Refresh Token 接口 | CLI 自动管理；skill 不需要也无法访问 |
| 禁止使用 `curl` 直连保护接口 | 一律走 `aijijin` CLI |
| 禁止通过 `python -c` 内联脚本调用 `aijijin_sdk` 模块绕过 CLI | 一律走 `aijijin` CLI |
| 禁止手工发送未在 CLI Schema 暴露的字段 | CLI 自动完成字段映射 |
