"""Separate API gateway and TTS supervisor; never owns the analysis process."""
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time

ROOT = Path('/workspace/intelligentai/deployment/api-gateway')
TTS = Path('/workspace/voice-coaching-tts')
RUN = Path('/run/intelligentai-gateway')
TTS_PYTHON = TTS / 'venv/bin/python'


def main():
    os.umask(0o077)
    RUN.mkdir(mode=0o700, exist_ok=True)
    lock = (RUN / 'supervisor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    Path('/run/voice-coaching-tts').mkdir(mode=0o700, exist_ok=True)
    token = Path('/run/voice-coaching-tts/token')
    token.write_bytes((TTS / 'secrets/token').read_bytes())
    token.chmod(0o600)
    for name in ('body', 'proxy'):
        path = RUN / name
        path.mkdir(mode=0o700, exist_ok=True)
        # nginx workers need traversal and their own request buffers.
        os.chown(path, 65534, 65534)
    os.chmod(RUN, 0o711)
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LD_LIBRARY_PATH') if key in os.environ}
    env.update(HF_HOME=str(TTS / 'hf'), NLTK_DATA=str(TTS / 'nltk'),
               HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', OMP_NUM_THREADS='2',
               TTS_TOKEN_FILE=str(token), TTS_DEVICE='cuda:0',
               PYTHONPATH=str(TTS / 'MeloTTS'), PYTHONDONTWRITEBYTECODE='1')
    definitions = {
        'gateway': (['/usr/sbin/nginx', '-c', str(ROOT / 'nginx.conf'), '-g', 'daemon off;'], ROOT),
        'tts': ([str(TTS_PYTHON), '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1',
                 '--port', '8083', '--no-access-log', '--log-level', 'warning'], TTS / 'service'),
    }
    children, retries, deadlines = {}, {}, {}
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            now = time.monotonic()
            for name, (command, cwd) in definitions.items():
                log_path = RUN / (name + '.log')
                if log_path.exists() and log_path.stat().st_size > 10 * 1024 * 1024:
                    shutil.copyfile(log_path, RUN / (name + '.log.1'))
                    # Preserve the live child's open log descriptor.
                    with log_path.open('r+b') as log:
                        log.truncate(0)
                process = children.get(name)
                if process is not None and process.poll() is None:
                    continue
                if process is not None:
                    # Reap/terminate orphaned model children after API crashes.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    children.pop(name)
                    retries[name] = retries.get(name, 0) + 1
                    deadlines[name] = now + min(60, 5 * 2 ** min(retries[name], 4))
                if now < deadlines.get(name, 0):
                    continue
                try:
                    with log_path.open('ab', buffering=0) as log:
                        children[name] = subprocess.Popen(command, cwd=cwd, env=env,
                                                          stdout=log, stderr=log, start_new_session=True)
                except OSError:
                    deadlines[name] = now + 30
                    retries[name] = retries.get(name, 0) + 1
            state = {'pid': os.getpid(), 'updatedAtEpoch': time.time(), 'services': {
                name: {'pid': children[name].pid if name in children else None,
                       'running': name in children and children[name].poll() is None,
                       'restarts': retries.get(name, 0)} for name in definitions}}
            temp = RUN / 'state.tmp'
            temp.write_text(json.dumps(state))
            temp.replace(RUN / 'state.json')
            time.sleep(1)
    finally:
        for process in children.values():
            if process.poll() is None:
                process.terminate()
        end = time.monotonic() + 35
        for process in children.values():
            try:
                process.wait(timeout=max(0, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == '__main__':
    main()
