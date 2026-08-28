"""Fail-closed WP5 S200 independence contract and data-card preflight.

This module deliberately does not implement Whisper or HuBERT inference,
training, protected-data loading, holdout evaluation, or metric computation.
Its only runtime write is an aggregate readiness lock, and that write is
unreachable until a completed S108 research-candidate commit and an aggregate
S203 expert-holdout attestation have both been validated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
DATA_CARD_SCHEMA_VERSION = "voice_coach.s2_s200_data_card.v1"
READINESS_LOCK_SCHEMA_VERSION = "voice_coach.s2_s200_readiness_lock.v1"
S108_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s108_final_s1_commit.v1"
S203_ATTESTATION_SCHEMA_VERSION = "voice_coach.s2_s203_expert_holdout_attestation.v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor",
    "dag",
    "independence",
    "s201",
    "s202",
    "s203",
    "data_card",
    "privacy",
    "licenses",
    "reproducibility",
    "outputs",
}
SEPARATION_DIMENSIONS = (
    "backbone",
    "alignment",
    "holdout",
    "objective",
    "training_labels",
    "model_features",
)
DAG_NODES = (
    "s200_independence_contract",
    "s201_whisper_content_consistency",
    "s202_independent_representation_auditor",
    "s203_expert_holdout_audit",
)
DAG_EDGES = (
    ("s200_independence_contract", "s201_whisper_content_consistency"),
    ("s201_whisper_content_consistency", "s202_independent_representation_auditor"),
    ("s202_independent_representation_auditor", "s203_expert_holdout_audit"),
)
S1_SPLITS = ("train", "dev", "stack", "calibration", "locked_test")
S2_SELECTION_SPLITS = ("s2_auditor_train", "s2_auditor_validation")
S2_HOLDOUT_SPLIT = "s2_expert_holdout"
TRAINING_SEEDS = (42, 43, 44)
S108_ARTIFACT_HASH_NAMES = (
    "final_s1_config",
    "final_s1_checkpoint",
    "final_fusion_checkpoint",
    "calibrator_parameters",
    "threshold_policy",
    "promotion_report",
    "source_chain",
    "independent_validation_receipt",
)
FORBIDDEN_MODEL_INPUT_FIELDS = (
    "realized_phone",
    "alignment_operation",
    "error_tag",
    "error_count",
    "phone_interval",
    "word_interval",
    "oracle_alignment",
    "predicted_alignment",
    "s1_prediction",
    "s1_score",
    "s1_logit",
    "s1_probability",
    "s1_confidence",
    "s1_threshold",
    "s1_label",
    "s1_wavlm_embedding",
    "s1_explicit_feature",
)
S201_INPUT_ALLOWLIST = (
    "item_id_sha256",
    "speaker_id_sha256",
    "audio_waveform",
    "canonical_prompt_text",
    "language_code",
)
S202_RAW_INPUT_ALLOWLIST = (
    "item_id_sha256",
    "speaker_id_sha256",
    "audio_waveform",
    "content_gate_pass",
)
S202_FEATURE_ALLOWLIST = (
    "independent_ssl_hidden_state",
    "independent_ssl_attention_mask",
    "s2_intrinsic_uncertainty",
)
S203_PRIMARY_GATES: Mapping[str, tuple[str, float]] = {
    "serious_s1_misjudgment_recall": ("greater_than_or_equal", 0.80),
    "false_high_score_relative_reduction": ("greater_than_or_equal", 0.30),
    "normal_sample_referral_rate": ("less_than_or_equal", 0.15),
    "disagreement_expert_error_hit_rate": ("greater_than_or_equal", 0.60),
}
STATIC_SOURCE_PREFIXES = (
    ".codex/ai_rule_developer/",
    "configs/",
    "docs/",
    "requirements/",
    "scripts/",
    "src/",
)


class S200ContractError(ValueError):
    """Raised before protected/model/holdout access or unverified publication."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "static_contract",
        audit: AccessAudit | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.audit = audit


class S200PublicationError(S200ContractError):
    """Raised when atomic, append-only publication cannot be guaranteed."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise S200ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters proving that the S200 preflight stays outside data/model zones."""

    source_pin_read_count: int = 0
    predecessor_metadata_read_attempt_count: int = 0
    predecessor_metadata_read_count: int = 0
    external_aggregate_read_attempt_count: int = 0
    external_aggregate_read_count: int = 0
    data_read_count: int = 0
    model_read_count: int = 0
    protected_data_read_count: int = 0
    holdout_read_count: int = 0
    locked_test_read_count: int = 0
    gpu_access_count: int = 0
    metric_count: int = 0
    result_count: int = 0
    output_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            field_name: int(getattr(self, field_name))
            for field_name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    sha256: str
    size_bytes: int
    device: int
    inode: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class StaticValidation:
    config_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]
    independence_contract_sha256: str
    data_card_sha256: str


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    commit_binding_sha256: str
    alignment_mode: str
    artifact_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ExternalEvidence:
    snapshot: FileSnapshot
    binding_sha256: str
    item_count: int
    split_hashes: Mapping[str, str]
    rater_cohort_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    readiness_lock_path: Path
    readiness_lock_sha256: str
    readiness_lock: Mapping[str, Any]
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S200ContractError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S200ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S200ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S200ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S200ContractError(f"{name} must be at least {minimum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S200ContractError(f"{name} must be numeric")
    result = float(value)
    if not (-float("inf") < result < float("inf")):
        raise S200ContractError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S200ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not SHA256_PATTERN.fullmatch(value)
        or value == "0" * 64
    ):
        raise S200ContractError(f"{name} must be a nonzero lowercase SHA-256")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S200ContractError(f"{name} must equal {expected!r}")


def _expect_keys(
    mapping: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(mapping)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise S200ContractError(f"{name} keys differ; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not (-float("inf") < value < float("inf")):
        raise S200ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S200ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                S200ContractError(f"{name} contains {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S200ContractError(f"{name} is not strict UTF-8 JSON") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def _relative_string(value: object, name: str) -> str:
    relative = _string(value, name)
    path = Path(relative)
    if (
        path.is_absolute()
        or relative != path.as_posix()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise S200ContractError(f"{name} must be a normalized workspace-relative path")
    return relative


def _workspace_relative(root: Path, path: Path, name: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise S200ContractError(f"{name} escaped the workspace") from exc


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    normalized = _relative_string(relative, name)
    current = root
    for part in Path(normalized).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise S200ContractError(f"{name} cannot traverse a symlink")
    return root / normalized


def _regular_file_snapshot(
    root: Path,
    relative: str,
    name: str,
) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise S200ContractError(
            f"{name} is not a readable non-symlink regular file: {relative}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise S200ContractError(f"{name} must be a regular file: {relative}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity:
        raise S200ContractError(f"{name} changed while it was read: {relative}")
    data = b"".join(chunks)
    digest = hashlib.sha256(data).hexdigest()
    return data, FileSnapshot(
        path=relative,
        sha256=digest,
        size_bytes=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        modified_ns=after.st_mtime_ns,
    )


def _snapshot_equal(left: FileSnapshot, right: FileSnapshot) -> bool:
    return left == right


def load_config(path: Path) -> Mapping[str, Any]:
    """Load strict YAML without following a symlink at the config leaf."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise S200ContractError("config must be a readable non-symlink file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise S200ContractError("config must be a regular file")
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
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise S200ContractError("config changed while it was read")
    try:
        value = yaml.load(b"".join(chunks).decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise S200ContractError("config is not strict UTF-8 YAML") from exc
    result = _mapping(value, "config")
    _reject_nonfinite_tree(result, "config")
    return result


def _validate_experiment(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config["experiment"], "experiment")
    _expect(
        experiment.get("id"),
        "aihub71490_s2_s200_independent_auditor_v1",
        "experiment.id",
    )
    _expect(
        experiment.get("stage"),
        "s200_static_independence_contract_and_data_card",
        "experiment.stage",
    )
    _expect(
        experiment.get("implementation_status"),
        "static_contract_only",
        "experiment.implementation_status",
    )
    for field_name in (
        "s200_completion_claim_allowed",
        "s201_trainer_implemented",
        "s202_trainer_implemented",
        "actual_model_download_allowed",
        "actual_model_training_allowed",
        "gpu_access_allowed",
        "row_level_data_access_allowed",
        "protected_data_access_allowed",
        "holdout_access_allowed",
        "locked_test_access_allowed",
        "metric_generation_allowed",
        "result_generation_allowed",
    ):
        _expect(experiment.get(field_name), False, f"experiment.{field_name}")


def _validate_dag(config: Mapping[str, Any]) -> None:
    dag = _mapping(config["dag"], "dag")
    _expect(tuple(_list(dag.get("nodes"), "dag.nodes")), DAG_NODES, "dag.nodes")
    edges = tuple(
        tuple(_list(edge, f"dag.edges[{index}]"))
        for index, edge in enumerate(_list(dag.get("edges"), "dag.edges"))
    )
    _expect(edges, DAG_EDGES, "dag.edges")
    _expect(
        tuple(_list(dag.get("execution_order"), "dag.execution_order")),
        ("s108_final_research_candidate", *DAG_NODES),
        "dag.execution_order",
    )
    for field_name in (
        "stop_after_first_incomplete",
        "downstream_training_before_predecessor_forbidden",
        "s201_and_s202_trainers_out_of_scope",
    ):
        _expect(dag.get(field_name), True, f"dag.{field_name}")


def _validate_predecessor_contract(config: Mapping[str, Any]) -> None:
    predecessor = _mapping(config["predecessor"], "predecessor")
    _expect(
        predecessor.get("validate_before_external_evidence"),
        True,
        "predecessor.validate_before_external_evidence",
    )
    _expect(
        predecessor.get("validate_before_output_resolution"),
        True,
        "predecessor.validate_before_output_resolution",
    )
    s108 = _mapping(
        predecessor.get("s108_final_research_candidate"),
        "predecessor.s108_final_research_candidate",
    )
    commit = _mapping(s108.get("commit"), "predecessor.s108.commit")
    registry = _mapping(s108.get("registry"), "predecessor.s108.registry")
    _relative_string(commit.get("path"), "predecessor.s108.commit.path")
    _expect(
        commit.get("schema_version"),
        S108_COMMIT_SCHEMA_VERSION,
        "predecessor.s108.commit.schema_version",
    )
    _expect(commit.get("required_status"), "committed", "s108 required status")
    _expect(
        commit.get("required_decision"),
        "research_candidate_passed",
        "s108 required decision",
    )
    _expect(
        commit.get("required_research_candidate_completed"),
        True,
        "s108 required completed flag",
    )
    _expect(
        commit.get("required_deployment_approved"),
        False,
        "s108 deployment boundary",
    )
    _expect(commit.get("required_locked_batch_access_count"), 1, "s108 access count")
    _expect(commit.get("required_locked_batch_rerun_count"), 0, "s108 rerun count")
    _relative_string(registry.get("path"), "predecessor.s108.registry.path")
    _expect(registry.get("schema_version"), 1, "s108 registry schema")
    _expect(
        registry.get("run_id"),
        "aihub71490-wavlm-s108-final-s1-v1",
        "s108 registry run_id",
    )
    _expect(registry.get("required_status"), "completed", "s108 registry status")
    _expect(
        tuple(
            _list(
                s108.get("required_artifact_hash_names"),
                "s108 required artifact names",
            )
        ),
        S108_ARTIFACT_HASH_NAMES,
        "s108 required artifact names",
    )
    _expect(
        tuple(_list(s108.get("allowed_alignment_modes"), "allowed alignment modes")),
        ("oracle_target_boundary", "predicted_alignment"),
        "allowed alignment modes",
    )
    _expect(
        s108.get("alignment_mode_must_be_explicit"),
        True,
        "alignment mode explicit",
    )
    _expect(
        s108.get("oracle_result_may_not_claim_predicted_or_deployment"),
        True,
        "oracle claim boundary",
    )


def _validate_independence(config: Mapping[str, Any]) -> None:
    independence = _mapping(config["independence"], "independence")
    _expect(
        tuple(
            _list(
                independence.get("required_separation_dimensions"),
                "required separation dimensions",
            )
        ),
        SEPARATION_DIMENSIONS,
        "required separation dimensions",
    )
    s1 = _mapping(independence.get("s1_reference"), "independence.s1_reference")
    s2 = _mapping(independence.get("s2_contract"), "independence.s2_contract")
    _expect(s1.get("backbone_family"), "wavlm", "S1 backbone")
    _expect(s2.get("content_backbone_family"), "whisper", "S201 backbone")
    _expect(
        s2.get("auditor_backbone_family"),
        "hubert_or_separately_approved_non_wavlm",
        "S202 backbone",
    )
    _expect(s2.get("alignment_mode"), "alignment_free_utterance", "S2 alignment")
    _expect(
        tuple(_list(s1.get("selection_splits"), "S1 selection splits")),
        S1_SPLITS,
        "S1 selection splits",
    )
    _expect(
        tuple(_list(s2.get("model_selection_splits"), "S2 selection splits")),
        S2_SELECTION_SPLITS,
        "S2 selection splits",
    )
    _expect(s2.get("final_holdout_split"), S2_HOLDOUT_SPLIT, "S2 holdout")

    split = _mapping(
        independence.get("split_disjointness"),
        "independence.split_disjointness",
    )
    for field_name in (
        "require_speaker_disjoint",
        "require_item_disjoint",
        "require_prompt_disjoint_challenge_report",
        "require_rater_cohort_disjoint",
        "overlap_counts_must_all_be_zero",
    ):
        _expect(split.get(field_name), True, f"split_disjointness.{field_name}")
    _expect(
        tuple(
            _list(
                split.get("forbidden_overlap_with_s1_splits"),
                "forbidden S1 overlap",
            )
        ),
        S1_SPLITS,
        "forbidden S1 overlap",
    )

    alignment = _mapping(independence.get("alignment"), "independence.alignment")
    for field_name in (
        "s1_oracle_boundaries_allowed_as_s2_input",
        "s1_predicted_boundaries_allowed_as_s2_input",
        "s1_alignment_confidence_allowed_as_s2_input",
        "s2_phone_or_word_timestamp_feature_allowed",
        "whisper_timestamps_allowed_as_model_feature",
    ):
        _expect(alignment.get(field_name), False, f"alignment.{field_name}")
    for field_name in (
        "oracle_and_predicted_results_must_be_separate",
        "undeclared_alignment_mode_forbidden",
    ):
        _expect(alignment.get(field_name), True, f"alignment.{field_name}")

    leakage = _mapping(independence.get("leakage"), "independence.leakage")
    for field_name in (
        "s1_predictions_allowed_in_s201_or_s202",
        "s1_labels_allowed_in_s2_training_or_selection",
        "s1_features_allowed_in_s2_training_or_selection",
        "s1_thresholds_allowed_in_s2_training_or_selection",
        "s2_holdout_labels_visible_before_freeze",
    ):
        _expect(leakage.get(field_name), False, f"leakage.{field_name}")
    _expect(
        leakage.get("s1_predictions_allowed_for_s203_evaluation_join_only"),
        True,
        "S1 prediction evaluation-only boundary",
    )
    forbidden = tuple(
        _list(leakage.get("forbidden_model_input_fields"), "forbidden model inputs")
    )
    _expect(forbidden, FORBIDDEN_MODEL_INPUT_FIELDS, "forbidden model inputs")
    model_fields = (
        set(S201_INPUT_ALLOWLIST)
        | set(S202_RAW_INPUT_ALLOWLIST)
        | set(S202_FEATURE_ALLOWLIST)
    )
    overlap = sorted(model_fields & set(forbidden))
    if overlap:
        raise S200ContractError(f"model allowlist overlaps leakage denylist: {overlap}")


def _validate_s201(config: Mapping[str, Any]) -> None:
    s201 = _mapping(config["s201"], "s201")
    _expect(
        s201.get("role"),
        "whisper_transcript_content_consistency_hard_gate_only",
        "s201.role",
    )
    _expect(s201.get("implementation_status"), "not_implemented", "s201 status")
    _expect(s201.get("model_family"), "whisper", "s201 model family")
    _expect(
        s201.get("exact_checkpoint_revision_status"),
        "must_be_pinned_in_s201_config",
        "s201 checkpoint state",
    )
    _expect(
        tuple(_list(s201.get("input_allowlist"), "s201 input allowlist")),
        S201_INPUT_ALLOWLIST,
        "s201 input allowlist",
    )
    _expect(
        s201.get("normalized_transcript_policy_status"),
        "must_be_pinned_in_s201_config",
        "s201 normalization policy",
    )
    _expect(
        s201.get("wer_threshold_status"),
        "must_be_preregistered_in_s201_config",
        "s201 WER threshold state",
    )
    _expect(s201.get("wer_threshold"), None, "s201 WER threshold")
    _expect(
        s201.get("execute_with_unresolved_threshold_allowed"),
        False,
        "s201 unresolved execution",
    )
    decoding = _mapping(s201.get("decoding"), "s201.decoding")
    _expect(_number(decoding.get("temperature"), "temperature"), 0.0, "temperature")
    _expect(decoding.get("sampling_allowed"), False, "s201 sampling")
    _expect(
        decoding.get("timestamps_as_features_allowed"),
        False,
        "s201 timestamp feature",
    )
    hard_gate = _mapping(s201.get("hard_gate"), "s201.hard_gate")
    _expect(hard_gate.get("evaluated_before_s202_decision"), True, "S201 order")
    _expect(hard_gate.get("bypass_allowed"), False, "S201 bypass")
    _expect(
        hard_gate.get("failed_content_may_not_be_rescued_by_s202"),
        True,
        "S201 rescue",
    )
    _expect(
        hard_gate.get("fine_phone_or_prosody_judgment_role_allowed"),
        False,
        "Whisper role boundary",
    )
    source = _mapping(s201.get("official_source"), "s201.official_source")
    _expect(
        source.get("repository_revision"),
        "90fdc51112dc59242201d038a48add97ffd24b5d",
        "Whisper source revision",
    )
    _expect(source.get("license"), "MIT", "Whisper license")
    _sha256(source.get("license_sha256"), "Whisper license hash")


def _validate_s202(config: Mapping[str, Any]) -> None:
    s202 = _mapping(config["s202"], "s202")
    _expect(
        s202.get("role"),
        "independent_acoustic_representation_pairwise_ordinal_auditor",
        "s202.role",
    )
    _expect(s202.get("implementation_status"), "not_implemented", "s202 status")
    backbone = _mapping(s202.get("backbone_policy"), "s202.backbone_policy")
    _expect(backbone.get("preferred_family"), "hubert", "S202 preferred backbone")
    _expect(
        tuple(_list(backbone.get("allowed_families"), "S202 allowed backbones")),
        ("hubert", "separately_approved_non_wavlm_ssl"),
        "S202 allowed backbones",
    )
    _expect(
        tuple(_list(backbone.get("forbidden_families"), "S202 forbidden backbones")),
        ("wavlm", "whisper_as_sole_fine_phone_or_prosody_auditor"),
        "S202 forbidden backbones",
    )
    _expect(
        backbone.get("exact_checkpoint_revision_status"),
        "must_be_pinned_in_s202_config",
        "S202 checkpoint state",
    )
    _expect(
        backbone.get("source_repository_revision"),
        "3d262bb25690e4eb2e7d3c1309b1e9c406ca4b99",
        "HuBERT source revision",
    )
    _expect(backbone.get("license"), "MIT", "HuBERT license")
    _sha256(backbone.get("license_sha256"), "HuBERT license hash")
    _expect(
        tuple(_list(s202.get("raw_input_allowlist"), "S202 raw input allowlist")),
        S202_RAW_INPUT_ALLOWLIST,
        "S202 raw input allowlist",
    )
    _expect(
        tuple(_list(s202.get("model_feature_allowlist"), "S202 feature allowlist")),
        S202_FEATURE_ALLOWLIST,
        "S202 feature allowlist",
    )
    _expect(
        tuple(_list(s202.get("routing_only_fields"), "S202 routing fields")),
        ("content_gate_pass",),
        "S202 routing fields",
    )
    _expect(
        tuple(_list(s202.get("objectives"), "S202 objectives")),
        ("pairwise_preference", "ordinal_audit"),
        "S202 objectives",
    )
    labels = _mapping(s202.get("labels"), "s202.labels")
    _expect(
        tuple(_list(labels.get("allowed"), "S202 allowed labels")),
        (
            "s2_independent_expert_pairwise_preference",
            "s2_independent_expert_ordinal_rating",
        ),
        "S202 allowed labels",
    )
    for field_name in (
        "phone_derived_proxy_allowed",
        "s1_prediction_derived_label_allowed",
        "aihub_silver_label_allowed",
    ):
        _expect(labels.get(field_name), False, f"s202.labels.{field_name}")
    _expect(
        tuple(_list(s202.get("output_states"), "S202 output states")),
        ("approve", "disagreement", "uncertain"),
        "S202 output states",
    )
    seed = _mapping(s202.get("seed_contract"), "s202.seed_contract")
    _expect(
        tuple(_list(seed.get("training_seeds"), "S202 seeds")),
        TRAINING_SEEDS,
        "S202 seeds",
    )
    _expect(seed.get("seed_predictions_kept_separate"), True, "seed predictions")
    _expect(
        seed.get("ensemble_policy_must_be_pinned_before_holdout"),
        True,
        "ensemble freeze",
    )


def _validate_s203(config: Mapping[str, Any]) -> None:
    s203 = _mapping(config["s203"], "s203")
    _expect(s203.get("role"), "frozen_s1_s2_expert_holdout_audit", "s203.role")
    _expect(s203.get("implementation_status"), "not_implemented", "s203 status")
    attestation = _mapping(
        s203.get("expert_holdout_attestation"),
        "s203.expert_holdout_attestation",
    )
    _relative_string(attestation.get("path"), "S203 attestation path")
    _expect(
        attestation.get("schema_version"),
        S203_ATTESTATION_SCHEMA_VERSION,
        "S203 attestation schema",
    )
    _expect(attestation.get("required_status"), "ready", "S203 status")
    _expect(attestation.get("minimum_item_count"), 1000, "S203 minimum items")
    _expect(attestation.get("maximum_item_count"), 2000, "S203 maximum items")
    _expect(attestation.get("independent_rater_count"), 2, "S203 raters")
    _expect(attestation.get("adjudicator_count"), 1, "S203 adjudicators")
    for field_name in (
        "rater_overlap_with_s1_count",
        "item_overlap_with_s1_selection_count",
        "speaker_overlap_with_s1_selection_count",
    ):
        _expect(attestation.get(field_name), 0, f"S203 {field_name}")
    primary = _mapping(s203.get("primary_metrics"), "s203.primary_metrics")
    _expect(set(primary), set(S203_PRIMARY_GATES), "S203 primary metric names")
    for metric_name, (operator, threshold) in S203_PRIMARY_GATES.items():
        gate = _mapping(
            _mapping(primary[metric_name], metric_name).get("gate"),
            f"{metric_name}.gate",
        )
        _expect(gate.get("operator"), operator, f"{metric_name} operator")
        _expect(_number(gate.get("value"), metric_name), threshold, metric_name)
    statistics = _mapping(s203.get("statistics"), "s203.statistics")
    _expect(statistics.get("gate_statistic"), "point_estimate", "S203 statistic")
    _expect(_number(statistics.get("confidence_level"), "confidence"), 0.95, "CI")
    _expect(
        statistics.get("cluster_unit"),
        "speaker_id_sha256",
        "S203 cluster unit",
    )
    _expect(statistics.get("bootstrap_replicates"), 5000, "bootstrap replicates")
    _expect(statistics.get("bootstrap_seed"), 20260729, "bootstrap seed")
    _expect(statistics.get("report_confidence_intervals"), True, "S203 CI report")
    _expect(s203.get("completion_requires_all_primary_gates"), True, "S203 gates")
    _expect(
        s203.get("deployment_approval_created_by_s203"),
        False,
        "S203 deployment boundary",
    )


def _validate_data_governance(config: Mapping[str, Any]) -> None:
    data_card = _mapping(config["data_card"], "data_card")
    _expect(data_card.get("schema_version"), DATA_CARD_SCHEMA_VERSION, "data card")
    _expect(
        data_card.get("current_data_status"),
        "no_s2_row_level_data_accessed",
        "data card data status",
    )
    _expect(
        data_card.get("current_model_status"),
        "no_s2_model_downloaded_or_trained",
        "data card model status",
    )
    _expect(
        data_card.get("current_metric_status"),
        "no_s2_metric_or_result",
        "data card metric status",
    )
    _expect(
        data_card.get("allowed_claim"),
        "implementation_readiness_contract_only",
        "data card claim",
    )
    _expect(
        tuple(_list(data_card.get("intended_splits"), "data card splits")),
        (*S2_SELECTION_SPLITS, S2_HOLDOUT_SPLIT),
        "data card splits",
    )
    _expect(
        tuple(_list(data_card.get("prohibited_reuse"), "prohibited reuse")),
        (
            "s1_train",
            "s1_dev",
            "s1_stack",
            "s1_calibration",
            "s1_locked_test",
            "s1_labeler_cohort",
        ),
        "prohibited reuse",
    )
    privacy = _mapping(config["privacy"], "privacy")
    _expect(
        tuple(_list(privacy.get("allowed_identifiers"), "allowed identifiers")),
        ("item_id_sha256", "speaker_id_sha256", "reviewer_id_sha256"),
        "allowed identifiers",
    )
    for field_name in (
        "raw_identifier_or_path_export_allowed",
        "free_text_notes_as_model_input_allowed",
        "public_row_level_release_allowed",
    ):
        _expect(privacy.get(field_name), False, f"privacy.{field_name}")
    for field_name in (
        "aggregate_only_publication",
        "consent_withdrawal_retention_destruction_plan_required",
        "s203_developer_blinding_required",
    ):
        _expect(privacy.get(field_name), True, f"privacy.{field_name}")
    licenses = _mapping(config["licenses"], "licenses")
    _expect(
        licenses.get("exact_checkpoint_license_reaudit_required_before_download"),
        True,
        "checkpoint license reaudit",
    )
    _expect(
        licenses.get("data_license_reaudit_required_before_row_access"),
        True,
        "data license reaudit",
    )
    assets = {
        _string(_mapping(record, "license record").get("asset"), "license asset")
        for record in _list(licenses.get("records"), "license records")
    }
    _expect(
        assets,
        {
            "aihub_71490",
            "s2_expert_evaluation",
            "whisper_code_and_weights",
            "fairseq_hubert_code_and_pretrained_models",
            "fleurs_ko_kr",
            "zeroth_korean",
        },
        "license assets",
    )


def _validate_reproducibility_and_outputs(config: Mapping[str, Any]) -> None:
    reproducibility = _mapping(config["reproducibility"], "reproducibility")
    _expect(reproducibility.get("required_hash_algorithm"), "sha256", "hash")
    for field_name in (
        "every_hash_must_be_lowercase_64_hex",
        "config_and_source_pins_before_predecessor",
        "predecessor_and_registry_hashes_before_external",
        "external_attestation_hash_before_output",
        "no_unpinned_model_download",
        "failure_artifacts_preserved",
    ):
        _expect(reproducibility.get(field_name), True, f"reproducibility.{field_name}")
    _expect(
        reproducibility.get("result_based_reselection_allowed"),
        False,
        "result reselection",
    )
    registries = _mapping(
        reproducibility.get("registry_contracts"),
        "reproducibility.registry_contracts",
    )
    _expect(set(registries), {"s200", "s201", "s202", "s203"}, "registry stages")
    s202_registry = _mapping(registries["s202"], "S202 registry")
    _expect(
        tuple(_list(s202_registry.get("seeds"), "registry S202 seeds")),
        TRAINING_SEEDS,
        "registry S202 seeds",
    )
    outputs = _mapping(config["outputs"], "outputs")
    _relative_string(outputs.get("readiness_lock_path"), "readiness lock path")
    _expect(
        outputs.get("readiness_lock_schema_version"),
        READINESS_LOCK_SCHEMA_VERSION,
        "readiness lock schema",
    )
    _expect(outputs.get("access_scope"), "A1_aggregate_contract_only", "output scope")
    for field_name in (
        "atomic_publish",
        "no_overwrite",
        "no_symlink",
        "content_addressed_binding",
    ):
        _expect(outputs.get(field_name), True, f"outputs.{field_name}")
    _expect(outputs.get("current_expected_output_count"), 0, "current outputs")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> StaticValidation:
    """Validate policy and source pins with no artifact, data, model, or output read."""

    root = workspace_root.resolve(strict=True)
    resolved_config = config_path.absolute()
    relative_config = _workspace_relative(root, resolved_config, "config path")
    _reject_symlink_chain(root, relative_config, "config path")
    _expect_keys(config, TOP_LEVEL_KEYS, "config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")
    _validate_experiment(config)
    _validate_dag(config)
    _validate_predecessor_contract(config)
    _validate_independence(config)
    _validate_s201(config)
    _validate_s202(config)
    _validate_s203(config)
    _validate_data_governance(config)
    _validate_reproducibility_and_outputs(config)

    canonical_sources = _mapping(config["canonical_sources"], "canonical_sources")
    _expect(
        canonical_sources.get("access_scope"),
        "C1_code_and_policy_only",
        "canonical source scope",
    )
    pins = _list(canonical_sources.get("pins"), "canonical_sources.pins")
    if not pins:
        raise S200ContractError("at least one canonical source pin is required")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"canonical_sources.pins[{index}]")
        _expect_keys(pin, {"path", "sha256"}, f"canonical_sources.pins[{index}]")
        relative = _relative_string(pin.get("path"), f"source pin {index} path")
        if relative in seen:
            raise S200ContractError(f"duplicate canonical source pin: {relative}")
        seen.add(relative)
        if relative != "data/README.md" and relative.startswith(
            ("data/", "artifacts/", "models/", "checkpoints/")
        ):
            raise S200ContractError(f"source pin enters a data/model zone: {relative}")
        if relative not in {"pyproject.toml", "data/README.md"} and not relative.startswith(
            STATIC_SOURCE_PREFIXES
        ):
            raise S200ContractError(
                f"source pin is outside C1 policy scope: {relative}"
            )
        expected_sha = _sha256(pin.get("sha256"), f"source pin {relative} SHA-256")
        _, snapshot = _regular_file_snapshot(root, relative, "canonical source")
        if snapshot.sha256 != expected_sha:
            raise S200ContractError(
                f"canonical source pin mismatch for {relative}: "
                f"expected {expected_sha}, observed {snapshot.sha256}"
            )
        snapshots.append(snapshot)

    config_data, config_snapshot = _regular_file_snapshot(
        root,
        relative_config,
        "S200 config",
    )
    try:
        config_from_file = yaml.load(
            config_data.decode("utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise S200ContractError("S200 config is not strict UTF-8 YAML") from exc
    if config_from_file != config:
        raise S200ContractError("provided config does not match the pinned config file")
    independence_sha = hashlib.sha256(
        canonical_json_bytes(
            cast(Mapping[str, Any], config["independence"]),
            pretty=False,
        )
    ).hexdigest()
    data_card_sha = hashlib.sha256(
        canonical_json_bytes(
            cast(Mapping[str, Any], config["data_card"]),
            pretty=False,
        )
    ).hexdigest()
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        source_snapshots=tuple(snapshots),
        independence_contract_sha256=independence_sha,
        data_card_sha256=data_card_sha,
    )


def _runtime_json(
    *,
    root: Path,
    relative: str,
    name: str,
    audit: AccessAudit,
    category: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    if category == "predecessor":
        audit.predecessor_metadata_read_attempt_count += 1
    elif category == "external":
        audit.external_aggregate_read_attempt_count += 1
    else:
        raise S200ContractError(f"unknown metadata category: {category}")
    try:
        data, snapshot = _regular_file_snapshot(root, relative, name)
        parsed = _parse_json(data, name)
    except S200ContractError as exc:
        raise S200ContractError(
            str(exc),
            stage=f"{category}_validation",
            audit=audit,
        ) from exc
    if category == "predecessor":
        audit.predecessor_metadata_read_count += 1
    else:
        audit.external_aggregate_read_count += 1
    return parsed, snapshot


def _binding_sha256(value: Mapping[str, Any]) -> str:
    body = {key: nested for key, nested in value.items() if key != "binding_sha256"}
    return hashlib.sha256(canonical_json_bytes(body, pretty=False)).hexdigest()


def _validate_s108_predecessor(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> PredecessorEvidence:
    s108 = _mapping(
        _mapping(config["predecessor"], "predecessor")["s108_final_research_candidate"],
        "s108 predecessor",
    )
    contract = _mapping(s108["commit"], "s108 commit contract")
    commit_path = _relative_string(contract["path"], "s108 commit path")
    commit, commit_snapshot = _runtime_json(
        root=root,
        relative=commit_path,
        name="S108 final research-candidate commit",
        audit=audit,
        category="predecessor",
    )
    _expect(commit.get("schema_version"), S108_COMMIT_SCHEMA_VERSION, "S108 schema")
    _expect(commit.get("status"), "committed", "S108 status")
    _expect(
        commit.get("decision"),
        "research_candidate_passed",
        "S108 research decision",
    )
    _expect(
        commit.get("research_candidate_completed"),
        True,
        "S108 completed flag",
    )
    _expect(commit.get("deployment_approved"), False, "S108 deployment flag")
    locked = _mapping(commit.get("locked_batch"), "S108 locked_batch")
    _expect(locked.get("access_count"), 1, "S108 locked access count")
    _expect(locked.get("rerun_count"), 0, "S108 locked rerun count")
    _expect(
        locked.get("result_based_reselection"),
        False,
        "S108 result-based reselection",
    )
    alignment = _mapping(commit.get("alignment_evidence"), "S108 alignment evidence")
    mode = _string(alignment.get("mode"), "S108 alignment mode")
    if mode not in {"oracle_target_boundary", "predicted_alignment"}:
        raise S200ContractError(f"unsupported S108 alignment mode: {mode}")
    _expect(
        alignment.get("deployment_claim_allowed"),
        False,
        "S108 alignment deployment claim",
    )
    if mode == "oracle_target_boundary":
        _expect(alignment.get("oracle_only"), True, "S108 oracle-only marker")
        _expect(
            alignment.get("predicted_alignment_evaluated"),
            False,
            "S108 predicted marker",
        )
    else:
        _expect(alignment.get("oracle_only"), False, "S108 oracle-only marker")
        _expect(
            alignment.get("predicted_alignment_evaluated"),
            True,
            "S108 predicted marker",
        )
    artifact_hashes = _mapping(commit.get("artifact_hashes"), "S108 artifact hashes")
    _expect(set(artifact_hashes), set(S108_ARTIFACT_HASH_NAMES), "S108 hash names")
    normalized_hashes = {
        name: _sha256(artifact_hashes[name], f"S108 artifact {name}")
        for name in S108_ARTIFACT_HASH_NAMES
    }
    observed_binding = _sha256(commit.get("binding_sha256"), "S108 binding")
    _expect(observed_binding, _binding_sha256(commit), "S108 binding")

    registry_contract = _mapping(s108["registry"], "S108 registry contract")
    registry_record = _mapping(commit.get("registry"), "S108 commit registry")
    registry_path = _relative_string(
        registry_contract["path"],
        "S108 registry path",
    )
    _expect(registry_record.get("path"), registry_path, "S108 registry path binding")
    registry_expected_sha = _sha256(
        registry_record.get("sha256"),
        "S108 registry SHA-256",
    )
    _expect(
        registry_record.get("run_id"),
        registry_contract.get("run_id"),
        "S108 registry run_id binding",
    )
    _expect(registry_record.get("status"), "completed", "S108 registry status binding")
    registry, registry_snapshot = _runtime_json(
        root=root,
        relative=registry_path,
        name="S108 completed registry",
        audit=audit,
        category="predecessor",
    )
    _expect(registry_snapshot.sha256, registry_expected_sha, "S108 registry hash")
    _expect(registry.get("schema_version"), 1, "registry schema")
    _expect(registry.get("run_id"), registry_contract.get("run_id"), "registry run_id")
    _expect(registry.get("status"), "completed", "registry status")
    _expect(registry.get("failure"), None, "registry failure")
    artifacts = _list(registry.get("artifacts"), "registry artifacts")
    if len(artifacts) < len(S108_ARTIFACT_HASH_NAMES):
        raise S200ContractError("S108 registry artifact list is incomplete")
    return PredecessorEvidence(
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        commit_binding_sha256=observed_binding,
        alignment_mode=mode,
        artifact_hashes=normalized_hashes,
    )


def _validate_external_attestation(
    *,
    root: Path,
    config: Mapping[str, Any],
    predecessor: PredecessorEvidence,
    audit: AccessAudit,
) -> ExternalEvidence:
    contract = _mapping(
        _mapping(config["s203"], "s203")["expert_holdout_attestation"],
        "S203 holdout attestation contract",
    )
    relative = _relative_string(contract["path"], "S203 holdout attestation path")
    record, snapshot = _runtime_json(
        root=root,
        relative=relative,
        name="S203 expert holdout aggregate attestation",
        audit=audit,
        category="external",
    )
    _expect(
        record.get("schema_version"),
        S203_ATTESTATION_SCHEMA_VERSION,
        "S203 attestation schema",
    )
    _expect(record.get("status"), "ready", "S203 attestation status")
    _expect(record.get("aggregate_only"), True, "S203 aggregate-only marker")
    for field_name in (
        "contains_rows",
        "contains_audio",
        "contains_transcripts",
        "contains_labels",
        "contains_predictions",
        "contains_raw_identifiers",
        "holdout_opened_for_model_selection",
    ):
        _expect(record.get(field_name), False, f"S203 attestation {field_name}")
    item_count = _integer(record.get("item_count"), "S203 item count", minimum=0)
    if not 1000 <= item_count <= 2000:
        raise S200ContractError("S203 item count must be in [1000, 2000]")
    _expect(record.get("independent_rater_count"), 2, "S203 rater count")
    _expect(record.get("adjudicator_count"), 1, "S203 adjudicator count")
    overlap = _mapping(record.get("overlap_counts"), "S203 overlap counts")
    expected_overlap_keys = {
        "s1_train_items",
        "s1_dev_items",
        "s1_stack_items",
        "s1_calibration_items",
        "s1_locked_test_items",
        "s1_selection_speakers",
        "s1_labeler_cohort",
    }
    _expect(set(overlap), expected_overlap_keys, "S203 overlap fields")
    for field_name, count in overlap.items():
        _expect(count, 0, f"S203 overlap {field_name}")
    blinding = _mapping(record.get("blinding"), "S203 blinding")
    for field_name in (
        "s1_predictions_hidden_from_s2_trainers",
        "s2_holdout_gold_hidden_until_freeze",
        "reviewers_blind_to_s1_and_s2",
        "adjudication_preserves_original_ratings",
    ):
        _expect(blinding.get(field_name), True, f"S203 blinding {field_name}")
    governance = _mapping(record.get("governance"), "S203 governance")
    for field_name in (
        "institutional_approval_complete",
        "consent_complete",
        "withdrawal_plan_complete",
        "retention_and_destruction_plan_complete",
        "restricted_redistribution",
    ):
        _expect(governance.get(field_name), True, f"S203 governance {field_name}")
    _expect(
        record.get("s108_commit_sha256"),
        predecessor.commit_snapshot.sha256,
        "S203 S108 commit binding",
    )
    split_hashes_raw = _mapping(record.get("split_hashes"), "S203 split hashes")
    _expect(
        set(split_hashes_raw),
        {"s2_auditor_train", "s2_auditor_validation", "s2_expert_holdout"},
        "S203 split hash names",
    )
    split_hashes = {
        name: _sha256(value, f"S203 split hash {name}")
        for name, value in split_hashes_raw.items()
    }
    rater_hashes_raw = _mapping(
        record.get("rater_cohort_hashes"),
        "S203 rater cohort hashes",
    )
    _expect(
        set(rater_hashes_raw),
        {"s1_labeler_cohort", "s2_labeler_cohort"},
        "S203 rater cohort hash names",
    )
    rater_hashes = {
        name: _sha256(value, f"S203 rater hash {name}")
        for name, value in rater_hashes_raw.items()
    }
    if rater_hashes["s1_labeler_cohort"] == rater_hashes["s2_labeler_cohort"]:
        raise S200ContractError("S1 and S2 rater cohort hashes must differ")
    observed_binding = _sha256(record.get("binding_sha256"), "S203 binding")
    _expect(observed_binding, _binding_sha256(record), "S203 binding")
    return ExternalEvidence(
        snapshot=snapshot,
        binding_sha256=observed_binding,
        item_count=item_count,
        split_hashes=split_hashes,
        rater_cohort_hashes=rater_hashes,
    )


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    _, observed = _regular_file_snapshot(root, expected.path, name)
    if not _snapshot_equal(observed, expected):
        raise S200ContractError(f"{name} changed before publication")


def _secure_directory(root: Path, relative_parent: Path) -> Path:
    current = root
    for part in relative_parent.parts:
        if part in ("", ".", ".."):
            raise S200PublicationError("output parent contains traversal")
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir(mode=0o750)
            except FileExistsError:
                pass
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise S200PublicationError("output parent must be a non-symlink directory")
    return current


def _atomic_no_overwrite_write(
    *,
    root: Path,
    relative: str,
    data: bytes,
    fault_injection: str | None,
) -> Path:
    normalized = _relative_string(relative, "readiness lock output")
    path = Path(normalized)
    parent = _secure_directory(root, path.parent)
    parent_fd = os.open(
        parent,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary_name = f".{path.name}.part-{uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o640,
            dir_fd=parent_fd,
        )
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise S200PublicationError("short write while staging readiness lock")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if fault_injection == "after_temporary_write":
            raise S200PublicationError("synthetic fault after temporary write")
        if fault_injection == "before_publish":
            raise S200PublicationError("synthetic fault before publish")
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise S200PublicationError(
                "readiness lock already exists; no overwrite"
            ) from exc
        os.fsync(parent_fd)
        os.unlink(temporary_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent_fd)
    return parent / path.name


def _readiness_lock(
    *,
    config: Mapping[str, Any],
    static: StaticValidation,
    predecessor: PredecessorEvidence,
    external: ExternalEvidence,
) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": READINESS_LOCK_SCHEMA_VERSION,
        "status": "readiness_contract_published",
        "s200_completion_claim_allowed": False,
        "s201_trainer_implemented": False,
        "s202_trainer_implemented": False,
        "actual_model_download_count": 0,
        "actual_model_training_count": 0,
        "gpu_access_count": 0,
        "data_read_count": 0,
        "protected_data_read_count": 0,
        "holdout_read_count": 0,
        "locked_test_read_count": 0,
        "metric_count": 0,
        "result_count": 0,
        "config_sha256": static.config_sha256,
        "canonical_source_hashes": {
            snapshot.path: snapshot.sha256 for snapshot in static.source_snapshots
        },
        "independence_contract_sha256": static.independence_contract_sha256,
        "data_card_sha256": static.data_card_sha256,
        "s108_predecessor": {
            "commit_sha256": predecessor.commit_snapshot.sha256,
            "registry_sha256": predecessor.registry_snapshot.sha256,
            "commit_binding_sha256": predecessor.commit_binding_sha256,
            "decision": "research_candidate_passed",
            "alignment_mode": predecessor.alignment_mode,
            "deployment_approved": False,
        },
        "s203_expert_holdout_attestation": {
            "sha256": external.snapshot.sha256,
            "binding_sha256": external.binding_sha256,
            "item_count": external.item_count,
            "aggregate_only": True,
            "row_level_accessed": False,
        },
        "dag": config["dag"],
        "data_card": config["data_card"],
        "downstream_execution_allowed": {
            "s201": False,
            "reason": "exact checkpoint, normalization, and WER threshold require S201 preregistration",
            "s202": False,
            "reason_s202": "exact checkpoint and ensemble policy require S202 preregistration",
            "s203": False,
            "reason_s203": "S201 and S202 immutable completed commits do not exist",
        },
    }
    payload["binding_sha256"] = _binding_sha256(payload)
    return payload


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    fault_injection: str | None = None,
    before_commit_hook: Callable[[], None] | None = None,
) -> PreflightResult:
    """Validate gates in order and publish one aggregate lock if all exist."""

    if fault_injection not in {None, "after_temporary_write", "before_publish"}:
        raise S200ContractError("unknown fault injection")
    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
        )
        audit.source_pin_read_count += len(static.source_snapshots)
        predecessor = _validate_s108_predecessor(
            root=root,
            config=config,
            audit=audit,
        )
        external = _validate_external_attestation(
            root=root,
            config=config,
            predecessor=predecessor,
            audit=audit,
        )
        if before_commit_hook is not None:
            before_commit_hook()

        # Precommit revalidation closes source/predecessor/attestation TOCTOU.
        repeated_static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
        )
        audit.source_pin_read_count += len(repeated_static.source_snapshots)
        if repeated_static != static:
            raise S200ContractError("static contract changed before publication")
        _verify_snapshot(root, predecessor.commit_snapshot, "S108 commit")
        _verify_snapshot(root, predecessor.registry_snapshot, "S108 registry")
        _verify_snapshot(root, external.snapshot, "S203 attestation")

        lock = _readiness_lock(
            config=config,
            static=static,
            predecessor=predecessor,
            external=external,
        )
        data = canonical_json_bytes(lock, pretty=True)
        output_relative = _relative_string(
            _mapping(config["outputs"], "outputs")["readiness_lock_path"],
            "readiness lock path",
        )
        published_path = _atomic_no_overwrite_write(
            root=root,
            relative=output_relative,
            data=data,
            fault_injection=fault_injection,
        )
        audit.output_count += 1
        return PreflightResult(
            readiness_lock_path=published_path,
            readiness_lock_sha256=hashlib.sha256(data).hexdigest(),
            readiness_lock=lock,
            audit=audit.as_dict(),
        )
    except S200ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    except OSError as exc:
        raise S200PublicationError(
            str(exc),
            stage="publication",
            audit=audit,
        ) from exc


def config_only_result(static: StaticValidation) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "implementation_status": "static_contract_only",
        "config_sha256": static.config_sha256,
        "independence_contract_sha256": static.independence_contract_sha256,
        "data_card_sha256": static.data_card_sha256,
        "source_pin_read_count": len(static.source_snapshots),
        "predecessor_metadata_read_attempt_count": 0,
        "predecessor_metadata_read_count": 0,
        "external_aggregate_read_attempt_count": 0,
        "external_aggregate_read_count": 0,
        "data_read_count": 0,
        "model_read_count": 0,
        "protected_data_read_count": 0,
        "holdout_read_count": 0,
        "locked_test_read_count": 0,
        "gpu_access_count": 0,
        "metric_count": 0,
        "result_count": 0,
        "output_count": 0,
        "s200_completion_claim_allowed": False,
        "s201_trainer_implemented": False,
        "s202_trainer_implemented": False,
    }


def failure_result(exc: S200ContractError) -> Mapping[str, Any]:
    audit = exc.audit or AccessAudit()
    return {
        "status": "failed_closed",
        "stage": exc.stage,
        "error": str(exc),
        **audit.as_dict(),
        "s200_completion_claim_allowed": False,
        "s201_trainer_implemented": False,
        "s202_trainer_implemented": False,
    }


__all__ = [
    "AccessAudit",
    "CONFIG_SCHEMA_VERSION",
    "DATA_CARD_SCHEMA_VERSION",
    "ExternalEvidence",
    "FileSnapshot",
    "PredecessorEvidence",
    "PreflightResult",
    "READINESS_LOCK_SCHEMA_VERSION",
    "S200ContractError",
    "S200PublicationError",
    "StaticValidation",
    "canonical_json_bytes",
    "config_only_result",
    "failure_result",
    "load_config",
    "run_full_preflight",
    "validate_static_config",
]
