"""Read-only export of pinned small source files, not models/media/env or inference."""
import base64
import hashlib
import json
from pathlib import Path

ROOTS = {
    'core': Path('/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda'),
    'llm': Path('/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1'),
}
PINS = {
    'core': ('release_manifest.json', '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2'),
    'llm': ('contracts/llm/dependency_lock.json', 'd8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01'),
}


def main():
    files = {}
    def add(kind, relative, expected):
        root = ROOTS[kind].resolve(strict=True)
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('FROZEN_FILE_SCOPE')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('FROZEN_FILE_HASH')
        files['frozen/' + kind + '/' + relative] = dict(sha256=expected, base64=base64.b64encode(raw).decode())
        return json.loads(raw) if relative.endswith('.json') else None
    for kind, (relative, expected) in PINS.items():
        manifest = add(kind, relative, expected)
        for name, digest in manifest['file_sha256' if kind == 'core' else 'files'].items():
            add(kind, name, digest)
    add('llm', 'contracts/llm/llm_contract_manifest.json',
        '1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73')
    print(json.dumps(files, separators=(',', ':')))


if __name__ == '__main__':
    main()
