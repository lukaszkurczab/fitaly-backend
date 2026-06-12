#!/usr/bin/env bash
set -euo pipefail

export FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-demo-fitaly-local}"
export FIRESTORE_DATABASE_ID="${FIRESTORE_DATABASE_ID:-(default)}"
export FIREBASE_STORAGE_BUCKET="${FIREBASE_STORAGE_BUCKET:-demo-fitaly-local.appspot.com}"
export FIREBASE_CLI_DISABLE_UPDATE_NOTIFIER="${FIREBASE_CLI_DISABLE_UPDATE_NOTIFIER:-true}"
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${TMPDIR:-/tmp}/fitaly-firebase-config}"
mkdir -p "$XDG_CONFIG_HOME"

if [ -z "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -f "service-account.json" ]; then
  export GOOGLE_APPLICATION_CREDENTIALS="service-account.json"
fi

pytest_command="${EVIDENCE_EMULATORS_PYTEST_CMD:-./.venv/bin/pytest tests/*emulator*.py -q}"

if [ -n "${EVIDENCE_EMULATORS_PYTEST_CMD:-}" ]; then
  case "$pytest_command" in
    *pytest*emulator*|*emulator*pytest*) ;;
    *)
      echo "EVIDENCE_EMULATORS_PYTEST_CMD must run pytest against emulator test files; include both 'pytest' and 'emulator' in the command." >&2
      exit 2
      ;;
  esac
fi

firebase emulators:exec \
  --only auth,firestore,storage \
  --project "$FIREBASE_PROJECT_ID" \
  "$pytest_command"
