"""Application service for non-promotional P6 visual-evidence composition."""

from __future__ import annotations

from typing import Protocol

from ..models.p6_visual_evidence_composition import (
    P6AvSyncTemporalEvidenceLane,
    P6GeometryReferenceEvidenceLane,
    P6PhotometricQualificationEvidenceLane,
    P6VisualEvidenceCompositionReceipt,
    _issue_validated_composition_receipt,
)


class P6VisualEvidenceCompositionValidationPort(Protocol):
    """External source validator for the three deliberately separate lanes."""

    def validate_geometry_reference(
        self, lane: P6GeometryReferenceEvidenceLane
    ) -> None: ...

    def validate_photometric_qualification(
        self, lane: P6PhotometricQualificationEvidenceLane
    ) -> None: ...

    def validate_av_sync_temporal(
        self, lane: P6AvSyncTemporalEvidenceLane
    ) -> None: ...


class P6VisualEvidenceCompositionService:
    """Require all source-artifact and completion-receipt hooks before issue."""

    def __init__(
        self, source_validator: P6VisualEvidenceCompositionValidationPort
    ) -> None:
        self._source_validator = source_validator

    def compose(
        self,
        *,
        geometry_reference_lane: P6GeometryReferenceEvidenceLane,
        photometric_qualification_lane: P6PhotometricQualificationEvidenceLane,
        av_sync_temporal_evidence_lane: P6AvSyncTemporalEvidenceLane,
    ) -> P6VisualEvidenceCompositionReceipt:
        """Cross-validate each source lane, then issue only a disabled receipt."""

        self._source_validator.validate_geometry_reference(geometry_reference_lane)
        self._source_validator.validate_photometric_qualification(
            photometric_qualification_lane
        )
        self._source_validator.validate_av_sync_temporal(
            av_sync_temporal_evidence_lane
        )
        return _issue_validated_composition_receipt(
            geometry_reference_lane=geometry_reference_lane,
            photometric_qualification_lane=photometric_qualification_lane,
            av_sync_temporal_evidence_lane=av_sync_temporal_evidence_lane,
        )


__all__ = [
    "P6VisualEvidenceCompositionService",
    "P6VisualEvidenceCompositionValidationPort",
]
