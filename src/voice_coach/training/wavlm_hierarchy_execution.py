"""Production online all-layer execution and commit gating for WP4 S105.

The five preregistered hierarchy heads share one frozen, same-seed S104
encoder forward for every streamed utterance.  Only train/dev metadata is
scanned; raw audio is resolved after all scope and split guards pass.  A
generation is visible only after deep validation, content-addressed rename,
five completed variant registries, and one immutable commit pointer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import time
import fcntl
from typing import Any, Literal, cast
from uuid import uuid4

import numpy as np
import pyarrow.dataset as ds  # type: ignore[import-untyped]
from safetensors import safe_open
from safetensors.torch import save_file as save_safetensors
import torch
from torch.nn import functional as torch_functional

from voice_coach.data.audio import decode_audio, make_label_aware_chunks
from voice_coach.data.intervals import Interval as AudioInterval
from voice_coach.evaluation.metrics import icc_2_1
from voice_coach.experiments.registry import ExperimentRun
from voice_coach.features.ssl import (
    WAVLM_BASE_PLUS_FRAME_GEOMETRY,
    prepare_wavlm_input,
)
from voice_coach.models.wavlm_adapter import AdapterTunedWavLM
from voice_coach.models.wavlm_hierarchy import IncrementalHierarchyHead, VARIANT_ORDER
from voice_coach.training.wavlm_adapter_training import (
    AudioIntegrityVerifier,
    FitMetadata,
    UtteranceLabel,
    _device_metadata,
    _load_trainable_state,
    _peak_rss_bytes,
    _safe_json,
    atomic_write_json,
    bounded_shuffle,
    build_adapter_model,
    hash_utterance_id,
    load_reserved_prompt_hashes,
    load_utterance_labels,
    local_snapshot_paths,
    resolve_audio_path,
    scan_fit_metadata,
    select_phone_threshold,
    set_deterministic_seed,
    slice_metrics_from_prediction_files,
    speaker_bucket,
)
from voice_coach.training.wavlm_hierarchy_contract import (
    EXECUTION_LOCK_SCHEMA_VERSION,
    PROGRESS_EVENT_SCHEMA_VERSION,
    ZERO_TARGET_PHONE_POLICY,
    load_config,
    load_json_strict,
    sha256_file,
    validate_config,
    validate_implementation_sources,
)
from voice_coach.training.wavlm_hierarchy_training import (
    CHECKPOINT_SCHEMA_VERSION,
    COMMIT_SCHEMA_VERSION,
    PHONE_PREDICTION_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    SEEDS,
    TASKS,
    UTTERANCE_PREDICTION_SCHEMA_VERSION,
    DeepValidationContext,
    DevPassLedger,
    GenerationPublicationDurabilityError,
    HierarchyPredictionWriters,
    HierarchyTrainingExample,
    S105TrainingError,
    artifact_records,
    assert_head_only_trainable,
    build_deep_validation_context,
    build_variant_report,
    canonical_json_bytes,
    expected_artifact_layout,
    joint_hierarchy_loss,
    map_phones_to_words,
    publish_content_addressed_generation,
    recompute_ablation_comparison,
    render_ablation_table,
    validate_generation,
    validate_s105_commit,
)


Subset = Literal["fit", "inner_validation", "dev"]
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
    "word_intervals",
    "target_phone_intervals",
    "phone_alignments",
)
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
        "artifact_validation",
        "artifact_publication",
        "registry_registration",
        "commit_publication",
    }
)
GenerationBuilder = Callable[[Path, DeepValidationContext], None]
FaultInjector = Callable[[str], None]

#: Progress goes to stdout as JSON lines, never into the generation tree: the
#: artifact set is fixed at 82 files and a commit must not depend on how often a
#: run happened to log. Only counts, losses, metrics and timings are emitted —
#: no utterance id, speaker id, filesystem path, or split payload.
_PROGRESS_FORBIDDEN_FIELDS = frozenset(
    {
        "audio_path",
        "dataset_root",
        "path",
        "pcm_sha256",
        "speaker_id_sha256",
        "utterance_id_sha256",
    }
)


def _emit_progress(event: str, /, **fields: Any) -> None:
    """Write one canonical progress line to stdout and flush it.

    Flushing matters: without it a multi-hour first epoch buffers its heartbeat
    and the run looks identical to a hang.
    """

    leaked = _PROGRESS_FORBIDDEN_FIELDS.intersection(fields)
    if leaked:
        raise S105TrainingError("progress event would leak a protected field")
    payload = {
        "schema_version": PROGRESS_EVENT_SCHEMA_VERSION,
        "event": event,
        **fields,
    }
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False),
        flush=True,
    )


@dataclass(frozen=True, slots=True)
class ProgressLoggingContract:
    """Declared progress-logging behaviour, resolved from the config."""

    heartbeat_utterances: int
    emit_epoch_summary: bool

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ProgressLoggingContract:
        runtime = cast(Mapping[str, Any], config["runtime"])
        declared = cast(Mapping[str, Any], runtime["progress_logging"])
        if declared.get("destination") != "stdout_json_lines":
            raise S105TrainingError("progress logging destination is not declared")
        if declared.get("schema_version") != PROGRESS_EVENT_SCHEMA_VERSION:
            raise S105TrainingError("progress event schema version differs")
        interval = declared.get("heartbeat_utterances")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            raise S105TrainingError("heartbeat_utterances must be a positive integer")
        summary = declared.get("emit_epoch_summary")
        if not isinstance(summary, bool):
            raise S105TrainingError("emit_epoch_summary must be a boolean")
        return cls(heartbeat_utterances=interval, emit_epoch_summary=summary)


@dataclass(frozen=True, slots=True)
class WordInterval:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class HierarchyOnlineExample:
    utterance_id_sha256: str
    speaker_id_sha256: str
    split: str
    audio_path: Path
    pcm_sha256: str
    phone_intervals: tuple[AudioInterval, ...]
    word_intervals: tuple[WordInterval, ...]
    phone_to_word: tuple[int, ...]
    binary_labels: tuple[int, ...]
    target_indices: tuple[int, ...]
    l1: str
    gender: str
    proficiency: str
    scores: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class PooledHierarchy:
    phone_layers: torch.Tensor
    word_layers: torch.Tensor
    utterance_layers: torch.Tensor
    phone_frame_counts: tuple[int, ...]
    word_frame_counts: tuple[int, ...]
    utterance_frame_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class HeadSelection:
    best_epoch: int
    threshold: float
    phone_macro_f1: float
    comprehensibility_icc_2_1: float | None
    stopped_early: bool
    history: tuple[Mapping[str, Any], ...]
    state: Mapping[str, torch.Tensor]


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _resolve_existing(workspace_root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise S105TrainingError(f"{name} is not a path")
    raw = Path(value)
    if any(part.casefold().startswith(".env") for part in raw.parts):
        raise S105TrainingError(f"{name} references an environment file")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical.relative_to(workspace_root)
        _assert_no_symlink_chain(lexical)
        path = lexical.resolve(strict=True)
        path.relative_to(workspace_root)
    except ValueError as error:
        raise S105TrainingError(f"{name} escapes the workspace") from error
    return path


def _resolve_output(workspace_root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise S105TrainingError(f"{name} is not a path")
    raw = Path(value)
    if any(part.casefold().startswith(".env") for part in raw.parts):
        raise S105TrainingError(f"{name} references an environment file")
    candidate = raw if raw.is_absolute() else workspace_root / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        relative = lexical.relative_to(workspace_root)
    except ValueError as error:
        raise S105TrainingError(f"{name} escapes the workspace") from error
    if any(
        part.casefold()
        in {"stack", "calibration", "locked", "locked_test", "locked-test"}
        for part in relative.parts
    ):
        raise S105TrainingError(f"{name} enters a protected output path")
    _assert_no_symlink_chain(lexical)
    path = lexical.resolve(strict=False)
    try:
        path.relative_to(workspace_root)
    except ValueError as error:
        raise S105TrainingError(f"{name} resolves outside the workspace") from error
    return path


def _assert_no_symlink_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise S105TrainingError("S105 output path contains a symlink component")


def _manifest_rows(path: Path, *, split: str) -> Iterator[Mapping[str, Any]]:
    if split not in {"train", "dev"}:
        raise S105TrainingError("manifest split is forbidden")
    scanner = ds.dataset(path, format="parquet").scanner(
        columns=list(MANIFEST_COLUMNS),
        filter=ds.field("split") == split,
        batch_size=1_024,
        use_threads=False,
    )
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            if row.get("split") != split:
                raise S105TrainingError("protected split escaped manifest scanner")
            yield row


def _clean_word_intervals(value: object) -> tuple[WordInterval, ...]:
    if not isinstance(value, list):
        raise S105TrainingError("word intervals are not a list")
    intervals: list[WordInterval] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise S105TrainingError("word interval is invalid")
        start = item.get("start")
        end = item.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
        ):
            raise S105TrainingError("word interval bounds are invalid")
        interval = WordInterval(start=float(start), end=float(end))
        if (
            not math.isfinite(interval.start)
            or not math.isfinite(interval.end)
            or interval.start < 0
            or interval.end <= interval.start
        ):
            raise S105TrainingError("word interval bounds are invalid")
        intervals.append(interval)
    if intervals != sorted(intervals, key=lambda item: (item.start, item.end)):
        raise S105TrainingError("word intervals are not ordered")
    return tuple(intervals)


def _clean_phone_targets(
    intervals_value: object,
    alignments_value: object,
) -> tuple[tuple[AudioInterval, ...], tuple[int, ...]]:
    # Keep this implementation local so the S105 source pin binds the exact
    # target/interval contract rather than relying on an unexported helper.
    if not isinstance(intervals_value, list) or not isinstance(
        alignments_value,
        list,
    ):
        raise S105TrainingError("phone annotation is not a list")
    intervals: list[AudioInterval] = []
    for item in intervals_value:
        if not isinstance(item, Mapping):
            raise S105TrainingError("phone interval is invalid")
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        start = item.get("start")
        end = item.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or isinstance(end, bool)
            or not isinstance(end, (int, float))
        ):
            raise S105TrainingError("phone interval bounds are invalid")
        interval = AudioInterval(start=float(start), end=float(end), label=label)
        if (
            not math.isfinite(interval.start)
            or not math.isfinite(interval.end)
            or interval.start < 0
            or interval.end <= interval.start
        ):
            raise S105TrainingError("phone interval bounds are invalid")
        intervals.append(interval)
    if intervals != sorted(intervals, key=lambda item: (item.start, item.end)):
        raise S105TrainingError("phone intervals are not ordered")
    target_bearing = [
        item
        for item in alignments_value
        if isinstance(item, Mapping) and item.get("target_label") is not None
    ]
    if len(target_bearing) != len(intervals):
        raise S105TrainingError("phone alignment cardinality differs")
    binary: list[int] = []
    for interval, alignment in zip(intervals, target_bearing, strict=True):
        if str(alignment.get("target_label")) != interval.label:
            raise S105TrainingError("phone alignment target differs")
        operation = alignment.get("operation")
        if operation == "match":
            binary.append(0)
        elif operation in {"substitution", "deletion"}:
            binary.append(1)
        else:
            raise S105TrainingError("phone alignment operation is invalid")
    # An empty target-phone set is returned rather than raised: the caller
    # applies the declared zero-target-phone policy, which keeps the decision in
    # the contract instead of hiding it in this validator.
    return tuple(intervals), tuple(binary)


def count_zero_target_phone_utterances(
    *,
    manifest_path: Path,
    labels: Mapping[str, UtteranceLabel],
    reserved_prompts: set[str],
    modulus: int,
    validation_buckets: set[int],
    allowed_l1: set[str],
    allowed_gender: set[str],
) -> Mapping[str, int]:
    """Count guarded train rows that carry no target phone, per inner subset.

    Runs before training on the manifest alone — no audio, no model — so the
    declared exclusion count can be checked against the data before the first
    optimizer step rather than discovered mid-epoch.
    """

    counts = {"fit": 0, "inner_validation": 0, "dev": 0}
    for split in ("train", "dev"):
        for row in _manifest_rows(manifest_path, split=split):
            if row.get("qc_flags"):
                continue
            if row.get("prompt_sha256") in reserved_prompts:
                continue
            if (
                str(row.get("l1") or "") not in allowed_l1
                or str(row.get("gender") or "") not in allowed_gender
            ):
                continue
            speaker = row.get("speaker_id")
            if not _is_sha256(speaker):
                raise S105TrainingError("manifest speaker hash is invalid")
            identifier = hash_utterance_id(row.get("id"))
            label = labels.get(identifier)
            if (
                label is None
                or label.speaker_id_sha256 != speaker
                or label.split != split
            ):
                raise S105TrainingError("manifest/explicit-label join differs")
            phones, _ = _clean_phone_targets(
                row.get("target_phone_intervals"),
                row.get("phone_alignments"),
            )
            if phones:
                continue
            if split == "dev":
                counts["dev"] += 1
                continue
            counts[
                "inner_validation"
                if speaker_bucket(str(speaker), modulus) in validation_buckets
                else "fit"
            ] += 1
    return counts


def iter_hierarchy_examples(
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
    zero_target_phone_policy: str,
    excluded_zero_target_phone: set[str] | None = None,
    shuffle_buffer: int | None = None,
    shuffle_seed: int | None = None,
) -> Iterator[HierarchyOnlineExample]:
    """Stream a guarded subset and resolve raw audio only as the final step."""

    if zero_target_phone_policy != ZERO_TARGET_PHONE_POLICY:
        raise S105TrainingError("zero-target-phone policy is not the declared policy")

    split = "dev" if subset == "dev" else "train"
    rows: Iterable[Mapping[str, Any]] = _manifest_rows(
        manifest_path,
        split=split,
    )
    if shuffle_buffer is not None:
        if subset != "fit" or shuffle_seed is None:
            raise S105TrainingError("only train-fit may use bounded shuffling")
        rows = bounded_shuffle(
            rows,
            buffer_size=shuffle_buffer,
            seed=shuffle_seed,
        )
    unknown = target_vocabulary.get("<UNK>")
    if unknown is None:
        raise S105TrainingError("target vocabulary lacks <UNK>")
    for row in rows:
        if row.get("qc_flags"):
            continue
        prompt = row.get("prompt_sha256")
        if not _is_sha256(prompt):
            raise S105TrainingError("manifest prompt hash is invalid")
        if prompt in reserved_prompts:
            continue
        l1 = str(row.get("l1") or "")
        gender = str(row.get("gender") or "")
        if l1 not in allowed_l1 or gender not in allowed_gender:
            continue
        speaker = row.get("speaker_id")
        if not _is_sha256(speaker):
            raise S105TrainingError("manifest speaker hash is invalid")
        in_validation = speaker_bucket(str(speaker), modulus) in validation_buckets
        if subset == "fit" and in_validation:
            continue
        if subset == "inner_validation" and not in_validation:
            continue
        identifier = hash_utterance_id(row.get("id"))
        label = labels.get(identifier)
        if label is None or label.speaker_id_sha256 != speaker or label.split != split:
            raise S105TrainingError("manifest/explicit-label join differs")
        pcm = row.get("pcm_sha256")
        if not _is_sha256(pcm):
            raise S105TrainingError("manifest PCM fingerprint is invalid")
        phones, binary = _clean_phone_targets(
            row.get("target_phone_intervals"),
            row.get("phone_alignments"),
        )
        if not phones:
            # Excluded before any raw-audio path is resolved, so a degenerate row
            # never causes an R0 read.
            if excluded_zero_target_phone is not None:
                excluded_zero_target_phone.add(identifier)
            continue
        words = _clean_word_intervals(row.get("word_intervals"))
        grouping = map_phones_to_words(
            [{"start": interval.start, "end": interval.end} for interval in phones],
            [{"start": interval.start, "end": interval.end} for interval in words],
        )
        target_indices = tuple(
            target_vocabulary.get(interval.label, unknown) for interval in phones
        )
        # This must remain the final operation: rejected/protected rows never
        # cause an R0 raw-audio path to be resolved or opened.
        audio_path = resolve_audio_path(dataset_root, row.get("audio_path"))
        yield HierarchyOnlineExample(
            utterance_id_sha256=identifier,
            speaker_id_sha256=str(speaker),
            split=split,
            audio_path=audio_path,
            pcm_sha256=str(pcm),
            phone_intervals=phones,
            word_intervals=words,
            phone_to_word=grouping,
            binary_labels=binary,
            target_indices=target_indices,
            l1=l1,
            gender=gender,
            proficiency=str(row.get("proficiency") or ""),
            scores=label.scores,
        )


def pool_all_encoder_layers(
    *,
    model: AdapterTunedWavLM,
    example: HierarchyOnlineExample,
    device: torch.device,
    do_normalize: bool,
    maximum_chunk_seconds: float,
    encoder_layer_count: int,
    use_bfloat16: bool,
    audio_integrity: AudioIntegrityVerifier,
) -> PooledHierarchy:
    """Pool every adapted encoder layer for phone, word, and utterance nodes."""

    audio_integrity.verify(example.audio_path, example.pcm_sha256)
    audio = decode_audio(example.audio_path, target_sample_rate=16_000)
    audio_integrity.verify(example.audio_path, example.pcm_sha256)
    tolerance = 1 / 16_000 + 1e-6
    if any(
        interval.end > audio.duration_seconds + tolerance
        for interval in example.phone_intervals
    ) or any(
        interval.end > audio.duration_seconds + tolerance
        for interval in example.word_intervals
    ):
        raise S105TrainingError("hierarchy interval exceeds decoded audio")
    phones = tuple(
        AudioInterval(
            start=min(item.start, audio.duration_seconds),
            end=min(item.end, audio.duration_seconds),
            label=item.label,
        )
        for item in example.phone_intervals
    )
    words = tuple(
        WordInterval(
            start=min(item.start, audio.duration_seconds),
            end=min(item.end, audio.duration_seconds),
        )
        for item in example.word_intervals
    )
    chunks = make_label_aware_chunks(
        audio.duration_seconds,
        phones,
        max_duration_seconds=maximum_chunk_seconds,
    )
    utterance_sum: torch.Tensor | None = None
    utterance_count = 0
    phone_sums: list[torch.Tensor | None] = [None] * len(phones)
    phone_counts = [0] * len(phones)
    word_sums: list[torch.Tensor | None] = [None] * len(words)
    word_counts = [0] * len(words)
    receptive_field = WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
    for chunk in chunks:
        start_sample = round(chunk.start_seconds * 16_000)
        end_sample = round(chunk.end_seconds * 16_000)
        values = prepare_wavlm_input(
            torch.from_numpy(audio.samples[start_sample:end_sample]),
            do_normalize=do_normalize,
        )
        actual_samples = int(values.numel())
        if actual_samples < receptive_field:
            values = torch_functional.pad(
                values,
                (0, receptive_field - actual_samples),
            )
        input_samples = int(values.numel())
        attention_mask = torch.zeros(
            (1, input_samples),
            dtype=torch.long,
            device=device,
        )
        attention_mask[:, :input_samples] = 1
        autocast = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if device.type == "cuda" and use_bfloat16
            else nullcontext()
        )
        with torch.no_grad(), autocast:
            output = model.wavlm(
                input_values=values.unsqueeze(0).to(device=device),
                attention_mask=attention_mask,
                output_hidden_states=True,
                return_dict=True,
            )
        hidden_states = getattr(output, "hidden_states", None)
        if (
            not isinstance(hidden_states, tuple)
            or len(hidden_states) != encoder_layer_count + 1
        ):
            raise S105TrainingError("WavLM all-layer output count differs")
        layers = torch.stack(
            [value[0].to(dtype=torch.float32) for value in hidden_states[1:]],
            dim=0,
        )
        expected_frames = WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_samples
        )
        if (
            layers.ndim != 3
            or layers.shape[0] != encoder_layer_count
            or layers.shape[1] != expected_frames
            or not bool(torch.isfinite(layers).all())
        ):
            raise S105TrainingError("WavLM all-layer frame geometry differs")
        chunk_sum = layers.sum(dim=1)
        utterance_sum = (
            chunk_sum if utterance_sum is None else utterance_sum + chunk_sum
        )
        utterance_count += int(layers.shape[1])

        def accumulate(
            intervals: Sequence[Any],
            sums: list[torch.Tensor | None],
            counts: list[int],
        ) -> None:
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
                if first < 0 or stop <= first or stop > layers.shape[1]:
                    raise S105TrainingError("hierarchy interval frame span is invalid")
                value = layers[:, first:stop].sum(dim=1)
                sums[index] = (
                    value
                    if sums[index] is None
                    else cast(torch.Tensor, sums[index]) + value
                )
                counts[index] += stop - first

        accumulate(phones, phone_sums, phone_counts)
        accumulate(words, word_sums, word_counts)
    if utterance_sum is None or utterance_count <= 0:
        raise S105TrainingError("utterance received no WavLM frame")
    if any(
        value is None or count <= 0
        for value, count in zip(phone_sums, phone_counts, strict=True)
    ):
        raise S105TrainingError("phone interval received no WavLM frame")
    if any(
        value is None or count <= 0
        for value, count in zip(word_sums, word_counts, strict=True)
    ):
        raise S105TrainingError("word interval received no WavLM frame")
    phone_layers = torch.stack(
        [
            cast(torch.Tensor, value) / count
            for value, count in zip(phone_sums, phone_counts, strict=True)
        ],
        dim=1,
    )
    if word_sums:
        word_layers = torch.stack(
            [
                cast(torch.Tensor, value) / count
                for value, count in zip(word_sums, word_counts, strict=True)
            ],
            dim=1,
        )
    else:
        word_layers = utterance_sum.new_empty(
            (encoder_layer_count, 0, utterance_sum.shape[-1])
        )
    return PooledHierarchy(
        phone_layers=phone_layers,
        word_layers=word_layers,
        utterance_layers=utterance_sum / utterance_count,
        phone_frame_counts=tuple(phone_counts),
        word_frame_counts=tuple(word_counts),
        utterance_frame_count=utterance_count,
        chunk_count=len(chunks),
    )


def _windows(
    values: Iterable[HierarchyOnlineExample],
    size: int,
) -> Iterator[list[HierarchyOnlineExample]]:
    if size <= 0:
        raise S105TrainingError("gradient accumulation size must be positive")
    window: list[HierarchyOnlineExample] = []
    for value in values:
        window.append(value)
        if len(window) == size:
            yield window
            window = []
    if window:
        yield window


def _state(model: IncrementalHierarchyHead) -> Mapping[str, torch.Tensor]:
    return {
        name: tensor.detach().cpu().clone()
        for name, tensor in model.state_dict().items()
    }


def _load_state(
    model: IncrementalHierarchyHead,
    state: Mapping[str, torch.Tensor],
) -> None:
    if set(state) != set(model.state_dict()):
        raise S105TrainingError("hierarchy head state allowlist differs")
    model.load_state_dict(dict(state), strict=True)


def _training_example(
    example: HierarchyOnlineExample,
    pooled: PooledHierarchy,
    *,
    device: torch.device,
) -> HierarchyTrainingExample:
    return HierarchyTrainingExample(
        utterance_id_sha256=example.utterance_id_sha256,
        speaker_id_sha256=example.speaker_id_sha256,
        split=example.split,
        phone_layers=pooled.phone_layers,
        word_layers=pooled.word_layers,
        utterance_layers=pooled.utterance_layers,
        phone_to_word=torch.tensor(
            example.phone_to_word,
            dtype=torch.long,
            device=device,
        ),
        target_indices=torch.tensor(
            example.target_indices,
            dtype=torch.long,
            device=device,
        ),
        phone_targets=torch.tensor(
            example.binary_labels,
            dtype=torch.float32,
            device=device,
        ),
        utterance_targets=torch.tensor(
            example.scores,
            dtype=torch.float32,
            device=device,
        ),
    )


def _better(
    *,
    phone: float,
    icc: float | None,
    best_phone: float,
    best_icc: float | None,
) -> bool:
    tolerance = 1e-12
    if phone > best_phone + tolerance:
        return True
    if abs(phone - best_phone) > tolerance:
        return False
    if icc is None:
        return False
    if best_icc is None:
        return True
    return icc > best_icc + tolerance


def _threshold_grid(config: Mapping[str, Any]) -> np.ndarray:
    optimization = cast(Mapping[str, Any], config["optimization"])
    values = cast(Mapping[str, Any], optimization["phone_threshold_grid"])
    start = float(values["start"])
    stop = float(values["stop"])
    step = float(values["step"])
    count = int(round((stop - start) / step))
    grid = start + step * np.arange(count + 1, dtype=np.float64)
    if (
        grid.size <= 0
        or not np.isclose(grid[-1], stop)
        or np.any((grid < 0) | (grid > 1))
    ):
        raise S105TrainingError("phone threshold grid is invalid")
    return grid


def _evaluate_inner_shared(
    *,
    heads: Mapping[str, IncrementalHierarchyHead],
    active: set[str],
    examples: Iterable[HierarchyOnlineExample],
    encoder: AdapterTunedWavLM,
    device: torch.device,
    selected_candidate: Mapping[str, Any],
    maximum_chunk_seconds: float,
    audio_integrity: AudioIntegrityVerifier,
) -> tuple[Mapping[str, Mapping[str, Any]], int]:
    targets: list[int] = []
    scores: list[tuple[float, float, float]] = []
    probabilities: dict[str, list[float]] = {variant: [] for variant in active}
    predictions: dict[str, list[tuple[float, float, float]]] = {
        variant: [] for variant in active
    }
    for variant in active:
        heads[variant].eval()
    utterances = 0
    with torch.no_grad():
        for example in examples:
            pooled = pool_all_encoder_layers(
                model=encoder,
                example=example,
                device=device,
                do_normalize=bool(selected_candidate["do_normalize"]),
                maximum_chunk_seconds=maximum_chunk_seconds,
                encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
                use_bfloat16=True,
                audio_integrity=audio_integrity,
            )
            training = _training_example(example, pooled, device=device)
            targets.extend(example.binary_labels)
            scores.append(example.scores)
            for variant in active:
                output = heads[variant](
                    phone_layers=training.phone_layers,
                    word_layers=training.word_layers,
                    utterance_layers=training.utterance_layers,
                    phone_to_word=training.phone_to_word,
                    target_indices=training.target_indices,
                )
                probabilities[variant].extend(
                    float(value)
                    for value in torch.sigmoid(output.phone_logits)
                    .to(device="cpu", dtype=torch.float32)
                    .tolist()
                )
                predictions[variant].append(
                    cast(
                        tuple[float, float, float],
                        tuple(
                            float(value)
                            for value in output.utterance_scores.to(
                                device="cpu",
                                dtype=torch.float32,
                            ).tolist()
                        ),
                    )
                )
            utterances += 1
    if utterances <= 0 or not targets:
        raise S105TrainingError("inner-validation stream is empty")
    target_array = np.asarray(targets, dtype=np.int8)
    score_array = np.asarray(scores, dtype=np.float32)
    output_values: dict[str, Mapping[str, Any]] = {}
    for variant in active:
        probability_array = np.asarray(probabilities[variant], dtype=np.float32)
        if probability_array.shape != target_array.shape:
            raise S105TrainingError("inner phone prediction shape differs")
        prediction_array = np.asarray(predictions[variant], dtype=np.float32)
        observed = np.isfinite(score_array[:, 2])
        icc_value = (
            icc_2_1(
                np.column_stack(
                    (score_array[observed, 2], prediction_array[observed, 2])
                )
            )
            if int(observed.sum()) >= 2
            else float("nan")
        )
        output_values[variant] = {
            "phone_targets": target_array,
            "phone_probabilities": probability_array,
            "comprehensibility_icc_2_1": (
                float(icc_value) if math.isfinite(icc_value) else None
            ),
        }
    return output_values, utterances


def train_and_select_all_heads(
    *,
    heads: Mapping[str, IncrementalHierarchyHead],
    encoder: AdapterTunedWavLM,
    config: Mapping[str, Any],
    seed: int,
    device: torch.device,
    metadata: FitMetadata,
    fit_examples_factory: Callable[[int], Iterable[HierarchyOnlineExample]],
    validation_examples_factory: Callable[[], Iterable[HierarchyOnlineExample]],
    selected_candidate: Mapping[str, Any],
    expected_fit_utterances: int,
    expected_validation_utterances: int,
    audio_integrity: AudioIntegrityVerifier,
) -> Mapping[str, HeadSelection]:
    """Train all variants with shared encoder scans and independent stopping."""

    optimization = cast(Mapping[str, Any], config["optimization"])
    optimizers = {
        variant: torch.optim.AdamW(
            head.parameters(),
            lr=float(optimization["learning_rate"]),
            weight_decay=float(optimization["weight_decay"]),
        )
        for variant, head in heads.items()
    }
    positive_weight = torch.tensor(
        metadata.pos_weight,
        dtype=torch.float32,
        device=device,
    )
    active = set(heads)
    best_state: dict[str, Mapping[str, torch.Tensor] | None] = {
        variant: None for variant in heads
    }
    best_epoch = {variant: 0 for variant in heads}
    best_threshold = {variant: float("nan") for variant in heads}
    best_phone = {variant: -math.inf for variant in heads}
    best_icc: dict[str, float | None] = {variant: None for variant in heads}
    histories: dict[str, list[Mapping[str, Any]]] = {variant: [] for variant in heads}
    no_improvement = {variant: 0 for variant in heads}
    stopped = {variant: False for variant in heads}
    maximum_epochs = int(optimization["maximum_epochs"])
    minimum_epochs = int(optimization["minimum_epochs"])
    patience = int(optimization["early_stopping_patience"])
    accumulation = int(optimization["gradient_accumulation_utterances"])
    thresholds = _threshold_grid(config)
    maximum_chunk_seconds = float(
        cast(Mapping[str, Any], config["runtime"])["maximum_chunk_seconds"]
    )
    progress = ProgressLoggingContract.from_config(config)
    for epoch in range(1, maximum_epochs + 1):
        if not active:
            break
        loss_totals = {variant: 0.0 for variant in active}
        optimizer_steps = {variant: 0 for variant in active}
        fit_count = 0
        epoch_started = time.monotonic()
        next_heartbeat = progress.heartbeat_utterances
        _emit_progress(
            "epoch_started",
            seed=seed,
            epoch=epoch,
            maximum_epochs=maximum_epochs,
            active_variant_count=len(active),
            fit_utterances_expected=expected_fit_utterances,
        )
        for window in _windows(fit_examples_factory(epoch), accumulation):
            for variant in active:
                heads[variant].train()
                optimizers[variant].zero_grad(set_to_none=True)
            for online in window:
                pooled = pool_all_encoder_layers(
                    model=encoder,
                    example=online,
                    device=device,
                    do_normalize=bool(selected_candidate["do_normalize"]),
                    maximum_chunk_seconds=maximum_chunk_seconds,
                    encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
                    use_bfloat16=True,
                    audio_integrity=audio_integrity,
                )
                training = _training_example(online, pooled, device=device)
                for variant in active:
                    head_output = heads[variant](
                        phone_layers=training.phone_layers,
                        word_layers=training.word_layers,
                        utterance_layers=training.utterance_layers,
                        phone_to_word=training.phone_to_word,
                        target_indices=training.target_indices,
                    )
                    losses = joint_hierarchy_loss(
                        head_output,
                        phone_targets=training.phone_targets,
                        utterance_targets=training.utterance_targets,
                        positive_weight=positive_weight,
                        phone_loss_weight=float(optimization["phone_loss_weight"]),
                        utterance_loss_weight=float(
                            optimization["utterance_loss_weight"]
                        ),
                        word_loss_weight=float(optimization["word_loss_weight"]),
                    )
                    (losses.total / len(window)).backward()
                    loss_totals[variant] += float(losses.total.detach().cpu())
                fit_count += 1
            for variant in active:
                norm = torch.nn.utils.clip_grad_norm_(
                    heads[variant].parameters(),
                    max_norm=float(optimization["gradient_clip_norm"]),
                    error_if_nonfinite=True,
                )
                if not bool(torch.isfinite(norm)):
                    raise S105TrainingError("hierarchy gradient norm is non-finite")
                optimizers[variant].step()
                if not all(
                    bool(torch.isfinite(parameter).all())
                    for parameter in heads[variant].parameters()
                ):
                    raise S105TrainingError("hierarchy parameter is non-finite")
                optimizer_steps[variant] += 1
            if fit_count >= next_heartbeat:
                elapsed = time.monotonic() - epoch_started
                _emit_progress(
                    "fit_heartbeat",
                    seed=seed,
                    epoch=epoch,
                    fit_utterances_done=fit_count,
                    fit_utterances_expected=expected_fit_utterances,
                    fraction_of_epoch=round(
                        fit_count / expected_fit_utterances, 6
                    ),
                    elapsed_seconds=round(elapsed, 1),
                    utterances_per_second=round(fit_count / elapsed, 3)
                    if elapsed > 0
                    else None,
                    projected_epoch_seconds=round(
                        elapsed * expected_fit_utterances / fit_count, 1
                    )
                    if fit_count
                    else None,
                    peak_gpu_bytes=int(torch.cuda.max_memory_allocated()),
                )
                next_heartbeat = fit_count + progress.heartbeat_utterances
        if fit_count != expected_fit_utterances:
            raise S105TrainingError("train-fit epoch cardinality changed")
        _emit_progress(
            "fit_epoch_finished",
            seed=seed,
            epoch=epoch,
            fit_utterances_done=fit_count,
            elapsed_seconds=round(time.monotonic() - epoch_started, 1),
            mean_joint_loss_by_variant={
                variant: round(loss_totals[variant] / fit_count, 6)
                for variant in sorted(active)
            },
            optimizer_steps_by_variant={
                variant: optimizer_steps[variant] for variant in sorted(active)
            },
        )
        validation, validation_count = _evaluate_inner_shared_with_grid(
            heads=heads,
            active=active,
            examples=validation_examples_factory(),
            encoder=encoder,
            device=device,
            selected_candidate=selected_candidate,
            maximum_chunk_seconds=maximum_chunk_seconds,
            audio_integrity=audio_integrity,
            thresholds=thresholds,
        )
        if validation_count != expected_validation_utterances:
            raise S105TrainingError(
                "inner-validation cardinality differs from execution lock"
            )
        completed: set[str] = set()
        for variant in sorted(active):
            result = validation[variant]
            phone = float(result["phone_macro_f1"])
            threshold = float(result["phone_decision_threshold"])
            icc = cast(float | None, result["comprehensibility_icc_2_1"])
            improved = _better(
                phone=phone,
                icc=icc,
                best_phone=best_phone[variant],
                best_icc=best_icc[variant],
            )
            if improved:
                best_state[variant] = _state(heads[variant])
                best_epoch[variant] = epoch
                best_threshold[variant] = threshold
                best_phone[variant] = phone
                best_icc[variant] = icc
                no_improvement[variant] = 0
            else:
                no_improvement[variant] += 1
            histories[variant].append(
                {
                    "epoch": epoch,
                    "train_mean_joint_loss": loss_totals[variant] / fit_count,
                    "optimizer_steps": optimizer_steps[variant],
                    "inner_phone_macro_f1": phone,
                    "inner_phone_threshold": threshold,
                    "inner_comprehensibility_icc_2_1": icc,
                    "inner_comprehensibility_icc_defined": icc is not None,
                    "selected_as_best": improved,
                }
            )
            if epoch >= minimum_epochs and no_improvement[variant] >= patience:
                stopped[variant] = True
                completed.add(variant)
        if progress.emit_epoch_summary:
            _emit_progress(
                "epoch_summary",
                seed=seed,
                epoch=epoch,
                inner_validation_utterances=validation_count,
                inner_phone_macro_f1_by_variant={
                    variant: round(float(validation[variant]["phone_macro_f1"]), 6)
                    for variant in sorted(active)
                },
                best_epoch_by_variant={
                    variant: best_epoch[variant] for variant in sorted(active)
                },
                epochs_without_improvement_by_variant={
                    variant: no_improvement[variant] for variant in sorted(active)
                },
                stopped_variants=sorted(completed),
                remaining_variant_count=len(active - completed),
            )
        active -= completed
    selections: dict[str, HeadSelection] = {}
    for variant, head in heads.items():
        state = best_state[variant]
        if (
            state is None
            or best_epoch[variant] <= 0
            or not math.isfinite(best_threshold[variant])
        ):
            raise S105TrainingError("inner selection did not produce a checkpoint")
        _load_state(head, state)
        selections[variant] = HeadSelection(
            best_epoch=best_epoch[variant],
            threshold=best_threshold[variant],
            phone_macro_f1=best_phone[variant],
            comprehensibility_icc_2_1=best_icc[variant],
            stopped_early=stopped[variant],
            history=tuple(histories[variant]),
            state=state,
        )
    return selections


def _evaluate_inner_shared_with_grid(
    *,
    heads: Mapping[str, IncrementalHierarchyHead],
    active: set[str],
    examples: Iterable[HierarchyOnlineExample],
    encoder: AdapterTunedWavLM,
    device: torch.device,
    selected_candidate: Mapping[str, Any],
    maximum_chunk_seconds: float,
    audio_integrity: AudioIntegrityVerifier,
    thresholds: np.ndarray,
) -> tuple[Mapping[str, Mapping[str, Any]], int]:
    raw, count = _evaluate_inner_shared(
        heads=heads,
        active=active,
        examples=examples,
        encoder=encoder,
        device=device,
        selected_candidate=selected_candidate,
        maximum_chunk_seconds=maximum_chunk_seconds,
        audio_integrity=audio_integrity,
    )
    output: dict[str, Mapping[str, Any]] = {}
    for variant, values in raw.items():
        threshold, phone = select_phone_threshold(
            cast(np.ndarray, values["phone_targets"]),
            cast(np.ndarray, values["phone_probabilities"]),
            thresholds,
        )
        output[variant] = {
            "phone_macro_f1": phone,
            "phone_decision_threshold": threshold,
            "comprehensibility_icc_2_1": values["comprehensibility_icc_2_1"],
        }
    return output, count


def evaluate_dev_shared(
    *,
    heads: Mapping[str, IncrementalHierarchyHead],
    selections: Mapping[str, HeadSelection],
    encoder: AdapterTunedWavLM,
    examples: Iterable[HierarchyOnlineExample],
    device: torch.device,
    selected_candidate: Mapping[str, Any],
    maximum_chunk_seconds: float,
    seed: int,
    writers: Mapping[str, HierarchyPredictionWriters],
    ledger: DevPassLedger,
    audio_integrity: AudioIntegrityVerifier,
) -> int:
    """One physical dev scanner/encoder pass feeds all five variants."""

    if set(heads) != set(VARIANT_ORDER) or set(writers) != set(VARIANT_ORDER):
        raise S105TrainingError("shared dev variant set differs")
    for variant in VARIANT_ORDER:
        heads[str(variant)].eval()
        for task in TASKS:
            ledger.enter(variant=str(variant), task=task, seed=seed)
    count = 0
    with torch.no_grad():
        for example in examples:
            pooled = pool_all_encoder_layers(
                model=encoder,
                example=example,
                device=device,
                do_normalize=bool(selected_candidate["do_normalize"]),
                maximum_chunk_seconds=maximum_chunk_seconds,
                encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
                use_bfloat16=True,
                audio_integrity=audio_integrity,
            )
            training = _training_example(example, pooled, device=device)
            for variant in VARIANT_ORDER:
                name = str(variant)
                output = heads[name](
                    phone_layers=training.phone_layers,
                    word_layers=training.word_layers,
                    utterance_layers=training.utterance_layers,
                    phone_to_word=training.phone_to_word,
                    target_indices=training.target_indices,
                )
                probabilities = (
                    torch.sigmoid(output.phone_logits)
                    .to(device="cpu", dtype=torch.float32)
                    .tolist()
                )
                threshold = selections[name].threshold
                for phone_index, (
                    interval,
                    binary,
                    probability,
                ) in enumerate(
                    zip(
                        example.phone_intervals,
                        example.binary_labels,
                        probabilities,
                        strict=True,
                    )
                ):
                    numeric = float(probability)
                    writers[name].add_phone(
                        {
                            "schema_version": PHONE_PREDICTION_SCHEMA_VERSION,
                            "variant": name,
                            "seed": seed,
                            "split": "dev",
                            "development_only": True,
                            "oracle_alignment": True,
                            "utterance_id_sha256": example.utterance_id_sha256,
                            "speaker_id_sha256": example.speaker_id_sha256,
                            "phone_index": phone_index,
                            "target_label": interval.label,
                            "binary_error_label": binary,
                            "error_probability": numeric,
                            "error_prediction": int(numeric >= threshold),
                            "l1": example.l1,
                            "gender": example.gender,
                            "proficiency": example.proficiency,
                        }
                    )
                predicted_scores = cast(
                    tuple[float, float, float],
                    tuple(
                        float(value)
                        for value in output.utterance_scores.to(
                            device="cpu",
                            dtype=torch.float32,
                        ).tolist()
                    ),
                )
                row: dict[str, Any] = {
                    "schema_version": UTTERANCE_PREDICTION_SCHEMA_VERSION,
                    "variant": name,
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
                for index, outcome in enumerate(
                    (
                        "pronunciation_score",
                        "fluency_score",
                        "comprehensibility_score",
                    )
                ):
                    observed = math.isfinite(example.scores[index])
                    row[f"{outcome}_true"] = example.scores[index] if observed else None
                    row[f"{outcome}_pred"] = predicted_scores[index]
                    row[f"{outcome}_observed"] = observed
                writers[name].add_utterance(row)
            count += 1
    if count <= 0:
        raise S105TrainingError("dev stream is empty")
    for variant in VARIANT_ORDER:
        for task in TASKS:
            ledger.complete(variant=str(variant), task=task, seed=seed)
    return count


def _head_audit(head: IncrementalHierarchyHead) -> Mapping[str, Any]:
    audit = assert_head_only_trainable(head)
    expected_names = [
        name for name, parameter in head.named_parameters() if parameter.requires_grad
    ]
    if audit["trainable_parameter_names"] != expected_names:
        raise S105TrainingError("hierarchy trainable allowlist order differs")
    return audit


def save_hierarchy_checkpoint(
    path: Path,
    *,
    head: IncrementalHierarchyHead,
    selection: HeadSelection,
    variant: str,
    seed: int,
    selected_candidate: Mapping[str, Any],
    metadata: FitMetadata,
    s104_checkpoint_sha256: str,
) -> Mapping[str, Any]:
    tensors = {
        name: tensor.detach().cpu().contiguous()
        for name, tensor in selection.state.items()
    }
    if set(tensors) != set(head.state_dict()) or any(
        not tensor.is_floating_point() or not bool(torch.isfinite(tensor).all())
        for tensor in tensors.values()
    ):
        raise S105TrainingError("hierarchy checkpoint state differs")
    save_safetensors(
        tensors,
        path,
        metadata={
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "variant": variant,
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
            "s104_checkpoint_sha256": s104_checkpoint_sha256,
            "word_loss_performed": "false",
        },
    )
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


def _build_heads(
    *,
    config: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
    metadata: FitMetadata,
    device: torch.device,
) -> Mapping[str, IncrementalHierarchyHead]:
    dimensions = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], config["variants"])["dimensions"],
    )
    heads: dict[str, IncrementalHierarchyHead] = {}
    for variant in VARIANT_ORDER:
        head = IncrementalHierarchyHead(
            variant=variant,
            hidden_size=int(selected_candidate["hidden_size"]),
            encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
            projection_dim=int(dimensions["projection_dim"]),
            target_vocabulary_size=len(metadata.target_vocabulary),
            target_embedding_dim=int(dimensions["phone_target_embedding_dim"]),
            attention_hidden_dim=int(dimensions["attention_hidden_dim"]),
            attention_aspects=int(dimensions["attention_aspects"]),
            dropout=float(dimensions["dropout"]),
            score_range=cast(Sequence[float], dimensions["score_range"]),
        ).to(device)
        _head_audit(head)
        heads[str(variant)] = head
    return heads


def _load_frozen_s104_encoder(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    execution_lock: Mapping[str, Any],
    metadata: FitMetadata,
    seed: int,
    device: torch.device,
) -> tuple[AdapterTunedWavLM, Mapping[str, Any], Path]:
    predecessor_config = cast(Mapping[str, Any], config["predecessor"])
    s104_config_path = _resolve_existing(
        workspace_root,
        predecessor_config["config"],
        "S104 config",
    )
    from voice_coach.training.wavlm_adapter_contract import (
        load_config as load_s104_config,
    )

    s104_config = load_s104_config(s104_config_path)
    predecessor = cast(Mapping[str, Any], execution_lock["predecessor"])
    selected = cast(Mapping[str, Any], predecessor["selected_candidate"])
    snapshot, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=workspace_root,
        selected_candidate=selected,
    )
    if (
        sha256_file(model_config_path) != selected["source_config_sha256"]
        or sha256_file(model_checkpoint_path) != selected["source_checkpoint_sha256"]
    ):
        raise S105TrainingError("selected local WavLM snapshot changed")
    encoder, _ = build_adapter_model(
        config=s104_config,
        selected_candidate=selected,
        metadata=metadata,
        snapshot=snapshot,
        device=device,
    )
    checkpoint_records = predecessor.get("same_seed_checkpoints")
    if not isinstance(checkpoint_records, list):
        raise S105TrainingError("S104 same-seed checkpoint list is absent")
    matches = [
        item
        for item in checkpoint_records
        if isinstance(item, Mapping) and item.get("seed") == seed
    ]
    if len(matches) != 1:
        raise S105TrainingError("S104 same-seed checkpoint binding differs")
    record = cast(Mapping[str, Any], matches[0])
    checkpoint = _resolve_existing(
        workspace_root,
        record["path"],
        "S104 same-seed checkpoint",
    )
    if (
        sha256_file(checkpoint) != record["sha256"]
        or checkpoint.stat().st_size != record["size_bytes"]
    ):
        raise S105TrainingError("S104 same-seed checkpoint changed")
    with safe_open(checkpoint, framework="pt") as handle:
        checkpoint_metadata = handle.metadata()
        state = {name: handle.get_tensor(name) for name in handle.keys()}
    if (
        checkpoint_metadata is None
        or checkpoint_metadata.get("seed") != str(seed)
        or checkpoint_metadata.get("model_id") != selected["model_id"]
        or checkpoint_metadata.get("revision") != selected["revision"]
        or json.loads(checkpoint_metadata["target_vocabulary_json"])
        != list(metadata.target_vocabulary)
    ):
        raise S105TrainingError("S104 checkpoint metadata differs")
    _load_trainable_state(encoder, state)
    for parameter in encoder.parameters():
        parameter.requires_grad_(False)
    encoder.eval()
    if any(parameter.requires_grad for parameter in encoder.parameters()):
        raise S105TrainingError("S104 encoder/adapter freeze failed")
    return encoder, selected, checkpoint


def _device() -> torch.device:
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise S105TrainingError("production S105 training requires BF16 CUDA")
    return torch.device("cuda")


def _runtime_environment(config: Mapping[str, Any]) -> None:
    runtime = cast(Mapping[str, Any], config["runtime"])
    expected = str(runtime["cublas_workspace_config"])
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != expected:
        raise S105TrainingError("CUBLAS_WORKSPACE_CONFIG differs")
    torch.use_deterministic_algorithms(True)


def build_online_generation(
    *,
    workspace_root: Path,
    temporary: Path,
    config: Mapping[str, Any],
    execution_lock: Mapping[str, Any],
    context: DeepValidationContext,
) -> None:
    """Run all five variants×three seeds under the preregistered contract."""

    del context
    _runtime_environment(config)
    device = _device()
    data = cast(Mapping[str, Any], config["data"])
    optimization = cast(Mapping[str, Any], config["optimization"])
    runtime = cast(Mapping[str, Any], config["runtime"])
    manifest_path = _resolve_existing(
        workspace_root,
        data["modeling_manifest"],
        "modeling manifest",
    )
    reservation_path = _resolve_existing(
        workspace_root,
        data["prompt_challenge_reservation"],
        "prompt reservation",
    )
    labels_path = _resolve_existing(
        workspace_root,
        data["explicit_utterance_labels"],
        "explicit labels",
    )
    dataset_root = _resolve_existing(
        workspace_root,
        data["dataset_root"],
        "restricted dataset root",
    )
    reserved = load_reserved_prompt_hashes(reservation_path)
    labels = load_utterance_labels(labels_path)
    inner = cast(Mapping[str, Any], data["inner_validation"])
    scope = cast(Mapping[str, Any], data["primary_analysis_scope"])
    modulus = int(inner["modulus"])
    validation_buckets = {
        int(value) for value in cast(Sequence[int], inner["validation_buckets"])
    }
    allowed_l1 = {str(value) for value in cast(Sequence[str], scope["l1"])}
    allowed_gender = {str(value) for value in cast(Sequence[str], scope["gender"])}
    metadata = scan_fit_metadata(
        manifest_path=manifest_path,
        reserved_prompts=reserved,
        modulus=modulus,
        validation_buckets=validation_buckets,
        allowed_l1=allowed_l1,
        allowed_gender=allowed_gender,
    )
    locked_data = cast(Mapping[str, Any], execution_lock["data"])
    if (
        metadata.fit_utterances != locked_data["inner_fit_utterances"]
        or metadata.fit_utterances <= 0
    ):
        raise S105TrainingError("fit metadata differs from execution lock")

    interval_mapping = cast(Mapping[str, Any], data["interval_mapping"])
    declared_exclusions = {
        str(subset): int(count)
        for subset, count in cast(
            Mapping[str, Any],
            interval_mapping["expected_zero_target_phone_exclusions"],
        ).items()
    }
    observed_exclusions = count_zero_target_phone_utterances(
        manifest_path=manifest_path,
        labels=labels,
        reserved_prompts=reserved,
        modulus=modulus,
        validation_buckets=validation_buckets,
        allowed_l1=allowed_l1,
        allowed_gender=allowed_gender,
    )
    if dict(observed_exclusions) != declared_exclusions:
        raise S105TrainingError(
            "zero-target-phone exclusion count differs from the declared contract"
        )
    excluded_identifiers: set[str] = set()
    vocabulary = {
        target: index for index, target in enumerate(metadata.target_vocabulary)
    }
    ledger = DevPassLedger()
    temporary.mkdir(parents=True, exist_ok=False)
    for variant in VARIANT_ORDER:
        for seed in SEEDS:
            (temporary / variant / f"seed_{seed}").mkdir(parents=True)
    run_started = time.monotonic()
    _emit_progress(
        "run_started",
        seeds=list(SEEDS),
        variants=[str(variant) for variant in VARIANT_ORDER],
        fit_utterances=metadata.fit_utterances - declared_exclusions["fit"],
        inner_validation_utterances=int(locked_data["inner_validation_utterances"])
        - declared_exclusions["inner_validation"],
        dev_utterances=cast(Mapping[str, Any], locked_data["split_utterances"])["dev"]
        - declared_exclusions["dev"],
        zero_target_phone_exclusions=dict(observed_exclusions),
        target_label_count=len(metadata.target_vocabulary),
    )
    for seed_position, seed in enumerate(SEEDS, start=1):
        seed_started = time.monotonic()
        _emit_progress(
            "seed_started",
            seed=seed,
            seed_position=seed_position,
            seed_count=len(SEEDS),
            elapsed_seconds=round(time.monotonic() - run_started, 1),
        )
        set_deterministic_seed(seed)
        torch.cuda.reset_peak_memory_stats()
        encoder, selected, s104_checkpoint = _load_frozen_s104_encoder(
            workspace_root=workspace_root,
            config=config,
            execution_lock=execution_lock,
            metadata=metadata,
            seed=seed,
            device=device,
        )
        heads = _build_heads(
            config=config,
            selected_candidate=selected,
            metadata=metadata,
            device=device,
        )
        integrity = AudioIntegrityVerifier()

        def examples(
            subset: Subset,
            *,
            epoch: int | None = None,
        ) -> Iterable[HierarchyOnlineExample]:
            return iter_hierarchy_examples(
                manifest_path=manifest_path,
                dataset_root=dataset_root,
                labels=labels,
                reserved_prompts=reserved,
                target_vocabulary=vocabulary,
                subset=subset,
                modulus=modulus,
                validation_buckets=validation_buckets,
                allowed_l1=allowed_l1,
                allowed_gender=allowed_gender,
                zero_target_phone_policy=str(
                    interval_mapping["zero_target_phone_utterance_policy"]
                ),
                excluded_zero_target_phone=excluded_identifiers,
                shuffle_buffer=(
                    int(optimization["bounded_shuffle_buffer_utterances"])
                    if subset == "fit"
                    else None
                ),
                shuffle_seed=(
                    seed * 100_000 + cast(int, epoch) if subset == "fit" else None
                ),
            )

        selections = train_and_select_all_heads(
            heads=heads,
            encoder=encoder,
            config=config,
            seed=seed,
            device=device,
            metadata=metadata,
            fit_examples_factory=lambda epoch: examples("fit", epoch=epoch),
            validation_examples_factory=lambda: examples("inner_validation"),
            selected_candidate=selected,
            # The fit scan and the lock both counted every guarded row, including
            # the rows the declared zero-target-phone policy excludes, so every
            # expected pass cardinality is that count minus those exclusions.
            expected_fit_utterances=(
                metadata.fit_utterances - declared_exclusions["fit"]
            ),
            expected_validation_utterances=(
                int(locked_data["inner_validation_utterances"])
                - declared_exclusions["inner_validation"]
            ),
            audio_integrity=integrity,
        )
        _emit_progress(
            "inner_selection_finished",
            seed=seed,
            best_epoch_by_variant={
                str(variant): selections[str(variant)].best_epoch
                for variant in VARIANT_ORDER
            },
            stopped_early_by_variant={
                str(variant): selections[str(variant)].stopped_early
                for variant in VARIANT_ORDER
            },
            elapsed_seconds=round(time.monotonic() - seed_started, 1),
        )
        checkpoint_records: dict[str, Mapping[str, Any]] = {}
        for variant in VARIANT_ORDER:
            name = str(variant)
            seed_root = temporary / name / f"seed_{seed}"
            checkpoint_records[name] = save_hierarchy_checkpoint(
                seed_root / "hierarchy_head.safetensors",
                head=heads[name],
                selection=selections[name],
                variant=name,
                seed=seed,
                selected_candidate=selected,
                metadata=metadata,
                s104_checkpoint_sha256=sha256_file(s104_checkpoint),
            )
        with ExitStack() as stack:
            writers = {
                str(variant): stack.enter_context(
                    HierarchyPredictionWriters(
                        phone_path=temporary
                        / variant
                        / f"seed_{seed}"
                        / "phone_dev_predictions.parquet",
                        utterance_path=temporary
                        / variant
                        / f"seed_{seed}"
                        / "utterance_dev_predictions.parquet",
                    )
                )
                for variant in VARIANT_ORDER
            }
            dev_count = evaluate_dev_shared(
                heads=heads,
                selections=selections,
                encoder=encoder,
                examples=examples("dev"),
                device=device,
                selected_candidate=selected,
                maximum_chunk_seconds=float(runtime["maximum_chunk_seconds"]),
                seed=seed,
                writers=writers,
                ledger=ledger,
                audio_integrity=integrity,
            )
        expected_dev_utterances = (
            cast(Mapping[str, Any], locked_data["split_utterances"])["dev"]
            - declared_exclusions["dev"]
        )
        if dev_count != expected_dev_utterances:
            raise S105TrainingError("dev cardinality differs from execution lock")
        integrity.assert_stable()
        _emit_progress(
            "seed_finished",
            seed=seed,
            seed_position=seed_position,
            seed_count=len(SEEDS),
            dev_utterances=dev_count,
            seed_elapsed_seconds=round(time.monotonic() - seed_started, 1),
            run_elapsed_seconds=round(time.monotonic() - run_started, 1),
            peak_gpu_bytes=int(torch.cuda.max_memory_allocated()),
        )
        device_report = _device_metadata(device)
        for variant in VARIANT_ORDER:
            name = str(variant)
            seed_root = temporary / name / f"seed_{seed}"
            selection = selections[name]
            slices = slice_metrics_from_prediction_files(
                phone_path=seed_root / "phone_dev_predictions.parquet",
                utterance_path=seed_root / "utterance_dev_predictions.parquet",
                threshold=selection.threshold,
            )
            atomic_write_json(
                seed_root / "dev_slice_metrics.json",
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "variant": name,
                    "seed": seed,
                    "development_only": True,
                    "dev_inference_passes_by_task": {
                        "phone_binary_error": 1,
                        "utterance_scores": 1,
                    },
                    "metrics": _safe_json(slices),
                },
            )
            atomic_write_json(
                seed_root / "resource_report.json",
                {
                    "schema_version": REPORT_SCHEMA_VERSION,
                    "variant": name,
                    "seed": seed,
                    "trainable_parameter_audit": _head_audit(heads[name]),
                    "checkpoint": checkpoint_records[name],
                    "fit_metadata": {
                        "target_vocabulary": list(metadata.target_vocabulary),
                        "positive_phone_rows": metadata.positive_phone_rows,
                        "negative_phone_rows": metadata.negative_phone_rows,
                        "pos_weight": metadata.pos_weight,
                        "fit_utterances": metadata.fit_utterances,
                    },
                    "selection": {
                        "best_epoch": selection.best_epoch,
                        "phone_decision_threshold": selection.threshold,
                        "inner_phone_macro_f1": selection.phone_macro_f1,
                        "inner_comprehensibility_icc_2_1": (
                            selection.comprehensibility_icc_2_1
                        ),
                        "stopped_early": selection.stopped_early,
                        "history": list(selection.history),
                    },
                    "device": device_report,
                    "peak_process_rss_bytes": _peak_rss_bytes(),
                    "peak_cuda_allocated_bytes": int(torch.cuda.max_memory_allocated()),
                    "peak_cuda_reserved_bytes": int(torch.cuda.max_memory_reserved()),
                    "automatic_oom_retry_count": 0,
                    "shared_encoder_forward_across_variants": True,
                    "dev_passes": {
                        "phone_binary_error": 1,
                        "utterance_scores": 1,
                    },
                    "s104_checkpoint_sha256": sha256_file(s104_checkpoint),
                },
            )
        del heads, encoder
        torch.cuda.empty_cache()
    ledger.assert_exact()
    for variant in VARIANT_ORDER:
        atomic_write_json(
            temporary / variant / "variant_report.json",
            build_variant_report(
                generation=temporary,
                variant=str(variant),
            ),
        )
    comparison = recompute_ablation_comparison(
        generation=temporary,
        config=config,
    )
    atomic_write_json(
        temporary / "ablation_comparison_report.json",
        comparison,
    )
    table = render_ablation_table(generation=temporary, comparison=comparison)
    table_path = temporary / "ablation_table.md"
    table_path.write_text(table, encoding="utf-8")
    with table_path.open("rb") as handle:
        os.fsync(handle.fileno())


def _commit_binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def _registry_paths(
    *,
    registry_root: Path,
    prefix: str,
) -> Mapping[str, Path]:
    return {
        str(variant): registry_root / f"{prefix}-{variant}" / "metadata.json"
        for variant in VARIANT_ORDER
    }


def _variant_records(
    records: Sequence[Mapping[str, Any]],
    variant: str,
) -> tuple[Mapping[str, Any], ...]:
    output = tuple(
        record for record in records if str(record["path"]).startswith(f"{variant}/")
    )
    if len(output) != 16:
        raise S105TrainingError("variant artifact cardinality differs")
    return output


def _validate_registry(
    *,
    workspace_root: Path,
    path: Path,
    variant: str,
    prefix: str,
    generation: Path,
    records: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    document = load_json_strict(path, "S105 variant registry")
    run_id = f"{prefix}-{variant}"
    artifacts = document.get("artifacts")
    if (
        document.get("schema_version") != 1
        or document.get("run_id") != run_id
        or document.get("status") != "completed"
        or document.get("failure") is not None
        or document.get("seeds") != {f"experiment_seed_{seed}": seed for seed in SEEDS}
        or not isinstance(document.get("metrics"), list)
        or not isinstance(artifacts, list)
        or len(artifacts) != 16
    ):
        raise S105TrainingError("S105 variant registry is not completed")
    expected = {
        str(record["name"]): record for record in _variant_records(records, variant)
    }
    if {item.get("name") for item in artifacts if isinstance(item, Mapping)} != set(
        expected
    ):
        raise S105TrainingError("S105 registry artifact allowlist differs")
    for item_value in artifacts:
        if not isinstance(item_value, Mapping):
            raise S105TrainingError("S105 registry artifact is invalid")
        item = cast(Mapping[str, Any], item_value)
        canonical = expected[str(item["name"])]
        artifact_path = generation / str(canonical["path"])
        if (
            item.get("path") != artifact_path.relative_to(workspace_root).as_posix()
            or item.get("sha256") != canonical["sha256"]
            or item.get("size_bytes") != canonical["size_bytes"]
        ):
            raise S105TrainingError("S105 registry artifact binding differs")
    return {
        "variant": variant,
        "run_id": run_id,
        "path": path.relative_to(workspace_root).as_posix(),
        "sha256": sha256_file(path),
        "status": "completed",
        "artifact_count": 16,
    }


def _registry_records(
    *,
    workspace_root: Path,
    registry_paths: Mapping[str, Path],
    prefix: str,
    generation: Path,
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        _validate_registry(
            workspace_root=workspace_root,
            path=registry_paths[str(variant)],
            variant=str(variant),
            prefix=prefix,
            generation=generation,
            records=records,
        )
        for variant in VARIANT_ORDER
    ]


def build_commit_payload(
    *,
    workspace_root: Path,
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    generation: Path,
    records: Sequence[Mapping[str, Any]],
    registries: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    comparison_path = generation / "ablation_comparison_report.json"
    comparison = load_json_strict(comparison_path, "S105 comparison report")
    gate = comparison.get("adoption_gate")
    if not isinstance(gate, Mapping):
        raise S105TrainingError("S105 adoption gate is absent")
    selected = (
        "levelwise_multi_aspect_hierarchy"
        if gate.get("adopt_candidate") is True
        else "simple_head_control"
    )
    predecessor = cast(Mapping[str, Any], execution_lock["predecessor"])
    payload: dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation.name,
        "generation_path": generation.relative_to(workspace_root).as_posix(),
        "artifacts": list(records),
        "comparison_report_sha256": sha256_file(comparison_path),
        "adoption_status": gate.get("status"),
        "selected_variant": selected,
        "registries": list(registries),
        "execution_lock": {
            "path": execution_lock_path.relative_to(workspace_root).as_posix(),
            "sha256": sha256_file(execution_lock_path),
        },
        "predecessor": {
            "stage": "s104_adapter",
            "commit_sha256": predecessor["commit_sha256"],
            "generation_id": predecessor["generation_id"],
        },
    }
    payload["binding_sha256"] = _commit_binding(payload)
    return payload


def _publish_immutable_commit(
    path: Path,
    payload: Mapping[str, Any],
) -> bool:
    encoded = canonical_json_bytes(payload, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(path.parent)
    temporary = path.with_name(f".{path.name}.part-{uuid4().hex}")
    created = False
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
            created = True
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
                raise S105TrainingError("immutable S105 commit already differs")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    if not path.is_file() or path.is_symlink() or path.read_bytes() != encoded:
        raise S105TrainingError("published S105 commit bytes differ")
    return created


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_registry_claim(
    *,
    registry_root: Path,
    prefix: str,
) -> Iterator[None]:
    """Hold one process-wide S105 claim before any fixed registry is started."""

    registry_root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_chain(registry_root)
    claim = registry_root / f".{prefix}.claim.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(claim, flags, 0o600)
    except OSError as error:
        raise S105TrainingError(
            "S105 registry claim cannot be opened safely"
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode) or claim.is_symlink():
            raise S105TrainingError("S105 registry claim is not a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise S105TrainingError(
                "another S105 registry claimant is active"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _generation_from_registry(
    *,
    workspace_root: Path,
    generation_root: Path,
    registry_path: Path,
) -> Path:
    document = load_json_strict(registry_path, "S105 recovery registry")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise S105TrainingError("recovery registry artifacts are absent")
    expected_name, expected_relative = expected_artifact_layout()[0]
    matches = [
        item
        for item in artifacts
        if isinstance(item, Mapping) and item.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise S105TrainingError("recovery generation locator differs")
    artifact = _resolve_existing(
        workspace_root,
        matches[0].get("path"),
        "recovery artifact",
    )
    generation = artifact
    for _ in Path(expected_relative).parts:
        generation = generation.parent
    if (
        generation.parent != generation_root
        or not _is_sha256(generation.name)
        or not generation.is_dir()
    ):
        raise S105TrainingError("recovery generation path differs")
    return generation


def recover_completed_commit(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    context: DeepValidationContext,
    generation_root: Path,
    commit_path: Path,
    registry_root: Path,
) -> Mapping[str, Any] | None:
    """Recover only five completed registries bound to one deep-valid tree."""

    outputs = cast(Mapping[str, Any], config["outputs"])
    prefix = str(outputs["registry_run_id_prefix"])
    paths = _registry_paths(registry_root=registry_root, prefix=prefix)
    existing = {variant: path.exists() for variant, path in paths.items()}
    if not any(existing.values()):
        if any(path.parent.exists() for path in paths.values()):
            raise S105TrainingError("partial S105 registry directory exists")
        return None
    if not all(existing.values()):
        raise S105TrainingError("partial S105 variant registry set exists")
    generation = _generation_from_registry(
        workspace_root=workspace_root,
        generation_root=generation_root,
        registry_path=paths[str(VARIANT_ORDER[0])],
    )
    validation = validate_generation(generation, context=context)
    records = artifact_records(generation)
    if validation["generation_id"] != generation.name:
        raise S105TrainingError("recoverable generation identity differs")
    registries = _registry_records(
        workspace_root=workspace_root,
        registry_paths=paths,
        prefix=prefix,
        generation=generation,
        records=records,
    )
    payload = build_commit_payload(
        workspace_root=workspace_root,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        generation=generation,
        records=records,
        registries=registries,
    )
    _publish_immutable_commit(commit_path, payload)
    validate_s105_commit(
        workspace_root=workspace_root,
        config_path=config_path,
    )
    return payload


def _invoke_fault(injector: FaultInjector | None, stage: str) -> None:
    if injector is not None:
        injector(stage)


def _failure_stage(
    runs: Sequence[ExperimentRun],
    stage: str,
    *,
    oom: bool = False,
) -> None:
    if stage not in FAILURE_STAGES:
        raise S105TrainingError("failure stage is not public")
    for run in runs:
        run.set_failure_context(stage=stage, oom=oom)


def _run_s105_training_claimed(
    *,
    workspace_root: Path,
    config_path: Path,
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    argv: Sequence[str],
    generation_builder: GenerationBuilder | None = None,
    fault_injector: FaultInjector | None = None,
) -> Mapping[str, Any]:
    """Execute, publish, register, and commit S105 with crash recovery."""

    from voice_coach.training.wavlm_hierarchy_contract import (
        validate_s104_predecessor,
    )

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolve_existing(root, str(config_path), "S105 config")
    resolved_lock = _resolve_existing(
        root,
        str(execution_lock_path),
        "S105 execution lock",
    )
    config = load_config(resolved_config)
    validate_config(config)
    implementation, implementation_paths = validate_implementation_sources(
        workspace_root=root,
        config=config,
    )
    predecessor = validate_s104_predecessor(workspace_root=root, config=config)
    if (
        execution_lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or execution_lock.get("status") != "ready_for_s105_gpu_training"
        or execution_lock.get("config_sha256") != sha256_file(resolved_config)
        or execution_lock.get("implementation") != implementation
        or cast(Mapping[str, Any], execution_lock.get("predecessor", {})).get(
            "commit_sha256"
        )
        != predecessor.commit_sha256
        or load_json_strict(resolved_lock, "S105 execution lock") != execution_lock
    ):
        raise S105TrainingError("S105 execution lock source binding differs")
    context = build_deep_validation_context(
        workspace_root=root,
        config=config,
        execution_lock=execution_lock,
    )
    outputs = cast(Mapping[str, Any], config["outputs"])
    generation_root = _resolve_output(
        root,
        outputs["generation_root"],
        "S105 generation root",
    )
    commit_path = _resolve_output(
        root,
        outputs["commit_manifest"],
        "S105 commit",
    )
    registry_root = _resolve_output(
        root,
        outputs["registry_root"],
        "S105 registry root",
    )
    if commit_path.exists():
        validate_s105_commit(
            workspace_root=root,
            config_path=resolved_config,
        )
        return load_json_strict(commit_path, "S105 commit")
    recovered = recover_completed_commit(
        workspace_root=root,
        config_path=resolved_config,
        config=config,
        execution_lock_path=resolved_lock,
        execution_lock=execution_lock,
        context=context,
        generation_root=generation_root,
        commit_path=commit_path,
        registry_root=registry_root,
    )
    if recovered is not None:
        return recovered
    generation_root.mkdir(parents=True, exist_ok=True)
    leftovers = [
        path for path in generation_root.iterdir() if path.name.startswith(".part-")
    ]
    if leftovers:
        raise S105TrainingError("partial S105 generation exists")
    temporary = generation_root / f".part-{uuid4().hex}"
    prefix = str(outputs["registry_run_id_prefix"])
    registry_paths = _registry_paths(registry_root=registry_root, prefix=prefix)
    selected = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], execution_lock["predecessor"])["selected_candidate"],
    )
    _, model_config_path, model_checkpoint_path = local_snapshot_paths(
        workspace_root=root,
        selected_candidate=selected,
    )
    checkpoint_paths = [
        _resolve_existing(root, item["path"], "S104 checkpoint input")
        for item in cast(
            Sequence[Mapping[str, Any]],
            cast(Mapping[str, Any], execution_lock["predecessor"])[
                "same_seed_checkpoints"
            ],
        )
    ]
    data = cast(Mapping[str, Any], config["data"])
    input_data = [
        _resolve_existing(root, data[name], f"S105 {name}")
        for name in (
            "modeling_manifest",
            "prompt_challenge_reservation",
            "explicit_utterance_labels",
        )
    ]
    runs = [
        ExperimentRun(
            registry_root,
            run_id=f"{prefix}-{variant}",
            input_files={
                "config": [
                    resolved_config,
                    resolved_lock,
                    *implementation_paths,
                ],
                "data": input_data,
                "model": [model_config_path, model_checkpoint_path],
                "checkpoint": checkpoint_paths,
            },
            seeds={f"experiment_seed_{seed}": seed for seed in SEEDS},
            argv=argv,
            repo_path=root,
        )
        for variant in VARIANT_ORDER
    ]
    published: Mapping[str, Any] | None = None
    current_stage = "registry_initialization"
    registries_completed = False
    try:
        with ExitStack() as stack:
            active_runs = [stack.enter_context(run) for run in runs]
            current_stage = "runtime_validation"
            _failure_stage(active_runs, current_stage)
            _invoke_fault(fault_injector, current_stage)
            current_stage = "metadata_scan"
            _failure_stage(active_runs, current_stage)
            _invoke_fault(fault_injector, current_stage)
            current_stage = "seed_training"
            _failure_stage(active_runs, current_stage)
            builder = generation_builder
            try:
                if builder is None:
                    build_online_generation(
                        workspace_root=root,
                        temporary=temporary,
                        config=config,
                        execution_lock=execution_lock,
                        context=context,
                    )
                else:
                    temporary.mkdir(parents=True, exist_ok=False)
                    builder(temporary, context)
            except torch.OutOfMemoryError:
                _failure_stage(active_runs, current_stage, oom=True)
                raise
            _invoke_fault(fault_injector, "artifact_generation")
            current_stage = "artifact_validation"
            _failure_stage(active_runs, current_stage)
            validation = validate_generation(temporary, context=context)
            if validation.get("artifact_count") != 82:
                raise S105TrainingError("S105 candidate generation is incomplete")
            _invoke_fault(fault_injector, current_stage)
            current_stage = "artifact_publication"
            _failure_stage(active_runs, current_stage)
            try:
                published = publish_content_addressed_generation(
                    temporary=temporary,
                    generation_root=generation_root,
                    context=context,
                )
            except GenerationPublicationDurabilityError as error:
                published = error.publication
                raise
            generation = cast(Path, published["generation_path"])
            _invoke_fault(fault_injector, current_stage)
            current_stage = "registry_registration"
            _failure_stage(active_runs, current_stage)
            records = artifact_records(generation)
            by_variant_run = {
                str(variant): run
                for variant, run in zip(VARIANT_ORDER, active_runs, strict=True)
            }
            for variant in VARIANT_ORDER:
                for record in _variant_records(records, str(variant)):
                    by_variant_run[str(variant)].add_artifact(
                        str(record["name"]),
                        generation / str(record["path"]),
                    )
                _invoke_fault(
                    fault_injector,
                    f"registry_registration_{variant}",
                )
        registries_completed = True
        generation = cast(Path, cast(Mapping[str, Any], published)["generation_path"])
        records = artifact_records(generation)
        registries = _registry_records(
            workspace_root=root,
            registry_paths=registry_paths,
            prefix=prefix,
            generation=generation,
            records=records,
        )
        # Revalidate after registry completion so the immutable commit cannot
        # bind an artifact tree that changed during registry persistence.
        validate_generation(generation, context=context)
        current_stage = "commit_publication"
        _invoke_fault(fault_injector, current_stage)
        payload = build_commit_payload(
            workspace_root=root,
            execution_lock_path=resolved_lock,
            execution_lock=execution_lock,
            generation=generation,
            records=records,
            registries=registries,
        )
        created_commit = _publish_immutable_commit(commit_path, payload)
        try:
            validate_s105_commit(
                workspace_root=root,
                config_path=resolved_config,
            )
        except BaseException:
            if created_commit:
                commit_path.unlink(missing_ok=True)
            raise
        return payload
    except BaseException as error:
        if temporary.is_file():
            temporary.unlink()
        elif temporary.exists():
            shutil.rmtree(temporary)
        oom = isinstance(error, torch.OutOfMemoryError)
        if published is not None and not registries_completed:
            generation = cast(Path, published["generation_path"])
            if published.get("created") is True and generation.exists():
                shutil.rmtree(generation)
        if registries_completed and current_stage != "commit_publication":
            for run in runs:
                if run.metadata.get("status") == "completed":
                    run.mark_post_completion_failure(
                        stage=current_stage,
                        exception_type=type(error),
                        oom=oom,
                    )
        raise


def run_s105_training(
    *,
    workspace_root: Path,
    config_path: Path,
    execution_lock_path: Path,
    execution_lock: Mapping[str, Any],
    argv: Sequence[str],
    generation_builder: GenerationBuilder | None = None,
    fault_injector: FaultInjector | None = None,
) -> Mapping[str, Any]:
    """Acquire one atomic registry claim before any execution/recovery step."""

    root = workspace_root.resolve(strict=True)
    resolved_config = _resolve_existing(root, str(config_path), "S105 config")
    config = load_config(resolved_config)
    validate_config(config)
    outputs = cast(Mapping[str, Any], config["outputs"])
    registry_root = _resolve_output(
        root,
        outputs["registry_root"],
        "S105 registry root",
    )
    with _exclusive_registry_claim(
        registry_root=registry_root,
        prefix=str(outputs["registry_run_id_prefix"]),
    ):
        return _run_s105_training_claimed(
            workspace_root=root,
            config_path=resolved_config,
            execution_lock_path=execution_lock_path,
            execution_lock=execution_lock,
            argv=argv,
            generation_builder=generation_builder,
            fault_injector=fault_injector,
        )


__all__ = [
    "FAILURE_STAGES",
    "HeadSelection",
    "HierarchyOnlineExample",
    "PooledHierarchy",
    "WordInterval",
    "build_commit_payload",
    "build_online_generation",
    "evaluate_dev_shared",
    "iter_hierarchy_examples",
    "pool_all_encoder_layers",
    "recover_completed_commit",
    "run_s105_training",
    "save_hierarchy_checkpoint",
    "train_and_select_all_heads",
]
