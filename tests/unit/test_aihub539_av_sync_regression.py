"""Source-independent tests for AI Hub 539 differential A/V-sync evidence."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
from typing import Callable

import pytest

from voice_coach.visual_feedback.models.aihub539_av_sync_regression import (
    AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT,
    AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256,
    AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION,
    AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION,
    Aihub539AvSyncAnchor,
    Aihub539AvSyncDifferentialMeasurementPair,
    Aihub539AvSyncRegressionError,
    Aihub539AvSyncRegressionResult,
    Aihub539AvSyncRegressionProtocol,
    Aihub539AvSyncRegressionRunIdentity,
    Aihub539AvSyncTemporalEvidence,
    Aihub539AvSyncTemporalEvidenceCompletionReceipt,
    parse_aihub539_av_sync_regression_protocol,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.services.aihub539_av_sync_regression import (
    Aihub539AvSyncDifferentialRegressionService,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = (
    ROOT
    / "configs"
    / "visual_feedback"
    / "aihub539_av_sync_regression_v1.json"
)
ANCHOR_TIMES_MS = (0.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0)


def _identity(**changes: object) -> Aihub539AvSyncRegressionRunIdentity:
    values: dict[str, object] = {
        "source_admission_receipt_sha256": "a" * 64,
        "source_aggregate_manifest_sha256": "b" * 64,
        "anchor_backend_revision": "synthetic_anchor_backend_v1",
        "anchor_backend_sha256": "c" * 64,
        "injection_backend_revision": "synthetic_injection_backend_v1",
        "injection_backend_sha256": "d" * 64,
    }
    values.update(changes)
    return Aihub539AvSyncRegressionRunIdentity(**values)


def _anchors(*, offset_ms: float, drift_ppm: float) -> tuple[Aihub539AvSyncAnchor, ...]:
    slope = 1.0 + drift_ppm / 1_000_000.0
    return tuple(
        Aihub539AvSyncAnchor(
            audio_time_ms=audio_time_ms,
            video_time_ms=offset_ms + slope * audio_time_ms,
        )
        for audio_time_ms in ANCHOR_TIMES_MS
    )


def _measurement_pairs(
    *,
    pairs_per_case: int = 2,
) -> tuple[Aihub539AvSyncDifferentialMeasurementPair, ...]:
    pairs: list[Aihub539AvSyncDifferentialMeasurementPair] = []
    protocol = Aihub539AvSyncRegressionProtocol.frozen()
    for case in protocol.injection_cases:
        for ordinal in range(pairs_per_case):
            baseline_offset_ms = 37.0 + 81.0 * ordinal
            baseline_drift_ppm = -900.0 + 450.0 * ordinal
            pairs.append(
                Aihub539AvSyncDifferentialMeasurementPair(
                    case_id=case.case_id,
                    pair_ordinal=ordinal,
                    baseline_anchors=_anchors(
                        offset_ms=baseline_offset_ms,
                        drift_ppm=baseline_drift_ppm,
                    ),
                    injected_anchors=_anchors(
                        offset_ms=(
                            baseline_offset_ms
                            + case.injected_offset_change_ms
                        ),
                        drift_ppm=(
                            baseline_drift_ppm
                            + case.injected_drift_change_parts_per_million
                        ),
                    ),
                )
            )
    return tuple(pairs)


def _service() -> Aihub539AvSyncDifferentialRegressionService:
    return Aihub539AvSyncDifferentialRegressionService(
        Aihub539AvSyncRegressionProtocol.frozen()
    )


def _replace_pair(
    pairs: tuple[Aihub539AvSyncDifferentialMeasurementPair, ...],
    index: int,
    replacement: Aihub539AvSyncDifferentialMeasurementPair,
) -> tuple[Aihub539AvSyncDifferentialMeasurementPair, ...]:
    mutable = list(pairs)
    mutable[index] = replacement
    return tuple(mutable)


def test_checked_in_protocol_is_exact_frozen_pre_observation_contract() -> None:
    payload = CONFIG.read_bytes()
    protocol = parse_aihub539_av_sync_regression_protocol(payload)
    document = json.loads(payload)
    unsigned = dict(document)
    observed_sha256 = unsigned.pop("protocol_sha256")

    assert protocol == Aihub539AvSyncRegressionProtocol.frozen()
    assert document == AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_DOCUMENT
    assert protocol.protocol_state == "frozen_pre_observation"
    assert protocol.aggregate_rule == (
        "all_fixed_cases_and_all_measurement_pairs_must_pass"
    )
    assert len(protocol.injection_cases) == 4
    assert {case.injected_offset_change_ms for case in protocol.injection_cases} == {
        -120.0,
        -60.0,
        60.0,
        120.0,
    }
    assert {
        case.injected_drift_change_parts_per_million
        for case in protocol.injection_cases
    } == {-2500.0, 0.0, 2500.0}
    assert observed_sha256 == AIHUB539_AV_SYNC_REGRESSION_PROTOCOL_SHA256
    assert sha256_document(unsigned) == observed_sha256
    assert protocol.native_sync_ground_truth_validated is False
    assert protocol.metadata_sync_truth_used is False
    assert protocol.p8_authorized is False


def test_protocol_rejects_observation_driven_tamper_even_with_new_digest() -> None:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    document["offset_change_absolute_tolerance_ms"] = 500.0
    unsigned = dict(document)
    unsigned.pop("protocol_sha256")
    document["protocol_sha256"] = sha256_document(unsigned)

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        Aihub539AvSyncRegressionProtocol.from_json_object(document)

    assert caught.value.reason_code == "aihub539_av_sync_protocol_invalid"


@pytest.mark.parametrize(
    "changes",
    (
        {"runtime_authorized": 0},
        {"minimum_anchor_span_ms": 4000},
        {
            "injection_cases": list(
                Aihub539AvSyncRegressionProtocol.frozen().injection_cases
            )
        },
    ),
)
def test_direct_protocol_construction_cannot_bypass_canonical_types(
    changes: dict[str, object],
) -> None:
    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        replace(Aihub539AvSyncRegressionProtocol.frozen(), **changes)

    assert caught.value.reason_code == "aihub539_av_sync_protocol_invalid"


def test_protocol_parser_rejects_duplicate_keys() -> None:
    payload = b'{"schema_version":"first","schema_version":"second"}'

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        parse_aihub539_av_sync_regression_protocol(payload)

    assert caught.value.reason_code == "aihub539_av_sync_protocol_invalid"


def test_known_offset_and_affine_drift_changes_issue_aggregate_only_evidence() -> None:
    result = _service().evaluate(
        identity=_identity(),
        measurement_pairs=_measurement_pairs(),
    )
    evidence = result.evidence.to_json_object()
    completion = result.completion_receipt.to_json_object()
    serialized = json.dumps(
        {"evidence": evidence, "completion": completion},
        ensure_ascii=False,
        sort_keys=True,
    )

    assert evidence["schema_version"] == (
        AIHUB539_AV_SYNC_TEMPORAL_EVIDENCE_SCHEMA_VERSION
    )
    assert completion["schema_version"] == (
        AIHUB539_AV_SYNC_TEMPORAL_COMPLETION_RECEIPT_SCHEMA_VERSION
    )
    assert evidence["evaluated_case_count"] == 4
    assert evidence["total_measurement_pair_count"] == 8
    assert completion["completed_case_count"] == 4
    assert completion["total_measurement_pair_count"] == 8
    assert completion["evidence_sha256"] == evidence["evidence_sha256"]
    for case_aggregate in evidence["case_aggregates"]:
        assert case_aggregate["measurement_pair_count"] == 2
        assert case_aggregate["passing_measurement_pair_count"] == 2
        assert case_aggregate["thresholds_passed"] is True
        assert case_aggregate["minimum_anchor_count"] == 6
        assert case_aggregate["minimum_anchor_span_ms"] == 5000.0

    for document in (evidence, completion):
        assert document["aggregate_only"] is True
        assert document["native_sync_ground_truth_validated"] is False
        assert document["metadata_sync_truth_used"] is False
        for field in (
            "raw_paths_serialized",
            "archive_member_names_serialized",
            "transcripts_serialized",
            "actor_identifiers_serialized",
            "per_item_media_hashes_serialized",
            "promotion_authorized",
            "p8_authorized",
            "p8_promotion_authorized",
            "locked_evaluation_authorized",
            "user_feedback_authorized",
            "user_visual_claims_authorized",
            "user_visual_runtime_authorized",
            "runtime_authorized",
            "runtime_publisher_authorized",
        ):
            assert document[field] is False
    for forbidden in (
        "/restricted/",
        ".tar",
        ".zip",
        "private transcript",
        "private actor",
            "media_item_sha256",
        ):
            assert forbidden not in serialized


def test_result_rejects_completion_count_not_bound_to_evidence() -> None:
    result = _service().evaluate(
        identity=_identity(), measurement_pairs=_measurement_pairs()
    )
    completion_unsigned = result.completion_receipt.unsigned_json_object()
    completion_unsigned["total_measurement_pair_count"] += 1
    forged = Aihub539AvSyncTemporalEvidenceCompletionReceipt(
        source_run_identity_sha256=result.completion_receipt.source_run_identity_sha256,
        evidence_sha256=result.completion_receipt.evidence_sha256,
        completed_case_count=result.completion_receipt.completed_case_count,
        total_measurement_pair_count=completion_unsigned["total_measurement_pair_count"],
        receipt_sha256=sha256_document(completion_unsigned),
    )
    with pytest.raises(Aihub539AvSyncRegressionError, match="result_binding_invalid"):
        Aihub539AvSyncRegressionResult(
            evidence=result.evidence, completion_receipt=forged
        )


def test_baseline_native_offset_and_drift_are_not_absolute_truth() -> None:
    protocol = Aihub539AvSyncRegressionProtocol.frozen()
    pairs: list[Aihub539AvSyncDifferentialMeasurementPair] = []
    for index, case in enumerate(protocol.injection_cases):
        baseline_offset_ms = 10_000.0 + index * 1234.0
        baseline_drift_ppm = -20_000.0 + index * 7000.0
        pairs.append(
            Aihub539AvSyncDifferentialMeasurementPair(
                case_id=case.case_id,
                pair_ordinal=0,
                baseline_anchors=_anchors(
                    offset_ms=baseline_offset_ms,
                    drift_ppm=baseline_drift_ppm,
                ),
                injected_anchors=_anchors(
                    offset_ms=(
                        baseline_offset_ms + case.injected_offset_change_ms
                    ),
                    drift_ppm=(
                        baseline_drift_ppm
                        + case.injected_drift_change_parts_per_million
                    ),
                ),
            )
        )

    result = _service().evaluate(
        identity=_identity(), measurement_pairs=tuple(pairs)
    )

    assert result.evidence.to_json_object()["truth_basis"] == (
        "injected_offset_and_affine_drift_change_only"
    )
    assert result.evidence.to_json_object()[
        "native_sync_ground_truth_validated"
    ] is False


@pytest.mark.parametrize(
    ("anchors", "reason_code"),
    (
        (
            tuple(
                Aihub539AvSyncAnchor(float(index), float(index))
                for index in range(5)
            ),
            "aihub539_av_sync_anchor_count_insufficient",
        ),
        (
            tuple(
                Aihub539AvSyncAnchor(0.0, float(index))
                for index in range(6)
            ),
            "aihub539_av_sync_anchors_ambiguous",
        ),
        (
            tuple(
                Aihub539AvSyncAnchor(float(index * 100), float(index * 100))
                for index in range(6)
            ),
            "aihub539_av_sync_anchors_ambiguous",
        ),
        (
            (
                Aihub539AvSyncAnchor(float("nan"), 0.0),
                *tuple(
                    Aihub539AvSyncAnchor(float(index * 1000), float(index * 1000))
                    for index in range(1, 6)
                ),
            ),
            "aihub539_av_sync_anchor_nonfinite",
        ),
        (
            (
                Aihub539AvSyncAnchor(0.0, float("inf")),
                *tuple(
                    Aihub539AvSyncAnchor(float(index * 1000), float(index * 1000))
                    for index in range(1, 6)
                ),
            ),
            "aihub539_av_sync_anchor_nonfinite",
        ),
    ),
)
def test_invalid_anchor_sets_fail_closed(
    anchors: tuple[Aihub539AvSyncAnchor, ...], reason_code: str
) -> None:
    pairs = _measurement_pairs(pairs_per_case=1)
    original = pairs[0]
    replacement = Aihub539AvSyncDifferentialMeasurementPair(
        case_id=original.case_id,
        pair_ordinal=original.pair_ordinal,
        baseline_anchors=anchors,
        injected_anchors=original.injected_anchors,
    )

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=_replace_pair(pairs, 0, replacement),
        )

    assert caught.value.reason_code == reason_code


def test_baseline_and_injected_anchor_identity_must_match() -> None:
    pairs = _measurement_pairs(pairs_per_case=1)
    original = pairs[0]
    mismatched = tuple(
        Aihub539AvSyncAnchor(
            anchor.audio_time_ms + (1.0 if index == 5 else 0.0),
            anchor.video_time_ms,
        )
        for index, anchor in enumerate(original.injected_anchors)
    )
    replacement = Aihub539AvSyncDifferentialMeasurementPair(
        case_id=original.case_id,
        pair_ordinal=0,
        baseline_anchors=original.baseline_anchors,
        injected_anchors=mismatched,
    )

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=_replace_pair(pairs, 0, replacement),
        )

    assert caught.value.reason_code == (
        "aihub539_av_sync_anchor_identity_mismatch"
    )


@pytest.mark.parametrize(
    ("offset_error_ms", "drift_error_ppm", "reason_code"),
    (
        (
            5.01,
            0.0,
            "aihub539_av_sync_offset_change_threshold_failed",
        ),
        (
            0.0,
            250.01,
            "aihub539_av_sync_drift_change_threshold_failed",
        ),
    ),
)
def test_recovered_change_threshold_failures_close_without_evidence(
    offset_error_ms: float,
    drift_error_ppm: float,
    reason_code: str,
) -> None:
    pairs = _measurement_pairs(pairs_per_case=1)
    original = pairs[0]
    first_case = Aihub539AvSyncRegressionProtocol.frozen().injection_cases[0]
    replacement = Aihub539AvSyncDifferentialMeasurementPair(
        case_id=original.case_id,
        pair_ordinal=0,
        baseline_anchors=original.baseline_anchors,
        injected_anchors=_anchors(
            offset_ms=37.0 + first_case.injected_offset_change_ms + offset_error_ms,
            drift_ppm=(
                -900.0
                + first_case.injected_drift_change_parts_per_million
                + drift_error_ppm
            ),
        ),
    )

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=_replace_pair(pairs, 0, replacement),
        )

    assert caught.value.reason_code == reason_code


def test_affine_fit_residual_threshold_failure_closes_first() -> None:
    pairs = _measurement_pairs(pairs_per_case=1)
    original = pairs[0]
    noisy = list(original.injected_anchors)
    changed = noisy[3]
    noisy[3] = Aihub539AvSyncAnchor(
        audio_time_ms=changed.audio_time_ms,
        video_time_ms=changed.video_time_ms + 30.0,
    )
    replacement = Aihub539AvSyncDifferentialMeasurementPair(
        case_id=original.case_id,
        pair_ordinal=0,
        baseline_anchors=original.baseline_anchors,
        injected_anchors=tuple(noisy),
    )

    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=_replace_pair(pairs, 0, replacement),
        )

    assert caught.value.reason_code == (
        "aihub539_av_sync_affine_fit_error_threshold_failed"
    )


def test_incomplete_or_malformed_case_and_pair_identities_fail_closed() -> None:
    pairs = _measurement_pairs(pairs_per_case=1)

    with pytest.raises(Aihub539AvSyncRegressionError) as incomplete:
        _service().evaluate(identity=_identity(), measurement_pairs=pairs[:-1])
    assert incomplete.value.reason_code == "aihub539_av_sync_case_set_incomplete"

    duplicate_ordinal = Aihub539AvSyncDifferentialMeasurementPair(
        case_id=pairs[0].case_id,
        pair_ordinal=0,
        baseline_anchors=pairs[0].baseline_anchors,
        injected_anchors=pairs[0].injected_anchors,
    )
    with pytest.raises(Aihub539AvSyncRegressionError) as duplicate:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=(pairs[0], duplicate_ordinal, *pairs[1:]),
        )
    assert duplicate.value.reason_code == (
        "aihub539_av_sync_pair_identity_malformed"
    )

    unknown_case = Aihub539AvSyncDifferentialMeasurementPair(
        case_id="unknown_case",
        pair_ordinal=0,
        baseline_anchors=pairs[0].baseline_anchors,
        injected_anchors=pairs[0].injected_anchors,
    )
    with pytest.raises(Aihub539AvSyncRegressionError) as unknown:
        _service().evaluate(
            identity=_identity(),
            measurement_pairs=(unknown_case, *pairs[1:]),
        )
    assert unknown.value.reason_code == (
        "aihub539_av_sync_case_identity_malformed"
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"dataset_id": 538},
        {"dataset_role": "p8_locked"},
        {"anchor_backend_sha256": "not-a-sha"},
        {"injection_backend_revision": "contains whitespace"},
    ),
)
def test_malformed_run_identity_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(Aihub539AvSyncRegressionError) as caught:
        _identity(**changes)

    assert caught.value.reason_code == "aihub539_av_sync_run_identity_invalid"


def test_evidence_and_completion_strict_reload_recompute_canonical_hashes() -> None:
    result = _service().evaluate(
        identity=_identity(), measurement_pairs=_measurement_pairs()
    )
    evidence_document = result.evidence.to_json_object()
    completion_document = result.completion_receipt.to_json_object()

    evidence_unsigned = dict(evidence_document)
    evidence_sha256 = evidence_unsigned.pop("evidence_sha256")
    completion_unsigned = dict(completion_document)
    completion_sha256 = completion_unsigned.pop("receipt_sha256")
    assert sha256_document(evidence_unsigned) == evidence_sha256
    assert sha256_document(completion_unsigned) == completion_sha256
    assert Aihub539AvSyncTemporalEvidence.from_json_object(
        json.loads(canonical_json_bytes(evidence_document))
    ) == result.evidence
    assert Aihub539AvSyncTemporalEvidenceCompletionReceipt.from_json_object(
        json.loads(canonical_json_bytes(completion_document))
    ) == result.completion_receipt


@pytest.mark.parametrize(
    ("target", "mutation", "digest_field", "loader"),
    (
        (
            "evidence",
            lambda document: document.__setitem__("p8_authorized", True),
            "evidence_sha256",
            Aihub539AvSyncTemporalEvidence.from_json_object,
        ),
        (
            "evidence",
            lambda document: document.__setitem__(
                "native_sync_ground_truth_validated", True
            ),
            "evidence_sha256",
            Aihub539AvSyncTemporalEvidence.from_json_object,
        ),
        (
            "evidence",
            lambda document: document["case_aggregates"][0].__setitem__(
                "thresholds_passed", False
            ),
            "evidence_sha256",
            Aihub539AvSyncTemporalEvidence.from_json_object,
        ),
        (
            "completion",
            lambda document: document.__setitem__(
                "runtime_authorized", True
            ),
            "receipt_sha256",
            Aihub539AvSyncTemporalEvidenceCompletionReceipt.from_json_object,
        ),
        (
            "completion",
            lambda document: document.__setitem__("unexpected", False),
            "receipt_sha256",
            Aihub539AvSyncTemporalEvidenceCompletionReceipt.from_json_object,
        ),
    ),
)
def test_rehashed_semantic_or_schema_tamper_is_rejected(
    target: str,
    mutation: Callable[[dict[str, object]], None],
    digest_field: str,
    loader: Callable[[object], object],
) -> None:
    result = _service().evaluate(
        identity=_identity(), measurement_pairs=_measurement_pairs()
    )
    original = (
        result.evidence.to_json_object()
        if target == "evidence"
        else result.completion_receipt.to_json_object()
    )
    document = json.loads(json.dumps(original))
    mutation(document)
    unsigned = dict(document)
    unsigned.pop(digest_field)
    document[digest_field] = sha256_document(unsigned)

    with pytest.raises(Aihub539AvSyncRegressionError):
        loader(document)


def test_canonical_digest_is_key_order_independent_and_nonfinite_json_is_rejected() -> None:
    result = _service().evaluate(
        identity=_identity(), measurement_pairs=_measurement_pairs()
    )
    document = result.evidence.to_json_object()
    reversed_document = dict(reversed(tuple(document.items())))

    assert canonical_json_bytes(document) == canonical_json_bytes(
        reversed_document
    )
    assert math.isfinite(
        float(document["case_aggregates"][0]["minimum_anchor_span_ms"])
    )
