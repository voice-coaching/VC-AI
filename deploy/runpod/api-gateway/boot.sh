#!/usr/bin/env bash
# Additive bootstrap: keep the original analysis boot and its overlay intact.
set -euo pipefail
umask 077
root=/workspace/intelligentai/deployment/api-gateway
mkdir -p /run/intelligentai-gateway
run_gateway() {
    local child=
    trap 'if [[ -n "$child" ]]; then kill -TERM "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; fi; exit 0' TERM INT
    while true; do
        python3 "$root/supervisor.py" &
        child=$!
        wait "$child" || true
        child=
        sleep 5 &
        child=$!
        wait "$child" || true
        child=
    done
}
run_gateway &
gateway_pid=$!
bash /workspace/intelligentai/deployment/int8-overlay/deploy/runpod/boot.sh &
analysis_pid=$!
stop() {
    trap - TERM INT
    kill -TERM "$gateway_pid" "$analysis_pid" 2>/dev/null || true
    wait "$gateway_pid" "$analysis_pid" 2>/dev/null || true
}
trap 'stop; exit 0' TERM INT
wait "$analysis_pid" || true
stop
exit 1
