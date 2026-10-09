import hashlib
import json
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SystemCapabilityCatalogTests(unittest.TestCase):
    def test_every_project_skill_is_listed_in_capability_catalog(self) -> None:
        catalog = (ROOT / "docs/capabilities.md").read_text(encoding="utf-8")
        skills = sorted(
            path.parent.name
            for path in (ROOT / ".agents/skills").glob("*/SKILL.md")
        )
        self.assertEqual(
            skills,
            [
                "design-fund-strategy",
                "fund-data-collect",
                "fund-metric-calc",
                "market-context-research",
                "market-data-collect",
                "thsfund",
                "verify-fund-trading-rules",
            ],
        )
        for skill in skills:
            self.assertIn(f"`{skill}`", catalog)

    def test_project_mcp_uses_environment_secret_and_exact_allowlist(self) -> None:
        text = (ROOT / ".codex/config.toml").read_text(encoding="utf-8")
        config = tomllib.loads(text)
        guchacha = config["mcp_servers"]["guchacha"]
        self.assertEqual(guchacha["bearer_token_env_var"], "GUCHACHA_MCP_TOKEN")
        self.assertNotIn("bearer_token =", text)
        self.assertEqual(
            set(guchacha["enabled_tools"]),
            {
                "list_datasets",
                "get_index_valuation",
                "get_index_weight",
                "get_index_forward_pe",
                "get_industry_crowding",
                "get_market_series",
                "get_macro",
            },
        )

    def test_agent_boot_sequence_includes_capability_catalog(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        read_first = agents.split("## Safety invariants", maxsplit=1)[0]
        self.assertIn("`docs/capabilities.md`", read_first)
        self.assertIn("docs/capabilities.md", (ROOT / "README.md").read_text())

    def test_jin10_is_narrowly_reviewed_and_scheduled_in_unified_daily_job(self) -> None:
        market = json.loads(
            (ROOT / "config/market_data_sync_v1.json").read_text(encoding="utf-8")
        )
        policy = market["jin10_policy"]
        self.assertEqual(set(policy["enabled_tools"]), {"get_quote", "list_calendar"})
        self.assertEqual(
            set(policy["interactive_tools"]),
            {"search_flash", "search_news", "get_news"},
        )
        self.assertEqual(
            set(policy["excluded_tools"]),
            {"get_kline", "list_flash", "list_news"},
        )
        self.assertEqual(
            policy["scheduler_status"], "active_unified_daily_maintenance"
        )
        scheduled_operations = {
            command.get("operation")
            for job in market["jobs"]
            for command in job["commands"]
        }
        self.assertIn("collect_jin10_quotes", scheduled_operations)
        self.assertIn("collect_jin10_calendar", scheduled_operations)
        daily = next(job for job in market["jobs"] if job["job_id"] == "market_daily_series")
        daily_operations = [command.get("operation") for command in daily["commands"]]
        self.assertEqual(daily_operations[-2:], ["collect_jin10_quotes", "collect_jin10_calendar"])
        review = (ROOT / "docs/integrations/jin10-mcp-review.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`get_kline` | 禁用", review)

    def test_visible_data_update_automation_uses_only_unified_runner(self) -> None:
        prompt = (ROOT / "config/codex_data_update_automation_v1.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("run_data_maintenance_cli.sh plan", prompt)
        self.assertIn("run_data_maintenance_cli.sh run-due", prompt)
        self.assertIn("不得绕过统一维护CLI", prompt)
        self.assertIn("禁止刷新爱基金账户", prompt)
        self.assertIn("禁止用模拟", prompt)
        self.assertFalse((ROOT / "scripts/run_scheduled_fund_data_sync.sh").exists())

    def test_reviewed_aijijin_artifact_matches_committed_lock(self) -> None:
        wheel = ROOT / ".agents/skills/thsfund/vendor/aijijin_sdk-0.2.1-py3-none-any.whl"
        expected = (
            ROOT / "config/locks/aijijin-sdk-0.2.1-project-skill.artifacts"
        ).read_text(encoding="utf-8").splitlines()[3].split()[0]
        self.assertEqual(hashlib.sha256(wheel.read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
