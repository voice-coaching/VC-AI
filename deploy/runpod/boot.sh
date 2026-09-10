#!/usr/bin/env bash
set -euo pipefail

# Keep the image's SSH setup available while rebuilding ephemeral dependencies.
/start.sh &
image_pid=$!
workspace=/workspace/intelligentai
overlay="$workspace/deployment/int8-overlay"
if [[ ! -f "$workspace/deployment/int8-migration-verified.json" ]]; then
    echo "Migration verification is pending; SSH remains available."
    wait "$image_pid"
    exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless python3.12-venv libseccomp2 libsndfile1 \
    libegl1 libgles2 rsync gpg ffmpeg
getent group 10001 >/dev/null || groupadd --gid 10001 voice-coach
getent passwd 10001 >/dev/null || useradd --uid 10001 --gid 10001 \
    --no-create-home --home-dir /nonexistent voice-coach

if ! python3 "$overlay/deploy/runpod/prepare_runtime.py"; then
    echo "Runtime preparation failed; SSH remains available for recovery."
    wait "$image_pid"
    exit 1
fi

# Startup failures remain visible in logs; do not loop costly model reloads.
python3 "$overlay/deploy/runpod/start_hyperclova.py" \
    >> "$workspace/deployment/hyperclova-int8.log" 2>&1 &
model_pid=$!
trap 'kill -TERM "$model_pid" "$image_pid" 2>/dev/null || true' TERM INT
wait "$model_pid" || echo "Clova stopped; SSH remains available for recovery."
wait "$image_pid"
