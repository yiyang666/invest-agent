#!/bin/zsh

set -u
set -o pipefail
umask 077

script_directory="${0:A:h}"
project_root="${script_directory:h}"
python_executable="${project_root}/.conda-env/bin/python"

if [[ -z "${JIN10_MCP_TOKEN:-}" ]]; then
  secret_value="$(/usr/bin/security find-generic-password -a "${USER}" -s invest-agent-jin10-mcp -w 2>/dev/null || true)"
  if [[ -n "${secret_value}" ]]; then
    export JIN10_MCP_TOKEN="${secret_value}"
  fi
  unset secret_value
fi

if [[ -z "${JIN10_MCP_TOKEN:-}" ]]; then
  exit 78
fi

cd "${project_root}" || exit 1
exec "${python_executable}" -m invest_agent.market_data.cli collect-jin10-quotes
