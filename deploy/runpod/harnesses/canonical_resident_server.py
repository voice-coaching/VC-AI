"""Private bounded resident core. No HTTP, credentials, GPT, or replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_release import prepare, APP_PY


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--socket', required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    generation = args.generation.resolve(strict=True)
    runtime, local = prepare(generation)
    mfa_socket = str(generation / 'mfa.sock')
    if len(mfa_socket.encode()) >= 104:
        raise ValueError('RESIDENT_SOCKET_PATH')
    host = None
    client = None
    server = None
    started = time.monotonic()
    try:
        with (generation / 'mfa.log').open('xb') as log:
            host = subprocess.Popen([APP_PY, '-B', '-u', str(ROOT / 'scripts/blackbox_host.py'),
                '--runtime', str(runtime), '--socket', mfa_socket,
                '--receipt', str(generation / 'host_ready.json')],
                cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        # Import and model preparation overlap the independent MFA bootstrap.
        sys.path.insert(0, str(ROOT / 'source/scripts'))
        import run_canonical_batch as batch
        from blackbox_client import BlackBoxMFAClient
        batch.checked_contract()
        native = batch.load_native()
        from korean_pronunciation_poc.final_pronunciation_artifacts import load_final_pronunciation_artifacts
        from korean_pronunciation_poc.final_pronunciation_detector import get_final_pronunciation_runtime
        config = json.loads((batch.RECOVERY / 'configs/candidate_alignment_config.json').read_bytes())
        final = config['final_pronunciation']
        get_final_pronunciation_runtime(load_final_pronunciation_artifacts(Path(final['artifact_root'])),
                                       device=final.get('device', 'cpu'))
        batch.G2pService()  # Corpus/dependency preparation, not an inference fixture.
        while not (generation / 'host_ready.json').exists():
            if host.poll() is not None or time.monotonic() - started > 300:
                raise RuntimeError('MFA_NOT_READY')
            time.sleep(.1)
        ready = json.loads((generation / 'host_ready.json').read_bytes())
        if ready.get('status') != 'READY' or not ready['startup']['ready']['direct_candidate']:
            raise RuntimeError('MFA_NOT_READY')
        client = BlackBoxMFAClient(mfa_socket)
        resident = dict(client=client, native=native, version=client.call('version'), startup=client.call('start'))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(args.socket)
        server.listen(1)
        manifest = digest(ROOT / 'release_manifest.json')
        temporary = generation / 'ready.tmp'
        temporary.write_text(json.dumps(dict(status='READY', coreManifestSha256=manifest)))
        temporary.replace(generation / 'ready.json')
        print(json.dumps(dict(event='canonical_cold_prepare', elapsedSeconds=time.monotonic()-started)), flush=True)
        seen = set()
        # Preserve the historical worker's three-request lifecycle bound. A new
        # generation starts only after its complete subtree has been reclaimed.
        for _ in range(3):
            conn, _ = server.accept()
            with conn:
                conn.settimeout(10)
                stream = conn.makefile('rwb', buffering=0)
                raw = stream.readline(4097)
                if len(raw) > 4096 or not raw.endswith(b'\n'):
                    raise ValueError('RESIDENT_REQUEST_SIZE')
                request = json.loads(raw)
                if set(request) != {'executionId', 'requestId', 'deadline', 'caseSha256', 'audioSha256'}:
                    raise ValueError('RESIDENT_REQUEST_SHAPE')
                execution, request_id = str(UUID(request['executionId'])), str(UUID(request['requestId']))
                if execution != request['executionId'] or request_id != request['requestId'] or execution in seen:
                    raise ValueError('RESIDENT_REQUEST_ID')
                seen.add(execution)
                if not time.monotonic() < request['deadline'] < time.monotonic() + 3600:
                    raise ValueError('RESIDENT_DEADLINE')
                work = workspace / execution / 'core' / execution
                cases = work / 'admitted-case.json'
                if cases.resolve(strict=True) != cases or cases.stat().st_size > 65536 or digest(cases) != request['caseSha256']:
                    raise ValueError('RESIDENT_CASE_IDENTITY')
                cases_value = json.loads(cases.read_bytes())
                if len(cases_value) != 1 or cases_value[0]['id'] != request_id:
                    raise ValueError('RESIDENT_CASE_BINDING')
                audio = Path(cases_value[0]['audio'])
                if (audio.resolve(strict=True) != audio or not audio.is_relative_to(workspace / execution / 'media')
                        or digest(audio) != request['audioSha256']):
                    raise ValueError('RESIDENT_AUDIO_BINDING')
                output = work / 'release-output'
                output.mkdir(mode=0o700, exist_ok=False)
                batch.run_batch(cases_value, output / 'candidate', False, mfa_socket, str(runtime), resident=resident)
                if time.monotonic() >= request['deadline']:
                    raise ValueError('RESIDENT_DEADLINE')
                stream.write(json.dumps(dict(executionId=execution, status='DONE')).encode() + b'\n')
                stream.close()
        client.call('close')
    finally:
        if server: server.close()
        if client: client.close()
        if host and host.poll() is None:
            os.killpg(host.pid, signal.SIGTERM)
            try: host.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(host.pid, signal.SIGKILL)
                host.wait(timeout=10)
        # Subreaper owns all descendants, including historical setsid workers.
        for path in (generation / 'ready.json', Path(args.socket)):
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"event":"canonical_resident_failed","code":"CORE_UNAVAILABLE"}', flush=True)
        raise SystemExit(1)
