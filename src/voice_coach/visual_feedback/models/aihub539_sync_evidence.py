"""Pure aggregate-only contracts for the AI Hub 539 sync-label audit.

The contract intentionally describes label schema consistency, not observed
audio/video synchronization.  Restricted names, paths, speaker identifiers,
transcript text, landmarks, and per-document hashes never enter the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .integrity import canonical_json_bytes, require_sha256, sha256_document


AIHUB539_CAMERAS = ("A", "B", "C", "D")
AIHUB539_DOCUMENT_COUNT = 13
AIHUB539_ACTORS_PER_DOCUMENT = 2
AIHUB539_FACE_POINT_COUNT = 28
AIHUB539_BODY_POINT_COUNT = 21
AIHUB539_TWO_DIMENSIONAL_POINT_SIZE = 2
AIHUB539_THREE_DIMENSIONAL_POINT_SIZE = 3
AIHUB539_AV_DURATION_TOLERANCE_SECONDS = 0.001
AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES = 0.5

AIHUB539_SYNC_AUDIT_POLICY = {
    "schema_version": "voice_coach.aihub539_sync_label_audit_policy.v1",
    "dataset_id": 539,
    "dataset_partition": "training_label_shard_8",
    "expected_document_count": AIHUB539_DOCUMENT_COUNT,
    "expected_extraction_receipt_count": 1,
    "expected_cameras": list(AIHUB539_CAMERAS),
    "actors_per_document": AIHUB539_ACTORS_PER_DOCUMENT,
    "face_point_count": AIHUB539_FACE_POINT_COUNT,
    "body_point_count": AIHUB539_BODY_POINT_COUNT,
    "two_dimensional_point_size": AIHUB539_TWO_DIMENSIONAL_POINT_SIZE,
    "three_dimensional_point_size": AIHUB539_THREE_DIMENSIONAL_POINT_SIZE,
    "audio_video_duration_tolerance_seconds": (
        AIHUB539_AV_DURATION_TOLERANCE_SECONDS
    ),
    "frame_agreement_tolerance_frames": (
        AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES
    ),
    "motion_transcript_actor_namespaces_distinct": True,
    "cross_namespace_actor_binding_validated": False,
    "actual_media_sync_validated": False,
    "promotion_authorized": False,
}
AIHUB539_SYNC_AUDIT_POLICY_SHA256 = sha256_document(AIHUB539_SYNC_AUDIT_POLICY)

_RECEIPT_KEYS = {
    "actors_per_document",
    "actual_media_sync_validated",
    "aggregate_only",
    "all_track_frame_counts_equal",
    "audio_video_duration_tolerance_seconds",
    "audit_policy_sha256",
    "body_point_count",
    "camera_ids",
    "cross_namespace_actor_binding_validated",
    "dataset_access_permission_confirmed",
    "dataset_access_permission_scope",
    "dataset_id",
    "dataset_partition",
    "dataset_role",
    "document_encoding",
    "extraction_receipt_count",
    "face_point_count",
    "frame_agreement_tolerance_frames",
    "keypoint_schema_sha256",
    "limitations",
    "motion_transcript_actor_namespaces_distinct",
    "permission_grants_technical_promotion",
    "promotion_authorized",
    "receipt_sha256",
    "schema_version",
    "sentence_counts_and_time_bounds_validated",
    "source_document_count",
    "source_hash_count",
    "source_manifest_sha256",
    "status",
    "three_dimensional_point_size",
    "three_dimensional_slice",
    "timing",
    "total_source_bytes",
    "two_dimensional_camera_slices",
    "two_dimensional_point_size",
    "validation_scope",
}
_TIMING_KEYS = {
    "frames_per_second",
    "maximum_audio_video_duration_delta_seconds",
    "maximum_duration_seconds",
    "maximum_frame_agreement_error_frames",
    "maximum_sentence_count",
    "maximum_timeline_frame_count",
    "minimum_duration_seconds",
    "minimum_sentence_count",
    "minimum_timeline_frame_count",
    "total_duration_seconds",
    "total_sentence_count",
    "total_timeline_frame_count",
}
_CAMERA_SLICE_KEYS = {
    "actor_frame_count",
    "actor_track_count",
    "body_point_observation_count",
    "camera_id",
    "document_count",
    "face_point_observation_count",
    "timeline_frame_count",
}
_THREE_DIMENSIONAL_SLICE_KEYS = {
    "actor_frame_count",
    "actor_track_count",
    "body_point_observation_count",
    "face_point_observation_count",
}


@dataclass(frozen=True, slots=True)
class Aihub539CameraSlice:
    """Aggregate two-dimensional tracking counts for one public camera code."""

    camera_id: str
    document_count: int
    actor_track_count: int
    timeline_frame_count: int
    actor_frame_count: int
    face_point_observation_count: int
    body_point_observation_count: int

    def to_json_object(self) -> dict[str, object]:
        """Return a path- and identifier-free camera slice."""

        return {
            "camera_id": self.camera_id,
            "document_count": self.document_count,
            "actor_track_count": self.actor_track_count,
            "timeline_frame_count": self.timeline_frame_count,
            "actor_frame_count": self.actor_frame_count,
            "face_point_observation_count": self.face_point_observation_count,
            "body_point_observation_count": self.body_point_observation_count,
        }


@dataclass(frozen=True, slots=True)
class Aihub539TimingAggregate:
    """Timing and transcript-bound results retained without row details."""

    total_duration_seconds: float
    minimum_duration_seconds: float
    maximum_duration_seconds: float
    frames_per_second: float
    total_timeline_frame_count: int
    minimum_timeline_frame_count: int
    maximum_timeline_frame_count: int
    maximum_audio_video_duration_delta_seconds: float
    maximum_frame_agreement_error_frames: float
    total_sentence_count: int
    minimum_sentence_count: int
    maximum_sentence_count: int

    def to_json_object(self) -> dict[str, object]:
        """Return deterministic aggregate timing metrics."""

        return {
            "total_duration_seconds": self.total_duration_seconds,
            "minimum_duration_seconds": self.minimum_duration_seconds,
            "maximum_duration_seconds": self.maximum_duration_seconds,
            "frames_per_second": self.frames_per_second,
            "total_timeline_frame_count": self.total_timeline_frame_count,
            "minimum_timeline_frame_count": self.minimum_timeline_frame_count,
            "maximum_timeline_frame_count": self.maximum_timeline_frame_count,
            "maximum_audio_video_duration_delta_seconds": (
                self.maximum_audio_video_duration_delta_seconds
            ),
            "maximum_frame_agreement_error_frames": (
                self.maximum_frame_agreement_error_frames
            ),
            "total_sentence_count": self.total_sentence_count,
            "minimum_sentence_count": self.minimum_sentence_count,
            "maximum_sentence_count": self.maximum_sentence_count,
        }


@dataclass(frozen=True, slots=True)
class Aihub539SyncAdmissionAggregate:
    """Private-adapter result containing aggregate values and root digests only."""

    source_manifest_sha256: str
    source_hash_count: int
    source_document_count: int
    extraction_receipt_count: int
    total_source_bytes: int
    keypoint_schema_sha256: str
    timing: Aihub539TimingAggregate
    camera_slices: tuple[Aihub539CameraSlice, ...]
    three_dimensional_actor_track_count: int
    three_dimensional_actor_frame_count: int
    three_dimensional_face_point_observation_count: int
    three_dimensional_body_point_observation_count: int


@dataclass(frozen=True, slots=True)
class Aihub539SyncLabelAuditReceipt:
    """Immutable evidence that the approved TL8 labels satisfy a frozen schema.

    This receipt is deliberately non-promotional.  Matching metadata durations
    and frame counts cannot establish media-level synchronization without
    decoding the corresponding audio and video bytes.
    """

    source_manifest_sha256: str
    source_hash_count: int
    total_source_bytes: int
    keypoint_schema_sha256: str
    timing: Aihub539TimingAggregate
    camera_slices: tuple[Aihub539CameraSlice, ...]
    three_dimensional_actor_track_count: int
    three_dimensional_actor_frame_count: int
    three_dimensional_face_point_observation_count: int
    three_dimensional_body_point_observation_count: int
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        aggregate: Aihub539SyncAdmissionAggregate,
    ) -> "Aihub539SyncLabelAuditReceipt":
        """Issue a digest-bound receipt from a fully admitted aggregate."""

        unsigned = _unsigned_receipt_json(aggregate)
        receipt = cls(
            source_manifest_sha256=aggregate.source_manifest_sha256,
            source_hash_count=aggregate.source_hash_count,
            total_source_bytes=aggregate.total_source_bytes,
            keypoint_schema_sha256=aggregate.keypoint_schema_sha256,
            timing=aggregate.timing,
            camera_slices=aggregate.camera_slices,
            three_dimensional_actor_track_count=(
                aggregate.three_dimensional_actor_track_count
            ),
            three_dimensional_actor_frame_count=(
                aggregate.three_dimensional_actor_frame_count
            ),
            three_dimensional_face_point_observation_count=(
                aggregate.three_dimensional_face_point_observation_count
            ),
            three_dimensional_body_point_observation_count=(
                aggregate.three_dimensional_body_point_observation_count
            ),
            receipt_sha256=sha256_document(unsigned),
        )
        receipt._validate()
        return receipt

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub539SyncLabelAuditReceipt":
        """Strictly reload an exact v1 receipt and recompute its digest.

        Static values are reconstructed rather than trusted.  A canonical-byte
        comparison therefore rejects changed booleans, numeric types, policy
        markers, limitations, and any extra or missing field.
        """

        document = _strict_object(value, _RECEIPT_KEYS)
        source_manifest_sha256 = require_sha256(
            document["source_manifest_sha256"], "source_manifest_sha256"
        )
        keypoint_schema_sha256 = require_sha256(
            document["keypoint_schema_sha256"], "keypoint_schema_sha256"
        )
        receipt_sha256 = require_sha256(
            document["receipt_sha256"], "receipt_sha256"
        )
        source_document_count = _strict_integer(
            document["source_document_count"], minimum=1
        )
        extraction_receipt_count = _strict_integer(
            document["extraction_receipt_count"], minimum=1
        )
        source_hash_count = _strict_integer(
            document["source_hash_count"], minimum=1
        )
        total_source_bytes = _strict_integer(
            document["total_source_bytes"], minimum=1
        )
        if (
            source_document_count != AIHUB539_DOCUMENT_COUNT
            or extraction_receipt_count != 1
            or source_hash_count != AIHUB539_DOCUMENT_COUNT + 1
        ):
            raise ValueError("aihub539_receipt_schema_invalid")

        timing = _parse_timing_aggregate(document["timing"])
        camera_slices = _parse_camera_slices(
            document["two_dimensional_camera_slices"],
            timing=timing,
        )
        three_dimensional = _parse_three_dimensional_slice(
            document["three_dimensional_slice"],
            timing=timing,
        )
        receipt = cls(
            source_manifest_sha256=source_manifest_sha256,
            source_hash_count=source_hash_count,
            total_source_bytes=total_source_bytes,
            keypoint_schema_sha256=keypoint_schema_sha256,
            timing=timing,
            camera_slices=camera_slices,
            three_dimensional_actor_track_count=(
                three_dimensional["actor_track_count"]
            ),
            three_dimensional_actor_frame_count=(
                three_dimensional["actor_frame_count"]
            ),
            three_dimensional_face_point_observation_count=(
                three_dimensional["face_point_observation_count"]
            ),
            three_dimensional_body_point_observation_count=(
                three_dimensional["body_point_observation_count"]
            ),
            receipt_sha256=receipt_sha256,
        )
        receipt._validate()
        if canonical_json_bytes(document) != canonical_json_bytes(
            receipt.to_json_object()
        ):
            raise ValueError("aihub539_receipt_schema_invalid")
        return receipt

    def to_json_object(self) -> dict[str, object]:
        """Serialize only fixed policy, aggregate metrics, and root digests."""

        aggregate = Aihub539SyncAdmissionAggregate(
            source_manifest_sha256=self.source_manifest_sha256,
            source_hash_count=self.source_hash_count,
            source_document_count=AIHUB539_DOCUMENT_COUNT,
            extraction_receipt_count=1,
            total_source_bytes=self.total_source_bytes,
            keypoint_schema_sha256=self.keypoint_schema_sha256,
            timing=self.timing,
            camera_slices=self.camera_slices,
            three_dimensional_actor_track_count=(
                self.three_dimensional_actor_track_count
            ),
            three_dimensional_actor_frame_count=(
                self.three_dimensional_actor_frame_count
            ),
            three_dimensional_face_point_observation_count=(
                self.three_dimensional_face_point_observation_count
            ),
            three_dimensional_body_point_observation_count=(
                self.three_dimensional_body_point_observation_count
            ),
        )
        document = _unsigned_receipt_json(aggregate)
        document["receipt_sha256"] = self.receipt_sha256
        return document

    def _validate(self) -> None:
        require_sha256(self.source_manifest_sha256, "source_manifest_sha256")
        require_sha256(self.keypoint_schema_sha256, "keypoint_schema_sha256")
        require_sha256(self.receipt_sha256, "receipt_sha256")
        document = self.to_json_object()
        observed_digest = document.pop("receipt_sha256")
        if sha256_document(document) != observed_digest:
            raise ValueError("aihub539_receipt_integrity_failed")


def _unsigned_receipt_json(
    aggregate: Aihub539SyncAdmissionAggregate,
) -> dict[str, object]:
    return {
        "schema_version": "voice_coach.aihub539_sync_label_audit_receipt.v1",
        "status": "completed",
        "dataset_id": 539,
        "dataset_partition": "training_label_shard_8",
        "dataset_role": "auxiliary_sync_schema_evidence_only",
        "validation_scope": "label_schema_and_internal_timing_only",
        "aggregate_only": True,
        "dataset_access_permission_confirmed": True,
        "dataset_access_permission_scope": (
            "portal_access_only_not_technical_validation"
        ),
        "permission_grants_technical_promotion": False,
        "audit_policy_sha256": AIHUB539_SYNC_AUDIT_POLICY_SHA256,
        "source_manifest_sha256": aggregate.source_manifest_sha256,
        "source_hash_count": aggregate.source_hash_count,
        "source_document_count": aggregate.source_document_count,
        "extraction_receipt_count": aggregate.extraction_receipt_count,
        "total_source_bytes": aggregate.total_source_bytes,
        "document_encoding": "cp949",
        "camera_ids": list(AIHUB539_CAMERAS),
        "actors_per_document": AIHUB539_ACTORS_PER_DOCUMENT,
        "face_point_count": AIHUB539_FACE_POINT_COUNT,
        "body_point_count": AIHUB539_BODY_POINT_COUNT,
        "two_dimensional_point_size": AIHUB539_TWO_DIMENSIONAL_POINT_SIZE,
        "three_dimensional_point_size": AIHUB539_THREE_DIMENSIONAL_POINT_SIZE,
        "keypoint_schema_sha256": aggregate.keypoint_schema_sha256,
        "audio_video_duration_tolerance_seconds": (
            AIHUB539_AV_DURATION_TOLERANCE_SECONDS
        ),
        "frame_agreement_tolerance_frames": (
            AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES
        ),
        "timing": aggregate.timing.to_json_object(),
        "two_dimensional_camera_slices": [
            camera_slice.to_json_object()
            for camera_slice in aggregate.camera_slices
        ],
        "three_dimensional_slice": {
            "actor_track_count": aggregate.three_dimensional_actor_track_count,
            "actor_frame_count": aggregate.three_dimensional_actor_frame_count,
            "face_point_observation_count": (
                aggregate.three_dimensional_face_point_observation_count
            ),
            "body_point_observation_count": (
                aggregate.three_dimensional_body_point_observation_count
            ),
        },
        "all_track_frame_counts_equal": True,
        "sentence_counts_and_time_bounds_validated": True,
        "motion_transcript_actor_namespaces_distinct": True,
        "cross_namespace_actor_binding_validated": False,
        "actual_media_sync_validated": False,
        "promotion_authorized": False,
        "limitations": [
            "labels_only_no_audio_or_video_bytes_admitted",
            "container_decode_not_executed",
            "motion_transcript_actor_mapping_not_provided",
            "waveform_video_sync_anchors_not_measured",
        ],
    }


def _parse_timing_aggregate(value: object) -> Aihub539TimingAggregate:
    document = _strict_object(value, _TIMING_KEYS)
    timing = Aihub539TimingAggregate(
        total_duration_seconds=_strict_float(
            document["total_duration_seconds"], minimum=0.0, exclusive=True
        ),
        minimum_duration_seconds=_strict_float(
            document["minimum_duration_seconds"], minimum=0.0, exclusive=True
        ),
        maximum_duration_seconds=_strict_float(
            document["maximum_duration_seconds"], minimum=0.0, exclusive=True
        ),
        frames_per_second=_strict_float(
            document["frames_per_second"], minimum=0.0, exclusive=True
        ),
        total_timeline_frame_count=_strict_integer(
            document["total_timeline_frame_count"], minimum=1
        ),
        minimum_timeline_frame_count=_strict_integer(
            document["minimum_timeline_frame_count"], minimum=1
        ),
        maximum_timeline_frame_count=_strict_integer(
            document["maximum_timeline_frame_count"], minimum=1
        ),
        maximum_audio_video_duration_delta_seconds=_strict_float(
            document["maximum_audio_video_duration_delta_seconds"], minimum=0.0
        ),
        maximum_frame_agreement_error_frames=_strict_float(
            document["maximum_frame_agreement_error_frames"], minimum=0.0
        ),
        total_sentence_count=_strict_integer(
            document["total_sentence_count"], minimum=1
        ),
        minimum_sentence_count=_strict_integer(
            document["minimum_sentence_count"], minimum=1
        ),
        maximum_sentence_count=_strict_integer(
            document["maximum_sentence_count"], minimum=1
        ),
    )
    if not math.isclose(
        timing.frames_per_second,
        29.97,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    if not (
        timing.minimum_duration_seconds
        <= timing.maximum_duration_seconds
        <= timing.total_duration_seconds
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    if not (
        timing.minimum_timeline_frame_count
        <= timing.maximum_timeline_frame_count
        <= timing.total_timeline_frame_count
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    if not (
        timing.minimum_sentence_count
        <= timing.maximum_sentence_count
        <= timing.total_sentence_count
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    if (
        timing.maximum_audio_video_duration_delta_seconds
        > AIHUB539_AV_DURATION_TOLERANCE_SECONDS
        or timing.maximum_frame_agreement_error_frames
        > AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    return timing


def _parse_camera_slices(
    value: object,
    *,
    timing: Aihub539TimingAggregate,
) -> tuple[Aihub539CameraSlice, ...]:
    if type(value) is not list or len(value) != len(AIHUB539_CAMERAS):
        raise ValueError("aihub539_receipt_schema_invalid")
    expected_actor_tracks = (
        AIHUB539_DOCUMENT_COUNT * AIHUB539_ACTORS_PER_DOCUMENT
    )
    expected_actor_frames = (
        timing.total_timeline_frame_count * AIHUB539_ACTORS_PER_DOCUMENT
    )
    slices: list[Aihub539CameraSlice] = []
    for expected_camera_id, item in zip(AIHUB539_CAMERAS, value, strict=True):
        document = _strict_object(item, _CAMERA_SLICE_KEYS)
        if type(document["camera_id"]) is not str:
            raise ValueError("aihub539_receipt_schema_invalid")
        camera_slice = Aihub539CameraSlice(
            camera_id=document["camera_id"],
            document_count=_strict_integer(document["document_count"], minimum=1),
            actor_track_count=_strict_integer(
                document["actor_track_count"], minimum=1
            ),
            timeline_frame_count=_strict_integer(
                document["timeline_frame_count"], minimum=1
            ),
            actor_frame_count=_strict_integer(
                document["actor_frame_count"], minimum=1
            ),
            face_point_observation_count=_strict_integer(
                document["face_point_observation_count"], minimum=1
            ),
            body_point_observation_count=_strict_integer(
                document["body_point_observation_count"], minimum=1
            ),
        )
        if (
            camera_slice.camera_id != expected_camera_id
            or camera_slice.document_count != AIHUB539_DOCUMENT_COUNT
            or camera_slice.actor_track_count != expected_actor_tracks
            or camera_slice.timeline_frame_count
            != timing.total_timeline_frame_count
            or camera_slice.actor_frame_count != expected_actor_frames
            or camera_slice.face_point_observation_count
            != expected_actor_frames * AIHUB539_FACE_POINT_COUNT
            or camera_slice.body_point_observation_count
            != expected_actor_frames * AIHUB539_BODY_POINT_COUNT
        ):
            raise ValueError("aihub539_receipt_schema_invalid")
        slices.append(camera_slice)
    return tuple(slices)


def _parse_three_dimensional_slice(
    value: object,
    *,
    timing: Aihub539TimingAggregate,
) -> dict[str, int]:
    document = _strict_object(value, _THREE_DIMENSIONAL_SLICE_KEYS)
    parsed = {
        key: _strict_integer(document[key], minimum=1)
        for key in _THREE_DIMENSIONAL_SLICE_KEYS
    }
    expected_actor_tracks = (
        AIHUB539_DOCUMENT_COUNT * AIHUB539_ACTORS_PER_DOCUMENT
    )
    expected_actor_frames = (
        timing.total_timeline_frame_count * AIHUB539_ACTORS_PER_DOCUMENT
    )
    if (
        parsed["actor_track_count"] != expected_actor_tracks
        or parsed["actor_frame_count"] != expected_actor_frames
        or parsed["face_point_observation_count"]
        != expected_actor_frames * AIHUB539_FACE_POINT_COUNT
        or parsed["body_point_observation_count"]
        != expected_actor_frames * AIHUB539_BODY_POINT_COUNT
    ):
        raise ValueError("aihub539_receipt_schema_invalid")
    return parsed


def _strict_object(
    value: object,
    expected_keys: set[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected_keys:
        raise ValueError("aihub539_receipt_schema_invalid")
    return value


def _strict_integer(value: object, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("aihub539_receipt_schema_invalid")
    return value


def _strict_float(
    value: object,
    *,
    minimum: float,
    exclusive: bool = False,
) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError("aihub539_receipt_schema_invalid")
    if value < minimum or (exclusive and value == minimum):
        raise ValueError("aihub539_receipt_schema_invalid")
    return value


__all__ = [
    "AIHUB539_ACTORS_PER_DOCUMENT",
    "AIHUB539_AV_DURATION_TOLERANCE_SECONDS",
    "AIHUB539_BODY_POINT_COUNT",
    "AIHUB539_CAMERAS",
    "AIHUB539_DOCUMENT_COUNT",
    "AIHUB539_FACE_POINT_COUNT",
    "AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES",
    "AIHUB539_SYNC_AUDIT_POLICY_SHA256",
    "Aihub539CameraSlice",
    "Aihub539SyncAdmissionAggregate",
    "Aihub539SyncLabelAuditReceipt",
    "Aihub539TimingAggregate",
]
