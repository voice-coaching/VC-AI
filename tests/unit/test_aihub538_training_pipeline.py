"""Transactional fixture tests for the AI Hub 538 training pipeline CLI."""

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
from zipfile import ZIP_STORED, ZipFile

import pytest

from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.aihub538_training_pipeline import (
    Aihub538TrainingPipeline,
    Aihub538TrainingPipelineError,
)


_PRIVATE_TRANSCRIPT = "비공개 테스트 전사 원문"
_PRIVATE_OUTER_STEM = "restricted-speaker-C032"
_IDENTITY_KEY = b"pipeline-fixture-key-material-00"
assert len(_IDENTITY_KEY) == 32


def _session_stem(*, angle: str) -> str:
    return f"lip_J_1_F_02_C032_{angle}_010"


def _label_record(*, angle: str) -> dict[str, object]:
    stem = _session_stem(angle=angle)
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
                "ID": "private-sentence-1",
                "topic": "private fixture topic",
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


def _tar_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _dataset_archive_bytes(*, archive_kind: str = "zip") -> tuple[bytes, bytes]:
    labels: dict[str, bytes] = {}
    sources: dict[str, bytes] = {}
    relative = "noise/C/F/group"
    for angle in ("A", "B"):
        stem = _session_stem(angle=angle)
        labels[
            f"restricted/Label/TL174/{relative}/{stem}.json"
        ] = json.dumps(
            [_label_record(angle=angle)],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sources[
            f"restricted/Source/TS174/{relative}/{stem}.mp4"
        ] = b"\x00\x00\x00\x18ftypisomfixture-video"
    sources[
        f"restricted/Source/TS174/{relative}/{_session_stem(angle='A')}.wav"
    ] = b"RIFF\x08\x00\x00\x00WAVEfixture-audio"
    if archive_kind == "zip":
        return _zip_bytes(labels), _zip_bytes(sources)
    assert archive_kind == "tar"
    return _tar_bytes(labels), _tar_bytes(sources)


def _split_members(
    *,
    private_directory: str,
    misleading_stem: str,
    payload: bytes,
    archive_kind: str = "zip",
) -> list[tuple[str, bytes]]:
    first_size = len(payload) // 2
    assert first_size > 0
    rows = [
        (
            f"{private_directory}/{misleading_stem}.{archive_kind}.part0",
            payload[:first_size],
        ),
        (
            (
                f"{private_directory}/{misleading_stem}.{archive_kind}.part"
                f"{first_size}"
            ),
            payload[first_size:],
        ),
    ]
    # Outer TAR order is intentionally reversed.  The merge primitive must use
    # byte offsets, not archive order or ordinal assumptions.
    return list(reversed(rows))


def _completed_download_fixture(
    directory: Path,
    *,
    second_role: str = "source",
    split_archive_kind: str = "zip",
) -> tuple[Path, Path]:
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    label_zip, source_zip = _dataset_archive_bytes(
        archive_kind=split_archive_kind
    )
    second_payload = source_zip if second_role == "source" else label_zip
    members = _split_members(
        private_directory=f"opaque/{_PRIVATE_OUTER_STEM}/first",
        misleading_stem="looks-like-source",
        payload=label_zip,
        archive_kind=split_archive_kind,
    ) + _split_members(
        private_directory=f"opaque/{_PRIVATE_OUTER_STEM}/second",
        misleading_stem="looks-like-label",
        payload=second_payload,
        archive_kind=split_archive_kind,
    )
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
                "file_keys": ["54725", "54944"],
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


def _identity_key(path: Path) -> Path:
    path.write_bytes(_IDENTITY_KEY)
    path.chmod(0o400)
    return path


def _readonly(stack: ExitStack, path: Path) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    stack.callback(os.close, descriptor)
    return descriptor


def _run(
    *,
    input_directory: Path,
    key_path: Path,
    output_directory: Path,
):
    with ExitStack() as stack:
        return Aihub538TrainingPipeline().run(
            archive_descriptor=_readonly(stack, input_directory / "download.tar"),
            receipt_descriptor=_readonly(
                stack,
                input_directory / "download.receipt.json",
            ),
            identity_key_descriptor=_readonly(stack, key_path),
            output_directory=output_directory,
            dataset_access_permission_confirmed=True,
        )


def test_pipeline_runs_offset_split_to_aggregate_smoke_without_raw_leakage(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed"
    _completed_download_fixture(input_directory)
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "run"

    evidence = _run(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
    )

    document = evidence.to_json_object()
    assert document["status"] == "completed_nonlocked_training_metadata_smoke"
    assert document["partial_download_accessed"] is False
    assert document["locked_partition_accessed"] is False
    assert document["semantic_role_classification"]["label_archive_count"] == 1
    assert document["semantic_role_classification"]["source_archive_count"] == 1
    assert document["dataset_admission"]["paired_sample_count"] == 2
    assert document["metadata_smoke"]["evaluated_sample_count"] == 2
    unsigned = dict(document)
    receipt_sha256 = unsigned.pop("completion_receipt_sha256")
    assert receipt_sha256 == sha256_document(unsigned)

    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        _PRIVATE_TRANSCRIPT,
        _PRIVATE_OUTER_STEM,
        "looks-like-source",
        "looks-like-label",
        "lip_J_1_F_02_C032",
        "private-sentence-1",
        str(tmp_path),
        _IDENTITY_KEY.hex(),
    ):
        assert forbidden not in serialized

    assert stat.S_IMODE(output_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((output_directory / "candidate-0.zip").stat().st_mode) == 0o600
    assert stat.S_IMODE((output_directory / "candidate-1.zip").stat().st_mode) == 0o600
    receipt_paths = sorted(output_directory.glob("*.json"))
    assert [path.name for path in receipt_paths] == [
        "00-download-verification.json",
        "10-outer-tar-extraction.json",
        "20-split-zip-merge.json",
        "30-zip-validation.json",
        "40-semantic-role-classification.json",
        "50-dataset-admission.json",
        "60-metadata-smoke.json",
        "99-completion.json",
    ]
    for path in receipt_paths:
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        persisted = path.read_text(encoding="utf-8")
        assert _PRIVATE_TRANSCRIPT not in persisted
        assert _PRIVATE_OUTER_STEM not in persisted
        assert str(tmp_path) not in persisted


def test_pipeline_repackages_actual_shape_split_inner_tar_before_admission(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed-inner-tar"
    _completed_download_fixture(
        input_directory,
        split_archive_kind="tar",
    )
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "tar-run"

    evidence = _run(
        input_directory=input_directory,
        key_path=key_path,
        output_directory=output_directory,
    )

    document = evidence.to_json_object()
    assert document["split_archive_merge"]["archive_kinds"] == [
        "inner_posix_tar"
    ]
    assert document["split_zip_merge"]["status"] == (
        "not_applicable_split_inner_tar_input"
    )
    assert document["inner_tar_repack"]["status"] == "completed"
    assert document["inner_tar_repack"]["archive_count"] == 2
    for archive in document["inner_tar_repack"]["archives"]:
        assert archive["normalized_path_hash_size_sha256_parity"] is True
        assert archive["zip_compression"] == "stored"
        assert archive["zip64_forced_for_streamed_members"] is True
    assert document["dataset_admission"]["paired_sample_count"] == 2
    assert document["metadata_smoke"]["evaluated_sample_count"] == 2
    assert (output_directory / "20-split-archive-merge.json").is_file()
    assert (output_directory / "25-inner-tar-repack.json").is_file()
    assert not (output_directory / "20-split-zip-merge.json").exists()
    for index in (0, 1):
        assert stat.S_IMODE(
            (output_directory / f"candidate-{index}.tar").stat().st_mode
        ) == 0o600
        assert stat.S_IMODE(
            (output_directory / f"candidate-{index}.zip").stat().st_mode
        ) == 0o600

    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        _PRIVATE_TRANSCRIPT,
        _PRIVATE_OUTER_STEM,
        "looks-like-source",
        "looks-like-label",
        "lip_J_1_F_02_C032",
        str(tmp_path),
    ):
        assert forbidden not in serialized


def test_semantic_role_failure_removes_fresh_output_and_same_path_retries(
    tmp_path: Path,
) -> None:
    invalid_input = tmp_path / "invalid-completed"
    _completed_download_fixture(invalid_input, second_role="label")
    valid_input = tmp_path / "valid-completed"
    _completed_download_fixture(valid_input)
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "retryable-run"

    with pytest.raises(Aihub538TrainingPipelineError) as caught:
        _run(
            input_directory=invalid_input,
            key_path=key_path,
            output_directory=output_directory,
        )
    assert caught.value.reason_code == (
        "aihub538_pipeline_semantic_role_pair_invalid"
    )
    assert not output_directory.exists()

    evidence = _run(
        input_directory=valid_input,
        key_path=key_path,
        output_directory=output_directory,
    )
    assert evidence.to_json_object()["status"] == (
        "completed_nonlocked_training_metadata_smoke"
    )


def test_tampered_completed_receipt_fails_before_creating_output(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "tampered-completed"
    _, receipt_path = _completed_download_fixture(input_directory)
    key_path = _identity_key(tmp_path / "identity.key")
    receipt_document = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_document["archive_sha256"] = "0" * 64
    receipt_path.chmod(0o600)
    receipt_path.write_text(
        json.dumps(receipt_document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    receipt_path.chmod(0o400)
    output_directory = tmp_path / "must-not-exist"

    with pytest.raises(Aihub538TrainingPipelineError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output_directory,
        )
    assert caught.value.reason_code == "aihub538_download_sha256_mismatch"
    assert not output_directory.exists()


def test_existing_output_is_not_overwritten_or_cleaned(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "completed"
    _completed_download_fixture(input_directory)
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "existing-run"
    output_directory.mkdir(mode=0o700)
    output_directory.chmod(0o700)
    marker = output_directory / "caller-owned.marker"
    marker.write_bytes(b"preserve")
    marker.chmod(0o600)

    with pytest.raises(Aihub538TrainingPipelineError) as caught:
        _run(
            input_directory=input_directory,
            key_path=key_path,
            output_directory=output_directory,
        )
    assert caught.value.reason_code == "aihub538_pipeline_output_directory_not_new"
    assert marker.read_bytes() == b"preserve"
    assert tuple(output_directory.iterdir()) == (marker,)


def test_completed_directory_opener_never_probes_partial_or_locked_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_directory = tmp_path / "not-completed"
    input_directory.mkdir(mode=0o700)
    input_directory.chmod(0o700)
    for name in ("download.tar.partial", "VS11.zip", "VL11.zip"):
        path = input_directory / name
        path.write_bytes(b"must-not-open")
        path.chmod(0o000)
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "must-not-exist"
    observed: list[str] = []
    real_open = os.open

    def recording_open(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, (str, Path)):
            observed.append(str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", recording_open)
    with ExitStack() as stack, pytest.raises(Aihub538TrainingPipelineError) as caught:
        Aihub538TrainingPipeline().run_from_completed_directory(
            input_directory=input_directory,
            identity_key_descriptor=_readonly(stack, key_path),
            output_directory=output_directory,
            dataset_access_permission_confirmed=True,
        )
    assert caught.value.reason_code == (
        "aihub538_completed_download_missing_or_invalid"
    )
    assert not output_directory.exists()
    assert not any(
        forbidden in opened
        for opened in observed
        for forbidden in ("download.tar.partial", "VS11", "VL11")
    )


def test_cli_directory_mode_emits_only_aggregate_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_directory = tmp_path / "completed"
    _completed_download_fixture(input_directory)
    key_path = _identity_key(tmp_path / "identity.key")
    output_directory = tmp_path / "cli-run"
    module = _load_cli_module()
    with ExitStack() as stack:
        result = module.main(  # type: ignore[attr-defined]
            [
                "--input-directory",
                str(input_directory),
                "--identity-key-fd",
                str(_readonly(stack, key_path)),
                "--output-directory",
                str(output_directory),
                "--dataset-access-permission-confirmed",
            ]
        )
    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    document = json.loads(captured.out)
    assert document["status"] == "completed_nonlocked_training_metadata_smoke"
    assert document["partial_download_accessed"] is False
    assert document["locked_partition_accessed"] is False
    for forbidden in (
        _PRIVATE_TRANSCRIPT,
        _PRIVATE_OUTER_STEM,
        str(input_directory),
        str(output_directory),
    ):
        assert forbidden not in captured.out


def _load_cli_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / (
        "run_aihub538_training_pipeline.py"
    )
    specification = importlib.util.spec_from_file_location(
        "run_aihub538_training_pipeline_fixture",
        path,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module
