#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

json_field() {
  local json_text="$1"
  local path_expr="$2"
  python3 - "$path_expr" "$json_text" <<'PY'
import json
import sys

path_expr = sys.argv[1].split('.')
data = json.loads(sys.argv[2])
current = data
for part in path_expr:
    if not part:
        continue
    if isinstance(current, dict):
        current = current.get(part)
    else:
        current = None
    if current is None:
        break
if isinstance(current, bool):
    print('true' if current else 'false')
elif current is None:
    print('')
else:
    print(current)
PY
}

extract_operation_name() {
  local text="$1"
  python3 - "$text" <<'PY'
import re
import sys

text = sys.argv[1]
match = re.search(r'(projects/[^\s]+/databases/[^\s]+/operations/[^\s]+|projects/[^\s]+/operations/[^\s]+)', text)
print(match.group(1) if match else '')
PY
}

wait_for_operation() {
  local project_id="$1"
  local operation_name="$2"
  local output_json="$3"
  local attempts="${OPERATION_MAX_ATTEMPTS:-180}"
  local interval_seconds="${OPERATION_POLL_INTERVAL_SECONDS:-20}"
  local describe_json=""

  for _ in $(seq 1 "$attempts"); do
    describe_json="$(gcloud firestore operations describe "$operation_name" --project="$project_id" --format=json)"
    if [[ "$(json_field "$describe_json" done)" == "true" ]]; then
      printf '%s\n' "$describe_json" > "$output_json"
      return 0
    fi
    sleep "$interval_seconds"
  done

  echo "Timed out waiting for Firestore operation ${operation_name}" >&2
  return 1
}

resolve_latest_export_path() {
  require_env FIRESTORE_BACKUP_BUCKET
  local backup_prefix="${FIRESTORE_BACKUP_PREFIX:-firestore}"
  local latest_path
  latest_path="$(gcloud storage ls "${FIRESTORE_BACKUP_BUCKET%/}/${backup_prefix}/" | sed 's#/$##' | sort | tail -n 1)"
  if [[ -z "$latest_path" ]]; then
    echo "Unable to resolve latest Firestore backup path from ${FIRESTORE_BACKUP_BUCKET%/}/${backup_prefix}/" >&2
    exit 1
  fi
  printf '%s\n' "$latest_path"
}

require_env FIRESTORE_RESTORE_PROJECT_ID

export_path="${FIRESTORE_EXPORT_PATH:-}"
if [[ -z "$export_path" ]]; then
  export_path="$(resolve_latest_export_path)"
fi

output_dir="${FIRESTORE_RESTORE_OUTPUT_DIR:-artifacts/firestore-restore-drill}"
metadata_path="${output_dir}/firestore-restore-operation.json"
manifest_path="${output_dir}/firestore-restore-manifest.json"
summary_path="${output_dir}/firestore-restore-summary.md"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
restore_base_url="${RESTORE_BACKEND_BASE_URL:-}"
version_payload=""

mkdir -p "$output_dir"

start_output="$(gcloud firestore import "$export_path" --project="$FIRESTORE_RESTORE_PROJECT_ID" --async 2>&1)"
printf '%s\n' "$start_output"
operation_name="$(extract_operation_name "$start_output")"

if [[ -z "$operation_name" ]]; then
  echo "Unable to extract Firestore import operation name." >&2
  exit 1
fi

wait_for_operation "$FIRESTORE_RESTORE_PROJECT_ID" "$operation_name" "$metadata_path"
operation_json="$(cat "$metadata_path")"
error_message="$(json_field "$operation_json" error.message)"
if [[ -n "$error_message" ]]; then
  echo "Firestore import failed: ${error_message}" >&2
  exit 1
fi

if [[ -n "$restore_base_url" ]]; then
  normalized_base_url="${restore_base_url%/}"
  bash scripts/check-health-endpoint.sh restore-health "${normalized_base_url}/api/v1/health" "${RESTORE_HEALTH_MAX_LATENCY_MS:-5000}"
  bash scripts/check-health-endpoint.sh restore-firestore "${normalized_base_url}/api/v1/health/firestore" "${RESTORE_FIRESTORE_MAX_LATENCY_MS:-5000}"
  version_payload="$(curl --fail --silent --show-error "${normalized_base_url}/api/v1/version")"
fi

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$manifest_path" <<MANIFEST
{
  "kind": "firestore-restore-drill",
  "startedAt": "${started_at}",
  "completedAt": "${completed_at}",
  "targetProjectId": "${FIRESTORE_RESTORE_PROJECT_ID}",
  "exportPath": "${export_path}",
  "operationName": "${operation_name}",
  "restoreBackendBaseUrl": "${restore_base_url}",
  "versionPayload": $(VERSION_PAYLOAD="$version_payload" python3 - <<'PY'
import json
import os
print(json.dumps(os.environ.get('VERSION_PAYLOAD', '')))
PY
)
}
MANIFEST

cat > "$summary_path" <<SUMMARY
# Firestore Restore Drill Summary

- Started at: ${started_at}
- Completed at: ${completed_at}
- Target project: ${FIRESTORE_RESTORE_PROJECT_ID}
- Import source: ${export_path}
- Operation: ${operation_name}
- Restore backend base URL: ${restore_base_url:-not configured}
SUMMARY

if [[ -n "$version_payload" ]]; then
  {
    echo ""
    echo "## Version payload"
    echo '```json'
    printf '%s\n' "$version_payload"
    echo '```'
  } >> "$summary_path"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "manifest_path=${manifest_path}"
    echo "summary_path=${summary_path}"
    echo "export_path=${export_path}"
    echo "operation_name=${operation_name}"
  } >> "$GITHUB_OUTPUT"
fi
