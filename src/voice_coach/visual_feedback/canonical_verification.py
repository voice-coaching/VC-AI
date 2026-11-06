"""Offline replay of visual evidence, with no media decoder/model/provider calls."""
from __future__ import annotations

from .canonical_service import EVIDENCE_SCHEMA, assess_aligned
from .feature_protocol import MAPPING, PROTOCOL, strict_json
from .parallel_analysis import MediaIntegrityFailure
from .reference_catalog import ReferenceCatalog, sha

FAILURE_REASONS = {
    "VIDEO_TIMEOUT", "VIDEO_PROCESSING_FAILED", "NO_FACE", "MULTIPLE_FACES", "LOW_VISIBILITY",
    "SYNC_UNCERTAIN", "UNSUPPORTED_ORIENTATION", "FRAME_LIMIT_EXCEEDED", "ALIGNMENT_UNAVAILABLE",
}


def require(ok):
    if not ok:
        raise MediaIntegrityFailure("VISUAL_EVIDENCE_MISMATCH")


def verify_visual_evidence(raw: bytes, *, core: dict, request, catalog: ReferenceCatalog,
                           expected_model_sha256: str, prepared_audio, core_raw=None):
    """Return evidence only after identity, structure, score and observation replay.

This verifies consistency of retained extraction evidence. It does not certify
expert calibration quality or cryptographically prove that MediaPipe executed.
Authenticated producer provenance is a separate handoff responsibility.
"""
    require(0 < len(raw) <= 16 * 1024**2 and request.visual_input is not None)
    value = strict_json(raw)
    keys = {"schemaVersion", "requestId", "executionId",
        "audioSha256", "videoSha256", "preparedWavSha256", "referenceCatalogSha256", "mappingRevision",
        "distancePolicyRevision", "sourcePcm", "preparedPcm", "timeMap", "decoder", "frames", "visual"}
    require(type(value) is dict and set(value) in (keys, keys | {"alignmentSidecar"}))
    require(value["schemaVersion"] == EVIDENCE_SCHEMA and value["requestId"] == str(request.event_id)
        and value["executionId"] == str(request.execution_id) and value["audioSha256"] == request.audio_sha256
        and value["videoSha256"] == request.visual_input.sha256
        and value["preparedWavSha256"] == core.get("prepared_wav_sha256")
        and value["referenceCatalogSha256"] == catalog.sha256 and value["mappingRevision"] == MAPPING.revision
        and value["distancePolicyRevision"] == catalog.policy.revision)
    decoder = value["decoder"]
    if decoder is None:
        require("alignmentSidecar" not in value)
        visual = value["visual"]
        require(type(visual) is dict and set(visual) == {"status", "correctiveClaimsAllowed", "score",
            "scoreKind", "coverage", "phoneAssessments", "reasonCodes", "alignmentScope", "unresolvedExpectedIndices"})
        require(visual["status"] in {"UNSCORABLE", "FAILED", "TIMEOUT"} and visual["correctiveClaimsAllowed"] is False
            and visual["score"] is None and visual["scoreKind"] == "NONE" and visual["phoneAssessments"] == []
            and visual["unresolvedExpectedIndices"] == [] and value["frames"] == [] and value["sourcePcm"] is None
            and value["preparedPcm"] is None and value["timeMap"] is None
            and visual["coverage"] == dict(targetCount=0, visuallyEligibleCount=0, observedCount=0, scoredCount=0, coverage=None)
            and visual["alignmentScope"] == "CANONICAL_MFA_DETECTOR_PHONES_V1")
        require(type(visual["reasonCodes"]) is list and len(visual["reasonCodes"]) == 1
                and visual["reasonCodes"][0] in FAILURE_REASONS)
        if visual["status"] == "TIMEOUT":
            require(visual["reasonCodes"] == ["VIDEO_TIMEOUT"])
        elif visual["status"] == "FAILED":
            require(visual["reasonCodes"] == ["VIDEO_PROCESSING_FAILED"])
        else:
            require(visual["reasonCodes"][0] not in {"VIDEO_TIMEOUT", "VIDEO_PROCESSING_FAILED"})
        return value
    require(type(decoder) is dict and set(decoder) == {"videoSha256", "modelSha256", "audioClock",
                                                    "decodedPcmSha256", "decodedPcmFrames", "decodedPcmSampleRate"})
    require(decoder["videoSha256"] == request.visual_input.sha256
            and decoder["modelSha256"] == sha(expected_model_sha256) and decoder["decodedPcmSampleRate"] == 16000)
    for pcm in (value["sourcePcm"], value["preparedPcm"]):
        if pcm is not None:
            require(type(pcm) is dict and set(pcm) == {"sha256", "frames", "sampleRate"})
            sha(pcm["sha256"])
            require(type(pcm["frames"]) is int and 0 < pcm["frames"] <= 120 * 16000 and pcm["sampleRate"] == 16000)
    require(prepared_audio is not None and prepared_audio.sha256 == value["preparedWavSha256"])
    if value["preparedPcm"] is not None:
        require(value["preparedPcm"]["frames"] == prepared_audio.frame_count
                and value["preparedPcm"]["sampleRate"] == prepared_audio.sample_rate)
    clock = decoder["audioClock"]
    if clock is None:
        require(decoder["decodedPcmSha256"] is None and decoder["decodedPcmFrames"] is None)
    else:
        require(type(clock) is dict and set(clock) == {"firstPtsNumerator", "firstPtsDenominator", "source", "sampleRate"})
        require(clock["source"] == "CONTINUOUS_DECODED_AUDIO_FRAMES"
                and type(clock["sampleRate"]) is int and 8000 <= clock["sampleRate"] <= 192000
                and type(clock["firstPtsNumerator"]) is int and type(clock["firstPtsDenominator"]) is int
                and 1 <= clock["firstPtsDenominator"] <= 10**9
                and abs(clock["firstPtsNumerator"]) <= 86400 * clock["firstPtsDenominator"])
        sha(decoder["decodedPcmSha256"])
        require(type(decoder["decodedPcmFrames"]) is int and 0 < decoder["decodedPcmFrames"] <= 120 * 16000)
    # Sparse retained frames must include all frames of each consumed phone
    # window. This completeness is attested by the producer; replay ensures
    # scores cannot be substituted or recalculated from a different reference.
    features = dict(decoder, features=dict(protocol=PROTOCOL, mappingRevision=MAPPING.revision, frames=value["frames"]))
    replay = assess_aligned(core=core, request=request, feature_value=features,
        source_pcm=value["sourcePcm"], prepared_pcm=value["preparedPcm"], catalog=catalog,
        alignment_evidence=value.get("alignmentSidecar"), core_raw=core_raw)
    require(replay == value)
    return value
