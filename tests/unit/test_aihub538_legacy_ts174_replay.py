"""Explicit current-to-legacy TS174 admission replay binding tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding import (
    bind_aihub538_legacy_ts174_replay_aggregate,
)
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import (
    AIHUB538_TS174_TL174_SHARD,
)
from voice_coach.visual_feedback.models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY,
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    Aihub538RestrictedIdentitySet,
    Aihub538RestrictedMemberSet,
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingAngleAggregate,
    Aihub538TrainingSampleAggregate,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
)


def _sha(label: str) -> str:
    return sha256_document({"fixture": label})


def _sample(angle: str) -> Aihub538TrainingSampleAggregate:
    return Aihub538TrainingSampleAggregate(
        sample_sha256=_sha(f"sample-{angle}"),
        angle=angle,
        noise_code=1,
        video_duration_seconds=1.0,
        audio_duration_seconds=1.0,
        frames_per_second=30.0,
        width=1920,
        height=1080,
        lip_bbox_frame_count=30,
        face_bbox_frame_count=30,
        sentence_count=1,
        sentence_duration_seconds=0.8,
        video_size_bytes=100,
        audio_size_bytes=20,
        video_crc32=1,
        audio_crc32=2,
    )


def _selection_sha256(
    aggregate: Aihub538TrainingAdmissionAggregate,
    *,
    policy_sha256: str,
) -> str:
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_training_sample_selection.v1"
            ),
            "selection_rule": aggregate.sample_selection_rule,
            "admission_policy_sha256": policy_sha256,
            "sample_sha256": [
                item.sample_sha256 for item in aggregate.samples
            ],
        }
    )


def _manifest_sha256(
    aggregate: Aihub538TrainingAdmissionAggregate,
    *,
    selection_sha256: str,
) -> str:
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_training_admission_manifest.v1"
            ),
            "label_archive_sha256": aggregate.label_archive_sha256,
            "source_archive_sha256": aggregate.source_archive_sha256,
            "label_content_root_sha256": (
                aggregate.label_zip_content_root_sha256
            ),
            "source_content_root_sha256": (
                aggregate.source_zip_content_root_sha256
            ),
            "sample_selection_sha256": selection_sha256,
            "paired_sample_count": len(aggregate.samples),
            "unique_audio_count": aggregate.unique_audio_count,
            "speaker_count": aggregate.speaker_count,
            "session_count": len(
                aggregate.restricted_identity_set.session_hmac_sha256
            ),
            "identity_namespace_commitment_sha256": (
                aggregate.restricted_identity_set.namespace_commitment_sha256
            ),
            "view_aggregates": [
                item.to_json_object() for item in aggregate.angle_aggregates
            ],
        }
    )


def _current_aggregate() -> Aihub538TrainingAdmissionAggregate:
    samples = tuple(sorted(
        (_sample(angle) for angle in AIHUB538_TRAINING_ANGLES),
        key=lambda item: item.sample_sha256,
    ))
    angles = tuple(
        Aihub538TrainingAngleAggregate(
            angle=angle,
            paired_sample_count=1,
            label_count=1,
            video_count=1,
            audio_reference_count=1,
            lip_bbox_frame_count=30,
            sentence_count=1,
        )
        for angle in AIHUB538_TRAINING_ANGLES
    )
    draft = Aihub538TrainingAdmissionAggregate(
        label_archive_sha256=_sha("label-archive"),
        source_archive_sha256=_sha("source-archive"),
        label_zip_content_root_sha256=_sha("label-content-root"),
        source_zip_content_root_sha256=_sha("source-content-root"),
        samples=samples,
        speaker_count=9,
        unique_audio_count=9,
        angle_aggregates=angles,
        sample_selection_sha256="0" * 64,
        admission_manifest_sha256="0" * 64,
        restricted_identity_set=Aihub538RestrictedIdentitySet(
            namespace_commitment_sha256=_sha("identity-namespace"),
            speaker_hmac_sha256=frozenset(
                _sha(f"speaker-{index}") for index in range(9)
            ),
            session_hmac_sha256=frozenset(
                _sha(f"session-{index}") for index in range(9)
            ),
        ),
        restricted_member_set=Aihub538RestrictedMemberSet(
            namespace_commitment_sha256=_sha("member-namespace"),
            relative_label_path_hmac_sha256=frozenset(
                _sha(f"member-{index}") for index in range(9)
            ),
        ),
    )
    selection = _selection_sha256(
        draft,
        policy_sha256=AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    )
    return replace(
        draft,
        sample_selection_sha256=selection,
        admission_manifest_sha256=_manifest_sha256(
            draft,
            selection_sha256=selection,
        ),
    )


def _legacy_receipt(
    current: Aihub538TrainingAdmissionAggregate,
) -> tuple[Aihub538TrainingAdmissionAggregate, Aihub538TrainingAdmissionReceipt]:
    selection = _selection_sha256(
        current,
        policy_sha256=AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    )
    legacy_bound = replace(
        current,
        admission_policy_sha256=(
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        ),
        sample_selection_sha256=selection,
        admission_manifest_sha256=_manifest_sha256(
            current,
            selection_sha256=selection,
        ),
    )
    return legacy_bound, Aihub538TrainingAdmissionReceipt.issue(legacy_bound)


def test_historical_policy_document_reproduces_the_pinned_digest() -> None:
    assert AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY["schema_version"] == (
        "voice_coach.aihub538_training_admission_policy.v2"
    )
    assert AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY["label_file_key"] == (
        "54944"
    )
    assert AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY["source_file_key"] == (
        "54725"
    )
    assert sha256_document(AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY) == (
        AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
    )


def test_current_replay_is_explicitly_bound_to_exact_legacy_receipt() -> None:
    current = _current_aggregate()
    expected_legacy_bound, legacy_receipt = _legacy_receipt(current)

    rebound = bind_aihub538_legacy_ts174_replay_aggregate(
        current_aggregate=current,
        legacy_receipt=legacy_receipt,
    )

    assert current.sample_selection_sha256 != rebound.sample_selection_sha256
    assert current.admission_manifest_sha256 != rebound.admission_manifest_sha256
    assert rebound == expected_legacy_bound
    assert rebound.samples is current.samples
    assert rebound.restricted_identity_set is current.restricted_identity_set
    assert rebound.restricted_member_set is current.restricted_member_set
    assert Aihub538TrainingAdmissionReceipt.issue(rebound) == legacy_receipt
    assert legacy_receipt.shard_id == AIHUB538_TS174_TL174_SHARD.shard_id


@pytest.mark.parametrize(
    "mutation",
    ("current-selection", "already-legacy", "receipt-archive"),
)
def test_replay_binding_rejects_any_unproven_or_reinterpreted_lineage(
    mutation: str,
) -> None:
    current = _current_aggregate()
    _expected_legacy_bound, legacy_receipt = _legacy_receipt(current)
    if mutation == "current-selection":
        current = replace(current, sample_selection_sha256=_sha("tampered"))
    elif mutation == "already-legacy":
        current = replace(
            current,
            admission_policy_sha256=(
                AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
            ),
        )
    else:
        draft = replace(
            legacy_receipt,
            label_archive_sha256=_sha("different-label-archive"),
            receipt_sha256="0" * 64,
        )
        legacy_receipt = replace(
            draft,
            receipt_sha256=sha256_document(draft._unsigned_json()),
        )
        legacy_receipt._validate()

    with pytest.raises(
        Aihub538DecisionCalibrationError,
        match="aihub538_legacy_ts174_replay_binding_failed",
    ):
        bind_aihub538_legacy_ts174_replay_aggregate(
            current_aggregate=current,
            legacy_receipt=legacy_receipt,
        )
