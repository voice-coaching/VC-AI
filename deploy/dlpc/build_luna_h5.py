"""Derive a model-only Luna H5 release; never activate or call the provider."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENT = '3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c'
LOCK = '58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4'
DIRECTORY = 'canonical_dlpc_gop_luna_20261024_v1_h5'


def build(parent: Path, destination: Path) -> dict:
    raw = (parent / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != PARENT:
        raise ValueError('LUNA_PARENT_MANIFEST_MISMATCH')
    manifest = json.loads(raw)
    _, files = load_files(parent, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    name = 'contracts/llm/request_config.json'
    config = json.loads(files[name])
    if config['model'] != 'gpt-6-astra' or config['reasoning_effort'] != 'low':
        raise ValueError('LUNA_PARENT_REQUEST_MISMATCH')
    config['model'] = 'gpt-6-luna'
    files[name] = encode(config)
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest.update(contract_version=DIRECTORY, parent_llm_manifest_sha256=PARENT,
                    dependency_lock_sha256=sha(lock),
                    model_migration=dict(from_model='gpt-6-astra', to_model='gpt-6-luna',
                                         reasoning_effort='low'))
    raw = encode(manifest)
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = raw
    write_release(destination / DIRECTORY, files)
    return dict(directory=DIRECTORY, llmManifestSha256=sha(raw), llmLockSha256=sha(lock),
                promptSha256=manifest['prompt_sha256'],
                coreManifestSha256=manifest['parent_core_manifest_sha256'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.destination), sort_keys=True))
