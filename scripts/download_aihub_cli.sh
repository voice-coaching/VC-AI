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
  - Official `aihubshell` must be on PATH.
  - AIHUB_API_KEY must be exported for download mode.
  - Dataset use approval must already be complete on the AI Hub portal.
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

if [[ ! "$dataset_key" =~ ^[0-9]+$ ]]; then
  echo "DATASET_KEY must contain digits only." >&2
  exit 2
fi
if [[ "$mode" == "download" && ! "$file_keys" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  echo "FILE_KEYS must be a comma-separated list of numeric keys." >&2
  exit 2
fi
if ! command -v aihubshell >/dev/null 2>&1; then
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

(
  cd "$destination"
  aihubshell \
    -mode d \
    -datasetkey "$dataset_key" \
    -filekey "$file_keys" \
    -aihubapikey "$AIHUB_API_KEY"
)
