#!/usr/bin/env bash
set -euo pipefail
umask 077

mkdir -p /run/intelligentai
exec 8>/run/intelligentai/manager.lock
flock -n 8 || exit 0
supervisor_pid=
stop() {
    trap - TERM INT
    if [[ -n "$supervisor_pid" ]]; then
        kill -TERM "$supervisor_pid" 2>/dev/null || true
        wait "$supervisor_pid" 2>/dev/null || true
    fi
    exit 0
}
trap stop TERM INT
while true; do
    python3 /opt/intelligentai/app/deploy/runpod/supervise_services.py \
        --config /opt/intelligentai/app/deploy/runpod/services.json --parent-pid "$$" 8>&- &
    supervisor_pid=$!
    wait "$supervisor_pid" || true
    supervisor_pid=
    echo "Service supervisor exited; retrying in 30s."
    sleep 30 & wait $! || true
done
