"""Build a new frozen core exporting real consonant AND vowel MFA locations.

Source packaging only. No inference, execution approval or deployment. Existing
P1 observations, legacy choice, GOP and Native forward are byte-preserved.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod"))
from release_packaging import encode, load_files, replace_once, sha, write_release

PARENT = "f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391"
NAME = "canonical_dlpc_audiovisual_core_20261007_v2"


def alignment_schema():
    nullable_time = dict(type=["number", "null"], minimum=0, maximum=120)
    properties = dict(expected_index=dict(type="integer", minimum=0, maximum=2047),
        source_expected_index=dict(type=["integer", "null"], minimum=0),
        expected_phone=dict(type="string", minLength=1, maxLength=20),
        source_phone=dict(type=["string", "null"], minLength=1, maxLength=20),
        word_index=dict(type="integer", minimum=0), word=dict(type="string", minLength=1, maxLength=200),
        role=dict(enum=["onset", "nucleus", "coda"]),
        localization_status=dict(enum=["EXACT_COUNT_WORD_INDEX", "UNIQUE_MONOTONIC_CONSONANT_MATCH",
            "WORD_INDEX_UNRESOLVED", "WORD_TEXT_UNRESOLVED", "UNRESOLVED_MONOTONIC_AMBIGUITY",
            "UNRESOLVED", "EXPECTED_CONTEXT_AMBIGUOUS", "VOWEL_LOCATION_UNRESOLVED"]),
        mfa_ipa=dict(type=["string", "null"], minLength=1, maxLength=40),
        **{key: nullable_time for key in ("word_start_s", "phone_start_s", "phone_end_s", "word_end_s")})
    return dict(type="object", additionalProperties=False,
        required=["schemaVersion", "indexNamespace", "rows"], properties=dict(
            schemaVersion=dict(const="voice-coaching.canonical-mfa-phone-alignment.v1"),
            indexNamespace=dict(const="DETECTOR_METADATA_V2"),
            rows=dict(type="array", maxItems=2048, items=dict(type="object", additionalProperties=False,
                required=list(properties), properties=properties))))


def build(source: Path, destination: Path):
    manifest, files = load_files(source, "release_manifest.json", PARENT, "file_sha256")
    path = "source/scripts/run_canonical_batch.py"
    runner = files[path].decode("utf-8")
    runner = replace_once(runner, "            from canonical_native.seungun_gop import extract as extract_gop",
        "            from canonical_mfa_alignment import export_alignment\n"
        "            envelope['mfa_phone_alignment'] = export_alignment(\n"
        "                state.get('phone_contract'), legacy, state.get('mfa_receipt'), p1.get_locations)\n"
        "            from canonical_native.seungun_gop import extract as extract_gop")
    files[path] = runner.encode("utf-8")
    files["source/scripts/canonical_mfa_alignment.py"] = (Path(__file__).parent /
        "audiovisual/mfa_alignment.py").read_bytes().replace(b"\r\n", b"\n")
    schema_path = "contracts/canonical_ai_output.schema.json"
    schema = json.loads(files[schema_path])
    schema["required"].append("mfa_phone_alignment")
    schema["properties"]["mfa_phone_alignment"] = alignment_schema()
    files[schema_path] = encode(schema)
    preflight = json.loads(files["config/release_preflight_identity.json"])
    preflight.update(release_blackbox_runner_sha256=sha(files[path]),
                     status="AUDIOVISUAL_DERIVED_DEVELOPER_QA_REQUIRED")
    files["config/release_preflight_identity.json"] = encode(preflight)
    manifest.update(release_version=NAME, parent_manifest_sha256=PARENT,
        release_root="/workspace/intelligentai/releases/" + NAME, deployed=False,
        production_modified=False, verification_scope="source compilation only; developer QA pending")
    manifest["file_sha256"] = {n: sha(raw) for n, raw in sorted(files.items())}
    raw = encode(manifest)
    files["release_manifest.json"] = raw
    write_release(destination / NAME, files)
    return dict(name=NAME, coreManifestSha256=sha(raw), productionAllowlisted=False,
                developerQaVerified=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination)))
