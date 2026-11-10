"""Replace only the coaching prompt in the tested v3 scoring H5s."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy/runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENTS = {
    'direct': '0f379753c1f8878e2133ccdf94e9e08d38d3f58281bc211fecbe78faddbb81cd',
    'internal': 'd21652d16dec4818c1244f4375190c3125d6bad73aacab3ec2ada14e18efb16b',
}
OLD_PREFIX_BYTES = 14755
OLD_PREFIX_SHA256 = '07fdbb603adc899000214d2d442c81a9f0fbaf6c9f8d4b1fc944f5f485eabc47'


def build(source, destination, lane):
    raw = (source / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != PARENTS[lane]:
        raise ValueError('SPECIFIC_FEEDBACK_PARENT_MISMATCH')
    parent = json.loads(raw)
    _, inherited = load_files(source, 'contracts/llm/dependency_lock.json',
                              parent['dependency_lock_sha256'], 'files')
    files = dict(inherited)
    prompt_name = 'contracts/llm/llm_system_prompt.txt'
    old = files[prompt_name]
    if sha(old[:OLD_PREFIX_BYTES]) != OLD_PREFIX_SHA256:
        raise ValueError('SPECIFIC_FEEDBACK_PROMPT_BOUNDARY')
    new = (ROOT / 'configs/coaching/six_dimension_system_v1.txt').read_bytes()
    if not new.strip() or sha(new) == OLD_PREFIX_SHA256:
        raise ValueError('SPECIFIC_FEEDBACK_NEW_PROMPT_REQUIRED')
    final_rules = (ROOT / 'configs/coaching/specific_feedback_final_v2.txt').read_bytes()
    if not final_rules.strip():
        raise ValueError('SPECIFIC_FEEDBACK_FINAL_RULES_REQUIRED')
    files[prompt_name] = new + old[OLD_PREFIX_BYTES:] + final_rules
    files['bridge/pipeline.py'] = (ROOT / 'src/voice_coach/six_dimension/pipeline.py').read_bytes()
    changed = {name for name in files if files[name] != inherited[name]}
    if changed != {prompt_name, 'bridge/pipeline.py'}:
        raise ValueError('SPECIFIC_FEEDBACK_UNEXPECTED_CHANGE')
    directory = f'canonical_specific_feedback_{lane}_20261110_v3_h5'
    lock = encode(dict(files={name: sha(body) for name, body in sorted(files.items())}))
    prompt_sha = sha(files[prompt_name])
    manifest = encode(dict(parent, contract_version=directory,
        parent_llm_manifest_sha256=sha(raw), dependency_lock_sha256=sha(lock),
        prompt_sha256=prompt_sha, productionActivated=False, developerQaVerified=False))
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = manifest
    target = destination / directory
    write_release(target, files)
    return dict(root=str(target), manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=prompt_sha, coreSha256=parent['parent_core_manifest_sha256'],
                changedFiles=sorted(changed), providerPosts=0, productionActivated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--lane', choices=tuple(PARENTS), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, args.lane)))
