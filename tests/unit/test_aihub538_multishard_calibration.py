"""Fixture-only contracts for staged AI Hub 538 multi-shard calibration."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import fields, replace
import argparse
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import threading
from types import ModuleType, SimpleNamespace
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest

from voice_coach.visual_feedback.adapters import (
    aihub538_training_dataset as training_dataset_adapter,
    aihub538_training_run_binding as training_run_binding_adapter,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY,
    AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
    Aihub538ArchiveError,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    aihub538_archive_security_policy_for_shard,
    verify_aihub538_nonlocked_shard_download,
)
from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
    Aihub538AdmittedArchiveBinding,
    _ts174_admission_manifest_sha256,
    _ts174_sample_selection_sha256,
    bind_aihub538_legacy_ts174_replay_aggregate,
)
from voice_coach.visual_feedback.adapters.aihub538_label_capacity import (
    Aihub538LabelCapacityAdapter,
)
from voice_coach.visual_feedback.adapters.aihub538_multishard_media import (
    Aihub538CalibrationShardSource,
    Aihub538CompositeCalibrationMediaSource,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (
    Aihub538TrainingAdmissionError,
    Aihub538TrainingDatasetAdapter,
)
from voice_coach.visual_feedback.adapters.aihub538_training_run_binding import (
    Aihub538AuthorizedTrainingRunBinding,
    open_aihub538_authorized_training_run,
)
from voice_coach.visual_feedback.models.aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityAuditReceipt,
    Aihub538LabelCapacityItem,
)
from voice_coach.visual_feedback.models import (
    aihub538_label_selection_authorization as label_selection_authorization_model,
    aihub538_multishard_admission as multishard_admission_model,
)
from voice_coach.visual_feedback.models.aihub538_cumulative_label_capacity import (
    Aihub538CumulativeLabelCapacityAuditReceipt,
)
from voice_coach.visual_feedback.models.aihub538_decision_calibration import (
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from voice_coach.visual_feedback.models.aihub538_multishard_admission import (
    Aihub538AuthorizedShardAdmission,
    Aihub538MultiShardAdmissionReceipt,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256,
    AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
    Aihub538NonlockedShardSpec,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingEvaluatorSmokeReceipt,
)
from voice_coach.visual_feedback.services import (
    aihub538_label_capacity_audit as capacity_audit,
)
from voice_coach.visual_feedback.services.aihub538_training_pipeline import (
    Aihub538TrainingPipeline,
    Aihub538TrainingPipelineError,
)
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (
    Aihub538CalibrationMediaItem,
    Aihub538DecisionCalibrationError,
    build_aihub538_calibration_split,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _pin_fixture_source_authorization(
    monkeypatch: pytest.MonkeyPatch,
    authorization: object,
) -> None:
    monkeypatch.setattr(
        label_selection_authorization_model,
        "AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256",
        authorization.receipt_sha256,
    )


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"fixture_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _label_record(*, speaker: str, angle: str, session: str) -> dict[str, object]:
    stem = f"lip_J_1_F_02_C{speaker}_{angle}_{session}"
    return {
        "dataSet": {
            "description": "fixture metadata",
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
        "Audio_env": {"Noise": 1},
        "Video_env": {"env": "indoor_light", "Angle": angle},
        "Sentence_info": [
            {
                "ID": "private",
                "topic": "private",
                "sentence_text": "비공개 fixture 전사",
                "start_time": 0.0,
                "end_time": 0.1,
            }
        ],
        "speaker_info": {
            "speaker_ID": f"C{speaker}",
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


def _zip_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED) as archive:
        for name, payload in members.items():
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _completed_download(
    directory: Path,
    *,
    file_keys: tuple[str, ...],
    groups: tuple[tuple[str, bytes], ...],
) -> Path:
    directory.mkdir(mode=0o700)
    outer = directory / "download.tar"
    with tarfile.open(outer, "w") as archive:
        for group_name, payload in groups:
            split = max(1, len(payload) // 2)
            for offset, part in ((0, payload[:split]), (split, payload[split:])):
                name = f"opaque/{group_name}/{group_name}.zip.part{offset}"
                info = tarfile.TarInfo(name)
                info.size = len(part)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(part))
    outer.chmod(0o600)
    payload = outer.read_bytes()
    receipt = directory / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": list(file_keys),
                "archive_size_bytes": len(payload),
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "download_completed_unix_seconds": 1,
                "credential_persisted": False,
                "archive_extracted": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    return directory


def _baseline_completed_download(directory: Path) -> Path:
    labels: dict[str, bytes] = {}
    sources: dict[str, bytes] = {}
    relative = "noise/C/F/baseline"
    for angle in "ABCDEFGHI":
        record = _label_record(speaker="032", angle=angle, session="010")
        stem = record["Video_info"]["video_Name"][:-4]
        labels[f"restricted/Label/TL174/{relative}/{stem}.json"] = json.dumps(
            [record], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        sources[f"restricted/Source/TS174/{relative}/{stem}.mp4"] = (
            b"\x00\x00\x00\x18ftypisomfixture"
        )
    a_stem = "lip_J_1_F_02_C032_A_010"
    sources[f"restricted/Source/TS174/{relative}/{a_stem}.wav"] = (
        b"RIFF\x08\x00\x00\x00WAVEfixture"
    )
    return _completed_download(
        directory,
        file_keys=("54725", "54944"),
        groups=(("a-label", _zip_payload(labels)), ("b-source", _zip_payload(sources))),
    )


def _tl175_completed_download(
    directory: Path,
    *,
    include_bounded_reversed_bbox: bool = False,
    speakers: tuple[str, ...] = ("101", "102", "103"),
    role_component: str = "TL175",
    file_key: str = "54726",
) -> Path:
    labels: dict[str, bytes] = {}
    relative = "noise/C/F/candidate"
    for speaker in speakers:
        for angle in "ABCDEFGHI":
            for index in range(8):
                session = f"{index:03d}"
                record = _label_record(
                    speaker=speaker,
                    angle=angle,
                    session=session,
                )
                if (
                    include_bounded_reversed_bbox
                    and speaker == speakers[0]
                    and angle == "C"
                    and index == 0
                ):
                    record["Bounding_box_info"]["Lip_bounding_box"][
                        "xtl_ytl_xbr_ybr"
                    ][0] = [180, 165, 120, 130]
                stem = record["Video_info"]["video_Name"][:-4]
                labels[
                    f"restricted/Label/{role_component}/{relative}/{stem}.json"
                ] = json.dumps(
                    [record], ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
    return _completed_download(
        directory,
        file_keys=(file_key,),
        groups=(("label-only", _zip_payload(labels)),),
    )


def _paired_ladder_download(
    directory: Path,
    *,
    speakers: tuple[str, ...],
    label_role_component: str,
    source_role_component: str,
    label_file_key: str,
    source_file_key: str,
    include_bounded_reversed_bbox: bool,
) -> Path:
    labels: dict[str, bytes] = {}
    sources: dict[str, bytes] = {}
    relative = "noise/C/F/candidate"
    for speaker in speakers:
        for angle in "ABCDEFGHI":
            for index in range(8):
                session = f"{index:03d}"
                record = _label_record(
                    speaker=speaker,
                    angle=angle,
                    session=session,
                )
                if (
                    include_bounded_reversed_bbox
                    and speaker == speakers[0]
                    and angle == "C"
                    and index == 0
                ):
                    record["Bounding_box_info"]["Lip_bounding_box"][
                        "xtl_ytl_xbr_ybr"
                    ][0] = [180, 165, 120, 130]
                stem = record["Video_info"]["video_Name"][:-4]
                labels[
                    f"restricted/Label/{label_role_component}/{relative}/{stem}.json"
                ] = json.dumps(
                    [record], ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                sources[
                    f"restricted/Source/{source_role_component}/{relative}/{stem}.mp4"
                ] = b"\x00\x00\x00\x18ftypisomfixture"
                a_stem = stem.rsplit("_", 2)[0] + f"_A_{session}"
                sources.setdefault(
                    f"restricted/Source/{source_role_component}/{relative}/{a_stem}.wav",
                    b"RIFF\x08\x00\x00\x00WAVEfixture",
                )
    return _completed_download(
        directory,
        file_keys=(label_file_key, source_file_key),
        groups=(
            ("a-label", _zip_payload(labels)),
            ("b-source", _zip_payload(sources)),
        ),
    )


def _capacity_aggregate(
    *,
    name: str,
    actors: tuple[str, ...],
    samples_per_actor_angle: int = 8,
    path_collision_token: str | None = None,
) -> Aihub538LabelCapacityAggregate:
    items: list[Aihub538LabelCapacityItem] = []
    paths: set[str] = set()
    for actor in actors:
        for angle in "ABCDEFGHI":
            for index in range(samples_per_actor_angle):
                sample_name = f"{name}:{actor}:{angle}:{index}"
                items.append(
                    Aihub538LabelCapacityItem(
                        sample_sha256=_digest(sample_name),
                        actor_token_sha256=_digest(f"actor:{actor}"),
                        attempt_token_sha256=_digest(
                            f"attempt:{name}:{actor}:{angle}:{index}"
                        ),
                        angle=angle,
                    )
                )
                paths.add(_digest(f"path:{sample_name}"))
    if path_collision_token is not None:
        paths.pop()
        paths.add(path_collision_token)
    return Aihub538LabelCapacityAggregate(
        label_archive_sha256=_digest(f"archive:{name}"),
        label_content_root_sha256=_digest(f"root:{name}"),
        identity_namespace_commitment_sha256=_digest("identity-namespace"),
        member_namespace_commitment_sha256=_digest("member-namespace"),
        items=tuple(items),
        relative_path_hmac_sha256=frozenset(paths),
    )


def _issue(
    baseline: Aihub538LabelCapacityAggregate,
    candidate: Aihub538LabelCapacityAggregate,
) -> Aihub538LabelCapacityAuditReceipt:
    return Aihub538LabelCapacityAuditReceipt.issue(
        baseline=baseline,
        candidate=candidate,
        baseline_admission_receipt_sha256=_digest("baseline-receipt"),
        baseline_admission_manifest_sha256=_digest("baseline-manifest"),
    )


def test_combined_label_capacity_uses_exact_quota_and_two_actors_per_partition() -> None:
    receipt = _issue(
        _capacity_aggregate(name="baseline", actors=("a", "b")),
        _capacity_aggregate(name="candidate", actors=("c", "d")),
    )

    assert receipt.actor_count == 4
    assert receipt.development_actor_count == 2
    assert receipt.calibration_actor_count == 2
    assert receipt.actor_overlap_count == 0
    assert receipt.attempt_overlap_count == 0
    assert all(
        item.development_selected_count == 8
        and item.calibration_selected_count == 8
        for item in receipt.angle_receipts
    )
    document = receipt.to_json_object()
    assert document["ts175_pair_admission_precondition_satisfied"] is True
    assert document["outcomes_observed"] is False
    assert document["baseline_source_partition_accessed"] is False
    serialized = json.dumps(document, sort_keys=True)
    assert "actor:a" not in serialized
    assert "attempt:" not in serialized


def test_combined_label_capacity_records_identity_collision_as_infeasible() -> None:
    receipt = _issue(
        _capacity_aggregate(name="baseline", actors=("a", "b")),
        _capacity_aggregate(name="candidate", actors=("a", "c")),
    )

    assert receipt.is_feasible is False
    assert receipt.cross_shard_actor_intersection_count == 1
    assert receipt.to_json_object()["status"] == "completed_infeasible"
    assert (
        receipt.to_json_object()[
            "ts175_pair_admission_precondition_satisfied"
        ]
        is False
    )


def test_combined_label_capacity_persists_path_collision_and_quota_shortfall() -> None:
    baseline = _capacity_aggregate(name="baseline", actors=("a", "b"))
    collision = next(iter(baseline.relative_path_hmac_sha256))
    collision_receipt = _issue(
        baseline,
        _capacity_aggregate(
            name="candidate",
            actors=("c", "d"),
            path_collision_token=collision,
        ),
    )
    assert collision_receipt.is_feasible is False
    assert collision_receipt.cross_shard_path_collision_count == 1

    shortfall = _issue(
        _capacity_aggregate(
            name="small-baseline",
            actors=("a", "b"),
            samples_per_actor_angle=1,
        ),
        _capacity_aggregate(
            name="small-candidate",
            actors=("c", "d"),
            samples_per_actor_angle=1,
        ),
    )
    assert shortfall.is_feasible is False
    assert shortfall.to_json_object()["status"] == "completed_infeasible"
    assert shortfall.to_json_object()["next_label_role_component"] == "TL176"
    assert shortfall.to_json_object()["next_label_file_key"] == "54727"
    assert all(
        item.development_available_count == 2
        and item.calibration_available_count == 2
        and item.development_selected_count == 2
        and item.calibration_selected_count == 2
        for item in shortfall.angle_receipts
    )
    assert (
        Aihub538LabelCapacityAuditReceipt.from_json_object(
            shortfall.to_json_object()
        )
        == shortfall
    )


def test_nonlocked_registry_rejects_locked_or_unlisted_keys() -> None:
    assert AIHUB538_TS174_TL174_SHARD.ordered_file_keys == ("54725", "54944")
    assert AIHUB538_TL175_CAPACITY_AUDIT_SHARD.ordered_file_keys == ("54726",)
    assert AIHUB538_TS175_TL175_SHARD.ordered_file_keys == ("54726", "54945")
    assert tuple(
        shard.ordered_file_keys[0]
        for shard in AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS
    ) == ("54726", "54727", "54728", "54729", "54730", "54732")
    assert tuple(
        shard.ordered_file_keys[1]
        for shard in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS
    ) == ("54945", "54946", "54947", "54948", "54949", "54951")
    assert len(AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256) == 64
    with pytest.raises(ValueError, match="not_allowlisted"):
        Aihub538NonlockedShardSpec(
            shard_id="locked",
            stage="paired_training_calibration",
            source_role_component="VS11",
            label_role_component="VL11",
            ordered_file_keys=("55114", "55082"),
        )


def test_authorized_prefix_uses_narrow_large_archive_policy() -> None:
    assert DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY.maximum_download_bytes == (
        64 * 1024**3
    )
    assert (
        AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY.maximum_download_bytes
        == 128 * 1024**3
    )
    assert (
        AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY.maximum_tar_uncompressed_bytes
        == 128 * 1024**3
    )
    assert (
        AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY.maximum_merged_zip_bytes
        == 128 * 1024**3
    )
    widened_fields = {
        "maximum_download_bytes",
        "maximum_tar_uncompressed_bytes",
        "maximum_merged_zip_bytes",
    }
    assert all(
        getattr(AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY, item.name)
        == getattr(DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY, item.name)
        for item in fields(DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY)
        if item.name not in widened_fields
    )
    assert (
        aihub538_archive_security_policy_for_shard(
            AIHUB538_TS174_TL174_SHARD
        )
        is DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    )
    assert (
        aihub538_archive_security_policy_for_shard(
            AIHUB538_TL175_CAPACITY_AUDIT_SHARD
        )
        is DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    )
    assert (
        aihub538_archive_security_policy_for_shard(
            AIHUB538_TS175_TL175_SHARD
        )
        is AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY
    )
    assert (
        aihub538_archive_security_policy_for_shard(
            AIHUB538_TS176_TL176_SHARD
        )
        is AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY
    )
    assert all(
        aihub538_archive_security_policy_for_shard(shard)
        is DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        for shard in AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[:2]
    )
    assert all(
        aihub538_archive_security_policy_for_shard(shard)
        is AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY
        for shard in AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:]
    )
    assert (
        Aihub538TrainingPipeline(
            shard_spec=AIHUB538_TS175_TL175_SHARD
        )._archive_policy
        is AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY
    )
    assert (
        Aihub538TrainingDatasetAdapter(
            shard_spec=AIHUB538_TS175_TL175_SHARD
        )._archive_policy
        is AIHUB538_AUTHORIZED_PREFIX_ARCHIVE_SECURITY_POLICY
    )
    assert all(
        aihub538_archive_security_policy_for_shard(shard)
        is AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY
        for shard in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:]
    )
    assert (
        AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY.maximum_download_bytes
        == 128 * 1024**3
    )


def test_authorized_prefix_download_rejects_more_than_128_gib(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "download.tar"
    with archive.open("wb") as stream:
        stream.truncate(128 * 1024**3 + 1)
    archive.chmod(0o600)
    receipt = tmp_path / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": ["54726", "54945"],
                "archive_size_bytes": archive.stat().st_size,
                "archive_sha256": "0" * 64,
                "download_completed_unix_seconds": 1,
                "credential_persisted": False,
                "archive_extracted": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    with ExitStack() as stack:
        archive_descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
        receipt_descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, archive_descriptor)
        stack.callback(os.close, receipt_descriptor)
        with pytest.raises(
            Aihub538ArchiveError,
            match="aihub538_download_archive_descriptor_invalid",
        ):
            verify_aihub538_nonlocked_shard_download(
                archive_descriptor=archive_descriptor,
                receipt_descriptor=receipt_descriptor,
                shard_spec=AIHUB538_TS175_TL175_SHARD,
            )


def test_cumulative_prefix_recomputes_balanced_assignment_and_exact_quota() -> None:
    baseline = _capacity_aggregate(
        name="baseline",
        actors=("a", "b"),
        samples_per_actor_angle=1,
    )
    tl175 = _capacity_aggregate(
        name="tl175",
        actors=("c", "d"),
        samples_per_actor_angle=1,
    )
    prior = _issue(baseline, tl175)
    assert prior.is_feasible is False
    tl176 = _capacity_aggregate(
        name="tl176",
        actors=("e", "f", "g", "h"),
    )

    receipt = Aihub538CumulativeLabelCapacityAuditReceipt.issue_tl175_tl176_prefix(
        baseline=baseline,
        tl175=tl175,
        tl176=tl176,
        prior_tl175_receipt=prior,
        baseline_admission_receipt_sha256=_digest("baseline-receipt"),
        baseline_admission_manifest_sha256=_digest("baseline-manifest"),
    )

    assert receipt.is_feasible is True
    assert receipt.development_actor_count == 4
    assert receipt.calibration_actor_count == 4
    assert all(
        item.development_selected_count == 8
        and item.calibration_selected_count == 8
        for item in receipt.angle_receipts
    )
    document = receipt.to_json_object()
    assert document["evaluated_label_prefix"] == ["TL175", "TL176"]
    assert document["authorized_source_role_prefix"] == ["TS175", "TS176"]
    assert document["authorized_source_file_key_prefix"] == ["54945", "54946"]
    assert (
        Aihub538CumulativeLabelCapacityAuditReceipt.from_json_object(document)
        == receipt
    )


def test_cumulative_prefix_collision_is_infeasible_without_identifiers() -> None:
    baseline = _capacity_aggregate(
        name="baseline",
        actors=("a", "b"),
        samples_per_actor_angle=1,
    )
    tl175 = _capacity_aggregate(
        name="tl175",
        actors=("c", "d"),
        samples_per_actor_angle=1,
    )
    prior = _issue(baseline, tl175)
    tl176 = _capacity_aggregate(
        name="tl176",
        actors=("c", "e", "f", "g"),
    )
    receipt = Aihub538CumulativeLabelCapacityAuditReceipt.issue_tl175_tl176_prefix(
        baseline=baseline,
        tl175=tl175,
        tl176=tl176,
        prior_tl175_receipt=prior,
        baseline_admission_receipt_sha256=_digest("baseline-receipt"),
        baseline_admission_manifest_sha256=_digest("baseline-manifest"),
    )
    assert receipt.is_feasible is False
    assert receipt.cross_shard_actor_intersection_count == 1
    serialized = json.dumps(receipt.to_json_object(), sort_keys=True)
    assert "actor:c" not in serialized


def test_label_audit_download_verifier_requires_exact_single_key(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "download.tar"
    archive.write_bytes(b"fixture-completed-download")
    archive.chmod(0o600)
    payload = archive.read_bytes()
    receipt = tmp_path / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "voice_coach.aihub_secure_download_receipt.v1",
                "dataset_key": "538",
                "file_keys": ["54726"],
                "archive_size_bytes": len(payload),
                "archive_sha256": hashlib.sha256(payload).hexdigest(),
                "download_completed_unix_seconds": 1,
                "credential_persisted": False,
                "archive_extracted": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    receipt.chmod(0o400)
    with ExitStack() as stack:
        archive_descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
        receipt_descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, archive_descriptor)
        stack.callback(os.close, receipt_descriptor)
        verified = verify_aihub538_nonlocked_shard_download(
            archive_descriptor=archive_descriptor,
            receipt_descriptor=receipt_descriptor,
            shard_spec=AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
        )
        assert verified.shard_spec == AIHUB538_TL175_CAPACITY_AUDIT_SHARD

    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["file_keys"] = ["54726", "54945"]
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(document), encoding="utf-8")
    receipt.chmod(0o400)
    with ExitStack() as stack:
        archive_descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
        receipt_descriptor = os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, archive_descriptor)
        stack.callback(os.close, receipt_descriptor)
        with pytest.raises(Aihub538ArchiveError, match="contract_mismatch"):
            verify_aihub538_nonlocked_shard_download(
                archive_descriptor=archive_descriptor,
                receipt_descriptor=receipt_descriptor,
                shard_spec=AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
            )


def test_capacity_receipt_reload_rejects_duplicate_keys_and_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _issue(
        _capacity_aggregate(name="baseline", actors=("a", "b")),
        _capacity_aggregate(name="candidate", actors=("c", "d")),
    )
    path = tmp_path / "capacity.json"
    path.write_text(
        json.dumps(receipt.to_json_object(), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert (
            capacity_audit.load_aihub538_label_capacity_receipt_descriptor(
                descriptor
            )
            == receipt
        )
    finally:
        os.close(descriptor)

    path.chmod(0o600)
    path.write_text(
        '{"receipt_sha256":"' + "0" * 64 + '","receipt_sha256":"'
        + "1" * 64 + '"}',
        encoding="utf-8",
    )
    path.chmod(0o400)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(
            capacity_audit.Aihub538LabelCapacityPipelineError,
            match="receipt_reload_failed",
        ):
            capacity_audit.load_aihub538_label_capacity_receipt_descriptor(
                descriptor
            )
    finally:
        os.close(descriptor)

    path.chmod(0o600)
    path.write_text(
        json.dumps(receipt.to_json_object(), sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o400)
    original_pread = capacity_audit.os.pread
    mutated = False

    def mutate_after_read(fd: int, count: int, offset: int) -> bytes:
        nonlocal mutated
        payload = original_pread(fd, count, offset)
        if not mutated:
            mutated = True
            path.chmod(0o600)
            with path.open("ab") as target:
                target.write(b" ")
            path.chmod(0o400)
        return payload

    monkeypatch.setattr(capacity_audit.os, "pread", mutate_after_read)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(
            capacity_audit.Aihub538LabelCapacityPipelineError,
            match="receipt_reload_failed",
        ):
            capacity_audit.load_aihub538_label_capacity_receipt_descriptor(
                descriptor
            )
    finally:
        os.close(descriptor)


def test_transactional_tl175_audit_binds_completed_ts174_baseline(
    tmp_path: Path,
) -> None:
    baseline_input = _baseline_completed_download(tmp_path / "baseline-input")
    candidate_input = _tl175_completed_download(
        tmp_path / "candidate-input",
        include_bounded_reversed_bbox=True,
    )
    identity_key = tmp_path / "identity.key"
    identity_key.write_bytes(b"k" * 32)
    assert identity_key.stat().st_size == 32
    identity_key.chmod(0o400)
    baseline_output = tmp_path / "baseline-run"
    with ExitStack() as stack:
        key_descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=baseline_input,
            identity_key_descriptor=key_descriptor,
            output_directory=baseline_output,
            dataset_access_permission_confirmed=True,
        )

    assert stat.S_IMODE((baseline_output / "candidate-0.zip").stat().st_mode) == 0o600
    # Candidate indices are merge-order artifacts, not semantic roles.  Flip
    # both files to prove baseline admission resolves the label from receipt
    # archive/content commitments instead of trusting candidate-0.
    temporary_candidate = baseline_output / "candidate-swap.zip"
    (baseline_output / "candidate-0.zip").replace(temporary_candidate)
    (baseline_output / "candidate-1.zip").replace(
        baseline_output / "candidate-0.zip"
    )
    temporary_candidate.replace(baseline_output / "candidate-1.zip")
    audit_output = tmp_path / "audit-run"
    with ExitStack() as stack:
        key_descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        receipt = capacity_audit.Aihub538LabelCapacityAuditPipeline().run_from_completed_directory(
            input_directory=candidate_input,
            baseline_admission_directory=baseline_output,
            identity_key_descriptor=key_descriptor,
            output_directory=audit_output,
            dataset_access_permission_confirmed=True,
        )

    assert receipt.actor_count == 4
    assert receipt.cross_shard_actor_intersection_count == 0
    assert receipt.cross_shard_attempt_intersection_count == 0
    assert receipt.development_actor_count == 2
    assert receipt.calibration_actor_count == 2
    assert receipt.candidate_face_reversed_bbox_unavailable_count == 0
    assert receipt.candidate_lip_reversed_bbox_unavailable_count == 1
    assert receipt.candidate_reversed_bbox_affected_label_count == 1
    assert receipt.to_json_object()["reversed_bbox_coordinates_reordered"] is False
    assert receipt.to_json_object()["bbox_outcomes_used_for_capacity_quota"] is False
    assert all(
        item.development_selected_count == 8
        and item.calibration_selected_count == 8
        for item in receipt.angle_receipts
    )
    assert stat.S_IMODE(audit_output.stat().st_mode) == 0o700
    assert stat.S_IMODE((audit_output / "label.zip").stat().st_mode) == 0o600
    for path in audit_output.glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
    serialized = json.dumps(receipt.to_json_object(), ensure_ascii=False)
    assert "C032" not in serialized
    assert "C101" not in serialized
    assert "비공개" not in serialized
    assert str(tmp_path) not in serialized


def test_transactional_tl175_infeasible_audit_persists_reloadable_receipt(
    tmp_path: Path,
) -> None:
    baseline_input = _baseline_completed_download(tmp_path / "baseline-input")
    candidate_input = _tl175_completed_download(
        tmp_path / "candidate-input",
        speakers=("101",),
    )
    identity_key = tmp_path / "identity.key"
    identity_key.write_bytes(b"k" * 32)
    identity_key.chmod(0o400)
    baseline_output = tmp_path / "baseline-run"
    with ExitStack() as stack:
        key_descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=baseline_input,
            identity_key_descriptor=key_descriptor,
            output_directory=baseline_output,
            dataset_access_permission_confirmed=True,
        )

    audit_output = tmp_path / "audit-run"
    with ExitStack() as stack:
        key_descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, key_descriptor)
        receipt = capacity_audit.Aihub538LabelCapacityAuditPipeline().run_from_completed_directory(
            input_directory=candidate_input,
            baseline_admission_directory=baseline_output,
            identity_key_descriptor=key_descriptor,
            output_directory=audit_output,
            dataset_access_permission_confirmed=True,
        )

    assert receipt.is_feasible is False
    assert audit_output.is_dir()
    capacity_path = audit_output / "50-label-capacity-audit.json"
    completion = json.loads(
        (audit_output / "99-completion.json").read_text(encoding="utf-8")
    )
    assert completion["status"] == "completed_infeasible"
    assert completion["source_download_authorized"] is False
    descriptor = os.open(capacity_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert (
            capacity_audit.load_aihub538_label_capacity_receipt_descriptor(
                descriptor
            )
            == receipt
        )
    finally:
        os.close(descriptor)


def test_transactional_tl176_cumulative_prefix_revalidates_tl175_lineage(
    tmp_path: Path,
) -> None:
    baseline_input = _baseline_completed_download(tmp_path / "baseline-input")
    tl175_input = _tl175_completed_download(
        tmp_path / "tl175-input",
        speakers=("101",),
        include_bounded_reversed_bbox=True,
    )
    tl176_input = _tl175_completed_download(
        tmp_path / "tl176-input",
        speakers=("201", "202", "203"),
        role_component="TL176",
        file_key="54727",
        include_bounded_reversed_bbox=True,
    )
    identity_key = tmp_path / "identity.key"
    identity_key.write_bytes(b"k" * 32)
    identity_key.chmod(0o400)
    baseline_output = tmp_path / "baseline-run"
    with ExitStack() as stack:
        descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=baseline_input,
            identity_key_descriptor=descriptor,
            output_directory=baseline_output,
            dataset_access_permission_confirmed=True,
        )

    tl175_output = tmp_path / "tl175-run"
    with ExitStack() as stack:
        descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        prior = capacity_audit.Aihub538LabelCapacityAuditPipeline().run_from_completed_directory(
            input_directory=tl175_input,
            baseline_admission_directory=baseline_output,
            identity_key_descriptor=descriptor,
            output_directory=tl175_output,
            dataset_access_permission_confirmed=True,
        )
    assert prior.is_feasible is False

    tl176_output = tmp_path / "tl176-run"
    with ExitStack() as stack:
        descriptor = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        receipt = capacity_audit.Aihub538LabelCapacityAuditPipeline(
            audit_shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1]
        ).run_from_completed_directory(
            input_directory=tl176_input,
            baseline_admission_directory=baseline_output,
            prior_prefix_audit_directory=tl175_output,
            identity_key_descriptor=descriptor,
            output_directory=tl176_output,
            dataset_access_permission_confirmed=True,
        )

    assert receipt.is_feasible is True
    assert receipt.prior_prefix_receipt_sha256 == prior.receipt_sha256
    assert receipt.metadata_excluded_label_count == 2
    assert receipt.historical_capacity_receipt_sha256 != receipt.receipt_sha256
    assert all(
        item.development_selected_count == 8
        and item.calibration_selected_count == 8
        for item in receipt.post_exclusion_angle_receipts
    )
    assert tuple(
        item.label_role_component for item in receipt.prefix_lineage
    ) == ("TL175", "TL176")
    assert stat.S_IMODE((tl176_output / "label.zip").stat().st_mode) == 0o600
    path = tl176_output / "50-label-capacity-audit.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert (
            capacity_audit.load_aihub538_label_selection_authorization_receipt_descriptor(
                descriptor
            )
            == receipt
        )
    finally:
        os.close(descriptor)


class _FixtureOwnedMediaSource:
    def __init__(
        self,
        *,
        marker: str,
        inventory: tuple[Aihub538CalibrationMediaItem, ...],
    ) -> None:
        self.marker = marker
        self._inventory = inventory
        self.measured: list[Aihub538CalibrationMediaItem] = []

    @property
    def inventory(self) -> tuple[Aihub538CalibrationMediaItem, ...]:
        return self._inventory

    def measure(self, item: Aihub538CalibrationMediaItem):  # noqa: ANN201
        self.measured.append(item)
        raise RuntimeError(f"owned-by-{self.marker}")

    def __enter__(self) -> "_FixtureOwnedMediaSource":
        return self

    def __exit__(self, *args: object) -> None:
        del args


def _produce_three_shard_transactional_runs(
    root: Path,
    *,
    legacy_baseline: bool = False,
) -> tuple[Path, object, tuple[Path, Path, Path]]:
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    key = root / "identity.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o400)
    baseline_input = _baseline_completed_download(root / "baseline-input")
    tl175_labels = _tl175_completed_download(
        root / "tl175-label-input",
        speakers=("101",),
        include_bounded_reversed_bbox=True,
    )
    tl176_labels = _tl175_completed_download(
        root / "tl176-label-input",
        speakers=("201", "202", "203"),
        role_component="TL176",
        file_key="54727",
        include_bounded_reversed_bbox=True,
    )
    baseline_run = root / "baseline-run"
    with ExitStack() as stack:
        descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=baseline_input,
            identity_key_descriptor=descriptor,
            output_directory=baseline_run,
            dataset_access_permission_confirmed=True,
        )
    if legacy_baseline:
        _convert_baseline_run_to_legacy_transitional(
            run=baseline_run,
            identity_key=key.read_bytes(),
        )
    tl175_audit = root / "tl175-audit"
    with ExitStack() as stack:
        descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        capacity_audit.Aihub538LabelCapacityAuditPipeline().run_from_completed_directory(
            input_directory=tl175_labels,
            baseline_admission_directory=baseline_run,
            identity_key_descriptor=descriptor,
            output_directory=tl175_audit,
            dataset_access_permission_confirmed=True,
        )
    tl176_audit = root / "tl176-audit"
    with ExitStack() as stack:
        descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        authorization = capacity_audit.Aihub538LabelCapacityAuditPipeline(
            audit_shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1]
        ).run_from_completed_directory(
            input_directory=tl176_labels,
            baseline_admission_directory=baseline_run,
            prior_prefix_audit_directory=tl175_audit,
            identity_key_descriptor=descriptor,
            output_directory=tl176_audit,
            dataset_access_permission_confirmed=True,
        )
    assert authorization.is_feasible

    pair_inputs = (
        _paired_ladder_download(
            root / "ts175-input",
            speakers=("101",),
            label_role_component="TL175",
            source_role_component="TS175",
            label_file_key="54726",
            source_file_key="54945",
            include_bounded_reversed_bbox=True,
        ),
        _paired_ladder_download(
            root / "ts176-input",
            speakers=("201", "202", "203"),
            label_role_component="TL176",
            source_role_component="TS176",
            label_file_key="54727",
            source_file_key="54946",
            include_bounded_reversed_bbox=True,
        ),
    )
    pair_runs = (root / "ts175-run", root / "ts176-run")
    pin_name = "AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256"
    original_release_pin = getattr(label_selection_authorization_model, pin_name)
    setattr(
        label_selection_authorization_model,
        pin_name,
        authorization.receipt_sha256,
    )
    try:
        for input_directory, output_directory, shard_spec in zip(
            pair_inputs,
            pair_runs,
            (AIHUB538_TS175_TL175_SHARD, AIHUB538_TS176_TL176_SHARD),
            strict=True,
        ):
            with ExitStack() as stack:
                descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
                stack.callback(os.close, descriptor)
                evidence = Aihub538TrainingPipeline(
                    shard_spec=shard_spec
                ).run_from_completed_directory(
                    input_directory=input_directory,
                    identity_key_descriptor=descriptor,
                    output_directory=output_directory,
                    dataset_access_permission_confirmed=True,
                    capacity_audit_receipt=authorization,
                )
            assert (
                evidence.capacity_audit_receipt_sha256
                == authorization.receipt_sha256
            )
    finally:
        setattr(
            label_selection_authorization_model,
            pin_name,
            original_release_pin,
        )
    return key, authorization, (baseline_run, *pair_runs)


def _write_private_json(path: Path, document: object) -> None:
    path.chmod(0o600)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o400)


def _convert_baseline_run_to_legacy_transitional(
    *,
    run: Path,
    identity_key: bytes,
) -> Aihub538TrainingAdmissionReceipt:
    with open_aihub538_authorized_training_run(
        directory=run,
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        capacity_receipt=None,
    ) as binding:
        roles = binding.candidate_role_binding
        current = Aihub538TrainingDatasetAdapter().admit(
            label_zip_descriptor=roles.label_zip_descriptor,
            source_zip_descriptor=roles.source_zip_descriptor,
            identity_hash_key=identity_key,
        )
    legacy_selection = _ts174_sample_selection_sha256(
        current,
        admission_policy_sha256=(
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        ),
    )
    legacy = replace(
        current,
        admission_policy_sha256=(
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        ),
        sample_selection_sha256=legacy_selection,
        admission_manifest_sha256=_ts174_admission_manifest_sha256(
            current,
            sample_selection_sha256=legacy_selection,
        ),
    )
    admission = Aihub538TrainingAdmissionReceipt.issue(legacy)
    smoke = Aihub538TrainingEvaluatorSmokeReceipt.issue(
        admission_receipt=admission,
        aggregate=legacy,
    )
    completion_path = run / "99-completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["schema_version"] = (
        "voice_coach.aihub538_training_pipeline_receipt.v1"
    )
    completion["dataset_admission"] = admission.to_json_object()
    completion["metadata_smoke"] = smoke.to_json_object()
    completion["completion_receipt_sha256"] = sha256_document(
        {
            key: value
            for key, value in completion.items()
            if key != "completion_receipt_sha256"
        }
    )
    _write_private_json(run / "50-dataset-admission.json", admission.to_json_object())
    _write_private_json(run / "60-metadata-smoke.json", smoke.to_json_object())
    _write_private_json(completion_path, completion)
    return admission


def _authorized_shard_and_media(
    *,
    stack: ExitStack,
    run: Path,
    shard_spec: Aihub538NonlockedShardSpec,
    capacity_receipt: object,
    identity_key: bytes,
) -> tuple[Aihub538AuthorizedShardAdmission, _FixtureOwnedMediaSource]:
    binding = stack.enter_context(
        open_aihub538_authorized_training_run(
            directory=run,
            shard_spec=shard_spec,
            capacity_receipt=(
                None
                if shard_spec == AIHUB538_TS174_TL174_SHARD
                else capacity_receipt
            ),
        )
    )
    roles = binding.candidate_role_binding
    prevalidated = Aihub538TrainingDatasetAdapter(
        shard_spec=shard_spec
    ).admit_prevalidated(
        archive_binding=binding.admitted_archive_binding,
        admission_receipt=binding.admission_receipt,
        identity_hash_key=identity_key,
    )
    aggregate = prevalidated.aggregate
    label_population = Aihub538LabelCapacityAdapter(
        shard_spec=shard_spec
    ).audit(
        label_zip_descriptor=roles.label_zip_descriptor,
        identity_hash_key=identity_key,
    )
    private_by_token = {
        item.sample_sha256: item for item in label_population.items
    }
    inventory = tuple(
        Aihub538CalibrationMediaItem(
            sample_sha256=sample.sample_sha256,
            actor_token_sha256=private_by_token[
                sample.label_selection_token_sha256
            ].actor_token_sha256,
            attempt_token_sha256=private_by_token[
                sample.label_selection_token_sha256
            ].attempt_token_sha256,
            angle=sample.angle,
            metadata_bbox_observation_available=(
                not bool(
                    sample.face_reversed_bbox_unavailable_count
                    or sample.lip_reversed_bbox_unavailable_count
                )
            ),
            label_selection_token_sha256=(
                sample.label_selection_token_sha256
            ),
        )
        for sample in aggregate.samples
    )
    if (
        binding.admission_receipt.admission_policy_sha256
        == AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
    ):
        authorization = binding.authorize(
            prevalidated.bind_legacy_ts174_replay()
        )
    else:
        authorization = binding.authorize(prevalidated)
    return (
        authorization,
        _FixtureOwnedMediaSource(
            marker=shard_spec.shard_id,
            inventory=inventory,
        ),
    )


def test_three_shard_transactional_composite_split_and_scope_guards(
    tmp_path: Path,
) -> None:
    key_path, capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path
    )
    identity_key = key_path.read_bytes()
    with ExitStack() as stack:
        rows = tuple(
            _authorized_shard_and_media(
                stack=stack,
                run=run,
                shard_spec=shard_spec,
                capacity_receipt=capacity_receipt,
                identity_key=identity_key,
            )
            for run, shard_spec in zip(
                runs,
                (
                    AIHUB538_TS174_TL174_SHARD,
                    AIHUB538_TS175_TL175_SHARD,
                    AIHUB538_TS176_TL176_SHARD,
                ),
                strict=True,
            )
        )
        authorizations = tuple(item[0] for item in rows)
        media_sources = tuple(item[1] for item in rows)
        multi = Aihub538MultiShardAdmissionReceipt.issue(
            ts174=authorizations[0],
            ts175=authorizations[1],
            ts176=authorizations[2],
            capacity_receipt=capacity_receipt,
        )
        sources = tuple(
            Aihub538CalibrationShardSource(
                shard_spec=shard_spec,
                authorization=authorization,
                media_source=media_source,
            )
            for shard_spec, authorization, media_source in zip(
                (
                    AIHUB538_TS174_TL174_SHARD,
                    AIHUB538_TS175_TL175_SHARD,
                    AIHUB538_TS176_TL176_SHARD,
                ),
                authorizations,
                media_sources,
                strict=True,
            )
        )
        composite = Aihub538CompositeCalibrationMediaSource(
            sources=sources,
            admission_receipt=multi,
            capacity_receipt=capacity_receipt,
        )
        split = build_aihub538_calibration_split(
            items=composite.inventory,
            admission_receipt=multi,
            protocol=MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
        )

        assert multi.to_json_object()["schema_version"].endswith(".v2")
        assert split.development_actor_count >= 2
        assert split.calibration_actor_count >= 2
        assert split.metadata_excluded_item_count == 2
        assert len(split.development) == 72
        assert len(split.calibration) == 72
        owned_item = media_sources[1].inventory[0]
        with pytest.raises(RuntimeError, match="owned-by-ts175_tl175"):
            composite.measure(owned_item)
        assert media_sources[1].measured == [owned_item]

        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="single_shard_scope_invalid",
        ):
            build_aihub538_calibration_split(
                items=media_sources[1].inventory,
                admission_receipt=authorizations[1].admission_receipt,
                protocol=DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
            )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="frozen_protocol_mismatch",
        ):
            build_aihub538_calibration_split(
                items=composite.inventory,
                admission_receipt=multi,
                protocol=DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
            )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="composite_source_set_invalid",
        ):
            Aihub538CompositeCalibrationMediaSource(
                sources=sources[:2],
                admission_receipt=multi,
                capacity_receipt=capacity_receipt,
            )


def test_transitional_legacy_ts174_run_rebinds_into_exact_three_shard_lineage(
    tmp_path: Path,
) -> None:
    key_path, capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path / "legacy-transactional",
        legacy_baseline=True,
    )
    completion = json.loads(
        (runs[0] / "99-completion.json").read_text(encoding="utf-8")
    )
    assert completion["schema_version"] == (
        "voice_coach.aihub538_training_pipeline_receipt.v1"
    )
    assert "split_archive_merge" in completion
    assert "inner_tar_repack" in completion

    with ExitStack() as stack:
        rows = tuple(
            _authorized_shard_and_media(
                stack=stack,
                run=run,
                shard_spec=shard_spec,
                capacity_receipt=capacity_receipt,
                identity_key=key_path.read_bytes(),
            )
            for run, shard_spec in zip(
                runs,
                (
                    AIHUB538_TS174_TL174_SHARD,
                    AIHUB538_TS175_TL175_SHARD,
                    AIHUB538_TS176_TL176_SHARD,
                ),
                strict=True,
            )
        )
        authorizations = tuple(row[0] for row in rows)
        assert authorizations[0].admission_receipt.admission_policy_sha256 == (
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        )
        multi = Aihub538MultiShardAdmissionReceipt.issue(
            ts174=authorizations[0],
            ts175=authorizations[1],
            ts176=authorizations[2],
            capacity_receipt=capacity_receipt,
        )
        sources = tuple(
            Aihub538CalibrationShardSource(
                shard_spec=shard_spec,
                authorization=authorization,
                media_source=row[1],
            )
            for shard_spec, authorization, row in zip(
                (
                    AIHUB538_TS174_TL174_SHARD,
                    AIHUB538_TS175_TL175_SHARD,
                    AIHUB538_TS176_TL176_SHARD,
                ),
                authorizations,
                rows,
                strict=True,
            )
        )
        composite = Aihub538CompositeCalibrationMediaSource(
            sources=sources,
            admission_receipt=multi,
            capacity_receipt=capacity_receipt,
        )
        split = build_aihub538_calibration_split(
            items=composite.inventory,
            admission_receipt=multi,
            protocol=MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
        )
        assert len(split.development) == len(split.calibration) == 72


def test_pair_pipeline_rejects_self_digested_but_unreleased_v5(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "release-pin"
    key_path, authorization, _runs = _produce_three_shard_transactional_runs(
        fixture_root
    )
    assert authorization.receipt_sha256 != (
        getattr(
            label_selection_authorization_model,
            "AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256",
        )
    )
    descriptor = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
    output = fixture_root / "unreleased-pair-run"
    try:
        with pytest.raises(
            Aihub538TrainingPipelineError,
            match="aihub538_capacity_audit_release_pin_mismatch",
        ):
            Aihub538TrainingPipeline(
                shard_spec=AIHUB538_TS175_TL175_SHARD
            ).run_from_completed_directory(
                input_directory=fixture_root / "ts175-input",
                identity_key_descriptor=descriptor,
                output_directory=output,
                dataset_access_permission_confirmed=True,
                capacity_audit_receipt=authorization,
            )
    finally:
        os.close(descriptor)
    assert not output.exists()


def test_historical_ts174_v1_completion_dispatches_and_stage_tamper_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "historical"
    root.mkdir(mode=0o700)
    key = root / "identity.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o400)
    completed = _baseline_completed_download(root / "input")
    run = root / "run"
    descriptor = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=completed,
            identity_key_descriptor=descriptor,
            output_directory=run,
            dataset_access_permission_confirmed=True,
        )
    finally:
        os.close(descriptor)
    legacy_receipt = _convert_baseline_run_to_legacy_transitional(
        run=run,
        identity_key=key.read_bytes(),
    )
    completion_path = run / "99-completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion.pop("split_archive_merge")
    completion.pop("inner_tar_repack")
    completion["completion_receipt_sha256"] = sha256_document(
        {
            name: value
            for name, value in completion.items()
            if name != "completion_receipt_sha256"
        }
    )
    _write_private_json(completion_path, completion)
    # A historical v1 producer persisted the legacy split-zip stage shape;
    # current producers persist the generalized v2 stage under the same file
    # name, so the fixture must project the stage as well as the completion.
    _write_private_json(
        run / "20-split-zip-merge.json",
        completion["split_zip_merge"],
    )

    with open_aihub538_authorized_training_run(
        directory=run,
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        capacity_receipt=None,
    ) as binding:
        roles = binding.candidate_role_binding
        current = Aihub538TrainingDatasetAdapter().admit_prevalidated(
            archive_binding=binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            identity_hash_key=key.read_bytes(),
        )
        rebound = current.bind_legacy_ts174_replay()
        authorization = binding.authorize(rebound)
        assert authorization.admission_receipt == legacy_receipt

    stage_path = run / "20-split-zip-merge.json"
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    stage["archive_count"] = 99
    _write_private_json(stage_path, stage)
    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_authorized_training_run_invalid",
    ):
        with open_aihub538_authorized_training_run(
            directory=run,
            shard_spec=AIHUB538_TS174_TL174_SHARD,
            capacity_receipt=None,
        ):
            pass


def test_authorized_run_capability_rejects_replace_fork_and_context_replay(
    tmp_path: Path,
) -> None:
    assert not hasattr(
        training_run_binding_adapter,
        "_issue_aihub538_authorized_training_run_binding",
    )
    key_path, _capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path
    )
    retained: Aihub538AuthorizedTrainingRunBinding
    retained_aggregate = None
    retained_authorization: Aihub538AuthorizedShardAdmission
    role_paths: tuple[tuple[Path, int], ...]
    with open_aihub538_authorized_training_run(
        directory=runs[0],
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        capacity_receipt=None,
    ) as binding:
        prevalidated = Aihub538TrainingDatasetAdapter().admit_prevalidated(
            archive_binding=binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            identity_hash_key=key_path.read_bytes(),
        )
        aggregate = prevalidated.aggregate

        with pytest.raises(
            ValueError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            replace(binding)
        with pytest.raises(
            ValueError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            Aihub538AuthorizedTrainingRunBinding(
                shard_spec=binding.shard_spec,
                candidate_role_binding=binding.candidate_role_binding,
                admission_receipt=binding.admission_receipt,
                pipeline_completion_receipt_sha256=(
                    binding.pipeline_completion_receipt_sha256
                ),
                capacity_audit_receipt_sha256=(
                    binding.capacity_audit_receipt_sha256
                ),
                admitted_archive_binding=binding.admitted_archive_binding,
                _capability=object(),  # type: ignore[arg-type]
            )
        for changes in (
            {
                "pipeline_completion_receipt_sha256": _digest(
                    "forged-completion"
                )
            },
            {"capacity_audit_receipt_sha256": _digest("forged-capacity")},
            {
                "candidate_role_binding": replace(
                    binding.candidate_role_binding
                )
            },
            {"shard_spec": replace(binding.shard_spec)},
        ):
            with pytest.raises(
                ValueError,
                match="aihub538_authorized_training_run_capability_invalid",
            ):
                replace(binding, **changes)

        member_set = aggregate.restricted_member_set
        assert member_set is not None
        stale_samples = (
            replace(
                aggregate.samples[0],
                sample_sha256=_digest("stale-sample-digest"),
            ),
            *aggregate.samples[1:],
        )
        aggregate_replacements = (
            replace(
                aggregate,
                restricted_member_set=replace(member_set),
            ),
            replace(
                aggregate,
                restricted_identity_set=replace(
                    aggregate.restricted_identity_set
                ),
            ),
            replace(aggregate, samples=stale_samples),
        )
        for forged_aggregate in aggregate_replacements:
            with pytest.raises(
                ValueError,
                match="aihub538_prevalidated_aggregate_capability_invalid",
            ):
                replace(
                    prevalidated,
                    aggregate=forged_aggregate,
                )
            with pytest.raises(
                Aihub538DecisionCalibrationError,
                match="aihub538_prevalidated_aggregate_capability_invalid",
            ):
                binding.authorize(forged_aggregate)  # type: ignore[arg-type]

        forged_aggregate = aggregate_replacements[0]
        forged_receipt = Aihub538TrainingAdmissionReceipt.issue(
            forged_aggregate
        )
        with pytest.raises(
            ValueError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            replace(
                binding,
                admission_receipt=forged_receipt,
            )

        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            Aihub538AuthorizedShardAdmission._issue_live(
                owner_binding=object(),
                prevalidated_admission=prevalidated,
            )

        authorization = binding.authorize(prevalidated)
        assert authorization.aggregate is aggregate
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match=(
                "aihub538_authorized_training_run_"
                "authorization_already_consumed"
            ),
        ):
            binding.authorize(prevalidated)
        cloned_binding = object.__new__(Aihub538AuthorizedTrainingRunBinding)
        for dataclass_field in fields(Aihub538AuthorizedTrainingRunBinding):
            object.__setattr__(
                cloned_binding,
                dataclass_field.name,
                getattr(binding, dataclass_field.name),
            )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            cloned_binding.authorize(prevalidated)
        for forged_aggregate in aggregate_replacements:
            with pytest.raises(
                ValueError,
                match="aihub538_authorized_shard_capability_invalid",
            ):
                replace(
                    authorization,
                    aggregate=forged_aggregate,
                )

        read_descriptor, write_descriptor = os.pipe()
        child = os.fork()
        if child == 0:  # pragma: no cover - assertion is read by the parent
            os.close(read_descriptor)
            reasons: list[str] = []
            try:
                prevalidated.assert_authorizable(
                    archive_binding=binding.admitted_archive_binding,
                    admission_receipt=binding.admission_receipt,
                    archive_policy=aihub538_archive_security_policy_for_shard(
                        binding.shard_spec
                    ),
                )
            except Aihub538TrainingAdmissionError as error:
                reasons.append(error.reason_code)
            try:
                binding.authorize(prevalidated)
            except Aihub538DecisionCalibrationError as error:
                reasons.append(error.reason_code)
            else:
                reasons.append("authorization_unexpectedly_succeeded")
            try:
                authorization._assert_live()
            except ValueError as error:
                reasons.append(str(error))
            os.write(
                write_descriptor,
                "|".join(reasons).encode("ascii"),
            )
            os.close(write_descriptor)
            os._exit(0)
        os.close(write_descriptor)
        child_result = os.read(read_descriptor, 4096).decode("ascii")
        os.close(read_descriptor)
        _, child_status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(child_status) == 0
        assert child_result == (
            "aihub538_prevalidated_aggregate_capability_invalid|"
            "aihub538_authorized_training_run_capability_invalid|"
            "aihub538_authorized_shard_capability_invalid"
        )

        retained = binding
        retained_aggregate = prevalidated
        retained_authorization = authorization
        role_paths = (
            (
                runs[0]
                / f"candidate-{binding.candidate_role_binding.label_candidate_index}.zip",
                binding.candidate_role_binding.label_zip_descriptor,
            ),
            (
                runs[0]
                / f"candidate-{binding.candidate_role_binding.source_candidate_index}.zip",
                binding.candidate_role_binding.source_zip_descriptor,
            ),
        )

    assert retained_aggregate is not None
    with pytest.raises(
        ValueError,
        match="aihub538_authorized_shard_capability_invalid",
    ):
        retained_authorization._assert_live()
    safe_descriptors: list[int] = []
    rebound_descriptors: list[int] = []
    try:
        for path, target in role_paths:
            source = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                safe = fcntl.fcntl(source, fcntl.F_DUPFD_CLOEXEC, 128)
            finally:
                os.close(source)
            safe_descriptors.append(safe)
            os.dup2(safe, target, inheritable=False)
            rebound_descriptors.append(target)
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="aihub538_authorized_training_run_capability_invalid",
        ):
            retained.authorize(retained_aggregate)
    finally:
        for descriptor in (*rebound_descriptors, *safe_descriptors):
            os.close(descriptor)


def test_authorized_run_simultaneous_authorize_succeeds_exactly_once(
    tmp_path: Path,
) -> None:
    key_path, _capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path
    )
    retained: Aihub538AuthorizedShardAdmission | None = None
    with open_aihub538_authorized_training_run(
        directory=runs[0],
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        capacity_receipt=None,
    ) as binding:
        prevalidated = Aihub538TrainingDatasetAdapter().admit_prevalidated(
            archive_binding=binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            identity_hash_key=key_path.read_bytes(),
        )
        barrier = threading.Barrier(3)
        outcomes: list[
            tuple[str, Aihub538AuthorizedShardAdmission | str] | None
        ] = [None, None]

        def authorize(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                outcomes[index] = ("authorized", binding.authorize(prevalidated))
            except Aihub538DecisionCalibrationError as error:
                outcomes[index] = ("rejected", error.reason_code)
            except BaseException as error:  # pragma: no cover - reported below
                outcomes[index] = ("unexpected", repr(error))

        workers = tuple(
            threading.Thread(target=authorize, args=(index,))
            for index in range(2)
        )
        for worker in workers:
            worker.start()
        barrier.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()

        authorized = tuple(
            value
            for status, value in outcomes  # type: ignore[misc]
            if status == "authorized"
        )
        rejected = tuple(
            value
            for status, value in outcomes  # type: ignore[misc]
            if status == "rejected"
        )
        assert len(authorized) == 1
        assert all(
            isinstance(value, Aihub538AuthorizedShardAdmission)
            for value in authorized
        )
        assert rejected == (
            "aihub538_authorized_training_run_authorization_already_consumed",
        )
        retained = authorized[0]  # type: ignore[assignment]
        retained._assert_live()

    assert retained is not None
    with pytest.raises(
        ValueError,
        match="aihub538_authorized_shard_capability_invalid",
    ):
        retained._assert_live()


def test_authorized_shard_issuance_and_context_revoke_are_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_path, _capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path
    )
    binding_ready = threading.Event()
    request_exit = threading.Event()
    context_exited = threading.Event()
    issuance_paused = threading.Event()
    release_issuance = threading.Event()
    cleanup_lock_entered = threading.Event()
    holder: dict[str, object] = {}
    context_errors: list[BaseException] = []

    real_lifetime_lock = (
        training_run_binding_adapter.AIHUB538_CAPABILITY_LIFETIME_LOCK
    )

    class ObservedLifetimeLock:
        def __enter__(self) -> object:
            if (
                request_exit.is_set()
                and threading.current_thread() is context_owner
            ):
                cleanup_lock_entered.set()
            return real_lifetime_lock.__enter__()

        def __exit__(self, *args: object) -> object:
            return real_lifetime_lock.__exit__(*args)

    observed_lifetime_lock = ObservedLifetimeLock()
    for module in (
        training_run_binding_adapter,
        training_dataset_adapter,
        multishard_admission_model,
    ):
        monkeypatch.setattr(
            module,
            "AIHUB538_CAPABILITY_LIFETIME_LOCK",
            observed_lifetime_lock,
        )

    def own_context() -> None:
        try:
            with open_aihub538_authorized_training_run(
                directory=runs[0],
                shard_spec=AIHUB538_TS174_TL174_SHARD,
                capacity_receipt=None,
            ) as binding:
                holder["binding"] = binding
                binding_ready.set()
                if not request_exit.wait(timeout=10):
                    raise TimeoutError("context exit was not requested")
        except BaseException as error:  # pragma: no cover - reported below
            context_errors.append(error)
        finally:
            context_exited.set()

    context_owner = threading.Thread(target=own_context)
    context_owner.start()
    assert binding_ready.wait(timeout=10)
    binding = holder["binding"]
    assert isinstance(binding, Aihub538AuthorizedTrainingRunBinding)
    prevalidated = Aihub538TrainingDatasetAdapter().admit_prevalidated(
        archive_binding=binding.admitted_archive_binding,
        admission_receipt=binding.admission_receipt,
        identity_hash_key=key_path.read_bytes(),
    )

    real_token_type = multishard_admission_model._AuthorizedShardCapabilityToken

    def blocking_token() -> object:
        issuance_paused.set()
        if not release_issuance.wait(timeout=10):
            raise TimeoutError("authorization issuance was not released")
        return real_token_type()

    monkeypatch.setattr(
        multishard_admission_model,
        "_AuthorizedShardCapabilityToken",
        blocking_token,
    )
    authorization_results: list[object] = []

    def authorize() -> None:
        try:
            authorization_results.append(binding.authorize(prevalidated))
        except BaseException as error:  # pragma: no cover - reported below
            authorization_results.append(error)

    authorization_worker = threading.Thread(target=authorize)
    authorization_worker.start()
    assert issuance_paused.wait(timeout=10)
    request_exit.set()
    assert cleanup_lock_entered.wait(timeout=10)
    assert not context_exited.is_set()
    release_issuance.set()
    authorization_worker.join(timeout=10)
    context_owner.join(timeout=10)
    assert not authorization_worker.is_alive()
    assert not context_owner.is_alive()
    assert context_exited.is_set()
    assert context_errors == []
    assert len(authorization_results) == 1
    authorization = authorization_results[0]
    assert isinstance(authorization, Aihub538AuthorizedShardAdmission)
    assert (
        authorization._capability
        not in multishard_admission_model._AUTHORIZED_SHARD_CAPABILITIES
    )
    with pytest.raises(
        ValueError,
        match="aihub538_authorized_shard_capability_invalid",
    ):
        authorization._assert_live()


def test_prevalidated_legacy_replay_simultaneous_bind_succeeds_exactly_once(
    tmp_path: Path,
) -> None:
    key_path, _capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path,
        legacy_baseline=True,
    )
    with open_aihub538_authorized_training_run(
        directory=runs[0],
        shard_spec=AIHUB538_TS174_TL174_SHARD,
        capacity_receipt=None,
    ) as binding:
        current = Aihub538TrainingDatasetAdapter().admit_prevalidated(
            archive_binding=binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            identity_hash_key=key_path.read_bytes(),
        )
        barrier = threading.Barrier(3)
        outcomes: list[tuple[str, object] | None] = [None, None]

        def bind_legacy(index: int) -> None:
            try:
                barrier.wait(timeout=10)
                outcomes[index] = (
                    "authorized",
                    current.bind_legacy_ts174_replay(),
                )
            except Aihub538TrainingAdmissionError as error:
                outcomes[index] = ("rejected", error.reason_code)
            except BaseException as error:  # pragma: no cover - reported below
                outcomes[index] = ("unexpected", repr(error))

        workers = tuple(
            threading.Thread(target=bind_legacy, args=(index,))
            for index in range(2)
        )
        for worker in workers:
            worker.start()
        barrier.wait(timeout=10)
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()

        authorized = tuple(
            value
            for status, value in outcomes  # type: ignore[misc]
            if status == "authorized"
        )
        rejected = tuple(
            value
            for status, value in outcomes  # type: ignore[misc]
            if status == "rejected"
        )
        assert len(authorized) == 1
        assert rejected == (
            "aihub538_prevalidated_legacy_replay_already_issued",
        )
        authorization = binding.authorize(authorized[0])  # type: ignore[arg-type]
        authorization._assert_live()


def test_multishard_rejects_capacity_lineage_actor_collision_and_token_tamper(
    tmp_path: Path,
) -> None:
    key_path, capacity_receipt, runs = _produce_three_shard_transactional_runs(
        tmp_path
    )
    with ExitStack() as stack:
        rows = tuple(
            _authorized_shard_and_media(
                stack=stack,
                run=run,
                shard_spec=shard_spec,
                capacity_receipt=capacity_receipt,
                identity_key=key_path.read_bytes(),
            )
            for run, shard_spec in zip(
                runs,
                (
                    AIHUB538_TS174_TL174_SHARD,
                    AIHUB538_TS175_TL175_SHARD,
                    AIHUB538_TS176_TL176_SHARD,
                ),
                strict=True,
            )
        )
        ts174, ts175, ts176 = (item[0] for item in rows)
        with pytest.raises(
            ValueError,
            match="aihub538_authorized_shard_capability_invalid",
        ):
            replace(
                ts175,
                capacity_audit_receipt_sha256=_digest("wrong-capacity"),
            )

        baseline_actor = next(
            iter(ts174.aggregate.restricted_identity_set.speaker_hmac_sha256)
        )
        ts176_actors = set(
            ts176.aggregate.restricted_identity_set.speaker_hmac_sha256
        )
        ts176_actors.pop()
        ts176_actors.add(baseline_actor)
        collision_aggregate = replace(
            ts176.aggregate,
            restricted_identity_set=replace(
                ts176.aggregate.restricted_identity_set,
                speaker_hmac_sha256=frozenset(ts176_actors),
            ),
        )
        with pytest.raises(
            ValueError,
            match="aihub538_authorized_shard_capability_invalid",
        ):
            replace(
                ts176,
                aggregate=collision_aggregate,
            )

        multi = Aihub538MultiShardAdmissionReceipt.issue(
            ts174=ts174,
            ts175=ts175,
            ts176=ts176,
            capacity_receipt=capacity_receipt,
        )
        tampered_inventory = list(rows[1][1].inventory)
        tampered_inventory[0] = replace(
            tampered_inventory[0],
            label_selection_token_sha256=_digest("tampered-label-token"),
        )
        tampered_source = _FixtureOwnedMediaSource(
            marker="tampered",
            inventory=tuple(tampered_inventory),
        )
        sources = (
            Aihub538CalibrationShardSource(
                shard_spec=AIHUB538_TS174_TL174_SHARD,
                authorization=ts174,
                media_source=rows[0][1],
            ),
            Aihub538CalibrationShardSource(
                shard_spec=AIHUB538_TS175_TL175_SHARD,
                authorization=ts175,
                media_source=tampered_source,
            ),
            Aihub538CalibrationShardSource(
                shard_spec=AIHUB538_TS176_TL176_SHARD,
                authorization=ts176,
                media_source=rows[2][1],
            ),
        )
        with pytest.raises(
            Aihub538DecisionCalibrationError,
            match="inventory_admission_mismatch",
        ):
            Aihub538CompositeCalibrationMediaSource(
                sources=sources,
                admission_receipt=multi,
                capacity_receipt=capacity_receipt,
            )


def test_low_level_downloader_denies_538_sources_without_typed_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("download_aihub_secure.py")
    monkeypatch.delenv(
        "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD",
        raising=False,
    )
    arguments = argparse.Namespace(
        dataset_key="538",
        file_keys="54726,54945",
        destination=tmp_path / "source",
        timeout_seconds=1.0,
    )
    with pytest.raises(
        module.DownloadError,
        match="aihub538_source_authorization_required",
    ):
        module._aihub538_source_authorization_guard(arguments)

    for locked_keys in ("55082,55114", "55082", "55114"):
        arguments.file_keys = locked_keys
        with pytest.raises(
            module.DownloadError,
            match="aihub538_locked_download_entrypoint_required",
        ):
            module._aihub538_source_authorization_guard(arguments)

    for dataset_key, file_keys, reason in (
        ("0538", "54945", "dataset_key_invalid"),
        ("538", "054945", "file_keys_invalid"),
        ("538", "055082", "file_keys_invalid"),
        ("538", "54726,054945", "file_keys_invalid"),
    ):
        noncanonical = argparse.Namespace(
            dataset_key=dataset_key,
            file_keys=file_keys,
            destination=tmp_path / "must-not-open-network",
            timeout_seconds=1.0,
        )
        with pytest.raises(module.DownloadError, match=reason):
            module._validate_inputs(noncanonical)
        monkeypatch.setenv("AIHUB_API_KEY", "fixture-never-forwarded")
        monkeypatch.setattr(
            module,
            "urlopen",
            lambda *args, **kwargs: pytest.fail(
                f"network opened for noncanonical identifier: {args!r} {kwargs!r}"
            ),
        )
        with pytest.raises(module.DownloadError, match=reason):
            module.download(noncanonical)
        assert not noncanonical.destination.exists()


def test_legacy_powershell_downloaders_fail_closed_before_dataset538_access() -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    for name, first_access_marker in (
        ("download_aihub_file_resumable.ps1", "Get-AihubApiKey"),
        ("download_aihub_l2_files.ps1", "Get-Command aihubshell"),
    ):
        text = (scripts / name).read_text(encoding="utf-8")
        guard = "if ($DatasetKey -eq 538)"
        assert guard in text
        assert "Dataset 538 is fail-closed" in text
        assert text.index(guard) < text.index(first_access_marker)
        assert "typed Python" in text


def test_authorized_downloader_requires_and_resumes_entire_pair_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _key, authorization, _runs = _produce_three_shard_transactional_runs(
        tmp_path / "authorization-fixture"
    )
    receipt_path = tmp_path / "authorization-v5.json"
    receipt_path.write_text(
        json.dumps(authorization.to_json_object(), sort_keys=True),
        encoding="utf-8",
    )
    receipt_path.chmod(0o400)
    module = _load_script("download_aihub538_authorized_sources.py")
    descriptor = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW)
    ts175_destination = tmp_path / "ts175-download"
    ts176_destination = tmp_path / "ts176-download"
    completed: set[Path] = set()
    commands: list[tuple[list[str], dict[str, object]]] = []
    identity_assertions = 0
    original_assert = module.assert_aihub538_descriptor_identity

    def assert_stable(fd: int, identity: object) -> None:
        nonlocal identity_assertions
        identity_assertions += 1
        original_assert(fd, identity)

    def verify_completed(*, destination: Path, shard_spec: object) -> bool:
        del shard_spec
        return destination in completed

    def run(command: list[str], **kwargs: object):  # noqa: ANN202
        commands.append((command, kwargs))
        assert kwargs["pass_fds"] == (descriptor,)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment[
            "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD"
        ] == str(descriptor)
        completed.add(Path(command[-1]))
        return module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        module,
        "assert_aihub538_descriptor_identity",
        assert_stable,
    )
    monkeypatch.setattr(module, "_verify_completed_pair_if_present", verify_completed)
    monkeypatch.setattr(module.subprocess, "run", run)
    try:
        arguments = [
            "--authorization-receipt-fd",
            str(descriptor),
            "--ts175-destination",
            str(ts175_destination),
            "--ts176-destination",
            str(ts176_destination),
        ]
        # A perfectly self-consistent feasible receipt is still rejected until
        # its digest is the independently released production pin.
        secure_module = _load_script("download_aihub_secure.py")
        monkeypatch.setenv(
            "VOICE_COACH_AIHUB538_SOURCE_AUTHORIZATION_FD",
            str(descriptor),
        )
        with pytest.raises(
            secure_module.DownloadError,
            match="aihub538_source_authorization_invalid",
        ):
            secure_module._aihub538_source_authorization_guard(
                argparse.Namespace(
                    dataset_key="538",
                    file_keys="54726,54945",
                    destination=ts175_destination,
                    timeout_seconds=1.0,
                )
            )
        assert module.main(arguments) == 65
        assert commands == []
        capsys.readouterr()
        _pin_fixture_source_authorization(monkeypatch, authorization)
        assert module.main(arguments) == 0
        first_output = json.loads(capsys.readouterr().out)
        assert first_output["status"] == "completed_authorized_prefix_download"
        assert first_output["first_feasible_prefix_complete"] is True
        assert [item[0][3] for item in commands] == [
            "54726,54945",
            "54727,54946",
        ]
        assert identity_assertions >= 4

        # A retry revalidates and skips the completed first pair while running
        # only the missing second pair.  This is prefix-resume, not cherry-pick.
        commands.clear()
        completed.remove(ts176_destination.absolute())
        assert module.main(arguments) == 0
        second_output = json.loads(capsys.readouterr().out)
        assert len(commands) == 1
        assert commands[0][0][3] == "54727,54946"
        assert second_output["completed_pairs"][0][
            "existing_completed_download_reused"
        ] is True
        assert second_output["completed_pairs"][1][
            "existing_completed_download_reused"
        ] is False

        with pytest.raises(SystemExit):
            module._arguments(
                [
                    "--authorization-receipt-fd",
                    str(descriptor),
                    "--shard-id",
                    "ts176_tl176",
                    "--destination",
                    str(ts176_destination),
                ]
            )
    finally:
        os.close(descriptor)


def _fixture_cli_media_adapter(
    *,
    label_zip_descriptor: int,
    source_zip_descriptor: int,
    identity_hash_key: bytes,
    shard_spec: Aihub538NonlockedShardSpec,
    archive_binding: Aihub538AdmittedArchiveBinding,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    **ignored: object,
) -> _FixtureOwnedMediaSource:
    del ignored
    prevalidated = Aihub538TrainingDatasetAdapter(
        shard_spec=shard_spec
    ).admit_prevalidated(
        archive_binding=archive_binding,
        admission_receipt=admission_receipt,
        identity_hash_key=identity_hash_key,
    )
    aggregate = prevalidated.aggregate
    label_population = Aihub538LabelCapacityAdapter(
        shard_spec=shard_spec
    ).audit(
        label_zip_descriptor=label_zip_descriptor,
        identity_hash_key=identity_hash_key,
    )
    private_by_token = {
        item.sample_sha256: item for item in label_population.items
    }
    inventory = tuple(
        Aihub538CalibrationMediaItem(
            sample_sha256=sample.sample_sha256,
            actor_token_sha256=private_by_token[
                sample.label_selection_token_sha256
            ].actor_token_sha256,
            attempt_token_sha256=private_by_token[
                sample.label_selection_token_sha256
            ].attempt_token_sha256,
            angle=sample.angle,
            metadata_bbox_observation_available=not bool(
                sample.face_reversed_bbox_unavailable_count
                or sample.lip_reversed_bbox_unavailable_count
            ),
            label_selection_token_sha256=sample.label_selection_token_sha256,
        )
        for sample in aggregate.samples
    )
    return _FixtureOwnedMediaSource(
        marker=shard_spec.shard_id,
        inventory=inventory,
    )


def test_multishard_calibration_cli_binds_three_runs_v5_and_fails_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_path, authorization, runs = _produce_three_shard_transactional_runs(
        tmp_path / "transactional"
    )
    authorization_path = tmp_path / "authorization-v5.json"
    authorization_path.write_text(
        json.dumps(authorization.to_json_object(), sort_keys=True),
        encoding="utf-8",
    )
    authorization_path.chmod(0o400)
    receipt_descriptor = os.open(
        authorization_path, os.O_RDONLY | os.O_NOFOLLOW
    )
    identity_state = tmp_path / "identity-state"
    identity_state.mkdir(mode=0o700)
    persisted_key = identity_state / "aihub538-identity-hmac-v1.key"
    persisted_key.write_bytes(key_path.read_bytes())
    persisted_key.chmod(0o400)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.mkdir()
    module = _load_script("calibrate_aihub538_runtime_decisions.py")
    events: list[str] = []
    publications: list[object] = []
    archive_validation_calls: list[int] = []
    original_assert = module.assert_aihub538_descriptor_identity
    original_loader = (
        module.load_aihub538_label_selection_authorization_receipt_descriptor
    )
    original_archive_validation = (
        training_run_binding_adapter.validate_aihub538_stable_zip_descriptor
    )

    def load_capacity(fd: int):  # noqa: ANN202
        events.append("load")
        return original_loader(fd)

    def assert_capacity(fd: int, identity: object) -> None:
        events.append("assert")
        original_assert(fd, identity)

    def validate_archive_once(
        descriptor: int,
        *,
        policy: object,
    ):  # noqa: ANN202
        archive_validation_calls.append(descriptor)
        return original_archive_validation(descriptor, policy=policy)

    def media_adapter(**values: object) -> _FixtureOwnedMediaSource:
        events.append("adapter")
        return _fixture_cli_media_adapter(**values)

    class FakeCalibrationService:
        def __init__(self, *, measurement_port: object, protocol: object) -> None:
            self.measurement_port = measurement_port
            self.protocol = protocol

        def calibrate(
            self,
            *,
            items: tuple[Aihub538CalibrationMediaItem, ...],
            admission_receipt: Aihub538MultiShardAdmissionReceipt,
            **ignored: object,
        ) -> object:
            del ignored
            events.append("calibrate")
            split = build_aihub538_calibration_split(
                items=items,
                admission_receipt=admission_receipt,
                protocol=self.protocol,
            )
            assert split.metadata_excluded_item_count == 2
            assert len(split.development) == len(split.calibration) == 72
            return SimpleNamespace(split=split)

    def publish(**values: object):  # noqa: ANN202
        events.append("publish")
        publications.append(values)
        admission_receipt = values["admission_receipt"]
        assert type(admission_receipt) is Aihub538MultiShardAdmissionReceipt
        evidence = SimpleNamespace(
            status="actual_media_calibrated",
            source_dataset_id="aihub_538",
            source_partitions=("TS174", "TL174", "TS175", "TL175", "TS176", "TL176"),
            evaluated_item_count=72,
            actor_overlap_count=0,
            attempt_overlap_count=0,
            fresh_process_reload_passed=True,
            frozen=True,
        )
        return evidence, tuple(output / f"artifact-{index}" for index in range(5))

    monkeypatch.setattr(
        module,
        "load_aihub538_label_selection_authorization_receipt_descriptor",
        load_capacity,
    )
    monkeypatch.setattr(module, "assert_aihub538_descriptor_identity", assert_capacity)
    monkeypatch.setattr(
        training_run_binding_adapter,
        "validate_aihub538_stable_zip_descriptor",
        validate_archive_once,
    )
    monkeypatch.setattr(
        training_dataset_adapter,
        "open_validated_zip_descriptor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("prevalidated dataset path rescanned a ZIP")
        ),
    )
    monkeypatch.setattr(module, "_actual_media_adapter", media_adapter)
    monkeypatch.setattr(module, "Aihub538DecisionCalibrationService", FakeCalibrationService)
    monkeypatch.setattr(
        module.calibration_publish,
        "publish_aihub538_decision_calibration_bundle",
        publish,
    )
    monkeypatch.setattr(
        module,
        "_model_sha256",
        lambda path: module.OFFICIAL_FACE_LANDMARKER_SHA256,
    )
    base_arguments = [
        "--label-selection-authorization-fd",
        str(receipt_descriptor),
        "--ts174-run-dir",
        str(runs[0]),
        "--ts175-run-dir",
        str(runs[1]),
        "--ts176-run-dir",
        str(runs[2]),
        "--identity-state-dir",
        str(identity_state),
        "--restricted-workspace-root",
        str(workspace),
        "--output-dir",
        str(output),
        "--dataset-access-permission-confirmed",
    ]
    try:
        events.clear()
        assert module.main(base_arguments) == 2
        capsys.readouterr()
        assert publications == []
        assert "adapter" not in events
        _pin_fixture_source_authorization(monkeypatch, authorization)
        events.clear()
        assert module.main(base_arguments) == 0
        output_document = json.loads(capsys.readouterr().out)
        assert output_document["source_partitions"] == [
            "TS174",
            "TL174",
            "TS175",
            "TL175",
            "TS176",
            "TL176",
        ]
        assert len(publications) == 1
        # Exactly two candidate archives per shard are fully validated.  The
        # dataset and actual-media consumers reuse the same live capability.
        assert len(archive_validation_calls) == 6
        first_adapter = events.index("adapter")
        assert events[:first_adapter].count("assert") >= 3
        assert events[-3:] == ["calibrate", "assert", "publish"]

        publications.clear()
        events.clear()
        missing_run_arguments = base_arguments.copy()
        missing_index = missing_run_arguments.index("--ts176-run-dir")
        del missing_run_arguments[missing_index : missing_index + 2]
        assert module.main(missing_run_arguments) == 2
        capsys.readouterr()
        assert publications == []

        mixed_arguments = base_arguments + [
            "--label-zip-fd",
            str(receipt_descriptor),
        ]
        assert module.main(mixed_arguments) == 2
        capsys.readouterr()
        assert publications == []

        completion_path = runs[2] / "99-completion.json"
        completion_document = json.loads(completion_path.read_text(encoding="utf-8"))
        completion_document["fsync_completed"] = False
        unsigned = {
            key: value
            for key, value in completion_document.items()
            if key != "completion_receipt_sha256"
        }
        completion_document["completion_receipt_sha256"] = sha256_document(unsigned)
        completion_path.chmod(0o600)
        completion_path.write_text(
            json.dumps(completion_document, sort_keys=True),
            encoding="utf-8",
        )
        completion_path.chmod(0o400)
        assert module.main(base_arguments) == 2
        capsys.readouterr()
        assert publications == []
    finally:
        os.close(receipt_descriptor)
