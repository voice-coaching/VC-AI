"""Aggregate-only contracts for AI Hub 538 TS174/TL174 training smoke.

The contracts deliberately cannot authorize P6/P8 promotion.  They prove only
that one non-locked training shard was downloaded, paired, schema-checked, and
run through a metadata/envelope smoke evaluator.  Actual video decode, visual
QC calibration, A/V sync-anchor evaluation, and locked-partition evaluation
remain separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .integrity import require_sha256, sha256_document


AIHUB538_TRAINING_ANGLES = tuple("ABCDEFGHI")
AIHUB538_TRAINING_ADMISSION_POLICY = {
    "schema_version": "voice_coach.aihub538_training_admission_policy.v1",
    "dataset_id": 538,
    "dataset_partition": "nonlocked_training_ts174_tl174",
    "source_file_key": "54725",
    "label_file_key": "54944",
    "source_role_component": "TS174",
    "label_role_component": "TL174",
    "creator_schema": "IIP-Sogang/olkavs-avspeech.preprocess.py",
    "creator_repository_revision": "af2feda3e3ac91ff3fef2454fcee662366ce9f4b",
    "creator_preprocess_sha256": (
        "a3e6bfc1943ac8754f81cf89245e1044663fbc658b4bc00cc63c30aa1b87d6c6"
    ),
    "creator_readme_sha256": (
        "a2c16d502f65d135f23256ce9d957480d049dca55cc16502a94d6278f1c43eb9"
    ),
    "label_document_rule": "json_array_exactly_one_object",
    "label_to_source_rule": "TL174_to_TS174_same_relative_stem",
    "audio_rule": "non_A_view_uses_same_session_A_wav",
    "lip_bbox_rule": "Bounding_box_info.Lip_bounding_box.xtl_ytl_xbr_ybr",
    "sentence_rule": "Sentence_info_start_time_end_time",
    "transcript_retained": False,
    "source_paths_retained": False,
    "speaker_identifiers_retained": False,
    "actual_media_decode_required": False,
    "threshold_calibration_authorized": False,
    "promotion_authorized": False,
}
AIHUB538_TRAINING_ADMISSION_POLICY_SHA256 = sha256_document(
    AIHUB538_TRAINING_ADMISSION_POLICY
)


@dataclass(frozen=True, slots=True)
class Aihub538TrainingSampleAggregate:
    """Restricted in-memory row with no raw name, path, transcript, or speaker ID."""

    sample_sha256: str
    angle: str
    noise_code: int
    video_duration_seconds: float
    audio_duration_seconds: float
    frames_per_second: float
    width: int
    height: int
    lip_bbox_frame_count: int
    face_bbox_frame_count: int
    sentence_count: int
    sentence_duration_seconds: float
    video_size_bytes: int
    audio_size_bytes: int
    video_crc32: int
    audio_crc32: int

    def __post_init__(self) -> None:
        require_sha256(self.sample_sha256, "sample_sha256")
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise ValueError("aihub538_training_sample_angle_invalid")


@dataclass(frozen=True, slots=True)
class Aihub538TrainingAngleAggregate:
    """Per-view counts only; no sample-level identifiers are serialized."""

    angle: str
    paired_sample_count: int
    label_count: int
    video_count: int
    audio_reference_count: int
    lip_bbox_frame_count: int
    sentence_count: int

    def __post_init__(self) -> None:
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise ValueError("aihub538_training_angle_aggregate_invalid")
        counts = (
            self.paired_sample_count,
            self.label_count,
            self.video_count,
            self.audio_reference_count,
            self.lip_bbox_frame_count,
            self.sentence_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("aihub538_training_angle_aggregate_invalid")
        if not (
            self.paired_sample_count
            == self.label_count
            == self.video_count
            == self.audio_reference_count
        ):
            raise ValueError("aihub538_training_angle_aggregate_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            "angle": self.angle,
            "paired_sample_count": self.paired_sample_count,
            "label_count": self.label_count,
            "video_count": self.video_count,
            "audio_reference_count": self.audio_reference_count,
            "lip_bbox_frame_count": self.lip_bbox_frame_count,
            "sentence_count": self.sentence_count,
        }


@dataclass(frozen=True, slots=True)
class Aihub538TrainingAdmissionAggregate:
    """Private adapter result retaining sanitized rows plus aggregate lineage."""

    label_archive_sha256: str
    source_archive_sha256: str
    label_zip_content_root_sha256: str
    source_zip_content_root_sha256: str
    samples: tuple[Aihub538TrainingSampleAggregate, ...]
    speaker_count: int
    unique_audio_count: int
    angle_aggregates: tuple[Aihub538TrainingAngleAggregate, ...]
    sample_selection_sha256: str
    admission_manifest_sha256: str
    restricted_identity_set: "Aihub538RestrictedIdentitySet"


@dataclass(frozen=True, slots=True)
class Aihub538RestrictedIdentitySet:
    """Non-serializable keyed actor/attempt tokens for partition intersection.

    Individual HMAC values must remain inside the restricted process.  Only the
    namespace commitment and aggregate intersection counts may be serialized.
    """

    namespace_commitment_sha256: str
    speaker_hmac_sha256: frozenset[str]
    session_hmac_sha256: frozenset[str]

    def __post_init__(self) -> None:
        require_sha256(
            self.namespace_commitment_sha256,
            "identity_namespace_commitment_sha256",
        )
        if not self.speaker_hmac_sha256 or not self.session_hmac_sha256:
            raise ValueError("aihub538_restricted_identity_set_empty")
        for value in self.speaker_hmac_sha256 | self.session_hmac_sha256:
            require_sha256(value, "restricted_identity_hmac_sha256")


@dataclass(frozen=True, slots=True)
class Aihub538IdentityIntersectionReceipt:
    """Aggregate-only actor/attempt overlap check between two role partitions."""

    left_manifest_sha256: str
    right_manifest_sha256: str
    namespace_commitment_sha256: str
    left_speaker_count: int
    right_speaker_count: int
    speaker_intersection_count: int
    left_session_count: int
    right_session_count: int
    session_intersection_count: int
    receipt_sha256: str

    @classmethod
    def compare(
        cls,
        *,
        left_manifest_sha256: str,
        right_manifest_sha256: str,
        left: Aihub538RestrictedIdentitySet,
        right: Aihub538RestrictedIdentitySet,
    ) -> "Aihub538IdentityIntersectionReceipt":
        if left.namespace_commitment_sha256 != right.namespace_commitment_sha256:
            raise ValueError("aihub538_identity_namespace_mismatch")
        draft = cls(
            left_manifest_sha256=left_manifest_sha256,
            right_manifest_sha256=right_manifest_sha256,
            namespace_commitment_sha256=left.namespace_commitment_sha256,
            left_speaker_count=len(left.speaker_hmac_sha256),
            right_speaker_count=len(right.speaker_hmac_sha256),
            speaker_intersection_count=len(
                left.speaker_hmac_sha256 & right.speaker_hmac_sha256
            ),
            left_session_count=len(left.session_hmac_sha256),
            right_session_count=len(right.session_hmac_sha256),
            session_intersection_count=len(
                left.session_hmac_sha256 & right.session_hmac_sha256
            ),
            receipt_sha256="0" * 64,
        )
        unsigned = draft._unsigned_json()
        issued = cls(
            left_manifest_sha256=draft.left_manifest_sha256,
            right_manifest_sha256=draft.right_manifest_sha256,
            namespace_commitment_sha256=draft.namespace_commitment_sha256,
            left_speaker_count=draft.left_speaker_count,
            right_speaker_count=draft.right_speaker_count,
            speaker_intersection_count=draft.speaker_intersection_count,
            left_session_count=draft.left_session_count,
            right_session_count=draft.right_session_count,
            session_intersection_count=draft.session_intersection_count,
            receipt_sha256=sha256_document(unsigned),
        )
        issued._validate()
        return issued

    @property
    def is_disjoint(self) -> bool:
        return (
            self.speaker_intersection_count == 0
            and self.session_intersection_count == 0
        )

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json()
        document["receipt_sha256"] = self.receipt_sha256
        return document

    def _unsigned_json(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_identity_intersection_receipt.v1",
            "status": (
                "verified_disjoint" if self.is_disjoint else "overlap_detected_failed_closed"
            ),
            "left_manifest_sha256": self.left_manifest_sha256,
            "right_manifest_sha256": self.right_manifest_sha256,
            "identity_namespace_commitment_sha256": (
                self.namespace_commitment_sha256
            ),
            "left_speaker_count": self.left_speaker_count,
            "right_speaker_count": self.right_speaker_count,
            "speaker_intersection_count": self.speaker_intersection_count,
            "left_session_count": self.left_session_count,
            "right_session_count": self.right_session_count,
            "session_intersection_count": self.session_intersection_count,
            "individual_identity_digests_serialized": False,
            "aggregate_only": True,
            "disjoint": self.is_disjoint,
            "promotion_authorized": False,
        }

    def _validate(self) -> None:
        require_sha256(self.left_manifest_sha256, "left_manifest_sha256")
        require_sha256(self.right_manifest_sha256, "right_manifest_sha256")
        require_sha256(
            self.namespace_commitment_sha256,
            "identity_namespace_commitment_sha256",
        )
        require_sha256(self.receipt_sha256, "receipt_sha256")
        counts = (
            self.left_speaker_count,
            self.right_speaker_count,
            self.speaker_intersection_count,
            self.left_session_count,
            self.right_session_count,
            self.session_intersection_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("aihub538_identity_intersection_count_invalid")
        if (
            self.left_speaker_count <= 0
            or self.right_speaker_count <= 0
            or self.left_session_count <= 0
            or self.right_session_count <= 0
            or self.speaker_intersection_count
            > min(self.left_speaker_count, self.right_speaker_count)
            or self.session_intersection_count
            > min(self.left_session_count, self.right_session_count)
        ):
            raise ValueError("aihub538_identity_intersection_count_invalid")
        if sha256_document(self._unsigned_json()) != self.receipt_sha256:
            raise ValueError("aihub538_identity_intersection_integrity_failed")


@dataclass(frozen=True, slots=True)
class Aihub538TrainingAdmissionReceipt:
    """Immutable, aggregate-only proof of exact TS174/TL174 pairing."""

    label_archive_sha256: str
    source_archive_sha256: str
    label_zip_content_root_sha256: str
    source_zip_content_root_sha256: str
    paired_sample_count: int
    label_count: int
    video_count: int
    unique_audio_count: int
    audio_reference_count: int
    speaker_count: int
    lip_bbox_frame_count: int
    sentence_count: int
    angle_aggregates: tuple[Aihub538TrainingAngleAggregate, ...]
    sample_selection_sha256: str
    admission_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        aggregate: Aihub538TrainingAdmissionAggregate,
    ) -> "Aihub538TrainingAdmissionReceipt":
        sample_count = len(aggregate.samples)
        receipt = cls(
            label_archive_sha256=aggregate.label_archive_sha256,
            source_archive_sha256=aggregate.source_archive_sha256,
            label_zip_content_root_sha256=(
                aggregate.label_zip_content_root_sha256
            ),
            source_zip_content_root_sha256=(
                aggregate.source_zip_content_root_sha256
            ),
            paired_sample_count=sample_count,
            label_count=sample_count,
            video_count=sample_count,
            unique_audio_count=aggregate.unique_audio_count,
            audio_reference_count=sample_count,
            speaker_count=aggregate.speaker_count,
            lip_bbox_frame_count=sum(
                sample.lip_bbox_frame_count for sample in aggregate.samples
            ),
            sentence_count=sum(sample.sentence_count for sample in aggregate.samples),
            angle_aggregates=aggregate.angle_aggregates,
            sample_selection_sha256=aggregate.sample_selection_sha256,
            admission_manifest_sha256=aggregate.admission_manifest_sha256,
            identity_namespace_commitment_sha256=(
                aggregate.restricted_identity_set.namespace_commitment_sha256
            ),
            receipt_sha256="0" * 64,
        )
        unsigned = receipt._unsigned_json()
        issued = cls(
            **{
                field: getattr(receipt, field)
                for field in receipt.__dataclass_fields__
                if field != "receipt_sha256"
            },
            receipt_sha256=sha256_document(unsigned),
        )
        issued._validate()
        return issued

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json()
        document["receipt_sha256"] = self.receipt_sha256
        return document

    def _unsigned_json(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_training_admission_receipt.v1",
            "status": "completed_nonlocked_training_admission",
            "dataset_id": 538,
            "dataset_partition": "nonlocked_training_ts174_tl174",
            "dataset_role": "training_smoke_only_not_locked_evaluation",
            "dataset_access_permission_confirmed": True,
            "permission_grants_technical_promotion": False,
            "aggregate_only": True,
            "admission_policy_sha256": AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
            "label_archive_sha256": self.label_archive_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "label_zip_content_root_sha256": self.label_zip_content_root_sha256,
            "source_zip_content_root_sha256": self.source_zip_content_root_sha256,
            "paired_sample_count": self.paired_sample_count,
            "label_count": self.label_count,
            "video_count": self.video_count,
            "unique_audio_count": self.unique_audio_count,
            "audio_reference_count": self.audio_reference_count,
            "speaker_count": self.speaker_count,
            "lip_bbox_frame_count": self.lip_bbox_frame_count,
            "sentence_count": self.sentence_count,
            "view_aggregates": [
                item.to_json_object() for item in self.angle_aggregates
            ],
            "sample_selection_sha256": self.sample_selection_sha256,
            "admission_manifest_sha256": self.admission_manifest_sha256,
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "individual_identity_digests_serialized": False,
            "exact_label_video_audio_binding_validated": True,
            "non_A_views_bound_to_A_audio": True,
            "source_paths_retained": False,
            "speaker_identifiers_retained": False,
            "transcripts_retained": False,
            "actual_media_decode_executed": False,
            "qc_threshold_calibrated": False,
            "av_sync_threshold_calibrated": False,
            "promotion_authorized": False,
            "locked_partition_accessed": False,
        }

    def _validate(self) -> None:
        for value in (
            self.label_archive_sha256,
            self.source_archive_sha256,
            self.label_zip_content_root_sha256,
            self.source_zip_content_root_sha256,
            self.sample_selection_sha256,
            self.admission_manifest_sha256,
            self.identity_namespace_commitment_sha256,
            self.receipt_sha256,
        ):
            require_sha256(value, "aihub538_training_receipt_sha256")
        integer_counts = (
            self.paired_sample_count,
            self.label_count,
            self.video_count,
            self.unique_audio_count,
            self.audio_reference_count,
            self.speaker_count,
            self.lip_bbox_frame_count,
            self.sentence_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integer_counts
        ):
            raise ValueError("aihub538_training_receipt_count_invalid")
        if not (
            self.paired_sample_count
            == self.label_count
            == self.video_count
            == self.audio_reference_count
            == sum(item.paired_sample_count for item in self.angle_aggregates)
        ):
            raise ValueError("aihub538_training_receipt_pair_count_invalid")
        if tuple(item.angle for item in self.angle_aggregates) != (
            AIHUB538_TRAINING_ANGLES
        ):
            raise ValueError("aihub538_training_receipt_view_aggregate_invalid")
        if (
            self.unique_audio_count > self.audio_reference_count
            or self.lip_bbox_frame_count
            != sum(item.lip_bbox_frame_count for item in self.angle_aggregates)
            or self.sentence_count
            != sum(item.sentence_count for item in self.angle_aggregates)
        ):
            raise ValueError("aihub538_training_receipt_view_aggregate_invalid")
        if sha256_document(self._unsigned_json()) != self.receipt_sha256:
            raise ValueError("aihub538_training_receipt_integrity_failed")


@dataclass(frozen=True, slots=True)
class Aihub538TrainingEvaluatorSmokeReceipt:
    """Metadata/envelope smoke result that cannot issue runtime thresholds."""

    admission_receipt_sha256: str
    paired_sample_count: int
    evaluated_sample_count: int
    maximum_audio_video_duration_delta_seconds: float
    maximum_bbox_frame_delta: int
    total_lip_bbox_frame_count: int
    total_sentence_count: int
    sample_selection_sha256: str
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        aggregate: Aihub538TrainingAdmissionAggregate,
    ) -> "Aihub538TrainingEvaluatorSmokeReceipt":
        samples = aggregate.samples
        if (
            admission_receipt.admission_manifest_sha256
            != aggregate.admission_manifest_sha256
            or admission_receipt.sample_selection_sha256
            != aggregate.sample_selection_sha256
            or admission_receipt.paired_sample_count != len(samples)
        ):
            raise ValueError("aihub538_training_smoke_admission_binding_invalid")
        maximum_duration_delta = max(
            abs(sample.video_duration_seconds - sample.audio_duration_seconds)
            for sample in samples
        )
        maximum_bbox_delta = max(
            abs(
                sample.lip_bbox_frame_count
                - round(sample.video_duration_seconds * sample.frames_per_second)
            )
            for sample in samples
        )
        draft = cls(
            admission_receipt_sha256=admission_receipt.receipt_sha256,
            paired_sample_count=admission_receipt.paired_sample_count,
            evaluated_sample_count=len(samples),
            maximum_audio_video_duration_delta_seconds=maximum_duration_delta,
            maximum_bbox_frame_delta=maximum_bbox_delta,
            total_lip_bbox_frame_count=sum(
                sample.lip_bbox_frame_count for sample in samples
            ),
            total_sentence_count=sum(sample.sentence_count for sample in samples),
            sample_selection_sha256=aggregate.sample_selection_sha256,
            receipt_sha256="0" * 64,
        )
        unsigned = draft._unsigned_json()
        issued = cls(
            admission_receipt_sha256=draft.admission_receipt_sha256,
            paired_sample_count=draft.paired_sample_count,
            evaluated_sample_count=draft.evaluated_sample_count,
            maximum_audio_video_duration_delta_seconds=(
                draft.maximum_audio_video_duration_delta_seconds
            ),
            maximum_bbox_frame_delta=draft.maximum_bbox_frame_delta,
            total_lip_bbox_frame_count=draft.total_lip_bbox_frame_count,
            total_sentence_count=draft.total_sentence_count,
            sample_selection_sha256=draft.sample_selection_sha256,
            receipt_sha256=sha256_document(unsigned),
        )
        issued._validate()
        return issued

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json()
        document["receipt_sha256"] = self.receipt_sha256
        return document

    def _unsigned_json(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_training_evaluator_smoke_receipt.v1",
            "status": "completed_metadata_and_container_envelope_smoke",
            "dataset_id": 538,
            "dataset_partition": "nonlocked_training_ts174_tl174",
            "evaluation_scope": "schema_pairing_timing_bbox_and_media_envelope_only",
            "admission_receipt_sha256": self.admission_receipt_sha256,
            "paired_sample_count": self.paired_sample_count,
            "evaluated_sample_count": self.evaluated_sample_count,
            "maximum_audio_video_duration_delta_seconds": (
                self.maximum_audio_video_duration_delta_seconds
            ),
            "maximum_bbox_frame_delta": self.maximum_bbox_frame_delta,
            "total_lip_bbox_frame_count": self.total_lip_bbox_frame_count,
            "total_sentence_count": self.total_sentence_count,
            "sample_selection_sha256": self.sample_selection_sha256,
            "all_admitted_samples_evaluated": True,
            "mp4_envelopes_validated": True,
            "wav_envelopes_validated": True,
            "actual_video_decode_executed": False,
            "actual_audio_decode_executed": False,
            "actual_av_sync_anchor_evaluated": False,
            "qc_threshold_artifact_issued": False,
            "av_sync_threshold_artifact_issued": False,
            "threshold_calibration_authorized": False,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
            "limitations": [
                "training_shard_not_locked_evaluation",
                "container_envelope_is_not_decode_validation",
                "metadata_duration_agreement_is_not_av_sync_measurement",
                "bbox_presence_is_not_tracker_or_qc_calibration",
            ],
        }

    def _validate(self) -> None:
        require_sha256(self.admission_receipt_sha256, "admission_receipt_sha256")
        require_sha256(self.sample_selection_sha256, "sample_selection_sha256")
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if (
            self.paired_sample_count <= 0
            or self.evaluated_sample_count != self.paired_sample_count
            or self.total_lip_bbox_frame_count <= 0
            or self.total_sentence_count <= 0
            or self.maximum_bbox_frame_delta < 0
            or not math.isfinite(self.maximum_audio_video_duration_delta_seconds)
            or self.maximum_audio_video_duration_delta_seconds < 0.0
        ):
            raise ValueError("aihub538_training_smoke_receipt_invalid")
        if sha256_document(self._unsigned_json()) != self.receipt_sha256:
            raise ValueError("aihub538_training_smoke_receipt_integrity_failed")


__all__ = [
    "AIHUB538_TRAINING_ADMISSION_POLICY",
    "AIHUB538_TRAINING_ADMISSION_POLICY_SHA256",
    "AIHUB538_TRAINING_ANGLES",
    "Aihub538TrainingAdmissionAggregate",
    "Aihub538TrainingAdmissionReceipt",
    "Aihub538TrainingAngleAggregate",
    "Aihub538TrainingEvaluatorSmokeReceipt",
    "Aihub538TrainingSampleAggregate",
    "Aihub538IdentityIntersectionReceipt",
    "Aihub538RestrictedIdentitySet",
]
