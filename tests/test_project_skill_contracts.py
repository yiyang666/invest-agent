from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectSkillContractTests(unittest.TestCase):
    def test_thsfund_project_override_blocks_upstream_auto_upgrade(self) -> None:
        text = (ROOT / ".agents/skills/thsfund/SKILL.md").read_text(encoding="utf-8")
        override = text.split("## §0 前置条件", maxsplit=1)[0]
        self.assertIn("禁止自动下载、升级、覆盖或删除Skill/SDK", override)
        self.assertIn("verify-fund-trading-rules", override)
        self.assertIn("赎回、撤单、自动提交与异常自动重试关闭", override)

    def test_trading_rule_skill_keeps_cli_first_read_only_guards(self) -> None:
        skill = ROOT / ".agents/skills/verify-fund-trading-rules/SKILL.md"
        text = skill.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertIn("name: verify-fund-trading-rules", frontmatter)
        self.assertIn("aijijin CLI", frontmatter)
        self.assertIn("不得先用网页搜索", frontmatter)
        for required in (
            "fund subscribe-init",
            "fund fee-rule",
            "fund redeem-render",
            "禁止调用 `fund buy`、`fund redeem`、`trade revoke`",
            "不持久化 CLI 原始响应",
            "完整阅读相邻 `../thsfund/SKILL.md`",
        ):
            self.assertIn(required, text)

    def test_trading_rule_skill_has_discoverable_ui_metadata(self) -> None:
        metadata = (
            ROOT
            / ".agents/skills/verify-fund-trading-rules/agents/openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('display_name: "基金交易规则核验"', metadata)
        self.assertIn("$verify-fund-trading-rules", metadata)


if __name__ == "__main__":
    unittest.main()
