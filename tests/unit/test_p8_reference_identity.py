"""Fixture-only tests for the P8 actual-source identity/state boundary."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import hmac
import json
import os
from pathlib import Path
import pickle

import pytest

from voice_coach.visual_feedback.adapters.p8_reference_identity import (
    P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
    P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION,
    P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
    P8ReferenceIdentityError,
    P8SealedReferenceBundle,
    canonical_restricted_identity_set_root_sha256,
    load_p8_sealed_reference_bundle,
    prepare_p8_actual_source_read_zero_seal,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p8_actual_source_state import (
    P8ActualSourceCleanupState,
    P8ActualSourceCleanupStatus,
    P8ActualSourceFinalResultCommit,
    P8ActualSourceFreshProcessReloadReceipt,
    P8ActualSourceGatePending,
    P8ActualSourceReadZeroSeal,
    P8ActualSourceResultStatus,
    P8CanonicalRestrictedIdentityCommitment,
    P8ReferenceIdentityRole,
)
from voice_coach.visual_feedback.models.p8_locked_evaluation import (
    P8ReadZeroSeal,
)


_SEALED_AT = "2026-08-28T01:00:00Z"
_PENDING_AT = "2026-08-28T01:01:00Z"
_CLEANED_AT = "2026-08-28T01:02:00Z"
_RELOADED_AT = "2026-08-28T01:03:00Z"
_COMMITTED_AT = "2026-08-28T01:04:00Z"
_IDENTITY_KEY = hashlib.sha256(b"fixture-only-p8-reference-key").digest()


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _namespace(key: bytes = _IDENTITY_KEY) -> str:
    return hmac.new(
        key,
        b"voice_coach.aihub538.identity_namespace.v1",
        hashlib.sha256,
    ).hexdigest()


def _base_seal(*, p6_receipt_sha256: str) -> P8ReadZeroSeal:
    media_contract = "p8_reference_fixture_media_v1"
    observation_contract = "p8_reference_fixture_observation_v1"
    return P8ReadZeroSeal.issue(
        seal_id="p8_reference_fixture_seal_001",
        protocol_revision="p8_reference_fixture_protocol_v1",
        source_revision="aihub_538_reference_fixture_v1",
        p6_evidence_receipt_sha256=p6_receipt_sha256,
        calibration_artifact_sha256=_sha("calibration"),
        claim_catalog_sha256=_sha("catalog"),
        claim_policy_sha256=_sha("claim-policy"),
        media_capture_contract_id=media_contract,
        media_capture_contract_sha256=sha256_document(
            {
                "schema_version": (
                    "voice_coach.media_capture_contract_reference.v1"
                ),
                "contract_id": media_contract,
            }
        ),
        visual_observation_contract_id=observation_contract,
        visual_observation_contract_sha256=sha256_document(
            {
                "schema_version": (
                    "voice_coach.visual_observation_contract_reference.v1"
                ),
                "contract_id": observation_contract,
            }
        ),
        decoder_code_sha256=_sha("decoder"),
        tracker_code_sha256=_sha("tracker"),
        tracker_adapter_code_sha256=_sha("tracker-adapter"),
        landmark_model_sha256=_sha("landmark-model"),
        normalizer_code_sha256=_sha("normalizer"),
        cue_extractor_code_sha256=_sha("cue-extractor"),
        video_qc_measurement_code_sha256=_sha("qc-measurement-code"),
        video_qc_measurement_policy_sha256=_sha("qc-measurement-policy"),
        video_qc_decision_code_sha256=_sha("qc-decision-code"),
        video_qc_decision_policy_revision="p8_fixture_qc_decision_v1",
        video_qc_decision_policy_sha256=_sha("qc-decision-policy"),
        av_sync_measurement_code_sha256=_sha("sync-measurement-code"),
        av_sync_measurement_policy_sha256=_sha("sync-measurement-policy"),
        av_sync_decision_code_sha256=_sha("sync-decision-code"),
        av_sync_decision_policy_revision="p8_fixture_sync_decision_v1",
        av_sync_decision_policy_sha256=_sha("sync-decision-policy"),
        phone_window_code_sha256=_sha("phone-window-code"),
        phone_window_policy_revision="p8_fixture_phone_window_v1",
        phone_window_policy_sha256=_sha("phone-window-policy"),
        evaluation_pipeline_code_sha256=_sha("evaluation-pipeline-code"),
        source_evaluator_code_sha256=_sha("source-evaluator-code"),
        media_engine_code_sha256=_sha("media-engine-code"),
        sealed_at_utc=_SEALED_AT,
    )


def _manifest_document(
    *,
    role: str,
    strict_admission_schema_version: str,
    strict_admission_receipt_sha256: str,
    strict_admission_manifest_sha256: str,
    actor_hmac_sha256: tuple[str, ...],
    attempt_hmac_sha256: tuple[str, ...],
    namespace_sha256: str = _namespace(),
) -> dict[str, object]:
    actors = tuple(sorted(actor_hmac_sha256))
    attempts = tuple(sorted(attempt_hmac_sha256))
    unsigned: dict[str, object] = {
        "schema_version": (
            P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION
        ),
        "role": role,
        "strict_admission_schema_version": strict_admission_schema_version,
        "strict_admission_receipt_sha256": (
            strict_admission_receipt_sha256
        ),
        "strict_admission_manifest_sha256": (
            strict_admission_manifest_sha256
        ),
        "identity_namespace_commitment_sha256": namespace_sha256,
        "actor_hmac_count": len(actors),
        "attempt_hmac_count": len(attempts),
        "actor_hmac_sha256": list(actors),
        "attempt_hmac_sha256": list(attempts),
        "identity_set_root_sha256": (
            canonical_restricted_identity_set_root_sha256(
                identity_namespace_commitment_sha256=namespace_sha256,
                actor_hmac_sha256=actors,
                attempt_hmac_sha256=attempts,
            )
        ),
        "individual_raw_identifiers_serialized": False,
        "source_paths_serialized": False,
        "transcripts_serialized": False,
        "restricted_private_artifact": True,
    }
    return {
        **unsigned,
        "restricted_manifest_sha256": sha256_document(unsigned),
    }


def _resign_manifest(document: dict[str, object]) -> None:
    unsigned = dict(document)
    unsigned.pop("restricted_manifest_sha256", None)
    document["restricted_manifest_sha256"] = sha256_document(unsigned)


def _write_private_json(path: Path, document: object) -> None:
    if path.exists() or path.is_symlink():
        path.chmod(0o600, follow_symlinks=False)
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o400)


def _write_private_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o400)


def _fixture(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    P8ReadZeroSeal,
    P8ActualSourceReadZeroSeal,
    dict[str, object],
    dict[str, object],
]:
    restricted_directory = tmp_path / "restricted-reference-fixture"
    restricted_directory.mkdir(mode=0o700, parents=True)
    restricted_directory.chmod(0o700)
    key_path = tmp_path / "fixture.key"
    _write_private_bytes(key_path, _IDENTITY_KEY)
    p6_receipt = _sha("p6-strict-admission-receipt")
    base_seal = _base_seal(p6_receipt_sha256=p6_receipt)
    p6_document = _manifest_document(
        role="p6_calibration",
        strict_admission_schema_version="voice_coach.p6_strict_admission.v1",
        strict_admission_receipt_sha256=p6_receipt,
        strict_admission_manifest_sha256=_sha("p6-admission-manifest"),
        actor_hmac_sha256=(_sha("p6-actor-1"), _sha("p6-actor-2")),
        attempt_hmac_sha256=(_sha("p6-attempt-1"), _sha("p6-attempt-2")),
    )
    ts_document = _manifest_document(
        role="ts174_training",
        strict_admission_schema_version=(
            "voice_coach.aihub538_training_admission_receipt.v2"
        ),
        strict_admission_receipt_sha256=_sha("ts174-admission-receipt"),
        strict_admission_manifest_sha256=_sha("ts174-admission-manifest"),
        actor_hmac_sha256=(_sha("ts-actor-1"), _sha("ts-actor-2")),
        attempt_hmac_sha256=(_sha("ts-attempt-1"), _sha("ts-attempt-2")),
    )
    _write_private_json(
        restricted_directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
        p6_document,
    )
    _write_private_json(
        restricted_directory / P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
        ts_document,
    )
    with ExitStack() as stack:
        directory_descriptor = os.open(
            restricted_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        stack.callback(os.close, directory_descriptor)
        key_descriptor = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        actual_source_seal = prepare_p8_actual_source_read_zero_seal(
            restricted_state_directory_descriptor=directory_descriptor,
            identity_key_descriptor=key_descriptor,
            base_read_zero_seal=base_seal,
            fresh_reload_worker_code_sha256=_sha("fresh-worker"),
            sealed_at_utc=_SEALED_AT,
        )
    return (
        restricted_directory,
        key_path,
        base_seal,
        actual_source_seal,
        p6_document,
        ts_document,
    )


def _load_bundle(
    *,
    restricted_directory: Path,
    key_path: Path,
    base_seal: P8ReadZeroSeal,
    actual_source_seal: P8ActualSourceReadZeroSeal,
) -> P8SealedReferenceBundle:
    with ExitStack() as stack:
        directory_descriptor = os.open(
            restricted_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        stack.callback(os.close, directory_descriptor)
        key_descriptor = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        return load_p8_sealed_reference_bundle(
            restricted_state_directory_descriptor=directory_descriptor,
            identity_key_descriptor=key_descriptor,
            base_read_zero_seal=base_seal,
            actual_source_seal=actual_source_seal,
        )


def test_prepare_and_load_issue_only_exact_sealed_pair(tmp_path: Path) -> None:
    directory, key_path, base, actual, p6_document, ts_document = _fixture(
        tmp_path
    )

    bundle = _load_bundle(
        restricted_directory=directory,
        key_path=key_path,
        base_seal=base,
        actual_source_seal=actual,
    )

    populations = bundle._populations_for_locked_evaluation()
    assert tuple(item.role for item in populations) == (
        "p6_calibration",
        "ts174_training",
    )
    assert tuple(item.manifest_sha256 for item in populations) == (
        p6_document["strict_admission_manifest_sha256"],
        ts_document["strict_admission_manifest_sha256"],
    )
    assert bundle.actual_source_seal_sha256 == actual.actual_source_seal_sha256
    assert bundle._identity_key_for_locked_evaluation().key == _IDENTITY_KEY
    assert "p6-actor" not in repr(bundle)
    assert repr(bundle) == "P8SealedReferenceBundle(<restricted>)"
    with pytest.raises(TypeError, match="serialization_forbidden"):
        pickle.dumps(bundle)


def test_manifest_set_substitution_is_rejected_by_companion_root(
    tmp_path: Path,
) -> None:
    directory, key_path, base, actual, p6_document, _ = _fixture(tmp_path)
    p6_path = directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME
    actors = list(p6_document["actor_hmac_sha256"])
    actors[0] = _sha("substituted-private-actor")
    p6_document["actor_hmac_sha256"] = sorted(actors)
    p6_document["identity_set_root_sha256"] = (
        canonical_restricted_identity_set_root_sha256(
            identity_namespace_commitment_sha256=(
                p6_document["identity_namespace_commitment_sha256"]
            ),
            actor_hmac_sha256=p6_document["actor_hmac_sha256"],
            attempt_hmac_sha256=p6_document["attempt_hmac_sha256"],
        )
    )
    _resign_manifest(p6_document)
    _write_private_json(p6_path, p6_document)

    with pytest.raises(
        P8ReferenceIdentityError, match="manifest_seal_binding_invalid"
    ):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )


@pytest.mark.parametrize(
    "fault",
    ("unsorted", "duplicate", "count", "root", "namespace", "role"),
)
def test_manifest_canonical_faults_fail_closed(
    tmp_path: Path, fault: str
) -> None:
    directory, key_path, base, actual, p6_document, _ = _fixture(tmp_path)
    p6_path = directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME
    actors = list(p6_document["actor_hmac_sha256"])
    if fault == "unsorted":
        p6_document["actor_hmac_sha256"] = list(reversed(actors))
    elif fault == "duplicate":
        p6_document["actor_hmac_sha256"] = [actors[0], actors[0]]
    elif fault == "count":
        p6_document["actor_hmac_count"] = len(actors) + 1
    elif fault == "root":
        p6_document["identity_set_root_sha256"] = _sha("wrong-root")
    elif fault == "namespace":
        p6_document["identity_namespace_commitment_sha256"] = _sha(
            "wrong-namespace"
        )
    elif fault == "role":
        p6_document["role"] = "ts174_training"
    else:  # pragma: no cover - parameter exhaustiveness
        raise AssertionError
    _resign_manifest(p6_document)
    _write_private_json(p6_path, p6_document)

    with pytest.raises(P8ReferenceIdentityError):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )


def test_p6_receipt_and_base_seal_cross_pin_is_required(tmp_path: Path) -> None:
    directory, key_path, base, actual, p6_document, _ = _fixture(tmp_path)
    p6_document["strict_admission_receipt_sha256"] = _sha(
        "different-p6-receipt"
    )
    _resign_manifest(p6_document)
    _write_private_json(
        directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
        p6_document,
    )

    with pytest.raises(P8ReferenceIdentityError):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )

    other_base = _base_seal(p6_receipt_sha256=_sha("other-base-receipt"))
    with pytest.raises(
        P8ReferenceIdentityError, match="base_seal_binding_invalid"
    ):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=other_base,
            actual_source_seal=actual,
        )


def test_duplicate_json_key_and_extra_key_are_rejected(tmp_path: Path) -> None:
    directory, key_path, base, actual, p6_document, _ = _fixture(tmp_path)
    p6_path = directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME
    raw = json.dumps(p6_document, separators=(",", ":"))
    raw = raw.replace(
        '"role":"p6_calibration"',
        '"role":"p6_calibration","role":"p6_calibration"',
        1,
    )
    p6_path.chmod(0o600)
    p6_path.write_text(raw, encoding="utf-8")
    p6_path.chmod(0o400)
    with pytest.raises(P8ReferenceIdentityError, match="json_invalid"):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )

    directory, key_path, base, actual, p6_document, _ = _fixture(
        tmp_path / "extra"
    )
    p6_document["unexpected"] = False
    _resign_manifest(p6_document)
    _write_private_json(
        directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
        p6_document,
    )
    with pytest.raises(P8ReferenceIdentityError, match="manifest_invalid"):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )


def test_private_modes_and_nofollow_are_enforced(tmp_path: Path) -> None:
    directory, key_path, base, actual, _, _ = _fixture(tmp_path)
    directory.chmod(0o750)
    with pytest.raises(P8ReferenceIdentityError, match="directory_descriptor"):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )

    directory.chmod(0o700)
    p6_path = directory / P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME
    p6_path.chmod(0o600)
    with pytest.raises(P8ReferenceIdentityError, match="descriptor_invalid"):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )

    p6_path.chmod(0o400)
    target = tmp_path / "private-target.json"
    target.write_bytes(p6_path.read_bytes())
    target.chmod(0o400)
    p6_path.unlink()
    p6_path.symlink_to(target)
    with pytest.raises(P8ReferenceIdentityError, match="open_failed"):
        _load_bundle(
            restricted_directory=directory,
            key_path=key_path,
            base_seal=base,
            actual_source_seal=actual,
        )


def test_actual_source_state_round_trips_and_pins_terminal_cleanup() -> None:
    p6_commitment = P8CanonicalRestrictedIdentityCommitment.issue(
        role=P8ReferenceIdentityRole.P6_CALIBRATION,
        strict_admission_schema_version="voice_coach.p6_strict_admission.v1",
        strict_admission_receipt_sha256=_sha("p6-receipt"),
        strict_admission_manifest_sha256=_sha("p6-manifest"),
        identity_namespace_commitment_sha256=_namespace(),
        identity_set_root_sha256=_sha("p6-root"),
        actor_hmac_count=2,
        attempt_hmac_count=3,
    )
    ts_commitment = P8CanonicalRestrictedIdentityCommitment.issue(
        role=P8ReferenceIdentityRole.TS174_TRAINING,
        strict_admission_schema_version=(
            "voice_coach.aihub538_training_admission_receipt.v2"
        ),
        strict_admission_receipt_sha256=_sha("ts-receipt"),
        strict_admission_manifest_sha256=_sha("ts-manifest"),
        identity_namespace_commitment_sha256=_namespace(),
        identity_set_root_sha256=_sha("ts-root"),
        actor_hmac_count=4,
        attempt_hmac_count=5,
    )
    seal = P8ActualSourceReadZeroSeal.issue(
        base_read_zero_seal_sha256=_sha("base-seal"),
        p6_evidence_receipt_sha256=_sha("p6-receipt"),
        reference_commitments=(p6_commitment, ts_commitment),
        fresh_reload_worker_code_sha256=_sha("worker"),
        sealed_at_utc=_SEALED_AT,
    )
    gate = P8ActualSourceGatePending.issue(
        actual_source_seal=seal,
        access_transaction_sha256=_sha("transaction"),
        report_id="p8_fixture_report_001",
        frozen_pipeline_pins_sha256=_sha("pipeline-pins"),
        pending_at_utc=_PENDING_AT,
    )
    cleanup = P8ActualSourceCleanupState.issue_completed(
        gate_pending=gate,
        removed_regular_file_count=7,
        removed_directory_count=2,
        workspace_cleanup_receipt_sha256=_sha("workspace-cleanup"),
        recorded_at_utc=_CLEANED_AT,
    )
    reload_receipt = P8ActualSourceFreshProcessReloadReceipt.issue(
        actual_source_seal=seal,
        gate_pending=gate,
        cleanup_state=cleanup,
        canonical_report_sha256=_sha("report"),
        locked_source_manifest_sha256=_sha("locked-manifest"),
        aggregate_metrics_sha256=_sha("metrics"),
        aggregate_lineage_sha256=_sha("lineage"),
        result_status=P8ActualSourceResultStatus.COMPLETED,
        verified_at_utc=_RELOADED_AT,
    )
    commit = P8ActualSourceFinalResultCommit.issue(
        fresh_reload_receipt=reload_receipt,
        source_terminal_sha256=_sha("source-terminal"),
        committed_at_utc=_COMMITTED_AT,
    )

    for value, model in (
        (p6_commitment, P8CanonicalRestrictedIdentityCommitment),
        (seal, P8ActualSourceReadZeroSeal),
        (gate, P8ActualSourceGatePending),
        (cleanup, P8ActualSourceCleanupState),
        (reload_receipt, P8ActualSourceFreshProcessReloadReceipt),
        (commit, P8ActualSourceFinalResultCommit),
    ):
        assert model.from_json_object(value.to_json_object()) == value
    assert commit.source_terminal_sha256 == _sha("source-terminal")
    assert cleanup.workspace_cleanup_receipt_sha256 == _sha(
        "workspace-cleanup"
    )
    assert commit.promotion_authorized is False

@pytest.mark.parametrize("work_absent", (False, True))
def test_incomplete_cleanup_is_sticky_failed_closed(
    tmp_path: Path, work_absent: bool
) -> None:
    directory, key_path, base, seal, _, _ = _fixture(tmp_path)
    _load_bundle(
        restricted_directory=directory,
        key_path=key_path,
        base_seal=base,
        actual_source_seal=seal,
    )
    gate = P8ActualSourceGatePending.issue(
        actual_source_seal=seal,
        access_transaction_sha256=_sha("transaction"),
        report_id="p8_fixture_failed_report_001",
        frozen_pipeline_pins_sha256=_sha("pipeline-pins"),
        pending_at_utc=_PENDING_AT,
    )
    cleanup = P8ActualSourceCleanupState.issue_incomplete(
        gate_pending=gate,
        restricted_work_directory_absent=work_absent,
        recorded_at_utc=_CLEANED_AT,
    )
    reload_receipt = P8ActualSourceFreshProcessReloadReceipt.issue(
        actual_source_seal=seal,
        gate_pending=gate,
        cleanup_state=cleanup,
        canonical_report_sha256=_sha("failed-report"),
        locked_source_manifest_sha256=None,
        aggregate_metrics_sha256=None,
        aggregate_lineage_sha256=None,
        result_status=P8ActualSourceResultStatus.FAILED_CLOSED,
        verified_at_utc=_RELOADED_AT,
    )
    commit = P8ActualSourceFinalResultCommit.issue(
        fresh_reload_receipt=reload_receipt,
        source_terminal_sha256=_sha("failed-terminal"),
        committed_at_utc=_COMMITTED_AT,
    )

    assert cleanup.workspace_cleanup_receipt_sha256 is None
    assert cleanup.removed_regular_file_count is None
    assert cleanup.restricted_bytes_may_remain is not work_absent
    assert commit.result_status is P8ActualSourceResultStatus.FAILED_CLOSED
    assert commit.promotion_authorized is False
    with pytest.raises(VisualFeedbackContractError):
        P8ActualSourceFreshProcessReloadReceipt.issue(
            actual_source_seal=seal,
            gate_pending=gate,
            cleanup_state=cleanup,
            canonical_report_sha256=_sha("invalid-completed-report"),
            locked_source_manifest_sha256=_sha("locked-manifest"),
            aggregate_metrics_sha256=_sha("metrics"),
            aggregate_lineage_sha256=_sha("lineage"),
            result_status=P8ActualSourceResultStatus.COMPLETED,
            verified_at_utc=_RELOADED_AT,
        )


def test_state_parsers_reject_extra_and_resigned_cross_field_lies(
    tmp_path: Path,
) -> None:
    _, _, _, seal, _, _ = _fixture(tmp_path)
    seal_document = seal.to_json_object()
    seal_document["unexpected"] = False
    with pytest.raises(VisualFeedbackContractError, match="fields differ"):
        P8ActualSourceReadZeroSeal.from_json_object(seal_document)

    gate = P8ActualSourceGatePending.issue(
        actual_source_seal=seal,
        access_transaction_sha256=_sha("transaction"),
        report_id="p8_fixture_report_002",
        frozen_pipeline_pins_sha256=_sha("pins"),
        pending_at_utc=_PENDING_AT,
    )
    cleanup = P8ActualSourceCleanupState.issue_completed(
        gate_pending=gate,
        removed_regular_file_count=1,
        removed_directory_count=1,
        workspace_cleanup_receipt_sha256=_sha("cleanup-receipt"),
        recorded_at_utc=_CLEANED_AT,
    )
    cleanup_document = cleanup.to_json_object()
    cleanup_document["workspace_cleanup_receipt_sha256"] = None
    unsigned = dict(cleanup_document)
    unsigned.pop("cleanup_state_sha256")
    cleanup_document["cleanup_state_sha256"] = sha256_document(unsigned)
    with pytest.raises(
        VisualFeedbackContractError, match="completed cleanup invariants"
    ):
        P8ActualSourceCleanupState.from_json_object(cleanup_document)


def test_public_state_and_bundle_do_not_serialize_private_identity_values(
    tmp_path: Path,
) -> None:
    directory, key_path, base, actual, p6_document, ts_document = _fixture(
        tmp_path
    )
    bundle = _load_bundle(
        restricted_directory=directory,
        key_path=key_path,
        base_seal=base,
        actual_source_seal=actual,
    )
    public_text = json.dumps(actual.to_json_object(), sort_keys=True)
    private_values = (
        list(p6_document["actor_hmac_sha256"])
        + list(p6_document["attempt_hmac_sha256"])
        + list(ts_document["actor_hmac_sha256"])
        + list(ts_document["attempt_hmac_sha256"])
    )

    assert all(value not in public_text for value in private_values)
    assert str(tmp_path) not in public_text
    assert all(value not in repr(bundle) for value in private_values)
    assert not hasattr(bundle, "to_json_object")
