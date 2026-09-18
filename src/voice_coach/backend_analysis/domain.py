"""Versioned VC-BE worker contracts, including closed-beta debug transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import math
import re
from typing import Mapping
from uuid import UUID


ANALYSIS_REQUEST_SCHEMA_VERSION = "voice-coaching.analysis-request.v5"
ANALYSIS_AUTHORIZATION_GRANT_VERSION = "voice-coaching.analysis-authorization.v4"
ANALYSIS_AUTHORIZATION_PURPOSE = "pronunciation_coaching"
ANALYSIS_AUTHORIZATION_DATA_CATEGORY = "learner_voice_recording"
ANALYSIS_RESULT_SCHEMA_VERSION = "voice-coaching.analysis-result.v4"
CLOSED_BETA_CONTEXT_SCHEMA_VERSION = "voice-coaching.closed-beta-context.v1"
CLOSED_BETA_DEBUG_SCHEMA_VERSION = "voice-coaching.closed-beta-debug.v1"
VISUAL_SUPPLEMENT_SCHEMA_VERSION = "voice-coaching.visual-supplement.v1"
PRONUNCIATION_EVIDENCE_SCHEMA_VERSION = (
    "voice-coaching.pronunciation-evidence.v1"
)
SEUNGUN_RANKING_SCORE_SEMANTICS = (
    "detector_ranking_score_not_calibrated_correctness_confidence"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEARNING_FOCUSES = frozenset({"PRONUNCIATION", "INTONATION", "BOTH"})
_JAVA_LONG_MAX = (1 << 63) - 1
_JAVA_INT_MAX = (1 << 31) - 1
_REQUEST_FIELDS_V4 = frozenset(
    {
        "schemaVersion",
        "eventId",
        "analysisId",
        "contentId",
        "promptRevision",
        "scriptText",
        "scriptSha256",
        "audioObjectKey",
        "audioSha256",
        "mimeType",
        "fileSizeBytes",
        "durationMs",
        "learningFocus",
        "visualInput",
        "authorizationGrant",
    }
)
_REQUEST_FIELDS_V5 = _REQUEST_FIELDS_V4 | {"closedBetaContext"}
_AUTHORIZATION_FIELDS_V3 = frozenset(
    {
        "grantVersion",
        "keyId",
        "requestEventId",
        "analysisId",
        "contentId",
        "promptRevision",
        "scriptSha256",
        "audioObjectKeySha256",
        "audioSha256",
        "mimeType",
        "fileSizeBytes",
        "durationMs",
        "learningFocus",
        "consentReceiptSha256",
        "consentPolicyRevision",
        "visualObjectKeySha256",
        "visualSha256",
        "visualMimeType",
        "visualFileSizeBytes",
        "visualConsentReceiptSha256",
        "visualConsentPolicyRevision",
        "issuedAtUtc",
        "expiresAtUtc",
        "purpose",
        "dataCategory",
        "deleteOnCompletion",
        "remoteEgressAllowed",
        "signature",
    }
)
_AUTHORIZATION_FIELDS_V4 = _AUTHORIZATION_FIELDS_V3 | {
    "closedBetaContextSha256"
}


@dataclass(frozen=True, slots=True)
class ClosedBetaContext:
    """Identifying request context intentionally transported for beta testing."""

    user_id: int
    session_id: int
    recording_id: int

    @classmethod
    def from_payload(cls, payload: object) -> "ClosedBetaContext":
        fields = {"schemaVersion", "userId", "sessionId", "recordingId"}
        if (
            not isinstance(payload, Mapping)
            or set(payload) != fields
            or payload["schemaVersion"] != CLOSED_BETA_CONTEXT_SCHEMA_VERSION
        ):
            raise BackendAnalysisContractError("closed_beta_context_invalid")
        return cls(
            user_id=_positive_int(payload["userId"]),
            session_id=_positive_int(payload["sessionId"]),
            recording_id=_positive_int(payload["recordingId"]),
        )

    def canonical_binding_input(self) -> bytes:
        return (
            f"schemaVersion:{CLOSED_BETA_CONTEXT_SCHEMA_VERSION}\n"
            f"userId:{self.user_id}\n"
            f"sessionId:{self.session_id}\n"
            f"recordingId:{self.recording_id}\n"
        ).encode("utf-8")

    def binding_sha256(self) -> str:
        return hashlib.sha256(self.canonical_binding_input()).hexdigest()

    def to_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": CLOSED_BETA_CONTEXT_SCHEMA_VERSION,
            "userId": self.user_id,
            "sessionId": self.session_id,
            "recordingId": self.recording_id,
        }


@dataclass(frozen=True, slots=True)
class ClosedBetaDebug:
    """Raw identifiers, paths, and media intentionally exposed in beta results."""

    context: ClosedBetaContext
    capture_state: str
    audio_object_key: str
    visual_object_key: str | None
    materialized_audio_path: str | None
    decoded_audio_path: str | None
    materialized_video_path: str | None
    audio_media_base64: str | None = field(repr=False)
    decoded_audio_media_base64: str | None = field(repr=False)
    video_media_base64: str | None = field(repr=False)

    @classmethod
    def unavailable(
        cls,
        *,
        context: ClosedBetaContext,
        audio_object_key: str,
        visual_object_key: str | None,
    ) -> "ClosedBetaDebug":
        """Retain the v5 identity binding when raw capture cannot complete."""

        return cls(
            context=context,
            capture_state="UNAVAILABLE",
            audio_object_key=audio_object_key,
            visual_object_key=visual_object_key,
            materialized_audio_path=None,
            decoded_audio_path=None,
            materialized_video_path=None,
            audio_media_base64=None,
            decoded_audio_media_base64=None,
            video_media_base64=None,
        )

    def __post_init__(self) -> None:
        if self.capture_state not in {"COMPLETE", "PARTIAL", "UNAVAILABLE"}:
            raise ValueError("closed beta capture state is invalid")
        _object_key(self.audio_object_key)
        if self.visual_object_key is not None:
            _object_key(self.visual_object_key)
        for value in (
            self.materialized_audio_path,
            self.decoded_audio_path,
            self.materialized_video_path,
        ):
            if value is not None and (not value.startswith("/") or len(value) > 4096):
                raise ValueError("closed beta raw path is invalid")
        for value in (
            self.audio_media_base64,
            self.decoded_audio_media_base64,
            self.video_media_base64,
        ):
            if value is not None and (
                not value
                or len(value) % 4 != 0
                or re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value) is None
            ):
                raise ValueError("closed beta media base64 is invalid")

    def to_payload(self) -> dict[str, object | None]:
        return {
            "schemaVersion": CLOSED_BETA_DEBUG_SCHEMA_VERSION,
            "context": self.context.to_payload(),
            "captureState": self.capture_state,
            "audioObjectKey": self.audio_object_key,
            "visualObjectKey": self.visual_object_key,
            "materializedAudioPath": self.materialized_audio_path,
            "decodedAudioPath": self.decoded_audio_path,
            "materializedVideoPath": self.materialized_video_path,
            "audioMediaBase64": self.audio_media_base64,
            "decodedAudioMediaBase64": self.decoded_audio_media_base64,
            "videoMediaBase64": self.video_media_base64,
        }


@dataclass(frozen=True, slots=True)
class AnalysisVisualInput:
    """Consent-bound canonical video used only after Seungun selects a phone."""

    object_key: str
    sha256: str
    mime_type: str
    file_size_bytes: int
    consent_receipt_sha256: str
    consent_policy_revision: str

    @classmethod
    def from_payload(cls, payload: object) -> "AnalysisVisualInput":
        fields = {
            "objectKey",
            "sha256",
            "mimeType",
            "fileSizeBytes",
            "consentReceiptSha256",
            "consentPolicyRevision",
        }
        if not isinstance(payload, Mapping) or set(payload) != fields:
            raise BackendAnalysisContractError("analysis_visual_contract_invalid")
        if payload["mimeType"] != "video/mp4":
            raise BackendAnalysisContractError("analysis_visual_contract_invalid")
        return cls(
            object_key=_object_key(payload["objectKey"]),
            sha256=_sha256(payload["sha256"]),
            mime_type="video/mp4",
            file_size_bytes=_positive_int_with_max(
                payload["fileSizeBytes"], maximum=_JAVA_LONG_MAX
            ),
            consent_receipt_sha256=_sha256(payload["consentReceiptSha256"]),
            consent_policy_revision=_text(
                payload["consentPolicyRevision"], maximum=100
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualSupplement:
    """Bounded public projection of the approved internal visual supplement."""

    selected_expected_index: int
    evidence_relation: str
    approved_claim_id: str
    renderer_key: str
    upstream_phone_anchor_ref: str
    supplement_sha256: str
    closed_beta_lip_observation: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.selected_expected_index, bool)
            or not isinstance(self.selected_expected_index, int)
            or not 0 <= self.selected_expected_index <= _JAVA_INT_MAX
        ):
            raise ValueError("visual selected phone index is invalid")
        if self.evidence_relation != "supports_upstream":
            raise ValueError("visual evidence may only support upstream evidence")
        _safe_identifier(self.approved_claim_id)
        _safe_identifier(self.renderer_key)
        _sha256(self.upstream_phone_anchor_ref)
        _sha256(self.supplement_sha256)
        if self.closed_beta_lip_observation is not None:
            _closed_beta_lip_observation(self.closed_beta_lip_observation)

    def to_payload(self) -> dict[str, object]:
        return {
            "schemaVersion": VISUAL_SUPPLEMENT_SCHEMA_VERSION,
            "selectedExpectedIndex": self.selected_expected_index,
            "evidenceRelation": self.evidence_relation,
            "approvedClaimId": self.approved_claim_id,
            "rendererKey": self.renderer_key,
            "upstreamPhoneAnchorRef": self.upstream_phone_anchor_ref,
            "supplementSha256": self.supplement_sha256,
            "closedBetaLipObservation": self.closed_beta_lip_observation,
        }


class BackendAnalysisContractError(ValueError):
    """Stable, non-sensitive error for a malformed stream contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AnalysisOutcome(StrEnum):
    """Terminal Seungun-owned attempt outcome accepted by VC-BE."""

    COACHING_READY = "COACHING_READY"
    COMPLETED_NO_ISSUE = "COMPLETED_NO_ISSUE"
    RERECORD_REQUIRED = "RERECORD_REQUIRED"
    UNCERTAIN = "UNCERTAIN"
    FAILED_CLOSED = "FAILED_CLOSED"


@dataclass(frozen=True, slots=True)
class PronunciationEvidence:
    """One selected-phone decision owned by the same Seungun attempt."""

    selected_phone: str
    selected_expected_index: int
    selected_start_ms: int | None
    selected_end_ms: int | None
    detector_score: float
    operating_threshold: float
    score_semantics: str
    evidence_state: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.selected_phone, str)
            or not self.selected_phone.strip()
            or len(self.selected_phone) > 16
        ):
            raise ValueError("selected phone is invalid")
        if (
            isinstance(self.selected_expected_index, bool)
            or not isinstance(self.selected_expected_index, int)
            or self.selected_expected_index < 0
            or self.selected_expected_index > _JAVA_INT_MAX
        ):
            raise ValueError("selected expected index is invalid")
        if (self.selected_start_ms is None) != (self.selected_end_ms is None):
            raise ValueError("selected phone offsets must be paired")
        if self.selected_start_ms is not None and (
            self.selected_start_ms < 0
            or self.selected_end_ms <= self.selected_start_ms
        ):
            raise ValueError("selected phone offsets are invalid")
        if (
            isinstance(self.detector_score, bool)
            or not isinstance(self.detector_score, int | float)
            or isinstance(self.operating_threshold, bool)
            or not isinstance(self.operating_threshold, int | float)
            or not 0.0
            <= float(self.operating_threshold)
            <= float(self.detector_score)
            <= 1.0
        ):
            raise ValueError("selected phone detector values are invalid")
        if self.score_semantics != SEUNGUN_RANKING_SCORE_SEMANTICS:
            raise ValueError("selected phone score semantics are invalid")
        if self.evidence_state != "frozen_detector_threshold_passed":
            raise ValueError("selected phone evidence state is invalid")

    def to_payload(self) -> dict[str, object | None]:
        return {
            "schemaVersion": PRONUNCIATION_EVIDENCE_SCHEMA_VERSION,
            "selectedPhone": self.selected_phone,
            "selectedExpectedIndex": self.selected_expected_index,
            "selectedStartMs": self.selected_start_ms,
            "selectedEndMs": self.selected_end_ms,
            "detectorScore": self.detector_score,
            "operatingThreshold": self.operating_threshold,
            "scoreSemantics": self.score_semantics,
            "evidenceState": self.evidence_state,
        }


@dataclass(frozen=True, slots=True)
class AnalysisAuthorizationGrant:
    """Signed authority whose claims are bound to exactly one request."""

    key_id: str
    request_event_id: UUID
    analysis_id: int
    content_id: int
    prompt_revision: str
    script_sha256: str
    audio_object_key_sha256: str
    audio_sha256: str
    mime_type: str
    file_size_bytes: int
    duration_ms: int
    learning_focus: str
    consent_receipt_sha256: str
    consent_policy_revision: str
    visual_object_key_sha256: str | None
    visual_sha256: str | None
    visual_mime_type: str | None
    visual_file_size_bytes: int | None
    visual_consent_receipt_sha256: str | None
    visual_consent_policy_revision: str | None
    issued_at_utc_text: str
    expires_at_utc_text: str
    purpose: str
    data_category: str
    delete_on_completion: bool
    remote_egress_allowed: bool
    signature: str
    closed_beta_context_sha256: str | None = None
    grant_version: str = ANALYSIS_AUTHORIZATION_GRANT_VERSION

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, object]
    ) -> "AnalysisAuthorizationGrant":
        if not isinstance(payload, Mapping):
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        grant_version = payload.get("grantVersion")
        expected_fields = (
            _AUTHORIZATION_FIELDS_V4
            if grant_version == ANALYSIS_AUTHORIZATION_GRANT_VERSION
            else _AUTHORIZATION_FIELDS_V3
        )
        if set(payload) != expected_fields or grant_version not in {
            "voice-coaching.analysis-authorization.v3",
            ANALYSIS_AUTHORIZATION_GRANT_VERSION,
        }:
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        key_id = _identifier(payload["keyId"])
        request_event_id = _uuid(payload["requestEventId"])
        analysis_id = _positive_int(payload["analysisId"])
        content_id = _positive_int(payload["contentId"])
        prompt_revision = _text(payload["promptRevision"], maximum=100)
        script_sha256 = _sha256(payload["scriptSha256"])
        audio_object_key_sha256 = _sha256(payload["audioObjectKeySha256"])
        audio_sha256 = _sha256(payload["audioSha256"])
        mime_type = _canonical_audio_mime(payload["mimeType"])
        file_size_bytes = _positive_int_with_max(
            payload["fileSizeBytes"], maximum=_JAVA_LONG_MAX
        )
        duration_ms = _positive_int_with_max(
            payload["durationMs"], maximum=_JAVA_INT_MAX
        )
        learning_focus = payload["learningFocus"]
        if learning_focus not in _LEARNING_FOCUSES:
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        consent_receipt_sha256 = _sha256(payload["consentReceiptSha256"])
        consent_policy_revision = _text(payload["consentPolicyRevision"], maximum=100)
        closed_beta_context_sha256 = (
            _sha256(payload["closedBetaContextSha256"])
            if grant_version == ANALYSIS_AUTHORIZATION_GRANT_VERSION
            else None
        )
        visual_claims = (
            payload["visualObjectKeySha256"],
            payload["visualSha256"],
            payload["visualMimeType"],
            payload["visualFileSizeBytes"],
            payload["visualConsentReceiptSha256"],
            payload["visualConsentPolicyRevision"],
        )
        if all(value is None for value in visual_claims):
            visual_object_key_sha256 = None
            visual_sha256 = None
            visual_mime_type = None
            visual_file_size_bytes = None
            visual_consent_receipt_sha256 = None
            visual_consent_policy_revision = None
        elif any(value is None for value in visual_claims):
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        else:
            visual_object_key_sha256 = _sha256(visual_claims[0])
            visual_sha256 = _sha256(visual_claims[1])
            if visual_claims[2] != "video/mp4":
                raise BackendAnalysisContractError(
                    "analysis_authorization_contract_invalid"
                )
            visual_mime_type = "video/mp4"
            visual_file_size_bytes = _positive_int_with_max(
                visual_claims[3], maximum=_JAVA_LONG_MAX
            )
            visual_consent_receipt_sha256 = _sha256(visual_claims[4])
            visual_consent_policy_revision = _text(visual_claims[5], maximum=100)
        issued_at_utc_text = _utc_instant_text(payload["issuedAtUtc"])
        expires_at_utc_text = _utc_instant_text(payload["expiresAtUtc"])
        if _parse_utc(expires_at_utc_text) <= _parse_utc(issued_at_utc_text):
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        purpose = payload["purpose"]
        data_category = payload["dataCategory"]
        delete_on_completion = payload["deleteOnCompletion"]
        remote_egress_allowed = payload["remoteEgressAllowed"]
        if (
            purpose != ANALYSIS_AUTHORIZATION_PURPOSE
            or data_category != ANALYSIS_AUTHORIZATION_DATA_CATEGORY
            or delete_on_completion is not True
            or remote_egress_allowed is not False
        ):
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        return cls(
            key_id=key_id,
            request_event_id=request_event_id,
            analysis_id=analysis_id,
            content_id=content_id,
            prompt_revision=prompt_revision,
            script_sha256=script_sha256,
            audio_object_key_sha256=audio_object_key_sha256,
            audio_sha256=audio_sha256,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            duration_ms=duration_ms,
            learning_focus=learning_focus,
            consent_receipt_sha256=consent_receipt_sha256,
            consent_policy_revision=consent_policy_revision,
            visual_object_key_sha256=visual_object_key_sha256,
            visual_sha256=visual_sha256,
            visual_mime_type=visual_mime_type,
            visual_file_size_bytes=visual_file_size_bytes,
            visual_consent_receipt_sha256=visual_consent_receipt_sha256,
            visual_consent_policy_revision=visual_consent_policy_revision,
            issued_at_utc_text=issued_at_utc_text,
            expires_at_utc_text=expires_at_utc_text,
            purpose=purpose,
            data_category=data_category,
            delete_on_completion=delete_on_completion,
            remote_egress_allowed=remote_egress_allowed,
            signature=_sha256(payload["signature"]),
            closed_beta_context_sha256=closed_beta_context_sha256,
            grant_version=grant_version,
        )

    @property
    def issued_at(self) -> datetime:
        return _parse_utc(self.issued_at_utc_text)

    @property
    def expires_at(self) -> datetime:
        return _parse_utc(self.expires_at_utc_text)

    def canonical_signing_input(self) -> bytes:
        fields: tuple[tuple[str, object | None], ...] = (
            ("grantVersion", self.grant_version),
            ("keyId", self.key_id),
            ("requestEventId", str(self.request_event_id)),
            ("analysisId", self.analysis_id),
            ("contentId", self.content_id),
            ("promptRevision", self.prompt_revision),
            ("scriptSha256", self.script_sha256),
            ("audioObjectKeySha256", self.audio_object_key_sha256),
            ("audioSha256", self.audio_sha256),
            ("mimeType", self.mime_type),
            ("fileSizeBytes", self.file_size_bytes),
            ("durationMs", self.duration_ms),
            ("learningFocus", self.learning_focus),
            ("consentReceiptSha256", self.consent_receipt_sha256),
            ("consentPolicyRevision", self.consent_policy_revision),
            ("visualObjectKeySha256", self.visual_object_key_sha256),
            ("visualSha256", self.visual_sha256),
            ("visualMimeType", self.visual_mime_type),
            ("visualFileSizeBytes", self.visual_file_size_bytes),
            ("visualConsentReceiptSha256", self.visual_consent_receipt_sha256),
            ("visualConsentPolicyRevision", self.visual_consent_policy_revision),
            ("issuedAtUtc", self.issued_at_utc_text),
            ("expiresAtUtc", self.expires_at_utc_text),
            ("purpose", self.purpose),
            ("dataCategory", self.data_category),
            ("deleteOnCompletion", self.delete_on_completion),
            ("remoteEgressAllowed", self.remote_egress_allowed),
        )
        if self.grant_version == ANALYSIS_AUTHORIZATION_GRANT_VERSION:
            insertion = 21
            fields = (
                fields[:insertion]
                + (("closedBetaContextSha256", self.closed_beta_context_sha256),)
                + fields[insertion:]
            )
        parts: list[str] = []
        for name, raw_value in fields:
            if raw_value is None:
                parts.append(f"{name}:-1:\n")
                continue
            if isinstance(raw_value, bool):
                value = "true" if raw_value else "false"
            else:
                value = str(raw_value)
            parts.append(f"{name}:{len(value.encode('utf-8'))}:{value}\n")
        return "".join(parts).encode("utf-8")


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """One admitted-to-queue request, before any object storage access."""

    event_id: UUID
    analysis_id: int
    content_id: int
    prompt_revision: str
    script_text: str
    script_sha256: str
    audio_object_key: str
    audio_sha256: str
    mime_type: str
    file_size_bytes: int
    duration_ms: int
    learning_focus: str
    visual_input: AnalysisVisualInput | None
    authorization_grant: AnalysisAuthorizationGrant
    closed_beta_context: ClosedBetaContext | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "AnalysisRequest":
        if not isinstance(payload, Mapping):
            raise BackendAnalysisContractError("analysis_request_contract_invalid")
        schema_version = payload.get("schemaVersion")
        if schema_version not in {
            "voice-coaching.analysis-request.v4",
            ANALYSIS_REQUEST_SCHEMA_VERSION,
        }:
            raise BackendAnalysisContractError("analysis_request_schema_unsupported")
        expected_fields = (
            _REQUEST_FIELDS_V5
            if schema_version == ANALYSIS_REQUEST_SCHEMA_VERSION
            else _REQUEST_FIELDS_V4
        )
        if set(payload) != expected_fields:
            raise BackendAnalysisContractError("analysis_request_contract_invalid")
        event_id = _uuid(payload["eventId"])
        analysis_id = _positive_int(payload["analysisId"])
        content_id = _positive_int(payload["contentId"])
        prompt_revision = _text(payload["promptRevision"], maximum=100)
        script_text = _text(payload["scriptText"], maximum=10_000)
        script_sha256 = _sha256(payload["scriptSha256"])
        if hashlib.sha256(script_text.encode("utf-8")).hexdigest() != script_sha256:
            raise BackendAnalysisContractError("analysis_request_script_integrity_invalid")
        audio_object_key = _object_key(payload["audioObjectKey"])
        audio_sha256 = _sha256(payload["audioSha256"])
        mime_type = _canonical_audio_mime(payload["mimeType"])
        file_size_bytes = _positive_int_with_max(
            payload["fileSizeBytes"], maximum=_JAVA_LONG_MAX
        )
        duration_ms = _positive_int_with_max(
            payload["durationMs"], maximum=_JAVA_INT_MAX
        )
        learning_focus = payload["learningFocus"]
        if learning_focus not in _LEARNING_FOCUSES:
            raise BackendAnalysisContractError("analysis_request_learning_focus_invalid")
        authorization = AnalysisAuthorizationGrant.from_payload(
            payload["authorizationGrant"]  # type: ignore[arg-type]
        )
        closed_beta_context = (
            ClosedBetaContext.from_payload(payload["closedBetaContext"])
            if schema_version == ANALYSIS_REQUEST_SCHEMA_VERSION
            else None
        )
        visual_input = (
            None
            if payload["visualInput"] is None
            else AnalysisVisualInput.from_payload(payload["visualInput"])
        )
        if (
            authorization.request_event_id != event_id
            or authorization.analysis_id != analysis_id
            or authorization.content_id != content_id
            or authorization.prompt_revision != prompt_revision
            or authorization.script_sha256 != script_sha256
            or authorization.audio_object_key_sha256
            != hashlib.sha256(audio_object_key.encode("utf-8")).hexdigest()
            or authorization.audio_sha256 != audio_sha256
            or authorization.mime_type != mime_type
            or authorization.file_size_bytes != file_size_bytes
            or authorization.duration_ms != duration_ms
            or authorization.learning_focus != learning_focus
            or (
                closed_beta_context is not None
                and authorization.closed_beta_context_sha256
                != closed_beta_context.binding_sha256()
            )
            or (
                closed_beta_context is None
                and authorization.closed_beta_context_sha256 is not None
            )
            or not _visual_authorization_matches(
                visual_input=visual_input,
                authorization=authorization,
            )
        ):
            raise BackendAnalysisContractError("analysis_authorization_binding_invalid")
        return cls(
            event_id=event_id,
            analysis_id=analysis_id,
            content_id=content_id,
            prompt_revision=prompt_revision,
            script_text=script_text,
            script_sha256=script_sha256,
            audio_object_key=audio_object_key,
            audio_sha256=audio_sha256,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            duration_ms=duration_ms,
            learning_focus=learning_focus,
            visual_input=visual_input,
            authorization_grant=authorization,
            closed_beta_context=closed_beta_context,
        )


@dataclass(frozen=True, slots=True)
class AnalysisWorkerResult:
    """The exact worker result shape stored in the Redis ``payload`` field."""

    event_id: UUID
    request_event_id: UUID
    analysis_id: int
    status: str
    outcome: AnalysisOutcome | None
    failure_code: str | None
    failure_reason: str | None
    summary_feedback: str | None
    pronunciation_evidence: PronunciationEvidence | None
    worker_revision: str | None
    pipeline_revision: str | None
    audio_sha256: str | None
    visual_supplement: VisualSupplement | None
    seungun_production_evidence: object | None = field(default=None, repr=False)
    overall_score: float | None = None
    scoring_evidence: dict | None = field(default=None, repr=False)
    coaching: dict | None = field(default=None, repr=False)
    closed_beta_debug: ClosedBetaDebug | None = field(default=None, repr=False)

    @classmethod
    def failed(
        cls,
        *,
        event_id: UUID,
        request: AnalysisRequest,
        code: str,
        reason: str,
        worker_revision: str,
        pipeline_revision: str | None = None,
        closed_beta_debug: ClosedBetaDebug | None = None,
    ) -> "AnalysisWorkerResult":
        _stable_code(code)
        _safe_reason(reason)
        return cls(
            event_id=event_id,
            request_event_id=request.event_id,
            analysis_id=request.analysis_id,
            status="FAILED",
            outcome=None,
            failure_code=code,
            failure_reason=reason,
            summary_feedback=None,
            pronunciation_evidence=None,
            worker_revision=worker_revision,
            pipeline_revision=pipeline_revision,
            audio_sha256=None,
            visual_supplement=None,
            closed_beta_debug=closed_beta_debug,
        )

    @classmethod
    def completed(
        cls,
        *,
        event_id: UUID,
        request: AnalysisRequest,
        outcome: AnalysisOutcome,
        summary_feedback: str | None,
        pronunciation_evidence: PronunciationEvidence | None,
        worker_revision: str,
        pipeline_revision: str,
        audio_sha256: str,
        visual_supplement: VisualSupplement | None = None,
        seungun_production_evidence: object | None = None,
        overall_score: float | None = None,
        scoring_evidence: dict | None = None,
        coaching: dict | None = None,
        closed_beta_debug: ClosedBetaDebug | None = None,
    ) -> "AnalysisWorkerResult":
        if outcome not in {
            AnalysisOutcome.COACHING_READY,
            AnalysisOutcome.COMPLETED_NO_ISSUE,
        }:
            raise ValueError("result v3 outcome is unsupported")
        if outcome is AnalysisOutcome.COACHING_READY and (
            not summary_feedback or pronunciation_evidence is None
        ):
            raise ValueError(
                "coaching-ready result requires grounded feedback and pronunciation evidence"
            )
        if visual_supplement is not None and (
            pronunciation_evidence is None
            or visual_supplement.selected_expected_index
            != pronunciation_evidence.selected_expected_index
        ):
            raise ValueError("visual supplement must bind Seungun selected phone")
        if outcome is not AnalysisOutcome.COACHING_READY and (
            summary_feedback is not None or pronunciation_evidence is not None
        ):
            raise ValueError(
                "non-coaching result must not carry pronunciation coaching evidence"
            )
        if summary_feedback is not None:
            _text(summary_feedback, maximum=20_000)
        _sha256(audio_sha256)
        if request.closed_beta_context is not None and closed_beta_debug is None:
            raise ValueError("closed beta request requires serialized debug evidence")
        if request.closed_beta_context is not None and (
            not isinstance(seungun_production_evidence, Mapping)
            or seungun_production_evidence.get("schema_version")
            != "korean_phone_ctc.production_analysis.v2"
        ):
            raise ValueError("result v4 requires Seungun production evidence")
        return cls(
            event_id=event_id,
            request_event_id=request.event_id,
            analysis_id=request.analysis_id,
            status="COMPLETED",
            outcome=outcome,
            failure_code=None,
            failure_reason=None,
            summary_feedback=summary_feedback,
            pronunciation_evidence=pronunciation_evidence,
            worker_revision=worker_revision,
            pipeline_revision=pipeline_revision,
            audio_sha256=audio_sha256,
            visual_supplement=visual_supplement,
            seungun_production_evidence=seungun_production_evidence,
            overall_score=overall_score,
            scoring_evidence=scoring_evidence,
            coaching=coaching,
            closed_beta_debug=closed_beta_debug,
        )

    def to_payload(self) -> dict[str, object | None]:
        """Return all Java record fields, with absent observations as ``null``."""

        legacy_result = self.closed_beta_debug is None
        return {
            "schemaVersion": (
                "voice-coaching.analysis-result.v3"
                if legacy_result
                else ANALYSIS_RESULT_SCHEMA_VERSION
            ),
            "eventId": str(self.event_id),
            "requestEventId": str(self.request_event_id),
            "analysisId": self.analysis_id,
            "status": self.status,
            "outcome": self.outcome.value if self.outcome is not None else None,
            "failureCode": self.failure_code,
            "failureReason": self.failure_reason,
            "transcript": None,
            "sttConfidence": None,
            "sttModelName": None,
            "overallScore": None,
            "pronunciationScore": None,
            "intonationScore": None,
            "speedWpm": None,
            "speedStatus": None,
            "stressScore": None,
            "pauseScore": None,
            "strengthsText": None,
            "weaknessesText": None,
            "summaryFeedback": self.summary_feedback,
            "pronunciationEvidence": (
                self.pronunciation_evidence.to_payload()
                if self.pronunciation_evidence is not None
                else None
            ),
            "workerRevision": self.worker_revision,
            "pipelineRevision": self.pipeline_revision,
            "audioSha256": self.audio_sha256,
            "segments": [],
            "visualSupplement": (
                self.visual_supplement.to_payload()
                if self.visual_supplement is not None
                else None
            ),
            "seungunProductionEvidence": (
                None if legacy_result else self.seungun_production_evidence
            ),
            "closedBetaDebug": (
                self.closed_beta_debug.to_payload()
                if self.closed_beta_debug is not None
                else None
            ),
        }


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    try:
        return UUID(value)
    except ValueError:
        raise BackendAnalysisContractError("analysis_request_contract_invalid") from None


def _positive_int(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= _JAVA_LONG_MAX
    ):
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return value


def _positive_int_with_max(value: object, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return value


def _canonical_audio_mime(value: object) -> str:
    if value != "audio/wav":
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return value


def _text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return value


def _sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value) is None:
        raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
    return value


def _utc_instant_text(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 40:
        raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
    _parse_utc(value)
    return value


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        raise BackendAnalysisContractError("analysis_authorization_contract_invalid") from None
    if parsed.tzinfo is None or parsed.astimezone(UTC).utcoffset() is None:
        raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
    return parsed.astimezone(UTC)


def _object_key(value: object) -> str:
    key = _text(value, maximum=1_000)
    if (
        key.startswith(("/", "http://", "https://"))
        or "\\" in key
        or any(segment in {"", ".", ".."} for segment in key.split("/"))
        or any(
            segment.lower() in {"users", "sessions"}
            for segment in key.split("/")
        )
    ):
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return key


def _visual_authorization_matches(
    *,
    visual_input: AnalysisVisualInput | None,
    authorization: AnalysisAuthorizationGrant,
) -> bool:
    if visual_input is None:
        return authorization.visual_object_key_sha256 is None
    return (
        hashlib.sha256(visual_input.object_key.encode("utf-8")).hexdigest()
        == authorization.visual_object_key_sha256
        and visual_input.sha256 == authorization.visual_sha256
        and visual_input.mime_type == authorization.visual_mime_type
        and visual_input.file_size_bytes == authorization.visual_file_size_bytes
        and visual_input.consent_receipt_sha256
        == authorization.visual_consent_receipt_sha256
        and visual_input.consent_policy_revision
        == authorization.visual_consent_policy_revision
    )


def _safe_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}", value) is None
    ):
        raise ValueError("visual identifier is invalid")
    return value


def _closed_beta_lip_observation(value: Mapping[str, object]) -> None:
    expected = {
        "schemaVersion",
        "status",
        "selectedExpectedIndex",
        "videoStartMs",
        "videoEndMs",
        "geometryArtifactSha256",
        "measurements",
        "containsPronunciationTruth",
        "containsActionTruth",
    }
    measurements = value.get("measurements")
    if (
        set(value) != expected
        or value.get("schemaVersion")
        != "voice-coaching.closed-beta-lip-observation.v1"
        or value.get("status") != "OBSERVED"
        or isinstance(value.get("selectedExpectedIndex"), bool)
        or not isinstance(value.get("selectedExpectedIndex"), int)
        or value["selectedExpectedIndex"] < 0
        or isinstance(value.get("videoStartMs"), bool)
        or not isinstance(value.get("videoStartMs"), int)
        or isinstance(value.get("videoEndMs"), bool)
        or not isinstance(value.get("videoEndMs"), int)
        or value["videoStartMs"] < 0
        or value["videoEndMs"] <= value["videoStartMs"]
        or not isinstance(measurements, Mapping)
        or not measurements
        or len(measurements) > 8
        or value.get("containsPronunciationTruth") is not False
        or value.get("containsActionTruth") is not False
    ):
        raise ValueError("closed beta lip observation is invalid")
    _sha256(value.get("geometryArtifactSha256"))
    allowed = {
        "mouth_width_fraction",
        "outer_aperture_ratio",
        "inner_aperture_ratio",
        "outer_area_ratio",
        "inner_area_ratio",
        "corner_height_asymmetry_ratio",
        "shape_change_rate_per_second",
    }
    for cue_id, measurement in measurements.items():
        if cue_id not in allowed or not isinstance(measurement, Mapping):
            raise ValueError("closed beta lip observation is invalid")
        raw = measurement.get("value")
        if (
            set(measurement) != {"value", "unit"}
            or isinstance(raw, bool)
            or not isinstance(raw, int | float)
            or not math.isfinite(float(raw))
            or not isinstance(measurement.get("unit"), str)
            or not measurement["unit"]
        ):
            raise ValueError("closed beta lip observation is invalid")


def _stable_code(value: str) -> None:
    if not value or len(value) > 100 or not re.fullmatch(r"[a-z0-9_]+", value):
        raise ValueError("failure code must be a stable snake_case code")


def _safe_reason(value: str) -> None:
    if not value or len(value) > 500:
        raise ValueError("failure reason must be non-empty and at most 500 characters")
