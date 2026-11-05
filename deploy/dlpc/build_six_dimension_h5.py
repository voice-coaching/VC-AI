"""Build a pinned service H5; reuses the selected existing CORE without inference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/runpod"))
sys.path.insert(0, str(ROOT / "src"))
from release_packaging import encode, load_files, sha, write_release
from voice_coach.six_dimension.policy import load_policy
from voice_coach.backend_analysis.phone_rubric import validate_policy as validate_rubric
from voice_coach.six_dimension.composite import load_composite_policy, validate_reference_asset

DIRECTORY = "canonical_six_dimension_direct_20261105_v5_h5"


def build(source: Path, core_manifest: Path, destination: Path, *, directory: str = DIRECTORY,
          composite_policy_path: Path | None = None, reference_path: Path | None = None):
    parent_raw = (source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    parent = json.loads(parent_raw)
    _, inherited = load_files(source, "contracts/llm/dependency_lock.json", parent["dependency_lock_sha256"], "files")
    core_sha = sha(core_manifest.read_bytes())
    policy_path = ROOT / "configs/pronunciation_feedback/six_dimension_v1.json"
    policy, policy_sha = load_policy(policy_path)
    composite_policy = None
    if composite_policy_path is not None:
        composite_policy, composite_sha = load_composite_policy(composite_policy_path)
        reference_raw = (reference_path or ROOT / "standalone/seungun/assets/reference.json").read_bytes()
        validate_reference_asset(composite_policy, reference_raw)
        if directory == DIRECTORY:
            directory = "canonical_composite_exponential_direct_20261105_v2_h5"
    files = {"bridge/adapter.py": (ROOT / "deploy/runpod/harnesses/six_dimension_bridge.py").read_bytes().replace(
                 b"__CORE_MANIFEST__", core_sha.encode()),
             "contracts/llm/canonical_parent.schema.json": inherited["contracts/llm/canonical_parent.schema.json"],
             "contracts/llm/six_dimension_policy.json": policy_path.read_bytes(),
             "contracts/llm/llm_system_prompt.txt": (ROOT / "configs/coaching/six_dimension_system_v1.txt").read_bytes(),
             "contracts/llm/six_dimension_result.schema.json": (ROOT / "docs/contracts/six_dimension_result_v1.schema.json").read_bytes(),
             "contracts/llm/request_config.json": encode(policy["request"])}
    files["contracts/llm/llm_system_prompt.txt"] += (ROOT / "configs/coaching/six_dimension_legacy_grade_v1.txt").read_bytes()
    if composite_policy is not None:
        files["contracts/llm/llm_system_prompt.txt"] += (ROOT / "configs/coaching/composite_exponential_v2.txt").read_bytes()
        composite_raw = composite_policy_path.read_bytes()
        if sha(composite_raw) != composite_sha:
            raise ValueError("COMPOSITE_POLICY_CHANGED_DURING_BUILD")
        files["contracts/llm/composite_policy.json"] = composite_raw
        files["contracts/llm/composite_reference.json"] = reference_raw
    rubric_raw = (ROOT / "configs/pronunciation_feedback/phone_rubric_20261003_v1.json").read_bytes()
    rubric = json.loads(rubric_raw)
    validate_rubric(rubric)
    files["contracts/llm/scoring_rubric.json"] = rubric_raw
    output_schema = json.loads(inherited["contracts/llm/llm_output.schema.json"])
    ready = [branch["properties"]["result"]["properties"]["rubricRevision"]
             for branch in output_schema["properties"]["scoring"]["oneOf"]
             if "result" in branch.get("properties", {})]
    if len(ready) != 1:
        raise ValueError("SCORING_OUTPUT_SCHEMA_IDENTITY")
    ready[0]["const"] = rubric["rubricRevision"]
    files["contracts/llm/llm_output.schema.json"] = encode(output_schema)
    # Embed the original pure calculator, not the source package's import shim.
    files["bridge/phone_rubric.py"] = (ROOT / "src/voice_coach/backend_analysis/phone_rubric.py").read_bytes()
    for name in ("policy", "assessment", "composite", "pipeline"):
        files["bridge/"+name+".py"] = (ROOT / "src/voice_coach/six_dimension" / (name+".py")).read_bytes()
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest = encode(dict(contract_version=directory, parent_llm_manifest_sha256=sha(parent_raw),
                           parent_core_manifest_sha256=core_sha, dependency_lock_sha256=sha(lock),
                           prompt_sha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
                           policy_sha256=policy_sha, execution_profile=composite_policy["revision"] if composite_policy else "six-dimension-experimental-v1",
                           composite_policy_sha256=composite_sha if composite_policy else None,
                           scoring_authority="PROVISIONAL_DETERMINISTIC_POLICY", developerQaVerified=False,
                           core_input="ORIGINAL_UTF8_BYTES_SEPARATE_USER_MESSAGE",
                           legacy_scoring_authority="SERVER_FIXED_RUBRIC_OR_UNSCORABLE",
                           public_contract="EXISTING_SCORING_AND_FEEDBACK_STRING", six_scores="PRIVATE_ARTIFACT_ONLY",
                           composite_score="EXISTING_PUBLIC_SCORE_OVERALL_SCORE" if composite_policy else None))
    files["contracts/llm/dependency_lock.json"] = lock
    files["contracts/llm/llm_contract_manifest.json"] = manifest
    write_release(destination / directory, files)
    return dict(revision=directory.replace("_", "-").removesuffix("-h5"), coreSha256=core_sha,
                manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
                policySha256=policy_sha, root=str(destination / directory), productionActivated=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--core-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--directory", default=DIRECTORY)
    parser.add_argument("--composite-policy", type=Path)
    parser.add_argument("--reference-file", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.core_manifest, args.destination, directory=args.directory,
                          composite_policy_path=args.composite_policy, reference_path=args.reference_file)))
