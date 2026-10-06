"""Receive role secrets over SSH stdin; no values in argv, logs or archives."""
import json
import os
from pathlib import Path
import re
import sys


def main():
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED_FOR_PRIVATE_SETTINGS')
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError('PRIVATE_SETTINGS_TOO_LARGE')
    body = json.loads(raw)
    if set(body) != {'analysis', 'ttsToken'}:
        raise ValueError('PRIVATE_SETTINGS_SHAPE')
    values = body['analysis']
    if not isinstance(values, dict) or not values or not isinstance(body['ttsToken'], str):
        raise ValueError('PRIVATE_SETTINGS_SHAPE')
    for key, value in values.items():
        if not isinstance(key, str) or not re.fullmatch('[A-Z][A-Z0-9_]*', key):
            raise ValueError('PRIVATE_SETTINGS_NAME')
        if (not key.startswith(('AI_ANALYSIS_', 'AI_CANONICAL_', 'AI_DIRECT_', 'AWS_'))
                and key not in {'OPENAI_API_KEY', 'VOICE_COACH_BUILD_REVISION'}):
            raise ValueError('PRIVATE_SETTINGS_ROLE')
        if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
            raise ValueError('PRIVATE_SETTINGS_VALUE')
    token = body['ttsToken']
    if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise ValueError('TTS_TOKEN_SHAPE')
    os.umask(0o077)
    root = Path('/etc/intelligentai')
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    files = {root / 'analysis.env': ''.join(k + '=' + v + '\n' for k, v in sorted(values.items())),
             root / 'tts-token': token + '\n'}
    if any(p.exists() or p.is_symlink() for p in files):
        raise ValueError('PRIVATE_SETTINGS_ALREADY_EXIST')
    for path, text in files.items():
        with path.open('x') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
    print('analysis and TTS private settings received; values not displayed')


if __name__ == '__main__':
    main()
