from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading

import pytest

from voice_coach.visual_feedback.adapters import p8_locked_label_download as P8_ADAPTER
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    Aihub538ArchiveError,
    verify_aihub538_locked_download,
    verify_aihub538_p8_label_download,
)
from voice_coach.visual_feedback.adapters.p8_locked_label_download import (
    P8LockedLabelDownloadAdapterError,
    load_p8_locked_cohort_discovery_seal_descriptor,
    lock_p8_locked_label_state_directory,
    open_p8_locked_label_state_directory,
    publish_p8_locked_label_download_completion,
    require_p8_locked_label_transaction_state_binding,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import (
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p8_actual_source_state import (
    P8ActualSourceReadZeroSeal,
    P8CanonicalRestrictedIdentityCommitment,
    P8ReferenceIdentityRole,
)
from voice_coach.visual_feedback.models.p8_locked_cohort import (
    P8_LOCKED_COHORT_DISCOVERY_CONTRACT,
    P8LockedCohortDiscoverySeal,
)
from voice_coach.visual_feedback.models.p8_locked_evaluation import (
    P8ReadZeroSeal,
)
from voice_coach.visual_feedback.models.p8_locked_label_download import (
    AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS,
    AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256,
    AIHUB538_P8_VALIDATION_PAIRS,
    AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS,
    P8LockedLabelDownloadCompletion,
    P8LockedLabelDownloadError,
    P8LockedLabelDownloadTransaction,
    issue_p8_locked_label_download_completion,
    issue_p8_locked_label_download_transaction,
    p8_locked_reference_commitments_root_sha256,
)


ROOT = Path(__file__).resolve().parents[2]
SECURE_SCRIPT = ROOT / "scripts/download_aihub_secure.py"
WRAPPER_SCRIPT = ROOT / "scripts/download_aihub538_p8_validation_labels.py"
SEAL_SCRIPT = ROOT / "scripts/seal_aihub538_p8_label_discovery.py"


def _load_script(name: str, path: Path):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SECURE = _load_script("p8_label_secure_download_fixture", SECURE_SCRIPT)
WRAPPER = _load_script("p8_label_download_wrapper_fixture", WRAPPER_SCRIPT)
SEAL = _load_script("p8_label_discovery_seal_fixture", SEAL_SCRIPT)

_REVIEWED_P8_VL_FILE_KEYS = (
    "55080", "55091", "55102", "55106", "55107", "55108", "55109", "55110",
    "55111", "55081", "55082", "55083", "55084", "55085", "55086", "55087",
    "55088", "55089", "55090", "55092", "55093", "55094", "55095", "55096",
    "55097", "55098", "55099", "55100", "55101", "55103", "55104", "55105",
)
_REVIEWED_P8_VS_FILE_KEYS = (
    "55112", "55123", "55134", "55138", "55139", "55140", "55141", "55142",
    "55143", "55113", "55114", "55115", "55116", "55117", "55118", "55119",
    "55120", "55121", "55122", "55124", "55125", "55126", "55127", "55128",
    "55129", "55130", "55131", "55132", "55133", "55135", "55136", "55137",
)
_REVIEWED_P8_REQUEST_SHA256 = (
    "c1298d035eaee65bd15e1c552561141133418b3b4c0fc8351eb7a0c96090fa9e"
)
_REVIEWED_DISCOVERY_CONTRACT_SHA256 = (
    "db2bef2d6e2720f3002b3e47da4892a86daf42a1a62510bae63e33b7bed89db7"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _base_seal() -> P8ReadZeroSeal:
    return P8ReadZeroSeal.issue(
        seal_id="p8_label_download_base_001",
        protocol_revision="p8_label_download_protocol_v1",
        source_revision="aihub_538_label_download_v1",
        p6_evidence_receipt_sha256=_sha("p6-receipt"),
        calibration_artifact_sha256=_sha("calibration"),
        claim_catalog_sha256=_sha("catalog"),
        claim_policy_sha256=_sha("policy"),
        media_capture_contract_id="p8_label_media_v1",
        media_capture_contract_sha256=sha256_document(
            {
                "schema_version": (
                    "voice_coach.media_capture_contract_reference.v1"
                ),
                "contract_id": "p8_label_media_v1",
            }
        ),
        visual_observation_contract_id="p8_label_observation_v1",
        visual_observation_contract_sha256=sha256_document(
            {
                "schema_version": (
                    "voice_coach.visual_observation_contract_reference.v1"
                ),
                "contract_id": "p8_label_observation_v1",
            }
        ),
        decoder_code_sha256=_sha("decoder"),
        tracker_code_sha256=_sha("tracker"),
        tracker_adapter_code_sha256=_sha("tracker-adapter"),
        landmark_model_sha256=_sha("landmark-model"),
        normalizer_code_sha256=_sha("normalizer"),
        cue_extractor_code_sha256=_sha("cue-extractor"),
        video_qc_measurement_code_sha256=_sha("qc-measurement"),
        video_qc_measurement_policy_sha256=_sha("qc-measurement-policy"),
        video_qc_decision_code_sha256=_sha("qc-decision"),
        video_qc_decision_policy_revision="p8_label_qc_v1",
        video_qc_decision_policy_sha256=_sha("qc-decision-policy"),
        av_sync_measurement_code_sha256=_sha("sync-measurement"),
        av_sync_measurement_policy_sha256=_sha("sync-measurement-policy"),
        av_sync_decision_code_sha256=_sha("sync-decision"),
        av_sync_decision_policy_revision="p8_label_sync_v1",
        av_sync_decision_policy_sha256=_sha("sync-decision-policy"),
        phone_window_code_sha256=_sha("phone-window"),
        phone_window_policy_revision="p8_label_phone_window_v1",
        phone_window_policy_sha256=_sha("phone-window-policy"),
        evaluation_pipeline_code_sha256=_sha("evaluation-pipeline"),
        source_evaluator_code_sha256=_sha("source-evaluator"),
        media_engine_code_sha256=_sha("media-engine"),
        sealed_at_utc="2026-08-29T00:00:00Z",
    )


def _actual_source_seal(base: P8ReadZeroSeal) -> P8ActualSourceReadZeroSeal:
    namespace = _sha("namespace")
    p6 = P8CanonicalRestrictedIdentityCommitment.issue(
        role=P8ReferenceIdentityRole.P6_CALIBRATION,
        strict_admission_schema_version="voice_coach.p6_admission.v1",
        strict_admission_receipt_sha256=base.p6_evidence_receipt_sha256,
        strict_admission_manifest_sha256=_sha("p6-manifest"),
        identity_namespace_commitment_sha256=namespace,
        identity_set_root_sha256=_sha("p6-root"),
        actor_hmac_count=2,
        attempt_hmac_count=3,
    )
    training = P8CanonicalRestrictedIdentityCommitment.issue(
        role=P8ReferenceIdentityRole.TS174_TRAINING,
        strict_admission_schema_version="voice_coach.ts174_admission.v2",
        strict_admission_receipt_sha256=_sha("training-receipt"),
        strict_admission_manifest_sha256=_sha("training-manifest"),
        identity_namespace_commitment_sha256=namespace,
        identity_set_root_sha256=_sha("training-root"),
        actor_hmac_count=4,
        attempt_hmac_count=5,
    )
    return P8ActualSourceReadZeroSeal.issue(
        base_read_zero_seal_sha256=base.seal_sha256,
        p6_evidence_receipt_sha256=base.p6_evidence_receipt_sha256,
        reference_commitments=(p6, training),
        fresh_reload_worker_code_sha256=_sha("reload-worker"),
        sealed_at_utc="2026-08-29T00:00:01Z",
    )


def _discovery_seal(
    actual: P8ActualSourceReadZeroSeal,
    state: Path,
) -> P8LockedCohortDiscoverySeal:
    state_metadata = state.stat()
    return P8LockedCohortDiscoverySeal.issue(
        seal_id="p8_label_discovery_001",
        protocol_revision="p8_locked_cohort_discovery_v1",
        reference_identity_set_commitments_sha256=(
            p8_locked_reference_commitments_root_sha256(actual)
        ),
        state_directory_device=state_metadata.st_dev,
        state_directory_inode=state_metadata.st_ino,
        sealed_at_utc="2026-08-29T00:00:02Z",
    )


def _private_directory(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def _state_fixture(
    tmp_path: Path, *, persist_discovery: bool = True
):  # noqa: ANN202
    state = _private_directory(tmp_path / "state")
    output = _private_directory(tmp_path / "output")
    base = _base_seal()
    actual = _actual_source_seal(base)
    discovery = _discovery_seal(actual, state)
    store = P8SealStateStore(state)
    store.create_read_zero_seal(base)
    store.create_actual_source_read_zero_seal(actual)
    if persist_discovery:
        store.create_locked_cohort_discovery_seal(discovery)
    return state, output, store, actual, discovery


def _transaction(
    output: Path,
    actual: P8ActualSourceReadZeroSeal,
    discovery: P8LockedCohortDiscoverySeal,
) -> P8LockedLabelDownloadTransaction:
    metadata = output.stat()
    state_root = P8SealStateStore(
        output.parent / "state"
    ).load_locked_label_state_root_seal()
    return issue_p8_locked_label_download_transaction(
        discovery_seal=discovery,
        actual_source_seal=actual,
        state_root_seal=state_root,
        output_directory_device=metadata.st_dev,
        output_directory_inode=metadata.st_ino,
        authorized_at_utc="2026-08-29T00:00:03Z",
        dataset_access_permission_confirmed=True,
    )


def _write_secure_receipt(output: Path, payload: bytes) -> None:
    receipt = output / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    "voice_coach.aihub_secure_download_receipt.v1"
                ),
                "dataset_key": "538",
                "file_keys": list(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
                "archive_size_bytes": len(payload),
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "download_completed_unix_seconds": 1,
                "credential_persisted": False,
                "archive_extracted": False,
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)


@contextmanager
def _child_environment(environment: dict[str, str]):  # noqa: ANN202
    names = (
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        "VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD",
    )
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = environment[name]
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_secure_guard_from_child_environment(
    *,
    command: list[str],
    environment: dict[str, str],
):  # noqa: ANN202
    with _child_environment(environment):
        arguments = argparse.Namespace(
            dataset_key=command[2],
            file_keys=command[3],
            destination=Path(command[4]),
        )
        stable = SECURE._aihub538_source_authorization_guard(arguments)
        stable()
        return stable


def test_inventory_is_exactly_all_32_vl_vs_pairs() -> None:
    assert AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS == _REVIEWED_P8_VL_FILE_KEYS
    assert AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS == _REVIEWED_P8_VS_FILE_KEYS
    assert (
        tuple(
            pair.label_file_key
            for pair in P8_LOCKED_COHORT_DISCOVERY_CONTRACT.candidate_pairs
        )
        == _REVIEWED_P8_VL_FILE_KEYS
    )
    assert (
        tuple(
            pair.source_file_key
            for pair in P8_LOCKED_COHORT_DISCOVERY_CONTRACT.candidate_pairs
        )
        == _REVIEWED_P8_VS_FILE_KEYS
    )
    assert (
        AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256
        == _REVIEWED_P8_REQUEST_SHA256
    )
    assert (
        P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
        == _REVIEWED_DISCOVERY_CONTRACT_SHA256
    )
    assert len(AIHUB538_P8_VALIDATION_PAIRS) == 32
    assert len(set(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS)) == 32
    assert len(set(AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS)) == 32
    assert set(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS).isdisjoint(
        AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS
    )
    assert tuple(pair.pair_id for pair in AIHUB538_P8_VALIDATION_PAIRS) == tuple(
        range(1, 33)
    )
    assert tuple(
        pair.label_partition_id for pair in AIHUB538_P8_VALIDATION_PAIRS
    ) == tuple(f"VL{pair_id}" for pair_id in range(1, 33))


def test_all_64_reserved_keys_reject_cross_dataset_before_environment_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EnvironmentReadForbidden(dict[str, str]):
        def get(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("protected-key guard read environment")

    monkeypatch.setattr(
        SECURE.os,
        "environ",
        EnvironmentReadForbidden(),
    )
    for file_key in _REVIEWED_P8_VL_FILE_KEYS + _REVIEWED_P8_VS_FILE_KEYS:
        arguments = argparse.Namespace(
            dataset_key="68",
            file_keys=file_key,
            destination=Path("must-not-be-resolved"),
            timeout_seconds=1.0,
        )
        with pytest.raises(SECURE.DownloadError, match="dataset_mismatch"):
            SECURE._aihub538_source_authorization_guard(arguments)
    with pytest.raises(SECURE.DownloadError, match="file_keys_invalid"):
        SECURE._aihub538_source_authorization_guard(
            argparse.Namespace(
                dataset_key="68",
                file_keys="055080",
                destination=Path("must-not-be-resolved"),
                timeout_seconds=1.0,
            )
        )


def test_discovery_seal_cli_persists_read_zero_state_once(tmp_path: Path) -> None:
    state, _output, store, _actual, expected = _state_fixture(
        tmp_path,
        persist_discovery=False,
    )
    result = SEAL.main(
        [
            "--state-dir",
            str(state),
            "--seal-id",
            expected.seal_id,
            "--protocol-revision",
            expected.protocol_revision,
            "--sealed-at-utc",
            expected.sealed_at_utc,
        ]
    )
    assert result == 0
    path = state / "p8_locked_cohort_discovery_seal.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert store.load_locked_cohort_discovery_seal() == expected
    assert SEAL.main(
        [
            "--state-dir",
            str(state),
            "--seal-id",
            expected.seal_id,
            "--protocol-revision",
            expected.protocol_revision,
            "--sealed-at-utc",
            expected.sealed_at_utc,
        ]
    ) == 65


def test_transaction_is_label_only_self_digested_and_output_bound(
    tmp_path: Path,
) -> None:
    _state, output, _store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    document = transaction.to_json_object()

    assert P8LockedLabelDownloadTransaction.from_json_object(document) == transaction
    assert document["dataset_id"] == "538"
    assert document["ordered_label_file_keys"] == list(
        AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
    )
    assert document["label_download_authorized"] is True
    assert document["label_archive_open_authorized"] is False
    assert document["label_census_access_authorized"] is False
    assert document["source_download_authorized"] is False
    assert document["source_media_open_authorized"] is False
    assert document["promotion_authorized"] is False
    assert document["authorization_reusable"] is False

    tampered = dict(document)
    tampered["ordered_label_file_keys"] = [
        *document["ordered_label_file_keys"][:-1],
        "055105",
    ]
    with pytest.raises(P8LockedLabelDownloadError, match="allowlist"):
        P8LockedLabelDownloadTransaction.from_json_object(tampered)


def test_state_consumes_authorization_once_and_requires_actual_seal_lineage(
    tmp_path: Path,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )

    transaction_path = state / "p8_locked_label_download_transaction.json"
    assert stat.S_IMODE(transaction_path.stat().st_mode) == 0o400
    assert store.load_locked_label_download_transaction() == transaction
    descriptor = store.open_locked_label_download_transaction_descriptor()
    try:
        assert os.fstat(descriptor).st_ino == transaction_path.stat().st_ino
    finally:
        os.close(descriptor)
    with pytest.raises(P8SealStateError, match="already_consumed"):
        store.create_locked_label_download_transaction(
            transaction,
            discovery_seal=discovery,
        )


def test_sealed_state_clone_cannot_consume_a_second_label_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, _store, _actual, _discovery = _state_fixture(tmp_path)
    clone_state = tmp_path / "cloned-state"
    shutil.copytree(state, clone_state)
    clone_state.chmod(0o700)
    clone_output = _private_directory(tmp_path / "cloned-output")

    def stop_after_consumption(command, **_kwargs):  # noqa: ANN001, ANN202
        return subprocess.CompletedProcess(command, 70)

    monkeypatch.setattr(WRAPPER.subprocess, "run", stop_after_consumption)
    original_discovery = state / "p8_locked_cohort_discovery_seal.json"
    with original_discovery.open("rb") as discovery_file:
        assert WRAPPER.main(
            [
                "--state-dir",
                str(state),
                "--output-dir",
                str(output),
                "--discovery-seal-fd",
                str(discovery_file.fileno()),
                "--dataset-access-permission-confirmed",
                "--authorized-at-utc",
                "2026-08-29T00:00:03Z",
            ]
        ) == 70

    cloned_discovery = clone_state / "p8_locked_cohort_discovery_seal.json"
    with cloned_discovery.open("rb") as discovery_file:
        assert WRAPPER.main(
            [
                "--state-dir",
                str(clone_state),
                "--output-dir",
                str(clone_output),
                "--discovery-seal-fd",
                str(discovery_file.fileno()),
                "--dataset-access-permission-confirmed",
                "--authorized-at-utc",
                "2026-08-29T00:00:03Z",
            ]
        ) == 65

    transaction_name = "p8_locked_label_download_transaction.json"
    assert (state / transaction_name).is_file()
    assert not (clone_state / transaction_name).exists()
    assert sum(
        int((directory / transaction_name).exists())
        for directory in (state, clone_state)
    ) == 1


def test_post_base_pre_discovery_clone_cannot_create_second_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, _store, _actual, expected = _state_fixture(
        tmp_path,
        persist_discovery=False,
    )
    clone_state = tmp_path / "pre-discovery-clone"
    shutil.copytree(state, clone_state)
    clone_state.chmod(0o700)
    clone_output = _private_directory(tmp_path / "pre-discovery-clone-output")
    seal_arguments = [
        "--seal-id",
        expected.seal_id,
        "--protocol-revision",
        expected.protocol_revision,
        "--sealed-at-utc",
        expected.sealed_at_utc,
    ]

    assert SEAL.main(["--state-dir", str(state), *seal_arguments]) == 0
    assert SEAL.main(["--state-dir", str(clone_state), *seal_arguments]) == 65
    assert not (
        clone_state / "p8_locked_cohort_discovery_seal.json"
    ).exists()

    def stop_after_consumption(command, **_kwargs):  # noqa: ANN001, ANN202
        return subprocess.CompletedProcess(command, 70)

    monkeypatch.setattr(WRAPPER.subprocess, "run", stop_after_consumption)
    discovery_path = state / "p8_locked_cohort_discovery_seal.json"
    with discovery_path.open("rb") as discovery_file:
        assert WRAPPER.main(
            [
                "--state-dir",
                str(state),
                "--output-dir",
                str(output),
                "--discovery-seal-fd",
                str(discovery_file.fileno()),
                "--dataset-access-permission-confirmed",
                "--authorized-at-utc",
                "2026-08-29T00:00:03Z",
            ]
        ) == 70
    assert (
        state / "p8_locked_label_download_transaction.json"
    ).is_file()
    assert not (
        clone_state / "p8_locked_label_download_transaction.json"
    ).exists()
    assert not any(clone_output.iterdir())


def test_discovery_only_crash_recovers_companion_on_same_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _output, store, _actual, discovery = _state_fixture(
        tmp_path,
        persist_discovery=False,
    )
    original_create = P8SealStateStore._create_exclusive_json

    def fail_state_root(
        directory_descriptor: int,
        filename: str,
        document: dict[str, object],
        *,
        collision_reason: str,
    ) -> None:
        if filename == "p8_locked_label_state_root_seal.json":
            raise P8SealStateError("injected_state_root_write_failure")
        original_create(
            directory_descriptor,
            filename,
            document,
            collision_reason=collision_reason,
        )

    with monkeypatch.context() as patcher:
        patcher.setattr(
            P8SealStateStore,
            "_create_exclusive_json",
            staticmethod(fail_state_root),
        )
        with pytest.raises(P8SealStateError, match="injected"):
            store.create_locked_cohort_discovery_seal(discovery)

    assert (state / "p8_locked_cohort_discovery_seal.json").is_file()
    assert not (state / "p8_locked_label_state_root_seal.json").exists()
    recovery_arguments = [
        "--state-dir",
        str(state),
        "--seal-id",
        discovery.seal_id,
        "--protocol-revision",
        discovery.protocol_revision,
    ]
    assert SEAL.main(recovery_arguments) == 0
    assert store.load_locked_label_state_root_seal().discovery_seal_sha256 == (
        discovery.seal_sha256
    )
    assert SEAL.main(recovery_arguments) == 65


def test_discovery_stage_blocks_legacy_source_access_and_interleaving(
    tmp_path: Path,
) -> None:
    state, output, store, actual, _discovery = _state_fixture(tmp_path)
    base = store.load_read_zero_seal()
    with pytest.raises(
        P8SealStateError,
        match="selected_cohort_source_authorization_required",
    ):
        store.consume_access_authorization(
            expected_seal_sha256=base.seal_sha256,
            transaction_id="source_before_label_forbidden",
            consumed_at_utc="2026-08-29T00:00:03Z",
        )
    assert not (state / "p8_access_transaction.json").exists()

    state_descriptor = open_p8_locked_label_state_directory(state.absolute())
    lock_p8_locked_label_state_directory(state_descriptor)
    try:
        with pytest.raises(P8SealStateError, match="state_operation_in_use"):
            store.consume_access_authorization(
                expected_seal_sha256=base.seal_sha256,
                transaction_id="source_interleaving_forbidden",
                consumed_at_utc="2026-08-29T00:00:03Z",
            )
    finally:
        os.close(state_descriptor)
    assert not (state / "p8_access_transaction.json").exists()

    wrong_discovery = P8LockedCohortDiscoverySeal.issue(
        seal_id="wrong_discovery_001",
        protocol_revision="p8_locked_cohort_discovery_v1",
        reference_identity_set_commitments_sha256=_sha("wrong-root"),
        state_directory_device=state.stat().st_dev,
        state_directory_inode=state.stat().st_ino,
        sealed_at_utc="2026-08-29T00:00:02Z",
    )
    with pytest.raises(P8LockedLabelDownloadError, match="commitments_mismatch"):
        issue_p8_locked_label_download_transaction(
            discovery_seal=wrong_discovery,
            actual_source_seal=actual,
            state_root_seal=store.load_locked_label_state_root_seal(),
            output_directory_device=output.stat().st_dev,
            output_directory_inode=output.stat().st_ino,
            authorized_at_utc="2026-08-29T00:00:03Z",
            dataset_access_permission_confirmed=True,
        )


def test_discovery_and_legacy_source_phase_race_has_one_winner(
    tmp_path: Path,
) -> None:
    state, _output, store, _actual, discovery = _state_fixture(
        tmp_path,
        persist_discovery=False,
    )
    base = store.load_read_zero_seal()
    barrier = threading.Barrier(3)
    results: list[tuple[str, str]] = []
    results_lock = threading.Lock()

    def run_discovery() -> None:
        barrier.wait()
        try:
            store.create_locked_cohort_discovery_seal(discovery)
        except P8SealStateError as error:
            outcome = ("discovery", str(error))
        else:
            outcome = ("discovery", "success")
        with results_lock:
            results.append(outcome)

    def run_source() -> None:
        barrier.wait()
        try:
            store.consume_access_authorization(
                expected_seal_sha256=base.seal_sha256,
                transaction_id="source_phase_race",
                consumed_at_utc="2026-08-29T00:00:03Z",
            )
        except P8SealStateError as error:
            outcome = ("source", str(error))
        else:
            outcome = ("source", "success")
        with results_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=run_discovery),
        threading.Thread(target=run_source),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result == "success" for _phase, result in results) == 1
    assert (
        (state / "p8_locked_cohort_discovery_seal.json").exists()
        != (state / "p8_access_transaction.json").exists()
    )


def test_discovery_descriptor_requires_read_only_0400_regular_json(
    tmp_path: Path,
) -> None:
    _state, _output, _store, actual, discovery = _state_fixture(tmp_path)
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(discovery.to_json_object()), encoding="utf-8")
    path.chmod(0o400)
    with path.open("rb") as source:
        assert load_p8_locked_cohort_discovery_seal_descriptor(
            source.fileno()
        ) == discovery

    path.chmod(0o600)
    with path.open("rb") as source, pytest.raises(
        P8LockedLabelDownloadAdapterError, match="descriptor_invalid"
    ):
        load_p8_locked_cohort_discovery_seal_descriptor(source.fileno())


def test_low_level_guard_denies_every_vs_and_requires_exact_32_vl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    descriptor = store.open_locked_label_download_transaction_descriptor()
    state_descriptor = open_p8_locked_label_state_directory(state.absolute())
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        str(descriptor),
    )
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD",
        str(state_descriptor),
    )
    exact = argparse.Namespace(
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        destination=output.absolute(),
    )
    guard = SECURE._aihub538_source_authorization_guard(exact)
    guard()
    os.close(descriptor)
    os.close(state_descriptor)

    for source_key in AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS:
        arguments = argparse.Namespace(
            dataset_key="538",
            file_keys=source_key,
            destination=output.absolute(),
        )
        with pytest.raises(SECURE.DownloadError, match="entrypoint_required"):
            SECURE._aihub538_source_authorization_guard(arguments)

    for invalid_keys in (
        AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS[:-1],
        tuple(reversed(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS)),
        (
            *AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS[:-1],
            AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS[-1],
        ),
    ):
        arguments = argparse.Namespace(
            dataset_key="538",
            file_keys=",".join(invalid_keys),
            destination=output.absolute(),
        )
        with pytest.raises(SECURE.DownloadError):
            SECURE._aihub538_source_authorization_guard(arguments)

    with pytest.raises(SECURE.DownloadError, match="file_keys_invalid"):
        SECURE._validate_inputs(
            argparse.Namespace(
                dataset_key="538",
                file_keys="055080",
                timeout_seconds=1.0,
            )
        )


def test_p8_label_verifier_is_not_v11_source_pair_verifier(tmp_path: Path) -> None:
    archive = tmp_path / "download.tar"
    archive.write_bytes(b"all-32-label-fixture")
    archive.chmod(0o600)
    receipt = tmp_path / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": list(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
                "archive_size_bytes": archive.stat().st_size,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "download_completed_unix_seconds": 1,
                "credential_persisted": False,
                "archive_extracted": False,
            }
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    with archive.open("rb") as archive_file, receipt.open("rb") as receipt_file:
        verified = verify_aihub538_p8_label_download(
            archive_descriptor=archive_file.fileno(),
            receipt_descriptor=receipt_file.fileno(),
        )
        assert verified.archive_size_bytes == archive.stat().st_size
        with pytest.raises(Aihub538ArchiveError, match="contract_mismatch"):
            verify_aihub538_locked_download(
                archive_descriptor=archive_file.fileno(),
                receipt_descriptor=receipt_file.fileno(),
            )


class _Response:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int,
        headers: dict[str, str],
    ) -> None:
        self._payload = payload
        self._offset = 0
        self.status = status
        self.headers = headers

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        return None

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_p8_close_delimited_200_cannot_publish_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    transaction_descriptor = (
        store.open_locked_label_download_transaction_descriptor()
    )
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        str(transaction_descriptor),
    )
    monkeypatch.setattr(
        SECURE,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            b"clean-eof-could-be-truncated",
            status=200,
            headers={},
        ),
    )
    arguments = argparse.Namespace(
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        destination=output.absolute(),
        timeout_seconds=1.0,
    )
    try:
        with pytest.raises(SECURE.DownloadError, match="framing_required"):
            SECURE._download_p8_label_request(
                arguments,
                api_key="fixture-secret",
                assert_authorization_stable=lambda: None,
            )
    finally:
        os.close(transaction_descriptor)

    partial = output / "download.tar.partial"
    assert partial.is_file()
    assert partial.stat().st_size == 0
    assert not (output / "download.tar").exists()
    assert not (output / "download.receipt.json").exists()
    assert not (output / "p8_locked_label_download_completion.json").exists()


def test_low_level_p8_path_uses_bound_resume_and_rejects_partial_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    transaction_descriptor = (
        store.open_locked_label_download_transaction_descriptor()
    )
    state_descriptor = open_p8_locked_label_state_directory(state.absolute())
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        str(transaction_descriptor),
    )
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD",
        str(state_descriptor),
    )
    arguments = argparse.Namespace(
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        destination=output.absolute(),
        timeout_seconds=1.0,
    )
    partial = output / "download.tar.partial"
    partial.write_bytes(b"abc")
    partial.chmod(0o600)
    response = _Response(
        b"def",
        status=206,
        headers={
            "Content-Length": "3",
            "Content-Range": "bytes 3-5/6",
        },
    )
    monkeypatch.setattr(SECURE, "urlopen", lambda *_args, **_kwargs: response)
    try:
        result = SECURE._download_p8_label_request(
            arguments,
            api_key="fixture-secret",
            assert_authorization_stable=lambda: None,
        )
    finally:
        os.close(state_descriptor)
        os.close(transaction_descriptor)
    assert result == output / "download.tar"
    assert (output / "download.tar").read_bytes() == b"abcdef"
    assert stat.S_IMODE((output / "download.tar").stat().st_mode) == 0o600
    assert stat.S_IMODE(
        (output / "download.receipt.json").stat().st_mode
    ) == 0o400
    assert not partial.exists()
    receipt_text = (output / "download.receipt.json").read_text(
        encoding="utf-8"
    )
    assert "fixture-secret" not in receipt_text

    unsafe = _private_directory(tmp_path / "unsafe-output")
    target = tmp_path / "outside-partial"
    target.write_bytes(b"must-not-be-opened")
    (unsafe / "download.tar.partial").symlink_to(target)
    unsafe_descriptor = os.open(unsafe, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(SECURE.DownloadError, match="partial_invalid"):
            SECURE._p8_partial_size(unsafe_descriptor)
    finally:
        os.close(unsafe_descriptor)


def test_wrapper_fake_download_seals_archive_and_never_adds_api_key_to_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, _store, actual, discovery = _state_fixture(tmp_path)
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(discovery.to_json_object()), encoding="utf-8"
    )
    discovery_path.chmod(0o400)
    monkeypatch.setenv("AIHUB_API_KEY", "fixture-secret-never-in-argv")

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        assert "fixture-secret-never-in-argv" not in command
        assert command[2] == "538"
        assert tuple(command[3].split(",")) == (
            AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
        )
        inherited = set(kwargs["pass_fds"])
        assert int(
            kwargs["env"][
                "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD"
            ]
        ) in inherited
        assert int(
            kwargs["env"]["VOICE_COACH_AIHUB538_P8_STATE_DIRECTORY_FD"]
        ) in inherited
        _run_secure_guard_from_child_environment(
            command=command,
            environment=kwargs["env"],
        )
        payload = b"aggregate-label-archive"
        archive = output / "download.tar"
        archive.write_bytes(payload)
        archive.chmod(0o600)
        _write_secure_receipt(output, payload)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(WRAPPER.subprocess, "run", fake_run)
    with discovery_path.open("rb") as discovery_file:
        result = WRAPPER.main(
            [
                "--state-dir",
                str(state),
                "--output-dir",
                str(output),
                "--discovery-seal-fd",
                str(discovery_file.fileno()),
                "--dataset-access-permission-confirmed",
                "--authorized-at-utc",
                "2026-08-29T00:00:03Z",
            ]
        )
    assert result == 0
    assert stat.S_IMODE((output / "download.tar").stat().st_mode) == 0o400
    completion_path = output / "p8_locked_label_download_completion.json"
    assert stat.S_IMODE(completion_path.stat().st_mode) == 0o400
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    assert P8LockedLabelDownloadCompletion.from_json_object(
        completion
    ).completion_sha256 == completion["completion_sha256"]
    assert completion["label_archive_opened"] is False
    assert completion["source_file_keys_requested"] is False
    assert completion["payload_admitted"] is False
    assert completion["label_archive_presence_verified"] is False
    assert completion["source_payload_presence_verified"] is False
    assert completion["promotion_authorized"] is False
    assert "fixture-secret-never-in-argv" not in completion_path.read_text(
        encoding="utf-8"
    )


def test_state_binding_rejects_a_copied_self_digested_transaction(
    tmp_path: Path,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    copied = tmp_path / "copied-transaction.json"
    copied.write_bytes(
        (state / "p8_locked_label_download_transaction.json").read_bytes()
    )
    copied.chmod(0o400)
    state_descriptor = open_p8_locked_label_state_directory(state.absolute())
    with copied.open("rb") as copied_file:
        with pytest.raises(
            P8LockedLabelDownloadAdapterError,
            match="not_durable_state_member",
        ):
            require_p8_locked_label_transaction_state_binding(
                state_directory_descriptor=state_descriptor,
                transaction_descriptor=copied_file.fileno(),
            )
    os.close(state_descriptor)


def test_wrapper_resume_reuses_verified_pair_and_completion_without_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    payload = b"download-finished-before-wrapper-completion"
    archive = output / "download.tar"
    archive.write_bytes(payload)
    archive.chmod(0o600)
    _write_secure_receipt(output, payload)
    os.link(
        output / "download.receipt.json",
        output / "download.receipt.json.pending",
    )

    def child_must_not_run(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("completed secure pair must be reused")

    monkeypatch.setattr(WRAPPER.subprocess, "run", child_must_not_run)
    resume = [
        "--state-dir",
        str(state),
        "--output-dir",
        str(output),
        "--resume-existing-transaction",
    ]
    assert WRAPPER.main(resume) == 0
    completion_path = output / "p8_locked_label_download_completion.json"
    first_completion = completion_path.read_bytes()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400
    assert not (output / "download.receipt.json.pending").exists()
    os.link(
        completion_path,
        output / "p8_locked_label_download_completion.json.pending",
    )
    assert WRAPPER.main(resume) == 0
    assert completion_path.read_bytes() == first_completion
    assert not (
        output / "p8_locked_label_download_completion.json.pending"
    ).exists()


def test_wrapper_resume_recovers_archive_only_through_real_secure_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    payload = b"archive-finalized-before-receipt"
    archive = output / "download.tar"
    archive.write_bytes(payload)
    archive.chmod(0o600)

    def no_network(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("archive-only recovery must not use the network")

    monkeypatch.setattr(SECURE, "urlopen", no_network)

    def recover_in_process(command, **kwargs):  # noqa: ANN001, ANN202
        stable = _run_secure_guard_from_child_environment(
            command=command,
            environment=kwargs["env"],
        )
        arguments = argparse.Namespace(
            dataset_key=command[2],
            file_keys=command[3],
            destination=Path(command[4]),
            timeout_seconds=1.0,
        )
        with _child_environment(kwargs["env"]):
            SECURE._download_p8_label_request(
                arguments,
                api_key="in-memory-fixture-only",
                assert_authorization_stable=stable,
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(WRAPPER.subprocess, "run", recover_in_process)
    assert WRAPPER.main(
        [
            "--state-dir",
            str(state),
            "--output-dir",
            str(output),
            "--resume-existing-transaction",
        ]
    ) == 0
    assert (output / "download.receipt.json").is_file()
    assert (output / "p8_locked_label_download_completion.json").is_file()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400


def test_partial_path_swap_after_request_binding_is_rejected_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    transaction_descriptor = (
        store.open_locked_label_download_transaction_descriptor()
    )
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        str(transaction_descriptor),
    )
    partial = output / "download.tar.partial"
    partial.write_bytes(b"bound")
    partial.chmod(0o600)
    replacement = b"replacement-must-not-be-written"

    def swap_path(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        partial.unlink()
        partial.write_bytes(replacement)
        partial.chmod(0o600)
        return _Response(
            b"remote-bytes",
            status=206,
            headers={
                "Content-Length": "12",
                "Content-Range": "bytes 5-16/17",
            },
        )

    monkeypatch.setattr(SECURE, "urlopen", swap_path)
    arguments = argparse.Namespace(
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        destination=output.absolute(),
        timeout_seconds=1.0,
    )
    try:
        with pytest.raises(SECURE.DownloadError, match="partial_invalid"):
            SECURE._download_p8_label_request(
                arguments,
                api_key="fixture-secret",
                assert_authorization_stable=lambda: None,
            )
    finally:
        os.close(transaction_descriptor)
    assert partial.read_bytes() == replacement
    assert not (output / "download.tar").exists()


def test_exact_end_range_416_finalizes_bound_partial_without_redownload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, output, store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    store.create_locked_label_download_transaction(
        transaction,
        discovery_seal=discovery,
    )
    transaction_descriptor = (
        store.open_locked_label_download_transaction_descriptor()
    )
    monkeypatch.setenv(
        "VOICE_COACH_AIHUB538_P8_LABEL_TRANSACTION_FD",
        str(transaction_descriptor),
    )
    payload = b"already-complete-response"
    partial = output / "download.tar.partial"
    partial.write_bytes(payload)
    partial.chmod(0o600)

    def range_complete(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise SECURE.HTTPError(
            "https://api.aihub.or.kr/down/0.6/538.do",
            416,
            "Range Not Satisfiable",
            {"Content-Range": f"bytes */{len(payload)}"},
            None,
        )

    monkeypatch.setattr(SECURE, "urlopen", range_complete)
    arguments = argparse.Namespace(
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        destination=output.absolute(),
        timeout_seconds=1.0,
    )
    try:
        result = SECURE._download_p8_label_request(
            arguments,
            api_key="fixture-secret",
            assert_authorization_stable=lambda: None,
        )
    finally:
        os.close(transaction_descriptor)
    assert result == output / "download.tar"
    assert (output / "download.tar").read_bytes() == payload
    assert not partial.exists()
    assert (output / "download.receipt.json").is_file()


def test_receipt_and_completion_final_paths_are_never_partially_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, output, _store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    completion = issue_p8_locked_label_download_completion(
        transaction=transaction,
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
        secure_download_receipt_sha256=_sha("receipt"),
        completed_at_utc="2026-08-29T00:00:04Z",
    )
    output_descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    secure_write_all = SECURE._write_all
    adapter_write_all = P8_ADAPTER._write_all

    def short_write(descriptor: int, payload: bytes) -> None:
        os.write(descriptor, payload[:5])
        raise OSError

    monkeypatch.setattr(SECURE, "_write_all", short_write)
    with pytest.raises(SECURE.DownloadError, match="receipt_write_failed"):
        SECURE._write_receipt_at(
            directory_descriptor=output_descriptor,
            dataset_key="538",
            file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
            archive_size_bytes=3,
            archive_sha256=_sha("archive"),
        )
    assert not (output / "download.receipt.json").exists()
    monkeypatch.setattr(SECURE, "_write_all", secure_write_all)
    SECURE._write_receipt_at(
        directory_descriptor=output_descriptor,
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
    )
    assert stat.S_IMODE(
        (output / "download.receipt.json").stat().st_mode
    ) == 0o400

    monkeypatch.setattr(P8_ADAPTER, "_write_all", short_write)
    with pytest.raises(
        P8LockedLabelDownloadAdapterError,
        match="completion_write_failed",
    ):
        publish_p8_locked_label_download_completion(
            directory_descriptor=output_descriptor,
            completion=completion,
        )
    assert not (output / "p8_locked_label_download_completion.json").exists()
    monkeypatch.setattr(P8_ADAPTER, "_write_all", adapter_write_all)
    recovered = publish_p8_locked_label_download_completion(
        directory_descriptor=output_descriptor,
        completion=completion,
    )
    assert recovered == completion
    assert stat.S_IMODE(
        (output / "p8_locked_label_download_completion.json").stat().st_mode
    ) == 0o400
    os.close(output_descriptor)


def test_completion_pending_timestamp_is_reused_after_link_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, output, _store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    first = issue_p8_locked_label_download_completion(
        transaction=transaction,
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
        secure_download_receipt_sha256=_sha("receipt"),
        completed_at_utc="2026-08-29T00:00:04Z",
    )
    second = issue_p8_locked_label_download_completion(
        transaction=transaction,
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
        secure_download_receipt_sha256=_sha("receipt"),
        completed_at_utc="2026-08-29T00:00:05Z",
    )
    descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    real_link = P8_ADAPTER.os.link
    link_calls = 0

    def fail_first_link(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal link_calls
        link_calls += 1
        if link_calls == 1:
            raise OSError
        return real_link(*args, **kwargs)

    monkeypatch.setattr(P8_ADAPTER.os, "link", fail_first_link)
    with pytest.raises(
        P8LockedLabelDownloadAdapterError,
        match="completion_publish_failed",
    ):
        publish_p8_locked_label_download_completion(
            directory_descriptor=descriptor,
            completion=first,
        )
    assert not (output / "p8_locked_label_download_completion.json").exists()
    recovered = publish_p8_locked_label_download_completion(
        directory_descriptor=descriptor,
        completion=second,
    )
    os.close(descriptor)
    assert recovered == first
    assert P8LockedLabelDownloadCompletion.from_json_object(
        json.loads(
            (output / "p8_locked_label_download_completion.json").read_text(
                encoding="utf-8"
            )
        )
    ) == first


def test_valid_pending_retries_file_fsync_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _state, output, _store, actual, discovery = _state_fixture(tmp_path)
    transaction = _transaction(output, actual, discovery)
    completion = issue_p8_locked_label_download_completion(
        transaction=transaction,
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
        secure_download_receipt_sha256=_sha("receipt"),
        completed_at_utc="2026-08-29T00:00:04Z",
    )
    directory_descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    real_fsync = os.fsync
    fsync_calls = 0

    def fail_first_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 1:
            raise OSError
        real_fsync(descriptor)

    monkeypatch.setattr(SECURE.os, "fsync", fail_first_fsync)
    with pytest.raises(SECURE.DownloadError, match="receipt_write_failed"):
        SECURE._write_receipt_at(
            directory_descriptor=directory_descriptor,
            dataset_key="538",
            file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
            archive_size_bytes=3,
            archive_sha256=_sha("archive"),
        )
    SECURE._write_receipt_at(
        directory_descriptor=directory_descriptor,
        dataset_key="538",
        file_keys=",".join(AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS),
        archive_size_bytes=3,
        archive_sha256=_sha("archive"),
    )
    assert (output / "download.receipt.json").is_file()

    completion_output = _private_directory(tmp_path / "completion-output")
    os.close(directory_descriptor)
    directory_descriptor = os.open(
        completion_output,
        os.O_RDONLY | os.O_DIRECTORY,
    )
    fsync_calls = 0
    with pytest.raises(
        P8LockedLabelDownloadAdapterError,
        match="completion_write_failed",
    ):
        publish_p8_locked_label_download_completion(
            directory_descriptor=directory_descriptor,
            completion=completion,
        )
    recovered = publish_p8_locked_label_download_completion(
        directory_descriptor=directory_descriptor,
        completion=completion,
    )
    os.close(directory_descriptor)
    assert recovered == completion
    assert (
        completion_output / "p8_locked_label_download_completion.json"
    ).is_file()
