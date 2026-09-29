"""Rebuild the existing pinned TTS environment on persistent storage."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from configure_korean import configure


REMOVED_PACKAGES = {'fugashi', 'jaconv', 'mecab-python3', 'pykakasi', 'unidic', 'unidic-lite'}


def main():
    os.umask(0o077)
    root = Path('/workspace/voice-coaching-tts')
    manifest = json.loads((root / 'manifest.json').read_text())
    for file, field in [('requirements.lock', 'dependenciesSha256'), ('service/server.py', 'serviceSha256')]:
        if hashlib.sha256((root / file).read_bytes()).hexdigest() != manifest[field]:
            raise RuntimeError('TTS_SOURCE_HASH_MISMATCH')
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
    env['UV_CACHE_DIR'] = str(root / 'uv-cache')
    python = root / 'venv/bin/python'
    if not python.exists():
        subprocess.run(['uv', 'venv', '--python', '/usr/bin/python3.10', str(root / 'venv')], env=env, check=True)
    # Melo is imported directly from the verified deployment source; do not run
    # its editable-install hooks or download new language/model assets.
    requirements = root / 'requirements.gateway.lock'
    requirements.write_text('\n'.join(line for line in (root / 'requirements.lock').read_text().splitlines()
                                      if not line.startswith('-e ')
                                      and line.split('==', 1)[0].lower() not in REMOVED_PACKAGES) + '\n')
    configure(root)
    old_link = root / 'venv/lib/python3.10/site-packages/unidic/dicdir'
    if old_link.is_symlink():
        if os.readlink(old_link) != '../unidic_lite/dicdir':
            raise RuntimeError('UNEXPECTED_LEGACY_DICTIONARY_LINK')
        old_link.unlink()
    subprocess.run(['uv', 'pip', 'sync', '--python', str(python), str(requirements),
                    '--extra-index-url', 'https://download.pytorch.org/whl/cu121',
                    '--index-strategy', 'unsafe-best-match'], env=env, check=True)
    (root / 'gateway-environment.json').write_text(json.dumps({
        'requirementsSha256': manifest['dependenciesSha256'], 'python': manifest['python'],
        'sourceServiceSha256': manifest['serviceSha256'],
        'runtimeRequirementsSha256': hashlib.sha256(requirements.read_bytes()).hexdigest(),
        'language': 'KR'}))


if __name__ == '__main__':
    main()
