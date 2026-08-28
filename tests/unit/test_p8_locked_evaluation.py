"""P8 read-zero seal and one-shot access stay durable and fail-closed."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from voice_coach.visual_feedback.adapters.p8_seal_store import (
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.artifacts import (
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import (
    CalibrationScope,
    DeviationDirection,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.models.p8_evaluation_completeness import (
    P8_EVALUATION_EXCLUSION_POLICY,
)
from voice_coach.visual_feedback.models.p8_locked_evaluation import (
    P8AccessTransaction,
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
    P8_LOCKED_EVALUATION_THRESHOLDS_SHA256,
    P8_LOCKED_SOURCE_LISTING_SHA256,
)
from voice_coach.visual_feedback.models.p8_locked_source_manifest import (
    P8LockedSourceArchiveDigest,
    P8LockedSourceEvaluationResult,
    P8LockedSourceManifest,
)
from voice_coach.visual_feedback.models.p8_promotion import (
    P8ZeroViolationReplayReceipt,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (
    AvSyncDecisionSemanticPolicy,
    AvSyncSemanticPolicy,
    P8RuntimePolicyArtifact,
    PhoneWindowSemanticPolicy,
    VideoQcDecisionSemanticPolicy,
    VideoQcSemanticPolicy,
)
from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    RuntimeVisualClaimPolicy,
    VisualRuntimePipelineIdentity,
    media_capture_contract_sha256,
    visual_observation_contract_sha256,
)
from voice_coach.visual_feedback.services.p8_locked_evaluation import (
    P8AggregateEvaluationMetrics,
    P8LockedEvaluationService,
)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/manage_p8_locked_evaluation.py"
SEALED_AT = "2026-08-28T01:00:00Z"
CONSUMED_AT = "2026-08-28T01:01:00Z"


def test_read_zero_seal_pins_listing_frozen_thresholds_and_code() -> None:
    seal = _seal()

    assert seal.dataset_id == "538"
    assert seal.access_count == 0
    assert seal.sealed_before_source_access is True
    assert seal.aggregate_only is True
    assert [
        (entry.file_key, entry.partition_id, entry.role, entry.listed_size)
        for entry in seal.source_listing_digest_inputs
    ] == [
        ("55114", "VS11", "locked_media", "18_GB"),
        ("55082", "VL11", "locked_labels", "168_MB"),
    ]
    assert seal.source_listing_sha256 == P8_LOCKED_SOURCE_LISTING_SHA256
    assert (
        seal.evaluation_thresholds_sha256
        == P8_LOCKED_EVALUATION_THRESHOLDS_SHA256
    )
    assert seal.evaluation_thresholds.decode_success_minimum == 0.98
    assert (
        seal.evaluation_thresholds.supported_clip_single_face_coverage_minimum
        == 0.90
    )
    assert (
        seal.evaluation_thresholds.aggregate_supported_slice_coverage_minimum
        == 0.90
    )
    assert seal.evaluation_thresholds.av_anchor_availability_minimum == 0.80
    assert (
        seal.evaluation_thresholds.ready_absolute_sync_offset_maximum_ms
        == 120.0
    )
    assert seal.evaluation_thresholds.claim_policy_violation_maximum == 0
    assert (
        seal.evaluation_thresholds.forbidden_claim_fixture_violation_maximum
        == 0
    )
    assert P8ReadZeroSeal.from_json_object(seal.to_json_object()) == seal
    with pytest.raises(FrozenInstanceError):
        seal.access_count = 1  # type: ignore[misc]


def test_resigned_threshold_tamper_is_rejected() -> None:
    document = _seal().to_json_object()
    thresholds = document["evaluation_thresholds"]
    assert isinstance(thresholds, dict)
    thresholds["decode_success_minimum"] = 0.97
    document["evaluation_thresholds_sha256"] = sha256_document(thresholds)
    _resign(document, "seal_sha256")

    with pytest.raises(VisualFeedbackContractError, match="frozen threshold"):
        P8ReadZeroSeal.from_json_object(document)


def test_store_uses_exclusive_read_only_state_and_refuses_seal_replace(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    store = P8SealStateStore(state_directory)
    seal = _seal()

    store.create_read_zero_seal(seal)

    seal_path = state_directory / "p8_read_zero_seal.json"
    assert stat.S_IMODE(seal_path.stat().st_mode) == 0o400
    assert store.load_read_zero_seal() == seal
    with pytest.raises(P8SealStateError, match="already_exists"):
        store.create_read_zero_seal(seal)


def test_executor_persists_consumption_before_source_callback_and_fresh_reloads(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)
    observed_order: list[str] = []

    def open_and_evaluate(transaction):  # noqa: ANN001
        persisted = P8SealStateStore(state_directory).load_access_transaction()
        assert persisted == transaction
        assert stat.S_IMODE(
            (state_directory / "p8_access_transaction.json").stat().st_mode
        ) == 0o400
        observed_order.append("source_callback_after_durable_consumption")
        return _passing_result(transaction)

    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=open_and_evaluate,
    )

    assert observed_order == ["source_callback_after_durable_consumption"]
    assert report.status is P8LockedEvaluationStatus.COMPLETED
    assert report.source_open_attempted is True
    assert report.access_count == 1
    assert report.aggregate_only is True
    assert (
        report.admitted_item_count,
        report.paired_item_count,
        report.evaluated_item_count,
        report.evaluation_exclusion_count,
    ) == (100, 100, 100, 0)
    assert report.evaluation_exclusion_policy == P8_EVALUATION_EXCLUSION_POLICY
    fresh_store = P8SealStateStore(state_directory)
    persisted_transaction = fresh_store.load_access_transaction()
    expected_result = _passing_result(persisted_transaction)
    assert fresh_store.load_locked_source_manifest() == expected_result.source_manifest
    assert fresh_store.load_aggregate_metrics() == expected_result.metrics
    assert fresh_store.load_locked_report() == report
    assert report.locked_source_manifest_sha256 == (
        expected_result.source_manifest.manifest_sha256
    )
    assert stat.S_IMODE(
        (state_directory / "p8_locked_source_manifest.json").stat().st_mode
    ) == 0o400
    assert stat.S_IMODE(
        (state_directory / "p8_aggregate_evaluation_metrics.json").stat().st_mode
    ) == 0o400
    assert stat.S_IMODE(
        (state_directory / "p8_locked_evaluation_report.json").stat().st_mode
    ) == 0o400


def test_replay_is_rejected_without_calling_source_again(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    service = P8LockedEvaluationService(P8SealStateStore(state_directory))
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )
    replay_source_call_count = 0

    def replay_source(transaction):  # noqa: ANN001
        nonlocal replay_source_call_count
        replay_source_call_count += 1
        return _passing_result(transaction)

    with pytest.raises(P8SealStateError, match="already_consumed"):
        service.execute_locked_evaluation(
            expected_seal_sha256=seal.seal_sha256,
            transaction_id="p8_access_002",
            consumed_at_utc="2026-08-28T01:02:00Z",
            report_id="p8_report_002",
            open_and_evaluate_aggregate=replay_source,
        )
    assert replay_source_call_count == 0


def test_source_failure_consumes_authorization_and_cannot_rerun(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)

    def failing_source(_transaction):  # noqa: ANN001
        raise RuntimeError("sensitive source detail must not persist")

    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=failing_source,
    )

    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_locked_source_evaluation_failed",)
    assert (
        report.admitted_item_count,
        report.paired_item_count,
        report.evaluated_item_count,
        report.evaluation_exclusion_count,
    ) == (0, 0, 0, 0)
    serialized = json.dumps(report.to_json_object(), sort_keys=True)
    assert "sensitive source detail" not in serialized
    assert store.load_access_transaction().access_count == 1
    assert not (state_directory / "p8_locked_source_manifest.json").exists()
    assert not (state_directory / "p8_aggregate_evaluation_metrics.json").exists()
    with pytest.raises(P8SealStateError, match="already_consumed"):
        service.execute_locked_evaluation(
            expected_seal_sha256=seal.seal_sha256,
            transaction_id="p8_access_002",
            consumed_at_utc="2026-08-28T01:02:00Z",
            report_id="p8_report_002",
            open_and_evaluate_aggregate=_passing_result,
        )


def test_executor_rejects_bare_metrics_callback_result(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)

    def bare_metrics(transaction):  # noqa: ANN001
        return _passing_result(transaction).metrics

    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=bare_metrics,  # type: ignore[arg-type]
    )

    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_locked_source_evaluation_failed",)
    assert report.locked_source_manifest_sha256 is None
    assert report.aggregate_metrics_sha256 is None
    assert not (state_directory / "p8_locked_source_manifest.json").exists()
    assert not (state_directory / "p8_aggregate_evaluation_metrics.json").exists()


def test_executor_fails_closed_on_partial_evaluation_accounting(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)

    def partial_evaluation(transaction):  # noqa: ANN001
        source_manifest = _source_manifest(transaction)
        document = _passing_metrics(
            source_manifest.manifest_sha256
        ).to_json_object()
        document.update(
            {
                "paired_item_count": 99,
                "evaluated_item_count": 99,
                "successful_item_count": 98,
                "qc_decision_lineage_valid_item_count": 99,
            }
        )
        _resign(document, "metrics_sha256")
        metrics = P8AggregateEvaluationMetrics.from_json_object(document)
        return P8LockedSourceEvaluationResult.issue(
            source_manifest=source_manifest,
            metrics=metrics,
        )

    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=partial_evaluation,
    )

    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_locked_source_evaluation_failed",)
    assert report.aggregate_metrics_sha256 is None
    assert report.locked_source_manifest_sha256 is None
    assert report.admitted_item_count == 0
    assert report.paired_item_count == 0
    assert report.evaluated_item_count == 0
    assert report.evaluation_exclusion_count == 0


def test_locked_source_evaluation_result_round_trip_is_strict() -> None:
    seal = _seal()
    transaction = P8AccessTransaction.issue(
        transaction_id="p8_access_001",
        seal=seal,
        consumed_at_utc=CONSUMED_AT,
    )
    result = _passing_result(transaction)

    assert P8LockedSourceEvaluationResult.from_json_object(
        result.to_json_object()
    ) == result
    document = result.to_json_object()
    document["unexpected"] = True
    with pytest.raises(VisualFeedbackContractError, match="fields differ"):
        P8LockedSourceEvaluationResult.from_json_object(document)


def test_completeness_fields_are_required_in_manifest_metrics_and_report() -> None:
    seal = _seal()
    transaction = P8AccessTransaction.issue(
        transaction_id="p8_access_001",
        seal=seal,
        consumed_at_utc=CONSUMED_AT,
    )
    result = _passing_result(transaction)
    report = P8LockedEvaluationReport.issue(
        report_id="p8_report_001",
        protocol_revision=seal.protocol_revision,
        transaction=transaction,
        status=P8LockedEvaluationStatus.COMPLETED,
        source_open_attempted=True,
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        successful_item_count=99,
        failed_item_count=1,
        locked_source_manifest_sha256=result.source_manifest.manifest_sha256,
        aggregate_metrics_sha256=result.metrics.metrics_sha256,
        reason_codes=(),
    )
    contracts = (
        (
            P8LockedSourceManifest.from_json_object,
            result.source_manifest.to_json_object(),
            ("admitted_item_count", "evaluation_exclusion_policy"),
        ),
        (
            P8AggregateEvaluationMetrics.from_json_object,
            result.metrics.to_json_object(),
            (
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "evaluation_exclusion_policy",
                "qc_decision_lineage_valid_item_count",
                "ready_sync_decision_lineage_valid_case_count",
            ),
        ),
        (
            P8LockedEvaluationReport.from_json_object,
            report.to_json_object(),
            (
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "evaluation_exclusion_policy",
            ),
        ),
    )

    for parser, document, required_fields in contracts:
        for field_name in required_fields:
            missing = json.loads(json.dumps(document))
            missing.pop(field_name)
            with pytest.raises(VisualFeedbackContractError, match="fields differ"):
                parser(missing)


@pytest.mark.parametrize(
    ("changes", "error_match"),
    (
        (
            {
                "paired_item_count": 99,
                "evaluated_item_count": 99,
                "successful_item_count": 98,
                "qc_decision_lineage_valid_item_count": 99,
            },
            "admission pairing/exclusion counts differ",
        ),
        (
            {
                "evaluated_item_count": 99,
                "successful_item_count": 98,
                "qc_decision_lineage_valid_item_count": 99,
            },
            "paired/evaluated counts differ",
        ),
    ),
)
def test_metrics_reject_partial_pairing_or_evaluation(
    changes: dict[str, int],
    error_match: str,
) -> None:
    document = _passing_metrics(_sha("actual_manifest")).to_json_object()
    document.update(changes)
    _resign(document, "metrics_sha256")

    with pytest.raises(VisualFeedbackContractError, match=error_match):
        P8AggregateEvaluationMetrics.from_json_object(document)


@pytest.mark.parametrize(
    ("field_name", "field_value", "error_match"),
    (
        (
            "qc_decision_lineage_valid_item_count",
            99,
            "QC decision lineage coverage is incomplete",
        ),
        (
            "ready_sync_decision_lineage_valid_case_count",
            79,
            "ready sync decision lineage coverage is incomplete",
        ),
    ),
)
def test_metrics_reject_raw_candidate_only_success_or_ready_counts(
    field_name: str,
    field_value: int,
    error_match: str,
) -> None:
    document = _passing_metrics(_sha("actual_manifest")).to_json_object()
    document[field_name] = field_value
    _resign(document, "metrics_sha256")

    with pytest.raises(VisualFeedbackContractError, match=error_match):
        P8AggregateEvaluationMetrics.from_json_object(document)


def test_locked_result_rejects_manifest_admission_count_mismatch() -> None:
    transaction = P8AccessTransaction.issue(
        transaction_id="p8_access_001",
        seal=_seal(),
        consumed_at_utc=CONSUMED_AT,
    )
    source_manifest = P8LockedSourceManifest.issue(
        source_revision="aihub_538_v1",
        transaction=transaction,
        admitted_item_count=101,
        archives=_source_manifest(transaction).archives,
    )
    metrics = _passing_metrics(source_manifest.manifest_sha256)

    with pytest.raises(
        VisualFeedbackContractError,
        match="admission count differs",
    ):
        P8LockedSourceEvaluationResult.issue(
            source_manifest=source_manifest,
            metrics=metrics,
        )


def test_zero_violation_replay_rejects_partial_or_missing_evaluation_counts() -> None:
    receipt = P8ZeroViolationReplayReceipt._issue_from_validated_locked_evaluation(
        protocol_revision="p8_protocol_v1",
        read_zero_seal_sha256=_sha("seal"),
        locked_evaluation_report_sha256=_sha("report"),
        locked_manifest_sha256=_sha("actual_manifest"),
        aggregate_metrics_sha256=_sha("metrics"),
        p6_promotion_receipt_sha256=_sha("p6_promotion"),
        calibration_artifact_sha256=_sha("calibration"),
        catalog_sha256=_sha("catalog"),
        policy_sha256=_sha("policy"),
        pipeline_identity_sha256=_sha("pipeline"),
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        evaluation_exclusion_policy=P8_EVALUATION_EXCLUSION_POLICY,
        evaluated_case_count=100,
        ready_case_count=80,
    )
    assert P8ZeroViolationReplayReceipt.from_json_object(
        receipt.to_json_object()
    ) == receipt

    missing = receipt.to_json_object()
    missing.pop("admitted_item_count")
    with pytest.raises(VisualFeedbackContractError, match="fields differ"):
        P8ZeroViolationReplayReceipt.from_json_object(missing)

    partial = receipt.to_json_object()
    partial["paired_item_count"] = 99
    partial["evaluated_item_count"] = 99
    partial["evaluation_exclusion_count"] = 1
    partial["evaluated_case_count"] = 99
    _resign(partial, "receipt_sha256")
    with pytest.raises(
        VisualFeedbackContractError,
        match="cannot promote an incomplete evaluation",
    ):
        P8ZeroViolationReplayReceipt.from_json_object(partial)


def test_threshold_failure_is_aggregate_only_and_not_completed(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    service = P8LockedEvaluationService(P8SealStateStore(state_directory))
    service.create_read_zero_seal(seal)
    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_threshold_failure_result,
    )
    persisted_transaction = P8SealStateStore(
        state_directory
    ).load_access_transaction()
    expected_result = _threshold_failure_result(persisted_transaction)
    metrics = expected_result.metrics

    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.aggregate_metrics_sha256 == metrics.metrics_sha256
    assert report.reason_codes == (
        "p8_decode_success_below_threshold",
        "p8_supported_clip_single_face_coverage_below_threshold",
        "p8_aggregate_supported_slice_coverage_below_threshold",
        "p8_av_anchor_availability_below_threshold",
        "p8_ready_sync_offset_above_threshold",
        "p8_claim_policy_violation_detected",
        "p8_forbidden_claim_fixture_violation_detected",
    )
    assert P8SealStateStore(state_directory).load_aggregate_metrics() == metrics
    assert P8SealStateStore(state_directory).load_locked_report() == report


def test_any_admission_exclusion_is_persisted_but_fails_closed(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)

    report = service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_exclusion_result,
    )

    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_evaluation_exclusion_detected",)
    assert (
        report.admitted_item_count,
        report.paired_item_count,
        report.evaluated_item_count,
        report.evaluation_exclusion_count,
    ) == (100, 99, 99, 1)
    assert report.evaluation_exclusion_policy == P8_EVALUATION_EXCLUSION_POLICY
    fresh_report, fresh_manifest, fresh_metrics = (
        P8SealStateStore(state_directory).load_locked_evaluation_result()
    )
    assert fresh_report == report
    assert fresh_manifest is not None
    assert fresh_metrics is not None
    assert fresh_manifest.admitted_item_count == 100
    assert fresh_metrics.evaluation_exclusion_count == 1


def test_fresh_reload_rejects_resigned_manifest_metrics_admission_mismatch(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )
    manifest_path = state_directory / "p8_locked_source_manifest.json"
    metrics_path = state_directory / "p8_aggregate_evaluation_metrics.json"
    report_path = state_directory / "p8_locked_evaluation_report.json"
    for path in (manifest_path, metrics_path, report_path):
        os.chmod(path, 0o600)
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_document["admitted_item_count"] = 101
    _resign(manifest_document, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest_document), encoding="utf-8")
    metrics_document = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_document["locked_source_manifest_sha256"] = manifest_document[
        "manifest_sha256"
    ]
    _resign(metrics_document, "metrics_sha256")
    metrics_path.write_text(json.dumps(metrics_document), encoding="utf-8")
    report_document = json.loads(report_path.read_text(encoding="utf-8"))
    report_document["locked_source_manifest_sha256"] = manifest_document[
        "manifest_sha256"
    ]
    report_document["aggregate_metrics_sha256"] = metrics_document[
        "metrics_sha256"
    ]
    _resign(report_document, "report_sha256")
    report_path.write_text(json.dumps(report_document), encoding="utf-8")
    for path in (manifest_path, metrics_path, report_path):
        os.chmod(path, 0o400)

    with pytest.raises(P8SealStateError, match="admission_count_pin_mismatch"):
        P8SealStateStore(state_directory).load_locked_report()


def test_fresh_reload_rejects_resigned_report_count_mismatch(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )
    report_path = state_directory / "p8_locked_evaluation_report.json"
    os.chmod(report_path, 0o600)
    report_document = json.loads(report_path.read_text(encoding="utf-8"))
    report_document.update(
        {
            "admitted_item_count": 99,
            "paired_item_count": 99,
            "evaluated_item_count": 99,
            "successful_item_count": 98,
        }
    )
    _resign(report_document, "report_sha256")
    report_path.write_text(json.dumps(report_document), encoding="utf-8")
    os.chmod(report_path, 0o400)

    with pytest.raises(P8SealStateError, match="aggregate_counts_mismatch"):
        P8SealStateStore(state_directory).load_locked_report()


def test_cli_reload_emits_digest_bound_completeness_aggregates(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    service = P8LockedEvaluationService(P8SealStateStore(state_directory))
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )

    for artifact_kind in ("manifest", "metrics", "report"):
        completed = _run_cli(
            "reload",
            "--state-dir",
            str(state_directory),
            artifact_kind,
        )
        assert completed.returncode == 0, completed.stderr
        document = json.loads(completed.stdout)
        assert document["admitted_item_count"] == 100
        assert document["evaluation_exclusion_policy"] == (
            P8_EVALUATION_EXCLUSION_POLICY
        )
        if artifact_kind != "manifest":
            assert document["paired_item_count"] == 100
            assert document["evaluated_item_count"] == 100
            assert document["evaluation_exclusion_count"] == 0
        if artifact_kind == "metrics":
            assert document["qc_decision_lineage_valid_item_count"] == 100
            assert (
                document["ready_sync_decision_lineage_valid_case_count"]
                == document["ready_case_count"]
                == 80
            )


def test_fresh_reload_recomputes_thresholds_and_rejects_resigned_status_lie(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )
    metrics_path = state_directory / "p8_aggregate_evaluation_metrics.json"
    report_path = state_directory / "p8_locked_evaluation_report.json"
    os.chmod(metrics_path, 0o600)
    os.chmod(report_path, 0o600)
    metrics_document = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics_document["decode_success_rate"] = 0.97
    _resign(metrics_document, "metrics_sha256")
    metrics_path.write_text(json.dumps(metrics_document), encoding="utf-8")
    report_document = json.loads(report_path.read_text(encoding="utf-8"))
    report_document["aggregate_metrics_sha256"] = metrics_document["metrics_sha256"]
    _resign(report_document, "report_sha256")
    report_path.write_text(json.dumps(report_document), encoding="utf-8")
    os.chmod(metrics_path, 0o400)
    os.chmod(report_path, 0o400)

    with pytest.raises(P8SealStateError, match="threshold_replay_mismatch"):
        P8SealStateStore(state_directory).load_locked_report()


def test_fresh_reload_rejects_resigned_actual_manifest_cross_pin_tamper(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    service = P8LockedEvaluationService(store)
    service.create_read_zero_seal(seal)
    service.execute_locked_evaluation(
        expected_seal_sha256=seal.seal_sha256,
        transaction_id="p8_access_001",
        consumed_at_utc=CONSUMED_AT,
        report_id="p8_report_001",
        open_and_evaluate_aggregate=_passing_result,
    )
    manifest_path = state_directory / "p8_locked_source_manifest.json"
    os.chmod(manifest_path, 0o600)
    manifest_document = json.loads(manifest_path.read_text(encoding="utf-8"))
    archives = manifest_document["archives"]
    assert isinstance(archives, list)
    first_archive = archives[0]
    assert isinstance(first_archive, dict)
    first_archive["byte_count"] = int(first_archive["byte_count"]) + 1
    manifest_document["total_byte_count"] = (
        int(manifest_document["total_byte_count"]) + 1
    )
    _resign(manifest_document, "manifest_sha256")
    manifest_path.write_text(json.dumps(manifest_document), encoding="utf-8")
    os.chmod(manifest_path, 0o400)

    with pytest.raises(P8SealStateError, match="source_manifest_pin_mismatch"):
        P8SealStateStore(state_directory).load_locked_report()


def test_tampered_durable_seal_fails_before_source_callback(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    seal = _seal()
    store = P8SealStateStore(state_directory)
    store.create_read_zero_seal(seal)
    seal_path = state_directory / "p8_read_zero_seal.json"
    os.chmod(seal_path, 0o600)
    document = json.loads(seal_path.read_text(encoding="utf-8"))
    document["decoder_code_sha256"] = _sha("tampered_decoder")
    seal_path.write_text(json.dumps(document), encoding="utf-8")
    os.chmod(seal_path, 0o400)
    source_call_count = 0

    def source(transaction):  # noqa: ANN001
        nonlocal source_call_count
        source_call_count += 1
        return _passing_result(transaction)

    with pytest.raises(P8SealStateError, match="seal_invalid"):
        P8LockedEvaluationService(store).execute_locked_evaluation(
            expected_seal_sha256=seal.seal_sha256,
            transaction_id="p8_access_001",
            consumed_at_utc=CONSUMED_AT,
            report_id="p8_report_001",
            open_and_evaluate_aggregate=source,
        )
    assert source_call_count == 0
    assert not (state_directory / "p8_access_transaction.json").exists()


def test_cli_seal_reload_and_explicit_non_execution_consumption(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    file_arguments, pinned_files = _pinned_file_arguments(tmp_path)
    sealed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "seal",
            "--state-dir",
            str(state_directory),
            "--seal-id",
            "p8_seal_001",
            "--protocol-revision",
            "p8_protocol_v1",
            "--source-revision",
            "aihub_538_v1",
            "--sealed-at-utc",
            SEALED_AT,
            *file_arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert sealed.returncode == 0, sealed.stderr
    seal_document = json.loads(sealed.stdout)
    assert str(state_directory) not in sealed.stdout
    assert seal_document["decoder_code_sha256"] == hashlib.sha256(
        pinned_files["decoder-code"]
    ).hexdigest()
    assert seal_document["landmark_model_sha256"] == hashlib.sha256(
        pinned_files["landmark-model"]
    ).hexdigest()
    p6_path = _argument_path(file_arguments, "--p6-evidence-receipt-file")
    p6_document = json.loads(p6_path.read_text(encoding="utf-8"))
    assert seal_document["p6_evidence_receipt_sha256"] == (
        p6_document["receipt_sha256"]
    )
    assert seal_document["p6_evidence_receipt_sha256"] != hashlib.sha256(
        p6_path.read_bytes()
    ).hexdigest()
    identity_path = _argument_path(file_arguments, "--pipeline-identity-file")
    identity_document = json.loads(identity_path.read_text(encoding="utf-8"))
    assert seal_document["decoder_code_sha256"] == identity_document[
        "decoder_sha256"
    ]
    assert seal_document["video_qc_measurement_policy_sha256"] == identity_document[
        "video_qc_measurement_policy_sha256"
    ]

    reloaded = _run_cli(
        "reload", "--state-dir", str(state_directory), "seal"
    )
    assert reloaded.returncode == 0, reloaded.stderr
    assert json.loads(reloaded.stdout) == seal_document

    consumed = _run_cli(
        "consume-without-source-open",
        "--state-dir",
        str(state_directory),
        "--expected-seal-sha256",
        seal_document["seal_sha256"],
        "--transaction-id",
        "p8_access_001",
        "--report-id",
        "p8_report_001",
        "--consumed-at-utc",
        CONSUMED_AT,
        "--confirm-irreversible-consumption",
    )
    assert consumed.returncode == 0, consumed.stderr
    report_document = json.loads(consumed.stdout)
    assert report_document["status"] == (
        "authorization_consumed_without_source_open"
    )
    assert report_document["source_open_attempted"] is False
    assert report_document["access_count"] == 1

    report_reload = _run_cli(
        "reload", "--state-dir", str(state_directory), "report"
    )
    assert report_reload.returncode == 0, report_reload.stderr
    assert json.loads(report_reload.stdout) == report_document


def test_cli_rejects_symlinked_pin_without_creating_seal(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    file_arguments, _ = _pinned_file_arguments(tmp_path)
    decoder_option_index = file_arguments.index("--decoder-code-file")
    decoder_path = Path(file_arguments[decoder_option_index + 1])
    actual_path = tmp_path / "actual-decoder.bin"
    actual_path.write_bytes(b"actual decoder\n")
    decoder_path.unlink()
    decoder_path.symlink_to(actual_path)

    completed = _run_cli(
        "seal",
        "--state-dir",
        str(state_directory),
        "--seal-id",
        "p8_seal_001",
        "--protocol-revision",
        "p8_protocol_v1",
        "--source-revision",
        "aihub_538_v1",
        "--sealed-at-utc",
        SEALED_AT,
        *file_arguments,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr) == {
        "reason_codes": ["p8_locked_state_operation_failed"],
        "status": "failed_closed",
    }
    assert not (state_directory / "p8_read_zero_seal.json").exists()


def test_cli_fails_closed_without_semantic_policy_artifact(tmp_path: Path) -> None:
    state_directory = _state_directory(tmp_path)
    file_arguments, _ = _pinned_file_arguments(tmp_path)
    policy_index = file_arguments.index("--video-qc-measurement-policy-file")
    file_arguments[policy_index + 1] = str(tmp_path / "missing-policy.json")

    completed = _run_cli(
        "seal",
        "--state-dir",
        str(state_directory),
        "--seal-id",
        "p8_seal_001",
        "--protocol-revision",
        "p8_protocol_v1",
        "--source-revision",
        "aihub_538_v1",
        "--sealed-at-utc",
        SEALED_AT,
        *file_arguments,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert not (state_directory / "p8_read_zero_seal.json").exists()


def test_cli_rejects_raw_pipeline_bytes_that_differ_from_identity(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    file_arguments, _ = _pinned_file_arguments(tmp_path)
    decoder_path = _argument_path(file_arguments, "--decoder-code-file")
    decoder_path.write_bytes(b"different decoder bytes\n")

    completed = _run_cli(
        "seal",
        "--state-dir",
        str(state_directory),
        "--seal-id",
        "p8_seal_001",
        "--protocol-revision",
        "p8_protocol_v1",
        "--source-revision",
        "aihub_538_v1",
        "--sealed-at-utc",
        SEALED_AT,
        *file_arguments,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert not (state_directory / "p8_read_zero_seal.json").exists()


def test_cli_rejects_runtime_identity_and_calibration_observation_mismatch(
    tmp_path: Path,
) -> None:
    state_directory = _state_directory(tmp_path)
    file_arguments, _ = _pinned_file_arguments(tmp_path)
    identity_path = _argument_path(file_arguments, "--pipeline-identity-file")
    p6_path = _argument_path(file_arguments, "--p6-evidence-receipt-file")
    identity_document = json.loads(identity_path.read_text(encoding="utf-8"))
    identity_document["visual_observation_contract_id"] = (
        "different_visual_observation_v1"
    )
    identity_document["visual_observation_contract_sha256"] = (
        visual_observation_contract_sha256("different_visual_observation_v1")
    )
    _resign(identity_document, "identity_sha256")
    identity_path.write_bytes(canonical_json_bytes(identity_document) + b"\n")
    p6_document = json.loads(p6_path.read_text(encoding="utf-8"))
    p6_document["pipeline_identity_sha256"] = identity_document[
        "identity_sha256"
    ]
    p6_document["visual_observation_contract_id"] = identity_document[
        "visual_observation_contract_id"
    ]
    p6_document["visual_observation_contract_sha256"] = identity_document[
        "visual_observation_contract_sha256"
    ]
    _resign(p6_document, "receipt_sha256")
    p6_path.write_bytes(canonical_json_bytes(p6_document) + b"\n")

    completed = _run_cli(
        "seal",
        "--state-dir",
        str(state_directory),
        "--seal-id",
        "p8_seal_001",
        "--protocol-revision",
        "p8_protocol_v1",
        "--source-revision",
        "aihub_538_v1",
        "--sealed-at-utc",
        SEALED_AT,
        *file_arguments,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert not (state_directory / "p8_read_zero_seal.json").exists()


def _seal() -> P8ReadZeroSeal:
    return P8ReadZeroSeal.issue(
        seal_id="p8_seal_001",
        protocol_revision="p8_protocol_v1",
        source_revision="aihub_538_v1",
        p6_evidence_receipt_sha256=_sha("p6_receipt"),
        calibration_artifact_sha256=_sha("calibration"),
        claim_catalog_sha256=_sha("catalog"),
        claim_policy_sha256=_sha("policy"),
        media_capture_contract_id="p8_test_media_capture_v1",
        media_capture_contract_sha256=media_capture_contract_sha256(
            "p8_test_media_capture_v1"
        ),
        visual_observation_contract_id="p8_test_visual_observation_v1",
        visual_observation_contract_sha256=visual_observation_contract_sha256(
            "p8_test_visual_observation_v1"
        ),
        decoder_code_sha256=_sha("decoder"),
        tracker_code_sha256=_sha("tracker"),
        tracker_adapter_code_sha256=_sha("tracker_adapter"),
        landmark_model_sha256=_sha("landmark_model"),
        normalizer_code_sha256=_sha("normalizer"),
        cue_extractor_code_sha256=_sha("cue_extractor"),
        video_qc_measurement_code_sha256=_sha("video_qc_measurement"),
        video_qc_measurement_policy_sha256=_sha("video_qc_measurement_policy"),
        video_qc_decision_code_sha256=_sha("video_qc_decision"),
        video_qc_decision_policy_revision="p8_test_video_qc_decision_v1",
        video_qc_decision_policy_sha256=_sha("video_qc_decision_policy"),
        av_sync_measurement_code_sha256=_sha("av_sync_measurement"),
        av_sync_measurement_policy_sha256=_sha("av_sync_measurement_policy"),
        av_sync_decision_code_sha256=_sha("av_sync_decision"),
        av_sync_decision_policy_revision="p8_test_av_sync_decision_v1",
        av_sync_decision_policy_sha256=_sha("av_sync_decision_policy"),
        phone_window_code_sha256=_sha("phone_window"),
        phone_window_policy_revision="p8_test_phone_window_policy_v1",
        phone_window_policy_sha256=_sha("phone_window_policy"),
        sealed_at_utc=SEALED_AT,
    )


def _source_manifest(transaction: P8AccessTransaction) -> P8LockedSourceManifest:
    return P8LockedSourceManifest.issue(
        source_revision="aihub_538_v1",
        transaction=transaction,
        admitted_item_count=100,
        archives=(
            P8LockedSourceArchiveDigest(
                file_key="55114",
                partition_id="VS11",
                role="locked_media",
                byte_count=18_000,
                archive_sha256=_sha("locked_media_archive"),
            ),
            P8LockedSourceArchiveDigest(
                file_key="55082",
                partition_id="VL11",
                role="locked_labels",
                byte_count=168,
                archive_sha256=_sha("locked_label_archive"),
            ),
        ),
    )


def _passing_metrics(
    locked_source_manifest_sha256: str,
) -> P8AggregateEvaluationMetrics:
    return P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=locked_source_manifest_sha256,
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        successful_item_count=99,
        failed_item_count=1,
        qc_decision_lineage_valid_item_count=100,
        decode_success_rate=0.99,
        supported_clip_count=90,
        supported_clip_below_single_face_threshold_count=0,
        aggregate_supported_slice_coverage=0.91,
        av_anchor_availability_rate=0.81,
        ready_case_count=80,
        ready_sync_decision_lineage_valid_case_count=80,
        maximum_ready_absolute_sync_offset_ms=119.0,
        claim_policy_violation_count=0,
        forbidden_claim_fixture_violation_count=0,
    )


def _passing_result(
    transaction: P8AccessTransaction,
) -> P8LockedSourceEvaluationResult:
    source_manifest = _source_manifest(transaction)
    return P8LockedSourceEvaluationResult.issue(
        source_manifest=source_manifest,
        metrics=_passing_metrics(source_manifest.manifest_sha256),
    )


def _threshold_failure_result(
    transaction: P8AccessTransaction,
) -> P8LockedSourceEvaluationResult:
    source_manifest = _source_manifest(transaction)
    metrics = P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=source_manifest.manifest_sha256,
        admitted_item_count=100,
        paired_item_count=100,
        evaluated_item_count=100,
        evaluation_exclusion_count=0,
        successful_item_count=97,
        failed_item_count=3,
        qc_decision_lineage_valid_item_count=100,
        decode_success_rate=0.97,
        supported_clip_count=90,
        supported_clip_below_single_face_threshold_count=1,
        aggregate_supported_slice_coverage=0.89,
        av_anchor_availability_rate=0.79,
        ready_case_count=70,
        ready_sync_decision_lineage_valid_case_count=70,
        maximum_ready_absolute_sync_offset_ms=121.0,
        claim_policy_violation_count=1,
        forbidden_claim_fixture_violation_count=1,
    )
    return P8LockedSourceEvaluationResult.issue(
        source_manifest=source_manifest,
        metrics=metrics,
    )


def _exclusion_result(
    transaction: P8AccessTransaction,
) -> P8LockedSourceEvaluationResult:
    source_manifest = _source_manifest(transaction)
    metrics = P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=source_manifest.manifest_sha256,
        admitted_item_count=100,
        paired_item_count=99,
        evaluated_item_count=99,
        evaluation_exclusion_count=1,
        successful_item_count=98,
        failed_item_count=1,
        qc_decision_lineage_valid_item_count=99,
        decode_success_rate=0.99,
        supported_clip_count=90,
        supported_clip_below_single_face_threshold_count=0,
        aggregate_supported_slice_coverage=0.91,
        av_anchor_availability_rate=0.81,
        ready_case_count=80,
        ready_sync_decision_lineage_valid_case_count=80,
        maximum_ready_absolute_sync_offset_ms=119.0,
        claim_policy_violation_count=0,
        forbidden_claim_fixture_violation_count=0,
    )
    return P8LockedSourceEvaluationResult.issue(
        source_manifest=source_manifest,
        metrics=metrics,
    )


def _state_directory(tmp_path: Path) -> Path:
    path = tmp_path / "p8-state"
    path.mkdir(mode=0o700)
    return path


def _sha(label: str) -> str:
    return sha256_document({"label": label})


def _resign(document: dict[str, object], digest_field: str) -> None:
    unsigned = {key: value for key, value in document.items() if key != digest_field}
    document[digest_field] = sha256_document(unsigned)


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _pinned_file_arguments(
    directory: Path,
) -> tuple[list[str], dict[str, bytes]]:
    pinned_files = {
        "decoder-code": b"decoder code\n",
        "tracker-code": b"tracker code\n",
        "tracker-adapter-code": b"tracker adapter code\n",
        "landmark-model": b"landmark model bytes\n",
        "normalizer-code": b"normalizer code\n",
        "cue-extractor-code": b"cue extractor code\n",
        "video-qc-measurement-code": b"video qc measurement code\n",
        "video-qc-decision-code": b"video qc decision code\n",
        "av-sync-measurement-code": b"av sync measurement code\n",
        "av-sync-decision-code": b"av sync decision code\n",
        "phone-window-code": b"phone window code\n",
    }
    media_capture_contract_id = "p8_test_media_capture_v1"
    visual_observation_contract_id = "p8_test_visible_lips_rgb_v1"
    population_id_sha256 = _sha("p8-test-population")
    context_contract_sha256 = _sha("p8-test-context-contract")
    calibration = VisualCalibrationArtifact.issue(
        artifact_id="p8_test_calibration_v1",
        revision="p8_test_calibrated_v1",
        scope=CalibrationScope.CALIBRATED,
        capture_contract_id=visual_observation_contract_id,
        population_id_sha256=population_id_sha256,
        rules=(
            CalibrationRule(
                cue_id="lip_aperture_ratio",
                measurement_unit="ratio",
                expected_phones=("ㅓ",),
                context_id="phone_center",
                reference_center=0.35,
                reference_scale=0.20,
                support_minimum=0.0,
                support_maximum=1.0,
                feedback_minimum_absolute_deviation=2.5,
            ),
        ),
    )
    claim = VisualClaim(
        action_id="reduce_visible_lip_opening",
        approved_claim_id="visible_lip_opening_above_reference_v1",
        renderer_key="visual_lip_opening_reduce",
        cue_id="lip_aperture_ratio",
        expected_phones=("ㅓ",),
        context_id="phone_center",
        direction=DeviationDirection.ABOVE_REFERENCE,
    )
    catalog = VisualClaimCatalog.issue(
        catalog_id="p8_test_claim_catalog_v1",
        revision="p8_test_visible_claims_v1",
        scope=CalibrationScope.CALIBRATED,
        claims=(claim,),
    )
    claim_policy_unsigned = {
        "schema_version": "voice_coach.visual_claim_policy.v1",
        "policy_revision": "p8_test_claim_policy_v1",
        "allowed_cue_ids": [claim.cue_id],
        "allowed_renderer_keys": [claim.renderer_key],
        "allowed_claim_bindings": [
            {
                "approved_claim_id": claim.approved_claim_id,
                "action_id": claim.action_id,
                "renderer_key": claim.renderer_key,
                "cue_id": claim.cue_id,
            }
        ],
        "forbidden_identifier_tokens": [
            "airflow",
            "diagnosis",
            "glottis",
            "medical",
            "tongue",
            "velum",
        ],
    }
    claim_policy = RuntimeVisualClaimPolicy.from_json_object(
        {
            **claim_policy_unsigned,
            "policy_sha256": sha256_document(claim_policy_unsigned),
        }
    )
    video_qc_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc",
        policy_revision="p8_test_video_qc_policy_v1",
        policy_document=VideoQcSemanticPolicy(
            underexposed_luma_max_8bit=32,
            overexposed_luma_min_8bit=224,
            lip_roi_padding_fraction=0.10,
            minimum_single_face_frames=3,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
        ),
    )
    av_sync_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync",
        policy_revision="p8_test_av_sync_policy_v1",
        policy_document=AvSyncSemanticPolicy(
            maximum_absolute_offset_ms=240,
            offset_step_ms=20,
            energy_window_ms=40,
            minimum_landmark_frames=3,
            maximum_anchor_count=32,
        ),
    )
    video_qc_decision_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc_decision",
        policy_revision="p8_test_video_qc_decision_policy_v1",
        policy_document=VideoQcDecisionSemanticPolicy(
            policy_revision="p8_test_video_qc_decision_policy_v1",
            expected_measurement_policy_sha256=video_qc_policy.policy_sha256,
            minimum_single_face_coverage=0.90,
            maximum_multiple_face_frame_fraction=0.0,
            minimum_mean_luma_8bit=20.0,
            maximum_mean_luma_8bit=230.0,
            maximum_underexposed_pixel_fraction=0.10,
            maximum_overexposed_pixel_fraction=0.10,
            minimum_lip_roi_mean_neighbor_luma_delta_8bit=1.0,
            minimum_track_transition_coverage=0.50,
            minimum_longest_single_face_run_fraction=0.80,
            maximum_lip_center_step_normalized=0.20,
        ),
    )
    av_sync_decision_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync_decision",
        policy_revision="p8_test_av_sync_decision_policy_v1",
        policy_document=AvSyncDecisionSemanticPolicy(
            policy_revision="p8_test_av_sync_decision_policy_v1",
            expected_measurement_policy_sha256=av_sync_policy.policy_sha256,
            maximum_absolute_offset_ms=240,
            minimum_correlation=0.80,
            minimum_anchor_count=1,
            minimum_anchor_strength=0.80,
        ),
    )
    phone_window_policy = P8RuntimePolicyArtifact.issue(
        policy_kind="phone_window",
        policy_revision="p8_test_phone_window_policy_v1",
        policy_document=PhoneWindowSemanticPolicy(
            policy_revision="p8_test_phone_window_policy_v1",
            expected_av_sync_policy_sha256=av_sync_policy.policy_sha256,
            context_contract_sha256=context_contract_sha256,
            maximum_absolute_offset_ms=240,
            padding_before_ms=100,
            padding_after_ms=100,
            minimum_frame_count=2,
            maximum_frame_count=12,
            maximum_window_duration_ms=1_000,
            maximum_rgb_bytes=80 * 1024 * 1024,
        ),
    )
    identity = VisualRuntimePipelineIdentity.issue(
        tracker_revision="p8_test_tracker_v1",
        tracker_sha256=hashlib.sha256(pinned_files["tracker-code"]).hexdigest(),
        tracker_adapter_revision="p8_test_tracker_adapter_v1",
        tracker_adapter_sha256=hashlib.sha256(
            pinned_files["tracker-adapter-code"]
        ).hexdigest(),
        decoder_revision="p8_test_decoder_v1",
        decoder_sha256=hashlib.sha256(pinned_files["decoder-code"]).hexdigest(),
        landmark_model_revision="p8_test_landmark_model_v1",
        landmark_model_sha256=hashlib.sha256(
            pinned_files["landmark-model"]
        ).hexdigest(),
        normalizer_revision="p8_test_normalizer_v1",
        normalizer_sha256=hashlib.sha256(
            pinned_files["normalizer-code"]
        ).hexdigest(),
        cue_extractor_revision="p8_test_cue_extractor_v1",
        cue_extractor_sha256=hashlib.sha256(
            pinned_files["cue-extractor-code"]
        ).hexdigest(),
        video_qc_measurement_revision="p8_test_video_qc_measurement_v1",
        video_qc_measurement_policy_sha256=video_qc_policy.policy_sha256,
        video_qc_measurement_code_sha256=hashlib.sha256(
            pinned_files["video-qc-measurement-code"]
        ).hexdigest(),
        video_qc_decision_policy_revision=(
            video_qc_decision_policy.policy_revision
        ),
        video_qc_decision_policy_sha256=video_qc_decision_policy.policy_sha256,
        video_qc_decision_code_sha256=hashlib.sha256(
            pinned_files["video-qc-decision-code"]
        ).hexdigest(),
        av_sync_measurement_revision="p8_test_av_sync_measurement_v1",
        av_sync_measurement_policy_sha256=av_sync_policy.policy_sha256,
        av_sync_measurement_code_sha256=hashlib.sha256(
            pinned_files["av-sync-measurement-code"]
        ).hexdigest(),
        av_sync_decision_policy_revision=(
            av_sync_decision_policy.policy_revision
        ),
        av_sync_decision_policy_sha256=av_sync_decision_policy.policy_sha256,
        av_sync_decision_code_sha256=hashlib.sha256(
            pinned_files["av-sync-decision-code"]
        ).hexdigest(),
        phone_window_code_revision="p8_test_phone_window_code_v1",
        phone_window_code_sha256=hashlib.sha256(
            pinned_files["phone-window-code"]
        ).hexdigest(),
        phone_window_policy_revision=phone_window_policy.policy_revision,
        phone_window_policy_sha256=phone_window_policy.policy_sha256,
        media_capture_contract_id=media_capture_contract_id,
        visual_observation_contract_id=visual_observation_contract_id,
        population_id_sha256=population_id_sha256,
        context_contract_sha256=context_contract_sha256,
    )
    p6_receipt = AutomatedP6PromotionReceipt._issue_from_validated_evidence(
        source_revision="p8_test_p6_source_v1",
        dataset_manifest_sha256=_sha("p8-test-p6-dataset-manifest"),
        geometry_bundle_sha256=_sha("p8-test-p6-geometry-bundle"),
        phone_reference_bundle_sha256=_sha("p8-test-p6b-bundle"),
        phone_reference_reload_receipt_sha256=_sha("p8-test-p6b-reload"),
        phone_reference_producer_sha256=_sha("p8-test-p6b-producer"),
        runtime_parity_receipt_sha256=_sha("p8-test-p6b-parity"),
        geometry_artifact_sha256=_sha("p8-test-p6-geometry-artifact"),
        geometry_reload_receipt_sha256=_sha("p8-test-p6-geometry-reload"),
        development_partition_sha256=_sha("p8-test-p6-development"),
        calibration_partition_sha256=_sha("p8-test-p6-calibration"),
        mapping_sha256=_sha("p8-test-p6-mapping"),
        threshold_protocol_sha256=_sha("p8-test-p6-thresholds"),
        calibration_artifact_sha256=calibration.artifact_sha256,
        catalog_sha256=catalog.catalog_sha256,
        policy_sha256=claim_policy.policy_sha256,
        decision_policy_evidence_receipt_sha256=_sha(
            "p8-test-decision-policy-evidence"
        ),
        pipeline_identity=identity,
    )
    typed_documents = {
        "p6-evidence-receipt": p6_receipt.to_json_object(),
        "calibration-artifact": calibration.to_json_object(),
        "claim-catalog": catalog.to_json_object(),
        "claim-policy": claim_policy.to_json_object(),
        "pipeline-identity": identity.to_json_object(),
        "video-qc-measurement-policy": video_qc_policy.to_json_object(),
        "video-qc-decision-policy": video_qc_decision_policy.to_json_object(),
        "av-sync-measurement-policy": av_sync_policy.to_json_object(),
        "av-sync-decision-policy": av_sync_decision_policy.to_json_object(),
        "phone-window-policy": phone_window_policy.to_json_object(),
    }
    arguments: list[str] = []
    for option_name, document in typed_documents.items():
        pinned_path = directory / f"{option_name}.json"
        pinned_path.write_bytes(canonical_json_bytes(document) + b"\n")
        arguments.extend((f"--{option_name}-file", str(pinned_path)))
    for option_name, payload in pinned_files.items():
        pinned_path = directory / f"{option_name}.bin"
        pinned_path.write_bytes(payload)
        arguments.extend((f"--{option_name}-file", str(pinned_path)))
    return arguments, pinned_files


def _argument_path(arguments: list[str], option: str) -> Path:
    return Path(arguments[arguments.index(option) + 1])
