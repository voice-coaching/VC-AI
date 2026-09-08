#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  download_aihub_cli.sh list DATASET_KEY [DESTINATION]
  download_aihub_cli.sh download DATASET_KEY FILE_KEYS [DESTINATION]

Examples:
  scripts/download_aihub_cli.sh list 71490
  scripts/download_aihub_cli.sh download 71490 \
    475431,475432,475434,475435,475437,475439,475440

Requirements:
  - Official `aihubshell` must be on PATH for list mode.
  - AIHUB_API_KEY must be exported for download mode.
  - Dataset use approval must already be complete on the AI Hub portal.
  - Download mode uses the credential-safe resumable Python client; the key is
    never forwarded as a command-line argument.
EOF
}

if [[ $# -lt 2 ]]; then
  usage >&2
  exit 2
fi

mode=$1
dataset_key=$2
file_keys=${3:-}
default_destination="data/restricted/aihub/dataset-${dataset_key}"

if [[ "$mode" == "list" ]]; then
  destination=${3:-$default_destination}
elif [[ "$mode" == "download" ]]; then
  if [[ $# -lt 3 ]]; then
    usage >&2
    exit 2
  fi
  destination=${4:-$default_destination}
else
  usage >&2
  exit 2
fi

if [[ ! "$dataset_key" =~ ^[1-9][0-9]*$ ]]; then
  echo "DATASET_KEY must be a canonical positive decimal." >&2
  exit 2
fi
if [[ "$mode" == "download" && ! "$file_keys" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]]; then
  echo "FILE_KEYS must be a comma-separated list of numeric keys." >&2
  exit 2
fi

script_directory=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repository_root=$(cd "$script_directory/.." && pwd -P)
if [[ -x "$repository_root/.venv/bin/python" ]]; then
  guard_python_binary="$repository_root/.venv/bin/python"
else
  guard_python_binary=python3
fi
if [[ "$mode" == "download" ]]; then
  "$guard_python_binary" \
    "$script_directory/guard_aihub538_p8_generic_request.py" \
    "$dataset_key" "$file_keys"
fi

if [[ -n "${VOICE_COACH_PYTHON:-}" ]]; then
  python_binary=$VOICE_COACH_PYTHON
elif [[ -x "$repository_root/.venv/bin/python" ]]; then
  python_binary="$repository_root/.venv/bin/python"
else
  python_binary=python3
fi

if [[ "$mode" == "download" ]]; then
  if [[ "$dataset_key" == "538" ]]; then
    case ",$file_keys," in
      *,54945,*|*,54946,*|*,54947,*|*,54948,*|*,54949,*|*,54951,*)
        echo "AI Hub 538 calibration source keys require scripts/download_aihub538_authorized_sources.py and a feasible 0400 v5 receipt." >&2
        exit 2
        ;;
    esac
  fi
fi
if [[ "$mode" == "list" ]] && ! command -v aihubshell >/dev/null 2>&1; then
  echo "Official aihubshell was not found on PATH." >&2
  exit 1
fi

umask 077
mkdir -p "$destination"
destination=$(cd "$destination" && pwd -P)

if [[ "$mode" == "list" ]]; then
  inventory="$destination/dataset-${dataset_key}-files.txt"
  aihubshell -mode l -datasetkey "$dataset_key" | tee "$inventory"
  echo "Inventory saved to: $inventory"
  exit 0
fi

: "${AIHUB_API_KEY:?Export AIHUB_API_KEY before download mode.}"
available_kib=$(df -Pk "$destination" | awk 'NR==2 {print $4}')
echo "Destination: $destination"
echo "Available space: $((available_kib / 1024 / 1024)) GiB"
echo "Dataset key: $dataset_key"
echo "File keys: $file_keys"

exec "$python_binary" "$script_directory/download_aihub_secure.py" \
  "$dataset_key" "$file_keys" "$destination"
