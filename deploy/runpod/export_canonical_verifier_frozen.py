"""Read-only export of pinned small source files, not models/media/env or inference."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

ROOTS = {
    'core': Path('/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda'),
    'llm': Path('/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1'),
    'canonical_h5_20261003': Path('/workspace/intelligentai/validation/canonical_h5_20261003'),
    'canonical_scored_core_20261003_v1': Path('/workspace/intelligentai/releases/canonical_scored_core_20261003_v1'),
    'canonical_scored_h5_20261003_v1': Path('/workspace/intelligentai/validation/canonical_scored_h5_20261003_v1'),
    'canonical_scored_core_20261003_v2': Path('/workspace/intelligentai/releases/canonical_scored_core_20261003_v2'),
    'canonical_scored_h5_20261003_v2': Path('/workspace/intelligentai/validation/canonical_scored_h5_20261003_v2'),
}
PINS = {
    'core': ('release_manifest.json', '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2'),
    'llm': ('contracts/llm/dependency_lock.json', 'd8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01'),
    'canonical_h5_20261003': ('contracts/llm/dependency_lock.json', '93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5'),
    'canonical_scored_core_20261003_v1': ('release_manifest.json', '6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776'),
    'canonical_scored_h5_20261003_v1': ('contracts/llm/dependency_lock.json', '17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d'),
    'canonical_scored_core_20261003_v2': ('release_manifest.json', '08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80'),
    'canonical_scored_h5_20261003_v2': ('contracts/llm/dependency_lock.json', 'b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989'),
}
MANIFESTS = {
    'llm': '1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73',
    'canonical_h5_20261003': '9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45',
    'canonical_scored_h5_20261003_v1': '820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6',
    'canonical_scored_h5_20261003_v2': '4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-root', type=Path, help='Local frozen directory containing all five pinned releases')
    parser.add_argument('--app-root', type=Path, help='Include the complete offline verifier application closure')
    args = parser.parse_args()
    roots = {kind: args.frozen_root / kind if args.frozen_root else root for kind, root in ROOTS.items()}
    files = {}
    def add(kind, relative, expected):
        root = roots[kind].resolve(strict=True)
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
        for name, digest in manifest['file_sha256' if relative == 'release_manifest.json' else 'files'].items():
            add(kind, name, digest)
    for kind, digest in MANIFESTS.items():
        add(kind, 'contracts/llm/llm_contract_manifest.json', digest)
    if args.app_root:
        from stage_backend_verifier_bundle import REQUIRED_APP_FILES
        root = args.app_root.resolve(strict=True)
        for name in sorted(REQUIRED_APP_FILES):
            path = (root / name.removeprefix('app/')).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('APP_FILE_SCOPE')
            raw = path.read_bytes()
            files[name] = dict(sha256=hashlib.sha256(raw).hexdigest(), base64=base64.b64encode(raw).decode())
    print(json.dumps(files, separators=(',', ':')))


if __name__ == '__main__':
    main()
