from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility.wavlm_cache import (
    CacheValidationError,
    sha256_file,
    validate_wavlm_feature_cache,
)


VALIDATION_SCHEMA_VERSION = "voice_coach.wavlm_large_feature_cache_validation.v1"
CACHE_REPORT_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_report.v1"
GENERIC_VALIDATION_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_validation.v1"

LARGE_CONFIG_RELATIVE_PATH = Path("configs/experiments/wavlm_large_frozen_v1.yaml")
LARGE_BUILDER_RELATIVE_PATH = Path("scripts/build_wavlm_large_feature_cache.py")
GENERIC_BUILDER_RELATIVE_PATH = Path("scripts/build_wavlm_feature_cache.py")
REVIEWER_CLI_RELATIVE_PATH = Path("scripts/review_wavlm_feature_cache_smoke.py")
REVIEWER_CORE_RELATIVE_PATH = Path(
    "src/voice_coach/reproducibility/wavlm_smoke_review.py"
)
VALIDATOR_CLI_RELATIVE_PATH = Path("scripts/validate_wavlm_large_feature_cache.py")
VALIDATOR_CORE_RELATIVE_PATH = Path(
    "src/voice_coach/reproducibility/wavlm_large_cache_validation.py"
)
GENERIC_VALIDATOR_CLI_RELATIVE_PATH = Path("scripts/validate_wavlm_feature_cache.py")
GENERIC_VALIDATOR_CORE_RELATIVE_PATH = Path(
    "src/voice_coach/reproducibility/wavlm_cache.py"
)
MODEL_CACHE_RELATIVE_PATH = Path("data/cache/huggingface/hub")
MODEL_REPOSITORY_NAME = "models--microsoft--wavlm-large"

CANONICAL_CACHE_RELATIVE_PATH = Path(
    "data/processed/aihub71490/features/wavlm_large_frozen_v1"
)
CANONICAL_REPORT_RELATIVE_PATH = Path(
    "data/processed/aihub71490/features/wavlm_large_frozen_v1_report.json"
)
BASE_PLUS_HEAD_REPORT_RELATIVE_PATH = Path(
    "artifacts/experiments/aihub71490_wavlm_base_plus_frozen_head_v1/report.json"
)
SMOKE_REVIEW_RELATIVE_PATH = Path(
    "artifacts/reviews/wavlm_large_frozen_v1_smoke100_v2_automated_review.json"
)
SMOKE_REPORT_RELATIVE_PATH = Path(
    "data/processed/aihub71490/features/wavlm_large_frozen_v1_smoke100_v2_report.json"
)
RECOMMENDED_OUTPUT_RELATIVE_PATH = Path(
    "artifacts/reviews/wavlm_large_frozen_v1_full_validation.json"
)

LARGE_CONFIG_SHA256 = "0191afdba13e38619ab6f2c24494f0766cf9f16a802575a132eda7dadcd9c4fd"
LARGE_BUILDER_SHA256 = (
    "bfca5a5d065a7a55b3fce5671750dd4011f8a2b0e538d3c3a6aa8eab4972772f"
)
GENERIC_BUILDER_SHA256 = (
    "2acd48d4f7dbb735dab96cdf4b9222a654627a96cf6a068539f0480839a56dde"
)
GENERIC_VALIDATOR_CLI_SHA256 = (
    "525e30cb43df42a870466001b8198d6265433d4933b27ba1ff65393f76e28501"
)
GENERIC_VALIDATOR_CORE_SHA256 = (
    "72efc68a7a07018b619761f9a530e1f127de29062d0545829ea4a21d3773b8a2"
)
MODEL_ID = "microsoft/wavlm-large"
MODEL_REVISION = "c1423ed94bb01d80a3f5ce5bc39f6026a0f4828c"
MODEL_CONFIG_SHA256 = "a3d8fe831aaf63d725b54a8ac36f3549cd4365c5086774b2c89cabbc6f9e129d"
MODEL_CHECKPOINT_SHA256 = (
    "fdee460e529396ddb2f8c8e8ce0ad74cfb747b726bc6f612e666c7c1e1963c9d"
)
MODEL_PREPROCESSOR_SHA256 = (
    "60ca5a31e13f69ee2fbf147504c8676db5f6398fd7a6b12294341dff838edfcf"
)
PINNED_PREPROCESSOR_URL = (
    "https://huggingface.co/microsoft/wavlm-large/blob/"
    f"{MODEL_REVISION}/preprocessor_config.json"
)
MODELING_MANIFEST_SHA256 = (
    "32f439ba10a035541f53b8a44f5d150b307e89f97387f09748704c3c78e85ef1"
)
PROMPT_RESERVATION_SHA256 = (
    "3ea7f12ea76be07a0796d7e62f0c3727ca9858c0278df7c85ac1e08d9b653fa9"
)
REVIEWER_CLI_SHA256 = "6e42189f5a927c9ba91062e04ca5337c5a8adff94b28cd1e56f826c85ce261f3"
REVIEWER_CORE_SHA256 = (
    "b7dc9a105d9817daa8d109910106c4bea74b0742c09b1ef5f8292ca6f9b7c85c"
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
SMOKE_REVIEW_SCHEMA_VERSION = "voice_coach.wavlm_feature_cache_smoke_review.v1"
SMOKE_OBSERVED_ROWS = {
    "utterance": 100,
    "phone": 1_798,
    "word": 321,
}
SMOKE_PROJECTED_BYTES_BY_KIND = {
    "utterance": 723_509_017,
    "phone": 4_398_939_038,
    "word": 862_620_763,
}
SMOKE_PROJECTED_BYTES_TOTAL = 5_985_068_818
SMOKE_ESTIMATED_UPPER_BYTES = 7_481_336_023

SOURCE_ROWS = 322_380
EXPECTED_ROWS = {
    "utterance": 169_837,
    "phone": 2_233_039,
    "word": 408_790,
}
HIDDEN_SIZE = 1_024
EXPECTED_DIMENSIONS = {
    "utterance": HIDDEN_SIZE * 2,
    "phone": HIDDEN_SIZE,
    "word": HIDDEN_SIZE,
}
EXPECTED_SHARDS = {
    "utterance": 17,
    "phone": 9,
    "word": 5,
}
FIXED_FLOAT16_BYTES = {
    kind: EXPECTED_ROWS[kind] * EXPECTED_DIMENSIONS[kind] * 2 for kind in EXPECTED_ROWS
}
FIXED_FLOAT16_BYTES_TOTAL = 6_106_118_144
ALLOWED_SPLITS = ("train", "dev")
EVALUATION_SPLITS = ("stack", "calibration", "locked_test")
SOURCE_ROWS_BY_SPLIT = {
    "train": 150_677,
    "dev": 35_074,
    "stack": 35_211,
    "calibration": 35_106,
    "locked_test": 66_312,
}
INCLUDED_UTTERANCES_BY_SPLIT = {
    "train": 137_759,
    "dev": 32_078,
}
EXPECTED_EXCLUSIONS = {
    "split_not_allowed": 136_629,
    "qc_flags_present": 642,
    "reserved_prompt": 15_272,
}
RESOURCE_SAFETY_FACTOR = 1.25
SOURCE_COLUMNS = (
    "id",
    "speaker_id",
    "split",
    "audio_path",
    "prompt_sha256",
    "qc_flags",
    "word_intervals",
    "target_phone_intervals",
    "phone_alignments",
    "l1",
    "gender",
    "proficiency",
)
MODEL_ARCHITECTURE = {
    "hidden_size": HIDDEN_SIZE,
    "num_hidden_layers": 24,
    "num_attention_heads": 16,
    "intermediate_size": 4_096,
    "conv_dim": [512, 512, 512, 512, 512, 512, 512],
    "conv_kernel": [10, 3, 3, 3, 3, 2, 2],
    "conv_stride": [5, 2, 2, 2, 2, 2, 2],
    "feat_extract_norm": "layer",
    "feat_extract_activation": "gelu",
    "hidden_act": "gelu",
}
REPORT_MODEL_ARCHITECTURE = {
    key: MODEL_ARCHITECTURE[key]
    for key in (
        "hidden_size",
        "num_hidden_layers",
        "num_attention_heads",
        "intermediate_size",
        "conv_dim",
        "conv_kernel",
        "conv_stride",
    )
}
CONFIG_MODEL_ARCHITECTURE = {
    **{key: value for key, value in MODEL_ARCHITECTURE.items() if key != "hidden_act"},
    "hidden_activation": "gelu",
}
_FORBIDDEN_PATH_PARTS = frozenset(
    {
        "raw",
        "restricted",
        "stack",
        "calibration",
        "locked_test",
        "locked-test",
    }
)
_FORBIDDEN_PATH_PREFIXES = (
    ".env",
    "stack_",
    "stack-",
    "calibration_",
    "calibration-",
    "locked_test",
    "locked-test",
)


class LargeCacheValidationError(CacheValidationError):
    """Raised for unsafe or structurally unreadable Large validation inputs."""


@dataclass(frozen=True, slots=True)
class _PinnedHfFile:
    snapshot_path: Path
    snapshot_relative: str
    resolved_path: Path
    resolved_relative: str
    sha256: str
    leaf_symlink: bool


@dataclass
class _Issues:
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, code: str, count: int = 1) -> None:
        self.counts[code] = self.counts.get(code, 0) + count

    def check(self, condition: bool, code: str) -> bool:
        if not condition:
            self.add(code)
        return condition

    def extend(self, values: object) -> None:
        if not isinstance(values, list):
            self.add("generic_validation_errors_invalid")
            return
        for value in values:
            if not isinstance(value, Mapping):
                self.add("generic_validation_errors_invalid")
                continue
            code = value.get("code")
            count = value.get("count")
            if not isinstance(code, str) or not code or not _is_int(count, minimum=1):
                self.add("generic_validation_errors_invalid")
                continue
            assert isinstance(count, int)
            self.add(code, count)

    def report(self) -> list[dict[str, Any]]:
        return [
            {"code": code, "count": count}
            for code, count in sorted(self.counts.items())
        ]


def _is_int(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_positive_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _contains_forbidden_path_part(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    return any(
        part in _FORBIDDEN_PATH_PARTS
        or any(part.startswith(prefix) for prefix in _FORBIDDEN_PATH_PREFIXES)
        for part in lowered
    )


def _require_finite_tree(value: object, *, role: str) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LargeCacheValidationError(f"{role} contains NaN or infinity")
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _require_finite_tree(item, role=role)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LargeCacheValidationError(f"{role} has a non-string key")
            _require_finite_tree(item, role=role)
        return
    raise LargeCacheValidationError(f"{role} contains an unsupported value")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _safe_input(
    root: Path,
    configured: str | Path,
    *,
    role: str,
    expected_relative: Path | None = None,
    directory: bool = False,
) -> tuple[Path, str]:
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise LargeCacheValidationError(f"{role} must not be a symlink")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise LargeCacheValidationError(f"{role} cannot be resolved safely") from exc
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise LargeCacheValidationError(f"{role} must stay inside workspace") from exc
    if _contains_forbidden_path_part(relative):
        raise LargeCacheValidationError(f"{role} is forbidden by privacy policy")
    if expected_relative is not None and relative != expected_relative:
        raise LargeCacheValidationError(f"{role} differs from the canonical path")
    if directory:
        if not resolved.is_dir():
            raise LargeCacheValidationError(f"{role} must be a directory")
    elif not resolved.is_file():
        raise LargeCacheValidationError(f"{role} must be a file")
    return resolved, relative.as_posix()


def safe_large_validation_output_path(
    workspace_root: str | Path,
    output_path: str | Path,
) -> Path:
    root = Path(workspace_root).resolve()
    candidate = Path(output_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise LargeCacheValidationError("validation output must not be a symlink")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise LargeCacheValidationError(
            "validation output cannot be resolved safely"
        ) from exc
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise LargeCacheValidationError(
            "validation output must stay inside workspace"
        ) from exc
    if _contains_forbidden_path_part(relative):
        raise LargeCacheValidationError(
            "validation output is forbidden by privacy policy"
        )
    if relative != RECOMMENDED_OUTPUT_RELATIVE_PATH:
        raise LargeCacheValidationError(
            "validation output must use the pinned aggregate review path"
        )
    if resolved.exists() and resolved.is_dir():
        raise LargeCacheValidationError("validation output must be a JSON file")
    current = root
    for part in relative.parent.parts:
        current = current / part
        if current.is_symlink():
            raise LargeCacheValidationError(
                "validation output parent must not be a symlink"
            )
        if current.exists() and not current.is_dir():
            raise LargeCacheValidationError(
                "validation output parent must be a directory"
            )
    return resolved


def write_large_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically publish aggregate JSON in a trusted, quiescent workspace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise LargeCacheValidationError("validation output must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".part",
    )
    temporary = Path(temporary_name)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LargeCacheValidationError(
                "validation temporary output is not a regular file"
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.is_symlink() or not temporary.is_file():
            raise LargeCacheValidationError("validation temporary output became unsafe")
        if path.is_symlink():
            raise LargeCacheValidationError(
                "validation output became a symlink before publish"
            )
        os.replace(temporary, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_json(path: Path, *, role: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, ValueError) as exc:
        raise LargeCacheValidationError(f"{role} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise LargeCacheValidationError(f"{role} must be a JSON object")
    _require_finite_tree(value, role=role)
    return value


def _load_yaml(path: Path, *, role: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LargeCacheValidationError(f"{role} is not readable YAML") from exc
    if not isinstance(value, dict):
        raise LargeCacheValidationError(f"{role} must be a YAML object")
    _require_finite_tree(value, role=role)
    return value


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _require_plain_directory_chain(
    root: Path,
    relative: Path,
    *,
    role: str,
) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LargeCacheValidationError(f"{role} contains an intermediate symlink")
        if not current.is_dir():
            raise LargeCacheValidationError(f"{role} directory is missing")
    return current


def _resolve_pinned_hf_snapshot_file(
    *,
    root: Path,
    config: Mapping[str, Any],
    filename: str,
    expected_sha256: str,
    role: str,
) -> _PinnedHfFile:
    model = _mapping(config.get("model"))
    if (
        model.get("id") != MODEL_ID
        or model.get("revision") != MODEL_REVISION
        or model.get("cache_dir") != MODEL_CACHE_RELATIVE_PATH.as_posix()
    ):
        raise LargeCacheValidationError(
            f"{role} identity/cache root differs from the pinned snapshot"
        )
    snapshot_relative = (
        MODEL_CACHE_RELATIVE_PATH
        / MODEL_REPOSITORY_NAME
        / "snapshots"
        / MODEL_REVISION
        / filename
    )
    snapshot_parent_relative = snapshot_relative.parent
    snapshot_parent = _require_plain_directory_chain(
        root,
        snapshot_parent_relative,
        role=f"{role} snapshot",
    )
    snapshot_path = snapshot_parent / filename
    leaf_symlink = snapshot_path.is_symlink()
    if not leaf_symlink:
        if not snapshot_path.is_file():
            raise LargeCacheValidationError(f"{role} snapshot file is missing")
        resolved_path = snapshot_path.resolve(strict=True)
    else:
        blobs_relative = MODEL_CACHE_RELATIVE_PATH / MODEL_REPOSITORY_NAME / "blobs"
        blobs_root = _require_plain_directory_chain(
            root,
            blobs_relative,
            role=f"{role} blobs",
        )
        try:
            link_value = Path(os.readlink(snapshot_path))
            if link_value.is_absolute():
                raise ValueError("absolute snapshot symlink")
            lexical_target = Path(os.path.normpath(snapshot_path.parent / link_value))
            lexical_blob_relative = lexical_target.relative_to(blobs_root)
            if not lexical_blob_relative.parts:
                raise ValueError("blob target is a directory")
            _require_plain_directory_chain(
                blobs_root,
                lexical_blob_relative.parent,
                role=f"{role} blob target",
            )
            if lexical_target.is_symlink():
                raise ValueError("blob target is another symlink")
            resolved_path = snapshot_path.resolve(strict=True)
            resolved_path.relative_to(root)
            blob_relative = resolved_path.relative_to(blobs_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise LargeCacheValidationError(
                f"{role} snapshot symlink is dangling or escapes pinned blobs"
            ) from exc
        if not blob_relative.parts:
            raise LargeCacheValidationError(
                f"{role} snapshot symlink does not target a blob file"
            )
        if resolved_path.is_symlink() or not resolved_path.is_file():
            raise LargeCacheValidationError(
                f"{role} snapshot symlink target is not a regular file"
            )
    try:
        resolved_relative = resolved_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise LargeCacheValidationError(f"{role} resolves outside workspace") from exc
    actual_sha256 = sha256_file(resolved_path)
    if actual_sha256 != expected_sha256:
        raise LargeCacheValidationError(f"{role} differs from the pinned SHA-256")
    return _PinnedHfFile(
        snapshot_path=snapshot_path,
        snapshot_relative=snapshot_relative.as_posix(),
        resolved_path=resolved_path,
        resolved_relative=resolved_relative,
        sha256=actual_sha256,
        leaf_symlink=leaf_symlink,
    )


def _validate_large_static_contract(
    *,
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    config_sha256: str,
    builder_sha256: str,
    issues: _Issues,
) -> None:
    experiment = _mapping(config.get("experiment"))
    model = _mapping(config.get("model"))
    data = _mapping(config.get("data"))
    features = _mapping(config.get("features"))
    output = _mapping(config.get("output"))
    gate = _mapping(config.get("full_run_gate"))
    report_source = _mapping(report.get("source"))
    report_model = _mapping(report.get("model"))
    report_configuration = _mapping(report.get("configuration"))

    issues.check(
        config_sha256 == LARGE_CONFIG_SHA256
        and experiment.get("id") == "aihub71490_wavlm_large_frozen_v1"
        and experiment.get("stage") == "feature_cache"
        and experiment.get("development_only") is True,
        "large_config_contract_invalid",
    )
    expected_model = {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "cache_dir": MODEL_CACHE_RELATIVE_PATH.as_posix(),
        "local_files_only": True,
        "frozen": True,
        "config_sha256": MODEL_CONFIG_SHA256,
        "checkpoint_sha256": MODEL_CHECKPOINT_SHA256,
        "preprocessor_config_sha256": MODEL_PREPROCESSOR_SHA256,
        "hidden_size": HIDDEN_SIZE,
        "do_normalize": True,
        "return_attention_mask": True,
        **CONFIG_MODEL_ARCHITECTURE,
    }
    issues.check(
        all(model.get(key) == expected for key, expected in expected_model.items())
        and report_model.get("id") == MODEL_ID
        and report_model.get("revision") == MODEL_REVISION
        and report_model.get("config_sha256") == MODEL_CONFIG_SHA256
        and report_model.get("checkpoint_sha256") == MODEL_CHECKPOINT_SHA256
        and report_model.get("preprocessor_config_sha256") == MODEL_PREPROCESSOR_SHA256
        and report_model.get("hidden_size") == HIDDEN_SIZE
        and all(
            report_model.get(key) == expected
            for key, expected in REPORT_MODEL_ARCHITECTURE.items()
        )
        and report_model.get("frame_stride_samples") == 320
        and report_model.get("receptive_field_samples") == 400
        and _is_int(report_model.get("parameter_count"), minimum=1)
        and report_model.get("frozen") is True
        and report_model.get("local_files_only") is True,
        "large_model_contract_invalid",
    )
    issues.check(
        builder_sha256 == LARGE_BUILDER_SHA256
        and report_source.get("experiment_config_sha256") == config_sha256
        and report_source.get("builder_sha256") == builder_sha256
        and report_source.get("modeling_manifest_sha256") == MODELING_MANIFEST_SHA256
        and report_source.get("prompt_reservation_sha256") == PROMPT_RESERVATION_SHA256,
        "large_report_source_contract_invalid",
    )
    issues.check(
        data.get("modeling_manifest_sha256") == MODELING_MANIFEST_SHA256
        and data.get("allowed_splits") == list(ALLOWED_SPLITS)
        and data.get("forbidden_metric_splits") == list(EVALUATION_SPLITS)
        and features.get("cuda_autocast_precision") == "bfloat16"
        and features.get("storage_precision") == "float16"
        and output.get("root") == CANONICAL_CACHE_RELATIVE_PATH.as_posix()
        and output.get("report") == CANONICAL_REPORT_RELATIVE_PATH.as_posix()
        and gate.get("resource_estimate_reference_rows") == EXPECTED_ROWS
        and gate.get("resource_estimate_safety_factor") == RESOURCE_SAFETY_FACTOR,
        "large_config_contract_invalid",
    )
    issues.check(
        report.get("schema_version") == CACHE_REPORT_SCHEMA_VERSION
        and report.get("status") == "completed"
        and report_configuration.get("full_run") is True
        and report_configuration.get("limit") is None
        and report_configuration.get("embedding_dimensions")
        == {
            "utterance_mean_std": EXPECTED_DIMENSIONS["utterance"],
            "phone_mean": EXPECTED_DIMENSIONS["phone"],
            "word_mean": EXPECTED_DIMENSIONS["word"],
        }
        and report_configuration.get("frame_geometry_source")
        == "pinned_local_wavlm_large_config",
        "large_model_contract_invalid",
    )


def _validate_local_model_config(
    *,
    root: Path,
    config: Mapping[str, Any],
    issues: _Issues,
) -> _PinnedHfFile | None:
    try:
        resolved = _resolve_pinned_hf_snapshot_file(
            root=root,
            config=config,
            filename="config.json",
            expected_sha256=MODEL_CONFIG_SHA256,
            role="Large model config",
        )
        document = _load_json(resolved.resolved_path, role="Large model config")
    except LargeCacheValidationError:
        issues.add("large_model_contract_invalid")
        return None
    issues.check(
        all(
            document.get(key) == expected
            for key, expected in MODEL_ARCHITECTURE.items()
        ),
        "large_model_contract_invalid",
    )
    return resolved


def _resolve_model_checkpoint(
    *,
    root: Path,
    config: Mapping[str, Any],
    issues: _Issues,
) -> _PinnedHfFile | None:
    try:
        return _resolve_pinned_hf_snapshot_file(
            root=root,
            config=config,
            filename="pytorch_model.bin",
            expected_sha256=MODEL_CHECKPOINT_SHA256,
            role="Large model checkpoint",
        )
    except LargeCacheValidationError:
        issues.add("large_model_contract_invalid")
        return None


def _validate_preprocessor(
    *,
    root: Path,
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    issues: _Issues,
) -> _PinnedHfFile | None:
    model = _mapping(config.get("model"))
    report_source = _mapping(report.get("source"))
    report_model = _mapping(report.get("model"))
    contract = _mapping(report_model.get("preprocessor_contract"))
    try:
        resolved = _resolve_pinned_hf_snapshot_file(
            root=root,
            config=config,
            filename="preprocessor_config.json",
            expected_sha256=MODEL_PREPROCESSOR_SHA256,
            role="Large preprocessor",
        )
        document = _load_json(
            resolved.resolved_path,
            role="Large preprocessor",
        )
    except LargeCacheValidationError:
        issues.add("large_preprocessor_contract_invalid")
        return None
    expected_document = {
        "do_normalize": True,
        "feature_extractor_type": "Wav2Vec2FeatureExtractor",
        "feature_size": 1,
        "padding_side": "right",
        "padding_value": 0.0,
        "return_attention_mask": True,
        "sampling_rate": 16_000,
    }
    expected_contract = {
        "sampling_rate_hz": 16_000,
        "do_normalize": True,
        "return_attention_mask": True,
        "source": "official_pinned_revision",
        "source_url": PINNED_PREPROCESSOR_URL,
        "config_sha256": MODEL_PREPROCESSOR_SHA256,
        "local_preprocessor_config_present": True,
        "local_preprocessor_config_validated": True,
        "auto_feature_extractor_used": False,
    }
    issues.check(
        model.get("preprocessor_config_sha256") == MODEL_PREPROCESSOR_SHA256
        and model.get("do_normalize") is True
        and model.get("return_attention_mask") is True
        and report_source.get("preprocessor_config_sha256") == MODEL_PREPROCESSOR_SHA256
        and report_model.get("preprocessor_config_sha256") == MODEL_PREPROCESSOR_SHA256
        and report_model.get("do_normalize") is True
        and document == expected_document
        and all(
            contract.get(key) == expected for key, expected in expected_contract.items()
        ),
        "large_preprocessor_contract_invalid",
    )
    return resolved


def _validate_builder_dependency(
    *,
    root: Path,
    report: Mapping[str, Any],
    issues: _Issues,
) -> tuple[Path | None, str | None]:
    source = _mapping(report.get("source"))
    try:
        path, _ = _safe_input(
            root,
            GENERIC_BUILDER_RELATIVE_PATH,
            role="generic cache builder",
            expected_relative=GENERIC_BUILDER_RELATIVE_PATH,
        )
        actual_sha256 = sha256_file(path)
    except LargeCacheValidationError:
        issues.add("large_builder_dependency_invalid")
        return None, None
    issues.check(
        actual_sha256 == GENERIC_BUILDER_SHA256
        and source.get("builder_dependency_path")
        == GENERIC_BUILDER_RELATIVE_PATH.as_posix()
        and source.get("builder_dependency_sha256") == actual_sha256,
        "large_builder_dependency_invalid",
    )
    return path, actual_sha256


def _safe_referenced_file(
    root: Path,
    base: Path,
    configured: object,
    *,
    role: str,
) -> Path:
    if not isinstance(configured, str) or not configured:
        raise LargeCacheValidationError(f"{role} path is invalid")
    relative = Path(configured)
    if relative.is_absolute() or ".." in relative.parts:
        raise LargeCacheValidationError(f"{role} path must be safely relative")
    base_resolved = base.resolve()
    current = base_resolved
    for part in relative.parent.parts:
        current = current / part
        if current.is_symlink():
            raise LargeCacheValidationError(
                f"{role} path contains an intermediate symlink"
            )
        if not current.is_dir():
            raise LargeCacheValidationError(f"{role} parent is missing")
    candidate = base_resolved / relative
    if candidate.is_symlink():
        raise LargeCacheValidationError(f"{role} must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base_resolved)
        workspace_relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LargeCacheValidationError(
            f"{role} is missing or escapes its root"
        ) from exc
    if _contains_forbidden_path_part(workspace_relative):
        raise LargeCacheValidationError(f"{role} is forbidden by privacy policy")
    if not resolved.is_file():
        raise LargeCacheValidationError(f"{role} must be a regular file")
    return resolved


def _verified_registry_role_hashes(
    root: Path,
    inputs: Mapping[str, Any],
    *,
    role: str,
) -> set[str]:
    entries = inputs.get(role)
    if not isinstance(entries, list):
        raise LargeCacheValidationError(f"Base+ registry {role} inputs are invalid")
    hashes: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping) or not _is_sha256(entry.get("sha256")):
            raise LargeCacheValidationError(
                f"Base+ registry {role} input hash is invalid"
            )
        path = _safe_referenced_file(
            root,
            root,
            entry.get("path"),
            role=f"Base+ registry {role} input",
        )
        if sha256_file(path) != entry["sha256"]:
            raise LargeCacheValidationError(f"Base+ registry {role} input hash differs")
        if (
            not _is_int(entry.get("size_bytes"), minimum=1)
            or path.stat().st_size != entry["size_bytes"]
        ):
            raise LargeCacheValidationError(f"Base+ registry {role} input size differs")
        hashes.add(str(entry["sha256"]))
    if len(hashes) != len(entries):
        raise LargeCacheValidationError(
            f"Base+ registry {role} inputs contain duplicate hashes"
        )
    return hashes


def _validate_base_plus_head_chain(
    *,
    root: Path,
    path: Path,
) -> tuple[dict[str, str], Path]:
    report = _load_json(path, role="Base+ head report")
    if (
        report.get("schema_version") != BASE_PLUS_HEAD_REPORT_SCHEMA
        or report.get("status") != "completed"
        or report.get("experiment_id") != BASE_PLUS_HEAD_EXPERIMENT_ID
        or report.get("development_only") is not True
    ):
        raise LargeCacheValidationError("Base+ head report identity is invalid")
    encoder = _mapping(report.get("encoder"))
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
    if any(encoder.get(key) != value for key, value in expected_encoder.items()):
        raise LargeCacheValidationError("Base+ head encoder contract is invalid")
    sources = _mapping(report.get("sources"))
    expected_sources = {
        "training_config_sha256": BASE_PLUS_HEAD_CONFIG_SHA256,
        "trainer_source_sha256": BASE_PLUS_HEAD_TRAINER_SHA256,
        "head_source_sha256": BASE_PLUS_HEAD_MODEL_SHA256,
        "explicit_utterance_labels_sha256": BASE_PLUS_EXPLICIT_LABEL_SHA256,
        "modeling_manifest_sha256": MODELING_MANIFEST_SHA256,
        "prompt_reservation_sha256": PROMPT_RESERVATION_SHA256,
        "cache_builder_config_sha256": BASE_PLUS_CACHE_CONFIG_SHA256,
        "cache_builder_sha256": GENERIC_BUILDER_SHA256,
        "encoder_config_sha256": BASE_PLUS_MODEL_CONFIG_SHA256,
        "encoder_checkpoint_sha256": BASE_PLUS_MODEL_CHECKPOINT_SHA256,
        "model_hashes_bound_by_validated_config_and_cache_report": True,
    }
    if any(sources.get(key) != value for key, value in expected_sources.items()):
        raise LargeCacheValidationError("Base+ head source chain is invalid")
    if not _is_sha256(sources.get("feature_cache_report_sha256")):
        raise LargeCacheValidationError("Base+ cache report hash is invalid")
    summary = _mapping(report.get("summary"))
    if (
        summary.get("seeds_completed") != REQUIRED_SEEDS
        or summary.get("results_are_development_only") is not True
        or summary.get("locked_test_remains_unread") is not True
        or summary.get("stack_remains_unread") is not True
        or summary.get("calibration_remains_unread") is not True
        or summary.get("dev_inference_passes_per_seed") != 1
    ):
        raise LargeCacheValidationError("Base+ completion summary is invalid")
    leakage = _mapping(report.get("leakage_controls"))
    accepted = leakage.get("accepted_splits")
    if (
        not isinstance(accepted, list)
        or set(accepted) != set(ALLOWED_SPLITS)
        or any(
            leakage.get(key) is not False
            for key in (
                "stack_accessed",
                "calibration_accessed",
                "locked_test_accessed",
                "dev_used_for_selection",
            )
        )
        or leakage.get("train_inner_validation_used") is not True
    ):
        raise LargeCacheValidationError("Base+ leakage controls are invalid")
    cache = _mapping(report.get("cache_contract"))
    if (
        any(
            cache.get(key) is not True
            for key in (
                "full_cache_validated",
                "source_scan_complete",
                "smoke_cache_rejected",
                "every_shard_hash_validated",
                "every_shard_schema_validated",
                "every_shard_split_policy_validated",
                "every_shard_privacy_metadata_validated",
            )
        )
        or cache.get("cache_report_status") != "completed"
        or cache.get("access_scope") != "restricted_internal"
        or cache.get("shareable") is not False
        or not _is_int(cache.get("speaker_overlap_train_dev"))
        or cache.get("speaker_overlap_train_dev") != 0
        or cache.get("rows") != EXPECTED_ROWS
    ):
        raise LargeCacheValidationError("Base+ full-cache contract is invalid")
    split_rows = _mapping(cache.get("split_rows"))
    fingerprints = _mapping(cache.get("shard_fingerprints"))
    shard_hashes: set[str] = set()
    for kind, expected_rows in EXPECTED_ROWS.items():
        counts = _mapping(split_rows.get(kind))
        values = fingerprints.get(kind)
        if (
            set(counts) != set(ALLOWED_SPLITS)
            or any(not _is_int(value, minimum=1) for value in counts.values())
            or sum(int(value) for value in counts.values()) != expected_rows
            or not isinstance(values, list)
            or len(values) != EXPECTED_SHARDS[kind]
        ):
            raise LargeCacheValidationError(f"Base+ {kind} cache rows are invalid")
        names: set[str] = set()
        fingerprint_rows = 0
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise LargeCacheValidationError(f"Base+ {kind} fingerprint is invalid")
            name = value.get("name")
            if (
                name != f"part-{index:05d}.parquet"
                or name in names
                or not _is_int(value.get("row_count"), minimum=1)
                or not _is_sha256(value.get("sha256"))
            ):
                raise LargeCacheValidationError(f"Base+ {kind} fingerprint is invalid")
            names.add(name)
            fingerprint_rows += int(value["row_count"])
            shard_hashes.add(str(value["sha256"]))
        if fingerprint_rows != expected_rows:
            raise LargeCacheValidationError(
                f"Base+ {kind} fingerprint rows are invalid"
            )
    if len(shard_hashes) != sum(EXPECTED_SHARDS.values()):
        raise LargeCacheValidationError("Base+ shard hashes are not globally unique")
    runs = report.get("runs")
    if (
        not isinstance(runs, list)
        or [run.get("seed") if isinstance(run, Mapping) else None for run in runs]
        != REQUIRED_SEEDS
    ):
        raise LargeCacheValidationError("Base+ run seeds are invalid")
    report_artifacts: dict[str, tuple[str, Path]] = {}
    for run in runs:
        assert isinstance(run, Mapping)
        seed = int(run["seed"])
        selection = _mapping(run.get("selection"))
        phone = _mapping(run.get("phone"))
        utterance = _mapping(run.get("utterance"))
        artifacts = _mapping(run.get("artifacts"))
        if (
            selection.get("dev_values_used_for_selection") is not False
            or not phone
            or not utterance
            or not artifacts
        ):
            raise LargeCacheValidationError(f"Base+ seed {seed} run is invalid")
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
                raise LargeCacheValidationError(f"Base+ {name} hash is invalid")
            artifact_path = _safe_referenced_file(
                root,
                path.parent,
                artifact.get("path"),
                role=f"Base+ {name}",
            )
            if sha256_file(artifact_path) != artifact["sha256"]:
                raise LargeCacheValidationError(f"Base+ {name} hash differs")
            report_artifacts[name] = (str(artifact["sha256"]), artifact_path)
    registry = _mapping(report.get("experiment_registry"))
    if (
        registry.get("integrated") is not True
        or registry.get("t03_complete") is not True
        or registry.get("implementation_source_hashes_match_registry_inputs")
        is not True
        or registry.get("metrics_recorded") is not True
        or registry.get("artifacts_recorded") is not True
        or registry.get("registered_artifact_count") != 13
        or not isinstance(registry.get("run_id"), str)
        or not registry.get("run_id")
    ):
        raise LargeCacheValidationError("Base+ registry link is invalid")
    registry_path = _safe_referenced_file(
        root,
        root,
        registry.get("metadata_path"),
        role="Base+ registry metadata",
    )
    registry_root = (root / "artifacts" / "experiment_registry").resolve()
    try:
        registry_path.relative_to(registry_root)
    except ValueError as exc:
        raise LargeCacheValidationError(
            "Base+ registry metadata is outside registry root"
        ) from exc
    metadata = _load_json(registry_path, role="Base+ registry metadata")
    if (
        metadata.get("status") != "completed"
        or metadata.get("failure") is not None
        or metadata.get("run_id") != registry.get("run_id")
        or metadata.get("seeds")
        != {f"experiment_seed_{seed}": seed for seed in REQUIRED_SEEDS}
    ):
        raise LargeCacheValidationError("Base+ registry completion is invalid")
    inputs = _mapping(metadata.get("inputs"))
    config_hashes = _verified_registry_role_hashes(root, inputs, role="config")
    data_hashes = _verified_registry_role_hashes(root, inputs, role="data")
    model_hashes = _verified_registry_role_hashes(root, inputs, role="model")
    checkpoint_hashes = _verified_registry_role_hashes(
        root,
        inputs,
        role="checkpoint",
    )
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
        raise LargeCacheValidationError("Base+ registry inputs are invalid")
    registry_artifacts = metadata.get("artifacts")
    if not isinstance(registry_artifacts, list):
        raise LargeCacheValidationError("Base+ registry artifacts are invalid")
    expected_registry_artifacts = {
        **report_artifacts,
        "final_report": (sha256_file(path), path.resolve()),
    }
    registry_names: set[str] = set()
    for item in registry_artifacts:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise LargeCacheValidationError("Base+ registry artifact name is invalid")
        registry_names.add(str(item["name"]))
    if registry_names != set(expected_registry_artifacts):
        raise LargeCacheValidationError("Base+ registry artifact set is invalid")
    for item in registry_artifacts:
        if (
            not isinstance(item, Mapping)
            or not _is_sha256(item.get("sha256"))
            or not _is_int(item.get("size_bytes"), minimum=1)
        ):
            raise LargeCacheValidationError("Base+ registry artifact is invalid")
        name = str(item["name"])
        expected_hash, expected_path = expected_registry_artifacts[name]
        artifact_path = _safe_referenced_file(
            root,
            root,
            item.get("path"),
            role=f"Base+ registry artifact {name}",
        )
        if (
            item["sha256"] != expected_hash
            or artifact_path != expected_path
            or sha256_file(artifact_path) != expected_hash
            or artifact_path.stat().st_size != item["size_bytes"]
        ):
            raise LargeCacheValidationError(f"Base+ registry artifact {name} differs")
    return (
        {
            "base_plus_head_report_sha256": sha256_file(path),
            "base_plus_head_registry_metadata_sha256": sha256_file(registry_path),
        },
        registry_path,
    )


def _ceil_ratio(numerator: int, denominator: int) -> int:
    if numerator <= 0 or denominator <= 0:
        raise LargeCacheValidationError("resource projection inputs must be positive")
    return (numerator + denominator - 1) // denominator


def _validate_smoke_resource_chain(
    *,
    smoke: Mapping[str, Any],
    review: Mapping[str, Any],
) -> dict[str, Any]:
    outputs = _mapping(smoke.get("outputs"))
    resource = _mapping(smoke.get("resource_estimate"))
    runtime = _mapping(smoke.get("runtime"))
    observed_rows: dict[str, int] = {}
    observed_bytes: dict[str, int] = {}
    for kind in EXPECTED_ROWS:
        output = _mapping(outputs.get(kind))
        rows = output.get("row_count")
        byte_count = output.get("byte_count")
        if not _is_int(rows, minimum=1) or not _is_int(byte_count, minimum=1):
            raise LargeCacheValidationError("Large smoke output size is invalid")
        assert isinstance(rows, int) and not isinstance(rows, bool)
        assert isinstance(byte_count, int) and not isinstance(byte_count, bool)
        observed_rows[kind] = rows
        observed_bytes[kind] = byte_count
    projected = {
        kind: _ceil_ratio(
            observed_bytes[kind] * EXPECTED_ROWS[kind],
            observed_rows[kind],
        )
        for kind in EXPECTED_ROWS
    }
    projected_total = sum(projected.values())
    estimated_upper = _ceil_ratio(projected_total * 5, 4)
    bytes_per_row = {
        kind: observed_bytes[kind] / observed_rows[kind] for kind in EXPECTED_ROWS
    }
    validation_summary = _mapping(review.get("validation_summary"))
    if (
        observed_rows != SMOKE_OBSERVED_ROWS
        or validation_summary.get("rows_checked") != observed_rows
        or validation_summary.get("embedding_vectors_checked") != observed_rows
        or validation_summary.get("source_scan_complete") is not False
    ):
        raise LargeCacheValidationError("Large smoke reviewed row counts are invalid")
    reported_bytes_per_row = _mapping(resource.get("observed_compressed_bytes_per_row"))
    if (
        resource.get("method")
        != "smoke compressed bytes per row scaled to audited full rows"
        or resource.get("reference_rows") != EXPECTED_ROWS
        or resource.get("projected_compressed_bytes_by_kind") != projected
        or resource.get("projected_compressed_bytes_total") != projected_total
        or resource.get("fixed_float16_embedding_bytes_by_kind") != FIXED_FLOAT16_BYTES
        or resource.get("fixed_float16_embedding_bytes_total")
        != FIXED_FLOAT16_BYTES_TOTAL
        or resource.get(
            "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead"
        )
        is not True
        or resource.get("safety_factor") != RESOURCE_SAFETY_FACTOR
        or resource.get("estimated_full_cache_upper_bytes") != estimated_upper
        or resource.get("estimate_is_capacity_planning_not_measured_full_output")
        is not True
        or any(
            not _is_positive_number(reported_bytes_per_row.get(kind))
            or not math.isclose(
                float(reported_bytes_per_row[kind]),
                bytes_per_row[kind],
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for kind in EXPECTED_ROWS
        )
        or projected != SMOKE_PROJECTED_BYTES_BY_KIND
        or projected_total != SMOKE_PROJECTED_BYTES_TOTAL
        or estimated_upper != SMOKE_ESTIMATED_UPPER_BYTES
    ):
        raise LargeCacheValidationError("Large smoke resource formula is invalid")
    reviewed = _mapping(review.get("resource_review"))
    peak_rss = runtime.get("peak_process_rss_bytes")
    if (
        reviewed.get("approved") is not True
        or not _is_int(peak_rss, minimum=1)
        or reviewed.get("peak_rss_bytes") != peak_rss
        or reviewed.get("estimated_full_cache_upper_bytes") != estimated_upper
        or not _is_int(reviewed.get("max_row_group_rows"), minimum=1)
        or int(reviewed["max_row_group_rows"]) > 4_096
        or reviewed.get("safety_factor") != RESOURCE_SAFETY_FACTOR
        or reviewed.get("projected_compressed_bytes_total") != projected_total
        or reviewed.get("observed_cache_bytes") != sum(observed_bytes.values())
    ):
        raise LargeCacheValidationError("Large smoke resource review is invalid")
    return {
        "source": "approved_large_v2_smoke_review",
        "observed_rows": observed_rows,
        "observed_bytes": observed_bytes,
        "projected_compressed_bytes_by_kind": projected,
        "projected_compressed_bytes_total": projected_total,
        "safety_factor": RESOURCE_SAFETY_FACTOR,
        "estimated_full_cache_upper_bytes": estimated_upper,
    }


def _validate_large_smoke_chain(
    *,
    root: Path,
    review_path: Path,
    smoke_report_path: Path,
    reviewer_cli_path: Path,
    reviewer_core_path: Path,
    config_sha256: str,
    builder_sha256: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    review = _load_json(review_path, role="Large smoke review")
    if (
        review.get("schema_version") != SMOKE_REVIEW_SCHEMA_VERSION
        or review.get("status") != "approved"
        or review.get("approved") is not True
        or review.get("full_run_approved") is not True
        or review.get("review_type") != "automated_engineering_gate"
        or review.get("human_expert_review") is not False
        or review.get("experiment_config_sha256") != config_sha256
        or review.get("builder_sha256") != builder_sha256
        or review.get("builder_dependency_sha256") != GENERIC_BUILDER_SHA256
    ):
        raise LargeCacheValidationError("Large smoke review identity is invalid")
    reviewer = _mapping(review.get("reviewer_source"))
    if (
        reviewer.get("cli_path") != REVIEWER_CLI_RELATIVE_PATH.as_posix()
        or reviewer.get("core_path") != REVIEWER_CORE_RELATIVE_PATH.as_posix()
        or reviewer.get("cli_sha256") != sha256_file(reviewer_cli_path)
        or reviewer.get("core_sha256") != sha256_file(reviewer_core_path)
        or reviewer.get("cli_sha256") != REVIEWER_CLI_SHA256
        or reviewer.get("core_sha256") != REVIEWER_CORE_SHA256
    ):
        raise LargeCacheValidationError("Large smoke reviewer source is invalid")
    source = _mapping(review.get("source_contract"))
    observed_output_splits = source.get("observed_output_splits")
    valid_observed_splits = isinstance(observed_output_splits, Mapping) and set(
        observed_output_splits
    ) == set(EXPECTED_ROWS)
    observed_splits: dict[str, list[str]] = {}
    if valid_observed_splits and isinstance(observed_output_splits, Mapping):
        for kind in EXPECTED_ROWS:
            values = observed_output_splits.get(kind)
            valid_values = (
                isinstance(values, list)
                and bool(values)
                and all(value in ALLOWED_SPLITS for value in values)
                and len(values) == len(set(values))
                and values == [split for split in ALLOWED_SPLITS if split in values]
            )
            if not valid_values:
                valid_observed_splits = False
                break
            assert isinstance(values, list)
            observed_splits[kind] = list(values)
    coverage_complete = valid_observed_splits and all(
        set(values) == set(ALLOWED_SPLITS) for values in observed_splits.values()
    )
    if (
        source.get("model_id") != MODEL_ID
        or source.get("model_revision") != MODEL_REVISION
        or source.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
        or source.get("do_normalize") is not True
        or source.get("allowed_splits") != list(ALLOWED_SPLITS)
        or not valid_observed_splits
        or source.get("allowed_split_coverage_complete") is not coverage_complete
        or source.get("full_cache_validation_requires_all_allowed_splits") is not True
        or source.get("metric_evaluation_performed") is not False
        or source.get(
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering"
        )
        is not True
        or source.get("evaluation_audio_decoded") is not False
        or source.get("evaluation_rows_sent_to_encoder") is not False
        or source.get("evaluation_labels_used_for_training_or_metrics") is not False
    ):
        raise LargeCacheValidationError("Large smoke source contract is invalid")
    technical = _mapping(review.get("technical_checks"))
    if (
        technical.get("report_status_completed") is not True
        or technical.get("smoke_utterance_limit") != 100
        or technical.get("embedding_dimensions")
        != {
            "utterance_mean_std": EXPECTED_DIMENSIONS["utterance"],
            "phone_mean": EXPECTED_DIMENSIONS["phone"],
            "word_mean": EXPECTED_DIMENSIONS["word"],
        }
        or any(
            technical.get(key) is not True
            for key in (
                "all_reported_shard_sizes_and_sha256_match",
                "storage_dtype_float16",
                "pinned_preprocessor_config_validated",
                "all_embeddings_finite_and_nonzero",
                "identifier_columns_are_lowercase_sha256",
                "frame_geometry_check_passed",
            )
        )
        or technical.get("prohibited_output_column_hits") != []
        or not _is_int(technical.get("oom_count"))
        or technical.get("oom_count") != 0
    ):
        raise LargeCacheValidationError("Large smoke technical checks are invalid")
    smoke = _load_json(smoke_report_path, role="Large smoke report")
    if (
        review.get("smoke_report_path") != SMOKE_REPORT_RELATIVE_PATH.as_posix()
        or review.get("smoke_report_sha256") != sha256_file(smoke_report_path)
        or smoke.get("schema_version") != CACHE_REPORT_SCHEMA_VERSION
        or smoke.get("status") != "completed"
    ):
        raise LargeCacheValidationError("Large smoke report link is invalid")
    smoke_source = _mapping(smoke.get("source"))
    smoke_model = _mapping(smoke.get("model"))
    smoke_runtime = _mapping(smoke.get("runtime"))
    smoke_policy = _mapping(smoke.get("policy"))
    smoke_configuration = _mapping(smoke.get("configuration"))
    smoke_progress = _mapping(smoke.get("progress"))
    smoke_evaluation_splits = smoke_policy.get("evaluation_splits")
    if (
        smoke_source.get("experiment_config_sha256") != config_sha256
        or smoke_source.get("builder_sha256") != builder_sha256
        or smoke_source.get("builder_dependency_path")
        != GENERIC_BUILDER_RELATIVE_PATH.as_posix()
        or smoke_source.get("builder_dependency_sha256") != GENERIC_BUILDER_SHA256
        or smoke_source.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
    ):
        raise LargeCacheValidationError("Large smoke source hashes are invalid")
    if (
        smoke_model.get("id") != MODEL_ID
        or smoke_model.get("revision") != MODEL_REVISION
        or smoke_model.get("config_sha256") != MODEL_CONFIG_SHA256
        or smoke_model.get("checkpoint_sha256") != MODEL_CHECKPOINT_SHA256
        or smoke_model.get("preprocessor_config_sha256") != MODEL_PREPROCESSOR_SHA256
        or any(
            smoke_model.get(key) != value
            for key, value in REPORT_MODEL_ARCHITECTURE.items()
        )
        or smoke_model.get("frame_stride_samples") != 320
        or smoke_model.get("receptive_field_samples") != 400
        or smoke_model.get("frozen") is not True
        or smoke_model.get("local_files_only") is not True
        or smoke_model.get("do_normalize") is not True
    ):
        raise LargeCacheValidationError("Large smoke model contract is invalid")
    preprocessor = _mapping(smoke_model.get("preprocessor_contract"))
    if preprocessor != {
        "sampling_rate_hz": 16_000,
        "do_normalize": True,
        "return_attention_mask": True,
        "source": "official_pinned_revision",
        "source_url": PINNED_PREPROCESSOR_URL,
        "config_sha256": MODEL_PREPROCESSOR_SHA256,
        "local_preprocessor_config_present": True,
        "local_preprocessor_config_validated": True,
        "auto_feature_extractor_used": False,
    }:
        raise LargeCacheValidationError("Large smoke preprocessor is invalid")
    observed_dtypes = smoke_runtime.get("observed_model_output_dtypes")
    if (
        smoke_runtime.get("cuda_autocast_enabled") is not True
        or smoke_runtime.get("cuda_autocast_precision") != "bfloat16"
        or smoke_runtime.get("model_output_dtype_policy")
        != "record_observed_dtype_at_runtime"
        or smoke_runtime.get("storage_precision") != "float16"
        or not isinstance(observed_dtypes, Mapping)
        or not observed_dtypes
        or any(not _is_int(value, minimum=1) for value in observed_dtypes.values())
        or sum(int(value) for value in observed_dtypes.values())
        != smoke_progress.get("chunk_count")
        or not _is_int(smoke_runtime.get("oom_count"))
        or smoke_runtime.get("oom_count") != 0
        or not _is_int(smoke_runtime.get("peak_process_rss_bytes"), minimum=1)
    ):
        raise LargeCacheValidationError("Large smoke runtime is invalid")
    if (
        smoke_policy.get("allowed_splits") != list(ALLOWED_SPLITS)
        or not isinstance(smoke_evaluation_splits, list)
        or set(smoke_evaluation_splits) != set(EVALUATION_SPLITS)
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
        raise LargeCacheValidationError("Large smoke policy is invalid")
    included = _mapping(smoke_progress.get("included_rows_by_split"))
    if (
        smoke_configuration.get("limit") != 100
        or smoke_configuration.get("full_run") is not False
        or set(included) != set(observed_splits.get("utterance", []))
        or any(not _is_int(value, minimum=1) for value in included.values())
        or sum(int(value) for value in included.values()) != 100
    ):
        raise LargeCacheValidationError("Large smoke coverage is invalid")
    resource = _validate_smoke_resource_chain(smoke=smoke, review=review)
    return (
        {
            "smoke_review_sha256": sha256_file(review_path),
            "approved_smoke_report_sha256": sha256_file(smoke_report_path),
            "approved_config_sha256": config_sha256,
            "approved_builder_sha256": builder_sha256,
            "approved_builder_dependency_sha256": GENERIC_BUILDER_SHA256,
            "reviewer_cli_sha256": sha256_file(reviewer_cli_path),
            "reviewer_core_sha256": sha256_file(reviewer_core_path),
        },
        resource,
    )


def _validate_full_gate_chain(
    *,
    root: Path,
    report: Mapping[str, Any],
    base_plus_head_report: Path,
    smoke_review_path: Path,
    config_sha256: str,
    builder_sha256: str,
    issues: _Issues,
) -> tuple[dict[str, str], dict[str, Path], dict[str, Any]]:
    report_configuration = _mapping(report.get("configuration"))
    reported_gate = report_configuration.get("full_run_gate")
    chain_paths: dict[str, Path] = {
        "base_plus_head_report": base_plus_head_report,
        "smoke_review": smoke_review_path,
    }
    try:
        smoke_review = _load_json(smoke_review_path, role="Large smoke review")
        reviewer_source = _mapping(smoke_review.get("reviewer_source"))
        smoke_report_path, _ = _safe_input(
            root,
            str(smoke_review.get("smoke_report_path")),
            role="Large v2 smoke report",
            expected_relative=SMOKE_REPORT_RELATIVE_PATH,
        )
        reviewer_cli_path, _ = _safe_input(
            root,
            str(reviewer_source.get("cli_path")),
            role="smoke reviewer CLI",
            expected_relative=REVIEWER_CLI_RELATIVE_PATH,
        )
        reviewer_core_path, _ = _safe_input(
            root,
            str(reviewer_source.get("core_path")),
            role="smoke reviewer core",
            expected_relative=REVIEWER_CORE_RELATIVE_PATH,
        )
        head_hashes, registry_path = _validate_base_plus_head_chain(
            root=root,
            path=base_plus_head_report,
        )
        smoke_hashes, smoke_resource = _validate_large_smoke_chain(
            root=root,
            review_path=smoke_review_path,
            smoke_report_path=smoke_report_path,
            reviewer_cli_path=reviewer_cli_path,
            reviewer_core_path=reviewer_core_path,
            config_sha256=config_sha256,
            builder_sha256=builder_sha256,
        )
    except LargeCacheValidationError:
        issues.add("large_full_run_gate_chain_invalid")
        return {}, chain_paths, {}
    chain_paths.update(
        {
            "base_plus_head_registry_metadata": registry_path,
            "smoke_report": smoke_report_path,
            "reviewer_cli": reviewer_cli_path,
            "reviewer_core": reviewer_core_path,
        }
    )
    actual = {**head_hashes, **smoke_hashes}
    issues.check(
        isinstance(reported_gate, Mapping)
        and set(reported_gate) == set(actual)
        and dict(reported_gate) == actual,
        "large_full_run_gate_chain_invalid",
    )
    return actual, chain_paths, smoke_resource


def _validate_runtime(
    *,
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    issues: _Issues,
) -> dict[str, Any]:
    features = _mapping(config.get("features"))
    runtime = _mapping(report.get("runtime"))
    progress = _mapping(report.get("progress"))
    observed = runtime.get("observed_model_output_dtypes")
    valid_observed = (
        isinstance(observed, Mapping)
        and bool(observed)
        and all(
            isinstance(key, str) and key and _is_int(value, minimum=1)
            for key, value in observed.items()
        )
    )
    observed_total = (
        sum(int(value) for value in observed.values())
        if valid_observed and isinstance(observed, Mapping)
        else -1
    )
    issues.check(
        features.get("cuda_autocast_precision") == "bfloat16"
        and features.get("observed_model_output_dtype") == "record_at_runtime"
        and features.get("storage_precision") == "float16"
        and runtime.get("cuda_autocast_enabled") is True
        and runtime.get("cuda_autocast_precision") == "bfloat16"
        and runtime.get("model_output_dtype_policy")
        == "record_observed_dtype_at_runtime"
        and runtime.get("storage_precision") == "float16"
        and valid_observed
        and observed_total == progress.get("chunk_count")
        and _is_int(runtime.get("oom_count"))
        and runtime.get("oom_count") == 0
        and _is_int(runtime.get("peak_allocated_bytes"), minimum=1)
        and _is_int(runtime.get("peak_reserved_bytes"), minimum=1)
        and _is_int(runtime.get("peak_process_rss_bytes"), minimum=1)
        and int(runtime.get("peak_reserved_bytes", 0))
        >= int(runtime.get("peak_allocated_bytes", 0)),
        "large_runtime_contract_invalid",
    )
    return {
        "cuda_autocast_precision": runtime.get("cuda_autocast_precision"),
        "storage_precision": runtime.get("storage_precision"),
        "observed_model_output_dtypes": (
            dict(observed) if isinstance(observed, Mapping) else {}
        ),
        "oom_count": runtime.get("oom_count"),
        "peak_allocated_bytes": runtime.get("peak_allocated_bytes"),
        "peak_reserved_bytes": runtime.get("peak_reserved_bytes"),
        "peak_process_rss_bytes": runtime.get("peak_process_rss_bytes"),
    }


def _validate_source_scan(
    *,
    report: Mapping[str, Any],
    generic_result: Mapping[str, Any] | None,
    issues: _Issues,
) -> dict[str, Any]:
    progress = _mapping(report.get("progress"))
    source = progress.get("source_rows_by_split")
    included = progress.get("included_rows_by_split")
    exclusions = progress.get("exclusions")
    valid_source = (
        isinstance(source, Mapping)
        and set(source) == set(SOURCE_ROWS_BY_SPLIT)
        and all(_is_int(value, minimum=1) for value in source.values())
    )
    valid_included = (
        isinstance(included, Mapping)
        and set(included) == set(ALLOWED_SPLITS)
        and all(_is_int(value, minimum=1) for value in included.values())
    )
    valid_exclusions = (
        isinstance(exclusions, Mapping)
        and set(exclusions)
        == {"split_not_allowed", "qc_flags_present", "reserved_prompt"}
        and all(_is_int(value, minimum=1) for value in exclusions.values())
    )
    source_values = (
        {str(key): int(value) for key, value in source.items()}
        if valid_source and isinstance(source, Mapping)
        else {}
    )
    included_values = (
        {str(key): int(value) for key, value in included.items()}
        if valid_included and isinstance(included, Mapping)
        else {}
    )
    exclusion_values = (
        {str(key): int(value) for key, value in exclusions.items()}
        if valid_exclusions and isinstance(exclusions, Mapping)
        else {}
    )
    generic_scan = (
        _mapping(generic_result.get("scan"))
        if isinstance(generic_result, Mapping)
        else {}
    )
    generic_splits = _mapping(generic_scan.get("split_counts"))
    utterance_splits = _mapping(generic_splits.get("utterance"))
    evaluation_rows = sum(source_values.get(split, 0) for split in EVALUATION_SPLITS)
    issues.check(
        progress.get("source_rows_scanned") == SOURCE_ROWS
        and progress.get("source_scan_complete") is True
        and valid_source
        and source_values == SOURCE_ROWS_BY_SPLIT
        and valid_included
        and included_values == INCLUDED_UTTERANCES_BY_SPLIT
        and valid_exclusions
        and exclusion_values == EXPECTED_EXCLUSIONS
        and sum(included_values.values()) + sum(exclusion_values.values())
        == SOURCE_ROWS
        and exclusion_values.get("split_not_allowed") == evaluation_rows
        and all(
            source_values.get(split, 0) >= included_values.get(split, 0)
            for split in ALLOWED_SPLITS
        )
        and progress.get("utterance_rows_written") == EXPECTED_ROWS["utterance"]
        and progress.get("phone_rows_written") == EXPECTED_ROWS["phone"]
        and progress.get("word_rows_written") == EXPECTED_ROWS["word"]
        and (generic_result is None or dict(utterance_splits) == included_values),
        "large_source_scan_contract_invalid",
    )
    return {
        "source_rows_scanned": progress.get("source_rows_scanned"),
        "source_scan_complete": progress.get("source_scan_complete"),
        "source_rows_by_split": source_values,
        "included_rows_by_split": included_values,
        "exclusions": exclusion_values,
    }


def _validate_evaluation_isolation(
    *,
    report: Mapping[str, Any],
    issues: _Issues,
) -> None:
    policy = _mapping(report.get("policy"))
    evaluation_splits = policy.get("evaluation_splits")
    issues.check(
        policy.get("allowed_splits") == list(ALLOWED_SPLITS)
        and isinstance(evaluation_splits, list)
        and set(evaluation_splits) == set(EVALUATION_SPLITS)
        and policy.get("evaluation_split_metric_access") == "forbidden"
        and policy.get("holistic_score_columns_read") == []
        and policy.get("metric_evaluation_performed") is False
        and policy.get("source_manifest_columns_deserialized_before_split_filter")
        == list(SOURCE_COLUMNS)
        and policy.get(
            "evaluation_manifest_annotations_may_be_deserialized_for_filtering"
        )
        is True
        and policy.get("evaluation_audio_decoded") is False
        and policy.get("evaluation_rows_sent_to_encoder") is False
        and policy.get("evaluation_labels_used_for_training_or_metrics") is False,
        "large_evaluation_isolation_invalid",
    )


def _validate_frame_mapping_audit(
    *,
    report: Mapping[str, Any],
    issues: _Issues,
) -> None:
    progress = _mapping(report.get("progress"))
    audit = _mapping(report.get("frame_mapping_audit"))
    issues.check(
        _is_int(progress.get("chunk_count"), minimum=1)
        and audit.get("validated_chunk_count") == progress.get("chunk_count")
        and audit.get("all_hidden_shapes_matched_pinned_geometry") is True
        and audit.get("frame_stride_samples") == 320
        and audit.get("receptive_field_samples") == 400
        and audit.get("frame_center_mapping") == "half_open_interval_by_frame_center",
        "large_frame_mapping_audit_invalid",
    )


def _validate_resource_estimate(
    *,
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    smoke_resource: Mapping[str, Any],
    issues: _Issues,
) -> dict[str, Any]:
    gate = _mapping(config.get("full_run_gate"))
    resource = _mapping(report.get("resource_estimate"))
    outputs = _mapping(report.get("outputs"))
    bytes_per_row: dict[str, float] = {}
    projected: dict[str, int] = {}
    output_bytes: dict[str, int] = {}
    valid_outputs = True
    for kind, expected_rows in EXPECTED_ROWS.items():
        value = _mapping(outputs.get(kind))
        byte_count = value.get("byte_count")
        row_count = value.get("row_count")
        if row_count != expected_rows or not _is_int(byte_count, minimum=1):
            valid_outputs = False
            continue
        assert isinstance(byte_count, int)
        output_bytes[kind] = byte_count
        bytes_per_row[kind] = byte_count / expected_rows
        projected[kind] = math.ceil(bytes_per_row[kind] * expected_rows)
    projected_total = sum(projected.values())
    estimated_upper = math.ceil(projected_total * RESOURCE_SAFETY_FACTOR)
    reported_bytes_per_row = _mapping(resource.get("observed_compressed_bytes_per_row"))
    reported_projected = _mapping(resource.get("projected_compressed_bytes_by_kind"))
    observed_match = valid_outputs and all(
        _is_positive_number(reported_bytes_per_row.get(kind))
        and math.isclose(
            float(reported_bytes_per_row[kind]),
            bytes_per_row[kind],
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for kind in EXPECTED_ROWS
    )
    actual_output_bytes = sum(output_bytes.values())
    pre_run_upper = smoke_resource.get("estimated_full_cache_upper_bytes")
    meaningful_capacity_gate = (
        smoke_resource.get("observed_rows") == SMOKE_OBSERVED_ROWS
        and smoke_resource.get("projected_compressed_bytes_by_kind")
        == SMOKE_PROJECTED_BYTES_BY_KIND
        and smoke_resource.get("projected_compressed_bytes_total")
        == SMOKE_PROJECTED_BYTES_TOTAL
        and pre_run_upper == SMOKE_ESTIMATED_UPPER_BYTES
        and valid_outputs
        and actual_output_bytes <= SMOKE_ESTIMATED_UPPER_BYTES
    )
    issues.check(
        gate.get("resource_estimate_reference_rows") == EXPECTED_ROWS
        and gate.get("resource_estimate_safety_factor") == RESOURCE_SAFETY_FACTOR
        and valid_outputs
        and resource.get("method")
        == "smoke compressed bytes per row scaled to audited full rows"
        and resource.get("reference_rows") == EXPECTED_ROWS
        and observed_match
        and dict(reported_projected) == projected
        and resource.get("projected_compressed_bytes_total") == projected_total
        and resource.get("fixed_float16_embedding_bytes_by_kind") == FIXED_FLOAT16_BYTES
        and resource.get("fixed_float16_embedding_bytes_total")
        == FIXED_FLOAT16_BYTES_TOTAL
        and resource.get(
            "fixed_embedding_estimate_excludes_metadata_and_parquet_overhead"
        )
        is True
        and resource.get("safety_factor") == RESOURCE_SAFETY_FACTOR
        and resource.get("estimated_full_cache_upper_bytes") == estimated_upper
        and resource.get("estimate_is_capacity_planning_not_measured_full_output")
        is True,
        "large_full_report_resource_formula_invalid",
    )
    issues.check(
        meaningful_capacity_gate,
        "large_smoke_capacity_gate_invalid",
    )
    return {
        "meaningful_pre_run_capacity_gate": {
            "source": "approved_large_v2_smoke_review",
            "smoke_observed_rows": dict(SMOKE_OBSERVED_ROWS),
            "smoke_observed_output_bytes": dict(
                _mapping(smoke_resource.get("observed_bytes"))
            ),
            "projected_compressed_bytes_by_kind": dict(SMOKE_PROJECTED_BYTES_BY_KIND),
            "projected_compressed_bytes_total": SMOKE_PROJECTED_BYTES_TOTAL,
            "safety_factor": RESOURCE_SAFETY_FACTOR,
            "estimated_full_cache_upper_bytes": SMOKE_ESTIMATED_UPPER_BYTES,
            "actual_full_output_bytes": actual_output_bytes,
            "actual_within_pre_run_upper": meaningful_capacity_gate,
        },
        "full_report_legacy_self_projection": {
            "semantics": (
                "measured_full_output_self_projection_non_independent_capacity_check"
            ),
            "method_label_as_reported": resource.get("method"),
            "reference_rows": dict(EXPECTED_ROWS),
            "observed_output_bytes": output_bytes,
            "projected_compressed_bytes_by_kind": projected,
            "projected_compressed_bytes_total": projected_total,
            "fixed_float16_embedding_bytes_by_kind": dict(FIXED_FLOAT16_BYTES),
            "fixed_float16_embedding_bytes_total": FIXED_FLOAT16_BYTES_TOTAL,
            "safety_factor": RESOURCE_SAFETY_FACTOR,
            "estimated_full_cache_upper_bytes": estimated_upper,
            "formula_valid": (
                "large_full_report_resource_formula_invalid" not in issues.counts
            ),
        },
    }


def _validate_generic_result(
    result: object,
    *,
    issues: _Issues,
) -> Mapping[str, Any] | None:
    if not isinstance(result, Mapping):
        issues.add("generic_full_cache_validation_invalid")
        return None
    errors = result.get("errors")
    valid = (
        result.get("schema_version") == GENERIC_VALIDATION_SCHEMA_VERSION
        and result.get("status") == "passed"
        and errors == []
    )
    if not valid:
        issues.add("generic_full_cache_validation_failed")
        issues.extend(errors)
    return result


def _validate_full_split_coverage(
    generic_result: Mapping[str, Any] | None,
    *,
    issues: _Issues,
) -> None:
    if generic_result is None:
        issues.add("large_full_split_coverage_invalid")
        return
    contract = _mapping(generic_result.get("contract"))
    files = _mapping(generic_result.get("files"))
    model_files = _mapping(files.get("model"))
    cache_files = _mapping(files.get("cache"))
    scan = _mapping(generic_result.get("scan"))
    rows = _mapping(scan.get("rows_checked"))
    vectors = _mapping(scan.get("embedding_vectors_checked"))
    scalars = _mapping(scan.get("embedding_values_checked"))
    split_counts = _mapping(scan.get("split_counts"))
    forbidden = _mapping(scan.get("forbidden_split_rows"))
    expected_scalars = {
        kind: EXPECTED_ROWS[kind] * EXPECTED_DIMENSIONS[kind] for kind in EXPECTED_ROWS
    }
    valid_splits = True
    for kind, expected_rows in EXPECTED_ROWS.items():
        values = _mapping(split_counts.get(kind))
        kind_files = _mapping(cache_files.get(kind))
        valid_splits = (
            valid_splits
            and set(values) == set(ALLOWED_SPLITS)
            and all(_is_int(value, minimum=1) for value in values.values())
            and sum(int(value) for value in values.values()) == expected_rows
            and forbidden.get(kind) == 0
            and kind_files.get("rows_checked") == expected_rows
            and kind_files.get("shards_checked") == EXPECTED_SHARDS[kind]
        )
    issues.check(
        contract.get("full_run") is True
        and contract.get("allowed_splits") == list(ALLOWED_SPLITS)
        and contract.get("expected_rows") == EXPECTED_ROWS
        and contract.get("hidden_size") == HIDDEN_SIZE
        and contract.get("embedding_dimensions") == EXPECTED_DIMENSIONS
        and contract.get("storage_type") == "float16"
        and model_files.get("config_file_checked") is True
        and model_files.get("config_sha256") == MODEL_CONFIG_SHA256
        and model_files.get("checkpoint_match") is True
        and dict(rows) == EXPECTED_ROWS
        and dict(vectors) == EXPECTED_ROWS
        and dict(scalars) == expected_scalars
        and valid_splits,
        "large_full_split_coverage_invalid",
    )


def _validator_source_hashes(
    root: Path,
    *,
    issues: _Issues,
) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for role, relative in (
        ("large_cli", VALIDATOR_CLI_RELATIVE_PATH),
        ("large_core", VALIDATOR_CORE_RELATIVE_PATH),
        ("generic_cli", GENERIC_VALIDATOR_CLI_RELATIVE_PATH),
        ("generic_core", GENERIC_VALIDATOR_CORE_RELATIVE_PATH),
    ):
        path, relative_value = _safe_input(
            root,
            relative,
            role=f"{role} validator source",
            expected_relative=relative,
        )
        output[role] = {
            "path": relative_value,
            "sha256": sha256_file(path),
        }
    issues.check(
        output["generic_cli"]["sha256"] == GENERIC_VALIDATOR_CLI_SHA256
        and output["generic_core"]["sha256"] == GENERIC_VALIDATOR_CORE_SHA256,
        "large_validator_source_chain_invalid",
    )
    return output


def failure_large_validation(code: str) -> dict[str, Any]:
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "failed",
        "privacy": {
            "raw_data_read": False,
            "source_modeling_manifest_read": False,
            "evaluation_audio_read": False,
            "environment_files_read": False,
            "aggregate_only_output": True,
            "row_identifiers_exported": False,
        },
        "errors": [{"code": code, "count": 1}],
    }


def validate_wavlm_large_feature_cache(
    *,
    cache_root: str | Path,
    report_path: str | Path,
    config_path: str | Path,
    builder_path: str | Path,
    base_plus_head_report: str | Path,
    smoke_review: str | Path,
    workspace_root: str | Path,
    batch_rows: int = 2_048,
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise LargeCacheValidationError("workspace root is invalid")
    if not _is_int(batch_rows, minimum=1):
        raise LargeCacheValidationError("batch_rows must be positive")
    cache, cache_relative = _safe_input(
        root,
        cache_root,
        role="Large canonical cache",
        expected_relative=CANONICAL_CACHE_RELATIVE_PATH,
        directory=True,
    )
    report_file, report_relative = _safe_input(
        root,
        report_path,
        role="Large canonical cache report",
        expected_relative=CANONICAL_REPORT_RELATIVE_PATH,
    )
    config_file, config_relative = _safe_input(
        root,
        config_path,
        role="Large config",
        expected_relative=LARGE_CONFIG_RELATIVE_PATH,
    )
    builder_file, builder_relative = _safe_input(
        root,
        builder_path,
        role="Large builder",
        expected_relative=LARGE_BUILDER_RELATIVE_PATH,
    )
    head_file, head_relative = _safe_input(
        root,
        base_plus_head_report,
        role="Base+ head report",
        expected_relative=BASE_PLUS_HEAD_REPORT_RELATIVE_PATH,
    )
    smoke_review_file, smoke_review_relative = _safe_input(
        root,
        smoke_review,
        role="Large v2 smoke review",
        expected_relative=SMOKE_REVIEW_RELATIVE_PATH,
    )

    config = _load_yaml(config_file, role="Large config")
    report = _load_json(report_file, role="Large cache report")
    config_sha256 = sha256_file(config_file)
    builder_sha256 = sha256_file(builder_file)
    report_sha256 = sha256_file(report_file)
    issues = _Issues()
    validator_sources = _validator_source_hashes(root, issues=issues)

    _validate_large_static_contract(
        config=config,
        report=report,
        config_sha256=config_sha256,
        builder_sha256=builder_sha256,
        issues=issues,
    )
    model_config_file = _validate_local_model_config(
        root=root,
        config=config,
        issues=issues,
    )
    model_checkpoint_file = _resolve_model_checkpoint(
        root=root,
        config=config,
        issues=issues,
    )
    preprocessor_file = _validate_preprocessor(
        root=root,
        config=config,
        report=report,
        issues=issues,
    )
    dependency_path, dependency_sha256 = _validate_builder_dependency(
        root=root,
        report=report,
        issues=issues,
    )
    gate_hashes, gate_paths, smoke_resource = _validate_full_gate_chain(
        root=root,
        report=report,
        base_plus_head_report=head_file,
        smoke_review_path=smoke_review_file,
        config_sha256=config_sha256,
        builder_sha256=builder_sha256,
        issues=issues,
    )
    runtime = _validate_runtime(config=config, report=report, issues=issues)
    _validate_evaluation_isolation(report=report, issues=issues)
    _validate_frame_mapping_audit(report=report, issues=issues)
    resource = _validate_resource_estimate(
        config=config,
        report=report,
        smoke_resource=smoke_resource,
        issues=issues,
    )

    generic_result: Mapping[str, Any] | None = None
    if not issues.counts:
        generic_result = _validate_generic_result(
            validate_wavlm_feature_cache(
                cache_root=cache,
                report_path=report_file,
                config_path=config_file,
                builder_path=builder_file,
                workspace_root=root,
                batch_rows=batch_rows,
                expected_rows=EXPECTED_ROWS,
            ),
            issues=issues,
        )
    source_scan = _validate_source_scan(
        report=report,
        generic_result=generic_result,
        issues=issues,
    )
    _validate_full_split_coverage(generic_result, issues=issues)

    generic_scan = (
        _mapping(generic_result.get("scan"))
        if isinstance(generic_result, Mapping)
        else {}
    )
    generic_files = (
        _mapping(generic_result.get("files"))
        if isinstance(generic_result, Mapping)
        else {}
    )
    generic_memory = (
        _mapping(generic_result.get("memory"))
        if isinstance(generic_result, Mapping)
        else {}
    )
    generic_inputs = (
        _mapping(generic_result.get("inputs"))
        if isinstance(generic_result, Mapping)
        else {}
    )
    source_hashes: dict[str, Any] = {
        "cache_report": {
            "path": report_relative,
            "sha256": report_sha256,
        },
        "config": {
            "path": config_relative,
            "sha256": config_sha256,
        },
        "builder": {
            "path": builder_relative,
            "sha256": builder_sha256,
        },
        "generic_builder_dependency": {
            "path": (
                dependency_path.relative_to(root).as_posix()
                if dependency_path is not None
                else GENERIC_BUILDER_RELATIVE_PATH.as_posix()
            ),
            "sha256": dependency_sha256,
        },
        "preprocessor": {
            "snapshot_path": (
                preprocessor_file.snapshot_relative
                if preprocessor_file is not None
                else None
            ),
            "resolved_path": (
                preprocessor_file.resolved_relative
                if preprocessor_file is not None
                else None
            ),
            "leaf_symlink": (
                preprocessor_file.leaf_symlink
                if preprocessor_file is not None
                else None
            ),
            "sha256": (
                preprocessor_file.sha256 if preprocessor_file is not None else None
            ),
        },
        "model_config": {
            "snapshot_path": (
                model_config_file.snapshot_relative
                if model_config_file is not None
                else None
            ),
            "resolved_path": (
                model_config_file.resolved_relative
                if model_config_file is not None
                else None
            ),
            "leaf_symlink": (
                model_config_file.leaf_symlink
                if model_config_file is not None
                else None
            ),
            "sha256": (
                model_config_file.sha256 if model_config_file is not None else None
            ),
        },
        "model_checkpoint": {
            "snapshot_path": (
                model_checkpoint_file.snapshot_relative
                if model_checkpoint_file is not None
                else None
            ),
            "resolved_path": (
                model_checkpoint_file.resolved_relative
                if model_checkpoint_file is not None
                else None
            ),
            "leaf_symlink": (
                model_checkpoint_file.leaf_symlink
                if model_checkpoint_file is not None
                else None
            ),
            "sha256": (
                model_checkpoint_file.sha256
                if model_checkpoint_file is not None
                else None
            ),
            "hash_validated_by_generic": bool(
                _mapping(generic_files.get("model")).get("checkpoint_match")
            ),
        },
        "base_plus_head_report": {
            "path": head_relative,
            "sha256": gate_hashes.get("base_plus_head_report_sha256"),
        },
        "large_smoke_review": {
            "path": smoke_review_relative,
            "sha256": gate_hashes.get("smoke_review_sha256"),
        },
        "modeling_manifest": {
            "read": False,
            "pinned_sha256": MODELING_MANIFEST_SHA256,
        },
        "validator_sources": validator_sources,
    }
    for role, path in gate_paths.items():
        if role in {"base_plus_head_report", "smoke_review"}:
            continue
        source_hashes[role] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        }

    errors = issues.report()
    full_gate_passed = not any(
        item["code"] == "large_full_run_gate_chain_invalid" for item in errors
    )
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "variant": "large",
        "development_only": True,
        "privacy": {
            "raw_data_read": False,
            "source_modeling_manifest_read": False,
            "evaluation_audio_read": False,
            "environment_files_read": False,
            "aggregate_only_output": True,
            "row_identifiers_exported": False,
        },
        "inputs": {
            "cache_root": cache_relative,
            "source_hashes": source_hashes,
            "generic_validation_inputs": dict(generic_inputs),
        },
        "contract": {
            "full_run": True,
            "source_rows": SOURCE_ROWS,
            "allowed_splits": list(ALLOWED_SPLITS),
            "evaluation_splits": list(EVALUATION_SPLITS),
            "expected_rows": dict(EXPECTED_ROWS),
            "hidden_size": HIDDEN_SIZE,
            "embedding_dimensions": dict(EXPECTED_DIMENSIONS),
            "embedding_values": {
                kind: EXPECTED_ROWS[kind] * EXPECTED_DIMENSIONS[kind]
                for kind in EXPECTED_ROWS
            },
            "expected_shards": dict(EXPECTED_SHARDS),
            "fixed_float16_embedding_bytes_by_kind": dict(FIXED_FLOAT16_BYTES),
            "storage_type": "float16",
            "fixed_float16_embedding_bytes_total": FIXED_FLOAT16_BYTES_TOTAL,
        },
        "components": {
            "generic_cache_validation": {
                "schema_version": (
                    generic_result.get("schema_version")
                    if isinstance(generic_result, Mapping)
                    else None
                ),
                "status": (
                    generic_result.get("status")
                    if isinstance(generic_result, Mapping)
                    else "not_run"
                ),
                "error_count": (
                    len(generic_result.get("errors", []))
                    if isinstance(generic_result, Mapping)
                    and isinstance(generic_result.get("errors"), list)
                    else None
                ),
            },
            "large_contract_validation": {
                "passed": not any(item["code"].startswith("large_") for item in errors),
            },
            "full_run_gate_validation": {
                "passed": full_gate_passed,
                "builder_code_executed": False,
                "base_plus_seeds_checked": (
                    len(REQUIRED_SEEDS) if full_gate_passed else None
                ),
                "base_plus_cache_shards_checked": (
                    sum(EXPECTED_SHARDS.values()) if full_gate_passed else None
                ),
                "base_plus_registry_artifacts_checked": (
                    13 if full_gate_passed else None
                ),
                "large_v2_smoke_rows_checked": (
                    dict(SMOKE_OBSERVED_ROWS) if full_gate_passed else {}
                ),
            },
        },
        "full_run_gate": gate_hashes,
        "source_scan": source_scan,
        "runtime": runtime,
        "resource_estimate": resource,
        "files": dict(generic_files),
        "scan": dict(generic_scan),
        "memory": dict(generic_memory),
        "errors": errors,
    }
