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

require_env SOURCE_PROJECT_ID
require_env FIRESTORE_BACKUP_BUCKET

backup_prefix="${FIRESTORE_BACKUP_PREFIX:-firestore}"
backup_stamp="${FIRESTORE_BACKUP_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
output_dir="${FIRESTORE_BACKUP_OUTPUT_DIR:-artifacts/firestore-backup}"
export_path="${FIRESTORE_BACKUP_BUCKET%/}/${backup_prefix}/${backup_stamp}"
metadata_path="${output_dir}/firestore-backup-operation.json"
manifest_path="${output_dir}/firestore-backup-manifest.json"
summary_path="${output_dir}/firestore-backup-summary.md"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$output_dir"

start_output="$(gcloud firestore export "$export_path" --project="$SOURCE_PROJECT_ID" --async 2>&1)"
printf '%s\n' "$start_output"
operation_name="$(extract_operation_name "$start_output")"

if [[ -z "$operation_name" ]]; then
  echo "Unable to extract Firestore export operation name." >&2
  exit 1
fi

wait_for_operation "$SOURCE_PROJECT_ID" "$operation_name" "$metadata_path"
operation_json="$(cat "$metadata_path")"
error_message="$(json_field "$operation_json" error.message)"
if [[ -n "$error_message" ]]; then
  echo "Firestore export failed: ${error_message}" >&2
  exit 1
fi

output_uri_prefix="$(json_field "$operation_json" metadata.outputUriPrefix)"
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > "$manifest_path" <<MANIFEST
{
  "kind": "firestore-backup",
  "startedAt": "${started_at}",
  "completedAt": "${completed_at}",
  "sourceProjectId": "${SOURCE_PROJECT_ID}",
  "backupBucket": "${FIRESTORE_BACKUP_BUCKET}",
  "backupPrefix": "${backup_prefix}",
  "backupStamp": "${backup_stamp}",
  "exportPath": "${export_path}",
  "outputUriPrefix": "${output_uri_prefix:-$export_path}",
  "operationName": "${operation_name}"
}
MANIFEST

cat > "$summary_path" <<SUMMARY
# Firestore Backup Summary

- Started at: ${started_at}
- Completed at: ${completed_at}
- Source project: ${SOURCE_PROJECT_ID}
- Export path: ${export_path}
- Output URI prefix: ${output_uri_prefix:-$export_path}
- Operation: ${operation_name}
SUMMARY

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "manifest_path=${manifest_path}"
    echo "summary_path=${summary_path}"
    echo "export_path=${export_path}"
    echo "operation_name=${operation_name}"
  } >> "$GITHUB_OUTPUT"
fi
