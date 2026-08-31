"""Fail-closed STATIC READINESS contract for the B05/S108 locked batch.

This module is intentionally incapable of executing the locked evaluation.
It validates immutable public/aggregate prerequisites in dependency order and
stops before resolving any locked locator, loading a model, using a GPU,
computing a metric, or writing an artifact.  Validators for future executor
artifacts are pure functions; synthetic fixtures can test the contract but can
never become evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml  # type: ignore[import-untyped]

CONFIG_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TBD_PREFIX = "TBD_"
SEEDS = (42, 43, 44)
ARMS = ("b05_explicit_baseline", "s108_final_s1")
OUTCOMES = (
    "pronunciation_score",
    "fluency_score",
    "comprehensibility_score",
)
SLICE_AXES = (
    "l1",
    "gender",
    "proficiency",
    "duration_bucket",
    "target_phone",
    "error_tag",
)

G04_SCHEMA_VERSION = "voice_coach.g04_alignment_completion_attestation.v1"
D07_SCHEMA_VERSION = "voice_coach.d07_gold_completion_attestation.v1"
S107_SCHEMA_VERSION = "voice_coach.wavlm_s107_calibration_commit.v1"
S108_FREEZE_SCHEMA_VERSION = "voice_coach.b05_s108_arm_freeze.v1"
PREREG_SCHEMA_VERSION = "voice_coach.b05_joint_locked_preregistration.v1"
BATCH_SCHEMA_VERSION = "voice_coach.b05_joint_locked_batch_manifest.v1"
AUTH_SCHEMA_VERSION = "voice_coach.b05_one_shot_authorization.v1"
PREDICTION_SCHEMA_VERSION = "voice_coach.b05_locked_row_predictions.v1"
REPORT_SCHEMA_VERSION = "voice_coach.b05_locked_aggregate_report.v1"
RECEIPT_SCHEMA_VERSION = "voice_coach.b05_joint_locked_batch_receipt.v1"
REGISTRY_SCHEMA_VERSION = "voice_coach.b05_locked_registry.v1"
COMMIT_SCHEMA_VERSION = "voice_coach.b05_locked_commit.v1"

B03_CONFIG_SHA256 = "cff6439e6b99b5f725c4214dfe1dda64b20892e8b59fbd064d88d3542c2615bd"
B03_REPORT_SHA256 = "a5c3ade0738777409bb57956b088e025081cb63886808c6d6fc87724d0875519"
B03_REGISTRY_SHA256 = "0445d69cb18fd9105a531a4e5d342d60c7cad94050c6a031c0d7ee3ef6e2141e"
B03_CHECKPOINT_SET_SHA256 = (
    "5a94575da57dfd936bced3eb0068ee5fe803cba1c69ee69b68377b21b67fefda"
)
B03_FEATURE_ALLOWLIST_SHA256 = (
    "6a0ac985f61c3208680780962741be7d084752e720e911522827d92366e20f2f"
)
B06_CONFIG_SHA256 = "62323455d80807ed9ad534bc53f6b21b41feb66836038699cefedc4b8fdedb10"
B06_REPORT_SHA256 = "ef16c405ad01d1c5add4c05ce077072b218836ee683e626e12a0e24f4637a7e1"
B06_REGISTRY_SHA256 = "e9c6ba64729f0b4a8bab577f569444f0a1e5689795fde71fc28544d1bdb43c99"
D07_CONFIG_SHA256 = "dab652e16e857a8cce19dd24f588938c5a881234bdf18718059b4bb2b0414630"
S107_CONFIG_SHA256 = "6702ea196481784319c03e413d8ee6844a060e83407aed204f9344cb0230909f"
S108_CONFIG_SHA256 = "038947cc9ee77840870cc0df31a6d66e9384f89908a7d91a8706cda0757e65aa"

B03_PHONE_FEATURES = (
    "target_label",
    "previous_target_label",
    "next_target_label",
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
)
B03_UTTERANCE_FEATURES = (
    "audio_duration_seconds",
    "rms",
    "peak",
    "clipping_ratio",
    "zero_crossing_rate",
    "pause_count",
    "pause_ratio",
    "voiced_ratio",
    "f0_median_hz",
    "f0_range_hz",
    "word_count",
    "target_phone_count",
    "speech_rate_phones_per_second",
    "articulation_rate_phones_per_second",
    "target_phone_label_duration_npvi",
)
B03_CHECKPOINTS = (
    (
        42,
        "artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/"
        "seed_42/checkpoint.pkl",
        "baa5dadb9de6ef78bd27b48013a645482b64a0f896ab1823646eb16c8db9c302",
    ),
    (
        43,
        "artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/"
        "seed_43/checkpoint.pkl",
        "b3295e5fd24dde4d73921c5ca58297a262bcb16d3f1fffe5ee429c75f0469770",
    ),
    (
        44,
        "artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/"
        "seed_44/checkpoint.pkl",
        "859047bd4a9c3783258397fe4f018a0c4b6859887c750da1c4b7ce9611e7e75c",
    ),
)

EXPECTED_SOURCE_PATHS = {
    "configs/evaluation/b05_joint_locked_batch_manifest_v1.schema.json",
    "configs/evaluation/b05_joint_locked_batch_receipt_v1.schema.json",
    "configs/evaluation/b05_joint_locked_preregistration_v1.schema.json",
    "configs/evaluation/b05_joint_locked_preregistration_v1.template.json",
    "configs/evaluation/b05_locked_aggregate_report_v1.schema.json",
    "configs/evaluation/b05_locked_commit_v1.schema.json",
    "configs/evaluation/b05_locked_registry_v1.schema.json",
    "configs/evaluation/b05_locked_row_predictions_v1.schema.json",
    "configs/evaluation/b05_one_shot_authorization_v1.schema.json",
    "configs/evaluation/b05_one_shot_authorization_v1.template.json",
    "configs/evaluation/b05_s108_arm_freeze_v1.schema.json",
    "scripts/prepare_b05_explicit_locked_evaluation.py",
    "src/voice_coach/evaluation/b05_locked_contract.py",
    "configs/experiments/explicit_baseline_v1.yaml",
    "artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/report.json",
    "artifacts/experiment_registry/"
    "aihub71490-explicit-v1-dev-primary-20260729/metadata.json",
    "configs/experiments/explicit_threshold_ablation_v1.yaml",
    "artifacts/experiments/aihub71490_explicit_threshold_ablation_v1/report.json",
    "artifacts/experiment_registry/"
    "aihub71490-explicit-threshold-ablation-v1-20260729/metadata.json",
    "configs/experiments/d07_independent_expert_gold_v1.yaml",
    "configs/expert_evaluation/d07_gold_completion_attestation_v1.schema.json",
    "src/voice_coach/evaluation/d07_expert_gold_contract.py",
    "configs/experiments/wavlm_s107_calibration_v1.yaml",
    "src/voice_coach/training/wavlm_s107_calibration_training.py",
    "configs/experiments/wavlm_s108_no_locked_dry_run_v1.yaml",
    "src/voice_coach/training/wavlm_s108_dry_run.py",
    "scripts/check_project_constraints.py",
    "pyproject.toml",
}

SCHEMA_PATHS = {
    "configs/evaluation/b05_joint_locked_batch_manifest_v1.schema.json": (
        BATCH_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_joint_locked_batch_receipt_v1.schema.json": (
        RECEIPT_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_joint_locked_preregistration_v1.schema.json": (
        PREREG_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_locked_aggregate_report_v1.schema.json": (
        REPORT_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_locked_commit_v1.schema.json": COMMIT_SCHEMA_VERSION,
    "configs/evaluation/b05_locked_registry_v1.schema.json": REGISTRY_SCHEMA_VERSION,
    "configs/evaluation/b05_locked_row_predictions_v1.schema.json": (
        PREDICTION_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_one_shot_authorization_v1.schema.json": (
        AUTH_SCHEMA_VERSION
    ),
    "configs/evaluation/b05_s108_arm_freeze_v1.schema.json": (
        S108_FREEZE_SCHEMA_VERSION
    ),
}


class B05ContractError(ValueError):
    """Fail-closed contract error with a stage and access audit."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise B05ContractError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    source_pin_read_count: int = 0
    source_toctou_verification_count: int = 0
    b03_static_validation_count: int = 0
    b06_static_validation_count: int = 0
    g04_path_resolution_count: int = 0
    g04_read_attempt_count: int = 0
    g04_read_count: int = 0
    g04_deep_validation_count: int = 0
    d07_path_resolution_count: int = 0
    d07_read_attempt_count: int = 0
    d07_read_count: int = 0
    d07_deep_validation_count: int = 0
    s107_path_resolution_count: int = 0
    s107_read_attempt_count: int = 0
    s107_read_count: int = 0
    s107_deep_validation_count: int = 0
    s108_freeze_path_resolution_count: int = 0
    s108_freeze_read_attempt_count: int = 0
    s108_freeze_read_count: int = 0
    preregistration_path_resolution_count: int = 0
    preregistration_read_attempt_count: int = 0
    preregistration_read_count: int = 0
    batch_manifest_path_resolution_count: int = 0
    batch_manifest_read_attempt_count: int = 0
    batch_manifest_read_count: int = 0
    b03_checkpoint_path_resolution_count: int = 0
    b03_checkpoint_hash_read_attempt_count: int = 0
    b03_checkpoint_hash_read_count: int = 0
    b03_checkpoint_deserialize_count: int = 0
    authorization_path_resolution_count: int = 0
    authorization_read_attempt_count: int = 0
    authorization_read_count: int = 0
    locked_locator_resolution_count: int = 0
    locked_row_read_count: int = 0
    locked_audio_read_count: int = 0
    locked_label_read_count: int = 0
    locked_prediction_read_count: int = 0
    joint_locked_access_count: int = 0
    model_access_count: int = 0
    model_deserialize_count: int = 0
    model_inference_count: int = 0
    gpu_access_count: int = 0
    metric_count: int = 0
    bootstrap_count: int = 0
    holm_count: int = 0
    result_count: int = 0
    network_count: int = 0
    output_path_resolution_count: int = 0
    output_write_count: int = 0
    registry_write_count: int = 0
    commit_write_count: int = 0
    publication_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {key: int(value) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    sha256: str
    size: int
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]
    source_payloads: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    g04_completion_sha256: str
    d07_completion_sha256: str
    s107_commit_sha256: str
    s108_arm_freeze_sha256: str
    preregistration_sha256: str
    batch_manifest_sha256: str
    authorization_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise B05ContractError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise B05ContractError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise B05ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise B05ContractError(f"{name} must be an integer >= {minimum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise B05ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise B05ContractError(f"{name} must be finite")
    return result


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise B05ContractError(f"{name} must be decimal-compatible")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise B05ContractError(f"{name} is invalid") from exc
    if not result.is_finite():
        raise B05ContractError(f"{name} must be finite")
    return result


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise B05ContractError(f"{name} must be lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise B05ContractError(f"{name}: expected {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise B05ContractError(
            f"{name} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise B05ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_nonfinite_tree(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonfinite_tree(child, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise B05ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise B05ContractError(f"invalid JSON numeric constant: {value}")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise B05ContractError(f"{name} is not strict UTF-8 JSON: {exc}") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def canonical_json_bytes(
    value: Mapping[str, Any],
    *,
    exclude_binding: bool = False,
) -> bytes:
    payload = dict(value)
    if exclude_binding:
        payload.pop("binding_sha256", None)
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def binding_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value, exclude_binding=True)).hexdigest()


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return TBD_PREFIX in value
    if isinstance(value, Mapping):
        return any(_contains_tbd(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(child) for child in value)
    return False


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    if "\\" in text or "\x00" in text:
        raise B05ContractError(f"{name} must use a normalized POSIX path")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != text
    ):
        raise B05ContractError(f"{name} must be workspace-relative and normalized")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    require_leaf: bool,
) -> Path:
    normalized = _relative_string(relative, name)
    current = root
    parts = PurePosixPath(normalized).parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if require_leaf or index < len(parts) - 1:
                raise B05ContractError(f"{name} does not exist: {normalized}") from None
            return current
        if stat.S_ISLNK(mode):
            raise B05ContractError(f"{name} crosses a symlink: {normalized}")
    return current


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name, require_leaf=True)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise B05ContractError(f"{name} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if identity != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise B05ContractError(f"{name} changed before read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != identity:
        raise B05ContractError(f"{name} changed during read")
    data = b"".join(chunks)
    if len(data) != opened.st_size:
        raise B05ContractError(f"{name} short read")
    return data, FileSnapshot(
        path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        device=int(opened.st_dev),
        inode=int(opened.st_ino),
        mtime_ns=int(opened.st_mtime_ns),
    )


def _verify_snapshot(root: Path, snapshot: FileSnapshot, name: str) -> None:
    _, observed = _read_file(root, snapshot.path, name)
    if observed != snapshot:
        raise B05ContractError(f"{name} changed during preflight")


def _read_exact_json(
    root: Path,
    relative: str,
    expected_sha256: str,
    name: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    data, snapshot = _read_file(root, relative, name)
    if snapshot.sha256 != expected_sha256:
        raise B05ContractError(
            f"{name} SHA mismatch: expected {expected_sha256}, got {snapshot.sha256}"
        )
    return _parse_json(data, name), snapshot


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise B05ContractError(f"cannot load B05 config: {exc}") from exc
    config = _mapping(value, "B05 config")
    _reject_nonfinite_tree(config, "B05 config")
    return config


def _schema_object_nodes(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        if value.get("type") == "object":
            _expect(value.get("additionalProperties"), False, f"{name} strictness")
            properties = _mapping(value.get("properties"), f"{name} properties")
            required = set(_list(value.get("required"), f"{name} required"))
            if required != set(properties):
                raise B05ContractError(
                    f"{name} required/properties mismatch: "
                    f"required={sorted(required)}, properties={sorted(properties)}"
                )
        for key, child in value.items():
            _schema_object_nodes(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _schema_object_nodes(child, f"{name}[{index}]")


def _validate_schema(
    payload: Mapping[str, Any],
    *,
    version: str,
    name: str,
) -> None:
    _expect(
        payload.get("$schema"),
        "https://json-schema.org/draft/2020-12/schema",
        f"{name} dialect",
    )
    _expect(payload.get("type"), "object", f"{name} root type")
    _expect(payload.get("additionalProperties"), False, f"{name} strictness")
    properties = _mapping(payload.get("properties"), f"{name} properties")
    version_node = _mapping(properties.get("schema_version"), f"{name} version")
    _expect(version_node.get("const"), version, f"{name} schema version")
    _schema_object_nodes(payload, name)


def _validate_template(payload: Mapping[str, Any], *, prereg: bool) -> None:
    if prereg:
        _expect(payload.get("schema_version"), PREREG_SCHEMA_VERSION, "template schema")
        _expect(payload.get("status"), "draft_not_approved", "template status")
        _expect(
            payload.get("frozen_before_any_locked_access"), False, "template freeze"
        )
        baseline = _mapping(payload.get("b03_canonical_baseline"), "template B03")
        _expect(tuple(baseline.get("seeds", [])), SEEDS, "template seeds")
        _expect(baseline.get("phone_decision_threshold"), 0.5, "template threshold")
        analysis = _mapping(payload.get("analysis_plan"), "template analysis")
        _expect(tuple(analysis.get("slice_axes", [])), SLICE_AXES, "template slices")
        _expect(analysis.get("bootstrap_repetitions"), 5000, "template bootstrap")
        _expect(analysis.get("confidence_level"), 0.95, "template confidence")
        _expect(
            analysis.get("multiple_comparison_correction"),
            "holm",
            "template multiplicity",
        )
        _expect(analysis.get("cluster_unit"), "speaker_id_sha256", "template cluster")
        _expect(
            analysis.get("slice_result_keys"),
            [],
            "template unresolved slice keys",
        )
        _expect(
            analysis.get("bootstrap_result_keys"),
            [],
            "template unresolved bootstrap keys",
        )
        _expect(
            analysis.get("holm_hypotheses"),
            [],
            "template unresolved Holm hypotheses",
        )
    else:
        _expect(payload.get("schema_version"), AUTH_SCHEMA_VERSION, "template schema")
        _expect(payload.get("status"), "draft_not_authorized", "template status")
    if not _contains_tbd(payload):
        raise B05ContractError("template must preserve unresolved TBD placeholders")


def _validate_config_values(config: Mapping[str, Any]) -> None:
    root_keys = {
        "schema_version",
        "experiment",
        "canonical_sources",
        "b03_canonical_baseline",
        "b06_posthoc_exclusion",
        "g04_gate",
        "d07_gate",
        "s107_gate",
        "s108_arm_gate",
        "joint_preregistration",
        "joint_batch_manifest",
        "one_shot_authorization",
        "schema_contract",
        "flow_contract",
        "statistics_contract",
        "execution_order",
        "outputs",
        "runtime",
    }
    _expect_keys(config, root_keys, "B05 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "b05_explicit_locked_evaluation_v1", "experiment")
    _expect(
        experiment.get("implementation_status"),
        "production_static_readiness_only",
        "implementation status",
    )
    for key, value in experiment.items():
        if key.startswith("actual_") or key == "b05_score_awarded":
            _expect(value, 0, f"experiment.{key}")
        if key.endswith("_claim_allowed"):
            _expect(value, False, f"experiment.{key}")

    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    _expect(
        canonical.get("access_scope"),
        "C1_static_and_A1_aggregate_only",
        "source scope",
    )
    _expect(
        canonical.get("mutable_tracker_self_pin_allowed"),
        False,
        "tracker self-pin",
    )
    pins = _list(canonical.get("pins"), "source pins")
    seen: set[str] = set()
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        path = _relative_string(pin.get("path"), f"source pin {index} path")
        if path in seen:
            raise B05ContractError(f"duplicate source pin: {path}")
        if "RESEARCH_COMPLETION_TRACKER" in path:
            raise B05ContractError("mutable tracker must not be self-pinned")
        seen.add(path)
        _sha256(pin.get("sha256"), f"source pin {index} SHA")
    if seen != EXPECTED_SOURCE_PATHS:
        raise B05ContractError(
            "source path set mismatch: "
            f"missing={sorted(EXPECTED_SOURCE_PATHS - seen)}, "
            f"extra={sorted(seen - EXPECTED_SOURCE_PATHS)}"
        )

    baseline = _mapping(config.get("b03_canonical_baseline"), "B03 baseline")
    for key, expected in {
        "config_sha256": B03_CONFIG_SHA256,
        "report_sha256": B03_REPORT_SHA256,
        "registry_sha256": B03_REGISTRY_SHA256,
        "checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "feature_allowlist_sha256": B03_FEATURE_ALLOWLIST_SHA256,
        "phone_decision_threshold": 0.5,
        "seed_ensemble_definition": (
            "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean"
        ),
        "locked_test_evaluation_allowed_in_b03_config": False,
        "checkpoint_deserialization_allowed_in_preflight": False,
        "result_based_change_allowed": False,
    }.items():
        _expect(baseline.get(key), expected, f"B03 {key}")
    _expect(tuple(baseline.get("seeds", [])), SEEDS, "B03 seeds")
    checkpoints = _list(baseline.get("checkpoints"), "B03 checkpoints")
    observed = tuple(
        (
            _mapping(item, "B03 checkpoint").get("seed"),
            _mapping(item, "B03 checkpoint").get("path"),
            _mapping(item, "B03 checkpoint").get("sha256"),
        )
        for item in checkpoints
    )
    _expect(observed, B03_CHECKPOINTS, "B03 checkpoint pins")

    exclusion = _mapping(config.get("b06_posthoc_exclusion"), "B06 exclusion")
    for key, expected in {
        "config_sha256": B06_CONFIG_SHA256,
        "report_sha256": B06_REPORT_SHA256,
        "registry_sha256": B06_REGISTRY_SHA256,
        "required_b06_status": "failed_unchanged",
        "required_post_hoc_exploratory": True,
        "promotion_decision_allowed": False,
        "locked_test_remains_unread": True,
        "may_replace_b03_config_checkpoint_threshold_or_feature_contract": False,
    }.items():
        _expect(exclusion.get(key), expected, f"B06 {key}")

    expected_orders = (
        ("g04_gate", 1),
        ("d07_gate", 2),
        ("s107_gate", 3),
        ("s108_arm_gate", 4),
        ("joint_preregistration", 5),
        ("joint_batch_manifest", 6),
        ("one_shot_authorization", 7),
    )
    for section_name, order in expected_orders:
        section = _mapping(config.get(section_name), section_name)
        _expect(section.get("validation_order"), order, f"{section_name} order")
        for key, value in section.items():
            if key.endswith("_path") or key == "artifact_path":
                _relative_string(value, f"{section_name}.{key}")
            elif key.endswith("_sha256"):
                text = _string(value, f"{section_name}.{key}")
                if not text.startswith(TBD_PREFIX):
                    _sha256(text, f"{section_name}.{key}")

    flow = _mapping(config.get("flow_contract"), "flow contract")
    _expect(tuple(flow.get("included_arms", [])), ARMS, "flow arms")
    for key in (
        "same_preregistered_batch_manifest_required",
        "both_arms_immutable_before_locked_access",
        "new_independent_test_set_required_for_any_new_run",
        "b05_and_s108_joint_publication_required",
    ):
        _expect(flow.get(key), True, f"flow {key}")
    for key in (
        "first_arm_result_visibility_before_both_sealed",
        "result_based_second_arm_change_allowed",
        "rerun_after_success_allowed",
        "rerun_after_failure_allowed",
        "b03_config_locked_test_flag_may_be_mutated",
        "output_back_reference_to_consumed_receipt_allowed",
        "arm_report_cross_reference_allowed",
        "child_commit_back_reference_to_joint_root_allowed",
    ):
        _expect(flow.get(key), False, f"flow {key}")
    _expect(flow.get("authorized_joint_locked_access_count"), 1, "flow access")
    _expect(
        flow.get("hash_dag_order"),
        [
            "preregistration_freeze_manifest_authorization",
            "arm_prediction_manifests",
            "independent_arm_reports",
            "consumed_joint_receipt",
            "independent_arm_registries",
            "single_joint_root_commit",
        ],
        "hash DAG",
    )

    statistics = _mapping(config.get("statistics_contract"), "statistics")
    _expect(tuple(statistics.get("slice_axes", [])), SLICE_AXES, "slice axes")
    _expect(statistics.get("cluster_unit"), "speaker_id_sha256", "cluster")
    _expect(statistics.get("bootstrap_repetitions"), 5000, "bootstrap")
    _expect(statistics.get("confidence_level"), 0.95, "confidence")
    _expect(
        statistics.get("multiple_comparison_correction"),
        "holm",
        "multiplicity",
    )
    for field in (
        "concrete_minimum_support_thresholds_required",
        "nonempty_exact_slice_result_keys_required",
        "nonempty_exact_bootstrap_result_keys_required",
        "nonempty_exact_holm_hypotheses_required",
    ):
        _expect(statistics.get(field), True, f"statistics {field}")
    _expect(statistics.get("numeric_result_in_static_package_allowed"), False, "result")

    expected_execution_order = [
        "validate_static_sources_schemas_templates_b03_and_b06_boundary",
        "validate_g04_240_of_240_and_at_least_228_before_path_to_any_later_gate",
        "validate_d07_actual_gold_calibration_locked_completion",
        "validate_s107_aggregate_commit_without_calibration_rows",
        "validate_s108_immutable_arm_freeze",
        "validate_joint_preregistration_without_tbd",
        "validate_single_shared_locked_batch_manifest",
        "hash_verify_three_b03_checkpoint_files_without_deserialization",
        "validate_separate_one_shot_joint_authorization",
        "stop_before_locked_locator_row_audio_label_prediction_model_gpu_metric_or_output",
    ]
    _expect(config.get("execution_order"), expected_execution_order, "execution order")

    runtime = _mapping(config.get("runtime"), "runtime")
    for key, value in runtime.items():
        _expect(value, False, f"runtime.{key}")
    outputs = _mapping(config.get("outputs"), "outputs")
    _expect(outputs.get("write_enabled_in_this_package"), False, "output writes")
    _expect(outputs.get("no_overwrite"), True, "no overwrite")
    for key, value in outputs.items():
        if key.endswith("_path") or key.endswith("_root"):
            _relative_string(value, f"outputs.{key}")


def _validate_b03_static(
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    experiment = _mapping(config.get("experiment"), "B03 experiment")
    _expect(experiment.get("seeds"), list(SEEDS), "B03 source seeds")
    data = _mapping(config.get("data"), "B03 data")
    forbidden = _list(data.get("forbidden_metric_splits"), "B03 forbidden splits")
    if "locked_test" not in forbidden:
        raise B05ContractError("B03 locked_test must remain forbidden")
    evaluation = _mapping(config.get("evaluation"), "B03 evaluation")
    _expect(evaluation.get("development_only"), True, "B03 development only")
    _expect(
        evaluation.get("locked_test_evaluation_allowed"),
        False,
        "B03 locked flag",
    )
    features = _mapping(config.get("features"), "B03 features")
    _expect(tuple(features.get("phone", [])), B03_PHONE_FEATURES, "B03 phone features")
    _expect(
        tuple(features.get("utterance", [])),
        B03_UTTERANCE_FEATURES,
        "B03 utterance features",
    )

    _expect(
        report.get("schema_version"),
        "voice_coach.explicit_baseline_report.v1",
        "B03 report schema",
    )
    _expect(report.get("status"), "completed", "B03 report status")
    _expect(report.get("development_only"), True, "B03 report development")
    _expect(report.get("oracle_alignment"), True, "B03 oracle")
    _expect(
        report.get("deployable_alignment_claim_allowed"),
        False,
        "B03 deployment",
    )
    leakage = _mapping(report.get("leakage_controls"), "B03 leakage")
    for field in ("locked_test_accessed", "stack_accessed", "calibration_accessed"):
        _expect(leakage.get(field), False, f"B03 leakage {field}")
    _expect(leakage.get("accepted_splits"), ["train", "dev"], "B03 splits")
    _expect(
        tuple(leakage.get("phone_feature_allowlist", [])),
        B03_PHONE_FEATURES,
        "B03 report phone features",
    )
    _expect(
        tuple(leakage.get("utterance_feature_allowlist", [])),
        B03_UTTERANCE_FEATURES,
        "B03 report utterance features",
    )
    runs = _list(report.get("runs"), "B03 runs")
    _expect(tuple(run.get("seed") for run in runs), SEEDS, "B03 run seeds")
    checkpoint_records = []
    for run in runs:
        seed = run.get("seed")
        artifacts = _mapping(run.get("artifacts"), f"B03 seed {seed} artifacts")
        checkpoint = _mapping(artifacts.get("checkpoint"), "B03 checkpoint")
        checkpoint_records.append(
            (
                seed,
                "artifacts/experiments/aihub71490_explicit_oracle_boundary_v1/"
                + _string(checkpoint.get("path"), "B03 checkpoint path"),
                checkpoint.get("sha256"),
            )
        )
    _expect(tuple(checkpoint_records), B03_CHECKPOINTS, "B03 report checkpoints")
    summary = _mapping(report.get("summary"), "B03 summary")
    _expect(summary.get("seeds_completed"), list(SEEDS), "B03 summary seeds")
    _expect(summary.get("results_are_development_only"), True, "B03 dev summary")
    _expect(summary.get("oracle_alignment"), True, "B03 oracle summary")
    _expect(
        summary.get("speaker_cluster_bootstrap_performed"),
        False,
        "B03 bootstrap summary",
    )
    _expect(
        summary.get("locked_test_remains_unread"),
        True,
        "B03 locked summary",
    )
    gate = _mapping(summary.get("gate_assessment"), "B03 gate assessment")
    _expect(gate.get("promotion_decision_allowed"), False, "B03 promotion")

    _expect(registry.get("schema_version"), 1, "B03 registry schema")
    _expect(
        registry.get("run_id"),
        "aihub71490-explicit-v1-dev-primary-20260729",
        "B03 registry run",
    )
    _expect(registry.get("status"), "completed", "B03 registry status")
    _expect(registry.get("failure"), None, "B03 registry failure")
    inputs = _mapping(registry.get("inputs"), "B03 registry inputs")
    configs = _list(inputs.get("config"), "B03 registry config")
    if not any(record.get("sha256") == B03_CONFIG_SHA256 for record in configs):
        raise B05ContractError("B03 registry lacks canonical config hash")
    artifact_records = _list(registry.get("artifacts"), "B03 registry artifacts")
    artifact_hashes = {
        _mapping(record, "B03 registry artifact").get("sha256")
        for record in artifact_records
    }
    required = {item[2] for item in B03_CHECKPOINTS} | {B03_REPORT_SHA256}
    if not required.issubset(artifact_hashes):
        raise B05ContractError("B03 registry lacks canonical artifacts")


def _validate_b06_static(
    config: Mapping[str, Any],
    report: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    experiment = _mapping(config.get("experiment"), "B06 experiment")
    _expect(experiment.get("status"), "post_hoc_exploratory_b06_remains_failed", "B06")
    evaluation = _mapping(config.get("evaluation"), "B06 evaluation")
    _expect(evaluation.get("b06_status_must_remain"), "failed", "B06 status")
    _expect(evaluation.get("post_hoc_exploratory"), True, "B06 posthoc")
    _expect(evaluation.get("promotion_decision_allowed"), False, "B06 promotion")
    summary = _mapping(report.get("summary"), "B06 summary")
    _expect(summary.get("post_hoc_exploratory"), True, "B06 report posthoc")
    _expect(summary.get("b06_status"), "failed_unchanged", "B06 report status")
    _expect(summary.get("promotion_decision_allowed"), False, "B06 report promotion")
    _expect(summary.get("locked_test_remains_unread"), True, "B06 locked boundary")
    _expect(registry.get("status"), "completed", "B06 registry status")
    _expect(registry.get("failure"), None, "B06 registry failure")


def _validate_static_config_impl(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> StaticValidation:
    """Validate immutable static sources without touching any runtime gate."""

    root = workspace_root.resolve(strict=True)
    _validate_config_values(config)
    canonical = _mapping(config.get("canonical_sources"), "canonical sources")
    pins = _list(canonical.get("pins"), "source pins")
    payloads: dict[str, bytes] = {}
    snapshots: list[FileSnapshot] = []
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        expected = _sha256(pin.get("sha256"), f"source pin {index} SHA")
        data, snapshot = _read_file(root, relative, f"source pin {relative}")
        audit.source_pin_read_count += 1
        if snapshot.sha256 != expected:
            raise B05ContractError(
                f"source pin SHA mismatch for {relative}: expected {expected}, "
                f"got {snapshot.sha256}",
                audit=audit,
            )
        payloads[relative] = data
        snapshots.append(snapshot)

    for relative, version in SCHEMA_PATHS.items():
        _validate_schema(
            _parse_json(payloads[relative], relative),
            version=version,
            name=relative,
        )
    _validate_template(
        _parse_json(
            payloads[
                "configs/evaluation/b05_joint_locked_preregistration_v1.template.json"
            ],
            "B05 preregistration template",
        ),
        prereg=True,
    )
    _validate_template(
        _parse_json(
            payloads["configs/evaluation/b05_one_shot_authorization_v1.template.json"],
            "B05 authorization template",
        ),
        prereg=False,
    )

    def source_yaml(relative: str) -> Mapping[str, Any]:
        try:
            value = yaml.load(
                payloads[relative].decode("utf-8"),
                Loader=_UniqueKeyLoader,
            )
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise B05ContractError(f"{relative} is not strict YAML: {exc}") from exc
        return _mapping(value, relative)

    _validate_b03_static(
        source_yaml("configs/experiments/explicit_baseline_v1.yaml"),
        _parse_json(
            payloads[
                "artifacts/experiments/"
                "aihub71490_explicit_oracle_boundary_v1/report.json"
            ],
            "B03 report",
        ),
        _parse_json(
            payloads[
                "artifacts/experiment_registry/"
                "aihub71490-explicit-v1-dev-primary-20260729/metadata.json"
            ],
            "B03 registry",
        ),
    )
    audit.b03_static_validation_count += 1
    _validate_b06_static(
        source_yaml("configs/experiments/explicit_threshold_ablation_v1.yaml"),
        _parse_json(
            payloads[
                "artifacts/experiments/"
                "aihub71490_explicit_threshold_ablation_v1/report.json"
            ],
            "B06 report",
        ),
        _parse_json(
            payloads[
                "artifacts/experiment_registry/"
                "aihub71490-explicit-threshold-ablation-v1-20260729/metadata.json"
            ],
            "B06 registry",
        ),
    )
    audit.b06_static_validation_count += 1

    for snapshot in snapshots:
        _verify_snapshot(root, snapshot, f"source snapshot {snapshot.path}")
        audit.source_toctou_verification_count += 1

    config_relative = config_path.resolve().relative_to(root).as_posix()
    config_data, config_snapshot = _read_file(root, config_relative, "B05 config")
    policy = {
        "flow_contract": config["flow_contract"],
        "statistics_contract": config["statistics_contract"],
        "execution_order": config["execution_order"],
        "runtime": config["runtime"],
    }
    return StaticValidation(
        config_sha256=hashlib.sha256(config_data).hexdigest(),
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(snapshots) + (config_snapshot,),
        source_payloads=payloads,
    )


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> StaticValidation:
    try:
        return _validate_static_config_impl(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
            audit=audit,
        )
    except B05ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise


def _validate_binding(payload: Mapping[str, Any], name: str) -> None:
    _sha256(payload.get("binding_sha256"), f"{name} binding")
    _expect(payload.get("binding_sha256"), binding_sha256(payload), f"{name} binding")


def _validate_no_tbd(payload: Mapping[str, Any], name: str) -> None:
    if _contains_tbd(payload):
        raise B05ContractError(f"{name} contains TBD")


def validate_g04_completion_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate only the privacy-safe G04 completion aggregate."""

    _reject_nonfinite_tree(payload, "G04 completion")
    keys = {
        "schema_version",
        "status",
        "aggregate_only",
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
        "locked_access_count",
        "expected_count",
        "completed_count",
        "correct_count",
        "alignment_accuracy",
        "scorer_status",
        "privacy_safe_report_sha256",
        "reviewer_qualification_confirmed",
        "research_lead_approved",
    }
    _expect_keys(payload, keys, "G04 completion")
    _expect(payload.get("schema_version"), G04_SCHEMA_VERSION, "G04 schema")
    _expect(payload.get("status"), "passed", "G04 status")
    _expect(payload.get("aggregate_only"), True, "G04 aggregate")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"G04 {field}")
    _expect(payload.get("locked_access_count"), 0, "G04 locked access")
    _expect(payload.get("expected_count"), 240, "G04 expected")
    _expect(payload.get("completed_count"), 240, "G04 completed")
    correct = _integer(payload.get("correct_count"), "G04 correct", 228)
    if correct > 240:
        raise B05ContractError("G04 correct_count exceeds 240")
    accuracy = _decimal(payload.get("alignment_accuracy"), "G04 accuracy")
    _expect(accuracy, Decimal(correct) / Decimal(240), "G04 accuracy/count")
    if accuracy < Decimal("0.95"):
        raise B05ContractError("G04 accuracy gate failed")
    _expect(payload.get("scorer_status"), "passed", "G04 scorer")
    _sha256(payload.get("privacy_safe_report_sha256"), "G04 report")
    _expect(payload.get("reviewer_qualification_confirmed"), True, "G04 reviewer")
    _expect(payload.get("research_lead_approved"), True, "G04 lead")
    return payload


def validate_d07_completion_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate actual D07 calibration+locked completion without reading rows."""

    _reject_nonfinite_tree(payload, "D07 completion")
    keys = {
        "schema_version",
        "status",
        "aggregate_only",
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
        "locked_access_count",
        "calibration_count",
        "locked_gold_count",
        "independent_rater_count",
        "adjudicator_count",
        "two_original_ratings_retained",
        "adjudication_completed",
        "scalar_agreement",
        "taxonomy_agreement",
        "agreement_gate_passed",
        "label_definition_frozen",
        "primary_definition_frozen",
        "rubric_unchanged_after_locked_start",
        "model_results_unseen",
        "research_lead_approved",
        "calibration_gold_sha256",
        "sealed_locked_gold_sha256",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
        "locked_evaluation_report_sha256",
        "expert_locked_access_transaction_count",
        "sensitivity_analysis_completed",
    }
    _expect_keys(payload, keys, "D07 completion")
    _expect(payload.get("schema_version"), D07_SCHEMA_VERSION, "D07 schema")
    _expect(payload.get("status"), "completed", "D07 status")
    _expect(payload.get("aggregate_only"), True, "D07 aggregate")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"D07 {field}")
    _expect(payload.get("locked_access_count"), 0, "D07 developer locked access")
    _expect(payload.get("calibration_count"), 800, "D07 calibration")
    locked = _integer(payload.get("locked_gold_count"), "D07 locked gold", 200)
    if locked > 1200:
        raise B05ContractError("D07 locked_gold_count exceeds 1200")
    _expect(payload.get("independent_rater_count"), 2, "D07 raters")
    _expect(payload.get("adjudicator_count"), 1, "D07 adjudicator")
    for field in (
        "two_original_ratings_retained",
        "adjudication_completed",
        "agreement_gate_passed",
        "label_definition_frozen",
        "primary_definition_frozen",
        "rubric_unchanged_after_locked_start",
        "model_results_unseen",
        "research_lead_approved",
        "sensitivity_analysis_completed",
    ):
        _expect(payload.get(field), True, f"D07 {field}")
    scalar = _decimal(payload.get("scalar_agreement"), "D07 scalar agreement")
    taxonomy = _decimal(payload.get("taxonomy_agreement"), "D07 taxonomy agreement")
    if scalar < Decimal("0.70") and taxonomy < Decimal("0.65"):
        raise B05ContractError("D07 agreement gate failed")
    for field in (
        "calibration_gold_sha256",
        "sealed_locked_gold_sha256",
        "h00_approval_sha256",
        "h00_attestation_sha256",
        "d07_institutional_approval_sha256",
        "reviewer_attestation_sha256",
        "preregistration_sha256",
        "calibration_report_sha256",
        "g04_completion_attestation_sha256",
        "locked_access_authorization_sha256",
        "locked_evaluation_report_sha256",
    ):
        _sha256(payload.get(field), f"D07 {field}")
    _expect(
        payload.get("expert_locked_access_transaction_count"),
        1,
        "D07 expert access",
    )
    return payload


def _validate_file_record(value: object, name: str) -> Mapping[str, Any]:
    record = _mapping(value, name)
    _expect_keys(record, {"path", "sha256", "size_bytes"}, name)
    _relative_string(record.get("path"), f"{name} path")
    _sha256(record.get("sha256"), f"{name} SHA")
    _integer(record.get("size_bytes"), f"{name} size", 1)
    return record


def validate_s107_commit_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate the aggregate S107 commit without loading calibration rows."""

    keys = {
        "schema_version",
        "status",
        "generation_id",
        "generation_path",
        "artifacts",
        "config",
        "execution_lock",
        "s106_commit",
        "s106_checkpoint",
        "calibration_bundle",
        "registry",
        "label_status",
        "promotion_decision_allowed",
        "s108_ready",
        "binding_sha256",
    }
    _expect_keys(payload, keys, "S107 commit")
    _expect(payload.get("schema_version"), S107_SCHEMA_VERSION, "S107 schema")
    _expect(payload.get("status"), "committed", "S107 status")
    _sha256(payload.get("generation_id"), "S107 generation")
    _relative_string(payload.get("generation_path"), "S107 generation path")
    for field in (
        "config",
        "execution_lock",
        "s106_commit",
        "s106_checkpoint",
        "calibration_bundle",
    ):
        _validate_file_record(payload.get(field), f"S107 {field}")
    config_record = _mapping(payload.get("config"), "S107 config")
    _expect(config_record.get("sha256"), S107_CONFIG_SHA256, "S107 config SHA")
    artifacts = _list(payload.get("artifacts"), "S107 artifacts")
    if len(artifacts) < 2:
        raise B05ContractError("S107 commit lacks calibration artifacts")
    names: set[str] = set()
    for index, raw in enumerate(artifacts):
        record = _mapping(raw, f"S107 artifact {index}")
        _expect_keys(
            record,
            {"name", "path", "sha256", "size_bytes"},
            f"S107 artifact {index}",
        )
        name = _string(record.get("name"), f"S107 artifact {index} name")
        if name in names:
            raise B05ContractError(f"duplicate S107 artifact name: {name}")
        names.add(name)
        _relative_string(record.get("path"), f"S107 artifact {index} path")
        _sha256(record.get("sha256"), f"S107 artifact {index} SHA")
        _integer(record.get("size_bytes"), f"S107 artifact {index} size", 1)
    if not {"calibrator_parameters", "threshold_policy_report"}.issubset(names):
        raise B05ContractError("S107 commit lacks calibrator or threshold policy")
    registry = _mapping(payload.get("registry"), "S107 registry")
    _expect(
        set(registry),
        {"path", "sha256", "size_bytes", "run_id", "status", "artifact_count"},
        "S107 registry keys",
    )
    _relative_string(registry.get("path"), "S107 registry path")
    _sha256(registry.get("sha256"), "S107 registry SHA")
    _integer(registry.get("size_bytes"), "S107 registry size", 1)
    _expect(registry.get("status"), "completed", "S107 registry status")
    _expect(registry.get("artifact_count"), len(artifacts), "S107 artifact count")
    _expect(payload.get("label_status"), "silver_oracle_provisional", "S107 labels")
    _expect(payload.get("promotion_decision_allowed"), False, "S107 promotion")
    _expect(payload.get("s108_ready"), False, "S107 final readiness")
    _validate_binding(payload, "S107 commit")
    return payload


S108_FREEZE_KEYS = {
    "schema_version",
    "freeze_id",
    "status",
    "g04_completion_sha256",
    "d07_completion_sha256",
    "s107_config_sha256",
    "s107_commit_sha256",
    "s108_no_locked_config_sha256",
    "s108_no_locked_receipt_sha256",
    "s108_model_identity_sha256",
    "s108_checkpoint_sha256",
    "s108_calibrator_sha256",
    "s108_threshold_policy_sha256",
    "s108_source_chain_sha256",
    "s108_execution_spec_sha256",
    "choices_frozen_before_locked_access",
    "ready_for_joint_locked_batch",
    "locked_results_unseen",
    "locked_access_count",
    "contains_identifiers",
    "contains_rows",
    "contains_audio",
    "contains_labels",
    "contains_predictions",
    "privacy_zone",
    "binding_sha256",
}


def validate_s108_arm_freeze_payload(
    payload: Mapping[str, Any],
    *,
    g04_sha256: str,
    d07_sha256: str,
    s107_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, S108_FREEZE_KEYS, "S108 arm freeze")
    _expect(payload.get("schema_version"), S108_FREEZE_SCHEMA_VERSION, "freeze schema")
    if (
        re.fullmatch(r"b05s108freeze_[0-9a-f]{32}", str(payload.get("freeze_id")))
        is None
    ):
        raise B05ContractError("invalid S108 freeze ID")
    _expect(
        payload.get("status"),
        "immutable_ready_for_joint_locked_batch",
        "freeze status",
    )
    _expect(payload.get("g04_completion_sha256"), g04_sha256, "freeze G04")
    _expect(payload.get("d07_completion_sha256"), d07_sha256, "freeze D07")
    _expect(payload.get("s107_config_sha256"), S107_CONFIG_SHA256, "freeze S107 config")
    _expect(payload.get("s107_commit_sha256"), s107_sha256, "freeze S107 commit")
    _expect(
        payload.get("s108_no_locked_config_sha256"),
        S108_CONFIG_SHA256,
        "freeze S108 config",
    )
    for field in (
        "s108_no_locked_receipt_sha256",
        "s108_model_identity_sha256",
        "s108_checkpoint_sha256",
        "s108_calibrator_sha256",
        "s108_threshold_policy_sha256",
        "s108_source_chain_sha256",
        "s108_execution_spec_sha256",
    ):
        _sha256(payload.get(field), f"freeze {field}")
    for field in (
        "choices_frozen_before_locked_access",
        "ready_for_joint_locked_batch",
        "locked_results_unseen",
    ):
        _expect(payload.get(field), True, f"freeze {field}")
    _expect(payload.get("locked_access_count"), 0, "freeze access")
    for field in (
        "contains_identifiers",
        "contains_rows",
        "contains_audio",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"freeze {field}")
    _expect(payload.get("privacy_zone"), "A1_aggregate_batch_readiness", "freeze zone")
    _validate_no_tbd(payload, "S108 arm freeze")
    _validate_binding(payload, "S108 arm freeze")
    return payload


PREREG_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "frozen_before_any_locked_access",
    "predecessor_bindings",
    "b03_canonical_baseline",
    "s108_arm",
    "joint_batch",
    "analysis_plan",
    "approvals",
    "privacy_and_claims",
    "binding_sha256",
}


def validate_preregistration_payload(
    payload: Mapping[str, Any],
    *,
    g04_sha256: str,
    d07_sha256: str,
    s107_sha256: str,
    s108_freeze_sha256: str,
    s108_freeze: Mapping[str, Any],
) -> Mapping[str, Any]:
    _expect_keys(payload, PREREG_KEYS, "B05 preregistration")
    _expect(payload.get("schema_version"), PREREG_SCHEMA_VERSION, "prereg schema")
    if (
        re.fullmatch(r"b05prereg_[0-9a-f]{32}", str(payload.get("preregistration_id")))
        is None
    ):
        raise B05ContractError("invalid B05 preregistration ID")
    _expect(payload.get("status"), "approved_and_frozen", "prereg status")
    _expect(payload.get("frozen_before_any_locked_access"), True, "prereg freeze")
    predecessor = _mapping(payload.get("predecessor_bindings"), "prereg predecessors")
    _expect_keys(
        predecessor,
        {
            "b03_config_sha256",
            "b03_report_sha256",
            "b03_registry_sha256",
            "b03_checkpoint_set_sha256",
            "g04_completion_sha256",
            "d07_completion_sha256",
            "s107_commit_sha256",
            "s108_arm_freeze_sha256",
        },
        "prereg predecessors",
    )
    for field, expected in {
        "b03_config_sha256": B03_CONFIG_SHA256,
        "b03_report_sha256": B03_REPORT_SHA256,
        "b03_registry_sha256": B03_REGISTRY_SHA256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "g04_completion_sha256": g04_sha256,
        "d07_completion_sha256": d07_sha256,
        "s107_commit_sha256": s107_sha256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
    }.items():
        _expect(predecessor.get(field), expected, f"prereg {field}")

    baseline = _mapping(payload.get("b03_canonical_baseline"), "prereg B03")
    _expect_keys(
        baseline,
        {
            "seeds",
            "checkpoint_set_sha256",
            "feature_allowlist_sha256",
            "phone_decision_threshold",
            "threshold_origin",
            "seed_ensemble_definition",
            "posthoc_b06_substitution_used",
            "result_based_change_allowed",
        },
        "prereg B03",
    )
    _expect(baseline.get("seeds"), list(SEEDS), "prereg B03 seeds")
    _expect(
        baseline.get("checkpoint_set_sha256"),
        B03_CHECKPOINT_SET_SHA256,
        "prereg checkpoints",
    )
    _expect(
        baseline.get("feature_allowlist_sha256"),
        B03_FEATURE_ALLOWLIST_SHA256,
        "prereg feature allowlist",
    )
    _expect(baseline.get("phone_decision_threshold"), 0.5, "prereg threshold")
    _expect(
        baseline.get("threshold_origin"),
        "b03_sklearn_predict_default_not_b06_posthoc",
        "prereg threshold origin",
    )
    _expect(
        baseline.get("seed_ensemble_definition"),
        "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean",
        "prereg seed definition",
    )
    _expect(baseline.get("posthoc_b06_substitution_used"), False, "prereg B06")
    _expect(baseline.get("result_based_change_allowed"), False, "prereg mutation")

    arm = _mapping(payload.get("s108_arm"), "prereg S108 arm")
    _expect_keys(
        arm,
        {
            "arm_freeze_sha256",
            "model_identity_sha256",
            "checkpoint_sha256",
            "calibrator_sha256",
            "threshold_policy_sha256",
            "execution_spec_sha256",
            "result_based_change_allowed",
        },
        "prereg S108 arm",
    )
    for field, freeze_field in {
        "arm_freeze_sha256": None,
        "model_identity_sha256": "s108_model_identity_sha256",
        "checkpoint_sha256": "s108_checkpoint_sha256",
        "calibrator_sha256": "s108_calibrator_sha256",
        "threshold_policy_sha256": "s108_threshold_policy_sha256",
        "execution_spec_sha256": "s108_execution_spec_sha256",
    }.items():
        expected = (
            s108_freeze_sha256 if freeze_field is None else s108_freeze[freeze_field]
        )
        _expect(arm.get(field), expected, f"prereg S108 {field}")
    _expect(arm.get("result_based_change_allowed"), False, "prereg S108 mutation")

    batch = _mapping(payload.get("joint_batch"), "prereg joint batch")
    _expect_keys(
        batch,
        {
            "included_arms",
            "same_locked_batch_manifest_required",
            "both_arms_immutable_before_access",
            "first_arm_result_hidden_until_both_arms_sealed",
            "authorized_locked_access_count",
            "rerun_after_success_allowed",
            "rerun_after_failure_allowed",
            "new_independent_test_set_required_for_any_new_run",
            "joint_atomic_publication_required",
        },
        "prereg joint batch",
    )
    _expect(batch.get("included_arms"), list(ARMS), "prereg arms")
    for field in (
        "same_locked_batch_manifest_required",
        "both_arms_immutable_before_access",
        "first_arm_result_hidden_until_both_arms_sealed",
        "new_independent_test_set_required_for_any_new_run",
        "joint_atomic_publication_required",
    ):
        _expect(batch.get(field), True, f"prereg batch {field}")
    for field in ("rerun_after_success_allowed", "rerun_after_failure_allowed"):
        _expect(batch.get(field), False, f"prereg batch {field}")
    _expect(batch.get("authorized_locked_access_count"), 1, "prereg access")

    analysis = _mapping(payload.get("analysis_plan"), "prereg analysis")
    analysis_keys = {
        "locked_population_definition_sha256",
        "phone_confusion_definition_sha256",
        "utterance_ordinal_confusion_definition_sha256",
        "slice_family_definition_sha256",
        "slice_axes",
        "speaker_cluster_bootstrap_method",
        "bootstrap_repetitions",
        "confidence_level",
        "multiple_comparison_correction",
        "holm_family_definition_sha256",
        "missing_data_policy_sha256",
        "undefined_metric_policy_sha256",
        "minimum_support_policy_sha256",
        "minimum_support_thresholds",
        "primary_estimands_sha256",
        "slice_result_keys",
        "bootstrap_result_keys",
        "holm_hypotheses",
        "cluster_unit",
    }
    _expect_keys(analysis, analysis_keys, "prereg analysis")
    for field in analysis_keys:
        if field.endswith("_sha256"):
            _sha256(analysis.get(field), f"prereg analysis {field}")
    method = _string(
        analysis.get("speaker_cluster_bootstrap_method"),
        "prereg bootstrap method",
    )
    if re.fullmatch(r"[a-z0-9_.-]+", method) is None:
        raise B05ContractError("invalid preregistered bootstrap method")
    _expect(analysis.get("slice_axes"), list(SLICE_AXES), "prereg slice axes")
    _expect(analysis.get("bootstrap_repetitions"), 5000, "prereg bootstrap reps")
    _expect(analysis.get("confidence_level"), 0.95, "prereg confidence")
    _expect(
        analysis.get("multiple_comparison_correction"),
        "holm",
        "prereg multiplicity",
    )
    _expect(analysis.get("cluster_unit"), "speaker_id_sha256", "prereg cluster")
    thresholds = _mapping(
        analysis.get("minimum_support_thresholds"),
        "prereg minimum support thresholds",
    )
    threshold_keys = {
        "phone_positive_count",
        "phone_negative_count",
        "utterance_row_count",
        "slice_speaker_count",
        "slice_row_count",
    }
    _expect_keys(thresholds, threshold_keys, "prereg minimum support thresholds")
    for field in threshold_keys:
        _integer(
            thresholds.get(field),
            f"prereg minimum support {field}",
            1,
        )

    slice_keys = _list(analysis.get("slice_result_keys"), "prereg slice result keys")
    if not slice_keys:
        raise B05ContractError("prereg slice result keys must not be empty")
    observed_slice_keys: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(slice_keys):
        key = _mapping(raw, f"prereg slice key {index}")
        _expect_keys(
            key,
            {
                "axis",
                "group_token_sha256",
                "unit",
                "metric_name",
                "missing_allowed",
            },
            f"prereg slice key {index}",
        )
        axis = _string(key.get("axis"), f"prereg slice key {index} axis")
        if axis not in SLICE_AXES:
            raise B05ContractError(f"invalid prereg slice axis: {axis}")
        group = _sha256(key.get("group_token_sha256"), "prereg slice group")
        unit = _string(key.get("unit"), "prereg slice unit")
        if unit not in {"phone", "utterance"}:
            raise B05ContractError(f"invalid prereg slice unit: {unit}")
        metric = _string(key.get("metric_name"), "prereg slice metric")
        if metric not in {"macro_f1", "auprc", "qwk", "spearman", "mae", "icc_2_1"}:
            raise B05ContractError(f"invalid prereg slice metric: {metric}")
        if not isinstance(key.get("missing_allowed"), bool):
            raise B05ContractError("prereg slice missing_allowed must be boolean")
        identity = (axis, group, unit, metric)
        if identity in observed_slice_keys:
            raise B05ContractError(f"duplicate prereg slice key: {identity}")
        observed_slice_keys.add(identity)

    bootstrap_keys = _list(
        analysis.get("bootstrap_result_keys"),
        "prereg bootstrap result keys",
    )
    if not bootstrap_keys:
        raise B05ContractError("prereg bootstrap result keys must not be empty")
    observed_bootstrap_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(bootstrap_keys):
        key = _mapping(raw, f"prereg bootstrap key {index}")
        _expect_keys(
            key,
            {"estimand_id_sha256", "metric_name"},
            f"prereg bootstrap key {index}",
        )
        bootstrap_identity = (
            _sha256(key.get("estimand_id_sha256"), "prereg bootstrap estimand"),
            _string(key.get("metric_name"), "prereg bootstrap metric"),
        )
        if bootstrap_identity in observed_bootstrap_keys:
            raise B05ContractError(
                f"duplicate prereg bootstrap key: {bootstrap_identity}"
            )
        observed_bootstrap_keys.add(bootstrap_identity)

    hypotheses = _list(analysis.get("holm_hypotheses"), "prereg Holm hypotheses")
    if not hypotheses:
        raise B05ContractError("prereg Holm hypotheses must not be empty")
    observed_hypotheses: set[tuple[str, str]] = set()
    family_counts: dict[str, int] = {}
    family_sizes: dict[str, int] = {}
    for index, raw in enumerate(hypotheses):
        hypothesis = _mapping(raw, f"prereg Holm hypothesis {index}")
        _expect_keys(
            hypothesis,
            {"family_id_sha256", "hypothesis_id_sha256", "family_size", "alpha"},
            f"prereg Holm hypothesis {index}",
        )
        family = _sha256(hypothesis.get("family_id_sha256"), "prereg Holm family")
        hypothesis_id = _sha256(
            hypothesis.get("hypothesis_id_sha256"),
            "prereg Holm hypothesis",
        )
        hypothesis_identity = (family, hypothesis_id)
        if hypothesis_identity in observed_hypotheses:
            raise B05ContractError(
                f"duplicate prereg Holm hypothesis: {hypothesis_identity}"
            )
        observed_hypotheses.add(hypothesis_identity)
        size = _integer(hypothesis.get("family_size"), "prereg Holm family size", 1)
        _expect(
            size,
            family_sizes.setdefault(family, size),
            "prereg Holm family size consistency",
        )
        alpha = _number(hypothesis.get("alpha"), "prereg Holm alpha")
        if not 0 < alpha < 1:
            raise B05ContractError("prereg Holm alpha must be in (0,1)")
        family_counts[family] = family_counts.get(family, 0) + 1
    for family, size in family_sizes.items():
        _expect(
            family_counts[family],
            size,
            f"prereg Holm family {family} exact count",
        )

    approvals = _mapping(payload.get("approvals"), "prereg approvals")
    _expect_keys(
        approvals,
        {
            "protocol_owner_approved",
            "statistics_owner_approved",
            "privacy_owner_approved",
            "signed_protocol_receipt_sha256",
            "signed_statistics_receipt_sha256",
            "signed_privacy_receipt_sha256",
        },
        "prereg approvals",
    )
    for field in (
        "protocol_owner_approved",
        "statistics_owner_approved",
        "privacy_owner_approved",
    ):
        _expect(approvals.get(field), True, f"prereg approval {field}")
    for field in (
        "signed_protocol_receipt_sha256",
        "signed_statistics_receipt_sha256",
        "signed_privacy_receipt_sha256",
    ):
        _sha256(approvals.get(field), f"prereg approval {field}")

    privacy = _mapping(payload.get("privacy_and_claims"), "prereg privacy")
    _expect_keys(
        privacy,
        {
            "contains_identifiers",
            "contains_rows",
            "contains_audio",
            "contains_labels",
            "contains_predictions",
            "contains_locked_results",
            "b05_completion_claim_allowed_before_joint_receipt",
            "s108_completion_claim_allowed_before_joint_receipt",
            "deployment_claim_allowed",
        },
        "prereg privacy",
    )
    for field in privacy:
        _expect(privacy.get(field), False, f"prereg privacy {field}")
    _validate_no_tbd(payload, "B05 preregistration")
    _validate_binding(payload, "B05 preregistration")
    return payload


BATCH_KEYS = {
    "schema_version",
    "batch_id",
    "status",
    "preregistration_sha256",
    "g04_completion_sha256",
    "d07_completion_sha256",
    "locked_split_manifest_sha256",
    "locked_set_identity_sha256",
    "locked_utterance_count",
    "b03_config_sha256",
    "b03_report_sha256",
    "b03_registry_sha256",
    "b03_checkpoint_set_sha256",
    "b03_feature_allowlist_sha256",
    "b03_phone_decision_threshold",
    "b03_seed_ensemble_definition",
    "s107_commit_sha256",
    "s108_arm_freeze_sha256",
    "arm_execution_specs",
    "execution_order",
    "both_arms_immutable_before_access",
    "result_visibility_embargo_until_both_sealed",
    "prior_joint_locked_access_count",
    "maximum_joint_locked_access_count",
    "rerun_after_success_allowed",
    "rerun_after_failure_allowed",
    "new_independent_test_set_required_for_any_new_run",
    "contains_locked_rows",
    "contains_audio_or_locator",
    "contains_labels",
    "contains_predictions",
    "privacy_zone",
    "binding_sha256",
}


def validate_batch_manifest_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
    g04_sha256: str,
    d07_sha256: str,
    s107_sha256: str,
    s108_freeze_sha256: str,
    s108_freeze: Mapping[str, Any],
) -> Mapping[str, Any]:
    _expect_keys(payload, BATCH_KEYS, "joint batch manifest")
    _expect(payload.get("schema_version"), BATCH_SCHEMA_VERSION, "batch schema")
    if re.fullmatch(r"b05batch_[0-9a-f]{32}", str(payload.get("batch_id"))) is None:
        raise B05ContractError("invalid joint batch ID")
    _expect(payload.get("status"), "frozen_authorized_not_opened", "batch status")
    for field, expected in {
        "preregistration_sha256": preregistration_sha256,
        "g04_completion_sha256": g04_sha256,
        "d07_completion_sha256": d07_sha256,
        "b03_config_sha256": B03_CONFIG_SHA256,
        "b03_report_sha256": B03_REPORT_SHA256,
        "b03_registry_sha256": B03_REGISTRY_SHA256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "b03_feature_allowlist_sha256": B03_FEATURE_ALLOWLIST_SHA256,
        "s107_commit_sha256": s107_sha256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
    }.items():
        _expect(payload.get(field), expected, f"batch {field}")
    _sha256(payload.get("locked_split_manifest_sha256"), "batch split manifest")
    _sha256(payload.get("locked_set_identity_sha256"), "batch set identity")
    _integer(payload.get("locked_utterance_count"), "batch utterance count", 1)
    _expect(payload.get("b03_phone_decision_threshold"), 0.5, "batch threshold")
    _expect(
        payload.get("b03_seed_ensemble_definition"),
        "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean",
        "batch seed definition",
    )
    specs = _list(payload.get("arm_execution_specs"), "batch arm specs")
    if len(specs) != 2:
        raise B05ContractError("batch must contain exactly two arm specs")
    expected_specs = (
        {
            "arm_id": ARMS[0],
            "immutable_spec_sha256": B03_CONFIG_SHA256,
            "checkpoint_set_or_model_sha256": B03_CHECKPOINT_SET_SHA256,
            "decision_policy_sha256": B03_FEATURE_ALLOWLIST_SHA256,
            "result_based_mutation_allowed": False,
        },
        {
            "arm_id": ARMS[1],
            "immutable_spec_sha256": s108_freeze["s108_execution_spec_sha256"],
            "checkpoint_set_or_model_sha256": s108_freeze["s108_model_identity_sha256"],
            "decision_policy_sha256": s108_freeze["s108_threshold_policy_sha256"],
            "result_based_mutation_allowed": False,
        },
    )
    for index, raw in enumerate(specs):
        spec = _mapping(raw, f"batch arm {index}")
        _expect_keys(spec, set(expected_specs[index]), f"batch arm {index}")
        _expect(dict(spec), expected_specs[index], f"batch arm {index}")
    arm_ids = [spec.get("arm_id") for spec in specs]
    if arm_ids != list(ARMS) or len(set(arm_ids)) != 2:
        raise B05ContractError("batch arm set/order must be exactly B05 then S108")
    _expect(payload.get("execution_order"), list(ARMS), "batch execution order")
    for field in (
        "both_arms_immutable_before_access",
        "result_visibility_embargo_until_both_sealed",
        "new_independent_test_set_required_for_any_new_run",
    ):
        _expect(payload.get(field), True, f"batch {field}")
    for field in (
        "rerun_after_success_allowed",
        "rerun_after_failure_allowed",
        "contains_locked_rows",
        "contains_audio_or_locator",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"batch {field}")
    _expect(payload.get("prior_joint_locked_access_count"), 0, "batch prior access")
    _expect(payload.get("maximum_joint_locked_access_count"), 1, "batch max access")
    _expect(
        payload.get("privacy_zone"),
        "D1_access_controlled_batch_control_no_locator",
        "batch privacy",
    )
    _validate_no_tbd(payload, "joint batch manifest")
    _validate_binding(payload, "joint batch manifest")
    return payload


AUTH_KEYS = {
    "schema_version",
    "authorization_id",
    "status",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "g04_completion_sha256",
    "d07_completion_sha256",
    "s107_commit_sha256",
    "s108_arm_freeze_sha256",
    "one_shot_transaction_id_sha256",
    "prior_joint_locked_access_count",
    "authorized_joint_locked_access_count",
    "authorized_arms",
    "both_arms_must_use_same_transaction",
    "first_arm_result_visibility_before_both_sealed",
    "failure_consumes_authorization",
    "rerun_authorized",
    "new_independent_test_set_required_for_new_authorization",
    "protocol_owner_signed_receipt_sha256",
    "data_custodian_signed_receipt_sha256",
    "privacy_zone",
    "binding_sha256",
}


def validate_authorization_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    g04_sha256: str,
    d07_sha256: str,
    s107_sha256: str,
    s108_freeze_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, AUTH_KEYS, "one-shot authorization")
    _expect(payload.get("schema_version"), AUTH_SCHEMA_VERSION, "authorization schema")
    if (
        re.fullmatch(r"b05auth_[0-9a-f]{32}", str(payload.get("authorization_id")))
        is None
    ):
        raise B05ContractError("invalid authorization ID")
    _expect(
        payload.get("status"),
        "authorized_one_shot_joint_batch",
        "authorization status",
    )
    for field, expected in {
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "g04_completion_sha256": g04_sha256,
        "d07_completion_sha256": d07_sha256,
        "s107_commit_sha256": s107_sha256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
    }.items():
        _expect(payload.get(field), expected, f"authorization {field}")
    _sha256(payload.get("one_shot_transaction_id_sha256"), "authorization tx")
    _expect(payload.get("prior_joint_locked_access_count"), 0, "authorization prior")
    _expect(
        payload.get("authorized_joint_locked_access_count"),
        1,
        "authorization access",
    )
    _expect(payload.get("authorized_arms"), list(ARMS), "authorization arms")
    _expect(
        payload.get("both_arms_must_use_same_transaction"),
        True,
        "authorization transaction",
    )
    _expect(
        payload.get("first_arm_result_visibility_before_both_sealed"),
        False,
        "authorization embargo",
    )
    _expect(
        payload.get("failure_consumes_authorization"),
        True,
        "authorization failure",
    )
    _expect(payload.get("rerun_authorized"), False, "authorization rerun")
    _expect(
        payload.get("new_independent_test_set_required_for_new_authorization"),
        True,
        "authorization new set",
    )
    for field in (
        "protocol_owner_signed_receipt_sha256",
        "data_custodian_signed_receipt_sha256",
    ):
        _sha256(payload.get(field), f"authorization {field}")
    _expect(
        payload.get("privacy_zone"),
        "C1_public_authorization_no_locator",
        "authorization privacy",
    )
    _validate_no_tbd(payload, "one-shot authorization")
    _validate_binding(payload, "one-shot authorization")
    return payload


PREDICTION_KEYS = {
    "schema_version",
    "prediction_manifest_id",
    "status",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "authorization_sha256",
    "one_shot_transaction_id_sha256",
    "b03_config_sha256",
    "b03_report_sha256",
    "b03_registry_sha256",
    "b03_checkpoint_set_sha256",
    "b03_feature_allowlist_sha256",
    "phone_decision_threshold",
    "seed_ensemble_definition",
    "split",
    "alignment_role",
    "seeds",
    "storage_format",
    "phone_row_schema",
    "utterance_row_schema",
    "shards",
    "phone_row_count",
    "utterance_row_count",
    "gold_labels_embedded",
    "raw_audio_or_locator_embedded",
    "direct_identifiers_embedded",
    "free_text_embedded",
    "publication_allowed",
    "privacy_zone",
    "binding_sha256",
}
PHONE_ROW_SCHEMA = (
    "row_id_sha256:string",
    "utterance_id_sha256:string",
    "speaker_id_sha256:string",
    "target_phone_index:int64",
    "seed:int64",
    "error_probability:float64",
    "predicted_binary_error:bool",
)
UTTERANCE_ROW_SCHEMA = (
    "row_id_sha256:string",
    "utterance_id_sha256:string",
    "speaker_id_sha256:string",
    "outcome:string",
    "seed:int64",
    "predicted_score:float64",
)


def validate_prediction_manifest_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    authorization_sha256: str,
    one_shot_transaction_id_sha256: str,
) -> Mapping[str, Any]:
    """Validate the D1 Parquet shard manifest without opening any shard."""

    _expect_keys(payload, PREDICTION_KEYS, "B05 prediction manifest")
    _expect(
        payload.get("schema_version"),
        PREDICTION_SCHEMA_VERSION,
        "prediction schema",
    )
    if (
        re.fullmatch(
            r"b05pred_[0-9a-f]{32}", str(payload.get("prediction_manifest_id"))
        )
        is None
    ):
        raise B05ContractError("invalid B05 prediction manifest ID")
    _expect(
        payload.get("status"),
        "sealed_before_joint_result_release",
        "prediction status",
    )
    for field, expected in {
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "authorization_sha256": authorization_sha256,
        "one_shot_transaction_id_sha256": one_shot_transaction_id_sha256,
        "b03_config_sha256": B03_CONFIG_SHA256,
        "b03_report_sha256": B03_REPORT_SHA256,
        "b03_registry_sha256": B03_REGISTRY_SHA256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "b03_feature_allowlist_sha256": B03_FEATURE_ALLOWLIST_SHA256,
        "phone_decision_threshold": 0.5,
        "seed_ensemble_definition": (
            "no_prediction_ensemble_independent_seed_metrics_then_arithmetic_mean"
        ),
        "split": "locked_test",
        "alignment_role": "oracle_target_boundaries_locked_research_only",
        "seeds": list(SEEDS),
        "storage_format": "parquet_shards",
        "phone_row_schema": list(PHONE_ROW_SCHEMA),
        "utterance_row_schema": list(UTTERANCE_ROW_SCHEMA),
    }.items():
        _expect(payload.get(field), expected, f"prediction {field}")

    shards = _list(payload.get("shards"), "prediction shards")
    if len(shards) < 12:
        raise B05ContractError("prediction manifest requires at least 12 shards")
    combinations: dict[tuple[str, int, str | None], list[int]] = {}
    paths: set[str] = set()
    phone_rows = 0
    utterance_rows = 0
    for index, raw in enumerate(shards):
        shard = _mapping(raw, f"prediction shard {index}")
        _expect_keys(
            shard,
            {
                "task",
                "outcome",
                "seed",
                "shard_index",
                "relative_path",
                "sha256",
                "row_count",
            },
            f"prediction shard {index}",
        )
        task = _string(shard.get("task"), f"prediction shard {index} task")
        if task not in {"phone_binary_error", "utterance_ordinal_score"}:
            raise B05ContractError(f"invalid prediction task: {task}")
        seed = _integer(shard.get("seed"), f"prediction shard {index} seed")
        if seed not in SEEDS:
            raise B05ContractError(f"invalid prediction seed: {seed}")
        outcome_raw = shard.get("outcome")
        if task == "phone_binary_error":
            _expect(outcome_raw, None, f"prediction shard {index} outcome")
            outcome: str | None = None
        else:
            outcome = _string(outcome_raw, f"prediction shard {index} outcome")
            if outcome not in OUTCOMES:
                raise B05ContractError(f"invalid utterance outcome: {outcome}")
        shard_index = _integer(
            shard.get("shard_index"),
            f"prediction shard {index} index",
        )
        relative = _relative_string(
            shard.get("relative_path"),
            f"prediction shard {index} path",
        )
        if not relative.endswith(".parquet"):
            raise B05ContractError("prediction shard must be Parquet")
        if relative in paths:
            raise B05ContractError(f"duplicate prediction shard path: {relative}")
        paths.add(relative)
        _sha256(shard.get("sha256"), f"prediction shard {index} SHA")
        count = _integer(
            shard.get("row_count"),
            f"prediction shard {index} rows",
            1,
        )
        combination = (task, seed, outcome)
        combinations.setdefault(combination, []).append(shard_index)
        if task == "phone_binary_error":
            phone_rows += count
        else:
            utterance_rows += count
    required_combinations: set[tuple[str, int, str | None]] = {
        ("phone_binary_error", seed, None) for seed in SEEDS
    } | {
        ("utterance_ordinal_score", seed, outcome)
        for seed in SEEDS
        for outcome in OUTCOMES
    }
    observed_combination_keys = set(combinations)
    if observed_combination_keys != required_combinations:
        raise B05ContractError(
            "prediction seed/outcome combinations differ: "
            f"missing={sorted(required_combinations - observed_combination_keys)}, "
            f"extra={sorted(observed_combination_keys - required_combinations)}"
        )
    for combination, indices in combinations.items():
        if len(indices) != len(set(indices)):
            raise B05ContractError(f"duplicate shard index for {combination}")
        if sorted(indices) != list(range(len(indices))):
            raise B05ContractError(f"non-contiguous shard indices for {combination}")
    _expect(payload.get("phone_row_count"), phone_rows, "prediction phone rows")
    _expect(
        payload.get("utterance_row_count"),
        utterance_rows,
        "prediction utterance rows",
    )
    for field in (
        "gold_labels_embedded",
        "raw_audio_or_locator_embedded",
        "direct_identifiers_embedded",
        "free_text_embedded",
        "publication_allowed",
    ):
        _expect(payload.get(field), False, f"prediction {field}")
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_locked_predictions",
        "prediction privacy",
    )
    _validate_no_tbd(payload, "B05 prediction manifest")
    _validate_binding(payload, "B05 prediction manifest")
    return payload


REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "config_sha256",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "authorization_sha256",
    "one_shot_transaction_id_sha256",
    "b03_config_sha256",
    "b03_report_sha256",
    "b03_registry_sha256",
    "b03_checkpoint_set_sha256",
    "b03_feature_allowlist_sha256",
    "s108_arm_freeze_sha256",
    "locked_predictions_sha256",
    "locked_utterance_count",
    "locked_phone_count",
    "seeds",
    "phone_seed_results",
    "utterance_seed_results",
    "slice_results",
    "speaker_cluster_bootstrap_results",
    "holm_results",
    "analysis_policy_bindings",
    "one_shot_assurance",
    "privacy_and_claims",
    "binding_sha256",
}
ANALYSIS_SHA_FIELDS = {
    "locked_population_definition_sha256",
    "phone_confusion_definition_sha256",
    "utterance_ordinal_confusion_definition_sha256",
    "slice_family_definition_sha256",
    "holm_family_definition_sha256",
    "missing_data_policy_sha256",
    "undefined_metric_policy_sha256",
    "minimum_support_policy_sha256",
    "primary_estimands_sha256",
}


def _optional_finite(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _number(value, name)


def _validate_metric_status(
    status: object,
    values: Sequence[object],
    name: str,
) -> None:
    if status == "estimable":
        if any(value is None for value in values):
            raise B05ContractError(f"{name} estimable metrics cannot be null")
    elif status == "undefined_per_preregistered_policy":
        if any(value is not None for value in values):
            raise B05ContractError(f"{name} undefined metrics must all be null")
    else:
        raise B05ContractError(f"{name} has invalid metric status")


def validate_aggregate_report_payload(
    payload: Mapping[str, Any],
    *,
    config_sha256: str,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    authorization_sha256: str,
    one_shot_transaction_id_sha256: str,
    s108_freeze_sha256: str,
    locked_predictions_sha256: str,
    preregistration: Mapping[str, Any],
    prediction_manifest: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Validate B05 results without any cross-arm report or receipt back-edge."""

    _reject_nonfinite_tree(payload, "B05 aggregate report")
    _expect_keys(payload, REPORT_KEYS, "B05 aggregate report")
    _expect(payload.get("schema_version"), REPORT_SCHEMA_VERSION, "report schema")
    if re.fullmatch(r"b05report_[0-9a-f]{32}", str(payload.get("report_id"))) is None:
        raise B05ContractError("invalid B05 report ID")
    _expect(
        payload.get("status"),
        "completed_joint_one_shot_locked_evaluation",
        "report status",
    )
    for field, expected in {
        "config_sha256": config_sha256,
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "authorization_sha256": authorization_sha256,
        "one_shot_transaction_id_sha256": one_shot_transaction_id_sha256,
        "b03_config_sha256": B03_CONFIG_SHA256,
        "b03_report_sha256": B03_REPORT_SHA256,
        "b03_registry_sha256": B03_REGISTRY_SHA256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "b03_feature_allowlist_sha256": B03_FEATURE_ALLOWLIST_SHA256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
        "locked_predictions_sha256": locked_predictions_sha256,
        "seeds": list(SEEDS),
    }.items():
        _expect(payload.get(field), expected, f"report {field}")
    utterance_count = _integer(
        payload.get("locked_utterance_count"),
        "report utterance count",
        1,
    )
    phone_count = _integer(payload.get("locked_phone_count"), "report phone count", 1)
    if prediction_manifest is not None:
        _expect(
            prediction_manifest.get("phone_row_count"),
            phone_count * len(SEEDS),
            "prediction/report phone count",
        )
        utterance_rows = _integer(
            prediction_manifest.get("utterance_row_count"),
            "prediction utterance count",
        )
        if utterance_rows > utterance_count * len(SEEDS) * len(OUTCOMES):
            raise B05ContractError("prediction utterance rows exceed maximum")

    analysis = _mapping(preregistration.get("analysis_plan"), "prereg analysis")
    thresholds = _mapping(
        analysis.get("minimum_support_thresholds"),
        "preregistered minimum support thresholds",
    )
    phone_positive_minimum = _integer(
        thresholds.get("phone_positive_count"),
        "phone positive minimum",
        1,
    )
    phone_negative_minimum = _integer(
        thresholds.get("phone_negative_count"),
        "phone negative minimum",
        1,
    )
    utterance_minimum = _integer(
        thresholds.get("utterance_row_count"),
        "utterance row minimum",
        1,
    )
    slice_speaker_minimum = _integer(
        thresholds.get("slice_speaker_count"),
        "slice speaker minimum",
        1,
    )
    slice_row_minimum = _integer(
        thresholds.get("slice_row_count"),
        "slice row minimum",
        1,
    )

    phone_results = _list(payload.get("phone_seed_results"), "phone results")
    if len(phone_results) != len(SEEDS):
        raise B05ContractError("phone results must contain exactly three seeds")
    observed_phone_seeds: list[int] = []
    for index, raw in enumerate(phone_results):
        result = _mapping(raw, f"phone result {index}")
        _expect_keys(
            result,
            {
                "seed",
                "true_negative_count",
                "false_positive_count",
                "false_negative_count",
                "true_positive_count",
                "negative_support",
                "positive_support",
                "metric_status",
                "macro_f1",
                "auprc",
            },
            f"phone result {index}",
        )
        seed = _integer(result.get("seed"), f"phone result {index} seed")
        observed_phone_seeds.append(seed)
        tn = _integer(result.get("true_negative_count"), "true negative")
        fp = _integer(result.get("false_positive_count"), "false positive")
        fn = _integer(result.get("false_negative_count"), "false negative")
        tp = _integer(result.get("true_positive_count"), "true positive")
        _expect(result.get("negative_support"), tn + fp, "negative support")
        _expect(result.get("positive_support"), fn + tp, "positive support")
        _expect(tn + fp + fn + tp, phone_count, "phone confusion support")
        macro = _optional_finite(result.get("macro_f1"), "phone macro F1")
        auprc = _optional_finite(result.get("auprc"), "phone AUPRC")
        for name, value in (("macro F1", macro), ("AUPRC", auprc)):
            if value is not None and not 0 <= value <= 1:
                raise B05ContractError(f"phone {name} must be in [0,1]")
        _validate_metric_status(
            result.get("metric_status"),
            (result.get("macro_f1"), result.get("auprc")),
            f"phone result {index}",
        )
        expected_phone_status = (
            "estimable"
            if fn + tp >= phone_positive_minimum and tn + fp >= phone_negative_minimum
            else "undefined_per_preregistered_policy"
        )
        _expect(
            result.get("metric_status"),
            expected_phone_status,
            f"phone result {index} support policy",
        )
    _expect(tuple(observed_phone_seeds), SEEDS, "phone seed order")
    if len(set(observed_phone_seeds)) != len(SEEDS):
        raise B05ContractError("duplicate phone seed result")

    utterance_results = _list(
        payload.get("utterance_seed_results"),
        "utterance results",
    )
    if len(utterance_results) != len(SEEDS) * len(OUTCOMES):
        raise B05ContractError("utterance results must contain exact 3x3 combinations")
    observed_combinations: list[tuple[int, str]] = []
    expected_order = [(seed, outcome) for seed in SEEDS for outcome in OUTCOMES]
    for index, raw in enumerate(utterance_results):
        result = _mapping(raw, f"utterance result {index}")
        _expect_keys(
            result,
            {
                "seed",
                "outcome",
                "support",
                "metric_status",
                "ordinal_confusion_matrix",
                "metrics",
            },
            f"utterance result {index}",
        )
        seed = _integer(result.get("seed"), f"utterance result {index} seed")
        outcome = _string(result.get("outcome"), f"utterance result {index} outcome")
        if seed not in SEEDS or outcome not in OUTCOMES:
            raise B05ContractError("invalid utterance seed/outcome")
        observed_combinations.append((seed, outcome))
        support = _integer(result.get("support"), "utterance support")
        if support > utterance_count:
            raise B05ContractError("utterance support exceeds locked population")
        matrix = _list(result.get("ordinal_confusion_matrix"), "ordinal matrix")
        if len(matrix) != 5:
            raise B05ContractError("ordinal confusion matrix must be 5x5")
        matrix_sum = 0
        for row_index, raw_row in enumerate(matrix):
            row = _list(raw_row, f"ordinal matrix row {row_index}")
            if len(row) != 5:
                raise B05ContractError("ordinal confusion matrix must be 5x5")
            matrix_sum += sum(
                _integer(cell, f"ordinal matrix [{row_index}] cell") for cell in row
            )
        _expect(matrix_sum, support, "utterance matrix support")
        metrics = _mapping(result.get("metrics"), "utterance metrics")
        _expect_keys(metrics, {"qwk", "spearman", "mae", "icc_2_1"}, "metrics")
        values = tuple(metrics.values())
        for name, value in metrics.items():
            observed = _optional_finite(value, f"utterance {name}")
            if observed is not None:
                if name in {"qwk", "spearman", "icc_2_1"} and not -1 <= observed <= 1:
                    raise B05ContractError(f"utterance {name} must be in [-1,1]")
                if name == "mae" and observed < 0:
                    raise B05ContractError("utterance MAE must be nonnegative")
        _validate_metric_status(
            result.get("metric_status"),
            values,
            f"utterance result {index}",
        )
        expected_utterance_status = (
            "estimable"
            if support >= utterance_minimum
            else "undefined_per_preregistered_policy"
        )
        _expect(
            result.get("metric_status"),
            expected_utterance_status,
            f"utterance result {index} support policy",
        )
    _expect(observed_combinations, expected_order, "utterance result order")
    if len(set(observed_combinations)) != len(expected_order):
        raise B05ContractError("duplicate utterance seed/outcome result")

    expected_slice_items = _list(
        analysis.get("slice_result_keys"),
        "preregistered slice result keys",
    )
    if not expected_slice_items:
        raise B05ContractError("preregistered slice result keys must not be empty")
    expected_slice_keys: list[tuple[str, str, str, str]] = []
    slice_missing_allowed: dict[tuple[str, str, str, str], bool] = {}
    for index, raw in enumerate(expected_slice_items):
        expected_item = _mapping(raw, f"preregistered slice key {index}")
        expected_key = (
            _string(expected_item.get("axis"), "preregistered slice axis"),
            _sha256(
                expected_item.get("group_token_sha256"),
                "preregistered slice group",
            ),
            _string(expected_item.get("unit"), "preregistered slice unit"),
            _string(expected_item.get("metric_name"), "preregistered slice metric"),
        )
        if expected_key in slice_missing_allowed:
            raise B05ContractError(f"duplicate preregistered slice key: {expected_key}")
        missing_allowed = expected_item.get("missing_allowed")
        if not isinstance(missing_allowed, bool):
            raise B05ContractError("slice missing_allowed must be boolean")
        expected_slice_keys.append(expected_key)
        slice_missing_allowed[expected_key] = missing_allowed
    slices = _list(payload.get("slice_results"), "slice results")
    observed_slice_keys: list[tuple[str, str, str, str]] = []
    for index, raw in enumerate(slices):
        result = _mapping(raw, f"slice result {index}")
        _expect_keys(
            result,
            {
                "axis",
                "group_token_sha256",
                "unit",
                "metric_name",
                "speaker_support",
                "row_support",
                "positive_support",
                "negative_support",
                "status",
                "estimate",
            },
            f"slice result {index}",
        )
        axis = _string(result.get("axis"), f"slice result {index} axis")
        if axis not in SLICE_AXES:
            raise B05ContractError(f"invalid slice axis: {axis}")
        group = _sha256(result.get("group_token_sha256"), "slice group")
        unit = _string(result.get("unit"), "slice unit")
        metric = _string(result.get("metric_name"), "slice metric")
        slice_key = (axis, group, unit, metric)
        if slice_key in observed_slice_keys:
            raise B05ContractError(f"duplicate slice result: {slice_key}")
        observed_slice_keys.append(slice_key)
        speaker_support = _integer(
            result.get("speaker_support"),
            "slice speaker support",
        )
        row_support = _integer(result.get("row_support"), "slice row support")
        _integer(result.get("positive_support"), "slice positive support")
        _integer(result.get("negative_support"), "slice negative support")
        estimate = _optional_finite(result.get("estimate"), "slice estimate")
        status = result.get("status")
        if status == "estimable" and estimate is None:
            raise B05ContractError("estimable slice must have an estimate")
        if (
            status
            in {
                "undefined_below_support",
                "missing_per_preregistered_policy",
            }
            and estimate is not None
        ):
            raise B05ContractError("undefined/missing slice estimate must be null")
        if status not in {
            "estimable",
            "undefined_below_support",
            "missing_per_preregistered_policy",
        }:
            raise B05ContractError("invalid slice status")
        if status == "missing_per_preregistered_policy":
            if not slice_missing_allowed.get(slice_key, False):
                raise B05ContractError(f"unapproved missing slice result: {slice_key}")
        else:
            expected_slice_status = (
                "estimable"
                if speaker_support >= slice_speaker_minimum
                and row_support >= slice_row_minimum
                else "undefined_below_support"
            )
            _expect(
                status,
                expected_slice_status,
                f"slice {slice_key} support policy",
            )
    _expect(
        observed_slice_keys,
        expected_slice_keys,
        "exact preregistered slice result keys",
    )

    expected_bootstrap_items = _list(
        analysis.get("bootstrap_result_keys"),
        "preregistered bootstrap result keys",
    )
    if not expected_bootstrap_items:
        raise B05ContractError("preregistered bootstrap result keys must not be empty")
    expected_bootstrap_keys = [
        (
            _sha256(
                _mapping(raw, f"preregistered bootstrap key {index}").get(
                    "estimand_id_sha256"
                ),
                "preregistered bootstrap estimand",
            ),
            _string(
                _mapping(raw, f"preregistered bootstrap key {index}").get(
                    "metric_name"
                ),
                "preregistered bootstrap metric",
            ),
        )
        for index, raw in enumerate(expected_bootstrap_items)
    ]
    if len(expected_bootstrap_keys) != len(set(expected_bootstrap_keys)):
        raise B05ContractError("duplicate preregistered bootstrap result key")
    bootstraps = _list(
        payload.get("speaker_cluster_bootstrap_results"),
        "bootstrap results",
    )
    observed_bootstrap_keys: list[tuple[str, str]] = []
    for index, raw in enumerate(bootstraps):
        result = _mapping(raw, f"bootstrap result {index}")
        _expect_keys(
            result,
            {
                "estimand_id_sha256",
                "metric_name",
                "cluster_unit",
                "method",
                "repetitions",
                "confidence_level",
                "status",
                "point_estimate",
                "confidence_interval_lower",
                "confidence_interval_upper",
            },
            f"bootstrap result {index}",
        )
        estimand = _sha256(result.get("estimand_id_sha256"), "bootstrap estimand")
        metric = _string(result.get("metric_name"), "bootstrap metric")
        bootstrap_key = (estimand, metric)
        if bootstrap_key in observed_bootstrap_keys:
            raise B05ContractError(f"duplicate bootstrap result: {bootstrap_key}")
        observed_bootstrap_keys.append(bootstrap_key)
        _expect(result.get("cluster_unit"), "speaker_id_sha256", "bootstrap cluster")
        _expect(
            result.get("method"),
            analysis.get("speaker_cluster_bootstrap_method"),
            "bootstrap method",
        )
        _expect(result.get("repetitions"), 5000, "bootstrap repetitions")
        _expect(result.get("confidence_level"), 0.95, "bootstrap confidence")
        point = _optional_finite(result.get("point_estimate"), "bootstrap point")
        lower = _optional_finite(
            result.get("confidence_interval_lower"),
            "bootstrap lower",
        )
        upper = _optional_finite(
            result.get("confidence_interval_upper"),
            "bootstrap upper",
        )
        _validate_metric_status(
            result.get("status"),
            (point, lower, upper),
            f"bootstrap result {index}",
        )
        if point is not None and lower is not None and upper is not None:
            if not lower <= point <= upper:
                raise B05ContractError("bootstrap interval does not contain point")
    _expect(
        observed_bootstrap_keys,
        expected_bootstrap_keys,
        "exact preregistered bootstrap result keys",
    )

    expected_hypothesis_items = _list(
        analysis.get("holm_hypotheses"),
        "preregistered Holm hypotheses",
    )
    if not expected_hypothesis_items:
        raise B05ContractError("preregistered Holm hypotheses must not be empty")
    expected_hypotheses: list[tuple[str, str, int, float]] = []
    for index, raw in enumerate(expected_hypothesis_items):
        hypothesis = _mapping(raw, f"preregistered Holm hypothesis {index}")
        expected_hypotheses.append(
            (
                _sha256(
                    hypothesis.get("family_id_sha256"),
                    "preregistered Holm family",
                ),
                _sha256(
                    hypothesis.get("hypothesis_id_sha256"),
                    "preregistered Holm hypothesis",
                ),
                _integer(
                    hypothesis.get("family_size"),
                    "preregistered Holm family size",
                    1,
                ),
                _number(hypothesis.get("alpha"), "preregistered Holm alpha"),
            )
        )
    if len({item[:2] for item in expected_hypotheses}) != len(expected_hypotheses):
        raise B05ContractError("duplicate preregistered Holm hypothesis")
    holm = _list(payload.get("holm_results"), "Holm results")
    observed_hypotheses: list[tuple[str, str, int, float]] = []
    family_sizes: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for index, raw in enumerate(holm):
        result = _mapping(raw, f"Holm result {index}")
        _expect_keys(
            result,
            {
                "family_id_sha256",
                "hypothesis_id_sha256",
                "family_size",
                "status",
                "raw_p_value",
                "adjusted_p_value",
                "alpha",
                "rejected",
            },
            f"Holm result {index}",
        )
        family = _sha256(result.get("family_id_sha256"), "Holm family")
        hypothesis_id = _sha256(
            result.get("hypothesis_id_sha256"),
            "Holm hypothesis",
        )
        holm_key = (family, hypothesis_id)
        if any(item[:2] == holm_key for item in observed_hypotheses):
            raise B05ContractError(f"duplicate Holm hypothesis: {holm_key}")
        size = _integer(result.get("family_size"), "Holm family size", 1)
        prior_size = family_sizes.setdefault(family, size)
        _expect(size, prior_size, "Holm family size consistency")
        family_counts[family] = family_counts.get(family, 0) + 1
        alpha = _number(result.get("alpha"), "Holm alpha")
        observed_hypotheses.append((family, hypothesis_id, size, alpha))
        status = result.get("status")
        raw_p = _optional_finite(result.get("raw_p_value"), "Holm raw p")
        adjusted = _optional_finite(result.get("adjusted_p_value"), "Holm adjusted p")
        rejected = result.get("rejected")
        if status == "estimable":
            if raw_p is None or adjusted is None or not isinstance(rejected, bool):
                raise B05ContractError("estimable Holm result cannot contain null")
            if not (0 <= raw_p <= adjusted <= 1 and 0 < alpha < 1):
                raise B05ContractError("invalid Holm p-value or alpha")
            _expect(rejected, adjusted <= alpha, "Holm decision")
        elif status == "undefined_per_preregistered_policy":
            if raw_p is not None or adjusted is not None or rejected is not None:
                raise B05ContractError("undefined Holm values must all be null")
        else:
            raise B05ContractError("invalid Holm result status")
    for family, size in family_sizes.items():
        _expect(family_counts[family], size, f"Holm family {family} count")
    _expect(
        observed_hypotheses,
        expected_hypotheses,
        "exact preregistered Holm hypotheses",
    )

    bindings = _mapping(
        payload.get("analysis_policy_bindings"),
        "analysis policy bindings",
    )
    _expect_keys(bindings, ANALYSIS_SHA_FIELDS, "analysis policy bindings")
    for field in ANALYSIS_SHA_FIELDS:
        _expect(bindings.get(field), analysis.get(field), f"analysis binding {field}")

    assurance = _mapping(payload.get("one_shot_assurance"), "one-shot assurance")
    assurance_expected = {
        "joint_locked_access_count": 1,
        "b05_execution_count": 1,
        "s108_execution_count": 1,
        "both_arms_sealed_before_result_release": True,
        "first_arm_result_visible_before_second_arm_sealed": False,
        "arm_mutation_after_access": False,
        "rerun_count": 0,
        "rerun_allowed": False,
        "new_independent_test_set_required_for_new_run": True,
    }
    _expect_keys(assurance, set(assurance_expected), "one-shot assurance")
    _expect(dict(assurance), assurance_expected, "one-shot assurance")
    privacy = _mapping(payload.get("privacy_and_claims"), "report privacy")
    privacy_expected = {
        "privacy_zone": "A1_aggregate_locked_evaluation",
        "contains_identifiers": False,
        "contains_rows": False,
        "contains_audio_or_locator": False,
        "contains_labels": False,
        "contains_predictions": False,
        "contains_free_text": False,
        "oracle_alignment_research_only": True,
        "deployment_claim_allowed": False,
        "b05_completion_claim_allowed": False,
    }
    _expect_keys(privacy, set(privacy_expected), "report privacy")
    _expect(dict(privacy), privacy_expected, "report privacy")
    _validate_no_tbd(payload, "B05 aggregate report")
    _validate_binding(payload, "B05 aggregate report")
    return payload


RECEIPT_KEYS = {
    "schema_version",
    "receipt_id",
    "status",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "authorization_sha256",
    "one_shot_transaction_id_sha256",
    "joint_locked_access_count",
    "b05_execution_count",
    "s108_execution_count",
    "b05_predictions_sha256",
    "s108_predictions_sha256",
    "b05_report_sha256",
    "s108_promotion_report_sha256",
    "failure_receipt_sha256",
    "both_arms_sealed_before_result_release",
    "first_arm_result_visible_before_second_arm_sealed",
    "b05_arm_mutated_after_access",
    "s108_arm_mutated_after_access",
    "rerun_count",
    "authorization_consumed",
    "rerun_allowed",
    "new_independent_test_set_required_for_any_new_run",
    "contains_rows",
    "contains_audio_or_locator",
    "contains_labels",
    "contains_predictions",
    "privacy_zone",
    "binding_sha256",
}


def validate_batch_receipt_payload(
    payload: Mapping[str, Any],
    *,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    authorization_sha256: str,
    one_shot_transaction_id_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, RECEIPT_KEYS, "joint batch receipt")
    _expect(payload.get("schema_version"), RECEIPT_SCHEMA_VERSION, "receipt schema")
    if re.fullmatch(r"b05receipt_[0-9a-f]{32}", str(payload.get("receipt_id"))) is None:
        raise B05ContractError("invalid batch receipt ID")
    for field, expected in {
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "authorization_sha256": authorization_sha256,
        "one_shot_transaction_id_sha256": one_shot_transaction_id_sha256,
    }.items():
        _expect(payload.get(field), expected, f"receipt {field}")
    _expect(payload.get("joint_locked_access_count"), 1, "receipt access")
    status = payload.get("status")
    result_fields = (
        "b05_predictions_sha256",
        "s108_predictions_sha256",
        "b05_report_sha256",
        "s108_promotion_report_sha256",
    )
    if status == "completed_both_arms_sealed":
        _expect(payload.get("b05_execution_count"), 1, "receipt B05 execution")
        _expect(payload.get("s108_execution_count"), 1, "receipt S108 execution")
        for field in result_fields:
            _sha256(payload.get(field), f"receipt {field}")
        _expect(payload.get("failure_receipt_sha256"), None, "receipt failure")
        _expect(
            payload.get("both_arms_sealed_before_result_release"),
            True,
            "receipt sealed",
        )
    elif status == "failed_irrecoverably_no_rerun":
        for field in ("b05_execution_count", "s108_execution_count"):
            count = _integer(payload.get(field), f"receipt {field}")
            if count > 1:
                raise B05ContractError(f"receipt {field} exceeds one")
        for field in result_fields:
            _expect(payload.get(field), None, f"failed receipt {field}")
        _sha256(payload.get("failure_receipt_sha256"), "failed receipt failure")
        _expect(
            payload.get("both_arms_sealed_before_result_release"),
            False,
            "failed receipt sealed",
        )
    else:
        raise B05ContractError("invalid joint batch receipt status")
    for field in (
        "first_arm_result_visible_before_second_arm_sealed",
        "b05_arm_mutated_after_access",
        "s108_arm_mutated_after_access",
        "rerun_allowed",
        "contains_rows",
        "contains_audio_or_locator",
        "contains_labels",
        "contains_predictions",
    ):
        _expect(payload.get(field), False, f"receipt {field}")
    _expect(payload.get("rerun_count"), 0, "receipt rerun count")
    _expect(payload.get("authorization_consumed"), True, "receipt consumed")
    _expect(
        payload.get("new_independent_test_set_required_for_any_new_run"),
        True,
        "receipt new set",
    )
    _expect(
        payload.get("privacy_zone"),
        "A1_aggregate_one_shot_receipt",
        "receipt privacy",
    )
    _validate_no_tbd(payload, "joint batch receipt")
    _validate_binding(payload, "joint batch receipt")
    return payload


REGISTRY_KEYS = {
    "schema_version",
    "run_id",
    "status",
    "failure",
    "config_sha256",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "authorization_sha256",
    "batch_receipt_sha256",
    "b03_config_sha256",
    "b03_report_sha256",
    "b03_registry_sha256",
    "b03_checkpoint_set_sha256",
    "s108_arm_freeze_sha256",
    "locked_predictions_sha256",
    "aggregate_report_sha256",
    "joint_locked_access_count",
    "b05_execution_count",
    "s108_execution_count",
    "rerun_count",
    "artifact_count",
    "result_based_mutation_detected",
    "posthoc_b06_substitution_used",
    "privacy_zone",
    "binding_sha256",
}


def validate_registry_payload(
    payload: Mapping[str, Any],
    *,
    config_sha256: str,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    authorization_sha256: str,
    batch_receipt_sha256: str,
    s108_freeze_sha256: str,
    locked_predictions_sha256: str,
    aggregate_report_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, REGISTRY_KEYS, "B05 registry")
    expected = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "run_id": "b05-explicit-locked-evaluation-v1",
        "status": "completed",
        "failure": None,
        "config_sha256": config_sha256,
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "authorization_sha256": authorization_sha256,
        "batch_receipt_sha256": batch_receipt_sha256,
        "b03_config_sha256": B03_CONFIG_SHA256,
        "b03_report_sha256": B03_REPORT_SHA256,
        "b03_registry_sha256": B03_REGISTRY_SHA256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
        "locked_predictions_sha256": locked_predictions_sha256,
        "aggregate_report_sha256": aggregate_report_sha256,
        "joint_locked_access_count": 1,
        "b05_execution_count": 1,
        "s108_execution_count": 1,
        "rerun_count": 0,
        "artifact_count": 2,
        "result_based_mutation_detected": False,
        "posthoc_b06_substitution_used": False,
        "privacy_zone": "A1_aggregate_registry",
    }
    for field, value in expected.items():
        _expect(payload.get(field), value, f"registry {field}")
    _validate_no_tbd(payload, "B05 registry")
    _validate_binding(payload, "B05 registry")
    return payload


COMMIT_KEYS = {
    "schema_version",
    "status",
    "config_sha256",
    "preregistration_sha256",
    "batch_manifest_sha256",
    "authorization_sha256",
    "batch_receipt_sha256",
    "b03_checkpoint_set_sha256",
    "s108_arm_freeze_sha256",
    "b05_locked_predictions_sha256",
    "b05_aggregate_report_sha256",
    "b05_registry_sha256",
    "s108_locked_predictions_sha256",
    "s108_promotion_report_sha256",
    "s108_registry_sha256",
    "joint_locked_transaction_id_sha256",
    "joint_locked_access_count",
    "both_arms_published_together",
    "first_result_used_to_change_other_arm",
    "rerun_count",
    "rerun_allowed",
    "new_independent_test_set_required_for_any_new_run",
    "b05_completion_claim_allowed",
    "s108_completion_claim_bound_to_joint_root",
    "child_commit_back_reference_allowed",
    "deployment_claim_allowed",
    "binding_sha256",
}


def validate_commit_payload(
    payload: Mapping[str, Any],
    *,
    config_sha256: str,
    preregistration_sha256: str,
    batch_manifest_sha256: str,
    authorization_sha256: str,
    batch_receipt_sha256: str,
    s108_freeze_sha256: str,
    b05_predictions_sha256: str,
    b05_report_sha256: str,
    b05_registry_sha256: str,
    s108_predictions_sha256: str,
    s108_report_sha256: str,
    s108_registry_sha256: str,
    one_shot_transaction_id_sha256: str,
) -> Mapping[str, Any]:
    _expect_keys(payload, COMMIT_KEYS, "joint root commit")
    expected = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "status": "committed_joint_one_shot",
        "config_sha256": config_sha256,
        "preregistration_sha256": preregistration_sha256,
        "batch_manifest_sha256": batch_manifest_sha256,
        "authorization_sha256": authorization_sha256,
        "batch_receipt_sha256": batch_receipt_sha256,
        "b03_checkpoint_set_sha256": B03_CHECKPOINT_SET_SHA256,
        "s108_arm_freeze_sha256": s108_freeze_sha256,
        "b05_locked_predictions_sha256": b05_predictions_sha256,
        "b05_aggregate_report_sha256": b05_report_sha256,
        "b05_registry_sha256": b05_registry_sha256,
        "s108_locked_predictions_sha256": s108_predictions_sha256,
        "s108_promotion_report_sha256": s108_report_sha256,
        "s108_registry_sha256": s108_registry_sha256,
        "joint_locked_transaction_id_sha256": one_shot_transaction_id_sha256,
        "joint_locked_access_count": 1,
        "both_arms_published_together": True,
        "first_result_used_to_change_other_arm": False,
        "rerun_count": 0,
        "rerun_allowed": False,
        "new_independent_test_set_required_for_any_new_run": True,
        "b05_completion_claim_allowed": True,
        "s108_completion_claim_bound_to_joint_root": True,
        "child_commit_back_reference_allowed": False,
        "deployment_claim_allowed": False,
    }
    for field, value in expected.items():
        _expect(payload.get(field), value, f"commit {field}")
    _validate_no_tbd(payload, "joint root commit")
    _validate_binding(payload, "joint root commit")
    return payload


def _collect_key_names(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_collect_key_names(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_collect_key_names(child))
    return keys


def validate_hash_dag_documents(
    *,
    b05_predictions: Mapping[str, Any],
    b05_report: Mapping[str, Any],
    b05_registry: Mapping[str, Any],
    joint_commit: Mapping[str, Any],
    s108_predictions: Mapping[str, Any] | None = None,
    s108_report: Mapping[str, Any] | None = None,
    s108_registry: Mapping[str, Any] | None = None,
) -> None:
    """Reject output→receipt and child→joint-root hash cycles."""

    for name, document in (
        ("B05 predictions", b05_predictions),
        ("B05 report", b05_report),
    ):
        keys = _collect_key_names(document)
        forbidden = {
            "batch_receipt_sha256",
            "joint_root_commit_sha256",
            "s108_promotion_report_sha256",
        }
        overlap = keys & forbidden
        if overlap:
            raise B05ContractError(f"{name} creates hash back-edge: {sorted(overlap)}")
    for name, s108_document in (
        ("S108 predictions", s108_predictions),
        ("S108 report", s108_report),
    ):
        if s108_document is not None:
            overlap = _collect_key_names(s108_document) & {
                "batch_receipt_sha256",
                "joint_root_commit_sha256",
                "b05_aggregate_report_sha256",
                "b05_report_sha256",
            }
            if overlap:
                raise B05ContractError(
                    f"{name} creates hash back-edge: {sorted(overlap)}"
                )
    for name, registry_document in (
        ("B05 registry", b05_registry),
        ("S108 registry", s108_registry),
    ):
        if registry_document is not None and (
            "joint_root_commit_sha256" in _collect_key_names(registry_document)
        ):
            raise B05ContractError(f"{name} may not reference joint root commit")
    if "s108_commit_sha256" in _collect_key_names(joint_commit):
        raise B05ContractError("joint root may not form a cycle with S108 child commit")


def _require_resolved_sha(
    value: object,
    name: str,
    *,
    stage: str,
    audit: AccessAudit,
) -> str:
    text = _string(value, f"{name} SHA")
    if text.startswith(TBD_PREFIX):
        raise B05ContractError(
            f"{name} SHA is {text}",
            stage=stage,
            audit=audit,
        )
    return _sha256(text, f"{name} SHA")


def _read_gate_json(
    *,
    root: Path,
    section: Mapping[str, Any],
    path_field: str,
    sha_field: str,
    name: str,
    stage: str,
    counter_prefix: str,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    expected = _require_resolved_sha(
        section.get(sha_field),
        name,
        stage=stage,
        audit=audit,
    )
    setattr(
        audit,
        f"{counter_prefix}_path_resolution_count",
        getattr(audit, f"{counter_prefix}_path_resolution_count") + 1,
    )
    relative = _relative_string(section.get(path_field), f"{name} path")
    setattr(
        audit,
        f"{counter_prefix}_read_attempt_count",
        getattr(audit, f"{counter_prefix}_read_attempt_count") + 1,
    )
    try:
        payload, snapshot = _read_exact_json(root, relative, expected, name)
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage=stage,
            audit=audit,
        ) from exc
    setattr(
        audit,
        f"{counter_prefix}_read_count",
        getattr(audit, f"{counter_prefix}_read_count") + 1,
    )
    return payload, snapshot


def _verify_b03_checkpoint_files(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> tuple[FileSnapshot, ...]:
    """Hash exact checkpoint bytes, never deserialize them.

    This function is reachable only after the joint manifest is frozen and
    before one-shot authorization is read.  It uses O_NOFOLLOW and verifies
    each file identity again after all three reads.
    """

    section = _mapping(config.get("b03_canonical_baseline"), "B03 baseline")
    checkpoints = _list(section.get("checkpoints"), "B03 checkpoints")
    snapshots: list[FileSnapshot] = []
    for index, raw in enumerate(checkpoints):
        item = _mapping(raw, f"B03 checkpoint {index}")
        expected = _sha256(item.get("sha256"), f"B03 checkpoint {index} SHA")
        relative = _relative_string(
            item.get("path"),
            f"B03 checkpoint {index} path",
        )
        audit.b03_checkpoint_path_resolution_count += 1
        audit.b03_checkpoint_hash_read_attempt_count += 1
        try:
            _, snapshot = _read_file(
                workspace_root,
                relative,
                f"B03 checkpoint {index}",
            )
        except B05ContractError as exc:
            raise B05ContractError(
                str(exc),
                stage="b03_checkpoint_hash_verification",
                audit=audit,
            ) from exc
        audit.b03_checkpoint_hash_read_count += 1
        if snapshot.sha256 != expected:
            raise B05ContractError(
                f"B03 checkpoint {index} SHA mismatch",
                stage="b03_checkpoint_hash_verification",
                audit=audit,
            )
        snapshots.append(snapshot)
    for index, snapshot in enumerate(snapshots):
        try:
            _verify_snapshot(
                workspace_root,
                snapshot,
                f"B03 checkpoint snapshot {index}",
            )
        except B05ContractError as exc:
            raise B05ContractError(
                str(exc),
                stage="b03_checkpoint_hash_verification",
                audit=audit,
            ) from exc
    if audit.b03_checkpoint_deserialize_count != 0:
        raise B05ContractError(
            "checkpoint deserialization occurred during preflight",
            stage="b03_checkpoint_hash_verification",
            audit=audit,
        )
    return tuple(snapshots)


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit,
) -> Mapping[str, Any]:
    return {
        "status": "static_contract_validated_no_locked_execution",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        **audit.as_dict(),
        "artifact_created": False,
        "actual_locked_access_performed": False,
        "actual_model_or_gpu_used": False,
        "numeric_result_produced": False,
        "b05_score_awarded": 0,
        "b05_completion_claim_allowed": False,
        "s108_completion_claim_allowed": False,
        "deployment_claim_allowed": False,
    }


def failure_result(exc: B05ContractError) -> Mapping[str, Any]:
    audit = exc.audit or AccessAudit()
    return {
        "status": "blocked_fail_closed",
        "stage": exc.stage,
        "error": str(exc),
        **audit.as_dict(),
        "artifact_created": False,
        "actual_locked_access_performed": False,
        "actual_model_or_gpu_used": False,
        "numeric_result_produced": False,
        "b05_score_awarded": 0,
        "b05_completion_claim_allowed": False,
        "s108_completion_claim_allowed": False,
        "deployment_claim_allowed": False,
    }


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run ordered aggregate preflight and stop before every protected action."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=root,
        config_path=config_path,
        config=config,
        audit=audit,
    )

    g04_section = _mapping(config.get("g04_gate"), "G04 gate")
    g04, g04_snapshot = _read_gate_json(
        root=root,
        section=g04_section,
        path_field="completion_path",
        sha_field="completion_sha256",
        name="G04 completion",
        stage="g04_completion",
        counter_prefix="g04",
        audit=audit,
    )
    try:
        validate_g04_completion_payload(g04)
        _verify_snapshot(root, g04_snapshot, "G04 completion snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="g04_completion",
            audit=audit,
        ) from exc
    audit.g04_deep_validation_count += 1

    d07_section = _mapping(config.get("d07_gate"), "D07 gate")
    d07, d07_snapshot = _read_gate_json(
        root=root,
        section=d07_section,
        path_field="completion_path",
        sha_field="completion_sha256",
        name="D07 completion",
        stage="d07_completion",
        counter_prefix="d07",
        audit=audit,
    )
    try:
        validate_d07_completion_payload(d07)
        _expect(
            d07.get("g04_completion_attestation_sha256"),
            g04_snapshot.sha256,
            "D07 G04 binding",
        )
        _verify_snapshot(root, d07_snapshot, "D07 completion snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="d07_completion",
            audit=audit,
        ) from exc
    audit.d07_deep_validation_count += 1

    s107_section = _mapping(config.get("s107_gate"), "S107 gate")
    s107, s107_snapshot = _read_gate_json(
        root=root,
        section=s107_section,
        path_field="commit_path",
        sha_field="commit_sha256",
        name="S107 commit",
        stage="s107_commit",
        counter_prefix="s107",
        audit=audit,
    )
    try:
        validate_s107_commit_payload(s107)
        _verify_snapshot(root, s107_snapshot, "S107 commit snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="s107_commit",
            audit=audit,
        ) from exc
    audit.s107_deep_validation_count += 1

    freeze_section = _mapping(config.get("s108_arm_gate"), "S108 arm gate")
    freeze, freeze_snapshot = _read_gate_json(
        root=root,
        section=freeze_section,
        path_field="freeze_path",
        sha_field="freeze_sha256",
        name="S108 arm freeze",
        stage="s108_arm_freeze",
        counter_prefix="s108_freeze",
        audit=audit,
    )
    try:
        validate_s108_arm_freeze_payload(
            freeze,
            g04_sha256=g04_snapshot.sha256,
            d07_sha256=d07_snapshot.sha256,
            s107_sha256=s107_snapshot.sha256,
        )
        _verify_snapshot(root, freeze_snapshot, "S108 freeze snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="s108_arm_freeze",
            audit=audit,
        ) from exc

    prereg_section = _mapping(
        config.get("joint_preregistration"),
        "joint preregistration",
    )
    prereg, prereg_snapshot = _read_gate_json(
        root=root,
        section=prereg_section,
        path_field="artifact_path",
        sha_field="artifact_sha256",
        name="B05 preregistration",
        stage="joint_preregistration",
        counter_prefix="preregistration",
        audit=audit,
    )
    try:
        validate_preregistration_payload(
            prereg,
            g04_sha256=g04_snapshot.sha256,
            d07_sha256=d07_snapshot.sha256,
            s107_sha256=s107_snapshot.sha256,
            s108_freeze_sha256=freeze_snapshot.sha256,
            s108_freeze=freeze,
        )
        _verify_snapshot(root, prereg_snapshot, "preregistration snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="joint_preregistration",
            audit=audit,
        ) from exc

    batch_section = _mapping(config.get("joint_batch_manifest"), "batch manifest")
    batch, batch_snapshot = _read_gate_json(
        root=root,
        section=batch_section,
        path_field="artifact_path",
        sha_field="artifact_sha256",
        name="joint batch manifest",
        stage="joint_batch_manifest",
        counter_prefix="batch_manifest",
        audit=audit,
    )
    try:
        validate_batch_manifest_payload(
            batch,
            preregistration_sha256=prereg_snapshot.sha256,
            g04_sha256=g04_snapshot.sha256,
            d07_sha256=d07_snapshot.sha256,
            s107_sha256=s107_snapshot.sha256,
            s108_freeze_sha256=freeze_snapshot.sha256,
            s108_freeze=freeze,
        )
        _verify_snapshot(root, batch_snapshot, "batch manifest snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="joint_batch_manifest",
            audit=audit,
        ) from exc

    _verify_b03_checkpoint_files(
        workspace_root=root,
        config=config,
        audit=audit,
    )

    authorization_section = _mapping(
        config.get("one_shot_authorization"),
        "one-shot authorization",
    )
    authorization, authorization_snapshot = _read_gate_json(
        root=root,
        section=authorization_section,
        path_field="artifact_path",
        sha_field="artifact_sha256",
        name="one-shot authorization",
        stage="one_shot_authorization",
        counter_prefix="authorization",
        audit=audit,
    )
    try:
        validate_authorization_payload(
            authorization,
            preregistration_sha256=prereg_snapshot.sha256,
            batch_manifest_sha256=batch_snapshot.sha256,
            g04_sha256=g04_snapshot.sha256,
            d07_sha256=d07_snapshot.sha256,
            s107_sha256=s107_snapshot.sha256,
            s108_freeze_sha256=freeze_snapshot.sha256,
        )
        _verify_snapshot(root, authorization_snapshot, "authorization snapshot")
    except B05ContractError as exc:
        raise B05ContractError(
            str(exc),
            stage="one_shot_authorization",
            audit=audit,
        ) from exc

    protected = {
        key: value
        for key, value in audit.as_dict().items()
        if key
        in {
            "locked_locator_resolution_count",
            "locked_row_read_count",
            "locked_audio_read_count",
            "locked_label_read_count",
            "locked_prediction_read_count",
            "joint_locked_access_count",
            "model_access_count",
            "model_deserialize_count",
            "model_inference_count",
            "gpu_access_count",
            "metric_count",
            "bootstrap_count",
            "holm_count",
            "result_count",
            "network_count",
            "output_path_resolution_count",
            "output_write_count",
            "registry_write_count",
            "commit_write_count",
            "publication_count",
        }
    }
    if any(protected.values()):
        raise B05ContractError(
            f"protected action occurred in preflight: {protected}",
            stage="protected_action_guard",
            audit=audit,
        )
    return PreflightResult(
        status="ready_for_separately_authorized_joint_locked_executor",
        config_sha256=static.config_sha256,
        g04_completion_sha256=g04_snapshot.sha256,
        d07_completion_sha256=d07_snapshot.sha256,
        s107_commit_sha256=s107_snapshot.sha256,
        s108_arm_freeze_sha256=freeze_snapshot.sha256,
        preregistration_sha256=prereg_snapshot.sha256,
        batch_manifest_sha256=batch_snapshot.sha256,
        authorization_sha256=authorization_snapshot.sha256,
        audit=audit.as_dict(),
    )


__all__ = [
    "AccessAudit",
    "B05ContractError",
    "PreflightResult",
    "StaticValidation",
    "binding_sha256",
    "config_only_result",
    "failure_result",
    "load_config",
    "run_full_preflight",
    "validate_aggregate_report_payload",
    "validate_authorization_payload",
    "validate_batch_manifest_payload",
    "validate_batch_receipt_payload",
    "validate_commit_payload",
    "validate_d07_completion_payload",
    "validate_g04_completion_payload",
    "validate_hash_dag_documents",
    "validate_prediction_manifest_payload",
    "validate_preregistration_payload",
    "validate_registry_payload",
    "validate_s107_commit_payload",
    "validate_s108_arm_freeze_payload",
    "validate_static_config",
]
