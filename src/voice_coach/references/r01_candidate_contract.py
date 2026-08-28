"""Fail-closed R01 human/CosyVoice candidate static-readiness contract.

This module intentionally performs no recording, model download, TTS
inference, audio read/write, GPU query, metric computation, candidate-ID
generation, manifest publication, or R02 evaluation.  The full preflight
orders its checks as follows:

1. validate the C1 config, source pins, schemas, and attestation templates;
2. deep-validate the completed R00 sentence card from public/aggregate evidence;
3. require exact human consent/license/recording attestations;
4. require exact CosyVoice code/model/weight-license/runtime/GPU attestations;
5. only then permit a separate execution package to resolve data/model/output.

The canonical configuration deliberately leaves both external attestations at
``TBD_EXTERNAL_ATTESTATION``.  Therefore the current full preflight must stop
after R00 validation and before any protected path, model, environment, GPU, or
output path is resolved.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from types import ModuleType
from typing import Any
import uuid

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
TBD = "TBD_EXTERNAL_ATTESTATION"
STATIC_STATUS = "static_preflight_only"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
CANDIDATE_ID_RE = re.compile(r"^r01c_([0-9a-f]{32})$")
SENTENCE_IDS = tuple(f"R00-{index:02d}" for index in range(1, 21))
SOURCE_KINDS = ("human_recording", "cosyvoice_tts")
SPEED_BUCKETS = ("slow", "neutral", "fast")
PITCH_BUCKETS = ("low", "neutral", "high")

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_r00",
    "external_attestations",
    "candidate_design",
    "human_recordings",
    "cosyvoice",
    "privacy_and_consent",
    "publication_gate",
    "execution_order",
    "outputs",
}

EXPECTED_EXECUTION_ORDER = (
    "validate_r01_c1_config_source_pins_and_templates",
    "deep_validate_r00_completion_from_public_or_aggregate_evidence",
    "validate_exact_human_consent_license_and_recording_attestation",
    "validate_exact_cosyvoice_code_model_weight_license_attestation",
    "validate_exact_offline_runtime_environment_and_gpu_pin",
    "resolve_human_recording_and_candidate_data_paths",
    "resolve_local_cosyvoice_model_bundle",
    "stop_ready_for_separate_recording_and_inference_execution",
)

SOURCE_PATHS = (
    ".codex/ai_rule_developer/GLOBAL_RULES.md",
    "artifacts/reference/r00/r00_sentence_card_v1.validation.json",
    "configs/reference/r00_sentence_card_v1.schema.json",
    "configs/reference/r01_candidate_manifest_v1.schema.json",
    "configs/reference/r01_cosyvoice_model_attestation_v1.template.json",
    "configs/reference/r01_human_recording_attestation_v1.template.json",
    "data/README.md",
    "data/reference/r00/CC0_NOTICE.txt",
    "data/reference/r00/r00_sentence_card_v1.json",
    "docs/paper/EXPERIMENTAL_MANUSCRIPT_KO.md",
    "docs/paper/RELATED_WORK_SOURCE_LEDGER_KO.md",
    "scripts/validate_r00_sentence_card.py",
)

R00_EXPECTED = {
    "card_path": "data/reference/r00/r00_sentence_card_v1.json",
    "card_sha256": "08dc149ccf10006a587e6216dda0471c19b5c55eab6b320b4348587527d82c4d",
    "schema_path": "configs/reference/r00_sentence_card_v1.schema.json",
    "schema_sha256": "1f8bcf0325414e1923cda0def9a973ac2f7be7bd85cca4ee869743ef862a7b92",
    "notice_path": "data/reference/r00/CC0_NOTICE.txt",
    "notice_sha256": "ffd86dc89cf0957dccc61f6ecb7af1c7ebbbdbff2bd87c951e54c40afc1262c9",
    "validator_path": "scripts/validate_r00_sentence_card.py",
    "validator_sha256": "967517e74194936c91dad1eed4fcfb0c059b5480af32b8adfc9725fdb7d6e7d0",
    "validation_report_path": (
        "artifacts/reference/r00/r00_sentence_card_v1.validation.json"
    ),
    "validation_report_sha256": (
        "95b4fd861a3c21222ff13859cfbc9c196e3fd06493852bca823d8a63dc2816dd"
    ),
}

LEDGER_COSYVOICE = {
    "canonical_repository": "https://github.com/QwenAudio/CosyVoice",
    "repository_revision": "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc",
    "repository_license_spdx": "Apache-2.0",
    "repository_license_sha256": (
        "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    ),
    "model_provider": "FunAudioLLM",
    "model_id": "CosyVoice2-0.5B",
    "model_revision": "eec1ae6c79877dbd9379285cf8789c9e0879293d",
    "model_card_license_metadata": "apache-2.0",
    "standalone_weight_license_file_observed": False,
    "grpo_recipe_publicly_observed": True,
    "grpo_effect_for_this_project_verified": False,
    "authorization_inferred_from_ledger": False,
}

CANDIDATE_MANIFEST_KEYS = {
    "schema_version",
    "manifest_id",
    "status",
    "r00_card_sha256",
    "human_attestation_sha256",
    "cosyvoice_attestation_sha256",
    "privacy_zone",
    "publication_allowed",
    "r02_hard_gate_passed",
    "candidate_rows",
}

CANDIDATE_ROW_KEYS = {
    "candidate_id",
    "sentence_id",
    "target_text_sha256",
    "source_kind",
    "source_attestation_sha256",
    "speaker_id_sha256",
    "audio_object_id_sha256",
    "audio_sha256",
    "privacy_zone",
    "consent_or_model_use_verified",
    "speed_bucket",
    "pitch_bucket",
    "preservation_group_id_sha256",
    "r02_hard_gate_status",
    "publication_allowed",
}

FORBIDDEN_MANIFEST_FIELDS = (
    "speaker_name",
    "legal_name",
    "email",
    "phone",
    "address",
    "date_of_birth",
    "raw_speaker_id",
    "source_path",
    "audio_path",
    "recording_path",
    "target_text",
    "transcript",
    "free_text_note",
    "r02_score",
    "mos",
    "cmos",
)

HUMAN_ATTESTATION_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "signed_at_utc",
    "signed_by_role",
    "institutional_authority",
    "consent_protocol",
    "recording_protocol",
    "speaker_receipts",
    "receipt_contract",
    "completion_claim_allowed",
}

INSTITUTIONAL_AUTHORITY_KEYS = {
    "determination_or_approval_id",
    "approval_revision",
    "approval_document_sha256",
    "valid_from",
    "valid_until",
}

CONSENT_PROTOCOL_KEYS = {
    "protocol_id",
    "protocol_revision",
    "protocol_sha256",
    "consent_form_revision",
    "consent_form_sha256",
    "allowed_purposes",
    "tts_training_allowed",
    "voice_cloning_allowed",
    "public_release_allowed",
    "commercial_use_allowed",
    "withdrawal_process_id",
    "retention_policy_id",
    "deletion_protocol_id",
}

RECORDING_PROTOCOL_KEYS = {
    "protocol_id",
    "protocol_revision",
    "protocol_sha256",
    "r00_card_sha256",
    "room_specification",
    "microphone_make_model",
    "audio_interface_make_model",
    "mouth_to_microphone_distance_cm",
    "sample_rate_hz",
    "bit_depth",
    "channel_count",
    "container_format",
    "operator_training_revision",
    "quality_control_protocol_sha256",
}

SPEAKER_RECEIPT_KEYS = {
    "speaker_id_sha256",
    "consent_receipt_sha256",
    "recording_license_grant_sha256",
    "native_korean_attested",
    "consent_active",
    "withdrawn",
    "expired",
    "valid_from",
    "valid_until",
    "permitted_sentence_ids",
}

RECEIPT_CONTRACT_KEYS = {
    "minimum_distinct_speakers",
    "speaker_id_format",
    "identity_mapping_zone",
    "raw_capture_zone",
    "pseudonymized_candidate_audio_zone",
    "each_receipt_requires_consent_receipt_sha256",
    "each_receipt_requires_recording_license_grant_sha256",
    "withdrawn_or_expired_receipts_prohibited",
}

COSYVOICE_ATTESTATION_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "signed_at_utc",
    "signed_by_role",
    "ledger_observation_only",
    "artifact_authorization",
    "runtime_environment",
    "gpu_pin",
    "completion_claim_allowed",
}

ARTIFACT_AUTHORIZATION_KEYS = {
    "decision",
    "exact_repository_url",
    "exact_repository_revision",
    "code_license_spdx",
    "code_license_evidence_sha256",
    "exact_model_provider",
    "exact_model_id",
    "exact_model_revision",
    "weight_manifest_sha256",
    "weight_license_spdx",
    "weight_license_evidence_sha256",
    "research_inference_use_allowed",
    "redistribution_allowed",
    "commercial_use_allowed",
    "grpo_allowed",
    "fine_tuning_allowed",
    "voice_cloning_allowed",
}

RUNTIME_ENVIRONMENT_KEYS = {
    "environment_lock_sha256",
    "container_image_digest",
    "python_version",
    "pytorch_version",
    "cuda_runtime_version",
    "cosyvoice_inference_entrypoint_sha256",
    "local_weight_bundle_manifest_sha256",
    "network_download_allowed",
    "local_files_only",
}

GPU_PIN_KEYS = {
    "machine_id",
    "device_uuid",
    "device_model",
    "visible_device_ordinal",
    "minimum_memory_bytes",
    "concurrent_training_process_allowed",
}


class R01ContractError(ValueError):
    """Raised before an unsafe or unsupported R01 action."""

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
            raise R01ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(slots=True)
class AccessAudit:
    """Counters that prove the fail-closed execution order."""

    source_pin_read_count: int = 0
    r00_validator_load_count: int = 0
    r00_deep_validation_count: int = 0
    r00_public_or_aggregate_evidence_read_count: int = 0
    r00_raw_audio_read_count: int = 0
    r00_row_level_read_count: int = 0
    human_attestation_read_attempt_count: int = 0
    human_attestation_read_count: int = 0
    cosyvoice_attestation_read_attempt_count: int = 0
    cosyvoice_attestation_read_count: int = 0
    recording_data_path_resolution_count: int = 0
    candidate_data_path_resolution_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    model_path_resolution_count: int = 0
    model_metadata_read_count: int = 0
    model_weight_read_count: int = 0
    model_download_count: int = 0
    runtime_environment_path_resolution_count: int = 0
    runtime_environment_read_count: int = 0
    gpu_access_count: int = 0
    candidate_id_generation_count: int = 0
    candidate_manifest_read_count: int = 0
    candidate_manifest_write_count: int = 0
    metric_count: int = 0
    r02_gate_read_count: int = 0
    publication_count: int = 0
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
class R00Evidence:
    report_snapshot: FileSnapshot
    report_sha256: str
    sentence_count: int


@dataclass(frozen=True, slots=True)
class ExternalEvidence:
    human_snapshot: FileSnapshot
    cosyvoice_snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class PreflightResult:
    status: str
    config_sha256: str
    r00_validation_report_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise R01ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise R01ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise R01ContractError(f"{name} must be a non-empty string")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise R01ContractError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise R01ContractError(f"{name} must be at least {minimum}")
    return value


def _number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise R01ContractError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise R01ContractError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise R01ContractError(f"{name} must be at least {minimum}")
    return result


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise R01ContractError(f"{name} must be a boolean")
    return value


def _sha256(value: object, name: str) -> str:
    text = _string(value, name)
    if SHA256_RE.fullmatch(text) is None:
        raise R01ContractError(f"{name} must be a lowercase SHA-256")
    return text


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise R01ContractError(f"{name} must be {expected!r}, got {actual!r}")


def _expect_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise R01ContractError(f"{name} keys differ; missing={missing}, extra={extra}")


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise R01ContractError(f"{name} contains a non-finite number")
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
            raise R01ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise R01ContractError(f"JSON contains a non-finite constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _relative_string(value: object, name: str) -> str:
    text = _string(value, name)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or text != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise R01ContractError(f"{name} must be a canonical relative path")
    return text


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    allow_missing_leaf: bool = False,
) -> Path:
    current = root
    parts = PurePosixPath(relative).parts
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if allow_missing_leaf:
                return root.joinpath(*parts)
            raise
        if stat.S_ISLNK(mode):
            raise R01ContractError(f"{name} may not contain a symlink: {relative}")
    return current


def _read_file(root: Path, relative: str, name: str) -> tuple[bytes, FileSnapshot]:
    relative = _relative_string(relative, name)
    path = _reject_symlink_chain(root, relative, name)
    try:
        descriptor = path.open("rb")
    except OSError as exc:
        raise R01ContractError(f"cannot read {name}: {relative}: {exc}") from exc
    with descriptor:
        before = os.fstat(descriptor.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise R01ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while chunk := descriptor.read(1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor.fileno())
    data = b"".join(chunks)
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
    if before_identity != after_identity or len(data) != before.st_size:
        raise R01ContractError(f"{name} changed while being read")
    final = _reject_symlink_chain(root, relative, name).lstat()
    if (final.st_dev, final.st_ino) != (before.st_dev, before.st_ino):
        raise R01ContractError(f"{name} path identity changed while being read")
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


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    observed = _snapshot_file(root, expected.path, name)
    if observed != expected:
        raise R01ContractError(f"{name} changed after validation: {expected.path}")


def _load_json_bytes(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise R01ContractError(f"{name} is not strict UTF-8 JSON: {exc}") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def load_json(path: Path) -> Mapping[str, Any]:
    try:
        return _load_json_bytes(path.read_bytes(), str(path))
    except OSError as exc:
        raise R01ContractError(f"cannot read JSON {path}: {exc}") from exc


def load_config(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise R01ContractError(f"cannot read config {path}: {exc}") from exc
    try:
        value = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise R01ContractError(f"config is not strict UTF-8 YAML: {exc}") from exc
    config = _mapping(value, "config")
    _reject_nonfinite_tree(config, "config")
    return config


def _validate_manifest_schema(schema: Mapping[str, Any]) -> None:
    _expect(
        schema.get("$id"),
        "https://voice-coach.local/schemas/r01_candidate_manifest_v1.schema.json",
        "candidate manifest schema $id",
    )
    properties = _mapping(schema.get("properties"), "schema.properties")
    _expect(
        set(properties),
        CANDIDATE_MANIFEST_KEYS,
        "candidate manifest schema properties",
    )
    rows = _mapping(properties["candidate_rows"], "schema.candidate_rows")
    _expect(rows.get("minItems"), 160, "schema candidate_rows.minItems")
    _expect(rows.get("maxItems"), 320, "schema candidate_rows.maxItems")
    defs = _mapping(schema.get("$defs"), "schema.$defs")
    row = _mapping(defs.get("candidateRow"), "schema.$defs.candidateRow")
    row_properties = _mapping(row.get("properties"), "candidateRow.properties")
    _expect(set(row_properties), CANDIDATE_ROW_KEYS, "candidate row properties")
    _expect(row.get("additionalProperties"), False, "candidateRow additionalProperties")
    if set(FORBIDDEN_MANIFEST_FIELDS) & set(row_properties):
        raise R01ContractError("candidate schema exposes a forbidden manifest field")


def _validate_human_template(template: Mapping[str, Any]) -> None:
    _expect_keys(template, HUMAN_ATTESTATION_KEYS, "human template")
    _expect(
        template.get("schema_version"),
        "voice_coach.r01_human_recording_attestation.v1",
        "human template schema_version",
    )
    _expect(template.get("status"), TBD, "human template status")
    _expect(template.get("speaker_receipts"), [], "human template speaker_receipts")
    _expect(
        template.get("completion_claim_allowed"),
        False,
        "human template completion claim",
    )
    consent = _mapping(template.get("consent_protocol"), "human consent protocol")
    _expect_keys(consent, CONSENT_PROTOCOL_KEYS, "human consent protocol")
    _expect(consent.get("tts_training_allowed"), False, "human TTS training")
    _expect(consent.get("voice_cloning_allowed"), False, "human voice cloning")
    _expect(consent.get("public_release_allowed"), False, "human public release")
    receipt = _mapping(template.get("receipt_contract"), "human receipt contract")
    _expect_keys(receipt, RECEIPT_CONTRACT_KEYS, "human receipt contract")
    _expect(receipt.get("minimum_distinct_speakers"), 2, "minimum human speakers")
    _expect(
        receipt.get("pseudonymized_candidate_audio_zone"),
        "D1_restricted_experiment",
        "human candidate audio zone",
    )


def _validate_cosyvoice_template(template: Mapping[str, Any]) -> None:
    _expect_keys(template, COSYVOICE_ATTESTATION_KEYS, "CosyVoice template")
    _expect(
        template.get("schema_version"),
        "voice_coach.r01_cosyvoice_model_attestation.v1",
        "CosyVoice template schema_version",
    )
    _expect(template.get("status"), TBD, "CosyVoice template status")
    _expect(
        dict(_mapping(template.get("ledger_observation_only"), "ledger observation")),
        LEDGER_COSYVOICE,
        "CosyVoice ledger observation",
    )
    authorization = _mapping(
        template.get("artifact_authorization"), "artifact authorization"
    )
    _expect_keys(
        authorization,
        ARTIFACT_AUTHORIZATION_KEYS,
        "artifact authorization template",
    )
    _expect(authorization.get("decision"), TBD, "CosyVoice authorization")
    _expect(authorization.get("grpo_allowed"), False, "CosyVoice GRPO")
    _expect(authorization.get("fine_tuning_allowed"), False, "CosyVoice fine-tuning")
    runtime = _mapping(template.get("runtime_environment"), "runtime environment")
    _expect_keys(runtime, RUNTIME_ENVIRONMENT_KEYS, "runtime template")
    _expect(runtime.get("network_download_allowed"), False, "network download")
    _expect(runtime.get("local_files_only"), True, "local files only")
    gpu = _mapping(template.get("gpu_pin"), "gpu pin")
    _expect_keys(gpu, GPU_PIN_KEYS, "gpu pin template")
    _expect(
        gpu.get("concurrent_training_process_allowed"),
        False,
        "concurrent GPU training",
    )
    _expect(
        template.get("completion_claim_allowed"),
        False,
        "CosyVoice template completion claim",
    )


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "schema_version")

    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(experiment.get("id"), "r01_human_cosyvoice_candidates_v1", "experiment.id")
    _expect(experiment.get("implementation_status"), STATIC_STATUS, "status")
    for field in (
        "actual_human_recording_allowed",
        "actual_model_download_allowed",
        "actual_tts_inference_allowed",
        "actual_gpu_access_allowed",
        "actual_audio_read_allowed",
        "actual_audio_write_allowed",
        "actual_manifest_write_allowed",
        "grpo_allowed",
        "fine_tuning_allowed",
        "metric_generation_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    predecessor = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    for field, expected in R00_EXPECTED.items():
        _expect(predecessor.get(field), expected, f"predecessor_r00.{field}")
    _expect(predecessor.get("deep_validation_required"), True, "R00 deep validation")
    _expect(predecessor.get("required_status"), "valid", "R00 status")
    _expect(predecessor.get("required_sentence_count"), 20, "R00 sentence count")
    _expect(
        predecessor.get("required_r00_completion_claim_allowed"),
        True,
        "R00 completion",
    )
    for field in (
        "required_r01_completion_claim_allowed",
        "tts_or_audio_created_must_be_false",
        "human_evaluation_completed_must_be_false",
    ):
        expected_bool = field != "required_r01_completion_claim_allowed"
        _expect(predecessor.get(field), expected_bool, f"predecessor_r00.{field}")

    external = _mapping(config.get("external_attestations"), "external_attestations")
    _expect(external.get("placeholder"), TBD, "attestation placeholder")
    _expect(
        external.get("validate_human_before_audio_or_data_resolution"),
        True,
        "human order gate",
    )
    _expect(
        external.get("validate_cosyvoice_before_model_or_environment_resolution"),
        True,
        "CosyVoice order gate",
    )
    for source, status, schema in (
        (
            "human",
            "approved_for_r01_recording",
            "voice_coach.r01_human_recording_attestation.v1",
        ),
        (
            "cosyvoice",
            "approved_for_r01_inference",
            "voice_coach.r01_cosyvoice_model_attestation.v1",
        ),
    ):
        item = _mapping(external.get(source), f"external_attestations.{source}")
        _relative_string(item.get("path"), f"{source} attestation path")
        if item.get("sha256") != TBD:
            _sha256(item.get("sha256"), f"{source} attestation SHA")
        _expect(item.get("required_status"), status, f"{source} status")
        _expect(item.get("required_schema_version"), schema, f"{source} schema")

    design = _mapping(config.get("candidate_design"), "candidate_design")
    _expect(
        tuple(_list(design.get("sentence_ids"), "sentence_ids")),
        SENTENCE_IDS,
        "sentence IDs",
    )
    counts = _mapping(design.get("candidates_per_sentence"), "candidate counts")
    _expect(dict(counts), {"minimum": 8, "planned": 12, "maximum": 16}, "counts")
    balance = _mapping(design.get("source_balance"), "source balance")
    _expect(
        balance.get("rule"),
        "exact_equal_human_and_cosyvoice_within_each_sentence",
        "source balance rule",
    )
    _expect(balance.get("minimum_per_source_per_sentence"), 4, "source minimum")
    _expect(balance.get("planned_per_source_per_sentence"), 6, "source planned")
    _expect(balance.get("maximum_per_source_per_sentence"), 8, "source maximum")
    identifier = _mapping(design.get("candidate_id"), "candidate ID")
    _expect(
        identifier.get("scheme"),
        "cryptographically_random_uuid4_without_hyphens",
        "candidate ID scheme",
    )
    _expect(identifier.get("prefix"), "r01c_", "candidate ID prefix")
    _expect(
        identifier.get("opaque_no_sentence_source_speaker_or_order_encoding"),
        True,
        "candidate ID opacity",
    )
    _expect(identifier.get("globally_unique"), True, "candidate ID uniqueness")
    diversity = _mapping(design.get("diversity_preservation"), "diversity preservation")
    _expect(
        tuple(_list(diversity.get("dimensions"), "diversity dimensions")),
        ("speaker", "speed", "pitch"),
        "diversity dimensions",
    )
    _expect(
        diversity.get("minimum_distinct_speakers_per_source_per_sentence"),
        2,
        "minimum source speakers",
    )
    _expect(
        tuple(_list(diversity.get("required_speed_buckets"), "speed buckets")),
        SPEED_BUCKETS,
        "speed buckets",
    )
    _expect(
        tuple(_list(diversity.get("required_pitch_buckets"), "pitch buckets")),
        PITCH_BUCKETS,
        "pitch buckets",
    )
    _expect(
        diversity.get("preserve_all_design_cells_until_r02"),
        True,
        "preserve design cells",
    )
    _expect(
        diversity.get("posthoc_relabel_or_collapse_allowed"),
        False,
        "posthoc collapse",
    )

    human = _mapping(config.get("human_recordings"), "human_recordings")
    _expect(human.get("source_kind"), "human_recording", "human source")
    for field in (
        "required_native_korean_speaker_attestation",
        "exact_individual_consent_receipt_required",
        "exact_recording_license_grant_required",
        "exact_recording_protocol_required",
    ):
        _expect(human.get(field), True, f"human_recordings.{field}")
    for field in (
        "expired_or_withdrawn_consent_allowed",
        "model_training_allowed",
        "voice_cloning_allowed",
        "public_release_allowed",
        "commercial_use_allowed",
    ):
        _expect(human.get(field), False, f"human_recordings.{field}")
    _expect(
        human.get("pseudonymized_candidate_audio_zone"),
        "D1_restricted_experiment",
        "human D1 audio",
    )

    cosy = _mapping(config.get("cosyvoice"), "cosyvoice")
    _expect(cosy.get("source_kind"), "cosyvoice_tts", "CosyVoice source")
    _expect(
        dict(_mapping(cosy.get("ledger_observation"), "CosyVoice ledger")),
        LEDGER_COSYVOICE,
        "CosyVoice ledger",
    )
    cosy_attestation_fields = (
        "authorization_decision",
        "exact_weight_license_decision",
        "exact_runtime_environment_lock_sha256",
        "exact_local_weight_bundle_manifest_sha256",
        "exact_gpu_machine_id",
        "exact_gpu_device_uuid",
        "exact_gpu_device_model",
        "exact_gpu_visible_device_ordinal",
    )
    cosy_attestation_values = [cosy.get(field) for field in cosy_attestation_fields]
    if any(value == TBD for value in cosy_attestation_values) and not all(
        value == TBD for value in cosy_attestation_values
    ):
        raise R01ContractError(
            "CosyVoice exact authorization/runtime/GPU fields must be all TBD "
            "or all externally attested"
        )
    if not all(value == TBD for value in cosy_attestation_values):
        _expect(
            cosy.get("authorization_decision"),
            "approved_for_r01_research_inference",
            "cosyvoice.authorization_decision",
        )
        _string(
            cosy.get("exact_weight_license_decision"),
            "cosyvoice.exact_weight_license_decision",
        )
        _sha256(
            cosy.get("exact_runtime_environment_lock_sha256"),
            "cosyvoice.exact_runtime_environment_lock_sha256",
        )
        _sha256(
            cosy.get("exact_local_weight_bundle_manifest_sha256"),
            "cosyvoice.exact_local_weight_bundle_manifest_sha256",
        )
        _string(cosy.get("exact_gpu_machine_id"), "cosyvoice.exact_gpu_machine_id")
        _string(cosy.get("exact_gpu_device_uuid"), "cosyvoice.exact_gpu_device_uuid")
        _string(cosy.get("exact_gpu_device_model"), "cosyvoice.exact_gpu_device_model")
        _integer(
            cosy.get("exact_gpu_visible_device_ordinal"),
            "cosyvoice.exact_gpu_visible_device_ordinal",
            minimum=0,
        )
    cosy_external = _mapping(
        external.get("cosyvoice"), "external_attestations.cosyvoice"
    )
    if (cosy_external.get("sha256") == TBD) != all(
        value == TBD for value in cosy_attestation_values
    ):
        raise R01ContractError(
            "CosyVoice external SHA and exact config bindings must become "
            "attested together"
        )
    _expect(cosy.get("network_download_allowed"), False, "CosyVoice download")
    _expect(cosy.get("local_files_only"), True, "CosyVoice local-only")
    _expect(cosy.get("inference_only"), True, "CosyVoice inference-only")
    for field in ("grpo_allowed", "fine_tuning_allowed", "voice_cloning_allowed"):
        _expect(cosy.get(field), False, f"cosyvoice.{field}")

    privacy = _mapping(config.get("privacy_and_consent"), "privacy_and_consent")
    _expect(
        privacy.get("manifest_zone"),
        "D1_pseudonymized_restricted_experiment",
        "manifest zone",
    )
    _expect(
        privacy.get("candidate_audio_zone"),
        "D1_pseudonymized_restricted_experiment",
        "audio zone",
    )
    for field in (
        "public_zone_allowed",
        "plaintext_prompt_in_manifest_allowed",
        "raw_speaker_identity_in_manifest_allowed",
        "source_path_in_manifest_allowed",
        "free_text_note_in_manifest_allowed",
    ):
        _expect(privacy.get(field), False, f"privacy_and_consent.{field}")
    _expect(
        tuple(_list(privacy.get("forbidden_manifest_fields"), "forbidden fields")),
        FORBIDDEN_MANIFEST_FIELDS,
        "forbidden fields",
    )

    publication = _mapping(config.get("publication_gate"), "publication_gate")
    _expect(
        publication.get("r02_hard_gate_required_before_any_candidate_publication"),
        True,
        "R02 publication gate",
    )
    _expect(publication.get("r02_gate_status"), "not_run", "R02 status")
    for field in (
        "publication_allowed",
        "reference_bank_claim_allowed",
        "quality_superiority_claim_allowed",
        "learning_effect_claim_allowed",
    ):
        _expect(publication.get(field), False, f"publication_gate.{field}")

    order = tuple(_list(config.get("execution_order"), "execution_order"))
    _expect(order, EXPECTED_EXECUTION_ORDER, "execution order")

    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "candidate_manifest_schema_path",
        "candidate_manifest_path",
        "candidate_audio_root",
        "failure_report_path",
    ):
        _relative_string(outputs.get(field), f"outputs.{field}")
    _expect(
        outputs.get("candidate_manifest_schema_path"),
        "configs/reference/r01_candidate_manifest_v1.schema.json",
        "candidate manifest schema path",
    )
    _expect(outputs.get("no_overwrite"), True, "output no-overwrite")
    _expect(outputs.get("atomic_publish_required"), True, "atomic publish")
    _expect(outputs.get("write_enabled_in_this_package"), False, "write enabled")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only C1/public pins; never resolve runtime or output paths."""

    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)

    try:
        if not config_path.is_absolute():
            raise ValueError("config path must be absolute")
        config_relative = config_path.relative_to(workspace_root)
        _relative_string(config_relative.as_posix(), "config path")
    except ValueError as exc:
        raise R01ContractError("config must be a regular workspace file") from exc
    config_snapshot = _snapshot_file(
        workspace_root, config_relative.as_posix(), "R01 config"
    )

    sources = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        sources.get("access_scope"),
        "C1_public_or_aggregate_only",
        "canonical source scope",
    )
    pins = _list(sources.get("pins"), "canonical_sources.pins")
    observed_paths: list[str] = []
    snapshots: list[FileSnapshot] = []
    payloads: dict[str, Mapping[str, Any]] = {}
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"canonical_sources.pins[{index}]")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        relative = _relative_string(pin.get("path"), f"source pin {index}.path")
        expected_hash = _sha256(pin.get("sha256"), f"source pin {index}.sha256")
        data, snapshot = _read_file(
            workspace_root, relative, f"canonical source {index}"
        )
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_hash:
            raise R01ContractError(
                f"canonical source SHA-256 mismatch: {relative}",
                audit=access,
            )
        observed_paths.append(relative)
        snapshots.append(snapshot)
        if relative.endswith(".json"):
            payloads[relative] = _load_json_bytes(data, relative)
    _expect(tuple(observed_paths), SOURCE_PATHS, "canonical source paths/order")
    if len(set(observed_paths)) != len(observed_paths):
        raise R01ContractError("canonical source paths must be unique", audit=access)
    if observed_paths != sorted(observed_paths):
        raise R01ContractError("canonical source pins must be sorted", audit=access)

    _validate_manifest_schema(
        payloads["configs/reference/r01_candidate_manifest_v1.schema.json"]
    )
    _validate_human_template(
        payloads["configs/reference/r01_human_recording_attestation_v1.template.json"]
    )
    _validate_cosyvoice_template(
        payloads["configs/reference/r01_cosyvoice_model_attestation_v1.template.json"]
    )

    source_set = set(observed_paths)
    outputs = _mapping(config.get("outputs"), "outputs")
    for field in (
        "candidate_manifest_path",
        "candidate_audio_root",
        "failure_report_path",
    ):
        target = _relative_string(outputs.get(field), f"outputs.{field}")
        if target in source_set:
            raise R01ContractError(f"output overlaps canonical source: {target}")
        target_parts = PurePosixPath(target).parts
        if any(
            PurePosixPath(source).parts[: len(target_parts)] == target_parts
            for source in source_set
        ):
            raise R01ContractError(f"output contains canonical source: {target}")

    policy_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "experiment": config["experiment"],
                "predecessor_r00": config["predecessor_r00"],
                "external_attestations": config["external_attestations"],
                "candidate_design": config["candidate_design"],
                "human_recordings": config["human_recordings"],
                "cosyvoice": config["cosyvoice"],
                "privacy_and_consent": config["privacy_and_consent"],
                "publication_gate": config["publication_gate"],
                "execution_order": config["execution_order"],
                "outputs": config["outputs"],
            }
        )
    ).hexdigest()
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        source_snapshots=tuple(snapshots),
        policy_sha256=policy_sha256,
    )


def _load_r00_validator(
    *, workspace_root: Path, validator_relative: str, audit: AccessAudit
) -> ModuleType:
    validator_path = _reject_symlink_chain(
        workspace_root, validator_relative, "R00 validator"
    )
    module_name = f"_voice_coach_r00_validator_{hashlib.sha256(str(validator_path).encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, validator_path)
    if spec is None or spec.loader is None:
        raise R01ContractError(
            "cannot load R00 validator", stage="predecessor_r00", audit=audit
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise R01ContractError(
            f"R00 validator import failed: {exc}",
            stage="predecessor_r00",
            audit=audit,
        ) from exc
    audit.r00_validator_load_count += 1
    return module


def deep_validate_r00(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    static: StaticValidation,
    audit: AccessAudit,
) -> R00Evidence:
    """Recompute R00 validation and compare it to the immutable report."""

    predecessor = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    validator_relative = _relative_string(
        predecessor.get("validator_path"), "R00 validator path"
    )
    validator_snapshot = next(
        (
            snapshot
            for snapshot in static.source_snapshots
            if snapshot.path == validator_relative
        ),
        None,
    )
    if validator_snapshot is None:
        raise R01ContractError(
            "R00 validator is not source-pinned",
            stage="predecessor_r00",
            audit=audit,
        )
    _verify_snapshot(workspace_root, validator_snapshot, "R00 validator")
    module = _load_r00_validator(
        workspace_root=workspace_root,
        validator_relative=validator_relative,
        audit=audit,
    )
    validate_bundle = getattr(module, "validate_bundle", None)
    if not callable(validate_bundle):
        raise R01ContractError(
            "R00 validator has no callable validate_bundle",
            stage="predecessor_r00",
            audit=audit,
        )
    try:
        computed = validate_bundle(workspace_root=workspace_root)
    except Exception as exc:
        raise R01ContractError(
            f"R00 deep validation failed: {exc}",
            stage="predecessor_r00",
            audit=audit,
        ) from exc
    computed_report = _mapping(computed, "computed R00 report")
    audit.r00_deep_validation_count += 1

    data_access = _mapping(computed_report.get("data_access"), "R00 data_access")
    audit.r00_public_or_aggregate_evidence_read_count += sum(
        _integer(data_access.get(field), f"R00 data_access.{field}", minimum=0)
        for field in (
            "public_sentence_card_files_read",
            "public_schema_files_read",
            "public_license_notice_files_read",
            "public_or_aggregate_evidence_files_read",
            "prompt_hash_values_read",
        )
    )
    audit.r00_raw_audio_read_count += _integer(
        data_access.get("raw_audio_files_read"), "R00 raw audio reads", minimum=0
    )
    audit.r00_row_level_read_count += _integer(
        data_access.get("row_level_files_read"), "R00 row reads", minimum=0
    )
    if audit.r00_raw_audio_read_count or audit.r00_row_level_read_count:
        raise R01ContractError(
            "R00 deep validator accessed protected rows or audio",
            stage="predecessor_r00",
            audit=audit,
        )

    report_relative = _relative_string(
        predecessor.get("validation_report_path"), "R00 report path"
    )
    report_data, report_snapshot = _read_file(
        workspace_root, report_relative, "R00 immutable validation report"
    )
    if report_snapshot.sha256 != predecessor.get("validation_report_sha256"):
        raise R01ContractError(
            "R00 validation report SHA-256 mismatch",
            stage="predecessor_r00",
            audit=audit,
        )
    immutable_report = _load_json_bytes(report_data, "R00 immutable report")
    if canonical_json_bytes(computed_report) != canonical_json_bytes(immutable_report):
        raise R01ContractError(
            "R00 recomputation differs from immutable report",
            stage="predecessor_r00",
            audit=audit,
        )
    _expect(computed_report.get("status"), "valid", "R00 report status")
    completion = _mapping(computed_report.get("completion"), "R00 completion")
    _expect(
        completion.get("r00_completion_claim_allowed"),
        True,
        "R00 completion claim",
    )
    _expect(
        completion.get("r01_completion_claim_allowed"),
        False,
        "R01 completion claim",
    )
    _expect(completion.get("tts_or_audio_created"), False, "R00 audio creation")
    _expect(
        completion.get("human_evaluation_completed"),
        False,
        "R00 human evaluation",
    )
    checks = _mapping(computed_report.get("checks"), "R00 checks")
    sentence_count = _integer(checks.get("sentence_count"), "R00 sentence count")
    _expect(sentence_count, 20, "R00 sentence count")
    return R00Evidence(
        report_snapshot=report_snapshot,
        report_sha256=report_snapshot.sha256,
        sentence_count=sentence_count,
    )


def _contains_tbd(value: Any) -> bool:
    if value == TBD:
        return True
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tbd(item) for item in value)
    return False


def _read_attestation(
    *,
    workspace_root: Path,
    raw: Mapping[str, Any],
    source: str,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    hash_value = raw.get("sha256")
    if hash_value == TBD:
        raise R01ContractError(
            f"{source} attestation is {TBD}",
            stage=f"{source}_attestation",
            audit=audit,
        )
    expected_hash = _sha256(hash_value, f"{source} attestation SHA")
    relative = _relative_string(raw.get("path"), f"{source} attestation path")
    if source == "human":
        audit.human_attestation_read_attempt_count += 1
    else:
        audit.cosyvoice_attestation_read_attempt_count += 1
    data, snapshot = _read_file(
        workspace_root, relative, f"{source} external attestation"
    )
    if snapshot.sha256 != expected_hash:
        raise R01ContractError(
            f"{source} attestation SHA-256 mismatch",
            stage=f"{source}_attestation",
            audit=audit,
        )
    if source == "human":
        audit.human_attestation_read_count += 1
    else:
        audit.cosyvoice_attestation_read_count += 1
    return _load_json_bytes(data, f"{source} attestation"), snapshot


def _require_no_tbd(value: Mapping[str, Any], name: str) -> None:
    if _contains_tbd(value):
        raise R01ContractError(f"{name} contains {TBD}")


def validate_human_attestation(
    payload: Mapping[str, Any],
    *,
    r00_card_sha256: str,
) -> None:
    """Validate a future signed aggregate human-recording attestation."""

    _require_no_tbd(payload, "human attestation")
    _expect_keys(payload, HUMAN_ATTESTATION_KEYS, "human attestation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r01_human_recording_attestation.v1",
        "human attestation schema",
    )
    _expect(payload.get("status"), "approved_for_r01_recording", "human status")
    _string(payload.get("attestation_id"), "human attestation ID")
    _string(payload.get("signed_at_utc"), "human signed_at_utc")
    _string(payload.get("signed_by_role"), "human signed_by_role")
    institutional = _mapping(
        payload.get("institutional_authority"), "institutional authority"
    )
    _expect_keys(
        institutional,
        INSTITUTIONAL_AUTHORITY_KEYS,
        "institutional authority",
    )
    for field in (
        "determination_or_approval_id",
        "approval_revision",
        "valid_from",
        "valid_until",
    ):
        _string(institutional.get(field), f"institutional authority.{field}")
    _sha256(
        institutional.get("approval_document_sha256"),
        "institutional approval document SHA",
    )
    consent = _mapping(payload.get("consent_protocol"), "consent protocol")
    _expect_keys(consent, CONSENT_PROTOCOL_KEYS, "consent protocol")
    for field in (
        "protocol_id",
        "protocol_revision",
        "consent_form_revision",
        "withdrawal_process_id",
        "retention_policy_id",
        "deletion_protocol_id",
    ):
        _string(consent.get(field), f"consent protocol.{field}")
    _sha256(consent.get("protocol_sha256"), "consent protocol SHA")
    _sha256(consent.get("consent_form_sha256"), "consent form SHA")
    _expect(
        tuple(_list(consent.get("allowed_purposes"), "allowed purposes")),
        (
            "research_reference_candidate_generation",
            "automated_quality_gating",
            "blinded_research_evaluation",
        ),
        "allowed purposes",
    )
    for field in (
        "tts_training_allowed",
        "voice_cloning_allowed",
        "public_release_allowed",
        "commercial_use_allowed",
    ):
        _expect(consent.get(field), False, f"consent.{field}")
    recording = _mapping(payload.get("recording_protocol"), "recording protocol")
    _expect_keys(recording, RECORDING_PROTOCOL_KEYS, "recording protocol")
    for field in (
        "protocol_id",
        "protocol_revision",
        "room_specification",
        "microphone_make_model",
        "audio_interface_make_model",
        "operator_training_revision",
    ):
        _string(recording.get(field), f"recording protocol.{field}")
    _sha256(recording.get("protocol_sha256"), "recording protocol SHA")
    _expect(recording.get("r00_card_sha256"), r00_card_sha256, "R00 card binding")
    _number(
        recording.get("mouth_to_microphone_distance_cm"),
        "mouth-to-microphone distance",
        minimum=1.0,
    )
    _integer(recording.get("sample_rate_hz"), "sample rate", minimum=16000)
    bit_depth = _integer(recording.get("bit_depth"), "bit depth", minimum=16)
    if bit_depth not in {16, 24, 32}:
        raise R01ContractError("bit depth must be one of 16, 24, or 32")
    channels = _integer(recording.get("channel_count"), "channel count", minimum=1)
    if channels not in {1, 2}:
        raise R01ContractError("channel count must be 1 or 2")
    container = _string(recording.get("container_format"), "container format")
    if container.lower() not in {"wav", "flac"}:
        raise R01ContractError("container format must be WAV or FLAC")
    _sha256(
        recording.get("quality_control_protocol_sha256"),
        "recording QC protocol SHA",
    )
    receipts = _list(payload.get("speaker_receipts"), "speaker receipts")
    if len(receipts) < 2:
        raise R01ContractError("human attestation requires at least two speakers")
    speaker_ids: set[str] = set()
    for index, raw_receipt in enumerate(receipts):
        receipt = _mapping(raw_receipt, f"speaker receipt {index}")
        _expect_keys(
            receipt,
            SPEAKER_RECEIPT_KEYS,
            f"speaker receipt {index}",
        )
        speaker_id = _sha256(
            receipt.get("speaker_id_sha256"), f"speaker receipt {index} ID"
        )
        if speaker_id in speaker_ids:
            raise R01ContractError("speaker receipt IDs must be unique")
        speaker_ids.add(speaker_id)
        _sha256(
            receipt.get("consent_receipt_sha256"),
            f"speaker receipt {index} consent SHA",
        )
        _sha256(
            receipt.get("recording_license_grant_sha256"),
            f"speaker receipt {index} license SHA",
        )
        _expect(receipt.get("native_korean_attested"), True, "native Korean status")
        _expect(receipt.get("consent_active"), True, "active consent")
        _expect(receipt.get("withdrawn"), False, "withdrawal status")
        _expect(receipt.get("expired"), False, "expiry status")
        _string(receipt.get("valid_from"), f"speaker receipt {index}.valid_from")
        _string(receipt.get("valid_until"), f"speaker receipt {index}.valid_until")
        _expect(
            tuple(
                _list(
                    receipt.get("permitted_sentence_ids"),
                    f"speaker receipt {index}.permitted_sentence_ids",
                )
            ),
            SENTENCE_IDS,
            f"speaker receipt {index} sentence permission",
        )
    receipt_contract = _mapping(payload.get("receipt_contract"), "receipt contract")
    _expect_keys(receipt_contract, RECEIPT_CONTRACT_KEYS, "receipt contract")
    _expect(
        receipt_contract.get("minimum_distinct_speakers"),
        2,
        "receipt contract minimum speakers",
    )
    _expect(
        receipt_contract.get("speaker_id_format"),
        "namespaced_lowercase_sha256",
        "receipt speaker ID format",
    )
    _expect(
        receipt_contract.get("identity_mapping_zone"),
        "R1_separate_access_controlled",
        "identity mapping zone",
    )
    _expect(
        receipt_contract.get("raw_capture_zone"),
        "R0_restricted",
        "raw capture zone",
    )
    _expect(
        receipt_contract.get("pseudonymized_candidate_audio_zone"),
        "D1_restricted_experiment",
        "candidate audio zone",
    )
    for field in (
        "each_receipt_requires_consent_receipt_sha256",
        "each_receipt_requires_recording_license_grant_sha256",
        "withdrawn_or_expired_receipts_prohibited",
    ):
        _expect(receipt_contract.get(field), True, f"receipt contract.{field}")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "human completion claim",
    )


def validate_cosyvoice_attestation(payload: Mapping[str, Any]) -> None:
    """Validate a future signed model/license/runtime/GPU attestation."""

    _require_no_tbd(payload, "CosyVoice attestation")
    _expect_keys(payload, COSYVOICE_ATTESTATION_KEYS, "CosyVoice attestation")
    _expect(
        payload.get("schema_version"),
        "voice_coach.r01_cosyvoice_model_attestation.v1",
        "CosyVoice attestation schema",
    )
    _expect(payload.get("status"), "approved_for_r01_inference", "CosyVoice status")
    _string(payload.get("attestation_id"), "CosyVoice attestation ID")
    _string(payload.get("signed_at_utc"), "CosyVoice signed_at_utc")
    _string(payload.get("signed_by_role"), "CosyVoice signed_by_role")
    _expect(
        dict(_mapping(payload.get("ledger_observation_only"), "ledger observation")),
        LEDGER_COSYVOICE,
        "ledger observation",
    )
    authorization = _mapping(
        payload.get("artifact_authorization"), "artifact authorization"
    )
    _expect_keys(
        authorization,
        ARTIFACT_AUTHORIZATION_KEYS,
        "artifact authorization",
    )
    _expect(
        authorization.get("decision"),
        "approved_for_r01_research_inference",
        "artifact authorization decision",
    )
    _expect(
        authorization.get("exact_repository_url"),
        LEDGER_COSYVOICE["canonical_repository"],
        "repository URL",
    )
    _expect(
        authorization.get("exact_repository_revision"),
        LEDGER_COSYVOICE["repository_revision"],
        "repository revision",
    )
    _expect(
        authorization.get("code_license_spdx"),
        LEDGER_COSYVOICE["repository_license_spdx"],
        "code license SPDX",
    )
    _expect(
        authorization.get("exact_model_provider"),
        LEDGER_COSYVOICE["model_provider"],
        "model provider",
    )
    _expect(
        authorization.get("exact_model_id"),
        LEDGER_COSYVOICE["model_id"],
        "model ID",
    )
    _expect(
        authorization.get("exact_model_revision"),
        LEDGER_COSYVOICE["model_revision"],
        "model revision",
    )
    for field in (
        "code_license_evidence_sha256",
        "weight_manifest_sha256",
        "weight_license_evidence_sha256",
    ):
        _sha256(authorization.get(field), f"authorization.{field}")
    _string(authorization.get("weight_license_spdx"), "weight license SPDX")
    _expect(
        authorization.get("research_inference_use_allowed"),
        True,
        "research inference use",
    )
    _boolean(authorization.get("redistribution_allowed"), "redistribution allowed")
    _boolean(authorization.get("commercial_use_allowed"), "commercial use allowed")
    for field in ("grpo_allowed", "fine_tuning_allowed", "voice_cloning_allowed"):
        _expect(authorization.get(field), False, f"authorization.{field}")
    runtime = _mapping(payload.get("runtime_environment"), "runtime environment")
    _expect_keys(runtime, RUNTIME_ENVIRONMENT_KEYS, "runtime environment")
    for field in (
        "environment_lock_sha256",
        "cosyvoice_inference_entrypoint_sha256",
        "local_weight_bundle_manifest_sha256",
    ):
        _sha256(runtime.get(field), f"runtime.{field}")
    for field in (
        "container_image_digest",
        "python_version",
        "pytorch_version",
        "cuda_runtime_version",
    ):
        _string(runtime.get(field), f"runtime.{field}")
    _expect(runtime.get("network_download_allowed"), False, "network download")
    _expect(runtime.get("local_files_only"), True, "local files only")
    gpu = _mapping(payload.get("gpu_pin"), "gpu pin")
    _expect_keys(gpu, GPU_PIN_KEYS, "gpu pin")
    _string(gpu.get("machine_id"), "gpu_pin.machine_id")
    device_uuid = _string(gpu.get("device_uuid"), "gpu_pin.device_uuid")
    if re.fullmatch(r"GPU-[A-Za-z0-9-]{8,}", device_uuid) is None:
        raise R01ContractError("gpu_pin.device_uuid must be an exact GPU UUID")
    _string(gpu.get("device_model"), "gpu_pin.device_model")
    _integer(
        gpu.get("visible_device_ordinal"),
        "gpu_pin.visible_device_ordinal",
        minimum=0,
    )
    _integer(
        gpu.get("minimum_memory_bytes"),
        "gpu_pin.minimum_memory_bytes",
        minimum=12 * 1024**3,
    )
    _expect(
        gpu.get("concurrent_training_process_allowed"),
        False,
        "concurrent training",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "CosyVoice completion claim",
    )


def validate_external_attestations(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ExternalEvidence:
    external = _mapping(config.get("external_attestations"), "external_attestations")
    human_raw = _mapping(external.get("human"), "human attestation config")
    human_payload, human_snapshot = _read_attestation(
        workspace_root=workspace_root,
        raw=human_raw,
        source="human",
        audit=audit,
    )
    try:
        validate_human_attestation(
            human_payload,
            r00_card_sha256=R00_EXPECTED["card_sha256"],
        )
    except R01ContractError as exc:
        raise R01ContractError(
            str(exc), stage="human_attestation", audit=audit
        ) from exc

    cosy_raw = _mapping(external.get("cosyvoice"), "CosyVoice attestation config")
    cosy_payload, cosy_snapshot = _read_attestation(
        workspace_root=workspace_root,
        raw=cosy_raw,
        source="cosyvoice",
        audit=audit,
    )
    try:
        validate_cosyvoice_attestation(cosy_payload)
        cosy_config = _mapping(config.get("cosyvoice"), "cosyvoice")
        authorization = _mapping(
            cosy_payload.get("artifact_authorization"),
            "CosyVoice artifact authorization",
        )
        runtime = _mapping(
            cosy_payload.get("runtime_environment"),
            "CosyVoice runtime environment",
        )
        gpu = _mapping(cosy_payload.get("gpu_pin"), "CosyVoice GPU pin")
        expected_bindings = {
            "authorization_decision": authorization.get("decision"),
            "exact_weight_license_decision": authorization.get("weight_license_spdx"),
            "exact_runtime_environment_lock_sha256": runtime.get(
                "environment_lock_sha256"
            ),
            "exact_local_weight_bundle_manifest_sha256": runtime.get(
                "local_weight_bundle_manifest_sha256"
            ),
            "exact_gpu_machine_id": gpu.get("machine_id"),
            "exact_gpu_device_uuid": gpu.get("device_uuid"),
            "exact_gpu_device_model": gpu.get("device_model"),
            "exact_gpu_visible_device_ordinal": gpu.get("visible_device_ordinal"),
        }
        for field, expected in expected_bindings.items():
            _expect(cosy_config.get(field), expected, f"cosyvoice.{field}")
    except R01ContractError as exc:
        raise R01ContractError(
            str(exc), stage="cosyvoice_attestation", audit=audit
        ) from exc
    return ExternalEvidence(
        human_snapshot=human_snapshot,
        cosyvoice_snapshot=cosy_snapshot,
    )


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Refuse symlinks and any existing R01 output target.

    This helper is never reached by the canonical preflight because external
    attestations are absent.  It exists for the later, separate execution
    package and for bounded no-overwrite tests; it does not create anything.
    """

    for field in (
        "candidate_manifest_path",
        "candidate_audio_root",
        "failure_report_path",
    ):
        relative = _relative_string(outputs.get(field), f"outputs.{field}")
        target = _reject_symlink_chain(
            workspace_root,
            relative,
            f"outputs.{field}",
            allow_missing_leaf=True,
        )
        if target.exists() or target.is_symlink():
            raise R01ContractError(f"no-overwrite target already exists: {relative}")


def _uuid4_candidate_id(value: object, name: str) -> str:
    text = _string(value, name)
    match = CANDIDATE_ID_RE.fullmatch(text)
    if match is None:
        raise R01ContractError(f"{name} must be an opaque r01c_ UUIDv4")
    parsed = uuid.UUID(hex=match.group(1))
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise R01ContractError(f"{name} must contain RFC4122 UUIDv4 bits")
    return text


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_collect_keys(nested))
    return keys


def validate_candidate_manifest_payload(
    payload: Mapping[str, Any],
    *,
    r00_sentence_hashes: Mapping[str, str],
    human_attestation_sha256: str,
    cosyvoice_attestation_sha256: str,
) -> Mapping[str, Any]:
    """Deep-validate a future private D1 candidate manifest without audio access."""

    _expect_keys(payload, CANDIDATE_MANIFEST_KEYS, "candidate manifest")
    leaked = set(FORBIDDEN_MANIFEST_FIELDS) & _collect_keys(payload)
    if leaked:
        raise R01ContractError(
            f"candidate manifest contains forbidden fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.r01_candidate_manifest.v1",
        "candidate manifest schema",
    )
    manifest_id = _string(payload.get("manifest_id"), "manifest_id")
    if re.fullmatch(r"r01-private-candidates-[0-9a-f]{32}", manifest_id) is None:
        raise R01ContractError("manifest_id must be opaque")
    _expect(
        payload.get("status"),
        "r01_private_candidates_pending_r02",
        "manifest status",
    )
    _expect(
        payload.get("r00_card_sha256"),
        R00_EXPECTED["card_sha256"],
        "R00 card SHA",
    )
    _expect(
        payload.get("human_attestation_sha256"),
        human_attestation_sha256,
        "human attestation binding",
    )
    _expect(
        payload.get("cosyvoice_attestation_sha256"),
        cosyvoice_attestation_sha256,
        "CosyVoice attestation binding",
    )
    _expect(
        payload.get("privacy_zone"),
        "D1_pseudonymized_restricted_experiment",
        "manifest privacy zone",
    )
    _expect(payload.get("publication_allowed"), False, "manifest publication")
    _expect(payload.get("r02_hard_gate_passed"), False, "R02 gate")

    if set(r00_sentence_hashes) != set(SENTENCE_IDS):
        raise R01ContractError("R00 sentence hash map must contain exactly 20 IDs")
    for sentence_id, digest in r00_sentence_hashes.items():
        _sha256(digest, f"R00 sentence hash {sentence_id}")

    rows = _list(payload.get("candidate_rows"), "candidate_rows")
    if not 160 <= len(rows) <= 320:
        raise R01ContractError("candidate row count must be 160-320")
    candidate_ids: set[str] = set()
    audio_ids: set[str] = set()
    audio_hashes: set[str] = set()
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row, f"candidate row {index}")
        _expect_keys(row, CANDIDATE_ROW_KEYS, f"candidate row {index}")
        candidate_id = _uuid4_candidate_id(
            row.get("candidate_id"), f"candidate row {index}.candidate_id"
        )
        if candidate_id in candidate_ids:
            raise R01ContractError("candidate IDs must be globally unique")
        candidate_ids.add(candidate_id)
        sentence_id = _string(row.get("sentence_id"), "sentence_id")
        if sentence_id not in r00_sentence_hashes:
            raise R01ContractError(f"unknown R00 sentence ID: {sentence_id}")
        _expect(
            row.get("target_text_sha256"),
            r00_sentence_hashes[sentence_id],
            "target text SHA",
        )
        source = _string(row.get("source_kind"), "source_kind")
        if source not in SOURCE_KINDS:
            raise R01ContractError(f"unsupported source_kind: {source}")
        expected_attestation = (
            human_attestation_sha256
            if source == "human_recording"
            else cosyvoice_attestation_sha256
        )
        _expect(
            row.get("source_attestation_sha256"),
            expected_attestation,
            "source attestation SHA",
        )
        _sha256(row.get("speaker_id_sha256"), "speaker ID")
        audio_id = _sha256(row.get("audio_object_id_sha256"), "audio object ID")
        if audio_id in audio_ids:
            raise R01ContractError("audio object IDs must be globally unique")
        audio_ids.add(audio_id)
        audio_hash = _sha256(row.get("audio_sha256"), "audio SHA")
        if audio_hash in audio_hashes:
            raise R01ContractError("audio SHA-256 values must be globally unique")
        audio_hashes.add(audio_hash)
        _sha256(row.get("preservation_group_id_sha256"), "preservation group ID")
        _expect(
            row.get("privacy_zone"),
            "D1_pseudonymized_restricted_experiment",
            "row privacy zone",
        )
        _expect(
            row.get("consent_or_model_use_verified"),
            True,
            "row source authorization",
        )
        if row.get("speed_bucket") not in SPEED_BUCKETS:
            raise R01ContractError("invalid speed bucket")
        if row.get("pitch_bucket") not in PITCH_BUCKETS:
            raise R01ContractError("invalid pitch bucket")
        _expect(row.get("r02_hard_gate_status"), "not_run", "row R02 status")
        _expect(row.get("publication_allowed"), False, "row publication")
        grouped[sentence_id].append(row)

    _expect(set(grouped), set(SENTENCE_IDS), "candidate sentence coverage")
    for sentence_id in SENTENCE_IDS:
        sentence_rows = grouped[sentence_id]
        if not 8 <= len(sentence_rows) <= 16:
            raise R01ContractError(f"{sentence_id} must have 8-16 candidates")
        source_counts = Counter(str(row["source_kind"]) for row in sentence_rows)
        unknown = set(source_counts) - set(SOURCE_KINDS)
        if unknown:
            raise R01ContractError(
                f"{sentence_id} has unknown source kinds: {sorted(unknown)}"
            )
        # A human recording is no longer required. The architecture under study is a
        # closed loop between a TTS generator and an evaluator, so synthesised
        # references are the subject of the experiment rather than a stand-in for
        # something better; requiring a human candidate per sentence made the design
        # unbuildable without changing what it measures. Source labelling is
        # untouched: every row still declares its `source_kind`, and synthetic audio
        # still carries its learner-facing disclosure.
        if not source_counts:
            raise R01ContractError(f"{sentence_id} has no candidate source")
        for source in SOURCE_KINDS:
            speakers = {
                str(row["speaker_id_sha256"])
                for row in sentence_rows
                if row["source_kind"] == source
            }
            if len(speakers) < 2:
                raise R01ContractError(
                    f"{sentence_id}/{source} needs at least two speakers"
                )
        if {str(row["speed_bucket"]) for row in sentence_rows} != set(SPEED_BUCKETS):
            raise R01ContractError(f"{sentence_id} must preserve all speed buckets")
        if {str(row["pitch_bucket"]) for row in sentence_rows} != set(PITCH_BUCKETS):
            raise R01ContractError(f"{sentence_id} must preserve all pitch buckets")

    return {
        "status": "valid_private_manifest_pending_r02",
        "sentence_count": len(grouped),
        "candidate_count": len(rows),
        "source_counts": dict(
            sorted(Counter(str(row["source_kind"]) for row in rows).items())
        ),
        "publication_allowed": False,
        "r02_hard_gate_passed": False,
    }


def _verify_static_snapshots(
    *, workspace_root: Path, static: StaticValidation, audit: AccessAudit
) -> None:
    for snapshot in static.source_snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, "canonical source")
        except R01ContractError as exc:
            raise R01ContractError(
                str(exc), stage="source_toctou", audit=audit
            ) from exc


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run read-only gates in their required order."""

    audit = AccessAudit()
    static = validate_static_config(
        workspace_root=workspace_root,
        config_path=config_path,
        config=config,
        audit=audit,
    )
    r00 = deep_validate_r00(
        workspace_root=workspace_root,
        config=config,
        static=static,
        audit=audit,
    )
    _verify_static_snapshots(
        workspace_root=workspace_root,
        static=static,
        audit=audit,
    )
    _verify_snapshot(
        workspace_root,
        r00.report_snapshot,
        "R00 immutable report before external gate",
    )

    # The canonical config stops here: human SHA is TBD.  No attestation path,
    # audio/data/model/environment/GPU/output path is touched.
    evidence = validate_external_attestations(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    _verify_snapshot(
        workspace_root, evidence.human_snapshot, "human attestation before readiness"
    )
    _verify_snapshot(
        workspace_root,
        evidence.cosyvoice_snapshot,
        "CosyVoice attestation before readiness",
    )

    # A later config version with signed attestations still cannot execute in
    # this static package.  It can only prove that output targets are fresh.
    audit.output_path_resolution_count += 3
    ensure_output_targets_absent(
        workspace_root=workspace_root,
        outputs=_mapping(config.get("outputs"), "outputs"),
    )
    raise R01ContractError(
        "external attestations validated; a separate recording/inference execution "
        "package is required",
        stage="separate_execution_package",
        audit=audit,
    )


def config_only_result(
    static: StaticValidation,
    audit: AccessAudit | None = None,
) -> Mapping[str, Any]:
    access = (
        audit
        if audit is not None
        else AccessAudit(source_pin_read_count=len(static.source_snapshots))
    )
    return {
        "status": "valid_config_only",
        "implementation_status": STATIC_STATUS,
        "config_sha256": static.config_sha256,
        "policy_sha256": static.policy_sha256,
        "source_pin_count": len(static.source_snapshots),
        **access.as_dict(),
        "human_recording_implemented": False,
        "tts_inference_implemented": False,
        "candidate_manifest_created": False,
        "audio_created": False,
        "r01_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: R01ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "human_recording_implemented": False,
        "tts_inference_implemented": False,
        "candidate_manifest_created": False,
        "audio_created": False,
        "r01_completion_claim_allowed": False,
        "publication_allowed": False,
    }
