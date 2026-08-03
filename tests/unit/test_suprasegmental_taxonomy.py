from __future__ import annotations

from collections.abc import Mapping

import pytest

from voice_coach.evaluation.suprasegmental_taxonomy import (
    CANONICAL_SUPRASEGMENTAL_AXIS_ORDER,
    CANONICAL_SUPRASEGMENTAL_ERROR_ORDER,
    SUPRASEGMENTAL_ERROR_AXIS,
    SUPRASEGMENTAL_TAXONOMY_POLICY_ID,
    ExternalExpertReviewStatus,
    SuprasegmentalAxis,
    SuprasegmentalAxisLabels,
    SuprasegmentalErrorType,
    SuprasegmentalEvidenceKind,
    SuprasegmentalLabelBundle,
    SuprasegmentalLabelProvenance,
    SuprasegmentalLabelStatus,
    SuprasegmentalTaxonomyError,
    build_suprasegmental_label_bundle,
)


def _provenance(index: int = 1) -> SuprasegmentalLabelProvenance:
    return SuprasegmentalLabelProvenance(
        evidence_kind=SuprasegmentalEvidenceKind.HUMAN_ANNOTATION,
        source_schema_id="unit-test.annotation.v1",
        source_artifact_sha256=f"{index:064x}",
        annotation_protocol_id="unit-test.protocol.v1",
        rubric_id=None,
        external_expert_review=(ExternalExpertReviewStatus.PENDING_OR_NOT_ATTESTED),
    )


def _provenance_by_axis() -> dict[
    SuprasegmentalAxis,
    SuprasegmentalLabelProvenance,
]:
    return {
        axis: _provenance(index + 1)
        for index, axis in enumerate(CANONICAL_SUPRASEGMENTAL_AXIS_ORDER)
    }


def _empty_observed_labels() -> dict[SuprasegmentalAxis, object]:
    return {axis: () for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER}


def _build(
    labels_by_axis: Mapping[SuprasegmentalAxis, object] | None = None,
    *,
    provenance_by_axis: Mapping[
        SuprasegmentalAxis,
        SuprasegmentalLabelProvenance,
    ]
    | None = None,
    missing_reasons_by_axis: Mapping[SuprasegmentalAxis, str] | None = None,
) -> SuprasegmentalLabelBundle:
    return build_suprasegmental_label_bundle(
        _empty_observed_labels() if labels_by_axis is None else labels_by_axis,
        provenance_by_axis=(
            _provenance_by_axis() if provenance_by_axis is None else provenance_by_axis
        ),
        missing_reasons_by_axis=missing_reasons_by_axis,
    )


def test_canonical_axes_are_only_the_six_generic_pdf_dimensions() -> None:
    assert tuple(axis.value for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER) == (
        "duration",
        "pause",
        "f0_contour",
        "boundary_tone",
        "prominence",
        "rhythm",
    )


def test_canonical_errors_are_only_the_eight_generic_pdf_descriptions() -> None:
    assert tuple(label.value for label in CANONICAL_SUPRASEGMENTAL_ERROR_ORDER) == (
        "excessive_duration",
        "insufficient_duration",
        "misplaced_pause",
        "monotone_intonation",
        "excessive_pitch_excursion",
        "boundary_tone_mismatch",
        "misplaced_prominence",
        "irregular_rhythm",
    )


def test_every_error_has_exactly_one_generic_parent_axis() -> None:
    assert tuple(SUPRASEGMENTAL_ERROR_AXIS) == (*CANONICAL_SUPRASEGMENTAL_ERROR_ORDER,)
    assert (
        SUPRASEGMENTAL_ERROR_AXIS[SuprasegmentalErrorType.EXCESSIVE_DURATION]
        is SuprasegmentalAxis.DURATION
    )
    assert (
        SUPRASEGMENTAL_ERROR_AXIS[SuprasegmentalErrorType.INSUFFICIENT_DURATION]
        is SuprasegmentalAxis.DURATION
    )
    assert (
        SUPRASEGMENTAL_ERROR_AXIS[SuprasegmentalErrorType.MONOTONE_INTONATION]
        is SuprasegmentalAxis.F0_CONTOUR
    )


def test_empty_sequence_is_observed_negative_but_none_is_missing() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.BOUNDARY_TONE] = None

    bundle = _build(
        labels,
        missing_reasons_by_axis={SuprasegmentalAxis.BOUNDARY_TONE: "not_annotated"},
    )

    assert bundle.observed_axis_count == 5
    assert bundle.positive_label_count == 0
    assert bundle.missing_axes == (SuprasegmentalAxis.BOUNDARY_TONE,)
    boundary = bundle.axes[
        CANONICAL_SUPRASEGMENTAL_AXIS_ORDER.index(SuprasegmentalAxis.BOUNDARY_TONE)
    ]
    assert boundary.status is SuprasegmentalLabelStatus.MISSING
    assert boundary.labels == ()
    assert boundary.missing_reason_id == "not_annotated"
    duration = bundle.axes[0]
    assert duration.status is SuprasegmentalLabelStatus.OBSERVED
    assert duration.labels == ()
    assert duration.missing_reason_id is None


def test_multilabel_input_is_retained_in_canonical_order() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.DURATION] = (
        SuprasegmentalErrorType.INSUFFICIENT_DURATION,
        SuprasegmentalErrorType.EXCESSIVE_DURATION,
    )
    labels[SuprasegmentalAxis.F0_CONTOUR] = (
        SuprasegmentalErrorType.EXCESSIVE_PITCH_EXCURSION,
        SuprasegmentalErrorType.MONOTONE_INTONATION,
    )
    labels[SuprasegmentalAxis.BOUNDARY_TONE] = (
        SuprasegmentalErrorType.BOUNDARY_TONE_MISMATCH,
    )

    bundle = _build(labels)

    assert bundle.is_multilabel is True
    assert bundle.positive_label_count == 5
    assert bundle.active_error_types == (
        SuprasegmentalErrorType.EXCESSIVE_DURATION,
        SuprasegmentalErrorType.INSUFFICIENT_DURATION,
        SuprasegmentalErrorType.MONOTONE_INTONATION,
        SuprasegmentalErrorType.EXCESSIVE_PITCH_EXCURSION,
        SuprasegmentalErrorType.BOUNDARY_TONE_MISMATCH,
    )
    assert bundle.axes[0].labels == (
        SuprasegmentalErrorType.EXCESSIVE_DURATION,
        SuprasegmentalErrorType.INSUFFICIENT_DURATION,
    )


def test_multilabel_axes_keep_distinct_provenance() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.F0_CONTOUR] = (
        SuprasegmentalErrorType.MONOTONE_INTONATION,
    )
    labels[SuprasegmentalAxis.PROMINENCE] = (
        SuprasegmentalErrorType.MISPLACED_PROMINENCE,
    )
    provenance = _provenance_by_axis()

    bundle = _build(labels, provenance_by_axis=provenance)

    payload = bundle.to_dict()
    axes = payload["axes"]
    assert isinstance(axes, list)
    assert axes[2]["provenance"]["source_artifact_sha256"] == f"{3:064x}"
    assert axes[4]["provenance"]["source_artifact_sha256"] == f"{5:064x}"


def test_payload_has_no_tobi_inventory_score_threshold_or_decision() -> None:
    payload = _build().to_dict()

    assert set(payload) == {
        "policy_id",
        "axis_order",
        "axes",
        "active_error_types",
        "observed_axis_count",
        "positive_label_count",
        "is_multilabel",
    }
    assert payload["policy_id"] == SUPRASEGMENTAL_TAXONOMY_POLICY_ID
    serialized = repr(payload).casefold()
    for forbidden in (
        "tobi",
        "h%",
        "l%",
        "threshold",
        "score",
        "severity",
        "confidence",
        "passed",
        "speaker_id",
        "audio_path",
        "utterance_id",
    ):
        assert forbidden not in serialized


def test_all_missing_axes_remain_representable_for_coverage_audit() -> None:
    labels = {axis: None for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER}
    reasons = {
        axis: "source_field_unavailable" for axis in CANONICAL_SUPRASEGMENTAL_AXIS_ORDER
    }

    bundle = _build(labels, missing_reasons_by_axis=reasons)

    assert bundle.observed_axis_count == 0
    assert bundle.positive_label_count == 0
    assert bundle.is_multilabel is False
    assert bundle.missing_axes == CANONICAL_SUPRASEGMENTAL_AXIS_ORDER


def test_every_axis_key_is_required_and_string_keys_fail_closed() -> None:
    missing = _empty_observed_labels()
    missing.pop(SuprasegmentalAxis.RHYTHM)
    with pytest.raises(SuprasegmentalTaxonomyError, match="every canonical axis"):
        _build(missing)

    string_keyed: dict[object, object] = {
        axis.value: labels for axis, labels in _empty_observed_labels().items()
    }
    with pytest.raises(
        SuprasegmentalTaxonomyError,
        match="explicit SuprasegmentalAxis",
    ):
        _build(string_keyed)  # type: ignore[arg-type]


def test_provenance_requires_every_axis_and_exact_objects() -> None:
    incomplete = _provenance_by_axis()
    incomplete.pop(SuprasegmentalAxis.RHYTHM)
    with pytest.raises(SuprasegmentalTaxonomyError, match="every canonical axis"):
        _build(provenance_by_axis=incomplete)

    invalid: dict[SuprasegmentalAxis, object] = _provenance_by_axis()
    invalid[SuprasegmentalAxis.RHYTHM] = object()
    with pytest.raises(SuprasegmentalTaxonomyError, match="exact"):
        _build(provenance_by_axis=invalid)  # type: ignore[arg-type]


def test_error_type_cannot_be_attached_to_the_wrong_axis() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.PAUSE] = (SuprasegmentalErrorType.BOUNDARY_TONE_MISMATCH,)

    with pytest.raises(SuprasegmentalTaxonomyError, match="does not belong"):
        _build(labels)


def test_duplicate_error_type_fails_closed() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.F0_CONTOUR] = (
        SuprasegmentalErrorType.MONOTONE_INTONATION,
        SuprasegmentalErrorType.MONOTONE_INTONATION,
    )

    with pytest.raises(SuprasegmentalTaxonomyError, match="duplicates"):
        _build(labels)


@pytest.mark.parametrize(
    "invalid",
    [
        "monotone_intonation",
        1,
        True,
        object(),
    ],
)
def test_non_sequence_or_implicit_label_values_fail_closed(invalid: object) -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.F0_CONTOUR] = invalid

    with pytest.raises(SuprasegmentalTaxonomyError):
        _build(labels)


def test_missing_axis_requires_reason_and_observed_axis_rejects_reason() -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.PROMINENCE] = None
    with pytest.raises(SuprasegmentalTaxonomyError, match="requires a missing reason"):
        _build(labels)

    with pytest.raises(
        SuprasegmentalTaxonomyError,
        match="observed axis .* cannot have",
    ):
        _build(missing_reasons_by_axis={SuprasegmentalAxis.PROMINENCE: "not_annotated"})


@pytest.mark.parametrize("invalid_reason", ["", "contains space", "../path", 3])
def test_missing_reason_is_a_bounded_policy_id(invalid_reason: object) -> None:
    labels = _empty_observed_labels()
    labels[SuprasegmentalAxis.RHYTHM] = None

    with pytest.raises(SuprasegmentalTaxonomyError, match="policy identifier"):
        _build(
            labels,
            missing_reasons_by_axis={
                SuprasegmentalAxis.RHYTHM: invalid_reason  # type: ignore[dict-item]
            },
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("source_schema_id", ""),
        ("source_schema_id", "contains space"),
        ("source_artifact_sha256", "a" * 63),
        ("source_artifact_sha256", "A" * 64),
        ("annotation_protocol_id", "../protocol"),
        ("rubric_id", "rubric with space"),
    ],
)
def test_invalid_provenance_identifiers_and_digest_fail_closed(
    field: str,
    invalid: object,
) -> None:
    kwargs: dict[str, object] = {
        "evidence_kind": SuprasegmentalEvidenceKind.DATASET_ANNOTATION,
        "source_schema_id": "source.schema.v1",
        "source_artifact_sha256": "a" * 64,
        "annotation_protocol_id": "annotation.protocol.v1",
        "rubric_id": None,
        "external_expert_review": (ExternalExpertReviewStatus.PENDING_OR_NOT_ATTESTED),
    }
    kwargs[field] = invalid

    with pytest.raises(SuprasegmentalTaxonomyError):
        SuprasegmentalLabelProvenance(**kwargs)  # type: ignore[arg-type]


def test_completed_expert_review_is_only_a_caller_attestation_with_rubric_id() -> None:
    with pytest.raises(SuprasegmentalTaxonomyError, match="rubric_id"):
        SuprasegmentalLabelProvenance(
            evidence_kind=SuprasegmentalEvidenceKind.HUMAN_ANNOTATION,
            source_schema_id="source.schema.v1",
            source_artifact_sha256="a" * 64,
            annotation_protocol_id="annotation.protocol.v1",
            rubric_id=None,
            external_expert_review=(
                ExternalExpertReviewStatus.CALLER_ATTESTED_COMPLETE
            ),
        )

    reviewed = SuprasegmentalLabelProvenance(
        evidence_kind=SuprasegmentalEvidenceKind.HUMAN_ANNOTATION,
        source_schema_id="source.schema.v1",
        source_artifact_sha256="a" * 64,
        annotation_protocol_id="annotation.protocol.v1",
        rubric_id="external.rubric.v1",
        external_expert_review=(ExternalExpertReviewStatus.CALLER_ATTESTED_COMPLETE),
    )
    assert reviewed.external_expert_review is (
        ExternalExpertReviewStatus.CALLER_ATTESTED_COMPLETE
    )


def test_direct_axis_object_cannot_bypass_missing_or_order_invariants() -> None:
    with pytest.raises(SuprasegmentalTaxonomyError, match="cannot contain"):
        SuprasegmentalAxisLabels(
            axis=SuprasegmentalAxis.RHYTHM,
            status=SuprasegmentalLabelStatus.MISSING,
            labels=(SuprasegmentalErrorType.IRREGULAR_RHYTHM,),
            provenance=_provenance(),
            missing_reason_id="not_annotated",
        )

    with pytest.raises(SuprasegmentalTaxonomyError, match="canonical taxonomy"):
        SuprasegmentalAxisLabels(
            axis=SuprasegmentalAxis.DURATION,
            status=SuprasegmentalLabelStatus.OBSERVED,
            labels=(
                SuprasegmentalErrorType.INSUFFICIENT_DURATION,
                SuprasegmentalErrorType.EXCESSIVE_DURATION,
            ),
            provenance=_provenance(),
        )


def test_direct_bundle_requires_all_axes_in_canonical_order() -> None:
    observed = tuple(
        SuprasegmentalAxisLabels(
            axis=axis,
            status=SuprasegmentalLabelStatus.OBSERVED,
            labels=(),
            provenance=_provenance(index + 1),
        )
        for index, axis in enumerate(CANONICAL_SUPRASEGMENTAL_AXIS_ORDER)
    )

    with pytest.raises(SuprasegmentalTaxonomyError, match="canonical order"):
        SuprasegmentalLabelBundle(axes=tuple(reversed(observed)))
