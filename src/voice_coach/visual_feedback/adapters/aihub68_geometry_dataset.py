"""Descriptor-safe admission for the AI Hub 68 24-point lip corpus.

The adapter deliberately keeps archive member names, actor identifiers, JPEG
bytes, and landmark arrays inside the restricted execution boundary.  Public
receipts produced by the calibration service contain only counts and digests.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import PurePosixPath
import stat
from zipfile import BadZipFile, ZipFile, ZipInfo

from ..models.integrity import sha256_document
from ..models.p6_geometry_schema import AIHUB68_KEYPOINT_NAMES

_EXPECTED_TOP_LEVEL_KEYS = {
    "actor",
    "annotations",
    "categories",
    "images",
    "info",
    "licenses",
    "meshs",
    "videos",
}
_EXPECTED_CATEGORY_KEYS = {"keypoints", "segments", "type", "type_id"}
_EXPECTED_ANNOTATION_KEYS = {
    "2D keypoints",
    "actor_id",
    "area",
    "bbox",
    "id",
    "image_id",
    "image_name",
    "num_keypoints",
    "segmentation",
    "video_id",
    "word",
    "word_id",
}
_EXPECTED_VIDEO_KEYS = {
    "camera_id",
    "date_captured",
    "file_name",
    "frame_rate",
    "height",
    "id",
    "parameters",
    "width",
}
_EXPECTED_IMAGE_KEYS = {"file_name", "frame_index", "height", "id", "width"}
_EXPECTED_ACTOR_KEYS = {"age", "id", "sex"}
_MAXIMUM_ARCHIVE_BYTES = 192 * 1024 * 1024
_MAXIMUM_MEMBER_COUNT = 5_000
_MAXIMUM_JSON_BYTES = 1024 * 1024
_MAXIMUM_JPEG_BYTES = 8 * 1024 * 1024
_MAXIMUM_COMPRESSION_RATIO = 200


class Aihub68AdmissionError(RuntimeError):
    """Path- and identifier-free admission failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub68GeometrySample:
    """Restricted in-memory sample; this type must never be serialized."""

    sample_sha256: str
    actor_id: str
    attempt_id: str
    camera_id: int
    sex: str
    age_band: str
    word: str
    word_id: int
    frame_index: int
    width: int
    height: int
    points_xy: tuple[tuple[float, float], ...]
    jpeg_bytes: bytes


@dataclass(frozen=True, slots=True)
class Aihub68AdmittedDataset:
    """Admitted restricted samples plus aggregate-only lineage fields."""

    samples: tuple[Aihub68GeometrySample, ...]
    dataset_manifest_sha256: str
    label_archive_sha256: str
    image_archive_sha256: str
    population_id_sha256: str
    actor_count: int
    camera_ids: tuple[int, ...]


class Aihub68GeometryDatasetAdapter:
    """Admit matching label/image ZIP descriptors under strict bounds."""

    def admit(
        self,
        *,
        label_archive_descriptor: int,
        image_archive_descriptor: int,
    ) -> Aihub68AdmittedDataset:
        label_payload = _read_regular_descriptor(label_archive_descriptor)
        image_payload = _read_regular_descriptor(image_archive_descriptor)
        label_sha256 = hashlib.sha256(label_payload).hexdigest()
        image_sha256 = hashlib.sha256(image_payload).hexdigest()

        try:
            with ZipFile(io.BytesIO(label_payload)) as label_zip, ZipFile(
                io.BytesIO(image_payload)
            ) as image_zip:
                label_members = _admit_members(label_zip, expected_suffix=".json")
                image_members = _admit_members(image_zip, expected_suffix=".jpg")
                if set(label_members) != set(image_members):
                    raise Aihub68AdmissionError("aihub68_exact_pairing_failed")

                samples = tuple(
                    _parse_pair(
                        pair_key=pair_key,
                        label_payload=label_zip.read(label_members[pair_key]),
                        jpeg_payload=image_zip.read(image_members[pair_key]),
                    )
                    for pair_key in sorted(label_members)
                )
        except Aihub68AdmissionError:
            raise
        except (BadZipFile, KeyError, OSError, RuntimeError):
            raise Aihub68AdmissionError("aihub68_archive_invalid") from None

        if not samples:
            raise Aihub68AdmissionError("aihub68_samples_missing")
        actor_ids = sorted({sample.actor_id for sample in samples})
        actor_hashes = tuple(
            hashlib.sha256(
                b"voice_coach.aihub68.actor.v1\x00" + actor_id.encode("utf-8")
            ).hexdigest()
            for actor_id in actor_ids
        )
        manifest_rows = tuple(
            {
                "sample_sha256": sample.sample_sha256,
                "actor_sha256": _actor_sha256(sample.actor_id),
                "attempt_sha256": _attempt_sha256(sample.attempt_id),
                "camera_id": sample.camera_id,
                "age_band": sample.age_band,
                "sex": sample.sex,
                "width": sample.width,
                "height": sample.height,
                "point_count": len(sample.points_xy),
            }
            for sample in samples
        )
        manifest_sha256 = sha256_document(
            {
                "schema_version": "voice_coach.aihub68_geometry_manifest.v1",
                "label_archive_sha256": label_sha256,
                "image_archive_sha256": image_sha256,
                "samples": manifest_rows,
            }
        )
        return Aihub68AdmittedDataset(
            samples=samples,
            dataset_manifest_sha256=manifest_sha256,
            label_archive_sha256=label_sha256,
            image_archive_sha256=image_sha256,
            population_id_sha256=sha256_document(
                {
                    "population_role": "aihub68_validation_lip_interjection",
                    "actor_sha256": sorted(actor_hashes),
                }
            ),
            actor_count=len(actor_ids),
            camera_ids=tuple(sorted({sample.camera_id for sample in samples})),
        )


def _read_regular_descriptor(descriptor: int) -> bytes:
    """Read an existing read-only regular descriptor without resolving a path."""

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise Aihub68AdmissionError("aihub68_archive_descriptor_invalid")
    try:
        duplicate = os.dup(descriptor)
    except OSError:
        raise Aihub68AdmissionError("aihub68_archive_descriptor_invalid") from None
    try:
        metadata = os.fstat(duplicate)
        if not stat.S_ISREG(metadata.st_mode):
            raise Aihub68AdmissionError("aihub68_archive_not_regular")
        access_mode = fcntl.fcntl(duplicate, fcntl.F_GETFL) & os.O_ACCMODE
        if access_mode != os.O_RDONLY:
            raise Aihub68AdmissionError("aihub68_archive_not_read_only")
        if metadata.st_size <= 0 or metadata.st_size > _MAXIMUM_ARCHIVE_BYTES:
            raise Aihub68AdmissionError("aihub68_archive_size_outside_policy")
        os.lseek(duplicate, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(duplicate, min(1024 * 1024, remaining))
            if not chunk:
                raise Aihub68AdmissionError("aihub68_archive_truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(duplicate)


def _admit_members(zip_file: ZipFile, *, expected_suffix: str) -> dict[str, ZipInfo]:
    infos = zip_file.infolist()
    if not 1 <= len(infos) <= _MAXIMUM_MEMBER_COUNT:
        raise Aihub68AdmissionError("aihub68_archive_member_count_outside_policy")
    normalized_names: set[str] = set()
    paired: dict[str, ZipInfo] = {}
    total_size = 0
    for info in infos:
        normalized = _safe_member_name(info)
        folded = normalized.casefold()
        if folded in normalized_names:
            raise Aihub68AdmissionError("aihub68_archive_duplicate_member")
        normalized_names.add(folded)
        if info.is_dir():
            continue
        suffix = PurePosixPath(normalized).suffix.lower()
        if suffix != expected_suffix:
            raise Aihub68AdmissionError("aihub68_archive_member_type_invalid")
        maximum_size = (
            _MAXIMUM_JSON_BYTES if expected_suffix == ".json" else _MAXIMUM_JPEG_BYTES
        )
        if not 1 <= info.file_size <= maximum_size:
            raise Aihub68AdmissionError("aihub68_archive_member_size_outside_policy")
        if info.flag_bits & 0x1:
            raise Aihub68AdmissionError("aihub68_encrypted_member_unsupported")
        if info.compress_size <= 0 or (
            info.file_size / info.compress_size > _MAXIMUM_COMPRESSION_RATIO
        ):
            raise Aihub68AdmissionError("aihub68_archive_compression_outside_policy")
        total_size += info.file_size
        if total_size > _MAXIMUM_ARCHIVE_BYTES:
            raise Aihub68AdmissionError("aihub68_uncompressed_size_outside_policy")
        pair_key = str(PurePosixPath(normalized).with_suffix(""))
        if pair_key in paired:
            raise Aihub68AdmissionError("aihub68_archive_duplicate_pair_key")
        paired[pair_key] = info
    return paired


def _safe_member_name(info: ZipInfo) -> str:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise Aihub68AdmissionError("aihub68_archive_member_path_invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise Aihub68AdmissionError("aihub68_archive_member_path_invalid")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise Aihub68AdmissionError("aihub68_archive_member_type_invalid")
    return str(path)


def _parse_pair(
    *,
    pair_key: str,
    label_payload: bytes,
    jpeg_payload: bytes,
) -> Aihub68GeometrySample:
    try:
        document = json.loads(label_payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Aihub68AdmissionError("aihub68_label_json_invalid") from None
    if not isinstance(document, dict) or set(document) != _EXPECTED_TOP_LEVEL_KEYS:
        raise Aihub68AdmissionError("aihub68_label_schema_invalid")
    category = _single_mapping(document["categories"])
    annotation = _single_mapping(document["annotations"])
    video = _single_mapping(document["videos"])
    image = _single_mapping(document["images"])
    actor = _single_mapping(document["actor"])
    if (
        set(category) != _EXPECTED_CATEGORY_KEYS
        or set(annotation) != _EXPECTED_ANNOTATION_KEYS
        or set(video) != _EXPECTED_VIDEO_KEYS
        or set(image) != _EXPECTED_IMAGE_KEYS
        or set(actor) != _EXPECTED_ACTOR_KEYS
    ):
        raise Aihub68AdmissionError("aihub68_label_schema_invalid")
    if (
        category["keypoints"] != list(AIHUB68_KEYPOINT_NAMES)
        or category["segments"] != [[0, 11], [12, 23]]
        or annotation["num_keypoints"] != 24
    ):
        raise Aihub68AdmissionError("aihub68_24_point_schema_invalid")

    image_name = image["file_name"]
    if (
        not isinstance(image_name, str)
        or PurePosixPath(pair_key).name != PurePosixPath(image_name).stem
        or annotation["image_name"] != image_name
        or annotation["image_id"] != image["id"]
        or annotation["video_id"] != video["id"]
        or annotation["actor_id"] != actor["id"]
    ):
        raise Aihub68AdmissionError("aihub68_label_pairing_invalid")
    actor_id = actor["id"]
    sex = actor["sex"]
    age = actor["age"]
    width = image["width"]
    height = image["height"]
    camera_id = video["camera_id"]
    frame_index = image["frame_index"]
    word_id = annotation["word_id"]
    word = annotation["word"]
    if (
        not isinstance(actor_id, str)
        or not actor_id
        or sex not in {"female", "male"}
        or isinstance(age, bool)
        or not isinstance(age, int)
        or not 0 <= age <= 120
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (width, height, camera_id, frame_index, word_id)
        )
        or width <= 0
        or height <= 0
        or video["width"] != width
        or video["height"] != height
        or not isinstance(word, str)
        or (word_id == 9 and word != "어")
        or (word_id == 10 and word != "어디")
    ):
        raise Aihub68AdmissionError("aihub68_label_value_invalid")
    raw_points = annotation["2D keypoints"]
    if not isinstance(raw_points, list) or len(raw_points) != 72:
        raise Aihub68AdmissionError("aihub68_24_point_schema_invalid")
    points: list[tuple[float, float]] = []
    for offset in range(0, 72, 3):
        raw_x, raw_y, visibility = raw_points[offset : offset + 3]
        if (
            isinstance(raw_x, bool)
            or isinstance(raw_y, bool)
            or not isinstance(raw_x, (int, float))
            or not isinstance(raw_y, (int, float))
            or not math.isfinite(float(raw_x))
            or not math.isfinite(float(raw_y))
            or not 0 <= float(raw_x) <= width
            or not 0 <= float(raw_y) <= height
            or visibility != 2
        ):
            raise Aihub68AdmissionError("aihub68_landmark_value_invalid")
        points.append((float(raw_x) / width, float(raw_y) / height))
    if len(jpeg_payload) < 4 or not (
        jpeg_payload.startswith(b"\xff\xd8") and jpeg_payload.endswith(b"\xff\xd9")
    ):
        raise Aihub68AdmissionError("aihub68_jpeg_envelope_invalid")
    sample_sha256 = sha256_document(
        {
            "label_sha256": hashlib.sha256(label_payload).hexdigest(),
            "jpeg_sha256": hashlib.sha256(jpeg_payload).hexdigest(),
        }
    )
    return Aihub68GeometrySample(
        sample_sha256=sample_sha256,
        actor_id=actor_id,
        attempt_id=f"{actor_id}:{video['id']}",
        camera_id=camera_id,
        sex=sex,
        age_band=_age_band(age),
        word=word,
        word_id=word_id,
        frame_index=frame_index,
        width=width,
        height=height,
        points_xy=tuple(points),
        jpeg_bytes=jpeg_payload,
    )


def _single_mapping(value: object) -> dict[str, object]:
    if (
        not isinstance(value, list)
        or len(value) != 1
        or not isinstance(value[0], dict)
        or not all(isinstance(key, str) for key in value[0])
    ):
        raise Aihub68AdmissionError("aihub68_label_schema_invalid")
    return value[0]


def _age_band(age: int) -> str:
    if age < 18:
        return "under_18"
    if age < 30:
        return "18_29"
    if age < 50:
        return "30_49"
    return "50_plus"


def _actor_sha256(actor_id: str) -> str:
    return hashlib.sha256(
        b"voice_coach.aihub68.actor.v1\x00" + actor_id.encode("utf-8")
    ).hexdigest()


def _attempt_sha256(attempt_id: str) -> str:
    return hashlib.sha256(
        b"voice_coach.aihub68.attempt.v1\x00" + attempt_id.encode("utf-8")
    ).hexdigest()


__all__ = [
    "AIHUB68_KEYPOINT_NAMES",
    "Aihub68AdmissionError",
    "Aihub68AdmittedDataset",
    "Aihub68GeometryDatasetAdapter",
    "Aihub68GeometrySample",
]
