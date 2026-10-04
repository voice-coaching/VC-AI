"""Read-only inventory. stdin is build_canonical_overlay.py --list-paths JSON."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

APP = Path('/opt/intelligentai/app')
OVERLAY = Path('/workspace/intelligentai/deployment/int8-overlay')


def main():
    requested = json.load(sys.stdin)
    if not isinstance(requested, list) or len(requested) > 200 or len(set(requested)) != len(requested):
        raise ValueError('source_list_invalid')
    manifest = (OVERLAY / 'overlay-manifest.json').read_bytes()
    files = {}
    for name in requested:
        if (not isinstance(name, str) or PurePosixPath(name).is_absolute()
                or '..' in PurePosixPath(name).parts
                or not name.startswith(('src/voice_coach/backend_analysis/', 'deploy/runpod/', 'docs/contracts/'))
                or not name.endswith(('.py', '.schema.json', 'canonical-requirements.lock'))):
            raise ValueError('source_path_invalid')
        digests = []
        for root in (APP, OVERLAY):
            target = root / name
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError('source_symlink')
            digests.append(hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None)
        if digests[0] != digests[1]: raise ValueError('app_overlay_drift')
        files[name] = digests[0]
    print(json.dumps(dict(baseOverlaySha256=hashlib.sha256(manifest).hexdigest(),
        baseRevision=json.loads(manifest)['sourceRevision'], files=files), sort_keys=True))


if __name__ == '__main__':
    main()
