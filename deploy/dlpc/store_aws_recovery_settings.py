"""Keep a dedicated recovery source on AWS; stdin only, never a model archive.

The existing DLPC reverse-forward key cannot read this directory. Recovery is
operator mediated until a platform-supported secret injection path is installed.
"""
import json
import os
from pathlib import Path
import re
import socket
import sys


ROOT = Path('/root/.config/intelligentai/dlpc-recovery-20261006')


def main():
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT_REQUIRED')
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError('SETTINGS_TOO_LARGE')
    document = json.loads(raw)
    if set(document) != {'analysis', 'ttsToken'} or not isinstance(document['analysis'], dict):
        raise ValueError('SETTINGS_SHAPE')
    settings = document['analysis']
    for key, value in settings.items():
        if (not re.fullmatch('[A-Z][A-Z0-9_]*', key)
                or (not key.startswith(('AI_ANALYSIS_', 'AI_CANONICAL_', 'AWS_'))
                    and key not in {'OPENAI_API_KEY', 'VOICE_COACH_BUILD_REVISION'})
                or not isinstance(value, str) or any(c in value for c in '\r\n\x00')):
            raise ValueError('SETTINGS_ROLE_OR_VALUE')
    token = document['ttsToken']
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ValueError('TTS_TOKEN_SHAPE')
    # Restore diagnostically. Operator gates must be satisfied again at cutover.
    settings.update(AI_CANONICAL_BOOTSTRAP_READY='false',
                    AI_CANONICAL_HANDOFF_ENABLED='false', AI_CANONICAL_ADMISSION_ENABLED='false')
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, content in (
        ('analysis.env', ''.join(k + '=' + v + '\n' for k, v in sorted(settings.items()))),
        ('tts-token', token + '\n'),
    ):
        with (ROOT / name).open('x') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        (ROOT / name).chmod(0o600)
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    print('AWS recovery settings stored root-only; no values displayed; admission disabled')


if __name__ == '__main__':
    main()
