"""Package the six-dimension evaluator without altering any deployed H5 identity.

The resulting executable bundle reuses a bound Seungun analysis directory. It is
not admitted to canonical result.v6 and does not activate/restart any service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from voice_coach.six_dimension.policy import encode, load_policy


def build(destination: Path) -> dict:
    policy_path = ROOT / "configs/pronunciation_feedback/six_dimension_v1.json"
    policy, policy_sha = load_policy(policy_path)
    names = ["scripts/analyze_six_dimension.py", "configs/coaching/six_dimension_system_v1.txt",
             "configs/coaching/six_dimension_legacy_grade_v1.txt", "configs/coaching/composite_exponential_v2.txt",
             "configs/coaching/composite_exponential_v3.txt",
             "configs/coaching/specific_feedback_final_v2.txt",
             "configs/coaching/native_rubric_single_call_v1.txt",
             "configs/pronunciation_feedback/six_dimension_v1.json",
             "configs/pronunciation_feedback/composite_exponential_v2.json",
             "configs/pronunciation_feedback/composite_exponential_v3.json",
             "configs/pronunciation_feedback/phone_rubric_20261003_v1.json",
             "configs/pronunciation_feedback/native_phone_rubric_evidence_v1.json",
             "standalone/seungun/assets/reference.json",
             "docs/contracts/six_dimension_result_v1.schema.json",
             "docs/canonical-integration/six-dimension-feedback-v1.md",
             "docs/canonical-integration/composite-exponential-v2.md",
             "docs/canonical-integration/composite-exponential-v3.md",
             "docs/canonical-integration/score-forwarding-contract-20261105.md",
             "docs/canonical-integration/native-rubric-single-call-v1.md",
             "src/voice_coach/backend_analysis/secret_files.py",
             "src/voice_coach/backend_analysis/phone_rubric.py"]
    names.extend(str(path.relative_to(ROOT)) for path in (ROOT / "src/voice_coach/six_dimension").glob("*.py"))
    files = {name: (ROOT / name).read_bytes() for name in sorted(names)}
    # Isolate the installed runtime from unrelated repository modules.
    files["src/voice_coach/__init__.py"] = b""
    files["src/voice_coach/backend_analysis/__init__.py"] = b""
    files["requirements.txt"] = b"jsonschema==4.26.0\n"
    files["README.txt"] = (
        "Six-dimension evaluator: developer QA and expert calibration pending.\n"
        "Python 3.12. Install requirements, then run scripts/analyze_six_dimension.py\n"
        "with --analysis-dir, --script-file and a new --output directory.\n"
        "--audio requires the complete repository and prepared Seungun runtime.\n"
        "Opt into exponential v2 with --composite-policy configs/pronunciation_feedback/composite_exponential_v2.json.\n"
        "Use composite_exponential_v3.json for absolute normalized distance power 2/3.\n"
        "V2 preserves the fixed phone grade; missing phone evidence leaves the composite total unscorable.\n"
        "Add --native-rubric-policy configs/pronunciation_feedback/native_phone_rubric_evidence_v1.json\n"
        "to derive the fixed rubric from unchanged Native/GOP evidence, with one GPT request and no retries.\n"
        "This bundle does not implement canonical result.v6 or activate a service.\n").encode()
    for name, raw in files.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(json.loads(files["docs/contracts/six_dimension_result_v1.schema.json"]))
    manifest = dict(schemaVersion="voice-coaching.six-dimension-bundle.v1",
                    policyRevision=policy["revision"], policySha256=policy_sha,
                    validationStatus=policy["validationStatus"],
                    productionActivated=False, developerQaVerified=False,
                    files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()})
    manifest_raw = encode(manifest)
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError("DESTINATION_MUST_BE_NEW")
    destination.mkdir(parents=True)
    for name, raw in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    (destination / "manifest.json").write_bytes(manifest_raw)
    return dict(destination=str(destination), manifestSha256=hashlib.sha256(manifest_raw).hexdigest(),
                policySha256=policy_sha, productionActivated=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.destination)))
