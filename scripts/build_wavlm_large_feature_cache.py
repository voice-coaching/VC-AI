#!/usr/bin/env python3
"""Build a frozen, pinned WavLM Large feature cache.

This is a separate S102 entry point.  It reuses the already audited generic
streaming/cache machinery from the immutable Base+ builder at an exact
dependency SHA-256, while pinning a distinct local-only Large checkpoint,
architecture, output dimensions, report metadata, and full-run gate.

The builder is development-only.  It rejects evaluation splits before audio
or labels reach inference, applies the same QC and reserved-prompt exclusions
as S101, and exports no restricted locator, transcript, realized-phone, or
alignment-operation identity.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import torch
import yaml  # type: ignore[import-untyped]

from voice_coach.features.ssl import WavLMFrameGeometry  # type: ignore[import-untyped]


MODEL_ID = "microsoft/wavlm-large"
MODEL_REVISION = "c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c"
MODEL_CACHE_NAME = "models--microsoft--wavlm-large"
MODEL_CONFIG_SHA256 = "a3d8fe831aaf63d725b54a8ac36f3549cd4365c5086774b2c89cabbc6f9e129d"
MODEL_CHECKPOINT_SHA256 = (
    "fdee460e529396ddb2f8c8e8ce0ad74cfb747b726bc6f612e666c7c1e1963c9d"
)
MODEL_PREPROCESSOR_SHA256 = (
    "60ca5a31e13f69ee2fbf147504c8676db5f6398fd7a6b12294341dff838edfcf"
)
HIDDEN_SIZE = 1_024
NUM_HIDDEN_LAYERS = 24
NUM_ATTENTION_HEADS = 16
INTERMEDIATE_SIZE = 4_096
CONV_DIM = (512, 512, 512, 512, 512, 512, 512)
CONV_KERNEL = (10, 3, 3, 3, 3, 2, 2)
CONV_STRIDE = (5, 2, 2, 2, 2, 2, 2)
FEAT_EXTRACT_NORM = "layer"
FEAT_EXTRACT_ACTIVATION = "gelu"
HIDDEN_ACTIVATION = "gelu"
DO_NORMALIZE = True
SAMPLE_RATE_HZ = 16_000
MAX_CHUNK_SECONDS = 20.0
ALLOWED_SPLITS = ("train", "dev")
EVALUATION_SPLITS = frozenset({"stack", "calibration", "locked_test"})
MODELING_MANIFEST_SHA256 = (
    "32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1"
)
PROMPT_RESERVATION_SHA256 = (
    "3ea7f12ea76be07a0796d7e62f0c3727ca9858c0278df7c85ac1e08d9b653fa9"
)
PROHIBITED_EXPORTS = (
    "raw_utterance_id",
    "raw_speaker_identity",
    "audio_path",
    "source_file_stem",
    "word_label",
    "realized_phone_identity",
    "alignment_operation",
    "error_tag",
    "holistic_score",
)
PINNED_PREPROCESSOR_URL = (
    "https://huggingface.co/microsoft/wavlm-large/blob/"
    f"{MODEL_REVISION}/preprocessor_config.json"
)

BASE_PLUS_MODEL_ID = "microsoft/wavlm-base-plus"
BASE_PLUS_MODEL_REVISION = "4c66d4806a428f2e922ccfa1a962776e232d487b"
BASE_PLUS_MODEL_CONFIG_SHA256 = (
    "fea6df1c2700a3954fc07e70588aecc9055eeb28db2ff57151a2db0d19180ed4"
)
BASE_PLUS_MODEL_CHECKPOINT_SHA256 = (
    "3bb273a6ace99408b50cfc81afdbb7ef2de02da2eab0234e18db608ce692fe51"
)
BASE_PLUS_CACHE_CONFIG_SHA256 = (
    "3641d04c974363d615cbc914a3e9d80eff39f9c0e64bc338aa2ff04ab9fef1ba"
)
BASE_PLUS_HEAD_CONFIG_SHA256 = (
    "9d0b8de8e8f41c66028c4bf895cb653dc77e69b64cdce3b9f1f7d0c59e8e0291"
)
BASE_PLUS_HEAD_TRAINER_SHA256 = (
    "0c0bd3b76e5e9bf0b9159c80c2d6267dde5ed2f74b88f88f639efecbe37d94a3"
)
BASE_PLUS_HEAD_MODEL_SHA256 = (
    "e5cdad3ece84e0ff8320b7366a64f0255a4be1a52d9df39654fb714f8f62f78b"
)
BASE_PLUS_EXPLICIT_LABEL_SHA256 = (
    "a9f99d66b200468dd23733b232265c035577c81e6bf7aaa565871c7f5863558f"
)
BASE_PLUS_HEAD_EXPERIMENT_ID = "aihub71490_wavlm_base_plus_frozen_head_v1"
BASE_PLUS_HEAD_REPORT_SCHEMA = "voice_coach.wavlm_frozen_head_report.v1"
REQUIRED_SEEDS = [42, 43, 44]
EXPECTED_CACHE_ROWS = {
    "utterance": 169_837,
    "phone": 2_233_039,
    "word": 408_790,
}
SMOKE_REVIEW_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_smoke_review.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REVIEWER_CLI_RELATIVE_PATH = Path("scripts/review_wavlm_feature_cache_smoke.py")
REVIEWER_CORE_RELATIVE_PATH = Path(
    "src/voice_coach/reproducibility/wavlm_smoke_review.py"
)
FULL_CACHE_VALIDATION_REQUIRES_ALL_ALLOWED_SPLITS = True

BASE_ENGINE_PATH = Path(__file__).with_name("build_wavlm_feature_cache.py")
BASE_ENGINE_SHA256 = "2acd48d4f7dbb735dab96cdf4b9222a654627a96cf6a068539f0480839a56dde"

WAVLM_LARGE_FRAME_GEOMETRY = WavLMFrameGeometry(
    sample_rate_hz=SAMPLE_RATE_HZ,
    conv_kernels=CONV_KERNEL,
    conv_strides=CONV_STRIDE,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_pinned_engine() -> Any:
    if _sha256_file(BASE_ENGINE_PATH) != BASE_ENGINE_SHA256:
        raise RuntimeError(
            "the immutable Base+ cache engine differs from the Large dependency pin"
        )
    module_name = "_voice_coach_wavlm_large_pinned_cache_engine"
    spec = importlib.util.spec_from_file_location(module_name, BASE_ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("could not load the pinned WavLM cache engine")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_ENGINE = _load_pinned_engine()
_ORIGINAL_BASE_REPORT = _ENGINE._base_report

# Configure the isolated dependency module.  This mutates only this process's
# private import, never the Base+ builder file or the already running S101 job.
_ENGINE.__file__ = str(Path(__file__).resolve())
_ENGINE.MODEL_ID = MODEL_ID
_ENGINE.MODEL_REVISION = MODEL_REVISION
_ENGINE.PINNED_PREPROCESSOR_URL = PINNED_PREPROCESSOR_URL
_ENGINE.HIDDEN_SIZE = HIDDEN_SIZE
_ENGINE.DO_NORMALIZE = DO_NORMALIZE
_ENGINE.SAMPLE_RATE_HZ = SAMPLE_RATE_HZ
_ENGINE.MAX_CHUNK_SECONDS = MAX_CHUNK_SECONDS
_ENGINE.ALLOWED_SPLITS = ALLOWED_SPLITS
_ENGINE.EVALUATION_SPLITS = EVALUATION_SPLITS
_ENGINE.WAVLM_BASE_PLUS_FRAME_GEOMETRY = WAVLM_LARGE_FRAME_GEOMETRY
_ENGINE.UTTERANCE_SCHEMA = _ENGINE._fixed_embedding_schema(
    kind="utterance",
    dimensions=HIDDEN_SIZE * 2,
)
_ENGINE.PHONE_SCHEMA = _ENGINE._fixed_embedding_schema(
    kind="phone",
    dimensions=HIDDEN_SIZE,
)
_ENGINE.WORD_SCHEMA = _ENGINE._fixed_embedding_schema(
    kind="word",
    dimensions=HIDDEN_SIZE,
)

UTTERANCE_SCHEMA = _ENGINE.UTTERANCE_SCHEMA
PHONE_SCHEMA = _ENGINE.PHONE_SCHEMA
WORD_SCHEMA = _ENGINE.WORD_SCHEMA
UTTERANCE_SCHEMA_VERSION = _ENGINE.UTTERANCE_SCHEMA_VERSION
PHONE_SCHEMA_VERSION = _ENGINE.PHONE_SCHEMA_VERSION
WORD_SCHEMA_VERSION = _ENGINE.WORD_SCHEMA_VERSION
REPORT_SCHEMA_VERSION = _ENGINE.REPORT_SCHEMA_VERSION
PROHIBITED_OUTPUT_COLUMNS = _ENGINE.PROHIBITED_OUTPUT_COLUMNS
SOURCE_COLUMNS = _ENGINE.SOURCE_COLUMNS
FeatureBuildError = _ENGINE.FeatureBuildError
ExtractedRows = _ENGINE.ExtractedRows
ShardedWriter = _ENGINE.ShardedWriter
hash_utterance_id = _ENGINE.hash_utterance_id
schema_with_metadata = _ENGINE.schema_with_metadata
assert_schema_privacy = _ENGINE.assert_schema_privacy
target_binary_labels = _ENGINE.target_binary_labels
extract_rows = _ENGINE.extract_rows
_gpu_metadata = _ENGINE._gpu_metadata


def _expect_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def load_and_validate_config(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = dict(_expect_mapping(loaded, "config"))
    experiment = _expect_mapping(root.get("experiment"), "experiment")
    model = _expect_mapping(root.get("model"), "model")
    data = _expect_mapping(root.get("data"), "data")
    features = _expect_mapping(root.get("features"), "features")
    output = _expect_mapping(root.get("output"), "output")
    gate = _expect_mapping(root.get("full_run_gate"), "full_run_gate")

    expected_experiment = {
        "id": "aihub71490_wavlm_large_frozen_v1",
        "stage": "feature_cache",
        "development_only": True,
    }
    for key, expected in expected_experiment.items():
        if experiment.get(key) != expected:
            raise ValueError(f"experiment.{key} must equal {expected!r}")

    expected_model = {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "local_files_only": True,
        "frozen": True,
        "config_sha256": MODEL_CONFIG_SHA256,
        "checkpoint_sha256": MODEL_CHECKPOINT_SHA256,
        "preprocessor_config_sha256": MODEL_PREPROCESSOR_SHA256,
        "hidden_size": HIDDEN_SIZE,
        "num_hidden_layers": NUM_HIDDEN_LAYERS,
        "num_attention_heads": NUM_ATTENTION_HEADS,
        "intermediate_size": INTERMEDIATE_SIZE,
        "conv_dim": list(CONV_DIM),
        "conv_kernel": list(CONV_KERNEL),
        "conv_stride": list(CONV_STRIDE),
        "feat_extract_norm": FEAT_EXTRACT_NORM,
        "feat_extract_activation": FEAT_EXTRACT_ACTIVATION,
        "hidden_activation": HIDDEN_ACTIVATION,
        "do_normalize": DO_NORMALIZE,
        "return_attention_mask": True,
    }
    for key, expected in expected_model.items():
        if model.get(key) != expected:
            raise ValueError(f"model.{key} must equal pinned value {expected!r}")

    expected_data = {
        "modeling_manifest_sha256": MODELING_MANIFEST_SHA256,
        "allowed_splits": list(ALLOWED_SPLITS),
        "forbidden_metric_splits": sorted(EVALUATION_SPLITS),
        "exclude_qc_flagged": True,
        "exclude_reserved_prompts": True,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "max_chunk_seconds": MAX_CHUNK_SECONDS,
        "chunk_boundary_source": "target_phone_intervals",
    }
    for key, expected in expected_data.items():
        actual = data.get(key)
        if key == "forbidden_metric_splits":
            actual = sorted(actual) if isinstance(actual, list) else actual
        if actual != expected:
            raise ValueError(f"data.{key} must equal {expected!r}")

    expected_features = {
        "cuda_autocast_precision": "bfloat16",
        "observed_model_output_dtype": "record_at_runtime",
        "storage_precision": "float16",
        "utterance_pooling": "mean_std",
        "target_phone_pooling": "mean",
        "word_pooling": "mean",
    }
    for key, expected in expected_features.items():
        if features.get(key) != expected:
            raise ValueError(f"features.{key} must equal {expected!r}")
    supervision = _expect_mapping(
        features.get("phone_supervision"),
        "features.phone_supervision",
    )
    if (
        supervision.get("task") != "binary_error"
        or supervision.get("negative_operations") != ["match"]
        or supervision.get("positive_operations") != ["substitution", "deletion"]
        or supervision.get("ignored_operations") != ["insertion"]
        or supervision.get("label_role_only") is not True
    ):
        raise ValueError("phone supervision policy differs from the pin")
    if features.get("prohibited_exports") != list(PROHIBITED_EXPORTS):
        raise ValueError("features.prohibited_exports differs from the privacy pin")

    for key in (
        "utterance_shard_rows",
        "phone_shard_rows",
        "word_shard_rows",
        "utterance_row_group_rows",
        "phone_row_group_rows",
        "word_row_group_rows",
    ):
        if not _is_positive_int(output.get(key)):
            raise ValueError(f"output.{key} must be a positive integer")
    for kind in ("utterance", "phone", "word"):
        row_group_rows = output[f"{kind}_row_group_rows"]
        shard_rows = output[f"{kind}_shard_rows"]
        if row_group_rows > shard_rows or row_group_rows > 4_096:
            raise ValueError(
                f"output.{kind}_row_group_rows must not exceed its shard size or 4096"
            )
    if output.get("compression") != "zstd":
        raise ValueError("output.compression must equal 'zstd'")
    if output.get("atomic_publish") is not True:
        raise ValueError("output.atomic_publish must be true")

    if (
        gate.get("require_completed_base_plus_head_result") is not True
        or gate.get("require_completed_smoke_review") is not True
        or gate.get("require_resource_review") is not True
        or gate.get("smoke_utterances") != 100
    ):
        raise ValueError("full-run gate policy differs from the Large pin")
    head_contract = _expect_mapping(
        gate.get("base_plus_head_contract"),
        "full_run_gate.base_plus_head_contract",
    )
    expected_head_contract = {
        "schema_version": BASE_PLUS_HEAD_REPORT_SCHEMA,
        "experiment_id": BASE_PLUS_HEAD_EXPERIMENT_ID,
        "encoder_id": BASE_PLUS_MODEL_ID,
        "encoder_revision": BASE_PLUS_MODEL_REVISION,
        "seeds_completed": REQUIRED_SEEDS,
        "modeling_manifest_sha256": MODELING_MANIFEST_SHA256,
        "prompt_reservation_sha256": PROMPT_RESERVATION_SHA256,
        "cache_config_sha256": BASE_PLUS_CACHE_CONFIG_SHA256,
        "cache_builder_sha256": BASE_ENGINE_SHA256,
        "encoder_config_sha256": BASE_PLUS_MODEL_CONFIG_SHA256,
        "encoder_checkpoint_sha256": BASE_PLUS_MODEL_CHECKPOINT_SHA256,
        "head_config_sha256": BASE_PLUS_HEAD_CONFIG_SHA256,
        "trainer_source_sha256": BASE_PLUS_HEAD_TRAINER_SHA256,
        "head_model_source_sha256": BASE_PLUS_HEAD_MODEL_SHA256,
        "explicit_label_sha256": BASE_PLUS_EXPLICIT_LABEL_SHA256,
    }
    for key, expected in expected_head_contract.items():
        if head_contract.get(key) != expected:
            raise ValueError(
                f"full_run_gate.base_plus_head_contract.{key} must equal {expected!r}"
            )
    reference_rows = gate.get("resource_estimate_reference_rows")
    if not isinstance(reference_rows, Mapping) or any(
        not _is_positive_int(reference_rows.get(kind))
        for kind in ("utterance", "phone", "word")
    ):
        raise ValueError("full-run resource reference rows are invalid")
    if {
        kind: int(reference_rows[kind]) for kind in ("utterance", "phone", "word")
    } != EXPECTED_CACHE_ROWS:
        raise ValueError("full-run resource reference rows differ from the audited pin")
    safety_factor = gate.get("resource_estimate_safety_factor")
    if (
        not isinstance(safety_factor, (int, float))
        or isinstance(safety_factor, bool)
        or safety_factor < 1
    ):
        raise ValueError("resource estimate safety factor must be at least 1")
    return root


def resolve_local_model_files(*, cache_dir: Path) -> dict[str, Path]:
    snapshot = cache_dir / MODEL_CACHE_NAME / "snapshots" / MODEL_REVISION
    files = {
        "config": snapshot / "config.json",
        "checkpoint": snapshot / "pytorch_model.bin",
        "preprocessor": snapshot / "preprocessor_config.json",
    }
    for role, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"pinned local WavLM Large {role} is unavailable")
    return files


def inspect_pinned_local_metadata(*, cache_dir: Path) -> dict[str, Any]:
    """Read and verify only local pinned model files; never contact a hub."""

    files = resolve_local_model_files(cache_dir=cache_dir)
    hashes = {role: _sha256_file(path) for role, path in files.items()}
    if hashes["config"] != MODEL_CONFIG_SHA256:
        raise ValueError("local WavLM Large config differs from the pin")
    if hashes["checkpoint"] != MODEL_CHECKPOINT_SHA256:
        raise ValueError("local WavLM Large checkpoint differs from the pin")
    if hashes["preprocessor"] != MODEL_PREPROCESSOR_SHA256:
        raise ValueError("local WavLM Large preprocessor differs from the pin")
    document = json.loads(files["config"].read_text(encoding="utf-8"))
    expected = {
        "hidden_size": HIDDEN_SIZE,
        "num_hidden_layers": NUM_HIDDEN_LAYERS,
        "num_attention_heads": NUM_ATTENTION_HEADS,
        "intermediate_size": INTERMEDIATE_SIZE,
        "conv_dim": list(CONV_DIM),
        "conv_kernel": list(CONV_KERNEL),
        "conv_stride": list(CONV_STRIDE),
        "feat_extract_norm": FEAT_EXTRACT_NORM,
        "feat_extract_activation": FEAT_EXTRACT_ACTIVATION,
        "hidden_act": HIDDEN_ACTIVATION,
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise ValueError(f"local WavLM Large config.{key} differs from the pin")
    preprocessor = json.loads(files["preprocessor"].read_text(encoding="utf-8"))
    expected_preprocessor = {
        "do_normalize": DO_NORMALIZE,
        "feature_extractor_type": "Wav2Vec2FeatureExtractor",
        "feature_size": 1,
        "padding_side": "right",
        "padding_value": 0.0,
        "return_attention_mask": True,
        "sampling_rate": SAMPLE_RATE_HZ,
    }
    if preprocessor != expected_preprocessor:
        raise ValueError("local WavLM Large preprocessor fields differ from the pin")
    return {
        "files": {role: str(path) for role, path in files.items()},
        "hashes": hashes,
        "architecture": expected,
        "preprocessor": expected_preprocessor,
        "frame_stride_samples": WAVLM_LARGE_FRAME_GEOMETRY.frame_stride_samples,
        "receptive_field_samples": (WAVLM_LARGE_FRAME_GEOMETRY.receptive_field_samples),
    }


def load_frozen_model(
    *,
    cache_dir: Path,
    device: torch.device,
) -> tuple[torch.nn.Module, dict[str, Any]]:
    from transformers import WavLMModel

    local = inspect_pinned_local_metadata(cache_dir=cache_dir)
    model = WavLMModel.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        cache_dir=str(cache_dir),
        local_files_only=True,
        use_safetensors=False,
    )
    config = model.config
    if getattr(config, "_commit_hash", None) != MODEL_REVISION:
        raise ValueError("loaded WavLM Large config does not match pinned revision")
    expected = {
        "hidden_size": HIDDEN_SIZE,
        "num_hidden_layers": NUM_HIDDEN_LAYERS,
        "num_attention_heads": NUM_ATTENTION_HEADS,
        "intermediate_size": INTERMEDIATE_SIZE,
        "conv_dim": CONV_DIM,
        "conv_kernel": CONV_KERNEL,
        "conv_stride": CONV_STRIDE,
        "feat_extract_norm": FEAT_EXTRACT_NORM,
        "feat_extract_activation": FEAT_EXTRACT_ACTIVATION,
        "hidden_act": HIDDEN_ACTIVATION,
    }
    for key, expected_value in expected.items():
        actual = getattr(config, key)
        if key in {"conv_dim", "conv_kernel", "conv_stride"}:
            actual = tuple(actual)
        if actual != expected_value:
            raise ValueError(f"loaded WavLM Large {key} differs from the pin")
    model.eval()
    model.requires_grad_(False)
    model.to(device)  # type: ignore[arg-type]
    if any(parameter.requires_grad for parameter in model.parameters()):
        raise AssertionError("WavLM Large encoder is not frozen")
    return model, {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "config_sha256": local["hashes"]["config"],
        "checkpoint_sha256": local["hashes"]["checkpoint"],
        "preprocessor_config_sha256": local["hashes"]["preprocessor"],
        "hidden_size": HIDDEN_SIZE,
        "num_hidden_layers": NUM_HIDDEN_LAYERS,
        "num_attention_heads": NUM_ATTENTION_HEADS,
        "intermediate_size": INTERMEDIATE_SIZE,
        "conv_dim": list(CONV_DIM),
        "conv_kernel": list(CONV_KERNEL),
        "conv_stride": list(CONV_STRIDE),
        "frame_stride_samples": WAVLM_LARGE_FRAME_GEOMETRY.frame_stride_samples,
        "receptive_field_samples": (WAVLM_LARGE_FRAME_GEOMETRY.receptive_field_samples),
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "frozen": True,
        "local_files_only": True,
        "do_normalize": DO_NORMALIZE,
        "preprocessor_contract": {
            "sampling_rate_hz": SAMPLE_RATE_HZ,
            "do_normalize": DO_NORMALIZE,
            "return_attention_mask": True,
            "source": "official_pinned_revision",
            "source_url": PINNED_PREPROCESSOR_URL,
            "config_sha256": local["hashes"]["preprocessor"],
            "local_preprocessor_config_present": True,
            "local_preprocessor_config_validated": True,
            "auto_feature_extractor_used": False,
        },
    }


def _load_gate_document(path: Path, *, kind: str) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"{kind} gate document must be a mapping")
    return document


def _safe_relative_file(root: Path, value: object, *, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} path is invalid")
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"{name} path must be repository-relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise ValueError(f"{name} path is missing or escapes its root")
    return resolved


def _verified_registry_role_hashes(
    inputs: Mapping[str, Any],
    *,
    role: str,
) -> set[str]:
    entries = inputs.get(role)
    if not isinstance(entries, list):
        raise ValueError(f"Base+ registry {role} inputs are invalid")
    hashes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or not _is_sha256(entry.get("sha256")):
            raise ValueError(f"Base+ registry {role} input hash is invalid")
        path = _safe_relative_file(
            REPOSITORY_ROOT,
            entry.get("path"),
            name=f"Base+ registry {role} input",
        )
        if _sha256_file(path) != entry["sha256"]:
            raise ValueError(f"Base+ registry {role} input hash differs")
        if (
            not _is_positive_int(entry.get("size_bytes"))
            or path.stat().st_size != entry["size_bytes"]
        ):
            raise ValueError(f"Base+ registry {role} input size differs")
        hashes.add(str(entry["sha256"]))
    if len(hashes) != len(entries):
        raise ValueError(f"Base+ registry {role} inputs contain duplicate hashes")
    return hashes


def _validate_base_plus_head_report(path: Path) -> dict[str, str]:
    report = _load_gate_document(path, kind="Base+ head report")
    if report.get("schema_version") != BASE_PLUS_HEAD_REPORT_SCHEMA:
        raise ValueError("Base+ head report schema differs from the pin")
    if report.get("status") != "completed":
        raise ValueError("Base+ head report is not completed")
    if report.get("experiment_id") != BASE_PLUS_HEAD_EXPERIMENT_ID:
        raise ValueError("Base+ head experiment ID differs from the pin")
    if report.get("development_only") is not True:
        raise ValueError("Base+ head result is not development-only")
    encoder = report.get("encoder")
    expected_encoder = {
        "variant": "base_plus",
        "id": BASE_PLUS_MODEL_ID,
        "revision": BASE_PLUS_MODEL_REVISION,
        "hidden_size": 768,
        "utterance_embedding_size": 1_536,
        "frozen": True,
        "source_config_sha256": BASE_PLUS_MODEL_CONFIG_SHA256,
        "source_checkpoint_sha256": BASE_PLUS_MODEL_CHECKPOINT_SHA256,
        "current_experiment_variant": "base_plus",
    }
    if not isinstance(encoder, Mapping) or any(
        encoder.get(key) != expected for key, expected in expected_encoder.items()
    ):
        raise ValueError("Base+ head encoder contract is invalid")

    sources = report.get("sources")
    expected_sources = {
        "training_config_sha256": BASE_PLUS_HEAD_CONFIG_SHA256,
        "trainer_source_sha256": BASE_PLUS_HEAD_TRAINER_SHA256,
        "head_source_sha256": BASE_PLUS_HEAD_MODEL_SHA256,
        "explicit_utterance_labels_sha256": BASE_PLUS_EXPLICIT_LABEL_SHA256,
        "modeling_manifest_sha256": MODELING_MANIFEST_SHA256,
        "prompt_reservation_sha256": PROMPT_RESERVATION_SHA256,
        "cache_builder_config_sha256": BASE_PLUS_CACHE_CONFIG_SHA256,
        "cache_builder_sha256": BASE_ENGINE_SHA256,
        "encoder_config_sha256": BASE_PLUS_MODEL_CONFIG_SHA256,
        "encoder_checkpoint_sha256": BASE_PLUS_MODEL_CHECKPOINT_SHA256,
        "model_hashes_bound_by_validated_config_and_cache_report": True,
    }
    if not isinstance(sources, Mapping) or any(
        sources.get(key) != expected for key, expected in expected_sources.items()
    ):
        raise ValueError("Base+ head source hash chain differs from the pin")
    if not _is_sha256(sources.get("feature_cache_report_sha256")):
        raise ValueError("Base+ feature-cache report hash is invalid")

    summary = report.get("summary")
    if (
        not isinstance(summary, Mapping)
        or summary.get("seeds_completed") != REQUIRED_SEEDS
        or summary.get("results_are_development_only") is not True
        or summary.get("locked_test_remains_unread") is not True
        or summary.get("stack_remains_unread") is not True
        or summary.get("calibration_remains_unread") is not True
        or summary.get("dev_inference_passes_per_seed") != 1
    ):
        raise ValueError("Base+ head completion summary is invalid")
    leakage = report.get("leakage_controls")
    if not isinstance(leakage, Mapping):
        raise ValueError("Base+ head report has no leakage controls")
    if set(leakage.get("accepted_splits", [])) != set(ALLOWED_SPLITS):
        raise ValueError("Base+ head report is not train/dev-only")
    required_false = (
        "stack_accessed",
        "calibration_accessed",
        "locked_test_accessed",
        "dev_used_for_selection",
    )
    if any(leakage.get(key) is not False for key in required_false):
        raise ValueError("Base+ head report violates split isolation")
    if leakage.get("train_inner_validation_used") is not True:
        raise ValueError("Base+ head did not use train-only inner validation")

    cache = report.get("cache_contract")
    required_cache_true = (
        "full_cache_validated",
        "source_scan_complete",
        "smoke_cache_rejected",
        "every_shard_hash_validated",
        "every_shard_schema_validated",
        "every_shard_split_policy_validated",
        "every_shard_privacy_metadata_validated",
    )
    if (
        not isinstance(cache, Mapping)
        or any(cache.get(key) is not True for key in required_cache_true)
        or cache.get("access_scope") != "restricted_internal"
        or cache.get("shareable") is not False
        or not isinstance(cache.get("speaker_overlap_train_dev"), int)
        or isinstance(cache.get("speaker_overlap_train_dev"), bool)
        or cache.get("speaker_overlap_train_dev") != 0
        or cache.get("rows") != EXPECTED_CACHE_ROWS
    ):
        raise ValueError("Base+ head full-cache contract is incomplete")
    split_rows = cache.get("split_rows")
    fingerprints = cache.get("shard_fingerprints")
    if not isinstance(split_rows, Mapping) or not isinstance(fingerprints, Mapping):
        raise ValueError("Base+ head cache split/fingerprint contract is missing")
    shard_hashes: set[str] = set()
    for kind, expected_rows in EXPECTED_CACHE_ROWS.items():
        counts = split_rows.get(kind)
        if (
            not isinstance(counts, Mapping)
            or set(counts) != set(ALLOWED_SPLITS)
            or any(not _is_positive_int(value) for value in counts.values())
            or sum(int(value) for value in counts.values()) != expected_rows
        ):
            raise ValueError(f"Base+ head {kind} cache split rows are invalid")
        values = fingerprints.get(kind)
        if not isinstance(values, list) or not values:
            raise ValueError(f"Base+ head {kind} shard fingerprints are missing")
        fingerprint_rows = 0
        names: set[str] = set()
        for value in values:
            if not isinstance(value, Mapping):
                raise ValueError(f"Base+ head {kind} shard fingerprint is invalid")
            name = value.get("name")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not name.startswith("part-")
                or not name.endswith(".parquet")
                or name in names
                or not _is_positive_int(value.get("row_count"))
                or not _is_sha256(value.get("sha256"))
            ):
                raise ValueError(f"Base+ head {kind} shard fingerprint is invalid")
            names.add(name)
            fingerprint_rows += int(value["row_count"])
            shard_hashes.add(str(value["sha256"]))
        if fingerprint_rows != expected_rows:
            raise ValueError(f"Base+ head {kind} shard row total differs")

    runs = report.get("runs")
    if (
        not isinstance(runs, list)
        or [run.get("seed") if isinstance(run, Mapping) else None for run in runs]
        != REQUIRED_SEEDS
    ):
        raise ValueError("Base+ head run set differs from the required seeds")
    report_artifacts: dict[str, tuple[str, Path]] = {}
    for run in runs:
        seed = int(run["seed"])
        selection = run.get("selection")
        phone = run.get("phone")
        utterance = run.get("utterance")
        artifacts = run.get("artifacts")
        if (
            not isinstance(selection, Mapping)
            or selection.get("dev_values_used_for_selection") is not False
            or not isinstance(phone, Mapping)
            or not isinstance(utterance, Mapping)
            or not isinstance(artifacts, Mapping)
        ):
            raise ValueError(f"Base+ head seed {seed} run contract is invalid")
        reported = {
            f"seed_{seed}_checkpoint": artifacts.get("checkpoint"),
            f"seed_{seed}_phone_predictions": phone.get("predictions"),
            f"seed_{seed}_utterance_predictions": utterance.get("predictions"),
            f"seed_{seed}_slice_metrics": artifacts.get("slice_metrics"),
        }
        for name, artifact in reported.items():
            if not isinstance(artifact, Mapping) or not _is_sha256(
                artifact.get("sha256")
            ):
                raise ValueError(f"Base+ head {name} report hash is invalid")
            artifact_path = _safe_relative_file(
                path.parent,
                artifact.get("path"),
                name=f"Base+ head {name}",
            )
            if _sha256_file(artifact_path) != artifact["sha256"]:
                raise ValueError(f"Base+ head {name} artifact hash differs")
            report_artifacts[name] = (str(artifact["sha256"]), artifact_path)

    registry = report.get("experiment_registry")
    if (
        not isinstance(registry, Mapping)
        or registry.get("integrated") is not True
        or registry.get("t03_complete") is not True
        or registry.get("implementation_source_hashes_match_registry_inputs")
        is not True
        or registry.get("metrics_recorded") is not True
        or registry.get("artifacts_recorded") is not True
        or registry.get("registered_artifact_count") != 13
        or not isinstance(registry.get("run_id"), str)
    ):
        raise ValueError("Base+ head report is not registry-integrated")
    registry_root = REPOSITORY_ROOT / "artifacts" / "experiment_registry"
    registry_metadata_path = _safe_relative_file(
        REPOSITORY_ROOT,
        registry.get("metadata_path"),
        name="Base+ registry metadata",
    )
    if not registry_metadata_path.is_relative_to(registry_root.resolve()):
        raise ValueError("Base+ registry metadata is outside the registry root")
    metadata = _load_gate_document(
        registry_metadata_path,
        kind="Base+ registry metadata",
    )
    if (
        metadata.get("status") != "completed"
        or metadata.get("failure") is not None
        or metadata.get("run_id") != registry.get("run_id")
        or metadata.get("seeds")
        != {f"experiment_seed_{seed}": seed for seed in REQUIRED_SEEDS}
    ):
        raise ValueError("Base+ registry completion contract is invalid")
    inputs = metadata.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("Base+ registry input roles are missing")
    config_hashes = _verified_registry_role_hashes(inputs, role="config")
    data_hashes = _verified_registry_role_hashes(inputs, role="data")
    model_hashes = _verified_registry_role_hashes(inputs, role="model")
    checkpoint_hashes = _verified_registry_role_hashes(inputs, role="checkpoint")
    expected_data_hashes = {
        str(sources["feature_cache_report_sha256"]),
        BASE_PLUS_EXPLICIT_LABEL_SHA256,
        *shard_hashes,
    }
    if (
        config_hashes != {BASE_PLUS_HEAD_CONFIG_SHA256}
        or data_hashes != expected_data_hashes
        or model_hashes != {BASE_PLUS_HEAD_TRAINER_SHA256, BASE_PLUS_HEAD_MODEL_SHA256}
        or checkpoint_hashes
    ):
        raise ValueError("Base+ registry input hash chain differs from the report")

    registry_artifacts = metadata.get("artifacts")
    if not isinstance(registry_artifacts, list):
        raise ValueError("Base+ registry artifacts are missing")
    expected_registry_artifacts = {
        **report_artifacts,
        "final_report": (_sha256_file(path), path.resolve()),
    }
    if {
        item.get("name") if isinstance(item, Mapping) else None
        for item in registry_artifacts
    } != set(expected_registry_artifacts):
        raise ValueError("Base+ registry artifact set differs from the report")
    for item in registry_artifacts:
        if (
            not isinstance(item, Mapping)
            or not _is_sha256(item.get("sha256"))
            or not _is_positive_int(item.get("size_bytes"))
        ):
            raise ValueError("Base+ registry artifact metadata is invalid")
        name = str(item["name"])
        expected_hash, expected_path = expected_registry_artifacts[name]
        artifact_path = _safe_relative_file(
            REPOSITORY_ROOT,
            item.get("path"),
            name=f"Base+ registry artifact {name}",
        )
        if (
            item["sha256"] != expected_hash
            or artifact_path != expected_path
            or _sha256_file(artifact_path) != expected_hash
            or artifact_path.stat().st_size != item["size_bytes"]
        ):
            raise ValueError(f"Base+ registry artifact {name} differs")
    return {
        "report_sha256": _sha256_file(path),
        "registry_metadata_sha256": _sha256_file(registry_metadata_path),
    }


def _resolve_review_smoke_report(review: Mapping[str, Any]) -> Path:
    value = review.get("smoke_report_path")
    if not isinstance(value, str) or not value:
        raise ValueError("Large smoke review has no smoke_report_path")
    path = Path(value)
    if not path.is_file():
        raise ValueError("Large smoke review references a missing smoke report")
    return path


def _validate_reviewer_source_fingerprints(
    review: Mapping[str, Any],
) -> dict[str, str]:
    reviewer = review.get("reviewer_source")
    if not isinstance(reviewer, Mapping):
        raise ValueError("Large smoke review has no reviewer source pins")
    hashes: dict[str, str] = {}
    for role, relative in (
        ("cli", REVIEWER_CLI_RELATIVE_PATH),
        ("core", REVIEWER_CORE_RELATIVE_PATH),
    ):
        reported_path = reviewer.get(f"{role}_path")
        reported_sha256 = reviewer.get(f"{role}_sha256")
        if reported_path != relative.as_posix() or not _is_sha256(reported_sha256):
            raise ValueError("Large smoke reviewer source pin is invalid")
        path = _safe_relative_file(
            REPOSITORY_ROOT,
            reported_path,
            name=f"Large smoke reviewer {role}",
        )
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != reported_sha256:
            raise ValueError("Large smoke reviewer source hash differs from the pin")
        hashes[role] = actual_sha256
    return hashes


def _validate_smoke_split_coverage(
    *,
    source: Mapping[str, Any],
    smoke: Mapping[str, Any],
) -> None:
    reported = source.get("observed_output_splits")
    if not isinstance(reported, Mapping) or set(reported) != set(
        ("utterance", "phone", "word")
    ):
        raise ValueError("Large smoke review split coverage is missing")
    observed: dict[str, list[str]] = {}
    for kind in ("utterance", "phone", "word"):
        values = reported.get(kind)
        if not isinstance(values, list) or not values:
            raise ValueError("Large smoke review observed split coverage is empty")
        if (
            any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
            or any(value not in ALLOWED_SPLITS for value in values)
            or any(value in EVALUATION_SPLITS for value in values)
        ):
            raise ValueError("Large smoke review observed split coverage is invalid")
        canonical = [split for split in ALLOWED_SPLITS if split in values]
        if values != canonical:
            raise ValueError("Large smoke review observed split order is invalid")
        observed[kind] = values

    coverage_complete = all(
        set(values) == set(ALLOWED_SPLITS) for values in observed.values()
    )
    if source.get("allowed_split_coverage_complete") is not coverage_complete:
        raise ValueError("Large smoke review split coverage flag is forged")
    if (
        source.get("full_cache_validation_requires_all_allowed_splits")
        is not FULL_CACHE_VALIDATION_REQUIRES_ALL_ALLOWED_SPLITS
    ):
        raise ValueError("Large smoke review full-cache split requirement is missing")

    progress = smoke.get("progress")
    included = (
        progress.get("included_rows_by_split")
        if isinstance(progress, Mapping)
        else None
    )
    if (
        not isinstance(included, Mapping)
        or not included
        or any(
            not isinstance(split, str)
            or not split
            or split not in ALLOWED_SPLITS
            or not _is_positive_int(count)
            for split, count in included.items()
        )
        or sum(int(count) for count in included.values()) != 100
        or set(included) != set(observed["utterance"])
    ):
        raise ValueError("Large smoke report included split counts are invalid")


def _validate_large_smoke_review(
    path: Path,
    *,
    expected_config_sha256: str,
    expected_builder_sha256: str,
) -> dict[str, str]:
    review = _load_gate_document(path, kind="Large smoke review")
    if review.get("schema_version") != SMOKE_REVIEW_SCHEMA_VERSION:
        raise ValueError("Large smoke review schema differs from the pin")
    if (
        review.get("status") != "approved"
        or review.get("full_run_approved") is not True
        or review.get("review_type") != "automated_engineering_gate"
        or review.get("human_expert_review") is not False
    ):
        raise ValueError("Large smoke review has not approved an engineering full run")
    if review.get("experiment_config_sha256") != expected_config_sha256:
        raise ValueError("Large smoke review does not pin the current config")
    if review.get("builder_sha256") != expected_builder_sha256:
        raise ValueError("Large smoke review does not pin the current builder")
    if review.get("builder_dependency_sha256") != BASE_ENGINE_SHA256:
        raise ValueError("Large smoke review does not pin the cache-engine dependency")
    reviewer_hashes = _validate_reviewer_source_fingerprints(review)

    source = review.get("source_contract")
    if (
        not isinstance(source, Mapping)
        or source.get("model_id") != MODEL_ID
        or source.get("model_revision") != MODEL_REVISION
        or source.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
        or source.get("do_normalize") is not DO_NORMALIZE
        or set(source.get("allowed_splits", [])) != set(ALLOWED_SPLITS)
        or source.get("metric_evaluation_performed") is not False
        or source.get(
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering"
        )
        is not True
        or source.get("evaluation_audio_decoded") is not False
        or source.get("evaluation_rows_sent_to_encoder") is not False
        or source.get("evaluation_labels_used_for_training_or_metrics") is not False
    ):
        raise ValueError("Large smoke review source contract is invalid")
    technical = review.get("technical_checks")
    expected_dimensions = {
        "utterance_mean_std": HIDDEN_SIZE * 2,
        "phone_mean": HIDDEN_SIZE,
        "word_mean": HIDDEN_SIZE,
    }
    if (
        not isinstance(technical, Mapping)
        or technical.get("report_status_completed") is not True
        or technical.get("smoke_utterance_limit") != 100
        or technical.get("embedding_dimensions") != expected_dimensions
        or technical.get("all_reported_shard_sizes_and_sha256_match") is not True
        or technical.get("storage_dtype_float16") is not True
        or technical.get("pinned_preprocessor_config_validated") is not True
        or technical.get("all_embeddings_finite_and_nonzero") is not True
        or technical.get("prohibited_output_column_hits") != []
        or technical.get("identifier_columns_are_lowercase_sha256") is not True
        or technical.get("frame_geometry_check_passed") is not True
        or technical.get("oom_count") != 0
    ):
        raise ValueError("Large smoke technical review is incomplete")
    resource = review.get("resource_review")
    if (
        not isinstance(resource, Mapping)
        or resource.get("approved") is not True
        or not _is_positive_int(resource.get("peak_rss_bytes"))
        or not _is_positive_int(resource.get("estimated_full_cache_upper_bytes"))
        or not _is_positive_int(resource.get("max_row_group_rows"))
        or not 1 <= resource["max_row_group_rows"] <= 4_096
    ):
        raise ValueError("Large smoke resource review is incomplete")

    smoke_report = _resolve_review_smoke_report(review)
    smoke_sha256 = _sha256_file(smoke_report)
    if review.get("smoke_report_sha256") != smoke_sha256:
        raise ValueError("Large smoke report SHA-256 differs from the review")
    smoke = _load_gate_document(smoke_report, kind="Large smoke report")
    smoke_source = smoke.get("source")
    smoke_model = smoke.get("model")
    smoke_runtime = smoke.get("runtime")
    smoke_policy = smoke.get("policy")
    smoke_configuration = smoke.get("configuration")
    if smoke.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("Large smoke report schema differs from the pin")
    if smoke.get("status") != "completed":
        raise ValueError("Large smoke report is not completed")
    if (
        not isinstance(smoke_source, Mapping)
        or smoke_source.get("experiment_config_sha256") != expected_config_sha256
        or smoke_source.get("builder_sha256") != expected_builder_sha256
        or smoke_source.get("builder_dependency_sha256") != BASE_ENGINE_SHA256
        or smoke_source.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
    ):
        raise ValueError("Large smoke report source hashes are invalid")
    if (
        not isinstance(smoke_model, Mapping)
        or smoke_model.get("id") != MODEL_ID
        or smoke_model.get("revision") != MODEL_REVISION
        or smoke_model.get("config_sha256") != MODEL_CONFIG_SHA256
        or smoke_model.get("checkpoint_sha256") != MODEL_CHECKPOINT_SHA256
        or smoke_model.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
        or smoke_model.get("hidden_size") != HIDDEN_SIZE
        or smoke_model.get("frozen") is not True
        or smoke_model.get("local_files_only") is not True
        or smoke_model.get("do_normalize") is not DO_NORMALIZE
    ):
        raise ValueError("Large smoke report model pin is invalid")
    smoke_preprocessor = smoke_model.get("preprocessor_contract")
    if (
        not isinstance(smoke_preprocessor, Mapping)
        or smoke_preprocessor.get("sampling_rate_hz") != SAMPLE_RATE_HZ
        or smoke_preprocessor.get("do_normalize") is not DO_NORMALIZE
        or smoke_preprocessor.get("return_attention_mask") is not True
        or smoke_preprocessor.get("source") != "official_pinned_revision"
        or smoke_preprocessor.get("source_url") != PINNED_PREPROCESSOR_URL
        or smoke_preprocessor.get("config_sha256") != MODEL_PREPROCESSOR_SHA256
        or smoke_preprocessor.get("local_preprocessor_config_present") is not True
        or smoke_preprocessor.get("local_preprocessor_config_validated") is not True
        or smoke_preprocessor.get("auto_feature_extractor_used") is not False
    ):
        raise ValueError("Large smoke preprocessor contract is invalid")
    if (
        not isinstance(smoke_runtime, Mapping)
        or smoke_runtime.get("cuda_autocast_precision") != "bfloat16"
        or smoke_runtime.get("storage_precision") != "float16"
        or not isinstance(smoke_runtime.get("observed_model_output_dtypes"), Mapping)
        or not smoke_runtime["observed_model_output_dtypes"]
        or smoke_runtime.get("oom_count") != 0
    ):
        raise ValueError("Large smoke runtime precision audit is incomplete")
    if (
        not isinstance(smoke_policy, Mapping)
        or set(smoke_policy.get("allowed_splits", [])) != set(ALLOWED_SPLITS)
        or smoke_policy.get("metric_evaluation_performed") is not False
        or smoke_policy.get("oracle_alignment") is not True
        or smoke_policy.get("source_manifest_columns_deserialized_before_split_filter")
        != list(SOURCE_COLUMNS)
        or smoke_policy.get(
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering"
        )
        is not True
        or smoke_policy.get("evaluation_audio_decoded") is not False
        or smoke_policy.get("evaluation_rows_sent_to_encoder") is not False
        or smoke_policy.get("evaluation_labels_used_for_training_or_metrics")
        is not False
    ):
        raise ValueError("Large smoke policy is invalid")
    if (
        not isinstance(smoke_configuration, Mapping)
        or smoke_configuration.get("limit") != 100
        or smoke_configuration.get("full_run") is not False
    ):
        raise ValueError("Large smoke report does not describe the required smoke")
    _validate_smoke_split_coverage(source=source, smoke=smoke)
    return {
        "smoke_review_sha256": _sha256_file(path),
        "approved_smoke_report_sha256": smoke_sha256,
        "approved_config_sha256": expected_config_sha256,
        "approved_builder_sha256": expected_builder_sha256,
        "approved_builder_dependency_sha256": BASE_ENGINE_SHA256,
        "reviewer_cli_sha256": reviewer_hashes["cli"],
        "reviewer_core_sha256": reviewer_hashes["core"],
    }


def validate_full_run_gate(
    *,
    explicit_dev_report: Path | None,
    smoke_review: Path | None,
    expected_config_sha256: str,
    expected_builder_sha256: str,
) -> dict[str, str]:
    """Require completed Base+ heads plus a hash/resource-pinned Large smoke."""

    if explicit_dev_report is None or smoke_review is None:
        raise ValueError("full run requires --base-plus-head-report and --smoke-review")
    head_gate = _validate_base_plus_head_report(explicit_dev_report)
    gate = {
        "base_plus_head_report_sha256": head_gate["report_sha256"],
        "base_plus_head_registry_metadata_sha256": head_gate[
            "registry_metadata_sha256"
        ],
    }
    gate.update(
        _validate_large_smoke_review(
            smoke_review,
            expected_config_sha256=expected_config_sha256,
            expected_builder_sha256=expected_builder_sha256,
        )
    )
    return gate


def _large_base_report(**kwargs: Any) -> dict[str, Any]:
    report = _ORIGINAL_BASE_REPORT(**kwargs)
    report["source"]["builder_dependency_path"] = "scripts/build_wavlm_feature_cache.py"
    report["source"]["builder_dependency_sha256"] = BASE_ENGINE_SHA256
    report["source"]["preprocessor_config_sha256"] = MODEL_PREPROCESSOR_SHA256
    runtime = report["runtime"]
    runtime["cuda_autocast_enabled"] = True
    runtime["cuda_autocast_precision"] = "bfloat16"
    runtime["model_output_dtype_policy"] = "record_observed_dtype_at_runtime"
    runtime.pop("inference_precision", None)
    report["configuration"]["embedding_dimensions"] = {
        "utterance_mean_std": HIDDEN_SIZE * 2,
        "phone_mean": HIDDEN_SIZE,
        "word_mean": HIDDEN_SIZE,
    }
    report["configuration"]["frame_geometry_source"] = "pinned_local_wavlm_large_config"
    report["policy"].update(
        {
            "source_manifest_columns_deserialized_before_split_filter": list(
                SOURCE_COLUMNS
            ),
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering": (True),
            "evaluation_audio_decoded": False,
            "evaluation_rows_sent_to_encoder": False,
            "evaluation_labels_used_for_training_or_metrics": False,
        }
    )
    return report


def _sync_engine_hooks() -> None:
    _ENGINE.load_and_validate_config = load_and_validate_config
    _ENGINE.validate_full_run_gate = validate_full_run_gate
    _ENGINE.resolve_local_model_files = resolve_local_model_files
    _ENGINE.load_frozen_model = load_frozen_model
    _ENGINE.extract_rows = extract_rows
    _ENGINE._gpu_metadata = _gpu_metadata
    _ENGINE._base_report = _large_base_report


def build_cache(args: argparse.Namespace) -> dict[str, Any]:
    validate_cli_args(args)
    _sync_engine_hooks()
    return _ENGINE.build_cache(args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a local-only frozen WavLM Large train/dev feature cache."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_large_frozen_v1.yaml"),
    )
    parser.add_argument("--modeling-manifest", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--prompt-reservation", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-output", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--limit",
        type=int,
        help="Post-filter smoke limit; use exactly 100 for the reviewed smoke",
    )
    mode.add_argument(
        "--allow-full",
        action="store_true",
        help="Run only after Base+ heads and Large smoke/resource approval",
    )
    parser.add_argument(
        "--base-plus-head-report",
        dest="explicit_dev_report",
        type=Path,
        metavar="BASE_PLUS_HEAD_REPORT",
    )
    parser.add_argument("--smoke-review", type=Path)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--progress-every", type=int, default=10)
    return parser.parse_args()


def validate_cli_args(args: argparse.Namespace) -> None:
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    if args.progress_every < 0:
        raise ValueError("progress-every must be non-negative")
    limit = getattr(args, "limit", None)
    allow_full = getattr(args, "allow_full", False)
    if not isinstance(allow_full, bool):
        raise ValueError("allow-full must be boolean")
    if (limit is None) == (not allow_full):
        raise ValueError("select exactly one of a 100-item smoke or --allow-full")
    if allow_full:
        if (
            getattr(args, "explicit_dev_report", None) is None
            or getattr(args, "smoke_review", None) is None
        ):
            raise ValueError(
                "full run requires --base-plus-head-report and --smoke-review"
            )
        return
    if limit != 100:
        raise ValueError("the S102 Large smoke must use --limit 100")
    if args.output is None or args.report_output is None:
        raise ValueError(
            "Large smoke requires explicit --output and --report-output "
            "to protect canonical full-cache paths"
        )
    config = load_and_validate_config(args.config)
    output = _expect_mapping(config["output"], "output")
    canonical_output = Path(str(output["root"])).resolve()
    canonical_report = Path(str(output["report"])).resolve()
    smoke_output = Path(args.output).resolve()
    smoke_report = Path(args.report_output).resolve()
    if smoke_output == canonical_output or smoke_report == canonical_report:
        raise ValueError("Large smoke paths must differ from canonical full-run paths")
    if smoke_report == smoke_output or smoke_output in smoke_report.parents:
        raise ValueError("Large smoke report must be outside the smoke cache directory")


def main() -> None:
    args = parse_args()
    validate_cli_args(args)
    report = build_cache(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "utterance_rows": report["progress"]["utterance_rows_written"],
                "phone_rows": report["progress"]["phone_rows_written"],
                "word_rows": report["progress"]["word_rows_written"],
                "oom_count": report["runtime"]["oom_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
