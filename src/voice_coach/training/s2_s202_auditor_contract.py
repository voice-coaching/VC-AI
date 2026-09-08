"""Fail-closed S202 independent-auditor static readiness contract.

This module deliberately does not implement HuBERT inference, feature
extraction, pairwise/ordinal training, metric computation, checkpoint
publication, or expert-holdout evaluation.  It validates only a C1 static
policy and, in full mode, an immutable predecessor chain in this exact order:

1. a passed S108 final S1 commit;
2. the S200 independence-readiness lock bound to that S108 commit;
3. a completed S201 content-hard-gate commit bound to both predecessors.

The repository configuration intentionally keeps every undocumented S202
model, label, objective, selection, metric, confidence-interval, and gate
choice at ``TBD_PREREGISTRATION``.  Consequently a successful predecessor
chain still ends at a preregistration blocker before any data or model path is
resolved.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml  # type: ignore[import-untyped]

from voice_coach.training import s2_s201_whisper_contract as s201


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "static_preflight_only"
TBD = "TBD_PREREGISTRATION"
S201_COMMIT_SCHEMA = "voice_coach.s2_s201_whisper_content_commit.v1"
S201_REGISTRY_RUN_ID = "aihub71490-s2-s201-whisper-content-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessors",
    "independence",
    "representation_model",
    "expert_labels",
    "splits_and_holdout",
    "training_protocol",
    "objectives",
    "evaluation",
    "privacy_and_license",
    "execution_order",
    "outputs",
}

INPUT_ALLOWLIST = (
    "item_id_sha256",
    "speaker_id_sha256",
    "audio_waveform",
    "content_gate_pass",
)

FEATURE_ALLOWLIST = (
    "independent_ssl_hidden_state",
    "independent_ssl_attention_mask",
    "s2_intrinsic_uncertainty",
)

FORBIDDEN_FIELDS = (
    "realized_phone",
    "alignment_operation",
    "error_tag",
    "error_count",
    "phone_interval",
    "word_interval",
    "oracle_alignment",
    "predicted_alignment",
    "s1_alignment_confidence",
    "s1_prediction",
    "s1_score",
    "s1_logit",
    "s1_probability",
    "s1_confidence",
    "s1_threshold",
    "s1_label",
    "s1_wavlm_embedding",
    "s1_explicit_feature",
    "aihub_silver_phone_label",
    "phone_derived_word_proxy",
    "whisper_transcript",
    "whisper_wer",
    "whisper_cer",
    "whisper_content_mismatch_score",
    "whisper_asr_score",
    "whisper_timestamp",
)

S201_ARTIFACT_NAMES = (
    "s201_config",
    "checkpoint_source_card",
    "transcript_normalization_policy",
    "aggregate_wer_cer_content_report",
    "privacy_safe_content_gate_predictions",
    "independent_validation_receipt",
)

EXPECTED_ORDER = (
    "validate_s202_c1_config_and_source_pins",
    "deep_validate_s108_final_passed_commit",
    "deep_validate_s200_readiness_lock",
    "deep_validate_s201_completed_content_hard_gate_commit",
    "validate_frozen_s202_preregistration",
    "validate_s2_expert_label_and_split_attestations",
    "validate_exact_local_only_independent_representation_bundle",
    "stop_ready_for_separate_production_trainer_implementation",
)


class S202ContractError(ValueError):
    """Raised before an unsafe, leaking, or unpreregistered S202 action."""

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
            raise S202ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters proving that static and predecessor gates precede runtime access."""

    source_pin_read_count: int = 0
    predecessor_metadata_read_attempt_count: int = 0
    predecessor_metadata_read_count: int = 0
    predecessor_artifact_read_count: int = 0
    s108_validation_count: int = 0
    s200_validation_count: int = 0
    s201_validation_count: int = 0
    preregistration_read_attempt_count: int = 0
    external_attestation_read_attempt_count: int = 0
    data_path_resolution_count: int = 0
    data_manifest_read_count: int = 0
    data_row_read_count: int = 0
    audio_read_count: int = 0
    model_path_resolution_count: int = 0
    model_metadata_read_count: int = 0
    model_weight_read_count: int = 0
    model_download_count: int = 0
    protected_data_read_count: int = 0
    expert_holdout_read_count: int = 0
    s1_prediction_read_count: int = 0
    oracle_alignment_read_count: int = 0
    silver_label_read_count: int = 0
    whisper_asr_score_read_count: int = 0
    locked_test_read_count: int = 0
    gpu_access_count: int = 0
    metric_count: int = 0
    prediction_count: int = 0
    checkpoint_count: int = 0
    result_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__dataclass_fields__}


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
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class S108Evidence:
    inner: s201.S108Evidence
    s201_config: Mapping[str, Any]
    s201_static: s201.StaticValidation


@dataclass(frozen=True, slots=True)
class S200Evidence:
    inner: s201.S200Evidence


@dataclass(frozen=True, slots=True)
class S201Evidence:
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    artifact_snapshots: tuple[FileSnapshot, ...]
    binding_sha256: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    s108_commit_sha256: str
    s200_readiness_lock_sha256: str
    s201_commit_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S202ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S202ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S202ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S202ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S202ContractError(f"{name} must be at least {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S202ContractError(f"{name} must be a boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise S202ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S202ContractError(f"{name} must be {expected!r}, got {actual!r}")


def _expect_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise S202ContractError(f"{name} keys differ; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S202ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_nonfinite_tree(nested, f"{name}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_nonfinite_tree(nested, f"{name}[{index}]")


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S202ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise S202ContractError(f"JSON contains a non-finite constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _binding_sha256(value: Mapping[str, Any]) -> str:
    body = {key: nested for key, nested in value.items() if key != "binding_sha256"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or text != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise S202ContractError(f"{name} must be a canonical relative path")
    return text


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise
        if stat.S_ISLNK(mode):
            raise S202ContractError(f"{name} may not contain a symlink: {relative}")
    return current


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    path = _reject_symlink_chain(root, _relative_string(relative, name), name)
    try:
        descriptor = path.open("rb")
    except OSError as exc:
        raise S202ContractError(f"cannot read {name}: {relative}: {exc}") from exc
    with descriptor:
        before = os.fstat(descriptor.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise S202ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while chunk := descriptor.read(1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor.fileno())
    data = b"".join(chunks)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(data) != before.st_size:
        raise S202ContractError(f"{name} changed while being read")
    final_path = _reject_symlink_chain(root, relative, name)
    final = final_path.lstat()
    if (final.st_dev, final.st_ino) != (before.st_dev, before.st_ino):
        raise S202ContractError(f"{name} path identity changed while being read")
    return (
        data,
        FileSnapshot(
            path=relative,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            device=before.st_dev,
            inode=before.st_ino,
            modified_ns=before.st_mtime_ns,
        ),
    )


def _snapshot_file(root: Path, relative: str, name: str) -> FileSnapshot:
    return _read_file(root, relative, name)[1]


def _verify_snapshot(
    root: Path,
    expected: FileSnapshot | s201.FileSnapshot,
    name: str,
) -> None:
    observed = _snapshot_file(root, expected.path, name)
    observed_identity = (
        observed.path,
        observed.sha256,
        observed.size_bytes,
        observed.device,
        observed.inode,
        observed.modified_ns,
    )
    expected_identity = (
        expected.path,
        expected.sha256,
        expected.size_bytes,
        expected.device,
        expected.inode,
        expected.modified_ns,
    )
    if observed_identity != expected_identity:
        raise S202ContractError(f"{name} changed during preflight")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S202ContractError(f"{name} is not strict UTF-8 JSON") from exc
    return _mapping(parsed, name)


def _read_json(
    *,
    root: Path,
    relative: str,
    name: str,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    audit.predecessor_metadata_read_attempt_count += 1
    data, snapshot = _read_file(root, relative, name)
    audit.predecessor_metadata_read_count += 1
    return _parse_json(data, name), snapshot


def load_config(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise S202ContractError("S202 config may not be a symlink")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise S202ContractError(f"cannot read S202 config: {exc}") from exc
    try:
        parsed = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise S202ContractError(f"invalid S202 YAML: {exc}") from exc
    config = _mapping(parsed, "S202 config")
    _reject_nonfinite_tree(config, "S202 config")
    return config


def _validate_experiment(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config["experiment"], "experiment")
    _expect(experiment.get("id"), "s2_s202_independent_auditor_v1", "experiment id")
    _expect(experiment.get("stage"), "s202_independent_representation_auditor", "stage")
    _expect(experiment.get("implementation_status"), STATIC_STATUS, "status")
    for field in (
        "production_trainer_implemented",
        "actual_model_download_allowed",
        "actual_training_allowed",
        "row_level_data_access_allowed",
        "expert_holdout_access_allowed",
        "gpu_access_allowed",
        "metric_generation_allowed",
        "prediction_generation_allowed",
        "checkpoint_generation_allowed",
        "result_generation_allowed",
        "completion_claim_allowed",
    ):
        _expect(_boolean(experiment.get(field), field), False, field)


def _validate_predecessors(config: Mapping[str, Any]) -> None:
    predecessors = _mapping(config["predecessors"], "predecessors")
    for field in (
        "validate_before_preregistration",
        "validate_before_data_path_resolution",
        "validate_before_model_path_resolution",
        "validate_before_output_path_resolution",
    ):
        _expect(_boolean(predecessors.get(field), field), True, field)
    s108_contract = _mapping(predecessors["s108_final"], "S108 predecessor")
    _relative_string(s108_contract.get("commit_path"), "S108 commit path")
    _relative_string(s108_contract.get("registry_path"), "S108 registry path")
    _expect(
        s108_contract.get("required_decision"),
        "research_candidate_passed",
        "S108 decision",
    )
    _expect(s108_contract.get("deep_validation_required"), True, "S108 deep validation")
    s200_contract = _mapping(predecessors["s200_readiness"], "S200 predecessor")
    _relative_string(s200_contract.get("config_path"), "S200 config path")
    _relative_string(s200_contract.get("lock_path"), "S200 lock path")
    _expect(
        s200_contract.get("required_status"),
        "readiness_contract_published",
        "S200 status",
    )
    _expect(s200_contract.get("deep_validation_required"), True, "S200 deep validation")
    s201_contract = _mapping(predecessors["s201_content_gate"], "S201 predecessor")
    _relative_string(s201_contract.get("config_path"), "S201 config path")
    _relative_string(s201_contract.get("commit_path"), "S201 commit path")
    _relative_string(s201_contract.get("registry_path"), "S201 registry path")
    _relative_string(s201_contract.get("artifact_root"), "S201 artifact root")
    _expect(s201_contract.get("schema_version"), S201_COMMIT_SCHEMA, "S201 schema")
    _expect(s201_contract.get("required_status"), "committed", "S201 status")
    _expect(
        s201_contract.get("required_decision"),
        "content_hard_gate_passed",
        "S201 decision",
    )
    _expect(
        tuple(_list(s201_contract.get("required_artifact_names"), "S201 artifacts")),
        S201_ARTIFACT_NAMES,
        "S201 artifact names",
    )
    _expect(s201_contract.get("deep_validation_required"), True, "S201 deep validation")


def _validate_independence(config: Mapping[str, Any]) -> None:
    independence = _mapping(config["independence"], "independence")
    _expect(independence.get("s1_backbone_family"), "wavlm", "S1 backbone")
    _expect(
        independence.get("s202_backbone_policy"),
        "hubert_or_separately_approved_non_wavlm_ssl",
        "S202 backbone policy",
    )
    _expect(independence.get("alignment_mode"), "alignment_free_utterance", "alignment")
    _expect(
        tuple(_list(independence.get("raw_input_allowlist"), "input allowlist")),
        INPUT_ALLOWLIST,
        "input allowlist",
    )
    _expect(
        tuple(_list(independence.get("model_feature_allowlist"), "feature allowlist")),
        FEATURE_ALLOWLIST,
        "feature allowlist",
    )
    _expect(
        tuple(_list(independence.get("forbidden_fields"), "forbidden fields")),
        FORBIDDEN_FIELDS,
        "forbidden fields",
    )
    for field in (
        "content_gate_pass_is_routing_only",
        "s1_predictions_allowed",
        "s1_labels_allowed",
        "s1_features_allowed",
        "s1_thresholds_allowed",
        "oracle_alignment_allowed",
        "predicted_alignment_allowed",
        "aihub_silver_labels_allowed",
        "whisper_transcript_as_feature_allowed",
        "whisper_asr_score_as_feature_or_target_allowed",
        "locked_test_access_allowed",
    ):
        expected = field == "content_gate_pass_is_routing_only"
        _expect(_boolean(independence.get(field), field), expected, field)


def _validate_model(config: Mapping[str, Any]) -> None:
    model = _mapping(config["representation_model"], "representation model")
    source = _mapping(model["verified_family_source"], "verified family source")
    _expect(source.get("family"), "hubert", "verified family")
    revision = _string(source.get("fairseq_source_revision"), "fairseq revision")
    if GIT_REVISION_RE.fullmatch(revision) is None:
        raise S202ContractError("fairseq source revision must be a 40-hex commit")
    _expect(source.get("license"), "MIT", "HuBERT source license")
    _sha256(source.get("license_sha256"), "HuBERT license hash")
    _expect(
        source.get("evidence_scope"),
        "family_and_license_only_not_checkpoint_identity",
        "HuBERT evidence scope",
    )
    selection = _mapping(model["selection"], "model selection")
    for field in (
        "status",
        "family",
        "model_id",
        "checkpoint_revision",
        "preprocessor_id",
        "preprocessor_revision",
        "weight_manifest_sha256",
        "bundle_root",
        "bundle_attestation_sha256",
    ):
        _expect(selection.get(field), TBD, f"model selection {field}")
    for field in (
        "local_files_only",
        "network_fallback_allowed",
        "automatic_download_allowed",
        "execution_allowed",
    ):
        expected = field == "local_files_only"
        _expect(_boolean(selection.get(field), field), expected, field)
    _expect(
        selection.get("separate_representation_justification_status"),
        TBD,
        "separate representation justification",
    )


def _validate_labels_splits_protocol(config: Mapping[str, Any]) -> None:
    labels = _mapping(config["expert_labels"], "expert labels")
    _expect(labels.get("source"), "independent_s2_experts", "label source")
    _expect(
        labels.get("allowed_families"),
        ["pairwise_preference", "ordinal_audit"],
        "label families",
    )
    for section_name in ("pairwise_schema", "ordinal_schema", "rater_governance"):
        section = _mapping(labels[section_name], section_name)
        for value in section.values():
            if isinstance(value, Mapping):
                for nested in value.values():
                    _expect(nested, TBD, f"{section_name} nested preregistration")
            else:
                _expect(value, TBD, f"{section_name} preregistration")
    for field in (
        "phone_derived_proxy_allowed",
        "s1_prediction_derived_label_allowed",
        "aihub_silver_label_allowed",
        "whisper_asr_score_derived_label_allowed",
    ):
        _expect(_boolean(labels.get(field), field), False, field)

    splits = _mapping(config["splits_and_holdout"], "splits")
    _expect(
        splits.get("split_names"),
        ["s2_auditor_train", "s2_auditor_validation", "s2_expert_holdout"],
        "split names",
    )
    for dimension in ("speaker", "prompt", "item"):
        _expect(
            splits.get(f"require_{dimension}_disjoint"),
            True,
            f"{dimension} disjoint",
        )
        _expect(
            splits.get(f"{dimension}_overlap_attestation_status"),
            TBD,
            f"{dimension} attestation",
        )
    _expect(splits.get("all_overlap_counts_required"), 0, "overlap count")
    _expect(splits.get("s1_split_reuse_allowed"), False, "S1 split reuse")
    _expect(splits.get("expert_holdout_owner"), "s203_only", "holdout owner")
    _expect(splits.get("s202_expert_holdout_access_allowed"), False, "holdout access")
    _expect(splits.get("expert_holdout_max_access_count"), 1, "holdout one-shot")
    _expect(splits.get("holdout_attestation_status"), TBD, "holdout attestation")

    protocol = _mapping(config["training_protocol"], "training protocol")
    _expect(protocol.get("training_seeds"), [42, 43, 44], "training seeds")
    _expect(protocol.get("seed_artifacts_kept_separate"), True, "seed separation")
    _expect(protocol.get("result_based_reselection_allowed"), False, "reselection")
    _expect(
        protocol.get("expert_holdout_visible_before_all_freezes"),
        False,
        "holdout visibility",
    )
    for field in (
        "inner_split_policy",
        "inner_selection_metric",
        "inner_selection_direction",
        "max_epochs",
        "early_stopping_policy",
        "dev_evaluation_schedule",
        "dev_model_selection_policy",
        "ensemble_policy",
        "freeze_manifest_sha256",
    ):
        _expect(protocol.get(field), TBD, f"training protocol {field}")


def _validate_objectives_evaluation(config: Mapping[str, Any]) -> None:
    objectives = _mapping(config["objectives"], "objectives")
    _expect(
        objectives.get("families"),
        ["pairwise_preference", "ordinal_audit"],
        "objective families",
    )
    for field in (
        "pairwise_loss",
        "ordinal_loss",
        "multi_objective_weighting",
        "uncertainty_definition",
        "three_state_decision_rule",
    ):
        _expect(objectives.get(field), TBD, f"objective {field}")
    _expect(
        objectives.get("output_states"),
        ["approve", "disagreement", "uncertain"],
        "output states",
    )
    evaluation = _mapping(config["evaluation"], "evaluation")
    for field in (
        "primary_metric",
        "secondary_metrics",
        "confidence_interval_method",
        "confidence_level",
        "resampling_unit",
        "resampling_replicates",
        "multiplicity_control",
        "development_gates",
        "promotion_gate",
    ):
        _expect(evaluation.get(field), TBD, f"evaluation {field}")
    _expect(evaluation.get("expert_holdout_evaluated_by_s202"), False, "holdout eval")
    _expect(evaluation.get("metric_generation_allowed"), False, "metric generation")


def _validate_governance_and_outputs(config: Mapping[str, Any]) -> None:
    governance = _mapping(config["privacy_and_license"], "privacy and license")
    _expect(governance.get("raw_audio_zone"), "R0", "raw audio zone")
    _expect(governance.get("pseudonymous_feature_zone"), "D1", "feature zone")
    _expect(governance.get("aggregate_report_zone"), "A1", "report zone")
    _expect(governance.get("public_row_level_release_allowed"), False, "row release")
    _expect(governance.get("exact_model_license_reaudit_required"), True, "license")
    _expect(governance.get("expert_data_governance_attestation_status"), TBD, "data")
    order = tuple(_list(config["execution_order"], "execution order"))
    _expect(order, EXPECTED_ORDER, "execution order")
    outputs = _mapping(config["outputs"], "outputs")
    _expect(outputs.get("production_trainer_in_scope"), False, "trainer scope")
    _expect(outputs.get("current_output_count"), 0, "current output count")
    _expect(outputs.get("output_path_resolution_allowed"), False, "output resolution")
    _expect(outputs.get("completion_claim_allowed"), False, "completion claim")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> StaticValidation:
    """Validate the static fail-closed policy and all C1 source pins."""

    root = workspace_root.resolve(strict=True)
    if config_path.is_symlink():
        raise S202ContractError("S202 config may not be a symlink")
    _expect_keys(config, TOP_LEVEL_KEYS, "S202 top level")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema version")
    _validate_experiment(config)
    _validate_predecessors(config)
    _validate_independence(config)
    _validate_model(config)
    _validate_labels_splits_protocol(config)
    _validate_objectives_evaluation(config)
    _validate_governance_and_outputs(config)

    sources = _mapping(config["canonical_sources"], "canonical sources")
    _expect(sources.get("access_scope"), "C1_code_config_docs_only", "source scope")
    pins = _list(sources.get("pins"), "source pins")
    if not pins:
        raise S202ContractError("at least one canonical source pin is required")
    snapshots: list[FileSnapshot] = []
    observed_paths: set[str] = set()
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin["path"], f"source pin {index} path")
        if relative in observed_paths:
            raise S202ContractError(f"duplicate canonical source pin: {relative}")
        observed_paths.add(relative)
        expected_hash = _sha256(pin["sha256"], f"source pin {index} hash")
        snapshot = _snapshot_file(root, relative, f"source pin {index}")
        _expect(snapshot.sha256, expected_hash, f"source pin {relative}")
        snapshots.append(snapshot)
    config_relative = config_path.resolve(strict=True).relative_to(root).as_posix()
    config_snapshot = _snapshot_file(root, config_relative, "S202 config")
    policy_sections = {
        name: config[name]
        for name in (
            "independence",
            "representation_model",
            "expert_labels",
            "splits_and_holdout",
            "training_protocol",
            "objectives",
            "evaluation",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        source_snapshots=tuple(snapshots),
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy_sections)).hexdigest(),
    )


def _copy_s201_audit(source: s201.AccessAudit, target: AccessAudit) -> None:
    for name in (
        "predecessor_metadata_read_attempt_count",
        "predecessor_metadata_read_count",
        "predecessor_artifact_read_count",
        "data_path_resolution_count",
        "data_manifest_read_count",
        "data_row_read_count",
        "audio_read_count",
        "model_path_resolution_count",
        "model_metadata_read_count",
        "model_weight_read_count",
        "model_download_count",
        "protected_data_read_count",
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "locked_test_read_count",
        "gpu_access_count",
        "metric_count",
        "prediction_count",
        "result_count",
        "output_path_resolution_count",
        "output_count",
    ):
        setattr(target, name, int(getattr(source, name)))


def deep_validate_s108_final(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S108Evidence:
    """Use the S201 public static contract and deep S108 validator."""

    s201_contract = _mapping(
        _mapping(config["predecessors"], "predecessors")["s201_content_gate"],
        "S201 predecessor",
    )
    relative = _relative_string(s201_contract["config_path"], "S201 config path")
    s201_config = s201.load_config(root / relative)
    try:
        local_predecessors = _mapping(config["predecessors"], "S202 predecessors")
        s201_predecessors = _mapping(
            s201_config["predecessors"],
            "S201 predecessors",
        )
        local_s108 = _mapping(local_predecessors["s108_final"], "S202 S108 contract")
        pinned_s108 = _mapping(s201_predecessors["s108_final"], "S201 S108 contract")
        _expect(
            local_s108.get("commit_path"),
            pinned_s108.get("commit_path"),
            "S108 commit path agreement",
        )
        _expect(
            local_s108.get("registry_path"),
            pinned_s108.get("registry_path"),
            "S108 registry path agreement",
        )
        local_s200 = _mapping(
            local_predecessors["s200_readiness"],
            "S202 S200 contract",
        )
        pinned_s200 = _mapping(
            s201_predecessors["s200_readiness_lock"],
            "S201 S200 contract",
        )
        _expect(
            local_s200.get("config_path"),
            pinned_s200.get("config_path"),
            "S200 config path agreement",
        )
        _expect(
            local_s200.get("lock_path"),
            pinned_s200.get("lock_path"),
            "S200 lock path agreement",
        )
        s201_static = s201.validate_static_config(
            workspace_root=root,
            config_path=root / relative,
            config=s201_config,
        )
        inner_audit = s201.AccessAudit()
        inner_audit.source_pin_read_count = len(s201_static.source_snapshots)
        inner = s201._deep_validate_s108(  # noqa: SLF001
            root=root,
            config=s201_config,
            audit=inner_audit,
        )
    except s201.S201ContractError as exc:
        if "inner_audit" in locals():
            _copy_s201_audit(inner_audit, audit)
        raise S202ContractError(
            f"S108 deep validation failed: {exc}",
            stage="predecessor_s108",
            audit=audit,
        ) from exc
    _copy_s201_audit(inner_audit, audit)
    audit.s108_validation_count = 1
    return S108Evidence(
        inner=inner,
        s201_config=s201_config,
        s201_static=s201_static,
    )


def deep_validate_s200_readiness(
    *,
    root: Path,
    s108: S108Evidence,
    audit: AccessAudit,
) -> S200Evidence:
    """Deep-validate the S200 lock after S108, never before."""

    inner_audit = s201.AccessAudit()
    try:
        inner = s201._deep_validate_s200_lock(  # noqa: SLF001
            root=root,
            config=s108.s201_config,
            s108=s108.inner,
            audit=inner_audit,
        )
    except s201.S201ContractError as exc:
        base_attempts = audit.predecessor_metadata_read_attempt_count
        base_reads = audit.predecessor_metadata_read_count
        base_artifacts = audit.predecessor_artifact_read_count
        _copy_s201_audit(inner_audit, audit)
        audit.predecessor_metadata_read_attempt_count += base_attempts
        audit.predecessor_metadata_read_count += base_reads
        audit.predecessor_artifact_read_count += base_artifacts
        raise S202ContractError(
            f"S200 deep validation failed: {exc}",
            stage="predecessor_s200",
            audit=audit,
        ) from exc
    audit.predecessor_metadata_read_attempt_count += (
        inner_audit.predecessor_metadata_read_attempt_count
    )
    audit.predecessor_metadata_read_count += inner_audit.predecessor_metadata_read_count
    audit.predecessor_artifact_read_count += inner_audit.predecessor_artifact_read_count
    audit.s200_validation_count = 1
    return S200Evidence(inner=inner)


def deep_validate_s201_completed(
    *,
    root: Path,
    config: Mapping[str, Any],
    s108: S108Evidence,
    s200: S200Evidence,
    audit: AccessAudit,
) -> S201Evidence:
    """Deep-validate a completed S201 commit and every registered artifact."""

    contract = _mapping(
        _mapping(config["predecessors"], "predecessors")["s201_content_gate"],
        "S201 predecessor",
    )
    artifact_root = _relative_string(contract["artifact_root"], "S201 artifact root")
    commit_relative = _relative_string(contract["commit_path"], "S201 commit path")
    if not commit_relative.startswith(artifact_root + "/"):
        raise S202ContractError("S201 commit escapes the pinned artifact root")
    commit, commit_snapshot = _read_json(
        root=root,
        relative=commit_relative,
        name="S201 completed commit",
        audit=audit,
    )
    _expect(commit.get("schema_version"), S201_COMMIT_SCHEMA, "S201 commit schema")
    _expect(commit.get("status"), "committed", "S201 commit status")
    _expect(commit.get("decision"), "content_hard_gate_passed", "S201 decision")
    _expect(commit.get("content_hard_gate_completed"), True, "S201 completion")
    _expect(commit.get("production_runner_implemented"), True, "S201 runner")
    _expect(commit.get("hard_gate_bypassed"), False, "S201 bypass")
    _expect(commit.get("failed_content_rescued_by_s202"), False, "S201 rescue")
    _expect(commit.get("fine_phone_or_prosody_role"), False, "S201 fine role")
    _expect(commit.get("content_gate_evaluation_count"), 1, "S201 gate evaluation")
    _integer(commit.get("metric_count"), "S201 metric count", minimum=1)
    _integer(commit.get("prediction_count"), "S201 prediction count", minimum=1)
    _sha256(
        commit.get("threshold_preregistration_sha256"),
        "S201 threshold preregistration SHA",
    )
    _sha256(
        commit.get("model_bundle_attestation_sha256"),
        "S201 model attestation SHA",
    )
    data_attestations = _mapping(
        commit.get("data_attestation_hashes"),
        "S201 data attestations",
    )
    _expect(
        set(data_attestations),
        {"fleurs_ko_kr", "zeroth_korean", "s2_expert_content"},
        "S201 data attestation names",
    )
    for name, digest in data_attestations.items():
        _sha256(digest, f"S201 data attestation {name}")
    for field in (
        "s1_prediction_read_count",
        "oracle_alignment_read_count",
        "whisper_timestamp_feature_count",
        "locked_test_read_count",
        "result_based_reselection_count",
    ):
        _expect(commit.get(field), 0, f"S201 {field}")
    predecessor_binding = _mapping(commit.get("predecessors"), "S201 predecessors")
    _expect(
        predecessor_binding.get("s108_commit_sha256"),
        s108.inner.commit_snapshot.sha256,
        "S201 S108 binding",
    )
    _expect(
        predecessor_binding.get("s200_readiness_lock_sha256"),
        s200.inner.lock_snapshot.sha256,
        "S201 S200 binding",
    )
    _expect(
        predecessor_binding.get("s201_config_sha256"),
        s108.s201_static.config_sha256,
        "S201 config binding",
    )
    artifact_hashes = _mapping(commit.get("artifact_hashes"), "S201 artifact hashes")
    _expect(set(artifact_hashes), set(S201_ARTIFACT_NAMES), "S201 artifact names")
    normalized_hashes = {
        name: _sha256(artifact_hashes[name], f"S201 artifact {name}")
        for name in S201_ARTIFACT_NAMES
    }
    observed_binding = _sha256(commit.get("binding_sha256"), "S201 binding")
    _expect(observed_binding, _binding_sha256(commit), "S201 binding")

    registry_binding = _mapping(commit.get("registry"), "S201 registry binding")
    registry_path = _relative_string(contract["registry_path"], "S201 registry path")
    _expect(registry_binding.get("path"), registry_path, "S201 registry path binding")
    _expect(registry_binding.get("run_id"), S201_REGISTRY_RUN_ID, "S201 run id")
    _expect(registry_binding.get("status"), "completed", "S201 registry status")
    registry_expected_sha = _sha256(
        registry_binding.get("sha256"),
        "S201 registry SHA",
    )
    registry, registry_snapshot = _read_json(
        root=root,
        relative=registry_path,
        name="S201 completed registry",
        audit=audit,
    )
    _expect(registry_snapshot.sha256, registry_expected_sha, "S201 registry hash")
    _expect(registry.get("schema_version"), 1, "S201 registry schema")
    _expect(registry.get("run_id"), S201_REGISTRY_RUN_ID, "S201 registry run id")
    _expect(registry.get("status"), "completed", "S201 registry completed")
    _expect(registry.get("failure"), None, "S201 registry failure")
    artifacts = _list(registry.get("artifacts"), "S201 registry artifacts")
    by_name: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(artifacts):
        artifact = _mapping(raw, f"S201 registry artifact {index}")
        name = _string(artifact.get("name"), f"S201 artifact {index} name")
        if name in by_name:
            raise S202ContractError(f"duplicate S201 registry artifact: {name}")
        by_name[name] = artifact
    _expect(set(by_name), set(S201_ARTIFACT_NAMES), "S201 registry artifact set")
    snapshots: list[FileSnapshot] = []
    for name in S201_ARTIFACT_NAMES:
        artifact = by_name[name]
        relative = _relative_string(artifact.get("path"), f"S201 {name} path")
        if not relative.startswith(artifact_root + "/"):
            raise S202ContractError(f"S201 artifact {name} escapes artifact root")
        expected_hash = _sha256(artifact.get("sha256"), f"S201 {name} SHA")
        _expect(expected_hash, normalized_hashes[name], f"S201 {name} commit hash")
        if name == "s201_config":
            _expect(
                expected_hash,
                s108.s201_static.config_sha256,
                "S201 artifact config hash",
            )
        expected_size = _integer(
            artifact.get("size_bytes"),
            f"S201 {name} size",
            minimum=1,
        )
        snapshot = _snapshot_file(root, relative, f"S201 artifact {name}")
        audit.predecessor_artifact_read_count += 1
        _expect(snapshot.sha256, expected_hash, f"S201 {name} hash")
        _expect(snapshot.size_bytes, expected_size, f"S201 {name} size")
        snapshots.append(snapshot)
    audit.s201_validation_count = 1
    return S201Evidence(
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        artifact_snapshots=tuple(snapshots),
        binding_sha256=observed_binding,
    )


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    before_final_verify_hook: Callable[[], None] | None = None,
) -> PreflightResult:
    """Run predecessor gates then fail closed at unresolved preregistration."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
        )
        audit.source_pin_read_count = len(static.source_snapshots)
        s108 = deep_validate_s108_final(root=root, config=config, audit=audit)
        s200 = deep_validate_s200_readiness(root=root, s108=s108, audit=audit)
        s201_evidence = deep_validate_s201_completed(
            root=root,
            config=config,
            s108=s108,
            s200=s200,
            audit=audit,
        )
        if before_final_verify_hook is not None:
            before_final_verify_hook()
        for source_snapshot in static.source_snapshots:
            _verify_snapshot(
                root,
                source_snapshot,
                f"preflight input {source_snapshot.path}",
            )
            audit.source_pin_read_count += 1
        for predecessor_snapshot in (
            s108.inner.commit_snapshot,
            s108.inner.registry_snapshot,
            s200.inner.lock_snapshot,
            s201_evidence.commit_snapshot,
            s201_evidence.registry_snapshot,
        ):
            _verify_snapshot(
                root,
                predecessor_snapshot,
                f"preflight input {predecessor_snapshot.path}",
            )
            audit.predecessor_metadata_read_count += 1
        for s108_artifact in s108.inner.artifact_snapshots:
            _verify_snapshot(
                root,
                s108_artifact,
                f"preflight input {s108_artifact.path}",
            )
            audit.predecessor_artifact_read_count += 1
        for s201_artifact in s201_evidence.artifact_snapshots:
            _verify_snapshot(
                root,
                s201_artifact,
                f"preflight input {s201_artifact.path}",
            )
            audit.predecessor_artifact_read_count += 1
        audit.preregistration_read_attempt_count += 1
        raise S202ContractError(
            "S202 model/label/objective/metric/gate choices remain TBD_PREREGISTRATION",
            stage="preregistration",
            audit=audit,
        )
    except S202ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    except OSError as exc:
        raise S202ContractError(
            str(exc),
            stage="filesystem",
            audit=audit,
        ) from exc


def config_only_result(static: StaticValidation) -> Mapping[str, Any]:
    return {
        "status": "valid_config_only",
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        **AccessAudit().as_dict(),
        "source_pin_read_count": len(static.source_snapshots),
        "production_trainer_implemented": False,
        "execution_allowed": False,
        "completion_claim_allowed": False,
    }


def failure_result(exc: S202ContractError) -> Mapping[str, Any]:
    audit = exc.audit.as_dict() if exc.audit is not None else AccessAudit().as_dict()
    return {
        "status": "failed_closed",
        "stage": exc.stage,
        "error": str(exc),
        **audit,
        "production_trainer_implemented": False,
        "execution_allowed": False,
        "completion_claim_allowed": False,
    }
