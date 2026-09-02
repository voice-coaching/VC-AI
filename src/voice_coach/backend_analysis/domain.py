"""Versioned, privacy-minimised VC-BE worker contracts.

These contracts deliberately reject unknown fields.  A worker must not accept
an accidental payload expansion containing a user identifier, signed URL, or
raw path merely because it happens to be valid JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import re
from typing import Mapping
from uuid import UUID


ANALYSIS_REQUEST_SCHEMA_VERSION = "voice-coaching.analysis-request.v3"
ANALYSIS_AUTHORIZATION_GRANT_VERSION = "voice-coaching.analysis-authorization.v2"
ANALYSIS_AUTHORIZATION_PURPOSE = "pronunciation_coaching"
ANALYSIS_AUTHORIZATION_DATA_CATEGORY = "learner_voice_recording"
ANALYSIS_RESULT_SCHEMA_VERSION = "voice-coaching.analysis-result.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEARNING_FOCUSES = frozenset({"PRONUNCIATION", "INTONATION", "BOTH"})
_JAVA_LONG_MAX = (1 << 63) - 1
_JAVA_INT_MAX = (1 << 31) - 1
_REQUEST_FIELDS = frozenset(
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
        "authorizationGrant",
    }
)
_AUTHORIZATION_FIELDS = frozenset(
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
        "issuedAtUtc",
        "expiresAtUtc",
        "purpose",
        "dataCategory",
        "deleteOnCompletion",
        "remoteEgressAllowed",
        "signature",
    }
)


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
    issued_at_utc_text: str
    expires_at_utc_text: str
    purpose: str
    data_category: str
    delete_on_completion: bool
    remote_egress_allowed: bool
    signature: str

    @classmethod
    def from_payload(
        cls, payload: Mapping[str, object]
    ) -> "AnalysisAuthorizationGrant":
        if not isinstance(payload, Mapping) or set(payload) != _AUTHORIZATION_FIELDS:
            raise BackendAnalysisContractError("analysis_authorization_contract_invalid")
        if payload["grantVersion"] != ANALYSIS_AUTHORIZATION_GRANT_VERSION:
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
            issued_at_utc_text=issued_at_utc_text,
            expires_at_utc_text=expires_at_utc_text,
            purpose=purpose,
            data_category=data_category,
            delete_on_completion=delete_on_completion,
            remote_egress_allowed=remote_egress_allowed,
            signature=_sha256(payload["signature"]),
        )

    @property
    def issued_at(self) -> datetime:
        return _parse_utc(self.issued_at_utc_text)

    @property
    def expires_at(self) -> datetime:
        return _parse_utc(self.expires_at_utc_text)

    def canonical_signing_input(self) -> bytes:
        fields: tuple[tuple[str, object | None], ...] = (
            ("grantVersion", ANALYSIS_AUTHORIZATION_GRANT_VERSION),
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
            ("issuedAtUtc", self.issued_at_utc_text),
            ("expiresAtUtc", self.expires_at_utc_text),
            ("purpose", self.purpose),
            ("dataCategory", self.data_category),
            ("deleteOnCompletion", self.delete_on_completion),
            ("remoteEgressAllowed", self.remote_egress_allowed),
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
    authorization_grant: AnalysisAuthorizationGrant

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "AnalysisRequest":
        if not isinstance(payload, Mapping):
            raise BackendAnalysisContractError("analysis_request_contract_invalid")
        if payload.get("schemaVersion") != ANALYSIS_REQUEST_SCHEMA_VERSION:
            raise BackendAnalysisContractError("analysis_request_schema_unsupported")
        if set(payload) != _REQUEST_FIELDS:
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
            authorization_grant=authorization,
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
    worker_revision: str | None
    pipeline_revision: str | None
    audio_sha256: str | None

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
            worker_revision=worker_revision,
            pipeline_revision=pipeline_revision,
            audio_sha256=None,
        )

    @classmethod
    def completed(
        cls,
        *,
        event_id: UUID,
        request: AnalysisRequest,
        outcome: AnalysisOutcome,
        summary_feedback: str | None,
        worker_revision: str,
        pipeline_revision: str,
        audio_sha256: str,
    ) -> "AnalysisWorkerResult":
        if outcome is AnalysisOutcome.COACHING_READY and not summary_feedback:
            raise ValueError("coaching-ready result requires grounded feedback")
        if summary_feedback is not None:
            _text(summary_feedback, maximum=20_000)
        _sha256(audio_sha256)
        return cls(
            event_id=event_id,
            request_event_id=request.event_id,
            analysis_id=request.analysis_id,
            status="COMPLETED",
            outcome=outcome,
            failure_code=None,
            failure_reason=None,
            summary_feedback=summary_feedback,
            worker_revision=worker_revision,
            pipeline_revision=pipeline_revision,
            audio_sha256=audio_sha256,
        )

    def to_payload(self) -> dict[str, object | None]:
        """Return all Java record fields, with absent observations as ``null``."""

        return {
            "schemaVersion": ANALYSIS_RESULT_SCHEMA_VERSION,
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
            "workerRevision": self.worker_revision,
            "pipelineRevision": self.pipeline_revision,
            "audioSha256": self.audio_sha256,
            "segments": [],
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
    ):
        raise BackendAnalysisContractError("analysis_request_contract_invalid")
    return key


def _stable_code(value: str) -> None:
    if not value or len(value) > 100 or not re.fullmatch(r"[a-z0-9_]+", value):
        raise ValueError("failure code must be a stable snake_case code")


def _safe_reason(value: str) -> None:
    if not value or len(value) > 500:
        raise ValueError("failure reason must be non-empty and at most 500 characters")
