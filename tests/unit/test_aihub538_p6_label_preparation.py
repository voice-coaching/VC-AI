from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
from types import ModuleType
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import pytest

from voice_coach.visual_feedback.adapters.aihub538_label_capacity import (
    Aihub538LabelCapacityAdapter,
)
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import (
    Aihub538TrainingAdmissionError,
    Aihub538TrainingDatasetAdapter,
    _parse_label_payload,
)
from voice_coach.visual_feedback.models.aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from voice_coach.visual_feedback.adapters.aihub538_p6_label_preparation import (
    Aihub538P6LabelPreparationAdapterError,
    load_aihub538_p6_label_preparation_receipt_descriptor,
    open_aihub538_p6_prepared_label_directory,
)
from voice_coach.visual_feedback.adapters.aihub538_training_archive import (
    AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
    aihub538_archive_security_policy_for_shard,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
)
from voice_coach.visual_feedback.models.aihub538_p6_label_preparation import (
    Aihub538P6LabelPreparationReceipt,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.aihub538_p6_label_preparation import (
    Aihub538P6LabelPreparationError,
    Aihub538P6LabelPreparationService,
)
from voice_coach.visual_feedback.services import (
    aihub538_p6_label_preparation as preparation_service_module,
)


_TL177 = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2]


def _label_record(
    *,
    speaker: str,
    angle: str,
    session: str,
) -> dict[str, object]:
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


def _label_members(role: str = "TL177") -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for speaker, angle, session in (
        ("201", "A", "001"),
        ("202", "B", "002"),
    ):
        record = _label_record(
            speaker=speaker,
            angle=angle,
            session=session,
        )
        stem = str(record["Video_info"]["video_Name"])[:-4]
        members[
            f"restricted/Label/{role}/opaque/{stem}.json"
        ] = json.dumps(
            [record],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    return members


def _payload(record: dict[str, object]) -> bytes:
    return json.dumps(
        [record],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@pytest.mark.parametrize(
    "complete_frames,accepted",
    (
        (15.0, True),
        (15.5, True),
        (15.9999999995, True),
        (16.0, False),
        (16.0000000005, False),
    ),
)
def test_label_capacity_terminal_tail_is_bounded_by_complete_30fps_frames(
    complete_frames: float,
    accepted: bool,
) -> None:
    record = _label_record(speaker="201", angle="A", session="001")
    record["Sentence_info"][0]["end_time"] = (  # type: ignore[index]
        0.1 + complete_frames / 30.0
    )
    if accepted:
        parsed = _parse_label_payload(
            _payload(record),
            allow_label_capacity_terminal_frame_tail=True,
        )
        assert parsed.terminal_media_overrun is True
        assert parsed.terminal_frame_tail_exception_applied is (
            complete_frames > 15.0
        )
    else:
        with pytest.raises(
            Aihub538TrainingAdmissionError,
            match="sentence_time_invalid",
        ):
            _parse_label_payload(
                _payload(record),
                allow_label_capacity_terminal_frame_tail=True,
            )


@pytest.mark.parametrize("wrong_fps_value", (25.0, 29.97, 60.0))
def test_terminal_tail_policy_rejects_nonterminal_and_non_30fps(
    wrong_fps_value: float,
) -> None:
    nonterminal = _label_record(speaker="201", angle="A", session="001")
    nonterminal["Sentence_info"] = [  # type: ignore[index]
        {
            "ID": "first",
            "topic": "private",
            "sentence_text": "private",
            "start_time": 0.0,
            "end_time": 0.11,
        },
        {
            "ID": "final",
            "topic": "private",
            "sentence_text": "private",
            "start_time": 0.11,
            "end_time": 0.12,
        },
    ]
    with pytest.raises(Aihub538TrainingAdmissionError):
        _parse_label_payload(
            _payload(nonterminal),
            allow_label_capacity_terminal_frame_tail=True,
        )
    # Ordinary training behavior remains unchanged for the same sub-0.5 s tail.
    _parse_label_payload(_payload(nonterminal))

    wrong_fps = _label_record(speaker="201", angle="A", session="001")
    wrong_fps["Video_info"]["FPS"] = wrong_fps_value  # type: ignore[index]
    frame_count = round(0.1 * wrong_fps_value)
    wrong_fps["Bounding_box_info"]["Face_bounding_box"][  # type: ignore[index]
        "xtl_ytl_xbr_ybr"
    ] = [[100, 100, 200, 180]] * frame_count
    wrong_fps["Bounding_box_info"]["Lip_bounding_box"][  # type: ignore[index]
        "xtl_ytl_xbr_ybr"
    ] = [[120, 130, 180, 165]] * frame_count
    wrong_fps["Sentence_info"][0]["end_time"] = 0.619002  # type: ignore[index]
    with pytest.raises(Aihub538TrainingAdmissionError):
        _parse_label_payload(
            _payload(wrong_fps),
            allow_label_capacity_terminal_frame_tail=True,
        )


def test_training_tail_and_overlap_rules_remain_strict_and_separate() -> None:
    record = _label_record(speaker="201", angle="A", session="001")
    record["Sentence_info"][0]["end_time"] = 0.619002  # type: ignore[index]
    with pytest.raises(Aihub538TrainingAdmissionError):
        _parse_label_payload(_payload(record))

    overlap = _label_record(speaker="201", angle="A", session="001")
    overlap["Video_info"]["video_Duration"] = "1.0"  # type: ignore[index]
    overlap["Audio_info"]["Audio_Duration"] = "1.0"  # type: ignore[index]
    boxes = [[100, 100, 200, 180]] * 30
    lip_boxes = [[120, 130, 180, 165]] * 30
    overlap["Bounding_box_info"]["Face_bounding_box"][  # type: ignore[index]
        "xtl_ytl_xbr_ybr"
    ] = boxes
    overlap["Bounding_box_info"]["Lip_bounding_box"][  # type: ignore[index]
        "xtl_ytl_xbr_ybr"
    ] = lip_boxes
    overlap["Sentence_info"] = [  # type: ignore[index]
        {
            "ID": "first",
            "topic": "private",
            "sentence_text": "private",
            "start_time": 0.0,
            "end_time": 0.8,
        },
        {
            "ID": "final",
            "topic": "private",
            "sentence_text": "private",
            "start_time": 0.799,
            "end_time": 0.9,
        },
    ]
    # Historical training retains its 0.5 s overlap tolerance.
    _parse_label_payload(_payload(overlap))
    # The new nonlocked label schema rejects even a 1 ms overlap.
    with pytest.raises(Aihub538TrainingAdmissionError):
        _parse_label_payload(
            _payload(overlap),
            allow_label_capacity_terminal_frame_tail=True,
        )


def test_future_source_pair_requires_exact_nonlocked_label_schema_policy() -> None:
    future_pair = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2]
    for policy_sha256 in (None, "0" * 64):
        with pytest.raises(
            Aihub538TrainingAdmissionError,
            match="nonlocked_label_schema_policy_required",
        ):
            Aihub538TrainingDatasetAdapter(
                shard_spec=future_pair,
                nonlocked_label_schema_policy_sha256=policy_sha256,
            )
    Aihub538TrainingDatasetAdapter(
        shard_spec=future_pair,
        nonlocked_label_schema_policy_sha256=(
            AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
        ),
    )


def _zip_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for name, payload in members.items():
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o600 << 16
            archive.writestr(info, payload)
    return buffer.getvalue()


def _tl177_terminal_tail_members() -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for index in range(5):
        record = _label_record(
            speaker=str(201 + index),
            angle=chr(ord("A") + index),
            session=f"{index + 1:03d}",
        )
        record["Sentence_info"][0]["end_time"] = 0.619002  # type: ignore[index]
        stem = str(record["Video_info"]["video_Name"])[:-4]  # type: ignore[index]
        members[f"opaque/TL177/private/{stem}.json"] = _payload(record)
    return members


def test_tl177_five_terminal_overruns_are_aggregate_only_evidence(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "tl177-five.zip"
    archive.write_bytes(_zip_payload(_tl177_terminal_tail_members()))
    archive.chmod(0o600)
    descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        aggregate = Aihub538LabelCapacityAdapter(shard_spec=_TL177).audit(
            label_zip_descriptor=descriptor,
            identity_hash_key=b"k" * 32,
        )
    finally:
        os.close(descriptor)
    assert aggregate.terminal_media_overrun_label_count == 5
    assert aggregate.terminal_frame_tail_exception_label_count == 5
    assert aggregate.maximum_complete_tail_frames_observed == 15
    assert aggregate.nonterminal_media_overrun_label_count == 0
    assert aggregate.sentence_overlap_label_count == 0
    assert aggregate.invalid_time_label_count == 0
    assert aggregate.nonconforming_fps_label_count == 0


def _tar_payload(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _completed_download(
    directory: Path,
    *,
    file_key: str = "54728",
    role: str = "TL177",
    inner_kind: str = "zip",
    offset_gap: bool = False,
    second_group: bool = False,
    label_members: dict[str, bytes] | None = None,
) -> Path:
    directory.parent.chmod(0o700)
    directory.mkdir(mode=0o700)
    inner = (
        _zip_payload(label_members or _label_members(role))
        if inner_kind == "zip"
        else _tar_payload(label_members or _label_members(role))
    )
    split = max(1, len(inner) // 2)
    second_offset = split + (1 if offset_gap else 0)
    outer = directory / "download.tar"
    with tarfile.open(outer, "w") as archive:
        for offset, payload in (
            (0, inner[:split]),
            (second_offset, inner[split:]),
        ):
            name = f"opaque/group/label.{inner_kind}.part{offset}"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
        if second_group:
            info = tarfile.TarInfo(
                f"opaque/other/other.{inner_kind}.part0"
            )
            info.size = len(inner)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(inner))
    outer.chmod(0o600)
    payload = outer.read_bytes()
    receipt = directory / "download.receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": (
                    "voice_coach.aihub_secure_download_receipt.v1"
                ),
                "dataset_key": "538",
                "file_keys": [file_key],
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


def _identity_key(path: Path) -> Path:
    path.write_bytes(b"k" * 32)
    path.chmod(0o400)
    return path


def _read_only(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _run(
    *,
    input_directory: Path,
    key_path: Path,
    output_directory: Path,
) -> Aihub538P6LabelPreparationReceipt:
    with ExitStack() as stack:
        return Aihub538P6LabelPreparationService(
            shard_spec=_TL177
        ).prepare_from_completed_directory(
            input_directory=input_directory,
            identity_key_descriptor=_read_only(stack, key_path),
            output_directory=output_directory,
            dataset_access_permission_confirmed=True,
        )


@pytest.mark.parametrize("inner_kind", ["zip", "tar"])
def test_prepare_exact_tl177_zip_or_tar_and_reload_strictly(
    tmp_path: Path,
    inner_kind: str,
) -> None:
    input_directory = _completed_download(
        tmp_path / "input",
        inner_kind=inner_kind,
    )
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"

    receipt = _run(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output,
    )

    assert receipt.label_role_component == "TL177"
    assert receipt.label_file_key == "54728"
    assert receipt.split_archive_kind == inner_kind
    assert receipt.label_member_count == 2
    assert receipt.actor_count == 2
    assert receipt.attempt_count == 2
    assert receipt.metadata_excluded_label_count == 0
    assert receipt.terminal_media_overrun_label_count == 0
    assert receipt.terminal_frame_tail_exception_label_count == 0
    assert receipt.maximum_complete_tail_frames_observed == 0
    assert receipt.nonterminal_media_overrun_label_count == 0
    assert receipt.sentence_overlap_label_count == 0
    assert receipt.invalid_time_label_count == 0
    assert receipt.nonconforming_fps_label_count == 0
    assert receipt.terminal_frame_tail_policy_sha256 == (
        AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE((output / "label.zip").stat().st_mode) == 0o600
    assert not (output / "outer").exists()
    assert not (output / "label.tar").exists()
    expected = {
        "00-download-verification.json",
        "10-outer-tar-extraction.json",
        "20-split-label-merge.json",
        "30-label-zip-validation.json",
        "40-label-role-admission.json",
        "99-completion.json",
        "label.zip",
    }
    if inner_kind == "tar":
        expected.add("25-inner-tar-repack.json")
        assert receipt.inner_tar_repack_receipt_sha256 is not None
    assert set(item.name for item in output.iterdir()) == expected
    for path in output.glob("*.json"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        serialized = path.read_text(encoding="utf-8")
        assert "비공개" not in serialized
        assert "C201" not in serialized
        assert "restricted/Label" not in serialized

    with open_aihub538_p6_prepared_label_directory(
        output,
        identity_hash_key=key_path.read_bytes(),
    ) as opened:
        descriptor, loaded = opened
        assert loaded == receipt
        aggregate = Aihub538LabelCapacityAdapter(
            shard_spec=_TL177,
            archive_policy=AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
        ).audit(
            label_zip_descriptor=descriptor,
            identity_hash_key=key_path.read_bytes(),
        )
        assert len(aggregate.items) == receipt.label_member_count


def test_preparation_receipt_persists_five_bounded_terminal_overruns(
    tmp_path: Path,
) -> None:
    output = tmp_path / "prepared"
    receipt = _run(
        input_directory=_completed_download(
            tmp_path / "input",
            label_members=_tl177_terminal_tail_members(),
        ),
        key_path=_identity_key(tmp_path / "identity.key"),
        output_directory=output,
    )
    assert receipt.terminal_media_overrun_label_count == 5
    assert receipt.terminal_frame_tail_exception_label_count == 5
    assert receipt.maximum_complete_tail_frames_observed == 15
    assert receipt.nonterminal_media_overrun_label_count == 0
    assert receipt.sentence_overlap_label_count == 0
    assert receipt.invalid_time_label_count == 0
    assert receipt.nonconforming_fps_label_count == 0
    assert receipt.terminal_frame_tail_policy_sha256 == (
        AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
    )
    admission = json.loads(
        (output / "40-label-role-admission.json").read_text(encoding="utf-8")
    )
    assert admission["schema_version"].endswith("label_admission.v2")
    assert admission["terminal_media_overrun_label_count"] == 5
    assert admission["terminal_frame_tail_exception_label_count"] == 5
    assert admission["maximum_complete_tail_frames_observed"] == 15
    assert admission["nonterminal_media_overrun_label_count"] == 0
    assert admission["sentence_overlap_label_count"] == 0
    assert admission["invalid_time_label_count"] == 0
    assert admission["nonconforming_fps_label_count"] == 0
    assert "end_time" not in json.dumps(admission, sort_keys=True)
def test_permission_gate_precedes_missing_input_and_key_access(
    tmp_path: Path,
) -> None:
    output = tmp_path / "must-not-exist"
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        Aihub538P6LabelPreparationService(
            shard_spec=_TL177
        ).prepare_from_completed_directory(
            input_directory=tmp_path / "missing-input",
            identity_key_descriptor=999_999,
            output_directory=output,
            dataset_access_permission_confirmed=False,
        )
    assert caught.value.reason_code == (
        "aihub538_dataset_access_permission_not_confirmed"
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "shard",
    [
        AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[0],
        AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1],
        AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2],
    ],
)
def test_only_tl177_through_tl180_label_audit_shards_are_allowed(
    shard: object,
) -> None:
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        Aihub538P6LabelPreparationService(shard_spec=shard)
    assert caught.value.reason_code in {
        "aihub538_p6_remediation_label_shard_not_allowed",
        "aihub538_label_audit_shard_required",
    }


def test_wrong_download_key_fails_and_removes_no_preexisting_data(
    tmp_path: Path,
) -> None:
    input_directory = _completed_download(
        tmp_path / "input",
        file_key="54729",
    )
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output,
        )
    assert caught.value.reason_code == (
        "aihub538_download_receipt_contract_mismatch"
    )
    assert not output.exists()


def test_completed_input_modes_are_strict_before_output_creation(
    tmp_path: Path,
) -> None:
    input_directory = _completed_download(tmp_path / "input")
    (input_directory / "download.tar").chmod(0o400)
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output,
        )
    assert caught.value.reason_code == (
        "aihub538_p6_label_preparation_input_descriptor_invalid"
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "fixture_options",
    [{"offset_gap": True}, {"second_group": True}],
)
def test_invalid_split_contract_removes_fresh_output(
    tmp_path: Path,
    fixture_options: dict[str, bool],
) -> None:
    input_directory = _completed_download(
        tmp_path / "input",
        **fixture_options,
    )
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    with pytest.raises(Aihub538P6LabelPreparationError):
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output,
        )
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    input_directory = _completed_download(tmp_path / "input")
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    output.mkdir(mode=0o700)
    marker = output / "owner.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output,
        )
    assert caught.value.reason_code == (
        "aihub538_p6_label_preparation_output_not_new"
    )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_injected_admission_failure_removes_fresh_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_directory = _completed_download(tmp_path / "input")
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    service = Aihub538P6LabelPreparationService(shard_spec=_TL177)

    def fail(**_kwargs: object) -> object:
        raise RuntimeError("injected private error")

    monkeypatch.setattr(service._label_adapter, "audit", fail)
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        with ExitStack() as stack:
            service.prepare_from_completed_directory(
                input_directory=input_directory,
                identity_key_descriptor=_read_only(stack, key_path),
                output_directory=output,
                dataset_access_permission_confirmed=True,
            )
    assert caught.value.reason_code == (
        "aihub538_p6_label_preparation_internal_failure"
    )
    assert not output.exists()


def test_completed_download_toctou_change_fails_and_cleans_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_directory = _completed_download(tmp_path / "input")
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    real_extract = preparation_service_module.extract_verified_tar_descriptor

    def extract_then_mutate(**kwargs: object) -> object:
        result = real_extract(**kwargs)
        archive = input_directory / "download.tar"
        descriptor = os.open(archive, os.O_RDWR | os.O_NOFOLLOW)
        try:
            first = os.pread(descriptor, 1, 0)
            os.pwrite(descriptor, b"X" if first != b"X" else b"Y", 0)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return result

    monkeypatch.setattr(
        preparation_service_module,
        "extract_verified_tar_descriptor",
        extract_then_mutate,
    )
    with pytest.raises(Aihub538P6LabelPreparationError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output,
        )
    assert caught.value.reason_code == (
        "aihub538_p6_label_preparation_input_changed"
    )
    assert not output.exists()


def test_completion_loader_rejects_mode_and_digest_forgery(
    tmp_path: Path,
) -> None:
    input_directory = _completed_download(tmp_path / "input")
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    receipt = _run(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output,
    )
    completion = output / "99-completion.json"
    descriptor = os.open(completion, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert (
            load_aihub538_p6_label_preparation_receipt_descriptor(descriptor)
            == receipt
        )
    finally:
        os.close(descriptor)

    completion.chmod(0o600)
    descriptor = os.open(completion, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6LabelPreparationAdapterError):
            load_aihub538_p6_label_preparation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)
    document = receipt.to_json_object()
    document["actor_count"] = 999
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    completion.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    completion.chmod(0o400)
    descriptor = os.open(completion, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6LabelPreparationAdapterError):
            load_aihub538_p6_label_preparation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    "field,value",
    (
        ("terminal_frame_tail_policy_sha256", "0" * 64),
        ("terminal_frame_tail_exception_label_count", 1),
        ("maximum_complete_tail_frames_observed", -1),
        ("sentence_overlap_label_count", 1),
    ),
)
def test_completion_reload_rejects_terminal_policy_substitution(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    output = tmp_path / "prepared"
    receipt = _run(
        input_directory=_completed_download(tmp_path / "input"),
        key_path=_identity_key(tmp_path / "identity.key"),
        output_directory=output,
    )
    document = receipt.to_json_object()
    document[field] = value
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)
    completion = output / "99-completion.json"
    completion.chmod(0o600)
    completion.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    completion.chmod(0o400)
    descriptor = os.open(completion, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(Aihub538P6LabelPreparationAdapterError):
            load_aihub538_p6_label_preparation_receipt_descriptor(descriptor)
    finally:
        os.close(descriptor)

def test_prepared_directory_loader_binds_every_stage_receipt(
    tmp_path: Path,
) -> None:
    input_directory = _completed_download(tmp_path / "input")
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    _run(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output,
    )
    stage = output / "10-outer-tar-extraction.json"
    stage.chmod(0o600)
    document = json.loads(stage.read_text(encoding="utf-8"))
    document["regular_file_count"] += 1
    stage.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    stage.chmod(0o400)
    with pytest.raises(Aihub538P6LabelPreparationAdapterError) as caught:
        with open_aihub538_p6_prepared_label_directory(
            output,
            identity_hash_key=key_path.read_bytes(),
        ):
            pass
    assert caught.value.reason_code == (
        "aihub538_p6_prepared_label_directory_invalid"
    )


def test_prepared_directory_rejects_jointly_recomputed_admission_and_completion(
    tmp_path: Path,
) -> None:
    key_path = _identity_key(tmp_path / "identity.key")
    output = tmp_path / "prepared"
    _run(
        input_directory=_completed_download(tmp_path / "input"),
        key_path=key_path,
        output_directory=output,
    )
    admission_path = output / "40-label-role-admission.json"
    completion_path = output / "99-completion.json"
    admission = json.loads(admission_path.read_text(encoding="utf-8"))
    admission["terminal_media_overrun_label_count"] = 1
    admission_unsigned = dict(admission)
    admission_unsigned.pop("receipt_sha256")
    admission["receipt_sha256"] = sha256_document(admission_unsigned)
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["terminal_media_overrun_label_count"] = 1
    completion["label_admission_receipt_sha256"] = admission["receipt_sha256"]
    completion_unsigned = dict(completion)
    completion_unsigned.pop("receipt_sha256")
    completion["receipt_sha256"] = sha256_document(completion_unsigned)
    for path, document in (
        (admission_path, admission),
        (completion_path, completion),
    ):
        path.chmod(0o600)
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        path.chmod(0o400)
    with pytest.raises(Aihub538P6LabelPreparationAdapterError):
        with open_aihub538_p6_prepared_label_directory(
            output,
            identity_hash_key=key_path.read_bytes(),
        ):
            pass


def test_remediation_policy_is_exactly_the_frozen_128_gib_policy() -> None:
    for shard in AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:]:
        policy = aihub538_archive_security_policy_for_shard(shard)
        assert policy is AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY
        assert policy.maximum_download_bytes == 128 * 1024**3
        assert policy.maximum_tar_uncompressed_bytes == 128 * 1024**3
        assert policy.maximum_merged_zip_bytes == 128 * 1024**3


def _load_script() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "prepare_aihub538_p6_remediation_label.py"
    )
    spec = importlib.util.spec_from_file_location("p6_label_prepare_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_exposes_only_exact_remediation_label_roles() -> None:
    parser = _load_script()._parser()
    action = next(
        item for item in parser._actions if item.dest == "label_role"
    )
    assert tuple(action.choices) == ("TL177", "TL178", "TL179", "TL180")
