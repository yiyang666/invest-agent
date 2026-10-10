"""One-time macOS Keychain setup for reviewed market integrations."""

from __future__ import annotations

from dataclasses import dataclass
import os
import pwd
import subprocess
import sys


SECURITY_ITEM_NOT_FOUND_EXIT = 44
SECURITY_INTERACTION_NOT_ALLOWED_EXIT = 36


@dataclass(frozen=True)
class IntegrationCredentialSpec:
    provider: str
    label: str
    endpoint: str
    service: str
    environment_variable: str


INTEGRATION_SPECS = {
    spec.provider: spec for spec in (
        IntegrationCredentialSpec(
            "guchacha", "股叉叉 MCP", "https://guchacha.com/mcp",
            "invest-agent-guchacha-mcp", "GUCHACHA_MCP_TOKEN",
        ),
        IntegrationCredentialSpec(
            "jin10", "金十 MCP", "https://mcp.jin10.com/mcp",
            "invest-agent-jin10-mcp", "JIN10_MCP_TOKEN",
        ),
    )
}


class CredentialStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class KeychainCredentialStore:
    """Store secrets through Keychain without exposing them in process arguments."""

    def __init__(self, account: str | None = None):
        self.account = account or pwd.getpwuid(os.getuid()).pw_name
        if sys.platform != "darwin":
            raise CredentialStoreError("keychain_unavailable")

    def configured(self, provider: str) -> bool:
        spec = INTEGRATION_SPECS[provider]
        try:
            result = subprocess.run(
                [
                    "/usr/bin/security", "find-generic-password",
                    "-a", self.account, "-s", spec.service, "-w",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CredentialStoreError("keychain_error") from error
        if result.returncode == 0:
            return True
        if result.returncode == SECURITY_ITEM_NOT_FOUND_EXIT:
            return False
        if result.returncode == SECURITY_INTERACTION_NOT_ALLOWED_EXIT:
            raise CredentialStoreError("keychain_locked")
        raise CredentialStoreError("keychain_error")

    def set(self, provider: str, secret: str) -> None:
        spec = INTEGRATION_SPECS[provider]
        # `security -i` reads the command on stdin. Hex encoding keeps the command
        # parser deterministic while the original token never appears in argv.
        command = (
            f"add-generic-password -U -a {self.account} -s {spec.service} "
            f"-l {spec.service} -X {secret.encode('utf-8').hex()}\n"
        )
        try:
            result = subprocess.run(
                ["/usr/bin/security", "-i"], input=command, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CredentialStoreError("keychain_error") from error
        finally:
            command = None
        if result.returncode == SECURITY_INTERACTION_NOT_ALLOWED_EXIT:
            raise CredentialStoreError("keychain_locked")
        if result.returncode != 0:
            raise CredentialStoreError("keychain_error")


def integration_status(store: KeychainCredentialStore) -> list[dict[str, object]]:
    result = []
    for spec in INTEGRATION_SPECS.values():
        environment_configured = bool(os.environ.get(spec.environment_variable))
        state = "configured" if environment_configured else "missing"
        if not environment_configured:
            try:
                state = "configured" if store.configured(spec.provider) else "missing"
            except CredentialStoreError as error:
                state = error.code
        result.append({
            "provider": spec.provider,
            "label": spec.label,
            "endpoint": spec.endpoint,
            "configured": state == "configured",
            "state": state,
            "storage": "当前服务环境变量" if environment_configured else "macOS 钥匙串",
        })
    return result
