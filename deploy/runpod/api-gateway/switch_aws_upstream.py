"""Guarded one-time migration of the existing AWS AI nginx virtual host."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


TARGET = Path('/etc/nginx/conf.d/ai.voice-coaching.site.conf')
EXPECTED = '25020daf013fbdd533f56ea4f8bbeb9f7c13b0f0687eeb3f3c109e50b7565f18'
HOST = 's8hb5b0l5k4qlx-8082.proxy.runpod.net'


def replace_atomic(data, metadata):
    temporary = TARGET.with_name(TARGET.name + '.gateway-tmp')
    with temporary.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, metadata.st_mode & 0o777)
    os.chown(temporary, metadata.st_uid, metadata.st_gid)
    os.replace(temporary, TARGET)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    original = TARGET.read_bytes()
    if hashlib.sha256(original).hexdigest() != EXPECTED:
        raise RuntimeError('NGINX_SOURCE_DRIFT_REINSPECT_BEFORE_APPLY')
    source = original.decode()
    old = '        proxy_pass http://127.0.0.1:18082;'
    if source.count(old) != 1 or source.count('s8hb5b0l5k4qlx-8080.proxy.runpod.net') != 3:
        raise RuntimeError('UNEXPECTED_UPSTREAM_LAYOUT')
    replacement = '\n'.join([
        '        proxy_pass https://' + HOST + ';',
        '        proxy_http_version 1.1;',
        '        proxy_set_header Host ' + HOST + ';',
        '        proxy_set_header Authorization $http_authorization;',
        '        proxy_ssl_server_name on;',
        '        proxy_ssl_name ' + HOST + ';',
        '        proxy_ssl_verify on;',
        '        proxy_ssl_trusted_certificate /etc/pki/tls/certs/ca-bundle.crt;',
        '        proxy_ssl_verify_depth 5;',
    ])
    updated = source.replace(old, replacement)
    updated = updated.replace('s8hb5b0l5k4qlx-8080.proxy.runpod.net', HOST)
    updated = updated.replace('        proxy_read_timeout 25s;', '        proxy_read_timeout 35s;')
    # Explicit pass-through in the analysis location as well; no token is stored.
    marker = '    location / {\n'
    if updated.count(marker) != 1:
        raise RuntimeError('UNEXPECTED_DEFAULT_LOCATION')
    updated = updated.replace(marker, marker + '        proxy_set_header Authorization $http_authorization;\n')
    data = updated.encode()
    result = {'oldSha256': EXPECTED, 'newSha256': hashlib.sha256(data).hexdigest(), 'upstream': HOST}
    if not args.apply:
        print(json.dumps(dict(result, applied=False)))
        return
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED')
    if subprocess.run(['nginx', '-t'], capture_output=True).returncode:
        raise RuntimeError('EXISTING_NGINX_CONFIG_INVALID')
    metadata = TARGET.stat()
    parent = Path('/var/backups/voice-coaching-gateway')
    parent.mkdir(mode=0o700, exist_ok=True)
    backup = parent / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(mode=0o700)
    saved = backup / TARGET.name
    shutil.copy2(TARGET, saved)
    saved.chmod(0o600)
    replace_atomic(data, metadata)
    valid = subprocess.run(['nginx', '-t'], capture_output=True).returncode == 0
    reloaded = valid and subprocess.run(['systemctl', 'reload', 'nginx'], capture_output=True).returncode == 0
    if not reloaded:
        replace_atomic(original, metadata)
        subprocess.run(['systemctl', 'reload', 'nginx'], capture_output=True)
        raise RuntimeError('NGINX_MIGRATION_FAILED_ORIGINAL_RESTORED_CHECK_SERVICE')
    print(json.dumps(dict(result, applied=True, backup=str(saved))))


if __name__ == '__main__':
    main()
