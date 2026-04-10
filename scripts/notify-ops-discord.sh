#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${OPS_ALERT_DISCORD_WEBHOOK_URL:-}" ]]; then
  echo "[ops-alert] OPS_ALERT_DISCORD_WEBHOOK_URL is not set; skipping Discord notification."
  exit 0
fi

payload="$(python3 - <<'PY'
import json
import os

repo = os.environ.get('GITHUB_REPOSITORY', 'unknown-repo')
workflow_name = os.environ.get('WORKFLOW_NAME', os.environ.get('GITHUB_WORKFLOW', 'unknown-workflow'))
environment_name = os.environ.get('WORKFLOW_ENVIRONMENT', 'unknown')
owner = os.environ.get('WORKFLOW_OWNER', 'unassigned')
status = os.environ.get('WORKFLOW_STATUS', 'failure').upper()
run_number = os.environ.get('GITHUB_RUN_NUMBER', 'unknown')
branch_name = os.environ.get('GITHUB_REF_NAME', 'unknown')
server_url = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
run_id = os.environ.get('GITHUB_RUN_ID', '')
run_url = f'{server_url}/{repo}/actions/runs/{run_id}' if run_id else server_url
sha_short = os.environ.get('GITHUB_SHA', 'unknown')[:7]

embed = {
    'title': f'[{repo}] {workflow_name} {status}',
    'description': (
        f'Environment: `{environment_name}`\n'
        f'Owner: `{owner}`\n'
        f'Branch: `{branch_name}`\n'
        f'SHA: `{sha_short}`\n'
        f'Run: [#{run_number}]({run_url})'
    ),
    'color': 15158332,
}
print(json.dumps({'embeds': [embed]}))
PY
)"

curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  -X POST \
  -d "$payload" \
  "$OPS_ALERT_DISCORD_WEBHOOK_URL"
