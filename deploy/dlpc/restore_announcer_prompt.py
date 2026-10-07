"""Derive a new DLPC LLM release restoring the historical announcer feedback.

No model invocation or activation. Preserve the GOP score policy, adapter,
output schema and core; never overwrite the deployed immutable H5 directory.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENT = '1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7'
LOCK = '3e2e81b43b1c1298266df428bc01ef0761d37f413073824cae2000623ea2eb3c'
PROMPT = '5f3ec5aa81f54fdb3afe28869db2c5791a2133e8a44a23ead52187e9be542ff8'
DIRECTORY = 'canonical_dlpc_gop_announcer_20261007_v2_h5'
HISTORICAL_COMMIT = '93db6add0a80c434b943949da9dc6f801d3cb156'


def apply_feedback_policy(original):
    """Replace only feedback guidance in either the base or restored prompt."""
    root = Path(__file__).resolve().parents[2]
    restored = (root / 'configs/coaching/announcer_feedback_system_20261007.txt').read_bytes().replace(b'\r\n', b'\n')
    historical = (root / 'configs/coaching/announcer_feedback_20260928.json').read_bytes().replace(b'\r\n', b'\n')
    if sha(historical) != 'e6e4d6a005929def118f03881e356fddd7a55a95778e77ed37180d9c9ed8e25f':
        raise ValueError('ANNOUNCER_HISTORICAL_SOURCE_CHANGED')
    if json.loads(historical)['cases'][1]['expertFeedback'] not in restored.decode('utf-8'):
        raise ValueError('ANNOUNCER_HISTORICAL_TEXT_MISSING')
    source = original.decode('utf-8')
    restored_heading = '피드백 작성 지침 —'
    start_marker = restored_heading if restored_heading in source else 'feedback은 '
    end_marker = 'JSON 객체 하나만 출력한다.'
    if source.count(end_marker) != 1:
        raise ValueError('ANNOUNCER_PROMPT_SECTION_MISMATCH')
    end = source.index(end_marker)
    if source[:end].count(start_marker) != 1:
        raise ValueError('ANNOUNCER_PROMPT_SECTION_MISMATCH')
    start = source.index(start_marker)
    prompt = (source[:start] + restored.decode('utf-8').strip() + '\n\n' + source[end:]).encode('utf-8')
    return prompt, sha(restored)


def build(parent, destination):
    manifest_raw = (parent / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(manifest_raw) != PARENT:
        raise ValueError('ANNOUNCER_PARENT_MANIFEST_MISMATCH')
    manifest = json.loads(manifest_raw)
    _, files = load_files(parent, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    original = files['contracts/llm/llm_system_prompt.txt']
    if sha(original) != PROMPT:
        raise ValueError('ANNOUNCER_PARENT_PROMPT_MISMATCH')
    prompt, policy_sha256 = apply_feedback_policy(original)
    files['contracts/llm/llm_system_prompt.txt'] = prompt
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest.update(contract_version=DIRECTORY, parent_llm_manifest_sha256=PARENT,
                    dependency_lock_sha256=sha(lock), prompt_sha256=sha(prompt),
                    feedback_restoration=dict(source_commit=HISTORICAL_COMMIT,
                        source_path='configs/coaching/announcer_feedback_20260928.json',
                        case_id='testvideo2', restored_policy_sha256=policy_sha256))
    raw = encode(manifest)
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = raw
    target = destination / DIRECTORY
    if target.exists():
        raise ValueError('ANNOUNCER_RELEASE_ALREADY_EXISTS')
    write_release(target, files)
    return dict(directory=DIRECTORY, coreManifestSha256=manifest['parent_core_manifest_sha256'],
                llmManifestSha256=sha(raw), llmLockSha256=sha(lock), promptSha256=sha(prompt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.destination), sort_keys=True))
