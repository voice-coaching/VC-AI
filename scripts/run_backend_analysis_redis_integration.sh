#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tls_dir=$(mktemp -d)
container_name="voice-coach-redis-integration-$$"
redis_image="redis:8.0-alpine@sha256:5f61955be8ab2ccee9372b84ae4d4da2e2b156f87281e3f218544055e7ee04d4"

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
  find "$tls_dir" -type f -delete
  rmdir "$tls_dir" 2>/dev/null || true
}
trap cleanup EXIT

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$tls_dir/server.key" \
  -out "$tls_dir/server.crt" \
  -days 1 \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  >/dev/null 2>&1
chmod 0755 "$tls_dir"
chmod 0644 "$tls_dir/server.key" "$tls_dir/server.crt"

docker run -d \
  --name "$container_name" \
  -p 127.0.0.1::6379 \
  -v "$tls_dir:/certs:ro" \
  "$redis_image" \
  redis-server \
  --port 0 \
  --tls-port 6379 \
  --tls-cert-file /certs/server.crt \
  --tls-key-file /certs/server.key \
  --tls-ca-cert-file /certs/server.crt \
  --tls-auth-clients no \
  --requirepass synthetic-integration-password \
  >/dev/null

redis_port=$(docker port "$container_name" 6379/tcp | sed 's/.*://')
for _attempt in $(seq 1 30); do
  if (exec 3<>/dev/tcp/127.0.0.1/"$redis_port") 2>/dev/null; then
    exec 3>&-
    break
  fi
  sleep 0.1
done

cd "$repo_root"
AI_TEST_REDIS_HOST=127.0.0.1 \
AI_TEST_REDIS_PORT="$redis_port" \
AI_TEST_REDIS_PASSWORD=synthetic-integration-password \
AI_TEST_REDIS_CA_CERT="$tls_dir/server.crt" \
  .venv/bin/python -m pytest \
    tests/integration/test_backend_analysis_redis_stream_integration.py
