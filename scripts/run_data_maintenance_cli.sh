#!/bin/zsh

set -u
set -o pipefail
umask 077

script_directory="${0:A:h}"
project_root="${script_directory:h}"
python_executable="${project_root}/.conda-env/bin/python"

case "${1:-}" in
  plan|run-due|run-job|run-bootstrap) ;;
  *)
    print -u2 -- "usage: ${0:t} {plan|run-due|run-job|run-bootstrap} [arguments]"
    exit 64
    ;;
esac

load_keychain_secret() {
  local environment_name="$1"
  local service_name="$2"
  local secret_value
  if [[ -n "${(P)environment_name:-}" ]]; then
    return 0
  fi
  secret_value="$(/usr/bin/security find-generic-password -a "${USER}" -s "${service_name}" -w 2>/dev/null || true)"
  if [[ -n "${secret_value}" ]]; then
    export "${environment_name}=${secret_value}"
  fi
  unset secret_value
}

load_keychain_secret GUCHACHA_MCP_TOKEN invest-agent-guchacha-mcp
load_keychain_secret JIN10_MCP_TOKEN invest-agent-jin10-mcp

cd "${project_root}" || exit 1
exec "${python_executable}" -m invest_agent.automation.maintenance_cli \
  "$@" --workspace-root "${project_root}"
