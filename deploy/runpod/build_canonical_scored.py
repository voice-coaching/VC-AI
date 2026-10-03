"""Build an additive core/H5 release: remove content gate, enable full Seungun.

Packaging and compilation only. Never executes a model or alters a running release.
The original manifests must match before any derivative is produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CORE_PARENT = "95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2"
H5_PARENT = "9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45"
H5_LOCK = "93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5"
CORE_DIRECTORY = "canonical_scored_core_20261003_v2"
LLM_DIRECTORY = "canonical_scored_h5_20261003_v2"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load_files(root, name, pin, key):
    root = root.resolve(strict=True)
    raw = (root / name).read_bytes()
    if sha(raw) != pin:
        raise ValueError("DERIVATIVE_PARENT_IDENTITY")
    manifest = json.loads(raw)
    files = {}
    for relative, digest in manifest[key].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("DERIVATIVE_SOURCE_PATH")
        content = path.read_bytes()
        if sha(content) != digest:
            raise ValueError("DERIVATIVE_SOURCE_HASH")
        files[relative] = content
    return manifest, files


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("DERIVATIVE_SOURCE_CONTEXT_CHANGED")
    return text.replace(before, after)


def remove_region(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("DERIVATIVE_SOURCE_CONTEXT_CHANGED")
    a, b = text.index(start), text.index(end)
    if b <= a:
        raise ValueError("DERIVATIVE_SOURCE_ORDER_CHANGED")
    return text[:a] + text[b:]


def write_release(target, files):
    if target.exists():
        raise ValueError("DERIVATIVE_REQUIRES_NEW_DIRECTORY")
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    target.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def build_core(source, target):
    manifest, files = load_files(source, "release_manifest.json", CORE_PARENT, "file_sha256")
    runner = files["source/scripts/run_canonical_batch.py"].decode()
    runner = replace_once(runner,
        "from korean_pronunciation_poc.demo_content_evidence import measure as measure_content, route as route_content\n", "")
    runner = remove_region(runner, "        native = self.state.get('native')\n", "        return pron\n")
    runner = remove_region(runner, "                decision = state.get('content_decision')\n",
                          "                return mfa_opt.run_mfa")
    runner = remove_region(runner,
        "            if validation.status == 'ACCEPT' and content_decision and content_decision['status'] == 'INCONCLUSIVE':\n",
        "            marks['content_gate_done']")
    runner = replace_once(runner, "config_raw['final_pronunciation']['enabled'] = False",
                          "config_raw['final_pronunciation']['enabled'] = True")
    runner = replace_once(runner, "ROOT / 'calibration/demo_content_gate_config_v2.json'",
                          "ROOT / 'calibration/content_gate_removed_v1.json'")
    runner = replace_once(runner, "assert content_config['schema'] == 'demo_content_gate_calibration_v2'",
                          "assert content_config == {'schema': 'content_gate_removed_v1', 'enabled': False}")
    # P1 remains the only coaching source; full Seungun evidence is consumed only
    # by the scoring adapter. Missing full evidence must fail scoring downstream.
    files["source/scripts/run_canonical_batch.py"] = runner.encode()
    policy = encode(dict(schema="content_gate_removed_v1", enabled=False))
    files["calibration/content_gate_removed_v1.json"] = policy
    files.pop("calibration/demo_content_gate_config_v2.json")
    files.pop("source/src/korean_pronunciation_poc/demo_content_evidence.py")
    identity = json.loads(files["config/release_preflight_identity.json"])
    # Deployment is a verified source snapshot, not a development checkout.
    # Record the real baseline; Git availability must not decide analysis success.
    files["source/config/release_provenance.json"] = encode(dict(
        schema="canonical-release-provenance-v1",
        source_git_commit=identity["base_source_commit"], source_modified=True))
    module_name = "source/src/korean_pronunciation_poc/manifest.py"
    module = files[module_name].decode()
    module = replace_once(module, "import platform\n", "import platform\nimport json\nimport re\n")
    start = module.index("def _git_state(")
    end = module.index("\ndef _tool_version(", start)
    module = module[:start] + '''def _git_state(config: PocConfig, runner: Runner) -> tuple[str, bool]:
    """Legacy output fields populated from the pinned deployment snapshot."""
    try:
        path = config.paths.project_root / "config/release_provenance.json"
        doc = json.loads(path.read_bytes())
        if (set(doc) != {"schema", "source_git_commit", "source_modified"}
                or doc["schema"] != "canonical-release-provenance-v1"
                or not re.fullmatch("[0-9a-f]{40}", doc["source_git_commit"])
                or type(doc["source_modified"]) is not bool):
            raise ValueError("RELEASE_PROVENANCE_INVALID")
        return doc["source_git_commit"], doc["source_modified"]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _failure("배포 출처 정보를 확인할 수 없습니다.",
                       exception_type=type(exc).__name__) from exc

''' + module[end:]
    files[module_name] = module.encode()
    identity["gate_config"] = dict(path="/workspace/intelligentai/releases/" + CORE_DIRECTORY
                                  + "/calibration/content_gate_removed_v1.json", sha256=sha(policy))
    identity["release_blackbox_runner_sha256"] = sha(files["source/scripts/run_canonical_batch.py"])
    identity["status"] = "DERIVED_NOT_INFERENCE_VALIDATED"
    files["config/release_preflight_identity.json"] = encode(identity)
    manifest.update(release_version=CORE_DIRECTORY, release_root="/workspace/intelligentai/releases/" + CORE_DIRECTORY,
                    parent_manifest_sha256=CORE_PARENT, deployed=False, production_modified=False,
                    phase2_gate_config=dict(enabled=False, sha256=sha(policy)),
                    verification_scope="source integrity and compilation only; no inference QA")
    manifest["file_sha256"] = {name: sha(raw) for name, raw in sorted(files.items())}
    raw = encode(manifest)
    files["release_manifest.json"] = raw
    write_release(target, files)
    return sha(raw), sha(policy)


def build_harness(source, target, core_sha, content_sha, rubric_path):
    _, files = load_files(source, "contracts/llm/dependency_lock.json", H5_LOCK, "files")
    parent_raw = (source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    if sha(parent_raw) != H5_PARENT:
        raise ValueError("DERIVATIVE_H5_PARENT_IDENTITY")
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    if rubric.get("rubricRevision") != "phone-rubric-20261003-v1":
        raise ValueError("DERIVATIVE_SCORING_POLICY_VERSION")
    app = Path(__file__).resolve().parents[2]
    calculator = {}
    calculator_source = (app / "src/voice_coach/backend_analysis/phone_rubric.py").read_bytes()
    exec(compile(calculator_source, "phone_rubric.py", "exec"), calculator)
    calculator["validate_policy"](rubric)
    policy = Path(__file__).parent / "harnesses"
    adapter = files["bridge/adapter.py"].decode()
    adapter = replace_once(adapter, 'PARENT = "' + CORE_PARENT + '"', 'PARENT = "' + core_sha + '"')
    adapter += "\n" + (policy / "canonical_scored_bridge.py").read_text(encoding="utf-8")
    files["bridge/adapter.py"] = adapter.encode()
    for name in ("phone_rubric", "gpt_rubric"):
        files["bridge/" + name + ".py"] = (app / "src/voice_coach/backend_analysis" / (name + ".py")).read_bytes().replace(b"\r\n", b"\n")
    component = json.loads(files["contracts/llm/component_identity.json"])
    component["content_gate_config_sha256"] = content_sha
    files["contracts/llm/component_identity.json"] = encode(component)
    files["contracts/llm/scoring_rubric.json"] = encode(rubric)
    files["contracts/llm/scoring_system_prompt.txt"] = (policy / "phone_rubric_prompt.txt").read_bytes().replace(b"\r\n", b"\n")
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest = encode(dict(contract_version=LLM_DIRECTORY, parent_llm_manifest_sha256=H5_PARENT,
        parent_core_manifest_sha256=core_sha, dependency_lock_sha256=sha(lock),
        prompt_sha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
        scoring_prompt_sha256=sha(files["contracts/llm/scoring_system_prompt.txt"]),
        scoring_rubric_sha256=sha(files["contracts/llm/scoring_rubric.json"]),
        changed_scope="remove consonant content gate; full Seungun server rubric and separate GPT scoring response",
        verification_scope="source integrity and compilation only; no inference QA"))
    files["contracts/llm/dependency_lock.json"] = lock
    files["contracts/llm/llm_contract_manifest.json"] = manifest
    write_release(target, files)
    return dict(manifestSha256=sha(manifest), lockSha256=sha(lock), coreManifestSha256=core_sha,
                promptSha256=sha(files["contracts/llm/llm_system_prompt.txt"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-source", type=Path, required=True)
    parser.add_argument("--core-target", type=Path, required=True)
    parser.add_argument("--h5-source", type=Path)
    parser.add_argument("--h5-target", type=Path)
    parser.add_argument("--rubric", type=Path)
    args = parser.parse_args()
    if any((args.h5_source, args.h5_target, args.rubric)) and not all((args.h5_source, args.h5_target, args.rubric)):
        parser.error("H5 source, target and rubric must be provided together")
    core, policy = build_core(args.core_source, args.core_target)
    identity = build_harness(args.h5_source, args.h5_target, core, policy, args.rubric) if args.h5_source else {}
    print(json.dumps(dict(coreManifestSha256=core, contentPolicySha256=policy, harness=identity)))
