"""Derive an immutable H5 with presentation-only feedback tolerance."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy/runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENTS = {
    'direct': '920570f14c74f7b8db4c72982fb3a5b6f992caa4f27401c37caaf2f9549aba6f',
    'internal': '9a1d148b8c6f19a88e62b38f9eb0f8ae94bdee198f20fd3e997967383d4994c2',
}


def build(source, destination, lane):
    parent_raw = (source / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(parent_raw) != PARENTS[lane]:
        raise ValueError('FEEDBACK_PRESENTATION_PARENT_MISMATCH')
    parent = json.loads(parent_raw)
    _, inherited = load_files(source, 'contracts/llm/dependency_lock.json',
                              parent['dependency_lock_sha256'], 'files')
    files = dict(inherited)
    files['bridge/pipeline.py'] = (ROOT / 'src/voice_coach/six_dimension/pipeline.py').read_bytes()
    changed = {k for k in files if sha(files[k]) != sha(inherited[k])}
    if changed != {'bridge/pipeline.py'}:
        raise ValueError('FEEDBACK_PRESENTATION_UNEXPECTED_CHANGE')
    directory = f'canonical_feedback_presentation_{lane}_20261109_v1_h5'
    lock = encode(dict(files={k: sha(v) for k, v in sorted(files.items())}))
    manifest = encode(dict(parent, contract_version=directory,
        parent_llm_manifest_sha256=sha(parent_raw), dependency_lock_sha256=sha(lock),
        productionActivated=False, developerQaVerified=False))
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = manifest
    target = destination / directory
    write_release(target, files)
    return dict(root=str(target), manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=parent['prompt_sha256'], coreSha256=parent['parent_core_manifest_sha256'],
                changedFiles=sorted(changed), providerPosts=0)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--lane', choices=tuple(PARENTS), required=True)
    a = p.parse_args()
    print(json.dumps(build(a.source, a.destination, a.lane)))
