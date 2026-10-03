"""Package a separate H5 derivative of pinned S7; no imports or inference runs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PARENT_MANIFEST = "1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73"
PARENT_LOCK = "d8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01"
REVISION = "canonical-h5-20261003-v1"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def build(source: Path, target: Path, policy: Path):
    source = source.resolve(strict=True)
    target = target.resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("H5_REQUIRES_SEPARATE_NEW_DIRECTORY")
    manifest_raw = (source / "contracts/llm/llm_contract_manifest.json").read_bytes()
    lock_raw = (source / "contracts/llm/dependency_lock.json").read_bytes()
    if sha(manifest_raw) != PARENT_MANIFEST or sha(lock_raw) != PARENT_LOCK:
        raise ValueError("S7_PARENT_IDENTITY_MISMATCH")
    files = {}
    for name, digest in json.loads(lock_raw)["files"].items():
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source):
            raise ValueError("S7_DEPENDENCY_PATH_INVALID")
        files[name] = path.read_bytes()
        if sha(files[name]) != digest:
            raise ValueError("S7_DEPENDENCY_MISMATCH")
    adapter = files["bridge/adapter.py"].decode()
    replacements = {
        "template_rows, _text\n": "template_rows, _text, _action\n",
        "class Bridge:\n": "class Bridge:\n    validate_action = staticmethod(_action)\n",
        '            for k in ("explanation","action","practice","selfCheck"):\n':
            '            _action(item["action"])\n            for k in ("explanation","action","practice","selfCheck"):\n',
    }
    for before, after in replacements.items():
        if adapter.count(before) != 1:
            raise ValueError("S7_ADAPTER_PATCH_CONTEXT_CHANGED")
        adapter = adapter.replace(before, after)
    files["bridge/adapter.py"] = adapter.encode()
    files["bridge/legacy_pure.py"] += b"\n" + (policy / "canonical_h5_policy.py").read_bytes().replace(b"\r\n", b"\n")
    files["contracts/llm/llm_system_prompt.txt"] += b"\n" + (policy / "canonical_h5_prompt.txt").read_bytes().replace(b"\r\n", b"\n")
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    lock = encode(dict(files={name: sha(raw) for name, raw in sorted(files.items())}))
    manifest = encode(dict(
        contract_version=REVISION, parent_llm_manifest_sha256=PARENT_MANIFEST,
        parent_llm_lock_sha256=PARENT_LOCK, parent_core_release=json.loads(manifest_raw)["parent_core_release"],
        prompt_sha256=sha(files["contracts/llm/llm_system_prompt.txt"]),
        dependency_lock_sha256=sha(lock), request_settings=json.loads(files["contracts/llm/request_config.json"]),
        changed_scope="H5 expression length, action validation and deterministic expression fallback only",
        preserved_scope="S7 prepare selection, scoped identity, guidance, MFA, permissions, references, model and request limits",
        verification_scope="source integrity and compilation only; no inference or quality QA"))
    files["contracts/llm/dependency_lock.json"] = lock
    files["contracts/llm/llm_contract_manifest.json"] = manifest
    target.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return dict(revision=REVISION, manifestSha256=sha(manifest), lockSha256=sha(lock),
                promptSha256=sha(files["contracts/llm/llm_system_prompt.txt"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.target, Path(__file__).parent / "harnesses")))
