# ADR-0038 本机网页引导爱基金 Skill 安装与设备初始化

日期：2026-10-10；状态：接受。

## 背景

首次部署爱基金能力需要安装项目级 `thsfund` Skill 与固定版本 `aijijin-sdk`，随后在同花顺 App 获取一次性 `initToken`，通过 `aijijin auth init` 注册当前设备并在 App 内确认授权。让用户把完整提示词和令牌发送到 Agent 可以完成流程，但会让一次性令牌进入不必要的对话链路。

爱基金凭证机制与股叉叉、金十的普通 Bearer token 不同。上游 CLI 固定把 access key、secret key、private key、refresh token 与设备信息保存到 `~/.aijijin/credentials.json` 并设置 `0600`；Work Token 与 Refresh Token 由 CLI 自动管理。把 `initToken` 长期存入 macOS 钥匙串既无用途，也不能替代 CLI 的凭证库。

## 决策

“接口配置”页在两个市场 MCP 之前展示爱基金首次使用卡片，提供官方安装说明链接、同花顺 App 的“理财 → 基金Skill → 复制提示词 → 授权”步骤，以及一段可复制给 Agent 的安全安装提示词。

页面允许用户只粘贴一次性 `initToken`。回环服务对长度与字符做门禁后，只调用项目隔离环境内固定的 `.conda-env/bin/aijijin auth init`，不调用 SDK 私有 token 接口，不读取或回显 Work Token / Refresh Token，不保存 `initToken`，也不记录 CLI 原始输出。页面只返回已初始化、待 App 确认或固定错误类别。设备注册会按已审查 CLI 行为采集并发送设备标识，页面在提交前明确提示。

爱基金凭证继续由上游 CLI 写入 `~/.aijijin/credentials.json`；不迁移到钥匙串，也不复制进项目、数据库、日志或 Git。macOS 钥匙串解锁仅适用于股叉叉和金十 MCP。Skill 或 CLI 缺失时页面只提示缺失，不自行下载、安装或升级；安装仍遵循第三方 Skill 静态审查和用户确认要求。

## 边界

该入口只完成首次设备注册，不调用持仓、交易、申购、赎回或撤单接口。App 最终授权仍由用户在同花顺内完成。真实交易的逐笔精确确认、未知状态对账与其他项目安全门禁保持不变。
