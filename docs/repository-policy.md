# Git提交与隐私边界

本仓库的GitHub远程默认必须为**私有仓库**。代码、规则和脱敏公共证据可以版本化；真实账户、金融历史、凭证、数据库和本机运行状态只能留在本机。

## 可以提交

| 类型 | 示例 | 条件 |
|---|---|---|
| 源码、测试和schema | `invest_agent/`、`tests/`、`schemas/` | 不含真实账户fixture或凭证 |
| 版本化政策与策略 | `config/*.json|yaml`、`strategies/` | 只含规则、公开基金代码和脱敏研究假设 |
| 文档与ADR | `README.md`、`docs/` | 不复制受保护接口原始响应 |
| 项目级Skill | `.agents/skills/` | 第三方包已审查、固定版本与哈希；无凭证 |
| MCP配置 | `.codex/config.toml` | 只写环境变量名，不写Bearer token |
| 公开规则快照 | `config/aijijin_research_route_snapshot_v1.json` | 仅白名单基金规则，不含账户、支付方式、客户信息 |
| 调度模板 | `config/launchd/*.plist.example`、`scripts/` | 不含用户名、绝对路径、代理或会话ID |

`config/investment_policy.yaml`包含个人投资偏好和资金边界，但不含账户号或凭证。它只适合当前私有仓库；仓库若转为公开，必须先改用示例模板并清理Git历史。

## 必须忽略

- `data/`：原始批次、SQLite仓、真实/脱敏持仓、规则快照、报告、归因结果、订单和同步状态；
- `.conda-env/`、`.conda-data-env/`、`.venv*`：本地Python环境；
- `.aijijin/`、`credentials.json`、`.env*`、`*.token`、`*.secret`：认证与环境秘密；
- `*.sqlite*`、`*.db*`、`*.log`、`*.pid`：数据库、事务日志和运行日志；
- `*.pem`、`*.key`、`*.p12`、`*.pfx`：私钥与证书；
- `config/launchd/*.plist`：含本机路径/代理的实际调度实例；
- `__pycache__/`、`*.pyc`和各类工具缓存；
- 第三方临时下载目录`vendor-downloads/`。

## 每次推送前

1. `git status --short --ignored`确认私有目录处于ignored；
2. 扫描常见token、私钥和本机绝对路径；
3. 检查待提交二进制和大文件；
4. 运行`git diff --check`；
5. 运行全量测试；
6. 检查远程仓库仍为private；
7. 只在上述门禁全部通过后提交和推送。

发现秘密已经进入提交历史时，不能只删除工作区文件：应立即轮换凭证，并在确认影响范围后重写Git历史。
