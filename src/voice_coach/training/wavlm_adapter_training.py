"""Executable online-audio training primitives for the WP4 S104 adapter run.

This module is imported only after the CPU preflight has published and
revalidated its immutable execution lock.  Raw audio is decoded lazily after a
manifest row has passed the train/dev, QC, reserved-prompt, primary-scope, and
speaker-partition guards.  Every WavLM chunk forward is shared by oracle-phone
and utterance sufficient-statistic pooling so encoder gradients are never
replaced by a frozen pooled-feature cache.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import shutil
import sys
from typing import Any, Literal, cast
from uuid import uuid4

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.dataset as ds  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from safetensors import safe_open
from safetensors.torch import save_file as save_safetensors
import torch
from torch import nn
from torch.nn import functional as functional
from transformers import WavLMModel

from voice_coach.data.audio import decode_audio, make_label_aware_chunks
from voice_coach.data.intervals import Interval
from voice_coach.data.wav_qc import pcm_sha256
from voice_coach.evaluation.metrics import (
    auprc,
    icc_2_1,
    macro_f1,
    mae,
    quadratic_weighted_kappa,
    spearman_correlation,
)
from voice_coach.experiments.registry import ExperimentRun, fingerprint_file
from voice_coach.features.ssl import (
    WAVLM_BASE_PLUS_FRAME_GEOMETRY,
    prepare_wavlm_input,
)
from voice_coach.models.wavlm_adapter import (
    AdapterTunedWavLM,
    audit_trainable_parameters,
    group_trainable_parameters,
)
from voice_coach.training.wavlm_adapter_contract import (
    EXECUTION_LOCK_SCHEMA_VERSION,
    OUTCOMES,
    SEEDS,
    S104ContractError,
    load_config,
    load_json_strict,
    sha256_file,
    validate_config,
    validate_implementation_sources,
    validate_s103_decision,
)


REPORT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_report.v1"
CHECKPOINT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_checkpoint.v1"
PHONE_PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s104_phone_predictions.v1"
UTTERANCE_PREDICTION_SCHEMA_VERSION = "voice_coach.wavlm_s104_utterance_predictions.v1"
COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s104_adapter_commit.v1"
UTTERANCE_HASH_NAMESPACE = "voice-coach.aihub71490.utterance.v1"
UNKNOWN_TARGET = "<UNK>"
ALLOWED_SPLITS = frozenset({"train", "dev"})
FAILURE_STAGES = frozenset(
    {
        "registry_initialization",
        "runtime_validation",
        "metadata_scan",
        "model_loading",
        "seed_training",
        "inner_selection",
        "dev_inference",
        "artifact_generation",
        "artifact_publication",
        "registry_registration",
        "commit_publication",
    }
)
MANIFEST_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "pcm_sha256",
    "prompt_sha256",
    "qc_flags",
    "l1",
    "gender",
    "proficiency",
    "target_phone_intervals",
    "phone_alignments",
)
SHA256_PATTERN = "0123456789abcdef"
Subset = Literal["fit", "inner_validation", "dev"]


class S104TrainingError(RuntimeError):
    """Raised when the executable S104 contract cannot be honored."""


@dataclass(frozen=True, slots=True)
class UtteranceLabel:
    speaker_id_sha256: str
    split: str
    scores: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class OnlineExample:
    utterance_id_sha256: str
    speaker_id_sha256: str
    split: str
    audio_path: Path
    pcm_sha256: str
    intervals: tuple[Interval, ...]
    binary_labels: tuple[int, ...]
    target_indices: tuple[int, ...]
    l1: str
    gender: str
    proficiency: str
    scores: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class PooledExample:
    phone_embeddings: torch.Tensor
    utterance_embedding: torch.Tensor
    phone_frame_counts: tuple[int, ...]
    utterance_frame_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class FitMetadata:
    target_vocabulary: tuple[str, ...]
    positive_phone_rows: int
    negative_phone_rows: int
    pos_weight: float
    fit_utterances: int


@dataclass(frozen=True, slots=True)
class ExpectedDevUtterance:
    speaker_id_sha256: str
    l1: str
    gender: str
    proficiency: str
    phones: tuple[tuple[str, int], ...]
    scores: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SelectionResult:
    best_epoch: int
    threshold: float
    phone_macro_f1: float
    comprehensibility_icc_2_1: float | None
    stopped_early: bool
    history: tuple[Mapping[str, Any], ...]
    trainable_state: Mapping[str, torch.Tensor]


@dataclass(frozen=True, slots=True)
class PredictionBundle:
    phone_targets: np.ndarray
    phone_probabilities: np.ndarray
    utterance_targets: np.ndarray
    utterance_predictions: np.ndarray
    utterance_observed: np.ndarray
    phone_slices: Mapping[str, Mapping[str, tuple[np.ndarray, np.ndarray]]]
    utterance_slices: Mapping[
        str,
        Mapping[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    ]
    utterance_rows: int
    phone_rows: int


@dataclass(frozen=True, slots=True)
class PublishedGeneration:
    generation_id: str
    generation_path: Path
    artifacts: tuple[Mapping[str, Any], ...]
    created: bool


class GenerationPublicationDurabilityError(OSError):
    """A complete rename succeeded but its parent fsync did not."""

    def __init__(self, publication: PublishedGeneration) -> None:
        super().__init__("generation durability fsync failed after complete rename")
        self.publication = publication


@dataclass(frozen=True, slots=True)
class _StableAudioIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int


class AudioIntegrityVerifier:
    """Hash each pinned WAV once and reject later metadata identity changes."""

    def __init__(self) -> None:
        self._verified: dict[Path, tuple[str, _StableAudioIdentity]] = {}

    @staticmethod
    def _identity(path: Path) -> _StableAudioIdentity:
        value = path.stat()
        return _StableAudioIdentity(
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
        )

    def verify(self, path: Path, expected_sha256: str) -> None:
        existing = self._verified.get(path)
        current = self._identity(path)
        if existing is not None:
            if existing != (expected_sha256, current):
                raise S104TrainingError("raw audio identity changed after verification")
            return
        before = current
        actual = pcm_sha256(path)
        after = self._identity(path)
        if before != after or actual != expected_sha256:
            raise S104TrainingError("raw audio PCM fingerprint differs")
        self._verified[path] = (expected_sha256, after)

    def assert_stable(self) -> None:
        for path, (expected, identity) in self._verified.items():
            if self._identity(path) != identity:
                raise S104TrainingError("raw audio identity changed during training")
            # Retain the expected digest in the in-memory identity binding.
            if not _is_sha256(expected):
                raise S104TrainingError("raw audio fingerprint binding is invalid")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in SHA256_PATTERN for character in value)
    )


def _safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    if isinstance(value, np.ndarray):
        return _safe_json(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": (",", ":"),
        "allow_nan": False,
    }
    if pretty:
        options.pop("separators")
        options["indent"] = 2
    return (json.dumps(_safe_json(value), **options) + "\n").encode("utf-8")


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    replaced = False
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def publish_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical_json_bytes(value, pretty=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != encoded:
            raise S104TrainingError(
                "immutable S104 commit already exists with different bytes"
            )
        return
    atomic_write_json(path, value)


def hash_utterance_id(source_id: object) -> str:
    if not isinstance(source_id, str) or not source_id:
        raise S104TrainingError("manifest id is invalid")
    return hashlib.sha256(
        f"{UTTERANCE_HASH_NAMESPACE}:{source_id}".encode("utf-8")
    ).hexdigest()


def speaker_bucket(speaker_id_sha256: str, modulus: int) -> int:
    if not _is_sha256(speaker_id_sha256) or modulus <= 0:
        raise S104TrainingError("speaker partition input is invalid")
    return int(speaker_id_sha256[:16], 16) % modulus


def resolve_audio_path(dataset_root: Path, relative_path: object) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise S104TrainingError("audio locator is invalid")
    relative = Path(relative_path)
    if relative.is_absolute():
        raise S104TrainingError("audio locator must be relative")
    if any(part.casefold().startswith(".env") for part in relative.parts):
        raise S104TrainingError("audio locator references an environment file")
    root = dataset_root.resolve(strict=True)
    candidate = (root / relative).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise S104TrainingError("audio locator escapes the dataset root") from error
    resolved_relative = candidate.relative_to(root)
    if any(part.casefold().startswith(".env") for part in resolved_relative.parts):
        raise S104TrainingError("audio locator resolves through an environment file")
    if not candidate.is_file():
        raise S104TrainingError("audio locator is not a regular file")
    return candidate


def load_reserved_prompt_hashes(path: Path) -> set[str]:
    document = load_json_strict(path, "prompt reservation")
    entries = document.get("reserved_prompts")
    if not isinstance(entries, list) or not entries:
        raise S104TrainingError("prompt reservation is empty")
    output: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise S104TrainingError("prompt reservation entry is invalid")
        prompt = entry.get("prompt_sha256")
        if not _is_sha256(prompt) or cast(str, prompt) in output:
            raise S104TrainingError("prompt reservation hash is invalid")
        output.add(cast(str, prompt))
    return output


def load_utterance_labels(path: Path) -> dict[str, UtteranceLabel]:
    dataset = ds.dataset(path, format="parquet")
    scanner = dataset.scanner(
        columns=[
            "utterance_id_sha256",
            "speaker_id_sha256",
            "split",
            *OUTCOMES,
        ],
        filter=ds.field("split").isin(sorted(ALLOWED_SPLITS)),
        batch_size=8_192,
        use_threads=False,
    )
    output: dict[str, UtteranceLabel] = {}
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            identifier = row["utterance_id_sha256"]
            speaker = row["speaker_id_sha256"]
            split = row["split"]
            if (
                not _is_sha256(identifier)
                or not _is_sha256(speaker)
                or split not in ALLOWED_SPLITS
                or identifier in output
            ):
                raise S104TrainingError("explicit-label join key is invalid")
            scores: list[float] = []
            for outcome in OUTCOMES:
                value = row[outcome]
                if value in (None, 0):
                    scores.append(float("nan"))
                    continue
                score = float(value)
                if not math.isfinite(score) or score not in (1.0, 2.0, 3.0, 4.0, 5.0):
                    raise S104TrainingError(
                        "explicit outcome is not an ordinal score 1-5"
                    )
                scores.append(score)
            output[str(identifier)] = UtteranceLabel(
                speaker_id_sha256=str(speaker),
                split=str(split),
                scores=cast(tuple[float, float, float], tuple(scores)),
            )
    if not output:
        raise S104TrainingError("explicit-label cache is empty")
    return output


def _clean_phone_targets(
    intervals_value: object,
    alignments_value: object,
) -> tuple[tuple[Interval, ...], tuple[int, ...]]:
    if not isinstance(intervals_value, list) or not isinstance(
        alignments_value,
        list,
    ):
        raise S104TrainingError("phone annotation is not a list")
    intervals: list[Interval] = []
    for item in intervals_value:
        if not isinstance(item, Mapping):
            raise S104TrainingError("phone interval is invalid")
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        start_value = item.get("start")
        end_value = item.get("end")
        if (
            isinstance(start_value, bool)
            or not isinstance(start_value, (int, float))
            or isinstance(end_value, bool)
            or not isinstance(end_value, (int, float))
        ):
            raise S104TrainingError("phone interval bounds are invalid")
        start = float(start_value)
        end = float(end_value)
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            raise S104TrainingError("phone interval bounds are invalid")
        intervals.append(Interval(start=start, end=end, label=label))
    if intervals != sorted(intervals, key=lambda item: (item.start, item.end)):
        raise S104TrainingError("phone intervals are not ordered")
    target_bearing = [
        item
        for item in alignments_value
        if isinstance(item, Mapping) and item.get("target_label") is not None
    ]
    if len(target_bearing) != len(intervals):
        raise S104TrainingError("phone alignment cardinality differs")
    labels: list[int] = []
    for interval, alignment in zip(intervals, target_bearing, strict=True):
        if str(alignment.get("target_label")) != interval.label:
            raise S104TrainingError("phone alignment target differs")
        operation = alignment.get("operation")
        if operation == "match":
            labels.append(0)
        elif operation in {"substitution", "deletion"}:
            labels.append(1)
        else:
            raise S104TrainingError("phone alignment operation is invalid")
    return tuple(intervals), tuple(labels)


def bounded_shuffle(
    items: Iterable[Mapping[str, Any]],
    *,
    buffer_size: int,
    seed: int,
) -> Iterator[Mapping[str, Any]]:
    """Deterministically shuffle a stream while retaining at most N rows."""

    if buffer_size <= 0:
        raise ValueError("buffer_size must be positive")
    generator = random.Random(seed)
    iterator = iter(items)
    buffer: list[Mapping[str, Any]] = []
    for _ in range(buffer_size):
        try:
            buffer.append(next(iterator))
        except StopIteration:
            break
    while buffer:
        index = generator.randrange(len(buffer))
        selected = buffer[index]
        try:
            buffer[index] = next(iterator)
        except StopIteration:
            buffer.pop(index)
        yield selected


def _manifest_rows(path: Path, *, split: str) -> Iterator[Mapping[str, Any]]:
    if split not in ALLOWED_SPLITS:
        raise S104TrainingError("manifest split is forbidden")
    scanner = ds.dataset(path, format="parquet").scanner(
        columns=list(MANIFEST_COLUMNS),
        filter=ds.field("split") == split,
        batch_size=1_024,
        use_threads=False,
    )
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            if row.get("split") != split:
                raise S104TrainingError("protected split escaped scanner filter")
            yield row


def iter_examples(
    *,
    manifest_path: Path,
    dataset_root: Path,
    labels: Mapping[str, UtteranceLabel],
    reserved_prompts: set[str],
    target_vocabulary: Mapping[str, int],
    subset: Subset,
    modulus: int,
    validation_buckets: set[int],
    allowed_l1: set[str],
    allowed_gender: set[str],
    shuffle_buffer: int | None = None,
    shuffle_seed: int | None = None,
) -> Iterator[OnlineExample]:
    """Stream one guarded subset and resolve raw audio only after row checks."""

    split = "dev" if subset == "dev" else "train"
    rows: Iterable[Mapping[str, Any]] = _manifest_rows(
        manifest_path,
        split=split,
    )
    if shuffle_buffer is not None:
        if subset != "fit" or shuffle_seed is None:
            raise S104TrainingError("only train-fit may use bounded shuffling")
        rows = bounded_shuffle(
            rows,
            buffer_size=shuffle_buffer,
            seed=shuffle_seed,
        )
    unknown_index = target_vocabulary.get(UNKNOWN_TARGET)
    if unknown_index is None:
        raise S104TrainingError("target vocabulary lacks <UNK>")
    for row in rows:
        if row.get("qc_flags"):
            continue
        prompt = row.get("prompt_sha256")
        if not _is_sha256(prompt):
            raise S104TrainingError("manifest prompt hash is invalid")
        if prompt in reserved_prompts:
            continue
        expected_pcm_sha256 = row.get("pcm_sha256")
        if not _is_sha256(expected_pcm_sha256):
            raise S104TrainingError("manifest PCM fingerprint is invalid")
        if str(row.get("l1") or "") not in allowed_l1:
            continue
        if str(row.get("gender") or "") not in allowed_gender:
            continue
        speaker = row.get("speaker_id")
        if not _is_sha256(speaker):
            raise S104TrainingError("manifest speaker hash is invalid")
        in_validation = speaker_bucket(str(speaker), modulus) in validation_buckets
        if subset == "fit" and in_validation:
            continue
        if subset == "inner_validation" and not in_validation:
            continue
        identifier = hash_utterance_id(row.get("id"))
        label = labels.get(identifier)
        if label is None or label.speaker_id_sha256 != speaker or label.split != split:
            raise S104TrainingError("manifest/explicit-label join differs")
        intervals, binary_labels = _clean_phone_targets(
            row.get("target_phone_intervals"),
            row.get("phone_alignments"),
        )
        indices = tuple(
            target_vocabulary.get(interval.label, unknown_index)
            for interval in intervals
        )
        # This is deliberately the final validation step before opening raw
        # audio. Protected/QC/reserved/out-of-scope rows never reach it.
        audio_path = resolve_audio_path(dataset_root, row.get("audio_path"))
        yield OnlineExample(
            utterance_id_sha256=identifier,
            speaker_id_sha256=str(speaker),
            split=split,
            audio_path=audio_path,
            pcm_sha256=str(expected_pcm_sha256),
            intervals=intervals,
            binary_labels=binary_labels,
            target_indices=indices,
            l1=str(row.get("l1") or ""),
            gender=str(row.get("gender") or ""),
            proficiency=str(row.get("proficiency") or ""),
            scores=label.scores,
        )


def expected_dev_ground_truth(
    *,
    manifest_path: Path,
    labels: Mapping[str, UtteranceLabel],
    reserved_prompts: set[str],
    allowed_l1: set[str],
    allowed_gender: set[str],
) -> Mapping[str, ExpectedDevUtterance]:
    """Reconstruct the exact persisted dev truth without opening raw audio."""

    output: dict[str, ExpectedDevUtterance] = {}
    for row in _manifest_rows(manifest_path, split="dev"):
        if row.get("qc_flags"):
            continue
        prompt = row.get("prompt_sha256")
        if not _is_sha256(prompt):
            raise S104TrainingError("dev manifest prompt hash is invalid")
        if prompt in reserved_prompts:
            continue
        l1 = str(row.get("l1") or "")
        gender = str(row.get("gender") or "")
        if l1 not in allowed_l1 or gender not in allowed_gender:
            continue
        speaker = row.get("speaker_id")
        if not _is_sha256(speaker):
            raise S104TrainingError("dev manifest speaker hash is invalid")
        identifier = hash_utterance_id(row.get("id"))
        if identifier in output:
            raise S104TrainingError("dev manifest utterance is duplicated")
        label = labels.get(identifier)
        if label is None or label.speaker_id_sha256 != speaker or label.split != "dev":
            raise S104TrainingError("dev manifest/label join differs")
        intervals, binary_labels = _clean_phone_targets(
            row.get("target_phone_intervals"),
            row.get("phone_alignments"),
        )
        output[identifier] = ExpectedDevUtterance(
            speaker_id_sha256=str(speaker),
            l1=l1,
            gender=gender,
            proficiency=str(row.get("proficiency") or ""),
            phones=tuple(
                (interval.label, binary)
                for interval, binary in zip(
                    intervals,
                    binary_labels,
                    strict=True,
                )
            ),
            scores=label.scores,
        )
    if not output:
        raise S104TrainingError("expected dev ground truth is empty")
    return output


def scan_fit_metadata(
    *,
    manifest_path: Path,
    reserved_prompts: set[str],
    modulus: int,
    validation_buckets: set[int],
    allowed_l1: set[str],
    allowed_gender: set[str],
) -> FitMetadata:
    """Derive vocabulary and class weight from train-fit metadata only."""

    targets: set[str] = set()
    positive = 0
    negative = 0
    utterances = 0
    for row in _manifest_rows(manifest_path, split="train"):
        if row.get("qc_flags") or row.get("prompt_sha256") in reserved_prompts:
            continue
        if (
            str(row.get("l1") or "") not in allowed_l1
            or str(row.get("gender") or "") not in allowed_gender
        ):
            continue
        speaker = row.get("speaker_id")
        if not _is_sha256(speaker):
            raise S104TrainingError("manifest speaker hash is invalid")
        if speaker_bucket(str(speaker), modulus) in validation_buckets:
            continue
        if not _is_sha256(row.get("pcm_sha256")):
            raise S104TrainingError("train-fit PCM fingerprint is invalid")
        intervals, labels = _clean_phone_targets(
            row.get("target_phone_intervals"),
            row.get("phone_alignments"),
        )
        targets.update(item.label for item in intervals)
        positive += sum(labels)
        negative += len(labels) - sum(labels)
        utterances += 1
    if utterances <= 0 or positive <= 0 or negative <= 0 or not targets:
        raise S104TrainingError("train-fit metadata lacks both phone classes")
    vocabulary = (UNKNOWN_TARGET, *sorted(targets))
    return FitMetadata(
        target_vocabulary=vocabulary,
        positive_phone_rows=positive,
        negative_phone_rows=negative,
        pos_weight=negative / positive,
        fit_utterances=utterances,
    )


def pool_online_example(
    *,
    model: AdapterTunedWavLM,
    example: OnlineExample,
    device: torch.device,
    do_normalize: bool,
    maximum_chunk_seconds: float,
    use_bfloat16: bool,
    audio_integrity: AudioIntegrityVerifier | None = None,
) -> PooledExample:
    """Run each chunk once and pool phones plus utterance sufficient stats."""

    if audio_integrity is not None:
        audio_integrity.verify(example.audio_path, example.pcm_sha256)
    audio = decode_audio(example.audio_path, target_sample_rate=16_000)
    if audio_integrity is not None:
        audio_integrity.verify(example.audio_path, example.pcm_sha256)
    tolerance = 1 / 16_000 + 1e-6
    if any(
        interval.end > audio.duration_seconds + tolerance
        for interval in example.intervals
    ):
        raise S104TrainingError("phone interval exceeds decoded audio")
    intervals = tuple(
        Interval(
            start=min(item.start, audio.duration_seconds),
            end=min(item.end, audio.duration_seconds),
            label=item.label,
        )
        for item in example.intervals
    )
    chunks = make_label_aware_chunks(
        audio.duration_seconds,
        intervals,
        max_duration_seconds=maximum_chunk_seconds,
    )
    utterance_sum: torch.Tensor | None = None
    utterance_square_sum: torch.Tensor | None = None
    utterance_count = 0
    phone_sums: list[torch.Tensor | None] = [None] * len(intervals)
    phone_counts = [0] * len(intervals)
    receptive_field = WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
    for chunk in chunks:
        start_sample = round(chunk.start_seconds * 16_000)
        end_sample = round(chunk.end_seconds * 16_000)
        values = prepare_wavlm_input(
            torch.from_numpy(audio.samples[start_sample:end_sample]),
            do_normalize=do_normalize,
        )
        actual_samples = int(values.numel())
        padded_short_chunk = actual_samples < receptive_field
        if actual_samples < receptive_field:
            values = functional.pad(
                values,
                (0, receptive_field - actual_samples),
            )
        input_samples = int(values.numel())
        attention_mask = torch.zeros(
            (1, input_samples),
            dtype=torch.long,
            device=device,
        )
        valid_attention_samples = (
            input_samples if padded_short_chunk else actual_samples
        )
        attention_mask[:, :valid_attention_samples] = 1
        autocast_context = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if device.type == "cuda" and use_bfloat16
            else nullcontext()
        )
        with autocast_context:
            hidden = model.encode(
                values.unsqueeze(0).to(device=device),
                attention_mask=attention_mask,
            )[0]
        expected_frames = WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_samples
        )
        if hidden.ndim != 2 or hidden.shape[0] != expected_frames:
            raise S104TrainingError("WavLM output disagrees with frame geometry")
        finite_hidden = hidden.to(dtype=torch.float32)
        if not bool(torch.isfinite(finite_hidden).all()):
            raise S104TrainingError("WavLM output is non-finite")
        chunk_sum = finite_hidden.sum(dim=0)
        chunk_square_sum = finite_hidden.square().sum(dim=0)
        utterance_sum = (
            chunk_sum if utterance_sum is None else utterance_sum + chunk_sum
        )
        utterance_square_sum = (
            chunk_square_sum
            if utterance_square_sum is None
            else utterance_square_sum + chunk_square_sum
        )
        utterance_count += int(finite_hidden.shape[0])
        for index, interval in enumerate(intervals):
            overlap_start = max(interval.start, chunk.start_seconds)
            overlap_end = min(interval.end, chunk.end_seconds)
            if overlap_end <= overlap_start:
                continue
            first, stop = WAVLM_BASE_PLUS_FRAME_GEOMETRY.interval_frame_span(
                start_seconds=overlap_start - chunk.start_seconds,
                end_seconds=overlap_end - chunk.start_seconds,
                input_sample_count=input_samples,
            )
            if first < 0 or stop <= first or stop > finite_hidden.shape[0]:
                raise S104TrainingError("phone frame span is invalid")
            part_sum = finite_hidden[first:stop].sum(dim=0)
            phone_sums[index] = (
                part_sum
                if phone_sums[index] is None
                else cast(torch.Tensor, phone_sums[index]) + part_sum
            )
            phone_counts[index] += stop - first
    if utterance_sum is None or utterance_square_sum is None or utterance_count <= 0:
        raise S104TrainingError("utterance received no WavLM frame")
    if any(
        value is None or count <= 0
        for value, count in zip(phone_sums, phone_counts, strict=True)
    ):
        raise S104TrainingError("a phone interval received no WavLM frame")
    mean = utterance_sum / utterance_count
    variance = torch.clamp(
        utterance_square_sum / utterance_count - mean.square(),
        min=0.0,
    )
    # Preserve an exact zero population standard deviation while avoiding the
    # infinite derivative of sqrt(0) for one-frame/constant dimensions.
    safe_standard_deviation = torch.where(
        variance > 0,
        torch.sqrt(torch.clamp(variance, min=1e-12)),
        torch.zeros_like(variance),
    )
    utterance_embedding = torch.cat(
        (mean, safe_standard_deviation),
        dim=0,
    )
    if phone_sums:
        phone_embeddings = torch.stack(
            [
                cast(torch.Tensor, value) / count
                for value, count in zip(phone_sums, phone_counts, strict=True)
            ]
        )
    else:
        phone_embeddings = mean.new_empty((0, mean.numel()))
    return PooledExample(
        phone_embeddings=phone_embeddings,
        utterance_embedding=utterance_embedding,
        phone_frame_counts=tuple(phone_counts),
        utterance_frame_count=utterance_count,
        chunk_count=len(chunks),
    )


def threshold_grid(config: Mapping[str, Any]) -> np.ndarray:
    grid = cast(Mapping[str, Any], config["optimization"]["phone_threshold_grid"])
    start = float(grid["start"])
    stop = float(grid["stop"])
    step = float(grid["step"])
    values = np.arange(start, stop + step / 2, step, dtype=np.float64)
    if values.size == 0 or not 0 < start <= stop < 1 or step <= 0 or values[-1] > 1:
        raise S104TrainingError("phone threshold grid is invalid")
    return values


def select_phone_threshold(
    targets: np.ndarray,
    probabilities: np.ndarray,
    thresholds: np.ndarray,
) -> tuple[float, float]:
    """Select F1, then distance to 0.5, then the lower threshold."""

    if (
        targets.ndim != 1
        or probabilities.ndim != 1
        or thresholds.ndim != 1
        or len(targets) != len(probabilities)
        or len(targets) == 0
    ):
        raise S104TrainingError("threshold selection arrays are invalid")
    candidates: list[tuple[float, float, float]] = []
    for threshold in thresholds:
        score = macro_f1(
            targets,
            probabilities >= float(threshold),
            labels=[0, 1],
        )
        candidates.append(
            (
                float(score),
                -abs(float(threshold) - 0.5),
                -float(threshold),
            )
        )
    index = max(range(len(candidates)), key=candidates.__getitem__)
    return float(thresholds[index]), float(candidates[index][0])


def _utterance_metric_bundle(
    targets: np.ndarray,
    predictions: np.ndarray,
) -> dict[str, float]:
    if len(targets) == 0:
        return {
            "qwk": float("nan"),
            "spearman": float("nan"),
            "mae": float("nan"),
            "icc_2_1": float("nan"),
        }
    rounded = np.clip(np.rint(predictions), 1, 5).astype(np.int64)
    return {
        "qwk": quadratic_weighted_kappa(
            targets.astype(np.int64),
            rounded,
            labels=[1, 2, 3, 4, 5],
        ),
        "spearman": spearman_correlation(targets, predictions),
        "mae": mae(targets, predictions),
        "icc_2_1": (
            icc_2_1(np.column_stack((targets, predictions)))
            if len(targets) >= 2
            else float("nan")
        ),
    }


def metric_bundle(
    bundle: PredictionBundle,
    *,
    threshold: float,
) -> dict[str, Any]:
    phone = {
        "rows": bundle.phone_rows,
        "decision_threshold": threshold,
        "macro_f1": macro_f1(
            bundle.phone_targets,
            bundle.phone_probabilities >= threshold,
            labels=[0, 1],
        ),
        "auprc": auprc(
            bundle.phone_targets,
            bundle.phone_probabilities,
            labels=[0, 1],
            positive_label=1,
            undefined="nan",
        ),
    }
    outcomes: dict[str, Any] = {}
    for index, outcome in enumerate(OUTCOMES):
        observed = bundle.utterance_observed[:, index]
        values = _utterance_metric_bundle(
            bundle.utterance_targets[observed, index],
            bundle.utterance_predictions[observed, index],
        )
        outcomes[outcome] = {
            "observed_rows": int(observed.sum()),
            **values,
        }
    return {
        "phone": phone,
        "utterance": {
            "rows": bundle.utterance_rows,
            "outcomes": outcomes,
        },
    }


def _slice_metrics(bundle: PredictionBundle, *, threshold: float) -> dict[str, Any]:
    phone_output: dict[str, Any] = {}
    for dimension, phone_groups_by_value in bundle.phone_slices.items():
        phone_output[dimension] = {}
        for group, (targets, probabilities) in phone_groups_by_value.items():
            phone_output[dimension][group] = {
                "rows": len(targets),
                "macro_f1": macro_f1(
                    targets,
                    probabilities >= threshold,
                    labels=[0, 1],
                ),
                "auprc": auprc(
                    targets,
                    probabilities,
                    labels=[0, 1],
                    positive_label=1,
                    undefined="nan",
                ),
            }
    utterance_output: dict[str, Any] = {}
    for dimension, utterance_groups_by_value in bundle.utterance_slices.items():
        utterance_output[dimension] = {}
        for group, (
            targets,
            predictions,
            observed,
        ) in utterance_groups_by_value.items():
            outcome_metrics: dict[str, Any] = {}
            for index, outcome in enumerate(OUTCOMES):
                selected = observed[:, index]
                outcome_metrics[outcome] = {
                    "observed_rows": int(selected.sum()),
                    **_utterance_metric_bundle(
                        targets[selected, index],
                        predictions[selected, index],
                    ),
                }
            utterance_output[dimension][group] = {
                "rows": len(targets),
                "outcomes": outcome_metrics,
            }
    return {
        "phone": phone_output,
        "utterance": utterance_output,
    }


def slice_metrics_from_prediction_files(
    *,
    phone_path: Path,
    utterance_path: Path,
    threshold: float,
) -> dict[str, Any]:
    """Compute one slice dimension at a time without another model pass."""

    phone_output: dict[str, Any] = {}
    for dimension in ("l1", "gender", "proficiency", "target_label"):
        groups: dict[str, tuple[list[int], list[float]]] = {}
        parquet = pq.ParquetFile(phone_path)
        for batch in parquet.iter_batches(
            batch_size=8_192,
            columns=[
                dimension,
                "binary_error_label",
                "error_probability",
            ],
        ):
            for row in batch.to_pylist():
                group = str(row[dimension])
                if group not in groups:
                    groups[group] = ([], [])
                groups[group][0].append(int(row["binary_error_label"]))
                groups[group][1].append(float(row["error_probability"]))
        phone_output[dimension] = {}
        for group, (targets_list, probabilities_list) in groups.items():
            targets = np.asarray(targets_list, dtype=np.int8)
            probabilities = np.asarray(
                probabilities_list,
                dtype=np.float32,
            )
            phone_output[dimension][group] = {
                "rows": len(targets),
                "macro_f1": macro_f1(
                    targets,
                    probabilities >= threshold,
                    labels=[0, 1],
                ),
                "auprc": auprc(
                    targets,
                    probabilities,
                    labels=[0, 1],
                    positive_label=1,
                    undefined="nan",
                ),
            }

    utterance_output: dict[str, Any] = {}
    outcome_columns = [
        field
        for outcome in OUTCOMES
        for field in (
            f"{outcome}_true",
            f"{outcome}_pred",
            f"{outcome}_observed",
        )
    ]
    for dimension in ("l1", "gender", "proficiency"):
        groups_values: dict[
            str,
            tuple[
                list[tuple[float, float, float]],
                list[tuple[float, float, float]],
                list[tuple[bool, bool, bool]],
            ],
        ] = {}
        parquet = pq.ParquetFile(utterance_path)
        for batch in parquet.iter_batches(
            batch_size=8_192,
            columns=[dimension, *outcome_columns],
        ):
            for row in batch.to_pylist():
                group = str(row[dimension])
                if group not in groups_values:
                    groups_values[group] = ([], [], [])
                outcome_targets = cast(
                    tuple[float, float, float],
                    tuple(
                        float(row[f"{outcome}_true"])
                        if row[f"{outcome}_observed"]
                        else float("nan")
                        for outcome in OUTCOMES
                    ),
                )
                outcome_predictions = cast(
                    tuple[float, float, float],
                    tuple(float(row[f"{outcome}_pred"]) for outcome in OUTCOMES),
                )
                outcome_observed = cast(
                    tuple[bool, bool, bool],
                    tuple(bool(row[f"{outcome}_observed"]) for outcome in OUTCOMES),
                )
                groups_values[group][0].append(outcome_targets)
                groups_values[group][1].append(outcome_predictions)
                groups_values[group][2].append(outcome_observed)
        utterance_output[dimension] = {}
        for group, values in groups_values.items():
            targets_array = np.asarray(values[0], dtype=np.float32)
            predictions_array = np.asarray(values[1], dtype=np.float32)
            observed_array = np.asarray(values[2], dtype=np.bool_)
            outcome_metrics: dict[str, Any] = {}
            for index, outcome in enumerate(OUTCOMES):
                selected = observed_array[:, index]
                outcome_metrics[outcome] = {
                    "observed_rows": int(selected.sum()),
                    **_utterance_metric_bundle(
                        targets_array[selected, index],
                        predictions_array[selected, index],
                    ),
                }
            utterance_output[dimension][group] = {
                "rows": len(targets_array),
                "outcomes": outcome_metrics,
            }
    return {
        "phone": phone_output,
        "utterance": utterance_output,
        "source": "single_dev_pass_prediction_parquet_no_reinference",
    }


def metric_bundle_from_prediction_files(
    *,
    phone_path: Path,
    utterance_path: Path,
    threshold: float,
) -> dict[str, Any]:
    """Recompute aggregate metrics solely from persisted predictions."""

    phone_table = pq.read_table(
        phone_path,
        columns=["binary_error_label", "error_probability"],
        use_threads=False,
    )
    phone_targets = np.asarray(
        phone_table["binary_error_label"].to_numpy(),
        dtype=np.int8,
    )
    phone_probabilities = np.asarray(
        phone_table["error_probability"].to_numpy(),
        dtype=np.float32,
    )
    outcome_columns = [
        field
        for outcome in OUTCOMES
        for field in (
            f"{outcome}_true",
            f"{outcome}_pred",
            f"{outcome}_observed",
        )
    ]
    utterance_table = pq.read_table(
        utterance_path,
        columns=outcome_columns,
        use_threads=False,
    )
    utterance_rows = utterance_table.num_rows
    utterance_targets = np.full(
        (utterance_rows, len(OUTCOMES)),
        np.nan,
        dtype=np.float32,
    )
    utterance_predictions = np.empty(
        (utterance_rows, len(OUTCOMES)),
        dtype=np.float32,
    )
    utterance_observed = np.empty(
        (utterance_rows, len(OUTCOMES)),
        dtype=np.bool_,
    )
    for index, outcome in enumerate(OUTCOMES):
        target_values = utterance_table[f"{outcome}_true"].to_pylist()
        observed = np.asarray(
            utterance_table[f"{outcome}_observed"].to_numpy(),
            dtype=np.bool_,
        )
        utterance_observed[:, index] = observed
        utterance_predictions[:, index] = np.asarray(
            utterance_table[f"{outcome}_pred"].to_numpy(),
            dtype=np.float32,
        )
        utterance_targets[:, index] = np.asarray(
            [
                float(value) if value is not None else float("nan")
                for value in target_values
            ],
            dtype=np.float32,
        )
    return metric_bundle(
        PredictionBundle(
            phone_targets=phone_targets,
            phone_probabilities=phone_probabilities,
            utterance_targets=utterance_targets,
            utterance_predictions=utterance_predictions,
            utterance_observed=utterance_observed,
            phone_slices={},
            utterance_slices={},
            utterance_rows=utterance_rows,
            phone_rows=len(phone_targets),
        ),
        threshold=threshold,
    )


def _trainable_state(model: nn.Module) -> dict[str, torch.Tensor]:
    return {
        name: parameter.detach().cpu().contiguous().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }


def _load_trainable_state(
    model: nn.Module,
    state: Mapping[str, torch.Tensor],
) -> None:
    named = dict(model.named_parameters())
    expected = {name for name, parameter in named.items() if parameter.requires_grad}
    if set(state) != expected:
        raise S104TrainingError("selected trainable state keys differ")
    with torch.no_grad():
        for name in sorted(expected):
            parameter = named[name]
            value = state[name]
            if parameter.shape != value.shape:
                raise S104TrainingError("selected trainable tensor shape differs")
            parameter.copy_(value.to(device=parameter.device, dtype=parameter.dtype))


def _set_train_mode(model: AdapterTunedWavLM) -> None:
    # Frozen WavLM dropout stays active by preregistration; layerdrop is
    # overridden to exactly zero. Validation/dev switch the whole model to eval.
    model.train()


def set_deterministic_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def _example_windows(
    examples: Iterable[OnlineExample],
    size: int,
) -> Iterator[list[OnlineExample]]:
    if size <= 0:
        raise ValueError("window size must be positive")
    window: list[OnlineExample] = []
    for example in examples:
        window.append(example)
        if len(window) == size:
            yield window
            window = []
    if window:
        yield window


def _backward_window_loss(
    loss: torch.Tensor,
    *,
    actual_utterances: int,
) -> None:
    if actual_utterances <= 0:
        raise S104TrainingError("gradient window is empty")
    (loss / actual_utterances).backward()


def _assert_trainable_parameters_finite(model: nn.Module) -> None:
    checks = [
        torch.isfinite(parameter).all()
        for parameter in model.parameters()
        if parameter.requires_grad
    ]
    if not checks or not bool(torch.stack(checks).all()):
        raise S104TrainingError("trainable parameter is non-finite")


def _joint_loss(
    *,
    model: AdapterTunedWavLM,
    example: OnlineExample,
    pooled: PooledExample,
    pos_weight: torch.Tensor,
    phone_weight: float,
    utterance_weight: float,
    device: torch.device,
) -> tuple[torch.Tensor, dict[str, float]]:
    losses: list[torch.Tensor] = []
    scalar: dict[str, float] = {}
    if pooled.phone_embeddings.shape[0] > 0:
        target_indices = torch.tensor(
            example.target_indices,
            dtype=torch.long,
            device=device,
        )
        binary = torch.tensor(
            example.binary_labels,
            dtype=torch.float32,
            device=device,
        )
        logits = model.phone_head(pooled.phone_embeddings, target_indices)
        phone_loss = functional.binary_cross_entropy_with_logits(
            logits,
            binary,
            pos_weight=pos_weight,
        )
        losses.append(phone_weight * phone_loss)
        scalar["phone_loss"] = float(phone_loss.detach().cpu())
    scores = torch.tensor(example.scores, dtype=torch.float32, device=device)
    observed = torch.isfinite(scores)
    if bool(observed.any()):
        predictions = model.utterance_head(pooled.utterance_embedding.unsqueeze(0))[0]
        utterance_loss = functional.mse_loss(
            predictions[observed],
            scores[observed],
        )
        losses.append(utterance_weight * utterance_loss)
        scalar["utterance_loss"] = float(utterance_loss.detach().cpu())
    if not losses:
        raise S104TrainingError("utterance has no supervised target")
    joint = torch.stack(losses).sum()
    if not bool(torch.isfinite(joint)):
        raise S104TrainingError("joint loss is non-finite")
    return joint, scalar


class DevPassGuard:
    """Permit exactly one complete dev scanner sweep for each seed."""

    def __init__(self) -> None:
        self._entered = False
        self._completed = False

    def enter(self) -> None:
        if self._entered:
            raise S104TrainingError("dev inference attempted more than once")
        self._entered = True

    def complete(self) -> None:
        if not self._entered or self._completed:
            raise S104TrainingError("dev inference completion is invalid")
        self._completed = True

    def assert_complete(self) -> None:
        if not self._completed:
            raise S104TrainingError("dev inference did not complete exactly once")


def _phone_prediction_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("seed", pa.int32(), nullable=False),
            pa.field("split", pa.string(), nullable=False),
            pa.field("development_only", pa.bool_(), nullable=False),
            pa.field("oracle_alignment", pa.bool_(), nullable=False),
            pa.field("utterance_id_sha256", pa.string(), nullable=False),
            pa.field("speaker_id_sha256", pa.string(), nullable=False),
            pa.field("phone_index", pa.int32(), nullable=False),
            pa.field("target_label", pa.string(), nullable=False),
            pa.field("binary_error_label", pa.int8(), nullable=False),
            pa.field("error_probability", pa.float32(), nullable=False),
            pa.field("error_prediction", pa.int8(), nullable=False),
            pa.field("l1", pa.string(), nullable=False),
            pa.field("gender", pa.string(), nullable=False),
            pa.field("proficiency", pa.string(), nullable=False),
        ],
        metadata={
            b"voice_coach.development_only": b"true",
            b"voice_coach.oracle_alignment": b"true",
            b"voice_coach.allowed_split": b"dev",
            b"voice_coach.raw_locator_columns_written": b"false",
            b"voice_coach.access_scope": b"restricted_internal",
        },
    )


def _utterance_prediction_schema() -> pa.Schema:
    fields = [
        pa.field("schema_version", pa.string(), nullable=False),
        pa.field("seed", pa.int32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("development_only", pa.bool_(), nullable=False),
        pa.field("oracle_alignment", pa.bool_(), nullable=False),
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("l1", pa.string(), nullable=False),
        pa.field("gender", pa.string(), nullable=False),
        pa.field("proficiency", pa.string(), nullable=False),
    ]
    for outcome in OUTCOMES:
        fields.extend(
            [
                pa.field(f"{outcome}_true", pa.float32()),
                pa.field(f"{outcome}_pred", pa.float32(), nullable=False),
                pa.field(f"{outcome}_observed", pa.bool_(), nullable=False),
            ]
        )
    return pa.schema(
        fields,
        metadata={
            b"voice_coach.development_only": b"true",
            b"voice_coach.oracle_alignment": b"true",
            b"voice_coach.allowed_split": b"dev",
            b"voice_coach.dev_inference_passes": b"1",
            b"voice_coach.raw_locator_columns_written": b"false",
            b"voice_coach.access_scope": b"restricted_internal",
        },
    )


class PredictionWriters:
    """Bounded Parquet row-group writers for one seed's single dev pass."""

    def __init__(
        self,
        *,
        phone_path: Path,
        utterance_path: Path,
        row_group_size: int = 8_192,
    ) -> None:
        self.phone_path = phone_path
        self.utterance_path = utterance_path
        self.row_group_size = row_group_size
        self._phone_rows: list[dict[str, Any]] = []
        self._utterance_rows: list[dict[str, Any]] = []
        self._phone_writer = pq.ParquetWriter(
            phone_path,
            _phone_prediction_schema(),
            compression="zstd",
        )
        self._utterance_writer = pq.ParquetWriter(
            utterance_path,
            _utterance_prediction_schema(),
            compression="zstd",
        )
        self._closed = False

    def add_phone(self, row: Mapping[str, Any]) -> None:
        self._phone_rows.append(dict(row))
        if len(self._phone_rows) >= self.row_group_size:
            self._flush_phone()

    def add_utterance(self, row: Mapping[str, Any]) -> None:
        self._utterance_rows.append(dict(row))
        if len(self._utterance_rows) >= self.row_group_size:
            self._flush_utterance()

    def _flush_phone(self) -> None:
        if self._phone_rows:
            self._phone_writer.write_table(
                pa.Table.from_pylist(
                    self._phone_rows,
                    schema=_phone_prediction_schema(),
                )
            )
            self._phone_rows.clear()

    def _flush_utterance(self) -> None:
        if self._utterance_rows:
            self._utterance_writer.write_table(
                pa.Table.from_pylist(
                    self._utterance_rows,
                    schema=_utterance_prediction_schema(),
                )
            )
            self._utterance_rows.clear()

    def close(self) -> None:
        if self._closed:
            return
        self._flush_phone()
        self._flush_utterance()
        self._phone_writer.close()
        self._utterance_writer.close()
        self._closed = True

    def __enter__(self) -> PredictionWriters:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def evaluate_examples(
    *,
    model: AdapterTunedWavLM,
    examples: Iterable[OnlineExample],
    device: torch.device,
    do_normalize: bool,
    maximum_chunk_seconds: float,
    use_bfloat16: bool,
    threshold: float | None,
    seed: int,
    writers: PredictionWriters | None = None,
    dev_guard: DevPassGuard | None = None,
    audio_integrity: AudioIntegrityVerifier | None = None,
    collect_slices: bool = False,
) -> PredictionBundle:
    if writers is not None and (threshold is None or dev_guard is None):
        raise S104TrainingError("dev writers require threshold and pass guard")
    if dev_guard is not None:
        dev_guard.enter()
    model.eval()
    phone_targets: list[int] = []
    phone_probabilities: list[float] = []
    utterance_targets: list[tuple[float, float, float]] = []
    utterance_predictions: list[tuple[float, float, float]] = []
    utterance_observed: list[tuple[bool, bool, bool]] = []
    phone_groups: dict[
        str,
        dict[str, tuple[list[int], list[float]]],
    ] = defaultdict(dict)
    utterance_groups: dict[
        str,
        dict[
            str,
            tuple[
                list[tuple[float, float, float]],
                list[tuple[float, float, float]],
                list[tuple[bool, bool, bool]],
            ],
        ],
    ] = defaultdict(dict)

    def phone_group(
        dimension: str,
        value: str,
    ) -> tuple[list[int], list[float]]:
        if value not in phone_groups[dimension]:
            phone_groups[dimension][value] = ([], [])
        return phone_groups[dimension][value]

    def utterance_group(
        dimension: str,
        value: str,
    ) -> tuple[
        list[tuple[float, float, float]],
        list[tuple[float, float, float]],
        list[tuple[bool, bool, bool]],
    ]:
        if value not in utterance_groups[dimension]:
            utterance_groups[dimension][value] = ([], [], [])
        return utterance_groups[dimension][value]

    with torch.inference_mode():
        for example in examples:
            pooled = pool_online_example(
                model=model,
                example=example,
                device=device,
                do_normalize=do_normalize,
                maximum_chunk_seconds=maximum_chunk_seconds,
                use_bfloat16=use_bfloat16,
                audio_integrity=audio_integrity,
            )
            if pooled.phone_embeddings.shape[0] > 0:
                target_indices = torch.tensor(
                    example.target_indices,
                    dtype=torch.long,
                    device=device,
                )
                logits = model.phone_head(
                    pooled.phone_embeddings,
                    target_indices,
                )
                probabilities = (
                    torch.sigmoid(logits).to(device="cpu", dtype=torch.float32).numpy()
                )
            else:
                probabilities = np.empty(0, dtype=np.float32)
            for index, (target, probability, interval) in enumerate(
                zip(
                    example.binary_labels,
                    probabilities,
                    example.intervals,
                    strict=True,
                )
            ):
                numeric_probability = float(probability)
                phone_targets.append(target)
                phone_probabilities.append(numeric_probability)
                if collect_slices:
                    for dimension, value in (
                        ("l1", example.l1),
                        ("gender", example.gender),
                        ("proficiency", example.proficiency),
                        ("target_label", interval.label),
                    ):
                        group_targets, group_probabilities = phone_group(
                            dimension,
                            value,
                        )
                        group_targets.append(target)
                        group_probabilities.append(numeric_probability)
                if writers is not None:
                    writers.add_phone(
                        {
                            "schema_version": PHONE_PREDICTION_SCHEMA_VERSION,
                            "seed": seed,
                            "split": "dev",
                            "development_only": True,
                            "oracle_alignment": True,
                            "utterance_id_sha256": (example.utterance_id_sha256),
                            "speaker_id_sha256": (example.speaker_id_sha256),
                            "phone_index": index,
                            "target_label": interval.label,
                            "binary_error_label": target,
                            "error_probability": numeric_probability,
                            "error_prediction": int(
                                numeric_probability >= cast(float, threshold)
                            ),
                            "l1": example.l1,
                            "gender": example.gender,
                            "proficiency": example.proficiency,
                        }
                    )
            predictions_tensor = model.utterance_head(
                pooled.utterance_embedding.unsqueeze(0)
            )[0]
            predictions_values = cast(
                tuple[float, float, float],
                tuple(
                    float(value)
                    for value in predictions_tensor.to(
                        device="cpu",
                        dtype=torch.float32,
                    ).tolist()
                ),
            )
            observed_values = cast(
                tuple[bool, bool, bool],
                tuple(math.isfinite(value) for value in example.scores),
            )
            utterance_targets.append(example.scores)
            utterance_predictions.append(predictions_values)
            utterance_observed.append(observed_values)
            if collect_slices:
                for dimension, value in (
                    ("l1", example.l1),
                    ("gender", example.gender),
                    ("proficiency", example.proficiency),
                ):
                    grouped_targets, grouped_predictions, grouped_observed = (
                        utterance_group(dimension, value)
                    )
                    grouped_targets.append(example.scores)
                    grouped_predictions.append(predictions_values)
                    grouped_observed.append(observed_values)
            if writers is not None:
                row: dict[str, Any] = {
                    "schema_version": UTTERANCE_PREDICTION_SCHEMA_VERSION,
                    "seed": seed,
                    "split": "dev",
                    "development_only": True,
                    "oracle_alignment": True,
                    "utterance_id_sha256": example.utterance_id_sha256,
                    "speaker_id_sha256": example.speaker_id_sha256,
                    "l1": example.l1,
                    "gender": example.gender,
                    "proficiency": example.proficiency,
                }
                for index, outcome in enumerate(OUTCOMES):
                    row[f"{outcome}_true"] = (
                        example.scores[index] if observed_values[index] else None
                    )
                    row[f"{outcome}_pred"] = predictions_values[index]
                    row[f"{outcome}_observed"] = observed_values[index]
                writers.add_utterance(row)
    if dev_guard is not None:
        dev_guard.complete()
    if not phone_targets or not utterance_targets:
        raise S104TrainingError("evaluation partition is empty")
    phone_slice_arrays: dict[
        str,
        dict[str, tuple[np.ndarray, np.ndarray]],
    ] = {}
    for dimension, phone_groups_by_value in phone_groups.items():
        phone_slice_arrays[dimension] = {
            group: (
                np.asarray(values[0], dtype=np.int8),
                np.asarray(values[1], dtype=np.float32),
            )
            for group, values in phone_groups_by_value.items()
        }
    utterance_slice_arrays: dict[
        str,
        dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    ] = {}
    for dimension, utterance_groups_by_value in utterance_groups.items():
        utterance_slice_arrays[dimension] = {
            group: (
                np.asarray(values[0], dtype=np.float32),
                np.asarray(values[1], dtype=np.float32),
                np.asarray(values[2], dtype=np.bool_),
            )
            for group, values in utterance_groups_by_value.items()
        }
    return PredictionBundle(
        phone_targets=np.asarray(phone_targets, dtype=np.int8),
        phone_probabilities=np.asarray(phone_probabilities, dtype=np.float32),
        utterance_targets=np.asarray(utterance_targets, dtype=np.float32),
        utterance_predictions=np.asarray(
            utterance_predictions,
            dtype=np.float32,
        ),
        utterance_observed=np.asarray(utterance_observed, dtype=np.bool_),
        phone_slices=phone_slice_arrays,
        utterance_slices=utterance_slice_arrays,
        utterance_rows=len(utterance_targets),
        phone_rows=len(phone_targets),
    )


def _is_better_selection(
    *,
    phone_f1: float,
    comprehensibility_icc: float | None,
    best_phone_f1: float,
    best_comprehensibility_icc: float | None,
) -> bool:
    tolerance = 1e-12
    if phone_f1 > best_phone_f1 + tolerance:
        return True
    if abs(phone_f1 - best_phone_f1) > tolerance:
        return False
    # Undefined secondary values can never improve a tie. An earlier epoch
    # remains selected for a complete tie because this returns False.
    if comprehensibility_icc is None:
        return False
    if best_comprehensibility_icc is None:
        return True
    return comprehensibility_icc > best_comprehensibility_icc + tolerance


def train_and_select(
    *,
    model: AdapterTunedWavLM,
    config: Mapping[str, Any],
    seed: int,
    device: torch.device,
    metadata: FitMetadata,
    fit_examples_factory: Any,
    validation_examples_factory: Any,
    do_normalize: bool,
    maximum_chunk_seconds: float,
    expected_validation_utterances: int,
    audio_integrity: AudioIntegrityVerifier,
) -> SelectionResult:
    optimization = cast(Mapping[str, Any], config["optimization"])
    groups = group_trainable_parameters(
        model,
        adapter_learning_rate=float(optimization["adapter_learning_rate"]),
        head_learning_rate=float(optimization["head_learning_rate"]),
    )
    optimizer_groups = [
        {
            "params": group["params"],
            "lr": group["lr"],
        }
        for group in groups
    ]
    optimizer = torch.optim.AdamW(
        optimizer_groups,
        weight_decay=float(optimization["weight_decay"]),
    )
    pos_weight = torch.tensor(
        metadata.pos_weight,
        dtype=torch.float32,
        device=device,
    )
    best_state: Mapping[str, torch.Tensor] | None = None
    best_epoch = 0
    best_threshold = float("nan")
    best_phone = -math.inf
    best_icc: float | None = None
    history: list[Mapping[str, Any]] = []
    epochs_without_improvement = 0
    stopped_early = False
    accumulation = int(optimization["gradient_accumulation_utterances"])
    thresholds = threshold_grid(config)
    maximum_epochs = int(optimization["maximum_epochs"])
    minimum_epochs = int(optimization["minimum_epochs"])
    patience = int(optimization["early_stopping_patience"])
    for epoch in range(1, maximum_epochs + 1):
        _set_train_mode(model)
        epoch_loss = 0.0
        epoch_utterances = 0
        optimizer_steps = 0
        for window in _example_windows(
            fit_examples_factory(epoch),
            accumulation,
        ):
            optimizer.zero_grad(set_to_none=True)
            divisor = len(window)
            for example in window:
                pooled = pool_online_example(
                    model=model,
                    example=example,
                    device=device,
                    do_normalize=do_normalize,
                    maximum_chunk_seconds=maximum_chunk_seconds,
                    use_bfloat16=True,
                    audio_integrity=audio_integrity,
                )
                loss, _ = _joint_loss(
                    model=model,
                    example=example,
                    pooled=pooled,
                    pos_weight=pos_weight,
                    phone_weight=float(optimization["phone_loss_weight"]),
                    utterance_weight=float(optimization["utterance_loss_weight"]),
                    device=device,
                )
                _backward_window_loss(
                    loss,
                    actual_utterances=divisor,
                )
                epoch_loss += float(loss.detach().cpu())
                epoch_utterances += 1
            torch.nn.utils.clip_grad_norm_(
                [
                    parameter
                    for parameter in model.parameters()
                    if parameter.requires_grad
                ],
                max_norm=float(optimization["gradient_clip_norm"]),
                error_if_nonfinite=True,
            )
            optimizer.step()
            _assert_trainable_parameters_finite(model)
            optimizer_steps += 1
        if epoch_utterances != metadata.fit_utterances:
            raise S104TrainingError("train-fit epoch cardinality changed")
        validation = evaluate_examples(
            model=model,
            examples=validation_examples_factory(),
            device=device,
            do_normalize=do_normalize,
            maximum_chunk_seconds=maximum_chunk_seconds,
            use_bfloat16=True,
            threshold=None,
            seed=seed,
            audio_integrity=audio_integrity,
            collect_slices=False,
        )
        if validation.utterance_rows != expected_validation_utterances:
            raise S104TrainingError(
                "inner-validation cardinality differs from execution lock"
            )
        selected_threshold, phone_f1 = select_phone_threshold(
            validation.phone_targets,
            validation.phone_probabilities,
            thresholds,
        )
        observed = validation.utterance_observed[:, 2]
        icc_value = (
            icc_2_1(
                np.column_stack(
                    (
                        validation.utterance_targets[observed, 2],
                        validation.utterance_predictions[observed, 2],
                    )
                )
            )
            if int(observed.sum()) >= 2
            else float("nan")
        )
        icc = float(icc_value) if math.isfinite(icc_value) else None
        improved = _is_better_selection(
            phone_f1=phone_f1,
            comprehensibility_icc=icc,
            best_phone_f1=best_phone,
            best_comprehensibility_icc=best_icc,
        )
        if improved:
            best_epoch = epoch
            best_threshold = selected_threshold
            best_phone = phone_f1
            best_icc = icc
            best_state = _trainable_state(model)
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        history.append(
            {
                "epoch": epoch,
                "train_mean_joint_loss": epoch_loss / epoch_utterances,
                "optimizer_steps": optimizer_steps,
                "inner_phone_macro_f1": phone_f1,
                "inner_phone_threshold": selected_threshold,
                "inner_comprehensibility_icc_2_1": icc,
                "inner_comprehensibility_icc_defined": icc is not None,
                "selected_as_best": improved,
            }
        )
        if epoch >= minimum_epochs and epochs_without_improvement >= patience:
            stopped_early = True
            break
    if best_state is None or best_epoch <= 0 or not math.isfinite(best_threshold):
        raise S104TrainingError("inner selection did not produce a checkpoint")
    _load_trainable_state(model, best_state)
    return SelectionResult(
        best_epoch=best_epoch,
        threshold=best_threshold,
        phone_macro_f1=best_phone,
        comprehensibility_icc_2_1=best_icc,
        stopped_early=stopped_early,
        history=tuple(history),
        trainable_state=best_state,
    )


def local_snapshot_paths(
    *,
    workspace_root: Path,
    selected_candidate: Mapping[str, Any],
) -> tuple[Path, Path, Path]:
    cache_dir = (workspace_root / str(selected_candidate["cache_dir"])).resolve(
        strict=True
    )
    repository = cache_dir / (
        "models--" + str(selected_candidate["model_id"]).replace("/", "--")
    )
    snapshot = (repository / "snapshots" / str(selected_candidate["revision"])).resolve(
        strict=True
    )
    config_path = snapshot / "config.json"
    checkpoint_path = snapshot / "pytorch_model.bin"
    if not config_path.is_file() or not checkpoint_path.is_file():
        raise S104TrainingError("selected local model snapshot is incomplete")
    return snapshot, config_path, checkpoint_path


def build_adapter_model(
    *,
    config: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
    metadata: FitMetadata,
    snapshot: Path,
    device: torch.device,
) -> tuple[AdapterTunedWavLM, Mapping[str, Any]]:
    wavlm = WavLMModel.from_pretrained(
        snapshot,
        local_files_only=True,
        use_safetensors=False,
    )
    wavlm.config.layerdrop = float(
        cast(Mapping[str, Any], config["runtime"])["encoder_layerdrop"]
    )
    wavlm.config.apply_spec_augment = bool(
        cast(Mapping[str, Any], config["runtime"])["apply_spec_augment"]
    )
    wavlm.encoder.config.layerdrop = wavlm.config.layerdrop
    wavlm.freeze_feature_encoder()
    wavlm.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    adapter = cast(Mapping[str, Any], config["adapter"])
    heads = cast(Mapping[str, Any], config["heads"])
    phone = cast(Mapping[str, Any], heads["phone"])
    utterance = cast(Mapping[str, Any], heads["utterance"])
    model = AdapterTunedWavLM(
        wavlm=wavlm,
        hidden_size=int(selected_candidate["hidden_size"]),
        encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
        adapter_bottleneck_dim=int(adapter["bottleneck_dim"]),
        adapter_dropout=float(adapter["dropout"]),
        zero_initialize_adapter_output=bool(adapter["zero_initialize_output"]),
        target_vocabulary_size=len(metadata.target_vocabulary),
        target_embedding_dim=int(phone["target_embedding_dim"]),
        phone_hidden_dim=int(phone["hidden_dim"]),
        phone_dropout=float(phone["dropout"]),
        utterance_hidden_dim=int(utterance["hidden_dim"]),
        utterance_dropout=float(utterance["dropout"]),
        score_range=cast(Sequence[float], utterance["score_range"]),
    ).to(device)
    audit = audit_trainable_parameters(
        model,
        allow_patterns=cast(
            Sequence[str],
            adapter["trainable_name_patterns"],
        ),
    )
    return model, audit


def save_adapter_checkpoint(
    path: Path,
    *,
    selection: SelectionResult,
    seed: int,
    selected_candidate: Mapping[str, Any],
    metadata: FitMetadata,
) -> Mapping[str, Any]:
    tensors = {
        name: value.detach().cpu().contiguous()
        for name, value in selection.trainable_state.items()
    }
    if not tensors or any(
        name.startswith("wavlm.") and ".adapter." not in name for name in tensors
    ):
        raise S104TrainingError("checkpoint contains a non-adapter backbone tensor")
    metadata_strings = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "seed": str(seed),
        "development_only": "true",
        "oracle_alignment": "true",
        "model_id": str(selected_candidate["model_id"]),
        "revision": str(selected_candidate["revision"]),
        "target_vocabulary_json": json.dumps(
            metadata.target_vocabulary,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "phone_decision_threshold": repr(selection.threshold),
        "best_epoch": str(selection.best_epoch),
        "full_backbone_tensor_count": "0",
    }
    save_safetensors(tensors, path, metadata=metadata_strings)
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    return {
        "tensor_count": len(tensors),
        "full_backbone_tensor_count": 0,
        "trainable_parameter_names": sorted(tensors),
        "tensor_shapes": {name: list(tensors[name].shape) for name in sorted(tensors)},
        "tensor_dtypes": {name: str(tensors[name].dtype) for name in sorted(tensors)},
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    multiplier = 1 if sys.platform == "darwin" else 1_024
    return int(value * multiplier)


def _device_metadata(device: torch.device) -> Mapping[str, Any]:
    if device.type != "cuda":
        raise S104TrainingError("production S104 training requires CUDA")
    index = device.index if device.index is not None else torch.cuda.current_device()
    properties = torch.cuda.get_device_properties(index)
    return {
        "type": "cuda",
        "index": index,
        "name": properties.name,
        "compute_capability": f"{properties.major}.{properties.minor}",
        "total_memory_bytes": int(properties.total_memory),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "mixed_precision": "bfloat16",
    }


def _aggregate(values: Sequence[float | None]) -> Mapping[str, Any]:
    finite = np.asarray(
        [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value))
        ],
        dtype=np.float64,
    )
    return {
        "mean": float(finite.mean()) if finite.size else None,
        "sample_std": (float(finite.std(ddof=1)) if finite.size > 1 else None),
        "valid_seeds": int(finite.size),
    }


def _artifact_layout() -> tuple[tuple[str, str], ...]:
    output: list[tuple[str, str]] = []
    for seed in SEEDS:
        output.extend(
            [
                (
                    f"seed_{seed}_adapter_and_heads_checkpoint",
                    f"seed_{seed}/adapter_and_heads.safetensors",
                ),
                (
                    f"seed_{seed}_phone_dev_predictions",
                    f"seed_{seed}/phone_dev_predictions.parquet",
                ),
                (
                    f"seed_{seed}_utterance_dev_predictions",
                    f"seed_{seed}/utterance_dev_predictions.parquet",
                ),
                (
                    f"seed_{seed}_dev_slice_metrics",
                    f"seed_{seed}/dev_slice_metrics.json",
                ),
                (
                    f"seed_{seed}_resource_and_trainable_parameter_report",
                    (f"seed_{seed}/resource_and_trainable_parameter_report.json"),
                ),
            ]
        )
    output.append(("experiment_report", "experiment_report.json"))
    return tuple(output)


def _expected_trainable_tensor_shapes(
    *,
    selected_candidate: Mapping[str, Any],
    adapter: Mapping[str, Any],
    heads: Mapping[str, Any],
    target_vocabulary_size: int,
) -> Mapping[str, tuple[int, ...]]:
    hidden = int(selected_candidate["hidden_size"])
    layer_count = int(selected_candidate["encoder_layer_count"])
    bottleneck = int(adapter["bottleneck_dim"])
    phone = cast(Mapping[str, Any], heads["phone"])
    utterance = cast(Mapping[str, Any], heads["utterance"])
    target_embedding = int(phone["target_embedding_dim"])
    phone_hidden = int(phone["hidden_dim"])
    utterance_hidden = int(utterance["hidden_dim"])
    shapes: dict[str, tuple[int, ...]] = {}
    for layer in range(layer_count):
        prefix = f"wavlm.encoder.layers.{layer}.adapter"
        shapes.update(
            {
                f"{prefix}.layer_norm.weight": (hidden,),
                f"{prefix}.layer_norm.bias": (hidden,),
                f"{prefix}.down.weight": (bottleneck, hidden),
                f"{prefix}.down.bias": (bottleneck,),
                f"{prefix}.up.weight": (hidden, bottleneck),
                f"{prefix}.up.bias": (hidden,),
            }
        )
    shapes.update(
        {
            "phone_head.target_embedding.weight": (
                target_vocabulary_size,
                target_embedding,
            ),
            "phone_head.network.0.weight": (hidden,),
            "phone_head.network.0.bias": (hidden,),
            "phone_head.network.1.weight": (phone_hidden, hidden),
            "phone_head.network.1.bias": (phone_hidden,),
            "phone_head.classifier.1.weight": (
                phone_hidden,
                phone_hidden + target_embedding,
            ),
            "phone_head.classifier.1.bias": (phone_hidden,),
            "phone_head.classifier.4.weight": (1, phone_hidden),
            "phone_head.classifier.4.bias": (1,),
            "utterance_head.network.0.weight": (hidden * 2,),
            "utterance_head.network.0.bias": (hidden * 2,),
            "utterance_head.network.1.weight": (
                utterance_hidden,
                hidden * 2,
            ),
            "utterance_head.network.1.bias": (utterance_hidden,),
            "utterance_head.network.4.weight": (len(OUTCOMES), utterance_hidden),
            "utterance_head.network.4.bias": (len(OUTCOMES),),
        }
    )
    return shapes


def _validate_prediction_parquet(
    path: Path,
    *,
    expected_schema: pa.Schema,
    seed: int,
    kind: str,
    threshold: float | None = None,
    allowed_l1: set[str] | None = None,
    allowed_gender: set[str] | None = None,
) -> int:
    parquet = pq.ParquetFile(path)
    if not parquet.schema_arrow.equals(expected_schema, check_metadata=True):
        raise S104TrainingError(f"{kind} prediction schema differs")
    if parquet.metadata.num_rows <= 0:
        raise S104TrainingError(f"{kind} prediction file is empty")
    if kind == "phone":
        if threshold is None or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise S104TrainingError("phone prediction threshold is invalid")
        columns = [
            "schema_version",
            "seed",
            "split",
            "development_only",
            "oracle_alignment",
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "target_label",
            "l1",
            "gender",
            "binary_error_label",
            "error_probability",
            "error_prediction",
        ]
    elif kind == "utterance":
        columns = [
            "schema_version",
            "seed",
            "split",
            "development_only",
            "oracle_alignment",
            "utterance_id_sha256",
            "speaker_id_sha256",
            "l1",
            "gender",
            *[
                field
                for outcome in OUTCOMES
                for field in (
                    f"{outcome}_true",
                    f"{outcome}_pred",
                    f"{outcome}_observed",
                )
            ],
        ]
    else:
        raise S104TrainingError("prediction kind is invalid")
    rows = 0
    for batch in parquet.iter_batches(batch_size=8_192, columns=columns):
        for row in batch.to_pylist():
            rows += 1
            if row["seed"] != seed or row["split"] != "dev":
                raise S104TrainingError(f"{kind} prediction seed/split differs")
            expected_version = (
                PHONE_PREDICTION_SCHEMA_VERSION
                if kind == "phone"
                else UTTERANCE_PREDICTION_SCHEMA_VERSION
            )
            if (
                row["schema_version"] != expected_version
                or row["development_only"] is not True
                or row["oracle_alignment"] is not True
                or not _is_sha256(row["utterance_id_sha256"])
                or not _is_sha256(row["speaker_id_sha256"])
                or (allowed_l1 is not None and row["l1"] not in allowed_l1)
                or (allowed_gender is not None and row["gender"] not in allowed_gender)
            ):
                raise S104TrainingError(f"{kind} prediction constants differ")
            if kind == "phone":
                probability = row["error_probability"]
                if (
                    isinstance(row["phone_index"], bool)
                    or not isinstance(row["phone_index"], int)
                    or row["phone_index"] < 0
                    or not isinstance(row["target_label"], str)
                    or not row["target_label"]
                    or row["binary_error_label"] not in (0, 1)
                    or row["error_prediction"] not in (0, 1)
                    or isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not math.isfinite(float(probability))
                    or not 0 <= float(probability) <= 1
                    or row["error_prediction"]
                    != int(float(probability) >= cast(float, threshold))
                ):
                    raise S104TrainingError("phone prediction value is invalid")
            else:
                for outcome in OUTCOMES:
                    observed = row[f"{outcome}_observed"]
                    target = row[f"{outcome}_true"]
                    prediction = row[f"{outcome}_pred"]
                    if (
                        not isinstance(observed, bool)
                        or isinstance(prediction, bool)
                        or not isinstance(prediction, (int, float))
                        or not math.isfinite(float(prediction))
                        or not 1 <= float(prediction) <= 5
                    ):
                        raise S104TrainingError("utterance prediction value is invalid")
                    if observed:
                        if (
                            isinstance(target, bool)
                            or not isinstance(target, (int, float))
                            or float(target) not in (1.0, 2.0, 3.0, 4.0, 5.0)
                        ):
                            raise S104TrainingError("utterance target value is invalid")
                    elif target is not None:
                        raise S104TrainingError(
                            "unobserved utterance target is not null"
                        )
    if rows != parquet.metadata.num_rows:
        raise S104TrainingError(f"{kind} prediction row count differs")
    return rows


def _validate_prediction_source_binding(
    *,
    phone_path: Path,
    utterance_path: Path,
    expected: Mapping[str, ExpectedDevUtterance],
) -> None:
    utterance_seen: set[str] = set()
    utterance_columns = [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "l1",
        "gender",
        "proficiency",
        *[
            field
            for outcome in OUTCOMES
            for field in (
                f"{outcome}_true",
                f"{outcome}_observed",
            )
        ],
    ]
    for batch in pq.ParquetFile(utterance_path).iter_batches(
        batch_size=8_192,
        columns=utterance_columns,
    ):
        for row in batch.to_pylist():
            identifier = row["utterance_id_sha256"]
            truth = expected.get(identifier)
            if (
                truth is None
                or identifier in utterance_seen
                or row["speaker_id_sha256"] != truth.speaker_id_sha256
                or row["l1"] != truth.l1
                or row["gender"] != truth.gender
                or row["proficiency"] != truth.proficiency
            ):
                raise S104TrainingError("utterance prediction source identity differs")
            utterance_seen.add(identifier)
            for index, outcome in enumerate(OUTCOMES):
                target = truth.scores[index]
                expected_observed = math.isfinite(target)
                if (
                    row[f"{outcome}_observed"] is not expected_observed
                    or (expected_observed and float(row[f"{outcome}_true"]) != target)
                    or (not expected_observed and row[f"{outcome}_true"] is not None)
                ):
                    raise S104TrainingError("utterance prediction ground truth differs")
    if utterance_seen != set(expected):
        raise S104TrainingError("utterance prediction identity set differs")

    phone_seen: set[tuple[str, int]] = set()
    phone_columns = [
        "utterance_id_sha256",
        "speaker_id_sha256",
        "phone_index",
        "target_label",
        "binary_error_label",
        "l1",
        "gender",
        "proficiency",
    ]
    for batch in pq.ParquetFile(phone_path).iter_batches(
        batch_size=8_192,
        columns=phone_columns,
    ):
        for row in batch.to_pylist():
            identifier = row["utterance_id_sha256"]
            truth = expected.get(identifier)
            phone_index = row["phone_index"]
            key = (identifier, phone_index)
            if (
                truth is None
                or isinstance(phone_index, bool)
                or not isinstance(phone_index, int)
                or phone_index < 0
                or phone_index >= len(truth.phones)
                or key in phone_seen
                or row["speaker_id_sha256"] != truth.speaker_id_sha256
                or row["l1"] != truth.l1
                or row["gender"] != truth.gender
                or row["proficiency"] != truth.proficiency
                or (
                    row["target_label"],
                    row["binary_error_label"],
                )
                != truth.phones[phone_index]
            ):
                raise S104TrainingError("phone prediction ground truth differs")
            phone_seen.add(key)
    expected_phone_keys = {
        (identifier, index)
        for identifier, truth in expected.items()
        for index in range(len(truth.phones))
    }
    if phone_seen != expected_phone_keys:
        raise S104TrainingError("phone prediction identity set differs")


def validate_generated_artifact_tree(
    temporary: Path,
    *,
    allowed_l1: set[str] | None = None,
    allowed_gender: set[str] | None = None,
    expected_fit_utterances: int | None = None,
    expected_dev_utterances: int | None = None,
    engineering_phone_threshold: float | None = None,
    engineering_icc_threshold: float | None = None,
    selected_candidate: Mapping[str, Any] | None = None,
    adapter_config: Mapping[str, Any] | None = None,
    heads_config: Mapping[str, Any] | None = None,
    expected_dev_truth: Mapping[str, ExpectedDevUtterance] | None = None,
    expected_fit_metadata: FitMetadata | None = None,
) -> Mapping[int, Mapping[str, Any]]:
    """Re-open every generated file before publication."""

    _artifact_records(temporary)
    checkpoint_thresholds: dict[int, float] = {}
    checkpoint_best_epochs: dict[int, int] = {}
    derived_metrics: dict[int, Mapping[str, Any]] = {}
    for seed in SEEDS:
        seed_directory = temporary / f"seed_{seed}"
        checkpoint = seed_directory / "adapter_and_heads.safetensors"
        with safe_open(checkpoint, framework="pt") as handle:
            keys = tuple(handle.keys())
            metadata = handle.metadata()
            tensors = {key: handle.get_tensor(key) for key in keys}
        try:
            threshold = float(
                cast(Mapping[str, str], metadata)["phone_decision_threshold"]
            )
            best_epoch = int(cast(Mapping[str, str], metadata)["best_epoch"])
            checkpoint_vocabulary = json.loads(
                cast(Mapping[str, str], metadata)["target_vocabulary_json"]
            )
        except (KeyError, TypeError, ValueError) as error:
            raise S104TrainingError(
                "adapter checkpoint threshold is invalid"
            ) from error
        if (
            not keys
            or metadata is None
            or set(metadata)
            != {
                "schema_version",
                "seed",
                "development_only",
                "oracle_alignment",
                "model_id",
                "revision",
                "target_vocabulary_json",
                "phone_decision_threshold",
                "best_epoch",
                "full_backbone_tensor_count",
            }
            or metadata.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or metadata.get("seed") != str(seed)
            or metadata.get("full_backbone_tensor_count") != "0"
            or metadata.get("development_only") != "true"
            or metadata.get("oracle_alignment") != "true"
            or any(key.startswith("wavlm.") and ".adapter." not in key for key in keys)
            or not any(".adapter." in key for key in keys)
            or not any(key.startswith("phone_head.") for key in keys)
            or not any(key.startswith("utterance_head.") for key in keys)
            or not math.isfinite(threshold)
            or not 0 <= threshold <= 1
            or best_epoch <= 0
            or not isinstance(checkpoint_vocabulary, list)
        ):
            raise S104TrainingError("adapter checkpoint contract differs")
        if any(
            tensor.numel() <= 0
            or not tensor.is_floating_point()
            or not bool(torch.isfinite(tensor).all())
            for tensor in tensors.values()
        ):
            raise S104TrainingError("adapter checkpoint tensor is invalid")
        checkpoint_thresholds[seed] = threshold
        checkpoint_best_epochs[seed] = best_epoch
        phone_path = seed_directory / "phone_dev_predictions.parquet"
        utterance_path = seed_directory / "utterance_dev_predictions.parquet"
        phone_rows = _validate_prediction_parquet(
            phone_path,
            expected_schema=_phone_prediction_schema(),
            seed=seed,
            kind="phone",
            threshold=threshold,
            allowed_l1=allowed_l1,
            allowed_gender=allowed_gender,
        )
        utterance_rows = _validate_prediction_parquet(
            utterance_path,
            expected_schema=_utterance_prediction_schema(),
            seed=seed,
            kind="utterance",
            allowed_l1=allowed_l1,
            allowed_gender=allowed_gender,
        )
        if expected_dev_truth is not None:
            _validate_prediction_source_binding(
                phone_path=phone_path,
                utterance_path=utterance_path,
                expected=expected_dev_truth,
            )
        slices = load_json_strict(
            seed_directory / "dev_slice_metrics.json",
            "S104 dev slice metrics",
        )
        if (
            set(slices)
            != {
                "schema_version",
                "seed",
                "development_only",
                "dev_inference_passes",
                "metrics",
            }
            or slices.get("schema_version") != REPORT_SCHEMA_VERSION
            or slices.get("seed") != seed
            or slices.get("development_only") is not True
            or slices.get("dev_inference_passes") != 1
        ):
            raise S104TrainingError("dev slice metric contract differs")
        expected_slices = slice_metrics_from_prediction_files(
            phone_path=phone_path,
            utterance_path=utterance_path,
            threshold=threshold,
        )
        if slices.get("metrics") != _safe_json(expected_slices):
            raise S104TrainingError("dev slice metrics differ from predictions")
        resources = load_json_strict(
            seed_directory / "resource_and_trainable_parameter_report.json",
            "S104 resource report",
        )
        audit_value = resources.get("trainable_parameter_audit")
        checkpoint_value = resources.get("checkpoint")
        if not isinstance(audit_value, Mapping) or not isinstance(
            checkpoint_value,
            Mapping,
        ):
            raise S104TrainingError("resource/trainable report mappings are absent")
        audit = cast(Mapping[str, Any], audit_value)
        checkpoint_record = cast(Mapping[str, Any], checkpoint_value)
        audit_names = audit.get("trainable_parameter_names")
        checkpoint_names = checkpoint_record.get("trainable_parameter_names")
        expected_audit_keys = {
            "total_parameter_count",
            "trainable_parameter_count",
            "frozen_parameter_count",
            "trainable_fraction",
            "trainable_tensor_count",
            "trainable_parameter_names",
            "allow_patterns",
            "backbone_non_adapter_trainable_count",
            "full_fine_tuning",
        }
        tensor_shapes = {name: list(tensors[name].shape) for name in sorted(tensors)}
        tensor_dtypes = {name: str(tensors[name].dtype) for name in sorted(tensors)}
        trainable_parameter_count = sum(
            int(tensor.numel()) for tensor in tensors.values()
        )
        total_parameter_count = audit.get("total_parameter_count")
        frozen_parameter_count = audit.get("frozen_parameter_count")
        trainable_fraction = audit.get("trainable_fraction")
        fit_metadata_value = resources.get("fit_metadata")
        if not isinstance(fit_metadata_value, Mapping):
            raise S104TrainingError("fit metadata is absent")
        fit_metadata = cast(Mapping[str, Any], fit_metadata_value)
        vocabulary = fit_metadata.get("target_vocabulary")
        positive_rows = fit_metadata.get("positive_phone_rows")
        negative_rows = fit_metadata.get("negative_phone_rows")
        fit_utterances = fit_metadata.get("fit_utterances")
        pos_weight = fit_metadata.get("pos_weight")
        numeric_resource_fields = (
            "peak_process_rss_bytes",
            "peak_cuda_allocated_bytes",
            "peak_cuda_reserved_bytes",
        )
        expected_resource_keys = {
            "schema_version",
            "seed",
            "trainable_parameter_audit",
            "checkpoint",
            "fit_metadata",
            "peak_process_rss_bytes",
            "peak_cuda_allocated_bytes",
            "peak_cuda_reserved_bytes",
            "automatic_oom_retry_count",
        }
        expected_checkpoint_record_keys = {
            "tensor_count",
            "full_backbone_tensor_count",
            "trainable_parameter_names",
            "tensor_shapes",
            "tensor_dtypes",
            "sha256",
            "size_bytes",
        }
        expected_fit_metadata_keys = {
            "target_vocabulary",
            "positive_phone_rows",
            "negative_phone_rows",
            "pos_weight",
            "fit_utterances",
        }
        if (
            set(resources) != expected_resource_keys
            or set(checkpoint_record) != expected_checkpoint_record_keys
            or set(fit_metadata) != expected_fit_metadata_keys
            or resources.get("schema_version") != REPORT_SCHEMA_VERSION
            or resources.get("seed") != seed
            or set(audit) != expected_audit_keys
            or audit.get("backbone_non_adapter_trainable_count") != 0
            or audit.get("full_fine_tuning") is not False
            or audit.get("trainable_tensor_count") != len(keys)
            or audit.get("trainable_parameter_count") != trainable_parameter_count
            or isinstance(total_parameter_count, bool)
            or not isinstance(total_parameter_count, int)
            or isinstance(frozen_parameter_count, bool)
            or not isinstance(frozen_parameter_count, int)
            or frozen_parameter_count <= 0
            or total_parameter_count
            != trainable_parameter_count + frozen_parameter_count
            or isinstance(trainable_fraction, bool)
            or not isinstance(trainable_fraction, (int, float))
            or not math.isfinite(float(trainable_fraction))
            or float(trainable_fraction)
            != trainable_parameter_count / total_parameter_count
            or checkpoint_record.get("full_backbone_tensor_count") != 0
            or checkpoint_record.get("tensor_count") != len(keys)
            or checkpoint_record.get("size_bytes") != checkpoint.stat().st_size
            or checkpoint_record.get("sha256") != sha256_file(checkpoint)
            or not isinstance(audit_names, list)
            or not isinstance(checkpoint_names, list)
            or not all(isinstance(name, str) for name in audit_names)
            or not all(isinstance(name, str) for name in checkpoint_names)
            or sorted(audit_names) != list(keys)
            or checkpoint_names != list(keys)
            or checkpoint_record.get("tensor_shapes") != tensor_shapes
            or checkpoint_record.get("tensor_dtypes") != tensor_dtypes
            or not isinstance(vocabulary, list)
            or not vocabulary
            or vocabulary[0] != UNKNOWN_TARGET
            or not all(isinstance(item, str) and item for item in vocabulary)
            or len(set(vocabulary)) != len(vocabulary)
            or checkpoint_vocabulary != vocabulary
            or isinstance(positive_rows, bool)
            or not isinstance(positive_rows, int)
            or positive_rows <= 0
            or isinstance(negative_rows, bool)
            or not isinstance(negative_rows, int)
            or negative_rows <= 0
            or isinstance(fit_utterances, bool)
            or not isinstance(fit_utterances, int)
            or fit_utterances <= 0
            or (
                expected_fit_utterances is not None
                and fit_utterances != expected_fit_utterances
            )
            or isinstance(pos_weight, bool)
            or not isinstance(pos_weight, (int, float))
            or not math.isfinite(float(pos_weight))
            or float(pos_weight) != negative_rows / positive_rows
            or resources.get("automatic_oom_retry_count") != 0
            or any(
                isinstance(resources.get(field), bool)
                or not isinstance(resources.get(field), int)
                or int(cast(int, resources.get(field))) < 1
                for field in numeric_resource_fields
            )
            or int(resources["peak_cuda_reserved_bytes"])
            < int(resources["peak_cuda_allocated_bytes"])
        ):
            raise S104TrainingError("resource/trainable report contract differs")
        if expected_fit_metadata is not None and (
            vocabulary != list(expected_fit_metadata.target_vocabulary)
            or positive_rows != expected_fit_metadata.positive_phone_rows
            or negative_rows != expected_fit_metadata.negative_phone_rows
            or float(pos_weight) != expected_fit_metadata.pos_weight
            or fit_utterances != expected_fit_metadata.fit_utterances
        ):
            raise S104TrainingError("fit metadata source binding differs")
        if (
            selected_candidate is not None
            and adapter_config is not None
            and heads_config is not None
        ):
            expected_shapes = _expected_trainable_tensor_shapes(
                selected_candidate=selected_candidate,
                adapter=adapter_config,
                heads=heads_config,
                target_vocabulary_size=len(vocabulary),
            )
            if (
                set(keys) != set(expected_shapes)
                or tensor_shapes
                != {
                    name: list(shape) for name, shape in sorted(expected_shapes.items())
                }
                or any(dtype != "torch.float32" for dtype in tensor_dtypes.values())
                or metadata.get("model_id") != str(selected_candidate["model_id"])
                or metadata.get("revision") != str(selected_candidate["revision"])
                or audit.get("allow_patterns")
                != list(adapter_config["trainable_name_patterns"])
            ):
                raise S104TrainingError(
                    "checkpoint canonical trainable allowlist differs"
                )
        if expected_dev_utterances is not None and (
            utterance_rows != expected_dev_utterances
        ):
            raise S104TrainingError("dev prediction cardinality differs")
        metrics = metric_bundle_from_prediction_files(
            phone_path=phone_path,
            utterance_path=utterance_path,
            threshold=threshold,
        )
        if (
            cast(Mapping[str, Any], metrics["phone"])["rows"] != phone_rows
            or cast(Mapping[str, Any], metrics["utterance"])["rows"] != utterance_rows
        ):
            raise S104TrainingError("prediction-derived metric rows differ")
        derived_metrics[seed] = cast(Mapping[str, Any], _safe_json(metrics))
    report = load_json_strict(
        temporary / "experiment_report.json",
        "S104 experiment report",
    )
    runs = report.get("runs")
    strict_report_schema = (
        selected_candidate is not None
        and adapter_config is not None
        and heads_config is not None
        and expected_dev_truth is not None
        and expected_fit_metadata is not None
        and engineering_phone_threshold is not None
        and engineering_icc_threshold is not None
    )
    expected_report_keys = {
        "schema_version",
        "status",
        "started_at_utc",
        "completed_at_utc",
        "experiment_id",
        "development_only",
        "oracle_alignment",
        "silver_label",
        "promotion_decision_allowed",
        "selected_candidate",
        "s103_branch_evidence",
        "sources",
        "selection_contract",
        "leakage",
        "runtime",
        "runs",
        "summary",
    }
    if (
        (strict_report_schema and set(report) != expected_report_keys)
        or report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("status") != "completed"
        or not isinstance(runs, list)
        or [run.get("seed") for run in runs if isinstance(run, Mapping)] != list(SEEDS)
        or len(runs) != len(SEEDS)
    ):
        raise S104TrainingError("aggregate experiment report contract differs")
    for run in runs:
        if not isinstance(run, Mapping):
            raise S104TrainingError("aggregate experiment run is invalid")
        run_seed_value = run.get("seed")
        selection = run.get("selection")
        dev = run.get("dev")
        if strict_report_schema and (
            set(run) != {"seed", "selection", "dev"}
            or not isinstance(selection, Mapping)
            or set(selection)
            != {
                "best_epoch",
                "decision_threshold",
                "inner_phone_macro_f1",
                "inner_comprehensibility_icc_2_1",
                "secondary_metric_defined",
                "undefined_secondary_failure_reason",
                "stopped_early",
                "history",
                "refit_performed",
            }
            or not isinstance(dev, Mapping)
            or set(dev)
            != {
                "inference_passes",
                "used_for_selection",
                "metrics",
                "engineering_targets",
                "word_metric_status",
            }
        ):
            raise S104TrainingError("aggregate experiment run schema differs")
        if (
            isinstance(run_seed_value, bool)
            or not isinstance(run_seed_value, int)
            or run_seed_value not in SEEDS
            or not isinstance(selection, Mapping)
            or not isinstance(dev, Mapping)
            or selection.get("decision_threshold")
            != checkpoint_thresholds[run_seed_value]
            or selection.get("best_epoch") != checkpoint_best_epochs[run_seed_value]
            or selection.get("refit_performed") is not False
            or dev.get("inference_passes") != 1
            or dev.get("used_for_selection") is not False
            or dev.get("metrics") != derived_metrics[run_seed_value]
            or dev.get("word_metric_status") != "not_evaluable"
        ):
            raise S104TrainingError("aggregate experiment run contract differs")
        targets = dev.get("engineering_targets")
        if strict_report_schema and (
            not isinstance(targets, Mapping)
            or set(targets)
            != {
                "phone_macro_f1_threshold",
                "primary_utterance_icc_2_1_threshold",
                "phone_passed",
                "primary_utterance_passed",
                "development_only_not_promotion",
            }
        ):
            raise S104TrainingError("engineering target schema differs")
        phone_metric = cast(
            Mapping[str, Any],
            derived_metrics[run_seed_value]["phone"],
        )["macro_f1"]
        primary_icc = cast(
            Mapping[str, Any],
            cast(
                Mapping[str, Any],
                derived_metrics[run_seed_value]["utterance"],
            )["outcomes"],
        )["comprehensibility_score"]["icc_2_1"]
        if (
            not isinstance(targets, Mapping)
            or targets.get("development_only_not_promotion") is not True
            or (
                engineering_phone_threshold is not None
                and (
                    targets.get("phone_macro_f1_threshold")
                    != engineering_phone_threshold
                    or targets.get("phone_passed")
                    is not (float(phone_metric) >= engineering_phone_threshold)
                )
            )
            or (
                engineering_icc_threshold is not None
                and (
                    targets.get("primary_utterance_icc_2_1_threshold")
                    != engineering_icc_threshold
                    or targets.get("primary_utterance_passed")
                    is not (
                        primary_icc is not None
                        and float(primary_icc) >= engineering_icc_threshold
                    )
                )
            )
        ):
            raise S104TrainingError("engineering target report differs")
        if strict_report_schema:
            history = selection.get("history")
            if not isinstance(history, list) or not history:
                raise S104TrainingError("selection history is absent")
            selected_history: list[Mapping[str, Any]] = []
            for expected_epoch, history_value in enumerate(
                history,
                start=1,
            ):
                if not isinstance(history_value, Mapping):
                    raise S104TrainingError("selection history row is invalid")
                history_row = cast(Mapping[str, Any], history_value)
                history_icc = history_row.get("inner_comprehensibility_icc_2_1")
                if (
                    set(history_row)
                    != {
                        "epoch",
                        "train_mean_joint_loss",
                        "optimizer_steps",
                        "inner_phone_macro_f1",
                        "inner_phone_threshold",
                        "inner_comprehensibility_icc_2_1",
                        "inner_comprehensibility_icc_defined",
                        "selected_as_best",
                    }
                    or history_row.get("epoch") != expected_epoch
                    or isinstance(
                        history_row.get("train_mean_joint_loss"),
                        bool,
                    )
                    or not isinstance(
                        history_row.get("train_mean_joint_loss"),
                        (int, float),
                    )
                    or not math.isfinite(float(history_row["train_mean_joint_loss"]))
                    or float(history_row["train_mean_joint_loss"]) < 0
                    or isinstance(history_row.get("optimizer_steps"), bool)
                    or not isinstance(
                        history_row.get("optimizer_steps"),
                        int,
                    )
                    or int(history_row["optimizer_steps"]) <= 0
                    or isinstance(
                        history_row.get("inner_phone_macro_f1"),
                        bool,
                    )
                    or not isinstance(
                        history_row.get("inner_phone_macro_f1"),
                        (int, float),
                    )
                    or not math.isfinite(float(history_row["inner_phone_macro_f1"]))
                    or not 0 <= float(history_row["inner_phone_macro_f1"]) <= 1
                    or isinstance(
                        history_row.get("inner_phone_threshold"),
                        bool,
                    )
                    or not isinstance(
                        history_row.get("inner_phone_threshold"),
                        (int, float),
                    )
                    or not 0 <= float(history_row["inner_phone_threshold"]) <= 1
                    or not isinstance(
                        history_row.get("inner_comprehensibility_icc_defined"),
                        bool,
                    )
                    or (
                        history_row["inner_comprehensibility_icc_defined"]
                        is not (history_icc is not None)
                    )
                    or (
                        history_icc is not None
                        and (
                            isinstance(history_icc, bool)
                            or not isinstance(history_icc, (int, float))
                            or not math.isfinite(float(history_icc))
                        )
                    )
                    or not isinstance(
                        history_row.get("selected_as_best"),
                        bool,
                    )
                ):
                    raise S104TrainingError("selection history row contract differs")
                if history_row["selected_as_best"]:
                    selected_history.append(history_row)
            best_epoch = selection["best_epoch"]
            best_rows = [row for row in selected_history if row["epoch"] == best_epoch]
            inner_icc = selection["inner_comprehensibility_icc_2_1"]
            if (
                isinstance(best_epoch, bool)
                or not isinstance(best_epoch, int)
                or len(best_rows) != 1
                or selected_history[-1]["epoch"] != best_epoch
                or best_rows[0]["inner_phone_threshold"]
                != selection["decision_threshold"]
                or best_rows[0]["inner_phone_macro_f1"]
                != selection["inner_phone_macro_f1"]
                or best_rows[0]["inner_comprehensibility_icc_2_1"] != inner_icc
                or selection.get("secondary_metric_defined")
                is not (inner_icc is not None)
                or selection.get("undefined_secondary_failure_reason")
                != (
                    None
                    if inner_icc is not None
                    else "icc_2_1_undefined_no_improvement"
                )
                or not isinstance(selection.get("stopped_early"), bool)
            ):
                raise S104TrainingError("selection history best-row binding differs")
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        raise S104TrainingError("aggregate experiment summary is absent")
    if strict_report_schema and set(summary) != {
        "seeds_completed",
        "phone_macro_f1",
        "comprehensibility_icc_2_1",
        "engineering_targets",
        "word_metric_status",
        "dev_inference_passes_per_seed",
        "dev_used_for_selection",
        "locked_test_remains_unread",
        "stack_remains_unread",
        "calibration_remains_unread",
    }:
        raise S104TrainingError("aggregate experiment summary schema differs")
    phone_summary = _aggregate(
        [
            float(cast(Mapping[str, Any], derived_metrics[seed]["phone"])["macro_f1"])
            for seed in SEEDS
        ]
    )
    icc_values = [
        cast(
            float | None,
            cast(
                Mapping[str, Any],
                cast(
                    Mapping[str, Any],
                    derived_metrics[seed]["utterance"],
                )["outcomes"],
            )["comprehensibility_score"]["icc_2_1"],
        )
        for seed in SEEDS
    ]
    icc_summary = _aggregate(icc_values)
    if (
        summary.get("seeds_completed") != list(SEEDS)
        or summary.get("phone_macro_f1") != phone_summary
        or summary.get("comprehensibility_icc_2_1") != icc_summary
        or summary.get("word_metric_status") != "not_evaluable"
        or summary.get("dev_inference_passes_per_seed") != 1
        or summary.get("dev_used_for_selection") is not False
        or summary.get("locked_test_remains_unread") is not True
        or summary.get("stack_remains_unread") is not True
        or summary.get("calibration_remains_unread") is not True
    ):
        raise S104TrainingError("aggregate experiment summary differs")
    summary_targets = summary.get("engineering_targets")
    if not isinstance(summary_targets, Mapping):
        raise S104TrainingError("summary engineering targets are absent")
    if strict_report_schema and set(summary_targets) != {
        "phone_macro_f1_threshold",
        "primary_utterance_icc_2_1_threshold",
        "mean_phone_passed",
        "mean_primary_utterance_passed",
        "development_only_not_promotion",
    }:
        raise S104TrainingError("summary engineering target schema differs")
    if (
        summary_targets.get("development_only_not_promotion") is not True
        or (
            engineering_phone_threshold is not None
            and (
                summary_targets.get("phone_macro_f1_threshold")
                != engineering_phone_threshold
                or summary_targets.get("mean_phone_passed")
                is not (
                    phone_summary["mean"] is not None
                    and float(phone_summary["mean"]) >= engineering_phone_threshold
                )
            )
        )
        or (
            engineering_icc_threshold is not None
            and (
                summary_targets.get("primary_utterance_icc_2_1_threshold")
                != engineering_icc_threshold
                or summary_targets.get("mean_primary_utterance_passed")
                is not (
                    icc_summary["mean"] is not None
                    and float(icc_summary["mean"]) >= engineering_icc_threshold
                )
            )
        )
    ):
        raise S104TrainingError("summary engineering target report differs")
    return derived_metrics


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _artifact_records(
    temporary: Path,
) -> tuple[Mapping[str, Any], ...]:
    expected = _artifact_layout()
    expected_relative = {relative for _, relative in expected}
    expected_directories = {f"seed_{seed}" for seed in SEEDS}
    actual_relative: set[str] = set()
    actual_directories: set[str] = set()
    for path in temporary.rglob("*"):
        if path.is_symlink():
            raise S104TrainingError("artifact generation contains a symlink")
        if path.is_file():
            actual_relative.add(path.relative_to(temporary).as_posix())
        elif path.is_dir():
            actual_directories.add(path.relative_to(temporary).as_posix())
        else:
            raise S104TrainingError("artifact generation node type differs")
    if (
        actual_relative != expected_relative
        or actual_directories != expected_directories
    ):
        raise S104TrainingError("artifact generation tree differs")
    records: list[Mapping[str, Any]] = []
    for name, relative in expected:
        path = temporary / relative
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return tuple(records)


def _generation_id(
    records: Sequence[Mapping[str, Any]],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "schema_version": COMMIT_SCHEMA_VERSION,
                "artifacts": list(records),
            },
            pretty=False,
        )
    ).hexdigest()


def publish_content_addressed_generation(
    *,
    temporary: Path,
    generation_root: Path,
) -> PublishedGeneration:
    """Atomically expose one exact 16-file content-addressed generation."""

    records = _artifact_records(temporary)
    generation_id = _generation_id(records)
    generation_root.mkdir(parents=True, exist_ok=True)
    destination = generation_root / generation_id
    created = False
    for record in records:
        path = temporary / str(record["path"])
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    directories = sorted(
        [path for path in temporary.rglob("*") if path.is_dir()],
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for directory in directories:
        _fsync_directory(directory)
    _fsync_directory(temporary)
    if destination.exists():
        if not destination.is_dir() or _artifact_records(destination) != records:
            raise S104TrainingError(
                "content-addressed generation already exists with other bytes"
            )
        shutil.rmtree(temporary)
    else:
        os.replace(temporary, destination)
        created = True
    # A post-rename fsync exception preserves the complete generation. The
    # commit pointer remains absent, so consumers still cannot treat it as
    # visible completed evidence.
    publication = PublishedGeneration(
        generation_id=generation_id,
        generation_path=destination,
        artifacts=records,
        created=created,
    )
    try:
        _fsync_directory(generation_root)
    except OSError as error:
        raise GenerationPublicationDurabilityError(publication) from error
    return publication


def _resolved_config_path(
    workspace_root: Path,
    configured: object,
) -> Path:
    if not isinstance(configured, str) or not configured:
        raise S104TrainingError("configured path is invalid")
    path = Path(configured)
    resolved = (path if path.is_absolute() else workspace_root / path).resolve(
        strict=True
    )
    try:
        resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S104TrainingError("configured path escapes workspace") from error
    if any(part.casefold().startswith(".env") for part in resolved.parts):
        raise S104TrainingError("environment files are forbidden")
    return resolved


def _stream_parameters(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    labels: Mapping[str, UtteranceLabel],
    reserved: set[str],
    vocabulary: Mapping[str, int],
) -> dict[str, Any]:
    data = cast(Mapping[str, Any], config["data"])
    inner = cast(Mapping[str, Any], data["inner_validation"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    return {
        "manifest_path": _resolved_config_path(
            workspace_root,
            data["modeling_manifest"],
        ),
        "dataset_root": _resolved_config_path(
            workspace_root,
            data["dataset_root"],
        ),
        "labels": labels,
        "reserved_prompts": reserved,
        "target_vocabulary": vocabulary,
        "modulus": int(inner["modulus"]),
        "validation_buckets": {int(value) for value in inner["validation_buckets"]},
        "allowed_l1": {str(value) for value in scope["l1"]},
        "allowed_gender": {str(value) for value in scope["gender"]},
    }


def _assert_execution_lock(
    *,
    lock: Mapping[str, Any],
    config_path: Path,
) -> None:
    if (
        lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or lock.get("status") != "ready_for_s104_gpu_training"
        or lock.get("config_sha256") != sha256_file(config_path)
    ):
        raise S104TrainingError("immutable execution lock differs")
    privacy = cast(Mapping[str, Any], lock.get("privacy"))
    if privacy.get("scope") != "execution_lock_payload_only":
        raise S104TrainingError("execution-lock privacy scope differs")


def _assert_runtime_source_bindings(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    expected_execution_lock_sha256: str,
) -> None:
    disk_config = load_config(config_path)
    disk_lock = load_json_strict(execution_lock_path, "S104 execution lock")
    if (
        disk_config != config
        or disk_lock != execution_lock
        or sha256_file(execution_lock_path) != expected_execution_lock_sha256
        or sha256_file(config_path) != execution_lock.get("config_sha256")
    ):
        raise S104TrainingError("config/execution-lock binding changed")
    data = cast(Mapping[str, Any], config["data"])
    lock_data = cast(Mapping[str, Any], execution_lock["data_contract"])
    source_hashes = cast(Mapping[str, Any], lock_data["source_hashes"])
    configured = {
        "modeling_manifest": data["modeling_manifest"],
        "prompt_challenge_reservation": data["prompt_challenge_reservation"],
        "explicit_utterance_labels": data["explicit_utterance_labels"],
    }
    for role, configured_path in configured.items():
        path = _resolved_config_path(workspace_root, configured_path)
        if sha256_file(path) != source_hashes.get(role):
            raise S104TrainingError("runtime data source binding changed")


def _assert_s103_predecessor_lineage(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    execution_lock: Mapping[str, Any],
) -> None:
    """Revalidate S103 and bind its live immutable chain to the S104 lock."""

    try:
        decision = validate_s103_decision(
            workspace_root=workspace_root,
            config=config,
        )
    except S104ContractError as error:
        raise S104TrainingError("S103 predecessor lineage is invalid") from error
    s103 = cast(Mapping[str, Any], config["s103"])
    expected_evidence = {
        "commit_sha256": decision.commit_sha256,
        "comparison_report_sha256": decision.report_sha256,
        "execution_receipt_sha256": decision.receipt_sha256,
        "generation_id": decision.generation_id,
        "metric": s103["metric"],
        "effect": s103["effect"],
        "estimate": decision.estimate,
        "threshold_absolute": decision.threshold,
        "gate_status": decision.gate_status,
        "gate_passed": decision.gate_passed,
    }
    selected = execution_lock.get("selected_candidate")
    if (
        not isinstance(selected, Mapping)
        or selected.get("variant") != decision.selected_candidate
        or execution_lock.get("s103_branch_evidence") != expected_evidence
    ):
        raise S104TrainingError("S103 predecessor lineage differs from execution lock")


def _select_device(config: Mapping[str, Any]) -> torch.device:
    runtime = cast(Mapping[str, Any], config["runtime"])
    if runtime.get("require_cuda_for_training") is not True:
        raise S104TrainingError("production S104 config must require CUDA")
    if not torch.cuda.is_available():
        raise S104TrainingError("CUDA is unavailable")
    if not torch.cuda.is_bf16_supported():
        raise S104TrainingError("selected CUDA device lacks bfloat16 support")
    return torch.device("cuda")


def _assert_runtime_environment(config: Mapping[str, Any]) -> None:
    runtime = cast(Mapping[str, Any], config["runtime"])
    expected_cublas = str(runtime["cublas_workspace_config"])
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != expected_cublas:
        raise S104TrainingError("deterministic cuBLAS workspace configuration differs")


def _set_failure_stage(run: ExperimentRun, stage: str) -> None:
    if stage not in FAILURE_STAGES:
        raise S104TrainingError("runner failure stage is not allowlisted")
    run.set_failure_context(stage=stage, oom=False)


def _record_failure(
    run: ExperimentRun,
    *,
    stage: str,
    error: BaseException,
) -> None:
    if stage not in FAILURE_STAGES:
        raise S104TrainingError("runner failure stage is not allowlisted")
    run.set_failure_context(
        stage=stage,
        oom=isinstance(error, torch.cuda.OutOfMemoryError),
    )


def run_online_training(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    argv: Sequence[str],
) -> Mapping[str, Any]:
    """Execute all three seeds and publish one commit-gated generation."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolved_config_path(root, str(config_path))
    resolved_execution_lock = _resolved_config_path(
        root,
        str(execution_lock_path),
    )
    _assert_execution_lock(lock=execution_lock, config_path=resolved_config)
    expected_execution_lock_sha256 = sha256_file(resolved_execution_lock)
    _assert_runtime_source_bindings(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=resolved_execution_lock,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=expected_execution_lock_sha256,
    )
    _assert_s103_predecessor_lineage(
        workspace_root=root,
        config=config,
        execution_lock=execution_lock,
    )
    selected_variant = str(
        cast(Mapping[str, Any], execution_lock["selected_candidate"])["variant"]
    )
    candidates = cast(Mapping[str, Any], config["candidates"])
    selected_candidate = cast(Mapping[str, Any], candidates[selected_variant])
    snapshot, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=root,
        selected_candidate=selected_candidate,
    )
    outputs = cast(Mapping[str, Any], config["outputs"])
    generation_root = _resolved_config_path_parent_allowed(
        root,
        outputs["generation_root"],
    )
    commit_path = _resolved_config_path_parent_allowed(
        root,
        outputs["commit_manifest"],
    )
    registry_root = _resolved_config_path_parent_allowed(
        root,
        outputs["registry_root"],
    )
    if commit_path.exists():
        validate_s104_commit(
            workspace_root=root,
            config_path=resolved_config,
        )
        return load_json_strict(commit_path, "S104 commit")
    recovered_commit = _recover_completed_registry_commit(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=resolved_execution_lock,
        execution_lock=execution_lock,
        generation_root=generation_root,
        commit_path=commit_path,
        registry_root=registry_root,
        expected_execution_lock_sha256=expected_execution_lock_sha256,
    )
    if recovered_commit is not None:
        return recovered_commit
    implementation, implementation_paths = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    data_config = cast(Mapping[str, Any], config["data"])
    optimization = cast(Mapping[str, Any], config["optimization"])
    manifest_path = _resolved_config_path(
        root,
        data_config["modeling_manifest"],
    )
    reservation_path = _resolved_config_path(
        root,
        data_config["prompt_challenge_reservation"],
    )
    label_path = _resolved_config_path(
        root,
        data_config["explicit_utterance_labels"],
    )
    registry = ExperimentRun(
        registry_root,
        run_id=str(outputs["planned_registry_run_id"]),
        input_files={
            "config": [
                resolved_config,
                resolved_execution_lock,
                *implementation_paths,
            ],
            "data": [
                manifest_path,
                reservation_path,
                label_path,
            ],
            "model": [model_config_path, model_checkpoint_path],
            "checkpoint": [],
        },
        seeds={f"experiment_seed_{seed}": int(seed) for seed in SEEDS},
        argv=argv,
        repo_path=root,
    )
    published: PublishedGeneration | None = None
    precommit_validated = False
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "running",
        "started_at_utc": utc_now(),
        "completed_at_utc": None,
        "experiment_id": cast(Mapping[str, Any], config["experiment"])["id"],
        "development_only": True,
        "oracle_alignment": True,
        "silver_label": True,
        "promotion_decision_allowed": False,
        "selected_candidate": dict(selected_candidate),
        "s103_branch_evidence": dict(
            cast(Mapping[str, Any], execution_lock["s103_branch_evidence"])
        ),
        "sources": {
            "config_sha256": sha256_file(resolved_config),
            "execution_lock_sha256": expected_execution_lock_sha256,
            "model_config_sha256": sha256_file(model_config_path),
            "model_checkpoint_sha256": sha256_file(model_checkpoint_path),
            "implementation": implementation,
        },
        "selection_contract": {
            "source": "train_inner_validation_only",
            "order": [
                "phone_macro_f1",
                "comprehensibility_score_icc_2_1",
                "earliest_epoch",
            ],
            "undefined_secondary_metric_policy": "no_improvement",
            "refit_after_inner_selection": False,
            "dev_used_for_selection": False,
            "phone_loss_reduction": optimization["phone_loss_reduction"],
            "utterance_loss_reduction": optimization["utterance_loss_reduction"],
        },
        "leakage": {
            "primary_analysis_scope": data_config["primary_analysis_scope"],
            "stack_accessed": False,
            "calibration_accessed": False,
            "locked_test_accessed": False,
            "environment_files_read": [],
            "pooled_feature_cache_read": False,
            "raw_audio_streaming": True,
        },
        "runtime": {
            "device": None,
            "deterministic_algorithms": True,
            "cublas_workspace_config": os.environ.get("CUBLAS_WORKSPACE_CONFIG"),
            "gradient_checkpointing": True,
            "encoder_layerdrop": 0.0,
            "apply_spec_augment": False,
            "freeze_feature_encoder_internal_gradient": True,
            "short_chunk_attention_policy": ("padded_receptive_field_marked_valid"),
            "frozen_backbone_mode_during_training": "train_with_dropout",
        },
        "runs": [],
    }
    generation_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        os.path.realpath(
            os.path.join(
                generation_root,
                f".part-{uuid4().hex}",
            )
        )
    )
    temporary.mkdir()
    current_stage = "registry_initialization"
    try:
        with registry as run:
            try:
                current_stage = "runtime_validation"
                _set_failure_stage(run, current_stage)
                _assert_runtime_environment(config)
                device = _select_device(config)
                report["runtime"]["device"] = _device_metadata(device)
                current_stage = "metadata_scan"
                _set_failure_stage(run, current_stage)
                _assert_runtime_source_bindings(
                    workspace_root=root,
                    config_path=resolved_config,
                    config=config,
                    execution_lock_path=resolved_execution_lock,
                    execution_lock=execution_lock,
                    expected_execution_lock_sha256=(expected_execution_lock_sha256),
                )
                reserved = load_reserved_prompt_hashes(reservation_path)
                labels = load_utterance_labels(label_path)
                if len(labels) != int(
                    cast(Mapping[str, Any], execution_lock["data_contract"])[
                        "included_utterances"
                    ]
                ):
                    raise S104TrainingError(
                        "explicit-label cardinality differs from execution lock"
                    )
                inner = cast(
                    Mapping[str, Any],
                    data_config["inner_validation"],
                )
                scope = cast(
                    Mapping[str, Any],
                    data_config["primary_analysis_scope"],
                )
                metadata = scan_fit_metadata(
                    manifest_path=manifest_path,
                    reserved_prompts=reserved,
                    modulus=int(inner["modulus"]),
                    validation_buckets={
                        int(value) for value in inner["validation_buckets"]
                    },
                    allowed_l1={str(value) for value in scope["l1"]},
                    allowed_gender={str(value) for value in scope["gender"]},
                )
                locked_data = cast(
                    Mapping[str, Any],
                    execution_lock["data_contract"],
                )
                if metadata.fit_utterances != int(locked_data["inner_train_rows"]):
                    raise S104TrainingError(
                        "train-fit cardinality differs from execution lock"
                    )
                primary_split_rows = cast(
                    Mapping[str, Any],
                    locked_data["primary_split_rows"],
                )
                if int(primary_split_rows["train"]) != (
                    int(locked_data["inner_train_rows"])
                    + int(locked_data["inner_validation_rows"])
                ):
                    raise S104TrainingError(
                        "primary train partition cardinality is inconsistent"
                    )
                vocabulary = {
                    label: index
                    for index, label in enumerate(metadata.target_vocabulary)
                }
                stream_parameters = _stream_parameters(
                    workspace_root=root,
                    config=config,
                    labels=labels,
                    reserved=reserved,
                    vocabulary=vocabulary,
                )
                expected_dev_truth = expected_dev_ground_truth(
                    manifest_path=manifest_path,
                    labels=labels,
                    reserved_prompts=reserved,
                    allowed_l1={str(value) for value in scope["l1"]},
                    allowed_gender={str(value) for value in scope["gender"]},
                )
                if len(expected_dev_truth) != int(primary_split_rows["dev"]):
                    raise S104TrainingError(
                        "dev source cardinality differs from execution lock"
                    )
                audio_integrity = AudioIntegrityVerifier()
                seed_reports: list[dict[str, Any]] = []
                for seed in SEEDS:
                    set_deterministic_seed(seed)
                    torch.cuda.empty_cache()
                    torch.cuda.reset_peak_memory_stats(device)
                    current_stage = "model_loading"
                    _set_failure_stage(run, current_stage)
                    model, trainable_audit = build_adapter_model(
                        config=config,
                        selected_candidate=selected_candidate,
                        metadata=metadata,
                        snapshot=snapshot,
                        device=device,
                    )

                    def fit_factory(epoch: int) -> Iterable[OnlineExample]:
                        return iter_examples(
                            **stream_parameters,
                            subset="fit",
                            shuffle_buffer=int(
                                cast(Mapping[str, Any], config["optimization"])[
                                    "bounded_shuffle_buffer_utterances"
                                ]
                            ),
                            shuffle_seed=seed * 1_000_003 + epoch,
                        )

                    def validation_factory() -> Iterable[OnlineExample]:
                        return iter_examples(
                            **stream_parameters,
                            subset="inner_validation",
                        )

                    current_stage = "seed_training"
                    _set_failure_stage(run, current_stage)
                    selection = train_and_select(
                        model=model,
                        config=config,
                        seed=seed,
                        device=device,
                        metadata=metadata,
                        fit_examples_factory=fit_factory,
                        validation_examples_factory=validation_factory,
                        do_normalize=bool(selected_candidate["do_normalize"]),
                        maximum_chunk_seconds=float(
                            cast(
                                Mapping[str, Any],
                                config["runtime"],
                            )["maximum_chunk_seconds"]
                        ),
                        expected_validation_utterances=int(
                            locked_data["inner_validation_rows"]
                        ),
                        audio_integrity=audio_integrity,
                    )
                    seed_directory = temporary / f"seed_{seed}"
                    seed_directory.mkdir()
                    current_stage = "dev_inference"
                    _set_failure_stage(run, current_stage)
                    guard = DevPassGuard()
                    phone_path = seed_directory / "phone_dev_predictions.parquet"
                    utterance_path = (
                        seed_directory / "utterance_dev_predictions.parquet"
                    )
                    with PredictionWriters(
                        phone_path=phone_path,
                        utterance_path=utterance_path,
                    ) as writers:
                        dev_bundle = evaluate_examples(
                            model=model,
                            examples=iter_examples(
                                **stream_parameters,
                                subset="dev",
                            ),
                            device=device,
                            do_normalize=bool(selected_candidate["do_normalize"]),
                            maximum_chunk_seconds=float(
                                cast(
                                    Mapping[str, Any],
                                    config["runtime"],
                                )["maximum_chunk_seconds"]
                            ),
                            use_bfloat16=True,
                            threshold=selection.threshold,
                            seed=seed,
                            writers=writers,
                            dev_guard=guard,
                            audio_integrity=audio_integrity,
                            collect_slices=False,
                        )
                    guard.assert_complete()
                    if dev_bundle.utterance_rows != int(primary_split_rows["dev"]):
                        raise S104TrainingError(
                            "dev cardinality differs from execution lock"
                        )
                    metrics = metric_bundle(
                        dev_bundle,
                        threshold=selection.threshold,
                    )
                    del dev_bundle
                    slices = slice_metrics_from_prediction_files(
                        phone_path=phone_path,
                        utterance_path=utterance_path,
                        threshold=selection.threshold,
                    )
                    current_stage = "artifact_generation"
                    _set_failure_stage(run, current_stage)
                    checkpoint_record = save_adapter_checkpoint(
                        seed_directory / "adapter_and_heads.safetensors",
                        selection=selection,
                        seed=seed,
                        selected_candidate=selected_candidate,
                        metadata=metadata,
                    )
                    slice_path = seed_directory / "dev_slice_metrics.json"
                    atomic_write_json(
                        slice_path,
                        {
                            "schema_version": REPORT_SCHEMA_VERSION,
                            "seed": seed,
                            "development_only": True,
                            "dev_inference_passes": 1,
                            "metrics": slices,
                        },
                    )
                    resource_path = (
                        seed_directory / "resource_and_trainable_parameter_report.json"
                    )
                    resource_report = {
                        "schema_version": REPORT_SCHEMA_VERSION,
                        "seed": seed,
                        "trainable_parameter_audit": trainable_audit,
                        "checkpoint": checkpoint_record,
                        "fit_metadata": {
                            "target_vocabulary": list(metadata.target_vocabulary),
                            "positive_phone_rows": (metadata.positive_phone_rows),
                            "negative_phone_rows": (metadata.negative_phone_rows),
                            "pos_weight": metadata.pos_weight,
                            "fit_utterances": metadata.fit_utterances,
                        },
                        "peak_process_rss_bytes": _peak_rss_bytes(),
                        "peak_cuda_allocated_bytes": int(
                            torch.cuda.max_memory_allocated(device)
                        ),
                        "peak_cuda_reserved_bytes": int(
                            torch.cuda.max_memory_reserved(device)
                        ),
                        "automatic_oom_retry_count": 0,
                    }
                    atomic_write_json(resource_path, resource_report)
                    phone_f1 = float(metrics["phone"]["macro_f1"])
                    primary_icc = metrics["utterance"]["outcomes"][
                        "comprehensibility_score"
                    ]["icc_2_1"]
                    seed_engineering_thresholds = cast(
                        Mapping[str, Any],
                        cast(
                            Mapping[str, Any],
                            config["evaluation"],
                        )["engineering_thresholds"],
                    )
                    seed_phone_threshold = float(
                        seed_engineering_thresholds["phone_macro_f1"]
                    )
                    seed_icc_threshold = float(
                        seed_engineering_thresholds["primary_utterance_icc_2_1"]
                    )
                    seed_report = {
                        "seed": seed,
                        "selection": {
                            "best_epoch": selection.best_epoch,
                            "decision_threshold": selection.threshold,
                            "inner_phone_macro_f1": (selection.phone_macro_f1),
                            "inner_comprehensibility_icc_2_1": (
                                selection.comprehensibility_icc_2_1
                            ),
                            "secondary_metric_defined": (
                                selection.comprehensibility_icc_2_1 is not None
                            ),
                            "undefined_secondary_failure_reason": (
                                None
                                if selection.comprehensibility_icc_2_1 is not None
                                else "icc_2_1_undefined_no_improvement"
                            ),
                            "stopped_early": selection.stopped_early,
                            "history": list(selection.history),
                            "refit_performed": False,
                        },
                        "dev": {
                            "inference_passes": 1,
                            "used_for_selection": False,
                            "metrics": metrics,
                            "engineering_targets": {
                                "phone_macro_f1_threshold": seed_phone_threshold,
                                "primary_utterance_icc_2_1_threshold": (
                                    seed_icc_threshold
                                ),
                                "phone_passed": phone_f1 >= seed_phone_threshold,
                                "primary_utterance_passed": (
                                    math.isfinite(float(primary_icc))
                                    and float(primary_icc) >= seed_icc_threshold
                                ),
                                "development_only_not_promotion": True,
                            },
                            "word_metric_status": "not_evaluable",
                        },
                    }
                    seed_reports.append(seed_report)
                    report["runs"].append(seed_report)
                    registry_metrics: dict[str, float] = {
                        "phone_macro_f1": phone_f1,
                    }
                    if math.isfinite(float(primary_icc)):
                        registry_metrics["comprehensibility_icc_2_1"] = float(
                            primary_icc
                        )
                    run.log_metrics(
                        registry_metrics,
                        step=seed,
                        split="dev",
                    )
                    del model
                    torch.cuda.empty_cache()
                phone_summary = _aggregate(
                    [
                        float(item["dev"]["metrics"]["phone"]["macro_f1"])
                        for item in seed_reports
                    ]
                )
                icc_summary = _aggregate(
                    [
                        cast(
                            float | None,
                            item["dev"]["metrics"]["utterance"]["outcomes"][
                                "comprehensibility_score"
                            ]["icc_2_1"],
                        )
                        for item in seed_reports
                    ]
                )
                evaluation = cast(Mapping[str, Any], config["evaluation"])
                engineering_thresholds = cast(
                    Mapping[str, Any],
                    evaluation["engineering_thresholds"],
                )
                phone_threshold = float(engineering_thresholds["phone_macro_f1"])
                icc_threshold = float(
                    engineering_thresholds["primary_utterance_icc_2_1"]
                )
                report["summary"] = {
                    "seeds_completed": list(SEEDS),
                    "phone_macro_f1": phone_summary,
                    "comprehensibility_icc_2_1": icc_summary,
                    "engineering_targets": {
                        "phone_macro_f1_threshold": phone_threshold,
                        "primary_utterance_icc_2_1_threshold": icc_threshold,
                        "mean_phone_passed": (
                            phone_summary["mean"] is not None
                            and float(phone_summary["mean"]) >= phone_threshold
                        ),
                        "mean_primary_utterance_passed": (
                            icc_summary["mean"] is not None
                            and float(icc_summary["mean"]) >= icc_threshold
                        ),
                        "development_only_not_promotion": True,
                    },
                    "word_metric_status": "not_evaluable",
                    "dev_inference_passes_per_seed": 1,
                    "dev_used_for_selection": False,
                    "locked_test_remains_unread": True,
                    "stack_remains_unread": True,
                    "calibration_remains_unread": True,
                }
                if sha256_file(model_config_path) != str(
                    selected_candidate["source_config_sha256"]
                ) or sha256_file(model_checkpoint_path) != str(
                    selected_candidate["source_checkpoint_sha256"]
                ):
                    raise S104TrainingError(
                        "selected model source changed during training"
                    )
                audio_integrity.assert_stable()
                _assert_runtime_source_bindings(
                    workspace_root=root,
                    config_path=resolved_config,
                    config=config,
                    execution_lock_path=resolved_execution_lock,
                    execution_lock=execution_lock,
                    expected_execution_lock_sha256=(expected_execution_lock_sha256),
                )
                final_implementation, _ = validate_implementation_sources(
                    workspace_root=root,
                    config=config,
                )
                if final_implementation != implementation:
                    raise S104TrainingError(
                        "implementation source changed during training"
                    )
                report["status"] = "completed"
                report["completed_at_utc"] = utc_now()
                atomic_write_json(
                    temporary / "experiment_report.json",
                    report,
                )
                validate_generated_artifact_tree(
                    temporary,
                    allowed_l1={str(value) for value in scope["l1"]},
                    allowed_gender={str(value) for value in scope["gender"]},
                    expected_fit_utterances=int(locked_data["inner_train_rows"]),
                    expected_dev_utterances=int(primary_split_rows["dev"]),
                    engineering_phone_threshold=phone_threshold,
                    engineering_icc_threshold=icc_threshold,
                    selected_candidate=selected_candidate,
                    adapter_config=cast(
                        Mapping[str, Any],
                        config["adapter"],
                    ),
                    heads_config=cast(
                        Mapping[str, Any],
                        config["heads"],
                    ),
                    expected_dev_truth=expected_dev_truth,
                    expected_fit_metadata=metadata,
                )
                current_stage = "artifact_publication"
                _set_failure_stage(run, current_stage)
                try:
                    published = publish_content_addressed_generation(
                        temporary=temporary,
                        generation_root=generation_root,
                    )
                except GenerationPublicationDurabilityError as error:
                    published = error.publication
                    raise
                current_stage = "registry_registration"
                _set_failure_stage(run, current_stage)
                for record in published.artifacts:
                    run.add_artifact(
                        str(record["name"]),
                        published.generation_path / str(record["path"]),
                        kind="s104_development_evidence",
                    )
                if len(run.metadata["artifacts"]) != 16:
                    raise S104TrainingError("completed registry artifact count differs")
            except BaseException as error:
                _record_failure(
                    run,
                    stage=current_stage,
                    error=error,
                )
                raise
        if published is None:
            raise S104TrainingError("artifact generation was not published")
        metadata_path = registry.metadata_path
        registry_document = load_json_strict(
            metadata_path,
            "S104 experiment registry",
        )
        if (
            registry_document.get("status") != "completed"
            or len(cast(list[Any], registry_document.get("artifacts"))) != 16
        ):
            raise S104TrainingError("S104 registry did not complete")
        _assert_runtime_source_bindings(
            workspace_root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=resolved_execution_lock,
            execution_lock=execution_lock,
            expected_execution_lock_sha256=expected_execution_lock_sha256,
        )
        final_implementation, _ = validate_implementation_sources(
            workspace_root=root,
            config=config,
        )
        if final_implementation != implementation:
            raise S104TrainingError(
                "implementation source changed before commit publication"
            )
        if sha256_file(model_config_path) != str(
            selected_candidate["source_config_sha256"]
        ) or sha256_file(model_checkpoint_path) != str(
            selected_candidate["source_checkpoint_sha256"]
        ):
            raise S104TrainingError(
                "selected model source changed before commit publication"
            )
        current_stage = "commit_publication"
        _validate_precommit_evidence(
            workspace_root=root,
            config_path=resolved_config,
            config=config,
            execution_lock_path=resolved_execution_lock,
            execution_lock=execution_lock,
            expected_execution_lock_sha256=(expected_execution_lock_sha256),
            generation=published,
            registry_path=metadata_path,
        )
        precommit_validated = True
        commit = _build_commit_payload(
            workspace_root=root,
            generation=published,
            registry_path=metadata_path,
            execution_lock_path=resolved_execution_lock,
            expected_execution_lock_sha256=(expected_execution_lock_sha256),
        )
        publish_immutable_json(commit_path, commit)
        validate_s104_commit(
            workspace_root=root,
            config_path=resolved_config,
        )
        return commit
    except BaseException as error:
        if temporary.exists():
            shutil.rmtree(temporary)
        if (
            published is not None
            and published.created
            and published.generation_path.exists()
            and not commit_path.exists()
        ):
            registry_completed = False
            try:
                registry_state = load_json_strict(
                    registry.metadata_path,
                    "S104 cleanup registry",
                )
                registry_completed = registry_state.get("status") == "completed"
            except BaseException:
                registry_completed = False
            if not precommit_validated:
                try:
                    in_memory_status = registry.metadata.get("status")
                except BaseException:
                    in_memory_status = None
                if in_memory_status == "completed":
                    try:
                        registry.mark_post_completion_failure(
                            stage=current_stage,
                            exception_type=type(error),
                            oom=isinstance(
                                error,
                                torch.cuda.OutOfMemoryError,
                            ),
                        )
                        registry_completed = False
                    except BaseException:
                        pass
            if registry_completed and not precommit_validated:
                try:
                    registry.mark_post_completion_failure(
                        stage=current_stage,
                        exception_type=type(error),
                        oom=isinstance(
                            error,
                            torch.cuda.OutOfMemoryError,
                        ),
                    )
                    registry_completed = False
                except BaseException:
                    registry_completed = True
            if not registry_completed:
                shutil.rmtree(published.generation_path)
                _fsync_directory(generation_root)
        raise


def _resolved_config_path_parent_allowed(
    workspace_root: Path,
    configured: object,
) -> Path:
    if not isinstance(configured, str) or not configured:
        raise S104TrainingError("configured output path is invalid")
    raw = Path(configured)
    candidate = raw if raw.is_absolute() else workspace_root / raw
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as error:
        raise S104TrainingError("configured output escapes workspace") from error
    if any(part.casefold().startswith(".env") for part in resolved.parts):
        raise S104TrainingError("environment output path is forbidden")
    return resolved


def _workspace_relative(workspace_root: Path, path: Path) -> str:
    resolved = path.resolve(strict=True)
    try:
        return resolved.relative_to(workspace_root).as_posix()
    except ValueError as error:
        raise S104TrainingError("evidence path escapes workspace") from error


def _expected_registry_fingerprints(
    *,
    workspace_root: Path,
    paths: Sequence[Path],
) -> list[Mapping[str, Any]]:
    resolved = [path.resolve(strict=True) for path in paths]
    if len(set(resolved)) != len(resolved):
        raise S104TrainingError("registry input paths contain a duplicate")
    return sorted(
        [fingerprint_file(path, relative_to=workspace_root) for path in resolved],
        key=lambda record: str(record["path"]),
    )


def _load_expected_dev_truth(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> Mapping[str, ExpectedDevUtterance]:
    data = cast(Mapping[str, Any], config["data"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    return expected_dev_ground_truth(
        manifest_path=_resolved_config_path(
            workspace_root,
            data["modeling_manifest"],
        ),
        labels=load_utterance_labels(
            _resolved_config_path(
                workspace_root,
                data["explicit_utterance_labels"],
            )
        ),
        reserved_prompts=load_reserved_prompt_hashes(
            _resolved_config_path(
                workspace_root,
                data["prompt_challenge_reservation"],
            )
        ),
        allowed_l1={str(item) for item in scope["l1"]},
        allowed_gender={str(item) for item in scope["gender"]},
    )


def _load_expected_fit_metadata(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
) -> FitMetadata:
    data = cast(Mapping[str, Any], config["data"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    inner = cast(Mapping[str, Any], data["inner_validation"])
    return scan_fit_metadata(
        manifest_path=_resolved_config_path(
            workspace_root,
            data["modeling_manifest"],
        ),
        reserved_prompts=load_reserved_prompt_hashes(
            _resolved_config_path(
                workspace_root,
                data["prompt_challenge_reservation"],
            )
        ),
        modulus=int(inner["modulus"]),
        validation_buckets={int(item) for item in inner["validation_buckets"]},
        allowed_l1={str(item) for item in scope["l1"]},
        allowed_gender={str(item) for item in scope["gender"]},
    )


def _validate_registry_document(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    config_path: Path,
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    registry_path: Path,
    generation_path: Path,
    artifact_records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    registry = load_json_strict(registry_path, "S104 experiment registry")
    expected_registry_keys = {
        "schema_version",
        "run_id",
        "status",
        "timestamps",
        "seeds",
        "argv",
        "inputs",
        "runtime",
        "accelerator",
        "git",
        "metrics",
        "artifacts",
        "failure",
    }
    timestamps = registry.get("timestamps")
    registry_runtime = registry.get("runtime")
    accelerator = registry.get("accelerator")
    git = registry.get("git")
    if (
        set(registry) != expected_registry_keys
        or not isinstance(timestamps, Mapping)
        or set(timestamps) != {"started", "updated", "ended"}
        or not all(
            isinstance(timestamps.get(name), Mapping)
            and set(cast(Mapping[str, Any], timestamps[name])) == {"utc", "kst"}
            and all(
                isinstance(
                    cast(Mapping[str, Any], timestamps[name]).get(zone),
                    str,
                )
                for zone in ("utc", "kst")
            )
            for name in ("started", "updated", "ended")
        )
        or not isinstance(registry.get("argv"), list)
        or any(
            not isinstance(argument, str) or ".env" in argument.casefold()
            for argument in cast(list[Any], registry.get("argv"))
        )
        or not isinstance(registry_runtime, Mapping)
        or set(registry_runtime) != {"python", "platform", "packages"}
        or not all(
            isinstance(registry_runtime.get(key), Mapping)
            for key in ("python", "platform", "packages")
        )
        or not isinstance(accelerator, Mapping)
        or set(accelerator)
        != {
            "cuda_available",
            "cuda_runtime_version",
            "torch_device_count",
            "torch_devices",
            "nvidia_smi",
        }
        or accelerator.get("cuda_available") is not True
        or isinstance(accelerator.get("torch_device_count"), bool)
        or not isinstance(accelerator.get("torch_device_count"), int)
        or int(accelerator["torch_device_count"]) <= 0
        or not isinstance(accelerator.get("torch_devices"), list)
        or len(cast(list[Any], accelerator["torch_devices"]))
        != accelerator["torch_device_count"]
        or not isinstance(accelerator.get("nvidia_smi"), Mapping)
        or not isinstance(git, Mapping)
        or set(git) != {"available", "commit", "dirty"}
        or git.get("available") is not True
        or not isinstance(git.get("commit"), str)
        or len(cast(str, git.get("commit"))) not in {40, 64}
        or not isinstance(git.get("dirty"), bool)
    ):
        raise S104TrainingError("completed registry schema differs")
    for device in cast(list[Any], accelerator["torch_devices"]):
        if (
            not isinstance(device, Mapping)
            or set(device)
            != {
                "index",
                "name",
                "compute_capability",
                "memory_total_bytes",
            }
            or isinstance(device.get("index"), bool)
            or not isinstance(device.get("index"), int)
            or not isinstance(device.get("name"), str)
            or not cast(str, device.get("name"))
            or not isinstance(device.get("compute_capability"), str)
            or isinstance(device.get("memory_total_bytes"), bool)
            or not isinstance(device.get("memory_total_bytes"), int)
            or int(device["memory_total_bytes"]) <= 0
        ):
            raise S104TrainingError("registry CUDA device schema differs")
    outputs = cast(Mapping[str, Any], config["outputs"])
    selected_variant = str(
        cast(Mapping[str, Any], execution_lock["selected_candidate"])["variant"]
    )
    selected_candidate = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config["candidates"])[selected_variant],
    )
    _, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=workspace_root,
        selected_candidate=selected_candidate,
    )
    if (
        sha256_file(model_config_path) != selected_candidate["source_config_sha256"]
        or sha256_file(model_checkpoint_path)
        != selected_candidate["source_checkpoint_sha256"]
    ):
        raise S104TrainingError("registry model source binding differs")
    implementation, implementation_paths = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    del implementation
    data = cast(Mapping[str, Any], config["data"])
    manifest_path = _resolved_config_path(
        workspace_root,
        data["modeling_manifest"],
    )
    reservation_path = _resolved_config_path(
        workspace_root,
        data["prompt_challenge_reservation"],
    )
    label_path = _resolved_config_path(
        workspace_root,
        data["explicit_utterance_labels"],
    )
    expected_inputs = {
        "config": _expected_registry_fingerprints(
            workspace_root=workspace_root,
            paths=[
                config_path,
                execution_lock_path,
                *implementation_paths,
            ],
        ),
        "data": _expected_registry_fingerprints(
            workspace_root=workspace_root,
            paths=[manifest_path, reservation_path, label_path],
        ),
        "model": _expected_registry_fingerprints(
            workspace_root=workspace_root,
            paths=[model_config_path, model_checkpoint_path],
        ),
        "checkpoint": [],
    }
    registry_artifacts = registry.get("artifacts")
    if not isinstance(registry_artifacts, list):
        raise S104TrainingError("registry artifacts are absent")
    expected_artifacts: list[dict[str, Any]] = []
    for record in artifact_records:
        path = generation_path / str(record["path"])
        expected_artifacts.append(
            {
                "name": record["name"],
                "kind": "s104_development_evidence",
                "path": _workspace_relative(workspace_root, path),
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            }
        )
    normalized_artifacts: list[dict[str, Any]] = []
    for value in registry_artifacts:
        if not isinstance(value, Mapping):
            raise S104TrainingError("registry artifact is invalid")
        registered_at = value.get("registered_at")
        if (
            set(value)
            != {
                "name",
                "kind",
                "path",
                "size_bytes",
                "sha256",
                "registered_at",
            }
            or not isinstance(registered_at, Mapping)
            or set(registered_at) != {"utc", "kst"}
            or not all(
                isinstance(registered_at.get(zone), str) for zone in ("utc", "kst")
            )
        ):
            raise S104TrainingError("registry artifact schema differs")
        normalized_artifacts.append(
            {
                key: value[key]
                for key in ("name", "kind", "path", "sha256", "size_bytes")
            }
        )
    metrics = registry.get("metrics")
    if not isinstance(metrics, list) or len(metrics) != len(SEEDS):
        raise S104TrainingError("registry dev metric count differs")
    report = load_json_strict(
        generation_path / "experiment_report.json",
        "S104 experiment report",
    )
    runs = report.get("runs")
    report_runtime = report.get("runtime")
    report_device = (
        report_runtime.get("device") if isinstance(report_runtime, Mapping) else None
    )
    if not isinstance(report_device, Mapping):
        raise S104TrainingError("report CUDA device is absent")
    matching_devices = [
        device
        for device in cast(list[Any], accelerator["torch_devices"])
        if isinstance(device, Mapping)
        and device.get("index") == report_device.get("index")
    ]
    if (
        len(matching_devices) != 1
        or matching_devices[0].get("name") != report_device.get("name")
        or matching_devices[0].get("memory_total_bytes")
        != report_device.get("total_memory_bytes")
    ):
        raise S104TrainingError("registry/report CUDA device binding differs")
    if not isinstance(runs, list):
        raise S104TrainingError("experiment report runs are absent")
    run_by_seed = {run.get("seed"): run for run in runs if isinstance(run, Mapping)}
    for expected_seed, event in zip(SEEDS, metrics, strict=True):
        if not isinstance(event, Mapping):
            raise S104TrainingError("registry metric event is invalid")
        values = event.get("values")
        report_run = run_by_seed.get(expected_seed)
        if not isinstance(report_run, Mapping):
            raise S104TrainingError("registry metric seed is absent from report")
        report_dev = report_run.get("dev")
        if not isinstance(report_dev, Mapping):
            raise S104TrainingError("registry metric report dev is absent")
        report_metrics = report_dev.get("metrics")
        if not isinstance(report_metrics, Mapping):
            raise S104TrainingError("registry metric report bundle is absent")
        expected_values: dict[str, float] = {
            "phone_macro_f1": float(
                cast(Mapping[str, Any], report_metrics["phone"])["macro_f1"]
            )
        }
        report_icc = cast(
            Mapping[str, Any],
            cast(
                Mapping[str, Any],
                report_metrics["utterance"],
            )["outcomes"],
        )["comprehensibility_score"]["icc_2_1"]
        if report_icc is not None:
            expected_values["comprehensibility_icc_2_1"] = float(report_icc)
        if (
            set(event) != {"timestamp", "step", "split", "values"}
            or not isinstance(event.get("timestamp"), Mapping)
            or set(cast(Mapping[str, Any], event["timestamp"])) != {"utc", "kst"}
            or not all(
                isinstance(
                    cast(Mapping[str, Any], event["timestamp"]).get(zone),
                    str,
                )
                for zone in ("utc", "kst")
            )
            or event.get("step") != expected_seed
            or event.get("split") != "dev"
            or not isinstance(values, Mapping)
            or not set(values).issubset(
                {
                    "phone_macro_f1",
                    "comprehensibility_icc_2_1",
                }
            )
            or "phone_macro_f1" not in values
            or values != expected_values
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                for item in values.values()
            )
        ):
            raise S104TrainingError("registry metric event differs")
    if (
        registry.get("schema_version") != 1
        or registry.get("run_id") != outputs["planned_registry_run_id"]
        or registry.get("status") != "completed"
        or registry.get("failure") is not None
        or registry.get("seeds") != {f"experiment_seed_{seed}": seed for seed in SEEDS}
        or registry.get("inputs") != expected_inputs
        or normalized_artifacts != expected_artifacts
    ):
        raise S104TrainingError("completed registry binding differs")
    return registry


def _build_commit_payload(
    *,
    workspace_root: Path,
    generation: PublishedGeneration,
    registry_path: Path,
    execution_lock_path: Path,
    expected_execution_lock_sha256: str,
) -> dict[str, Any]:
    if sha256_file(execution_lock_path) != expected_execution_lock_sha256:
        raise S104TrainingError("execution lock changed before commit publication")
    commit: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation.generation_id,
        "generation_path": _workspace_relative(
            workspace_root,
            generation.generation_path,
        ),
        "artifacts": list(generation.artifacts),
        "experiment_report_sha256": sha256_file(
            generation.generation_path / "experiment_report.json"
        ),
        "registry": {
            "run_id": load_json_strict(
                registry_path,
                "S104 experiment registry",
            )["run_id"],
            "path": _workspace_relative(workspace_root, registry_path),
            "sha256": sha256_file(registry_path),
            "status": "completed",
            "artifact_count": len(generation.artifacts),
        },
        "execution_lock": {
            "path": _workspace_relative(workspace_root, execution_lock_path),
            "sha256": expected_execution_lock_sha256,
        },
    }
    commit["binding_sha256"] = hashlib.sha256(
        canonical_json_bytes(commit, pretty=False)
    ).hexdigest()
    return commit


def _validate_precommit_evidence(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    expected_execution_lock_sha256: str,
    generation: PublishedGeneration,
    registry_path: Path,
) -> None:
    """Validate every transitive evidence edge before exposing the pointer."""

    registry_sha256 = sha256_file(registry_path)
    _assert_s103_predecessor_lineage(
        workspace_root=workspace_root,
        config=config,
        execution_lock=execution_lock,
    )
    _assert_runtime_source_bindings(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=expected_execution_lock_sha256,
    )
    selected_variant = str(
        cast(Mapping[str, Any], execution_lock["selected_candidate"])["variant"]
    )
    selected_candidate = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config["candidates"])[selected_variant],
    )
    _, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=workspace_root,
        selected_candidate=selected_candidate,
    )
    implementation, _ = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    data = cast(Mapping[str, Any], config["data"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    lock_data = cast(Mapping[str, Any], execution_lock["data_contract"])
    primary_rows = cast(
        Mapping[str, Any],
        lock_data["primary_split_rows"],
    )
    evaluation = cast(Mapping[str, Any], config["evaluation"])
    engineering = cast(
        Mapping[str, Any],
        evaluation["engineering_thresholds"],
    )
    validate_generated_artifact_tree(
        generation.generation_path,
        allowed_l1={str(item) for item in scope["l1"]},
        allowed_gender={str(item) for item in scope["gender"]},
        expected_fit_utterances=int(lock_data["inner_train_rows"]),
        expected_dev_utterances=int(primary_rows["dev"]),
        engineering_phone_threshold=float(engineering["phone_macro_f1"]),
        engineering_icc_threshold=float(engineering["primary_utterance_icc_2_1"]),
        selected_candidate=selected_candidate,
        adapter_config=cast(Mapping[str, Any], config["adapter"]),
        heads_config=cast(Mapping[str, Any], config["heads"]),
        expected_dev_truth=_load_expected_dev_truth(
            workspace_root=workspace_root,
            config=config,
        ),
        expected_fit_metadata=_load_expected_fit_metadata(
            workspace_root=workspace_root,
            config=config,
        ),
    )
    records = _artifact_records(generation.generation_path)
    if (
        tuple(records) != tuple(generation.artifacts)
        or _generation_id(records) != generation.generation_id
        or generation.generation_path.name != generation.generation_id
    ):
        raise S104TrainingError("precommit generation binding differs")
    _validate_registry_document(
        workspace_root=workspace_root,
        config=config,
        config_path=config_path,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        registry_path=registry_path,
        generation_path=generation.generation_path,
        artifact_records=records,
    )
    report = load_json_strict(
        generation.generation_path / "experiment_report.json",
        "S104 experiment report",
    )
    report_sources = report.get("sources")
    leakage = report.get("leakage")
    selection_contract = report.get("selection_contract")
    runtime_report = report.get("runtime")
    optimization = cast(Mapping[str, Any], config["optimization"])
    expected_source_keys = {
        "config_sha256",
        "execution_lock_sha256",
        "model_config_sha256",
        "model_checkpoint_sha256",
        "implementation",
    }
    expected_leakage_keys = {
        "primary_analysis_scope",
        "stack_accessed",
        "calibration_accessed",
        "locked_test_accessed",
        "environment_files_read",
        "pooled_feature_cache_read",
        "raw_audio_streaming",
    }
    expected_selection_contract_keys = {
        "source",
        "order",
        "undefined_secondary_metric_policy",
        "refit_after_inner_selection",
        "dev_used_for_selection",
        "phone_loss_reduction",
        "utterance_loss_reduction",
    }
    expected_runtime_keys = {
        "device",
        "deterministic_algorithms",
        "cublas_workspace_config",
        "gradient_checkpointing",
        "encoder_layerdrop",
        "apply_spec_augment",
        "freeze_feature_encoder_internal_gradient",
        "short_chunk_attention_policy",
        "frozen_backbone_mode_during_training",
    }
    expected_device_keys = {
        "type",
        "index",
        "name",
        "compute_capability",
        "total_memory_bytes",
        "torch_version",
        "cuda_version",
        "mixed_precision",
    }
    if (
        report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("status") != "completed"
        or report.get("experiment_id")
        != cast(Mapping[str, Any], config["experiment"])["id"]
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
        or not isinstance(report.get("started_at_utc"), str)
        or not isinstance(report.get("completed_at_utc"), str)
        or report.get("selected_candidate") != dict(selected_candidate)
        or report.get("s103_branch_evidence") != execution_lock["s103_branch_evidence"]
        or not isinstance(report_sources, Mapping)
        or set(report_sources) != expected_source_keys
        or report_sources.get("config_sha256") != sha256_file(config_path)
        or report_sources.get("execution_lock_sha256") != expected_execution_lock_sha256
        or report_sources.get("model_config_sha256") != sha256_file(model_config_path)
        or report_sources.get("model_checkpoint_sha256")
        != sha256_file(model_checkpoint_path)
        or report_sources.get("implementation") != implementation
        or not isinstance(leakage, Mapping)
        or set(leakage) != expected_leakage_keys
        or leakage.get("primary_analysis_scope") != scope
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
        or leakage.get("environment_files_read") != []
        or leakage.get("pooled_feature_cache_read") is not False
        or leakage.get("raw_audio_streaming") is not True
        or not isinstance(selection_contract, Mapping)
        or set(selection_contract) != expected_selection_contract_keys
        or selection_contract.get("source") != "train_inner_validation_only"
        or selection_contract.get("order")
        != [
            "phone_macro_f1",
            "comprehensibility_score_icc_2_1",
            "earliest_epoch",
        ]
        or selection_contract.get("undefined_secondary_metric_policy")
        != "no_improvement"
        or selection_contract.get("refit_after_inner_selection") is not False
        or selection_contract.get("dev_used_for_selection") is not False
        or selection_contract.get("phone_loss_reduction")
        != optimization["phone_loss_reduction"]
        or selection_contract.get("utterance_loss_reduction")
        != optimization["utterance_loss_reduction"]
        or not isinstance(runtime_report, Mapping)
        or set(runtime_report) != expected_runtime_keys
        or runtime_report.get("deterministic_algorithms") is not True
        or runtime_report.get("gradient_checkpointing") is not True
        or runtime_report.get("encoder_layerdrop") != 0.0
        or runtime_report.get("apply_spec_augment") is not False
        or runtime_report.get("freeze_feature_encoder_internal_gradient") is not True
        or runtime_report.get("short_chunk_attention_policy")
        != "padded_receptive_field_marked_valid"
        or runtime_report.get("frozen_backbone_mode_during_training")
        != "train_with_dropout"
        or runtime_report.get("cublas_workspace_config")
        != cast(Mapping[str, Any], config["runtime"])["cublas_workspace_config"]
        or not isinstance(runtime_report.get("device"), Mapping)
        or set(cast(Mapping[str, Any], runtime_report["device"]))
        != expected_device_keys
        or cast(Mapping[str, Any], runtime_report["device"]).get("type") != "cuda"
        or cast(
            Mapping[str, Any],
            runtime_report["device"],
        ).get("mixed_precision")
        != "bfloat16"
        or isinstance(
            cast(Mapping[str, Any], runtime_report["device"]).get("total_memory_bytes"),
            bool,
        )
        or not isinstance(
            cast(Mapping[str, Any], runtime_report["device"]).get("total_memory_bytes"),
            int,
        )
        or int(cast(Mapping[str, Any], runtime_report["device"])["total_memory_bytes"])
        <= 0
    ):
        raise S104TrainingError("precommit experiment report binding differs")
    total_memory_bytes = int(
        cast(Mapping[str, Any], runtime_report["device"])["total_memory_bytes"]
    )
    for seed in SEEDS:
        resource_report = load_json_strict(
            generation.generation_path
            / f"seed_{seed}"
            / "resource_and_trainable_parameter_report.json",
            "S104 resource report",
        )
        if (
            int(resource_report["peak_cuda_allocated_bytes"]) > total_memory_bytes
            or int(resource_report["peak_cuda_reserved_bytes"]) > total_memory_bytes
        ):
            raise S104TrainingError("reported peak CUDA memory exceeds device")
    _assert_runtime_source_bindings(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=expected_execution_lock_sha256,
    )
    final_implementation, _ = validate_implementation_sources(
        workspace_root=workspace_root,
        config=config,
    )
    _assert_s103_predecessor_lineage(
        workspace_root=workspace_root,
        config=config,
        execution_lock=execution_lock,
    )
    if (
        sha256_file(execution_lock_path) != expected_execution_lock_sha256
        or sha256_file(registry_path) != registry_sha256
        or final_implementation != implementation
        or sha256_file(model_config_path) != selected_candidate["source_config_sha256"]
        or sha256_file(model_checkpoint_path)
        != selected_candidate["source_checkpoint_sha256"]
        or _artifact_records(generation.generation_path) != records
    ):
        raise S104TrainingError("precommit evidence changed during validation")


def validate_s104_commit(
    *,
    workspace_root: Path,
    config_path: Path,
) -> Mapping[str, Any]:
    """Read-only validation of the complete S104 evidence hash chain."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolved_config_path(root, str(config_path))
    config = load_config(resolved_config)
    validate_config(config)
    outputs = cast(Mapping[str, Any], config["outputs"])
    commit_path = _resolved_config_path(
        root,
        outputs["commit_manifest"],
    )
    commit_sha256 = sha256_file(commit_path)
    commit = load_json_strict(commit_path, "S104 commit")
    if set(commit) != {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "experiment_report_sha256",
        "registry",
        "execution_lock",
        "binding_sha256",
    }:
        raise S104TrainingError("S104 commit schema differs")
    unsigned = dict(commit)
    binding = unsigned.pop("binding_sha256", None)
    expected_binding = hashlib.sha256(
        canonical_json_bytes(unsigned, pretty=False)
    ).hexdigest()
    generation_id = commit.get("generation_id")
    if (
        commit.get("schema_version") != COMMIT_SCHEMA_VERSION
        or commit.get("status") != "committed"
        or not _is_sha256(generation_id)
        or binding != expected_binding
    ):
        raise S104TrainingError("S104 commit binding differs")
    generation_root = _resolved_config_path_parent_allowed(
        root,
        outputs["generation_root"],
    )
    generation_path = (generation_root / cast(str, generation_id)).resolve(strict=True)
    if (
        not generation_path.is_dir()
        or generation_path.parent != generation_root
        or commit.get("generation_path") != _workspace_relative(root, generation_path)
    ):
        raise S104TrainingError("S104 generation path binding differs")
    execution_lock_path = _resolved_config_path(
        root,
        outputs["execution_lock"],
    )
    execution_lock_sha256 = sha256_file(execution_lock_path)
    execution_lock = load_json_strict(
        execution_lock_path,
        "S104 execution lock",
    )
    lock_record = commit.get("execution_lock")
    if (
        not isinstance(lock_record, Mapping)
        or set(lock_record) != {"path", "sha256"}
        or lock_record.get("path") != _workspace_relative(root, execution_lock_path)
        or lock_record.get("sha256") != execution_lock_sha256
    ):
        raise S104TrainingError("commit execution-lock record differs")
    _assert_execution_lock(
        lock=execution_lock,
        config_path=resolved_config,
    )
    _assert_runtime_source_bindings(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=execution_lock_sha256,
    )
    data = cast(Mapping[str, Any], config["data"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    locked_data = cast(Mapping[str, Any], execution_lock["data_contract"])
    evaluation = cast(Mapping[str, Any], config["evaluation"])
    engineering = cast(
        Mapping[str, Any],
        evaluation["engineering_thresholds"],
    )
    validate_generated_artifact_tree(
        generation_path,
        allowed_l1={str(item) for item in scope["l1"]},
        allowed_gender={str(item) for item in scope["gender"]},
        expected_fit_utterances=int(locked_data["inner_train_rows"]),
        expected_dev_utterances=int(
            cast(Mapping[str, Any], locked_data["primary_split_rows"])["dev"]
        ),
        engineering_phone_threshold=float(engineering["phone_macro_f1"]),
        engineering_icc_threshold=float(engineering["primary_utterance_icc_2_1"]),
        selected_candidate=cast(
            Mapping[str, Any],
            cast(Mapping[str, Any], config["candidates"])[
                str(
                    cast(
                        Mapping[str, Any],
                        execution_lock["selected_candidate"],
                    )["variant"]
                )
            ],
        ),
        adapter_config=cast(Mapping[str, Any], config["adapter"]),
        heads_config=cast(Mapping[str, Any], config["heads"]),
        expected_dev_truth=_load_expected_dev_truth(
            workspace_root=root,
            config=config,
        ),
        expected_fit_metadata=_load_expected_fit_metadata(
            workspace_root=root,
            config=config,
        ),
    )
    artifact_records = _artifact_records(generation_path)
    if (
        commit.get("artifacts") != list(artifact_records)
        or _generation_id(artifact_records) != generation_id
        or commit.get("experiment_report_sha256")
        != sha256_file(generation_path / "experiment_report.json")
    ):
        raise S104TrainingError("commit artifact binding differs")
    registry_record = commit.get("registry")
    registry_path = _resolved_config_path(
        root,
        str(outputs["registry_root"])
        + "/"
        + str(outputs["planned_registry_run_id"])
        + "/metadata.json",
    )
    if (
        not isinstance(registry_record, Mapping)
        or set(registry_record)
        != {"run_id", "path", "sha256", "status", "artifact_count"}
        or registry_record.get("run_id") != outputs["planned_registry_run_id"]
        or registry_record.get("path") != _workspace_relative(root, registry_path)
        or registry_record.get("sha256") != sha256_file(registry_path)
        or registry_record.get("status") != "completed"
        or registry_record.get("artifact_count") != len(artifact_records)
    ):
        raise S104TrainingError("commit registry record differs")
    _validate_registry_document(
        workspace_root=root,
        config=config,
        config_path=resolved_config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        registry_path=registry_path,
        generation_path=generation_path,
        artifact_records=artifact_records,
    )
    _validate_precommit_evidence(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=execution_lock_sha256,
        generation=PublishedGeneration(
            generation_id=cast(str, generation_id),
            generation_path=generation_path,
            artifacts=artifact_records,
            created=False,
        ),
        registry_path=registry_path,
    )
    report = load_json_strict(
        generation_path / "experiment_report.json",
        "S104 experiment report",
    )
    selected_variant = str(
        cast(Mapping[str, Any], execution_lock["selected_candidate"])["variant"]
    )
    selected_candidate = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config["candidates"])[selected_variant],
    )
    implementation, _ = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    report_sources = report.get("sources")
    leakage = report.get("leakage")
    selection_contract = report.get("selection_contract")
    runtime_report = report.get("runtime")
    optimization = cast(Mapping[str, Any], config["optimization"])
    if (
        report.get("schema_version") != REPORT_SCHEMA_VERSION
        or report.get("status") != "completed"
        or report.get("experiment_id")
        != cast(Mapping[str, Any], config["experiment"])["id"]
        or report.get("development_only") is not True
        or report.get("oracle_alignment") is not True
        or report.get("silver_label") is not True
        or report.get("promotion_decision_allowed") is not False
        or not isinstance(report.get("started_at_utc"), str)
        or not isinstance(report.get("completed_at_utc"), str)
        or report.get("selected_candidate") != dict(selected_candidate)
        or report.get("s103_branch_evidence") != execution_lock["s103_branch_evidence"]
        or not isinstance(report_sources, Mapping)
        or report_sources.get("config_sha256") != sha256_file(resolved_config)
        or report_sources.get("execution_lock_sha256") != execution_lock_sha256
        or report_sources.get("implementation") != implementation
        or not isinstance(leakage, Mapping)
        or leakage.get("primary_analysis_scope") != scope
        or leakage.get("stack_accessed") is not False
        or leakage.get("calibration_accessed") is not False
        or leakage.get("locked_test_accessed") is not False
        or leakage.get("environment_files_read") != []
        or leakage.get("pooled_feature_cache_read") is not False
        or leakage.get("raw_audio_streaming") is not True
        or not isinstance(selection_contract, Mapping)
        or selection_contract.get("source") != "train_inner_validation_only"
        or selection_contract.get("order")
        != [
            "phone_macro_f1",
            "comprehensibility_score_icc_2_1",
            "earliest_epoch",
        ]
        or selection_contract.get("undefined_secondary_metric_policy")
        != "no_improvement"
        or selection_contract.get("refit_after_inner_selection") is not False
        or selection_contract.get("dev_used_for_selection") is not False
        or selection_contract.get("phone_loss_reduction")
        != optimization["phone_loss_reduction"]
        or selection_contract.get("utterance_loss_reduction")
        != optimization["utterance_loss_reduction"]
        or not isinstance(runtime_report, Mapping)
        or runtime_report.get("deterministic_algorithms") is not True
        or runtime_report.get("gradient_checkpointing") is not True
        or runtime_report.get("encoder_layerdrop") != 0.0
        or runtime_report.get("apply_spec_augment") is not False
        or runtime_report.get("freeze_feature_encoder_internal_gradient") is not True
        or runtime_report.get("short_chunk_attention_policy")
        != "padded_receptive_field_marked_valid"
        or runtime_report.get("frozen_backbone_mode_during_training")
        != "train_with_dropout"
        or runtime_report.get("cublas_workspace_config")
        != cast(Mapping[str, Any], config["runtime"])["cublas_workspace_config"]
        or not isinstance(runtime_report.get("device"), Mapping)
        or cast(Mapping[str, Any], runtime_report["device"]).get("type") != "cuda"
        or cast(
            Mapping[str, Any],
            runtime_report["device"],
        ).get("mixed_precision")
        != "bfloat16"
    ):
        raise S104TrainingError("experiment report source binding differs")
    _, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=root,
        selected_candidate=selected_candidate,
    )
    if report_sources.get("model_config_sha256") != sha256_file(
        model_config_path
    ) or report_sources.get("model_checkpoint_sha256") != sha256_file(
        model_checkpoint_path
    ):
        raise S104TrainingError("experiment report model binding differs")
    if (
        sha256_file(commit_path) != commit_sha256
        or sha256_file(execution_lock_path) != execution_lock_sha256
        or _artifact_records(generation_path) != artifact_records
        or sha256_file(registry_path) != registry_record["sha256"]
    ):
        raise S104TrainingError("completed evidence changed during validation")
    return {
        "status": "valid",
        "generation_id": generation_id,
        "artifact_count": len(artifact_records),
        "commit_sha256": commit_sha256,
        "registry_sha256": registry_record["sha256"],
        "execution_lock_sha256": execution_lock_sha256,
        "development_only": True,
        "locked_test_accessed": False,
    }


def _recover_completed_registry_commit(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    generation_root: Path,
    commit_path: Path,
    registry_root: Path,
    expected_execution_lock_sha256: str,
) -> Mapping[str, Any] | None:
    """Recover only a fully completed registry/generation publication."""

    outputs = cast(Mapping[str, Any], config["outputs"])
    registry_path = (
        registry_root / str(outputs["planned_registry_run_id"]) / "metadata.json"
    )
    if not registry_path.exists():
        if registry_path.parent.exists():
            raise S104TrainingError(
                "planned S104 registry initialization is incomplete"
            )
        return None
    registry = load_json_strict(registry_path, "S104 recovery registry")
    if registry.get("status") != "completed":
        raise S104TrainingError(
            "planned S104 registry exists without a recoverable completed state"
        )
    registry_artifacts = registry.get("artifacts")
    first_name, first_relative = _artifact_layout()[0]
    if not isinstance(registry_artifacts, list):
        raise S104TrainingError("recoverable registry artifacts are absent")
    matching = [
        value
        for value in registry_artifacts
        if isinstance(value, Mapping) and value.get("name") == first_name
    ]
    if len(matching) != 1 or not isinstance(matching[0].get("path"), str):
        raise S104TrainingError("recoverable generation locator is invalid")
    first_path = _resolved_config_path(
        workspace_root,
        matching[0]["path"],
    )
    generation_path = first_path
    for _ in Path(first_relative).parts:
        generation_path = generation_path.parent
    if generation_path.parent != generation_root or not _is_sha256(
        generation_path.name
    ):
        raise S104TrainingError("recoverable generation path differs")
    data = cast(Mapping[str, Any], config["data"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    lock_data = cast(Mapping[str, Any], execution_lock["data_contract"])
    primary_rows = cast(
        Mapping[str, Any],
        lock_data["primary_split_rows"],
    )
    evaluation = cast(Mapping[str, Any], config["evaluation"])
    engineering = cast(
        Mapping[str, Any],
        evaluation["engineering_thresholds"],
    )
    validate_generated_artifact_tree(
        generation_path,
        allowed_l1={str(item) for item in scope["l1"]},
        allowed_gender={str(item) for item in scope["gender"]},
        expected_fit_utterances=int(lock_data["inner_train_rows"]),
        expected_dev_utterances=int(primary_rows["dev"]),
        engineering_phone_threshold=float(engineering["phone_macro_f1"]),
        engineering_icc_threshold=float(engineering["primary_utterance_icc_2_1"]),
        selected_candidate=cast(
            Mapping[str, Any],
            cast(Mapping[str, Any], config["candidates"])[
                str(
                    cast(
                        Mapping[str, Any],
                        execution_lock["selected_candidate"],
                    )["variant"]
                )
            ],
        ),
        adapter_config=cast(Mapping[str, Any], config["adapter"]),
        heads_config=cast(Mapping[str, Any], config["heads"]),
        expected_dev_truth=_load_expected_dev_truth(
            workspace_root=workspace_root,
            config=config,
        ),
        expected_fit_metadata=_load_expected_fit_metadata(
            workspace_root=workspace_root,
            config=config,
        ),
    )
    records = _artifact_records(generation_path)
    generation_id = _generation_id(records)
    if generation_path.name != generation_id:
        raise S104TrainingError("recoverable generation identity differs")
    _validate_registry_document(
        workspace_root=workspace_root,
        config=config,
        config_path=config_path,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        registry_path=registry_path,
        generation_path=generation_path,
        artifact_records=records,
    )
    recovered = PublishedGeneration(
        generation_id=generation_id,
        generation_path=generation_path,
        artifacts=records,
        created=False,
    )
    _validate_precommit_evidence(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        expected_execution_lock_sha256=(expected_execution_lock_sha256),
        generation=recovered,
        registry_path=registry_path,
    )
    commit = _build_commit_payload(
        workspace_root=workspace_root,
        generation=recovered,
        registry_path=registry_path,
        execution_lock_path=execution_lock_path,
        expected_execution_lock_sha256=expected_execution_lock_sha256,
    )
    publish_immutable_json(commit_path, commit)
    validate_s104_commit(
        workspace_root=workspace_root,
        config_path=config_path,
    )
    return commit
