"""Install existing limited writer via stdin; emit no credential values."""
import base64
import json
import os
from pathlib import Path
import stat
import sys
import time
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def main():
    if os.geteuid() != 0:
        raise ValueError()
    os.umask(0o077)
    key = json.loads(sys.stdin.buffer.read(16385))
    if (key["applicationKeyId"] != "0055c17e18c6bc70000000005"
            or key["namePrefix"] != "canonical-evidence/v1/"
            or not key["bucketIds"] or len(key["bucketIds"]) != 1
            or not {"readFiles", "writeFiles"}.issubset(set(key["capabilities"]))
            or "deleteFiles" in key["capabilities"]
            or key["expirationTimestamp"] <= time.time() * 1000):
        raise ValueError()
    source = Path("/root/.config/intelligentai/analysis-http.env")
    info = source.lstat()
    if source.is_symlink() or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError()
    values = dict(line.split("=", 1) for line in source.read_text().splitlines()
                  if line and not line.startswith("#"))
    secret = {"AI_CANONICAL_EVIDENCE_B2_KEY_ID": key["applicationKeyId"],
              "AI_CANONICAL_EVIDENCE_B2_APPLICATION_KEY": key["applicationKey"]}
    aad = b"intelligentai/canonical-writer/v1/s8hb5b0l5k4qlx"
    salt, nonce = os.urandom(32), os.urandom(12)
    wrapping = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=aad).derive(
        values["AI_ANALYSIS_CALLBACK_TOKEN"].encode())
    ciphertext = AESGCM(wrapping).encrypt(nonce, json.dumps(secret).encode(), aad)
    out = Path("/workspace/intelligentai/deployment/private/canonical-writer-v1.enc.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"version": 1, "salt": base64.b64encode(salt).decode(),
                   "nonce": base64.b64encode(nonce).decode(),
                   "ciphertext": base64.b64encode(ciphertext).decode()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    print("canonical_writer_envelope_installed; plaintext_shared_volume=false")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("canonical_writer_injection_failed", file=sys.stderr)
        raise SystemExit(1)
