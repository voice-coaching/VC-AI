"""Pure Phase 0 contract for the PDF's generic suprasegmental taxonomy.

The taxonomy preserves only the dimensions and error descriptions explicitly
listed in the design PDF.  It does not define Korean ToBI categories, a
boundary-tone inventory, prominence rules, a scoring rubric, or thresholds.
Each dimension is represented explicitly so that a missing annotation cannot
be confused with an observed negative annotation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType


SUPRASEGMENTAL_TAXONOMY_POLICY_ID = "voice_coach.suprasegmental_taxonomy.pdf_generic.v1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SuprasegmentalTaxonomyError(ValueError):
    """Raised when a suprasegmental label bundle violates the contract."""


class SuprasegmentalAxis(StrEnum):
    """The generic suprasegmental dimensions named by PDF section 5.2."""

    DURATION = "duration"
    PAUSE = "pause"
    F0_CONTOUR = "f0_contour"
    BOUNDARY_TONE = "boundary_tone"
    PROMINENCE = "prominence"
    RHYTHM = "rhythm"


class SuprasegmentalErrorType(StrEnum):
    """The generic error descriptions named by PDF section 5.2."""

    EXCESSIVE_DURATION = "excessive_duration"
    INSUFFICIENT_DURATION = "insufficient_duration"
    MISPLACED_PAUSE = "misplaced_pause"
    MONOTONE_INTONATION = "monotone_intonation"
    EXCESSIVE_PITCH_EXCURSION = "excessive_pitch_excursion"
    BOUNDARY_TONE_MISMATCH = "boundary_tone_mismatch"
    MISPLACED_PROMINENCE = "misplaced_prominence"
    IRREGULAR_RHYTHM = "irregular_rhythm"


class SuprasegmentalLabelStatus(StrEnum):
    """Whether one dimension was annotated rather than merely absent."""

    OBSERVED = "observed"
    MISSING = "missing"


class SuprasegmentalEvidenceKind(StrEnum):
    """Caller-attested origin of one dimension's annotation evidence."""

    HUMAN_ANNOTATION = "human_annotation"
    DATASET_ANNOTATION = "dataset_annotation"
    DERIVED_MEASUREMENT = "derived_measurement"


class ExternalExpertReviewStatus(StrEnum):
    """Caller attestation; this primitive performs no expert verification."""

    PENDING_OR_NOT_ATTESTED = "pending_or_not_attested"
    CALLER_ATTESTED_COMPLETE = "caller_attested_complete"


CANONICAL_SUPRASEGMENTAL_AXIS_ORDER: tuple[SuprasegmentalAxis, ...] = tuple(
    SuprasegmentalAxis
)
CANONICAL_SUPRASEGMENTAL_ERROR_ORDER: tuple[SuprasegmentalErrorType, ...] = tuple(
    SuprasegmentalErrorType
)

SUPRASEGMENTAL_ERROR_AXIS = MappingProxyType(
    {
        SuprasegmentalErrorType.EXCESSIVE_DURATION: SuprasegmentalAxis.DURATION,
        SuprasegmentalErrorType.INSUFFICIENT_DURATION: SuprasegmentalAxis.DURATION,
        SuprasegmentalErrorType.MISPLACED_PAUSE: SuprasegmentalAxis.PAUSE,
        SuprasegmentalErrorType.MONOTONE_INTONATION: SuprasegmentalAxis.F0_CONTOUR,
        SuprasegmentalErrorType.EXCESSIVE_PITCH_EXCURSION: (
            SuprasegmentalAxis.F0_CONTOUR
        ),
        SuprasegmentalErrorType.BOUNDARY_TONE_MISMATCH: (
            SuprasegmentalAxis.BOUNDARY_TONE
        ),
        SuprasegmentalErrorType.MISPLACED_PROMINENCE: (SuprasegmentalAxis.PROMINENCE),
        SuprasegmentalErrorType.IRREGULAR_RHYTHM: SuprasegmentalAxis.RHYTHM,
    }
)

_CANONICAL_AXIS_SET = frozenset(CANONICAL_SUPRASEGMENTAL_AXIS_ORDER)


def _safe_id(value: object, *, field_name: str) -> str:
    if type(value) is not str or _SAFE_ID.fullmatch(value) is None:
        raise SuprasegmentalTaxonomyError(
            f"{field_name} must be a non-empty bounded policy identifier"
        )
    return value


def _optional_safe_id(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _safe_id(value, field_name=field_name)


def _sha256(value: object, *, field_name: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise SuprasegmentalTaxonomyError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return value


def _validate_complete_axis_mapping(
    value: object,
    *,
    field_name: str,
) -> Mapping[SuprasegmentalAxis, object]:
    if not isinstance(value, Mapping):
        raise SuprasegmentalTaxonomyError(f"{field_name} must be a mapping")

    provided_axes: set[SuprasegmentalAxis] = set()
    for axis in value:
        if type(axis) is not SuprasegmentalAxis:
            raise SuprasegmentalTaxonomyError(
                f"{field_name} keys must be explicit SuprasegmentalAxis values"
            )
        provided_axes.add(axis)

    if provided_axes != _CANONICAL_AXIS_SET or len(value) != len(
        CANONICAL_SUPRASEGMENTAL_AXIS_ORDER
    ):
        missing = sorted(axis.value for axis in _CANONICAL_AXIS_SET - provided_axes)
        unexpected = sorted(axis.value for axis in provided_axes - _CANONICAL_AXIS_SET)
        raise SuprasegmentalTaxonomyError(
            f"{field_name} must contain every canonical axis exactly once; "
            f"missing={missing}, unexpected={unexpected}"
        )
    return value


def _validate_missing_reason_mapping(
    value: object,
) -> Mapping[SuprasegmentalAxis, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SuprasegmentalTaxonomyError(
            "missing_reasons_by_axis must be a mapping or None"
        )
    for axis in value:
        if type(axis) is not SuprasegmentalAxis:
            raise SuprasegmentalTaxonomyError(
                "missing_reasons_by_axis keys must be explicit "
                "SuprasegmentalAxis values"
            )
    return value


def _canonical_labels(
    axis: SuprasegmentalAxis,
    value: object,
) -> tuple[SuprasegmentalErrorType, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise SuprasegmentalTaxonomyError(
            f"labels_by_axis[{axis.value}] must be a sequence or None"
        )

    provided: list[SuprasegmentalErrorType] = []
    for label in value:
        if type(label) is not SuprasegmentalErrorType:
            raise SuprasegmentalTaxonomyError(
                f"labels_by_axis[{axis.value}] must contain explicit "
                "SuprasegmentalErrorType values"
            )
        if SUPRASEGMENTAL_ERROR_AXIS[label] is not axis:
            raise SuprasegmentalTaxonomyError(
                f"{label.value} does not belong to the {axis.value} axis"
            )
        provided.append(label)

    if len(provided) != len(set(provided)):
        raise SuprasegmentalTaxonomyError(
            f"labels_by_axis[{axis.value}] must not contain duplicates"
        )
    return tuple(
        label for label in CANONICAL_SUPRASEGMENTAL_ERROR_ORDER if label in provided
    )


@dataclass(frozen=True, slots=True)
class SuprasegmentalLabelProvenance:
    """Privacy-neutral, caller-attested lineage for one axis annotation.

    The digest identifies a finalized source artifact; it does not establish
    label correctness, license, consent, rubric validity, or expert review.
    Item, participant, speaker, audio, and filesystem identifiers are outside
    this contract.
    """

    evidence_kind: SuprasegmentalEvidenceKind
    source_schema_id: str
    source_artifact_sha256: str
    annotation_protocol_id: str
    rubric_id: str | None
    external_expert_review: ExternalExpertReviewStatus

    def __post_init__(self) -> None:
        if type(self.evidence_kind) is not SuprasegmentalEvidenceKind:
            raise SuprasegmentalTaxonomyError(
                "evidence_kind must be an explicit SuprasegmentalEvidenceKind"
            )
        _safe_id(self.source_schema_id, field_name="source_schema_id")
        _sha256(
            self.source_artifact_sha256,
            field_name="source_artifact_sha256",
        )
        _safe_id(
            self.annotation_protocol_id,
            field_name="annotation_protocol_id",
        )
        _optional_safe_id(self.rubric_id, field_name="rubric_id")
        if type(self.external_expert_review) is not ExternalExpertReviewStatus:
            raise SuprasegmentalTaxonomyError(
                "external_expert_review must be an explicit ExternalExpertReviewStatus"
            )
        if (
            self.external_expert_review
            is ExternalExpertReviewStatus.CALLER_ATTESTED_COMPLETE
            and self.rubric_id is None
        ):
            raise SuprasegmentalTaxonomyError(
                "caller-attested expert review requires an explicit rubric_id"
            )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-safe provenance without row identifiers."""

        return {
            "evidence_kind": self.evidence_kind.value,
            "source_schema_id": self.source_schema_id,
            "source_artifact_sha256": self.source_artifact_sha256,
            "annotation_protocol_id": self.annotation_protocol_id,
            "rubric_id": self.rubric_id,
            "external_expert_review": self.external_expert_review.value,
        }


@dataclass(frozen=True, slots=True)
class SuprasegmentalAxisLabels:
    """One dimension's observed labels or explicit missing-label state."""

    axis: SuprasegmentalAxis
    status: SuprasegmentalLabelStatus
    labels: tuple[SuprasegmentalErrorType, ...]
    provenance: SuprasegmentalLabelProvenance
    missing_reason_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.axis) is not SuprasegmentalAxis:
            raise SuprasegmentalTaxonomyError(
                "axis must be an explicit SuprasegmentalAxis"
            )
        if type(self.status) is not SuprasegmentalLabelStatus:
            raise SuprasegmentalTaxonomyError(
                "status must be an explicit SuprasegmentalLabelStatus"
            )
        if type(self.labels) is not tuple:
            raise SuprasegmentalTaxonomyError("labels must be an exact tuple")
        if type(self.provenance) is not SuprasegmentalLabelProvenance:
            raise SuprasegmentalTaxonomyError(
                "provenance must be an exact SuprasegmentalLabelProvenance"
            )

        canonical_labels = _canonical_labels(self.axis, self.labels)
        if self.labels != canonical_labels:
            raise SuprasegmentalTaxonomyError(
                "labels must be unique and in canonical taxonomy order"
            )
        if self.status is SuprasegmentalLabelStatus.MISSING:
            if self.labels:
                raise SuprasegmentalTaxonomyError(
                    "missing dimensions cannot contain positive labels"
                )
            _safe_id(
                self.missing_reason_id,
                field_name="missing_reason_id",
            )
        elif self.missing_reason_id is not None:
            raise SuprasegmentalTaxonomyError(
                "observed dimensions cannot carry a missing_reason_id"
            )

    @property
    def is_observed(self) -> bool:
        """Return true for both observed-positive and observed-negative axes."""

        return self.status is SuprasegmentalLabelStatus.OBSERVED

    def to_dict(self) -> dict[str, object]:
        """Return one deterministic, JSON-safe dimension annotation."""

        return {
            "axis": self.axis.value,
            "status": self.status.value,
            "labels": [label.value for label in self.labels],
            "missing_reason_id": self.missing_reason_id,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SuprasegmentalLabelBundle:
    """Canonical six-axis annotation with lossless missing/multilabel state."""

    axes: tuple[SuprasegmentalAxisLabels, ...]

    def __post_init__(self) -> None:
        if type(self.axes) is not tuple or any(
            type(item) is not SuprasegmentalAxisLabels for item in self.axes
        ):
            raise SuprasegmentalTaxonomyError(
                "axes must be an exact SuprasegmentalAxisLabels tuple"
            )
        actual_order = tuple(item.axis for item in self.axes)
        if actual_order != CANONICAL_SUPRASEGMENTAL_AXIS_ORDER:
            raise SuprasegmentalTaxonomyError(
                "axes must contain every dimension once in canonical order"
            )

    @property
    def observed_axis_count(self) -> int:
        """Return the number of dimensions explicitly annotated."""

        return sum(item.is_observed for item in self.axes)

    @property
    def positive_label_count(self) -> int:
        """Return the number of active generic error labels."""

        return sum(len(item.labels) for item in self.axes)

    @property
    def is_multilabel(self) -> bool:
        """Return whether two or more generic error labels are active."""

        return self.positive_label_count > 1

    @property
    def missing_axes(self) -> tuple[SuprasegmentalAxis, ...]:
        """Return missing dimensions in canonical order."""

        return tuple(item.axis for item in self.axes if not item.is_observed)

    @property
    def active_error_types(self) -> tuple[SuprasegmentalErrorType, ...]:
        """Return active errors in canonical taxonomy order."""

        active = {label for item in self.axes for label in item.labels}
        return tuple(
            label for label in CANONICAL_SUPRASEGMENTAL_ERROR_ORDER if label in active
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic bundle without scores or decisions."""

        return {
            "policy_id": SUPRASEGMENTAL_TAXONOMY_POLICY_ID,
            "axis_order": [axis.value for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER],
            "axes": [item.to_dict() for item in self.axes],
            "active_error_types": [label.value for label in self.active_error_types],
            "observed_axis_count": self.observed_axis_count,
            "positive_label_count": self.positive_label_count,
            "is_multilabel": self.is_multilabel,
        }


def build_suprasegmental_label_bundle(
    labels_by_axis: Mapping[SuprasegmentalAxis, object],
    *,
    provenance_by_axis: Mapping[
        SuprasegmentalAxis,
        SuprasegmentalLabelProvenance,
    ],
    missing_reasons_by_axis: Mapping[SuprasegmentalAxis, str] | None = None,
) -> SuprasegmentalLabelBundle:
    """Build a canonical bundle while preserving missing versus negative labels.

    Each dimension key is required. ``None`` means missing; an empty sequence
    means the dimension was observed and none of this generic taxonomy's error
    labels was assigned. Multiple labels are retained and canonicalized.
    """

    label_mapping = _validate_complete_axis_mapping(
        labels_by_axis,
        field_name="labels_by_axis",
    )
    provenance_mapping = _validate_complete_axis_mapping(
        provenance_by_axis,
        field_name="provenance_by_axis",
    )
    missing_reason_mapping = _validate_missing_reason_mapping(missing_reasons_by_axis)

    axis_labels: list[SuprasegmentalAxisLabels] = []
    for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER:
        provenance = provenance_mapping[axis]
        if type(provenance) is not SuprasegmentalLabelProvenance:
            raise SuprasegmentalTaxonomyError(
                f"provenance_by_axis[{axis.value}] must be an exact "
                "SuprasegmentalLabelProvenance"
            )

        raw_labels = label_mapping[axis]
        if raw_labels is None:
            if axis not in missing_reason_mapping:
                raise SuprasegmentalTaxonomyError(
                    f"missing axis {axis.value} requires a missing reason"
                )
            missing_reason = _safe_id(
                missing_reason_mapping[axis],
                field_name=f"missing_reasons_by_axis[{axis.value}]",
            )
            axis_labels.append(
                SuprasegmentalAxisLabels(
                    axis=axis,
                    status=SuprasegmentalLabelStatus.MISSING,
                    labels=(),
                    provenance=provenance,
                    missing_reason_id=missing_reason,
                )
            )
            continue

        if axis in missing_reason_mapping:
            raise SuprasegmentalTaxonomyError(
                f"observed axis {axis.value} cannot have a missing reason"
            )
        axis_labels.append(
            SuprasegmentalAxisLabels(
                axis=axis,
                status=SuprasegmentalLabelStatus.OBSERVED,
                labels=_canonical_labels(axis, raw_labels),
                provenance=provenance,
            )
        )

    extra_missing_reasons = set(missing_reason_mapping) - _CANONICAL_AXIS_SET
    if extra_missing_reasons:
        raise SuprasegmentalTaxonomyError(
            "missing_reasons_by_axis contains a non-canonical axis"
        )
    return SuprasegmentalLabelBundle(axes=tuple(axis_labels))
