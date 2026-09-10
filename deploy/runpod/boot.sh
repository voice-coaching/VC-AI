#!/usr/bin/env bash
set -euo pipefail
umask 027

mkdir -p /run/intelligentai
exec 9>/run/intelligentai/boot.lock
if ! flock -n 9; then
    echo "RunPod boot is already managed."
    exit 0
fi

# Keep the image's SSH setup available while rebuilding ephemeral dependencies.
/start.sh 9>&- &
image_pid=$!
supervisor_pid=
workspace=/workspace/intelligentai
overlay="$workspace/deployment/int8-overlay"

stop_services() {
    trap - TERM INT
    if [[ -n "$supervisor_pid" ]]; then
        kill -TERM "$supervisor_pid" 2>/dev/null || true
        wait "$supervisor_pid" 2>/dev/null || true
    fi
    kill -TERM "$image_pid" 2>/dev/null || true
    wait "$image_pid" 2>/dev/null || true
    exit 0
}
trap stop_services TERM INT

while [[ ! -f "$workspace/deployment/int8-migration-verified.json" ]]; do
    echo "Migration verification is pending; SSH remains available."
    sleep 30 & wait $! || true
done

export DEBIAN_FRONTEND=noninteractive
prepare_host() {
    apt-get update || return 1
    apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless python3.12-venv libseccomp2 libsndfile1 \
        libegl1 libgles2 rsync gpg ffmpeg || return 1
    getent group 10001 >/dev/null || groupadd --gid 10001 voice-coach || return 1
    getent passwd 10001 >/dev/null || useradd --uid 10001 --gid 10001 \
        --no-create-home --home-dir /nonexistent voice-coach || return 1
    python3 "$overlay/deploy/runpod/prepare_runtime.py"
}

delay=15
until prepare_host; do
    echo "Runtime preparation failed; retrying in ${delay}s. SSH remains available."
    sleep "$delay" & wait $! || true
    delay=$((delay < 150 ? delay * 2 : 300))
done

# Each child has its own readiness, bounded backoff, logs, and graceful shutdown.
while true; do
    bash /opt/intelligentai/app/deploy/runpod/run_services.sh 9>&- &
    supervisor_pid=$!
    wait "$supervisor_pid" || true
    supervisor_pid=
    echo "Service supervisor exited; retrying in 30s. SSH remains available."
    sleep 30 & wait $! || true
done
