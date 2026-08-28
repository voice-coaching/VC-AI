"""Read-only adapter for already-produced immutable Seungun evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..errors import SeungunEvidenceUnavailableError, VisualFeedbackContractError
from ..models.binding import (
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    SeungunAnalysisReference,
    expected_phone_sequence_sha256,
)
from ..models.enums import UpstreamReliabilityStatus
from ..models.evidence import (
    SEUNGUN_ANALYSIS_SCHEMA_VERSION,
    AdapterReceipt,
    SeungunAnalysisSourceRecord,
    SeungunEvidenceSnapshot,
    UpstreamPhoneAnchor,
)
from ..models.integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_non_empty_string,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    sha256_document,
)

_ANALYSIS_FIELDS = {
    "schema_version",
    "detector_version",
    "detector_config_sha256",
    "backbone_name",
    "backbone_tree_sha256",
    "expected_phones",
    "ctc_hypothesis",
    "insertions",
    "phones",
    "notices",
}
_PHONE_FIELDS = {
    "expected_index",
    "expected_phone",
    "observed_candidate",
    "alignment_operation",
    "start_ms",
    "end_ms",
    "error_score",
    "operating_threshold",
    "is_error",
    "score_semantics",
    "soft_features",
}


@runtime_checkable
class SeungunAnalysisSource(Protocol):
    """Resolve a facade-registered immutable analysis by opaque reference."""

    def get(
        self, reference: SeungunAnalysisReference
    ) -> SeungunAnalysisSourceRecord: ...


class InMemorySeungunAnalysisSource:
    """Internal host double keyed only by opaque Seungun reference hashes."""

    def __init__(self, records: Mapping[str, SeungunAnalysisSourceRecord]) -> None:
        self._records = dict(records)
        for reference_id, record in self._records.items():
            if reference_id != record.reference.reference_id:
                raise VisualFeedbackContractError(
                    "Seungun source key differs from its reference"
                )

    def get(self, reference: SeungunAnalysisReference) -> SeungunAnalysisSourceRecord:
        record = self._records.get(reference.reference_id)
        if record is None or record.reference != reference:
            raise SeungunEvidenceUnavailableError(
                "immutable Seungun analysis is unavailable"
            )
        return record


class FrozenSeungunPronunciationEvidenceAdapter:
    """Validate same-attempt binding without rerunning or mutating Seungun."""

    def __init__(
        self,
        source: SeungunAnalysisSource,
        *,
        backend_revision: str = "seungun_frozen_analysis_v1",
    ) -> None:
        self._source = source
        self._backend_revision = backend_revision
        self.resolve_count = 0

    def resolve(
        self,
        *,
        analysis_ref: SeungunAnalysisReference,
        binding_receipt: SameAttemptBindingReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> SeungunEvidenceSnapshot:
        self.resolve_count += 1
        try:
            record = self._source.get(analysis_ref)
            self._validate_binding(
                record=record,
                analysis_ref=analysis_ref,
                binding=binding_receipt,
                video_handle=video_handle,
            )
            anchors = parse_seungun_analysis(
                record.analysis,
                analysis_ref=analysis_ref,
            )
            expected_phones = tuple(anchor.expected_phone for anchor in anchors)
            if (
                expected_phone_sequence_sha256(expected_phones)
                != record.expected_phone_sha256
            ):
                raise SeungunEvidenceUnavailableError(
                    "expected phone sequence digest differs"
                )
        except SeungunEvidenceUnavailableError:
            raise
        except VisualFeedbackContractError as error:
            raise SeungunEvidenceUnavailableError(
                "Seungun evidence contract could not be validated"
            ) from error

        return SeungunEvidenceSnapshot(
            analysis_reference_id=analysis_ref.reference_id,
            analysis_sha256=analysis_ref.analysis_sha256,
            analysis_schema_version=SEUNGUN_ANALYSIS_SCHEMA_VERSION,
            attempt_id_sha256=analysis_ref.attempt_id_sha256,
            prompt_id_sha256=record.prompt_id_sha256,
            prompt_revision=record.prompt_revision,
            expected_phone_sha256=record.expected_phone_sha256,
            audio_derivation_receipt_sha256=(
                record.audio_derivation_receipt_sha256
            ),
            binding_receipt_sha256=binding_receipt.receipt_sha256,
            reliability_status=record.reliability_receipt.status,
            reliability_receipt_sha256=record.reliability_receipt.receipt_sha256,
            phone_anchors=anchors,
            adapter_receipt=AdapterReceipt(
                backend_id="frozen_seungun_evidence",
                backend_revision=self._backend_revision,
                status="resolved",
            ),
        )

    @staticmethod
    def _validate_binding(
        *,
        record: SeungunAnalysisSourceRecord,
        analysis_ref: SeungunAnalysisReference,
        binding: SameAttemptBindingReceipt,
        video_handle: RestrictedVideoHandle,
    ) -> None:
        checks = (
            binding.receipt_sha256 == record.binding_receipt_sha256,
            binding.attempt_id_sha256 == analysis_ref.attempt_id_sha256,
            binding.attempt_id_sha256 == video_handle.attempt_id_sha256,
            binding.analysis_sha256 == analysis_ref.analysis_sha256,
            binding.prompt_id_sha256 == record.prompt_id_sha256,
            binding.prompt_revision == record.prompt_revision,
            binding.expected_phone_sha256 == record.expected_phone_sha256,
            binding.audio_source_sha256 == record.audio_source_sha256,
            binding.audio_derivation_receipt_sha256
            == record.audio_derivation_receipt_sha256,
            binding.video_source_sha256 == video_handle.source_sha256,
            binding.video_timebase.timebase_sha256
            == video_handle.timebase_sha256,
            binding.audio_timebase == record.audio_timebase,
            binding.upstream_reliability_receipt_sha256
            == record.reliability_receipt.receipt_sha256,
        )
        if not all(checks):
            raise SeungunEvidenceUnavailableError(
                "same-attempt Seungun binding differs"
            )
        if record.reliability_receipt.status is not UpstreamReliabilityStatus.SUPPORTED:
            raise SeungunEvidenceUnavailableError(
                "Seungun reliability receipt is not eligible"
            )
        if sha256_document(record.analysis) != analysis_ref.analysis_sha256:
            raise SeungunEvidenceUnavailableError(
                "Seungun analysis was mutated after registration"
            )


def parse_seungun_analysis(
    analysis: Mapping[str, object],
    *,
    analysis_ref: SeungunAnalysisReference,
) -> tuple[UpstreamPhoneAnchor, ...]:
    document = require_mapping(analysis, "seungun_analysis")
    require_exact_keys(document, _ANALYSIS_FIELDS, "seungun_analysis")
    if document["schema_version"] != SEUNGUN_ANALYSIS_SCHEMA_VERSION:
        raise SeungunEvidenceUnavailableError("Seungun analysis schema differs")
    require_non_empty_string(document["detector_version"], "detector_version")
    require_sha256(document["detector_config_sha256"], "detector_config_sha256")
    require_non_empty_string(document["backbone_name"], "backbone_name")
    require_sha256(document["backbone_tree_sha256"], "backbone_tree_sha256")
    expected_phones = _phone_sequence(document["expected_phones"], "expected_phones")
    _phone_sequence(document["ctc_hypothesis"], "ctc_hypothesis", allow_empty=True)
    _phone_sequence(document["insertions"], "insertions", allow_empty=True)
    _string_sequence(document["notices"], "notices")
    raw_phones = document["phones"]
    if isinstance(raw_phones, (str, bytes)) or not isinstance(raw_phones, Sequence):
        raise VisualFeedbackContractError("Seungun phones must be an array")
    anchors = tuple(
        _phone_anchor(value, position=index, analysis_ref=analysis_ref)
        for index, value in enumerate(raw_phones)
    )
    if not anchors:
        raise VisualFeedbackContractError("Seungun phones must not be empty")
    if tuple(anchor.expected_phone for anchor in anchors) != expected_phones:
        raise VisualFeedbackContractError(
            "Seungun expected phone sequence differs from phone evidence"
        )
    return anchors


def _phone_anchor(
    value: object,
    *,
    position: int,
    analysis_ref: SeungunAnalysisReference,
) -> UpstreamPhoneAnchor:
    phone = require_mapping(value, "seungun_phone")
    require_exact_keys(phone, _PHONE_FIELDS, "seungun_phone")
    expected_index = require_integer(
        phone["expected_index"], "expected_index", minimum=0
    )
    if expected_index != position:
        raise VisualFeedbackContractError(
            "Seungun phone indices must be contiguous and ordered"
        )
    expected_phone = require_text_token(phone["expected_phone"], "expected_phone")
    observed = phone["observed_candidate"]
    if observed is not None:
        observed = require_text_token(observed, "observed_candidate")
    operation = require_non_empty_string(
        phone["alignment_operation"], "alignment_operation"
    )
    start_ms = _optional_integer(phone["start_ms"], "start_ms")
    end_ms = _optional_integer(phone["end_ms"], "end_ms")
    error_score = require_finite_number(phone["error_score"], "error_score")
    threshold = require_finite_number(
        phone["operating_threshold"], "operating_threshold"
    )
    is_error = require_boolean(phone["is_error"], "is_error")
    score_semantics = require_non_empty_string(
        phone["score_semantics"], "score_semantics"
    )
    soft_features = require_mapping(phone["soft_features"], "soft_features")
    for feature_name, feature_value in soft_features.items():
        require_safe_identifier(feature_name, "soft_features key")
        require_finite_number(feature_value, f"soft_features.{feature_name}")
    anchor_ref = sha256_document(
        {
            "analysis_reference_id": analysis_ref.reference_id,
            "expected_index": expected_index,
            "expected_phone": expected_phone,
            "observed_candidate": observed,
            "alignment_operation": operation,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "error_score": error_score,
            "operating_threshold": threshold,
            "is_error": is_error,
            "score_semantics": score_semantics,
        }
    )
    return UpstreamPhoneAnchor(
        anchor_ref=anchor_ref,
        expected_index=expected_index,
        expected_phone=expected_phone,
        alignment_operation=operation,
        start_ms=start_ms,
        end_ms=end_ms,
        observed_candidate=observed,
        error_score=error_score,
        operating_threshold=threshold,
        is_error=is_error,
        score_semantics=score_semantics,
    )


def _phone_sequence(
    value: object,
    name: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    result = tuple(require_text_token(item, f"{name}[]") for item in value)
    if not result and not allow_empty:
        raise VisualFeedbackContractError(f"{name} must not be empty")
    return result


def _string_sequence(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    return tuple(require_non_empty_string(item, f"{name}[]") for item in value)


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return require_integer(value, name, minimum=0)


__all__ = [
    "FrozenSeungunPronunciationEvidenceAdapter",
    "InMemorySeungunAnalysisSource",
    "SeungunAnalysisSource",
    "SeungunAnalysisSourceRecord",
    "parse_seungun_analysis",
]
