"""Non-promotional composition contract for three independent P6 evidence lanes.

This receipt is deliberately a lineage index, not a runtime bundle.  It binds
only the aggregate artifact and completion-receipt digests supplied by each
lane.  It cannot authorize a visual runtime, a claim, P8, or locked-data work.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .aihub538_photometric_calibration_v4 import (
    AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION,
)
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p6_geometry import (
    P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION,
    P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
)


P6_VISUAL_EVIDENCE_COMPOSITION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p6_visual_evidence_composition_receipt.v1"
)
P6_VISUAL_EVIDENCE_SOURCE_COMPLETION_BINDING_SCHEMA_VERSION = (
    "voice_coach.p6_visual_evidence_source_completion_binding.v1"
)
P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT = (
    "three_typed_lanes_source_artifact_and_completion_receipt_v1"
)
P6_VISUAL_EVIDENCE_COMPOSITION_STATUS = "non_promotional_evidence_composed"

_PHOTOMETRIC_COMPLETION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p6_v4_production_completion.v1"
)
_AV_SYNC_TEMPORAL_ARTIFACT_SCHEMA_VERSION = (
    "voice_coach.actual_media_av_sync_temporal_evidence.v1"
)
_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.actual_media_av_sync_temporal_completion_receipt.v1"
)

_SOURCE_COMPLETION_KEYS = {
    "completion_receipt_schema_version",
    "completion_receipt_sha256",
    "source_artifact_schema_version",
    "source_artifact_sha256",
    "source_completion_binding_sha256",
}
_RECEIPT_KEYS = {
    "av_sync_temporal_evidence_lane",
    "claim_authorized",
    "composition_cross_validation_commitment_sha256",
    "cross_validation_contract",
    "geometry_reference_lane",
    "locked_authorized",
    "locked_evaluation_authorized",
    "p8_authorized",
    "p8_promotion_authorized",
    "photometric_qualification_lane",
    "promotion_authorized",
    "receipt_sha256",
    "runtime_authorized",
    "runtime_publisher_authorized",
    "schema_version",
    "status",
    "user_visual_runtime_authorized",
    "visual_claim_authorized",
}
_AUTHORIZATION_FLAG_NAMES = (
    "runtime_authorized",
    "runtime_publisher_authorized",
    "promotion_authorized",
    "p8_authorized",
    "p8_promotion_authorized",
    "locked_authorized",
    "locked_evaluation_authorized",
    "claim_authorized",
    "visual_claim_authorized",
    "user_visual_runtime_authorized",
)


def _source_completion_binding_sha256(
    *,
    lane_kind: str,
    source_artifact_schema_version: str,
    source_artifact_sha256: str,
    completion_receipt_schema_version: str,
    completion_receipt_sha256: str,
) -> str:
    """Bind one lane's source artifact to its completion receipt."""

    return sha256_document(
        {
            "schema_version": (
                P6_VISUAL_EVIDENCE_SOURCE_COMPLETION_BINDING_SCHEMA_VERSION
            ),
            "lane_kind": lane_kind,
            "source_artifact_schema_version": source_artifact_schema_version,
            "source_artifact_sha256": source_artifact_sha256,
            "completion_receipt_schema_version": completion_receipt_schema_version,
            "completion_receipt_sha256": completion_receipt_sha256,
        }
    )


@dataclass(frozen=True, slots=True)
class P6GeometryReferenceEvidenceLane:
    """AI Hub 68 geometry/reference evidence, without runtime implication."""

    source_artifact_sha256: str
    completion_receipt_sha256: str
    source_completion_binding_sha256: str
    source_artifact_schema_version: str = P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION
    completion_receipt_schema_version: str = P6_GEOMETRY_RELOAD_SCHEMA_VERSION

    @classmethod
    def bind(
        cls,
        *,
        source_artifact_sha256: str,
        completion_receipt_sha256: str,
    ) -> "P6GeometryReferenceEvidenceLane":
        """Create the only valid AI Hub 68 source/completion binding."""

        return cls(
            source_artifact_sha256=source_artifact_sha256,
            completion_receipt_sha256=completion_receipt_sha256,
            source_completion_binding_sha256=_source_completion_binding_sha256(
                lane_kind="aihub68_geometry_reference",
                source_artifact_schema_version=P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION,
                source_artifact_sha256=source_artifact_sha256,
                completion_receipt_schema_version=P6_GEOMETRY_RELOAD_SCHEMA_VERSION,
                completion_receipt_sha256=completion_receipt_sha256,
            ),
        )

    def _validate(self) -> None:
        if (
            self.source_artifact_schema_version
            != P6_GEOMETRY_ARTIFACT_SCHEMA_VERSION
            or self.completion_receipt_schema_version
            != P6_GEOMETRY_RELOAD_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError("P6 geometry lane schema differs")
        _validate_source_completion_binding(
            lane_kind="aihub68_geometry_reference",
            source_artifact_schema_version=self.source_artifact_schema_version,
            source_artifact_sha256=self.source_artifact_sha256,
            completion_receipt_schema_version=self.completion_receipt_schema_version,
            completion_receipt_sha256=self.completion_receipt_sha256,
            source_completion_binding_sha256=self.source_completion_binding_sha256,
        )

    def to_json_object(self) -> dict[str, object]:
        self._validate()
        return _source_completion_json(self)

    @classmethod
    def from_json_object(cls, value: object) -> "P6GeometryReferenceEvidenceLane":
        fields = _parse_source_completion_json(value, "geometry_reference_lane")
        lane = cls(**fields)
        lane._validate()
        return lane


@dataclass(frozen=True, slots=True)
class P6PhotometricQualificationEvidenceLane:
    """AI Hub 538 photometric-v4 qualification, separate from P8."""

    source_artifact_sha256: str
    completion_receipt_sha256: str
    source_completion_binding_sha256: str
    source_artifact_schema_version: str = (
        AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION
    )
    completion_receipt_schema_version: str = (
        _PHOTOMETRIC_COMPLETION_RECEIPT_SCHEMA_VERSION
    )

    @classmethod
    def bind(
        cls,
        *,
        source_artifact_sha256: str,
        completion_receipt_sha256: str,
    ) -> "P6PhotometricQualificationEvidenceLane":
        """Create the only valid AI Hub 538 qualification binding."""

        return cls(
            source_artifact_sha256=source_artifact_sha256,
            completion_receipt_sha256=completion_receipt_sha256,
            source_completion_binding_sha256=_source_completion_binding_sha256(
                lane_kind="aihub538_photometric_v4_qualification",
                source_artifact_schema_version=(
                    AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION
                ),
                source_artifact_sha256=source_artifact_sha256,
                completion_receipt_schema_version=(
                    _PHOTOMETRIC_COMPLETION_RECEIPT_SCHEMA_VERSION
                ),
                completion_receipt_sha256=completion_receipt_sha256,
            ),
        )

    def _validate(self) -> None:
        if (
            self.source_artifact_schema_version
            != AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION
            or self.completion_receipt_schema_version
            != _PHOTOMETRIC_COMPLETION_RECEIPT_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError("P6 photometric lane schema differs")
        _validate_source_completion_binding(
            lane_kind="aihub538_photometric_v4_qualification",
            source_artifact_schema_version=self.source_artifact_schema_version,
            source_artifact_sha256=self.source_artifact_sha256,
            completion_receipt_schema_version=self.completion_receipt_schema_version,
            completion_receipt_sha256=self.completion_receipt_sha256,
            source_completion_binding_sha256=self.source_completion_binding_sha256,
        )

    def to_json_object(self) -> dict[str, object]:
        self._validate()
        return _source_completion_json(self)

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P6PhotometricQualificationEvidenceLane":
        fields = _parse_source_completion_json(
            value, "photometric_qualification_lane"
        )
        lane = cls(**fields)
        lane._validate()
        return lane


@dataclass(frozen=True, slots=True)
class P6AvSyncTemporalEvidenceLane:
    """A separately produced A/V-sync temporal evidence binding only."""

    source_artifact_sha256: str
    completion_receipt_sha256: str
    source_completion_binding_sha256: str
    source_artifact_schema_version: str = _AV_SYNC_TEMPORAL_ARTIFACT_SCHEMA_VERSION
    completion_receipt_schema_version: str = (
        _AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
    )

    @classmethod
    def bind(
        cls,
        *,
        source_artifact_sha256: str,
        completion_receipt_sha256: str,
    ) -> "P6AvSyncTemporalEvidenceLane":
        """Create a binding only for the independent temporal lane."""

        return cls(
            source_artifact_sha256=source_artifact_sha256,
            completion_receipt_sha256=completion_receipt_sha256,
            source_completion_binding_sha256=_source_completion_binding_sha256(
                lane_kind="separate_av_sync_temporal_evidence",
                source_artifact_schema_version=(
                    _AV_SYNC_TEMPORAL_ARTIFACT_SCHEMA_VERSION
                ),
                source_artifact_sha256=source_artifact_sha256,
                completion_receipt_schema_version=(
                    _AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
                ),
                completion_receipt_sha256=completion_receipt_sha256,
            ),
        )

    def _validate(self) -> None:
        if (
            self.source_artifact_schema_version
            != _AV_SYNC_TEMPORAL_ARTIFACT_SCHEMA_VERSION
            or self.completion_receipt_schema_version
            != _AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError("P6 A/V sync lane schema differs")
        _validate_source_completion_binding(
            lane_kind="separate_av_sync_temporal_evidence",
            source_artifact_schema_version=self.source_artifact_schema_version,
            source_artifact_sha256=self.source_artifact_sha256,
            completion_receipt_schema_version=self.completion_receipt_schema_version,
            completion_receipt_sha256=self.completion_receipt_sha256,
            source_completion_binding_sha256=self.source_completion_binding_sha256,
        )

    def to_json_object(self) -> dict[str, object]:
        self._validate()
        return _source_completion_json(self)

    @classmethod
    def from_json_object(cls, value: object) -> "P6AvSyncTemporalEvidenceLane":
        fields = _parse_source_completion_json(
            value, "av_sync_temporal_evidence_lane"
        )
        lane = cls(**fields)
        lane._validate()
        return lane


@dataclass(frozen=True, slots=True)
class P6VisualEvidenceCompositionReceipt:
    """Exact three-lane P6 evidence receipt with every authority disabled."""

    geometry_reference_lane: P6GeometryReferenceEvidenceLane
    photometric_qualification_lane: P6PhotometricQualificationEvidenceLane
    av_sync_temporal_evidence_lane: P6AvSyncTemporalEvidenceLane
    composition_cross_validation_commitment_sha256: str
    receipt_sha256: str
    schema_version: str = P6_VISUAL_EVIDENCE_COMPOSITION_RECEIPT_SCHEMA_VERSION
    status: str = P6_VISUAL_EVIDENCE_COMPOSITION_STATUS
    cross_validation_contract: str = P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT
    runtime_authorized: bool = False
    runtime_publisher_authorized: bool = False
    promotion_authorized: bool = False
    p8_authorized: bool = False
    p8_promotion_authorized: bool = False
    locked_authorized: bool = False
    locked_evaluation_authorized: bool = False
    claim_authorized: bool = False
    visual_claim_authorized: bool = False
    user_visual_runtime_authorized: bool = False

    def _validate(self) -> None:
        if (
            self.schema_version != P6_VISUAL_EVIDENCE_COMPOSITION_RECEIPT_SCHEMA_VERSION
            or self.status != P6_VISUAL_EVIDENCE_COMPOSITION_STATUS
            or self.cross_validation_contract
            != P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT
        ):
            raise VisualFeedbackContractError("P6 evidence composition contract differs")
        if type(self.geometry_reference_lane) is not P6GeometryReferenceEvidenceLane:
            raise VisualFeedbackContractError("P6 geometry lane type differs")
        if (
            type(self.photometric_qualification_lane)
            is not P6PhotometricQualificationEvidenceLane
        ):
            raise VisualFeedbackContractError("P6 photometric lane type differs")
        if type(self.av_sync_temporal_evidence_lane) is not P6AvSyncTemporalEvidenceLane:
            raise VisualFeedbackContractError("P6 A/V sync lane type differs")
        self.geometry_reference_lane._validate()
        self.photometric_qualification_lane._validate()
        self.av_sync_temporal_evidence_lane._validate()
        source_artifacts = {
            self.geometry_reference_lane.source_artifact_sha256,
            self.photometric_qualification_lane.source_artifact_sha256,
            self.av_sync_temporal_evidence_lane.source_artifact_sha256,
        }
        completions = {
            self.geometry_reference_lane.completion_receipt_sha256,
            self.photometric_qualification_lane.completion_receipt_sha256,
            self.av_sync_temporal_evidence_lane.completion_receipt_sha256,
        }
        if len(source_artifacts) != 3 or len(completions) != 3:
            raise VisualFeedbackContractError("P6 evidence lanes must be distinct")
        expected_commitment = _composition_cross_validation_commitment(
            geometry_reference_lane=self.geometry_reference_lane,
            photometric_qualification_lane=self.photometric_qualification_lane,
            av_sync_temporal_evidence_lane=self.av_sync_temporal_evidence_lane,
        )
        if (
            require_sha256(
                self.composition_cross_validation_commitment_sha256,
                "composition_cross_validation_commitment_sha256",
            )
            != expected_commitment
        ):
            raise VisualFeedbackContractError(
                "P6 evidence composition cross-validation differs"
            )
        for name in _AUTHORIZATION_FLAG_NAMES:
            if require_boolean(getattr(self, name), name) is not False:
                raise VisualFeedbackContractError(
                    "P6 evidence composition cannot authorize promotion"
                )
        unsigned = _unsigned_receipt_json(
            geometry_reference_lane=self.geometry_reference_lane,
            photometric_qualification_lane=self.photometric_qualification_lane,
            av_sync_temporal_evidence_lane=self.av_sync_temporal_evidence_lane,
            composition_cross_validation_commitment_sha256=(
                self.composition_cross_validation_commitment_sha256
            ),
        )
        if require_sha256(self.receipt_sha256, "receipt_sha256") != sha256_document(
            unsigned
        ):
            raise VisualFeedbackContractError("P6 evidence composition digest differs")

    def to_json_object(self) -> dict[str, object]:
        """Return the exact public-safe receipt document."""

        self._validate()
        return {
            **_unsigned_receipt_json(
                geometry_reference_lane=self.geometry_reference_lane,
                photometric_qualification_lane=self.photometric_qualification_lane,
                av_sync_temporal_evidence_lane=self.av_sync_temporal_evidence_lane,
                composition_cross_validation_commitment_sha256=(
                    self.composition_cross_validation_commitment_sha256
                ),
            ),
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P6VisualEvidenceCompositionReceipt":
        """Strictly reload an exact receipt and revalidate every disabled flag."""

        document = require_mapping(value, "p6_visual_evidence_composition_receipt")
        require_exact_keys(document, _RECEIPT_KEYS, "p6_visual_evidence_composition_receipt")
        receipt = cls(
            geometry_reference_lane=P6GeometryReferenceEvidenceLane.from_json_object(
                document["geometry_reference_lane"]
            ),
            photometric_qualification_lane=(
                P6PhotometricQualificationEvidenceLane.from_json_object(
                    document["photometric_qualification_lane"]
                )
            ),
            av_sync_temporal_evidence_lane=P6AvSyncTemporalEvidenceLane.from_json_object(
                document["av_sync_temporal_evidence_lane"]
            ),
            composition_cross_validation_commitment_sha256=require_sha256(
                document["composition_cross_validation_commitment_sha256"],
                "composition_cross_validation_commitment_sha256",
            ),
            receipt_sha256=require_sha256(document["receipt_sha256"], "receipt_sha256"),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            status=require_safe_identifier(document["status"], "status"),
            cross_validation_contract=require_safe_identifier(
                document["cross_validation_contract"], "cross_validation_contract"
            ),
            **{
                name: require_boolean(document[name], name)
                for name in _AUTHORIZATION_FLAG_NAMES
            },
        )
        receipt._validate()
        return receipt


def _validate_source_completion_binding(
    *,
    lane_kind: str,
    source_artifact_schema_version: str,
    source_artifact_sha256: str,
    completion_receipt_schema_version: str,
    completion_receipt_sha256: str,
    source_completion_binding_sha256: str,
) -> None:
    """Validate fixed lane schemas and their source/completion cross-binding."""

    require_safe_identifier(lane_kind, "lane_kind")
    require_safe_identifier(
        source_artifact_schema_version, "source_artifact_schema_version"
    )
    require_sha256(source_artifact_sha256, "source_artifact_sha256")
    require_safe_identifier(
        completion_receipt_schema_version, "completion_receipt_schema_version"
    )
    require_sha256(completion_receipt_sha256, "completion_receipt_sha256")
    expected = _source_completion_binding_sha256(
        lane_kind=lane_kind,
        source_artifact_schema_version=source_artifact_schema_version,
        source_artifact_sha256=source_artifact_sha256,
        completion_receipt_schema_version=completion_receipt_schema_version,
        completion_receipt_sha256=completion_receipt_sha256,
    )
    if (
        require_sha256(
            source_completion_binding_sha256,
            "source_completion_binding_sha256",
        )
        != expected
    ):
        raise VisualFeedbackContractError("P6 lane source/completion binding differs")


def _source_completion_json(
    lane: (
        P6GeometryReferenceEvidenceLane
        | P6PhotometricQualificationEvidenceLane
        | P6AvSyncTemporalEvidenceLane
    ),
) -> dict[str, object]:
    return {
        "source_artifact_schema_version": lane.source_artifact_schema_version,
        "source_artifact_sha256": lane.source_artifact_sha256,
        "completion_receipt_schema_version": lane.completion_receipt_schema_version,
        "completion_receipt_sha256": lane.completion_receipt_sha256,
        "source_completion_binding_sha256": lane.source_completion_binding_sha256,
    }


def _parse_source_completion_json(
    value: object, name: str
) -> dict[str, str]:
    document = require_mapping(value, name)
    require_exact_keys(document, _SOURCE_COMPLETION_KEYS, name)
    return {
        "source_artifact_schema_version": require_safe_identifier(
            document["source_artifact_schema_version"],
            f"{name}.source_artifact_schema_version",
        ),
        "source_artifact_sha256": require_sha256(
            document["source_artifact_sha256"], f"{name}.source_artifact_sha256"
        ),
        "completion_receipt_schema_version": require_safe_identifier(
            document["completion_receipt_schema_version"],
            f"{name}.completion_receipt_schema_version",
        ),
        "completion_receipt_sha256": require_sha256(
            document["completion_receipt_sha256"],
            f"{name}.completion_receipt_sha256",
        ),
        "source_completion_binding_sha256": require_sha256(
            document["source_completion_binding_sha256"],
            f"{name}.source_completion_binding_sha256",
        ),
    }


def _composition_cross_validation_commitment(
    *,
    geometry_reference_lane: P6GeometryReferenceEvidenceLane,
    photometric_qualification_lane: P6PhotometricQualificationEvidenceLane,
    av_sync_temporal_evidence_lane: P6AvSyncTemporalEvidenceLane,
) -> str:
    """Commit to the exact typed lanes; artifacts cannot be substituted."""

    return sha256_document(
        {
            "cross_validation_contract": P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT,
            "lanes": [
                {
                    "lane_kind": "aihub68_geometry_reference",
                    **geometry_reference_lane.to_json_object(),
                },
                {
                    "lane_kind": "aihub538_photometric_v4_qualification",
                    **photometric_qualification_lane.to_json_object(),
                },
                {
                    "lane_kind": "separate_av_sync_temporal_evidence",
                    **av_sync_temporal_evidence_lane.to_json_object(),
                },
            ],
        }
    )


def _issue_validated_composition_receipt(
    *,
    geometry_reference_lane: P6GeometryReferenceEvidenceLane,
    photometric_qualification_lane: P6PhotometricQualificationEvidenceLane,
    av_sync_temporal_evidence_lane: P6AvSyncTemporalEvidenceLane,
) -> P6VisualEvidenceCompositionReceipt:
    """Internal factory called only after the service validates all sources."""

    commitment = _composition_cross_validation_commitment(
        geometry_reference_lane=geometry_reference_lane,
        photometric_qualification_lane=photometric_qualification_lane,
        av_sync_temporal_evidence_lane=av_sync_temporal_evidence_lane,
    )
    unsigned = _unsigned_receipt_json(
        geometry_reference_lane=geometry_reference_lane,
        photometric_qualification_lane=photometric_qualification_lane,
        av_sync_temporal_evidence_lane=av_sync_temporal_evidence_lane,
        composition_cross_validation_commitment_sha256=commitment,
    )
    receipt = P6VisualEvidenceCompositionReceipt(
        geometry_reference_lane=geometry_reference_lane,
        photometric_qualification_lane=photometric_qualification_lane,
        av_sync_temporal_evidence_lane=av_sync_temporal_evidence_lane,
        composition_cross_validation_commitment_sha256=commitment,
        receipt_sha256=sha256_document(unsigned),
    )
    receipt._validate()
    return receipt


def _unsigned_receipt_json(
    *,
    geometry_reference_lane: P6GeometryReferenceEvidenceLane,
    photometric_qualification_lane: P6PhotometricQualificationEvidenceLane,
    av_sync_temporal_evidence_lane: P6AvSyncTemporalEvidenceLane,
    composition_cross_validation_commitment_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": P6_VISUAL_EVIDENCE_COMPOSITION_RECEIPT_SCHEMA_VERSION,
        "status": P6_VISUAL_EVIDENCE_COMPOSITION_STATUS,
        "cross_validation_contract": P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT,
        "geometry_reference_lane": geometry_reference_lane.to_json_object(),
        "photometric_qualification_lane": (
            photometric_qualification_lane.to_json_object()
        ),
        "av_sync_temporal_evidence_lane": (
            av_sync_temporal_evidence_lane.to_json_object()
        ),
        "composition_cross_validation_commitment_sha256": (
            composition_cross_validation_commitment_sha256
        ),
        **{name: False for name in _AUTHORIZATION_FLAG_NAMES},
    }


__all__ = [
    "P6AvSyncTemporalEvidenceLane",
    "P6GeometryReferenceEvidenceLane",
    "P6PhotometricQualificationEvidenceLane",
    "P6VisualEvidenceCompositionReceipt",
    "P6_VISUAL_EVIDENCE_COMPOSITION_RECEIPT_SCHEMA_VERSION",
    "P6_VISUAL_EVIDENCE_CROSS_VALIDATION_CONTRACT",
]
