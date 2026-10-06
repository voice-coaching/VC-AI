"""DLPC service launcher: explicit role secrets, private listener, no Pod boot."""
import argparse
import os
from pathlib import Path
import re
import stat

ROOT = Path('/opt/intelligentai')
STATE = Path('/run/intelligentai')
UID = GID = 10001


def private_settings(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 131072):
            raise ValueError('DLPC_PRIVATE_CONFIG_PERMISSIONS')
        raw = stream.read().decode('utf-8')
    result = {}
    for line in raw.splitlines():
        if not line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        if not re.fullmatch('[A-Z][A-Z0-9_]*', key) or key in result or '\x00' in value:
            raise ValueError('DLPC_PRIVATE_CONFIG_INVALID')
        # Reuse literal dotenv values. Never evaluate shell syntax.
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=('analysis', 'tts'))
    parser.add_argument('--admit', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('DLPC_LAUNCH_REQUIRES_ROOT')
    os.umask(0o077)
    env = dict(PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8', LC_ALL='C.UTF-8',
               PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1', HF_HUB_OFFLINE='1',
               TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
    if args.role == 'analysis':
        settings = private_settings(Path('/etc/intelligentai/analysis.env'))
        allowed = ('AI_ANALYSIS_', 'AI_CANONICAL_', 'AWS_')
        env.update({k: v for k, v in settings.items() if k.startswith(allowed)
                    or k in {'OPENAI_API_KEY', 'VOICE_COACH_BUILD_REVISION'}})
        env.update(PYTHONPATH=str(ROOT / 'app/src') + ':' + str(ROOT / 'app/seungun/src') + ':' + str(ROOT / 'http-python'),
                   VOICE_COACH_REPOSITORY_ROOT=str(ROOT / 'app'), HOME=str(STATE / 'worker'),
                   TMPDIR=str(STATE / 'worker/tmp'), MPLCONFIGDIR=str(STATE / 'worker/tmp/matplotlib'),
                   HF_HOME=str(STATE / 'worker/tmp/huggingface'), AWS_EC2_METADATA_DISABLED='true',
                   AI_ANALYSIS_RESOURCE_PROFILE='dlpc-4cpu', AI_CANONICAL_STANDBY_RESERVE_BYTES='0',
                   AI_CANONICAL_STANDBY_MIN_FREE_BYTES='0')
        if not args.admit:
            # Staging must not restore/claim another host's production journal.
            env.update(AI_CANONICAL_BOOTSTRAP_READY='false', AI_CANONICAL_HANDOFF_ENABLED='false')
        command = [str(ROOT / 'envs/scorer/bin/python'), '-m', 'uvicorn',
                   'voice_coach.backend_analysis.http_api:create_app', '--factory',
                   '--host', '127.0.0.1', '--port', '8080', '--workers', '1',
                   '--no-access-log', '--no-proxy-headers', '--log-level', 'warning']
        cwd = ROOT / 'app'
    else:
        tts = Path('/workspace/voice-coaching-tts')
        env.update(PYTHONPATH=str(tts / 'MeloTTS'), HF_HOME=str(tts / 'hf'),
                   NLTK_DATA=str(tts / 'nltk'), TTS_TOKEN_FILE='/run/voice-coaching-tts/token',
                   TTS_DEVICE='cuda:0', HOME=str(STATE / 'worker'))
        command = [str(tts / 'venv/bin/python'), '-m', 'uvicorn', 'server:app',
                   '--host', '127.0.0.1', '--port', '8083', '--workers', '1',
                   '--no-access-log', '--no-proxy-headers', '--log-level', 'warning']
        cwd = tts / 'service'
    os.chdir(cwd)
    os.setgroups([GID])
    os.setgid(GID)
    os.setuid(UID)
    os.execve(command[0], command, env)


if __name__ == '__main__':
    main()
