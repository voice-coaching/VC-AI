"""Additive v6 boundary; preserve and reuse all v5 audio semantic verification."""
from __future__ import annotations

from dataclasses import replace
import hashlib

from .canonical_bridge import _require, _encode, _json, _sha
from .canonical_codec import read_document, encode_document, validate_document
from .http_models import REQUEST_V3, RESULT_V5, RESULT_V6, HANDOFF_PROFILE, AUDIOVISUAL_PROFILE


def audio_request(request):
    return replace(request, visual_input=None, media_preparation=None,
                   request_schema_version=REQUEST_V3, result_schema_version=RESULT_V5,
                   analysis_profile=HANDOFF_PROFILE)


def audio_expression(result):
    association = _json(result.association_bytes)
    for key in ("videoSha256", "mediaReceiptSha256", "visualEvidenceSha256"):
        association.pop(key)
    association["visualStatus"] = "NOT_CONNECTED"
    return replace(result, association_bytes=_encode(association), media_receipt_bytes=None, visual_evidence_bytes=None)


def visual_assets(frozen):
    from .canonical_harness import AUDIOVISUAL
    from voice_coach.visual_feedback.reference_catalog import ReferenceCatalog
    _require(frozen.artifacts.identity == AUDIOVISUAL, "AUDIOVISUAL_HARNESS_REQUIRED")
    lock = frozen.artifacts.verify()
    root = frozen.artifacts.llm_root
    catalog_path = "contracts/visual/reference-catalog.json"
    catalog = ReferenceCatalog.load(root / catalog_path, lock["files"][catalog_path])
    configuration = _json((root / "contracts/visual/runtime-candidate.json").read_bytes())
    return catalog, configuration


def validated_visual(raw, *, request, frozen, core, prepared_audio):
    from .media_preparation import MediaPreparation
    from voice_coach.visual_feedback.canonical_verification import verify_visual_evidence
    _require(request.is_audiovisual and request.visual_input is not None, "AUDIOVISUAL_REQUEST_REQUIRED")
    MediaPreparation.from_wire(request.media_preparation, audio_sha256=request.audio_sha256,
                               video_sha256=request.visual_input.sha256)
    catalog, configuration = visual_assets(frozen)
    return verify_visual_evidence(raw, core=core, request=request, catalog=catalog,
        expected_model_sha256=configuration["modelSha256"], prepared_audio=prepared_audio)


def preflight(result, *, request, frozen, prepared_audio=None):
    from .canonical_result import preflight_canonical_result, CanonicalPreflight, PrivateArtifact
    from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
    _require(result.media_receipt_bytes is not None and result.visual_evidence_bytes is not None,
             "AUDIOVISUAL_ARTIFACTS_REQUIRED")
    core = _json(result.core_bytes)
    visual = validated_visual(result.visual_evidence_bytes, request=request, frozen=frozen,
                               core=core, prepared_audio=prepared_audio)
    _require(_json(result.media_receipt_bytes) == request.media_preparation["receipt"], "MEDIA_RECEIPT_MISMATCH")
    association = _json(result.association_bytes)
    _require({k: association[k] for k in ("videoSha256", "mediaReceiptSha256", "visualEvidenceSha256", "visualStatus")} == dict(
        videoSha256=request.visual_input.sha256, mediaReceiptSha256=request.media_preparation["receiptSha256"],
        visualEvidenceSha256=_sha(result.visual_evidence_bytes), visualStatus=visual["visual"]["status"]),
        "VISUAL_ASSOCIATION_MISMATCH")
    validate_document(association, "association", schema_name="v6")
    payload = prepare_visual_coaching(result.visual_evidence_bytes, core_bytes=result.core_bytes)
    base = preflight_canonical_result(audio_expression(result), request=audio_request(request),
                                     frozen=frozen, prepared_audio=prepared_audio, _visual_payload=payload)
    # Replay the actual audiovisual prompt construction without dispatch. The
    # base replay separately validates the original audio scoring and selection.
    artifacts = [PrivateArtifact(a.kind, "voice-coaching.canonical-association.v2", result.association_bytes)
                 if a.kind == "ASSOCIATION" else a for a in base.artifacts]
    artifacts.extend((PrivateArtifact("MEDIA_RECEIPT", "voice-coaching.media-preparation.v1", result.media_receipt_bytes),
                      PrivateArtifact("VISUAL_EVIDENCE", "voice-coaching.visual-evidence.v1", result.visual_evidence_bytes)))
    _require(all(0 < len(a.raw) <= 16 * 1024**2 for a in artifacts), "EVIDENCE_SIZE_INVALID")
    return CanonicalPreflight(result, base.association, tuple(artifacts))


def media_binding(request, visual_raw=None):
    return dict(videoSha256=request.visual_input.sha256,
        mediaReceiptSha256=request.media_preparation["receiptSha256"],
        visualEvidenceSha256=_sha(visual_raw) if visual_raw is not None else None)


def public_visual(visual):
    # Original assessments remain in VISUAL_EVIDENCE; never silently drop facts.
    value = dict(visual, phoneAssessments=visual["phoneAssessments"][:128],
                omittedPhoneAssessmentCount=max(0, len(visual["phoneAssessments"]) - 128))
    # A valid catalog can contain long IDs for 32 references per phone. Bound
    # projection bytes as well as count so video cannot crowd out valid audio.
    while len(encode_document(value)) > 256 * 1024 and value["phoneAssessments"]:
        value["phoneAssessments"].pop()
        value["omittedPhoneAssessmentCount"] += 1
    _require(len(encode_document(value)) <= 256 * 1024, "VISUAL_SUMMARY_TOO_LARGE")
    return value


def serialize(result, *, request, identity, frozen, receipt=None, prepared_audio=None):
    from .canonical_result import serialize_canonical_result
    _require(receipt is None, "V6_RECEIPT_FORBIDDEN")
    checked = preflight(result, request=request, frozen=frozen, prepared_audio=prepared_audio)
    from voice_coach.visual_feedback.coaching_input import prepare_visual_coaching
    visual = public_visual(_json(result.visual_evidence_bytes)["visual"])
    media = media_binding(request, result.visual_evidence_bytes)
    budget = 1024**2 - len(encode_document(visual)) - len(encode_document(media)) - 128
    raw = serialize_canonical_result(audio_expression(result), request=audio_request(request),
        identity=identity, frozen=frozen, prepared_audio=prepared_audio,
        _visual_payload=prepare_visual_coaching(result.visual_evidence_bytes, core_bytes=result.core_bytes),
        _max_result_bytes=budget)
    document = read_document(raw)
    document.update(schemaVersion=RESULT_V6, analysisProfile=AUDIOVISUAL_PROFILE,
        visual=visual, mediaBinding=media)
    validate(document, preflight=checked)
    raw = encode_document(document)
    _require(len(raw) <= 1024**2, "RESULT_ENVELOPE_TOO_LARGE")
    return raw


def validate(document, *, preflight=None, receipt=None):
    from .canonical_result import validate_canonical_result_document, CanonicalPreflight
    _require(receipt is None, "V6_RECEIPT_FORBIDDEN")
    validate_document(document, "result", schema_name="v6")
    base_doc = dict(document, schemaVersion=RESULT_V5, analysisProfile=HANDOFF_PROFILE,
                    visual=dict(status="NOT_CONNECTED", correctiveClaimsAllowed=False))
    del base_doc["mediaBinding"]
    if preflight is None:
        _require(document["canonical"] is None and document["mediaBinding"]["visualEvidenceSha256"] is None,
                 "VISUAL_PRECORE_INVALID")
        _require(document["visual"] == public_visual(unavailable_visual()), "VISUAL_PRECORE_INVALID")
        validate_canonical_result_document(base_doc)
        return
    expression = preflight.result
    evidence = _json(expression.visual_evidence_bytes)
    association = _json(expression.association_bytes)
    _require(_json(encode_document(document["visual"])) == public_visual(evidence["visual"]) and document["mediaBinding"] == {
        k: association[k] for k in ("videoSha256", "mediaReceiptSha256", "visualEvidenceSha256")}, "VISUAL_RESULT_MISMATCH")
    base = CanonicalPreflight(audio_expression(expression), preflight.association, ())
    validate_canonical_result_document(base_doc, preflight=base)


def unavailable_visual():
    return dict(status="UNSCORABLE", correctiveClaimsAllowed=False, score=None, scoreKind="NONE",
        coverage=dict(targetCount=0, visuallyEligibleCount=0, observedCount=0, scoredCount=0, coverage=None),
        phoneAssessments=[], reasonCodes=["AUDIO_ANALYSIS_UNAVAILABLE"],
        alignmentScope="CANONICAL_MFA_DETECTOR_PHONES_V1", unresolvedExpectedIndices=[])


def precore(*, request, identity, failure):
    from .canonical_result import serialize_precore_failure
    document = read_document(serialize_precore_failure(request=audio_request(request), identity=identity, failure=failure))
    document.update(schemaVersion=RESULT_V6, analysisProfile=AUDIOVISUAL_PROFILE,
                    visual=public_visual(unavailable_visual()), mediaBinding=media_binding(request))
    validate(document)
    return encode_document(document)
