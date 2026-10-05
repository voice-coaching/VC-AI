"""Private canonical v4/v5 preflight and serialization; no storage or activation.

The calling server owns admission, current execution/lease, prepared-WAV
verification and authenticated Backend receipt lookup. A local artifact upload,
UUID, matching SHA, or this module's preflight does NOT constitute that receipt.
Never expose these objects (including original_error) through a public API.

The original frozen schemas are packaged alongside the service schema. Frozen
prepare is replayed without dispatch to verify guidance and selection, not to
generate another answer. S7 and H5 identities select their own pinned validator;
the original S7 files and core remain unchanged.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
import re
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from .canonical_bridge import (
    CORE_MANIFEST_SHA256, LLM_LOCK_SHA256, LLM_MANIFEST_SHA256, MAX_CORE_BYTES,
    CanonicalExpressionResult, CanonicalIntegrationError, FrozenCanonicalCoaching,
    _encode, _require, _sha,
)
from .canonical_media import PreparedAudioIdentity
from .canonical_harness import H5, HarnessIdentity
# Compatibility aliases for existing private callers; new infrastructure imports
# the codec directly instead of reaching through result assembly.
from .canonical_codec import (
    read_document as _read_json, encode_document as _wire,
    validate_document as _validate, _schema,
)

if TYPE_CHECKING:
    from .http_models import AnalysisInput


from .http_models import REQUEST_V2, REQUEST_V3, RESULT_V4, RESULT_V5, CANONICAL_PROFILE, HANDOFF_PROFILE

def _canonical_profile(request):
    from voice_coach.direct_analysis.domain import DirectInput
    if type(request) is DirectInput:
        return True
    return (request.request_schema_version, request.result_schema_version, request.analysis_profile) in {
        (REQUEST_V2, RESULT_V4, CANONICAL_PROFILE), (REQUEST_V3, RESULT_V5, HANDOFF_PROFILE)}

RESULT_VERSION = "voice-coaching.runpod-analysis-result.v4"
PROFILE = "CANONICAL_FROZEN_20260928_V4"
PROMPT_SHA256 = "362ccc1f15d2e166110cca52d90fd3d6e81ee85646e96eb7018d8b8278898714"
MAX_RESULT_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = MAX_CORE_BYTES
MAX_MANIFEST_BYTES = 65536
ArtifactKind = Literal["CORE", "BRIDGE_RESULT", "SELECTION_PROJECTION", "BINDING", "ASSOCIATION"]
ARTIFACT_SCHEMAS = {
    "CORE": "canonical_ai_predeploy_v1",
    "BRIDGE_RESULT": "canonical-llm-expression-result-v1",
    "SELECTION_PROJECTION": "canonical-llm-expression-input-v1",
    "BINDING": "voice-coaching.canonical-binding.v1",
    "ASSOCIATION": "voice-coaching.canonical-association.v1",
}
EVIDENCE_POINTERS = {
    "pronunciation": "/pronunciation_evidence", "prosody": "/core_result/prosody",
    "mfa": "/mfa_receipt", "contentGate": "/content_gate",
    "inputValidation": "/input_validation", "core": "/core_result",
    "llmFacingEvidence": "/llm_facing_evidence",
}


@dataclass(frozen=True, slots=True)
class ExecutionAssociation:
    request_id: UUID
    execution_id: UUID
    analysis_id: int
    recording_id: int
    content_id: int
    canonical_analysis_id: str
    core_sha256: str

    def to_document(self) -> dict:
        return dict(requestId=str(self.request_id), executionId=str(self.execution_id),
                    analysisId=self.analysis_id, recordingId=self.recording_id,
                    contentId=self.content_id, canonicalAnalysisId=self.canonical_analysis_id,
                    coreSha256=self.core_sha256)


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    kind: ArtifactKind
    schema_version: str
    sha256: str
    byte_size: int
    object_key: str = field(repr=False)
    version_id: str = field(repr=False)

    def to_document(self) -> dict:
        value = dict(kind=self.kind, schemaVersion=self.schema_version, sha256=self.sha256,
                     byteSize=self.byte_size, objectKey=self.object_key, versionId=self.version_id)
        _validate(value, "artifact")
        _require(self.version_id != "null" and not any(ord(c) < 32 for c in self.version_id),
                 "EVIDENCE_VERSION_INVALID")
        _require(not any(part in ("", ".", "..") for part in self.object_key.split("/")),
                 "EVIDENCE_KEY_INVALID")
        return value

    @classmethod
    def from_document(cls, value: dict) -> ArtifactReference:
        _validate(value, "artifact")
        result = cls(value["kind"], value["schemaVersion"], value["sha256"],
                     value["byteSize"], value["objectKey"], value["versionId"])
        result.to_document()
        return result


@dataclass(frozen=True, slots=True)
class TrustedBackendVerifiedReceipt:
    """INTERNAL CALLER ASSERTION, populated only after authenticated VERIFIED.

    There is deliberately no from_payload/from_uuid/local-verify constructor.
    Python construction is not authentication. The server-owned caller MUST
    obtain these immutable fields from Backend's verified receipt record and
    verify ownership/current execution, retention and manifest association.
    This module checks consistency only; it cannot attest remote durability.
    """

    evidence_receipt_id: UUID
    manifest_sha256: str
    artifacts: tuple[ArtifactReference, ...]
    association: ExecutionAssociation
    manifest_bytes: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class PrivateArtifact:
    kind: ArtifactKind
    schema_version: str
    raw: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CanonicalPreflight:
    """Validated in-memory bytes for a retention caller, NOT a storage receipt."""

    result: CanonicalExpressionResult = field(repr=False)
    association: ExecutionAssociation
    artifacts: tuple[PrivateArtifact, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResultIdentity:
    """Server chooses event ID once and persists serialized bytes for retries."""

    event_id: UUID
    worker_instance_id: UUID
    worker_revision: str
    pipeline_revision: str


@dataclass(frozen=True, slots=True)
class ServiceFailure:
    origin: Literal["CORE", "INTEGRATION"]
    code: str
    stage: str

    def to_document(self) -> dict:
        value = dict(origin=self.origin, code=self.code, stage=self.stage)
        _validate(value, "failure")
        return value


def _binding_and_association(request: AnalysisInput, core: dict, raw: bytes,
                             prepared_audio: PreparedAudioIdentity | None, harness: HarnessIdentity):
    _require(_canonical_profile(request),
             "CANONICAL_PROFILE_REQUIRED")
    _require(_sha(request.script_text.encode("utf-8")) == request.script_sha256,
             "ADMITTED_SCRIPT_MISMATCH")
    _require(core["request_id"] == str(request.event_id)
             and core["source_audio_sha256"] == request.audio_sha256, "CORE_ATTEMPT_MISMATCH")
    core_id = core["core_result"]["metadata"]["analysis_id"]
    prepared_sha = core['prepared_wav_sha256']
    _require((prepared_sha is None and prepared_audio is None)
             or (type(prepared_audio) is PreparedAudioIdentity and prepared_audio.sha256 == prepared_sha),
             'PREPARED_DURATION_UNVERIFIED')
    binding = dict(parent_manifest_sha256=harness.core_sha256,
                   core_sha256=_sha(raw), script_sha256=request.script_sha256,
                   request_id=str(request.event_id), analysis_id=core_id,
                   source_audio_sha256=request.audio_sha256,
                   prepared_wav_sha256=core["prepared_wav_sha256"],
                   provenance_source="admitted-http-canonical-producer-v1")
    from voice_coach.direct_analysis.domain import DirectAssociation, DirectInput
    association_type = DirectAssociation if type(request) is DirectInput else ExecutionAssociation
    identity = association_type(request.event_id, request.execution_id, request.analysis_id,
                                    request.recording_id, request.content_id, core_id, _sha(raw))
    association = dict(identity.to_document(), parentManifestSha256=harness.core_sha256,
                       llmManifestSha256=harness.manifest_sha256, llmDependencyLockSha256=harness.lock_sha256,
                       scriptSha256=request.script_sha256, audioSha256=request.audio_sha256,
                       preparedWavSha256=core["prepared_wav_sha256"],
                       preparedAudio=prepared_audio.to_document() if prepared_audio is not None else None,
                       visualStatus="NOT_CONNECTED", visualCorrectiveClaimsAllowed=False)
    return binding, association, identity


def _selection_semantics(core: dict, view: dict, prepared_audio: PreparedAudioIdentity | None):
    rows = core["pronunciation_evidence"]
    indices = [row["expected_index"] for row in rows]
    _require(len(indices) == len(set(indices)) and indices == sorted(indices), "EXPECTED_INDEX_IDENTITY")
    scope = _sha(_encode([core["request_id"], core["core_result"]["metadata"]["analysis_id"],
                          view["sourceIdentity"]["coreSha256"]]))
    _require(view["attemptScope"] == scope, "ATTEMPT_SCOPE_MISMATCH")
    visible = [row for row in rows if row["llm_visible"]]
    groups = {}
    for row in visible:
        groups.setdefault((row["expected_phone"], row["native_phone"], row["role"]), []).append(row)
    ranked = sorted(groups.values(), key=lambda members: (-len(members), members[0]["expected_index"]))
    ids = [scope + ":candidate-" + str(members[0]["expected_index"]) for members in ranked]
    coverage = dict(evaluatedConsonantPositions=len(rows), visiblePositionCount=len(visible),
                    permittedPatternCount=len(ranked), includedCandidateIds=ids[:3],
                    omittedCandidateIds=ids[3:], selectionPolicy="repetition_then_first_occurrence_max3",
                    omittedReason="EXISTING_MAX3_SELECTION" if len(ranked) > 3 else None,
                    presentationStateCounts=dict(Counter(row["presentation_state"] for row in rows)))
    _require(view["coverage"] == coverage, "COVERAGE_MISMATCH")
    _require(view["reviewReasonCounts"] == dict(Counter(row["decision_reason"] for row in rows
             if row["presentation_state"] == "INSUFFICIENT_EVIDENCE")), "REVIEW_REASONS_MISMATCH")
    candidates = view["candidates"]
    _require([c["candidateId"] for c in candidates] == ids[:3], "SELECTION_ORDER_ID_MISMATCH")
    if visible:
        _require(isinstance(prepared_audio, PreparedAudioIdentity)
                 and prepared_audio.sha256 == core["prepared_wav_sha256"], "PREPARED_DURATION_UNVERIFIED")
        duration = prepared_audio.duration_s
        # Check all permitted rows, including patterns omitted by frozen max3.
        for row in visible:
            times = [row[key] for key in ("word_start_s", "phone_start_s", "phone_end_s", "word_end_s")]
            _require(all(isinstance(t, (int, Decimal)) and not isinstance(t, bool) for t in times)
                     and 0 <= times[0] <= times[1] <= times[2] <= times[3] <= duration,
                     "MFA_SPAN_OUTSIDE_PREPARED_AUDIO")
    for candidate, members in zip(candidates, ranked[:3]):
        facts = [dict(evidenceId=scope + ":phone-" + str(row["expected_index"]),
                      expectedIndex=row["expected_index"],
                      location=dict(word=row["word"], wordIndex=row["word_index"],
                                    wordStartS=row["word_start_s"], wordEndS=row["word_end_s"],
                                    phoneStartS=row["phone_start_s"], phoneEndS=row["phone_end_s"],
                                    timingProvenance="MFA_LOCATION_ONLY"),
                      localizationStatus=row["localization_status"]) for row in members]
        _require(candidate["facts"] == facts
                 and candidate["evidenceIds"] == [fact["evidenceId"] for fact in facts]
                 and candidate["repetitionCount"] == len(members), "CANDIDATE_EVIDENCE_MISMATCH")
        first = members[0]
        _require(candidate["expectedPhone"] == first["expected_phone"]
                 and candidate["observedCandidate"] == first["native_phone"]
                 and candidate["expectedRole"] == first["role"], "CANDIDATE_ROLE_MISMATCH")


def preflight_canonical_result(
    result: CanonicalExpressionResult, *, request: AnalysisInput,
    frozen: FrozenCanonicalCoaching, prepared_audio: PreparedAudioIdentity | None = None,
) -> CanonicalPreflight:
    """Validate actual originals before retention, without storage or dispatch.

    Requires the full pinned R/S7 installation to replay prepare. The downloaded
    adapter/schema subset alone is intentionally insufficient to bless guidance.
    Invalid raw cores are not converted into fake canonical failure states.
    """
    _require(type(result) is CanonicalExpressionResult, "CANONICAL_PRIVATE_RESULT_REQUIRED")
    core = _read_json(result.core_bytes, MAX_CORE_BYTES)
    _validate(core, schema_name="parent")
    binding = _read_json(result.binding_bytes)
    association = _read_json(result.association_bytes)
    expression = _read_json(result.expression_bytes)
    _validate(binding, "binding")
    from voice_coach.direct_analysis.domain import DirectInput
    if type(request) is not DirectInput:
        _validate(association, "association")
    # Direct associations use UUID job/audio IDs, not invented Backend integers.
    # Full exact association is reconstructed and compared immediately below.
    _validate(expression, "coaching")
    expected_binding, expected_association, identity = _binding_and_association(
        request, core, result.core_bytes, prepared_audio, frozen.artifacts.identity)
    _require(binding == expected_binding and association == expected_association, "FULL_BINDING_MISMATCH")
    _require(expression["coreSha256"] == _sha(result.core_bytes)
             and expression["coreInputValidation"] == core["input_validation"], "EXPRESSION_CORE_MISMATCH")
    bridge = frozen._bridge()
    if frozen.artifacts.identity.scoring:
        native_args = {}
        if frozen.artifacts.identity.native:
            from .native_execution import admitted_binding
            native_args['native_binding'] = admitted_binding(request,
                core_sha256=frozen.artifacts.identity.core_sha256, prepared_sha256=core.get('prepared_wav_sha256'))
        bridge.verify_scoring(core, expression.get("scoring"), **native_args)
    else:
        _require("scoring" not in expression, "UNEXPECTED_SCORING_RECORD")
    try:
        _, expected_view, _, skip = bridge.prepare(result.core_bytes, request.script_text, binding)
    except Exception as exc:
        _require(result.selection_bytes is None and expression["adapterStatus"] == "FAIL_CLOSED"
                 and expression["errorCode"] == type(exc).__name__, "PREPARE_FAILURE_MISMATCH")
    else:
        expected_bytes = _encode(expected_view) if expected_view is not None else None
        _require(result.selection_bytes == expected_bytes, "FROZEN_PROJECTION_BYTES_MISMATCH")
        if expected_view is not None:
            view = _read_json(result.selection_bytes)
            _validate(view, schema_name="input")
            _selection_semantics(core, view, prepared_audio)
        if (frozen.artifacts.identity.scoring and expression.get("scoring") == {"status": "UNSCORABLE"}):
            _require(expression["adapterStatus"] == "FAIL_CLOSED"
                     and expression["errorCode"] == "SCORING_UNSCORABLE", "SCORING_FAILURE_STATUS")
        elif skip:
            _require(expression["adapterStatus"] == skip, "ADAPTER_STATUS_MISMATCH")
        else:
            _require(expression["adapterStatus"] == "READY", "ADAPTER_STATUS_MISMATCH")
            items = expression["items"]
            _require([item["candidate"] for item in items] == view["candidates"], "EXPRESSION_CANDIDATES_MISMATCH")
            _require([item["expression"]["candidateId"] for item in items]
                     == view["coverage"]["includedCandidateIds"], "EXPRESSION_ORDER_MISMATCH")
            for item in items:
                candidate, text = item["candidate"], item["expression"]
                _require(text["guidanceId"] == candidate["guidance"]["guidanceId"], "GUIDANCE_MISMATCH")
                if frozen.artifacts.identity == H5 or frozen.artifacts.identity.scoring:
                    bridge.validate_action(text["action"])
                for value in text.values():
                    _require(len(value.encode("utf-16-le")) // 2 <= 1200, "EXPRESSION_UTF16_LIMIT")
            if expression["generationStatus"] == "DETERMINISTIC_FALLBACK":
                # Exact frozen fallback rows, without invoking transport.
                expected_rows = bridge.dispatch.__func__.__globals__["template_rows"](expected_view)
                _require([item["expression"] for item in items] == expected_rows, "FALLBACK_ROWS_MISMATCH")
            else:
                # Dispatch preserves expressions, not the provider response.
                # Repeat only the checks supported by these retained fields;
                # never invent a model/refusal/finish_reason response envelope.
                normalize = bridge.validate_response.__func__.__globals__["_text"]
                prohibited = r"(입을 더 크게 벌리|입을 크게 벌리|입모양이 작|영상을 보았|영상을 봤|녹음을 직접 들|확정된 오발음|발음 정확도)"
                for item in items:
                    for key in ("explanation", "action", "practice", "selfCheck"):
                        text = item["expression"][key]
                        _require(normalize(text) == text and re.search(prohibited, text) is None,
                                 "FROZEN_EXPRESSION_INVALID")
    if core["input_validation"]["status"] != "ACCEPT":
        _require(expression["dispatchAttempts"] == 0 and expression["items"] == []
                 and result.selection_bytes is None, "NON_ACCEPT_DISPATCH_INVALID")
    raw_by_kind = [("CORE", result.core_bytes), ("BRIDGE_RESULT", result.expression_bytes),
                   ("BINDING", result.binding_bytes), ("ASSOCIATION", result.association_bytes)]
    if result.selection_bytes is not None:
        raw_by_kind.append(("SELECTION_PROJECTION", result.selection_bytes))
    artifacts = tuple(PrivateArtifact(kind,
        "voice-coaching.direct-association.v1" if type(request) is DirectInput and kind == "ASSOCIATION"
        else ARTIFACT_SCHEMAS[kind], raw) for kind, raw in raw_by_kind)
    _require(all(0 < len(a.raw) <= MAX_ARTIFACT_BYTES for a in artifacts), "EVIDENCE_SIZE_INVALID")
    return CanonicalPreflight(result, identity, artifacts)


def _verified_retention(receipt: TrustedBackendVerifiedReceipt, preflight: CanonicalPreflight) -> dict:
    _require(type(receipt) is TrustedBackendVerifiedReceipt, "BACKEND_VERIFIED_RECEIPT_REQUIRED")
    _require(type(receipt.evidence_receipt_id) is UUID and type(receipt.artifacts) is tuple
             and receipt.association == preflight.association, "RECEIPT_ASSOCIATION_MISMATCH")
    _require(_sha(receipt.manifest_bytes) == receipt.manifest_sha256, "MANIFEST_BYTES_MISMATCH")
    manifest = _read_json(receipt.manifest_bytes, MAX_MANIFEST_BYTES)
    _validate(manifest, "evidenceManifest", schema_name="control")
    artifacts = [a.to_document() for a in receipt.artifacts]
    _require(isinstance(manifest, dict) and manifest.get("artifacts") == artifacts,
             "MANIFEST_ARTIFACTS_MISMATCH")
    # The closed control schema checks manifest shape/version; namespace policy
    # remains with retention and Backend. Check execution association explicitly;
    # never infer one from an object key or receipt UUID.
    _require(manifest.get("association") == preflight.association.to_document(),
             "MANIFEST_ASSOCIATION_MISMATCH")
    expected = {a.kind: a for a in preflight.artifacts}
    _require(len(artifacts) == len(expected) and {a["kind"] for a in artifacts} == set(expected),
             "RETAINED_ARTIFACT_SET_MISMATCH")
    _require(len({(a["objectKey"], a["versionId"]) for a in artifacts}) == len(artifacts),
             "RETAINED_ARTIFACT_ALIAS")
    for artifact in artifacts:
        original = expected[artifact["kind"]]
        _require(artifact["schemaVersion"] == original.schema_version
                 and artifact["sha256"] == _sha(original.raw)
                 and artifact["byteSize"] == len(original.raw), "RETAINED_ORIGINAL_MISMATCH")
    value = dict(evidenceReceiptId=str(receipt.evidence_receipt_id),
                 manifestSha256=receipt.manifest_sha256, artifacts=artifacts)
    _validate(value, "retainedEvidence")
    return value


def _base_document(request: AnalysisInput, identity: ResultIdentity, harness: HarnessIdentity = H5) -> dict:
    _require(type(identity) is ResultIdentity and type(identity.event_id) is UUID
             and type(identity.worker_instance_id) is UUID, "RESULT_IDENTITY_INVALID")
    _require(_canonical_profile(request),
             "CANONICAL_PROFILE_REQUIRED")
    _require(_sha(request.script_text.encode("utf-8")) == request.script_sha256, "ADMITTED_SCRIPT_MISMATCH")
    return dict(schemaVersion=request.result_schema_version, eventId=str(identity.event_id),
                requestId=str(request.event_id), executionId=str(request.execution_id),
                workerInstanceId=str(identity.worker_instance_id), analysisId=request.analysis_id,
                recordingId=request.recording_id, contentId=request.content_id,
                workerRevision=identity.worker_revision, pipelineRevision=identity.pipeline_revision,
                analysisProfile=request.analysis_profile,
                sourceIdentity=dict(scriptSha256=request.script_sha256, audioSha256=request.audio_sha256,
                                    preparedWavSha256=None, canonicalAnalysisId=None, coreSha256=None,
                                    parentManifestSha256=harness.core_sha256,
                                    llmManifestSha256=harness.manifest_sha256,
                                    llmDependencyLockSha256=harness.lock_sha256, promptSha256=harness.prompt_sha256),
                visual=dict(status="NOT_CONNECTED", correctiveClaimsAllowed=False),
                prosodyPolicy=dict(expressionPermission="NO_APPROVED_GUIDANCE_MAPPING"),
                score=dict(overallScore=None, validity="NOT_AVAILABLE", reason="NO_APPROVED_SCORING_CONTRACT"))


def _public_score(expression, view, harness):
    if not harness.scoring:
        return dict(overallScore=None, validity=view["score"]["validity"] if view else "NOT_AVAILABLE",
                    reason="NO_APPROVED_SCORING_CONTRACT")
    scoring = expression["scoring"]
    if scoring["status"] == "READY":
        return dict(scoring["result"], validity="RUBRIC_COMPUTED", reason="EVIDENCE_BOUND_PRACTICE_SCORE")
    return dict(overallScore=None, validity="UNSCORABLE" if scoring["status"] == "UNSCORABLE" else "NOT_AVAILABLE",
                reason="SCORING_FAILED" if scoring["status"] == "UNSCORABLE" else "INPUT_NOT_ACCEPTED")


def _public_selection(view, expression):
    if view is None or expression["adapterStatus"] == "FAIL_CLOSED":
        return None
    return {key: view[key] for key in ("attemptScope", "coverage", "reviewReasonCounts", "candidates")}


def serialize_canonical_result(
    result: CanonicalExpressionResult, *, request: AnalysisInput, identity: ResultIdentity,
    frozen: FrozenCanonicalCoaching, receipt: TrustedBackendVerifiedReceipt | None = None,
    prepared_audio: PreparedAudioIdentity | None = None,
) -> bytes:
    """Build v4 with a verified B2 receipt, or v5 for durable-original handoff.

    No upload, receipt issuance, event-ID creation or readiness mutation occurs.
    Caller must persist/reuse these exact bytes for retransmission. A full-sized
    envelope is replaced only by the defined RETAINED_ONLY delivery failure;
    every original remains required in the verified artifact set.
    """
    preflight = preflight_canonical_result(result, request=request, frozen=frozen, prepared_audio=prepared_audio)
    if request.result_schema_version == RESULT_V4:
        retained = _verified_retention(receipt, preflight)
    else:
        _require(receipt is None, "V5_RECEIPT_FORBIDDEN")
        retained = None
    core = _read_json(result.core_bytes)
    expression = _read_json(result.expression_bytes)
    view = _read_json(result.selection_bytes) if result.selection_bytes is not None else None
    document = _base_document(request, identity, frozen.artifacts.identity)
    document["sourceIdentity"].update(preparedWavSha256=core["prepared_wav_sha256"],
                                      canonicalAnalysisId=preflight.association.canonical_analysis_id,
                                      coreSha256=preflight.association.core_sha256)
    validation = core["input_validation"]
    document.update(status="COMPLETED", failure=None,
                    canonical=dict(schemaVersion=core["schema_version"], representation="INLINE",
                                   decision={key: validation[key] for key in ("status", "reason_code", "stage")},
                                   inputValidation=validation, coreStatus=core["core_result"]["status"],
                                   feedbackDeliveryAllowed=core["feedback_delivery_allowed"],
                                   pronunciationFeedbackSource=core["pronunciation_feedback_source"],
                                   evidence=dict(artifactKind="CORE", sha256=_sha(result.core_bytes),
                                                 pointers=dict(EVIDENCE_POINTERS))),
                    selection=_public_selection(view, expression), coaching=expression, retainedEvidence=retained)
    if request.result_schema_version == RESULT_V5:
        del document["retainedEvidence"]
    document["score"] = _public_score(expression, view, frozen.artifacts.identity)
    if expression["adapterStatus"] == "FAIL_CLOSED":
        document.update(status="FAILED", failure=ServiceFailure(
            "INTEGRATION", "CANONICAL_ADAPTER_FAILED", "ADAPTER").to_document())
    elif validation["status"] == "SYSTEM_FAILURE":
        document.update(status="FAILED", failure=ServiceFailure(
            "CORE", "CORE_SYSTEM_FAILURE", "EXECUTION").to_document())
    raw = _wire(document)
    if len(raw) > MAX_RESULT_BYTES:
        document.update(status="FAILED", failure=ServiceFailure(
            "INTEGRATION", "RESULT_ENVELOPE_TOO_LARGE", "SERIALIZATION").to_document(),
                        selection=None, coaching=None)
        document["canonical"].update(representation="RETAINED_ONLY", inputValidation=None)
        if frozen.artifacts.identity.scoring:
            document["score"] = dict(overallScore=None, validity="NOT_AVAILABLE", reason="RESULT_NOT_DELIVERED")
    validate_canonical_result_document(document, preflight=preflight, receipt=receipt)
    raw = _wire(document)
    _require(len(raw) <= MAX_RESULT_BYTES, "RESULT_SUMMARY_TOO_LARGE")
    return raw


def serialize_precore_failure(*, request: AnalysisInput, identity: ResultIdentity, failure: ServiceFailure) -> bytes:
    """Only for execution without a valid core; no fabricated canonical object.

    Invalid core bytes require a separate quarantine policy. This does not turn
    them into normal evidence, fabricate a receipt, or support post-core loss.
    """
    _require(type(failure) is ServiceFailure and failure.origin == "INTEGRATION"
             and failure.code in {"CANONICAL_EXECUTION_FAILED", "CORE_CONTRACT_INVALID",
                                  "CANONICAL_BINDING_FAILED", "EVIDENCE_STORAGE_FAILED"},
             "PRECORE_FAILURE_INVALID")
    document = _base_document(request, identity)
    document.update(status="FAILED", failure=failure.to_document(), canonical=None,
                    selection=None, coaching=None, retainedEvidence=None)
    if request.result_schema_version == RESULT_V5:
        del document["retainedEvidence"]
    validate_canonical_result_document(document)
    raw = _wire(document)
    _require(len(raw) <= MAX_RESULT_BYTES, "RESULT_ENVELOPE_TOO_LARGE")
    return raw


def validate_canonical_result_document(
    document: dict, *, preflight: CanonicalPreflight | None = None,
    receipt: TrustedBackendVerifiedReceipt | None = None,
) -> None:
    """Strict service validation against private originals, not a Map-only DTO.

    Preflight is an internal value returned by preflight_canonical_result, never
    deserialized from a callback. Remote ingress must independently retrieve and
    validate its retained originals before calling this boundary.
    """
    v5 = document.get("schemaVersion") == RESULT_V5
    _validate(document, "result", schema_name="v5" if v5 else "v4")
    _require(len(_wire(document)) <= MAX_RESULT_BYTES, "RESULT_ENVELOPE_TOO_LARGE")
    if document["canonical"] is None:
        _require(preflight is None and receipt is None and document["failure"]["origin"] == "INTEGRATION",
                 "PRECORE_FAILURE_INVALID")
        return
    _require(type(preflight) is CanonicalPreflight, "VERIFIED_ORIGINALS_REQUIRED")
    if v5:
        _require(receipt is None, "V5_RECEIPT_FORBIDDEN")
    else:
        _require(type(receipt) is TrustedBackendVerifiedReceipt, "VERIFIED_ORIGINALS_REQUIRED")
        retained = _verified_retention(receipt, preflight)
        _require(document["retainedEvidence"] == retained, "RECEIPT_DOCUMENT_MISMATCH")
    result = preflight.result
    core = _read_json(result.core_bytes)
    association = _read_json(result.association_bytes)
    for key in ("requestId", "executionId", "analysisId", "recordingId", "contentId"):
        _require(document[key] == association[key], "RESULT_ASSOCIATION_MISMATCH")
    source = document["sourceIdentity"]
    for key in ("scriptSha256", "audioSha256", "preparedWavSha256", "canonicalAnalysisId", "coreSha256",
                "parentManifestSha256", "llmManifestSha256", "llmDependencyLockSha256"):
        _require(source[key] == association[key], "RESULT_SOURCE_MISMATCH")
    canonical = document["canonical"]
    validation = core["input_validation"]
    _require(canonical["decision"] == {key: validation[key] for key in ("status", "reason_code", "stage")}
             and canonical["coreStatus"] == core["core_result"]["status"]
             and canonical["feedbackDeliveryAllowed"] == core["feedback_delivery_allowed"]
             and canonical["pronunciationFeedbackSource"] == core["pronunciation_feedback_source"],
             "CORE_DECISION_MISMATCH")
    _require(canonical["evidence"] == dict(artifactKind="CORE", sha256=_sha(result.core_bytes),
                                           pointers=EVIDENCE_POINTERS), "CORE_EVIDENCE_MISMATCH")
    expression = _read_json(result.expression_bytes)
    view = _read_json(result.selection_bytes) if result.selection_bytes is not None else None
    from .canonical_harness import harness_identity
    harness = harness_identity(source["llmManifestSha256"])
    expected_score = (dict(overallScore=None, validity="NOT_AVAILABLE", reason="RESULT_NOT_DELIVERED")
                      if harness.scoring and canonical["representation"] == "RETAINED_ONLY"
                      else _public_score(expression, view, harness))
    _require(document["score"] == expected_score,
             "SCORE_RESULT_MISMATCH")
    if canonical["representation"] == "RETAINED_ONLY":
        # Schema enforces failure/null combination. Also prove the original
        # inline envelope really exceeded the limit, rather than hide content.
        inline = dict(document, selection=_public_selection(view, expression),
                      coaching=expression, score=_public_score(expression, view, harness),
                      canonical=dict(canonical, representation="INLINE", inputValidation=validation))
        original_failure = None
        if expression["adapterStatus"] == "FAIL_CLOSED":
            original_failure = ServiceFailure("INTEGRATION", "CANONICAL_ADAPTER_FAILED", "ADAPTER").to_document()
        elif validation["status"] == "SYSTEM_FAILURE":
            original_failure = ServiceFailure("CORE", "CORE_SYSTEM_FAILURE", "EXECUTION").to_document()
        inline.update(status="FAILED" if original_failure else "COMPLETED", failure=original_failure)
        _require(len(_wire(inline)) > MAX_RESULT_BYTES, "RETAINED_ONLY_WITHOUT_OVERSIZE")
        return
    _require(canonical["inputValidation"] == validation and document["coaching"] == expression,
             "ORIGINAL_RESULT_MISMATCH")
    selection = _public_selection(view, expression)
    _require(document["selection"] == selection, "ORIGINAL_SELECTION_MISMATCH")
    expected_failure = None
    if expression["adapterStatus"] == "FAIL_CLOSED":
        expected_failure = ServiceFailure("INTEGRATION", "CANONICAL_ADAPTER_FAILED", "ADAPTER").to_document()
    elif validation["status"] == "SYSTEM_FAILURE":
        expected_failure = ServiceFailure("CORE", "CORE_SYSTEM_FAILURE", "EXECUTION").to_document()
    _require(document["failure"] == expected_failure
             and document["status"] == ("FAILED" if expected_failure else "COMPLETED"), "RESULT_STATUS_MISMATCH")
