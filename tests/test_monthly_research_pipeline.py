import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.decision.pipeline import (
    ARTIFACT_KEYS,
    build_monthly_research_pipeline,
    write_immutable,
)


ROOT = Path(__file__).resolve().parents[1]


class MonthlyResearchPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (ROOT / "config/monthly_research_pipeline_v1.json").read_text(
                encoding="utf-8"
            )
        )

    def test_builds_four_stage_non_executable_pipeline(self) -> None:
        manifest, artifacts = build_monthly_research_pipeline(
            self.config, workspace_root=ROOT
        )

        self.assertEqual(manifest["summary"]["publication_gate"], "passed")
        self.assertEqual(manifest["summary"]["stages_passed"], 4)
        self.assertEqual(set(artifacts), set(ARTIFACT_KEYS))
        self.assertFalse(manifest["execution"]["network_used"])
        self.assertFalse(manifest["execution"]["automatic_schedule_used"])
        self.assertEqual(manifest["execution"]["orders"], [])

    def test_artifact_manifest_hashes_match_exact_bytes(self) -> None:
        manifest, artifacts = build_monthly_research_pipeline(
            self.config, workspace_root=ROOT
        )
        for key, content in artifacts.items():
            self.assertEqual(
                manifest["artifacts"][key]["sha256"],
                hashlib.sha256(content).hexdigest(),
            )

    def test_same_inputs_produce_identical_manifest_and_artifacts(self) -> None:
        first = build_monthly_research_pipeline(self.config, workspace_root=ROOT)
        second = build_monthly_research_pipeline(self.config, workspace_root=ROOT)
        self.assertEqual(first, second)

    def test_source_config_hash_drift_fails_before_pipeline(self) -> None:
        config = copy.deepcopy(self.config)
        config["source_configs"]["monthly_decision"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            build_monthly_research_pipeline(config, workspace_root=ROOT)

    def test_network_schedule_or_execution_cannot_be_enabled(self) -> None:
        for key in (
            "network_allowed",
            "automatic_schedule_enabled",
            "order_intent_generation_allowed",
            "real_trading_enabled",
        ):
            config = copy.deepcopy(self.config)
            config["safety"][key] = True
            with self.assertRaisesRegex(ValueError, "safety contract"):
                build_monthly_research_pipeline(config, workspace_root=ROOT)

    def test_output_path_escape_and_collision_are_rejected(self) -> None:
        config = copy.deepcopy(self.config)
        config["artifact_paths"]["decision_pack_json"] = "../outside.json"
        with self.assertRaisesRegex(ValueError, "escapes workspace"):
            build_monthly_research_pipeline(config, workspace_root=ROOT)

        config = copy.deepcopy(self.config)
        config["artifact_paths"]["research_report_json"] = config["artifact_paths"]["decision_pack_json"]
        with self.assertRaisesRegex(ValueError, "paths must be unique"):
            build_monthly_research_pipeline(config, workspace_root=ROOT)

    def test_immutable_writer_accepts_identical_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            write_immutable(path, b"same\n")
            write_immutable(path, b"same\n")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                write_immutable(path, b"changed\n")


if __name__ == "__main__":
    unittest.main()
