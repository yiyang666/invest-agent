"""Reviewed first-run onboarding for the project-local aijijin CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


class AijijinOnboardingError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AijijinOnboarding:
    def __init__(self, root: Path, credential_path: Path | None = None):
        self.root = root.resolve()
        self.executable = self.root / ".conda-env/bin/aijijin"
        self.skill_path = self.root / ".agents/skills/thsfund/SKILL.md"
        self.credential_path = credential_path or Path.home() / ".aijijin/credentials.json"

    def status(self) -> dict[str, object]:
        skill_installed = self.skill_path.is_file()
        cli_installed = self.executable.is_file()
        configured = self.credential_path.is_file()
        state = (
            "configured" if configured
            else "ready" if skill_installed and cli_installed
            else "missing_cli" if skill_installed
            else "missing_skill"
        )
        return {
            "state": state,
            "configured": configured,
            "skill_installed": skill_installed,
            "cli_installed": cli_installed,
            "storage": "aijijin CLI 凭证库" if configured else None,
        }

    def initialize(self, init_token: str) -> dict[str, object]:
        if not self.skill_path.is_file():
            raise AijijinOnboardingError("skill_missing")
        if not self.executable.is_file():
            raise AijijinOnboardingError("cli_missing")
        try:
            result = subprocess.run(
                [str(self.executable), "auth", "init", init_token],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AijijinOnboardingError("init_unavailable") from error
        if result.returncode == 0:
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError):
                payload = {}
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            confirm_status = data.get("confirmStatus") if isinstance(data, dict) else None
            return {"state": "pending_authorization" if confirm_status in (0, "0") else "configured"}
        codes = {2: "invalid_token", 3: "expired_token", 4: "business_error", 5: "network_error"}
        raise AijijinOnboardingError(codes.get(result.returncode, "init_failed"))
