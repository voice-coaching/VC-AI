"""Restricted-corpus loading for adversarial pronunciation training.

The module keeps corpus policy, manifest filtering, audio resolution, sampling,
and tensor collation separate from the model.  Raw transcript text is never
placed in a record or report.  Native content supervision is enabled only when
an externally reviewed canonical-token sidecar supplies tokens for that item.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict, deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import io
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import random
import re
from typing import TYPE_CHECKING, Literal, cast

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]

from voice_coach.data.audio import (
    DecodedAudio,
    decode_audio,
    pad_audio_batch,
    resample_audio,
)
from voice_coach.data.native_korean import CORPUS_POLICIES
from voice_coach.features.gop import PHONE_INVENTORY

if TYPE_CHECKING:
    import torch

    from voice_coach.training.adversarial_tts import AdversarialSpeechBatch


ASPECT_NAMES = ("segmental", "fluency", "prosody", "comprehensibility")
PAD_TOKEN_ID = 0
PHONE_TO_TOKEN_ID = {phone: index + 1 for index, phone in enumerate(PHONE_INVENTORY)}
UNKNOWN_TEXT_TOKEN_ID = len(PHONE_TO_TOKEN_ID) + 1
PROTECTED_LEARNER_SPLITS = frozenset({"stack", "calibration", "locked_test"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EMBEDDED_AUDIO_PATTERN = re.compile(
    r"^(?P<shard>[^#]+[.]parquet)#row=(?P<row>[0-9]+):audio[.]bytes$"
)
AudioStorage = Literal["relative_file", "parquet_embedded"]


class AdversarialSpeechDataError(ValueError):
    """Raised when operational data would violate a training boundary."""


@dataclass(frozen=True, slots=True)
class LearnerSpeechRecord:
    """One filtered AI Hub learner utterance with observed-only supervision."""

    stable_id: str
    audio_path: Path
    duration_seconds: float
    text_tokens: tuple[int, ...]
    quality_target: float
    aspect_targets: tuple[float, float, float, float]
    aspect_mask: tuple[bool, bool, bool, bool]
    content_supervised: bool
    l1_group: str
    gender_group: str
    proficiency_group: str
    error_presence: bool


@dataclass(frozen=True, slots=True)
class NativeSpeechRecord:
    """One policy-approved native utterance and its restricted source locator."""

    stable_id: str
    source_id: str
    audio_storage: AudioStorage
    relative_path: str | None
    embedded_reference: str | None
    duration_seconds: float
    sample_rate_hz: int
    channels: int
    text_tokens: tuple[int, ...]
    quality_target: float
    aspect_targets: tuple[float, float, float, float]
    aspect_mask: tuple[bool, bool, bool, bool]
    content_supervised: bool


@dataclass(frozen=True, slots=True)
class NativeManifestPolicy:
    """License and role values that a native source must match exactly."""

    source_id: str
    license_id: str
    intended_role: str
    noncommercial_only: bool
    allowed_splits: tuple[str, ...] = ("train",)


@dataclass(frozen=True, slots=True)
class LearnerLoadResult:
    records: tuple[LearnerSpeechRecord, ...]
    source_rows: int
    exclusions: tuple[tuple[str, int], ...]

    def exclusion_counts(self) -> dict[str, int]:
        return dict(self.exclusions)


@dataclass(frozen=True, slots=True)
class NativeLoadResult:
    records: tuple[NativeSpeechRecord, ...]
    source_rows: int
    exclusions: tuple[tuple[str, int], ...]

    def exclusion_counts(self) -> dict[str, int]:
        return dict(self.exclusions)


@dataclass(frozen=True, slots=True)
class AdversarialSpeechPair:
    learner: LearnerSpeechRecord
    native: NativeSpeechRecord


@dataclass(frozen=True, slots=True)
class EmbeddedAudioReference:
    shard_relative_path: str
    row_index: int


def native_policy_from_catalog(
    source_id: str,
    *,
    allowed_splits: Sequence[str] = ("train",),
) -> NativeManifestPolicy:
    """Build an exact policy from the reviewed native-corpus catalog."""

    try:
        policy = CORPUS_POLICIES[source_id]
    except KeyError as exc:
        raise AdversarialSpeechDataError(
            f"native source has no reviewed policy: {source_id}"
        ) from exc
    splits = _nonempty_strings(allowed_splits, field_name="allowed_splits")
    return NativeManifestPolicy(
        source_id=policy.source_id,
        license_id=policy.license_id,
        intended_role=policy.intended_role,
        noncommercial_only=policy.noncommercial_only,
        allowed_splits=splits,
    )


def _nonempty_strings(
    values: Sequence[str],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if (
        isinstance(values, (str, bytes))
        or not values
        or not all(isinstance(value, str) and value for value in values)
    ):
        raise AdversarialSpeechDataError(
            f"{field_name} must be a non-empty string sequence"
        )
    return tuple(values)


def _safe_relative_path(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdversarialSpeechDataError(f"{field_name} must be non-empty")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise AdversarialSpeechDataError(f"{field_name} escapes its corpus root")
    return path.as_posix()


def _contained_path(
    root: Path,
    relative_path: str,
    *,
    field_name: str,
    require_file: bool,
) -> Path:
    safe = _safe_relative_path(relative_path, field_name=field_name)
    candidate = (root / Path(safe)).resolve(strict=require_file)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AdversarialSpeechDataError(
            f"{field_name} resolves outside its corpus root"
        ) from exc
    if require_file and not candidate.is_file():
        raise AdversarialSpeechDataError(f"{field_name} is not a file")
    return candidate


def _positive_duration(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdversarialSpeechDataError(f"{field_name} must be numeric")
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0:
        raise AdversarialSpeechDataError(f"{field_name} must be finite and positive")
    return parsed


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdversarialSpeechDataError(f"{field_name} must be a positive integer")
    return int(value)


def _soft_human_score(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdversarialSpeechDataError(f"{field_name} must be a 1--5 score")
    parsed = float(value)
    if not parsed.is_integer() or not 1 <= parsed <= 5:
        raise AdversarialSpeechDataError(f"{field_name} must be a 1--5 score")
    return (parsed - 1.0) / 4.0


def encode_target_phone_intervals(
    intervals: object,
) -> tuple[int, ...]:
    """Encode the reviewed 38-label AI Hub target-phone inventory."""

    if not isinstance(intervals, Sequence) or isinstance(intervals, (str, bytes)):
        raise AdversarialSpeechDataError("target_phone_intervals must be a sequence")
    output: list[int] = []
    previous_end = 0.0
    for index, interval in enumerate(intervals):
        if not isinstance(interval, Mapping):
            raise AdversarialSpeechDataError(
                f"target_phone_intervals[{index}] must be a mapping"
            )
        start = interval.get("start")
        end = interval.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not isfinite(float(start))
            or not isfinite(float(end))
            or float(start) < previous_end
            or float(end) <= float(start)
        ):
            raise AdversarialSpeechDataError(
                f"target_phone_intervals[{index}] has invalid boundaries"
            )
        label = interval.get("label")
        if label not in PHONE_TO_TOKEN_ID:
            raise AdversarialSpeechDataError(
                f"target_phone_intervals[{index}] has an unknown label"
            )
        output.append(PHONE_TO_TOKEN_ID[str(label)])
        previous_end = float(end)
    return tuple(output)


def _phone_accuracy(alignments: object) -> float | None:
    if not isinstance(alignments, Sequence) or isinstance(alignments, (str, bytes)):
        raise AdversarialSpeechDataError("phone_alignments must be a sequence")
    counts: Counter[str] = Counter()
    allowed = {"match", "substitution", "deletion", "insertion"}
    for index, alignment in enumerate(alignments):
        if not isinstance(alignment, Mapping):
            raise AdversarialSpeechDataError(
                f"phone_alignments[{index}] must be a mapping"
            )
        operation = alignment.get("operation")
        if operation not in allowed:
            raise AdversarialSpeechDataError(
                f"phone_alignments[{index}] has an unknown operation"
            )
        counts[str(operation)] += 1
    target_count = counts["match"] + counts["substitution"] + counts["deletion"]
    return counts["match"] / target_count if target_count else None


def _learner_targets(
    row: Mapping[str, object],
) -> tuple[
    float,
    tuple[float, float, float, float],
    tuple[bool, bool, bool, bool],
]:
    phone_accuracy = _phone_accuracy(row.get("phone_alignments"))
    pronunciation = _soft_human_score(
        row.get("pronunciation_score"),
        field_name="pronunciation_score",
    )
    fluency = _soft_human_score(
        row.get("fluency_score"),
        field_name="fluency_score",
    )
    comprehensibility = _soft_human_score(
        row.get("comprehensibility_score"),
        field_name="comprehensibility_score",
    )
    segmental_values = [
        value for value in (phone_accuracy, pronunciation) if value is not None
    ]
    if not segmental_values:
        raise AdversarialSpeechDataError(
            "learner row has neither phone nor pronunciation supervision"
        )
    segmental = sum(segmental_values) / len(segmental_values)
    targets = (
        segmental,
        fluency if fluency is not None else 0.0,
        0.0,
        comprehensibility if comprehensibility is not None else 0.0,
    )
    mask = (True, fluency is not None, False, comprehensibility is not None)
    quality = sum(
        target for target, observed in zip(targets, mask, strict=True) if observed
    ) / sum(mask)
    return quality, targets, mask


def load_reserved_prompt_hashes(path: str | Path) -> frozenset[str]:
    source = Path(path).resolve(strict=True)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        entries = document["reserved_prompts"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdversarialSpeechDataError("invalid prompt-reservation artifact") from exc
    if not isinstance(entries, list) or not entries:
        raise AdversarialSpeechDataError("prompt-reservation artifact has no entries")
    output: set[str] = set()
    for index, entry in enumerate(entries):
        value = entry.get("prompt_sha256") if isinstance(entry, Mapping) else None
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise AdversarialSpeechDataError(
                f"reserved_prompts[{index}] has an invalid hash"
            )
        if value in output:
            raise AdversarialSpeechDataError("reserved prompt hashes repeat")
        output.add(value)
    return frozenset(output)


def load_aihub_learner_records(
    manifest_path: str | Path,
    *,
    dataset_root: str | Path,
    prompt_reservation_path: str | Path,
    allowed_splits: Sequence[str] = ("train",),
    max_duration_seconds: float | None = None,
    limit: int | None = None,
    require_audio_files: bool = True,
) -> LearnerLoadResult:
    """Project and filter the AI Hub modeling manifest before decoding audio."""

    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - scorer dependency
        raise RuntimeError("pyarrow is required for adversarial data loading") from exc

    splits = _nonempty_strings(allowed_splits, field_name="allowed_splits")
    if set(splits) != {"train"} or set(splits) & PROTECTED_LEARNER_SPLITS:
        raise AdversarialSpeechDataError(
            "adversarial learner loading permits only the train split"
        )
    if max_duration_seconds is not None:
        max_duration_seconds = _positive_duration(
            max_duration_seconds,
            field_name="max_duration_seconds",
        )
    if limit is not None and (isinstance(limit, bool) or limit <= 0):
        raise AdversarialSpeechDataError("limit must be positive")

    root = Path(dataset_root).resolve(strict=True)
    if not root.is_dir():
        raise AdversarialSpeechDataError("dataset_root must be a directory")
    reserved = load_reserved_prompt_hashes(prompt_reservation_path)
    parquet = pq.ParquetFile(Path(manifest_path).resolve(strict=True))
    columns = [
        "id",
        "split",
        "audio_path",
        "duration_seconds",
        "l1",
        "gender",
        "proficiency",
        "error_presence",
        "target_phone_intervals",
        "phone_alignments",
        "qc_flags",
        "prompt_sha256",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    ]
    output: list[LearnerSpeechRecord] = []
    exclusions: Counter[str] = Counter()
    source_rows = 0
    stop = False
    for batch in parquet.iter_batches(batch_size=1024, columns=columns):
        for row in batch.to_pylist():
            source_rows += 1
            if row.get("split") not in splits:
                exclusions["split_not_allowed"] += 1
                continue
            flags = row.get("qc_flags") or []
            if not isinstance(flags, list) or not all(
                isinstance(flag, str) for flag in flags
            ):
                raise AdversarialSpeechDataError("qc_flags must be a string list")
            if flags:
                exclusions["qc_flags_present"] += 1
                continue
            prompt_hash = row.get("prompt_sha256")
            if not isinstance(prompt_hash, str) or not _SHA256_PATTERN.fullmatch(
                prompt_hash
            ):
                raise AdversarialSpeechDataError("prompt_sha256 is invalid")
            if prompt_hash in reserved:
                exclusions["reserved_prompt"] += 1
                continue
            duration = _positive_duration(
                row.get("duration_seconds"),
                field_name="duration_seconds",
            )
            if max_duration_seconds is not None and duration > max_duration_seconds:
                exclusions["duration_above_limit"] += 1
                continue
            tokens = encode_target_phone_intervals(row.get("target_phone_intervals"))
            if not tokens:
                exclusions["empty_target_phone_sequence"] += 1
                continue
            relative_audio = _safe_relative_path(
                row.get("audio_path"),
                field_name="audio_path",
            )
            audio_path = _contained_path(
                root,
                relative_audio,
                field_name="audio_path",
                require_file=require_audio_files,
            )
            quality, aspect_targets, aspect_mask = _learner_targets(row)
            stable_id = row.get("id")
            if not isinstance(stable_id, str) or not stable_id:
                raise AdversarialSpeechDataError("learner id must be non-empty")
            error_presence = row.get("error_presence")
            if not isinstance(error_presence, bool):
                raise AdversarialSpeechDataError("error_presence must be boolean")
            output.append(
                LearnerSpeechRecord(
                    stable_id=stable_id,
                    audio_path=audio_path,
                    duration_seconds=duration,
                    text_tokens=tokens,
                    quality_target=quality,
                    aspect_targets=aspect_targets,
                    aspect_mask=aspect_mask,
                    content_supervised=True,
                    l1_group=str(row.get("l1") or "missing"),
                    gender_group=str(row.get("gender") or "missing"),
                    proficiency_group=str(row.get("proficiency") or "missing"),
                    error_presence=error_presence,
                )
            )
            if limit is not None and len(output) >= limit:
                stop = True
                break
        if stop:
            break
    return LearnerLoadResult(
        records=tuple(output),
        source_rows=source_rows,
        exclusions=tuple(sorted(exclusions.items())),
    )


def _validate_text_tokens(
    values: Sequence[int],
    *,
    vocabulary_size: int,
    field_name: str,
) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not values:
        raise AdversarialSpeechDataError(f"{field_name} must be non-empty")
    output: list[int] = []
    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not PAD_TOKEN_ID < value < vocabulary_size
        ):
            raise AdversarialSpeechDataError(
                f"{field_name} contains an invalid token ID"
            )
        output.append(int(value))
    return tuple(output)


def parse_embedded_audio_reference(value: object) -> EmbeddedAudioReference:
    if not isinstance(value, str):
        raise AdversarialSpeechDataError("embedded audio reference must be a string")
    match = _EMBEDDED_AUDIO_PATTERN.fullmatch(value)
    if match is None:
        raise AdversarialSpeechDataError(
            "embedded audio reference violates the Parquet locator contract"
        )
    shard = _safe_relative_path(
        match.group("shard"),
        field_name="embedded shard",
    )
    return EmbeddedAudioReference(
        shard_relative_path=shard,
        row_index=int(match.group("row")),
    )


def load_native_speech_records(
    manifest_path: str | Path,
    *,
    policies: Mapping[str, NativeManifestPolicy],
    native_tokens_by_id: Mapping[str, Sequence[int]] | None = None,
    vocabulary_size: int = 512,
    max_duration_seconds: float | None = None,
    limit: int | None = None,
) -> NativeLoadResult:
    """Load only rows matching an explicit, reviewed license/role policy."""

    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - scorer dependency
        raise RuntimeError("pyarrow is required for adversarial data loading") from exc

    if not policies:
        raise AdversarialSpeechDataError("at least one native policy is required")
    if any(key != policy.source_id for key, policy in policies.items()):
        raise AdversarialSpeechDataError("native policy keys and source IDs differ")
    if vocabulary_size <= UNKNOWN_TEXT_TOKEN_ID:
        raise AdversarialSpeechDataError(
            "vocabulary_size does not cover the unknown native token"
        )
    if max_duration_seconds is not None:
        max_duration_seconds = _positive_duration(
            max_duration_seconds,
            field_name="max_duration_seconds",
        )
    if limit is not None and (isinstance(limit, bool) or limit <= 0):
        raise AdversarialSpeechDataError("limit must be positive")

    parquet = pq.ParquetFile(Path(manifest_path).resolve(strict=True))
    columns = [
        "stable_id",
        "source_id",
        "audio_storage",
        "relative_path",
        "embedded_reference",
        "sample_rate_hz",
        "channels",
        "duration_seconds",
        "split",
        "license_id",
        "noncommercial_only",
        "intended_role",
        "access_scope",
    ]
    output: list[NativeSpeechRecord] = []
    exclusions: Counter[str] = Counter()
    source_rows = 0
    stop = False
    for batch in parquet.iter_batches(batch_size=1024, columns=columns):
        for row in batch.to_pylist():
            source_rows += 1
            source_id = row.get("source_id")
            if source_id not in policies:
                exclusions["source_not_allowed"] += 1
                continue
            policy = policies[str(source_id)]
            if (
                row.get("license_id") != policy.license_id
                or row.get("intended_role") != policy.intended_role
                or row.get("noncommercial_only") is not policy.noncommercial_only
            ):
                raise AdversarialSpeechDataError(
                    f"native policy mismatch for source {source_id}"
                )
            if row.get("access_scope") != "restricted_internal":
                raise AdversarialSpeechDataError(
                    "native record is outside the restricted access scope"
                )
            if row.get("split") not in policy.allowed_splits:
                exclusions["split_not_allowed"] += 1
                continue
            duration = _positive_duration(
                row.get("duration_seconds"),
                field_name="duration_seconds",
            )
            if max_duration_seconds is not None and duration > max_duration_seconds:
                exclusions["duration_above_limit"] += 1
                continue
            stable_id = row.get("stable_id")
            if not isinstance(stable_id, str) or not stable_id:
                raise AdversarialSpeechDataError("native stable_id must be non-empty")
            storage = row.get("audio_storage")
            relative_path = row.get("relative_path")
            embedded_reference = row.get("embedded_reference")
            if storage == "relative_file":
                relative_path = _safe_relative_path(
                    relative_path,
                    field_name="relative_path",
                )
                if embedded_reference is not None:
                    raise AdversarialSpeechDataError(
                        "relative native audio also has an embedded locator"
                    )
            elif storage == "parquet_embedded":
                parse_embedded_audio_reference(embedded_reference)
                if relative_path is not None:
                    raise AdversarialSpeechDataError(
                        "embedded native audio also has a relative path"
                    )
            else:
                raise AdversarialSpeechDataError("native audio_storage is unsupported")
            supplied_tokens = (
                native_tokens_by_id.get(stable_id)
                if native_tokens_by_id is not None
                else None
            )
            tokens: tuple[int, ...]
            if supplied_tokens is None:
                tokens = (UNKNOWN_TEXT_TOKEN_ID,)
                content_supervised = False
            else:
                tokens = _validate_text_tokens(
                    supplied_tokens,
                    vocabulary_size=vocabulary_size,
                    field_name=f"native_tokens_by_id[{stable_id!r}]",
                )
                content_supervised = True
            output.append(
                NativeSpeechRecord(
                    stable_id=stable_id,
                    source_id=str(source_id),
                    audio_storage=cast(AudioStorage, storage),
                    relative_path=cast(str | None, relative_path),
                    embedded_reference=cast(str | None, embedded_reference),
                    duration_seconds=duration,
                    sample_rate_hz=_positive_integer(
                        row.get("sample_rate_hz"),
                        field_name="sample_rate_hz",
                    ),
                    channels=_positive_integer(
                        row.get("channels"),
                        field_name="channels",
                    ),
                    text_tokens=tokens,
                    # This is the native-domain target, not a human MOS.
                    quality_target=1.0,
                    aspect_targets=(0.0, 0.0, 0.0, 0.0),
                    aspect_mask=(False, False, False, False),
                    content_supervised=content_supervised,
                )
            )
            if limit is not None and len(output) >= limit:
                stop = True
                break
        if stop:
            break
    return NativeLoadResult(
        records=tuple(output),
        source_rows=source_rows,
        exclusions=tuple(sorted(exclusions.items())),
    )


class NativeAudioResolver:
    """Decode relative files and embedded Parquet audio without materializing it."""

    def __init__(
        self,
        corpus_roots: Mapping[str, str | Path],
        *,
        target_sample_rate: int = 16_000,
        embedded_batch_size: int = 32,
    ) -> None:
        if target_sample_rate <= 0:
            raise AdversarialSpeechDataError("target_sample_rate must be positive")
        if embedded_batch_size <= 0:
            raise AdversarialSpeechDataError("embedded_batch_size must be positive")
        roots: dict[str, Path] = {}
        for source_id, value in corpus_roots.items():
            if not isinstance(source_id, str) or not source_id:
                raise AdversarialSpeechDataError("corpus source ID is invalid")
            root = Path(value).resolve(strict=True)
            if not root.is_dir():
                raise AdversarialSpeechDataError(
                    f"native corpus root is not a directory: {source_id}"
                )
            roots[source_id] = root
        self._roots = roots
        self.target_sample_rate = int(target_sample_rate)
        self.embedded_batch_size = int(embedded_batch_size)

    def _root(self, source_id: str) -> Path:
        try:
            return self._roots[source_id]
        except KeyError as exc:
            raise AdversarialSpeechDataError(
                f"native corpus root is missing: {source_id}"
            ) from exc

    def _validate_decoded(
        self,
        record: NativeSpeechRecord,
        decoded: DecodedAudio,
    ) -> DecodedAudio:
        if decoded.source_sample_rate != record.sample_rate_hz:
            raise AdversarialSpeechDataError(
                "decoded native sample rate differs from the manifest"
            )
        if decoded.source_channels != record.channels:
            raise AdversarialSpeechDataError(
                "decoded native channel count differs from the manifest"
            )
        tolerance = max(0.02, 2.0 / record.sample_rate_hz)
        if abs(decoded.duration_seconds - record.duration_seconds) > tolerance:
            raise AdversarialSpeechDataError(
                "decoded native duration differs from the manifest"
            )
        return decoded

    def _decode_payload(
        self,
        record: NativeSpeechRecord,
        payload: bytes,
    ) -> DecodedAudio:
        try:
            samples, source_sample_rate = sf.read(
                io.BytesIO(payload),
                dtype="float32",
                always_2d=True,
            )
        except (OSError, RuntimeError, sf.LibsndfileError) as exc:
            raise AdversarialSpeechDataError(
                "failed to decode embedded native audio"
            ) from exc
        if (
            samples.ndim != 2
            or samples.shape[0] == 0
            or samples.shape[1] == 0
            or not np.isfinite(samples).all()
        ):
            raise AdversarialSpeechDataError(
                "embedded native audio is empty or non-finite"
            )
        source_channels = int(samples.shape[1])
        mono = (
            samples[:, 0]
            if source_channels == 1
            else np.mean(samples, axis=1, dtype=np.float64).astype(np.float32)
        )
        resampled = resample_audio(
            mono,
            int(source_sample_rate),
            self.target_sample_rate,
        )
        decoded = DecodedAudio(
            samples=resampled,
            sample_rate=self.target_sample_rate,
            source_sample_rate=int(source_sample_rate),
            source_channels=source_channels,
        )
        return self._validate_decoded(record, decoded)

    def decode_many(
        self,
        records: Sequence[NativeSpeechRecord],
    ) -> tuple[DecodedAudio, ...]:
        if not records:
            raise AdversarialSpeechDataError("native records must not be empty")
        output: list[DecodedAudio | None] = [None] * len(records)
        embedded: defaultdict[
            tuple[str, str],
            list[tuple[int, int, NativeSpeechRecord]],
        ] = defaultdict(list)
        for position, record in enumerate(records):
            if record.audio_storage == "relative_file":
                if record.relative_path is None:
                    raise AdversarialSpeechDataError(
                        "relative native record has no path"
                    )
                source = _contained_path(
                    self._root(record.source_id),
                    record.relative_path,
                    field_name="native relative_path",
                    require_file=True,
                )
                output[position] = self._validate_decoded(
                    record,
                    decode_audio(
                        source,
                        target_sample_rate=self.target_sample_rate,
                    ),
                )
                continue
            reference = parse_embedded_audio_reference(record.embedded_reference)
            embedded[(record.source_id, reference.shard_relative_path)].append(
                (position, reference.row_index, record)
            )

        if embedded:
            try:
                import pyarrow.parquet as pq  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - scorer dependency
                raise RuntimeError(
                    "pyarrow is required to decode embedded native audio"
                ) from exc
            for (source_id, shard_relative), requests in embedded.items():
                shard = _contained_path(
                    self._root(source_id),
                    shard_relative,
                    field_name="embedded shard",
                    require_file=True,
                )
                wanted: defaultdict[
                    int,
                    list[tuple[int, NativeSpeechRecord]],
                ] = defaultdict(list)
                for position, row_index, record in requests:
                    wanted[row_index].append((position, record))
                parquet = pq.ParquetFile(shard)
                if max(wanted) >= parquet.metadata.num_rows:
                    raise AdversarialSpeechDataError(
                        "embedded row index exceeds its Parquet shard"
                    )
                cursor = 0
                for batch in parquet.iter_batches(
                    batch_size=self.embedded_batch_size,
                    columns=["audio"],
                ):
                    batch_end = cursor + batch.num_rows
                    selected = [
                        row_index
                        for row_index in wanted
                        if cursor <= row_index < batch_end
                    ]
                    for row_index in selected:
                        audio = batch.column(0)[row_index - cursor].as_py()
                        payload = (
                            audio.get("bytes") if isinstance(audio, Mapping) else None
                        )
                        if not isinstance(payload, bytes) or not payload:
                            raise AdversarialSpeechDataError(
                                "embedded Parquet row has no audio bytes"
                            )
                        for position, record in wanted.pop(row_index):
                            output[position] = self._decode_payload(
                                record,
                                payload,
                            )
                    cursor = batch_end
                    if not wanted:
                        break
                if wanted:
                    raise AdversarialSpeechDataError(
                        "embedded Parquet rows could not be resolved"
                    )
        if any(value is None for value in output):
            raise RuntimeError("native audio resolution left an empty result")
        return tuple(value for value in output if value is not None)


class DurationMatchedPairSampler:
    """Deterministic nuisance-balanced learner order with duration-matched native."""

    def __init__(
        self,
        learner_records: Sequence[LearnerSpeechRecord],
        native_records: Sequence[NativeSpeechRecord],
        *,
        seed: int,
        duration_boundaries: Sequence[float] = (2.0, 4.0, 6.0, 8.0, 10.0),
    ) -> None:
        if not learner_records or not native_records:
            raise AdversarialSpeechDataError(
                "learner and native records must both be non-empty"
            )
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise AdversarialSpeechDataError("seed must be an integer")
        parsed_boundaries = tuple(float(value) for value in duration_boundaries)
        if (
            not parsed_boundaries
            or any(not isfinite(value) or value <= 0 for value in parsed_boundaries)
            or any(
                right <= left
                for left, right in zip(
                    parsed_boundaries,
                    parsed_boundaries[1:],
                    strict=False,
                )
            )
        ):
            raise AdversarialSpeechDataError(
                "duration boundaries must be finite, positive, and increasing"
            )
        self.learner_records = tuple(learner_records)
        self.native_records = tuple(native_records)
        self.seed = seed
        self.duration_boundaries = parsed_boundaries

    def _bucket(self, duration_seconds: float) -> int:
        return bisect_right(self.duration_boundaries, duration_seconds)

    def _learner_order(self, epoch: int) -> Iterator[LearnerSpeechRecord]:
        groups: defaultdict[tuple[object, ...], list[LearnerSpeechRecord]] = (
            defaultdict(list)
        )
        for record in self.learner_records:
            quality_bin = min(4, int(record.quality_target * 5))
            groups[
                (
                    record.l1_group,
                    record.gender_group,
                    record.proficiency_group,
                    record.error_presence,
                    quality_bin,
                )
            ].append(record)
        rng = random.Random(self.seed + 1_000_003 * epoch)
        queues: dict[tuple[object, ...], deque[LearnerSpeechRecord]] = {}
        for key in sorted(groups, key=repr):
            values = groups[key]
            rng.shuffle(values)
            queues[key] = deque(values)
        active = list(queues)
        while active:
            rng.shuffle(active)
            next_active: list[tuple[object, ...]] = []
            for key in active:
                yield queues[key].popleft()
                if queues[key]:
                    next_active.append(key)
            active = next_active

    def iter_epoch(self, epoch: int = 0) -> Iterator[AdversarialSpeechPair]:
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise AdversarialSpeechDataError("epoch must be a non-negative integer")
        by_bucket: defaultdict[int, list[NativeSpeechRecord]] = defaultdict(list)
        for record in self.native_records:
            by_bucket[self._bucket(record.duration_seconds)].append(record)
        available_buckets = tuple(sorted(by_bucket))
        queues: dict[int, deque[NativeSpeechRecord]] = {}
        cycles: Counter[int] = Counter()

        def refill(bucket: int) -> None:
            values = list(by_bucket[bucket])
            rng = random.Random(
                self.seed + 1_000_003 * epoch + 10_007 * bucket + 97 * cycles[bucket]
            )
            rng.shuffle(values)
            queues[bucket] = deque(values)
            cycles[bucket] += 1

        for learner in self._learner_order(epoch):
            desired = self._bucket(learner.duration_seconds)
            bucket = min(
                available_buckets,
                key=lambda candidate: (abs(candidate - desired), candidate),
            )
            if bucket not in queues or not queues[bucket]:
                refill(bucket)
            yield AdversarialSpeechPair(
                learner=learner,
                native=queues[bucket].popleft(),
            )

    def __iter__(self) -> Iterator[AdversarialSpeechPair]:
        yield from self.iter_epoch(0)

    def __len__(self) -> int:
        return len(self.learner_records)


def _pad_token_sequences(
    sequences: Sequence[tuple[int, ...]],
) -> np.ndarray:
    if not sequences or any(not sequence for sequence in sequences):
        raise AdversarialSpeechDataError("text token sequences must be non-empty")
    width = max(map(len, sequences))
    output = np.full(
        (len(sequences), width),
        PAD_TOKEN_ID,
        dtype=np.int64,
    )
    for row, sequence in enumerate(sequences):
        output[row, : len(sequence)] = sequence
    return output


def _mismatched_tokens(
    tokens: "torch.Tensor",
    sequences: Sequence[tuple[int, ...]],
) -> "torch.Tensor | None":
    import torch

    for shift in range(1, len(sequences)):
        if all(
            sequences[index] != sequences[(index + shift) % len(sequences)]
            for index in range(len(sequences))
        ):
            return torch.roll(tokens, shifts=-shift, dims=0)
    return None


def collate_adversarial_pairs(
    pairs: Sequence[AdversarialSpeechPair],
    *,
    native_audio_resolver: NativeAudioResolver,
    target_sample_rate: int = 16_000,
    pad_to_multiple_of: int = 4,
) -> "AdversarialSpeechBatch":
    """Decode, independently pad each domain, and build the training batch."""

    import torch

    from voice_coach.training.adversarial_tts import AdversarialSpeechBatch

    if not pairs:
        raise AdversarialSpeechDataError("pairs must not be empty")
    learner_audio = [
        decode_audio(
            pair.learner.audio_path,
            target_sample_rate=target_sample_rate,
        )
        for pair in pairs
    ]
    native_audio = native_audio_resolver.decode_many([pair.native for pair in pairs])
    learner_padded = pad_audio_batch(
        [audio.samples for audio in learner_audio],
        pad_to_multiple_of=pad_to_multiple_of,
    )
    native_padded = pad_audio_batch(
        [audio.samples for audio in native_audio],
        pad_to_multiple_of=pad_to_multiple_of,
    )
    learner_sequences = [pair.learner.text_tokens for pair in pairs]
    native_sequences = [pair.native.text_tokens for pair in pairs]
    learner_tokens = torch.from_numpy(_pad_token_sequences(learner_sequences))
    native_tokens = torch.from_numpy(_pad_token_sequences(native_sequences))
    return AdversarialSpeechBatch(
        learner_waveform=torch.from_numpy(learner_padded.input_values),
        learner_text_tokens=learner_tokens,
        learner_quality_target=torch.tensor(
            [pair.learner.quality_target for pair in pairs],
            dtype=torch.float32,
        ),
        learner_aspect_targets=torch.tensor(
            [pair.learner.aspect_targets for pair in pairs],
            dtype=torch.float32,
        ),
        native_waveform=torch.from_numpy(native_padded.input_values),
        native_text_tokens=native_tokens,
        native_quality_target=torch.tensor(
            [pair.native.quality_target for pair in pairs],
            dtype=torch.float32,
        ),
        native_aspect_targets=torch.tensor(
            [pair.native.aspect_targets for pair in pairs],
            dtype=torch.float32,
        ),
        learner_mask=torch.from_numpy(learner_padded.attention_mask.astype(np.bool_)),
        native_mask=torch.from_numpy(native_padded.attention_mask.astype(np.bool_)),
        mismatched_learner_text_tokens=_mismatched_tokens(
            learner_tokens,
            learner_sequences,
        ),
        mismatched_native_text_tokens=_mismatched_tokens(
            native_tokens,
            native_sequences,
        ),
        learner_aspect_mask=torch.tensor(
            [pair.learner.aspect_mask for pair in pairs],
            dtype=torch.bool,
        ),
        native_aspect_mask=torch.tensor(
            [pair.native.aspect_mask for pair in pairs],
            dtype=torch.bool,
        ),
        learner_content_mask=torch.tensor(
            [pair.learner.content_supervised for pair in pairs],
            dtype=torch.bool,
        ),
        native_content_mask=torch.tensor(
            [pair.native.content_supervised for pair in pairs],
            dtype=torch.bool,
        ),
    )


__all__ = [
    "ASPECT_NAMES",
    "AdversarialSpeechDataError",
    "AdversarialSpeechPair",
    "DurationMatchedPairSampler",
    "EmbeddedAudioReference",
    "LearnerLoadResult",
    "LearnerSpeechRecord",
    "NativeAudioResolver",
    "NativeLoadResult",
    "NativeManifestPolicy",
    "NativeSpeechRecord",
    "PAD_TOKEN_ID",
    "PHONE_TO_TOKEN_ID",
    "UNKNOWN_TEXT_TOKEN_ID",
    "collate_adversarial_pairs",
    "encode_target_phone_intervals",
    "load_aihub_learner_records",
    "load_native_speech_records",
    "load_reserved_prompt_hashes",
    "native_policy_from_catalog",
    "parse_embedded_audio_reference",
]
