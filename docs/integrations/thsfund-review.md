# 爱基金 `thsfund` Skill 静态审查

- 审查日期：2026-08-15
- 页面：[爱基金 Skills 安装说明](https://mams.10jqka.com.cn/new/server/html/110213.html)
- Bundle 版本：`thsfund 0.2.0`
- SDK：`aijijin-sdk 0.2.0`
- Bundle SHA-256：`bc80a9064e5145a6449ff491474ca14fe7822c7d078820956d11c5e55371ece8`
- 项目内wheel SHA-256：`14089db9d9cd5e9c75e5a3ecaa01cc3f7bd194485974e68bf127116ffa240ccf`
- 状态：已静态审查；Skill 与 SDK 已项目级安装；逐笔受控申购试运行完成；赎回/撤单及自动交易关闭

项目副本仅移除了上游Skill头部不受Codex schema支持的`version`字段；业务流程未改，版本继续由审查记录、wheel文件名和哈希锁定。

## 1. 能力范围

Skill 包含五类受保护业务：

1. 持仓/钱包总览
2. 基金申购
3. 基金赎回
4. 交易记录与详情查询
5. 撤单

SDK 通过 `aijijin` CLI 调用 `trade.5ifund.com` 和 `fund.10jqka.com.cn` 的接口。

## 2. 包内容

- `SKILL.md`
- 申购、赎回、持仓、交易查询、撤单和安装说明
- 初始化二维码页面
- 纯 Python wheel：`aijijin_sdk-0.2.0-py3-none-any.whl`
- wheel 依赖：`requests>=2.31.0`

## 3. 静态代码观察

- 未发现 `eval`、动态代码下载执行或 shell 拼接执行。
- 网络请求通过 `requests` 发往配置的网关和业务 API。
- macOS 初始化会运行固定参数的 `ioreg`，采集 `IOPlatformUUID`；降级路径可能使用 MAC 地址和主机名。
- 设备唯一标识、设备名和设备哈希参与注册，并随凭证本地保存。
- 凭证默认写到 `~/.aijijin/credentials.json`；POSIX 权限设置为 `0600`。
- 凭证包含 access key、secret key、private key、refresh token 和设备信息，属于高敏感数据。
- CLI 仅在明确 HTTP 401 时刷新凭证并重试一次；其他网络/服务异常不自动重试。

## 4. 需要额外加固的问题

### 4.1 申购缺少统一的最终订单确认

上游申购流程在部分分支会从协议/签约步骤直接提交；钱包余额足额或无需签约时，没有一个统一、紧邻提交动作的完整订单摘要确认。

本项目必须在 `aijijin fund buy` 前增加自己的最终确认门：展示基金、金额、支付账户、费用、协议状态和预计确认信息，并验证一次性批准。

基金候选的渠道核验不得以调用 `fund buy` 试单。申购费优先通过只读的 `fund fee-rule` 查询；只有核验可购状态、最低金额或限额确有需要时，才调用只读的 `fund subscribe-init`，并且只提取非敏感规则字段，不保存账户、支付方式或原始响应。动态规则必须记录渠道与核验时点，查询结果仍不能解除 `advisory_only`。

### 4.2 自动升级路径不适用于 Codex

上游 Skill 的自动升级说明硬编码解压到 `~/.claude/skills/`，不适合 Codex，并会覆盖现有文件。因此禁用任何自动升级；升级由本项目固定版本、校验并人工执行。

### 4.3 运行地址可被环境变量覆盖

SDK 支持 `AIJIJIN_GATEWAY_URL` 和 `AIJIJIN_API_BASE_URL`。生产运行前必须拒绝未知覆盖或校验域名白名单，防止凭证发送到非预期服务器。

### 4.4 安装说明包含不可逆清理

上游 runbook 会删除五个旧版 `fund-*` Skill 目录。本项目在未盘点并取得用户明确批准前，不执行任何删除。

### 4.5 设备隐私

安装 SDK 本身不注册设备；执行 `aijijin auth init` 时会采集并发送设备唯一标识。认证前必须让用户明确知情并同意。

## 5. 本项目建议的安装方式

1. 使用项目级 `.agents/skills/thsfund/`，不安装到全局目录。
2. SDK 使用 Miniconda 创建的项目专用 `.conda-env`（Python 3.12，conda-forge），不修改全局 Python，也不写入 shell 初始化配置。
3. 固定 `0.2.0` 与上述 SHA-256；先校验再复制/安装。
4. 首次只启用只读能力，验证持仓和订单查询。
5. 申购、赎回、撤单由项目自己的 Execution Gateway 包装并默认关闭。
6. 自动更新关闭；任何升级重新静态审查。

动态产品交易规则优先通过CLI只读预检获取，公开网页仅用于交叉核验。赎回预览原始响应包含交易账户信息，不得持久化；项目只保留脱敏归一化规则。

## 6. 安装前置门禁

- [x] 用户同意项目级安装 Skill
- [x] 用户同意创建隔离 Python 环境并安装 wheel
- [x] 用户知情并同意认证时采集设备唯一标识
- [x] 凭证已保存到约定位置，文件权限为仅当前用户可读写
- [ ] 确认域名白名单与环境变量策略
- [x] 解决 `.agents/` 当前只读限制并完成项目级安装
- [x] 完成只读账户总览命令测试
- [x] 明确批准前保持真实交易禁用

## 7. 本地安装与验证记录

- 项目 Skill 路径：`.agents/skills/thsfund/`
- Python 环境：`.conda-env`，Python 3.12，Miniconda，conda-forge
- SDK：`aijijin-sdk 0.2.0`
- HTTP 依赖：`requests 2.34.2`
- TLS：OpenSSL 3.6.3
- 已验证 SDK 导入、版本和 `aijijin --help`；已完成设备注册、最终授权及一次只读账户总览查询。
- 已读取钱包余额、基金持仓及总资产汇总；原始结果未写入仓库，后续仅通过脱敏 PortfolioSnapshot 输出。
- 上游说明中的 `aijijin --version` 在 0.2.0 不受支持，因此以包元数据和 Python 导入双重核对版本。
- macOS 系统 Python 3.9 使用旧版 LibreSSL，不作为本项目运行时。

## 8. 代表基金研究快照（2026-08-26）

为 `hierarchical_risk_budget_valuation@0.2.0` 的探索门，项目对8只代表基金运行了只读 `fund fee-rule`，并在确有必要时运行只读 `fund subscribe-init`。未调用 `fund buy`、`fund redeem`、撤单或订单提交接口。

归一化结果保存于 `config/aijijin_research_route_snapshot_v1.json`，只包含基金身份、可购状态、最低/最高申购额、当日剩余额度、风险等级、费率阶梯、渠道折扣及平台展示的确认/到账工作日。原始受保护响应未持久化，账户、客户、支付方式和凭证字段均未进入仓库。

该快照只证明2026-08-26当前渠道状态。用于历史回测时必须标记 `counterfactual_execution`，并用法定首档费率重复费用敏感性；它不解除 `advisory_only`，也不代表历史每天适用同一限额或到账规则。
