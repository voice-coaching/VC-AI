"""Offline acoustic execution with bounded lifetime and original evidence bytes."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

from assets import PACKAGE, digest, fetch, verify
from layout import APP_PY, MFA_PY, materialize, verify_source, write


def descendants() -> list[int]:
    """Return only this isolated launcher's descendant processes."""
    pairs = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
            pairs.append((int(directory.name), int(fields[1])))
        except (OSError, ValueError, IndexError):
            continue
    owned = {os.getpid()}
    while True:
        added = {pid for pid, parent in pairs if parent in owned} - owned
        if not added:
            return sorted(owned - {os.getpid()}, reverse=True)
        owned.update(added)


def cleanup() -> None:
    """Use pidfds to avoid signaling a reused PID; no inference retry occurs."""
    handles = []
    for pid in descendants():
        try:
            handle = os.pidfd_open(pid)
            # Recheck ancestry after opening; pidfd now pins the actual process.
            if pid in descendants():
                handles.append(handle)
            else:
                os.close(handle)
        except ProcessLookupError:
            continue
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for handle in handles:
                try:
                    signal.pidfd_send_signal(handle, sig)
                except ProcessLookupError:
                    pass
            if sig == signal.SIGTERM:
                time.sleep(1)
    finally:
        for handle in handles:
            os.close(handle)
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break


def analyze(args: argparse.Namespace, cache: Path) -> dict:
    if args.device != os.environ.get('SEUNGUN_IMAGE_DEVICE'):
        raise ValueError('IMAGE_DEVICE_PROFILE_MISMATCH')
    if verify(cache)['status'] != 'ready':
        raise ValueError('ASSETS_NOT_READY_RUN_PREPARE')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise ValueError('LINUX_PIDFD_REQUIRED')
    try:
        handle = os.pidfd_open(os.getpid())
        os.close(handle)
    except OSError as error:
        raise ValueError('LINUX_PIDFD_REQUIRED') from error
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise ValueError('SUBREAPER_UNAVAILABLE')
    if not args.text.strip() or len(args.text) > 500 or not 1 <= args.timeout <= 3600:
        raise ValueError('INVALID_SCRIPT_OR_TIMEOUT')
    if not args.audio.is_file() or args.audio.stat().st_size > 20 * 1024**2:
        raise ValueError('AUDIO_MISSING_OR_OVERSIZED')
    output = args.output.resolve(strict=True)
    if any(output.iterdir()):
        raise ValueError('OUTPUT_NOT_EMPTY')
    deadline = time.monotonic() + args.timeout
    core, identity = materialize(cache, args.device)
    work = Path('/runtime/jobs')
    work.mkdir(parents=True)
    generation = Path('/runtime/generation')
    generation.mkdir()
    execution, request_id = str(uuid4()), str(uuid4())
    media = work / execution / 'media'
    media.mkdir(parents=True)
    audio = media / ('source' + args.audio.suffix)
    # Bound streamed input instead of reading an untrusted file into memory.
    count = 0
    with args.audio.open('rb') as source, audio.open('xb') as target:
        while chunk := source.read(1024**2):
            count += len(chunk)
            if count > 20 * 1024**2:
                raise ValueError('AUDIO_OVERSIZED')
            target.write(chunk)
    case_root = work / execution / 'core' / execution
    case_root.mkdir(parents=True)
    cases = case_root / 'admitted-case.json'
    write(cases, [{'id': request_id, 'executionId': execution,
        'audio': str(audio), 'script': args.text}])
    log = (output / 'runtime.log').open('xb')
    process = None
    try:
        process = subprocess.Popen([str(APP_PY), '-B', '-u', str(core / 'scripts/run_resident.py'),
            '--workspace', str(work), '--generation', str(generation),
            '--socket', '/runtime/core.sock'], stdout=log, stderr=log)
        while not (generation / 'ready.json').exists():
            if process.poll() is not None:
                raise ValueError('ACOUSTIC_STARTUP_FAILED_SEE_RUNTIME_LOG')
            if time.monotonic() >= deadline:
                raise TimeoutError('ACOUSTIC_STARTUP_DEADLINE')
            time.sleep(.2)
        ready = json.loads((generation / 'ready.json').read_bytes())
        if ready.get('coreManifestSha256') != identity['localCoreManifestSha256']:
            raise ValueError('RESIDENT_IDENTITY_MISMATCH')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(.1, deadline - time.monotonic()))
            client.connect('/runtime/core.sock')
            request = {'executionId': execution, 'requestId': request_id, 'deadline': deadline,
                'caseSha256': digest(cases), 'audioSha256': digest(audio)}
            client.sendall(json.dumps(request).encode() + b'\n')
            response = bytearray()
            while not response.endswith(b'\n'):
                chunk = client.recv(4096)
                if not chunk or len(response) + len(chunk) > 65536:
                    raise ValueError('RESIDENT_RESPONSE_INVALID')
                response.extend(chunk)
            receipt = json.loads(response)
            if receipt != {'executionId': execution, 'status': 'DONE'}:
                raise ValueError('RESIDENT_RESPONSE_MISMATCH')
        raw = case_root / 'release-output/candidate/results' / (request_id + '.json')
        evidence = json.loads(raw.read_bytes())
        if evidence.get('source_audio_sha256') != digest(audio) or evidence.get('request_id') != request_id:
            raise ValueError('OUTPUT_ASSOCIATION_MISMATCH')
        # Preserve the actual acoustic result, including its original timing and indexes.
        (output / 'evidence.json').write_bytes(raw.read_bytes())
        prepared = case_root / 'release-output/candidate/prepared_cache' / (request_id + '.wav')
        if prepared.exists():
            if digest(prepared) != evidence.get('prepared_wav_sha256'):
                raise ValueError('PREPARED_AUDIO_MISMATCH')
            (output / 'prepared.wav').write_bytes(prepared.read_bytes())
        accepted = evidence.get('input_validation', {}).get('status') in ('ACCEPT', 'INCONCLUSIVE')
        status = 'COMPLETED' if accepted and evidence.get('core_result', {}).get('status') == 'ok' else 'ANALYSIS_REJECTED_OR_FAILED'
        result = {'schemaVersion': 'seungun-standalone-result.v1', 'status': status,
            'requestId': request_id, 'executionId': execution, 'sourceIdentity': identity,
            'sourceAudioSha256': digest(audio), 'scriptSha256': hashlib.sha256(args.text.encode()).hexdigest(),
            'evidenceSha256': digest(raw), 'inputValidation': evidence.get('input_validation'),
            'gptScoring': 'NOT_INCLUDED', 'coaching': 'NOT_INCLUDED',
            'phonemeAssessment': evidence.get('phonemeAssessment'),
            'assetManifestSha256': digest(PACKAGE / 'manifests/assets.json')}
        write(output / 'result.json', result)
        return result
    finally:
        cleanup()
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('fetch', 'doctor', 'analyze'))
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--text')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    try:
        cache = Path('/assets')
        if args.command == 'fetch':
            import fcntl
            with (cache / '.prepare.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = fetch(cache)
        elif args.command == 'doctor':
            verify_source()
            result = verify(cache)
            result['interpretersPresent'] = APP_PY.is_file() and MFA_PY.is_file()
            result['platformAcceptance'] = 'DEVELOPER_QA_PENDING'
            if not result['interpretersPresent']:
                result['status'] = 'not_ready'
        else:
            if args.audio is None or args.text is None or args.output is None:
                parser.error('analyze requires --audio, --text and --output')
            result = analyze(args, cache)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result['status'] in ('ready', 'COMPLETED') else 2
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # Never print a provider response or inherited environment.
        print(json.dumps({'status': 'failed', 'code': str(error) if isinstance(error, (ValueError, TimeoutError)) else type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
