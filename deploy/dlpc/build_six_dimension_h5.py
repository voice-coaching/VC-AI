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

DIRECTORY = "canonical_six_dimension_20261104_v1_h5"


def build(source: Path, core_manifest: Path, destination: Path):
    parent_raw = (source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    parent = json.loads(parent_raw)
    _, inherited = load_files(source, "contracts/llm/dependency_lock.json", parent["dependency_lock_sha256"], "files")
    core_sha = sha(core_manifest.read_bytes())
    policy_path = ROOT / "configs/pronunciation_feedback/six_dimension_v1.json"
    policy, policy_sha = load_policy(policy_path)
    files = {"bridge/adapter.py": (ROOT / "deploy/runpod/harnesses/six_dimension_bridge.py").read_bytes().replace(
                 b"__CORE_MANIFEST__", core_sha.encode()),
             "contracts/llm/canonical_parent.schema.json": inherited["contracts/llm/canonical_parent.schema.json"],
             "contracts/llm/six_dimension_policy.json": policy_path.read_bytes(),
             "contracts/llm/llm_system_prompt.txt": (ROOT / "configs/coaching/six_dimension_system_v1.txt").read_bytes(),
             "contracts/llm/six_dimension_result.schema.json": (ROOT / "docs/contracts/six_dimension_result_v1.schema.json").read_bytes(),
             "contracts/llm/request_config.json": encode(policy["request"])}
    for name in ("policy", "assessment", "pipeline"):
        files["bridge/"+name+".py"] = (ROOT / "src/voice_coach/six_dimension" / (name+".py")).read_bytes()
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest = encode(dict(contract_version=DIRECTORY, parent_llm_manifest_sha256=sha(parent_raw),
                           parent_core_manifest_sha256=core_sha, dependency_lock_sha256=sha(lock),
                           prompt_sha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
                           policy_sha256=policy_sha, execution_profile="six-dimension-experimental-v1",
                           scoring_authority="PROVISIONAL_DETERMINISTIC_POLICY", developerQaVerified=False))
    files["contracts/llm/dependency_lock.json"] = lock
    files["contracts/llm/llm_contract_manifest.json"] = manifest
    write_release(destination / DIRECTORY, files)
    return dict(revision="canonical-six-dimension-20261104-v1", coreSha256=core_sha,
                manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
                policySha256=policy_sha, root=str(destination / DIRECTORY), productionActivated=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--core-manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.core_manifest, args.destination)))
