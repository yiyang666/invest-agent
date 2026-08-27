#!/bin/zsh

set -u
set -o pipefail
umask 077

script_directory="${0:A:h}"
project_root="${script_directory:h}"
python_executable="${project_root}/.conda-env/bin/python"
sync_config="${project_root}/config/fund_data_sync_v1.json"
private_automation_directory="${project_root}/data/private/automation"
private_sync_directory="${project_root}/data/private/sync"
session_id_file="${private_automation_directory}/codex-session-id"
notification_disabled_file="${private_automation_directory}/disable-codex-notification"
codex_executable="/Applications/ChatGPT.app/Contents/Resources/codex"

mkdir -p "${private_automation_directory}" "${private_sync_directory}"
chmod 700 "${private_automation_directory}" "${private_sync_directory}"

run_stamp="$(TZ=Asia/Shanghai date '+%Y%m%dT%H%M%S%z')"
report_path="${private_sync_directory}/fund-data-sync-${run_stamp}.json"

cd "${project_root}" || exit 1
"${python_executable}" -m invest_agent.data.sync_cli \
  --config "${sync_config}" \
  --workspace-root "${project_root}" \
  --output "${report_path}"
sync_exit=$?

if [[ ! -f "${notification_disabled_file}" && -f "${session_id_file}" && -x "${codex_executable}" ]]; then
  session_id="$(tr -d '[:space:]' < "${session_id_file}")"
  if [[ "${session_id}" =~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' ]]; then
    if [[ -f "${report_path}" ]]; then
      notification_prompt="定时基金数据同步已结束（同步退出码 ${sync_exit}）。这是后台任务自动触发的回报。只读取报告 ${report_path}，用中文简短汇报本次状态、基金数、发布/拒绝/错误/陈旧数量，以及是否需要人工处理；明确说明未运行策略、未生成订单、未交易。不要联网、不要重新同步、不要修改文件、不要调用任何交易能力。"
    else
      notification_prompt="定时基金数据同步失败（同步退出码 ${sync_exit}），且没有生成报告。请只向用户说明后台同步失败，并建议在本会话中发送“检查定时同步”；不要联网、不要重新同步、不要修改文件、不要调用任何交易能力。"
    fi
    "${codex_executable}" exec resume "${session_id}" "${notification_prompt}"
    notification_exit=$?
    if (( notification_exit != 0 )); then
      print -u2 -- "Codex conversation notification failed with exit ${notification_exit}"
    fi
  else
    print -u2 -- "Skipped Codex notification: invalid session id file"
  fi
fi

exit "${sync_exit}"
