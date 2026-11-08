"""Derive sparse-frame coaching from Luna Standard, without changing CORE/assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy/runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENTS = {
    'direct': 'c085724ea47290009f8d3243002cfe403552924f328e52757d21732c407d1c0b',
    'internal': '9c34499f21f0f408f7f26fe45da309977494a2f1b0a6cdae5a7f9662c33e7a5f',
}
BASE_PROMPT_BYTES = 13666
BASE_PROMPT_SHA = '7e3e61994360f067928021997ef7d29dc163be52ad1199727866ced75dc605fc'


def build(source: Path, destination: Path, *, lane: str):
    parent_raw = (source / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(parent_raw) != PARENTS[lane]:
        raise ValueError('SPARSE_LIP_PARENT')
    parent = json.loads(parent_raw)
    _, inherited = load_files(source, 'contracts/llm/dependency_lock.json',
                              parent['dependency_lock_sha256'], 'files')
    files = dict(inherited)
    config = json.loads(files['contracts/llm/request_config.json'])
    if (config['model'], config['reasoning_effort'], config['service_tier']) != (
            'gpt-6-luna', 'none', 'default'):
        raise ValueError('SPARSE_LIP_REQUEST')
    prompt_key = 'contracts/llm/llm_system_prompt.txt'
    previous = files[prompt_key]
    if sha(previous[:BASE_PROMPT_BYTES]) != BASE_PROMPT_SHA:
        raise ValueError('SPARSE_LIP_PROMPT_PARENT')
    # Retain all server-rubric/composite instructions byte for byte.
    files[prompt_key] = (ROOT / 'configs/coaching/six_dimension_system_v1.txt').read_bytes() + previous[BASE_PROMPT_BYTES:]
    files['bridge/visual.py'] = (ROOT / 'src/voice_coach/six_dimension/visual.py').read_bytes()
    changed = {k for k in files if files[k] != inherited[k]}
    if changed != {prompt_key, 'bridge/visual.py'}:
        raise ValueError('SPARSE_LIP_UNEXPECTED_CHANGE')
    directory = f'canonical_sparse_lip_{lane}_20261108_v1_h5'
    lock = encode({'files': {k: sha(v) for k, v in sorted(files.items())}})
    manifest = dict(parent, contract_version=directory, parent_llm_manifest_sha256=sha(parent_raw),
                    dependency_lock_sha256=sha(lock), prompt_sha256=sha(files[prompt_key]),
                    visual_assessment_policy='PHONE_ROLE_SPARSE_30FPS_V1',
                    productionActivated=False, developerQaVerified=False)
    raw = encode(manifest)
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = raw
    write_release(destination / directory, files)
    return dict(root=str(destination / directory), revision=directory.replace('_', '-').removesuffix('-h5'),
                coreSha256=parent['parent_core_manifest_sha256'], manifestSha256=sha(raw),
                lockSha256=sha(lock), promptSha256=manifest['prompt_sha256'],
                changedFiles=sorted(changed), productionActivated=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--lane', choices=tuple(PARENTS), required=True)
    a = p.parse_args()
    print(json.dumps(build(a.source, a.destination, lane=a.lane)))
