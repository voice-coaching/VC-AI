"""Admission, split, calibration, and reload tests for real P6 geometry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from voice_coach.visual_feedback.adapters.aihub68_geometry_dataset import (
    AIHUB68_KEYPOINT_NAMES,
    Aihub68AdmissionError,
    Aihub68AdmittedDataset,
    Aihub68GeometryDatasetAdapter,
    Aihub68GeometrySample,
)
from voice_coach.visual_feedback.adapters.p6_geometry_reload import (
    P6GeometryReloadAdapter,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry import (
    P6MediaPipeBatch,
    P6MediaPipeObservation,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry_worker import (
    BACKEND_REVISION,
    PROTOCOL_VERSION,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p6_geometry import (
    P6GeometryArtifact,
    P6GeometryEvidenceBundle,
)
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)
from voice_coach.visual_feedback.services.p6_geometry_calibration import (
    P6GeometryCalibrationService,
)


ROOT = Path(__file__).resolve().parents[2]


def _label_document(*, actor_id: str = "restricted_actor") -> dict[str, object]:
    points = []
    for index in range(24):
        points.extend([400 + index * 2, 500 + index, 2])
    return {
        "categories": [
            {
                "type": "lip",
                "type_id": 0,
                "keypoints": list(AIHUB68_KEYPOINT_NAMES),
                "segments": [[0, 11], [12, 23]],
            }
        ],
        "licenses": [{"id": 1, "name": "restricted", "url": "https://example"}],
        "info": {
            "version": "1.0",
            "description": "fixture",
            "contributor": "fixture",
            "url": "https://example",
            "date_created": "2021-01-01",
        },
        "annotations": [
            {
                "id": 1,
                "image_name": "frame.jpg",
                "image_id": 2,
                "video_id": 3,
                "bbox": [0, 0, 1, 1],
                "segmentation": [[], []],
                "area": [1, 1],
                "actor_id": actor_id,
                "word": "x",
                "word_id": 1,
                "num_keypoints": 24,
                "2D keypoints": points,
            }
        ],
        "videos": [
            {
                "id": 3,
                "frame_rate": 30.0,
                "width": 1600,
                "height": 1200,
                "camera_id": 0,
                "parameters": {"intrinsic": [], "extrinsic": []},
                "file_name": "video.mp4",
                "date_captured": "2021-01-01 00:00:00",
            }
        ],
        "images": [
            {
                "id": 2,
                "file_name": "frame.jpg",
                "width": 1600,
                "height": 1200,
                "frame_index": 0,
            }
        ],
        "meshs": [],
        "actor": [{"id": actor_id, "sex": "female", "age": 30}],
    }


def _archives(tmp_path: Path) -> tuple[Path, Path]:
    labels = tmp_path / "labels.zip"
    images = tmp_path / "images.zip"
    with ZipFile(labels, "w") as archive:
        archive.writestr(
            "partition/frame.json",
            json.dumps(_label_document()).encode("utf-8"),
        )
    with ZipFile(images, "w") as archive:
        archive.writestr("partition/frame.jpg", b"\xff\xd8fixture\xff\xd9")
    return labels, images


def _admit_paths(labels: Path, images: Path) -> Aihub68AdmittedDataset:
    label_fd = os.open(labels, os.O_RDONLY)
    image_fd = os.open(images, os.O_RDONLY)
    try:
        return Aihub68GeometryDatasetAdapter().admit(
            label_archive_descriptor=label_fd,
            image_archive_descriptor=image_fd,
        )
    finally:
        os.close(label_fd)
        os.close(image_fd)


def test_descriptor_admission_requires_exact_pair_and_24_point_schema(
    tmp_path: Path,
) -> None:
    labels, images = _archives(tmp_path)

    dataset = _admit_paths(labels, images)

    assert len(dataset.samples) == 1
    assert dataset.actor_count == 1
    assert dataset.camera_ids == (0,)
    assert "restricted_actor" not in dataset.dataset_manifest_sha256


def test_descriptor_admission_rejects_traversal_member(tmp_path: Path) -> None:
    labels, images = _archives(tmp_path)
    with ZipFile(labels, "w") as archive:
        archive.writestr(
            "../frame.json", json.dumps(_label_document()).encode("utf-8")
        )

    with pytest.raises(Aihub68AdmissionError) as caught:
        _admit_paths(labels, images)

    assert caught.value.reason_code == "aihub68_archive_member_path_invalid"
    assert str(tmp_path) not in str(caught.value)


def test_descriptor_admission_rejects_changed_keypoint_schema(tmp_path: Path) -> None:
    labels, images = _archives(tmp_path)
    document = _label_document()
    document["categories"][0]["keypoints"][0] = "changed"
    with ZipFile(labels, "w") as archive:
        archive.writestr("partition/frame.json", json.dumps(document).encode("utf-8"))

    with pytest.raises(Aihub68AdmissionError) as caught:
        _admit_paths(labels, images)

    assert caught.value.reason_code == "aihub68_24_point_schema_invalid"


def test_descriptor_admission_pins_word_id_nine_to_eo(tmp_path: Path) -> None:
    labels, images = _archives(tmp_path)
    document = _label_document()
    document["annotations"][0]["word_id"] = 9
    document["annotations"][0]["word"] = "어디"
    with ZipFile(labels, "w") as archive:
        archive.writestr("partition/frame.json", json.dumps(document).encode("utf-8"))

    with pytest.raises(Aihub68AdmissionError) as caught:
        _admit_paths(labels, images)

    assert caught.value.reason_code == "aihub68_label_value_invalid"


class _PerfectInference:
    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6MediaPipeBatch:
        observations = []
        for sample in samples:
            candidates = [(0.95, 0.95)] * len(MEDIAPIPE_LIP_CANDIDATE_INDICES)
            for index, point in enumerate(sample.points_xy):
                candidates[index] = point
            observations.append(
                P6MediaPipeObservation(
                    sample_sha256=sample.sample_sha256,
                    face_count=1,
                    candidate_points_xy=tuple(candidates),
                )
            )
        return P6MediaPipeBatch(
            observations=tuple(observations),
            backend_revision=BACKEND_REVISION,
            model_sha256="1" * 64,
            network_isolation="seccomp_socket_denied",
            candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        )


def _calibration_dataset() -> Aihub68AdmittedDataset:
    points = tuple(
        (0.30 + (index % 6) * 0.05, 0.40 + (index // 6) * 0.03)
        for index in range(24)
    )
    samples = []
    for actor_index in range(6):
        for frame_group in range(8):
            for camera_id in range(5):
                sample_sha256 = sha256_document(
                    {
                        "actor": actor_index,
                        "frame_group": frame_group,
                        "camera": camera_id,
                    }
                )
                samples.append(
                    Aihub68GeometrySample(
                        sample_sha256=sample_sha256,
                        actor_id=f"private_{actor_index}",
                        attempt_id=f"private_{actor_index}:{camera_id}",
                        camera_id=camera_id,
                        sex="female" if actor_index % 2 else "male",
                        age_band="30_49",
                        word="어디",
                        word_id=1,
                        frame_index=frame_group,
                        width=1600,
                        height=1200,
                        points_xy=points,
                        jpeg_bytes=b"\xff\xd8fixture\xff\xd9",
                    )
                )
    return Aihub68AdmittedDataset(
        samples=tuple(samples),
        dataset_manifest_sha256="2" * 64,
        label_archive_sha256="3" * 64,
        image_archive_sha256="4" * 64,
        population_id_sha256="5" * 64,
        actor_count=6,
        camera_ids=(0, 1, 2, 3, 4),
    )


def test_actor_disjoint_calibration_and_fresh_reload_are_self_validating() -> None:
    artifact = P6GeometryCalibrationService(_PerfectInference()).calibrate(
        _calibration_dataset(), rights_confirmed=True
    )
    reload_receipt = P6GeometryReloadAdapter(repository_root=ROOT).reload(artifact)
    bundle = P6GeometryEvidenceBundle.issue(
        artifact=artifact,
        reload_receipt=reload_receipt,
    )

    parsed = P6GeometryEvidenceBundle.from_json_object(bundle.to_json_object())

    assert parsed == bundle
    assert artifact.calibration_actor_count == 5
    assert artifact.development_actor_count == 1
    assert artifact.actor_overlap_count == 0
    assert artifact.attempt_overlap_count == 0
    assert artifact.thresholds_frozen_before_metric_access is True
    assert artifact.split_frozen_before_metric_access is True
    assert artifact.metrics.calibration_sample_count == 200
    assert artifact.metrics.calibration_coverage_fraction == 1.0
    assert artifact.metrics.calibration_normalized_landmark_error == pytest.approx(0)
    assert artifact.metrics.repeatability_normalized_cue_rmse == pytest.approx(0)
    serialized = json.dumps(bundle.to_json_object(), sort_keys=True)
    assert "private_" not in serialized
    assert str(ROOT) not in serialized


def test_bundle_parser_rejects_reload_cross_digest_tamper() -> None:
    artifact = P6GeometryCalibrationService(_PerfectInference()).calibrate(
        _calibration_dataset(), rights_confirmed=True
    )
    receipt = P6GeometryReloadAdapter(repository_root=ROOT).reload(artifact)
    document = P6GeometryEvidenceBundle.issue(
        artifact=artifact, reload_receipt=receipt
    ).to_json_object()
    document["reload_receipt"]["mapping_sha256"] = "f" * 64

    with pytest.raises(VisualFeedbackContractError):
        P6GeometryEvidenceBundle.from_json_object(document)


def test_calibration_worker_denies_network_socket() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_coach.visual_feedback.adapters.p6_mediapipe_geometry_worker",
            "--network-isolation-self-test",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "protocol_version": PROTOCOL_VERSION,
        "status": "passed",
        "network_isolation": "seccomp_socket_denied",
    }
