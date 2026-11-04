"""Read-only export of pinned small source files, not models/media/env or inference."""
import argparse
import base64
import hashlib
import json
from pathlib import Path

ROOTS = {
    'canonical_dlpc_gop_announcer_20261007_v1_h5': Path('/workspace/intelligentai/validation/canonical_dlpc_gop_announcer_20261007_v1_h5'),
    'canonical_dlpc_gop_cuda_20261007_v2': Path('/workspace/intelligentai/releases/canonical_dlpc_gop_cuda_20261007_v2'),
    'canonical_dlpc_gop_cuda_20261007_v2_h5': Path('/workspace/intelligentai/validation/canonical_dlpc_gop_cuda_20261007_v2_h5'),
    'canonical_seungun_core_20261006_v1': Path('/workspace/intelligentai/releases/canonical_seungun_core_20261006_v1'),
    'canonical_seungun_gop_core_20261006_v1': Path('/workspace/intelligentai/releases/canonical_seungun_gop_core_20261006_v1'),
    'canonical_seungun_gpt_20261006_v1': Path('/workspace/intelligentai/validation/canonical_seungun_gpt_20261006_v1'),
    'canonical_seungun_gop_gpt_20261006_v1': Path('/workspace/intelligentai/validation/canonical_seungun_gop_gpt_20261006_v1'),
    'canonical_native_core_20261005_v4': Path('/workspace/intelligentai/releases/canonical_native_core_20261005_v4'),
    'canonical_native_h5_20261005_v4': Path('/workspace/intelligentai/validation/canonical_native_h5_20261005_v4'),
    'canonical_native_core_20261004_v1': Path('/workspace/intelligentai/releases/canonical_native_core_20261004_v1'),
    'canonical_native_h5_20261004_v1': Path('/workspace/intelligentai/validation/canonical_native_h5_20261004_v1'),
    'canonical_resident_core_20261004_v1': Path('/workspace/intelligentai/releases/canonical_resident_core_20261004_v1'),
    'canonical_resident_h5_20261004_v1': Path('/workspace/intelligentai/validation/canonical_resident_h5_20261004_v1'),
    'canonical_scored_h5_20261003_v3': Path('/workspace/intelligentai/validation/canonical_scored_h5_20261003_v3'),
    'core': Path('/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda'),
    'llm': Path('/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1'),
    'canonical_h5_20261003': Path('/workspace/intelligentai/validation/canonical_h5_20261003'),
    'canonical_scored_core_20261003_v1': Path('/workspace/intelligentai/releases/canonical_scored_core_20261003_v1'),
    'canonical_scored_h5_20261003_v1': Path('/workspace/intelligentai/validation/canonical_scored_h5_20261003_v1'),
    'canonical_scored_core_20261003_v2': Path('/workspace/intelligentai/releases/canonical_scored_core_20261003_v2'),
    'canonical_scored_h5_20261003_v2': Path('/workspace/intelligentai/validation/canonical_scored_h5_20261003_v2'),
}
PINS = {
    'canonical_dlpc_gop_announcer_20261007_v1_h5': ('contracts/llm/dependency_lock.json', 'b605c3b9ba06bbf18fc126770b18de3d2da0d3f70c08e04873f17bc735b948bc'),
    'canonical_dlpc_gop_cuda_20261007_v2': ('release_manifest.json', 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'),
    'canonical_dlpc_gop_cuda_20261007_v2_h5': ('contracts/llm/dependency_lock.json', '3e2e81b43b1c1298266df428bc01ef0761d37f413073824cae2000623ea2eb3c'),
    'canonical_seungun_core_20261006_v1': ('release_manifest.json', '20acb7275aa06d0be4aca1a567420ea603c0eec084e50c651c96501903034f1b'),
    'canonical_seungun_gop_core_20261006_v1': ('release_manifest.json', '818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e'),
    'canonical_seungun_gpt_20261006_v1': ('contracts/llm/dependency_lock.json', '3f4ce0a44ae75192fe7fea3b03b879c21ab0b2be7cc0a620522c60c2155b674b'),
    'canonical_seungun_gop_gpt_20261006_v1': ('contracts/llm/dependency_lock.json', '74784bf0c7ba99628ed2ff8db19157c63d22a2a1cd66c58000ad2e811dc93160'),
    'canonical_native_core_20261005_v4': ('release_manifest.json', 'f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841'),
    'canonical_native_h5_20261005_v4': ('contracts/llm/dependency_lock.json', '1b8cb378b889817e14bf06be5065b04016589f6b74dcd5501776dde758f38259'),
    'canonical_native_core_20261004_v1': ('release_manifest.json', '9e2a2107db7d30fe8910c8984687974d85b95120760e2bcce53d57640a5d5a32'),
    'canonical_native_h5_20261004_v1': ('contracts/llm/dependency_lock.json', '798b47b6f5355b120141a7fa730350739298082549036233ea5804226dbad80c'),
    'canonical_resident_core_20261004_v1': ('release_manifest.json', 'ce16f635da012237b5316b36161d5eeff9432279cd08dbbe7967f5ab7ace7f7e'),
    'canonical_resident_h5_20261004_v1': ('contracts/llm/dependency_lock.json', '1945dcf2fad7fc97621830abed34353c2d372d8224cc9ad11a9c422eabfdf9f4'),
    'canonical_scored_h5_20261003_v3': ('contracts/llm/dependency_lock.json', 'dc3db9cc03a3355ddeb5d5819cd6fd46726935c8ca9b830f513e37163c89f4d8'),
    'core': ('release_manifest.json', '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2'),
    'llm': ('contracts/llm/dependency_lock.json', 'd8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01'),
    'canonical_h5_20261003': ('contracts/llm/dependency_lock.json', '93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5'),
    'canonical_scored_core_20261003_v1': ('release_manifest.json', '6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776'),
    'canonical_scored_h5_20261003_v1': ('contracts/llm/dependency_lock.json', '17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d'),
    'canonical_scored_core_20261003_v2': ('release_manifest.json', '08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80'),
    'canonical_scored_h5_20261003_v2': ('contracts/llm/dependency_lock.json', 'b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989'),
}
MANIFESTS = {
    'canonical_dlpc_gop_announcer_20261007_v1_h5': 'def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77',
    'canonical_dlpc_gop_cuda_20261007_v2_h5': '1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7',
    'canonical_seungun_gpt_20261006_v1': '6a3bf7c872e718086cb9add4d48412c825188310cdc9eb394089301dd784ff57',
    'canonical_seungun_gop_gpt_20261006_v1': 'f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5',
    'canonical_native_h5_20261005_v4': 'efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428',
    'canonical_native_h5_20261004_v1': '358365399f7445ab0a77797ce46619979955503c4faac8185724e2225aa5205c',
    'canonical_resident_h5_20261004_v1': '885a2f0a67494dc3bebed28962222bfe95a0143292fe2de04194b84ea8beadc5',
    'canonical_scored_h5_20261003_v3': 'e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3',
    'llm': '1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73',
    'canonical_h5_20261003': '9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45',
    'canonical_scored_h5_20261003_v1': '820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6',
    'canonical_scored_h5_20261003_v2': '4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-root', type=Path, help='Local frozen directory containing all pinned releases')
    parser.add_argument('--app-root', type=Path, help='Include the complete offline verifier application closure')
    args = parser.parse_args()
    roots = {kind: args.frozen_root / kind if args.frozen_root else root for kind, root in ROOTS.items()}
    omitted = set()
    for optional in (
        {'canonical_native_core_20261005_v4', 'canonical_native_h5_20261005_v4'},
        {'canonical_dlpc_gop_cuda_20261007_v2_h5', 'canonical_dlpc_gop_cuda_20261007_v2'},
        {'canonical_dlpc_gop_announcer_20261007_v1_h5'},
    ):
        present = {kind for kind in optional if roots[kind].exists()}
        if present and present != optional:
            raise ValueError('NATIVE_PAIR_INCOMPLETE')
        omitted.update(optional - present)
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
        if kind in omitted:
            continue
        manifest = add(kind, relative, expected)
        for name, digest in manifest['file_sha256' if relative == 'release_manifest.json' else 'files'].items():
            add(kind, name, digest)
    for kind, digest in MANIFESTS.items():
        if kind not in omitted:
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
