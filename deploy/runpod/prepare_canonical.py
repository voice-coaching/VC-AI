"""Root boot preparation; encrypted writer envelope, isolated pinned interpreter.

Envelope key is derived from the existing private HTTP callback token supplied
by RunPod, not stored on the shared volume. Token rotation requires rewrapping.
No master B2 key, Backend DB credential or reader key belongs on this Pod.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path("/opt/intelligentai")
PYTHON = ROOT / "envs/canonical/bin/python"
CACHE = Path("/workspace/intelligentai/deployment/canonical-dependencies-v1")
ENVELOPE = Path("/workspace/intelligentai/deployment/private/canonical-writer-v1.enc.json")
WORK = Path("/run/intelligentai/canonical")
AAD = b"intelligentai/canonical-writer/v1/s8hb5b0l5k4qlx"


def decode(values):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    envelope = json.loads(ENVELOPE.read_bytes())
    if set(envelope) != {"version", "salt", "nonce", "ciphertext"} or envelope["version"] != 1:
        raise ValueError("canonical_envelope_invalid")
    key = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=base64.b64decode(envelope["salt"], validate=True), info=AAD).derive(
                   values["AI_ANALYSIS_CALLBACK_TOKEN"].encode())
    secret = json.loads(AESGCM(key).decrypt(
        base64.b64decode(envelope["nonce"], validate=True),
        base64.b64decode(envelope["ciphertext"], validate=True), AAD))
    expected = {"AI_CANONICAL_EVIDENCE_B2_KEY_ID", "AI_CANONICAL_EVIDENCE_B2_APPLICATION_KEY"}
    if set(secret) != expected or any(not isinstance(v, str) or not v or any(c in v for c in "\r\n\x00")
                                     for v in secret.values()):
        raise ValueError("canonical_envelope_fields")
    return secret


def prepare(values):
    values["AI_CANONICAL_BOOTSTRAP_READY"] = "false"
    # Backend verification/apply readiness is a separate server-side rollout.
    values["AI_CANONICAL_ADMISSION_ENABLED"] = "false"
    if not ENVELOPE.is_file():
        return
    lock = ROOT / "app/deploy/runpod/canonical-requirements.lock"
    if not PYTHON.is_file():
        subprocess.run(["/usr/bin/python3.12", "-m", "venv", str(PYTHON.parent.parent)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "--no-index", "--require-hashes",
                    "--find-links", str(CACHE), "-r", str(lock)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Root owns code/dependencies; service group may only read/execute.
    for parent, _, files in os.walk(PYTHON.parent.parent):
        os.chown(parent, 0, 10001)
        os.chmod(parent, 0o750)
        for name in files:
            p = Path(parent) / name
            if not p.is_symlink():
                executable = p.stat().st_mode & 0o111
                os.chown(p, 0, 10001)
                os.chmod(p, 0o750 if executable else 0o640)
    result = subprocess.run([str(PYTHON), str(Path(__file__)), "--decode"],
        input=json.dumps(values).encode(), capture_output=True, timeout=15,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    if result.returncode != 0:
        raise ValueError("canonical_secret_unavailable")
    values.update(json.loads(result.stdout))
    values.update({
        "AI_CANONICAL_PYTHON": str(PYTHON),
        "AI_CANONICAL_WORKSPACE_ROOT": str(WORK),
        "AI_CANONICAL_CORE_ROOT": "/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda",
        "AI_CANONICAL_LLM_ROOT": "/workspace/intelligentai/validation/canonical_llm_step7_20260928T154608Z_bc3aa1",
        "AI_CANONICAL_PIPELINE_REVISION": "canonical-R-421fda-S7-bc3aa1",
        "AI_CANONICAL_EVIDENCE_B2_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
        "AI_CANONICAL_EVIDENCE_B2_REGION": "us-east-005",
        "AI_CANONICAL_EVIDENCE_B2_BUCKET": "speakai2026",
        "AI_CANONICAL_EVIDENCE_B2_PREFIX": "canonical-evidence/v1/",
        "AI_CANONICAL_EVIDENCE_RETENTION_DAYS": "INDEFINITE",
        "AI_CANONICAL_EVIDENCE_DELETION_GRACE_HOURS": "INDEFINITE",
        "AI_CANONICAL_EVIDENCE_ORPHAN_GRACE_HOURS": "INDEFINITE",
    })
    WORK.mkdir(mode=0o700, exist_ok=True)
    if WORK.is_symlink() or WORK.resolve() != WORK:
        raise ValueError("canonical_workspace_invalid")
    os.chown(WORK, 10001, 10001)
    WORK.chmod(0o700)
    sys.path.insert(0, str(ROOT / "app/src"))
    from voice_coach.backend_analysis.canonical_settings import child_environment
    check = subprocess.run([str(PYTHON), "-B", "-m",
                            "voice_coach.backend_analysis.canonical_executor", "--check"],
        cwd=ROOT / "app", env=child_environment(values), user=10001, group=10001, extra_groups=[],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    if check.returncode != 0:
        raise ValueError("canonical_preparation_check_failed")
    values["AI_CANONICAL_BOOTSTRAP_READY"] = "true"
    # Actual admission additionally requires fresh authenticated Backend readiness
    # and identical schemas in HttpRuntime, never this configuration flag alone.
    values["AI_CANONICAL_ADMISSION_ENABLED"] = "true"


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--decode"] or os.geteuid() != 0:
            raise ValueError()
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError()
        print(json.dumps(decode(json.loads(raw))))
    except Exception:
        raise SystemExit(1)
