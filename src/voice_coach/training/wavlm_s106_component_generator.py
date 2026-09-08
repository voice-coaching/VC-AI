"""Production generator for the twelve label-free S106 component inputs.

The security boundary in this module is intentionally visible in the API:

1. the existing S106 preflight validates S104 -> S105 -> B07 and freezes all
   component checkpoints;
2. only label-free stack identity, target intervals, and word intervals are
   scanned;
3. the identity matrix and all twelve prediction files are committed in a
   content-addressed generation and bound by a feature-freeze receipt;
4. only a label materializer that has validated that receipt may request the
   ``phone_alignments`` column and derive the separate five-column target
   matrix;
5. the v3 component bundle is the final visibility commit.

No S104, S105, or B07 canonical source is modified by this implementation.
The production backend reconstructs models exclusively from the exact
checkpoint schemas and helpers used by those stages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import re
import shutil
import stat
import tempfile
from typing import Any, BinaryIO, Protocol, cast

import numpy as np
from numpy.typing import NDArray
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.dataset as pads  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from voice_coach.training import wavlm_s106_component_projection as projection
from voice_coach.training import wavlm_s106_fusion_contract as fusion_contract


CONFIG_SCHEMA_VERSION = 1
FEATURE_FREEZE_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_feature_freeze.v1"
GENERATOR_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_input_commit.v2"
REGISTRY_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_input_registry.v1"
SYNTHETIC_BUNDLE_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_bundle_commit.synthetic_test.v1"
)
SYNTHETIC_GENERATOR_COMMIT_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_input_commit.synthetic_test.v1"
)
SYNTHETIC_REGISTRY_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_component_input_registry.synthetic_test.v1"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FEATURE_IDENTITY_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "alignment_confidence",
    "qc_flagged",
    "reserved_prompt",
)
LABEL_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "reference_binary_error_label",
)
PREDICTION_COLUMNS = projection.PREDICTION_COLUMNS
LABEL_FREE_STACK_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "pcm_sha256",
    "prompt_sha256",
    "qc_flags",
    "word_intervals",
    "target_phone_intervals",
)
LABEL_MATERIALIZER_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "target_phone_intervals",
    "phone_alignments",
)
FORBIDDEN_BEFORE_FREEZE = frozenset(
    {
        "phone_alignments",
        "operation",
        "realized_phone_intervals",
        "realized_label",
        "error_intervals",
        "error_presence",
        "pronunciation_score",
        "fluency_score",
        "comprehensibility_score",
    }
)
FEATURE_ATTESTATIONS = {
    component: {
        "label_free": True,
        "stack_only": True,
        "binary_error_label_used": False,
        "realized_phone_identity_used": False,
        "dev_prediction_reused": False,
        "representation": projection.PROJECTION_REPRESENTATIONS[component],
    }
    for component in projection.COMPONENTS
}
IDENTITY_SCHEMA = pa.schema(
    [
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("phone_index", pa.int32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("alignment_confidence", pa.float64(), nullable=True),
        pa.field("qc_flagged", pa.bool_(), nullable=False),
        pa.field("reserved_prompt", pa.bool_(), nullable=False),
    ]
)
LABEL_SCHEMA = pa.schema(
    [
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("phone_index", pa.int32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("reference_binary_error_label", pa.int8(), nullable=False),
    ]
)
PREDICTION_SCHEMA = pa.schema(
    [
        pa.field("utterance_id_sha256", pa.string(), nullable=False),
        pa.field("speaker_id_sha256", pa.string(), nullable=False),
        pa.field("phone_index", pa.int32(), nullable=False),
        pa.field("split", pa.string(), nullable=False),
        pa.field("probability", pa.float64(), nullable=False),
    ]
)


class S106ComponentGeneratorError(RuntimeError):
    """Raised before an invalid projection generation can become visible."""


@dataclass(frozen=True, slots=True)
class TargetInterval:
    start: float
    end: float
    label: str


@dataclass(frozen=True, slots=True)
class WordInterval:
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class LabelFreeUtterance:
    """Only the fields permitted before the feature-freeze receipt."""

    utterance_id_sha256: str
    speaker_id_sha256: str
    split: str
    audio_relative_path: str
    pcm_sha256: str
    target_intervals: tuple[TargetInterval, ...]
    word_intervals: tuple[WordInterval, ...]

    @property
    def row_keys(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (self.utterance_id_sha256, index)
            for index in range(len(self.target_intervals))
        )


@dataclass(frozen=True, slots=True)
class GeneratedComponentInputs:
    generation_id: str
    feature_freeze_receipt: Mapping[str, Any]
    label_record: Mapping[str, Any]
    bundle: Mapping[str, Any]
    commit: Mapping[str, Any]
    registry: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LabelFreeStackScan:
    utterances: tuple[LabelFreeUtterance, ...]
    # Raw manifest IDs exist only long enough to push the exact eligible
    # universe into the post-freeze label scanner.  They are never serialized.
    eligible_source_ids: tuple[str, ...]
    excluded_qc_utterances: int
    excluded_reserved_prompt_utterances: int
    source_sha256: str
    source_size_bytes: int


class ProjectionBackend(Protocol):
    """A checkpoint-bound backend with no label argument."""

    def project(
        self,
        utterance: LabelFreeUtterance,
    ) -> Mapping[str, Mapping[int, Sequence[float]]]:
        """Return one probability per target interval for 4 components x 3 seeds."""

    def close(self) -> None:
        """Release model resources."""


FaultInjector = Callable[[str], None]
Preflight = Callable[
    [Path, Path],
    tuple[Mapping[str, Any], Path],
]
BundleBuilder = Callable[..., Mapping[str, Any]]


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    output: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise S106ComponentGeneratorError(
                f"generator YAML contains duplicate key: {key!r}"
            )
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S106ComponentGeneratorError(f"{name} must be a mapping")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise S106ComponentGeneratorError(f"{name} must be a list")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S106ComponentGeneratorError(f"{name} differs")


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise S106ComponentGeneratorError(f"{name} keys differ")


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise S106ComponentGeneratorError(f"{name} must be lowercase SHA-256")
    return value


def _relative_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S106ComponentGeneratorError(f"{name} must be a non-empty path")
    path = Path(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.casefold().startswith(".env") for part in path.parts)
    ):
        raise S106ComponentGeneratorError(
            f"{name} must be a safe workspace-relative path"
        )
    return value


def _resolve_file(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S106ComponentGeneratorError(f"{name} cannot traverse a symlink")
    try:
        path = current.resolve(strict=True)
        path.relative_to(root)
    except (OSError, ValueError) as error:
        raise S106ComponentGeneratorError(f"{name} cannot be resolved") from error
    if not path.is_file() or path.is_symlink():
        raise S106ComponentGeneratorError(f"{name} must be a regular file")
    return path


def _resolve_output(root: Path, value: object, name: str) -> Path:
    relative = _relative_path(value, name)
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S106ComponentGeneratorError(f"{name} cannot traverse a symlink")
    try:
        path = current.resolve(strict=False)
        path.relative_to(root)
    except ValueError as error:
        raise S106ComponentGeneratorError(f"{name} escapes workspace") from error
    if path.is_symlink():
        raise S106ComponentGeneratorError(f"{name} cannot be a symlink")
    return path


def _workspace_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise S106ComponentGeneratorError("output path is outside workspace") from error


def canonical_json_bytes(value: object, *, pretty: bool) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned, pretty=False)).hexdigest()


def load_generator_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as error:
        raise S106ComponentGeneratorError("cannot load generator config") from error
    config = dict(_mapping(value, "generator config"))
    validate_generator_config(config)
    return config


def validate_generator_config(config: Mapping[str, Any]) -> None:
    """Validate the separate generator contract without touching runtime inputs."""

    _exact_keys(
        config,
        {
            "schema_version",
            "generator",
            "access_order",
            "inputs",
            "representations",
            "outputs",
            "schemas",
            "runtime",
            "privacy",
        },
        "generator config",
    )
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    generator = _mapping(config.get("generator"), "generator")
    _exact_keys(
        generator,
        {
            "id",
            "stage",
            "main_config",
            "main_execution_lock",
            "status",
            "development_only",
            "oracle_alignment",
            "stack_only",
        },
        "generator",
    )
    _expect(
        generator.get("id"),
        "aihub71490_wavlm_s106_component_inputs_v1",
        "generator.id",
    )
    _expect(generator.get("status"), "production_ready", "generator.status")
    _expect(generator.get("development_only"), True, "generator.development_only")
    _expect(generator.get("oracle_alignment"), True, "generator.oracle_alignment")
    _expect(generator.get("stack_only"), True, "generator.stack_only")
    for name in ("main_config", "main_execution_lock"):
        _relative_path(generator.get(name), f"generator.{name}")

    _expect(
        list(_sequence(config.get("access_order"), "access_order")),
        [
            "validate_s104",
            "validate_s105_with_nested_s104",
            "validate_b07",
            "validate_explicit",
            "resolve_stack",
            "scan_label_free_identity_target_and_word_intervals",
            "retain_eligible_raw_source_ids_in_memory_only",
            "probe_frozen_checkpoints",
            "materialize_private_immutable_model_and_config_snapshot",
            "initialize_gpu_and_models",
            "generate_identity_and_twelve_predictions",
            "publish_feature_freeze_receipt",
            "push_down_exact_eligible_source_ids_and_open_phone_alignments",
            "materialize_separate_label_matrix",
            "prepare_generator_commit_and_completed_registry",
            "publish_component_bundle_as_final_visibility_commit",
        ],
        "access_order",
    )
    inputs = _mapping(config.get("inputs"), "inputs")
    _exact_keys(
        inputs,
        {
            "stack_columns_before_feature_freeze",
            "forbidden_before_feature_freeze",
            "label_materializer_columns",
            "split",
            "dataset_root",
            "prompt_challenge_reservation",
            "prompt_challenge_reservation_sha256",
        },
        "inputs",
    )
    _expect(
        tuple(inputs.get("stack_columns_before_feature_freeze", ())),
        LABEL_FREE_STACK_COLUMNS,
        "inputs.stack_columns_before_feature_freeze",
    )
    _expect(
        frozenset(inputs.get("forbidden_before_feature_freeze", ())),
        FORBIDDEN_BEFORE_FREEZE,
        "inputs.forbidden_before_feature_freeze",
    )
    _expect(
        tuple(inputs.get("label_materializer_columns", ())),
        LABEL_MATERIALIZER_COLUMNS,
        "inputs.label_materializer_columns",
    )
    _expect(inputs.get("split"), "stack", "inputs.split")
    _relative_path(inputs.get("dataset_root"), "inputs.dataset_root")
    _relative_path(
        inputs.get("prompt_challenge_reservation"),
        "inputs.prompt_challenge_reservation",
    )
    _sha256(
        inputs.get("prompt_challenge_reservation_sha256"),
        "inputs.prompt_challenge_reservation_sha256",
    )
    if set(LABEL_FREE_STACK_COLUMNS) & FORBIDDEN_BEFORE_FREEZE:
        raise S106ComponentGeneratorError("label-free manifest projection leaks labels")

    representations = _mapping(config.get("representations"), "representations")
    _expect(
        representations.get("explicit"),
        projection.PROJECTION_REPRESENTATIONS["explicit"],
        "representations.explicit",
    )
    _expect(
        representations.get("s104_adapter"),
        projection.PROJECTION_REPRESENTATIONS["s104_adapter"],
        "representations.s104_adapter",
    )
    _expect(
        representations.get("s105_selected_hierarchy"),
        projection.PROJECTION_REPRESENTATIONS["s105_selected_hierarchy"],
        "representations.s105",
    )
    _expect(
        representations.get("gop_acoustic_posterior"),
        "unadapted_wavlm_base_plus_target_interval_pooled_float16_roundtrip",
        "representations.b07",
    )
    _expect(
        representations.get("b07_forward_sharing_with_s104_or_s105"),
        False,
        "representations.b07_forward_sharing",
    )

    outputs = _mapping(config.get("outputs"), "outputs")
    expected_output_keys = {
        "generation_root",
        "staging_root",
        "feature_identity_matrix",
        "label_matrix",
        "prediction_root",
        "feature_freeze_receipt",
        "component_bundle_commit",
        "generator_commit",
        "registry",
        "atomic_publish",
        "content_addressed_generation",
        "bundle_is_visibility_commit",
    }
    _exact_keys(outputs, expected_output_keys, "outputs")
    for name in expected_output_keys - {
        "atomic_publish",
        "content_addressed_generation",
        "bundle_is_visibility_commit",
    }:
        _relative_path(outputs.get(name), f"outputs.{name}")
    for name in (
        "atomic_publish",
        "content_addressed_generation",
        "bundle_is_visibility_commit",
    ):
        _expect(outputs.get(name), True, f"outputs.{name}")
    _expect(
        outputs.get("prediction_root"),
        projection.COMPONENT_PREDICTION_ROOT,
        "outputs.prediction_root",
    )

    schemas = _mapping(config.get("schemas"), "schemas")
    _expect(
        schemas.get("feature_freeze_receipt"),
        FEATURE_FREEZE_SCHEMA_VERSION,
        "schemas.feature_freeze_receipt",
    )
    _expect(
        schemas.get("generator_commit"),
        GENERATOR_COMMIT_SCHEMA_VERSION,
        "schemas.generator_commit",
    )
    _expect(schemas.get("registry"), REGISTRY_SCHEMA_VERSION, "schemas.registry")
    _expect(
        schemas.get("prediction"),
        projection.PREDICTION_RECORD_SCHEMA_VERSION,
        "schemas.prediction",
    )
    _expect(
        schemas.get("bundle"),
        projection.COMPONENT_BUNDLE_SCHEMA_VERSION,
        "schemas.bundle",
    )
    _expect(
        tuple(schemas.get("feature_identity_columns", ())),
        FEATURE_IDENTITY_COLUMNS,
        "schemas.feature_identity_columns",
    )
    _expect(
        tuple(schemas.get("label_columns", ())),
        LABEL_COLUMNS,
        "schemas.label_columns",
    )
    _expect(
        tuple(schemas.get("prediction_columns", ())),
        PREDICTION_COLUMNS,
        "schemas.prediction_columns",
    )
    _expect(
        tuple(schemas.get("row_key", ())),
        ("utterance_id_sha256", "phone_index"),
        "schemas.row_key",
    )

    runtime = _mapping(config.get("runtime"), "runtime")
    _expect(tuple(runtime.get("seeds", ())), projection.SEEDS, "runtime.seeds")
    _expect(
        runtime.get("maximum_projection_batch_rows"),
        projection.MAXIMUM_PROJECTION_BATCH_ROWS,
        "runtime.maximum_projection_batch_rows",
    )
    _expect(runtime.get("sample_rate_hz"), 16_000, "runtime.sample_rate_hz")
    _expect(
        float(cast(float, runtime.get("maximum_chunk_seconds"))),
        20.0,
        "runtime.maximum_chunk_seconds",
    )
    _expect(
        runtime.get("b07_embedding_storage_equivalence"),
        "float16_roundtrip_then_float32",
        "runtime.b07_embedding_storage_equivalence",
    )
    _expect(
        runtime.get("stack_manifest_input_binding"),
        "held_no_follow_fd_projected_filter_no_disk_copy",
        "runtime.stack_manifest_input_binding",
    )
    _expect(
        runtime.get("audio_input_binding"),
        "held_no_follow_fd_same_bytes_pcm_hash_and_decode",
        "runtime.audio_input_binding",
    )
    _expect(
        runtime.get("model_input_binding"),
        "private_read_only_snapshot_from_hash_validated_bytes",
        "runtime.model_input_binding",
    )
    _expect(
        runtime.get("label_matrix_staging"),
        "anonymous_unlinked_held_fd",
        "runtime.label_matrix_staging",
    )
    _expect(
        runtime.get("production_publication_lock"),
        "crash_released_flock_single_writer",
        "runtime.production_publication_lock",
    )
    _expect(
        runtime.get("s104_s105_config_binding"),
        "private_read_only_copy_from_lock_canonical_source",
        "runtime.s104_s105_config_binding",
    )
    _expect(
        runtime.get("production_dependency_injection_allowed"),
        False,
        "runtime.production_dependency_injection_allowed",
    )
    _expect(
        runtime.get("synthetic_schema_accepted_by_production_validator"),
        False,
        "runtime.synthetic_schema_accepted_by_production_validator",
    )
    _expect(runtime.get("parquet_compression"), "zstd", "runtime.parquet_compression")
    _expect(
        runtime.get("parquet_dictionary_encoding"),
        False,
        "runtime.parquet_dictionary_encoding",
    )
    _expect(runtime.get("parquet_statistics"), True, "runtime.parquet_statistics")
    _expect(
        runtime.get("environment_files_forbidden"),
        True,
        "runtime.environment_files_forbidden",
    )
    resource = _mapping(runtime.get("gpu_resource_contract"), "gpu resource")
    _expect(
        resource,
        {
            "require_cuda": True,
            "require_bfloat16": True,
            "encoder_model_count": 4,
            "adapted_encoder_model_count": 3,
            "unadapted_encoder_model_count": 1,
            "encoder_forwards_per_utterance": 4,
            "adapted_forward_per_seed": 1,
            "unadapted_forward_shared_across_b07_seeds": True,
            "bounded_parquet_row_groups": True,
        },
        "runtime.gpu_resource_contract",
    )

    privacy = _mapping(config.get("privacy"), "privacy")
    for name in (
        "raw_ids_in_outputs",
        "raw_paths_in_outputs",
        "target_labels_in_outputs",
        "realized_phone_identity_in_outputs",
        "operation_in_feature_outputs",
        "alignment_confidence_source_available",
    ):
        _expect(privacy.get(name), False, f"privacy.{name}")
    _expect(privacy.get("labels_physically_separate"), True, "privacy.labels")
    _expect(
        privacy.get("raw_ids_retained_only_transient_memory"),
        True,
        "privacy.raw_ids_retained_only_transient_memory",
    )
    _expect(
        privacy.get("absent_alignment_confidence_policy"),
        "null_with_missing_indicator",
        "privacy.alignment_confidence",
    )


def validate_static_generator(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> Mapping[str, Any]:
    """Config-only validation: source pins only, runtime access and output zero."""

    root = workspace_root.resolve(strict=True)
    resolved = generator_config_path
    if not resolved.is_absolute():
        resolved = root / resolved
    resolved = resolved.resolve(strict=True)
    config = load_generator_config(resolved)
    main_relative = _mapping(config["generator"], "generator")["main_config"]
    main_path = _resolve_file(root, main_relative, "generator.main_config")
    main_config = fusion_contract.load_config(main_path)
    fusion_contract.validate_config(main_config)
    _validate_canonical_generator_config_binding(
        root=root,
        generator_config_path=resolved,
        main_config=main_config,
    )
    implementation = fusion_contract.validate_implementation_sources(
        workspace_root=root,
        config=main_config,
    )
    return {
        "status": "config_valid",
        "generator_config": {
            "path": _workspace_relative(root, resolved),
            "sha256": sha256_file(resolved),
            "size_bytes": resolved.stat().st_size,
        },
        "main_config": {
            "path": _workspace_relative(root, main_path),
            "sha256": sha256_file(main_path),
            "size_bytes": main_path.stat().st_size,
        },
        "implementation": implementation,
        "predecessor_read": False,
        "explicit_checkpoint_read": False,
        "b07_checkpoint_read": False,
        "stack_path_resolved": False,
        "stack_rows_read": 0,
        "raw_audio_read": False,
        "gpu_initialized": False,
        "output_written": False,
    }


def _finite_interval(
    value: object,
    *,
    name: str,
    with_label: bool,
) -> TargetInterval | WordInterval:
    record = _mapping(value, name)
    start = record.get("start")
    end = record.get("end")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
    ):
        raise S106ComponentGeneratorError(f"{name} bounds must be numeric")
    start_value = float(start)
    end_value = float(end)
    if (
        not math.isfinite(start_value)
        or not math.isfinite(end_value)
        or start_value < 0.0
        or end_value <= start_value
    ):
        raise S106ComponentGeneratorError(f"{name} bounds are invalid")
    if with_label:
        # Match the canonical S104/S105 target cleaner exactly.  In
        # particular, vocabulary lookup and every downstream projection must
        # see the stripped target rather than a whitespace-drifted variant.
        label = str(record.get("label") or "").strip()
        if not label:
            raise S106ComponentGeneratorError(f"{name} target label is invalid")
        return TargetInterval(start_value, end_value, label)
    return WordInterval(start_value, end_value)


def _intervals(
    value: object,
    *,
    name: str,
    with_label: bool,
    allow_empty: bool = False,
) -> tuple[TargetInterval, ...] | tuple[WordInterval, ...]:
    if not isinstance(value, list):
        raise S106ComponentGeneratorError(f"{name} must be a list")
    cleaned = tuple(
        _finite_interval(item, name=f"{name}[{index}]", with_label=with_label)
        for index, item in enumerate(value)
    )
    if not cleaned and not allow_empty:
        raise S106ComponentGeneratorError(f"{name} cannot be empty")
    if cleaned != tuple(sorted(cleaned, key=lambda item: (item.start, item.end))):
        raise S106ComponentGeneratorError(f"{name} must be ordered")
    return cast(
        tuple[TargetInterval, ...] | tuple[WordInterval, ...],
        cleaned,
    )


def _source_batches(
    descriptor: int,
    *,
    columns: Sequence[str],
    batch_size: int,
    eligible_source_ids: Sequence[str] | None = None,
) -> Iterator[pa.RecordBatch]:
    """Read only projected columns from one already-held Parquet descriptor."""

    identifiers: tuple[str, ...] | None = None
    if eligible_source_ids is not None:
        identifiers = tuple(eligible_source_ids)
        if (
            not identifiers
            or len(identifiers) != len(set(identifiers))
            or any(not isinstance(value, str) or not value for value in identifiers)
            or "id" not in columns
        ):
            raise S106ComponentGeneratorError(
                "eligible label-scanner source IDs are invalid"
            )
    try:
        table_descriptor = os.dup(descriptor)
        with os.fdopen(table_descriptor, "rb") as raw_source:
            raw_source.seek(0)
            with pa.PythonFile(raw_source, mode="r") as arrow_source:
                fragment = pads.ParquetFileFormat().make_fragment(arrow_source)
                missing = set(columns) - set(fragment.physical_schema.names)
                if missing:
                    raise S106ComponentGeneratorError(
                        f"stack manifest is missing columns: {sorted(missing)!r}"
                    )
                predicate = pads.field("split") == "stack"
                if identifiers is not None:
                    predicate = predicate & pads.field("id").isin(list(identifiers))
                scanner = pads.Scanner.from_fragment(
                    fragment,
                    columns=list(columns),
                    filter=predicate,
                    batch_size=batch_size,
                    batch_readahead=1,
                    fragment_readahead=1,
                    use_threads=False,
                )
                for batch in scanner.to_batches():
                    if batch.num_rows == 0:
                        continue
                    if tuple(batch.schema.names) != tuple(columns):
                        raise S106ComponentGeneratorError(
                            "held stack projection order differs"
                        )
                    yield batch
    except (OSError, pa.ArrowException) as error:
        raise S106ComponentGeneratorError(
            "held stack manifest projected read failed"
        ) from error


def _stable_source_snapshot(path: Path) -> tuple[str, int, tuple[int, ...]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError("manifest no-follow open failed") from error
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = os.stat(path, follow_symlinks=False)

    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    expected = identity(before)
    if expected != identity(after) or expected != identity(path_after):
        raise S106ComponentGeneratorError("manifest changed during stable snapshot")
    return digest.hexdigest(), size, expected


def _descriptor_digest(descriptor: int) -> tuple[str, int]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            break
        digest.update(block)
        size += len(block)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), size


def _source_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@contextmanager
def _held_parquet_descriptor(
    path: Path,
    *,
    expected_sha256: str,
    name: str,
) -> Iterator[tuple[int, str, int, tuple[int, ...]]]:
    """Yield one verified no-follow FD without copying restricted source bytes."""

    expected_sha256 = _sha256(expected_sha256, f"{name} expected SHA-256")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError(f"{name} no-follow open failed") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S106ComponentGeneratorError(f"{name} must be a regular file")
        try:
            path_before = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise S106ComponentGeneratorError(
                f"{name} path identity read failed"
            ) from error
        source_identity = _source_identity(before)
        if source_identity != _source_identity(path_before):
            raise S106ComponentGeneratorError(f"{name} path identity differs")
        before_sha256, before_size = _descriptor_digest(descriptor)
        if before_size != before.st_size or before_sha256 != expected_sha256:
            raise S106ComponentGeneratorError(f"{name} digest differs")
        yield descriptor, before_sha256, before_size, source_identity
        after_sha256, after_size = _descriptor_digest(descriptor)
        after = os.fstat(descriptor)
        try:
            path_after = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise S106ComponentGeneratorError(
                f"{name} changed during safe scan"
            ) from error
        if (
            source_identity != _source_identity(after)
            or source_identity != _source_identity(path_after)
            or after_size != before_size
            or after_sha256 != before_sha256
        ):
            raise S106ComponentGeneratorError(f"{name} changed during safe scan")
    finally:
        os.close(descriptor)


def _held_source_batches(
    path: Path,
    *,
    expected_sha256: str,
    name: str,
    columns: Sequence[str],
    batch_size: int,
    eligible_source_ids: Sequence[str] | None = None,
) -> Iterator[pa.RecordBatch]:
    with _held_parquet_descriptor(
        path,
        expected_sha256=expected_sha256,
        name=name,
    ) as (descriptor, _, _, _):
        yield from _source_batches(
            descriptor,
            columns=columns,
            batch_size=batch_size,
            eligible_source_ids=eligible_source_ids,
        )


def _pinned_model_file_binding(path: Path) -> Mapping[str, Any]:
    lexical = os.lstat(path)
    link_target = os.readlink(path) if path.is_symlink() else None
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise S106ComponentGeneratorError("pinned model file target is invalid")
    digest, size, identity = _stable_source_snapshot(resolved)
    return {
        "lexical_path": path,
        "lexical_identity": (
            lexical.st_dev,
            lexical.st_ino,
            lexical.st_mode,
            lexical.st_size,
            lexical.st_mtime_ns,
            lexical.st_ctime_ns,
        ),
        "link_target": link_target,
        "resolved_path": resolved,
        "resolved_sha256": digest,
        "resolved_size": size,
        "resolved_identity": identity,
    }


def _revalidate_pinned_model_file(binding: Mapping[str, Any]) -> None:
    lexical_path = cast(Path, binding["lexical_path"])
    lexical = os.lstat(lexical_path)
    lexical_identity = (
        lexical.st_dev,
        lexical.st_ino,
        lexical.st_mode,
        lexical.st_size,
        lexical.st_mtime_ns,
        lexical.st_ctime_ns,
    )
    current_target = os.readlink(lexical_path) if lexical_path.is_symlink() else None
    resolved = lexical_path.resolve(strict=True)
    digest, size, identity = _stable_source_snapshot(resolved)
    if (
        lexical_identity != binding["lexical_identity"]
        or current_target != binding["link_target"]
        or resolved != binding["resolved_path"]
        or digest != binding["resolved_sha256"]
        or size != binding["resolved_size"]
        or identity != binding["resolved_identity"]
    ):
        raise S106ComponentGeneratorError(
            "pinned Base+ file changed during model loading"
        )


def scan_label_free_stack(
    *,
    stack_path: Path,
    reserved_prompt_hashes: frozenset[str],
    expected_sha256: str,
    batch_size: int = projection.MAXIMUM_PROJECTION_BATCH_ROWS,
) -> LabelFreeStackScan:
    """Scan stack identity/target/word data without requesting alignment labels."""

    projection.validate_label_free_projection_columns(LABEL_FREE_STACK_COLUMNS)
    if set(LABEL_FREE_STACK_COLUMNS) & FORBIDDEN_BEFORE_FREEZE:
        raise S106ComponentGeneratorError("label-free stack columns are unsafe")
    utterances: list[LabelFreeUtterance] = []
    excluded_qc = 0
    excluded_reserved = 0
    seen_utterances: set[str] = set()
    seen_keys: set[tuple[str, int]] = set()
    source_ids_by_hash: dict[str, str] = {}
    with _held_parquet_descriptor(
        stack_path,
        expected_sha256=expected_sha256,
        name="stack manifest",
    ) as (descriptor, before_sha256, before_size, before_identity):
        for batch in _source_batches(
            descriptor,
            columns=LABEL_FREE_STACK_COLUMNS,
            batch_size=batch_size,
        ):
            if tuple(batch.schema.names) != LABEL_FREE_STACK_COLUMNS:
                raise S106ComponentGeneratorError("label-free projection order differs")
            for row in batch.to_pylist():
                split = row.get("split")
                if split != "stack":
                    raise S106ComponentGeneratorError(
                        "predicate-pushed stack scanner returned a non-stack row"
                    )
                qc_flags = row.get("qc_flags")
                if qc_flags is None:
                    qc_flags = []
                if not isinstance(qc_flags, list):
                    raise S106ComponentGeneratorError("qc_flags must be a list")
                if qc_flags:
                    excluded_qc += 1
                    continue
                from voice_coach.training.wavlm_adapter_training import (
                    hash_utterance_id,
                )

                raw_source_id = row.get("id")
                utterance_hash = hash_utterance_id(raw_source_id)
                speaker = _sha256(row.get("speaker_id"), "stack speaker_id")
                pcm_sha256 = _sha256(row.get("pcm_sha256"), "stack pcm_sha256")
                prompt = _sha256(row.get("prompt_sha256"), "stack prompt_sha256")
                if prompt in reserved_prompt_hashes:
                    excluded_reserved += 1
                    continue
                audio_path = _relative_path(row.get("audio_path"), "stack audio_path")
                targets = cast(
                    tuple[TargetInterval, ...],
                    _intervals(
                        row.get("target_phone_intervals"),
                        name="target_phone_intervals",
                        with_label=True,
                    ),
                )
                words = cast(
                    tuple[WordInterval, ...],
                    _intervals(
                        row.get("word_intervals"),
                        name="word_intervals",
                        with_label=False,
                        allow_empty=True,
                    ),
                )
                if utterance_hash in seen_utterances:
                    raise S106ComponentGeneratorError("stack utterance is duplicated")
                seen_utterances.add(utterance_hash)
                source_ids_by_hash[utterance_hash] = cast(str, raw_source_id)
                for phone_index in range(len(targets)):
                    key = (utterance_hash, phone_index)
                    if key in seen_keys:
                        raise S106ComponentGeneratorError(
                            "stack phone key is duplicated"
                        )
                    seen_keys.add(key)
                utterances.append(
                    LabelFreeUtterance(
                        utterance_id_sha256=utterance_hash,
                        speaker_id_sha256=speaker,
                        split="stack",
                        audio_relative_path=audio_path,
                        pcm_sha256=pcm_sha256,
                        target_intervals=targets,
                        word_intervals=words,
                    )
                )
    if not utterances:
        raise S106ComponentGeneratorError("stack label-free universe is empty")
    utterances.sort(key=lambda item: item.utterance_id_sha256)
    after_sha256, after_size, after_identity = _stable_source_snapshot(stack_path)
    if (
        after_sha256 != before_sha256
        or after_size != before_size
        or after_identity != before_identity
    ):
        raise S106ComponentGeneratorError("stack manifest changed during safe scan")
    return LabelFreeStackScan(
        utterances=tuple(utterances),
        eligible_source_ids=tuple(
            source_ids_by_hash[value.utterance_id_sha256] for value in utterances
        ),
        excluded_qc_utterances=excluded_qc,
        excluded_reserved_prompt_utterances=excluded_reserved,
        source_sha256=after_sha256,
        source_size_bytes=after_size,
    )


class _StableParquetSink:
    def __init__(
        self,
        *,
        path: Path,
        schema: pa.Schema,
        metadata: Mapping[bytes, bytes] | None,
        batch_rows: int,
    ) -> None:
        self.path = path
        self.schema = (
            schema.with_metadata(dict(metadata)) if metadata is not None else schema
        )
        self.batch_rows = batch_rows
        self.rows: list[Mapping[str, Any]] = []
        self.row_count = 0
        self.writer: pq.ParquetWriter | None = None

    def append(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows.extend(dict(row) for row in rows)
        if len(self.rows) >= self.batch_rows:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(
                self.path,
                self.schema,
                compression="zstd",
                use_dictionary=False,
                write_statistics=True,
            )
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        self.writer.write_table(table, row_group_size=self.batch_rows)
        self.row_count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
        if self.row_count <= 0 or not self.path.is_file():
            raise S106ComponentGeneratorError("Parquet output is empty")
        with self.path.open("rb") as handle:
            os.fsync(handle.fileno())
        _fsync_directory(self.path.parent)


class _AnonymousParquetSink:
    """Write Parquet through an unlinked held file, leaving no crash orphan."""

    def __init__(
        self,
        *,
        handle: BinaryIO,
        schema: pa.Schema,
        metadata: Mapping[bytes, bytes] | None,
        batch_rows: int,
    ) -> None:
        information = os.fstat(handle.fileno())
        if not stat.S_ISREG(information.st_mode) or information.st_nlink != 0:
            raise S106ComponentGeneratorError(
                "anonymous Parquet staging file must be unlinked"
            )
        self.handle = handle
        self.schema = (
            schema.with_metadata(dict(metadata)) if metadata is not None else schema
        )
        self.batch_rows = batch_rows
        self.rows: list[Mapping[str, Any]] = []
        self.row_count = 0
        self.writer: pq.ParquetWriter | None = None

    def append(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows.extend(dict(row) for row in rows)
        if len(self.rows) >= self.batch_rows:
            self.flush()

    def flush(self) -> None:
        if not self.rows:
            return
        if self.writer is None:
            self.handle.seek(0)
            self.writer = pq.ParquetWriter(
                self.handle,
                self.schema,
                compression="zstd",
                use_dictionary=False,
                write_statistics=True,
            )
        table = pa.Table.from_pylist(self.rows, schema=self.schema)
        self.writer.write_table(table, row_group_size=self.batch_rows)
        self.row_count += len(self.rows)
        self.rows.clear()

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
        self.handle.flush()
        if self.row_count <= 0 or os.fstat(self.handle.fileno()).st_size <= 0:
            raise S106ComponentGeneratorError("anonymous Parquet output is empty")
        os.fsync(self.handle.fileno())
        self.handle.seek(0)


def _file_record(
    root: Path,
    path: Path,
    row_count: int,
    *,
    content_path: Path | None = None,
) -> Mapping[str, Any]:
    if row_count <= 0:
        raise S106ComponentGeneratorError("file record row count must be positive")
    source = path if content_path is None else content_path
    if not source.is_file() or source.is_symlink():
        raise S106ComponentGeneratorError("file record content is unavailable")
    return {
        "path": _workspace_relative(root, path),
        "sha256": sha256_file(source),
        "size_bytes": source.stat().st_size,
        "row_count": row_count,
    }


def _prediction_metadata(
    *,
    component: str,
    seed: int,
    source_lineage_sha256: str,
    source_checkpoint_sha256s: Sequence[str],
) -> Mapping[bytes, bytes]:
    attestation = FEATURE_ATTESTATIONS[component]
    return {
        b"voice_coach_schema_version": (
            projection.PREDICTION_RECORD_SCHEMA_VERSION.encode()
        ),
        b"voice_coach_component": component.encode(),
        b"voice_coach_seed": str(seed).encode(),
        b"voice_coach_split": b"stack",
        b"voice_coach_source_lineage_sha256": source_lineage_sha256.encode(),
        b"voice_coach_source_checkpoint_sha256s": ",".join(
            source_checkpoint_sha256s
        ).encode(),
        b"voice_coach_projection_attestation_sha256": hashlib.sha256(
            canonical_json_bytes(attestation, pretty=False)
        )
        .hexdigest()
        .encode(),
    }


def _checkpoint_record(
    frozen_component: Mapping[str, Any],
    *,
    component: str,
    seed: int,
) -> Mapping[str, Any]:
    records = frozen_component.get("projection_checkpoints")
    if not isinstance(records, list):
        raise S106ComponentGeneratorError("frozen checkpoint list is absent")
    matches = [
        _mapping(record, f"{component} checkpoint")
        for record in records
        if isinstance(record, Mapping) and record.get("seed") == seed
    ]
    if len(matches) != 1:
        raise S106ComponentGeneratorError(f"{component} seed {seed} checkpoint differs")
    return matches[0]


def _checkpoint_hashes(
    frozen_component: Mapping[str, Any],
    *,
    component: str,
    seed: int,
) -> tuple[str, ...]:
    record = _checkpoint_record(
        frozen_component,
        component=component,
        seed=seed,
    )
    if component == "gop_acoustic_posterior":
        return tuple(
            _sha256(
                _mapping(record.get(f"{kind}_checkpoint"), kind).get("sha256"),
                f"{component} {kind} checkpoint",
            )
            for kind in ("acoustic", "error")
        )
    return (
        _sha256(
            _mapping(record.get("checkpoint"), "checkpoint").get("sha256"),
            f"{component} checkpoint",
        ),
    )


def _source_lineage(
    frozen_component: Mapping[str, Any],
    component: str,
) -> str:
    if component == "explicit":
        return _sha256(
            _mapping(
                _mapping(frozen_component.get("sources"), "explicit sources").get(
                    "registry"
                ),
                "explicit registry",
            ).get("sha256"),
            "explicit registry",
        )
    return _sha256(
        frozen_component.get("source_commit_sha256"),
        f"{component} source commit",
    )


def _prediction_record(
    *,
    root: Path,
    path: Path,
    row_count: int,
    component: str,
    seed: int,
    frozen_component: Mapping[str, Any],
    content_path: Path | None = None,
) -> Mapping[str, Any]:
    return {
        **dict(
            _file_record(
                root,
                path,
                row_count,
                content_path=content_path,
            )
        ),
        "schema_version": projection.PREDICTION_RECORD_SCHEMA_VERSION,
        "component": component,
        "seed": seed,
        "split": "stack",
        "source_lineage_sha256": _source_lineage(frozen_component, component),
        "source_checkpoint_sha256s": list(
            _checkpoint_hashes(
                frozen_component,
                component=component,
                seed=seed,
            )
        ),
        "projection_attestation": dict(FEATURE_ATTESTATIONS[component]),
    }


def _validate_backend_output(
    value: Mapping[str, Mapping[int, Sequence[float]]],
    *,
    phone_count: int,
) -> None:
    if tuple(value) != projection.COMPONENTS:
        raise S106ComponentGeneratorError("backend component order differs")
    for component in projection.COMPONENTS:
        seeds = value[component]
        if tuple(seeds) != projection.SEEDS:
            raise S106ComponentGeneratorError(f"{component} backend seed order differs")
        for seed in projection.SEEDS:
            probabilities: NDArray[np.float64] = np.asarray(
                seeds[seed],
                dtype=np.float64,
            )
            if (
                probabilities.shape != (phone_count,)
                or not np.isfinite(probabilities).all()
                or np.any((probabilities < 0.0) | (probabilities > 1.0))
            ):
                raise S106ComponentGeneratorError(
                    f"{component} seed {seed} probabilities differ"
                )


def _feature_digest_payload(
    *,
    execution_lock_record: Mapping[str, Any],
    stack_source_record: Mapping[str, Any],
    identity_record: Mapping[str, Any],
    component_records: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Mapping[str, Any]:
    return {
        "execution_lock": dict(execution_lock_record),
        "stack_source": dict(stack_source_record),
        "feature_identity_matrix": dict(identity_record),
        "components": {
            component: {
                f"seed_{seed}": dict(component_records[component][f"seed_{seed}"])
                for seed in projection.SEEDS
            }
            for component in projection.COMPONENTS
        },
    }


def build_feature_freeze_receipt(
    *,
    generation_id: str,
    canonical_row_key_order_sha256: str,
    row_count: int,
    execution_lock_record: Mapping[str, Any],
    stack_source_record: Mapping[str, Any],
    identity_record: Mapping[str, Any],
    component_records: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Mapping[str, Any]:
    """Bind the complete label-free universe before labels can be requested."""

    _sha256(generation_id, "generation_id")
    _sha256(
        canonical_row_key_order_sha256,
        "canonical_row_key_order_sha256",
    )
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count <= 0:
        raise S106ComponentGeneratorError("feature-freeze row_count is invalid")
    payload = _feature_digest_payload(
        execution_lock_record=execution_lock_record,
        stack_source_record=stack_source_record,
        identity_record=identity_record,
        component_records=component_records,
    )
    digest = hashlib.sha256(canonical_json_bytes(payload, pretty=False)).hexdigest()
    receipt: dict[str, Any] = {
        "schema_version": FEATURE_FREEZE_SCHEMA_VERSION,
        "status": "committed",
        "generation_id": generation_id,
        "feature_digest_sha256": digest,
        "canonical_row_key_order_sha256": canonical_row_key_order_sha256,
        "row_count": row_count,
        **payload,
        "audit": {
            "identity_and_prediction_files_frozen": True,
            "prediction_file_count": 12,
            "all_twelve_predictions_exact_key_order": True,
            "phone_alignments_read": False,
            "operation_read": False,
            "binary_label_read": False,
            "label_matrix_present": False,
            "feature_builder_received_labels": False,
            "realized_phone_identity_used": False,
            "alignment_confidence_source": "absent",
        },
    }
    receipt["binding_sha256"] = _binding(receipt)
    return receipt


def validate_feature_freeze_receipt(
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    expected_keys = {
        "schema_version",
        "status",
        "generation_id",
        "feature_digest_sha256",
        "canonical_row_key_order_sha256",
        "row_count",
        "execution_lock",
        "stack_source",
        "feature_identity_matrix",
        "components",
        "audit",
        "binding_sha256",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version") != FEATURE_FREEZE_SCHEMA_VERSION
        or receipt.get("status") != "committed"
        or receipt.get("binding_sha256") != _binding(receipt)
        or receipt.get("audit")
        != {
            "identity_and_prediction_files_frozen": True,
            "prediction_file_count": 12,
            "all_twelve_predictions_exact_key_order": True,
            "phone_alignments_read": False,
            "operation_read": False,
            "binary_label_read": False,
            "label_matrix_present": False,
            "feature_builder_received_labels": False,
            "realized_phone_identity_used": False,
            "alignment_confidence_source": "absent",
        }
    ):
        raise S106ComponentGeneratorError("feature-freeze receipt differs")
    components = _mapping(receipt.get("components"), "receipt components")
    if set(components) != set(projection.COMPONENTS):
        raise S106ComponentGeneratorError("receipt component set differs")
    for component in projection.COMPONENTS:
        seeds = _mapping(components.get(component), f"receipt {component}")
        if set(seeds) != {f"seed_{seed}" for seed in projection.SEEDS}:
            raise S106ComponentGeneratorError(f"receipt {component} seed set differs")
        for seed in projection.SEEDS:
            _mapping(
                seeds.get(f"seed_{seed}"),
                f"receipt {component} seed {seed}",
            )
    payload = _feature_digest_payload(
        execution_lock_record=_mapping(
            receipt.get("execution_lock"),
            "receipt execution lock",
        ),
        stack_source_record=_mapping(
            receipt.get("stack_source"),
            "receipt stack source",
        ),
        identity_record=_mapping(
            receipt.get("feature_identity_matrix"),
            "receipt identity",
        ),
        component_records=cast(
            Mapping[str, Mapping[str, Mapping[str, Any]]],
            components,
        ),
    )
    expected = hashlib.sha256(canonical_json_bytes(payload, pretty=False)).hexdigest()
    if receipt.get("feature_digest_sha256") != expected:
        raise S106ComponentGeneratorError("feature-freeze digest differs")
    _sha256(
        receipt.get("canonical_row_key_order_sha256"),
        "receipt canonical row-key order",
    )
    row_count = receipt.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count <= 0:
        raise S106ComponentGeneratorError("receipt row count differs")
    return receipt


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    try:
        pairs = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=list)
    except (OSError, json.JSONDecodeError) as error:
        raise S106ComponentGeneratorError(f"cannot read {name}") from error

    def convert(value: object) -> object:
        if isinstance(value, list) and all(
            isinstance(item, tuple) and len(item) == 2 for item in value
        ):
            output: dict[str, Any] = {}
            for raw_key, raw_value in cast(list[tuple[object, object]], value):
                if not isinstance(raw_key, str) or raw_key in output:
                    raise S106ComponentGeneratorError(
                        f"{name} has duplicate/non-string key"
                    )
                output[raw_key] = convert(raw_value)
            return output
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return _mapping(convert(pairs), name)


def _stable_file_bytes(path: Path, name: str) -> bytes:
    expected_sha256, size, before_identity = _stable_source_snapshot(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError(f"{name} stable open failed") from error
    try:
        payload = b""
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = os.stat(path, follow_symlinks=False)
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    path_identity = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mode,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    )
    if (
        len(payload) != size
        or hashlib.sha256(payload).hexdigest() != expected_sha256
        or after_identity != before_identity
        or path_identity != before_identity
    ):
        raise S106ComponentGeneratorError(f"{name} changed during stable read")
    return payload


def _load_json_stable(path: Path, name: str) -> Mapping[str, Any]:
    return _strict_json_bytes(_stable_file_bytes(path, name), name)


def _validate_frozen_feature_universe(
    *,
    root: Path,
    receipt: Mapping[str, Any],
    expected_utterances: Sequence[LabelFreeUtterance],
) -> tuple[tuple[str, int], ...]:
    """Recheck receipt-bound bytes before the first alignment-column request."""

    lock_record = _mapping(receipt.get("execution_lock"), "receipt execution lock")
    lock_path = _resolve_file(root, lock_record.get("path"), "receipt execution lock")
    lock_payload = _stable_file_bytes(lock_path, "receipt execution lock")
    if hashlib.sha256(lock_payload).hexdigest() != lock_record.get("sha256"):
        raise S106ComponentGeneratorError("receipt execution-lock bytes differ")
    identity_record = _mapping(
        receipt.get("feature_identity_matrix"),
        "receipt feature identity",
    )
    identity_table = _stable_parquet_from_record(
        root=root,
        record=identity_record,
        name="pre-label feature identity",
    )
    if tuple(identity_table.column_names) != FEATURE_IDENTITY_COLUMNS:
        raise S106ComponentGeneratorError("pre-label feature identity columns differ")
    rows = identity_table.to_pylist()
    expected_rows = [
        (
            utterance.utterance_id_sha256,
            utterance.speaker_id_sha256,
            phone_index,
        )
        for utterance in expected_utterances
        for phone_index in range(len(utterance.target_intervals))
    ]
    observed_rows = [
        (
            str(row.get("utterance_id_sha256")),
            str(row.get("speaker_id_sha256")),
            int(row.get("phone_index")),
        )
        for row in rows
    ]
    if observed_rows != expected_rows or any(
        row.get("split") != "stack"
        or row.get("alignment_confidence") is not None
        or row.get("qc_flagged") is not False
        or row.get("reserved_prompt") is not False
        for row in rows
    ):
        raise S106ComponentGeneratorError("pre-label identity universe/order differs")
    identity_keys = tuple(
        (utterance, phone_index) for utterance, _, phone_index in observed_rows
    )
    digest = hashlib.sha256(
        canonical_json_bytes(
            [[utterance, phone_index] for utterance, phone_index in identity_keys],
            pretty=False,
        )
    ).hexdigest()
    if receipt.get("canonical_row_key_order_sha256") != digest or receipt.get(
        "row_count"
    ) != len(identity_keys):
        raise S106ComponentGeneratorError("pre-label receipt key/order digest differs")
    components = _mapping(receipt.get("components"), "receipt components")
    for component in projection.COMPONENTS:
        seeds = _mapping(components.get(component), f"receipt {component}")
        for seed in projection.SEEDS:
            record = _mapping(
                seeds.get(f"seed_{seed}"),
                f"receipt {component} seed {seed}",
            )
            table = _stable_parquet_from_record(
                root=root,
                record=record,
                name=f"pre-label {component} seed {seed}",
            )
            if tuple(
                table.column_names
            ) != PREDICTION_COLUMNS or table.schema.metadata != _prediction_metadata(
                component=component,
                seed=seed,
                source_lineage_sha256=str(record["source_lineage_sha256"]),
                source_checkpoint_sha256s=cast(
                    Sequence[str],
                    record["source_checkpoint_sha256s"],
                ),
            ):
                raise S106ComponentGeneratorError(
                    f"pre-label {component} seed {seed} schema differs"
                )
            prediction_rows = table.to_pylist()
            prediction_identity = [
                (
                    str(row.get("utterance_id_sha256")),
                    str(row.get("speaker_id_sha256")),
                    int(row.get("phone_index")),
                )
                for row in prediction_rows
            ]
            if prediction_identity != observed_rows:
                raise S106ComponentGeneratorError(
                    f"pre-label {component} seed {seed} key/order differs"
                )
            for row in prediction_rows:
                probability = row.get("probability")
                if (
                    row.get("split") != "stack"
                    or isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not math.isfinite(float(probability))
                    or not 0.0 <= float(probability) <= 1.0
                ):
                    raise S106ComponentGeneratorError(
                        f"pre-label {component} seed {seed} value differs"
                    )
    return identity_keys


def _canonical_row_key_digest(
    utterances: Sequence[LabelFreeUtterance],
) -> tuple[str, int]:
    digest = hashlib.sha256()
    digest.update(b"[")
    row_count = 0
    for utterance in utterances:
        for phone_index in range(len(utterance.target_intervals)):
            if row_count:
                digest.update(b",")
            digest.update(
                canonical_json_bytes(
                    [utterance.utterance_id_sha256, phone_index],
                    pretty=False,
                ).rstrip(b"\n")
            )
            row_count += 1
    digest.update(b"]\n")
    return digest.hexdigest(), row_count


def materialize_label_rows(
    *,
    workspace_root: Path,
    stack_path: Path,
    receipt_path: Path,
    expected_receipt_sha256: str,
    expected_utterances: Sequence[LabelFreeUtterance],
    eligible_source_ids: Sequence[str],
    batch_size: int = projection.MAXIMUM_PROJECTION_BATCH_ROWS,
    event_hook: Callable[[str], None] | None = None,
    row_consumer: Callable[[Sequence[Mapping[str, Any]]], None] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Open operations only after validating an on-disk feature-freeze receipt."""

    if event_hook is not None:
        event_hook("validate_feature_freeze_receipt")
    root = workspace_root.resolve(strict=True)
    expected_receipt_sha256 = _sha256(
        expected_receipt_sha256,
        "expected feature-freeze receipt SHA-256",
    )
    receipt_bytes = _stable_file_bytes(receipt_path, "feature-freeze receipt")
    if hashlib.sha256(receipt_bytes).hexdigest() != expected_receipt_sha256:
        raise S106ComponentGeneratorError(
            "published feature-freeze receipt changed before label open"
        )
    receipt = _strict_json_bytes(receipt_bytes, "feature-freeze receipt")
    validate_feature_freeze_receipt(receipt)
    receipt_stack = _mapping(receipt.get("stack_source"), "receipt stack source")
    expected_stack_sha256 = _sha256(
        receipt_stack.get("sha256"),
        "receipt stack SHA-256",
    )
    if (
        _resolve_file(
            root,
            receipt_stack.get("path"),
            "receipt stack path",
        )
        != stack_path
    ):
        raise S106ComponentGeneratorError("receipt stack path differs")
    before_sha256, before_size, before_identity = _stable_source_snapshot(stack_path)
    if before_sha256 != expected_stack_sha256:
        raise S106ComponentGeneratorError(
            "stack manifest changed before label materialization"
        )
    frozen: dict[str, LabelFreeUtterance] = {
        value.utterance_id_sha256: value for value in expected_utterances
    }
    if len(frozen) != len(expected_utterances):
        raise S106ComponentGeneratorError("frozen utterance universe is duplicated")
    eligible_ids = tuple(eligible_source_ids)
    if (
        len(eligible_ids) != len(expected_utterances)
        or len(eligible_ids) != len(set(eligible_ids))
        or any(not isinstance(value, str) or not value for value in eligible_ids)
    ):
        raise S106ComponentGeneratorError(
            "eligible label-scanner source IDs do not match the frozen universe"
        )
    from voice_coach.training.wavlm_adapter_training import hash_utterance_id

    if tuple(hash_utterance_id(value) for value in eligible_ids) != tuple(
        value.utterance_id_sha256 for value in expected_utterances
    ):
        raise S106ComponentGeneratorError(
            "eligible label-scanner source IDs do not bind frozen hashes"
        )
    eligible_id_set = frozenset(eligible_ids)
    key_digest, expected_row_count = _canonical_row_key_digest(expected_utterances)
    if (
        receipt.get("canonical_row_key_order_sha256") != key_digest
        or receipt.get("row_count") != expected_row_count
    ):
        raise S106ComponentGeneratorError(
            "feature-freeze receipt does not bind the expected key order"
        )
    _validate_frozen_feature_universe(
        root=root,
        receipt=receipt,
        expected_utterances=expected_utterances,
    )
    if event_hook is not None:
        event_hook("open_phone_alignments")
    labels_by_utterance: dict[str, tuple[int, ...]] = {}
    observed: set[str] = set()
    for batch in _held_source_batches(
        stack_path,
        expected_sha256=expected_stack_sha256,
        name="label-materializer stack manifest",
        columns=LABEL_MATERIALIZER_COLUMNS,
        batch_size=batch_size,
        eligible_source_ids=eligible_ids,
    ):
        for row in batch.to_pylist():
            if row.get("split") != "stack":
                raise S106ComponentGeneratorError(
                    "label scanner returned a non-stack row"
                )
            raw_source_id = row.get("id")
            if raw_source_id not in eligible_id_set:
                raise S106ComponentGeneratorError(
                    "label scanner returned a non-frozen raw source ID"
                )
            utterance_hash = hash_utterance_id(raw_source_id)
            expected = frozen.get(utterance_hash)
            if expected is None:
                raise S106ComponentGeneratorError(
                    "label scanner returned a non-frozen utterance"
                )
            if utterance_hash in observed:
                raise S106ComponentGeneratorError(
                    "label materializer utterance is duplicated"
                )
            observed.add(utterance_hash)
            speaker = _sha256(row.get("speaker_id"), "label speaker")
            if speaker != expected.speaker_id_sha256:
                raise S106ComponentGeneratorError(
                    "label materializer speaker binding differs"
                )
            targets = cast(
                tuple[TargetInterval, ...],
                _intervals(
                    row.get("target_phone_intervals"),
                    name="label target_phone_intervals",
                    with_label=True,
                ),
            )
            if targets != expected.target_intervals:
                raise S106ComponentGeneratorError(
                    "target intervals changed after feature freeze"
                )
            raw_alignments = row.get("phone_alignments")
            if not isinstance(raw_alignments, list):
                raise S106ComponentGeneratorError("phone_alignments must be a list")
            alignments = [
                _mapping(value, "phone alignment")
                for value in raw_alignments
                if isinstance(value, Mapping) and value.get("target_label") is not None
            ]
            if len(alignments) != len(targets):
                raise S106ComponentGeneratorError(
                    "target-bearing alignment cardinality differs"
                )
            alignment_labels: list[int] = []
            for target, alignment in zip(targets, alignments, strict=True):
                operation = alignment.get("operation")
                if str(
                    alignment.get("target_label") or ""
                ).strip() != target.label or operation not in {
                    "match",
                    "substitution",
                    "deletion",
                }:
                    raise S106ComponentGeneratorError(
                        "target alignment contract differs"
                    )
                alignment_labels.append(int(operation != "match"))
            labels_by_utterance[utterance_hash] = tuple(alignment_labels)
    if observed != set(frozen):
        raise S106ComponentGeneratorError(
            "label materializer does not cover frozen utterances"
        )
    if (
        set(labels_by_utterance) != set(frozen)
        or sum(len(values) for values in labels_by_utterance.values())
        != expected_row_count
    ):
        raise S106ComponentGeneratorError(
            "label matrix does not exact-key match feature universe"
        )
    output: list[Mapping[str, Any]] = []
    pending: list[Mapping[str, Any]] = []
    for utterance in expected_utterances:
        utterance_labels = labels_by_utterance[utterance.utterance_id_sha256]
        for phone_index, label in enumerate(utterance_labels):
            row = {
                "utterance_id_sha256": utterance.utterance_id_sha256,
                "speaker_id_sha256": utterance.speaker_id_sha256,
                "phone_index": phone_index,
                "split": "stack",
                "reference_binary_error_label": label,
            }
            if row_consumer is None:
                output.append(row)
            else:
                pending.append(row)
                if len(pending) >= batch_size:
                    row_consumer(tuple(pending))
                    pending.clear()
    if row_consumer is not None and pending:
        row_consumer(tuple(pending))
    after_sha256, after_size, after_identity = _stable_source_snapshot(stack_path)
    if (
        after_sha256 != before_sha256
        or after_size != before_size
        or after_identity != before_identity
    ):
        raise S106ComponentGeneratorError(
            "stack manifest changed during label materialization"
        )
    return tuple(output)


def _fsync_directory(path: Path) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError(
            f"cannot open publication directory for fsync: {path.name}"
        ) from error
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, document: Mapping[str, Any]) -> bool:
    payload = canonical_json_bytes(document, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.lstat(path)
    except FileNotFoundError:
        path_exists = False
    else:
        path_exists = True
    if path_exists:
        _require_immutable_regular_file(path, f"existing {path.name}")
        if _stable_file_bytes(path, f"existing {path.name}") != payload:
            raise S106ComponentGeneratorError(
                f"immutable JSON already differs: {path.name}"
            )
        return False
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), 0o440)
            os.fsync(handle.fileno())
        os.link(temporary, path)
        created = True
    except FileExistsError:
        created = False
        _require_immutable_regular_file(path, f"raced {path.name}")
        if _stable_file_bytes(path, f"raced {path.name}") != payload:
            raise S106ComponentGeneratorError(
                f"immutable JSON publication raced: {path.name}"
            )
    finally:
        temporary.unlink(missing_ok=True)
    _require_immutable_regular_file(path, path.name)
    _fsync_directory(path.parent)
    return created


def _materialize_file(source: Path, destination: Path) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_descriptor = os.open(source, flags)
    except OSError as error:
        raise S106ComponentGeneratorError(
            f"cannot no-follow open source {source.name}"
        ) from error
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S106ComponentGeneratorError("materialization source is not regular")
        created = _materialize_descriptor(source_descriptor, destination)
        after = os.fstat(source_descriptor)
        path_after = os.stat(source, follow_symlinks=False)
        if _source_identity(before) != _source_identity(after) or _source_identity(
            before
        ) != _source_identity(path_after):
            raise S106ComponentGeneratorError(
                f"materialization source changed: {source.name}"
            )
        return created
    finally:
        os.close(source_descriptor)


def _materialize_descriptor(source_descriptor: int, destination: Path) -> bool:
    """Publish held regular bytes through an O_EXCL temporary and hard-link."""

    source_information = os.fstat(source_descriptor)
    if not stat.S_ISREG(source_information.st_mode):
        raise S106ComponentGeneratorError("materialization descriptor is not regular")
    source_sha256, source_size = _descriptor_digest(source_descriptor)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.lstat(destination)
    except FileNotFoundError:
        destination_exists = False
    else:
        destination_exists = True
    if destination_exists:
        _require_immutable_regular_file(destination, f"existing {destination.name}")
        if (
            destination.stat().st_size != source_size
            or sha256_file(destination) != source_sha256
        ):
            raise S106ComponentGeneratorError(
                f"immutable output differs: {destination.name}"
            )
        return False
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.copy-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination_handle:
            os.lseek(source_descriptor, 0, os.SEEK_SET)
            while True:
                block = os.read(source_descriptor, 1024 * 1024)
                if not block:
                    break
                destination_handle.write(block)
            os.lseek(source_descriptor, 0, os.SEEK_SET)
            destination_handle.flush()
            os.fchmod(destination_handle.fileno(), 0o440)
            os.fsync(destination_handle.fileno())
        try:
            os.link(temporary, destination)
            created = True
        except FileExistsError:
            created = False
            _require_immutable_regular_file(destination, f"raced {destination.name}")
            if (
                destination.stat().st_size != source_size
                or sha256_file(destination) != source_sha256
            ):
                raise S106ComponentGeneratorError(
                    f"immutable output publication raced: {destination.name}"
                )
    finally:
        temporary.unlink(missing_ok=True)
    _require_immutable_regular_file(destination, destination.name)
    _fsync_directory(destination.parent)
    return created


def _require_immutable_regular_file(path: Path, name: str) -> os.stat_result:
    try:
        information = os.lstat(path)
    except OSError as error:
        raise S106ComponentGeneratorError(f"{name} is absent") from error
    if (
        not stat.S_ISREG(information.st_mode)
        or stat.S_ISLNK(information.st_mode)
        or stat.S_IMODE(information.st_mode) != 0o440
        or information.st_nlink != 1
    ):
        raise S106ComponentGeneratorError(
            f"{name} must be a sealed single-link regular file"
        )
    return information


def _verify_generation_collision(staged: Path, existing: Path) -> None:
    def records(
        root: Path,
        *,
        sealed: bool,
    ) -> tuple[frozenset[str], Mapping[str, tuple[str, int]]]:
        root_information = os.lstat(root)
        if not stat.S_ISDIR(root_information.st_mode) or stat.S_ISLNK(
            root_information.st_mode
        ):
            raise S106ComponentGeneratorError(
                "component generation collision root is not a no-follow directory"
            )
        if sealed and stat.S_IMODE(root_information.st_mode) != 0o550:
            raise S106ComponentGeneratorError(
                "content-addressed generation collision root mode differs"
            )
        output: dict[str, tuple[str, int]] = {}
        directories: set[str] = set()
        for path in sorted(root.rglob("*")):
            information = os.lstat(path)
            if stat.S_ISLNK(information.st_mode):
                raise S106ComponentGeneratorError(
                    "component generation cannot contain a symlink"
                )
            if stat.S_ISREG(information.st_mode):
                if information.st_nlink != 1:
                    raise S106ComponentGeneratorError(
                        "component generation cannot contain a hard-linked file"
                    )
                if sealed and stat.S_IMODE(information.st_mode) != 0o440:
                    raise S106ComponentGeneratorError(
                        "content-addressed generation collision file mode differs"
                    )
                relative = path.relative_to(root).as_posix()
                output[relative] = (sha256_file(path), information.st_size)
            elif stat.S_ISDIR(information.st_mode):
                if sealed and stat.S_IMODE(information.st_mode) != 0o550:
                    raise S106ComponentGeneratorError(
                        "content-addressed generation collision directory mode differs"
                    )
                directories.add(path.relative_to(root).as_posix())
            else:
                raise S106ComponentGeneratorError(
                    "component generation contains a non-regular entry"
                )
        return frozenset(directories), output

    staged_records = records(staged, sealed=False)
    if staged_records != records(existing, sealed=False):
        raise S106ComponentGeneratorError(
            "content-addressed component generation collision differs"
        )
    # A crash can occur after the atomic generation rename but before sealing.
    # Exact bytes/tree with no links are safe to finish sealing on retry.
    _seal_generation_tree(existing)
    if staged_records != records(existing, sealed=True):
        raise S106ComponentGeneratorError(
            "content-addressed generation collision seal differs"
        )


def _seal_generation_tree(root: Path) -> None:
    directories_to_sync: set[Path] = set()
    for path in sorted(root.rglob("*"), reverse=True):
        information = os.lstat(path)
        if stat.S_ISLNK(information.st_mode):
            raise S106ComponentGeneratorError(
                "component generation cannot seal a symlink"
            )
        if stat.S_ISREG(information.st_mode):
            if information.st_nlink != 1:
                raise S106ComponentGeneratorError(
                    "component generation cannot seal a hard-linked file"
                )
            path.chmod(0o440)
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            directories_to_sync.add(path.parent)
        elif stat.S_ISDIR(information.st_mode):
            path.chmod(0o550)
            directories_to_sync.add(path)
            directories_to_sync.add(path.parent)
        else:
            raise S106ComponentGeneratorError(
                "component generation cannot seal a non-regular entry"
            )
    root_information = os.lstat(root)
    if not stat.S_ISDIR(root_information.st_mode) or stat.S_ISLNK(
        root_information.st_mode
    ):
        raise S106ComponentGeneratorError(
            "component generation root must be a no-follow directory"
        )
    root.chmod(0o550)
    directories_to_sync.add(root)
    directories_to_sync.add(root.parent)
    for directory in sorted(directories_to_sync, key=lambda value: len(value.parts)):
        _fsync_directory(directory)


def _default_preflight(
    root: Path,
    main_config_path: Path,
) -> tuple[Mapping[str, Any], Path]:
    return fusion_contract.prepare_s106_execution(
        workspace_root=root,
        config_path=main_config_path,
    )


def _producer_identity(
    main_config: Mapping[str, Any],
    *,
    production: bool,
) -> Mapping[str, Any]:
    if not production:
        return {
            "mode": "synthetic_test_only",
            "production_schema_allowed": False,
            "dependency_injection_allowed": True,
        }
    implementation = _mapping(main_config.get("implementation"), "implementation")
    roles = (
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
    )
    return {
        "mode": "production",
        "production_schema_allowed": True,
        "dependency_injection_allowed": False,
        "backend_factory": (
            "voice_coach.training.wavlm_s106_component_generator."
            "production_backend_factory"
        ),
        "preflight": (
            "voice_coach.training.wavlm_s106_fusion_contract.prepare_s106_execution"
        ),
        "bundle_builder": (
            "voice_coach.training.wavlm_s106_component_projection."
            "build_component_bundle_document"
        ),
        "pinned_sources": {
            role: dict(_mapping(implementation.get(role), f"implementation.{role}"))
            for role in roles
        },
    }


def _validate_canonical_generator_config_binding(
    *,
    root: Path,
    generator_config_path: Path,
    main_config: Mapping[str, Any],
) -> None:
    implementation = _mapping(main_config.get("implementation"), "implementation")
    record = _mapping(
        implementation.get("component_generator_config"),
        "implementation.component_generator_config",
    )
    canonical_path = _resolve_file(
        root,
        record.get("path"),
        "canonical component generator config",
    )
    if generator_config_path != canonical_path or sha256_file(
        generator_config_path
    ) != _sha256(
        record.get("sha256"),
        "canonical component generator config SHA-256",
    ):
        raise S106ComponentGeneratorError(
            "production generator config is not the canonical pinned source"
        )


def _invoke_fault(injector: FaultInjector | None, stage: str) -> None:
    if injector is not None:
        injector(stage)


def _production_lock_path(root: Path, generator_config_path: Path) -> Path:
    identity = hashlib.sha256(
        f"{root.resolve(strict=True)}\0{generator_config_path.resolve(strict=True)}".encode(
            "utf-8"
        )
    ).hexdigest()
    return Path(tempfile.gettempdir()) / f"voice-coach-s106-production-{identity}.lock"


@contextmanager
def _production_publish_lock(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> Iterator[Path]:
    """Serialize production publication with a crash-released advisory lock."""

    lock_path = _production_lock_path(workspace_root, generator_config_path)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise S106ComponentGeneratorError(
            "production publication lock open failed"
        ) from error
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_nlink != 1
            or information.st_uid != os.geteuid()
        ):
            raise S106ComponentGeneratorError(
                "production publication lock integrity differs"
            )
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise S106ComponentGeneratorError(
                "production component generation is already active"
            ) from error
        yield lock_path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _run_component_generation_core(
    *,
    workspace_root: Path,
    generator_config_path: Path,
    backend_factory: Callable[
        [Path, Mapping[str, Any], Mapping[str, Any], Sequence[LabelFreeUtterance]],
        ProjectionBackend,
    ],
    preflight: Preflight = _default_preflight,
    bundle_builder: BundleBuilder = projection.build_component_bundle_document,
    fault_injector: FaultInjector | None = None,
    label_event_hook: Callable[[str], None] | None = None,
    production: bool,
) -> GeneratedComponentInputs:
    if not production:
        return _run_component_generation_core_impl(
            workspace_root=workspace_root,
            generator_config_path=generator_config_path,
            backend_factory=backend_factory,
            preflight=preflight,
            bundle_builder=bundle_builder,
            fault_injector=fault_injector,
            label_event_hook=label_event_hook,
            production=False,
        )
    root = workspace_root.resolve(strict=True)
    generator_path = generator_config_path
    if not generator_path.is_absolute():
        generator_path = root / generator_path
    generator_path = generator_path.resolve(strict=True)
    with _production_publish_lock(
        workspace_root=root,
        generator_config_path=generator_path,
    ):
        return _run_component_generation_core_impl(
            workspace_root=root,
            generator_config_path=generator_path,
            backend_factory=backend_factory,
            preflight=preflight,
            bundle_builder=bundle_builder,
            fault_injector=fault_injector,
            label_event_hook=label_event_hook,
            production=True,
        )


def _run_component_generation_core_impl(
    *,
    workspace_root: Path,
    generator_config_path: Path,
    backend_factory: Callable[
        [Path, Mapping[str, Any], Mapping[str, Any], Sequence[LabelFreeUtterance]],
        ProjectionBackend,
    ],
    preflight: Preflight = _default_preflight,
    bundle_builder: BundleBuilder = projection.build_component_bundle_document,
    fault_injector: FaultInjector | None = None,
    label_event_hook: Callable[[str], None] | None = None,
    production: bool,
) -> GeneratedComponentInputs:
    """Generate and publish exact S106 inputs after the frozen predecessor gate."""

    root = workspace_root.resolve(strict=True)
    generator_path = generator_config_path
    if not generator_path.is_absolute():
        generator_path = root / generator_path
    generator_path = generator_path.resolve(strict=True)
    generator_config = load_generator_config(generator_path)
    generator_section = _mapping(generator_config["generator"], "generator")
    main_config_path = _resolve_file(
        root,
        generator_section["main_config"],
        "generator.main_config",
    )
    main_config = fusion_contract.load_config(main_config_path)
    fusion_contract.validate_config(main_config)
    if production:
        _validate_canonical_generator_config_binding(
            root=root,
            generator_config_path=generator_path,
            main_config=main_config,
        )
    if production and (
        backend_factory is not production_backend_factory
        or preflight is not _default_preflight
        or bundle_builder is not projection.build_component_bundle_document
        or fault_injector is not None
        or label_event_hook is not None
    ):
        raise S106ComponentGeneratorError(
            "production component generation dependencies are immutable"
        )
    producer_identity = _producer_identity(main_config, production=production)
    # The preflight is the sole authority that may cross the predecessor gate.
    # On a missing S104/S105 commit it fails before explicit/B07/stack/output.
    execution_lock, execution_lock_path = preflight(root, main_config_path)
    if (
        execution_lock.get("schema_version") != projection.EXECUTION_LOCK_SCHEMA_VERSION
        or execution_lock.get("status") != "ready"
    ):
        raise S106ComponentGeneratorError("preflight did not return a ready v2 lock")
    _invoke_fault(fault_injector, "after_predecessor_validation")
    outputs = _mapping(generator_config["outputs"], "outputs")
    visible_bundle = root / str(outputs["component_bundle_commit"])
    if visible_bundle.is_file():
        _validate_component_generation_commit(
            workspace_root=root,
            generator_config_path=generator_path,
            expected_producer_mode=(
                "production" if production else "synthetic_test_only"
            ),
        )
        recovered_receipt = _load_json_stable(
            root / str(outputs["feature_freeze_receipt"]),
            "recovered feature-freeze receipt",
        )
        recovered_bundle = _load_json_stable(
            visible_bundle,
            "recovered component bundle",
        )
        recovered_commit = _load_json_stable(
            root / str(outputs["generator_commit"]),
            "recovered generator commit",
        )
        recovered_registry = _load_json_stable(
            root / str(outputs["registry"]),
            "recovered generator registry",
        )
        return GeneratedComponentInputs(
            generation_id=str(recovered_commit["generation_id"]),
            feature_freeze_receipt=recovered_receipt,
            label_record=_mapping(
                recovered_commit["label_matrix"],
                "recovered label record",
            ),
            bundle=recovered_bundle,
            commit=recovered_commit,
            registry=recovered_registry,
        )

    stack_record = _mapping(execution_lock.get("stack_source"), "stack source")
    stack_path = _resolve_file(root, stack_record.get("path"), "stack source")
    if sha256_file(stack_path) != stack_record.get("sha256"):
        raise S106ComponentGeneratorError("stack source changed after preflight")
    inputs = _mapping(generator_config["inputs"], "inputs")
    reservation_path = _resolve_file(
        root,
        inputs["prompt_challenge_reservation"],
        "prompt challenge reservation",
    )
    _, reservation_bytes = _read_bound_bytes(
        root=root,
        record={
            "path": str(inputs["prompt_challenge_reservation"]),
            "sha256": str(inputs["prompt_challenge_reservation_sha256"]),
            "size_bytes": reservation_path.stat().st_size,
        },
        name="prompt challenge reservation",
    )
    reserved_prompt_hashes = _reserved_prompt_hashes(
        _strict_json_bytes(
            reservation_bytes,
            "prompt challenge reservation",
        )
    )
    scan = scan_label_free_stack(
        stack_path=stack_path,
        reserved_prompt_hashes=reserved_prompt_hashes,
        expected_sha256=str(stack_record["sha256"]),
    )
    utterances = scan.utterances
    _invoke_fault(fault_injector, "after_label_free_scan")

    # Checkpoint probes and GPU/model initialization happen only now.
    try:
        backend = backend_factory(
            root,
            generator_config,
            execution_lock,
            utterances,
        )
    except S106ComponentGeneratorError:
        raise
    except Exception as error:
        raise S106ComponentGeneratorError(
            "checkpoint probe or production model initialization failed"
        ) from error
    try:
        _invoke_fault(fault_injector, "after_checkpoint_probes")
        staging_root = _resolve_output(root, outputs["staging_root"], "staging root")
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="generation-", dir=staging_root))
    except Exception:
        try:
            backend.close()
        except Exception:
            pass
        raise
    created_outputs: list[Path] = []
    committed_bundle = False
    try:
        frozen_components = _mapping(
            execution_lock.get("frozen_components"),
            "frozen components",
        )
        identity_path = staging / "feature_identity.parquet"
        identity_sink = _StableParquetSink(
            path=identity_path,
            schema=IDENTITY_SCHEMA,
            metadata=None,
            batch_rows=projection.MAXIMUM_PROJECTION_BATCH_ROWS,
        )
        prediction_sinks: dict[tuple[str, int], _StableParquetSink] = {}
        for component in projection.COMPONENTS:
            frozen_component = _mapping(
                frozen_components.get(component),
                f"frozen {component}",
            )
            source_lineage = _source_lineage(frozen_component, component)
            for seed in projection.SEEDS:
                hashes = _checkpoint_hashes(
                    frozen_component,
                    component=component,
                    seed=seed,
                )
                prediction_sinks[(component, seed)] = _StableParquetSink(
                    path=(
                        staging
                        / "predictions"
                        / component
                        / f"seed_{seed}_stack_predictions.parquet"
                    ),
                    schema=PREDICTION_SCHEMA,
                    metadata=_prediction_metadata(
                        component=component,
                        seed=seed,
                        source_lineage_sha256=source_lineage,
                        source_checkpoint_sha256s=hashes,
                    ),
                    batch_rows=projection.MAXIMUM_PROJECTION_BATCH_ROWS,
                )
        for utterance in utterances:
            identity_rows = [
                {
                    "utterance_id_sha256": utterance.utterance_id_sha256,
                    "speaker_id_sha256": utterance.speaker_id_sha256,
                    "phone_index": phone_index,
                    "split": "stack",
                    "alignment_confidence": None,
                    "qc_flagged": False,
                    "reserved_prompt": False,
                }
                for phone_index in range(len(utterance.target_intervals))
            ]
            identity_sink.append(identity_rows)
            try:
                projected = backend.project(utterance)
            except S106ComponentGeneratorError:
                raise
            except Exception as error:
                raise S106ComponentGeneratorError(
                    "label-free component projection failed"
                ) from error
            _validate_backend_output(
                projected,
                phone_count=len(utterance.target_intervals),
            )
            for component in projection.COMPONENTS:
                for seed in projection.SEEDS:
                    prediction_sinks[(component, seed)].append(
                        [
                            {
                                "utterance_id_sha256": utterance.utterance_id_sha256,
                                "speaker_id_sha256": utterance.speaker_id_sha256,
                                "phone_index": phone_index,
                                "split": "stack",
                                "probability": float(probability),
                            }
                            for phone_index, probability in enumerate(
                                projected[component][seed]
                            )
                        ]
                    )
        identity_sink.close()
        for sink in prediction_sinks.values():
            sink.close()
            if sink.row_count != identity_sink.row_count:
                raise S106ComponentGeneratorError(
                    "one of twelve predictions is not exact-universe complete"
                )
        backend.close()
        _invoke_fault(fault_injector, "after_feature_files")

        final_identity = _resolve_output(
            root,
            outputs["feature_identity_matrix"],
            "feature identity output",
        )
        identity_record = _file_record(
            root,
            final_identity,
            identity_sink.row_count,
            content_path=identity_path,
        )
        component_records: dict[str, dict[str, Mapping[str, Any]]] = {}
        for component in projection.COMPONENTS:
            frozen_component = _mapping(
                frozen_components.get(component),
                f"frozen {component}",
            )
            seeds: dict[str, Mapping[str, Any]] = {}
            for seed in projection.SEEDS:
                staged_path = prediction_sinks[(component, seed)].path
                final_path = _resolve_output(
                    root,
                    (
                        f"{projection.COMPONENT_PREDICTION_ROOT}/{component}/"
                        f"seed_{seed}_stack_predictions.parquet"
                    ),
                    f"{component} prediction",
                )
                record = dict(
                    _prediction_record(
                        root=root,
                        path=final_path,
                        row_count=prediction_sinks[(component, seed)].row_count,
                        component=component,
                        seed=seed,
                        frozen_component=frozen_component,
                        content_path=staged_path,
                    )
                )
                seeds[f"seed_{seed}"] = record
            component_records[component] = seeds
        lock_record = {
            "path": _workspace_relative(root, execution_lock_path),
            "sha256": sha256_file(execution_lock_path),
        }
        locked_stack_record = {
            "path": str(stack_record["path"]),
            "sha256": str(stack_record["sha256"]),
        }
        feature_payload = _feature_digest_payload(
            execution_lock_record=lock_record,
            stack_source_record=locked_stack_record,
            identity_record=identity_record,
            component_records=component_records,
        )
        generation_id = hashlib.sha256(
            canonical_json_bytes(feature_payload, pretty=False)
        ).hexdigest()
        row_key_order_sha256, ordered_row_count = _canonical_row_key_digest(utterances)
        receipt = build_feature_freeze_receipt(
            generation_id=generation_id,
            canonical_row_key_order_sha256=row_key_order_sha256,
            row_count=ordered_row_count,
            execution_lock_record=lock_record,
            stack_source_record=locked_stack_record,
            identity_record=identity_record,
            component_records=component_records,
        )
        receipt_staged = staging / "feature_freeze_receipt.json"
        if not _atomic_json(receipt_staged, receipt):
            raise S106ComponentGeneratorError(
                "feature-freeze receipt staging unexpectedly existed"
            )

        generation_root = _resolve_output(
            root,
            outputs["generation_root"],
            "generation root",
        )
        generation_root.mkdir(parents=True, exist_ok=True)
        generation_path = generation_root / generation_id
        try:
            existing_generation = os.lstat(generation_path)
        except FileNotFoundError:
            existing_generation = None
        if existing_generation is not None:
            if not stat.S_ISDIR(existing_generation.st_mode) or stat.S_ISLNK(
                existing_generation.st_mode
            ):
                raise S106ComponentGeneratorError(
                    "generation collision is not a no-follow directory"
                )
            _verify_generation_collision(staging, generation_path)
            _seal_generation_tree(generation_path)
            shutil.rmtree(staging)
        else:
            os.rename(staging, generation_path)
            _seal_generation_tree(generation_path)
        _fsync_directory(generation_root)
        _invoke_fault(fault_injector, "after_generation_publish_and_fsync")
        identity_source = generation_path / "feature_identity.parquet"
        if _materialize_file(identity_source, final_identity):
            created_outputs.append(final_identity)
        for component in projection.COMPONENTS:
            for seed in projection.SEEDS:
                source = (
                    generation_path
                    / "predictions"
                    / component
                    / f"seed_{seed}_stack_predictions.parquet"
                )
                destination = root / str(
                    component_records[component][f"seed_{seed}"]["path"]
                )
                if _materialize_file(source, destination):
                    created_outputs.append(destination)
        receipt_path = _resolve_output(
            root,
            outputs["feature_freeze_receipt"],
            "feature-freeze receipt",
        )
        if _materialize_file(
            generation_path / "feature_freeze_receipt.json",
            receipt_path,
        ):
            created_outputs.append(receipt_path)
        _invoke_fault(fault_injector, "after_feature_freeze_receipt")

        label_path = _resolve_output(root, outputs["label_matrix"], "label matrix")
        with tempfile.TemporaryFile(
            mode="w+b",
            prefix=".labels-anonymous-",
            dir=staging_root,
        ) as label_handle:
            label_sink = _AnonymousParquetSink(
                handle=label_handle,
                schema=LABEL_SCHEMA,
                metadata=None,
                batch_rows=projection.MAXIMUM_PROJECTION_BATCH_ROWS,
            )
            # This is the first call that can request phone_alignments/operation.
            materialize_label_rows(
                workspace_root=root,
                stack_path=stack_path,
                receipt_path=receipt_path,
                expected_receipt_sha256=hashlib.sha256(
                    canonical_json_bytes(receipt, pretty=True)
                ).hexdigest(),
                expected_utterances=utterances,
                eligible_source_ids=scan.eligible_source_ids,
                event_hook=label_event_hook,
                row_consumer=label_sink.append,
            )
            label_sink.close()
            label_row_count = label_sink.row_count
            if _materialize_descriptor(label_handle.fileno(), label_path):
                created_outputs.append(label_path)
        label_record = _file_record(root, label_path, label_row_count)
        _invoke_fault(fault_injector, "after_label_matrix")

        receipt_record = {
            "path": _workspace_relative(root, receipt_path),
            "sha256": sha256_file(receipt_path),
            "size_bytes": receipt_path.stat().st_size,
        }
        commit: dict[str, Any] = {
            "schema_version": (
                GENERATOR_COMMIT_SCHEMA_VERSION
                if production
                else SYNTHETIC_GENERATOR_COMMIT_SCHEMA_VERSION
            ),
            "status": "committed",
            "generation_id": generation_id,
            "producer_identity": producer_identity,
            "generator_config": {
                "path": _workspace_relative(root, generator_path),
                "sha256": sha256_file(generator_path),
                "size_bytes": generator_path.stat().st_size,
            },
            "execution_lock": lock_record,
            "feature_freeze_receipt": receipt_record,
            "label_matrix": dict(label_record),
            "prediction_file_count": 12,
            "feature_rows": identity_sink.row_count,
            "label_rows": label_row_count,
            "excluded_utterances": {
                "qc_flagged": scan.excluded_qc_utterances,
                "reserved_prompt": scan.excluded_reserved_prompt_utterances,
            },
            "access_audit": {
                "predecessor_validation_completed_before_runtime_access": True,
                "label_free_scan_before_checkpoint_probe": True,
                "eligible_raw_ids_retained_in_memory_only": True,
                "restricted_parquet_disk_snapshot_created": False,
                "label_scanner_exact_eligible_id_held_fd_predicate_pushed_down": True,
                "label_staging_anonymous_unlinked": True,
                "source_parquet_scanner_batches_bounded": True,
                "label_rows_streamed_in_bounded_batches": True,
                "production_publication_serialized": production,
                "phone_alignments_opened_after_feature_freeze": True,
                "operation_opened_after_feature_freeze": True,
                "audio_pcm_hash_and_decode_used_same_held_bytes": True,
                "models_loaded_from_private_immutable_snapshot": True,
                "production_dependencies_immutable": production,
                "b07_unadapted_forward_separate": True,
                "s104_s105_adapted_forward_shared_per_seed": True,
                "completed_registry_published_before_visibility_bundle": True,
                "bundle_is_final_visibility_commit": True,
                "development_only": True,
                "stack_only": True,
            },
        }
        commit["binding_sha256"] = _binding(commit)
        commit_path = _resolve_output(
            root,
            outputs["generator_commit"],
            "generator commit",
        )
        if _atomic_json(commit_path, commit):
            created_outputs.append(commit_path)
        _invoke_fault(fault_injector, "after_generator_commit")
        commit_record = {
            "path": _workspace_relative(root, commit_path),
            "sha256": sha256_file(commit_path),
            "size_bytes": commit_path.stat().st_size,
        }

        bundle = dict(
            bundle_builder(
                workspace_root=root,
                execution_lock=execution_lock,
                execution_lock_record=lock_record,
                stack_source_record=locked_stack_record,
                feature_identity_matrix_record=identity_record,
                label_matrix_record=label_record,
                component_records=component_records,
                generator_commit_record=commit_record,
                feature_freeze_receipt_record=receipt_record,
                producer_identity=producer_identity,
            )
        )
        if not production:
            bundle["schema_version"] = SYNTHETIC_BUNDLE_SCHEMA_VERSION
            bundle["binding_sha256"] = _binding(bundle)
        bundle_path = _resolve_output(
            root,
            outputs["component_bundle_commit"],
            "component bundle",
        )
        bundle_payload = canonical_json_bytes(bundle, pretty=True)
        bundle_record = {
            "path": _workspace_relative(root, bundle_path),
            "sha256": hashlib.sha256(bundle_payload).hexdigest(),
            "size_bytes": len(bundle_payload),
        }

        registry: dict[str, Any] = {
            "schema_version": (
                REGISTRY_SCHEMA_VERSION
                if production
                else SYNTHETIC_REGISTRY_SCHEMA_VERSION
            ),
            "status": "completed",
            "run_id": "aihub71490-wavlm-s106-component-inputs-v1",
            "failure": None,
            "generation_id": generation_id,
            "producer_identity": producer_identity,
            "commit": commit_record,
            "component_bundle": bundle_record,
            "feature_freeze_receipt": receipt_record,
            "artifact_counts": {
                "feature_identity": 1,
                "component_predictions": 12,
                "label_matrix": 1,
                "feature_freeze_receipt": 1,
                "component_bundle": 1,
                "generator_commit": 1,
            },
            "privacy": {
                "raw_identifiers_published": False,
                "raw_paths_published": False,
                "target_or_realized_phone_identity_published": False,
                "row_level_scope": "D1_restricted_internal",
            },
        }
        registry["binding_sha256"] = _binding(registry)
        registry_path = _resolve_output(root, outputs["registry"], "registry")
        if _atomic_json(registry_path, registry):
            created_outputs.append(registry_path)
        _invoke_fault(fault_injector, "after_registry_before_bundle")
        if _atomic_json(bundle_path, bundle):
            created_outputs.append(bundle_path)
        if (
            sha256_file(bundle_path) != bundle_record["sha256"]
            or bundle_path.stat().st_size != bundle_record["size_bytes"]
        ):
            raise S106ComponentGeneratorError(
                "visibility bundle bytes differ from prepared commit"
            )
        _invoke_fault(fault_injector, "after_bundle_visibility_commit")
        if production:
            _validate_component_generation_commit(
                workspace_root=root,
                generator_config_path=generator_path,
                expected_producer_mode="production",
            )
        committed_bundle = True
        return GeneratedComponentInputs(
            generation_id=generation_id,
            feature_freeze_receipt=receipt,
            label_record=label_record,
            bundle=bundle,
            commit=commit,
            registry=registry,
        )
    except Exception:
        try:
            backend.close()
        except Exception:
            pass
        if not committed_bundle:
            cleanup_parents: set[Path] = set()
            for path in reversed(created_outputs):
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                cleanup_parents.add(path.parent)
            for parent in sorted(cleanup_parents, key=lambda value: len(value.parts)):
                _fsync_directory(parent)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def run_component_generation_for_test(
    *,
    workspace_root: Path,
    generator_config_path: Path,
    backend_factory: Callable[
        [Path, Mapping[str, Any], Mapping[str, Any], Sequence[LabelFreeUtterance]],
        ProjectionBackend,
    ],
    preflight: Preflight,
    bundle_builder: BundleBuilder,
    fault_injector: FaultInjector | None = None,
    label_event_hook: Callable[[str], None] | None = None,
) -> GeneratedComponentInputs:
    """Exercise publication mechanics without producing production schemas."""

    return _run_component_generation_core(
        workspace_root=workspace_root,
        generator_config_path=generator_config_path,
        backend_factory=backend_factory,
        preflight=preflight,
        bundle_builder=bundle_builder,
        fault_injector=fault_injector,
        label_event_hook=label_event_hook,
        production=False,
    )


def _read_bound_bytes(
    *,
    root: Path,
    record: Mapping[str, Any],
    name: str,
) -> tuple[Path, bytes]:
    """Read the exact checkpoint bytes once through a no-follow descriptor."""

    if set(record) != {"path", "sha256", "size_bytes"}:
        raise S106ComponentGeneratorError(f"{name} record schema differs")
    path = _resolve_file(root, record.get("path"), f"{name}.path")
    expected_sha256 = _sha256(record.get("sha256"), f"{name}.sha256")
    expected_size = record.get("size_bytes")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
    ):
        raise S106ComponentGeneratorError(f"{name}.size_bytes is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError(f"{name} no-follow open failed") from error
    try:
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    identities = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identities = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    path_after = os.stat(path, follow_symlinks=False)
    path_identities = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mode,
        path_after.st_size,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    )
    if (
        identities != after_identities
        or identities != path_identities
        or len(payload) != expected_size
        or hashlib.sha256(payload).hexdigest() != expected_sha256
    ):
        raise S106ComponentGeneratorError(f"{name} stable-byte binding differs")
    return path, payload


def _strict_json_bytes(payload: bytes, name: str) -> Mapping[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> Mapping[str, Any]:
        output: dict[str, Any] = {}
        for key, value in values:
            if key in output:
                raise S106ComponentGeneratorError(f"{name} has duplicate key")
            output[key] = value
        return output

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise S106ComponentGeneratorError(f"{name} JSON is invalid") from error
    return _mapping(document, name)


def _reserved_prompt_hashes(document: Mapping[str, Any]) -> frozenset[str]:
    entries = document.get("reserved_prompts")
    if not isinstance(entries, list) or not entries:
        raise S106ComponentGeneratorError("prompt reservation is empty")
    output: set[str] = set()
    for index, value in enumerate(entries):
        prompt = _sha256(
            _mapping(value, f"reserved prompt {index}").get("prompt_sha256"),
            f"reserved prompt {index}",
        )
        if prompt in output:
            raise S106ComponentGeneratorError("reserved prompt is duplicated")
        output.add(prompt)
    return frozenset(output)


def _safetensors_document(
    payload: bytes,
    name: str,
) -> tuple[Mapping[str, str], Mapping[str, Any]]:
    """Load safetensors metadata/state from already hash-validated bytes."""

    if len(payload) < 8:
        raise S106ComponentGeneratorError(f"{name} safetensors header is absent")
    header_size = int.from_bytes(payload[:8], byteorder="little", signed=False)
    if header_size <= 0 or 8 + header_size > len(payload):
        raise S106ComponentGeneratorError(f"{name} safetensors header differs")
    header = _strict_json_bytes(payload[8 : 8 + header_size], f"{name} header")
    raw_metadata = header.get("__metadata__")
    metadata = _mapping(raw_metadata, f"{name} metadata")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise S106ComponentGeneratorError(f"{name} metadata must contain strings")
    try:
        from safetensors.torch import load as load_safetensors

        state = load_safetensors(payload)
    except Exception as error:
        raise S106ComponentGeneratorError(
            f"{name} safetensors tensors are invalid"
        ) from error
    if not state:
        raise S106ComponentGeneratorError(f"{name} safetensors state is empty")
    return cast(Mapping[str, str], metadata), state


def _target_context(
    utterance: LabelFreeUtterance,
) -> tuple[Mapping[str, Sequence[str | None]], np.ndarray]:
    targets = utterance.target_intervals
    categorical: Mapping[str, Sequence[str | None]] = {
        "target_label": [item.label for item in targets],
        "previous_target_label": [
            targets[index - 1].label if index else None for index in range(len(targets))
        ],
        "next_target_label": [
            targets[index + 1].label if index + 1 < len(targets) else None
            for index in range(len(targets))
        ],
    }
    durations: NDArray[np.float64] = np.asarray(
        [item.end - item.start for item in targets],
        dtype=np.float64,
    )
    return categorical, durations


def _read_and_decode_pinned_audio(
    path: Path,
    *,
    expected_pcm_sha256: str,
) -> Any:
    """Hash canonical PCM and decode the exact bytes held by one no-follow fd."""

    import io
    import struct
    import wave

    import soundfile as sf  # type: ignore[import-untyped]

    from voice_coach.data.audio import DecodedAudio, resample_audio

    expected_pcm_sha256 = _sha256(expected_pcm_sha256, "audio PCM SHA-256")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise S106ComponentGeneratorError("audio no-follow open failed") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S106ComponentGeneratorError("audio source is not regular")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    path_after = os.stat(path, follow_symlinks=False)

    def identity(value: os.stat_result) -> tuple[int, ...]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if (
        not payload
        or identity(before) != identity(after)
        or identity(before) != identity(path_after)
    ):
        raise S106ComponentGeneratorError("audio changed during stable byte read")
    try:
        digest = hashlib.sha256()
        with wave.open(io.BytesIO(payload), "rb") as reader:
            digest.update(
                struct.pack(
                    ">IIII",
                    reader.getnchannels(),
                    reader.getsampwidth(),
                    reader.getframerate(),
                    reader.getnframes(),
                )
            )
            while frames := reader.readframes(1_048_576):
                digest.update(frames)
        if digest.hexdigest() != expected_pcm_sha256:
            raise S106ComponentGeneratorError("raw audio PCM fingerprint differs")
        decoded, source_sample_rate = sf.read(
            io.BytesIO(payload),
            dtype="float32",
            always_2d=True,
        )
    except S106ComponentGeneratorError:
        raise
    except Exception as error:
        raise S106ComponentGeneratorError(
            "exact pinned audio bytes cannot be decoded"
        ) from error
    if (
        isinstance(source_sample_rate, bool)
        or not isinstance(source_sample_rate, (int, np.integer))
        or int(source_sample_rate) <= 0
        or decoded.ndim != 2
        or decoded.shape[0] <= 0
        or decoded.shape[1] <= 0
        or not np.isfinite(decoded).all()
    ):
        raise S106ComponentGeneratorError("decoded pinned audio contract differs")
    source_channels = int(decoded.shape[1])
    mono = (
        decoded[:, 0]
        if source_channels == 1
        else np.mean(decoded, axis=1, dtype=np.float64).astype(np.float32)
    )
    samples = resample_audio(
        mono,
        source_sample_rate=int(source_sample_rate),
        target_sample_rate=16_000,
    )
    return DecodedAudio(
        samples=samples,
        sample_rate=16_000,
        source_sample_rate=int(source_sample_rate),
        source_channels=source_channels,
    )


def _explicit_numeric_features(
    *,
    audio: Any,
    utterance: LabelFreeUtterance,
) -> np.ndarray:
    rows: list[tuple[float, float, float]] = []
    for interval in utterance.target_intervals:
        start_index = max(
            0,
            min(audio.samples.size, round(interval.start * 16_000)),
        )
        end_index = max(
            start_index,
            min(audio.samples.size, round(interval.end * 16_000)),
        )
        segment = audio.samples[start_index:end_index]
        if segment.size == 0:
            rms = 0.0
            zcr = 0.0
        else:
            rms = float(np.sqrt(np.mean(np.square(segment, dtype=np.float64))))
            signs = np.signbit(segment)
            zcr = float(np.mean(signs[1:] != signs[:-1])) if segment.size > 1 else 0.0
        rows.append((interval.end - interval.start, rms, zcr))
    matrix: NDArray[np.float64] = np.asarray(rows, dtype=np.float64)
    if (
        matrix.shape != (len(utterance.target_intervals), 3)
        or not np.isfinite(matrix).all()
    ):
        raise S106ComponentGeneratorError("explicit numeric features differ")
    return matrix


def _pool_adapted_encoder_layers_from_audio(
    *,
    model: Any,
    audio: Any,
    phone_intervals: Sequence[Any],
    word_intervals: Sequence[Any],
    device: Any,
) -> Any:
    """Canonical S105 all-layer pooling without reopening the audio pathname."""

    import torch

    from voice_coach.data import Interval as AudioInterval
    from voice_coach.data.audio import make_label_aware_chunks
    from voice_coach.features.ssl import (
        WAVLM_BASE_PLUS_FRAME_GEOMETRY,
        prepare_wavlm_input,
    )
    from voice_coach.training.wavlm_hierarchy_execution import PooledHierarchy

    tolerance = 1 / 16_000 + 1e-6
    if any(
        interval.end > audio.duration_seconds + tolerance
        for interval in (*phone_intervals, *word_intervals)
    ):
        raise S106ComponentGeneratorError("hierarchy interval exceeds pinned audio")
    phones = tuple(
        AudioInterval(
            start=min(item.start, audio.duration_seconds),
            end=min(item.end, audio.duration_seconds),
            label=item.label,
        )
        for item in phone_intervals
    )
    words = tuple(
        WordInterval(
            start=min(item.start, audio.duration_seconds),
            end=min(item.end, audio.duration_seconds),
        )
        for item in word_intervals
    )
    chunks = make_label_aware_chunks(
        audio.duration_seconds,
        phones,
        max_duration_seconds=20.0,
    )
    utterance_sum: Any = None
    utterance_count = 0
    phone_sums: list[Any] = [None] * len(phones)
    phone_counts = [0] * len(phones)
    word_sums: list[Any] = [None] * len(words)
    word_counts = [0] * len(words)
    receptive_field = WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
    for chunk in chunks:
        start_sample = round(chunk.start_seconds * 16_000)
        end_sample = round(chunk.end_seconds * 16_000)
        values = prepare_wavlm_input(
            torch.from_numpy(audio.samples[start_sample:end_sample]),
            do_normalize=False,
        )
        actual_samples = int(values.numel())
        if actual_samples < receptive_field:
            values = torch.nn.functional.pad(
                values,
                (0, receptive_field - actual_samples),
            )
        input_samples = int(values.numel())
        attention_mask = torch.ones(
            (1, input_samples),
            dtype=torch.long,
            device=device,
        )
        context = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if device.type == "cuda"
            else cast(Any, _NullContext())
        )
        with torch.inference_mode(), context:
            output = model.wavlm(
                input_values=values.unsqueeze(0).to(device=device),
                attention_mask=attention_mask,
                output_hidden_states=True,
                return_dict=True,
            )
        hidden_states = getattr(output, "hidden_states", None)
        if not isinstance(hidden_states, tuple) or len(hidden_states) != 13:
            raise S106ComponentGeneratorError(
                "adapted WavLM all-layer output count differs"
            )
        layers = torch.stack(
            [value[0].to(dtype=torch.float32) for value in hidden_states[1:]],
            dim=0,
        )
        expected_frames = WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_samples
        )
        if (
            layers.ndim != 3
            or layers.shape[0] != 12
            or layers.shape[1] != expected_frames
            or not bool(torch.isfinite(layers).all())
        ):
            raise S106ComponentGeneratorError(
                "adapted WavLM all-layer frame geometry differs"
            )
        chunk_sum = layers.sum(dim=1)
        utterance_sum = (
            chunk_sum if utterance_sum is None else utterance_sum + chunk_sum
        )
        utterance_count += int(layers.shape[1])

        def accumulate(
            intervals: Sequence[Any],
            sums: list[Any],
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
                    raise S106ComponentGeneratorError(
                        "adapted hierarchy interval frame span differs"
                    )
                value = layers[:, first:stop].sum(dim=1)
                sums[index] = value if sums[index] is None else sums[index] + value
                counts[index] += stop - first

        accumulate(phones, phone_sums, phone_counts)
        accumulate(words, word_sums, word_counts)
    if utterance_sum is None or utterance_count <= 0:
        raise S106ComponentGeneratorError("adapted utterance received no frame")
    if any(
        value is None or count <= 0
        for value, count in zip(phone_sums, phone_counts, strict=True)
    ) or any(
        value is None or count <= 0
        for value, count in zip(word_sums, word_counts, strict=True)
    ):
        raise S106ComponentGeneratorError(
            "adapted phone/word interval received no frame"
        )
    phone_layers = torch.stack(
        [value / count for value, count in zip(phone_sums, phone_counts, strict=True)],
        dim=1,
    )
    word_layers = (
        torch.stack(
            [
                value / count
                for value, count in zip(word_sums, word_counts, strict=True)
            ],
            dim=1,
        )
        if word_sums
        else utterance_sum.new_empty((12, 0, utterance_sum.shape[-1]))
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


def _unadapted_phone_embeddings(
    *,
    model: Any,
    audio: Any,
    targets: Sequence[Any],
    device: Any,
) -> np.ndarray:
    """Canonical B07 Base+ final-layer pooling with float16 cache parity."""

    import torch

    from voice_coach.data.audio import make_label_aware_chunks
    from voice_coach.features.ssl import (
        WAVLM_BASE_PLUS_FRAME_GEOMETRY,
        prepare_wavlm_input,
    )

    chunks = make_label_aware_chunks(
        audio.duration_seconds,
        targets,
        max_duration_seconds=20.0,
    )
    parts: list[list[torch.Tensor]] = [[] for _ in targets]
    receptive_field = WAVLM_BASE_PLUS_FRAME_GEOMETRY.receptive_field_samples
    for chunk in chunks:
        start_sample = round(chunk.start_seconds * 16_000)
        end_sample = round(chunk.end_seconds * 16_000)
        values = prepare_wavlm_input(
            torch.from_numpy(audio.samples[start_sample:end_sample]),
            do_normalize=False,
        )
        actual_sample_count = int(values.numel())
        if actual_sample_count < receptive_field:
            values = torch.nn.functional.pad(
                values,
                (0, receptive_field - actual_sample_count),
            )
        input_sample_count = int(values.numel())
        attention_mask = torch.zeros(
            (1, input_sample_count),
            dtype=torch.long,
            device=device,
        )
        # B07 cache parity: padding is not marked valid for the unadapted pass.
        attention_mask[:, :actual_sample_count] = 1
        context = (
            torch.autocast(device_type="cuda", dtype=torch.bfloat16)
            if device.type == "cuda"
            else cast(Any, _NullContext())
        )
        with torch.inference_mode(), context:
            output = model(
                input_values=values.unsqueeze(0).to(device=device),
                attention_mask=attention_mask,
            )
        hidden = output.last_hidden_state[0]
        expected_frames = WAVLM_BASE_PLUS_FRAME_GEOMETRY.output_frame_count(
            input_sample_count
        )
        if hidden.shape != (expected_frames, 768):
            raise S106ComponentGeneratorError("unadapted Base+ output geometry differs")
        hidden_cpu = hidden.detach().to(device="cpu", dtype=torch.float32)
        for index, interval in enumerate(targets):
            overlap_start = max(interval.start, chunk.start_seconds)
            overlap_end = min(interval.end, chunk.end_seconds)
            if overlap_end <= overlap_start:
                continue
            first, stop = WAVLM_BASE_PLUS_FRAME_GEOMETRY.interval_frame_span(
                start_seconds=overlap_start - chunk.start_seconds,
                end_seconds=overlap_end - chunk.start_seconds,
                input_sample_count=input_sample_count,
            )
            if first < 0 or stop <= first or stop > hidden_cpu.shape[0]:
                raise S106ComponentGeneratorError(
                    "unadapted Base+ interval span differs"
                )
            parts[index].append(hidden_cpu[first:stop])
    pooled: list[np.ndarray] = []
    for index, interval_parts in enumerate(parts):
        if not interval_parts:
            raise S106ComponentGeneratorError(
                f"unadapted Base+ target {index} received no frame"
            )
        vector = (
            torch.cat(interval_parts, dim=0).mean(dim=0).to(dtype=torch.float32).numpy()
        )
        # Training serialized these vectors as float16 and B07 restored them
        # as float32.  Reproduce that quantization boundary exactly.
        pooled.append(vector.astype(np.float16).astype(np.float32))
    output = np.stack(pooled, axis=0)
    if output.shape != (len(targets), 768) or not np.isfinite(output).all():
        raise S106ComponentGeneratorError("unadapted Base+ pooled embeddings differ")
    return output


class _NullContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None


def _remove_private_model_staging(private_root: Path) -> None:
    private_snapshot = private_root / "snapshot"
    for directory in (private_snapshot, private_root):
        try:
            information = os.lstat(directory)
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
            raise S106ComponentGeneratorError(
                "private model staging directory integrity differs"
            )
        directory.chmod(0o700)
    try:
        shutil.rmtree(private_root)
    except OSError as error:
        raise S106ComponentGeneratorError(
            "private model staging cleanup failed"
        ) from error
    if os.path.lexists(private_root):
        raise S106ComponentGeneratorError(
            "private model staging cleanup was incomplete"
        )


class ProductionProjectionBackend:
    """Exact production replay of explicit, S104, S105, and B07 checkpoints."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        generator_config: Mapping[str, Any],
        execution_lock: Mapping[str, Any],
    ) -> None:
        import torch

        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise S106ComponentGeneratorError(
                "production component projection requires CUDA BF16"
            )
        expected_workspace = str(
            _mapping(generator_config["runtime"], "runtime")["cublas_workspace_config"]
        )
        if os.environ.get("CUBLAS_WORKSPACE_CONFIG") != expected_workspace:
            raise S106ComponentGeneratorError("CUBLAS_WORKSPACE_CONFIG differs")
        torch.use_deterministic_algorithms(True)
        self.root = workspace_root
        self.config = generator_config
        self.lock = execution_lock
        self.device = torch.device("cuda")
        self.dataset_root = _resolve_file_or_directory(
            workspace_root,
            _mapping(generator_config["inputs"], "inputs")["dataset_root"],
            "dataset root",
        )
        self.explicit_checkpoints: dict[int, Mapping[str, Any]] = {}
        self.s104_models: dict[int, Any] = {}
        self.s105_heads: dict[int, Any] = {}
        self.vocabularies: dict[int, Mapping[str, int]] = {}
        self.b07_documents: dict[int, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
        self.unadapted_model: Any
        try:
            self._load_all()
        except Exception as error:
            self.close()
            if isinstance(error, S106ComponentGeneratorError):
                raise
            raise S106ComponentGeneratorError(
                "production checkpoint/model loading failed"
            ) from error

    def _load_all(self) -> None:
        from voice_coach.training.wavlm_adapter_contract import (
            load_config as load_s104_config,
        )
        from voice_coach.training.wavlm_adapter_training import (
            local_snapshot_paths,
        )

        predecessors = _mapping(self.lock.get("predecessors"), "predecessors")
        private_root = Path(tempfile.mkdtemp(prefix="s106-private-model-inputs-"))
        try:
            private_configs: dict[str, Path] = {}
            for stage in ("s104", "s105"):
                canonical_sources = _mapping(
                    _mapping(predecessors.get(stage), f"{stage} predecessor").get(
                        "canonical_sources"
                    ),
                    f"{stage} canonical sources",
                )
                record = _mapping(
                    canonical_sources.get("config"),
                    f"{stage} canonical config",
                )
                _, payload = _read_bound_bytes(
                    root=self.root,
                    record=record,
                    name=f"{stage} canonical config",
                )
                destination = private_root / f"{stage}.yaml"
                destination.write_bytes(payload)
                destination.chmod(0o400)
                private_configs[stage] = destination
            s104_config = load_s104_config(private_configs["s104"])
            selected_candidate = dict(
                _mapping(
                    _mapping(s104_config["candidates"], "S104 candidates")["base_plus"],
                    "S104 Base+ candidate",
                )
            )
            _, model_config_path, model_checkpoint_path = local_snapshot_paths(
                workspace_root=self.root,
                selected_candidate=selected_candidate,
            )
            model_config_binding = _pinned_model_file_binding(model_config_path)
            model_checkpoint_binding = _pinned_model_file_binding(model_checkpoint_path)
            model_config_payload = _stable_file_bytes(
                cast(Path, model_config_binding["resolved_path"]),
                "pinned Base+ config",
            )
            model_checkpoint_payload = _stable_file_bytes(
                cast(Path, model_checkpoint_binding["resolved_path"]),
                "pinned Base+ checkpoint",
            )
            if (
                hashlib.sha256(model_config_payload).hexdigest()
                != selected_candidate["source_config_sha256"]
                or hashlib.sha256(model_checkpoint_payload).hexdigest()
                != selected_candidate["source_checkpoint_sha256"]
            ):
                raise S106ComponentGeneratorError("local Base+ snapshot differs")
            private_snapshot = private_root / "snapshot"
            private_snapshot.mkdir(mode=0o700)
            private_model_config = private_snapshot / "config.json"
            private_model_checkpoint = private_snapshot / "pytorch_model.bin"
            private_model_config.write_bytes(model_config_payload)
            private_model_checkpoint.write_bytes(model_checkpoint_payload)
            private_model_config.chmod(0o400)
            private_model_checkpoint.chmod(0o400)
            private_snapshot.chmod(0o500)
            self._load_all_from_private(
                s104_config_path=private_configs["s104"],
                s105_config_path=private_configs["s105"],
                snapshot=private_snapshot,
            )
            _revalidate_pinned_model_file(model_config_binding)
            _revalidate_pinned_model_file(model_checkpoint_binding)
        finally:
            _remove_private_model_staging(private_root)

    def _load_all_from_private(
        self,
        *,
        s104_config_path: Path,
        s105_config_path: Path,
        snapshot: Path,
    ) -> None:
        import torch

        from voice_coach.training.wavlm_adapter_contract import (
            load_config as load_s104_config,
        )
        from voice_coach.training.wavlm_adapter_training import (
            FitMetadata,
            _load_trainable_state,
            build_adapter_model,
        )
        from voice_coach.training.wavlm_hierarchy_contract import (
            load_config as load_s105_config,
        )
        from voice_coach.models.wavlm_hierarchy import IncrementalHierarchyHead
        from voice_coach.training.wavlm_hierarchy_execution import _load_state

        frozen = _mapping(self.lock.get("frozen_components"), "frozen components")
        explicit_frozen = _mapping(frozen.get("explicit"), "frozen explicit")
        s104_frozen = _mapping(frozen.get("s104_adapter"), "frozen S104")
        s105_frozen = _mapping(
            frozen.get("s105_selected_hierarchy"),
            "frozen S105",
        )
        b07_frozen = _mapping(
            frozen.get("gop_acoustic_posterior"),
            "frozen B07",
        )
        s104_config = load_s104_config(s104_config_path)
        s105_config = load_s105_config(s105_config_path)
        selected_candidate = dict(
            _mapping(
                _mapping(s104_config["candidates"], "S104 candidates")["base_plus"],
                "S104 Base+ candidate",
            )
        )

        # Probe actual explicit and B07 checkpoints only after S104/S105/B07
        # preflight and the safe stack scan have already succeeded.
        for seed in projection.SEEDS:
            explicit_record = _mapping(
                _checkpoint_record(
                    explicit_frozen,
                    component="explicit",
                    seed=seed,
                ).get("checkpoint"),
                "explicit checkpoint",
            )
            _, explicit_bytes = _read_bound_bytes(
                root=self.root,
                record=explicit_record,
                name=f"explicit seed {seed} checkpoint",
            )
            try:
                explicit = pickle.loads(explicit_bytes)
            except Exception as error:
                raise S106ComponentGeneratorError(
                    f"explicit seed {seed} checkpoint cannot be loaded"
                ) from error
            checkpoint = _mapping(explicit, f"explicit seed {seed} checkpoint")
            phone = _mapping(checkpoint.get("phone"), "explicit phone checkpoint")
            expected_features = (
                "target_label",
                "previous_target_label",
                "next_target_label",
                "oracle_duration_seconds",
                "segment_rms",
                "segment_zero_crossing_rate",
            )
            pipeline = phone.get("pipeline")
            classes = np.asarray(getattr(pipeline, "classes_", ()))
            selected_c = phone.get("selected_c")
            if (
                checkpoint.get("schema_version")
                != "voice_coach.explicit_baseline_checkpoint.v1"
                or checkpoint.get("seed") != seed
                or checkpoint.get("development_only") is not True
                or checkpoint.get("oracle_alignment") is not True
                or checkpoint.get("deployable_alignment_claim_allowed") is not False
                or tuple(phone.get("features", ())) != expected_features
                or isinstance(selected_c, bool)
                or not isinstance(selected_c, (int, float))
                or not math.isfinite(float(selected_c))
                or float(selected_c) <= 0.0
                or not callable(getattr(pipeline, "predict_proba", None))
                or classes.shape != (2,)
                or classes.tolist() != [0, 1]
            ):
                raise S106ComponentGeneratorError(
                    f"explicit seed {seed} checkpoint contract differs"
                )
            self.explicit_checkpoints[seed] = checkpoint

            s104_record = _mapping(
                _checkpoint_record(
                    s104_frozen,
                    component="s104_adapter",
                    seed=seed,
                ).get("checkpoint"),
                "S104 checkpoint",
            )
            _, s104_bytes = _read_bound_bytes(
                root=self.root,
                record=s104_record,
                name=f"S104 seed {seed} checkpoint",
            )
            s104_metadata, s104_state = _safetensors_document(
                s104_bytes,
                f"S104 seed {seed}",
            )
            try:
                vocabulary = tuple(
                    str(value)
                    for value in json.loads(s104_metadata["target_vocabulary_json"])
                )
            except (KeyError, json.JSONDecodeError, TypeError) as error:
                raise S106ComponentGeneratorError(
                    "S104 target vocabulary metadata differs"
                ) from error
            if (
                not vocabulary
                or len(vocabulary) != len(set(vocabulary))
                or s104_metadata.get("schema_version")
                != "voice_coach.wavlm_s104_adapter_checkpoint.v1"
                or s104_metadata.get("seed") != str(seed)
                or s104_metadata.get("development_only") != "true"
                or s104_metadata.get("oracle_alignment") != "true"
                or s104_metadata.get("model_id") != selected_candidate["model_id"]
                or s104_metadata.get("revision") != selected_candidate["revision"]
                or s104_metadata.get("full_backbone_tensor_count") != "0"
            ):
                raise S106ComponentGeneratorError(
                    f"S104 seed {seed} checkpoint metadata differs"
                )
            metadata = FitMetadata(
                target_vocabulary=vocabulary,
                positive_phone_rows=1,
                negative_phone_rows=1,
                pos_weight=1.0,
                fit_utterances=1,
            )
            model, _ = build_adapter_model(
                config=s104_config,
                selected_candidate=selected_candidate,
                metadata=metadata,
                snapshot=snapshot,
                device=self.device,
            )
            try:
                _load_trainable_state(model, s104_state)
            except Exception as error:
                raise S106ComponentGeneratorError(
                    f"S104 seed {seed} tensor state differs"
                ) from error
            for parameter in model.parameters():
                parameter.requires_grad_(False)
            model.eval()
            self.s104_models[seed] = model
            vocabulary_index = {value: index for index, value in enumerate(vocabulary)}
            if "<UNK>" not in vocabulary_index:
                raise S106ComponentGeneratorError("S104 <UNK> target is absent")
            self.vocabularies[seed] = vocabulary_index

            s105_record = _mapping(
                _checkpoint_record(
                    s105_frozen,
                    component="s105_selected_hierarchy",
                    seed=seed,
                ).get("checkpoint"),
                "S105 checkpoint",
            )
            _, s105_bytes = _read_bound_bytes(
                root=self.root,
                record=s105_record,
                name=f"S105 seed {seed} checkpoint",
            )
            s105_metadata, s105_state = _safetensors_document(
                s105_bytes,
                f"S105 seed {seed}",
            )
            selected_variant = s105_frozen.get("selected_variant")
            try:
                s105_vocabulary = tuple(
                    str(value)
                    for value in json.loads(s105_metadata["target_vocabulary_json"])
                )
            except (KeyError, json.JSONDecodeError, TypeError) as error:
                raise S106ComponentGeneratorError(
                    "S105 target vocabulary metadata differs"
                ) from error
            if (
                selected_variant
                not in {
                    "simple_head_control",
                    "levelwise_multi_aspect_hierarchy",
                }
                or s105_vocabulary != vocabulary
                or s105_metadata.get("schema_version")
                != "voice_coach.wavlm_s105_hierarchy_checkpoint.v1"
                or s105_metadata.get("variant") != selected_variant
                or s105_metadata.get("seed") != str(seed)
                or s105_metadata.get("development_only") != "true"
                or s105_metadata.get("oracle_alignment") != "true"
                or s105_metadata.get("model_id") != selected_candidate["model_id"]
                or s105_metadata.get("revision") != selected_candidate["revision"]
                or s105_metadata.get("full_backbone_tensor_count") != "0"
                or s105_metadata.get("word_loss_performed") != "false"
                or s105_metadata.get("s104_checkpoint_sha256") != s104_record["sha256"]
            ):
                raise S106ComponentGeneratorError(
                    f"S105 seed {seed} checkpoint metadata differs"
                )
            dimensions = _mapping(
                _mapping(s105_config["variants"], "S105 variants")["dimensions"],
                "S105 dimensions",
            )
            head = IncrementalHierarchyHead(
                variant=cast(Any, selected_variant),
                hidden_size=int(selected_candidate["hidden_size"]),
                encoder_layer_count=int(selected_candidate["encoder_layer_count"]),
                projection_dim=int(dimensions["projection_dim"]),
                target_vocabulary_size=len(vocabulary),
                target_embedding_dim=int(dimensions["phone_target_embedding_dim"]),
                attention_hidden_dim=int(dimensions["attention_hidden_dim"]),
                attention_aspects=int(dimensions["attention_aspects"]),
                dropout=float(dimensions["dropout"]),
                score_range=cast(Sequence[float], dimensions["score_range"]),
            ).to(self.device)
            if set(s105_state) != set(head.state_dict()) or any(
                not tensor.is_floating_point() or not bool(torch.isfinite(tensor).all())
                for tensor in s105_state.values()
            ):
                raise S106ComponentGeneratorError(
                    f"S105 seed {seed} tensor allowlist differs"
                )
            try:
                _load_state(head, s105_state)
            except Exception as error:
                raise S106ComponentGeneratorError(
                    f"S105 seed {seed} tensor shapes/dtypes differ"
                ) from error
            head.requires_grad_(False)
            head.eval()
            self.s105_heads[seed] = head

            b07_record = _checkpoint_record(
                b07_frozen,
                component="gop_acoustic_posterior",
                seed=seed,
            )
            documents: list[Mapping[str, Any]] = []
            for kind in ("acoustic", "error"):
                _, payload = _read_bound_bytes(
                    root=self.root,
                    record=_mapping(
                        b07_record.get(f"{kind}_checkpoint"),
                        f"B07 {kind} checkpoint",
                    ),
                    name=f"B07 seed {seed} {kind} checkpoint",
                )
                documents.append(
                    _strict_json_bytes(
                        payload,
                        f"B07 seed {seed} {kind} checkpoint",
                    )
                )
            projection.validate_b07_projection_checkpoints(
                acoustic_checkpoint=documents[0],
                error_checkpoint=documents[1],
                expected_seed=seed,
            )
            self.b07_documents[seed] = (documents[0], documents[1])

        from transformers import WavLMModel
        from voice_coach.features.ssl import WAVLM_BASE_PLUS_FRAME_GEOMETRY

        unadapted = WavLMModel.from_pretrained(
            snapshot,
            local_files_only=True,
            use_safetensors=False,
        )
        if (
            int(unadapted.config.hidden_size) != 768
            or tuple(unadapted.config.conv_kernel)
            != WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_kernels
            or tuple(unadapted.config.conv_stride)
            != WAVLM_BASE_PLUS_FRAME_GEOMETRY.conv_strides
        ):
            raise S106ComponentGeneratorError("unadapted Base+ architecture differs")
        unadapted.requires_grad_(False)
        unadapted.eval()
        self.unadapted_model = cast(Any, unadapted).to(self.device)

    def project(
        self,
        utterance: LabelFreeUtterance,
    ) -> Mapping[str, Mapping[int, Sequence[float]]]:
        import torch

        from voice_coach.data import Interval as AudioInterval
        from voice_coach.training.wavlm_adapter_training import resolve_audio_path
        from voice_coach.training.wavlm_hierarchy_training import (
            Interval as HierarchyInterval,
            map_phones_to_words,
        )

        audio_path = resolve_audio_path(
            self.dataset_root,
            utterance.audio_relative_path,
        )
        audio = _read_and_decode_pinned_audio(
            audio_path,
            expected_pcm_sha256=utterance.pcm_sha256,
        )
        categorical, _ = _target_context(utterance)
        explicit_numeric = _explicit_numeric_features(
            audio=audio,
            utterance=utterance,
        )
        explicit_matrix = np.column_stack(
            (
                np.asarray(categorical["target_label"], dtype=object),
                np.asarray(
                    categorical["previous_target_label"],
                    dtype=object,
                ),
                np.asarray(categorical["next_target_label"], dtype=object),
                explicit_numeric,
            )
        )
        target_audio_intervals = tuple(
            AudioInterval(item.start, item.end, item.label)
            for item in utterance.target_intervals
        )
        hierarchy_phone_intervals = tuple(
            HierarchyInterval(item.start, item.end)
            for item in utterance.target_intervals
        )
        hierarchy_word_intervals = tuple(
            HierarchyInterval(item.start, item.end) for item in utterance.word_intervals
        )
        phone_to_word_values = map_phones_to_words(
            hierarchy_phone_intervals,
            hierarchy_word_intervals,
        )
        explicit_output: dict[int, Sequence[float]] = {}
        s104_output: dict[int, Sequence[float]] = {}
        s105_output: dict[int, Sequence[float]] = {}
        for seed in projection.SEEDS:
            explicit_output[seed] = projection.project_explicit_label_free_features(
                checkpoint=self.explicit_checkpoints[seed],
                feature_rows=explicit_matrix,
            ).tolist()
            vocabulary = self.vocabularies[seed]
            target_indices = torch.tensor(
                [
                    vocabulary.get(item.label, vocabulary["<UNK>"])
                    for item in utterance.target_intervals
                ],
                dtype=torch.long,
                device=self.device,
            )
            pooled = _pool_adapted_encoder_layers_from_audio(
                model=self.s104_models[seed],
                audio=audio,
                phone_intervals=target_audio_intervals,
                word_intervals=utterance.word_intervals,
                device=self.device,
            )
            s104_output[seed] = projection.project_s104_label_free_embeddings(
                model=self.s104_models[seed],
                phone_embeddings=pooled.phone_layers[-1],
                target_indices=target_indices,
                representation_attestation={
                    "representation": "adapter_applied_target_interval_pooled",
                    "adapter_applied": True,
                    "target_interval_pooling": True,
                    "label_free": True,
                },
            ).tolist()
            phone_to_word = torch.tensor(
                phone_to_word_values,
                dtype=torch.long,
                device=self.device,
            )
            s105_output[seed] = projection.project_s105_label_free_layers(
                head=self.s105_heads[seed],
                phone_layers=pooled.phone_layers,
                word_layers=pooled.word_layers,
                utterance_layers=pooled.utterance_layers,
                phone_to_word=phone_to_word,
                target_indices=target_indices,
                representation_attestation={
                    "representation": (
                        "adapter_applied_all_encoder_layers_target_interval_pooled"
                    ),
                    "adapter_applied": True,
                    "all_encoder_layers": True,
                    "target_interval_pooling": True,
                    "label_free": True,
                },
            ).tolist()
        unadapted_embeddings = _unadapted_phone_embeddings(
            model=self.unadapted_model,
            audio=audio,
            targets=target_audio_intervals,
            device=self.device,
        )
        identifiers = [
            {
                "utterance_id_sha256": utterance.utterance_id_sha256,
                "speaker_id_sha256": utterance.speaker_id_sha256,
                "phone_index": index,
                "split": "stack",
            }
            for index in range(len(utterance.target_intervals))
        ]
        b07_output: dict[int, Sequence[float]] = {}
        for seed in projection.SEEDS:
            acoustic, error = self.b07_documents[seed]
            rows = projection.project_b07_label_free_batch(
                seed=seed,
                identifiers=identifiers,
                embeddings=unadapted_embeddings,
                categorical_rows=categorical,
                explicit_numeric_rows=explicit_numeric,
                acoustic_checkpoint=acoustic,
                error_checkpoint=error,
            )
            b07_output[seed] = [float(row["probability"]) for row in rows]
        return {
            "explicit": explicit_output,
            "s104_adapter": s104_output,
            "s105_selected_hierarchy": s105_output,
            "gop_acoustic_posterior": b07_output,
        }

    def close(self) -> None:
        try:
            import torch

            self.explicit_checkpoints.clear()
            self.b07_documents.clear()
            self.s104_models.clear()
            self.s105_heads.clear()
            if hasattr(self, "unadapted_model"):
                del self.unadapted_model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            return


def _resolve_file_or_directory(
    root: Path,
    value: object,
    name: str,
) -> Path:
    relative = _relative_path(value, name)
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise S106ComponentGeneratorError(f"{name} cannot traverse a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise S106ComponentGeneratorError(f"{name} cannot be resolved") from error
    if not resolved.is_dir() or resolved.is_symlink():
        raise S106ComponentGeneratorError(f"{name} must be a directory")
    return resolved


def production_backend_factory(
    root: Path,
    config: Mapping[str, Any],
    execution_lock: Mapping[str, Any],
    utterances: Sequence[LabelFreeUtterance],
) -> ProjectionBackend:
    if not utterances:
        raise S106ComponentGeneratorError("production backend received no stack rows")
    return ProductionProjectionBackend(
        workspace_root=root,
        generator_config=config,
        execution_lock=execution_lock,
    )


def run_component_generation(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> GeneratedComponentInputs:
    """Run the production generator with immutable canonical dependencies."""

    return _run_component_generation_core(
        workspace_root=workspace_root,
        generator_config_path=generator_config_path,
        backend_factory=production_backend_factory,
        preflight=_default_preflight,
        bundle_builder=projection.build_component_bundle_document,
        production=True,
    )


def _stable_parquet_from_record(
    *,
    root: Path,
    record: Mapping[str, Any],
    name: str,
) -> pa.Table:
    base = {key: record.get(key) for key in ("path", "sha256", "size_bytes")}
    path, payload = _read_bound_bytes(root=root, record=base, name=name)
    _require_immutable_regular_file(path, name)
    try:
        table = pq.read_table(pa.BufferReader(payload))
    except Exception as error:
        raise S106ComponentGeneratorError(f"{name} Parquet is invalid") from error
    row_count = record.get("row_count")
    if (
        isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or table.num_rows != row_count
        or table.num_rows <= 0
    ):
        raise S106ComponentGeneratorError(f"{name} row count differs")
    return table


def _validate_generation_tree_and_public_copies(
    *,
    root: Path,
    outputs: Mapping[str, Any],
    generation_id: str,
    receipt: Mapping[str, Any],
    receipt_path: Path,
) -> None:
    """Require the exact sealed generation and separate-inode public views."""

    generation_root = _resolve_output(
        root,
        outputs["generation_root"],
        "component generation root",
    )
    generation_path = generation_root / generation_id
    for path, name in (
        (generation_root, "component generation root"),
        (generation_path, "component generation"),
    ):
        try:
            information = os.lstat(path)
        except OSError as error:
            raise S106ComponentGeneratorError(f"{name} is absent") from error
        if not stat.S_ISDIR(information.st_mode) or stat.S_ISLNK(information.st_mode):
            raise S106ComponentGeneratorError(f"{name} must be a no-follow directory")
        if path == generation_path and stat.S_IMODE(information.st_mode) != 0o550:
            raise S106ComponentGeneratorError(
                "component generation directory mode differs"
            )

    expected_directories = {
        "predictions",
        *(f"predictions/{component}" for component in projection.COMPONENTS),
    }
    expected_files = {
        "feature_identity.parquet",
        "feature_freeze_receipt.json",
        *(
            f"predictions/{component}/seed_{seed}_stack_predictions.parquet"
            for component in projection.COMPONENTS
            for seed in projection.SEEDS
        ),
    }
    observed_directories: set[str] = set()
    observed_files: set[str] = set()
    for path in sorted(generation_path.rglob("*")):
        relative = path.relative_to(generation_path).as_posix()
        information = os.lstat(path)
        if stat.S_ISLNK(information.st_mode):
            raise S106ComponentGeneratorError(
                "component generation tree contains a symlink"
            )
        if stat.S_ISDIR(information.st_mode):
            if stat.S_IMODE(information.st_mode) != 0o550:
                raise S106ComponentGeneratorError(
                    "component generation subdirectory mode differs"
                )
            observed_directories.add(relative)
        elif stat.S_ISREG(information.st_mode):
            if stat.S_IMODE(information.st_mode) != 0o440 or information.st_nlink != 1:
                raise S106ComponentGeneratorError(
                    "component generation file seal differs"
                )
            observed_files.add(relative)
        else:
            raise S106ComponentGeneratorError(
                "component generation tree contains a non-regular entry"
            )
    if observed_directories != expected_directories or observed_files != expected_files:
        raise S106ComponentGeneratorError("component generation exact tree differs")

    def require_separate_copy(source: Path, destination: Path, name: str) -> None:
        source_stat = os.lstat(source)
        destination_stat = os.lstat(destination)
        if (
            not stat.S_ISREG(source_stat.st_mode)
            or not stat.S_ISREG(destination_stat.st_mode)
            or stat.S_ISLNK(source_stat.st_mode)
            or stat.S_ISLNK(destination_stat.st_mode)
            or stat.S_IMODE(source_stat.st_mode) != 0o440
            or stat.S_IMODE(destination_stat.st_mode) != 0o440
            or source_stat.st_nlink != 1
            or destination_stat.st_nlink != 1
            or (source_stat.st_dev, source_stat.st_ino)
            == (destination_stat.st_dev, destination_stat.st_ino)
            or _stable_file_bytes(source, f"generation {name}")
            != _stable_file_bytes(destination, f"public {name}")
        ):
            raise S106ComponentGeneratorError(
                f"generation/public {name} copy binding differs"
            )

    identity_record = _mapping(
        receipt.get("feature_identity_matrix"),
        "receipt feature identity",
    )
    require_separate_copy(
        generation_path / "feature_identity.parquet",
        _resolve_file(root, identity_record.get("path"), "public feature identity"),
        "feature identity",
    )
    require_separate_copy(
        generation_path / "feature_freeze_receipt.json",
        receipt_path,
        "feature-freeze receipt",
    )
    components = _mapping(receipt.get("components"), "receipt components")
    for component in projection.COMPONENTS:
        seeds = _mapping(components.get(component), f"receipt {component}")
        for seed in projection.SEEDS:
            record = _mapping(
                seeds.get(f"seed_{seed}"),
                f"receipt {component} seed {seed}",
            )
            require_separate_copy(
                generation_path
                / "predictions"
                / component
                / f"seed_{seed}_stack_predictions.parquet",
                _resolve_file(
                    root,
                    record.get("path"),
                    f"public {component} seed {seed}",
                ),
                f"{component} seed {seed}",
            )


def validate_component_producer_evidence(
    *,
    workspace_root: Path,
    generator_config_path: Path,
    generation_id: str,
    receipt_path: Path,
    receipt: Mapping[str, Any],
    label_record: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate the non-recursive production tree and its separate public views."""

    root = workspace_root.resolve(strict=True)
    generator_path = generator_config_path
    if not generator_path.is_absolute():
        generator_path = root / generator_path
    generator_path = generator_path.resolve(strict=True)
    generator_config = load_generator_config(generator_path)
    generator = _mapping(generator_config["generator"], "generator")
    outputs = _mapping(generator_config["outputs"], "outputs")
    main_config_path = _resolve_file(
        root,
        generator["main_config"],
        "generator.main_config",
    )
    main_config = fusion_contract.load_config(main_config_path)
    fusion_contract.validate_config(main_config)
    _validate_canonical_generator_config_binding(
        root=root,
        generator_config_path=generator_path,
        main_config=main_config,
    )
    fusion_contract.validate_implementation_sources(
        workspace_root=root,
        config=main_config,
    )
    expected_receipt_path = _resolve_file(
        root,
        outputs["feature_freeze_receipt"],
        "feature-freeze receipt",
    )
    if receipt_path.resolve(strict=True) != expected_receipt_path:
        raise S106ComponentGeneratorError(
            "feature-freeze receipt canonical path differs"
        )
    validate_feature_freeze_receipt(receipt)
    if (
        receipt.get("generation_id") != generation_id
        or receipt.get("feature_digest_sha256") != generation_id
    ):
        raise S106ComponentGeneratorError(
            "producer evidence generation identity differs"
        )
    identity_record = _mapping(
        receipt.get("feature_identity_matrix"),
        "receipt feature identity",
    )
    if identity_record.get("path") != _relative_path(
        outputs["feature_identity_matrix"],
        "outputs.feature_identity_matrix",
    ):
        raise S106ComponentGeneratorError(
            "receipt feature identity canonical path differs"
        )
    exact_label_record = _mapping(label_record, "producer label record")
    if exact_label_record.get("path") != _relative_path(
        outputs["label_matrix"],
        "outputs.label_matrix",
    ):
        raise S106ComponentGeneratorError("producer label canonical path differs")
    _validate_generation_tree_and_public_copies(
        root=root,
        outputs=outputs,
        generation_id=generation_id,
        receipt=receipt,
        receipt_path=expected_receipt_path,
    )
    return {
        "status": "valid",
        "generator_config_path": generator_path,
        "main_config_path": main_config_path,
        "producer_identity": _producer_identity(main_config, production=True),
        "generation_id": generation_id,
        "feature_identity_path": _resolve_file(
            root,
            outputs["feature_identity_matrix"],
            "feature identity matrix",
        ),
        "label_path": _resolve_file(root, outputs["label_matrix"], "label matrix"),
    }


def _validate_component_generation_commit(
    *,
    workspace_root: Path,
    generator_config_path: Path,
    expected_producer_mode: str,
) -> Mapping[str, Any]:
    """Deeply validate receipt, 12 exact-key files, labels, registry, and bundle."""

    root = workspace_root.resolve(strict=True)
    generator_path = generator_config_path
    if not generator_path.is_absolute():
        generator_path = root / generator_path
    generator_path = generator_path.resolve(strict=True)
    generator_config = load_generator_config(generator_path)
    generator = _mapping(generator_config["generator"], "generator")
    outputs = _mapping(generator_config["outputs"], "outputs")
    main_config_path = _resolve_file(
        root,
        generator["main_config"],
        "generator.main_config",
    )
    main_config = fusion_contract.load_config(main_config_path)
    fusion_contract.validate_config(main_config)
    if expected_producer_mode not in {"production", "synthetic_test_only"}:
        raise S106ComponentGeneratorError("generator producer mode is invalid")
    production = expected_producer_mode == "production"
    if production:
        _validate_canonical_generator_config_binding(
            root=root,
            generator_config_path=generator_path,
            main_config=main_config,
        )
        fusion_contract.validate_implementation_sources(
            workspace_root=root,
            config=main_config,
        )
    expected_producer_identity = _producer_identity(
        main_config,
        production=production,
    )
    execution_lock_path = _resolve_file(
        root,
        generator["main_execution_lock"],
        "generator.main_execution_lock",
    )
    bundle_path = _resolve_file(
        root,
        outputs["component_bundle_commit"],
        "component bundle",
    )
    commit_path = _resolve_file(
        root,
        outputs["generator_commit"],
        "generator commit",
    )
    registry_path = _resolve_file(root, outputs["registry"], "generator registry")
    receipt_path = _resolve_file(
        root,
        outputs["feature_freeze_receipt"],
        "feature-freeze receipt",
    )
    for path, name in (
        (bundle_path, "component bundle"),
        (commit_path, "generator commit"),
        (registry_path, "generator registry"),
        (receipt_path, "feature-freeze receipt"),
    ):
        _require_immutable_regular_file(path, name)
    commit = _load_json_stable(commit_path, "generator commit")
    registry = _load_json_stable(registry_path, "generator registry")
    receipt = _load_json_stable(receipt_path, "feature-freeze receipt")
    validate_feature_freeze_receipt(receipt)
    if receipt.get("generation_id") != receipt.get("feature_digest_sha256"):
        raise S106ComponentGeneratorError("generation ID must equal the feature digest")
    receipt_identity = _mapping(
        receipt.get("feature_identity_matrix"),
        "receipt feature identity",
    )
    if receipt_identity.get("path") != _relative_path(
        outputs["feature_identity_matrix"],
        "outputs.feature_identity_matrix",
    ):
        raise S106ComponentGeneratorError(
            "receipt feature identity canonical path differs"
        )
    expected_commit_keys = {
        "schema_version",
        "status",
        "generation_id",
        "producer_identity",
        "generator_config",
        "execution_lock",
        "feature_freeze_receipt",
        "label_matrix",
        "prediction_file_count",
        "feature_rows",
        "label_rows",
        "excluded_utterances",
        "access_audit",
        "binding_sha256",
    }
    expected_access_audit = {
        "predecessor_validation_completed_before_runtime_access": True,
        "label_free_scan_before_checkpoint_probe": True,
        "eligible_raw_ids_retained_in_memory_only": True,
        "restricted_parquet_disk_snapshot_created": False,
        "label_scanner_exact_eligible_id_held_fd_predicate_pushed_down": True,
        "label_staging_anonymous_unlinked": True,
        "source_parquet_scanner_batches_bounded": True,
        "label_rows_streamed_in_bounded_batches": True,
        "production_publication_serialized": production,
        "phone_alignments_opened_after_feature_freeze": True,
        "operation_opened_after_feature_freeze": True,
        "audio_pcm_hash_and_decode_used_same_held_bytes": True,
        "models_loaded_from_private_immutable_snapshot": True,
        "production_dependencies_immutable": production,
        "b07_unadapted_forward_separate": True,
        "s104_s105_adapted_forward_shared_per_seed": True,
        "completed_registry_published_before_visibility_bundle": True,
        "bundle_is_final_visibility_commit": True,
        "development_only": True,
        "stack_only": True,
    }
    excluded = commit.get("excluded_utterances")
    commit_label = _mapping(commit.get("label_matrix"), "commit label matrix")
    if (
        set(commit) != expected_commit_keys
        or commit.get("schema_version")
        != (
            GENERATOR_COMMIT_SCHEMA_VERSION
            if production
            else SYNTHETIC_GENERATOR_COMMIT_SCHEMA_VERSION
        )
        or commit.get("status") != "committed"
        or commit.get("binding_sha256") != _binding(commit)
        or commit.get("producer_identity") != expected_producer_identity
        or commit.get("generation_id") != receipt.get("generation_id")
        or commit.get("prediction_file_count") != 12
        or commit.get("feature_rows") != receipt.get("row_count")
        or commit.get("label_rows") != receipt.get("row_count")
        or commit.get("access_audit") != expected_access_audit
        or commit_label.get("path")
        != _relative_path(outputs["label_matrix"], "outputs.label_matrix")
        or not isinstance(excluded, Mapping)
        or set(excluded) != {"qc_flagged", "reserved_prompt"}
        or any(
            isinstance(excluded.get(name), bool)
            or not isinstance(excluded.get(name), int)
            or int(excluded[name]) < 0
            for name in ("qc_flagged", "reserved_prompt")
        )
    ):
        raise S106ComponentGeneratorError("generator commit contract differs")
    expected_registry_keys = {
        "schema_version",
        "status",
        "run_id",
        "failure",
        "generation_id",
        "producer_identity",
        "commit",
        "component_bundle",
        "feature_freeze_receipt",
        "artifact_counts",
        "privacy",
        "binding_sha256",
    }
    if (
        set(registry) != expected_registry_keys
        or registry.get("schema_version")
        != (
            REGISTRY_SCHEMA_VERSION if production else SYNTHETIC_REGISTRY_SCHEMA_VERSION
        )
        or registry.get("status") != "completed"
        or registry.get("run_id") != "aihub71490-wavlm-s106-component-inputs-v1"
        or registry.get("failure") is not None
        or registry.get("generation_id") != commit.get("generation_id")
        or registry.get("producer_identity") != expected_producer_identity
        or registry.get("artifact_counts")
        != {
            "feature_identity": 1,
            "component_predictions": 12,
            "label_matrix": 1,
            "feature_freeze_receipt": 1,
            "component_bundle": 1,
            "generator_commit": 1,
        }
        or registry.get("privacy")
        != {
            "raw_identifiers_published": False,
            "raw_paths_published": False,
            "target_or_realized_phone_identity_published": False,
            "row_level_scope": "D1_restricted_internal",
        }
        or registry.get("binding_sha256") != _binding(registry)
    ):
        raise S106ComponentGeneratorError("generator registry contract differs")

    def plain_record(path: Path) -> Mapping[str, Any]:
        return {
            "path": _workspace_relative(root, path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }

    if (
        commit.get("generator_config") != plain_record(generator_path)
        or commit.get("execution_lock")
        != {
            "path": _workspace_relative(root, execution_lock_path),
            "sha256": sha256_file(execution_lock_path),
        }
        or commit.get("feature_freeze_receipt") != plain_record(receipt_path)
        or registry.get("commit") != plain_record(commit_path)
        or registry.get("component_bundle") != plain_record(bundle_path)
        or registry.get("feature_freeze_receipt")
        != commit.get("feature_freeze_receipt")
    ):
        raise S106ComponentGeneratorError(
            "generator commit/registry file binding differs"
        )
    if production:
        producer_evidence = validate_component_producer_evidence(
            workspace_root=root,
            generator_config_path=generator_path,
            generation_id=str(commit["generation_id"]),
            receipt_path=receipt_path,
            receipt=receipt,
            label_record=commit_label,
        )
        if producer_evidence.get("producer_identity") != expected_producer_identity:
            raise S106ComponentGeneratorError("producer evidence identity differs")
    else:
        _validate_generation_tree_and_public_copies(
            root=root,
            outputs=outputs,
            generation_id=str(commit["generation_id"]),
            receipt=receipt,
            receipt_path=receipt_path,
        )
    from voice_coach.training import wavlm_s106_fusion_training as fusion_training

    execution_lock = fusion_training.validate_execution_lock(
        root=root,
        config_path=main_config_path,
        config=main_config,
        lock_path=execution_lock_path,
    )
    bundle = fusion_training.validate_component_bundle_commit(
        root=root,
        config=main_config,
        execution_lock_path=execution_lock_path,
        execution_lock=execution_lock,
        bundle_path=bundle_path,
    )
    bundle_document = bundle.document
    if (
        bundle_document.get("schema_version")
        != (
            projection.COMPONENT_BUNDLE_SCHEMA_VERSION
            if production
            else SYNTHETIC_BUNDLE_SCHEMA_VERSION
        )
        or bundle_document.get("generator_commit") != plain_record(commit_path)
        or bundle_document.get("feature_freeze_receipt") != plain_record(receipt_path)
        or bundle_document.get("producer_identity") != expected_producer_identity
        or receipt.get("execution_lock") != bundle_document.get("execution_lock")
        or receipt.get("stack_source") != bundle_document.get("stack_source")
        or receipt.get("feature_identity_matrix")
        != bundle_document.get("feature_identity_matrix")
        or receipt.get("components") != bundle_document.get("components")
        or commit.get("label_matrix") != bundle_document.get("label_matrix")
    ):
        raise S106ComponentGeneratorError(
            "feature receipt/generator commit/bundle lineage differs"
        )
    identity_record = _mapping(
        receipt.get("feature_identity_matrix"),
        "receipt identity",
    )
    identity_table = _stable_parquet_from_record(
        root=root,
        record=identity_record,
        name="feature identity",
    )
    if tuple(identity_table.column_names) != FEATURE_IDENTITY_COLUMNS:
        raise S106ComponentGeneratorError("identity exact columns differ")
    identity_rows = identity_table.to_pylist()
    identity_keys: list[tuple[str, int]] = []
    for index, row in enumerate(identity_rows):
        utterance = _sha256(
            row.get("utterance_id_sha256"),
            f"identity row {index} utterance",
        )
        _sha256(row.get("speaker_id_sha256"), f"identity row {index} speaker")
        phone_index = row.get("phone_index")
        if (
            isinstance(phone_index, bool)
            or not isinstance(phone_index, int)
            or phone_index < 0
            or row.get("split") != "stack"
            or row.get("alignment_confidence") is not None
            or row.get("qc_flagged") is not False
            or row.get("reserved_prompt") is not False
        ):
            raise S106ComponentGeneratorError("identity row contract differs")
        identity_keys.append((utterance, phone_index))
    if len(identity_keys) != len(set(identity_keys)) or identity_keys != sorted(
        identity_keys
    ):
        raise S106ComponentGeneratorError("identity key order differs")
    key_digest = hashlib.sha256(
        canonical_json_bytes(
            [[utterance, phone_index] for utterance, phone_index in identity_keys],
            pretty=False,
        )
    ).hexdigest()
    if receipt.get("canonical_row_key_order_sha256") != key_digest or receipt.get(
        "row_count"
    ) != len(identity_keys):
        raise S106ComponentGeneratorError("receipt row-key digest differs")
    components = _mapping(receipt.get("components"), "receipt components")
    for component in projection.COMPONENTS:
        seeds = _mapping(components.get(component), f"{component} seeds")
        for seed in projection.SEEDS:
            record = _mapping(
                seeds.get(f"seed_{seed}"),
                f"{component} seed {seed}",
            )
            table = _stable_parquet_from_record(
                root=root,
                record=record,
                name=f"{component} seed {seed}",
            )
            if tuple(table.column_names) != PREDICTION_COLUMNS:
                raise S106ComponentGeneratorError(
                    f"{component} seed {seed} columns differ"
                )
            expected_metadata = _prediction_metadata(
                component=component,
                seed=seed,
                source_lineage_sha256=str(record["source_lineage_sha256"]),
                source_checkpoint_sha256s=cast(
                    Sequence[str],
                    record["source_checkpoint_sha256s"],
                ),
            )
            if table.schema.metadata != expected_metadata:
                raise S106ComponentGeneratorError(
                    f"{component} seed {seed} metadata differs"
                )
            rows = table.to_pylist()
            keys = [
                (str(row["utterance_id_sha256"]), int(row["phone_index"]))
                for row in rows
            ]
            if keys != identity_keys:
                raise S106ComponentGeneratorError(
                    f"{component} seed {seed} key/order differs"
                )
            for index, row in enumerate(rows):
                probability = row.get("probability")
                if (
                    row.get("speaker_id_sha256")
                    != identity_rows[index]["speaker_id_sha256"]
                    or row.get("split") != "stack"
                    or isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not math.isfinite(float(probability))
                    or not 0.0 <= float(probability) <= 1.0
                ):
                    raise S106ComponentGeneratorError(
                        f"{component} seed {seed} row differs"
                    )
    label_record = _mapping(commit.get("label_matrix"), "commit label matrix")
    label_table = _stable_parquet_from_record(
        root=root,
        record=label_record,
        name="label matrix",
    )
    if tuple(label_table.column_names) != LABEL_COLUMNS:
        raise S106ComponentGeneratorError("label exact columns differ")
    label_rows = label_table.to_pylist()
    label_keys = [
        (str(row["utterance_id_sha256"]), int(row["phone_index"])) for row in label_rows
    ]
    if label_keys != identity_keys:
        raise S106ComponentGeneratorError("label key/order differs")
    for index, row in enumerate(label_rows):
        if (
            row.get("speaker_id_sha256") != identity_rows[index]["speaker_id_sha256"]
            or row.get("split") != "stack"
            or row.get("reference_binary_error_label") not in {0, 1}
        ):
            raise S106ComponentGeneratorError("label row contract differs")
    matrix = fusion_training.build_component_matrix(root=root, bundle=bundle)
    if len(matrix.rows) != len(identity_keys):
        raise S106ComponentGeneratorError("consumer matrix row count differs")
    return {
        "status": "valid",
        "generation_id": commit["generation_id"],
        "feature_rows": len(identity_keys),
        "label_rows": len(label_keys),
        "prediction_file_count": 12,
        "feature_freeze_receipt_sha256": sha256_file(receipt_path),
        "component_bundle_sha256": sha256_file(bundle_path),
        "generator_commit_sha256": sha256_file(commit_path),
        "registry_sha256": sha256_file(registry_path),
        "consumer_feature_freeze_sha256": matrix.feature_freeze_sha256,
        "all_twelve_predictions_exact_key_order": True,
        "bundle_final_visibility_commit": True,
    }


def validate_component_generation_commit(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> Mapping[str, Any]:
    """Deeply validate only an immutable-dependency production generation."""

    return _validate_component_generation_commit(
        workspace_root=workspace_root,
        generator_config_path=generator_config_path,
        expected_producer_mode="production",
    )


def _validate_test_component_generation_commit(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> Mapping[str, Any]:
    """Test-only counterpart; synthetic schemas cannot pass the public gate."""

    return _validate_component_generation_commit(
        workspace_root=workspace_root,
        generator_config_path=generator_config_path,
        expected_producer_mode="synthetic_test_only",
    )


def recover_component_generation(
    *,
    workspace_root: Path,
    generator_config_path: Path,
) -> Mapping[str, Any] | None:
    """Recover only a generation already visible through the final bundle."""

    root = workspace_root.resolve(strict=True)
    config_path = generator_config_path
    if not config_path.is_absolute():
        config_path = root / config_path
    config = load_generator_config(config_path.resolve(strict=True))
    bundle_path = root / str(
        _mapping(config["outputs"], "outputs")["component_bundle_commit"]
    )
    if not bundle_path.is_file():
        return None
    return validate_component_generation_commit(
        workspace_root=root,
        generator_config_path=config_path,
    )


__all__ = [
    "FEATURE_FREEZE_SCHEMA_VERSION",
    "FEATURE_IDENTITY_COLUMNS",
    "GeneratedComponentInputs",
    "LabelFreeStackScan",
    "LABEL_COLUMNS",
    "LABEL_FREE_STACK_COLUMNS",
    "LabelFreeUtterance",
    "PREDICTION_COLUMNS",
    "ProductionProjectionBackend",
    "ProjectionBackend",
    "S106ComponentGeneratorError",
    "TargetInterval",
    "WordInterval",
    "build_feature_freeze_receipt",
    "canonical_json_bytes",
    "load_generator_config",
    "materialize_label_rows",
    "production_backend_factory",
    "recover_component_generation",
    "run_component_generation",
    "run_component_generation_for_test",
    "scan_label_free_stack",
    "sha256_file",
    "validate_feature_freeze_receipt",
    "validate_generator_config",
    "validate_component_generation_commit",
    "validate_static_generator",
]
