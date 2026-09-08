"""Synthetic contract tests for the aggregate-only AI Hub 539 TL8 audit."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Callable

import pytest

from voice_coach.visual_feedback.adapters.aihub539_sync_dataset import (
    Aihub539SyncAuditError,
    Aihub539SyncDatasetAdapter,
)
from voice_coach.visual_feedback.models.aihub539_sync_evidence import (
    AIHUB539_CAMERAS,
    Aihub539SyncLabelAuditReceipt,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.aihub539_sync_audit import (
    Aihub539SyncLabelAuditService,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_aihub539_sync_labels.py"


def _matrix(rows: int, columns: int) -> list[list[float]]:
    return [
        [float(row_index * columns + column_index + 1) for column_index in range(columns)]
        for row_index in range(rows)
    ]


def _landmark_frames(
    *, point_count: int, point_size: int, document_index: int
) -> list[list[list[float]]]:
    return [
        [
            [
                float(document_index + frame_index + point_index + axis_index) / 10.0
                for axis_index in range(point_size)
            ]
            for point_index in range(point_count)
        ]
        for frame_index in range(3)
    ]


def _track(
    *, actor_id: str, point_size: int, document_index: int
) -> dict[str, object]:
    return {
        "body": _landmark_frames(
            point_count=21,
            point_size=point_size,
            document_index=document_index,
        ),
        "face": _landmark_frames(
            point_count=28,
            point_size=point_size,
            document_index=document_index,
        ),
        "speaker_ID": actor_id,
    }


def _document(document_index: int) -> dict[str, object]:
    motion_actor_ids = (
        f"private_motion_actor_{document_index}_0",
        f"private_motion_actor_{document_index}_1",
    )
    transcript_actor_ids = (
        f"private_transcript_actor_{document_index}_0",
        f"private_transcript_actor_{document_index}_1",
    )
    return {
        "Info": {
            "Audio_Info": {
                "audio_duration": 0.1,
                "audio_format": "wav",
                "audio_name": [f"private_audio_{document_index}.wav"],
                "channel": 2,
                "sampling_rate": "48kHz",
            },
            "Dataset": {
                "URL": "https://fixture.invalid",
                "description": f"비공개 합성 문서 {document_index}",
                "version": 1.0,
            },
            "Video_Info": {
                "FPS": 29.97,
                "extrinsic": {
                    camera_id: _matrix(3, 4) for camera_id in AIHUB539_CAMERAS
                },
                "intrinsic": {
                    camera_id: _matrix(3, 3) for camera_id in AIHUB539_CAMERAS
                },
                "resolution": [1920.0, 1080.0],
                "video_duration": 0.1,
                "video_format": "mp4",
                "video_name": {
                    camera_id: f"private_video_{document_index}_{camera_id}.mp4"
                    for camera_id in AIHUB539_CAMERAS
                },
            },
        },
        "Motion": {
            "Keypoints": {
                "keypoints_2d": {
                    camera_id: [
                        _track(
                            actor_id=actor_id,
                            point_size=2,
                            document_index=document_index,
                        )
                        for actor_id in motion_actor_ids
                    ]
                    for camera_id in AIHUB539_CAMERAS
                },
                "keypoints_3d": [
                    _track(
                        actor_id=actor_id,
                        point_size=3,
                        document_index=document_index,
                    )
                    for actor_id in motion_actor_ids
                ],
            },
            "Keypoints_Info": {
                "body": [f"body_{point_index}" for point_index in range(21)],
                "face": [f"face_{point_index}" for point_index in range(28)],
            },
        },
        "Transcript": {
            "Sentences": [
                {
                    "emotion": "neutral",
                    "end_time": 0.04,
                    "sentence_text": f"비공개 문장 전반 {document_index}",
                    "speaker_ID": transcript_actor_ids[0],
                    "start_time": 0.01,
                },
                {
                    "emotion": "neutral",
                    "end_time": 0.09,
                    "sentence_text": f"비공개 문장 후반 {document_index}",
                    "speaker_ID": transcript_actor_ids[1],
                    "start_time": 0.05,
                },
            ],
            "Speaker_Info": [
                {
                    "Accent": "N",
                    "Age": "3",
                    "Gender": "F" if actor_index == 0 else "M",
                    "Specificity": "N",
                    "speaker_ID": actor_id,
                }
                for actor_index, actor_id in enumerate(transcript_actor_ids)
            ],
            "Transcript_Info": {
                "sentence_count": 2,
                "subject": "fixture",
                "type": "dialogue",
            },
        },
    }


def _write_data_document(path: Path, document: dict[str, object]) -> None:
    path.write_bytes(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("cp949")
    )


def _write_extraction_receipt(labels_root: Path) -> None:
    document_paths = sorted((labels_root / "partition").glob("*.json"))
    receipt = {
        "schema_version": 1,
        "completed_at": "2026-08-28T12:00:00+09:00",
        "archive": "fixture.part0",
        "archive_sha256": "a" * 64,
        "file_count": len(document_paths),
        "uncompressed_bytes": sum(path.stat().st_size for path in document_paths),
    }
    (labels_root / "extraction_receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _labels_fixture(tmp_path: Path) -> Path:
    labels_root = tmp_path / "labels"
    partition = labels_root / "partition"
    partition.mkdir(parents=True)
    for document_index in range(13):
        _write_data_document(
            partition / f"document_{document_index:02d}.json",
            _document(document_index),
        )
    _write_extraction_receipt(labels_root)
    return labels_root


def _mutate_document(
    labels_root: Path,
    document_index: int,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    path = labels_root / "partition" / f"document_{document_index:02d}.json"
    document = json.loads(path.read_bytes().decode("cp949"))
    mutation(document)
    _write_data_document(path, document)
    _write_extraction_receipt(labels_root)


def _service() -> Aihub539SyncLabelAuditService:
    return Aihub539SyncLabelAuditService(Aihub539SyncDatasetAdapter())


def test_audit_emits_only_aggregate_non_promotional_evidence(
    tmp_path: Path,
) -> None:
    labels_root = _labels_fixture(tmp_path)

    receipt = _service().audit(
        labels_root=labels_root,
        dataset_access_permission_confirmed=True,
    )
    document = receipt.to_json_object()
    serialized = json.dumps(document, ensure_ascii=False, sort_keys=True)

    assert document["source_document_count"] == 13
    assert document["source_hash_count"] == 14
    assert document["dataset_access_permission_confirmed"] is True
    assert document["permission_grants_technical_promotion"] is False
    assert document["actual_media_sync_validated"] is False
    assert document["promotion_authorized"] is False
    assert document["timing"]["total_timeline_frame_count"] == 39
    assert len(document["two_dimensional_camera_slices"]) == 4
    assert "private_motion_actor_0_0" not in serialized
    assert "private_transcript_actor_0_0" not in serialized
    assert "비공개 문장" not in serialized
    assert str(labels_root) not in serialized
    unsigned = dict(document)
    observed_digest = unsigned.pop("receipt_sha256")
    assert sha256_document(unsigned) == observed_digest
    reloaded = Aihub539SyncLabelAuditReceipt.from_json_object(document)
    assert reloaded.receipt_sha256 == observed_digest


@pytest.mark.parametrize(
    "mutation",
    (
        lambda document: document.__setitem__("unexpected", True),
        lambda document: document.__setitem__("source_hash_count", 14.0),
        lambda document: document.__setitem__("promotion_authorized", True),
    ),
)
def test_strict_reload_rejects_schema_type_and_policy_tampering(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    labels_root = _labels_fixture(tmp_path)
    document = _service().audit(
        labels_root=labels_root,
        dataset_access_permission_confirmed=True,
    ).to_json_object()
    mutation(document)
    unsigned = dict(document)
    unsigned.pop("receipt_sha256")
    document["receipt_sha256"] = sha256_document(unsigned)

    with pytest.raises(ValueError):
        Aihub539SyncLabelAuditReceipt.from_json_object(document)


def test_audit_requires_dataset_access_permission_confirmation(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)

    with pytest.raises(Aihub539SyncAuditError) as caught:
        _service().audit(
            labels_root=labels_root,
            dataset_access_permission_confirmed=False,
        )

    assert caught.value.reason_code == (
        "aihub539_dataset_access_permission_not_confirmed"
    )


def test_audit_rejects_symlink_without_disclosing_target(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)
    target = labels_root / "partition" / "document_00.json"
    (labels_root / "000_link.json").symlink_to(target)

    with pytest.raises(Aihub539SyncAuditError) as caught:
        _service().audit(
            labels_root=labels_root,
            dataset_access_permission_confirmed=True,
        )

    assert caught.value.reason_code == "aihub539_symlink_forbidden"
    assert str(target) not in str(caught.value)


def test_audit_rejects_audio_video_duration_mismatch(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)

    def mismatch(document: dict[str, object]) -> None:
        document["Info"]["Audio_Info"]["audio_duration"] = 0.2

    _mutate_document(labels_root, 0, mismatch)

    with pytest.raises(Aihub539SyncAuditError) as caught:
        _service().audit(
            labels_root=labels_root,
            dataset_access_permission_confirmed=True,
        )

    assert caught.value.reason_code == "aihub539_audio_video_duration_mismatch"


def test_audit_rejects_wrong_landmark_shape(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)

    def change_point_shape(document: dict[str, object]) -> None:
        document["Motion"]["Keypoints"]["keypoints_2d"]["A"][0]["face"][0][0] = [
            1.0
        ]

    _mutate_document(labels_root, 0, change_point_shape)

    with pytest.raises(Aihub539SyncAuditError) as caught:
        _service().audit(
            labels_root=labels_root,
            dataset_access_permission_confirmed=True,
        )

    assert caught.value.reason_code == "aihub539_schema_invalid"


def test_audit_rejects_sentence_outside_media_bounds(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)

    def change_sentence_time(document: dict[str, object]) -> None:
        document["Transcript"]["Sentences"][0]["end_time"] = 0.2

    _mutate_document(labels_root, 0, change_sentence_time)

    with pytest.raises(Aihub539SyncAuditError) as caught:
        _service().audit(
            labels_root=labels_root,
            dataset_access_permission_confirmed=True,
        )

    assert caught.value.reason_code == "aihub539_sentence_time_outside_media"


def test_cli_writes_immutable_aggregate_receipt(tmp_path: Path) -> None:
    labels_root = _labels_fixture(tmp_path)
    output = tmp_path / "receipt.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--labels-root",
            str(labels_root),
            "--output",
            str(output),
            "--dataset-access-permission-confirmed",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout
    response = json.loads(completed.stdout)
    document = json.loads(output.read_text(encoding="utf-8"))
    assert response["status"] == "completed"
    assert response["actual_media_sync_validated"] is False
    assert document["promotion_authorized"] is False
    assert output.stat().st_mode & 0o777 == 0o400

    reload_code = """
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / 'src'))
from voice_coach.visual_feedback.models.aihub539_sync_evidence import Aihub539SyncLabelAuditReceipt
document = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
receipt = Aihub539SyncLabelAuditReceipt.from_json_object(document)
print(receipt.receipt_sha256)
"""
    fresh_reload = subprocess.run(
        [sys.executable, "-c", reload_code, str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert fresh_reload.returncode == 0, fresh_reload.stderr
    assert fresh_reload.stdout.strip() == document["receipt_sha256"]

    repeated = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--labels-root",
            str(labels_root),
            "--output",
            str(output),
            "--dataset-access-permission-confirmed",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)["reason_codes"] == [
        "aihub539_execution_boundary_failed"
    ]
