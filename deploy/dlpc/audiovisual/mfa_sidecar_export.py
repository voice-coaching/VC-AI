"""Export already-computed MFA locations after the original CORE is written.

No second alignment, original-JSON mutation or synthetic timing. The CORE
output file is read-only; failures leave the audio result available.
"""
import hashlib
import json
import os

from canonical_mfa_alignment import export_alignment


def write_sidecar(core_path, state, legacy, locator):
    try:
        raw = core_path.read_bytes()
        core = json.loads(raw)
        if core.get("mfa_receipt") is None or state.get("phone_contract") is None:
            return
        alignment = export_alignment(state["phone_contract"], legacy, core["mfa_receipt"], locator)
        receipt = json.dumps(core["mfa_receipt"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()
        value = dict(schemaVersion="voice-coaching.mfa-alignment-sidecar.v1", requestId=core["request_id"],
            coreSha256=hashlib.sha256(raw).hexdigest(), sourceAudioSha256=core["source_audio_sha256"],
            preparedWavSha256=core["prepared_wav_sha256"], mfaReceiptSha256=hashlib.sha256(receipt).hexdigest(),
            alignment=alignment)
        data = (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()
        if len(data) > 1024**2:
            return
    except Exception:
        return
    path = core_path.with_suffix(".alignment.json")
    pending = core_path.with_suffix(".alignment.pending")
    created = False
    try:
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-replace publication: a partial optional artifact never
        # becomes a reason for an otherwise valid audio analysis to fail.
        os.link(pending, path, follow_symlinks=False)
    except OSError:
        return
    finally:
        if created:
            try:
                pending.unlink()
            except OSError:
                pass
