"""Derive a v3 scoring H5 from YuNet; keep CORE, media and visual policy intact."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy/runpod'))
sys.path.insert(0, str(ROOT / 'src'))
from release_packaging import encode, load_files, sha, write_release
from voice_coach.six_dimension.composite import load_composite_policy, validate_reference_asset

PARENTS = {
    'direct': '19802f16e8bcd8b8e9ada0148eb24e0aa2d6fb30b56769dae05c84794d350430',
    'internal': 'd3e13ad9f3b5ad25d209736e7fbd8642c2df2c081d66cf1587e6def8834aaae4',
}


def build(source, destination, lane):
    raw = (source / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != PARENTS[lane]:
        raise ValueError('COMPOSITE_POWER_PARENT_MISMATCH')
    parent = json.loads(raw)
    _, inherited = load_files(source, 'contracts/llm/dependency_lock.json',
                              parent['dependency_lock_sha256'], 'files')
    files = dict(inherited)
    policy_raw = (ROOT / 'configs/pronunciation_feedback/composite_exponential_v3.json').read_bytes()
    policy, policy_sha = load_composite_policy(
        ROOT / 'configs/pronunciation_feedback/composite_exponential_v3.json')
    if sha(policy_raw) != policy_sha:
        raise ValueError('COMPOSITE_POWER_POLICY_CHANGED')
    validate_reference_asset(policy, files['contracts/llm/composite_reference.json'])
    old_prompt = (ROOT / 'configs/coaching/composite_exponential_v2.txt').read_bytes()
    new_prompt = (ROOT / 'configs/coaching/composite_exponential_v3.txt').read_bytes()
    prompt = files['contracts/llm/llm_system_prompt.txt']
    if prompt.count(old_prompt) != 1:
        raise ValueError('COMPOSITE_POWER_PROMPT_BOUNDARY')
    files['contracts/llm/llm_system_prompt.txt'] = prompt.replace(old_prompt, new_prompt)
    files['contracts/llm/composite_policy.json'] = policy_raw
    files['contracts/llm/six_dimension_result.schema.json'] = (
        ROOT / 'docs/contracts/six_dimension_result_v1.schema.json').read_bytes()
    files['bridge/composite.py'] = (ROOT / 'src/voice_coach/six_dimension/composite.py').read_bytes()
    changed = {name for name in files if files[name] != inherited[name]}
    if changed != {'bridge/composite.py', 'contracts/llm/llm_system_prompt.txt',
                   'contracts/llm/composite_policy.json', 'contracts/llm/six_dimension_result.schema.json'}:
        raise ValueError('COMPOSITE_POWER_UNEXPECTED_CHANGE')
    directory = f'canonical_composite_power_{lane}_20261110_v3_h5'
    lock = encode(dict(files={name: sha(body) for name, body in sorted(files.items())}))
    prompt_sha = sha(files['contracts/llm/llm_system_prompt.txt'])
    manifest = encode(dict(parent, contract_version=directory,
        parent_llm_manifest_sha256=sha(raw), dependency_lock_sha256=sha(lock),
        prompt_sha256=prompt_sha, execution_profile=policy['revision'],
        composite_policy_sha256=policy_sha, productionActivated=False, developerQaVerified=False))
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = manifest
    target = destination / directory
    write_release(target, files)
    return dict(root=str(target), manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=prompt_sha, coreSha256=parent['parent_core_manifest_sha256'],
                policySha256=policy_sha, changedFiles=sorted(changed), providerPosts=0,
                productionActivated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--lane', choices=tuple(PARENTS), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, args.lane)))
