"""P6B phone-conditioned visible-lip producer contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_coach.visual_feedback.adapters.aihub68_geometry_dataset import (
    Aihub68AdmittedDataset,
    Aihub68GeometrySample,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry import (
    P6MediaPipeBatch,
    P6MediaPipeObservation,
)
from voice_coach.visual_feedback.adapters.p6_mediapipe_geometry_worker import (
    BACKEND_REVISION as CALIBRATION_BACKEND_REVISION,
)
from voice_coach.visual_feedback.adapters.p6_phone_reference_reload import (
    P6PhoneReferenceReloadAdapter,
)
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.p6_geometry import P6GeometryEvidenceBundle
from voice_coach.visual_feedback.models.p6_geometry_schema import (
    MEDIAPIPE_LIP_CANDIDATE_INDICES,
)
from voice_coach.visual_feedback.models.p6_phone_reference import (
    P6PhoneReferenceEvidenceBundle,
    P6PhoneReferenceProducerEvidence,
)
from voice_coach.visual_feedback.services.p6_geometry_calibration import (
    P6GeometryCalibrationService,
)
from voice_coach.visual_feedback.services.p6_phone_reference import (
    P6PhoneReferenceCalibrationService,
    P6ProductionCueBatch,
    P6ProductionCueObservation,
    validate_p6_phone_reference_producer,
)
from voice_coach.visual_feedback.adapters.p6_geometry_reload import (
    P6GeometryReloadAdapter,
)


ROOT = Path(__file__).resolve().parents[2]
MODEL_SHA256 = "9" * 64
PRODUCTION_WORKER_SHA256 = "8" * 64
PRODUCTION_REVISION = "mediapipe_1.0.1_face_landmarker_float16_v1"
ACTUAL_MAPPING = (
    61, 39, 37, 0, 267, 269, 291, 321, 314, 17, 84, 91,
    78, 80, 82, 13, 312, 310, 308, 318, 317, 14, 87, 88,
)


class _MappedInference:
    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6MediaPipeBatch:
        observations = []
        for sample in samples:
            points = [(0.98, 0.98)] * len(MEDIAPIPE_LIP_CANDIDATE_INDICES)
            offsets = {
                index: MEDIAPIPE_LIP_CANDIDATE_INDICES.index(index)
                for index in ACTUAL_MAPPING
            }
            for gold_offset, index in enumerate(ACTUAL_MAPPING):
                points[offsets[index]] = sample.points_xy[gold_offset]
            observations.append(
                P6MediaPipeObservation(
                    sample_sha256=sample.sample_sha256,
                    face_count=1,
                    candidate_points_xy=tuple(points),
                )
            )
        return P6MediaPipeBatch(
            observations=tuple(observations),
            backend_revision=CALIBRATION_BACKEND_REVISION,
            model_sha256=MODEL_SHA256,
            network_isolation="seccomp_socket_denied",
            candidate_indices=MEDIAPIPE_LIP_CANDIDATE_INDICES,
        )


class _MatchingProduction:
    def infer(
        self, samples: tuple[Aihub68GeometrySample, ...]
    ) -> P6ProductionCueBatch:
        observations = tuple(
            P6ProductionCueObservation(
                sample_sha256=sample.sample_sha256,
                face_count=1,
                aperture_ratio=_aperture(sample),
            )
            for sample in samples
        )
        return P6ProductionCueBatch(
            observations=observations,
            backend_revision=PRODUCTION_REVISION,
            worker_sha256=PRODUCTION_WORKER_SHA256,
            adapter_sha256="7" * 64,
            model_sha256=MODEL_SHA256,
        )


def _dataset() -> Aihub68AdmittedDataset:
    samples = []
    for actor in range(17):
        for camera in range(5):
            for ordinal, frame in enumerate((10, 0, 1, 2, 3, 4, 5, 6)):
                ratio = 0.20 + actor * 0.002 + camera * 0.003
                points = list(_base_points())
                points[0] = (0.30, 0.50)
                points[6] = (0.70, 0.50)
                points[15] = (0.50, 0.50 - ratio * 0.20)
                points[21] = (0.50, 0.50 + ratio * 0.20)
                sample_sha256 = sha256_document(
                    {"actor": actor, "camera": camera, "frame": frame}
                )
                samples.append(
                    Aihub68GeometrySample(
                        sample_sha256=sample_sha256,
                        actor_id=f"restricted_actor_{actor}",
                        attempt_id=f"restricted_actor_{actor}:{camera}",
                        camera_id=camera,
                        sex="female" if actor % 2 else "male",
                        age_band="30_49",
                        word="어" if ordinal == 0 else "어디",
                        word_id=9 if ordinal == 0 else 10,
                        frame_index=frame,
                        width=1600,
                        height=1200,
                        points_xy=tuple(points),
                        jpeg_bytes=b"\xff\xd8fixture\xff\xd9",
                    )
                )
    return Aihub68AdmittedDataset(
        samples=tuple(samples),
        dataset_manifest_sha256="1" * 64,
        label_archive_sha256="2" * 64,
        image_archive_sha256="3" * 64,
        population_id_sha256="4" * 64,
        actor_count=17,
        camera_ids=(0, 1, 2, 3, 4),
    )


def _base_points() -> tuple[tuple[float, float], ...]:
    return tuple(
        (0.25 + (index % 6) * 0.08, 0.35 + (index // 6) * 0.08)
        for index in range(24)
    )


def _aperture(sample: Aihub68GeometrySample) -> float:
    return abs(sample.points_xy[21][1] - sample.points_xy[15][1]) / abs(
        sample.points_xy[6][0] - sample.points_xy[0][0]
    )


def _geometry_bundle(dataset: Aihub68AdmittedDataset) -> P6GeometryEvidenceBundle:
    artifact = P6GeometryCalibrationService(_MappedInference()).calibrate(
        dataset, rights_confirmed=True
    )
    receipt = P6GeometryReloadAdapter(repository_root=ROOT).reload(artifact)
    return P6GeometryEvidenceBundle.issue(artifact=artifact, reload_receipt=receipt)


def test_phone_reference_is_actor_disjoint_visible_only_and_reloadable() -> None:
    dataset = _dataset()
    geometry_bundle = _geometry_bundle(dataset)
    producer = P6PhoneReferenceCalibrationService(
        calibration_inference=_MappedInference(),
        production_parity=_MatchingProduction(),
    ).calibrate(
        dataset=dataset,
        geometry_bundle=geometry_bundle,
        rights_confirmed=True,
    )
    reload_receipt = P6PhoneReferenceReloadAdapter(repository_root=ROOT).reload(
        producer
    )
    bundle = P6PhoneReferenceEvidenceBundle.issue(
        producer=producer, reload_receipt=reload_receipt
    )

    parsed = P6PhoneReferenceEvidenceBundle.from_json_object(
        bundle.to_json_object()
    )

    assert parsed == bundle
    assert producer.metrics.development_sample_count == 60
    assert producer.metrics.calibration_sample_count == 25
    assert producer.metrics.single_face_coverage == 1.0
    assert producer.metrics.calibration_support_coverage == 1.0
    assert producer.to_json_object()["source_selector"] == {
        "word_id": 9,
        "word": "어",
        "frame_index": 10,
        "expected_phone": "ㅓ",
    }
    assert producer.runtime_parity_receipt.mapping_indices == (61, 291, 13, 14)
    assert producer.runtime_parity_receipt.maximum_absolute_cue_difference == 0.0
    assert {claim.action_id for claim in producer.claim_catalog.claims} == {
        "reduce_visible_lip_opening",
        "increase_visible_lip_opening",
    }
    serialized = str(bundle.to_json_object())
    assert "restricted_actor" not in serialized
    assert "correctness" in producer.claim_policy.forbidden_identifier_tokens


def test_runtime_integration_requires_actual_production_worker_identity() -> None:
    dataset = _dataset()
    geometry_bundle = _geometry_bundle(dataset)
    producer = P6PhoneReferenceCalibrationService(
        calibration_inference=_MappedInference(),
        production_parity=_MatchingProduction(),
    ).calibrate(
        dataset=dataset,
        geometry_bundle=geometry_bundle,
        rights_confirmed=True,
    )

    validate_p6_phone_reference_producer(
        producer=producer,
        geometry_bundle=geometry_bundle,
        production_tracker_revision=PRODUCTION_REVISION,
        production_tracker_sha256=PRODUCTION_WORKER_SHA256,
        landmark_model_sha256=MODEL_SHA256,
    )

    with pytest.raises(Exception, match="p6b_production_identity_mismatch"):
        validate_p6_phone_reference_producer(
            producer=producer,
            geometry_bundle=geometry_bundle,
            production_tracker_revision=PRODUCTION_REVISION,
            production_tracker_sha256="0" * 64,
            landmark_model_sha256=MODEL_SHA256,
        )


def test_bundle_rejects_arbitrary_catalog_tamper() -> None:
    dataset = _dataset()
    geometry_bundle = _geometry_bundle(dataset)
    producer = P6PhoneReferenceCalibrationService(
        calibration_inference=_MappedInference(),
        production_parity=_MatchingProduction(),
    ).calibrate(
        dataset=dataset,
        geometry_bundle=geometry_bundle,
        rights_confirmed=True,
    )
    document = producer.to_json_object()
    document["claim_catalog"]["claims"][0]["action_id"] = "diagnose_tongue"

    with pytest.raises(VisualFeedbackContractError):
        P6PhoneReferenceProducerEvidence.from_json_object(document)
