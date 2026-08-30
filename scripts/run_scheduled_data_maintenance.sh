#!/bin/zsh

set -u
set -o pipefail
umask 077

script_directory="${0:A:h}"
project_root="${script_directory:h}"
python_executable="${project_root}/.conda-env/bin/python"
private_automation_directory="${project_root}/data/private/automation"
session_id_file="${private_automation_directory}/data-maintenance-codex-session-id"
notification_disabled_file="${private_automation_directory}/disable-data-maintenance-notification"
codex_executable="/Applications/ChatGPT.app/Contents/Resources/codex"
keychain_service="invest-agent-guchacha-mcp"

mkdir -p "${private_automation_directory}"
chmod 700 "${private_automation_directory}"

if [[ -z "${GUCHACHA_MCP_TOKEN:-}" ]]; then
  keychain_token="$(/usr/bin/security find-generic-password -a "${USER}" -s "${keychain_service}" -w 2>/dev/null || true)"
  if [[ -n "${keychain_token}" ]]; then
    export GUCHACHA_MCP_TOKEN="${keychain_token}"
  fi
  unset keychain_token
fi

run_stamp="$(TZ=Asia/Shanghai date '+%Y%m%dT%H%M%S%z')"
report_path="${private_automation_directory}/data-maintenance-${run_stamp}.json"

cd "${project_root}" || exit 1
"${python_executable}" -m invest_agent.automation.maintenance_cli \
  run-due --workspace-root "${project_root}" --output "${report_path}"
maintenance_exit=$?

if [[ ! -f "${notification_disabled_file}" && -f "${session_id_file}" && -x "${codex_executable}" ]]; then
  session_id="$(tr -d '[:space:]' < "${session_id_file}")"
  if [[ "${session_id}" =~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' ]]; then
    if [[ -f "${report_path}" ]]; then
      notification_prompt="数据维护调度器已结束（退出码 ${maintenance_exit}）。这是后台任务自动触发的只读回报。只读取报告 ${report_path}，用中文简短汇报本次到期作业、成功/失败命令、数据质量和是否需要人工处理；明确说明未运行策略、未生成订单、未交易。不要联网、不要重新采集、不要修改文件、不要调用任何交易能力。"
    else
      notification_prompt="数据维护调度器失败（退出码 ${maintenance_exit}），且没有生成报告。请只说明后台任务失败，并建议在本会话中发送“检查数据维护任务”；不要联网、不要重新采集、不要修改文件、不要调用任何交易能力。"
    fi
    "${codex_executable}" exec resume "${session_id}" "${notification_prompt}"
    notification_exit=$?
    if (( notification_exit != 0 )); then
      print -u2 -- "Codex data-maintenance notification failed with exit ${notification_exit}"
    fi
  else
    print -u2 -- "Skipped Codex notification: invalid data-maintenance session id"
  fi
fi

exit "${maintenance_exit}"
