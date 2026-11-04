"""Install an additive, digest-addressed verifier release on AWS, never activate it.

Stdin: file map from reviewed local code and the pinned RunPod source exporter.
Contains source/schema only. No secret loading, symlink switch, env, service or DB change.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

BASE = Path('/opt/alpha-canonical/releases')
# The offline entry point bypasses package __init__ deliberately. Ship its full
# private dependency closure, including the extracted codec, as one new release.
REQUIRED_APP_FILES = frozenset({
    'app/deploy/runpod/canonical_verify.py',
    'app/src/voice_coach/direct_analysis/__init__.py',
    'app/src/voice_coach/direct_analysis/domain.py',
    *(f'app/src/voice_coach/backend_analysis/{name}.py' for name in (
        'canonical_generation', 'canonical_bridge', 'canonical_codec', 'canonical_media', 'canonical_harness',
        'canonical_result', 'canonical_audiovisual_result', 'canonical_settings', 'media_preparation', 'http_models', 'domain',
        'native_execution', 'native_phone_contract', 'native_phone_assessment', 'phone_rubric',
    )),
    *(f'app/src/voice_coach/visual_feedback/{name}.py' for name in (
        'canonical_verification', 'canonical_service', 'canonical_alignment', 'audiovisual_evidence',
        'reference_catalog', 'reference_distance', 'lip_geometry', 'feature_protocol', 'coaching_input', 'parallel_analysis',
    )),
    *(f'app/docs/contracts/{name}.schema.json' for name in (
        'runpod_result_v4', 'runpod_canonical_parent', 'runpod_canonical_input',
        'runpod_canonical_output', 'runpod_http_control_v1_2',
        'runpod_http_control_v1', 'runpod_analysis_request_v2',
        'runpod_result_v1', 'runpod_canonical_journal_v1',
        'native_phone_evidence', 'native_logits', 'runpod_analysis_request_v4', 'runpod_result_v6',
        'runpod_media_preparation_v1', 'runpod_canonical_handoff_v2', 'runpod_visual_coaching_input_v1',
    )),
})


def require(condition, code):
    if not condition:
        raise ValueError(code)


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ROOT_LINUX_REQUIRED')
    os.umask(0o022)
    raw = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
    require(0 < len(raw) <= 20 * 1024 * 1024, 'BUNDLE_SIZE')
    files = json.loads(raw)
    # The additive DLPC CUDA pair takes the full historical closure to 816 files.
    require(type(files) is dict and 90 <= len(files) <= 1300, 'BUNDLE_FILES')
    require(REQUIRED_APP_FILES.issubset(files), 'BUNDLE_APP_DEPENDENCIES_MISSING')
    decoded = {}
    for name, entry in files.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and all(p not in ('', '.', '..') for p in name.split('/'))
                and '\\' not in name and path.as_posix() == name
                and (name.startswith(('frozen/core/', 'frozen/llm/', 'frozen/canonical_h5_20261003/',
                     'frozen/canonical_scored_core_20261003_v1/', 'frozen/canonical_scored_h5_20261003_v1/',
                     'frozen/canonical_scored_h5_20261003_v3/', 'frozen/canonical_scored_core_20261003_v2/', 'frozen/canonical_scored_h5_20261003_v2/',
                     'frozen/canonical_resident_core_20261004_v1/', 'frozen/canonical_resident_h5_20261004_v1/',
                     'frozen/canonical_native_core_20261005_v4/', 'frozen/canonical_native_h5_20261005_v4/',
                     'frozen/canonical_seungun_core_20261006_v1/', 'frozen/canonical_seungun_gpt_20261006_v1/',
                     'frozen/canonical_dlpc_gop_cuda_20261007_v2/', 'frozen/canonical_dlpc_gop_cuda_20261007_v2_h5/',
                     'frozen/canonical_dlpc_gop_announcer_20261007_v1_h5/',
                     'frozen/canonical_dlpc_gop_announcer_20261007_v2_h5/',
                     'frozen/canonical_six_dimension_20261104_v1_h5/',
                     'frozen/canonical_dlpc_audiovisual_20261007_v2_h5/',
                     'frozen/canonical_dlpc_audiovisual_core_20261007_v2/',
                     'frozen/canonical_seungun_gop_core_20261006_v1/', 'frozen/canonical_seungun_gop_gpt_20261006_v1/',
                     'frozen/canonical_native_core_20261004_v1/', 'frozen/canonical_native_h5_20261004_v1/',
                     'app/src/voice_coach/', 'app/docs/contracts/'))
                     or name == 'app/deploy/runpod/canonical_verify.py')
                and not any(p == '.env' or p.startswith('.env.') for p in path.parts), 'BUNDLE_PATH')
        require(type(entry) is dict and set(entry) == {'sha256', 'base64'}, 'BUNDLE_ENTRY')
        content = base64.b64decode(entry['base64'], validate=True)
        require(len(content) <= 2 * 1024 * 1024 and hashlib.sha256(content).hexdigest() == entry['sha256'], 'BUNDLE_HASH')
        decoded[name] = content
    pins = {
        'frozen/canonical_native_core_20261004_v1/release_manifest.json': '9e2a2107db7d30fe8910c8984687974d85b95120760e2bcce53d57640a5d5a32',
        'frozen/canonical_native_h5_20261004_v1/contracts/llm/dependency_lock.json': '798b47b6f5355b120141a7fa730350739298082549036233ea5804226dbad80c',
        'frozen/canonical_native_h5_20261004_v1/contracts/llm/llm_contract_manifest.json': '358365399f7445ab0a77797ce46619979955503c4faac8185724e2225aa5205c',
        'frozen/canonical_resident_core_20261004_v1/release_manifest.json': 'ce16f635da012237b5316b36161d5eeff9432279cd08dbbe7967f5ab7ace7f7e',
        'frozen/canonical_resident_h5_20261004_v1/contracts/llm/dependency_lock.json': '1945dcf2fad7fc97621830abed34353c2d372d8224cc9ad11a9c422eabfdf9f4',
        'frozen/canonical_resident_h5_20261004_v1/contracts/llm/llm_contract_manifest.json': '885a2f0a67494dc3bebed28962222bfe95a0143292fe2de04194b84ea8beadc5',
        'frozen/canonical_scored_h5_20261003_v3/contracts/llm/dependency_lock.json': 'dc3db9cc03a3355ddeb5d5819cd6fd46726935c8ca9b830f513e37163c89f4d8',
        'frozen/canonical_scored_h5_20261003_v3/contracts/llm/llm_contract_manifest.json': 'e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3',
        'frozen/canonical_scored_core_20261003_v2/release_manifest.json': '08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80',
        'frozen/canonical_scored_h5_20261003_v2/contracts/llm/dependency_lock.json': 'b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989',
        'frozen/canonical_scored_h5_20261003_v2/contracts/llm/llm_contract_manifest.json': '4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf',
        'frozen/canonical_scored_core_20261003_v1/release_manifest.json': '6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776',
        'frozen/canonical_scored_h5_20261003_v1/contracts/llm/dependency_lock.json': '17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d',
        'frozen/canonical_scored_h5_20261003_v1/contracts/llm/llm_contract_manifest.json': '820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6',
        'frozen/core/release_manifest.json': '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2',
        'frozen/llm/contracts/llm/dependency_lock.json': 'd8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01',
        'frozen/llm/contracts/llm/llm_contract_manifest.json': '1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73',
        'frozen/canonical_h5_20261003/contracts/llm/dependency_lock.json': '93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5',
        'frozen/canonical_h5_20261003/contracts/llm/llm_contract_manifest.json': '9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45',
    }
    pins.update({
        'frozen/canonical_seungun_core_20261006_v1/release_manifest.json': '20acb7275aa06d0be4aca1a567420ea603c0eec084e50c651c96501903034f1b',
        'frozen/canonical_seungun_gop_core_20261006_v1/release_manifest.json': '818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e',
        'frozen/canonical_seungun_gpt_20261006_v1/contracts/llm/dependency_lock.json': '3f4ce0a44ae75192fe7fea3b03b879c21ab0b2be7cc0a620522c60c2155b674b',
        'frozen/canonical_seungun_gop_gpt_20261006_v1/contracts/llm/dependency_lock.json': '74784bf0c7ba99628ed2ff8db19157c63d22a2a1cd66c58000ad2e811dc93160',
        'frozen/canonical_seungun_gpt_20261006_v1/contracts/llm/llm_contract_manifest.json': '6a3bf7c872e718086cb9add4d48412c825188310cdc9eb394089301dd784ff57',
        'frozen/canonical_seungun_gop_gpt_20261006_v1/contracts/llm/llm_contract_manifest.json': 'f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5',
    })
    if any(name.startswith('frozen/canonical_dlpc_audiovisual_20261007_v2_h5/') for name in files):
        pins.update({
            'frozen/canonical_dlpc_audiovisual_core_20261007_v2/release_manifest.json': '4cc0cbacd41b5c6f2ac20e6e4840bc5406f4c7ab79ecf53702586eeb5874ecbc',
            'frozen/canonical_dlpc_audiovisual_20261007_v2_h5/contracts/llm/dependency_lock.json': '934729ee467a2eb030c244584c6fa5c3575ca9ed0a43348a59a486199c0f55e9',
            'frozen/canonical_dlpc_audiovisual_20261007_v2_h5/contracts/llm/llm_contract_manifest.json': 'd70372926d3406842b95c370b5ceb152480e7f0af83ba840028ec8d9ace2dc50',
        })
    native_v2 = {
        'frozen/canonical_native_core_20261005_v4/release_manifest.json': 'f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841',
        'frozen/canonical_native_h5_20261005_v4/contracts/llm/dependency_lock.json': '1b8cb378b889817e14bf06be5065b04016589f6b74dcd5501776dde758f38259',
        'frozen/canonical_native_h5_20261005_v4/contracts/llm/llm_contract_manifest.json': 'efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428',
    }
    if any(name.startswith(('frozen/canonical_native_core_20261005_v4/',
                            'frozen/canonical_native_h5_20261005_v4/')) for name in files):
        pins.update(native_v2)
    if any(name.startswith(('frozen/canonical_dlpc_cuda_20261005_v2/',
                            'frozen/canonical_dlpc_cuda_20261005_v2_h5/')) for name in files):
        pins.update({
            'frozen/canonical_dlpc_cuda_20261005_v2/release_manifest.json': '4d29ddf6da45d43de6ceb0b07b70cecaf291ae5bc92b700ece6b320a8e6c423d',
            'frozen/canonical_dlpc_cuda_20261005_v2_h5/contracts/llm/dependency_lock.json': 'fc48fc935df8c4fb90c0a9345651c047a320da220fe46c12e6e00d395a6c556d',
            'frozen/canonical_dlpc_cuda_20261005_v2_h5/contracts/llm/llm_contract_manifest.json': 'de8f66aa7688404f2f59fc17e67dd2c250cf746d58f9697bebd8fee87095f5f9',
        })
    if any(name.startswith('frozen/canonical_dlpc_gop_cuda_20261007_v2/') or name.startswith('frozen/canonical_dlpc_gop_cuda_20261007_v2_h5/') for name in files):
        pins.update({
            'frozen/canonical_dlpc_gop_cuda_20261007_v2/release_manifest.json': 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
            'frozen/canonical_dlpc_gop_cuda_20261007_v2_h5/contracts/llm/dependency_lock.json': '3e2e81b43b1c1298266df428bc01ef0761d37f413073824cae2000623ea2eb3c',
            'frozen/canonical_dlpc_gop_cuda_20261007_v2_h5/contracts/llm/llm_contract_manifest.json': '1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7',
        })
    if any(name.startswith('frozen/canonical_dlpc_gop_announcer_20261007_v1_h5/') for name in files):
        pins.update({
            'frozen/canonical_dlpc_gop_cuda_20261007_v2/release_manifest.json': 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
            'frozen/canonical_dlpc_gop_announcer_20261007_v1_h5/contracts/llm/dependency_lock.json': 'b605c3b9ba06bbf18fc126770b18de3d2da0d3f70c08e04873f17bc735b948bc',
            'frozen/canonical_dlpc_gop_announcer_20261007_v1_h5/contracts/llm/llm_contract_manifest.json': 'def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77',
        })
    if any(name.startswith('frozen/canonical_dlpc_gop_announcer_20261007_v2_h5/') for name in files):
        pins.update({
            'frozen/canonical_dlpc_gop_announcer_20261007_v2_h5/contracts/llm/dependency_lock.json': '58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4',
            'frozen/canonical_dlpc_gop_announcer_20261007_v2_h5/contracts/llm/llm_contract_manifest.json': '3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c',
        })
    if any(name.startswith('frozen/canonical_six_dimension_20261104_v1_h5/') for name in files):
        pins.update({
            'frozen/canonical_dlpc_gop_cuda_20261007_v2/release_manifest.json': 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391',
            'frozen/canonical_six_dimension_20261104_v1_h5/contracts/llm/dependency_lock.json': 'f4309130f20715b3c2f055121a88197e09c8ae37088e8eb2341b337f36cc65c8',
            'frozen/canonical_six_dimension_20261104_v1_h5/contracts/llm/llm_contract_manifest.json': '7fbfbd4a56bd0d7a7d9c750a62c6b6e78c8654cd0ac5d721df9fe29c331a39e2',
        })
    for name, expected in pins.items():
        require(name in files and files[name]['sha256'] == expected, 'BUNDLE_FROZEN_PIN')
    identities = {name: files[name]['sha256'] for name in sorted(files)}
    manifest_bytes = json.dumps(identities, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    require(BASE.parent.is_dir() and BASE.parent.resolve() == BASE.parent, 'BUNDLE_PARENT')
    if not BASE.exists():
        BASE.mkdir(mode=0o755)
    for parent in (Path('/opt'), BASE.parent, BASE):
        info = parent.lstat()
        require(parent.resolve() == parent and stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not stat.S_IMODE(info.st_mode) & 0o022, 'BUNDLE_DIRECTORY_PERMISSIONS')
    target = BASE / digest
    require(not target.exists() and not target.is_symlink(), 'BUNDLE_ALREADY_EXISTS_INSPECT_FIRST')
    target.mkdir(mode=0o755)
    for name, content in decoded.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with path.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o444)
    with (target / 'deployment.json').open('xb') as stream:
        stream.write(manifest_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    (target / 'deployment.json').chmod(0o444)
    for directory in sorted((p for p in target.rglob('*') if p.is_dir()), reverse=True):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory.chmod(0o555)
    descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    target.chmod(0o555)
    descriptor = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(json.dumps(dict(path=str(target), bundleSha256=digest, files=len(files),
                          bytes=sum(map(len, decoded.values())), activated=False, secretsIncluded=False)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import re
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[A-Z0-9_]+', str(error)) else 'BUNDLE_STAGE_FAILED'
        print(json.dumps(dict(error=code, instruction='Inspect the exact release directory; do not blindly overwrite.')))
        sys.exit(1)
