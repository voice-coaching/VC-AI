"""Package original Native observations and one GPT rubric/coaching request."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

from native_base_source import CORE_PARENT, native_base
from native_resident_source import resident_runner
from native_mfa_source import resident_mfa
from native_source_pruning import prune
from release_packaging import encode, load_files, replace_once, sha, write_release

CORE_DIRECTORY = "canonical_seungun_gop_core_20261006_v1"
LLM_DIRECTORY = "canonical_seungun_gop_gpt_20261006_v1"
H5_PARENT = "9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45"
H5_LOCK = "93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5"


def build(core_source, h5_source, destination):
    app = Path(__file__).resolve().parents[2]
    manifest, files, identity, content_sha = native_base(core_source)
    removed = prune(files)
    resident_mfa(files)
    runner = resident_runner(files["source/scripts/run_canonical_batch.py"].decode(), include_scoring=False)
    gop = (Path(__file__).parent / "harnesses/seungun_gop.py").read_bytes().replace(b"\r\n", b"\n")
    files["source/scripts/canonical_native/seungun_gop.py"] = gop
    runner = replace_once(runner, "            marks['core_json_done'] = time.perf_counter()", """            from canonical_native.seungun_gop import extract as extract_gop
            marks['gop_start'] = time.perf_counter()
            if native is not None and 'phone_contract' in state:
                try:
                    envelope['phonemeAssessment'] = extract_gop(native,
                        [row.model_dump(mode='json') for row in state['phone_contract'].full_metadata], vr)
                except (ValueError, RuntimeError) as exc:
                    envelope['phonemeAssessment'] = {'revision': 'gop-ctc-af-sd-native-v1',
                        'status': 'UNAVAILABLE', 'errorCode': type(exc).__name__}
            marks['gop_done'] = time.perf_counter()
            marks['core_json_done'] = time.perf_counter()""")
    files["source/scripts/run_canonical_batch.py"] = runner.encode()
    files["scripts/run_resident.py"] = (Path(__file__).parent / "harnesses/canonical_resident_server.py").read_bytes().replace(b"\r\n", b"\n")
    launcher = ast.parse(files["scripts/run_release.py"])
    launcher.body = [n for n in launcher.body if not isinstance(n, ast.If) and not (
        isinstance(n, ast.FunctionDef) and n.name == "main")]
    files["scripts/run_release.py"] = (ast.unparse(launcher) + "\n").encode()
    identity.update(status="NATIVE_DERIVED_DEVELOPER_QA_REQUIRED", release_blackbox_runner_sha256=sha(runner.encode()),
        gate_config=dict(path="/workspace/intelligentai/releases/" + CORE_DIRECTORY + "/calibration/content_gate_removed_v1.json",
                         sha256=content_sha))
    files["config/release_preflight_identity.json"] = encode(identity)
    manifest.update(release_version=CORE_DIRECTORY, parent_manifest_sha256=CORE_PARENT,
        release_root="/workspace/intelligentai/releases/" + CORE_DIRECTORY,
        deployed=False, production_modified=False, execution_profile="seungun-gop-gpt-v1",
        removed_inference_sources=sorted(removed), verification_scope="source integrity and compilation; developer QA separate")
    manifest["file_sha256"] = {name: sha(raw) for name, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files["release_manifest.json"] = core_raw
    _, llm = load_files(h5_source, "contracts/llm/dependency_lock.json", H5_LOCK, "files")
    if sha((h5_source / "contracts/llm/llm_contract_manifest.json").read_bytes()) != H5_PARENT:
        raise ValueError("SEUNGUN_H5_PARENT")
    adapter = (Path(__file__).parent / "harnesses/seungun_gop_bridge.py").read_text(encoding="utf-8").replace("__CORE_MANIFEST__", sha(core_raw))
    llm = {name: raw for name, raw in llm.items() if name in {
        "contracts/llm/canonical_parent.schema.json", "contracts/llm/request_config.json"}}
    llm["bridge/adapter.py"] = adapter.encode()
    rubric = json.loads((app / "configs/pronunciation_feedback/phone_rubric_gop_ctc_af_sd_v1.json").read_bytes())
    llm["contracts/llm/scoring_rubric.json"] = encode(rubric)
    llm["contracts/llm/llm_system_prompt.txt"] = (Path(__file__).parent / "harnesses/seungun_gop_prompt.txt").read_bytes().replace(b"\r\n", b"\n")
    parent = json.loads(llm["contracts/llm/canonical_parent.schema.json"])
    parent["properties"]["core_result"]["$defs"]["AICoreMetadata"]["properties"]["stage1_model_safetensors_sha256"] = {"type": "null"}
    parent["properties"]["phonemeAssessment"] = {"type": "object", "required": ["revision"], "properties": {"revision": {"const": "gop-ctc-af-sd-native-v1"}}}
    llm["contracts/llm/canonical_parent.schema.json"] = encode(parent)
    output = {"type": "object", "additionalProperties": False, "required": ["feedback"], "properties": {"feedback": {"type": "string", "minLength": 1, "maxLength": 4000}}}
    criteria = [{"type": "object", "additionalProperties": False, "required": ["criterionId", "level"],
        "properties": {"criterionId": {"const": row["id"]}, "level": {"type": ["integer", "null"], "minimum": 0, "maximum": 4}}}
        for row in rubric["criteria"]]
    output["properties"]["scoring"] = {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["status"], "properties": {"status": {"const": "UNSCORABLE"}}},
        {"type": "object", "additionalProperties": False, "required": ["status", "result"], "properties": {
            "status": {"const": "READY"}, "result": {"type": "object", "additionalProperties": False,
            "required": ["rubricRevision", "criteria", "overallScore"], "properties": {
                "rubricRevision": {"const": "phone-rubric-gop-ctc-af-sd-v1"},
                "criteria": {"type": "array", "prefixItems": criteria, "items": False, "minItems": 9, "maxItems": 9},
                "overallScore": {"type": "number", "minimum": 0, "maximum": 100}}}}}]}
    output["required"].append("scoring")
    llm["contracts/llm/llm_output.schema.json"] = encode(output)
    config = json.loads(llm["contracts/llm/request_config.json"])
    config.update(max_completion_tokens=3000, scope="ONE_GPT_REQUEST_GOP_AND_SINGLE_RUBRIC")
    llm["contracts/llm/request_config.json"] = encode(config)
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(llm.items())}))
    lm = encode(dict(contract_version=LLM_DIRECTORY, parent_llm_manifest_sha256=H5_PARENT,
        parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock),
        prompt_sha256=sha(llm["contracts/llm/llm_system_prompt.txt"]),
        scoring_rubric_sha256=sha(llm["contracts/llm/scoring_rubric.json"]),
        execution_profile="seungun-gop-gpt-v1", scoring_authority="GPT_RUBRIC"))
    llm["contracts/llm/dependency_lock.json"], llm["contracts/llm/llm_contract_manifest.json"] = lock, lm
    if (destination / CORE_DIRECTORY).exists() or (destination / LLM_DIRECTORY).exists():
        raise ValueError("DERIVATIVE_REQUIRES_NEW_DIRECTORY")
    for name, raw in (files | {"llm/" + n: v for n, v in llm.items()}).items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    write_release(destination / CORE_DIRECTORY, files)
    write_release(destination / LLM_DIRECTORY, llm)
    return dict(coreManifestSha256=sha(core_raw), manifestSha256=sha(lm), lockSha256=sha(lock),
                promptSha256=sha(llm["contracts/llm/llm_system_prompt.txt"]), scoringAuthority="GPT_RUBRIC")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-source", type=Path, required=True)
    parser.add_argument("--h5-source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.core_source, args.h5_source, args.destination)))
