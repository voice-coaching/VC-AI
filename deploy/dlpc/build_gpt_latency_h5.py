"""Build separate immutable standard-tier Luna stages; never activate or infer."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

PARENTS = {
    'none': ('1c62cd5f42625d80c2ca772d631ec3a2f9b6286d7e2a4b48e1b0b4f5a290899e',
             '8da3410fba23a1f0c802b436edca181a7fa0c3a14f2b455f4a26b9f7a0330c73'),
    'compact': ('a0be7ab1bd5738c14798f15e6c72f82558106f717798ad7e5da90f52e2dc5691',
                'ed9bab85e9fbbda03196c77ebc7d191f382dfb108670a6fb296f15f83f061869'),
    'cached': ('50701f1b8a64ddb1b6c5c9d440a7e1beb04ce12a0b423cc1e5f3277647d69db0',
               '1044117f1c7d300984210263a9afebabaa2ee0501f56aa5ad83922479a08e20b'),
}
CORE = 'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391'
NAMES = {stage: 'canonical_dlpc_gop_luna_' + stage + '_20261024_v1_h5'
         for stage in ('none', 'compact', 'cached')}


def compact_output(files):
    templates = Path(__file__).resolve().parents[1] / 'runpod/harnesses'
    name = 'contracts/llm/llm_system_prompt.txt'
    original = files[name].decode('utf-8')
    marker = 'JSON 객체 하나만 출력한다.'
    if original.count(marker) != 1:
        raise ValueError('COMPACT_PROMPT_SECTION')
    files[name] = (original[:original.index(marker)] +
        (templates / 'compact_gop_output_prompt.txt').read_text()).encode('utf-8')
    adapter = replace_once(files['bridge/adapter.py'].decode('utf-8'),
        '            candidate = strict_json(choice["message"]["content"])\n',
        '            compact = strict_json(choice["message"]["content"])\n'
        '            jsonschema.Draft202012Validator(self.read("compact_output.schema.json")).validate(compact)\n'
        '            candidate = expand_compact_output(compact, self.read("scoring_rubric.json"))\n')
    files['bridge/adapter.py'] = (adapter + '\n\n' +
        (templates / 'compact_gop_output.py').read_text()).encode('utf-8')
    files['contracts/llm/compact_output.schema.json'] = (templates / 'compact_gop_output.schema.json').read_bytes()


def cached_prefix(files):
    adapter = files['bridge/adapter.py'].decode('utf-8')
    adapter = replace_once(adapter,
        '            RUBRIC=self.read("scoring_rubric.json"))',
        '            )')
    adapter = replace_once(adapter,
        '        messages = [dict(role="system", content=(self.root / "contracts/llm/llm_system_prompt.txt").read_text(encoding="utf-8")),\n'
        '                    dict(role="user", content=encode(data).decode("utf-8"))]\n'
        '        require(sum(len(m["content"].encode("utf-8")) for m in messages) <= config["input_utf8_bound"], "INPUT_BUDGET_NO_SILENT_TRUNCATION")',
        '        # Cache only fixed guidance and the unchanged rubric; user evidence follows the boundary.\n'
        '        messages = [dict(role="system", content=[\n'
        '            dict(type="text", text=(self.root / "contracts/llm/llm_system_prompt.txt").read_text(encoding="utf-8")),\n'
        '            dict(type="text", text=encode(dict(RUBRIC=self.read("scoring_rubric.json"))).decode("utf-8"),\n'
        '                 prompt_cache_breakpoint=dict(mode="explicit"))]),\n'
        '            dict(role="user", content=encode(data).decode("utf-8"))]\n'
        '        require(sum(len(part["text"].encode("utf-8")) for part in messages[0]["content"])\n'
        '                + len(messages[1]["content"].encode("utf-8")) <= config["input_utf8_bound"], "INPUT_BUDGET_NO_SILENT_TRUNCATION")')
    adapter = replace_once(adapter, 'service_tier=config["service_tier"]))',
        'service_tier=config["service_tier"], prompt_cache_options=config["prompt_cache_options"]))')
    files['bridge/adapter.py'] = adapter.encode('utf-8')


def build(parent: Path, destination: Path, stage: str) -> dict:
    manifest_pin, lock_pin = PARENTS[stage]
    manifest_raw = (parent / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(manifest_raw) != manifest_pin:
        raise ValueError('LATENCY_PARENT_IDENTITY')
    manifest = json.loads(manifest_raw)
    if manifest['parent_core_manifest_sha256'] != CORE or manifest['dependency_lock_sha256'] != lock_pin:
        raise ValueError('LATENCY_PARENT_TUPLE')
    _, files = load_files(parent, 'contracts/llm/dependency_lock.json', lock_pin, 'files')
    config = json.loads(files['contracts/llm/request_config.json'])
    if config['model'] != 'gpt-6-luna' or config.get('service_tier') not in (None, 'default'):
        raise ValueError('LATENCY_STANDARD_LUNA_REQUIRED')
    if stage == 'none':
        if config['reasoning_effort'] != 'low':
            raise ValueError('LATENCY_LOW_PARENT_REQUIRED')
        config.update(reasoning_effort='none', service_tier='default')
        files['bridge/adapter.py'] = replace_once(files['bridge/adapter.py'].decode('utf-8'),
            'response_format=config["response_format"], store=config["store"]))',
            'response_format=config["response_format"], store=config["store"], service_tier=config["service_tier"]))').encode('utf-8')
    else:
        if config['reasoning_effort'] != 'none' or config.get('service_tier') != 'default':
            raise ValueError('LATENCY_NONE_PARENT_REQUIRED')
        if stage == 'compact':
            compact_output(files)
            manifest['response_encoding'] = dict(revision='compact-gop-output-v1',
                fields=['levels', 'score', 'feedback'], expanded_output_schema_preserved=True,
                server_recalculates_scores=False)
        elif stage == 'cached':
            cached_prefix(files)
            config['prompt_cache_options'] = dict(mode='explicit', ttl='30m')
            manifest['prompt_layout_revision'] = 'fixed-guidance-rubric-before-user-v1'
    files['contracts/llm/request_config.json'] = encode(config)
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest.update(contract_version=NAMES[stage], parent_llm_manifest_sha256=manifest_pin,
        dependency_lock_sha256=sha(lock), prompt_sha256=sha(files['contracts/llm/llm_system_prompt.txt']),
        latency_stage=stage, service_tier='default')
    raw = encode(manifest)
    files['contracts/llm/dependency_lock.json'] = lock
    files['contracts/llm/llm_contract_manifest.json'] = raw
    write_release(destination / NAMES[stage], files)
    return dict(stage=stage, directory=NAMES[stage], llmManifestSha256=sha(raw),
                llmLockSha256=sha(lock), promptSha256=manifest['prompt_sha256'],
                coreManifestSha256=CORE, activated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--stage', choices=tuple(NAMES), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.parent, args.destination, args.stage), sort_keys=True))
