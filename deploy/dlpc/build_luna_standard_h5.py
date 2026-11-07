"""Derive Luna/none/Standard from the pinned lip release, preserving its evidence and prompts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/runpod"))
sys.path.insert(0, str(ROOT / "src"))
from release_packaging import encode, load_files, sha, write_release
from voice_coach.six_dimension.policy import validate_policy

PARENTS = {
    "direct": "382c46531b72ecc8d6909aef6c1d4527ee4ede7aa4fcb780328c751b5bbfbb6a",
    "internal": "ed86be97bf36354f3b38e36c4374b695988be6044c0cfbfc46e7eb3eb3c2bd44",
}


def build(source: Path, destination: Path, *, lane: str) -> dict:
    parent_raw = (source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    if sha(parent_raw) != PARENTS[lane]:
        raise ValueError("LUNA_STANDARD_PARENT")
    parent = json.loads(parent_raw)
    _, files = load_files(source, "contracts/llm/dependency_lock.json",
                          parent["dependency_lock_sha256"], "files")
    config_name = "contracts/llm/request_config.json"
    policy_name = "contracts/llm/six_dimension_policy.json"
    config = json.loads(files[config_name])
    policy = json.loads(files[policy_name])
    if (config["model"], config["reasoning_effort"], config["service_tier"]) != (
            "gpt-6.1-terra", "none", "default") or policy["request"] != config:
        raise ValueError("LUNA_STANDARD_PARENT_REQUEST")
    config["model"] = "gpt-6-luna"
    policy["request"] = config
    validate_policy(policy)
    files[config_name] = encode(config)
    files[policy_name] = encode(policy)
    # Only the model compatibility predicate changes in the frozen calculator code.
    name = "bridge/policy.py"
    old = files[name].decode("utf-8")
    before = '(config["model"] == "gpt-6.1-terra" and config["reasoning_effort"] == "none")'
    after = '(config["model"] in {"gpt-6.1-terra", "gpt-6-luna"}\n                  and config["reasoning_effort"] == "none")'
    if old.count(before) != 1:
        raise ValueError("LUNA_STANDARD_POLICY_CONTEXT")
    files[name] = old.replace(before, after).encode("utf-8")
    if files[name] != (ROOT / "src/voice_coach/six_dimension/policy.py").read_bytes():
        raise ValueError("LUNA_STANDARD_POLICY_SOURCE")
    changed = {config_name, policy_name, name}
    _, previous = load_files(source, "contracts/llm/dependency_lock.json",
                             parent["dependency_lock_sha256"], "files")
    if {name for name in files if files[name] != previous[name]} != changed:
        raise ValueError("LUNA_STANDARD_UNEXPECTED_CHANGE")
    directory = f"canonical_luna_standard_{lane}_20261107_v1_h5"
    lock = encode({"files": {name: sha(raw) for name, raw in sorted(files.items())}})
    manifest = dict(parent, contract_version=directory,
                    parent_llm_manifest_sha256=sha(parent_raw),
                    dependency_lock_sha256=sha(lock), policy_sha256=sha(files[policy_name]))
    manifest["model_migration"] = dict(from_model="gpt-6.1-terra", to_model="gpt-6-luna",
                                      reasoning_effort="none", service_tier="default")
    manifest_raw = encode(manifest)
    files["contracts/llm/dependency_lock.json"] = lock
    files["contracts/llm/llm_contract_manifest.json"] = manifest_raw
    target = destination / directory
    write_release(target, files)
    return dict(root=str(target), revision=directory.replace("_", "-").removesuffix("-h5"),
                coreSha256=parent["parent_core_manifest_sha256"],
                manifestSha256=sha(manifest_raw), lockSha256=sha(lock),
                promptSha256=parent["prompt_sha256"], changedFiles=sorted(changed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--lane", choices=tuple(PARENTS), required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, lane=args.lane)))
