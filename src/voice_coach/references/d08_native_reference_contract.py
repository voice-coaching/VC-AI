"""Fail-closed D08 native multi-speaker reference static-readiness contract.

This module is deliberately read-only.  It validates the D08 policy, future
attestations, restricted metadata, pseudonymized QC, and aggregate completion
documents, but it never recruits a speaker, collects consent, accesses a
microphone, reads or writes audio, runs ASR/a model, queries a GPU, uses the
network, computes QC from audio, publishes data, or writes a completion
artifact.

The canonical full preflight is ordered as follows:

1. validate C1 source pins, strict schemas, and non-authorizing templates;
2. recompute the actually completed R00 sentence-card validation;
3. require H00 or equivalent institutional approval;
4. require active individual consent and recording-rights attestations;
5. require a frozen QC preregistration for all unsupported numeric choices;
6. stop before any R0/R1/D1 path, microphone, audio, model, GPU, or output.

The canonical H00 SHA is ``TBD_EXTERNAL_ATTESTATION``.  The preflight therefore
stops after R00, before even resolving or probing the H00 path.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
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

import yaml  # type: ignore[import-untyped]


CONFIG_SCHEMA_VERSION = 1
STATIC_STATUS = "production_static_preflight_only"
TBD_EXTERNAL = "TBD_EXTERNAL_ATTESTATION"
TBD_PREREGISTRATION = "TBD_PREREGISTRATION"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
METADATA_ID_RE = re.compile(r"^d08m_[0-9a-f]{32}$")
RECORDING_ID_RE = re.compile(r"^d08r_[0-9a-f]{32}$")
QC_REPORT_ID_RE = re.compile(r"^d08q_[0-9a-f]{32}$")
COMPLETION_ID_RE = re.compile(r"^d08c_[0-9a-f]{32}$")
SENTENCE_IDS = tuple(f"R00-{index:02d}" for index in range(1, 21))
R00_CARD_SHA256 = "08dc149ccf10006a587e6216dda0471c19b5c55eab6b320b4348587527d82c4d"
CLIPPING_RATIO_EXCLUSIVE_MAXIMUM = 0.001
PLANNING_TARGET_SPEAKER_COUNT = 4

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "canonical_sources",
    "predecessor_r00",
    "external_prerequisites",
    "recording_design",
    "quality_control",
    "privacy_license_boundaries",
    "execution_order",
    "outputs",
}

SOURCE_PATHS = (
    "artifacts/reference/r00/r00_sentence_card_v1.validation.json",
    "configs/reference/d08_completion_v1.schema.json",
    "configs/reference/d08_h00_institutional_approval_v1.template.json",
    "configs/reference/d08_individual_consent_attestation_v1.template.json",
    "configs/reference/d08_qc_preregistration_v1.template.json",
    "configs/reference/d08_qc_report_v1.schema.json",
    "configs/reference/d08_recording_metadata_v1.schema.json",
    "configs/reference/d08_recording_rights_attestation_v1.template.json",
    "configs/reference/r00_sentence_card_v1.schema.json",
    "data/reference/r00/CC0_NOTICE.txt",
    "data/reference/r00/r00_sentence_card_v1.json",
    "docs/constraints/DATA_PRIVACY_BOUNDARY_KO.md",
    "docs/constraints/R00_FIXED_SENTENCE_CARD_DATA_CARD_KO.md",
    "docs/constraints/R01_CANDIDATE_DATA_MODEL_LICENSE_CARD_KO.md",
    "docs/constraints/R01_CANDIDATE_GENERATION_READINESS_KO.md",
    "scripts/prepare_d08_native_multispeaker_reference.py",
    "scripts/validate_r00_sentence_card.py",
    "src/voice_coach/references/d08_native_reference_contract.py",
)

EXECUTION_ORDER = (
    "validate_d08_c1_config_sources_schemas_and_templates",
    "deep_validate_actual_r00_completion",
    "validate_h00_or_equivalent_institutional_approval",
    "validate_active_individual_consent_attestation",
    "validate_recording_rights_source_license_attestation",
    "validate_frozen_qc_preregistration_without_tbd",
    "cross_validate_speaker_floor_protocol_and_attestation_bindings",
    "stop_before_r0_r1_d1_audio_microphone_model_network_gpu_or_output",
)

RUNTIME_FIELDS = (
    "h00_path_resolution_count",
    "h00_read_attempt_count",
    "h00_read_count",
    "individual_consent_path_resolution_count",
    "individual_consent_read_attempt_count",
    "individual_consent_read_count",
    "recording_rights_path_resolution_count",
    "recording_rights_read_attempt_count",
    "recording_rights_read_count",
    "qc_preregistration_path_resolution_count",
    "qc_preregistration_read_attempt_count",
    "qc_preregistration_read_count",
    "recruitment_count",
    "consent_collection_count",
    "recording_data_path_resolution_count",
    "metadata_manifest_read_count",
    "metadata_manifest_write_count",
    "microphone_access_count",
    "audio_read_count",
    "audio_write_count",
    "recording_session_count",
    "recording_take_count",
    "model_access_count",
    "network_access_count",
    "gpu_access_count",
    "qc_report_read_count",
    "qc_report_write_count",
    "qc_metric_computation_count",
    "qc_human_listening_count",
    "qc_adjudication_count",
    "completion_read_count",
    "completion_write_count",
    "publication_count",
    "commit_count",
    "output_path_resolution_count",
    "output_count",
)

METADATA_KEYS = {
    "schema_version",
    "manifest_id",
    "status",
    "r00_card_sha256",
    "h00_approval_sha256",
    "individual_consent_attestation_sha256",
    "recording_rights_attestation_sha256",
    "qc_preregistration_sha256",
    "privacy_zone",
    "publication_allowed",
    "contains_direct_identifiers",
    "contains_free_text",
    "rows",
}

METADATA_ROW_KEYS = {
    "recording_id",
    "speaker_id_sha256",
    "sentence_id",
    "target_text_sha256",
    "speaking_style",
    "take_index",
    "session_id_sha256",
    "equipment_profile_id_sha256",
    "microphone_profile_id_sha256",
    "room_profile_id_sha256",
    "r0_audio_locator_token_sha256",
    "audio_sha256",
    "sample_rate_hz",
    "bit_depth",
    "channel_count",
    "container_format",
    "codec",
    "mouth_to_microphone_distance_cm",
    "source_kind",
    "consent_receipt_sha256",
    "recording_license_grant_sha256",
    "native_korean_attested",
    "raw_audio_zone",
    "locator_manifest_zone",
    "publication_allowed",
}

QC_REPORT_KEYS = {
    "schema_version",
    "report_id",
    "status",
    "metadata_manifest_sha256",
    "qc_preregistration_sha256",
    "privacy_zone",
    "contains_r0_locator",
    "contains_direct_identifiers",
    "contains_free_text",
    "publication_allowed",
    "rows",
}

QC_ROW_KEYS = {
    "recording_id",
    "decoder_error_count",
    "clipping_ratio",
    "dc_offset_metric_id",
    "dc_offset_value",
    "dc_offset_pass",
    "content_match_metric_id",
    "content_match_value",
    "content_match_pass",
    "asr_used_for_triage_only",
    "asr_used_as_final_qc",
    "human_reviewer_pool_size",
    "human_listener_confirmation_count",
    "human_content_and_pronunciation_confirmed",
    "noise_metric_id",
    "noise_value",
    "noise_pass",
    "reverb_metric_id",
    "reverb_value",
    "reverb_pass",
    "interruption_metric_id",
    "interruption_value",
    "interruption_pass",
    "adjudication_required",
    "adjudication_status",
    "adjudication_receipt_sha256",
    "final_qc_pass",
    "exclusion_reason_code",
}

COMPLETION_KEYS = {
    "schema_version",
    "completion_id",
    "status",
    "config_sha256",
    "r00_card_sha256",
    "h00_approval_sha256",
    "individual_consent_attestation_sha256",
    "recording_rights_attestation_sha256",
    "qc_preregistration_sha256",
    "metadata_manifest_sha256",
    "qc_report_sha256",
    "privacy_zone",
    "contains_row_level_data",
    "contains_direct_or_pseudonymous_ids",
    "contains_audio_or_locator_hashes",
    "contains_free_text",
    "sentence_count",
    "binding_minimum_distinct_speakers_per_sentence",
    "planning_target_minimum_distinct_speakers_per_sentence",
    "planning_target_met_all_sentences",
    "same_equipment_and_space_verified",
    "format_and_distance_verified",
    "source_license_honesty_verified",
    "qc_adjudication_contract_verified",
    "sentences",
    "totals",
    "actual_recruitment_completed",
    "actual_consent_completed",
    "actual_recording_completed",
    "actual_qc_completed",
    "completion_claim_allowed",
    "publication_allowed",
    "implementation_git_commit",
}

COMPLETION_SENTENCE_KEYS = {
    "sentence_id",
    "distinct_speaker_count",
    "accepted_natural_take_count",
    "accepted_clear_educational_take_count",
    "accepted_take_count",
    "speaker_floor_pass",
    "planning_target_met",
    "exact_take_pattern_pass",
    "all_qc_pass",
}

COMPLETION_TOTAL_KEYS = {
    "distinct_speaker_sentence_pairs",
    "accepted_natural_take_count",
    "accepted_clear_educational_take_count",
    "accepted_take_count",
    "failed_take_count",
    "decoder_error_count",
    "clipping_failure_count",
    "dc_offset_failure_count",
    "content_mismatch_failure_count",
    "noise_failure_count",
    "reverb_failure_count",
    "interruption_failure_count",
    "human_review_failure_count",
    "adjudication_failure_count",
}

H00_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "signed_at_utc",
    "signed_by_role",
    "determination",
    "approved_scope",
    "privacy_governance",
    "d08_collection_allowed",
    "completion_claim_allowed",
}

CONSENT_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "signed_at_utc",
    "signed_by_role",
    "h00_approval_sha256",
    "consent_protocol",
    "speaker_receipts",
    "receipt_contract",
    "completion_claim_allowed",
}

RIGHTS_KEYS = {
    "schema_version",
    "attestation_id",
    "status",
    "signed_at_utc",
    "signed_by_role",
    "h00_approval_sha256",
    "individual_consent_attestation_sha256",
    "source_classification",
    "recording_protocol",
    "speaker_rights_receipts",
    "rights_contract",
    "completion_claim_allowed",
}

PREREGISTRATION_KEYS = {
    "schema_version",
    "preregistration_id",
    "status",
    "registered_at_utc",
    "registered_by_role",
    "frozen_before_recruitment_or_audio_access",
    "r00_card_sha256",
    "speaker_contract",
    "fixed_qc_gates",
    "measurement_contracts",
    "adjudication_contract",
    "approval",
    "completion_claim_allowed",
}

FORBIDDEN_IDENTITY_FIELDS = {
    "name",
    "speaker_name",
    "legal_name",
    "email",
    "phone",
    "address",
    "date_of_birth",
    "raw_speaker_id",
    "national_id",
    "free_text",
    "free_text_note",
    "comment",
}

FORBIDDEN_A1_FIELDS = {
    "recording_id",
    "recording_ids",
    "speaker_id",
    "speaker_ids",
    "speaker_id_sha256",
    "session_id_sha256",
    "audio_sha256",
    "audio_path",
    "recording_path",
    "source_path",
    "r0_audio_locator_token_sha256",
    "consent_receipt_sha256",
    "recording_license_grant_sha256",
    "target_text",
    "transcript",
    "rows",
}


class D08ContractError(ValueError):
    """A fail-closed contract violation with an execution-stage audit."""

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
    """Reject duplicate YAML mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> Mapping[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise D08ContractError("YAML mapping keys must be strings")
        if key in result:
            raise D08ContractError(f"YAML contains duplicate key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class AccessAudit:
    source_pin_read_count: int = 0
    source_pin_verify_count: int = 0
    r00_validator_load_count: int = 0
    r00_deep_validation_count: int = 0
    r00_public_or_aggregate_evidence_read_count: int = 0
    r00_raw_audio_read_count: int = 0
    r00_row_level_read_count: int = 0
    h00_path_resolution_count: int = 0
    h00_read_attempt_count: int = 0
    h00_read_count: int = 0
    individual_consent_path_resolution_count: int = 0
    individual_consent_read_attempt_count: int = 0
    individual_consent_read_count: int = 0
    recording_rights_path_resolution_count: int = 0
    recording_rights_read_attempt_count: int = 0
    recording_rights_read_count: int = 0
    qc_preregistration_path_resolution_count: int = 0
    qc_preregistration_read_attempt_count: int = 0
    qc_preregistration_read_count: int = 0
    recruitment_count: int = 0
    consent_collection_count: int = 0
    recording_data_path_resolution_count: int = 0
    metadata_manifest_read_count: int = 0
    metadata_manifest_write_count: int = 0
    microphone_access_count: int = 0
    audio_read_count: int = 0
    audio_write_count: int = 0
    recording_session_count: int = 0
    recording_take_count: int = 0
    model_access_count: int = 0
    network_access_count: int = 0
    gpu_access_count: int = 0
    qc_report_read_count: int = 0
    qc_report_write_count: int = 0
    qc_metric_computation_count: int = 0
    qc_human_listening_count: int = 0
    qc_adjudication_count: int = 0
    completion_read_count: int = 0
    completion_write_count: int = 0
    publication_count: int = 0
    commit_count: int = 0
    output_path_resolution_count: int = 0
    output_count: int = 0

    def as_dict(self) -> dict[str, int]:
        return {key: int(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class StaticValidation:
    config_sha256: str
    policy_sha256: str
    source_snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class R00Evidence:
    report_sha256: str
    report_snapshot: FileSnapshot
    sentence_hashes: Mapping[str, str]


@dataclass(frozen=True)
class ExternalEvidence:
    h00_snapshot: FileSnapshot
    consent_snapshot: FileSnapshot
    rights_snapshot: FileSnapshot
    preregistration_snapshot: FileSnapshot
    h00: Mapping[str, Any]
    consent: Mapping[str, Any]
    rights: Mapping[str, Any]
    preregistration: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightResult:
    status: str
    config_sha256: str
    r00_report_sha256: str
    audit: Mapping[str, int]


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise D08ContractError(f"{name} must be a mapping")
    return value


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise D08ContractError(f"{name} must be a list")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise D08ContractError(f"{name} must be a non-empty string")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise D08ContractError(f"{name} must be boolean")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise D08ContractError(f"{name} must be an integer >= {minimum}")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise D08ContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise D08ContractError(f"{name} must be finite")
    return result


def _sha256(value: object, name: str) -> str:
    result = _string(value, name)
    if SHA256_RE.fullmatch(result) is None or result == "0" * 64:
        raise D08ContractError(f"{name} must be a nonzero lowercase SHA-256")
    return result


def _expect(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise D08ContractError(f"{name} must equal {expected!r}, got {actual!r}")


def _expect_keys(
    value: Mapping[str, Any],
    expected: set[str],
    name: str,
) -> None:
    observed = set(value)
    if observed != expected:
        raise D08ContractError(
            f"{name} keys differ; missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _contains_tbd(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("TBD_")
    if isinstance(value, Mapping):
        return any(_contains_tbd(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_tbd(item) for item in value)
    return False


def _reject_nonfinite_tree(value: Any, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise D08ContractError(f"{name} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite_tree(item, f"{name}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_nonfinite_tree(item, f"{name}[{index}]")


def _collect_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            result.add(str(key))
            result.update(_collect_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            result.update(_collect_keys(item))
    return result


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise D08ContractError(f"JSON contains duplicate key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise D08ContractError(f"JSON contains forbidden constant: {value}")


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    _reject_nonfinite_tree(value, "canonical JSON")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _parse_json(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise D08ContractError(f"{name} must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise D08ContractError(f"{name} must be strict JSON") from exc
    result = _mapping(value, name)
    _reject_nonfinite_tree(result, name)
    return result


def _relative_string(value: object, name: str) -> str:
    result = _string(value, name)
    pure = PurePosixPath(result)
    if (
        pure.is_absolute()
        or result != pure.as_posix()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\\" in result
    ):
        raise D08ContractError(f"{name} must be a normalized workspace-relative path")
    return result


def _reject_symlink_chain(
    root: Path,
    relative: str,
    name: str,
    *,
    allow_missing_leaf: bool = False,
) -> Path:
    current = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        current = current / part
        is_leaf = index == len(parts) - 1
        try:
            metadata = current.lstat()
        except FileNotFoundError as exc:
            if allow_missing_leaf:
                return root.joinpath(*parts)
            raise D08ContractError(f"{name} does not exist: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise D08ContractError(f"{name} cannot traverse a symlink")
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise D08ContractError(f"{name} parent must be a directory")
    return current


def _read_file(
    root: Path,
    relative: str,
    name: str,
    *,
    before_second_stat_hook: Callable[[], None] | None = None,
) -> tuple[bytes, FileSnapshot]:
    normalized = _relative_string(relative, f"{name} path")
    path = _reject_symlink_chain(root, normalized, name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise D08ContractError(f"{name} is not readable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise D08ContractError(f"{name} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        if before_second_stat_hook is not None:
            before_second_stat_hook()
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
        raise D08ContractError(f"{name} changed while being read")
    current = path.lstat()
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )
    if current_identity != after_identity:
        raise D08ContractError(f"{name} changed while being read")
    data = b"".join(chunks)
    return data, FileSnapshot(
        path=normalized,
        sha256=hashlib.sha256(data).hexdigest(),
        device=after.st_dev,
        inode=after.st_ino,
        size=after.st_size,
        mtime_ns=after.st_mtime_ns,
    )


def _verify_snapshot(root: Path, expected: FileSnapshot, name: str) -> None:
    _, observed = _read_file(root, expected.path, name)
    if observed != expected:
        raise D08ContractError(f"{name} changed after validation")


def load_config(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise D08ContractError("D08 config may not be a symlink")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise D08ContractError("D08 config is not readable") from exc
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_UniqueKeyLoader)
    except UnicodeDecodeError as exc:
        raise D08ContractError("D08 config must be UTF-8") from exc
    except yaml.YAMLError as exc:
        raise D08ContractError("D08 config must be unique-key YAML") from exc
    result = _mapping(value, "D08 config")
    _reject_nonfinite_tree(result, "D08 config")
    return result


def _schema_properties(
    schema: Mapping[str, Any],
    *,
    expected_id: str,
    expected_keys: set[str],
    name: str,
) -> Mapping[str, Any]:
    _expect(schema.get("$id"), expected_id, f"{name} $id")
    _expect(schema.get("type"), "object", f"{name} root type")
    _expect(schema.get("additionalProperties"), False, f"{name} strictness")
    _expect(
        set(_list(schema.get("required"), f"{name} required")),
        expected_keys,
        f"{name} required fields",
    )
    properties = _mapping(schema.get("properties"), f"{name} properties")
    _expect(set(properties), expected_keys, f"{name} property fields")
    return properties


def _validate_metadata_schema(schema: Mapping[str, Any]) -> None:
    properties = _schema_properties(
        schema,
        expected_id="voice_coach.d08_recording_metadata_manifest.v1",
        expected_keys=METADATA_KEYS,
        name="D08 metadata schema",
    )
    for field, row_expected in (
        ("privacy_zone", "R1_access_controlled_locator_metadata"),
        ("publication_allowed", False),
        ("contains_direct_identifiers", False),
        ("contains_free_text", False),
    ):
        _expect(
            _mapping(properties.get(field), f"metadata schema {field}").get("const"),
            row_expected,
            f"metadata schema {field}",
        )
    row = _mapping(
        _mapping(schema.get("$defs"), "metadata definitions").get("recordingRow"),
        "metadata row schema",
    )
    _expect(row.get("additionalProperties"), False, "metadata row strictness")
    _expect(
        set(_list(row.get("required"), "metadata row required")),
        METADATA_ROW_KEYS,
        "metadata row required fields",
    )
    row_properties = _mapping(row.get("properties"), "metadata row properties")
    _expect(set(row_properties), METADATA_ROW_KEYS, "metadata row fields")
    for field, row_field_expected in (
        ("sample_rate_hz", 48000),
        ("bit_depth", 24),
        ("channel_count", 1),
        ("container_format", "WAV"),
        ("codec", "PCM"),
        ("source_kind", "human_recording"),
        ("native_korean_attested", True),
        ("raw_audio_zone", "R0_restricted_raw_audio"),
        ("locator_manifest_zone", "R1_access_controlled_locator_metadata"),
        ("publication_allowed", False),
    ):
        _expect(
            _mapping(row_properties.get(field), f"metadata row {field}").get("const"),
            row_field_expected,
            f"metadata row {field}",
        )
    distance = _mapping(
        row_properties.get("mouth_to_microphone_distance_cm"),
        "metadata distance",
    )
    _expect(distance.get("minimum"), 15, "metadata minimum distance")
    _expect(distance.get("maximum"), 20, "metadata maximum distance")
    leaked = FORBIDDEN_IDENTITY_FIELDS & _collect_keys(schema)
    if leaked:
        raise D08ContractError(
            f"metadata schema exposes forbidden identity fields: {sorted(leaked)}"
        )


def _validate_qc_schema(schema: Mapping[str, Any]) -> None:
    properties = _schema_properties(
        schema,
        expected_id="voice_coach.d08_qc_report.v1",
        expected_keys=QC_REPORT_KEYS,
        name="D08 QC schema",
    )
    for field, qc_expected in (
        ("privacy_zone", "D1_pseudonymized_restricted_qc"),
        ("contains_r0_locator", False),
        ("contains_direct_identifiers", False),
        ("contains_free_text", False),
        ("publication_allowed", False),
    ):
        _expect(
            _mapping(properties.get(field), f"QC schema {field}").get("const"),
            qc_expected,
            f"QC schema {field}",
        )
    row = _mapping(
        _mapping(schema.get("$defs"), "QC definitions").get("qcRow"),
        "QC row schema",
    )
    _expect(row.get("additionalProperties"), False, "QC row strictness")
    _expect(
        set(_list(row.get("required"), "QC row required")),
        QC_ROW_KEYS,
        "QC row required fields",
    )
    row_properties = _mapping(row.get("properties"), "QC row properties")
    _expect(set(row_properties), QC_ROW_KEYS, "QC row fields")
    _expect(
        _mapping(row_properties.get("decoder_error_count"), "decoder gate").get(
            "const"
        ),
        0,
        "decoder error gate",
    )
    _expect(
        _mapping(row_properties.get("clipping_ratio"), "clipping gate").get(
            "exclusiveMaximum"
        ),
        CLIPPING_RATIO_EXCLUSIVE_MAXIMUM,
        "clipping gate",
    )
    _expect(
        _mapping(
            row_properties.get("asr_used_for_triage_only"),
            "ASR triage schema",
        ).get("const"),
        True,
        "ASR triage-only schema",
    )
    _expect(
        _mapping(
            row_properties.get("asr_used_as_final_qc"),
            "ASR final schema",
        ).get("const"),
        False,
        "ASR final-QC schema",
    )
    serialized = json.dumps(schema, sort_keys=True).casefold()
    for forbidden in (
        '"audio_path"',
        '"recording_path"',
        '"speaker_name"',
        '"free_text_note"',
    ):
        if forbidden in serialized:
            raise D08ContractError(f"QC schema exposes forbidden field {forbidden}")


def _validate_completion_schema(schema: Mapping[str, Any]) -> None:
    properties = _schema_properties(
        schema,
        expected_id="voice_coach.d08_completion.v1",
        expected_keys=COMPLETION_KEYS,
        name="D08 completion schema",
    )
    for field, expected_value in (
        ("privacy_zone", "A1_aggregate_review_required"),
        ("contains_row_level_data", False),
        ("contains_direct_or_pseudonymous_ids", False),
        ("contains_audio_or_locator_hashes", False),
        ("contains_free_text", False),
        ("sentence_count", 20),
        ("planning_target_minimum_distinct_speakers_per_sentence", 4),
        ("same_equipment_and_space_verified", True),
        ("format_and_distance_verified", True),
        ("source_license_honesty_verified", True),
        ("qc_adjudication_contract_verified", True),
        ("publication_allowed", False),
    ):
        _expect(
            _mapping(properties.get(field), f"completion schema {field}").get("const"),
            expected_value,
            f"completion schema {field}",
        )
    definitions = _mapping(schema.get("$defs"), "completion definitions")
    sentence = _mapping(
        definitions.get("sentenceSummary"), "completion sentence schema"
    )
    _expect(
        set(_list(sentence.get("required"), "completion sentence required")),
        COMPLETION_SENTENCE_KEYS,
        "completion sentence required fields",
    )
    _expect(
        set(_mapping(sentence.get("properties"), "completion sentence properties")),
        COMPLETION_SENTENCE_KEYS,
        "completion sentence fields",
    )
    totals = _mapping(definitions.get("totals"), "completion totals schema")
    _expect(
        set(_list(totals.get("required"), "completion totals required")),
        COMPLETION_TOTAL_KEYS,
        "completion totals required fields",
    )
    _expect(
        set(_mapping(totals.get("properties"), "completion totals properties")),
        COMPLETION_TOTAL_KEYS,
        "completion totals fields",
    )
    serialized = json.dumps(schema, sort_keys=True).casefold()
    for forbidden in (
        '"speaker_id_sha256"',
        '"recording_id"',
        '"audio_sha256"',
        '"audio_path"',
        '"rows"',
    ):
        if forbidden in serialized:
            raise D08ContractError(
                f"completion schema exposes forbidden A1 field {forbidden}"
            )


def _validate_h00_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, H00_KEYS, "H00 template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_h00_institutional_approval.v1",
        "H00 template schema",
    )
    _expect(payload.get("status"), TBD_EXTERNAL, "H00 template status")
    _expect(payload.get("d08_collection_allowed"), False, "H00 template collection")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "H00 template completion",
    )
    if not _contains_tbd(payload):
        raise D08ContractError("H00 template must remain non-authorizing")


def _validate_consent_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, CONSENT_KEYS, "consent template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_individual_consent_attestation.v1",
        "consent template schema",
    )
    _expect(payload.get("status"), TBD_EXTERNAL, "consent template status")
    _expect(payload.get("speaker_receipts"), [], "consent template receipts")
    contract = _mapping(payload.get("receipt_contract"), "consent receipt contract")
    _expect(
        contract.get("planning_target_minimum_distinct_speakers_per_sentence"),
        PLANNING_TARGET_SPEAKER_COUNT,
        "consent planning target",
    )
    _expect(
        contract.get("binding_completion_floor"),
        TBD_PREREGISTRATION,
        "consent binding floor",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "consent template completion",
    )


def _validate_rights_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, RIGHTS_KEYS, "recording-rights template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_recording_rights_attestation.v1",
        "recording-rights template schema",
    )
    _expect(payload.get("status"), TBD_EXTERNAL, "recording-rights template status")
    source = _mapping(payload.get("source_classification"), "source classification")
    _expect(source.get("public_corpus_audio_used"), False, "public corpus use")
    _expect(source.get("tts_audio_used"), False, "TTS audio use")
    _expect(
        source.get("corpus_or_tts_disguised_as_human"),
        False,
        "source disguise",
    )
    protocol = _mapping(payload.get("recording_protocol"), "recording protocol")
    for field, expected_value in (
        ("r00_card_sha256", R00_CARD_SHA256),
        ("natural_take_count_per_speaker_sentence", 2),
        ("clear_educational_take_count_per_speaker_sentence", 1),
        ("sample_rate_hz", 48000),
        ("bit_depth", 24),
        ("channel_count", 1),
        ("container_format", "WAV"),
        ("codec", "PCM"),
        ("same_equipment_profile_required", True),
        ("same_microphone_profile_required", True),
        ("same_room_profile_required", True),
        ("mouth_to_microphone_distance_min_cm", 15),
        ("mouth_to_microphone_distance_max_cm", 20),
    ):
        _expect(
            protocol.get(field),
            expected_value,
            f"recording protocol {field}",
        )
    _expect(
        payload.get("speaker_rights_receipts"),
        [],
        "recording-rights template receipts",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "recording-rights template completion",
    )


def _validate_preregistration_template(payload: Mapping[str, Any]) -> None:
    _expect_keys(payload, PREREGISTRATION_KEYS, "QC preregistration template")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_qc_preregistration.v1",
        "QC preregistration template schema",
    )
    _expect(
        payload.get("status"),
        TBD_PREREGISTRATION,
        "QC preregistration template status",
    )
    speaker = _mapping(payload.get("speaker_contract"), "speaker contract")
    _expect(
        speaker.get("planning_target_minimum_distinct_speakers_per_sentence"),
        PLANNING_TARGET_SPEAKER_COUNT,
        "speaker planning target",
    )
    _expect(
        speaker.get("binding_minimum_distinct_speakers_per_sentence"),
        TBD_PREREGISTRATION,
        "binding speaker floor",
    )
    fixed = _mapping(payload.get("fixed_qc_gates"), "fixed QC gates")
    for field, expected_value in (
        ("clipping_ratio_operator", "<"),
        ("clipping_ratio_threshold", CLIPPING_RATIO_EXCLUSIVE_MAXIMUM),
        ("decoder_error_count_required", 0),
        ("human_reviewer_pool_size", 2),
        ("minimum_human_listener_confirmations", 1),
        ("asr_role", "candidate_triage_only_not_final_qc"),
    ):
        _expect(fixed.get(field), expected_value, f"fixed QC gate {field}")
    measurements = _mapping(
        payload.get("measurement_contracts"), "measurement contracts"
    )
    _expect(
        set(measurements),
        {"dc_offset", "content_match", "noise", "reverb", "interruption"},
        "measurement contract categories",
    )
    for name, raw in measurements.items():
        section = _mapping(raw, f"{name} measurement")
        _expect(
            set(section),
            {"metric_id", "unit", "direction", "threshold"},
            f"{name} measurement keys",
        )
        if not _contains_tbd(section):
            raise D08ContractError(
                f"{name} template must not invent an exact threshold"
            )
    if not _contains_tbd(payload.get("adjudication_contract")):
        raise D08ContractError("adjudication template must remain unresolved")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "QC preregistration template completion",
    )


def _validate_config_values(config: Mapping[str, Any]) -> None:
    _expect_keys(config, TOP_LEVEL_KEYS, "D08 config")
    _expect(config.get("schema_version"), CONFIG_SCHEMA_VERSION, "config schema")
    experiment = _mapping(config.get("experiment"), "experiment")
    _expect(
        experiment.get("id"),
        "d08_native_multispeaker_reference_v1",
        "experiment ID",
    )
    _expect(
        experiment.get("stage"),
        "wp1_d08_native_multispeaker_reference",
        "experiment stage",
    )
    _expect(
        experiment.get("implementation_status"),
        STATIC_STATUS,
        "implementation status",
    )
    for field in (
        "actual_recruitment_allowed",
        "actual_consent_collection_allowed",
        "actual_recording_allowed",
        "actual_audio_read_allowed",
        "actual_audio_write_allowed",
        "actual_microphone_access_allowed",
        "actual_model_access_allowed",
        "actual_network_access_allowed",
        "actual_gpu_access_allowed",
        "actual_qc_execution_allowed",
        "actual_output_write_allowed",
        "publication_allowed",
        "completion_claim_allowed",
    ):
        _expect(experiment.get(field), False, f"experiment.{field}")

    predecessor = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    expected_r00 = {
        "deep_validation_required": True,
        "card_path": "data/reference/r00/r00_sentence_card_v1.json",
        "card_sha256": R00_CARD_SHA256,
        "schema_path": "configs/reference/r00_sentence_card_v1.schema.json",
        "schema_sha256": (
            "1f8bcf0325414e1923cda0def9a973ac2f7be7bd85cca4ee869743ef862a7b92"
        ),
        "notice_path": "data/reference/r00/CC0_NOTICE.txt",
        "notice_sha256": (
            "ffd86dc89cf0957dccc61f6ecb7af1c7ebbbdbff2bd87c951e54c40afc1262c9"
        ),
        "validator_path": "scripts/validate_r00_sentence_card.py",
        "validator_sha256": (
            "967517e74194936c91dad1eed4fcfb0c059b5480af32b8adfc9725fdb7d6e7d0"
        ),
        "validation_report_path": (
            "artifacts/reference/r00/r00_sentence_card_v1.validation.json"
        ),
        "validation_report_sha256": (
            "95b4fd861a3c21222ff13859cfbc9c196e3fd06493852bca823d8a63dc2816dd"
        ),
        "required_status": "valid",
        "required_sentence_count": 20,
        "required_r00_completion_claim_allowed": True,
        "required_r01_completion_claim_allowed": False,
        "tts_or_audio_created_must_be_false": True,
        "human_evaluation_completed_must_be_false": True,
    }
    _expect(dict(predecessor), expected_r00, "R00 predecessor contract")

    external = _mapping(config.get("external_prerequisites"), "external_prerequisites")
    _expect(external.get("external_placeholder"), TBD_EXTERNAL, "external marker")
    _expect(
        external.get("preregistration_placeholder"),
        TBD_PREREGISTRATION,
        "preregistration marker",
    )
    expected_external = {
        "h00_institutional_approval": (
            "artifacts/external_attestations/d08/h00_institutional_approval_v1.json",
            TBD_EXTERNAL,
            "voice_coach.d08_h00_institutional_approval.v1",
            "approved_for_d08_recording",
        ),
        "individual_consent": (
            "artifacts/external_attestations/d08/"
            "individual_consent_attestation_v1.json",
            TBD_EXTERNAL,
            "voice_coach.d08_individual_consent_attestation.v1",
            "approved_active_individual_consent",
        ),
        "recording_rights": (
            "artifacts/external_attestations/d08/recording_rights_attestation_v1.json",
            TBD_EXTERNAL,
            "voice_coach.d08_recording_rights_attestation.v1",
            "approved_active_recording_rights",
        ),
        "qc_preregistration": (
            "artifacts/preregistration/d08/d08_qc_preregistration_v1.json",
            TBD_PREREGISTRATION,
            "voice_coach.d08_qc_preregistration.v1",
            "registered_and_frozen",
        ),
    }
    for name, external_binding in expected_external.items():
        section = _mapping(external.get(name), f"external {name}")
        _expect_keys(
            section,
            {"path", "sha256", "required_schema_version", "required_status"},
            f"external {name}",
        )
        observed = (
            section.get("path"),
            section.get("sha256"),
            section.get("required_schema_version"),
            section.get("required_status"),
        )
        _expect(observed, external_binding, f"external {name} binding")

    design = _mapping(config.get("recording_design"), "recording_design")
    _expect(
        tuple(_list(design.get("sentence_ids"), "sentence IDs")),
        SENTENCE_IDS,
        "sentence IDs",
    )
    _expect(
        design.get("sentence_count"),
        20,
        "recording sentence count",
    )
    _expect(
        design.get("planning_target_minimum_distinct_speakers_per_sentence"),
        PLANNING_TARGET_SPEAKER_COUNT,
        "speaker planning target",
    )
    _expect(
        design.get("binding_completion_minimum_distinct_speakers_per_sentence"),
        TBD_PREREGISTRATION,
        "binding speaker floor",
    )
    take = _mapping(design.get("take_pattern"), "take pattern")
    _expect(
        dict(take),
        {
            "natural_speed_take_count_per_speaker_sentence": 2,
            "clear_educational_speed_take_count_per_speaker_sentence": 1,
            "exact_take_pattern_required": True,
        },
        "take pattern",
    )
    audio_format = _mapping(design.get("audio_format"), "audio format")
    _expect(
        dict(audio_format),
        {
            "sample_rate_hz": 48000,
            "bit_depth": 24,
            "channel_count": 1,
            "container_format": "WAV",
            "codec": "PCM",
        },
        "audio format",
    )
    setup = _mapping(design.get("recording_setup"), "recording setup")
    for field, expected_value in (
        ("same_equipment_profile_required", True),
        ("same_microphone_profile_required", True),
        ("same_room_profile_required", True),
        ("mouth_to_microphone_distance_min_cm", 15),
        ("mouth_to_microphone_distance_max_cm", 20),
        ("equipment_profile_id_sha256", TBD_EXTERNAL),
        ("microphone_profile_id_sha256", TBD_EXTERNAL),
        ("room_profile_id_sha256", TBD_EXTERNAL),
        ("session_metadata_required", True),
        ("take_metadata_required", True),
    ):
        _expect(setup.get(field), expected_value, f"recording setup {field}")

    qc = _mapping(config.get("quality_control"), "quality_control")
    fixed = _mapping(qc.get("fixed_gates"), "fixed QC gates")
    for field, qc_expected in (
        ("clipping_ratio_operator", "<"),
        ("clipping_ratio_threshold", CLIPPING_RATIO_EXCLUSIVE_MAXIMUM),
        ("decoder_error_count_required", 0),
        ("asr_role", "candidate_triage_only_not_final_qc"),
        ("human_reviewer_pool_size", 2),
        ("minimum_human_listener_confirmations", 1),
        ("final_content_and_pronunciation_human_confirmation_required", True),
    ):
        _expect(fixed.get(field), qc_expected, f"fixed QC {field}")
    unresolved = _mapping(
        qc.get("preregistered_before_audio_access"),
        "unresolved QC choices",
    )
    _expect(
        set(unresolved),
        {
            "binding_speaker_floor",
            "dc_offset_metric_unit_direction_threshold",
            "content_match_metric_direction_threshold",
            "noise_metric_unit_direction_threshold",
            "reverb_metric_unit_direction_threshold",
            "interruption_metric_unit_direction_threshold",
            "qc_adjudication_trigger_and_decision_rule",
        },
        "unresolved QC fields",
    )
    for field, value in unresolved.items():
        _expect(value, TBD_PREREGISTRATION, f"unresolved QC {field}")

    boundary = _mapping(
        config.get("privacy_license_boundaries"),
        "privacy_license_boundaries",
    )
    expected_boundary = {
        "raw_audio_zone": "R0_restricted_raw_audio",
        "locator_and_identity_mapping_zone": "R1_access_controlled_locator_metadata",
        "pseudonymized_qc_and_embedding_zone": "D1_restricted_experiment",
        "aggregate_completion_zone": "A1_aggregate_review_required",
        "schema_code_documentation_zone": "C1_public_code",
        "raw_audio_publication_allowed": False,
        "locator_manifest_publication_allowed": False,
        "d1_row_publication_allowed": False,
        "aggregate_requires_separate_release_review": True,
        "namespaced_sha256_speaker_ids_required": True,
        "direct_identity_in_metadata_or_qc_allowed": False,
        "free_text_in_metadata_qc_or_completion_allowed": False,
        "public_corpus_audio_may_be_disguised_as_human": False,
        "tts_audio_may_be_disguised_as_human": False,
        "explicit_human_tts_source_and_license_required": True,
        "checksum_implies_license_or_publication_permission": False,
    }
    _expect(dict(boundary), expected_boundary, "privacy/license boundary")
    _expect(
        tuple(_list(config.get("execution_order"), "execution order")),
        EXECUTION_ORDER,
        "execution order",
    )
    outputs = _mapping(config.get("outputs"), "outputs")
    expected_schema_paths = {
        "metadata_schema_path": (
            "configs/reference/d08_recording_metadata_v1.schema.json"
        ),
        "qc_schema_path": "configs/reference/d08_qc_report_v1.schema.json",
        "completion_schema_path": "configs/reference/d08_completion_v1.schema.json",
    }
    for field, expected_path in expected_schema_paths.items():
        _expect(outputs.get(field), expected_path, f"outputs.{field}")
    for field in (
        "metadata_manifest_path",
        "qc_report_path",
        "completion_path",
        "completion_commit_path",
        "failure_report_path",
    ):
        _relative_string(outputs.get(field), f"outputs.{field}")
    for field, expected_value in (
        ("no_overwrite", True),
        ("atomic_publish_required", True),
        ("write_enabled_in_this_package", False),
    ):
        _expect(outputs.get(field), expected_value, f"outputs.{field}")


def validate_static_config(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    audit: AccessAudit | None = None,
) -> StaticValidation:
    """Validate only static C1/public sources and never resolve runtime paths."""

    access = audit if audit is not None else AccessAudit()
    _validate_config_values(config)
    try:
        relative_config = config_path.relative_to(workspace_root).as_posix()
    except ValueError as exc:
        raise D08ContractError("D08 config must be inside the workspace") from exc
    _, config_snapshot = _read_file(workspace_root, relative_config, "D08 config")

    sources = _mapping(config.get("canonical_sources"), "canonical_sources")
    _expect(
        sources.get("access_scope"),
        "C1_public_or_aggregate_only",
        "canonical source scope",
    )
    pins = _list(sources.get("pins"), "canonical source pins")
    source_snapshots: list[FileSnapshot] = []
    payloads: dict[str, bytes] = {}
    observed_paths: list[str] = []
    for index, raw_pin in enumerate(pins):
        pin = _mapping(raw_pin, f"source pin {index}")
        _expect_keys(pin, {"path", "sha256"}, f"source pin {index}")
        path = _relative_string(pin.get("path"), f"source pin {index} path")
        expected_hash = _sha256(pin.get("sha256"), f"source pin {index} SHA-256")
        data, snapshot = _read_file(workspace_root, path, f"canonical source {path}")
        access.source_pin_read_count += 1
        if snapshot.sha256 != expected_hash:
            raise D08ContractError(
                f"canonical source SHA-256 drift: {path}",
                stage="source_pin",
                audit=access,
            )
        observed_paths.append(path)
        source_snapshots.append(snapshot)
        payloads[path] = data
    _expect(tuple(observed_paths), SOURCE_PATHS, "canonical source path order")
    if observed_paths != sorted(observed_paths):
        raise D08ContractError("canonical source pins must be sorted")
    if len(observed_paths) != len(set(observed_paths)):
        raise D08ContractError("canonical source pins must be unique")

    _validate_metadata_schema(
        _parse_json(
            payloads["configs/reference/d08_recording_metadata_v1.schema.json"],
            "D08 metadata schema",
        )
    )
    _validate_qc_schema(
        _parse_json(
            payloads["configs/reference/d08_qc_report_v1.schema.json"],
            "D08 QC schema",
        )
    )
    _validate_completion_schema(
        _parse_json(
            payloads["configs/reference/d08_completion_v1.schema.json"],
            "D08 completion schema",
        )
    )
    _validate_h00_template(
        _parse_json(
            payloads[
                "configs/reference/d08_h00_institutional_approval_v1.template.json"
            ],
            "D08 H00 template",
        )
    )
    _validate_consent_template(
        _parse_json(
            payloads[
                "configs/reference/d08_individual_consent_attestation_v1.template.json"
            ],
            "D08 consent template",
        )
    )
    _validate_rights_template(
        _parse_json(
            payloads[
                "configs/reference/d08_recording_rights_attestation_v1.template.json"
            ],
            "D08 recording-rights template",
        )
    )
    _validate_preregistration_template(
        _parse_json(
            payloads["configs/reference/d08_qc_preregistration_v1.template.json"],
            "D08 QC preregistration template",
        )
    )

    source_set = set(observed_paths)
    outputs = _mapping(config.get("outputs"), "outputs")
    writable_fields = (
        "metadata_manifest_path",
        "qc_report_path",
        "completion_path",
        "completion_commit_path",
        "failure_report_path",
    )
    output_paths = [
        _relative_string(outputs.get(field), f"outputs.{field}")
        for field in writable_fields
    ]
    if len(output_paths) != len(set(output_paths)):
        raise D08ContractError("D08 writable output paths must be unique")
    for output in output_paths:
        output_parts = PurePosixPath(output).parts
        for source in source_set:
            source_parts = PurePosixPath(source).parts
            if (
                output_parts == source_parts
                or output_parts[: len(source_parts)] == source_parts
                or source_parts[: len(output_parts)] == output_parts
            ):
                raise D08ContractError(
                    f"output/source overlap prohibited: {output} / {source}"
                )

    policy = {
        key: config[key]
        for key in (
            "experiment",
            "predecessor_r00",
            "external_prerequisites",
            "recording_design",
            "quality_control",
            "privacy_license_boundaries",
            "execution_order",
            "outputs",
        )
    }
    return StaticValidation(
        config_sha256=config_snapshot.sha256,
        policy_sha256=hashlib.sha256(canonical_json_bytes(policy)).hexdigest(),
        source_snapshots=tuple(source_snapshots),
    )


def _load_r00_validator(
    *,
    workspace_root: Path,
    path: str,
    audit: AccessAudit,
) -> ModuleType:
    validator_path = _reject_symlink_chain(workspace_root, path, "R00 validator")
    module_name = (
        "_voice_coach_d08_r00_validator_"
        + hashlib.sha256(str(validator_path).encode()).hexdigest()
    )
    spec = importlib.util.spec_from_file_location(module_name, validator_path)
    if spec is None or spec.loader is None:
        raise D08ContractError(
            "R00 validator cannot be loaded", stage="r00", audit=audit
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise D08ContractError(
            f"R00 validator import failed: {exc}",
            stage="r00",
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
    """Recompute R00 from public/aggregate inputs and require an exact report."""

    predecessor = _mapping(config.get("predecessor_r00"), "predecessor_r00")
    validator_path = _relative_string(
        predecessor.get("validator_path"), "R00 validator path"
    )
    validator_snapshot = next(
        (
            snapshot
            for snapshot in static.source_snapshots
            if snapshot.path == validator_path
        ),
        None,
    )
    if validator_snapshot is None:
        raise D08ContractError(
            "R00 validator is not source-pinned", stage="r00", audit=audit
        )
    try:
        _verify_snapshot(workspace_root, validator_snapshot, "R00 validator")
    except D08ContractError as exc:
        raise D08ContractError(str(exc), stage="r00_toctou", audit=audit) from exc
    module = _load_r00_validator(
        workspace_root=workspace_root,
        path=validator_path,
        audit=audit,
    )
    validate_bundle = getattr(module, "validate_bundle", None)
    if not callable(validate_bundle):
        raise D08ContractError(
            "R00 validator has no validate_bundle", stage="r00", audit=audit
        )
    try:
        computed_raw = validate_bundle(
            workspace_root=workspace_root,
            card_relative_path=_relative_string(
                predecessor.get("card_path"), "R00 card path"
            ),
            schema_relative_path=_relative_string(
                predecessor.get("schema_path"), "R00 schema path"
            ),
        )
    except Exception as exc:
        raise D08ContractError(
            f"R00 deep validation failed: {exc}",
            stage="r00",
            audit=audit,
        ) from exc
    computed = _mapping(computed_raw, "computed R00 report")
    audit.r00_deep_validation_count += 1
    data_access = _mapping(computed.get("data_access"), "R00 data access")
    audit.r00_public_or_aggregate_evidence_read_count += sum(
        _integer(data_access.get(field), f"R00 data access {field}")
        for field in (
            "public_sentence_card_files_read",
            "public_schema_files_read",
            "public_license_notice_files_read",
            "public_or_aggregate_evidence_files_read",
            "prompt_hash_values_read",
        )
    )
    audit.r00_raw_audio_read_count += _integer(
        data_access.get("raw_audio_files_read"), "R00 raw audio count"
    )
    audit.r00_row_level_read_count += _integer(
        data_access.get("row_level_files_read"), "R00 row-level count"
    )
    if audit.r00_raw_audio_read_count or audit.r00_row_level_read_count:
        raise D08ContractError(
            "R00 validator crossed the raw/row boundary",
            stage="r00",
            audit=audit,
        )

    report_path = _relative_string(
        predecessor.get("validation_report_path"), "R00 report path"
    )
    report_data, report_snapshot = _read_file(
        workspace_root, report_path, "R00 immutable report"
    )
    _expect(
        report_snapshot.sha256,
        predecessor.get("validation_report_sha256"),
        "R00 report SHA-256",
    )
    stored = _parse_json(report_data, "R00 immutable report")
    if canonical_json_bytes(computed) != canonical_json_bytes(stored):
        raise D08ContractError(
            "R00 recomputation differs from immutable report",
            stage="r00",
            audit=audit,
        )
    _expect(stored.get("status"), "valid", "R00 status")
    checks = _mapping(stored.get("checks"), "R00 checks")
    _expect(checks.get("sentence_count"), 20, "R00 sentence count")
    completion = _mapping(stored.get("completion"), "R00 completion")
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
    card_data, card_snapshot = _read_file(
        workspace_root,
        _relative_string(predecessor.get("card_path"), "R00 card path"),
        "R00 sentence card",
    )
    _expect(card_snapshot.sha256, R00_CARD_SHA256, "R00 card SHA-256")
    card = _parse_json(card_data, "R00 sentence card")
    sentences = _list(card.get("sentences"), "R00 sentences")
    sentence_hashes: dict[str, str] = {}
    for index, raw in enumerate(sentences):
        row = _mapping(raw, f"R00 sentence {index}")
        sentence_id = _string(row.get("sentence_id"), "R00 sentence ID")
        sentence_hashes[sentence_id] = _sha256(
            row.get("normalized_text_sha256"),
            f"R00 sentence {sentence_id} hash",
        )
    _expect(tuple(sentence_hashes), SENTENCE_IDS, "R00 sentence order")
    return R00Evidence(
        report_sha256=report_snapshot.sha256,
        report_snapshot=report_snapshot,
        sentence_hashes=sentence_hashes,
    )


def _require_no_tbd(payload: Mapping[str, Any], name: str) -> None:
    if _contains_tbd(payload):
        raise D08ContractError(f"{name} contains an unresolved TBD value")


def validate_h00_approval_payload(payload: Mapping[str, Any]) -> None:
    """Validate a future signed H00 or equivalent institutional approval."""

    _require_no_tbd(payload, "H00 approval")
    _expect_keys(payload, H00_KEYS, "H00 approval")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_h00_institutional_approval.v1",
        "H00 schema",
    )
    _expect(payload.get("status"), "approved_for_d08_recording", "H00 status")
    for field in ("attestation_id", "signed_at_utc", "signed_by_role"):
        _string(payload.get(field), f"H00 {field}")
    determination = _mapping(payload.get("determination"), "H00 determination")
    _expect_keys(
        determination,
        {
            "approval_or_determination_id",
            "protocol_revision",
            "approval_document_sha256",
            "valid_from",
            "valid_until",
        },
        "H00 determination",
    )
    _string(
        determination.get("approval_or_determination_id"),
        "H00 approval ID",
    )
    _string(determination.get("protocol_revision"), "H00 protocol revision")
    _sha256(determination.get("approval_document_sha256"), "H00 document SHA")
    _string(determination.get("valid_from"), "H00 valid_from")
    _string(determination.get("valid_until"), "H00 valid_until")
    scope = _mapping(payload.get("approved_scope"), "H00 approved scope")
    _expect_keys(
        scope,
        {
            "adult_native_korean_speaker_recruitment",
            "voice_recording",
            "automated_qc",
            "human_listening_qc",
            "research_reference_use",
            "model_input_reviewed_separately",
            "third_party_provision_reviewed_separately",
        },
        "H00 approved scope",
    )
    for field in (
        "adult_native_korean_speaker_recruitment",
        "voice_recording",
        "automated_qc",
        "human_listening_qc",
        "research_reference_use",
        "model_input_reviewed_separately",
        "third_party_provision_reviewed_separately",
    ):
        _expect(scope.get(field), True, f"H00 scope {field}")
    privacy = _mapping(payload.get("privacy_governance"), "H00 privacy")
    _expect_keys(
        privacy,
        {
            "access_control_plan_sha256",
            "retention_plan_sha256",
            "deletion_plan_sha256",
            "withdrawal_process_sha256",
            "incident_response_plan_sha256",
            "r0_r1_d1_a1_boundary_approved",
        },
        "H00 privacy",
    )
    for field in (
        "access_control_plan_sha256",
        "retention_plan_sha256",
        "deletion_plan_sha256",
        "withdrawal_process_sha256",
        "incident_response_plan_sha256",
    ):
        _sha256(privacy.get(field), f"H00 privacy {field}")
    _expect(
        privacy.get("r0_r1_d1_a1_boundary_approved"),
        True,
        "H00 R0/R1/D1/A1 approval",
    )
    _expect(payload.get("d08_collection_allowed"), True, "H00 collection")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "H00 completion claim",
    )


def validate_individual_consent_payload(
    payload: Mapping[str, Any],
    *,
    h00_sha256: str,
) -> Mapping[str, Mapping[str, Any]]:
    """Validate future aggregate receipts without accepting direct identifiers."""

    _require_no_tbd(payload, "individual consent attestation")
    _expect_keys(payload, CONSENT_KEYS, "individual consent attestation")
    leaked = FORBIDDEN_IDENTITY_FIELDS & _collect_keys(payload)
    if leaked:
        raise D08ContractError(
            f"consent attestation contains forbidden identity fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_individual_consent_attestation.v1",
        "consent schema",
    )
    _expect(
        payload.get("status"),
        "approved_active_individual_consent",
        "consent status",
    )
    _expect(payload.get("h00_approval_sha256"), h00_sha256, "consent H00 binding")
    for field in ("attestation_id", "signed_at_utc", "signed_by_role"):
        _string(payload.get(field), f"consent {field}")
    protocol = _mapping(payload.get("consent_protocol"), "consent protocol")
    _expect_keys(
        protocol,
        {
            "protocol_id",
            "protocol_revision",
            "protocol_sha256",
            "consent_form_sha256",
            "voice_recording_allowed",
            "research_reference_use_allowed",
            "automated_qc_allowed",
            "human_listening_qc_allowed",
            "model_input_allowed",
            "third_party_provision_allowed",
            "public_release_allowed",
            "commercial_use_allowed",
            "withdrawal_deadline_policy_id",
            "withdrawal_process_sha256",
            "retention_policy_sha256",
            "deletion_protocol_sha256",
        },
        "consent protocol",
    )
    for field in ("protocol_id", "protocol_revision", "withdrawal_deadline_policy_id"):
        _string(protocol.get(field), f"consent protocol {field}")
    for field in (
        "protocol_sha256",
        "consent_form_sha256",
        "withdrawal_process_sha256",
        "retention_policy_sha256",
        "deletion_protocol_sha256",
    ):
        _sha256(protocol.get(field), f"consent protocol {field}")
    for field in (
        "voice_recording_allowed",
        "research_reference_use_allowed",
        "automated_qc_allowed",
        "human_listening_qc_allowed",
    ):
        _expect(protocol.get(field), True, f"consent scope {field}")
    for field in ("model_input_allowed", "third_party_provision_allowed"):
        _boolean(protocol.get(field), f"separated consent scope {field}")
    for field in ("public_release_allowed", "commercial_use_allowed"):
        _expect(protocol.get(field), False, f"consent scope {field}")

    receipts_raw = _list(payload.get("speaker_receipts"), "speaker receipts")
    if not receipts_raw:
        raise D08ContractError("consent attestation needs at least one receipt")
    receipts: dict[str, Mapping[str, Any]] = {}
    receipt_keys = {
        "speaker_id_sha256",
        "consent_receipt_sha256",
        "scope_decision_sha256",
        "adult_attested",
        "native_korean_attested",
        "consent_active",
        "withdrawn",
        "expired",
        "valid_from",
        "valid_until",
        "permitted_sentence_ids",
    }
    for index, raw in enumerate(receipts_raw):
        receipt = _mapping(raw, f"consent receipt {index}")
        _expect_keys(receipt, receipt_keys, f"consent receipt {index}")
        speaker_id = _sha256(
            receipt.get("speaker_id_sha256"),
            f"consent receipt {index} speaker ID",
        )
        if speaker_id in receipts:
            raise D08ContractError("consent speaker IDs must be unique")
        _sha256(
            receipt.get("consent_receipt_sha256"),
            f"consent receipt {index} receipt SHA",
        )
        _sha256(
            receipt.get("scope_decision_sha256"),
            f"consent receipt {index} scope SHA",
        )
        for field in (
            "adult_attested",
            "native_korean_attested",
            "consent_active",
        ):
            _expect(receipt.get(field), True, f"consent receipt {index} {field}")
        for field in ("withdrawn", "expired"):
            _expect(receipt.get(field), False, f"consent receipt {index} {field}")
        _string(receipt.get("valid_from"), f"consent receipt {index} valid_from")
        _string(receipt.get("valid_until"), f"consent receipt {index} valid_until")
        _expect(
            tuple(
                _list(
                    receipt.get("permitted_sentence_ids"),
                    f"consent receipt {index} sentence IDs",
                )
            ),
            SENTENCE_IDS,
            f"consent receipt {index} sentence permissions",
        )
        receipts[speaker_id] = receipt
    contract = _mapping(payload.get("receipt_contract"), "receipt contract")
    _expect_keys(
        contract,
        {
            "planning_target_minimum_distinct_speakers_per_sentence",
            "binding_completion_floor",
            "speaker_id_format",
            "direct_identifiers_allowed",
            "identity_mapping_zone",
            "raw_audio_zone",
            "each_receipt_requires_scope_decision_sha256",
            "inactive_withdrawn_or_expired_receipts_prohibited",
        },
        "receipt contract",
    )
    _expect(
        contract.get("planning_target_minimum_distinct_speakers_per_sentence"),
        PLANNING_TARGET_SPEAKER_COUNT,
        "receipt planning target",
    )
    _integer(
        contract.get("binding_completion_floor"), "binding speaker floor", minimum=1
    )
    _expect(
        contract.get("speaker_id_format"),
        "namespaced_lowercase_sha256",
        "speaker ID format",
    )
    _expect(contract.get("direct_identifiers_allowed"), False, "direct identity")
    _expect(
        contract.get("identity_mapping_zone"),
        "R1_separate_access_controlled",
        "identity mapping zone",
    )
    _expect(
        contract.get("raw_audio_zone"),
        "R0_restricted_raw_audio",
        "raw audio zone",
    )
    _expect(
        contract.get("each_receipt_requires_scope_decision_sha256"),
        True,
        "scope receipt requirement",
    )
    _expect(
        contract.get("inactive_withdrawn_or_expired_receipts_prohibited"),
        True,
        "inactive receipt prohibition",
    )
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "consent completion claim",
    )
    return receipts


def validate_recording_rights_payload(
    payload: Mapping[str, Any],
    *,
    h00_sha256: str,
    consent_sha256: str,
    consent_receipts: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    """Validate honest human/source/license and exact recording rights."""

    _require_no_tbd(payload, "recording-rights attestation")
    _expect_keys(payload, RIGHTS_KEYS, "recording-rights attestation")
    leaked = FORBIDDEN_IDENTITY_FIELDS & _collect_keys(payload)
    if leaked:
        raise D08ContractError(
            f"recording-rights attestation contains identity fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_recording_rights_attestation.v1",
        "recording-rights schema",
    )
    _expect(
        payload.get("status"),
        "approved_active_recording_rights",
        "recording-rights status",
    )
    _expect(payload.get("h00_approval_sha256"), h00_sha256, "rights H00 binding")
    _expect(
        payload.get("individual_consent_attestation_sha256"),
        consent_sha256,
        "rights consent binding",
    )
    for field in ("attestation_id", "signed_at_utc", "signed_by_role"):
        _string(payload.get(field), f"recording-rights {field}")
    source = _mapping(payload.get("source_classification"), "source classification")
    _expect_keys(
        source,
        {
            "source_kind",
            "public_corpus_audio_used",
            "tts_audio_used",
            "corpus_or_tts_disguised_as_human",
            "human_tts_source_label_required",
            "license_basis",
        },
        "source classification",
    )
    _expect(
        source.get("source_kind"),
        "newly_recorded_consented_adult_native_korean_human",
        "source kind",
    )
    for field in (
        "public_corpus_audio_used",
        "tts_audio_used",
        "corpus_or_tts_disguised_as_human",
    ):
        _expect(source.get(field), False, f"source classification {field}")
    _expect(
        source.get("human_tts_source_label_required"),
        True,
        "source label requirement",
    )
    _string(source.get("license_basis"), "recording license basis")
    protocol = _mapping(payload.get("recording_protocol"), "recording protocol")
    expected_protocol_keys = {
        "protocol_id",
        "protocol_revision",
        "protocol_sha256",
        "r00_card_sha256",
        "natural_take_count_per_speaker_sentence",
        "clear_educational_take_count_per_speaker_sentence",
        "sample_rate_hz",
        "bit_depth",
        "channel_count",
        "container_format",
        "codec",
        "same_equipment_profile_required",
        "same_microphone_profile_required",
        "same_room_profile_required",
        "mouth_to_microphone_distance_min_cm",
        "mouth_to_microphone_distance_max_cm",
        "equipment_profile_id_sha256",
        "microphone_profile_id_sha256",
        "room_profile_id_sha256",
    }
    _expect_keys(protocol, expected_protocol_keys, "recording protocol")
    for field in ("protocol_id", "protocol_revision"):
        _string(protocol.get(field), f"recording protocol {field}")
    _sha256(protocol.get("protocol_sha256"), "recording protocol SHA")
    for field, expected_value in (
        ("r00_card_sha256", R00_CARD_SHA256),
        ("natural_take_count_per_speaker_sentence", 2),
        ("clear_educational_take_count_per_speaker_sentence", 1),
        ("sample_rate_hz", 48000),
        ("bit_depth", 24),
        ("channel_count", 1),
        ("container_format", "WAV"),
        ("codec", "PCM"),
        ("same_equipment_profile_required", True),
        ("same_microphone_profile_required", True),
        ("same_room_profile_required", True),
        ("mouth_to_microphone_distance_min_cm", 15),
        ("mouth_to_microphone_distance_max_cm", 20),
    ):
        _expect(
            protocol.get(field),
            expected_value,
            f"recording protocol {field}",
        )
    for field in (
        "equipment_profile_id_sha256",
        "microphone_profile_id_sha256",
        "room_profile_id_sha256",
    ):
        _sha256(protocol.get(field), f"recording protocol {field}")

    rights_raw = _list(
        payload.get("speaker_rights_receipts"), "speaker rights receipts"
    )
    rights: dict[str, Mapping[str, Any]] = {}
    rights_keys = {
        "speaker_id_sha256",
        "recording_license_grant_sha256",
        "permitted_use_scope_sha256",
        "right_active",
        "withdrawn",
        "expired",
        "research_reference_use_allowed",
        "automated_qc_allowed",
        "human_listening_qc_allowed",
        "model_input_allowed",
        "third_party_provision_allowed",
        "public_release_allowed",
        "commercial_use_allowed",
        "permitted_sentence_ids",
    }
    for index, raw in enumerate(rights_raw):
        receipt = _mapping(raw, f"rights receipt {index}")
        _expect_keys(receipt, rights_keys, f"rights receipt {index}")
        speaker_id = _sha256(
            receipt.get("speaker_id_sha256"),
            f"rights receipt {index} speaker ID",
        )
        if speaker_id not in consent_receipts:
            raise D08ContractError("rights speaker lacks a consent receipt")
        if speaker_id in rights:
            raise D08ContractError("rights speaker IDs must be unique")
        for field in (
            "recording_license_grant_sha256",
            "permitted_use_scope_sha256",
        ):
            _sha256(receipt.get(field), f"rights receipt {index} {field}")
        for field in (
            "right_active",
            "research_reference_use_allowed",
            "automated_qc_allowed",
            "human_listening_qc_allowed",
        ):
            _expect(receipt.get(field), True, f"rights receipt {index} {field}")
        for field in (
            "withdrawn",
            "expired",
            "public_release_allowed",
            "commercial_use_allowed",
        ):
            _expect(receipt.get(field), False, f"rights receipt {index} {field}")
        for field in ("model_input_allowed", "third_party_provision_allowed"):
            _boolean(receipt.get(field), f"rights receipt {index} {field}")
        _expect(
            tuple(
                _list(
                    receipt.get("permitted_sentence_ids"),
                    f"rights receipt {index} sentence IDs",
                )
            ),
            SENTENCE_IDS,
            f"rights receipt {index} sentence permissions",
        )
        rights[speaker_id] = receipt
    _expect(set(rights), set(consent_receipts), "consent/rights speaker set")
    contract = _mapping(payload.get("rights_contract"), "rights contract")
    expected_contract = {
        "each_speaker_requires_recording_license_grant_sha256": True,
        "each_speaker_requires_all_20_sentence_permissions": True,
        "active_nonwithdrawn_nonexpired_right_required": True,
        "raw_audio_redistribution_allowed": False,
        "public_release_allowed": False,
        "commercial_use_allowed": False,
        "source_and_license_must_be_explicit_per_reference": True,
    }
    _expect(dict(contract), expected_contract, "rights contract")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "recording-rights completion claim",
    )
    return rights


def validate_qc_preregistration_payload(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate future exact thresholds without inventing them in this package."""

    _require_no_tbd(payload, "QC preregistration")
    _expect_keys(payload, PREREGISTRATION_KEYS, "QC preregistration")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_qc_preregistration.v1",
        "QC preregistration schema",
    )
    _expect(payload.get("status"), "registered_and_frozen", "QC preregistration status")
    for field in ("preregistration_id", "registered_at_utc", "registered_by_role"):
        _string(payload.get(field), f"QC preregistration {field}")
    _expect(
        payload.get("frozen_before_recruitment_or_audio_access"),
        True,
        "QC preregistration freeze timing",
    )
    _expect(payload.get("r00_card_sha256"), R00_CARD_SHA256, "QC R00 binding")
    speaker = _mapping(payload.get("speaker_contract"), "speaker contract")
    _expect_keys(
        speaker,
        {
            "planning_target_minimum_distinct_speakers_per_sentence",
            "binding_minimum_distinct_speakers_per_sentence",
            "target_shortfall_reporting_rule",
        },
        "speaker contract",
    )
    _expect(
        speaker.get("planning_target_minimum_distinct_speakers_per_sentence"),
        PLANNING_TARGET_SPEAKER_COUNT,
        "speaker planning target",
    )
    binding_floor = _integer(
        speaker.get("binding_minimum_distinct_speakers_per_sentence"),
        "binding minimum speakers",
        minimum=1,
    )
    _string(
        speaker.get("target_shortfall_reporting_rule"),
        "target shortfall reporting rule",
    )
    fixed = _mapping(payload.get("fixed_qc_gates"), "fixed QC gates")
    expected_fixed = {
        "clipping_ratio_operator": "<",
        "clipping_ratio_threshold": CLIPPING_RATIO_EXCLUSIVE_MAXIMUM,
        "decoder_error_count_required": 0,
        "human_reviewer_pool_size": 2,
        "minimum_human_listener_confirmations": 1,
        "asr_role": "candidate_triage_only_not_final_qc",
    }
    _expect(dict(fixed), expected_fixed, "fixed QC gates")
    measurements = _mapping(
        payload.get("measurement_contracts"), "measurement contracts"
    )
    _expect(
        set(measurements),
        {"dc_offset", "content_match", "noise", "reverb", "interruption"},
        "measurement categories",
    )
    normalized_measurements: dict[str, Mapping[str, Any]] = {}
    for name, raw in measurements.items():
        section = _mapping(raw, f"{name} measurement")
        _expect_keys(
            section,
            {"metric_id", "unit", "direction", "threshold"},
            f"{name} measurement",
        )
        _string(section.get("metric_id"), f"{name} metric ID")
        _string(section.get("unit"), f"{name} unit")
        direction = _string(section.get("direction"), f"{name} direction")
        if direction not in {"maximum", "minimum", "absolute_maximum", "exact"}:
            raise D08ContractError(f"unsupported {name} threshold direction")
        _finite(section.get("threshold"), f"{name} threshold")
        normalized_measurements[name] = section
    adjudication = _mapping(
        payload.get("adjudication_contract"), "adjudication contract"
    )
    _expect_keys(
        adjudication,
        {
            "trigger_rule_id",
            "decision_rule_id",
            "adjudicator_role",
            "independence_required",
            "receipt_required_when_triggered",
        },
        "adjudication contract",
    )
    for field in ("trigger_rule_id", "decision_rule_id", "adjudicator_role"):
        _string(adjudication.get(field), f"adjudication {field}")
    _boolean(adjudication.get("independence_required"), "adjudicator independence")
    _expect(
        adjudication.get("receipt_required_when_triggered"),
        True,
        "adjudication receipt requirement",
    )
    approval = _mapping(payload.get("approval"), "QC approval")
    _expect_keys(
        approval,
        {"decision", "protocol_sha256", "signed_receipt_sha256"},
        "QC approval",
    )
    _expect(approval.get("decision"), "approved", "QC approval decision")
    _sha256(approval.get("protocol_sha256"), "QC protocol SHA")
    _sha256(approval.get("signed_receipt_sha256"), "QC approval receipt SHA")
    _expect(
        payload.get("completion_claim_allowed"),
        False,
        "QC preregistration completion claim",
    )
    return {
        "binding_minimum_distinct_speakers_per_sentence": binding_floor,
        "measurements": normalized_measurements,
        "adjudication": adjudication,
    }


def _read_external_document(
    *,
    workspace_root: Path,
    raw: Mapping[str, Any],
    name: str,
    stage: str,
    path_counter: str,
    attempt_counter: str,
    read_counter: str,
    audit: AccessAudit,
) -> tuple[Mapping[str, Any], FileSnapshot]:
    expected_hash = _string(raw.get("sha256"), f"{name} SHA")
    if expected_hash.startswith("TBD_"):
        raise D08ContractError(
            f"{name} is {expected_hash}",
            stage=stage,
            audit=audit,
        )
    _sha256(expected_hash, f"{name} SHA")
    setattr(audit, path_counter, getattr(audit, path_counter) + 1)
    path = _relative_string(raw.get("path"), f"{name} path")
    setattr(audit, attempt_counter, getattr(audit, attempt_counter) + 1)
    try:
        data, snapshot = _read_file(workspace_root, path, name)
    except D08ContractError as exc:
        raise D08ContractError(str(exc), stage=stage, audit=audit) from exc
    setattr(audit, read_counter, getattr(audit, read_counter) + 1)
    if snapshot.sha256 != expected_hash:
        raise D08ContractError(f"{name} SHA-256 drift", stage=stage, audit=audit)
    return _parse_json(data, name), snapshot


def validate_external_prerequisites(
    *,
    workspace_root: Path,
    config: Mapping[str, Any],
    audit: AccessAudit,
) -> ExternalEvidence:
    """Read future evidence in order; canonical execution stops at H00 read zero."""

    external = _mapping(config.get("external_prerequisites"), "external_prerequisites")
    h00, h00_snapshot = _read_external_document(
        workspace_root=workspace_root,
        raw=_mapping(external.get("h00_institutional_approval"), "H00 config"),
        name="H00 institutional approval",
        stage="h00_institutional_approval",
        path_counter="h00_path_resolution_count",
        attempt_counter="h00_read_attempt_count",
        read_counter="h00_read_count",
        audit=audit,
    )
    try:
        validate_h00_approval_payload(h00)
    except D08ContractError as exc:
        raise D08ContractError(
            str(exc), stage="h00_institutional_approval", audit=audit
        ) from exc
    consent, consent_snapshot = _read_external_document(
        workspace_root=workspace_root,
        raw=_mapping(external.get("individual_consent"), "consent config"),
        name="individual consent attestation",
        stage="individual_consent",
        path_counter="individual_consent_path_resolution_count",
        attempt_counter="individual_consent_read_attempt_count",
        read_counter="individual_consent_read_count",
        audit=audit,
    )
    try:
        consent_receipts = validate_individual_consent_payload(
            consent, h00_sha256=h00_snapshot.sha256
        )
    except D08ContractError as exc:
        raise D08ContractError(
            str(exc), stage="individual_consent", audit=audit
        ) from exc
    rights, rights_snapshot = _read_external_document(
        workspace_root=workspace_root,
        raw=_mapping(external.get("recording_rights"), "recording-rights config"),
        name="recording-rights attestation",
        stage="recording_rights",
        path_counter="recording_rights_path_resolution_count",
        attempt_counter="recording_rights_read_attempt_count",
        read_counter="recording_rights_read_count",
        audit=audit,
    )
    try:
        validate_recording_rights_payload(
            rights,
            h00_sha256=h00_snapshot.sha256,
            consent_sha256=consent_snapshot.sha256,
            consent_receipts=consent_receipts,
        )
    except D08ContractError as exc:
        raise D08ContractError(str(exc), stage="recording_rights", audit=audit) from exc
    prereg, prereg_snapshot = _read_external_document(
        workspace_root=workspace_root,
        raw=_mapping(external.get("qc_preregistration"), "QC prereg config"),
        name="QC preregistration",
        stage="qc_preregistration",
        path_counter="qc_preregistration_path_resolution_count",
        attempt_counter="qc_preregistration_read_attempt_count",
        read_counter="qc_preregistration_read_count",
        audit=audit,
    )
    try:
        prereg_contract = validate_qc_preregistration_payload(prereg)
    except D08ContractError as exc:
        raise D08ContractError(
            str(exc), stage="qc_preregistration", audit=audit
        ) from exc
    consent_contract = _mapping(
        consent.get("receipt_contract"), "consent receipt contract"
    )
    _expect(
        consent_contract.get("binding_completion_floor"),
        prereg_contract["binding_minimum_distinct_speakers_per_sentence"],
        "consent/preregistration speaker floor binding",
    )
    rights_protocol = _mapping(
        rights.get("recording_protocol"), "rights recording protocol"
    )
    design = _mapping(config.get("recording_design"), "recording_design")
    setup = _mapping(design.get("recording_setup"), "recording setup")
    for config_field, rights_field in (
        ("equipment_profile_id_sha256", "equipment_profile_id_sha256"),
        ("microphone_profile_id_sha256", "microphone_profile_id_sha256"),
        ("room_profile_id_sha256", "room_profile_id_sha256"),
    ):
        _expect(
            setup.get(config_field),
            rights_protocol.get(rights_field),
            f"config/rights {config_field} binding",
        )
    return ExternalEvidence(
        h00_snapshot=h00_snapshot,
        consent_snapshot=consent_snapshot,
        rights_snapshot=rights_snapshot,
        preregistration_snapshot=prereg_snapshot,
        h00=h00,
        consent=consent,
        rights=rights,
        preregistration=prereg,
    )


def _threshold_pass(value: float, contract: Mapping[str, Any]) -> bool:
    threshold = _finite(contract.get("threshold"), "measurement threshold")
    direction = _string(contract.get("direction"), "measurement direction")
    if direction == "maximum":
        return value <= threshold
    if direction == "minimum":
        return value >= threshold
    if direction == "absolute_maximum":
        return abs(value) <= threshold
    if direction == "exact":
        return value == threshold
    raise D08ContractError(f"unsupported threshold direction: {direction}")


def validate_recording_metadata_payload(
    payload: Mapping[str, Any],
    *,
    sentence_hashes: Mapping[str, str],
    h00_sha256: str,
    consent_sha256: str,
    rights_sha256: str,
    preregistration_sha256: str,
    consent_payload: Mapping[str, Any],
    rights_payload: Mapping[str, Any],
    preregistration_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Deep-validate future R1 metadata without opening any audio."""

    _expect_keys(payload, METADATA_KEYS, "D08 metadata manifest")
    leaked = FORBIDDEN_IDENTITY_FIELDS & _collect_keys(payload)
    if leaked:
        raise D08ContractError(
            f"metadata contains forbidden identity fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_recording_metadata_manifest.v1",
        "metadata schema",
    )
    if (
        METADATA_ID_RE.fullmatch(_string(payload.get("manifest_id"), "manifest ID"))
        is None
    ):
        raise D08ContractError("metadata manifest ID must be opaque")
    _expect(payload.get("status"), "recorded_pending_qc", "metadata status")
    for field, expected_value in (
        ("r00_card_sha256", R00_CARD_SHA256),
        ("h00_approval_sha256", h00_sha256),
        ("individual_consent_attestation_sha256", consent_sha256),
        ("recording_rights_attestation_sha256", rights_sha256),
        ("qc_preregistration_sha256", preregistration_sha256),
        ("privacy_zone", "R1_access_controlled_locator_metadata"),
        ("publication_allowed", False),
        ("contains_direct_identifiers", False),
        ("contains_free_text", False),
    ):
        _expect(payload.get(field), expected_value, f"metadata {field}")
    _expect(set(sentence_hashes), set(SENTENCE_IDS), "R00 sentence hash set")
    for sentence_id, digest in sentence_hashes.items():
        _sha256(digest, f"R00 sentence hash {sentence_id}")

    consent_receipts = validate_individual_consent_payload(
        consent_payload, h00_sha256=h00_sha256
    )
    rights_receipts = validate_recording_rights_payload(
        rights_payload,
        h00_sha256=h00_sha256,
        consent_sha256=consent_sha256,
        consent_receipts=consent_receipts,
    )
    prereg = validate_qc_preregistration_payload(preregistration_payload)
    binding_floor = _integer(
        prereg["binding_minimum_distinct_speakers_per_sentence"],
        "binding speaker floor",
        minimum=1,
    )
    rights_protocol = _mapping(
        rights_payload.get("recording_protocol"), "rights recording protocol"
    )
    expected_equipment = _sha256(
        rights_protocol.get("equipment_profile_id_sha256"), "equipment profile"
    )
    expected_microphone = _sha256(
        rights_protocol.get("microphone_profile_id_sha256"), "microphone profile"
    )
    expected_room = _sha256(
        rights_protocol.get("room_profile_id_sha256"), "room profile"
    )

    rows_raw = _list(payload.get("rows"), "metadata rows")
    if not rows_raw:
        raise D08ContractError("metadata manifest cannot be empty")
    recording_ids: set[str] = set()
    audio_hashes: set[str] = set()
    locator_tokens: set[str] = set()
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    by_sentence: dict[str, set[str]] = defaultdict(set)
    rows: list[Mapping[str, Any]] = []
    for index, raw in enumerate(rows_raw):
        row = _mapping(raw, f"metadata row {index}")
        _expect_keys(row, METADATA_ROW_KEYS, f"metadata row {index}")
        recording_id = _string(
            row.get("recording_id"), f"metadata row {index} recording ID"
        )
        if RECORDING_ID_RE.fullmatch(recording_id) is None:
            raise D08ContractError("recording IDs must be opaque")
        if recording_id in recording_ids:
            raise D08ContractError("recording IDs must be globally unique")
        recording_ids.add(recording_id)
        speaker_id = _sha256(
            row.get("speaker_id_sha256"),
            f"metadata row {index} speaker ID",
        )
        if speaker_id not in consent_receipts or speaker_id not in rights_receipts:
            raise D08ContractError("metadata speaker lacks consent or rights")
        sentence_id = _string(
            row.get("sentence_id"), f"metadata row {index} sentence ID"
        )
        if sentence_id not in sentence_hashes:
            raise D08ContractError(f"unknown D08 sentence ID: {sentence_id}")
        _expect(
            row.get("target_text_sha256"),
            sentence_hashes[sentence_id],
            f"metadata row {index} target text hash",
        )
        style = _string(row.get("speaking_style"), f"metadata row {index} style")
        if style not in {"natural", "clear_educational"}:
            raise D08ContractError("unsupported D08 speaking style")
        take_index = _integer(
            row.get("take_index"), f"metadata row {index} take index", minimum=1
        )
        if style == "natural" and take_index not in {1, 2}:
            raise D08ContractError("natural take index must be 1 or 2")
        if style == "clear_educational" and take_index != 1:
            raise D08ContractError("clear educational take index must be 1")
        for field in (
            "session_id_sha256",
            "equipment_profile_id_sha256",
            "microphone_profile_id_sha256",
            "room_profile_id_sha256",
            "r0_audio_locator_token_sha256",
            "audio_sha256",
            "consent_receipt_sha256",
            "recording_license_grant_sha256",
        ):
            _sha256(row.get(field), f"metadata row {index} {field}")
        _expect(
            row.get("equipment_profile_id_sha256"),
            expected_equipment,
            "same equipment profile",
        )
        _expect(
            row.get("microphone_profile_id_sha256"),
            expected_microphone,
            "same microphone profile",
        )
        _expect(
            row.get("room_profile_id_sha256"),
            expected_room,
            "same room profile",
        )
        _expect(
            row.get("consent_receipt_sha256"),
            consent_receipts[speaker_id]["consent_receipt_sha256"],
            "metadata consent receipt binding",
        )
        _expect(
            row.get("recording_license_grant_sha256"),
            rights_receipts[speaker_id]["recording_license_grant_sha256"],
            "metadata recording-right binding",
        )
        audio_hash = _string(row.get("audio_sha256"), "audio SHA")
        locator = _string(row.get("r0_audio_locator_token_sha256"), "R0 locator token")
        if audio_hash in audio_hashes or locator in locator_tokens:
            raise D08ContractError("audio hashes and locator tokens must be unique")
        audio_hashes.add(audio_hash)
        locator_tokens.add(locator)
        for field, format_expected in (
            ("sample_rate_hz", 48000),
            ("bit_depth", 24),
            ("channel_count", 1),
            ("container_format", "WAV"),
            ("codec", "PCM"),
            ("source_kind", "human_recording"),
            ("native_korean_attested", True),
            ("raw_audio_zone", "R0_restricted_raw_audio"),
            ("locator_manifest_zone", "R1_access_controlled_locator_metadata"),
            ("publication_allowed", False),
        ):
            _expect(
                row.get(field),
                format_expected,
                f"metadata row {index} {field}",
            )
        distance = _finite(
            row.get("mouth_to_microphone_distance_cm"),
            f"metadata row {index} distance",
        )
        if not 15 <= distance <= 20:
            raise D08ContractError("mouth-to-microphone distance must be 15-20 cm")
        grouped[(speaker_id, sentence_id)].append(row)
        by_sentence[sentence_id].add(speaker_id)
        rows.append(row)

    _expect(set(by_sentence), set(SENTENCE_IDS), "D08 sentence coverage")
    for sentence_id in SENTENCE_IDS:
        if len(by_sentence[sentence_id]) < binding_floor:
            raise D08ContractError(
                f"{sentence_id} is below the preregistered speaker floor"
            )
    for (speaker_id, sentence_id), group in grouped.items():
        observed_takes = {
            (str(row["speaking_style"]), int(row["take_index"])) for row in group
        }
        expected_takes = {
            ("natural", 1),
            ("natural", 2),
            ("clear_educational", 1),
        }
        if observed_takes != expected_takes or len(group) != 3:
            raise D08ContractError(
                f"{speaker_id}/{sentence_id} must have exactly natural 1/2 "
                "and clear_educational 1"
            )
    sentence_summary = {
        sentence_id: {
            "distinct_speaker_count": len(by_sentence[sentence_id]),
            "accepted_natural_take_count": 2 * len(by_sentence[sentence_id]),
            "accepted_clear_educational_take_count": len(by_sentence[sentence_id]),
            "accepted_take_count": 3 * len(by_sentence[sentence_id]),
        }
        for sentence_id in SENTENCE_IDS
    }
    return {
        "status": "valid_restricted_metadata_pending_qc",
        "binding_minimum_distinct_speakers_per_sentence": binding_floor,
        "planning_target_met_all_sentences": all(
            len(by_sentence[sentence_id]) >= PLANNING_TARGET_SPEAKER_COUNT
            for sentence_id in SENTENCE_IDS
        ),
        "recording_ids": tuple(sorted(recording_ids)),
        "row_count": len(rows),
        "distinct_speaker_sentence_pairs": len(grouped),
        "sentence_summary": sentence_summary,
    }


def validate_qc_report_payload(
    payload: Mapping[str, Any],
    *,
    metadata_manifest_sha256: str,
    preregistration_sha256: str,
    recording_ids: Sequence[str],
    preregistration_payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate future D1 QC rows against externally preregistered thresholds."""

    _expect_keys(payload, QC_REPORT_KEYS, "D08 QC report")
    leaked = FORBIDDEN_IDENTITY_FIELDS & _collect_keys(payload)
    if leaked:
        raise D08ContractError(
            f"QC report contains forbidden identity fields: {sorted(leaked)}"
        )
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_qc_report.v1",
        "QC report schema",
    )
    if (
        QC_REPORT_ID_RE.fullmatch(_string(payload.get("report_id"), "QC report ID"))
        is None
    ):
        raise D08ContractError("QC report ID must be opaque")
    for field, expected_value in (
        ("status", "qc_completed_pending_completion"),
        ("metadata_manifest_sha256", metadata_manifest_sha256),
        ("qc_preregistration_sha256", preregistration_sha256),
        ("privacy_zone", "D1_pseudonymized_restricted_qc"),
        ("contains_r0_locator", False),
        ("contains_direct_identifiers", False),
        ("contains_free_text", False),
        ("publication_allowed", False),
    ):
        _expect(payload.get(field), expected_value, f"QC report {field}")
    prereg = validate_qc_preregistration_payload(preregistration_payload)
    measurements = _mapping(prereg["measurements"], "QC measurement contracts")
    expected_recording_ids = tuple(sorted(recording_ids))
    if len(expected_recording_ids) != len(set(expected_recording_ids)):
        raise D08ContractError("metadata recording IDs must be unique")
    rows_raw = _list(payload.get("rows"), "QC rows")
    if len(rows_raw) != len(expected_recording_ids):
        raise D08ContractError("QC row count must match metadata row count")
    observed_ids: set[str] = set()
    failure_counts: Counter[str] = Counter()
    all_pass = True
    for index, raw in enumerate(rows_raw):
        row = _mapping(raw, f"QC row {index}")
        _expect_keys(row, QC_ROW_KEYS, f"QC row {index}")
        recording_id = _string(row.get("recording_id"), "QC recording ID")
        if recording_id not in expected_recording_ids:
            raise D08ContractError("QC row references an unknown recording")
        if recording_id in observed_ids:
            raise D08ContractError("QC recording IDs must be unique")
        observed_ids.add(recording_id)
        decoder_pass = (
            _integer(row.get("decoder_error_count"), "decoder error count") == 0
        )
        clipping = _finite(row.get("clipping_ratio"), "clipping ratio")
        clipping_pass = 0 <= clipping < CLIPPING_RATIO_EXCLUSIVE_MAXIMUM
        metric_fields = {
            "dc_offset": ("dc_offset_metric_id", "dc_offset_value", "dc_offset_pass"),
            "content_match": (
                "content_match_metric_id",
                "content_match_value",
                "content_match_pass",
            ),
            "noise": ("noise_metric_id", "noise_value", "noise_pass"),
            "reverb": ("reverb_metric_id", "reverb_value", "reverb_pass"),
            "interruption": (
                "interruption_metric_id",
                "interruption_value",
                "interruption_pass",
            ),
        }
        metric_passes: dict[str, bool] = {}
        for name, (id_field, value_field, pass_field) in metric_fields.items():
            contract = _mapping(measurements.get(name), f"{name} contract")
            _expect(row.get(id_field), contract.get("metric_id"), f"{name} metric ID")
            value = _finite(row.get(value_field), f"{name} value")
            expected_pass = _threshold_pass(value, contract)
            _expect(row.get(pass_field), expected_pass, f"{name} pass flag")
            metric_passes[name] = expected_pass
        _expect(row.get("asr_used_for_triage_only"), True, "ASR triage-only")
        _expect(row.get("asr_used_as_final_qc"), False, "ASR final QC")
        _expect(row.get("human_reviewer_pool_size"), 2, "human reviewer pool")
        confirmations = _integer(
            row.get("human_listener_confirmation_count"),
            "human listener confirmations",
        )
        if confirmations not in {1, 2}:
            raise D08ContractError("human listener confirmations must be 1 or 2")
        human_pass = _boolean(
            row.get("human_content_and_pronunciation_confirmed"),
            "human content/pronunciation confirmation",
        )
        adjudication_required = _boolean(
            row.get("adjudication_required"), "adjudication required"
        )
        adjudication_status = _string(
            row.get("adjudication_status"), "adjudication status"
        )
        adjudication_receipt = row.get("adjudication_receipt_sha256")
        if adjudication_required:
            _expect(adjudication_status, "completed", "adjudication status")
            _sha256(adjudication_receipt, "adjudication receipt SHA")
            adjudication_pass = True
        else:
            _expect(adjudication_status, "not_required", "adjudication status")
            _expect(adjudication_receipt, None, "adjudication receipt")
            adjudication_pass = True
        expected_final = (
            decoder_pass
            and clipping_pass
            and all(metric_passes.values())
            and human_pass
            and adjudication_pass
        )
        _expect(row.get("final_qc_pass"), expected_final, "final QC pass")
        reason = _string(row.get("exclusion_reason_code"), "exclusion reason")
        if expected_final:
            _expect(reason, "none", "passing QC exclusion reason")
        elif reason == "none":
            raise D08ContractError("failed QC row needs a coded exclusion reason")
        all_pass = all_pass and expected_final
        for name, passed in (
            ("decoder", decoder_pass),
            ("clipping", clipping_pass),
            ("dc_offset", metric_passes["dc_offset"]),
            ("content_mismatch", metric_passes["content_match"]),
            ("noise", metric_passes["noise"]),
            ("reverb", metric_passes["reverb"]),
            ("interruption", metric_passes["interruption"]),
            ("human_review", human_pass),
            ("adjudication", adjudication_pass),
        ):
            if not passed:
                failure_counts[name] += 1
    _expect(
        tuple(sorted(observed_ids)), expected_recording_ids, "QC recording coverage"
    )
    return {
        "status": "valid_qc_report",
        "row_count": len(rows_raw),
        "all_qc_pass": all_pass,
        "failure_counts": dict(failure_counts),
    }


def validate_completion_payload(
    payload: Mapping[str, Any],
    *,
    config_sha256: str,
    h00_sha256: str,
    consent_sha256: str,
    rights_sha256: str,
    preregistration_sha256: str,
    metadata_manifest_sha256: str,
    qc_report_sha256: str,
    metadata_summary: Mapping[str, Any],
    qc_summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Validate a future A1 aggregate completion claim, never row-level data."""

    leaked = FORBIDDEN_A1_FIELDS & _collect_keys(payload)
    if leaked:
        raise D08ContractError(
            f"completion contains forbidden A1 fields: {sorted(leaked)}"
        )
    _expect_keys(payload, COMPLETION_KEYS, "D08 completion")
    _expect(
        payload.get("schema_version"),
        "voice_coach.d08_completion.v1",
        "completion schema",
    )
    if (
        COMPLETION_ID_RE.fullmatch(
            _string(payload.get("completion_id"), "completion ID")
        )
        is None
    ):
        raise D08ContractError("completion ID must be opaque")
    for field, expected_value in (
        ("status", "completed"),
        ("config_sha256", config_sha256),
        ("r00_card_sha256", R00_CARD_SHA256),
        ("h00_approval_sha256", h00_sha256),
        ("individual_consent_attestation_sha256", consent_sha256),
        ("recording_rights_attestation_sha256", rights_sha256),
        ("qc_preregistration_sha256", preregistration_sha256),
        ("metadata_manifest_sha256", metadata_manifest_sha256),
        ("qc_report_sha256", qc_report_sha256),
        ("privacy_zone", "A1_aggregate_review_required"),
        ("contains_row_level_data", False),
        ("contains_direct_or_pseudonymous_ids", False),
        ("contains_audio_or_locator_hashes", False),
        ("contains_free_text", False),
        ("sentence_count", 20),
        (
            "binding_minimum_distinct_speakers_per_sentence",
            metadata_summary["binding_minimum_distinct_speakers_per_sentence"],
        ),
        (
            "planning_target_minimum_distinct_speakers_per_sentence",
            PLANNING_TARGET_SPEAKER_COUNT,
        ),
        (
            "planning_target_met_all_sentences",
            metadata_summary["planning_target_met_all_sentences"],
        ),
        ("same_equipment_and_space_verified", True),
        ("format_and_distance_verified", True),
        ("source_license_honesty_verified", True),
        ("qc_adjudication_contract_verified", True),
        ("actual_recruitment_completed", True),
        ("actual_consent_completed", True),
        ("actual_recording_completed", True),
        ("actual_qc_completed", True),
        ("completion_claim_allowed", True),
        ("publication_allowed", False),
    ):
        _expect(payload.get(field), expected_value, f"completion {field}")
    if not qc_summary.get("all_qc_pass"):
        raise D08ContractError("completion requires every selected take to pass QC")
    sentence_summary = _mapping(
        metadata_summary.get("sentence_summary"), "metadata sentence summary"
    )
    sentences = _list(payload.get("sentences"), "completion sentences")
    if len(sentences) != 20:
        raise D08ContractError("completion must contain 20 sentence summaries")
    total_speaker_pairs = 0
    total_natural = 0
    total_clear = 0
    total_accepted = 0
    for index, raw in enumerate(sentences):
        row = _mapping(raw, f"completion sentence {index}")
        _expect_keys(row, COMPLETION_SENTENCE_KEYS, f"completion sentence {index}")
        sentence_id = SENTENCE_IDS[index]
        _expect(row.get("sentence_id"), sentence_id, "completion sentence order")
        computed = _mapping(
            sentence_summary.get(sentence_id),
            f"metadata summary {sentence_id}",
        )
        for field in (
            "distinct_speaker_count",
            "accepted_natural_take_count",
            "accepted_clear_educational_take_count",
            "accepted_take_count",
        ):
            _expect(row.get(field), computed.get(field), f"{sentence_id} {field}")
        distinct = _integer(
            computed.get("distinct_speaker_count"), f"{sentence_id} speakers"
        )
        binding_floor = _integer(
            metadata_summary["binding_minimum_distinct_speakers_per_sentence"],
            "binding speaker floor",
            minimum=1,
        )
        _expect(
            row.get("speaker_floor_pass"), distinct >= binding_floor, "speaker floor"
        )
        _expect(
            row.get("planning_target_met"),
            distinct >= PLANNING_TARGET_SPEAKER_COUNT,
            "planning target status",
        )
        _expect(row.get("exact_take_pattern_pass"), True, "take pattern")
        _expect(row.get("all_qc_pass"), True, "sentence QC")
        total_speaker_pairs += distinct
        total_natural += int(computed["accepted_natural_take_count"])
        total_clear += int(computed["accepted_clear_educational_take_count"])
        total_accepted += int(computed["accepted_take_count"])
    totals = _mapping(payload.get("totals"), "completion totals")
    _expect_keys(totals, COMPLETION_TOTAL_KEYS, "completion totals")
    expected_totals = {
        "distinct_speaker_sentence_pairs": total_speaker_pairs,
        "accepted_natural_take_count": total_natural,
        "accepted_clear_educational_take_count": total_clear,
        "accepted_take_count": total_accepted,
        "failed_take_count": 0,
        "decoder_error_count": 0,
        "clipping_failure_count": 0,
        "dc_offset_failure_count": 0,
        "content_mismatch_failure_count": 0,
        "noise_failure_count": 0,
        "reverb_failure_count": 0,
        "interruption_failure_count": 0,
        "human_review_failure_count": 0,
        "adjudication_failure_count": 0,
    }
    _expect(dict(totals), expected_totals, "completion totals")
    commit = _string(
        payload.get("implementation_git_commit"), "implementation git commit"
    )
    if GIT_COMMIT_RE.fullmatch(commit) is None or commit == "0" * 40:
        raise D08ContractError("implementation git commit must be a nonzero 40-hex")
    return {
        "status": "valid_completed_aggregate",
        "sentence_count": 20,
        "accepted_take_count": total_accepted,
        "planning_target_met_all_sentences": payload[
            "planning_target_met_all_sentences"
        ],
        "completion_claim_allowed": True,
        "publication_allowed": False,
    }


def ensure_output_targets_absent(
    *,
    workspace_root: Path,
    outputs: Mapping[str, Any],
) -> None:
    """Reject existing/symlinked future output targets without creating them."""

    for field in (
        "metadata_manifest_path",
        "qc_report_path",
        "completion_path",
        "completion_commit_path",
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
            raise D08ContractError(f"no-overwrite target exists: {relative}")


def _verify_static_snapshots(
    *,
    workspace_root: Path,
    static: StaticValidation,
    audit: AccessAudit,
) -> None:
    for snapshot in static.source_snapshots:
        try:
            _verify_snapshot(workspace_root, snapshot, "canonical source")
        except D08ContractError as exc:
            raise D08ContractError(
                str(exc), stage="source_toctou", audit=audit
            ) from exc
        audit.source_pin_verify_count += 1


def run_full_preflight(
    *,
    workspace_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
) -> PreflightResult:
    """Run read-only gates in the safety-critical order."""

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
    try:
        _verify_snapshot(
            workspace_root,
            r00.report_snapshot,
            "R00 immutable report before external approval",
        )
    except D08ContractError as exc:
        raise D08ContractError(str(exc), stage="r00_toctou", audit=audit) from exc

    # Canonical execution stops inside this call before resolving the H00 path.
    evidence = validate_external_prerequisites(
        workspace_root=workspace_root,
        config=config,
        audit=audit,
    )
    for name, snapshot in (
        ("H00 approval", evidence.h00_snapshot),
        ("individual consent", evidence.consent_snapshot),
        ("recording rights", evidence.rights_snapshot),
        ("QC preregistration", evidence.preregistration_snapshot),
    ):
        try:
            _verify_snapshot(workspace_root, snapshot, name)
        except D08ContractError as exc:
            raise D08ContractError(
                str(exc), stage="external_toctou", audit=audit
            ) from exc
    raise D08ContractError(
        "external prerequisites validated; a separately audited recruitment/"
        "recording/QC executor is required",
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
        "r00_actual_completion_required": True,
        "planning_target_speakers_per_sentence": PLANNING_TARGET_SPEAKER_COUNT,
        "binding_speaker_floor": TBD_PREREGISTRATION,
        "actual_recruitment_completed": False,
        "actual_consent_completed": False,
        "actual_recording_completed": False,
        "actual_qc_completed": False,
        "d08_completion_claim_allowed": False,
        "publication_allowed": False,
    }


def failure_result(error: D08ContractError) -> Mapping[str, Any]:
    audit = error.audit if error.audit is not None else AccessAudit()
    return {
        "status": "failed_closed",
        "stage": error.stage,
        "error": str(error),
        **audit.as_dict(),
        "actual_recruitment_completed": False,
        "actual_consent_completed": False,
        "actual_recording_completed": False,
        "actual_qc_completed": False,
        "d08_completion_claim_allowed": False,
        "publication_allowed": False,
    }
