"""Package Native-only core/H5 sources. Draft calibration cannot enable inference."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from native_base_source import CORE_PARENT, native_base
from native_resident_source import resident_runner
from native_source_pruning import prune
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE_DIRECTORY = "canonical_native_core_20261004_v1"
LLM_DIRECTORY = "canonical_native_h5_20261004_v1"
H5_PARENT = "9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45"
H5_LOCK = "93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5"
MODULES = ("phone_rubric", "native_phone_contract", "native_phone_alignment", "native_phone_assessment",
           "native_phone_evidence", "gpt_rubric", "native_gpt_rubric")


def scoring_sources(app):
    result = {}
    for name in MODULES:
        raw = (app / "src/voice_coach/backend_analysis" / (name + ".py")).read_bytes()
        tree = ast.parse(raw)
        # Historic evidence readers remain in the app for archived releases only.
        # They are absent from the new executable core and H5 package.
        removed = {"evidence_rows"} if name == "phone_rubric" else {"score_core", "verify_score_record"} if name == "gpt_rubric" else set()
        if removed:
            tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef) or node.name not in removed]
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "phone_rubric":
                    node.names = [n for n in node.names if n.name != "evidence_rows"]
            raw = (ast.unparse(tree) + "\n").encode()
        result[name] = raw.replace(b"\r\n", b"\n")
    return result


def build(core_source, h5_source, destination, policy_path):
    app = Path(__file__).resolve().parents[2]
    sources = scoring_sources(app)
    # Validate policy shape without falsely claiming calibration or model QA.
    import sys
    sys.path.insert(0, str(app / "src"))
    from voice_coach.backend_analysis.native_phone_assessment import policy_contract
    policy = json.loads(policy_path.read_bytes())
    policy_contract(policy)
    manifest, files, identity, content_sha = native_base(core_source)
    removed = prune(files)
    runner = resident_runner(files["source/scripts/run_canonical_batch.py"].decode())
    files["source/scripts/run_canonical_batch.py"] = runner.encode()
    for name, raw in sources.items():
        files["source/src/native_scoring/" + name + ".py"] = raw
    files["source/src/native_scoring/__init__.py"] = b""
    files["source/scripts/native_core_adapter.py"] = (Path(__file__).parent / "harnesses/native_core_adapter.py").read_bytes().replace(b"\r\n", b"\n")
    files["scripts/run_resident.py"] = (Path(__file__).parent / "harnesses/canonical_resident_server.py").read_bytes().replace(b"\r\n", b"\n")
    # Keep runtime preparation, remove the one-shot inference entry point.
    launcher = ast.parse(files["scripts/run_release.py"])
    launcher.body = [n for n in launcher.body if not isinstance(n, ast.If) and not (
        isinstance(n, ast.FunctionDef) and n.name == "main")]
    files["scripts/run_release.py"] = (ast.unparse(launcher) + "\n").encode()
    files["config/native_phone_assessment.json"] = encode(policy)
    identity.update(status="NATIVE_DERIVED_DEVELOPER_QA_REQUIRED", release_blackbox_runner_sha256=sha(runner.encode()),
                    gate_config=dict(path="/workspace/intelligentai/releases/" + CORE_DIRECTORY + "/calibration/content_gate_removed_v1.json",
                                     sha256=content_sha))
    files["config/release_preflight_identity.json"] = encode(identity)
    manifest.update(release_version=CORE_DIRECTORY, parent_manifest_sha256=CORE_PARENT,
                    release_root="/workspace/intelligentai/releases/" + CORE_DIRECTORY,
                    deployed=False, production_modified=False, execution_profile="native-only-resident-v1",
                    calibration_status=policy["status"], removed_inference_sources=sorted(removed),
                    verification_scope="source integrity and compilation only; calibration and developer QA required")
    manifest["file_sha256"] = {name: sha(raw) for name, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files["release_manifest.json"] = core_raw
    # Check both packages before any output is created.
    _, llm = load_files(h5_source, "contracts/llm/dependency_lock.json", H5_LOCK, "files")
    parent = (h5_source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    if sha(parent) != H5_PARENT:
        raise ValueError("NATIVE_H5_PARENT")
    adapter = replace_once(llm["bridge/adapter.py"].decode(), 'PARENT = "' + CORE_PARENT + '"', 'PARENT = "' + sha(core_raw) + '"')
    adapter += "\n" + (Path(__file__).parent / "harnesses/canonical_native_bridge.py").read_text(encoding="utf-8")
    llm["bridge/adapter.py"] = adapter.encode()
    for name, raw in sources.items():
        llm["bridge/" + name + ".py"] = raw
    llm["contracts/llm/native_phone_assessment.json"] = encode(policy)
    rubric = json.loads((app / "configs/pronunciation_feedback/phone_rubric_20261003_v1.json").read_bytes())
    rubric["rubricRevision"] = "phone-rubric-native-v1"
    llm["contracts/llm/scoring_rubric.json"] = encode(rubric)
    llm["contracts/llm/scoring_system_prompt.txt"] = (Path(__file__).parent / "harnesses/phone_rubric_prompt.txt").read_bytes().replace(b"\r\n", b"\n")
    component = json.loads(llm["contracts/llm/component_identity.json"])
    component["content_gate_config_sha256"] = content_sha
    llm["contracts/llm/component_identity.json"] = encode(component)
    parent_schema = json.loads(llm["contracts/llm/canonical_parent.schema.json"])
    parent_schema["properties"]["nativeScoringEvidence"] = json.loads((app / "docs/contracts/native_phone_evidence.schema.json").read_bytes())
    parent_schema["required"].append("nativeScoringEvidence")
    parent_schema["properties"]["nativeScoringLogits"] = json.loads((app / "docs/contracts/native_logits.schema.json").read_bytes())
    parent_schema["required"].append("nativeScoringLogits")
    parent_schema["properties"]["core_result"]["$defs"]["AICoreMetadata"]["properties"]["stage1_model_safetensors_sha256"] = {"type": "null"}
    llm["contracts/llm/canonical_parent.schema.json"] = encode(parent_schema)
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(llm.items())}))
    lm = encode(dict(contract_version=LLM_DIRECTORY, parent_llm_manifest_sha256=H5_PARENT,
                     parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock),
                     prompt_sha256=sha(llm["contracts/llm/llm_system_prompt.txt"]),
                     scoring_rubric_sha256=sha(llm["contracts/llm/scoring_rubric.json"]),
                     scoring_prompt_sha256=sha(llm["contracts/llm/scoring_system_prompt.txt"]),
                     assessment_policy_sha256=sha(llm["contracts/llm/native_phone_assessment.json"]),
                     calibration_status=policy["status"], execution_profile="native-only-resident-v1"))
    llm["contracts/llm/dependency_lock.json"], llm["contracts/llm/llm_contract_manifest.json"] = lock, lm
    if (destination / CORE_DIRECTORY).exists() or (destination / LLM_DIRECTORY).exists():
        raise ValueError("DERIVATIVE_REQUIRES_NEW_DIRECTORY")
    for name, raw in (files | {"llm/" + n: v for n, v in llm.items()}).items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    write_release(destination / CORE_DIRECTORY, files)
    write_release(destination / LLM_DIRECTORY, llm)
    return dict(coreManifestSha256=sha(core_raw), manifestSha256=sha(lm), lockSha256=sha(lock),
                calibrationStatus=policy["status"], executionApproved=False,
                removedInferenceModules=len(removed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-source", type=Path, required=True)
    parser.add_argument("--h5-source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core_source, args.h5_source, args.destination, args.policy)))
