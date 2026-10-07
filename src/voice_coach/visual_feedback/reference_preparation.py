"""Prepare reviewable phone trajectories and compile explicitly reviewed references.

No calibration fitting, inferred expert labels, decoding or model execution.
Capture uses the same deterministic MFA/PTS join as the production service.
"""
from __future__ import annotations
import hashlib
import json
from types import SimpleNamespace

from .canonical_alignment import anchors_from_core
from .canonical_service import assess_aligned
from .feature_protocol import MAPPING, strict_json
from .reference_catalog import ReferenceCatalog, require, sha, text

CAPTURE_SCHEMA = "voice-coaching.lip-reference-capture.v1"
REVIEW_SCHEMA = "voice-coaching.lip-reference-review.v1"


def encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def capture_reference(*, core, core_raw, receipt, features, source_pcm, prepared_pcm, catalog):
    require(receipt["canonicalPcmSha256"] == core["source_audio_sha256"])
    request = SimpleNamespace(event_id=core["request_id"], execution_id=core["request_id"],
        audio_sha256=core["source_audio_sha256"], visual_input=SimpleNamespace(sha256=receipt["canonicalVideoSha256"]),
        media_preparation=dict(receipt=receipt))
    evidence = assess_aligned(core=core, request=request, feature_value=features,
        source_pcm=source_pcm, prepared_pcm=prepared_pcm, catalog=catalog)
    require(evidence["timeMap"] is not None)
    located = anchors_from_core(core, request_id=core["request_id"], audio_sha256=core["source_audio_sha256"],
                                prepared_sha256=core["prepared_wav_sha256"])
    anchors = {a.expected_index: a for a in located.anchors}
    phones = []
    for row in evidence["visual"]["phoneAssessments"]:
        # A capture records candidates for inspection, not reference approval.
        selected = [r for r in evidence["frames"] if row["videoStartSeconds"] <= r["pts"] < row["videoEndSeconds"]]
        valid = 3 <= len(selected) <= catalog.policy.max_frames and all(r["reason"] is None for r in selected)
        phones.append(dict(expectedIndex=row["expectedIndex"], expectedPhone=row["expectedPhone"], role=row["role"],
            context=anchors[row["expectedIndex"]].context, audioStartSeconds=row["audioStartSeconds"],
            audioEndSeconds=row["audioEndSeconds"], videoStartSeconds=row["videoStartSeconds"],
            videoEndSeconds=row["videoEndSeconds"], observable=valid,
            trajectory=None if not valid else dict(times=[r["pts"] for r in selected],
                landmarks=[r["landmarks"] for r in selected], features=[r["features"] for r in selected])))
    return dict(schemaVersion=CAPTURE_SCHEMA, sourceSha256=receipt["sourceSha256"],
        videoSha256=receipt["canonicalVideoSha256"], preparedWavSha256=core["prepared_wav_sha256"],
        coreSha256=hashlib.sha256(core_raw).hexdigest(), modelSha256=features["modelSha256"],
        mappingRevision=MAPPING.revision, alignmentScope=located.scope,
        unresolvedExpectedIndices=list(located.unresolved_indices), phones=phones)


def compile_reviewed_catalog(*, capture_raw, review_raw, base_raw, revision, model_sha256):
    """A reviewer selects actual measured spans. No labels are filled in by code."""
    require(0 < len(capture_raw) <= 8 * 1024**2 and 0 < len(review_raw) <= 256 * 1024)
    capture, review = strict_json(capture_raw), strict_json(review_raw)
    require(type(capture) is dict and set(capture) == {"schemaVersion", "sourceSha256", "videoSha256",
        "preparedWavSha256", "coreSha256", "modelSha256", "mappingRevision", "alignmentScope",
        "unresolvedExpectedIndices", "phones"})
    require(capture["schemaVersion"] == CAPTURE_SCHEMA and capture["mappingRevision"] == MAPPING.revision
        and capture["modelSha256"] == sha(model_sha256)
        and capture["alignmentScope"] == "CANONICAL_MFA_DETECTOR_PHONES_V1")
    require(type(review) is dict and set(review) == {"schemaVersion", "captureSha256", "expertReviewId", "approvedExpectedIndices"})
    capture_sha = hashlib.sha256(capture_raw).hexdigest()
    require(review["schemaVersion"] == REVIEW_SCHEMA and review["captureSha256"] == capture_sha)
    review_id = text(review["expertReviewId"])
    require(review_id != "PENDING")
    approved = review["approvedExpectedIndices"]
    require(type(approved) is list and approved and len(approved) <= 256
        and all(type(i) is int and 0 <= i < 2048 for i in approved) and approved == sorted(set(approved)))
    require(type(capture["phones"]) is list and len(capture["phones"]) <= 2048)
    phone_keys = {"expectedIndex", "expectedPhone", "role", "context", "audioStartSeconds", "audioEndSeconds",
                  "videoStartSeconds", "videoEndSeconds", "observable", "trajectory"}
    require(all(type(p) is dict and set(p) == phone_keys for p in capture["phones"]))
    indices = [p["expectedIndex"] for p in capture["phones"]]
    require(all(type(i) is int for i in indices) and indices == sorted(set(indices)))
    by_index = {p["expectedIndex"]: p for p in capture["phones"]}
    ReferenceCatalog.from_bytes(base_raw, hashlib.sha256(base_raw).hexdigest())
    catalog = strict_json(base_raw)
    require(text(revision) != catalog["revision"])
    catalog["revision"] = revision
    for index in approved:
        require(index in by_index and by_index[index]["observable"] is True)
        phone = by_index[index]
        provenance = {k: capture[k] for k in ("sourceSha256", "coreSha256", "preparedWavSha256")}
        provenance.update(captureSha256=capture_sha, expectedIndex=index,
            **{k: phone[k] for k in ("audioStartSeconds", "audioEndSeconds", "videoStartSeconds", "videoEndSeconds")})
        catalog["references"].append(dict(referenceId=f"{capture_sha}:phone-{index}",
            videoSha256=capture["videoSha256"], expectedPhone=phone["expectedPhone"], role=phone["role"],
            context=phone["context"], expertReviewId=review_id, provenance=provenance, trajectory=phone["trajectory"]))
    raw = encode(catalog)
    ReferenceCatalog.from_bytes(raw, hashlib.sha256(raw).hexdigest())
    return raw
