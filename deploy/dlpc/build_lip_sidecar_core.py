"""Package a pinned derived CORE exporting a separate MFA alignment artifact.

The original envelope/schema and Seungun calculations stay untouched. This
does not register a harness, execute inference or activate production.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod"))
from release_packaging import encode, load_files, replace_once, sha, write_release

NAME = "canonical_lip_sidecar_core_20261106_v1"


def patch_runner(raw):
    runner = raw.decode("utf-8")
    marker = "            dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=str)+'\\n')\n"
    return replace_once(runner, marker, marker +
        "            from canonical_mfa_sidecar_export import write_sidecar\n"
        "            write_sidecar(dest, state, legacy, p1.get_locations)\n").encode("utf-8")


def build(source, destination, *, expected_parent_sha256, directory=NAME):
    if not isinstance(directory, str) or re.fullmatch(r"[a-z0-9_]{1,96}", directory) is None:
        raise ValueError("LIP_CORE_DIRECTORY_INVALID")
    manifest, files = load_files(source, "release_manifest.json", expected_parent_sha256, "file_sha256")
    path = "source/scripts/run_canonical_batch.py"
    runner = patch_runner(files[path]).decode("utf-8")
    runner = replace_once(runner, "            base_deps = pipeline_mod.PipelineDependencies()\n",
        "            from canonical_mfa_time_bounds import parse_textgrid as parse_pcm_bounded_textgrid\n"
        "            base_deps = dataclasses.replace(pipeline_mod.PipelineDependencies(),\n"
        "                parse_textgrid=parse_pcm_bounded_textgrid)\n")
    files[path] = runner.encode("utf-8")
    host_path = "scripts/blackbox_host.py"
    host = files[host_path].decode("utf-8")
    host = replace_once(host, " from korean_pronunciation_poc.textgrid import parse_textgrid\n",
                       " from mfa_time_bounds import parse_textgrid\n")
    host = replace_once(host, '"textgrid_sha256":sha(rec["output"])},',
        '"textgrid_sha256":sha(rec["output"]),"time_bounds_normalization":parsed.bounds_normalization},')
    files[host_path] = host.encode("utf-8")
    files["scripts/mfa_time_bounds.py"] = (Path(__file__).parent / "audiovisual/mfa_time_bounds.py").read_bytes()
    files["source/scripts/canonical_mfa_time_bounds.py"] = files["scripts/mfa_time_bounds.py"]
    for origin, target in (("mfa_alignment.py", "canonical_mfa_alignment.py"),
                           ("mfa_sidecar_export.py", "canonical_mfa_sidecar_export.py")):
        files["source/scripts/" + target] = (Path(__file__).parent / "audiovisual" / origin).read_bytes()
    preflight = json.loads(files["config/release_preflight_identity.json"])
    preflight.update(release_blackbox_runner_sha256=sha(files[path]), status="LIP_SIDECAR_QA_REQUIRED")
    files["config/release_preflight_identity.json"] = encode(preflight)
    manifest.update(release_version=directory, parent_manifest_sha256=expected_parent_sha256,
        release_root="/workspace/intelligentai/releases/" + directory, deployed=False,
        production_modified=False, verification_scope="source packaging; actual analysis QA pending",
        alignmentArtifact="results/{requestId}.alignment.json", coreOutputMutation=False)
    manifest["file_sha256"] = {n: sha(raw) for n, raw in sorted(files.items())}
    raw = encode(manifest)
    files["release_manifest.json"] = raw
    write_release(destination / directory, files)
    return dict(name=directory, coreManifestSha256=sha(raw), productionAllowlisted=False, developerQaVerified=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--expected-parent-sha256", required=True)
    parser.add_argument("--directory", default=NAME)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, expected_parent_sha256=args.expected_parent_sha256,
                           directory=args.directory)))
