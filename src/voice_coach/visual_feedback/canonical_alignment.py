"""Convert only verified canonical MFA locations; never substitute CTC time spans."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import stat
import wave

from .audiovisual_evidence import PhoneAnchor, VerifiedTimeMap
from .parallel_analysis import MediaIntegrityFailure


@dataclass(frozen=True)
class AlignmentAnchors:
    anchors: tuple[PhoneAnchor, ...]
    unresolved_indices: tuple[int, ...]
    target_count: int
    scope: str = "CANONICAL_PRONUNCIATION_EVIDENCE_CONSONANTS"


def anchors_from_core(core: dict, *, request_id: str, audio_sha256: str, prepared_sha256: str):
    if (core.get("request_id") != request_id or core.get("source_audio_sha256") != audio_sha256
            or core.get("prepared_wav_sha256") != prepared_sha256):
        raise MediaIntegrityFailure("MFA_ATTEMPT_MISMATCH")
    rows = core.get("pronunciation_evidence")
    if type(rows) is not list or len(rows) > 2048:
        raise MediaIntegrityFailure("MFA_EVIDENCE_INVALID")
    indices = [r.get("expected_index") for r in rows]
    if (any(type(i) is not int or i < 0 for i in indices) or indices != sorted(set(indices))):
        raise MediaIntegrityFailure("MFA_INDEX_MISMATCH")
    anchors, unresolved = [], []
    previous_end = 0.0
    for row in rows:
        times = [row.get(k) for k in ("word_start_s", "phone_start_s", "phone_end_s", "word_end_s")]
        valid = (core.get("mfa_receipt") is not None and row.get("localization_status") in {
                    "EXACT_COUNT_WORD_INDEX", "UNIQUE_MONOTONIC_CONSONANT_MATCH"}
                 and row.get("role") in {"onset", "nucleus", "coda"}
                 and type(row.get("mfa_ipa")) is str and bool(row["mfa_ipa"])
                 and type(row.get("word")) is str and bool(row["word"])
                 and type(row.get("expected_phone")) is str and bool(row["expected_phone"])
                 and all(type(t) in (int, float, Decimal) and math.isfinite(t) for t in times))
        if valid:
            ws, start, end, we = map(float, times)
            valid = 0 <= ws <= start < end <= we <= 120 and start >= previous_end
        if not valid:
            unresolved.append(row["expected_index"])
            continue
        previous_end = end
        anchors.append(PhoneAnchor(row["expected_index"], row["expected_phone"], row["role"],
            "word:" + row["word"], start, end, "MFA_LOCATION_ONLY"))
    # This frozen core exports only consonant locations. Do not invent vowel
    # spans from full-phone CTC/GOP indices or claim whole-utterance coverage.
    return AlignmentAnchors(tuple(anchors), tuple(unresolved), len(rows))


def pcm_identity(path: Path, expected_file_sha256: str):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 16 * 1024**2
                or hashlib.file_digest(stream, "sha256").hexdigest() != expected_file_sha256):
            raise MediaIntegrityFailure("PCM_IDENTITY_MISMATCH")
        stream.seek(0)
        with wave.open(stream, "rb") as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getcomptype()) != (1, 2, 16000, "NONE"):
                return None
            frames = reader.getnframes()
            if not 0 < frames <= 120 * 16000:
                return None
            digest, size = hashlib.sha256(), 0
            while chunk := reader.readframes(8192):
                size += len(chunk)
                if size > frames * 2:
                    raise MediaIntegrityFailure("PCM_LENGTH_MISMATCH")
                digest.update(chunk)
            if size != frames * 2:
                raise MediaIntegrityFailure("PCM_LENGTH_MISMATCH")
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise MediaIntegrityFailure("PCM_CHANGED")
    return dict(sha256=digest.hexdigest(), frames=frames, sampleRate=16000)


def verified_time_map(*, source_pcm, prepared_pcm, prepared_sha256: str, video_sha256: str, features: dict):
    if features["videoSha256"] != video_sha256:
        raise MediaIntegrityFailure("VIDEO_ATTEMPT_MISMATCH")
    clock = features["audioClock"]
    if source_pcm is None or prepared_pcm is None or clock is None:
        return None
    extracted = dict(sha256=features["decodedPcmSha256"], frames=features["decodedPcmFrames"],
                     sampleRate=features["decodedPcmSampleRate"])
    if source_pcm != prepared_pcm or source_pcm != extracted:
        # Different decoding/preparation is not evidence of zero offset or a
        # fitted time warp. Preserve audio analysis but withhold phone matching.
        return None
    offset = Fraction(clock["firstPtsNumerator"], clock["firstPtsDenominator"])
    return VerifiedTimeMap(prepared_sha256, video_sha256, 1, float(offset),
                           "SAMPLE_EXACT_PCM_AND_CONTINUOUS_VIDEO_AUDIO_V1")
