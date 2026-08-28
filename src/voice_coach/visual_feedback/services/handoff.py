"""Detector-independent handoff from completed Seungun analysis to the sidecar."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Protocol

from ..adapters.seungun import parse_seungun_analysis
from ..errors import VisualFeedbackContractError
from ..models.binding import (
    ConsentReceipt,
    MediaTimebaseReceipt,
    RestrictedVideoHandle,
    SameAttemptBindingReceipt,
    SeungunAnalysisReference,
    UpstreamReliabilityReceipt,
    expected_phone_sequence_sha256,
)
from ..models.evidence import SeungunAnalysisSourceRecord
from ..models.integrity import (
    canonical_json_bytes,
    require_mapping,
    require_sha256,
    sha256_document,
)
from ..models.request import VisualFeedbackRequest

_REFERENCE_SCHEMA_VERSION = "voice_coach.seungun_visual_handoff_reference.v1"


class PronunciationAnalysisLike(Protocol):
    """Structural boundary implemented by Seungun ``PronunciationAnalysis``."""

    def to_json_object(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class SeungunVisualHandoff:
    """Atomically-created request and the exact source record that backs it."""

    request: VisualFeedbackRequest
    source_record: SeungunAnalysisSourceRecord

    def __post_init__(self) -> None:
        binding = self.request.binding_receipt
        record = self.source_record
        if self.request.analysis_ref != record.reference:
            raise VisualFeedbackContractError(
                "handoff request and source reference differ"
            )
        if binding.receipt_sha256 != record.binding_receipt_sha256:
            raise VisualFeedbackContractError(
                "handoff request is not backed by the registered binding"
            )
        checks = (
            binding.analysis_sha256 == record.reference.analysis_sha256,
            binding.prompt_id_sha256 == record.prompt_id_sha256,
            binding.prompt_revision == record.prompt_revision,
            binding.expected_phone_sha256 == record.expected_phone_sha256,
            binding.audio_source_sha256 == record.audio_source_sha256,
            binding.audio_derivation_receipt_sha256
            == record.audio_derivation_receipt_sha256,
            binding.audio_timebase == record.audio_timebase,
            binding.upstream_reliability_receipt_sha256
            == record.reliability_receipt.receipt_sha256,
        )
        if not all(checks):
            raise VisualFeedbackContractError(
                "handoff request and source metadata differ"
            )


class SeungunAttemptBindingFacade:
    """Bind an already-completed analysis without importing or invoking a detector."""

    def bind_completed_analysis(
        self,
        *,
        analysis: Mapping[str, object] | PronunciationAnalysisLike,
        attempt_id_sha256: str,
        prompt_id_sha256: str,
        prompt_revision: str,
        expected_phones: Sequence[str],
        audio_source_sha256: str,
        audio_timebase: MediaTimebaseReceipt,
        video_handle: RestrictedVideoHandle,
        video_timebase: MediaTimebaseReceipt,
        reliability_receipt: UpstreamReliabilityReceipt,
        consent_receipt: ConsentReceipt,
        issued_at_utc: str,
        upstream_phone_expected_index: int,
        capture_contract_id: str,
        population_id_sha256: str,
        context_id: str,
        audio_derivation_receipt_sha256: str,
    ) -> SeungunVisualHandoff:
        """Create a consistent source/request pair from one completed analysis."""

        attempt_id = require_sha256(attempt_id_sha256, "attempt_id_sha256")
        if video_handle.attempt_id_sha256 != attempt_id:
            raise VisualFeedbackContractError(
                "video handle belongs to a different attempt"
            )
        if consent_receipt.attempt_id_sha256 != attempt_id:
            raise VisualFeedbackContractError(
                "consent receipt belongs to a different attempt"
            )
        if video_handle.timebase_sha256 != video_timebase.timebase_sha256:
            raise VisualFeedbackContractError(
                "video handle and video timebase differ"
            )
        require_sha256(
            audio_derivation_receipt_sha256,
            "audio_derivation_receipt_sha256",
        )

        analysis_document = _canonical_analysis_snapshot(analysis)
        analysis_sha256 = sha256_document(analysis_document)
        reference = SeungunAnalysisReference(
            reference_id=sha256_document(
                {
                    "schema_version": _REFERENCE_SCHEMA_VERSION,
                    "attempt_id_sha256": attempt_id,
                    "analysis_sha256": analysis_sha256,
                }
            ),
            attempt_id_sha256=attempt_id,
            analysis_sha256=analysis_sha256,
        )
        anchors = parse_seungun_analysis(
            analysis_document,
            analysis_ref=reference,
        )
        analysis_expected_phone_sha256 = expected_phone_sequence_sha256(
            tuple(anchor.expected_phone for anchor in anchors)
        )
        supplied_expected_phone_sha256 = expected_phone_sequence_sha256(
            expected_phones
        )
        if analysis_expected_phone_sha256 != supplied_expected_phone_sha256:
            raise VisualFeedbackContractError(
                "prompt expected phones differ from completed Seungun analysis"
            )
        if upstream_phone_expected_index >= len(anchors):
            raise VisualFeedbackContractError(
                "upstream phone index is absent from completed Seungun analysis"
            )

        binding = SameAttemptBindingReceipt.issue(
            attempt_id_sha256=attempt_id,
            prompt_id_sha256=prompt_id_sha256,
            prompt_revision=prompt_revision,
            expected_phone_sha256=supplied_expected_phone_sha256,
            analysis_sha256=analysis_sha256,
            audio_source_sha256=audio_source_sha256,
            audio_derivation_receipt_sha256=audio_derivation_receipt_sha256,
            video_source_sha256=video_handle.source_sha256,
            audio_timebase=audio_timebase,
            video_timebase=video_timebase,
            upstream_reliability_receipt_sha256=(
                reliability_receipt.receipt_sha256
            ),
            consent_receipt_sha256=consent_receipt.receipt_sha256,
            issued_at_utc=issued_at_utc,
        )
        source_record = SeungunAnalysisSourceRecord(
            reference=reference,
            analysis=analysis_document,
            prompt_id_sha256=binding.prompt_id_sha256,
            prompt_revision=binding.prompt_revision,
            expected_phone_sha256=binding.expected_phone_sha256,
            audio_source_sha256=binding.audio_source_sha256,
            audio_timebase=binding.audio_timebase,
            reliability_receipt=reliability_receipt,
            binding_receipt_sha256=binding.receipt_sha256,
            audio_derivation_receipt_sha256=audio_derivation_receipt_sha256,
        )
        request = VisualFeedbackRequest(
            analysis_ref=reference,
            upstream_phone_expected_index=upstream_phone_expected_index,
            video_handle=video_handle,
            binding_receipt=binding,
            consent_receipt=consent_receipt,
            capture_contract_id=capture_contract_id,
            population_id_sha256=population_id_sha256,
            context_id=context_id,
        )
        return SeungunVisualHandoff(
            request=request,
            source_record=source_record,
        )


def _canonical_analysis_snapshot(
    analysis: Mapping[str, object] | PronunciationAnalysisLike,
) -> dict[str, object]:
    if isinstance(analysis, Mapping):
        raw_document: object = analysis
    else:
        serializer = getattr(analysis, "to_json_object", None)
        if not callable(serializer):
            raise VisualFeedbackContractError(
                "analysis must be a mapping or expose to_json_object()"
            )
        raw_document = serializer()
    document = require_mapping(raw_document, "seungun_analysis")
    snapshot = json.loads(canonical_json_bytes(dict(document)))
    return dict(require_mapping(snapshot, "seungun_analysis"))


__all__ = [
    "PronunciationAnalysisLike",
    "SeungunAttemptBindingFacade",
    "SeungunVisualHandoff",
]
