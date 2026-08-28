"""Restricted, aggregate-only admission of AI Hub 539 TL8 label JSON.

The adapter reads a bounded local tree without following symlinks, validates
every coordinate and timing field, and discards all raw content before it
returns.  Its result contains counts plus content-root digests only.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from ..models.aihub539_sync_evidence import (
    AIHUB539_ACTORS_PER_DOCUMENT,
    AIHUB539_AV_DURATION_TOLERANCE_SECONDS,
    AIHUB539_BODY_POINT_COUNT,
    AIHUB539_CAMERAS,
    AIHUB539_DOCUMENT_COUNT,
    AIHUB539_FACE_POINT_COUNT,
    AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES,
    AIHUB539_SYNC_AUDIT_POLICY_SHA256,
    AIHUB539_THREE_DIMENSIONAL_POINT_SIZE,
    AIHUB539_TWO_DIMENSIONAL_POINT_SIZE,
    Aihub539CameraSlice,
    Aihub539SyncAdmissionAggregate,
    Aihub539TimingAggregate,
)
from ..models.integrity import sha256_document


_EXPECTED_TOTAL_FILE_COUNT = AIHUB539_DOCUMENT_COUNT + 1
_MAXIMUM_DIRECTORY_COUNT = 8
_MAXIMUM_TREE_DEPTH = 8
_MAXIMUM_DOCUMENT_BYTES = 128 * 1024 * 1024
_MAXIMUM_TOTAL_SOURCE_BYTES = 1280 * 1024 * 1024
_MAXIMUM_TIMELINE_FRAME_COUNT = 20_000
_MAXIMUM_SENTENCE_COUNT = 1_000
_MAXIMUM_DURATION_SECONDS = 3_600.0
_EXPECTED_FRAMES_PER_SECOND = 29.97
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_DATA_TOP_LEVEL_KEYS = {"Info", "Motion", "Transcript"}
_INFO_KEYS = {"Audio_Info", "Dataset", "Video_Info"}
_AUDIO_INFO_KEYS = {
    "audio_duration",
    "audio_format",
    "audio_name",
    "channel",
    "sampling_rate",
}
_DATASET_INFO_KEYS = {"URL", "description", "version"}
_VIDEO_INFO_KEYS = {
    "FPS",
    "extrinsic",
    "intrinsic",
    "resolution",
    "video_duration",
    "video_format",
    "video_name",
}
_MOTION_KEYS = {"Keypoints", "Keypoints_Info"}
_KEYPOINTS_KEYS = {"keypoints_2d", "keypoints_3d"}
_KEYPOINT_INFO_KEYS = {"body", "face"}
_TRACK_KEYS = {"body", "face", "speaker_ID"}
_TRANSCRIPT_KEYS = {"Sentences", "Speaker_Info", "Transcript_Info"}
_TRANSCRIPT_INFO_KEYS = {"sentence_count", "subject", "type"}
_SENTENCE_KEYS = {
    "emotion",
    "end_time",
    "sentence_text",
    "speaker_ID",
    "start_time",
}
_SPEAKER_INFO_KEYS = {
    "Accent",
    "Age",
    "Gender",
    "Specificity",
    "speaker_ID",
}
_EXTRACTION_RECEIPT_KEYS = {
    "archive",
    "archive_sha256",
    "completed_at",
    "file_count",
    "schema_version",
    "uncompressed_bytes",
}


class Aihub539SyncAuditError(RuntimeError):
    """Fail-closed error carrying no source path or restricted value."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _SourcePayload:
    payload: bytes
    source_sha256: str
    source_bytes: int


@dataclass(frozen=True, slots=True)
class _DocumentAggregate:
    source_sha256: str
    source_bytes: int
    duration_seconds: float
    frames_per_second: float
    timeline_frame_count: int
    frame_agreement_error_frames: float
    audio_video_duration_delta_seconds: float
    sentence_count: int
    keypoint_schema_sha256: str


@dataclass(frozen=True, slots=True)
class _ExtractionAggregate:
    source_sha256: str
    source_bytes: int
    declared_file_count: int
    declared_uncompressed_bytes: int


class Aihub539SyncDatasetAdapter:
    """Admit the exact approved TL8 label shard under a frozen schema policy."""

    def admit(self, *, labels_root: Path) -> Aihub539SyncAdmissionAggregate:
        """Validate every source and return only aggregate non-promotional evidence."""

        document_aggregates: list[_DocumentAggregate] = []
        extraction_aggregates: list[_ExtractionAggregate] = []
        total_source_bytes = 0
        source_hashes: set[str] = set()

        for source in _iter_source_payloads(labels_root):
            total_source_bytes += source.source_bytes
            if total_source_bytes > _MAXIMUM_TOTAL_SOURCE_BYTES:
                raise Aihub539SyncAuditError("aihub539_total_size_outside_policy")
            if source.source_sha256 in source_hashes:
                raise Aihub539SyncAuditError("aihub539_duplicate_source_content")
            source_hashes.add(source.source_sha256)

            document, encoding = _load_json(source.payload)
            if set(document) == _DATA_TOP_LEVEL_KEYS:
                if encoding != "cp949":
                    raise Aihub539SyncAuditError(
                        "aihub539_document_encoding_invalid"
                    )
                document_aggregates.append(
                    _validate_data_document(
                        document,
                        source_sha256=source.source_sha256,
                        source_bytes=source.source_bytes,
                    )
                )
            elif set(document) == _EXTRACTION_RECEIPT_KEYS:
                if encoding != "utf-8":
                    raise Aihub539SyncAuditError(
                        "aihub539_extraction_receipt_invalid"
                    )
                extraction_aggregates.append(
                    _validate_extraction_receipt(
                        document,
                        source_sha256=source.source_sha256,
                        source_bytes=source.source_bytes,
                    )
                )
            else:
                raise Aihub539SyncAuditError("aihub539_top_level_schema_invalid")

        if len(document_aggregates) != AIHUB539_DOCUMENT_COUNT:
            raise Aihub539SyncAuditError("aihub539_document_count_invalid")
        if len(extraction_aggregates) != 1:
            raise Aihub539SyncAuditError(
                "aihub539_extraction_receipt_count_invalid"
            )
        if len(source_hashes) != _EXPECTED_TOTAL_FILE_COUNT:
            raise Aihub539SyncAuditError("aihub539_source_count_invalid")

        document_aggregates.sort(key=lambda item: item.source_sha256)
        extraction = extraction_aggregates[0]
        document_bytes = sum(item.source_bytes for item in document_aggregates)
        if (
            extraction.declared_file_count != AIHUB539_DOCUMENT_COUNT
            or extraction.declared_uncompressed_bytes != document_bytes
        ):
            raise Aihub539SyncAuditError("aihub539_extraction_receipt_mismatch")

        schema_hashes = {
            item.keypoint_schema_sha256 for item in document_aggregates
        }
        if len(schema_hashes) != 1:
            raise Aihub539SyncAuditError("aihub539_keypoint_schema_inconsistent")

        return _aggregate_admitted_sources(
            documents=tuple(document_aggregates),
            extraction=extraction,
            total_source_bytes=total_source_bytes,
            keypoint_schema_sha256=next(iter(schema_hashes)),
        )


def _iter_source_payloads(labels_root: Path) -> Iterator[_SourcePayload]:
    """Yield bounded regular files while rejecting every symlink or special node."""

    if not labels_root.is_absolute():
        raise Aihub539SyncAuditError("aihub539_labels_root_invalid")
    try:
        root_metadata = os.lstat(labels_root)
    except OSError:
        raise Aihub539SyncAuditError("aihub539_labels_root_invalid") from None
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise Aihub539SyncAuditError("aihub539_labels_root_invalid")

    file_count = 0
    directory_count = 0
    try:
        walker = os.fwalk(labels_root, topdown=True, follow_symlinks=False)
        for directory_path, directory_names, file_names, directory_descriptor in walker:
            directory_count += 1
            if directory_count > _MAXIMUM_DIRECTORY_COUNT:
                raise Aihub539SyncAuditError(
                    "aihub539_directory_count_outside_policy"
                )
            relative_depth = len(Path(directory_path).relative_to(labels_root).parts)
            if relative_depth > _MAXIMUM_TREE_DEPTH:
                raise Aihub539SyncAuditError("aihub539_tree_depth_outside_policy")

            directory_names.sort()
            file_names.sort()
            for directory_name in directory_names:
                metadata = os.stat(
                    directory_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if stat.S_ISLNK(metadata.st_mode):
                    raise Aihub539SyncAuditError("aihub539_symlink_forbidden")
                if not stat.S_ISDIR(metadata.st_mode):
                    raise Aihub539SyncAuditError("aihub539_special_node_forbidden")

            for file_name in file_names:
                file_count += 1
                if file_count > _EXPECTED_TOTAL_FILE_COUNT:
                    raise Aihub539SyncAuditError(
                        "aihub539_file_count_outside_policy"
                    )
                if Path(file_name).suffix != ".json":
                    raise Aihub539SyncAuditError("aihub539_file_type_invalid")
                yield _read_source_at(
                    directory_descriptor=directory_descriptor,
                    file_name=file_name,
                )
    except Aihub539SyncAuditError:
        raise
    except OSError:
        raise Aihub539SyncAuditError("aihub539_tree_read_failed") from None

    if file_count != _EXPECTED_TOTAL_FILE_COUNT:
        raise Aihub539SyncAuditError("aihub539_file_count_outside_policy")


def _read_source_at(*, directory_descriptor: int, file_name: str) -> _SourcePayload:
    metadata = os.stat(
        file_name,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if stat.S_ISLNK(metadata.st_mode):
        raise Aihub539SyncAuditError("aihub539_symlink_forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise Aihub539SyncAuditError("aihub539_special_node_forbidden")
    if not 1 <= metadata.st_size <= _MAXIMUM_DOCUMENT_BYTES:
        raise Aihub539SyncAuditError("aihub539_source_size_outside_policy")

    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(file_name, flags, dir_fd=directory_descriptor)
    except OSError:
        raise Aihub539SyncAuditError("aihub539_source_open_failed") from None
    try:
        opened_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_metadata.st_mode)
            or opened_metadata.st_dev != metadata.st_dev
            or opened_metadata.st_ino != metadata.st_ino
            or opened_metadata.st_size != metadata.st_size
        ):
            raise Aihub539SyncAuditError("aihub539_source_changed_during_open")
        chunks: list[bytes] = []
        remaining = opened_metadata.st_size
        digest = hashlib.sha256()
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise Aihub539SyncAuditError("aihub539_source_truncated")
            digest.update(chunk)
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise Aihub539SyncAuditError("aihub539_source_changed_during_read")
    finally:
        os.close(descriptor)

    return _SourcePayload(
        payload=b"".join(chunks),
        source_sha256=digest.hexdigest(),
        source_bytes=metadata.st_size,
    )


def _load_json(payload: bytes) -> tuple[dict[str, object], str]:
    decoded: str
    encoding: str
    try:
        decoded = payload.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        try:
            decoded = payload.decode("cp949")
            encoding = "cp949"
        except UnicodeDecodeError:
            raise Aihub539SyncAuditError("aihub539_json_encoding_invalid") from None
    try:
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, _DuplicateJsonKeyError):
        raise Aihub539SyncAuditError("aihub539_json_invalid") from None
    if type(document) is not dict:
        raise Aihub539SyncAuditError("aihub539_json_invalid")
    return document, encoding


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateJsonKeyError
        document[key] = value
    return document


def _reject_json_constant(_: str) -> object:
    raise _DuplicateJsonKeyError


def _validate_data_document(
    document: dict[str, object],
    *,
    source_sha256: str,
    source_bytes: int,
) -> _DocumentAggregate:
    _expect_exact_keys(document, _DATA_TOP_LEVEL_KEYS, "aihub539_schema_invalid")
    info = _expect_object(document["Info"], _INFO_KEYS)
    audio_info = _expect_object(info["Audio_Info"], _AUDIO_INFO_KEYS)
    dataset_info = _expect_object(info["Dataset"], _DATASET_INFO_KEYS)
    video_info = _expect_object(info["Video_Info"], _VIDEO_INFO_KEYS)

    _validate_bounded_text(dataset_info["URL"], maximum_length=2_048)
    _validate_bounded_text(dataset_info["description"], maximum_length=4_096)
    if _finite_number(dataset_info["version"]) <= 0.0:
        raise Aihub539SyncAuditError("aihub539_schema_invalid")

    duration_seconds, frames_per_second = _validate_media_info(
        audio_info=audio_info,
        video_info=video_info,
    )
    motion = _expect_object(document["Motion"], _MOTION_KEYS)
    keypoints = _expect_object(motion["Keypoints"], _KEYPOINTS_KEYS)
    keypoint_info = _expect_object(motion["Keypoints_Info"], _KEYPOINT_INFO_KEYS)
    keypoint_schema_sha256 = _validate_keypoint_info(keypoint_info)

    expected_frame_count = int(math.floor(frames_per_second * duration_seconds + 0.5))
    if not 1 <= expected_frame_count <= _MAXIMUM_TIMELINE_FRAME_COUNT:
        raise Aihub539SyncAuditError("aihub539_frame_count_outside_policy")

    actor_ids, actual_frame_count = _validate_motion_tracks(
        keypoints,
        expected_frame_count=expected_frame_count,
    )
    frame_error = abs(actual_frame_count - frames_per_second * duration_seconds)
    if frame_error > AIHUB539_FRAME_AGREEMENT_TOLERANCE_FRAMES:
        raise Aihub539SyncAuditError("aihub539_frame_duration_disagreement")

    transcript = _expect_object(document["Transcript"], _TRANSCRIPT_KEYS)
    sentence_count = _validate_transcript(
        transcript,
        motion_actor_ids=actor_ids,
        duration_seconds=duration_seconds,
    )
    audio_video_delta = abs(
        _finite_number(audio_info["audio_duration"])
        - _finite_number(video_info["video_duration"])
    )
    return _DocumentAggregate(
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        duration_seconds=duration_seconds,
        frames_per_second=frames_per_second,
        timeline_frame_count=actual_frame_count,
        frame_agreement_error_frames=frame_error,
        audio_video_duration_delta_seconds=audio_video_delta,
        sentence_count=sentence_count,
        keypoint_schema_sha256=keypoint_schema_sha256,
    )


def _validate_media_info(
    *,
    audio_info: dict[str, object],
    video_info: dict[str, object],
) -> tuple[float, float]:
    audio_duration = _finite_number(audio_info["audio_duration"])
    video_duration = _finite_number(video_info["video_duration"])
    if not 0.0 < audio_duration <= _MAXIMUM_DURATION_SECONDS:
        raise Aihub539SyncAuditError("aihub539_duration_outside_policy")
    if not 0.0 < video_duration <= _MAXIMUM_DURATION_SECONDS:
        raise Aihub539SyncAuditError("aihub539_duration_outside_policy")
    if (
        abs(audio_duration - video_duration)
        > AIHUB539_AV_DURATION_TOLERANCE_SECONDS
    ):
        raise Aihub539SyncAuditError("aihub539_audio_video_duration_mismatch")
    if audio_info["audio_format"] != "wav":
        raise Aihub539SyncAuditError("aihub539_audio_format_invalid")
    if type(audio_info["channel"]) is not int or audio_info["channel"] != 2:
        raise Aihub539SyncAuditError("aihub539_audio_channel_invalid")
    if audio_info["sampling_rate"] != "48kHz":
        raise Aihub539SyncAuditError("aihub539_audio_sampling_rate_invalid")
    audio_names = _expect_list(audio_info["audio_name"], exact_length=1)
    _validate_source_name(audio_names[0], expected_suffix=".wav")

    frames_per_second = _finite_number(video_info["FPS"])
    if not math.isclose(
        frames_per_second,
        _EXPECTED_FRAMES_PER_SECOND,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise Aihub539SyncAuditError("aihub539_video_fps_invalid")
    if video_info["video_format"] != "mp4":
        raise Aihub539SyncAuditError("aihub539_video_format_invalid")
    resolution = _expect_list(video_info["resolution"], exact_length=2)
    if any(_finite_number(value) <= 0.0 for value in resolution):
        raise Aihub539SyncAuditError("aihub539_video_resolution_invalid")
    video_names = _expect_camera_object(video_info["video_name"])
    for video_name in video_names.values():
        _validate_source_name(video_name, expected_suffix=".mp4")
    _validate_camera_matrices(
        _expect_camera_object(video_info["intrinsic"]), rows=3, columns=3
    )
    _validate_camera_matrices(
        _expect_camera_object(video_info["extrinsic"]), rows=3, columns=4
    )
    return video_duration, frames_per_second


def _validate_camera_matrices(
    matrices: dict[str, object], *, rows: int, columns: int
) -> None:
    for matrix in matrices.values():
        matrix_rows = _expect_list(matrix, exact_length=rows)
        for row in matrix_rows:
            values = _expect_list(row, exact_length=columns)
            for value in values:
                _finite_number(value)


def _validate_keypoint_info(keypoint_info: dict[str, object]) -> str:
    body_names = _expect_list(
        keypoint_info["body"], exact_length=AIHUB539_BODY_POINT_COUNT
    )
    face_names = _expect_list(
        keypoint_info["face"], exact_length=AIHUB539_FACE_POINT_COUNT
    )
    for value in (*body_names, *face_names):
        _validate_bounded_text(value, maximum_length=128)
    if len(set(body_names)) != len(body_names) or len(set(face_names)) != len(
        face_names
    ):
        raise Aihub539SyncAuditError("aihub539_keypoint_names_invalid")
    return sha256_document({"body": body_names, "face": face_names})


def _validate_motion_tracks(
    keypoints: dict[str, object], *, expected_frame_count: int
) -> tuple[frozenset[str], int]:
    two_dimensional = _expect_camera_object(keypoints["keypoints_2d"])
    actor_ids: frozenset[str] | None = None
    observed_frame_count: int | None = None
    for camera_id in AIHUB539_CAMERAS:
        tracks = _expect_list(
            two_dimensional[camera_id],
            exact_length=AIHUB539_ACTORS_PER_DOCUMENT,
        )
        camera_actor_ids: set[str] = set()
        for track in tracks:
            track_object = _expect_object(track, _TRACK_KEYS)
            actor_id = _validate_actor_id(track_object["speaker_ID"])
            if actor_id in camera_actor_ids:
                raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
            camera_actor_ids.add(actor_id)
            track_frame_count = _validate_track_points(
                track_object,
                expected_frame_count=expected_frame_count,
                point_size=AIHUB539_TWO_DIMENSIONAL_POINT_SIZE,
            )
            if observed_frame_count is None:
                observed_frame_count = track_frame_count
            elif track_frame_count != observed_frame_count:
                raise Aihub539SyncAuditError("aihub539_track_frame_count_mismatch")
        if actor_ids is None:
            actor_ids = frozenset(camera_actor_ids)
        elif frozenset(camera_actor_ids) != actor_ids:
            raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")

    three_dimensional = _expect_list(
        keypoints["keypoints_3d"], exact_length=AIHUB539_ACTORS_PER_DOCUMENT
    )
    three_dimensional_actor_ids: set[str] = set()
    for track in three_dimensional:
        track_object = _expect_object(track, _TRACK_KEYS)
        actor_id = _validate_actor_id(track_object["speaker_ID"])
        if actor_id in three_dimensional_actor_ids:
            raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
        three_dimensional_actor_ids.add(actor_id)
        track_frame_count = _validate_track_points(
            track_object,
            expected_frame_count=expected_frame_count,
            point_size=AIHUB539_THREE_DIMENSIONAL_POINT_SIZE,
        )
        if observed_frame_count is None or track_frame_count != observed_frame_count:
            raise Aihub539SyncAuditError("aihub539_track_frame_count_mismatch")

    if actor_ids is None or frozenset(three_dimensional_actor_ids) != actor_ids:
        raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
    if observed_frame_count is None:
        raise Aihub539SyncAuditError("aihub539_track_frame_count_mismatch")
    return actor_ids, observed_frame_count


def _validate_track_points(
    track: dict[str, object], *, expected_frame_count: int, point_size: int
) -> int:
    face_frames = _expect_list(track["face"], exact_length=expected_frame_count)
    body_frames = _expect_list(track["body"], exact_length=expected_frame_count)
    for face_frame, body_frame in zip(face_frames, body_frames, strict=True):
        _validate_landmark_frame(
            face_frame,
            point_count=AIHUB539_FACE_POINT_COUNT,
            point_size=point_size,
        )
        _validate_landmark_frame(
            body_frame,
            point_count=AIHUB539_BODY_POINT_COUNT,
            point_size=point_size,
        )
    return len(face_frames)


def _validate_landmark_frame(
    frame: object, *, point_count: int, point_size: int
) -> None:
    points = _expect_list(frame, exact_length=point_count)
    for point in points:
        coordinates = _expect_list(point, exact_length=point_size)
        for coordinate in coordinates:
            _finite_number(coordinate)


def _validate_transcript(
    transcript: dict[str, object],
    *,
    motion_actor_ids: frozenset[str],
    duration_seconds: float,
) -> int:
    speaker_info = _expect_list(
        transcript["Speaker_Info"], exact_length=AIHUB539_ACTORS_PER_DOCUMENT
    )
    transcript_actor_ids: set[str] = set()
    for speaker in speaker_info:
        speaker_object = _expect_object(speaker, _SPEAKER_INFO_KEYS)
        actor_id = _validate_actor_id(speaker_object["speaker_ID"])
        if actor_id in transcript_actor_ids:
            raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
        transcript_actor_ids.add(actor_id)
        for field in ("Accent", "Age", "Gender", "Specificity"):
            _validate_bounded_text(speaker_object[field], maximum_length=128)
    if motion_actor_ids.intersection(transcript_actor_ids):
        raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")

    transcript_info = _expect_object(
        transcript["Transcript_Info"], _TRANSCRIPT_INFO_KEYS
    )
    _validate_bounded_text(transcript_info["subject"], maximum_length=1_024)
    _validate_bounded_text(transcript_info["type"], maximum_length=128)
    sentences = _expect_list(transcript["Sentences"])
    if not 1 <= len(sentences) <= _MAXIMUM_SENTENCE_COUNT:
        raise Aihub539SyncAuditError("aihub539_sentence_count_outside_policy")
    declared_sentence_count = transcript_info["sentence_count"]
    if (
        isinstance(declared_sentence_count, bool)
        or not isinstance(declared_sentence_count, int)
        or declared_sentence_count != len(sentences)
    ):
        raise Aihub539SyncAuditError("aihub539_sentence_count_mismatch")

    observed_sentence_actor_ids: set[str] = set()
    for sentence in sentences:
        sentence_object = _expect_object(sentence, _SENTENCE_KEYS)
        actor_id = _validate_actor_id(sentence_object["speaker_ID"])
        if actor_id not in transcript_actor_ids:
            raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
        observed_sentence_actor_ids.add(actor_id)
        _validate_bounded_text(sentence_object["emotion"], maximum_length=128)
        _validate_bounded_text(sentence_object["sentence_text"], maximum_length=10_000)
        start_time = _finite_number(sentence_object["start_time"])
        end_time = _finite_number(sentence_object["end_time"])
        if (
            start_time < 0.0
            or end_time <= start_time
            or end_time
            > duration_seconds + AIHUB539_AV_DURATION_TOLERANCE_SECONDS
        ):
            raise Aihub539SyncAuditError("aihub539_sentence_time_outside_media")
    if observed_sentence_actor_ids != transcript_actor_ids:
        raise Aihub539SyncAuditError("aihub539_actor_binding_invalid")
    return len(sentences)


def _validate_extraction_receipt(
    document: dict[str, object],
    *,
    source_sha256: str,
    source_bytes: int,
) -> _ExtractionAggregate:
    _expect_exact_keys(
        document,
        _EXTRACTION_RECEIPT_KEYS,
        "aihub539_extraction_receipt_invalid",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    archive_name = _validate_bounded_text(document["archive"], maximum_length=256)
    if archive_name in {".", ".."} or "/" in archive_name or "\\" in archive_name:
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    archive_sha256 = document["archive_sha256"]
    if not isinstance(archive_sha256, str) or not _SHA256_PATTERN.fullmatch(
        archive_sha256
    ):
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    completed_at = _validate_bounded_text(document["completed_at"], maximum_length=64)
    try:
        parsed_completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid") from None
    if parsed_completed_at.tzinfo is None or parsed_completed_at.utcoffset() is None:
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    declared_file_count = document["file_count"]
    declared_uncompressed_bytes = document["uncompressed_bytes"]
    if isinstance(declared_file_count, bool) or not isinstance(
        declared_file_count, int
    ):
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    if isinstance(declared_uncompressed_bytes, bool) or not isinstance(
        declared_uncompressed_bytes, int
    ):
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    if declared_file_count <= 0 or declared_uncompressed_bytes <= 0:
        raise Aihub539SyncAuditError("aihub539_extraction_receipt_invalid")
    return _ExtractionAggregate(
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        declared_file_count=declared_file_count,
        declared_uncompressed_bytes=declared_uncompressed_bytes,
    )


def _aggregate_admitted_sources(
    *,
    documents: tuple[_DocumentAggregate, ...],
    extraction: _ExtractionAggregate,
    total_source_bytes: int,
    keypoint_schema_sha256: str,
) -> Aihub539SyncAdmissionAggregate:
    timeline_frame_count = sum(item.timeline_frame_count for item in documents)
    actor_track_count = AIHUB539_DOCUMENT_COUNT * AIHUB539_ACTORS_PER_DOCUMENT
    actor_frame_count = timeline_frame_count * AIHUB539_ACTORS_PER_DOCUMENT
    camera_slices = tuple(
        Aihub539CameraSlice(
            camera_id=camera_id,
            document_count=AIHUB539_DOCUMENT_COUNT,
            actor_track_count=actor_track_count,
            timeline_frame_count=timeline_frame_count,
            actor_frame_count=actor_frame_count,
            face_point_observation_count=(
                actor_frame_count * AIHUB539_FACE_POINT_COUNT
            ),
            body_point_observation_count=(
                actor_frame_count * AIHUB539_BODY_POINT_COUNT
            ),
        )
        for camera_id in AIHUB539_CAMERAS
    )
    manifest_rows = [
        {
            "role": "data_document",
            "source_sha256": item.source_sha256,
            "source_bytes": item.source_bytes,
        }
        for item in documents
    ]
    manifest_rows.append(
        {
            "role": "extraction_receipt",
            "source_sha256": extraction.source_sha256,
            "source_bytes": extraction.source_bytes,
        }
    )
    manifest_rows.sort(key=lambda row: (str(row["role"]), str(row["source_sha256"])))
    source_manifest_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.aihub539_sync_source_manifest.v1",
            "audit_policy_sha256": AIHUB539_SYNC_AUDIT_POLICY_SHA256,
            "sources": manifest_rows,
        }
    )
    durations = [item.duration_seconds for item in documents]
    frame_counts = [item.timeline_frame_count for item in documents]
    sentence_counts = [item.sentence_count for item in documents]
    frames_per_second_values = {item.frames_per_second for item in documents}
    if len(frames_per_second_values) != 1:
        raise Aihub539SyncAuditError("aihub539_video_fps_inconsistent")
    timing = Aihub539TimingAggregate(
        total_duration_seconds=round(math.fsum(durations), 6),
        minimum_duration_seconds=min(durations),
        maximum_duration_seconds=max(durations),
        frames_per_second=next(iter(frames_per_second_values)),
        total_timeline_frame_count=timeline_frame_count,
        minimum_timeline_frame_count=min(frame_counts),
        maximum_timeline_frame_count=max(frame_counts),
        maximum_audio_video_duration_delta_seconds=round(
            max(item.audio_video_duration_delta_seconds for item in documents), 9
        ),
        maximum_frame_agreement_error_frames=round(
            max(item.frame_agreement_error_frames for item in documents), 9
        ),
        total_sentence_count=sum(sentence_counts),
        minimum_sentence_count=min(sentence_counts),
        maximum_sentence_count=max(sentence_counts),
    )
    return Aihub539SyncAdmissionAggregate(
        source_manifest_sha256=source_manifest_sha256,
        source_hash_count=len(documents) + 1,
        source_document_count=len(documents),
        extraction_receipt_count=1,
        total_source_bytes=total_source_bytes,
        keypoint_schema_sha256=keypoint_schema_sha256,
        timing=timing,
        camera_slices=camera_slices,
        three_dimensional_actor_track_count=actor_track_count,
        three_dimensional_actor_frame_count=actor_frame_count,
        three_dimensional_face_point_observation_count=(
            actor_frame_count * AIHUB539_FACE_POINT_COUNT
        ),
        three_dimensional_body_point_observation_count=(
            actor_frame_count * AIHUB539_BODY_POINT_COUNT
        ),
    )


def _expect_object(value: object, expected_keys: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise Aihub539SyncAuditError("aihub539_schema_invalid")
    _expect_exact_keys(value, expected_keys, "aihub539_schema_invalid")
    return value


def _expect_camera_object(value: object) -> dict[str, object]:
    if type(value) is not dict or tuple(value) != AIHUB539_CAMERAS:
        raise Aihub539SyncAuditError("aihub539_camera_schema_invalid")
    return value


def _expect_exact_keys(
    value: dict[str, object], expected_keys: set[str], reason_code: str
) -> None:
    if set(value) != expected_keys:
        raise Aihub539SyncAuditError(reason_code)


def _expect_list(value: object, *, exact_length: int | None = None) -> list[object]:
    if type(value) is not list:
        raise Aihub539SyncAuditError("aihub539_schema_invalid")
    if exact_length is not None and len(value) != exact_length:
        raise Aihub539SyncAuditError("aihub539_schema_invalid")
    return value


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Aihub539SyncAuditError("aihub539_numeric_value_invalid")
    number = float(value)
    if not math.isfinite(number):
        raise Aihub539SyncAuditError("aihub539_numeric_value_invalid")
    return number


def _validate_bounded_text(value: object, *, maximum_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum_length:
        raise Aihub539SyncAuditError("aihub539_text_field_invalid")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise Aihub539SyncAuditError("aihub539_text_field_invalid")
    return value


def _validate_actor_id(value: object) -> str:
    return _validate_bounded_text(value, maximum_length=128)


def _validate_source_name(value: object, *, expected_suffix: str) -> None:
    source_name = _validate_bounded_text(value, maximum_length=256)
    if (
        source_name in {".", ".."}
        or "/" in source_name
        or "\\" in source_name
        or not source_name.lower().endswith(expected_suffix)
    ):
        raise Aihub539SyncAuditError("aihub539_media_name_invalid")


__all__ = [
    "Aihub539SyncAuditError",
    "Aihub539SyncDatasetAdapter",
]
