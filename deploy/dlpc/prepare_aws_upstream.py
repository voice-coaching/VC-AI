"""Prepare a private AWS nginx candidate; preserve TLS/auth and never reload."""
import argparse
import hashlib
import os
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.read_text()
    origin = 'https://s8hb5b0l5k4qlx-8082.proxy.runpod.net'
    if source.count('proxy_pass ' + origin + ';') != 2:
        raise ValueError('AWS_UPSTREAM_CONTEXT_CHANGED')
    match = re.search(r'    location = /v1/tts/synthesize \{.*?\n    \}', source, re.S)
    if match is None or source.count('    location / {') != 1:
        raise ValueError('AWS_LOCATION_CONTEXT_CHANGED')

    def private_upstream(block, port):
        block = block.replace('proxy_pass ' + origin + ';', f'proxy_pass http://127.0.0.1:{port};')
        block = re.sub(r'^\s*proxy_ssl_[^\n]+\n', '\n', block, flags=re.M)
        block = block.replace('proxy_set_header Host s8hb5b0l5k4qlx-8082.proxy.runpod.net;', 'proxy_set_header Host $host;')
        return block

    tts = private_upstream(match.group(), 18083)
    health = tts.replace('location = /v1/tts/synthesize {', 'location ~ ^/health/(live|ready)$ {')
    # Direct analysis carries a binary WAV body (20 MiB), metadata in a header,
    # and SSE results. It cannot inherit the 64 KiB internal JSON route budget.
    direct = '''    location ^~ /direct/ {
        client_max_body_size 20m;
        client_body_timeout 60s;
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Connection "";
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_cache off;
        proxy_next_upstream off;
        proxy_connect_timeout 15s;
        proxy_send_timeout 65s;
        proxy_read_timeout 930s;
    }'''
    if re.search(r'location\s+[^\n]*\/direct|large_client_header_buffers', source):
        raise ValueError('EXISTING_DIRECT_CONFIGURATION_REQUIRES_REVIEW')
    candidate = (source[:match.start()] + '    large_client_header_buffers 4 32k;\n\n'
                 + direct + '\n\n' + tts + '\n\n' + health + source[match.end():])
    candidate = private_upstream(candidate, 18080)
    candidate = candidate.replace('    location / {',
        '    location = /gateway/health {\n        default_type application/json;\n'
        '        return 200 \'{"status":"alive","service":"api-gateway"}\';\n    }\n\n'
        '    location / {\n        client_max_body_size 64k;\n        proxy_next_upstream off;', 1)
    if 'proxy.runpod.net' in candidate:
        raise ValueError('AWS_RUNPOD_DEPENDENCY_REMAINS')
    os.umask(0o077)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open('x') as stream:
        stream.write(candidate)
    args.destination.chmod(0o600)
    # Never print the candidate: existing authorization directives may be private.
    print('nginx candidate prepared; sourceSha256=' + hashlib.sha256(source.encode()).hexdigest())


if __name__ == '__main__':
    main()
