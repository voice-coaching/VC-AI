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
    *(f'app/src/voice_coach/backend_analysis/{name}.py' for name in (
        'canonical_bridge', 'canonical_codec', 'canonical_media', 'canonical_harness',
        'canonical_result', 'http_models', 'domain',
    )),
    *(f'app/docs/contracts/{name}.schema.json' for name in (
        'runpod_result_v4', 'runpod_canonical_parent', 'runpod_canonical_input',
        'runpod_canonical_output', 'runpod_http_control_v1_2',
        'runpod_http_control_v1', 'runpod_analysis_request_v2',
        'runpod_result_v1', 'runpod_canonical_journal_v1',
    )),
})


def require(condition, code):
    if not condition:
        raise ValueError(code)


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ROOT_LINUX_REQUIRED')
    os.umask(0o022)
    raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    require(0 < len(raw) <= 8 * 1024 * 1024, 'BUNDLE_SIZE')
    files = json.loads(raw)
    require(type(files) is dict and 90 <= len(files) <= 300, 'BUNDLE_FILES')
    require(REQUIRED_APP_FILES.issubset(files), 'BUNDLE_APP_DEPENDENCIES_MISSING')
    decoded = {}
    for name, entry in files.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and all(p not in ('', '.', '..') for p in name.split('/'))
                and '\\' not in name and path.as_posix() == name
                and (name.startswith(('frozen/core/', 'frozen/llm/', 'frozen/canonical_h5_20261003/',
                     'frozen/canonical_scored_core_20261003_v1/', 'frozen/canonical_scored_h5_20261003_v1/',
                     'app/src/voice_coach/', 'app/docs/contracts/'))
                     or name == 'app/deploy/runpod/canonical_verify.py')
                and not any(p == '.env' or p.startswith('.env.') for p in path.parts), 'BUNDLE_PATH')
        require(type(entry) is dict and set(entry) == {'sha256', 'base64'}, 'BUNDLE_ENTRY')
        content = base64.b64decode(entry['base64'], validate=True)
        require(len(content) <= 2 * 1024 * 1024 and hashlib.sha256(content).hexdigest() == entry['sha256'], 'BUNDLE_HASH')
        decoded[name] = content
    pins = {
        'frozen/canonical_scored_core_20261003_v1/release_manifest.json': '6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776',
        'frozen/canonical_scored_h5_20261003_v1/contracts/llm/dependency_lock.json': '17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d',
        'frozen/canonical_scored_h5_20261003_v1/contracts/llm/llm_contract_manifest.json': '820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6',
        'frozen/core/release_manifest.json': '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2',
        'frozen/llm/contracts/llm/dependency_lock.json': 'd8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01',
        'frozen/llm/contracts/llm/llm_contract_manifest.json': '1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73',
        'frozen/canonical_h5_20261003/contracts/llm/dependency_lock.json': '93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5',
        'frozen/canonical_h5_20261003/contracts/llm/llm_contract_manifest.json': '9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45',
    }
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
