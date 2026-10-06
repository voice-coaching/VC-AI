"""Stage Backend TTS identity settings privately; never restart or activate."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

SOURCE = Path('/etc/alpha-backend.env')
DEST = Path('/root/.config/intelligentai/dlpc-tts-candidate-20261006')
REVISION = 'melo-ko-practice-dlpc-r1'
FINGERPRINT = 'ace97bc52df31f304513bc5f18177669723fe996f5cd19235691a5449d9f3766'


def main():
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT_REQUIRED')
    document = json.loads(sys.stdin.buffer.read(16385))
    manifest = document['manifest']
    fingerprint = hashlib.sha256(json.dumps(manifest, ensure_ascii=True,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if fingerprint != FINGERPRINT or manifest['revision'] != REVISION:
        raise ValueError('TTS_IDENTITY_CHANGED')
    info = SOURCE.lstat()
    if SOURCE.is_symlink() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_BACKEND_ENV_REQUIRED')
    raw = SOURCE.read_bytes()
    lines = raw.decode().splitlines(keepends=True)
    replacements = {
        'EXAMPLE_TTS_REVISION': ('melo-ko-practice-r1', REVISION),
        'EXAMPLE_TTS_FINGERPRINT': (
            '9474d12bafea63f2344e0e2e48eccbf57a512a9be717832248eab6397f8f7f0b', FINGERPRINT),
    }
    for key, (old, new) in replacements.items():
        matches = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if len(matches) != 1 or lines[matches[0]].strip().split('=', 1)[1].strip('"') != old:
            raise ValueError('BACKEND_TTS_BASE_CHANGED')
        lines[matches[0]] = key + '=' + new + '\n'
    candidate = ''.join(lines).encode()
    os.umask(0o077)
    DEST.mkdir(mode=0o700, exist_ok=False)
    for name, data in (('backend.env', candidate), ('synthesis-identity.json',
            json.dumps(document, sort_keys=True).encode())):
        with (DEST / name).open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    receipt = dict(sourceSha256=hashlib.sha256(raw).hexdigest(),
        candidateSha256=hashlib.sha256(candidate).hexdigest(), revision=REVISION,
        fingerprint=FINGERPRINT, activated=False, qualityVerified=False)
    (DEST / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True))
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
