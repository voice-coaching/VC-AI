#!/usr/bin/env bash
set -Eeuo pipefail

dataset_key=${DATASET_KEY:-71490}
file_keys=${FILE_KEYS:-475431,475432,475434,475435,475437,475439,475440}
retry_seconds=${RETRY_SECONDS:-300}
max_attempts=${MAX_ATTEMPTS:-24}
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
env_path="$project_root/.env.local"
destination="$project_root/data/restricted/aihub/dataset-$dataset_key"
status_path="$destination/approval-watcher.log"

if [[ ! "$dataset_key" =~ ^[1-9][0-9]*$ ]]; then
  echo "DATASET_KEY must be a canonical positive decimal." >&2
  exit 2
fi
if [[ ! "$file_keys" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]]; then
  echo "FILE_KEYS must be a canonical comma-separated key list." >&2
  exit 2
fi
if [[ -x "$project_root/.venv/bin/python" ]]; then
  guard_python_binary="$project_root/.venv/bin/python"
else
  guard_python_binary=python3
fi
"$guard_python_binary" \
  "$project_root/scripts/guard_aihub538_p8_generic_request.py" \
  "$dataset_key" "$file_keys"

if [[ -n "${VOICE_COACH_PYTHON:-}" ]]; then
  python_binary=$VOICE_COACH_PYTHON
elif [[ -x "$project_root/.venv/bin/python" ]]; then
  python_binary="$project_root/.venv/bin/python"
else
  python_binary=python3
fi

if [[ ! -f "$env_path" ]]; then
  echo ".env.local is missing." >&2
  exit 1
fi
if ! [[ "$retry_seconds" =~ ^[1-9][0-9]*$ && "$max_attempts" =~ ^[1-9][0-9]*$ ]]; then
  echo "RETRY_SECONDS and MAX_ATTEMPTS must be positive integers." >&2
  exit 2
fi

api_key=
while IFS= read -r line || [[ -n "$line" ]]; do
  line=${line%$'\r'}
  if [[ "$line" == AIHUB_API_KEY=* ]]; then
    api_key=${line#AIHUB_API_KEY=}
    api_key=${api_key#\"}
    api_key=${api_key%\"}
    api_key=${api_key#\'}
    api_key=${api_key%\'}
    break
  fi
done < "$env_path"
if [[ -z "$api_key" ]]; then
  echo "AIHUB_API_KEY is missing or empty in .env.local." >&2
  exit 1
fi

umask 077
mkdir -p "$destination"
for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  attempt_log=$(printf '%s/approval-attempt-%02d.log' "$destination" "$attempt")
  printf '%s ATTEMPT %d/%d\n' "$(date --iso-8601=seconds)" "$attempt" "$max_attempts" \
    >> "$status_path"

  set +e
  AIHUB_API_KEY="$api_key" \
    bash "$project_root/scripts/download_aihub_cli.sh" download \
      "$dataset_key" "$file_keys" "$destination" > "$attempt_log" 2>&1
  exit_code=$?
  set -e

  # aihubshell exits 0 even when the server refuses with HTTP 502, so the exit
  # code alone would report a completed download for a request that transferred
  # nothing. The log is inspected first, and success additionally requires that a
  # payload file actually appeared.
  downloaded=$(find "$destination" -type f \
    ! -name 'approval-watcher.log' ! -name 'approval-attempt-*.log' | head -n 1)
  if [[ $exit_code -eq 0 && -n "$downloaded" ]] \
    && ! grep -Eq 'HTTP status [45][0-9][0-9]|Download failed' "$attempt_log"; then
    printf '%s DOWNLOAD_COMPLETED\n' "$(date --iso-8601=seconds)" >> "$status_path"
    exit 0
  fi
  if grep -Eq 'HTTP status 502|권한|인증|Authentication|승인' "$attempt_log"; then
    printf '%s WAITING_FOR_PORTAL_APPROVAL\n' "$(date --iso-8601=seconds)" \
      >> "$status_path"
    if ((attempt < max_attempts)); then
      sleep "$retry_seconds"
    fi
    continue
  fi

  printf '%s UNEXPECTED_FAILURE\n' "$(date --iso-8601=seconds)" >> "$status_path"
  echo "Unexpected AI Hub failure; inspect $attempt_log" >&2
  exit "$exit_code"
done

printf '%s APPROVAL_NOT_AVAILABLE_BEFORE_RETRY_LIMIT\n' \
  "$(date --iso-8601=seconds)" >> "$status_path"
exit 2
