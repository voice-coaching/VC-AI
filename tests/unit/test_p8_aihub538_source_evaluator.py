"""Fixture-only tests for the AI Hub 538 P8 locked-source boundary."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import stat
import tarfile
from zipfile import ZIP_STORED, ZipFile

import pytest

from voice_coach.visual_feedback.adapters.aihub538_locked_dataset import (
    Aihub538LockedDatasetError,
    Aihub538LockedDatasetEvaluator,
    P8ClaimPolicyReplayAggregate,
    P8LockedItemEvaluation,
    P8RestrictedReferencePopulation,
    p8_frozen_pipeline_pins_sha256,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (
    _restricted_identity_set,
)
from voice_coach.visual_feedback.adapters.p8_seal_store import (
    P8SealStateError,
    P8SealStateStore,
)
from voice_coach.visual_feedback.adapters.p8_actual_source_reload import (
    p8_actual_source_reload_worker_code_sha256,
)
from voice_coach.visual_feedback.adapters.p8_reference_identity import (
    P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
    P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION,
    P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
    canonical_restricted_identity_set_root_sha256,
    prepare_p8_actual_source_read_zero_seal,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p8_locked_evaluation import (
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from voice_coach.visual_feedback.services import (
    p8_aihub538_source_evaluator as source_module,
)
from voice_coach.visual_feedback.services.p8_aihub538_source_evaluator import (
    P8Aihub538OneShotRunner,
    P8Aihub538SourceError,
)
from voice_coach.visual_feedback.services.p8_locked_evaluation import (
    P8LockedEvaluationService,
)


_PRIVATE_TRANSCRIPT = "잠금 fixture 전사 원문은 receipt에 없어야 한다"
_PRIVATE_OUTER_STEM = "locked-speaker-C032"
_IDENTITY_KEY = b"p8-locked-fixture-identity-key-0"
assert len(_IDENTITY_KEY) == 32
_SEALED_AT = "2026-08-28T05:00:00Z"
_CONSUMED_AT = "2026-08-28T05:01:00Z"
_PENDING_AT = "2026-08-28T05:02:00Z"
_CLEANED_AT = "2026-08-28T05:03:00Z"
_RELOADED_AT = "2026-08-28T05:04:00Z"
_COMMITTED_AT = "2026-08-28T05:05:00Z"


def _sha(label: str) -> str:
    return sha256_document({"label": label})


def _session_stem(
    *,
    angle: str,
    speaker_digits: str = "032",
    session: str = "010",
    environment: str = "J",
    noise: int = 1,
) -> str:
    return (
        f"lip_{environment}_{noise}_F_02_C{speaker_digits}_{angle}_{session}"
    )


def _label_record(
    *, angle: str, session: str, environment: str, noise: int
) -> dict[str, object]:
    stem = _session_stem(
        angle=angle,
        session=session,
        environment=environment,
        noise=noise,
    )
    return {
        "dataSet": {
            "description": "restricted locked fixture metadata",
            "url": "https://www.aihub.or.kr/",
            "version": 1.0,
            "year": "2022",
        },
        "Video_info": {
            "video_Name": f"{stem}.mp4",
            "video_Format": "mp4",
            "video_Duration": "0.1",
            "FPS": 30.0,
            "Resolution": "640x480",
        },
        "Audio_info": {
            "Audio_Name": f"{stem}.wav",
            "Audio_Format": "wav",
            "Audio_Duration": "0.1",
            "Sampling_rate": "48kHz",
            "Channel(s)": 1,
        },
        "Audio_env": {"Noise": noise},
        "Video_env": {
            "env": (
                "indoor_light" if environment == "J" else "outdoor_light"
            ),
            "Angle": angle,
        },
        "Sentence_info": [
            {
                "ID": "private-locked-sentence",
                "topic": "private locked topic",
                "sentence_text": _PRIVATE_TRANSCRIPT,
                "start_time": 0.0,
                "end_time": 0.1,
            }
        ],
        "speaker_info": {
            "speaker_ID": "C032",
            "Specificity": "C",
            "Gender": "F",
            "Age": 2,
            "Accent": "N",
        },
        "Bounding_box_info": {
            "Face_bounding_box": {
                "xtl_ytl_xbr_ybr": [[100, 100, 200, 180]] * 3
            },
            "Lip_bounding_box": {
                "xtl_ytl_xbr_ybr": [[120, 130, 180, 165]] * 3
            },
        },
    }


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _locked_members(
    *, angles: str = "ABCDEFGHI"
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    labels: dict[str, bytes] = {}
    sources: dict[str, bytes] = {}
    relative = "noise/C/F/group"
    for index, angle in enumerate(angles):
        session = f"{index + 10:03d}"
        noise = index % 6 + 1
        environment = "J" if index % 2 == 0 else "K"
        stem = _session_stem(
            angle=angle,
            session=session,
            environment=environment,
            noise=noise,
        )
        audio_stem = _session_stem(
            angle="A",
            session=session,
            environment=environment,
            noise=noise,
        )
        labels[f"restricted/Label/VL11/{relative}/{stem}.json"] = json.dumps(
            [
                _label_record(
                    angle=angle,
                    session=session,
                    environment=environment,
                    noise=noise,
                )
            ],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sources[
            f"restricted/Source/VS11/{relative}/{stem}.mp4"
        ] = b"\x00\x00\x00\x18ftypisomfixture-video"
        sources[
            f"restricted/Source/VS11/{relative}/{audio_stem}.wav"
        ] = b"RIFF\x08\x00\x00\x00WAVEfixture-audio"
    return labels, sources


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _split_members(
    *, directory: str, stem: str, payload: bytes, archive_kind: str
) -> list[tuple[str, bytes]]:
    first_size = len(payload) // 2
    return [
        (
            f"{directory}/{stem}.{archive_kind}.part{first_size}",
            payload[first_size:],
        ),
        (f"{directory}/{stem}.{archive_kind}.part0", payload[:first_size]),
    ]


def _completed_locked_fixture(
    directory: Path,
    *,
    file_keys: tuple[str, str] = ("55114", "55082"),
    archive_kind: str = "tar",
    archive_fault: str | None = None,
    angles: str = "ABCDEFGHI",
) -> tuple[Path, Path]:
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    label_members, source_members = _locked_members(angles=angles)
    pack = _tar_bytes if archive_kind == "tar" else _zip_bytes
    label_archive = pack(label_members)
    source_archive = pack(source_members)
    members = _split_members(
        directory=f"opaque/{_PRIVATE_OUTER_STEM}/labels",
        stem="misleading-media-name",
        payload=label_archive,
        archive_kind=archive_kind,
    ) + _split_members(
        directory=f"opaque/{_PRIVATE_OUTER_STEM}/media",
        stem="misleading-label-name",
        payload=source_archive,
        archive_kind=archive_kind,
    )
    if archive_fault == "gap":
        name, payload = members[0]
        offset = int(name.rsplit(".part", 1)[1])
        members[0] = (name.rsplit(".part", 1)[0] + f".part{offset + 1}", payload)
    elif archive_fault == "mixed":
        name, payload = members[0]
        replacement = "zip" if archive_kind == "tar" else "tar"
        members[0] = (name.replace(f".{archive_kind}.part", f".{replacement}.part"), payload)
    elif archive_fault == "tamper":
        name, payload = members[1]
        members[1] = (name, b"X" + payload[1:])
    elif archive_fault is not None:
        raise AssertionError("unknown fixture archive fault")
    archive_path = directory / "download.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o777
            archive.addfile(info, io.BytesIO(payload))
    archive_path.chmod(0o600)
    archive_payload = archive_path.read_bytes()
    receipt_path = directory / "download.receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": list(file_keys),
                "archive_size_bytes": len(archive_payload),
                "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
                "download_completed_unix_seconds": 1_787_856_000,
                "credential_persisted": False,
                "archive_extracted": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    receipt_path.chmod(0o400)
    return archive_path, receipt_path


def _identity_key_file(path: Path) -> Path:
    path.write_bytes(_IDENTITY_KEY)
    path.chmod(0o400)
    return path


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _seal() -> P8ReadZeroSeal:
    media_contract = "p8_locked_fixture_media_v1"
    observation_contract = "p8_locked_fixture_observation_v1"
    return P8ReadZeroSeal.issue(
        seal_id="p8_locked_fixture_seal_001",
        protocol_revision="p8_locked_fixture_protocol_v1",
        source_revision="aihub_538_locked_fixture_v1",
        p6_evidence_receipt_sha256=_sha("p6-receipt"),
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
        video_qc_decision_policy_revision="p8_locked_fixture_qc_decision_v1",
        video_qc_decision_policy_sha256=_sha("qc-decision-policy"),
        av_sync_measurement_code_sha256=_sha("sync-measurement-code"),
        av_sync_measurement_policy_sha256=_sha("sync-measurement-policy"),
        av_sync_decision_code_sha256=_sha("sync-decision-code"),
        av_sync_decision_policy_revision="p8_locked_fixture_sync_decision_v1",
        av_sync_decision_policy_sha256=_sha("sync-decision-policy"),
        phone_window_code_sha256=_sha("phone-window-code"),
        phone_window_policy_revision="p8_locked_fixture_phone_window_v1",
        phone_window_policy_sha256=_sha("phone-window-policy"),
        evaluation_pipeline_code_sha256=_sha("evaluation-pipeline-code"),
        source_evaluator_code_sha256=_sha("source-evaluator-code"),
        media_engine_code_sha256=_sha("media-engine-code"),
        sealed_at_utc=_SEALED_AT,
    )


def _state(tmp_path: Path) -> tuple[Path, P8SealStateStore, P8ReadZeroSeal]:
    state_directory = tmp_path / "p8-state"
    state_directory.mkdir(mode=0o700)
    state_directory.chmod(0o700)
    store = P8SealStateStore(state_directory)
    seal = _seal()
    P8LockedEvaluationService(store).create_read_zero_seal(seal)
    return state_directory, store, seal


def _reference_populations(
    seal: P8ReadZeroSeal,
    *,
    overlap_locked: bool = False,
) -> tuple[P8RestrictedReferencePopulation, P8RestrictedReferencePopulation]:
    p6_speakers = {"C032"} if overlap_locked else {"C900"}
    p6_sessions = {
        _session_stem(angle="A") if overlap_locked else "p6_session_900"
    }
    p6 = _restricted_identity_set(
        identity_hash_key=_IDENTITY_KEY,
        speaker_ids=p6_speakers,
        session_ids=p6_sessions,
    )
    training = _restricted_identity_set(
        identity_hash_key=_IDENTITY_KEY,
        speaker_ids={"C901"},
        session_ids={"ts174_session_901"},
    )
    return (
        P8RestrictedReferencePopulation(
            role="p6_calibration",
            manifest_sha256=_sha("p6-admission-manifest"),
            identities=p6,
        ),
        P8RestrictedReferencePopulation(
            role="ts174_training",
            manifest_sha256=_sha("ts174-training-manifest"),
            identities=training,
        ),
    )


def _restricted_manifest_document(
    *,
    role: str,
    admission_receipt_sha256: str,
    admission_manifest_sha256: str,
    identities,  # noqa: ANN001
) -> dict[str, object]:
    actors = tuple(sorted(identities.speaker_hmac_sha256))
    attempts = tuple(sorted(identities.session_hmac_sha256))
    unsigned = {
        "schema_version": (
            P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION
        ),
        "role": role,
        "strict_admission_schema_version": (
            "voice_coach.fixture_strict_admission.v1"
        ),
        "strict_admission_receipt_sha256": admission_receipt_sha256,
        "strict_admission_manifest_sha256": admission_manifest_sha256,
        "identity_namespace_commitment_sha256": (
            identities.namespace_commitment_sha256
        ),
        "actor_hmac_count": len(actors),
        "attempt_hmac_count": len(attempts),
        "actor_hmac_sha256": list(actors),
        "attempt_hmac_sha256": list(attempts),
        "identity_set_root_sha256": (
            canonical_restricted_identity_set_root_sha256(
                identity_namespace_commitment_sha256=(
                    identities.namespace_commitment_sha256
                ),
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


def _write_restricted_reference_directory(
    *,
    directory: Path,
    seal: P8ReadZeroSeal,
    references: tuple[
        P8RestrictedReferencePopulation,
        P8RestrictedReferencePopulation,
    ],
) -> None:
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    documents = (
        _restricted_manifest_document(
            role="p6_calibration",
            admission_receipt_sha256=seal.p6_evidence_receipt_sha256,
            admission_manifest_sha256=references[0].manifest_sha256,
            identities=references[0].identities,
        ),
        _restricted_manifest_document(
            role="ts174_training",
            admission_receipt_sha256=_sha("ts174-admission-receipt"),
            admission_manifest_sha256=references[1].manifest_sha256,
            identities=references[1].identities,
        ),
    )
    for filename, document in zip(
        (
            P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
            P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
        ),
        documents,
        strict=True,
    ):
        path = directory / filename
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o400)


class _FrozenFixturePipeline:
    def __init__(
        self,
        seal: P8ReadZeroSeal,
        *,
        fail_angle: str | None = None,
        fail_all: bool = False,
        claim_violations: int = 0,
    ) -> None:
        self.pipeline_pins_sha256 = p8_frozen_pipeline_pins_sha256(seal)
        self.fail_angle = fail_angle
        self.fail_all = fail_all
        self.claim_violations = claim_violations
        self.evaluated_angles: list[str] = []
        self.evaluated_bindings: list[str] = []

    def evaluate(self, item):  # noqa: ANN001
        self.evaluated_angles.append(item.angle)
        self.evaluated_bindings.append(item.item_binding_sha256)
        if self.fail_all or item.angle == self.fail_angle:
            raise RuntimeError("private per-item failure detail")
        with item.open_label() as label_source:
            assert _PRIVATE_TRANSCRIPT.encode("utf-8") in label_source.read()
        with item.open_video() as video_source:
            assert video_source.read(8)[4:8] == b"ftyp"
        with item.open_audio() as audio_source:
            assert audio_source.read(4) == b"RIFF"
        return P8LockedItemEvaluation(
            item_binding_sha256=item.item_binding_sha256,
            pipeline_pins_sha256=self.pipeline_pins_sha256,
            decode_succeeded=True,
            qc_decision_lineage_valid=True,
            supported_clip=True,
            single_face_coverage=0.95,
            av_anchor_available=True,
            ready=True,
            sync_decision_lineage_valid=True,
            absolute_sync_offset_ms=30.0,
            evaluation_succeeded=True,
        )

    def replay_claim_policy(
        self, *, evaluated_case_count: int
    ) -> P8ClaimPolicyReplayAggregate:
        pair_count = evaluated_case_count * 2
        return P8ClaimPolicyReplayAggregate.issue(
            pipeline_pins_sha256=self.pipeline_pins_sha256,
            claim_catalog_sha256=_seal().claim_catalog_sha256,
            claim_policy_sha256=_seal().claim_policy_sha256,
            forbidden_fixture_catalog_sha256=_sha("forbidden-fixtures"),
            evaluated_case_count=evaluated_case_count,
            catalog_claim_count=2,
            catalog_claim_replayed_count=2,
            expected_case_claim_pair_count=pair_count,
            replayed_case_claim_pair_count=pair_count,
            forbidden_fixture_count=3,
            forbidden_fixture_replayed_count=3,
            claim_policy_violation_count=self.claim_violations,
            forbidden_claim_fixture_violation_count=0,
        )


def _execute_directory(
    *,
    input_directory: Path,
    key_path: Path,
    output_directory: Path,
    store: P8SealStateStore,
    seal: P8ReadZeroSeal,
    pipeline: _FrozenFixturePipeline,
    references: tuple[
        P8RestrictedReferencePopulation,
        P8RestrictedReferencePopulation,
    ],
):
    restricted_directory = (
        store._state_directory_for_isolated_reload().parent
        / "restricted-reference-fixture"
    )
    actual_seal_path = (
        store._state_directory_for_isolated_reload()
        / "p8_actual_source_read_zero_seal.json"
    )
    if not actual_seal_path.exists():
        _write_restricted_reference_directory(
            directory=restricted_directory,
            seal=seal,
            references=references,
        )
        with ExitStack() as prepare_stack:
            reference_descriptor = os.open(
                restricted_directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            prepare_stack.callback(os.close, reference_descriptor)
            actual = prepare_p8_actual_source_read_zero_seal(
                restricted_state_directory_descriptor=reference_descriptor,
                identity_key_descriptor=_readonly(prepare_stack, key_path),
                base_read_zero_seal=seal,
                fresh_reload_worker_code_sha256=(
                    p8_actual_source_reload_worker_code_sha256()
                ),
                sealed_at_utc=_SEALED_AT,
            )
        store.create_actual_source_read_zero_seal(actual)
    actual = store.load_actual_source_read_zero_seal()
    with ExitStack() as stack:
        reference_descriptor = os.open(
            restricted_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        stack.callback(os.close, reference_descriptor)
        runner = P8Aihub538OneShotRunner(state_store=store)
        return runner._execute_fixture_from_completed_directory(
            confirm_irreversible_consumption=True,
            input_directory=input_directory,
            identity_key_descriptor=_readonly(stack, key_path),
            restricted_reference_directory_descriptor=reference_descriptor,
            frozen_pipeline=pipeline,
            output_directory=output_directory,
            expected_seal_sha256=seal.seal_sha256,
            expected_actual_source_seal_sha256=(
                actual.actual_source_seal_sha256
            ),
            transaction_id="p8_locked_access_001",
            consumed_at_utc=_CONSUMED_AT,
            report_id="p8_locked_report_001",
            pending_at_utc=_PENDING_AT,
            cleanup_recorded_at_utc=_CLEANED_AT,
            reload_verified_at_utc=_RELOADED_AT,
            committed_at_utc=_COMMITTED_AT,
        )


def test_one_shot_directory_source_opens_only_after_durable_transaction_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    references = _reference_populations(seal)
    output_directory = tmp_path / "p8-source-run"
    real_opener = source_module.open_completed_aihub538_download_directory
    open_order: list[str] = []

    @contextmanager
    def checked_opener(path):  # noqa: ANN001
        assert path == input_directory
        fresh_store = P8SealStateStore(state_directory)
        fresh_store.load_access_transaction()
        fresh_store.load_actual_source_gate_pending()
        open_order.append("opened_after_durable_gate_pending")
        with real_opener(path) as opened:
            yield opened

    monkeypatch.setattr(
        source_module,
        "open_completed_aihub538_download_directory",
        checked_opener,
    )
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=references,
    )

    assert open_order == ["opened_after_durable_gate_pending"]
    assert sorted(pipeline.evaluated_angles) == list("ABCDEFGHI")
    assert report.status is P8LockedEvaluationStatus.COMPLETED
    assert (output_directory / "work").exists() is False
    assert [path.name for path in sorted(output_directory.glob("*.json"))] == [
        "00-gate-pending.json",
        "10-cleanup.json",
        "20-aggregate-lineage.json",
        "99-terminal.json",
    ]
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o400
        for path in output_directory.glob("*.json")
    )
    cleanup = json.loads(
        (output_directory / "10-cleanup.json").read_text(encoding="utf-8")
    )
    assert cleanup["restricted_media_retained"] is False
    lineage = json.loads(
        (output_directory / "20-aggregate-lineage.json").read_text(
            encoding="utf-8"
        )
    )
    dataset_evaluation = lineage["dataset_evaluation"]
    assert dataset_evaluation["admitted_item_count"] == 9
    assert dataset_evaluation["evaluated_item_count"] == 9
    assert dataset_evaluation["evaluation_exclusion_count"] == 0
    assert len(dataset_evaluation["slice_aggregates"]) == 17
    assert [
        item["reference_role"]
        for item in dataset_evaluation["identity_intersections"]
    ] == ["p6_calibration", "ts174_training"]
    assert all(
        item["intersection"]["disjoint"] is True
        for item in dataset_evaluation["identity_intersections"]
    )
    assert dataset_evaluation["decode_success_rate"] == 1.0
    assert dataset_evaluation["av_anchor_availability_rate"] == 1.0
    assert (
        dataset_evaluation["maximum_ready_absolute_sync_offset_ms"]
        == 30.0
    )
    metrics = store.load_aggregate_metrics()
    assert metrics.qc_decision_lineage_valid_item_count == 9
    assert metrics.ready_sync_decision_lineage_valid_case_count == 9
    assert metrics.claim_policy_violation_count == 0
    reload_receipt = store.load_actual_source_fresh_reload_receipt()
    commit = store.load_actual_source_final_commit()
    expected_pipeline_pins = p8_frozen_pipeline_pins_sha256(seal)
    terminal_document = json.loads(
        (output_directory / "99-terminal.json").read_text(encoding="utf-8")
    )
    assert lineage["frozen_pipeline_pins_sha256"] == expected_pipeline_pins
    assert terminal_document["frozen_pipeline_pins_sha256"] == (
        expected_pipeline_pins
    )
    assert reload_receipt.frozen_pipeline_pins_sha256 == expected_pipeline_pins
    assert reload_receipt.fresh_process is True
    assert reload_receipt.network_isolation == "seccomp_socket_denied"
    assert reload_receipt.restricted_work_directory_absent is True
    assert reload_receipt.promotion_authorized is False
    assert commit.canonical_report_sha256 == report.report_sha256
    assert commit.source_terminal_sha256 == terminal_document["terminal_sha256"]

    serialized = json.dumps(
        [
            cleanup,
            lineage,
            json.loads(
                (output_directory / "99-terminal.json").read_text(
                    encoding="utf-8"
                )
            ),
            report.to_json_object(),
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    locked_identities = _restricted_identity_set(
        identity_hash_key=_IDENTITY_KEY,
        speaker_ids={"C032"},
        session_ids={
            _session_stem(
                angle="A",
                session=f"{index + 10:03d}",
                environment="J" if index % 2 == 0 else "K",
                noise=index % 6 + 1,
            )
            for index in range(9)
        },
    )
    for forbidden in (
        _PRIVATE_TRANSCRIPT,
        _PRIVATE_OUTER_STEM,
        "misleading-media-name",
        "misleading-label-name",
        "lip_J_1_F_02_C032",
        "private-locked-sentence",
        str(tmp_path),
        _IDENTITY_KEY.hex(),
        *pipeline.evaluated_bindings,
        *references[0].identities.speaker_hmac_sha256,
        *references[0].identities.session_hmac_sha256,
        *references[1].identities.speaker_hmac_sha256,
        *references[1].identities.session_hmac_sha256,
        *locked_identities.speaker_hmac_sha256,
        *locked_identities.session_hmac_sha256,
    ):
        assert forbidden not in serialized


def test_split_zip_regression_remains_supported(tmp_path: Path) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory, archive_kind="zip")
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=tmp_path / "p8-source-run",
        store=store,
        seal=seal,
        pipeline=_FrozenFixturePipeline(seal),
        references=_reference_populations(seal),
    )
    assert report.status is P8LockedEvaluationStatus.COMPLETED
    lineage = json.loads(
        (tmp_path / "p8-source-run" / "20-aggregate-lineage.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(item["repack"] is None for item in lineage["merged_archives"])
    assert all(
        item["merge"]["schema_version"]
        == "voice_coach.aihub538_split_zip_merge_receipt.v2"
        for item in lineage["merged_archives"]
    )


@pytest.mark.parametrize("archive_fault", ["mixed", "gap", "tamper"])
def test_split_tar_mixed_gap_and_tamper_fail_closed(
    tmp_path: Path, archive_fault: str
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(
        input_directory,
        archive_kind="tar",
        archive_fault=archive_fault,
    )
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=tmp_path / "p8-source-run",
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_locked_source_evaluation_failed",)
    assert pipeline.evaluated_angles == []


def test_missing_required_angle_slice_fails_before_item_evaluation(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory, angles="ABCDEFGH")
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=tmp_path / "p8-source-run",
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert pipeline.evaluated_angles == []


def test_directory_only_entrypoint_and_claim_violation_persist_threshold_failure(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal, claim_violations=1)
    runner = P8Aihub538OneShotRunner(state_store=store)
    assert not hasattr(runner, "execute_from_descriptors")
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=tmp_path / "p8-source-run",
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_claim_policy_violation_detected",)
    assert store.load_aggregate_metrics().claim_policy_violation_count == 1
    terminal = json.loads(
        (tmp_path / "p8-source-run" / "99-terminal.json").read_text(
            encoding="utf-8"
        )
    )
    assert terminal["report_status"] == "failed_closed"
    assert terminal["canonical_report_sha256"] == report.report_sha256


def test_item_failure_is_accounted_as_exclusion_and_all_items_are_attempted(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal, fail_angle="A")
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=tmp_path / "p8-source-run",
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )
    assert sorted(pipeline.evaluated_angles) == list("ABCDEFGHI")
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == (
        "p8_evaluation_exclusion_detected",
        "p8_aggregate_supported_slice_coverage_below_threshold",
    )
    assert report.admitted_item_count == 9
    assert report.evaluated_item_count == 8
    assert report.evaluation_exclusion_count == 1


def test_all_evaluation_fail_preserves_n_zero_n_completeness(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal, fail_all=True)
    output_directory = tmp_path / "p8-source-run"
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )
    assert sorted(pipeline.evaluated_angles) == list("ABCDEFGHI")
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    completeness = json.loads(
        (output_directory / "15-failure-completeness.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        completeness["admitted_item_count"],
        completeness["evaluated_item_count"],
        completeness["evaluation_exclusion_count"],
    ) == (9, 0, 9)
    assert completeness["per_item_outcomes_serialized"] is False


def test_identity_overlap_fails_before_any_item_evaluation_and_burns_one_shot(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    output_directory = tmp_path / "p8-source-run"
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal, overlap_locked=True),
    )
    assert pipeline.evaluated_angles == []
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert report.reason_codes == ("p8_locked_source_evaluation_failed",)
    assert (output_directory / "work").exists() is False
    terminal = json.loads(
        (output_directory / "99-terminal.json").read_text(encoding="utf-8")
    )
    assert terminal["report_status"] == "failed_closed"
    assert terminal["canonical_report_sha256"] == report.report_sha256
    with pytest.raises(P8SealStateError, match="already_consumed"):
        _execute_directory(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=tmp_path / "retry-run",
            store=store,
            seal=seal,
            pipeline=pipeline,
            references=_reference_populations(seal),
        )


def test_wrong_secure_download_keys_fail_after_consumption_without_path_leak(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(
        input_directory, file_keys=("54725", "54944")
    )
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    output_directory = tmp_path / "p8-source-run"
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=_FrozenFixturePipeline(seal),
        references=_reference_populations(seal),
    )
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert store.load_access_transaction().access_count == 1
    terminal_text = (output_directory / "99-terminal.json").read_text(
        encoding="utf-8"
    )
    assert str(tmp_path) not in terminal_text
    assert "54725" not in terminal_text


def test_python_level_interrupt_after_source_open_is_persisted_failed_closed(
    tmp_path: Path,
) -> None:
    class InterruptingPipeline(_FrozenFixturePipeline):
        def evaluate(self, item):  # noqa: ANN001
            self.evaluated_angles.append(item.angle)
            raise KeyboardInterrupt

    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)
    pipeline = InterruptingPipeline(seal)
    output_directory = tmp_path / "p8-source-run"

    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=pipeline,
        references=_reference_populations(seal),
    )

    assert pipeline.evaluated_angles == ["A"]
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert store.load_locked_report() == report
    assert store.load_access_transaction().access_count == 1
    terminal = json.loads(
        (output_directory / "99-terminal.json").read_text(encoding="utf-8")
    )
    assert terminal["report_status"] == "failed_closed"
    assert terminal["canonical_report_sha256"] == report.report_sha256
    assert (output_directory / "work").exists() is False


def test_pipeline_pin_mismatch_is_rejected_before_consumption_or_source_open(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "does-not-need-to-exist"
    key_path = _identity_key_file(tmp_path / "identity.key")
    state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    pipeline.pipeline_pins_sha256 = _sha("wrong-pipeline")
    with pytest.raises(
        P8Aihub538SourceError, match="pipeline_pin_mismatch"
    ):
        _execute_directory(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=tmp_path / "p8-source-run",
            store=store,
            seal=seal,
            pipeline=pipeline,
            references=_reference_populations(seal),
        )
    assert not (state_directory / "p8_access_transaction.json").exists()
    assert not input_directory.exists()


def test_irreversible_confirmation_is_required_before_source_or_key_read(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "does-not-need-to-exist"
    state_directory, store, seal = _state(tmp_path)
    with pytest.raises(
        P8Aihub538SourceError, match="confirmation_required"
    ):
        P8Aihub538OneShotRunner(
            state_store=store
        )._execute_fixture_from_completed_directory(
            confirm_irreversible_consumption=False,
            input_directory=input_directory,
            identity_key_descriptor=-1,
            restricted_reference_directory_descriptor=-1,
            frozen_pipeline=_FrozenFixturePipeline(seal),
            output_directory=tmp_path / "p8-source-run",
            expected_seal_sha256=seal.seal_sha256,
            expected_actual_source_seal_sha256=_sha("not-read"),
            transaction_id="p8_locked_access_001",
            consumed_at_utc=_CONSUMED_AT,
            report_id="p8_locked_report_001",
            pending_at_utc=_PENDING_AT,
            cleanup_recorded_at_utc=_CLEANED_AT,
            reload_verified_at_utc=_RELOADED_AT,
            committed_at_utc=_COMMITTED_AT,
        )
    assert not (state_directory / "p8_access_transaction.json").exists()
    assert not input_directory.exists()


def test_companion_seal_is_required_before_consumption_or_source_open(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "does-not-need-to-exist"
    key_path = _identity_key_file(tmp_path / "identity.key")
    state_directory, store, seal = _state(tmp_path)
    with ExitStack() as stack, pytest.raises(
        P8SealStateError, match="state_document_unavailable"
    ):
        P8Aihub538OneShotRunner(
            state_store=store
        )._execute_fixture_from_completed_directory(
            confirm_irreversible_consumption=True,
            input_directory=input_directory,
            identity_key_descriptor=_readonly(stack, key_path),
            restricted_reference_directory_descriptor=-1,
            frozen_pipeline=_FrozenFixturePipeline(seal),
            output_directory=tmp_path / "p8-source-run",
            expected_seal_sha256=seal.seal_sha256,
            expected_actual_source_seal_sha256=_sha("missing-companion"),
            transaction_id="p8_locked_access_001",
            consumed_at_utc=_CONSUMED_AT,
            report_id="p8_locked_report_001",
            pending_at_utc=_PENDING_AT,
            cleanup_recorded_at_utc=_CLEANED_AT,
            reload_verified_at_utc=_RELOADED_AT,
            committed_at_utc=_COMMITTED_AT,
        )
    assert not (state_directory / "p8_access_transaction.json").exists()
    assert not input_directory.exists()


def test_raw_reference_population_injection_is_rejected_before_archive_open(
    tmp_path: Path,
) -> None:
    _state_directory, _store, seal = _state(tmp_path)
    with pytest.raises(
        Aihub538LockedDatasetError, match="sealed_reference_bundle_required"
    ):
        Aihub538LockedDatasetEvaluator().evaluate(
            label_zip_descriptor=-1,
            source_zip_descriptor=-1,
            sealed_references=_reference_populations(seal),
            frozen_pipeline=_FrozenFixturePipeline(seal),
            seal=seal,
        )


def test_gate_pending_write_failure_burns_access_without_source_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    state_directory, store, seal = _state(tmp_path)
    pipeline = _FrozenFixturePipeline(seal)
    opened = 0

    def fail_pending(_pending):  # noqa: ANN001
        raise P8SealStateError("injected_gate_pending_fsync_failure")

    @contextmanager
    def forbidden_open(_path):  # noqa: ANN001
        nonlocal opened
        opened += 1
        yield (-1, -1)

    monkeypatch.setattr(store, "create_actual_source_gate_pending", fail_pending)
    monkeypatch.setattr(
        source_module,
        "open_completed_aihub538_download_directory",
        forbidden_open,
    )
    with pytest.raises(P8SealStateError):
        _execute_directory(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=tmp_path / "p8-source-run",
            store=store,
            seal=seal,
            pipeline=pipeline,
            references=_reference_populations(seal),
        )
    assert opened == 0
    assert P8SealStateStore(state_directory).load_access_transaction().access_count == 1
    assert not (state_directory / "p8_actual_source_final_commit.json").exists()


def test_cleanup_failure_is_sticky_incomplete_and_never_reopens_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)

    def fail_cleanup(**_kwargs):  # noqa: ANN003
        raise P8Aihub538SourceError("injected_cleanup_failure")

    monkeypatch.setattr(source_module, "_remove_private_child", fail_cleanup)
    output_directory = tmp_path / "p8-source-run"
    report = _execute_directory(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
        store=store,
        seal=seal,
        pipeline=_FrozenFixturePipeline(seal),
        references=_reference_populations(seal),
    )
    cleanup = store.load_actual_source_cleanup_state()
    commit = store.load_actual_source_final_commit()
    assert report.status is P8LockedEvaluationStatus.FAILED_CLOSED
    assert cleanup.status.value == "cleanup_incomplete"
    assert cleanup.restricted_work_directory_absent is False
    assert commit.result_status.value == "failed_closed"
    assert commit.promotion_authorized is False
    with pytest.raises((P8SealStateError, P8Aihub538SourceError)):
        _execute_directory(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=tmp_path / "retry-run",
            store=store,
            seal=seal,
            pipeline=_FrozenFixturePipeline(seal),
            references=_reference_populations(seal),
        )


def test_missing_final_commit_keeps_staged_report_noncanonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_directory = tmp_path / "completed-locked"
    _completed_locked_fixture(input_directory)
    key_path = _identity_key_file(tmp_path / "identity.key")
    _state_directory, store, seal = _state(tmp_path)

    def fail_commit(_commit):  # noqa: ANN001
        raise P8SealStateError("injected_final_commit_fsync_failure")

    monkeypatch.setattr(store, "create_actual_source_final_commit", fail_commit)
    with pytest.raises(P8SealStateError):
        _execute_directory(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=tmp_path / "p8-source-run",
            store=store,
            seal=seal,
            pipeline=_FrozenFixturePipeline(seal),
            references=_reference_populations(seal),
        )
    with pytest.raises(P8SealStateError, match="final_commit_required"):
        store.load_locked_report()
