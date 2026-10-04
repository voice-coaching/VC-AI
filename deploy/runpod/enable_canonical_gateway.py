"""Expose authenticated canonical and deployment health routes; preserve TTS."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    config = Path("/workspace/intelligentai/deployment/api-gateway/nginx.conf")
    raw = config.read_bytes()
    anchor = b"location = /health/services { proxy_pass http://127.0.0.1:8080; }"
    additions = [b"location = /health/handoff { proxy_pass http://127.0.0.1:8080; }",
                 b"location = /health/deployment { proxy_pass http://127.0.0.1:8080; }"]
    if raw.count(anchor) != 1 or any(raw.count(row) > 1 for row in additions):
        raise RuntimeError("GATEWAY_STATE_REQUIRES_RECONCILIATION")
    missing = [row for row in additions if row not in raw]
    if not missing:
        print(json.dumps({"status": "HEALTH_ROUTES_ALREADY_CONFIGURED"})); return
    backup = Path("/root/.config/intelligentai") / ("gateway-before-canonical-" + hashlib.sha256(raw).hexdigest() + ".conf")
    if backup.exists():
        if backup.is_symlink() or backup.read_bytes() != raw:
            raise RuntimeError("GATEWAY_BACKUP_MISMATCH")
    else:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    modified = raw.replace(b"        location = /health/canonical { proxy_pass http://127.0.0.1:8080; }\n", b"").replace(anchor, anchor + b"\n        " + b"\n        ".join(missing))
    temporary = config.with_name(".nginx-canonical.conf")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(modified)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, config)
    check = subprocess.run(["nginx", "-t", "-c", str(config)], capture_output=True)
    if check.returncode:
        config.write_bytes(raw)
        raise RuntimeError("NGINX_CONFIGURATION_INVALID_RESTORED")
    reload = subprocess.run(["nginx", "-s", "reload", "-c", str(config)], capture_output=True)
    if reload.returncode:
        raise RuntimeError("NGINX_RELOAD_UNCONFIRMED")
    print(json.dumps({"status": "CANONICAL_HEALTH_ROUTE_ENABLED",
        "beforeSha256": hashlib.sha256(raw).hexdigest(),
        "sha256": hashlib.sha256(modified).hexdigest(), "ttsChanged": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "GATEWAY_REQUIRES_RECONCILIATION", "errorType": type(error).__name__}))
        sys.exit(1)
