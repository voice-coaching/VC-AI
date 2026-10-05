"""Native baseline with content gate removed and immutable provenance."""
import json
from release_packaging import encode, load_files, remove_region, replace_once, sha
CORE_PARENT = "95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2"
def native_base(source):
    manifest, files = load_files(source, 'release_manifest.json', CORE_PARENT, 'file_sha256')
    runner = files['source/scripts/run_canonical_batch.py'].decode()
    runner = replace_once(runner, 'from korean_pronunciation_poc.demo_content_evidence import measure as measure_content, route as route_content\n', '')
    runner = remove_region(runner, "        native = self.state.get('native')\n", '        return pron\n')
    runner = remove_region(runner, "                decision = state.get('content_decision')\n", '                return mfa_opt.run_mfa')
    runner = remove_region(runner, "            if validation.status == 'ACCEPT' and content_decision and content_decision['status'] == 'INCONCLUSIVE':\n", "            marks['content_gate_done']")
    runner = replace_once(runner, "config_raw['final_pronunciation']['enabled'] = False", "config_raw['final_pronunciation'] = {'enabled': False}")
    runner = replace_once(runner, "config_raw['pronunciation']['enabled'] = False", "config_raw['pronunciation'] = {'enabled': False}")
    runner = replace_once(runner, "ROOT / 'calibration/demo_content_gate_config_v2.json'", "ROOT / 'calibration/content_gate_removed_v1.json'")
    runner = replace_once(runner, "assert content_config['schema'] == 'demo_content_gate_calibration_v2'", "assert content_config == {'schema': 'content_gate_removed_v1', 'enabled': False}")
    files['source/scripts/run_canonical_batch.py'] = runner.encode()
    policy = encode(dict(schema='content_gate_removed_v1', enabled=False))
    files['calibration/content_gate_removed_v1.json'] = policy
    files.pop('calibration/demo_content_gate_config_v2.json')
    files.pop('source/src/korean_pronunciation_poc/demo_content_evidence.py')
    identity = json.loads(files['config/release_preflight_identity.json'])
    files['source/config/release_provenance.json'] = encode(dict(schema='canonical-release-provenance-v1', source_git_commit=identity['base_source_commit'], source_modified=True))
    module_name = 'source/src/korean_pronunciation_poc/manifest.py'
    module = files[module_name].decode()
    module = replace_once(module, 'import platform\n', 'import platform\nimport json\nimport re\n')
    start = module.index('def _git_state(')
    end = module.index('\ndef _tool_version(', start)
    module = module[:start] + 'def _git_state(config: PocConfig, runner: Runner) -> tuple[str, bool]:\n    """Legacy output fields populated from the pinned deployment snapshot."""\n    try:\n        path = config.paths.project_root / "config/release_provenance.json"\n        doc = json.loads(path.read_bytes())\n        if (set(doc) != {"schema", "source_git_commit", "source_modified"}\n                or doc["schema"] != "canonical-release-provenance-v1"\n                or not re.fullmatch("[0-9a-f]{40}", doc["source_git_commit"])\n                or type(doc["source_modified"]) is not bool):\n            raise ValueError("RELEASE_PROVENANCE_INVALID")\n        return doc["source_git_commit"], doc["source_modified"]\n    except (OSError, ValueError, TypeError, KeyError) as exc:\n        raise _failure("배포 출처 정보를 확인할 수 없습니다.",\n                       exception_type=type(exc).__name__) from exc\n\n' + module[end:]
    files[module_name] = module.encode()

    return manifest, files, identity, sha(policy)
