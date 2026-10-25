"""Derive a pinned Sol 6.1 low H5; preserve the prepared core and compact prompt."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, sha, write_release

PARENT = 'c3d78433cdb0d9ca999ab7b407464084a95cbf8f6d931a80ffd9610656ea3b68'
LOCK = '2864be8b43b850136c7b41ffc9242265b5faa5890c48c59847f807051fc44114'
CORE = '9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba'
DIRECTORY = 'canonical_dlpc_pipeline_sol61_light_20261026_v1_h5'


def build(parent: Path, destination: Path) -> dict:
    raw = (parent / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != PARENT:
        raise ValueError('SOL61_PARENT_MANIFEST')
    manifest = json.loads(raw)
    if manifest['parent_core_manifest_sha256'] != CORE or manifest['dependency_lock_sha256'] != LOCK:
        raise ValueError('SOL61_PARENT_PAIR')
    _, files = load_files(parent, 'contracts/llm/dependency_lock.json', LOCK, 'files')
    name = 'contracts/llm/request_config.json'
    config = json.loads(files[name])
    if (config['model'], config['reasoning_effort'], config.get('service_tier')) != ('gpt-6-luna', 'none', 'default'):
        raise ValueError('SOL61_PARENT_REQUEST')
    config.update(model='gpt-6.1-sol', reasoning_effort='low')
    files[name] = encode(config)
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest.update(contract_version=DIRECTORY, parent_llm_manifest_sha256=PARENT,
        dependency_lock_sha256=sha(lock), model_migration=dict(from_model='gpt-6-luna',
            from_reasoning_effort='none', to_model='gpt-6.1-sol', reasoning_effort='low',
            user_effort_label='light', service_tier='default'))
    raw = encode(manifest)
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = raw
    for name, content in files.items():
        if name.endswith('.json'):
            json.loads(content)
    write_release(destination / DIRECTORY, files)
    return dict(directory=DIRECTORY, llmManifestSha256=sha(raw), llmLockSha256=sha(lock),
        promptSha256=manifest['prompt_sha256'], coreManifestSha256=CORE, activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.destination), sort_keys=True))
