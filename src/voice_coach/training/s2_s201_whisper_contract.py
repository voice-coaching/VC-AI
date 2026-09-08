"""Fail-closed S201 Whisper content-gate readiness contract.

This module implements policy validation and a read-only preflight only.  It
does not download or run Whisper, read row-level transcripts/audio, compute
WER/CER, create predictions, use a GPU, or publish an experiment result.

Runtime ordering is deliberately strict:

1. validate the completed S108 final commit and every registered artifact;
2. validate the S200 readiness lock and its binding to that S108 commit;
3. require a frozen WER/CER/content-mismatch preregistration;
4. validate aggregate data-role/license/partition attestations;
5. validate an offline, local-only Whisper bundle;
6. validate manifest hashes without opening manifest rows.

The repository configuration intentionally stops at step 1 today because the
S108 final commit does not exist.  Even after all future evidence exists, this
module can only report readiness for a separately reviewed production runner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from voice_coach.training import s2_s200_auditor_contract as s200


CONFIG_SCHEMA_VERSION = 1
S201_CONFIG_STATUS = "static_preflight_only"
S108_COMMIT_SCHEMA_VERSION = "voice_coach.wavlm_s108_final_s1_commit.v1"
S200_LOCK_SCHEMA_VERSION = "voice_coach.s2_s200_readiness_lock.v1"
THRESHOLD_SCHEMA_VERSION = "voice_coach.s2_s201_threshold_preregistration.v1"
DATA_ATTESTATION_SCHEMA_VERSION = "voice_coach.s2_s201_data_attestation.v1"
MODEL_ATTESTATION_SCHEMA_VERSION = "voice_coach.s2_s201_model_bundle_attestation.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
TBD_ATTESTATION = "TBD_ATTESTATION"
TBD_LOCAL_BUNDLE = "TBD_LOCAL_BUNDLE"

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessors",
    "model",
    "transcript_normalization",
    "language_and_decoding",
    "determinism",
    "hard_gate",
    "data_roles",
    "independence",
    "execution_order",
    "outputs",
}

S108_ARTIFACT_NAMES = (
    "final_s1_config",
    "final_s1_checkpoint",
    "final_fusion_checkpoint",
    "calibrator_parameters",
    "threshold_policy",
    "promotion_report",
    "source_chain",
    "independent_validation_receipt",
)

S201_INPUT_ALLOWLIST = (
    "item_id_sha256",
    "speaker_id_sha256",
    "audio_waveform",
    "canonical_prompt_text",
    "language_code",
)

FORBIDDEN_S1_FIELDS = (
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

EXPECTED_NORMALIZATION_STEPS = (
    "decode_utf8_strict",
    "unicode_nfc",
    "unicode_casefold",
    "replace_unicode_punctuation_with_space",
    "map_all_unicode_whitespace_to_ascii_space",
    "collapse_ascii_spaces",
    "strip_ascii_space",
)

EXPECTED_ORDER = (
    "validate_c1_config_and_source_pins",
    "deep_validate_s108_final_passed_commit",
    "deep_validate_s200_readiness_lock",
    "validate_frozen_threshold_preregistration",
    "validate_data_license_partition_attestations",
    "validate_s2_expert_content_transcript_attestation",
    "validate_local_only_whisper_bundle",
    "validate_manifest_hashes_without_row_reads",
    "stop_ready_for_separate_runner_implementation",
)


class S201ContractError(ValueError):
    """Raised before an unsafe or unpreregistered S201 action."""

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
            raise S201ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters that make fail-before-resolution ordering externally testable."""

    source_pin_read_count: int = 0
    predecessor_metadata_read_attempt_count: int = 0
    predecessor_metadata_read_count: int = 0
    predecessor_artifact_read_count: int = 0
    threshold_attestation_read_attempt_count: int = 0
    threshold_attestation_read_count: int = 0
    external_attestation_read_attempt_count: int = 0
    external_attestation_read_count: int = 0
    data_path_resolution_count: int = 0
    data_manifest_read_count: int = 0
    data_row_read_count: int = 0
    audio_read_count: int = 0
    transcript_row_read_count: int = 0
    model_path_resolution_count: int = 0
    model_metadata_read_count: int = 0
    model_weight_read_count: int = 0
    model_download_count: int = 0
    protected_data_read_count: int = 0
    s1_prediction_read_count: int = 0
    oracle_alignment_read_count: int = 0
    locked_test_read_count: int = 0
    gpu_access_count: int = 0
    metric_count: int = 0
    prediction_count: int = 0
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
    commit_snapshot: FileSnapshot
    registry_snapshot: FileSnapshot
    commit_binding_sha256: str
    artifact_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True, slots=True)
class S200Evidence:
    lock_snapshot: FileSnapshot
    lock_binding_sha256: str
    s200_config_sha256: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    s108_commit_sha256: str
    s200_readiness_lock_sha256: str
    threshold_preregistration_sha256: str
    data_attestation_hashes: Mapping[str, str]
    model_attestation_sha256: str
    manifest_hashes: Mapping[str, str]
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S201ContractError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise S201ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S201ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise S201ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise S201ContractError(f"{name} must be at least {minimum}")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise S201ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise S201ContractError(f"{name} must be finite")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise S201ContractError(f"{name} must be boolean")
    return value


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not SHA256_RE.fullmatch(value)
        or value == "0" * 64
    ):
        raise S201ContractError(f"{name} must be a nonzero lowercase SHA-256")
    return value


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise S201ContractError(f"{name} must equal {expected!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        raise S201ContractError(
            f"{name} keys differ; "
            f"missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise S201ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise S201ContractError(f"JSON contains non-finite constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any], *, pretty: bool) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _binding_sha256(value: Mapping[str, Any]) -> str:
    body = {key: nested for key, nested in value.items() if key != "binding_sha256"}
    return hashlib.sha256(canonical_json_bytes(body, pretty=False)).hexdigest()


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or text != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise S201ContractError(f"{name} must be a normalized workspace-relative path")
    return text


def _workspace_relative(root: Path, path: Path, name: str) -> str:
    try:
        return path.absolute().relative_to(root).as_posix()
    except ValueError as exc:
        raise S201ContractError(f"{name} must stay inside the workspace") from exc


def _reject_symlink_chain(root: Path, relative: str, name: str) -> Path:
    normalized = _relative_string(relative, name)
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise S201ContractError(f"{name} is missing: {normalized}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise S201ContractError(f"{name} contains a symlink: {normalized}")
    return current


def _snapshot_file(
    *,
    root: Path,
    relative: str,
    name: str,
    maximum_bytes: int | None = None,
) -> tuple[bytes | None, FileSnapshot]:
    path = _reject_symlink_chain(root, relative, name)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise S201ContractError(f"{name} must be a regular file: {relative}")
    if maximum_bytes is not None and before.st_size > maximum_bytes:
        raise S201ContractError(f"{name} exceeds the size limit: {relative}")
    digest = hashlib.sha256()
    captured = bytearray() if maximum_bytes is not None else None
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if captured is not None:
                captured.extend(chunk)
    after = path.stat()
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
    if identity_before != identity_after:
        raise S201ContractError(f"{name} changed while being read: {relative}")
    snapshot = FileSnapshot(
        path=relative,
        sha256=digest.hexdigest(),
        size_bytes=after.st_size,
        device=after.st_dev,
        inode=after.st_ino,
        modified_ns=after.st_mtime_ns,
    )
    return (bytes(captured) if captured is not None else None), snapshot


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S201ContractError(f"{name} is not strict UTF-8 JSON") from exc
    return _mapping(value, name)


def _read_json(
    *,
    root: Path,
    relative: str,
    name: str,
    audit: AccessAudit,
    category: str,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    if category == "predecessor":
        audit.predecessor_metadata_read_attempt_count += 1
    elif category == "threshold":
        audit.threshold_attestation_read_attempt_count += 1
    elif category == "external":
        audit.external_attestation_read_attempt_count += 1
    elif category == "model":
        audit.model_path_resolution_count += 1
    else:
        raise S201ContractError(f"unknown read category: {category}")
    try:
        data, snapshot = _snapshot_file(
            root=root,
            relative=relative,
            name=name,
            maximum_bytes=16 * 1024 * 1024,
        )
    except S201ContractError as exc:
        if category == "predecessor":
            exc.stage = "predecessor_validation"
        elif category == "threshold":
            exc.stage = "threshold_preregistration"
        elif category == "external":
            exc.stage = "data_role_attestation"
        else:
            exc.stage = "local_model_validation"
        raise
    assert data is not None
    parsed = _parse_json(data, name)
    if category == "predecessor":
        audit.predecessor_metadata_read_count += 1
    elif category == "threshold":
        audit.threshold_attestation_read_count += 1
    elif category == "external":
        audit.external_attestation_read_count += 1
    else:
        audit.model_metadata_read_count += 1
    return parsed, snapshot


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise S201ContractError(f"cannot read S201 config: {exc}") from exc
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise S201ContractError("S201 config is not strict UTF-8 YAML") from exc
    config = _mapping(value, "S201 config")
    _reject_nonfinite_tree(config, "S201 config")
    return config


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise S201ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_nonfinite_tree(nested, name)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _reject_nonfinite_tree(nested, name)


def _validate_experiment(config: Mapping[str, Any]) -> None:
    experiment = _mapping(config["experiment"], "experiment")
    _expect(experiment.get("id"), "s2_s201_whisper_content_gate_v1", "experiment id")
    _expect(
        experiment.get("stage"),
        "s201_whisper_content_consistency_hard_gate",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        S201_CONFIG_STATUS,
        "implementation status",
    )
    for name in (
        "production_runner_implemented",
        "actual_model_download_allowed",
        "actual_inference_allowed",
        "row_level_data_access_allowed",
        "gpu_access_allowed",
        "metric_generation_allowed",
        "prediction_generation_allowed",
        "result_generation_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(name), False, f"experiment.{name}")


def _validate_predecessor_contract(config: Mapping[str, Any]) -> None:
    predecessors = _mapping(config["predecessors"], "predecessors")
    for name in (
        "validate_before_threshold_attestation",
        "validate_before_data_role_attestation",
        "validate_before_model_path_resolution",
        "validate_before_data_path_resolution",
        "validate_before_output_path_resolution",
    ):
        _expect(predecessors.get(name), True, f"predecessors.{name}")
    s108 = _mapping(predecessors.get("s108_final"), "predecessors.s108_final")
    _relative_string(s108.get("commit_path"), "S108 commit path")
    _relative_string(s108.get("registry_path"), "S108 registry path")
    _relative_string(s108.get("artifact_root"), "S108 artifact root")
    _expect(s108.get("schema_version"), S108_COMMIT_SCHEMA_VERSION, "S108 schema")
    _expect(s108.get("required_status"), "committed", "S108 status")
    _expect(
        s108.get("required_decision"),
        "research_candidate_passed",
        "S108 decision",
    )
    _expect(s108.get("required_locked_access_count"), 1, "S108 locked access")
    _expect(s108.get("required_locked_rerun_count"), 0, "S108 rerun count")
    _expect(
        tuple(_list(s108.get("required_artifact_names"), "S108 artifact names")),
        S108_ARTIFACT_NAMES,
        "S108 artifact names",
    )
    s200_contract = _mapping(
        predecessors.get("s200_readiness_lock"),
        "predecessors.s200_readiness_lock",
    )
    _relative_string(s200_contract.get("config_path"), "S200 config path")
    _relative_string(s200_contract.get("lock_path"), "S200 readiness lock path")
    _expect(
        s200_contract.get("schema_version"),
        S200_LOCK_SCHEMA_VERSION,
        "S200 lock schema",
    )
    _expect(
        s200_contract.get("required_status"),
        "readiness_contract_published",
        "S200 lock status",
    )
    _expect(
        s200_contract.get("require_s108_commit_binding"),
        True,
        "S200 S108 binding",
    )


def _validate_model_contract(config: Mapping[str, Any]) -> None:
    model = _mapping(config["model"], "model")
    identity = _mapping(model.get("identity"), "model.identity")
    _expect(identity.get("provider"), "huggingface_hub", "model provider")
    _expect(identity.get("model_id"), "openai/whisper-large-v3", "model id")
    revision = _string(identity.get("revision"), "model revision")
    if not GIT_REVISION_RE.fullmatch(revision):
        raise S201ContractError("model revision must be an exact 40-hex commit")
    source_revision = _string(
        identity.get("official_source_revision"),
        "official source revision",
    )
    if not GIT_REVISION_RE.fullmatch(source_revision):
        raise S201ContractError(
            "official source revision must be an exact 40-hex commit"
        )
    _expect(identity.get("architecture"), "WhisperForConditionalGeneration", "model")
    _expect(identity.get("weight_format"), "safetensors_only", "weight format")
    _expect(identity.get("pickle_weights_allowed"), False, "pickle weights")
    _expect(identity.get("license"), "MIT", "model license")
    _expect(
        identity.get("license_sha256"),
        "b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d",
        "model license hash",
    )
    _expect(
        identity.get("license_reaudit_before_acquisition"),
        True,
        "model license reaudit",
    )

    preprocessor = _mapping(model.get("preprocessor"), "model.preprocessor")
    _expect(
        preprocessor.get("processor_id"),
        identity.get("model_id"),
        "processor id",
    )
    _expect(
        preprocessor.get("revision"),
        revision,
        "processor revision",
    )
    _expect(
        preprocessor.get("processor_class"),
        "WhisperProcessor",
        "processor class",
    )
    _expect(
        preprocessor.get("feature_extractor_class"),
        "WhisperFeatureExtractor",
        "feature extractor",
    )
    _expect(
        preprocessor.get("tokenizer_class"),
        "WhisperTokenizerFast",
        "tokenizer class",
    )
    _expect(preprocessor.get("sampling_rate_hz"), 16000, "sampling rate")
    _expect(preprocessor.get("feature_size"), 128, "mel feature size")
    _expect(preprocessor.get("hop_length"), 160, "hop length")
    _expect(preprocessor.get("n_fft"), 400, "FFT size")
    _expect(preprocessor.get("chunk_length_seconds"), 30, "chunk length")
    _expect(
        preprocessor.get("configuration_source"),
        "same_exact_local_revision",
        "preprocessor source",
    )

    offline = _mapping(model.get("offline_only"), "model.offline_only")
    for name in (
        "local_files_only",
        "hf_hub_offline_required",
        "transformers_offline_required",
        "network_fallback_allowed",
        "automatic_download_allowed",
        "execution_when_local_bundle_missing_allowed",
    ):
        expected = name in {
            "local_files_only",
            "hf_hub_offline_required",
            "transformers_offline_required",
        }
        _expect(offline.get(name), expected, f"model.offline_only.{name}")
    _relative_string(offline.get("bundle_root"), "model bundle root")
    _relative_string(offline.get("attestation_path"), "model attestation path")
    status = _string(offline.get("attestation_status"), "model attestation status")
    attestation_sha = offline.get("attestation_sha256")
    if status == "missing_local_bundle":
        _expect(attestation_sha, TBD_LOCAL_BUNDLE, "model attestation SHA")
    elif status == "ready":
        _sha256(attestation_sha, "model attestation SHA")
    else:
        raise S201ContractError("unsupported model attestation status")


def _validate_text_and_decoding(config: Mapping[str, Any]) -> None:
    policy = _mapping(
        config["transcript_normalization"],
        "transcript_normalization",
    )
    _expect(policy.get("policy_id"), "ko_s201_nfc_literal_v1", "normalization id")
    _expect(
        tuple(_list(policy.get("ordered_steps"), "normalization steps")),
        EXPECTED_NORMALIZATION_STEPS,
        "normalization steps",
    )
    _expect(policy.get("apply_identically_to_reference_and_hypothesis"), True, "norm")
    _expect(policy.get("unicode_normalization"), "NFC", "Unicode normalization")
    _expect(policy.get("latin_case_policy"), "unicode_casefold", "case policy")
    _expect(
        policy.get("punctuation_policy"),
        "all_unicode_general_category_P_to_space",
        "punctuation policy",
    )
    _expect(policy.get("digit_expansion"), "none", "digit expansion")
    _expect(policy.get("morphological_tokenizer"), "none", "morph tokenizer")
    _expect(policy.get("pronunciation_rewrite"), "none", "pronunciation rewrite")
    _expect(policy.get("spell_correction"), "none", "spell correction")
    _expect(policy.get("word_unit"), "ascii_space_delimited_token", "WER unit")
    _expect(
        policy.get("character_unit"), "unicode_codepoint_excluding_space", "CER unit"
    )
    _expect(
        policy.get("empty_reference_policy"), "invalid_item_fail_closed", "empty ref"
    )
    _expect(policy.get("raw_transcript_export_allowed"), False, "transcript export")

    language = _mapping(config["language_and_decoding"], "language_and_decoding")
    _expect(language.get("language"), "ko", "language")
    _expect(language.get("language_token"), "<|ko|>", "language token")
    _expect(language.get("task"), "transcribe", "task")
    _expect(language.get("language_detection_allowed"), False, "language detection")
    _expect(language.get("translation_allowed"), False, "translation")
    _expect(language.get("forced_language_required"), True, "language forcing")
    _expect(language.get("do_sample"), False, "sampling")
    _expect(language.get("temperature"), 0.0, "temperature")
    _expect(language.get("num_beams"), 1, "beam count")
    _expect(language.get("num_return_sequences"), 1, "return count")
    _expect(language.get("return_timestamps"), False, "timestamps")
    _expect(language.get("timestamp_features_allowed"), False, "timestamp feature")
    _expect(language.get("condition_on_prev_tokens"), False, "previous tokens")
    _expect(language.get("fallback_temperatures"), [0.0], "fallback temperatures")
    _expect(language.get("max_new_tokens"), 448, "max new tokens")
    _expect(
        language.get("generation_config_source"),
        "exact_local_bundle_revision",
        "generation config source",
    )

    determinism = _mapping(config["determinism"], "determinism")
    _expect(determinism.get("seed"), 20260729, "determinism seed")
    _expect(
        determinism.get("torch_deterministic_algorithms"), True, "torch deterministic"
    )
    _expect(determinism.get("cudnn_benchmark"), False, "cuDNN benchmark")
    _expect(determinism.get("cudnn_deterministic"), True, "cuDNN deterministic")
    _expect(determinism.get("allow_tf32"), False, "TF32")
    _expect(
        determinism.get("cublas_workspace_config"),
        ":4096:8",
        "CUBLAS workspace",
    )
    _expect(determinism.get("transformers_version"), "5.14.1", "transformers version")
    _expect(determinism.get("torch_version"), "2.12.1+cu130", "torch version")
    _expect(determinism.get("tokenizers_version"), "0.22.2", "tokenizers version")
    _expect(determinism.get("safetensors_version"), "0.8.0", "safetensors version")


def _validate_hard_gate(config: Mapping[str, Any]) -> None:
    gate = _mapping(config["hard_gate"], "hard_gate")
    _expect(gate.get("role"), "content_consistency_only", "hard-gate role")
    _expect(gate.get("evaluated_before_s202"), True, "hard-gate ordering")
    _expect(gate.get("bypass_allowed"), False, "hard-gate bypass")
    _expect(gate.get("failed_content_rescue_by_s202_allowed"), False, "gate rescue")
    _expect(gate.get("fine_phone_judgment_allowed"), False, "phone role")
    _expect(gate.get("prosody_judgment_allowed"), False, "prosody role")
    prereg = _mapping(
        gate.get("threshold_preregistration"),
        "threshold preregistration",
    )
    status = _string(prereg.get("status"), "threshold status")
    metrics = _mapping(gate.get("metrics"), "hard_gate.metrics")
    _expect(set(metrics), {"wer", "cer", "content_mismatch"}, "hard-gate metrics")
    _relative_string(prereg.get("path"), "threshold preregistration path")
    sha_value = prereg.get("sha256")
    if status == TBD_PREREGISTRATION:
        _expect(
            metrics["wer"],
            {
                "formula": "word_levenshtein_distance_over_reference_word_count",
                "operator": "less_than_or_equal",
                "threshold": TBD_PREREGISTRATION,
            },
            "WER contract",
        )
        _expect(
            metrics["cer"],
            {
                "formula": (
                    "codepoint_levenshtein_distance_over_reference_character_count"
                ),
                "operator": "less_than_or_equal",
                "threshold": TBD_PREREGISTRATION,
            },
            "CER contract",
        )
        _expect(
            metrics["content_mismatch"],
            {
                "formula": TBD_PREREGISTRATION,
                "operator": TBD_PREREGISTRATION,
                "threshold": TBD_PREREGISTRATION,
            },
            "content-mismatch contract",
        )
        _expect(sha_value, TBD_PREREGISTRATION, "threshold preregistration SHA")
        _expect(
            prereg.get("execution_allowed"), False, "unresolved threshold execution"
        )
    elif status == "frozen":
        for name, formula in (
            (
                "wer",
                "word_levenshtein_distance_over_reference_word_count",
            ),
            (
                "cer",
                "codepoint_levenshtein_distance_over_reference_character_count",
            ),
        ):
            metric = _mapping(metrics[name], f"{name} metric")
            _expect(metric.get("formula"), formula, f"{name} formula")
            _expect(metric.get("operator"), "less_than_or_equal", f"{name} operator")
            threshold = _number(metric.get("threshold"), f"{name} threshold")
            if not 0.0 <= threshold <= 1.0:
                raise S201ContractError(f"{name} threshold must be in [0, 1]")
        _expect(
            metrics["content_mismatch"],
            {
                "formula": "fail_if_wer_or_cer_exceeds_its_frozen_threshold",
                "operator": "boolean_equals",
                "threshold": False,
            },
            "content-mismatch contract",
        )
        _sha256(sha_value, "threshold preregistration SHA")
        _expect(prereg.get("execution_allowed"), True, "frozen threshold execution")
    else:
        raise S201ContractError("unsupported threshold preregistration status")
    _expect(
        prereg.get("must_be_frozen_before_data_or_model_path_resolution"),
        True,
        "threshold freeze ordering",
    )
    _expect(
        prereg.get("native_external_benchmark_may_set_threshold"),
        False,
        "external benchmark threshold selection",
    )


def _validate_data_roles(config: Mapping[str, Any]) -> None:
    roles = _mapping(config["data_roles"], "data_roles")
    expert = _mapping(roles.get("s2_expert_content"), "S2 expert content role")
    _expect(
        expert.get("role"), "l2_content_gate_reference_and_evaluation", "expert role"
    )
    _expect(expert.get("independent_from_s1"), True, "expert independence")
    _expect(expert.get("s1_label_or_prediction_allowed"), False, "expert S1 input")
    _expect(
        expert.get("fine_phone_or_prosody_label_allowed"), False, "expert fine label"
    )
    _expect(expert.get("row_access_before_attestation_allowed"), False, "expert access")
    _expect(
        expert.get("partition_policy"),
        "preregistered_s2_expert_content_partitions",
        "expert partition policy",
    )
    _relative_string(expert.get("manifest_root"), "expert manifest root")
    _relative_string(expert.get("attestation_path"), "expert attestation path")
    _validate_attestation_pin(expert, "expert")

    external = _mapping(roles.get("native_external_asr"), "native external ASR")
    _expect(set(external), {"fleurs_ko_kr", "zeroth_korean"}, "external datasets")
    for name in ("fleurs_ko_kr", "zeroth_korean"):
        record = _mapping(external[name], f"external dataset {name}")
        _expect(record.get("role"), "asr_external_evaluation_only", f"{name} role")
        _expect(record.get("license"), "CC-BY-4.0", f"{name} license")
        _expect(record.get("attribution_required"), True, f"{name} attribution")
        _expect(record.get("original_partition_required"), True, f"{name} partition")
        _expect(record.get("learner_error_training_allowed"), False, f"{name} training")
        _expect(
            record.get("s2_expert_transcript_substitute_allowed"), False, f"{name} role"
        )
        _expect(
            record.get("row_access_before_attestation_allowed"), False, f"{name} access"
        )
        _expect(
            record.get("partition_policy"),
            "preserve_official_partition_names_and_hashes",
            f"{name} partition policy",
        )
        _relative_string(record.get("manifest_root"), f"{name} manifest root")
        _relative_string(record.get("attestation_path"), f"{name} attestation path")
        _validate_attestation_pin(record, name)


def _validate_attestation_pin(record: Mapping[str, Any], name: str) -> None:
    status = _string(record.get("attestation_status"), f"{name} attestation status")
    sha_value = record.get("attestation_sha256")
    if status == "required_before_access":
        _expect(sha_value, TBD_ATTESTATION, f"{name} attestation SHA")
    elif status == "ready":
        _sha256(sha_value, f"{name} attestation SHA")
    else:
        raise S201ContractError(f"unsupported {name} attestation status")


def _validate_independence_and_order(config: Mapping[str, Any]) -> None:
    independence = _mapping(config["independence"], "independence")
    _expect(
        tuple(_list(independence.get("input_allowlist"), "input allowlist")),
        S201_INPUT_ALLOWLIST,
        "S201 input allowlist",
    )
    _expect(
        tuple(_list(independence.get("forbidden_s1_fields"), "forbidden S1 fields")),
        FORBIDDEN_S1_FIELDS,
        "forbidden S1 fields",
    )
    for name in (
        "s1_predictions_allowed",
        "s1_labels_allowed",
        "s1_features_allowed",
        "s1_thresholds_allowed",
        "oracle_alignment_allowed",
        "predicted_alignment_allowed",
        "whisper_timestamps_as_features_allowed",
        "locked_test_access_allowed",
    ):
        _expect(independence.get(name), False, f"independence.{name}")
    _expect(independence.get("alignment_mode"), "alignment_free_utterance", "alignment")
    _expect(
        tuple(_list(config["execution_order"], "execution order")),
        EXPECTED_ORDER,
        "execution order",
    )
    outputs = _mapping(config["outputs"], "outputs")
    _expect(outputs.get("production_runner_in_scope"), False, "runner scope")
    _expect(outputs.get("current_output_count"), 0, "current output count")
    _expect(outputs.get("output_path_resolution_allowed"), False, "output resolution")
    _expect(
        outputs.get("future_artifacts"),
        [
            "s201_config",
            "checkpoint_source_card",
            "transcript_normalization_policy",
            "aggregate_wer_cer_content_report",
            "privacy_safe_content_gate_predictions",
            "s201_commit",
        ],
        "future artifacts",
    )


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> StaticValidation:
    """Validate only C1 policy and source pins."""

    root = workspace_root.resolve(strict=True)
    relative_config = _workspace_relative(root, config_path.absolute(), "config path")
    _reject_symlink_chain(root, relative_config, "config path")
    _expect_keys(config, TOP_LEVEL_KEYS, "config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema version")
    _validate_experiment(config)
    _validate_predecessor_contract(config)
    _validate_model_contract(config)
    _validate_text_and_decoding(config)
    _validate_hard_gate(config)
    _validate_data_roles(config)
    _validate_independence_and_order(config)

    sources = _mapping(config["canonical_sources"], "canonical_sources")
    _expect(sources.get("access_scope"), "C1_code_config_docs_only", "source scope")
    pins = _list(sources.get("pins"), "canonical source pins")
    if not pins:
        raise S201ContractError("at least one canonical source pin is required")
    seen: set[str] = set()
    snapshots: list[FileSnapshot] = []
    for index, raw in enumerate(pins):
        pin = _mapping(raw, f"canonical source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"canonical source pin {index}")
        relative = _relative_string(pin.get("path"), f"canonical source path {index}")
        if relative in seen:
            raise S201ContractError(f"duplicate source pin: {relative}")
        seen.add(relative)
        if relative.startswith(("data/", "artifacts/", "models/", "checkpoints/")):
            raise S201ContractError(f"source pin enters data/model zone: {relative}")
        expected = _sha256(pin.get("sha256"), f"source pin {relative}")
        _, snapshot = _snapshot_file(
            root=root,
            relative=relative,
            name=f"canonical source {relative}",
        )
        if snapshot.sha256 != expected:
            raise S201ContractError(
                f"canonical source pin mismatch for {relative}: "
                f"expected {expected}, observed {snapshot.sha256}"
            )
        snapshots.append(snapshot)

    config_data, config_snapshot = _snapshot_file(
        root=root,
        relative=relative_config,
        name="S201 config",
        maximum_bytes=4 * 1024 * 1024,
    )
    assert config_data is not None
    try:
        on_disk = yaml.load(config_data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise S201ContractError("S201 config is not strict UTF-8 YAML") from exc
    if on_disk != config:
        raise S201ContractError("provided config differs from on-disk S201 config")
    policy = {
        key: config[key]
        for key in (
            "model",
            "transcript_normalization",
            "language_and_decoding",
            "determinism",
            "hard_gate",
            "data_roles",
            "independence",
            "execution_order",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        source_snapshots=tuple(snapshots),
        policy_sha256=hashlib.sha256(
            canonical_json_bytes(policy, pretty=False)
        ).hexdigest(),
    )


def _verify_snapshot(
    root: Path,
    expected: FileSnapshot,
    name: str,
    *,
    audit: AccessAudit,
    counter_name: str,
) -> None:
    _, observed = _snapshot_file(
        root=root,
        relative=expected.path,
        name=name,
    )
    setattr(audit, counter_name, int(getattr(audit, counter_name)) + 1)
    if observed != expected:
        raise S201ContractError(f"{name} changed during preflight")


def _deep_validate_s108(
    *,
    root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> S108Evidence:
    predecessor = _mapping(config["predecessors"], "predecessors")
    s108_contract = _mapping(predecessor["s108_final"], "S108 contract")
    s200_contract = _mapping(
        predecessor["s200_readiness_lock"],
        "S200 contract",
    )
    s200_config_path = _relative_string(
        s200_contract["config_path"],
        "S200 config path",
    )
    try:
        s200_config = s200.load_config(root / s200_config_path)
        s200_static = s200.validate_static_config(
            workspace_root=root,
            config_path=root / s200_config_path,
            config=s200_config,
        )
        audit.predecessor_metadata_read_count += len(s200_static.source_snapshots) + 1
        s200_audit = s200.AccessAudit()
        evidence = s200._validate_s108_predecessor(  # noqa: SLF001
            root=root,
            config=s200_config,
            audit=s200_audit,
        )
        audit.predecessor_metadata_read_attempt_count += (
            s200_audit.predecessor_metadata_read_attempt_count
        )
        audit.predecessor_metadata_read_count += (
            s200_audit.predecessor_metadata_read_count
        )
    except s200.S200ContractError as exc:
        if "s200_audit" in locals():
            audit.predecessor_metadata_read_attempt_count += max(
                0,
                s200_audit.predecessor_metadata_read_attempt_count
                - audit.predecessor_metadata_read_attempt_count,
            )
            audit.predecessor_metadata_read_count += max(
                0,
                s200_audit.predecessor_metadata_read_count,
            )
        raise S201ContractError(
            f"S108 deep validation failed: {exc}",
            stage="predecessor_validation",
            audit=audit,
        ) from exc

    _expect(
        evidence.commit_snapshot.path,
        s108_contract.get("commit_path"),
        "S108 commit path agreement",
    )
    _expect(
        evidence.registry_snapshot.path,
        s108_contract.get("registry_path"),
        "S108 registry path agreement",
    )
    commit, commit_snapshot = _read_json(
        root=root,
        relative=evidence.commit_snapshot.path,
        name="S108 final commit",
        audit=audit,
        category="predecessor",
    )
    registry, registry_snapshot = _read_json(
        root=root,
        relative=evidence.registry_snapshot.path,
        name="S108 completed registry",
        audit=audit,
        category="predecessor",
    )
    if commit_snapshot.sha256 != evidence.commit_snapshot.sha256:
        raise S201ContractError("S108 commit changed during deep validation")
    if registry_snapshot.sha256 != evidence.registry_snapshot.sha256:
        raise S201ContractError("S108 registry changed during deep validation")
    expected_hashes = _mapping(commit.get("artifact_hashes"), "S108 artifact hashes")
    registry_artifacts = _list(registry.get("artifacts"), "S108 registry artifacts")
    by_name: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(registry_artifacts):
        artifact = _mapping(raw, f"S108 registry artifact {index}")
        name = _string(artifact.get("name"), f"S108 artifact {index} name")
        if name in by_name:
            raise S201ContractError(f"duplicate S108 registry artifact: {name}")
        by_name[name] = artifact
    if not set(S108_ARTIFACT_NAMES).issubset(by_name):
        raise S201ContractError("S108 registry is missing required named artifacts")
    artifact_snapshots: list[FileSnapshot] = []
    for name in S108_ARTIFACT_NAMES:
        artifact = by_name[name]
        relative = _relative_string(artifact.get("path"), f"S108 artifact {name} path")
        artifact_root = _relative_string(
            s108_contract.get("artifact_root"),
            "S108 artifact root",
        )
        if not relative.startswith(artifact_root + "/"):
            raise S201ContractError(
                f"S108 artifact {name} escapes the pinned artifact root"
            )
        expected_sha = _sha256(artifact.get("sha256"), f"S108 artifact {name} SHA")
        _expect(expected_sha, expected_hashes[name], f"S108 commit artifact {name}")
        expected_size = _integer(
            artifact.get("size_bytes"),
            f"S108 artifact {name} size",
            minimum=1,
        )
        _, snapshot = _snapshot_file(
            root=root,
            relative=relative,
            name=f"S108 artifact {name}",
        )
        audit.predecessor_artifact_read_count += 1
        _expect(snapshot.sha256, expected_sha, f"S108 artifact {name} hash")
        _expect(snapshot.size_bytes, expected_size, f"S108 artifact {name} size")
        artifact_snapshots.append(snapshot)
    return S108Evidence(
        commit_snapshot=commit_snapshot,
        registry_snapshot=registry_snapshot,
        commit_binding_sha256=evidence.commit_binding_sha256,
        artifact_snapshots=tuple(artifact_snapshots),
    )


def _deep_validate_s200_lock(
    *,
    root: Path,
    config: Mapping[str, Any],
    s108: S108Evidence,
    audit: AccessAudit,
) -> S200Evidence:
    contract = _mapping(
        _mapping(config["predecessors"], "predecessors")["s200_readiness_lock"],
        "S200 readiness contract",
    )
    lock, snapshot = _read_json(
        root=root,
        relative=_relative_string(contract["lock_path"], "S200 lock path"),
        name="S200 readiness lock",
        audit=audit,
        category="predecessor",
    )
    _expect(lock.get("schema_version"), S200_LOCK_SCHEMA_VERSION, "S200 lock schema")
    _expect(lock.get("status"), "readiness_contract_published", "S200 lock status")
    _expect(lock.get("s200_completion_claim_allowed"), False, "S200 completion claim")
    _expect(lock.get("s201_trainer_implemented"), False, "S200 S201 runner claim")
    for name in (
        "actual_model_download_count",
        "actual_model_training_count",
        "gpu_access_count",
        "data_read_count",
        "protected_data_read_count",
        "holdout_read_count",
        "locked_test_read_count",
        "metric_count",
        "result_count",
    ):
        _expect(lock.get(name), 0, f"S200 lock {name}")
    observed_binding = _sha256(lock.get("binding_sha256"), "S200 lock binding")
    _expect(observed_binding, _binding_sha256(lock), "S200 lock binding")
    s108_record = _mapping(lock.get("s108_predecessor"), "S200 S108 binding")
    _expect(
        s108_record.get("commit_sha256"),
        s108.commit_snapshot.sha256,
        "S200 S108 commit hash",
    )
    _expect(
        s108_record.get("registry_sha256"),
        s108.registry_snapshot.sha256,
        "S200 S108 registry hash",
    )
    _expect(
        s108_record.get("commit_binding_sha256"),
        s108.commit_binding_sha256,
        "S200 S108 commit binding",
    )
    _expect(s108_record.get("decision"), "research_candidate_passed", "S200 decision")
    _expect(s108_record.get("deployment_approved"), False, "S200 deployment")
    external = _mapping(
        lock.get("s203_expert_holdout_attestation"),
        "S200 expert attestation",
    )
    _sha256(external.get("sha256"), "S200 expert attestation SHA")
    _sha256(external.get("binding_sha256"), "S200 expert attestation binding")
    _expect(external.get("aggregate_only"), True, "S200 aggregate-only")
    _expect(external.get("row_level_accessed"), False, "S200 row-level access")
    downstream = _mapping(
        lock.get("downstream_execution_allowed"),
        "S200 downstream execution",
    )
    _expect(downstream.get("s201"), False, "S200 S201 execution")

    s200_config_path = _relative_string(contract["config_path"], "S200 config path")
    s200_config = s200.load_config(root / s200_config_path)
    s200_static = s200.validate_static_config(
        workspace_root=root,
        config_path=root / s200_config_path,
        config=s200_config,
    )
    audit.predecessor_metadata_read_count += len(s200_static.source_snapshots) + 1
    _expect(lock.get("config_sha256"), s200_static.config_sha256, "S200 config hash")
    expected_sources = {item.path: item.sha256 for item in s200_static.source_snapshots}
    _expect(
        lock.get("canonical_source_hashes"),
        expected_sources,
        "S200 source hashes",
    )
    _expect(
        lock.get("independence_contract_sha256"),
        s200_static.independence_contract_sha256,
        "S200 independence hash",
    )
    _expect(
        lock.get("data_card_sha256"),
        s200_static.data_card_sha256,
        "S200 data-card hash",
    )
    return S200Evidence(
        lock_snapshot=snapshot,
        lock_binding_sha256=observed_binding,
        s200_config_sha256=s200_static.config_sha256,
    )


def _validate_threshold_preregistration(
    *,
    root: Path,
    config: Mapping[str, Any],
    s108: S108Evidence,
    s200_evidence: S200Evidence,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    prereg = _mapping(
        _mapping(config["hard_gate"], "hard_gate")["threshold_preregistration"],
        "threshold preregistration",
    )
    if prereg.get("status") != "frozen":
        raise S201ContractError(
            "WER/CER/content-mismatch thresholds remain TBD_PREREGISTRATION",
            stage="threshold_preregistration",
            audit=audit,
        )
    record, snapshot = _read_json(
        root=root,
        relative=_relative_string(prereg["path"], "threshold preregistration path"),
        name="S201 threshold preregistration",
        audit=audit,
        category="threshold",
    )
    _expect(snapshot.sha256, prereg.get("sha256"), "threshold preregistration hash")
    _expect(record.get("schema_version"), THRESHOLD_SCHEMA_VERSION, "threshold schema")
    _expect(record.get("status"), "frozen", "threshold status")
    _expect(record.get("results_seen_before_freeze"), False, "threshold result access")
    _expect(
        record.get("data_paths_resolved_before_freeze"), False, "threshold data access"
    )
    _expect(
        record.get("model_path_resolved_before_freeze"), False, "threshold model access"
    )
    _expect(
        record.get("native_external_benchmark_selected_threshold"),
        False,
        "threshold source",
    )
    _expect(
        record.get("s108_commit_sha256"),
        s108.commit_snapshot.sha256,
        "threshold S108 binding",
    )
    _expect(
        record.get("s200_readiness_lock_sha256"),
        s200_evidence.lock_snapshot.sha256,
        "threshold S200 binding",
    )
    thresholds = _mapping(record.get("thresholds"), "frozen thresholds")
    _expect(set(thresholds), {"wer", "cer", "content_mismatch"}, "threshold names")
    for name in ("wer", "cer"):
        metric = _mapping(thresholds[name], f"{name} threshold")
        _expect(metric.get("operator"), "less_than_or_equal", f"{name} operator")
        value = _number(metric.get("value"), f"{name} threshold value")
        if not 0.0 <= value <= 1.0:
            raise S201ContractError(f"{name} threshold must be in [0, 1]")
    content = _mapping(thresholds["content_mismatch"], "content mismatch threshold")
    _expect(
        content.get("rule"),
        "fail_if_wer_or_cer_exceeds_its_frozen_threshold",
        "content mismatch rule",
    )
    _expect(content.get("bypass_allowed"), False, "content mismatch bypass")
    config_metrics = _mapping(
        _mapping(config["hard_gate"], "hard gate")["metrics"],
        "hard-gate metrics",
    )
    for name in ("wer", "cer"):
        configured = _mapping(config_metrics[name], f"configured {name}")
        _expect(
            configured.get("threshold"),
            _mapping(thresholds[name], f"frozen {name}").get("value"),
            f"{name} preregistration/config binding",
        )
    configured_content = _mapping(
        config_metrics["content_mismatch"],
        "configured content mismatch",
    )
    _expect(
        configured_content.get("formula"),
        content.get("rule"),
        "content rule preregistration/config binding",
    )
    _expect(
        configured_content.get("threshold"),
        False,
        "content threshold preregistration/config binding",
    )
    _expect(
        _sha256(record.get("binding_sha256"), "threshold binding"),
        _binding_sha256(record),
        "threshold binding",
    )
    return record, snapshot


def _validate_data_attestation(
    *,
    root: Path,
    role_name: str,
    contract: Mapping[str, Any],
    s108: S108Evidence,
    s200_evidence: S200Evidence,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    if contract.get("attestation_status") != "ready":
        raise S201ContractError(
            f"{role_name} license/partition attestation is not ready",
            stage="data_role_attestation",
            audit=audit,
        )
    record, snapshot = _read_json(
        root=root,
        relative=_relative_string(
            contract["attestation_path"],
            f"{role_name} attestation path",
        ),
        name=f"S201 {role_name} attestation",
        audit=audit,
        category="external",
    )
    _expect(
        snapshot.sha256,
        contract.get("attestation_sha256"),
        f"{role_name} attestation hash",
    )
    _expect(
        record.get("schema_version"), DATA_ATTESTATION_SCHEMA_VERSION, "data schema"
    )
    _expect(record.get("status"), "ready", f"{role_name} status")
    _expect(record.get("dataset_id"), role_name, f"{role_name} dataset id")
    _expect(record.get("aggregate_only"), True, f"{role_name} aggregate-only")
    for field in (
        "contains_rows",
        "contains_audio",
        "contains_transcripts",
        "contains_s1_predictions",
        "contains_oracle_alignment",
        "contains_raw_identifiers",
    ):
        _expect(record.get(field), False, f"{role_name} {field}")
    _expect(
        record.get("s108_commit_sha256"),
        s108.commit_snapshot.sha256,
        f"{role_name} S108 binding",
    )
    _expect(
        record.get("s200_readiness_lock_sha256"),
        s200_evidence.lock_snapshot.sha256,
        f"{role_name} S200 binding",
    )
    _expect(
        record.get("partition_policy"),
        contract.get("partition_policy"),
        f"{role_name} partition policy",
    )
    partition_hashes = _mapping(
        record.get("partition_hashes"),
        f"{role_name} partition hashes",
    )
    if not partition_hashes:
        raise S201ContractError(f"{role_name} must attest at least one partition")
    for partition_name, partition_sha in partition_hashes.items():
        if not isinstance(partition_name, str) or not partition_name:
            raise S201ContractError(f"{role_name} has an invalid partition name")
        _sha256(partition_sha, f"{role_name} partition {partition_name}")
    manifest = _mapping(record.get("manifest"), f"{role_name} manifest")
    manifest_path = _relative_string(
        manifest.get("path"),
        f"{role_name} manifest path",
    )
    manifest_root = _relative_string(
        contract.get("manifest_root"),
        f"{role_name} manifest root",
    )
    if not manifest_path.startswith(manifest_root + "/"):
        raise S201ContractError(
            f"{role_name} manifest escapes the pinned role-specific root"
        )
    _sha256(manifest.get("sha256"), f"{role_name} manifest SHA")
    _integer(manifest.get("item_count"), f"{role_name} item count", minimum=1)
    if role_name == "s2_expert_content":
        _expect(
            record.get("role"),
            "l2_content_gate_reference_and_evaluation",
            "expert content role",
        )
        _expect(record.get("independent_from_s1"), True, "expert independence")
        _expect(record.get("fine_phone_or_prosody_labels"), False, "expert fine labels")
        _expect(
            record.get("license"),
            "separate_consent_and_institution_policy",
            "expert license",
        )
        overlap = _mapping(record.get("overlap_counts"), "expert overlap counts")
        _expect(
            set(overlap),
            {
                "s1_train_items",
                "s1_dev_items",
                "s1_stack_items",
                "s1_calibration_items",
                "s1_locked_test_items",
                "s1_selection_speakers",
                "s1_labeler_cohort",
            },
            "expert overlap fields",
        )
        for field, count in overlap.items():
            _expect(count, 0, f"expert overlap {field}")
        governance = _mapping(record.get("governance"), "expert governance")
        for field in (
            "institutional_approval_complete",
            "consent_complete",
            "withdrawal_plan_complete",
            "retention_and_destruction_plan_complete",
            "restricted_redistribution",
        ):
            _expect(governance.get(field), True, f"expert governance {field}")
    else:
        _expect(record.get("role"), "asr_external_evaluation_only", f"{role_name} role")
        _expect(record.get("license"), "CC-BY-4.0", f"{role_name} license")
        _expect(record.get("attribution_complete"), True, f"{role_name} attribution")
        _expect(
            record.get("original_partition_preserved"),
            True,
            f"{role_name} partition",
        )
        _expect(
            record.get("learner_error_training_used"),
            False,
            f"{role_name} training role",
        )
        _expect(
            record.get("s2_expert_transcript_substitute_used"),
            False,
            f"{role_name} expert role",
        )
        _expect(
            record.get("license_audit_completed"),
            True,
            f"{role_name} license audit",
        )
    _expect(
        _sha256(record.get("binding_sha256"), f"{role_name} binding"),
        _binding_sha256(record),
        f"{role_name} binding",
    )
    return record, snapshot


def _validate_local_model(
    *,
    root: Path,
    config: Mapping[str, Any],
    s108: S108Evidence,
    s200_evidence: S200Evidence,
    audit: AccessAudit,
) -> tuple[FileSnapshot, tuple[FileSnapshot, ...]]:
    model = _mapping(config["model"], "model")
    identity = _mapping(model["identity"], "model identity")
    offline = _mapping(model["offline_only"], "offline model")
    if offline.get("attestation_status") != "ready":
        raise S201ContractError(
            "exact Whisper bundle is absent; local-only execution remains forbidden",
            stage="local_model_validation",
            audit=audit,
        )
    attestation, snapshot = _read_json(
        root=root,
        relative=_relative_string(
            offline["attestation_path"], "model attestation path"
        ),
        name="S201 local model attestation",
        audit=audit,
        category="model",
    )
    _expect(
        snapshot.sha256, offline.get("attestation_sha256"), "model attestation hash"
    )
    _expect(
        attestation.get("schema_version"),
        MODEL_ATTESTATION_SCHEMA_VERSION,
        "model schema",
    )
    _expect(attestation.get("status"), "ready", "model status")
    _expect(attestation.get("model_id"), identity.get("model_id"), "model id")
    _expect(attestation.get("revision"), identity.get("revision"), "model revision")
    _expect(attestation.get("processor_id"), identity.get("model_id"), "processor id")
    _expect(
        attestation.get("processor_revision"),
        identity.get("revision"),
        "processor revision",
    )
    _expect(attestation.get("local_files_only"), True, "local-files-only")
    _expect(
        attestation.get("preflight_network_access_count"),
        0,
        "model preflight network access",
    )
    _expect(
        attestation.get("preflight_download_count"),
        0,
        "model preflight download count",
    )
    _expect(attestation.get("license"), identity.get("license"), "model license")
    _expect(
        attestation.get("license_sha256"),
        identity.get("license_sha256"),
        "model license hash",
    )
    _expect(
        attestation.get("s108_commit_sha256"),
        s108.commit_snapshot.sha256,
        "model S108 binding",
    )
    _expect(
        attestation.get("s200_readiness_lock_sha256"),
        s200_evidence.lock_snapshot.sha256,
        "model S200 binding",
    )
    bundle_root = _relative_string(offline["bundle_root"], "model bundle root")
    _expect(attestation.get("bundle_root"), bundle_root, "model bundle root")
    files = _list(attestation.get("files"), "model files")
    if not files:
        raise S201ContractError("model bundle attestation contains no files")
    snapshots: list[FileSnapshot] = []
    seen: set[str] = set()
    required_kinds: set[str] = set()
    for index, raw in enumerate(files):
        record = _mapping(raw, f"model file {index}")
        relative = _relative_string(record.get("path"), f"model file {index} path")
        if not relative.startswith(bundle_root + "/"):
            raise S201ContractError("model file escapes the pinned bundle root")
        if relative in seen:
            raise S201ContractError(f"duplicate model file: {relative}")
        seen.add(relative)
        kind = _string(record.get("kind"), f"model file {index} kind")
        if kind not in {
            "model_weight",
            "model_config",
            "generation_config",
            "preprocessor_config",
            "tokenizer",
        }:
            raise S201ContractError(f"unsupported model file kind: {kind}")
        if kind == "model_weight" and not relative.endswith(".safetensors"):
            raise S201ContractError("model weights must use safetensors")
        if relative.endswith((".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")):
            raise S201ContractError("pickle-capable model files are forbidden")
        required_kinds.add(kind)
        expected_sha = _sha256(record.get("sha256"), f"model file {index} SHA")
        expected_size = _integer(
            record.get("size_bytes"),
            f"model file {index} size",
            minimum=1,
        )
        audit.model_path_resolution_count += 1
        _, observed = _snapshot_file(
            root=root,
            relative=relative,
            name=f"local model file {index}",
        )
        if kind == "model_weight":
            audit.model_weight_read_count += 1
        else:
            audit.model_metadata_read_count += 1
        _expect(observed.sha256, expected_sha, f"model file {index} hash")
        _expect(observed.size_bytes, expected_size, f"model file {index} size")
        snapshots.append(observed)
    if required_kinds != {
        "model_weight",
        "model_config",
        "generation_config",
        "preprocessor_config",
        "tokenizer",
    }:
        raise S201ContractError("local model bundle lacks a required file kind")
    _expect(
        _sha256(attestation.get("binding_sha256"), "model binding"),
        _binding_sha256(attestation),
        "model binding",
    )
    return snapshot, tuple(snapshots)


def _validate_manifests_without_rows(
    *,
    root: Path,
    records: Mapping[str, Mapping[str, Any]],
    audit: AccessAudit,
) -> tuple[Mapping[str, str], tuple[FileSnapshot, ...]]:
    hashes: dict[str, str] = {}
    snapshots: list[FileSnapshot] = []
    for role_name in ("fleurs_ko_kr", "zeroth_korean", "s2_expert_content"):
        manifest = _mapping(records[role_name]["manifest"], f"{role_name} manifest")
        relative = _relative_string(manifest["path"], f"{role_name} manifest path")
        expected = _sha256(manifest["sha256"], f"{role_name} manifest SHA")
        audit.data_path_resolution_count += 1
        _, snapshot = _snapshot_file(
            root=root,
            relative=relative,
            name=f"{role_name} manifest",
        )
        audit.data_manifest_read_count += 1
        _expect(snapshot.sha256, expected, f"{role_name} manifest hash")
        hashes[role_name] = snapshot.sha256
        snapshots.append(snapshot)
    return hashes, tuple(snapshots)


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    before_final_verify_hook: Callable[[], None] | None = None,
) -> PreflightResult:
    """Run read-only gates in order; never download, infer, score, or publish."""

    root = workspace_root.resolve(strict=True)
    audit = AccessAudit()
    try:
        static = validate_static_config(
            workspace_root=root,
            config_path=config_path,
            config=config,
        )
        audit.source_pin_read_count = len(static.source_snapshots)
        s108 = _deep_validate_s108(root=root, config=config, audit=audit)
        s200_evidence = _deep_validate_s200_lock(
            root=root,
            config=config,
            s108=s108,
            audit=audit,
        )
        _, threshold_snapshot = _validate_threshold_preregistration(
            root=root,
            config=config,
            s108=s108,
            s200_evidence=s200_evidence,
            audit=audit,
        )
        roles = _mapping(config["data_roles"], "data roles")
        native = _mapping(roles["native_external_asr"], "external ASR roles")
        data_records: dict[str, Mapping[str, Any]] = {}
        data_snapshots: list[FileSnapshot] = []
        for role_name, contract in (
            ("fleurs_ko_kr", _mapping(native["fleurs_ko_kr"], "FLEURS contract")),
            ("zeroth_korean", _mapping(native["zeroth_korean"], "ZEROTH contract")),
            (
                "s2_expert_content",
                _mapping(roles["s2_expert_content"], "expert content contract"),
            ),
        ):
            record, snapshot = _validate_data_attestation(
                root=root,
                role_name=role_name,
                contract=contract,
                s108=s108,
                s200_evidence=s200_evidence,
                audit=audit,
            )
            data_records[role_name] = record
            data_snapshots.append(snapshot)
        model_attestation, model_snapshots = _validate_local_model(
            root=root,
            config=config,
            s108=s108,
            s200_evidence=s200_evidence,
            audit=audit,
        )
        manifest_hashes, manifest_snapshots = _validate_manifests_without_rows(
            root=root,
            records=data_records,
            audit=audit,
        )
        if before_final_verify_hook is not None:
            before_final_verify_hook()
        for snapshot in static.source_snapshots:
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name="source_pin_read_count",
            )
        for snapshot in (
            s108.commit_snapshot,
            s108.registry_snapshot,
            s200_evidence.lock_snapshot,
        ):
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name="predecessor_metadata_read_count",
            )
        for snapshot in s108.artifact_snapshots:
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name="predecessor_artifact_read_count",
            )
        _verify_snapshot(
            root,
            threshold_snapshot,
            f"preflight input {threshold_snapshot.path}",
            audit=audit,
            counter_name="threshold_attestation_read_count",
        )
        for snapshot in data_snapshots:
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name="external_attestation_read_count",
            )
        _verify_snapshot(
            root,
            model_attestation,
            f"preflight input {model_attestation.path}",
            audit=audit,
            counter_name="model_metadata_read_count",
        )
        for snapshot in model_snapshots:
            counter = (
                "model_weight_read_count"
                if snapshot.path.endswith(".safetensors")
                else "model_metadata_read_count"
            )
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name=counter,
            )
        for snapshot in manifest_snapshots:
            _verify_snapshot(
                root,
                snapshot,
                f"preflight input {snapshot.path}",
                audit=audit,
                counter_name="data_manifest_read_count",
            )
        return PreflightResult(
            status="ready_for_separately_reviewed_runner_implementation",
            config_sha256=static.config_sha256,
            s108_commit_sha256=s108.commit_snapshot.sha256,
            s200_readiness_lock_sha256=s200_evidence.lock_snapshot.sha256,
            threshold_preregistration_sha256=threshold_snapshot.sha256,
            data_attestation_hashes={
                name: snapshot.sha256
                for name, snapshot in zip(
                    ("fleurs_ko_kr", "zeroth_korean", "s2_expert_content"),
                    data_snapshots,
                    strict=True,
                )
            },
            model_attestation_sha256=model_attestation.sha256,
            manifest_hashes=manifest_hashes,
            audit=audit.as_dict(),
        )
    except S201ContractError as exc:
        if exc.audit is None:
            exc.audit = audit
        raise
    except OSError as exc:
        raise S201ContractError(
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
        "production_runner_implemented": False,
        "execution_allowed": False,
        "completion_claim_allowed": False,
    }


def failure_result(exc: S201ContractError) -> Mapping[str, Any]:
    audit = exc.audit.as_dict() if exc.audit is not None else AccessAudit().as_dict()
    return {
        "status": "failed_closed",
        "stage": exc.stage,
        "error": str(exc),
        **audit,
        "production_runner_implemented": False,
        "execution_allowed": False,
        "completion_claim_allowed": False,
    }
