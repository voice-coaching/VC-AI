"""Fixture admission/evaluator tests for AI Hub 538 TS174/TL174."""

from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
from typing import Callable
from zipfile import ZIP_STORED, ZipFile

import pytest

from voice_coach.visual_feedback.adapters.aihub538_identity_key_store import (
    Aihub538IdentityKeyStoreError,
    create_aihub538_identity_key,
    load_aihub538_identity_key,
    load_aihub538_identity_key_descriptor,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (
    Aihub538TrainingDatasetAdapter,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    Aihub538IdentityIntersectionReceipt,
)
from voice_coach.visual_feedback.services.aihub538_training_smoke import (
    Aihub538TrainingSmokeError,
    Aihub538TrainingSmokeService,
)


_FIXTURE_IDENTITY_KEY = b"k" * 32
assert len(_FIXTURE_IDENTITY_KEY) == 32
_PRIVATE_TRANSCRIPT = "테스트 전사 원문은 공개 receipt에 없어야 한다"


def _session_stem(*, angle: str, speaker_digits: str = "032", session: str = "010") -> str:
    return f"lip_J_1_F_02_C{speaker_digits}_{angle}_{session}"


def _label_record(
    *,
    angle: str,
    speaker_digits: str = "032",
    session: str = "010",
) -> dict[str, object]:
    stem = _session_stem(
        angle=angle,
        speaker_digits=speaker_digits,
        session=session,
    )
    audio_stem = _session_stem(
        angle="A",
        speaker_digits=speaker_digits,
        session=session,
    )
    boxes = [[100, 100, 200, 180] for _ in range(3)]
    lip_boxes = [[120, 130, 180, 165] for _ in range(3)]
    return {
        "dataSet": {
            "description": "restricted fixture metadata",
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
            "Audio_Name": f"{audio_stem}.wav",
            "Audio_Format": "wav",
            "Audio_Duration": "0.1",
            "Sampling_rate": "48kHz",
            "Channel(s)": 1,
        },
        "Audio_env": {"Noise": 1},
        "Video_env": {"env": "J", "Angle": angle},
        "Sentence_info": [
            {
                "ID": "sentence-1",
                "topic": "fixture topic",
                "sentence_text": _PRIVATE_TRANSCRIPT,
                "start_time": 0.0,
                "end_time": 0.1,
            }
        ],
        "speaker_info": {
            "speaker_ID": f"C{speaker_digits}",
            "Specificity": "C",
            "Gender": "F",
            "Age": 2,
            "Accent": "N",
        },
        "Bounding_box_info": {
            "Face_bounding_box": {"xtl_ytl_xbr_ybr": boxes},
            "Lip_bounding_box": {"xtl_ytl_xbr_ybr": lip_boxes},
        },
    }


def _private_zip(path: Path, members: dict[str, bytes]) -> Path:
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    path.chmod(0o600)
    return path


def _dataset_fixture(
    root: Path,
    *,
    speaker_digits: str = "032",
    session: str = "010",
    mutate_record: Callable[[str, dict[str, object]], None] | None = None,
    raw_label_transform: Callable[[str, bytes], bytes] | None = None,
    omit_source_name: str | None = None,
    add_extra_source: bool = False,
) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    label_members: dict[str, bytes] = {}
    source_members: dict[str, bytes] = {}
    relative_directory = "noise/C/F/group"
    for angle in ("A", "B"):
        stem = _session_stem(
            angle=angle,
            speaker_digits=speaker_digits,
            session=session,
        )
        record = _label_record(
            angle=angle,
            speaker_digits=speaker_digits,
            session=session,
        )
        if mutate_record is not None:
            mutate_record(angle, record)
        payload = json.dumps(
            [record],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if raw_label_transform is not None:
            payload = raw_label_transform(angle, payload)
        label_members[
            f"restricted/Label/TL174/{relative_directory}/{stem}.json"
        ] = payload
        video_name = f"restricted/Source/TS174/{relative_directory}/{stem}.mp4"
        if video_name != omit_source_name:
            source_members[video_name] = b"\x00\x00\x00\x18ftypisomfixture-video"
    a_stem = _session_stem(
        angle="A",
        speaker_digits=speaker_digits,
        session=session,
    )
    audio_name = f"restricted/Source/TS174/{relative_directory}/{a_stem}.wav"
    if audio_name != omit_source_name:
        source_members[audio_name] = b"RIFF\x08\x00\x00\x00WAVEfixture-audio"
    if add_extra_source:
        source_members[
            f"restricted/Source/TS174/{relative_directory}/extra.mp4"
        ] = b"\x00\x00\x00\x18ftypisomextra"
    return (
        _private_zip(root / "labels.zip", label_members),
        _private_zip(root / "sources.zip", source_members),
    )


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _service() -> Aihub538TrainingSmokeService:
    return Aihub538TrainingSmokeService(Aihub538TrainingDatasetAdapter())


def _evaluate(
    label_zip: Path,
    source_zip: Path,
    *,
    identity_key: bytes = _FIXTURE_IDENTITY_KEY,
):
    with ExitStack() as stack:
        return _service().evaluate(
            label_zip_descriptor=_readonly(stack, label_zip),
            source_zip_descriptor=_readonly(stack, source_zip),
            dataset_access_permission_confirmed=True,
            identity_hash_key=identity_key,
        )


def _admit(
    label_zip: Path,
    source_zip: Path,
    *,
    identity_key: bytes = _FIXTURE_IDENTITY_KEY,
):
    with ExitStack() as stack:
        return Aihub538TrainingDatasetAdapter().admit(
            label_zip_descriptor=_readonly(stack, label_zip),
            source_zip_descriptor=_readonly(stack, source_zip),
            identity_hash_key=identity_key,
        )


def test_exact_pairing_and_evaluator_smoke_are_aggregate_non_promotional(
    tmp_path: Path,
) -> None:
    label_zip, source_zip = _dataset_fixture(tmp_path / "dataset")
    evidence = _evaluate(label_zip, source_zip)
    admission = evidence.admission_receipt.to_json_object()
    smoke = evidence.evaluator_smoke_receipt.to_json_object()

    assert admission["paired_sample_count"] == 2
    assert admission["label_count"] == 2
    assert admission["video_count"] == 2
    assert admission["unique_audio_count"] == 1
    assert admission["audio_reference_count"] == 2
    assert admission["speaker_count"] == 1
    assert len(admission["view_aggregates"]) == 9
    assert admission["view_aggregates"][0]["paired_sample_count"] == 1
    assert admission["view_aggregates"][1]["paired_sample_count"] == 1
    assert admission["actual_media_decode_executed"] is False
    assert admission["qc_threshold_calibrated"] is False
    assert admission["av_sync_threshold_calibrated"] is False
    assert admission["promotion_authorized"] is False
    assert smoke["evaluated_sample_count"] == 2
    assert smoke["all_admitted_samples_evaluated"] is True
    assert smoke["actual_av_sync_anchor_evaluated"] is False
    assert smoke["qc_threshold_artifact_issued"] is False
    assert smoke["av_sync_threshold_artifact_issued"] is False
    assert smoke["locked_partition_accessed"] is False

    serialized = json.dumps(
        {"admission": admission, "smoke": smoke},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "C032" not in serialized
    assert _PRIVATE_TRANSCRIPT not in serialized
    assert "lip_J_1" not in serialized
    assert str(tmp_path) not in serialized
    assert _FIXTURE_IDENTITY_KEY.hex() not in serialized


def test_admission_selection_and_receipts_are_deterministic(tmp_path: Path) -> None:
    label_zip, source_zip = _dataset_fixture(tmp_path / "dataset")
    first = _evaluate(label_zip, source_zip)
    second = _evaluate(label_zip, source_zip)
    assert first.admission_receipt == second.admission_receipt
    assert first.evaluator_smoke_receipt == second.evaluator_smoke_receipt


def test_permission_and_identity_key_are_required(tmp_path: Path) -> None:
    label_zip, source_zip = _dataset_fixture(tmp_path / "dataset")
    with ExitStack() as stack, pytest.raises(Aihub538TrainingSmokeError) as caught:
        _service().evaluate(
            label_zip_descriptor=_readonly(stack, label_zip),
            source_zip_descriptor=_readonly(stack, source_zip),
            dataset_access_permission_confirmed=False,
            identity_hash_key=_FIXTURE_IDENTITY_KEY,
        )
    assert caught.value.reason_code == "aihub538_dataset_access_permission_not_confirmed"

    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip, identity_key=b"short")
    assert caught.value.reason_code == "aihub538_identity_hash_key_invalid"


def test_non_A_view_reuses_exact_A_wav_and_extra_media_fails_closed(
    tmp_path: Path,
) -> None:
    label_zip, source_zip = _dataset_fixture(
        tmp_path / "missing",
        omit_source_name=(
            "restricted/Source/TS174/noise/C/F/group/"
            "lip_J_1_F_02_C032_A_010.wav"
        ),
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == "aihub538_training_exact_media_pairing_failed"

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "extra",
        add_extra_source=True,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == "aihub538_training_exact_media_pairing_failed"


def test_metadata_must_name_A_wav_for_non_A_view(tmp_path: Path) -> None:
    def mutate(angle: str, record: dict[str, object]) -> None:
        if angle == "B":
            record["Audio_info"]["Audio_Name"] = (  # type: ignore[index]
                f"{_session_stem(angle='B')}.wav"
            )

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "dataset",
        mutate_record=mutate,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == (
        "aihub538_training_metadata_media_name_mismatch"
    )


def test_creator_json_must_be_single_item_array_without_duplicate_keys(
    tmp_path: Path,
) -> None:
    def two_items(_: str, payload: bytes) -> bytes:
        document = json.loads(payload)
        return json.dumps([document[0], document[0]]).encode("utf-8")

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "two-items",
        raw_label_transform=two_items,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == (
        "aihub538_training_label_root_not_single_item_array"
    )

    def duplicate_key(_: str, payload: bytes) -> bytes:
        return payload.replace(
            b'"description":"restricted fixture metadata"',
            b'"description":"restricted fixture metadata","description":"duplicate"',
            1,
        )

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "duplicate-key",
        raw_label_transform=duplicate_key,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == "aihub538_training_label_json_invalid"


def test_bbox_and_sentence_times_are_finite_in_frame_bounds(tmp_path: Path) -> None:
    def invalid_bbox(angle: str, record: dict[str, object]) -> None:
        if angle == "A":
            record["Bounding_box_info"]["Lip_bounding_box"][  # type: ignore[index]
                "xtl_ytl_xbr_ybr"
            ][0] = [120, 130, 700, 165]  # type: ignore[index]

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "bbox",
        mutate_record=invalid_bbox,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == "aihub538_training_bbox_coordinate_invalid"

    def invalid_time(angle: str, record: dict[str, object]) -> None:
        if angle == "A":
            record["Sentence_info"][0]["end_time"] = 0.5  # type: ignore[index]

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "time",
        mutate_record=invalid_time,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == "aihub538_training_sentence_time_invalid"


def test_metadata_duration_disagreement_fails_smoke_without_threshold_artifact(
    tmp_path: Path,
) -> None:
    def mismatch(angle: str, record: dict[str, object]) -> None:
        if angle == "B":
            record["Audio_info"]["Audio_Duration"] = "0.5"  # type: ignore[index]

    label_zip, source_zip = _dataset_fixture(
        tmp_path / "dataset",
        mutate_record=mismatch,
    )
    with pytest.raises(Aihub538TrainingSmokeError) as caught:
        _evaluate(label_zip, source_zip)
    assert caught.value.reason_code == (
        "aihub538_training_metadata_av_duration_mismatch"
    )


def test_keyed_identity_sets_support_aggregate_only_disjoint_receipt(
    tmp_path: Path,
) -> None:
    left_label, left_source = _dataset_fixture(
        tmp_path / "left",
        speaker_digits="032",
        session="010",
    )
    right_label, right_source = _dataset_fixture(
        tmp_path / "right",
        speaker_digits="033",
        session="011",
    )
    left = _admit(left_label, left_source)
    right = _admit(right_label, right_source)
    receipt = Aihub538IdentityIntersectionReceipt.compare(
        left_manifest_sha256=left.admission_manifest_sha256,
        right_manifest_sha256=right.admission_manifest_sha256,
        left=left.restricted_identity_set,
        right=right.restricted_identity_set,
    )
    document = receipt.to_json_object()
    assert document["speaker_intersection_count"] == 0
    assert document["session_intersection_count"] == 0
    assert document["disjoint"] is True
    assert document["promotion_authorized"] is False
    serialized = json.dumps(document, sort_keys=True)
    for digest in (
        left.restricted_identity_set.speaker_hmac_sha256
        | left.restricted_identity_set.session_hmac_sha256
        | right.restricted_identity_set.speaker_hmac_sha256
        | right.restricted_identity_set.session_hmac_sha256
    ):
        assert digest not in serialized
    assert "C032" not in serialized and "C033" not in serialized


def test_identity_intersection_detects_overlap_and_rejects_other_key_namespace(
    tmp_path: Path,
) -> None:
    label_zip, source_zip = _dataset_fixture(tmp_path / "dataset")
    admitted = _admit(label_zip, source_zip)
    overlap = Aihub538IdentityIntersectionReceipt.compare(
        left_manifest_sha256=admitted.admission_manifest_sha256,
        right_manifest_sha256=admitted.admission_manifest_sha256,
        left=admitted.restricted_identity_set,
        right=admitted.restricted_identity_set,
    )
    assert overlap.speaker_intersection_count == 1
    assert overlap.session_intersection_count == 1
    assert overlap.is_disjoint is False

    other = _admit(label_zip, source_zip, identity_key=b"z" * 32)
    with pytest.raises(ValueError, match="namespace_mismatch"):
        Aihub538IdentityIntersectionReceipt.compare(
            left_manifest_sha256=admitted.admission_manifest_sha256,
            right_manifest_sha256=other.admission_manifest_sha256,
            left=admitted.restricted_identity_set,
            right=other.restricted_identity_set,
        )


def test_identity_key_store_is_O_EXCL_0400_fsynced_and_reloadable(
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "restricted-state"
    created, created_receipt = create_aihub538_identity_key(
        state_directory=state_directory
    )
    key_path = state_directory / "aihub538-identity-hmac-v1.key"
    assert stat.S_IMODE(state_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o400
    assert key_path.stat().st_size == 32
    assert "key=" not in repr(created)
    assert created.key.hex() not in json.dumps(
        created_receipt.to_json_object(),
        sort_keys=True,
    )

    loaded, loaded_receipt = load_aihub538_identity_key(
        state_directory=state_directory
    )
    assert loaded.key == created.key
    assert loaded.namespace_commitment_sha256 == created.namespace_commitment_sha256
    assert loaded_receipt.status == "loaded"

    before = key_path.read_bytes()
    with pytest.raises(Aihub538IdentityKeyStoreError) as caught:
        create_aihub538_identity_key(state_directory=state_directory)
    assert caught.value.reason_code == (
        "aihub538_identity_key_already_exists_or_unwritable"
    )
    assert key_path.read_bytes() == before

    descriptor = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        descriptor_loaded = load_aihub538_identity_key_descriptor(descriptor)
    finally:
        os.close(descriptor)
    assert descriptor_loaded.key == created.key


def test_identity_key_store_rejects_mode_tampering(tmp_path: Path) -> None:
    state_directory = tmp_path / "restricted-state"
    create_aihub538_identity_key(state_directory=state_directory)
    key_path = state_directory / "aihub538-identity-hmac-v1.key"
    key_path.chmod(0o600)
    with pytest.raises(Aihub538IdentityKeyStoreError) as caught:
        load_aihub538_identity_key(state_directory=state_directory)
    assert caught.value.reason_code == "aihub538_identity_key_descriptor_invalid"
