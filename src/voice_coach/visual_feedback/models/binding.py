"""Immutable same-attempt and consent receipts for audio/video binding."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from ..errors import VisualFeedbackContractError
from .enums import ConsentDecision, UpstreamReliabilityStatus
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    require_utc_timestamp,
    sha256_document,
)

BINDING_SCHEMA_VERSION = "voice_coach.same_attempt_binding.v2"
CONSENT_SCHEMA_VERSION = "voice_coach.visual_consent_receipt.v1"
RELIABILITY_SCHEMA_VERSION = "voice_coach.seungun_reliability_receipt.v1"
VISUAL_PROCESSING_PURPOSE = "lip_shape_feedback"
FACIAL_VIDEO_DATA_CATEGORY = "facial_video"


def expected_phone_sequence_sha256(expected_phones: Sequence[str]) -> str:
    """Hash the exact ordered target-phone sequence used by Seungun."""

    if isinstance(expected_phones, (str, bytes)) or not expected_phones:
        raise VisualFeedbackContractError("expected phone sequence must not be empty")
    phones = [
        require_text_token(phone, "expected_phones[]") for phone in expected_phones
    ]
    return sha256_document({"expected_phones": phones})


@dataclass(frozen=True, slots=True)
class MediaTimebaseReceipt:
    """Clock and transform metadata without a media path or raw identifier."""

    clock_id_sha256: str
    duration_ms: int
    origin_offset_ms: int
    transform_revision: str

    def __post_init__(self) -> None:
        require_sha256(self.clock_id_sha256, "clock_id_sha256")
        require_integer(self.duration_ms, "duration_ms", minimum=1)
        require_integer(self.origin_offset_ms, "origin_offset_ms")
        require_safe_identifier(self.transform_revision, "transform_revision")

    def to_json_object(self) -> dict[str, object]:
        return {
            "clock_id_sha256": self.clock_id_sha256,
            "duration_ms": self.duration_ms,
            "origin_offset_ms": self.origin_offset_ms,
            "transform_revision": self.transform_revision,
        }

    @property
    def timebase_sha256(self) -> str:
        """Return the canonical digest bound into an opaque media handle."""

        return sha256_document(self.to_json_object())

    @classmethod
    def from_json_object(cls, value: object) -> MediaTimebaseReceipt:
        document = require_mapping(value, "timebase")
        require_exact_keys(
            document,
            {
                "clock_id_sha256",
                "duration_ms",
                "origin_offset_ms",
                "transform_revision",
            },
            "timebase",
        )
        return cls(
            clock_id_sha256=require_sha256(
                document["clock_id_sha256"], "timebase.clock_id_sha256"
            ),
            duration_ms=require_integer(
                document["duration_ms"], "timebase.duration_ms", minimum=1
            ),
            origin_offset_ms=require_integer(
                document["origin_offset_ms"], "timebase.origin_offset_ms"
            ),
            transform_revision=require_safe_identifier(
                document["transform_revision"], "timebase.transform_revision"
            ),
        )


@dataclass(frozen=True, slots=True)
class UpstreamReliabilityReceipt:
    """Fail-closed statement that seungun evidence is eligible for sidecar use."""

    status: UpstreamReliabilityStatus
    policy_revision: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = RELIABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RELIABILITY_SCHEMA_VERSION:
            raise VisualFeedbackContractError("reliability schema_version differs")
        require_safe_identifier(self.policy_revision, "policy_revision")
        for reason in self.reason_codes:
            require_safe_identifier(reason, "reason_codes[]")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise VisualFeedbackContractError("reason_codes must be unique")
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("reliability receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        status: UpstreamReliabilityStatus,
        policy_revision: str,
        reason_codes: tuple[str, ...] = (),
    ) -> UpstreamReliabilityReceipt:
        unsigned = {
            "schema_version": RELIABILITY_SCHEMA_VERSION,
            "status": status.value,
            "policy_revision": policy_revision,
            "reason_codes": list(reason_codes),
        }
        return cls(
            status=status,
            policy_revision=policy_revision,
            reason_codes=reason_codes,
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "policy_revision": self.policy_revision,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> UpstreamReliabilityReceipt:
        document = require_mapping(value, "reliability_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "policy_revision",
                "reason_codes",
                "receipt_sha256",
            },
            "reliability_receipt",
        )
        try:
            status = UpstreamReliabilityStatus(document["status"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError(
                "reliability status is unknown"
            ) from error
        reasons = document["reason_codes"]
        if not isinstance(reasons, list):
            raise VisualFeedbackContractError("reason_codes must be an array")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "reliability_receipt.schema_version"
            ),
            status=status,
            policy_revision=require_safe_identifier(
                document["policy_revision"], "reliability_receipt.policy_revision"
            ),
            reason_codes=tuple(
                require_safe_identifier(reason, "reliability_receipt.reason_codes[]")
                for reason in reasons
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "reliability_receipt.receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class SameAttemptBindingReceipt:
    """Binds immutable seungun analysis to the exact submitted video attempt."""

    attempt_id_sha256: str
    prompt_id_sha256: str
    prompt_revision: str
    expected_phone_sha256: str
    analysis_sha256: str
    audio_source_sha256: str
    audio_derivation_receipt_sha256: str
    video_source_sha256: str
    audio_timebase: MediaTimebaseReceipt
    video_timebase: MediaTimebaseReceipt
    upstream_reliability_receipt_sha256: str
    consent_receipt_sha256: str
    issued_at_utc: str
    receipt_sha256: str
    schema_version: str = BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != BINDING_SCHEMA_VERSION:
            raise VisualFeedbackContractError("binding schema_version differs")
        for name in (
            "attempt_id_sha256",
            "prompt_id_sha256",
            "expected_phone_sha256",
            "analysis_sha256",
            "audio_source_sha256",
            "audio_derivation_receipt_sha256",
            "video_source_sha256",
            "upstream_reliability_receipt_sha256",
            "consent_receipt_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_safe_identifier(self.prompt_revision, "prompt_revision")
        require_utc_timestamp(self.issued_at_utc, "issued_at_utc")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("same-attempt binding digest differs")

    @classmethod
    def issue(
        cls,
        *,
        attempt_id_sha256: str,
        prompt_id_sha256: str,
        prompt_revision: str,
        expected_phone_sha256: str,
        analysis_sha256: str,
        audio_source_sha256: str,
        audio_derivation_receipt_sha256: str,
        video_source_sha256: str,
        audio_timebase: MediaTimebaseReceipt,
        video_timebase: MediaTimebaseReceipt,
        upstream_reliability_receipt_sha256: str,
        consent_receipt_sha256: str,
        issued_at_utc: str,
    ) -> SameAttemptBindingReceipt:
        unsigned = {
            "schema_version": BINDING_SCHEMA_VERSION,
            "attempt_id_sha256": attempt_id_sha256,
            "prompt_id_sha256": prompt_id_sha256,
            "prompt_revision": prompt_revision,
            "expected_phone_sha256": expected_phone_sha256,
            "analysis_sha256": analysis_sha256,
            "audio_source_sha256": audio_source_sha256,
            "audio_derivation_receipt_sha256": audio_derivation_receipt_sha256,
            "video_source_sha256": video_source_sha256,
            "audio_timebase": audio_timebase.to_json_object(),
            "video_timebase": video_timebase.to_json_object(),
            "upstream_reliability_receipt_sha256": upstream_reliability_receipt_sha256,
            "consent_receipt_sha256": consent_receipt_sha256,
            "issued_at_utc": issued_at_utc,
        }
        return cls(
            attempt_id_sha256=attempt_id_sha256,
            prompt_id_sha256=prompt_id_sha256,
            prompt_revision=prompt_revision,
            expected_phone_sha256=expected_phone_sha256,
            analysis_sha256=analysis_sha256,
            audio_source_sha256=audio_source_sha256,
            audio_derivation_receipt_sha256=audio_derivation_receipt_sha256,
            video_source_sha256=video_source_sha256,
            audio_timebase=audio_timebase,
            video_timebase=video_timebase,
            upstream_reliability_receipt_sha256=upstream_reliability_receipt_sha256,
            consent_receipt_sha256=consent_receipt_sha256,
            issued_at_utc=issued_at_utc,
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "prompt_id_sha256": self.prompt_id_sha256,
            "prompt_revision": self.prompt_revision,
            "expected_phone_sha256": self.expected_phone_sha256,
            "analysis_sha256": self.analysis_sha256,
            "audio_source_sha256": self.audio_source_sha256,
            "audio_derivation_receipt_sha256": (
                self.audio_derivation_receipt_sha256
            ),
            "video_source_sha256": self.video_source_sha256,
            "audio_timebase": self.audio_timebase.to_json_object(),
            "video_timebase": self.video_timebase.to_json_object(),
            "upstream_reliability_receipt_sha256": (
                self.upstream_reliability_receipt_sha256
            ),
            "consent_receipt_sha256": self.consent_receipt_sha256,
            "issued_at_utc": self.issued_at_utc,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> SameAttemptBindingReceipt:
        document = require_mapping(value, "binding_receipt")
        expected_fields = {
            "schema_version",
            "attempt_id_sha256",
            "prompt_id_sha256",
            "prompt_revision",
            "expected_phone_sha256",
            "analysis_sha256",
            "audio_source_sha256",
            "audio_derivation_receipt_sha256",
            "video_source_sha256",
            "audio_timebase",
            "video_timebase",
            "upstream_reliability_receipt_sha256",
            "consent_receipt_sha256",
            "issued_at_utc",
            "receipt_sha256",
        }
        require_exact_keys(document, expected_fields, "binding_receipt")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "binding_receipt.schema_version"
            ),
            attempt_id_sha256=require_sha256(
                document["attempt_id_sha256"], "binding_receipt.attempt_id_sha256"
            ),
            prompt_id_sha256=require_sha256(
                document["prompt_id_sha256"], "binding_receipt.prompt_id_sha256"
            ),
            prompt_revision=require_safe_identifier(
                document["prompt_revision"], "binding_receipt.prompt_revision"
            ),
            expected_phone_sha256=require_sha256(
                document["expected_phone_sha256"],
                "binding_receipt.expected_phone_sha256",
            ),
            analysis_sha256=require_sha256(
                document["analysis_sha256"], "binding_receipt.analysis_sha256"
            ),
            audio_source_sha256=require_sha256(
                document["audio_source_sha256"],
                "binding_receipt.audio_source_sha256",
            ),
            audio_derivation_receipt_sha256=require_sha256(
                document["audio_derivation_receipt_sha256"],
                "binding_receipt.audio_derivation_receipt_sha256",
            ),
            video_source_sha256=require_sha256(
                document["video_source_sha256"],
                "binding_receipt.video_source_sha256",
            ),
            audio_timebase=MediaTimebaseReceipt.from_json_object(
                document["audio_timebase"]
            ),
            video_timebase=MediaTimebaseReceipt.from_json_object(
                document["video_timebase"]
            ),
            upstream_reliability_receipt_sha256=require_sha256(
                document["upstream_reliability_receipt_sha256"],
                "binding_receipt.upstream_reliability_receipt_sha256",
            ),
            consent_receipt_sha256=require_sha256(
                document["consent_receipt_sha256"],
                "binding_receipt.consent_receipt_sha256",
            ),
            issued_at_utc=require_utc_timestamp(
                document["issued_at_utc"], "binding_receipt.issued_at_utc"
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "binding_receipt.receipt_sha256"
            ),
        )


HTTP_BINDING_SCHEMA_VERSION = "voice_coach.http_same_attempt_binding.v1"


@dataclass(frozen=True, slots=True)
class HttpSameAttemptBindingReceipt:
    """Binds HTTP execution identity and media; this is not a permission receipt."""

    attempt_id_sha256: str
    prompt_id_sha256: str
    prompt_revision: str
    expected_phone_sha256: str
    analysis_sha256: str
    audio_source_sha256: str
    audio_derivation_receipt_sha256: str
    video_source_sha256: str
    audio_timebase: MediaTimebaseReceipt
    video_timebase: MediaTimebaseReceipt
    upstream_reliability_receipt_sha256: str
    execution_binding_sha256: str
    issued_at_utc: str
    receipt_sha256: str
    schema_version: str = HTTP_BINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HTTP_BINDING_SCHEMA_VERSION:
            raise VisualFeedbackContractError("binding schema_version differs")
        for name in (
            "attempt_id_sha256",
            "prompt_id_sha256",
            "expected_phone_sha256",
            "analysis_sha256",
            "audio_source_sha256",
            "audio_derivation_receipt_sha256",
            "video_source_sha256",
            "upstream_reliability_receipt_sha256",
            "execution_binding_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_safe_identifier(self.prompt_revision, "prompt_revision")
        require_utc_timestamp(self.issued_at_utc, "issued_at_utc")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("same-attempt binding digest differs")

    @classmethod
    def issue(
        cls,
        *,
        attempt_id_sha256: str,
        prompt_id_sha256: str,
        prompt_revision: str,
        expected_phone_sha256: str,
        analysis_sha256: str,
        audio_source_sha256: str,
        audio_derivation_receipt_sha256: str,
        video_source_sha256: str,
        audio_timebase: MediaTimebaseReceipt,
        video_timebase: MediaTimebaseReceipt,
        upstream_reliability_receipt_sha256: str,
        execution_binding_sha256: str,
        issued_at_utc: str,
    ) -> HttpSameAttemptBindingReceipt:
        unsigned = {
            "schema_version": HTTP_BINDING_SCHEMA_VERSION,
            "attempt_id_sha256": attempt_id_sha256,
            "prompt_id_sha256": prompt_id_sha256,
            "prompt_revision": prompt_revision,
            "expected_phone_sha256": expected_phone_sha256,
            "analysis_sha256": analysis_sha256,
            "audio_source_sha256": audio_source_sha256,
            "audio_derivation_receipt_sha256": audio_derivation_receipt_sha256,
            "video_source_sha256": video_source_sha256,
            "audio_timebase": audio_timebase.to_json_object(),
            "video_timebase": video_timebase.to_json_object(),
            "upstream_reliability_receipt_sha256": upstream_reliability_receipt_sha256,
            "execution_binding_sha256": execution_binding_sha256,
            "issued_at_utc": issued_at_utc,
        }
        return cls(
            attempt_id_sha256=attempt_id_sha256,
            prompt_id_sha256=prompt_id_sha256,
            prompt_revision=prompt_revision,
            expected_phone_sha256=expected_phone_sha256,
            analysis_sha256=analysis_sha256,
            audio_source_sha256=audio_source_sha256,
            audio_derivation_receipt_sha256=audio_derivation_receipt_sha256,
            video_source_sha256=video_source_sha256,
            audio_timebase=audio_timebase,
            video_timebase=video_timebase,
            upstream_reliability_receipt_sha256=upstream_reliability_receipt_sha256,
            execution_binding_sha256=execution_binding_sha256,
            issued_at_utc=issued_at_utc,
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "prompt_id_sha256": self.prompt_id_sha256,
            "prompt_revision": self.prompt_revision,
            "expected_phone_sha256": self.expected_phone_sha256,
            "analysis_sha256": self.analysis_sha256,
            "audio_source_sha256": self.audio_source_sha256,
            "audio_derivation_receipt_sha256": (
                self.audio_derivation_receipt_sha256
            ),
            "video_source_sha256": self.video_source_sha256,
            "audio_timebase": self.audio_timebase.to_json_object(),
            "video_timebase": self.video_timebase.to_json_object(),
            "upstream_reliability_receipt_sha256": (
                self.upstream_reliability_receipt_sha256
            ),
            "execution_binding_sha256": self.execution_binding_sha256,
            "issued_at_utc": self.issued_at_utc,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> HttpSameAttemptBindingReceipt:
        document = require_mapping(value, "binding_receipt")
        expected_fields = {
            "schema_version",
            "attempt_id_sha256",
            "prompt_id_sha256",
            "prompt_revision",
            "expected_phone_sha256",
            "analysis_sha256",
            "audio_source_sha256",
            "audio_derivation_receipt_sha256",
            "video_source_sha256",
            "audio_timebase",
            "video_timebase",
            "upstream_reliability_receipt_sha256",
            "execution_binding_sha256",
            "issued_at_utc",
            "receipt_sha256",
        }
        require_exact_keys(document, expected_fields, "binding_receipt")
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "binding_receipt.schema_version"
            ),
            attempt_id_sha256=require_sha256(
                document["attempt_id_sha256"], "binding_receipt.attempt_id_sha256"
            ),
            prompt_id_sha256=require_sha256(
                document["prompt_id_sha256"], "binding_receipt.prompt_id_sha256"
            ),
            prompt_revision=require_safe_identifier(
                document["prompt_revision"], "binding_receipt.prompt_revision"
            ),
            expected_phone_sha256=require_sha256(
                document["expected_phone_sha256"],
                "binding_receipt.expected_phone_sha256",
            ),
            analysis_sha256=require_sha256(
                document["analysis_sha256"], "binding_receipt.analysis_sha256"
            ),
            audio_source_sha256=require_sha256(
                document["audio_source_sha256"],
                "binding_receipt.audio_source_sha256",
            ),
            audio_derivation_receipt_sha256=require_sha256(
                document["audio_derivation_receipt_sha256"],
                "binding_receipt.audio_derivation_receipt_sha256",
            ),
            video_source_sha256=require_sha256(
                document["video_source_sha256"],
                "binding_receipt.video_source_sha256",
            ),
            audio_timebase=MediaTimebaseReceipt.from_json_object(
                document["audio_timebase"]
            ),
            video_timebase=MediaTimebaseReceipt.from_json_object(
                document["video_timebase"]
            ),
            upstream_reliability_receipt_sha256=require_sha256(
                document["upstream_reliability_receipt_sha256"],
                "binding_receipt.upstream_reliability_receipt_sha256",
            ),
            execution_binding_sha256=require_sha256(
                document["execution_binding_sha256"],
                "binding_receipt.execution_binding_sha256",
            ),
            issued_at_utc=require_utc_timestamp(
                document["issued_at_utc"], "binding_receipt.issued_at_utc"
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "binding_receipt.receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class ConsentReceipt:
    """Purpose-limited facial-video consent without a participant identifier."""

    attempt_id_sha256: str
    decision: ConsentDecision
    purpose: str
    data_category: str
    retention_policy_id: str
    delete_on_completion: bool
    remote_egress_allowed: bool
    issued_at_utc: str
    expires_at_utc: str
    receipt_sha256: str
    schema_version: str = CONSENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONSENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("consent schema_version differs")
        require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        require_safe_identifier(self.purpose, "purpose")
        require_safe_identifier(self.data_category, "data_category")
        require_safe_identifier(self.retention_policy_id, "retention_policy_id")
        require_boolean(self.delete_on_completion, "delete_on_completion")
        require_boolean(self.remote_egress_allowed, "remote_egress_allowed")
        issued = _parse_timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _parse_timestamp(self.expires_at_utc, "expires_at_utc")
        if expires <= issued:
            raise VisualFeedbackContractError("consent must expire after issue time")
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("consent receipt digest differs")

    @classmethod
    def issue(
        cls,
        *,
        attempt_id_sha256: str,
        decision: ConsentDecision,
        retention_policy_id: str,
        issued_at_utc: str,
        expires_at_utc: str,
        purpose: str = VISUAL_PROCESSING_PURPOSE,
        data_category: str = FACIAL_VIDEO_DATA_CATEGORY,
        delete_on_completion: bool = True,
        remote_egress_allowed: bool = False,
    ) -> ConsentReceipt:
        unsigned = {
            "schema_version": CONSENT_SCHEMA_VERSION,
            "attempt_id_sha256": attempt_id_sha256,
            "decision": decision.value,
            "purpose": purpose,
            "data_category": data_category,
            "retention_policy_id": retention_policy_id,
            "delete_on_completion": delete_on_completion,
            "remote_egress_allowed": remote_egress_allowed,
            "issued_at_utc": issued_at_utc,
            "expires_at_utc": expires_at_utc,
        }
        return cls(
            attempt_id_sha256=attempt_id_sha256,
            decision=decision,
            purpose=purpose,
            data_category=data_category,
            retention_policy_id=retention_policy_id,
            delete_on_completion=delete_on_completion,
            remote_egress_allowed=remote_egress_allowed,
            issued_at_utc=issued_at_utc,
            expires_at_utc=expires_at_utc,
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "decision": self.decision.value,
            "purpose": self.purpose,
            "data_category": self.data_category,
            "retention_policy_id": self.retention_policy_id,
            "delete_on_completion": self.delete_on_completion,
            "remote_egress_allowed": self.remote_egress_allowed,
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> ConsentReceipt:
        document = require_mapping(value, "consent_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "attempt_id_sha256",
                "decision",
                "purpose",
                "data_category",
                "retention_policy_id",
                "delete_on_completion",
                "remote_egress_allowed",
                "issued_at_utc",
                "expires_at_utc",
                "receipt_sha256",
            },
            "consent_receipt",
        )
        try:
            decision = ConsentDecision(document["decision"])
        except (TypeError, ValueError) as error:
            raise VisualFeedbackContractError("consent decision is unknown") from error
        return cls(
            schema_version=require_non_empty_string(
                document["schema_version"], "consent_receipt.schema_version"
            ),
            attempt_id_sha256=require_sha256(
                document["attempt_id_sha256"], "consent_receipt.attempt_id_sha256"
            ),
            decision=decision,
            purpose=require_safe_identifier(
                document["purpose"], "consent_receipt.purpose"
            ),
            data_category=require_safe_identifier(
                document["data_category"], "consent_receipt.data_category"
            ),
            retention_policy_id=require_safe_identifier(
                document["retention_policy_id"],
                "consent_receipt.retention_policy_id",
            ),
            delete_on_completion=require_boolean(
                document["delete_on_completion"],
                "consent_receipt.delete_on_completion",
            ),
            remote_egress_allowed=require_boolean(
                document["remote_egress_allowed"],
                "consent_receipt.remote_egress_allowed",
            ),
            issued_at_utc=require_utc_timestamp(
                document["issued_at_utc"], "consent_receipt.issued_at_utc"
            ),
            expires_at_utc=require_utc_timestamp(
                document["expires_at_utc"], "consent_receipt.expires_at_utc"
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "consent_receipt.receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class SeungunAnalysisReference:
    """Opaque handle to an already-produced immutable seungun analysis."""

    reference_id: str
    attempt_id_sha256: str
    analysis_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.reference_id, "reference_id")
        require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        require_sha256(self.analysis_sha256, "analysis_sha256")


@dataclass(frozen=True, slots=True)
class RestrictedVideoHandle:
    """Opaque video handle exposed to services; never a filesystem path."""

    handle_id: str
    attempt_id_sha256: str
    source_sha256: str
    timebase_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.handle_id, "handle_id")
        require_sha256(self.attempt_id_sha256, "attempt_id_sha256")
        require_sha256(self.source_sha256, "source_sha256")
        require_sha256(self.timebase_sha256, "timebase_sha256")


def _parse_timestamp(value: str, name: str) -> datetime:
    require_utc_timestamp(value, name)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "BINDING_SCHEMA_VERSION",
    "CONSENT_SCHEMA_VERSION",
    "FACIAL_VIDEO_DATA_CATEGORY",
    "RELIABILITY_SCHEMA_VERSION",
    "VISUAL_PROCESSING_PURPOSE",
    "ConsentReceipt",
    "MediaTimebaseReceipt",
    "RestrictedVideoHandle",
    "SameAttemptBindingReceipt",
    "SeungunAnalysisReference",
    "UpstreamReliabilityReceipt",
    "expected_phone_sequence_sha256",
]
