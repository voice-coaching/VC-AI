"""Validation contract for the Korean error taxonomy and advice ontology.

Only the pinned privacy-safe aggregate data card is read.  Row-level manifests,
raw audio/labels, locators, environment files, and identifiers are outside this
module's authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from voice_coach.phonology.error_tags import (
    RAW_ERROR_TAG_TO_ID,
    ErrorTagId,
)


CATALOG_SCHEMA_VERSION = "voice_coach.korean_error_advice_ontology.v1"
CATALOG_ID = "aihub71490-korean-error-advice-v1"
AGGREGATE_SCHEMA_VERSION = "aihub71490.data_card.v1"
ALLOWED_AGGREGATE_PATH = PurePosixPath(
    "data/processed/aihub71490/manifests/aihub71490_data_card.json"
)
SCHEMA_ID = (
    "https://voice-coach.local/schemas/korean_error_advice_ontology_v1.schema.json"
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DENIED_PATH_COMPONENTS = frozenset({"raw", "restricted", "locator", "locators"})
_SENSITIVE_KEYS = frozenset(
    {
        "speaker_id",
        "user_id",
        "userid",
        "item_id",
        "record_id",
        "file_stem",
        "audio_path",
        "json_path",
        "textgrid_path",
        "relative_path",
        "source_path",
        "locator",
    }
)

_EXPECTED_LABEL_ROLES = (
    "supervision_label",
    "evaluation_slice",
    "human_audit_stratum",
)
_EXPECTED_PROHIBITED_FEATURES = (
    "raw_error_tag",
    "normalized_error_tag_id",
    "error_interval",
    "error_tag_count",
    "realized_phone",
    "alignment_operation",
)
_EXPECTED_RANKING_TIES = (
    "start_ms",
    "end_ms",
    "issue_type",
    "target",
    "observed",
    "evidence",
)
_EXPECTED_PHONE_TIES = (
    "lowest_target_phone_index",
    "lexicographic_target_phone_label",
)

_TAG_POLICY: Mapping[ErrorTagId, tuple[str, str]] = {
    ErrorTagId.INITIAL_CONSONANT: ("onset_consonant", "phone_overlap_required"),
    ErrorTagId.FINAL_CONSONANT: ("coda_consonant", "phone_overlap_required"),
    ErrorTagId.VOWEL_ERROR: ("vowel", "phone_overlap_required"),
    ErrorTagId.TENSIFICATION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.OTHER: ("unknown", "no_target_phone_claim"),
    ErrorTagId.REPETITION: ("non_target_fluency_event", "no_target_phone_claim"),
    ErrorTagId.NASALIZATION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.ASPIRATION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.LIQUIDIZATION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.LIAISON_RULE: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.BOUNDARY_INTONATION_ERROR: (
        "prosodic_region",
        "prosodic_span_only",
    ),
    ErrorTagId.N_INSERTION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.PALATALIZATION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.STRESS_ERROR: ("prosodic_region", "prosodic_span_only"),
    ErrorTagId.INTERJECTION: (
        "non_target_fluency_event",
        "no_target_phone_claim",
    ),
    ErrorTagId.VOWEL_INSERTION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
    ErrorTagId.PHONEME_DELETION: (
        "contextual_phone_sequence",
        "phone_overlap_required",
    ),
}


class OntologyValidationError(ValueError):
    """The ontology bundle is unsafe, inconsistent, or source-drifted."""


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OntologyValidationError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
        )
    except UnicodeDecodeError as exc:
        raise OntologyValidationError(f"{path} is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise OntologyValidationError(f"{path} is not valid JSON: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_safe_relative(
    workspace_root: Path,
    relative_path: str | PurePosixPath,
    *,
    expected: PurePosixPath | None = None,
) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise OntologyValidationError(f"unsafe relative path: {str(pure)!r}")
    if expected is not None and pure != expected:
        raise OntologyValidationError(
            f"aggregate source must be {str(expected)!r}, got {str(pure)!r}"
        )
    lowered = tuple(part.casefold() for part in pure.parts)
    if any(
        part in _DENIED_PATH_COMPONENTS or part.startswith(".env") for part in lowered
    ):
        raise OntologyValidationError(f"protected path is forbidden: {str(pure)!r}")

    root = workspace_root.resolve()
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise OntologyValidationError(f"symlink input is forbidden: {current}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise OntologyValidationError(f"required file is missing: {candidate}") from exc
    if not resolved.is_relative_to(root):
        raise OntologyValidationError(f"path escapes workspace: {candidate}")
    if not resolved.is_file():
        raise OntologyValidationError(f"input must be a regular file: {candidate}")
    return resolved


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_json_schema_subset(
    value: Any,
    schema: Mapping[str, Any],
    *,
    location: str = "$",
) -> None:
    """Validate the JSON-Schema keywords used by the checked-in schema."""

    if "const" in schema and value != schema["const"]:
        raise OntologyValidationError(f"{location} violates const")
    if "enum" in schema and value not in schema["enum"]:
        raise OntologyValidationError(f"{location} is outside enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(types, list) or not all(
            isinstance(item, str) for item in types
        ):
            raise OntologyValidationError(f"{location} schema has invalid type")
        if not any(_json_type_matches(value, item) for item in types):
            raise OntologyValidationError(
                f"{location} has invalid JSON type; expected {types!r}"
            )

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise OntologyValidationError(f"{location} schema required is invalid")
        missing = [item for item in required if item not in value]
        if missing:
            raise OntologyValidationError(
                f"{location} misses required keys: {missing!r}"
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise OntologyValidationError(f"{location} schema properties is invalid")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise OntologyValidationError(
                    f"{location} has unexpected keys: {sorted(extras)!r}"
                )
        for key, item in value.items():
            if key in properties:
                _validate_json_schema_subset(
                    item,
                    properties[key],
                    location=f"{location}.{key}",
                )

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise OntologyValidationError(f"{location} has too few items")
        if maximum is not None and len(value) > maximum:
            raise OntologyValidationError(f"{location} has too many items")
        if schema.get("uniqueItems"):
            serialized = [
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value
            ]
            if len(serialized) != len(set(serialized)):
                raise OntologyValidationError(f"{location} items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_json_schema_subset(
                    item,
                    item_schema,
                    location=f"{location}[{index}]",
                )

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise OntologyValidationError(f"{location} is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise OntologyValidationError(f"{location} does not match pattern")

    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        if "minimum" in schema and value < schema["minimum"]:
            raise OntologyValidationError(f"{location} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise OntologyValidationError(f"{location} is above maximum")


def _require_exact_keys(
    document: Mapping[str, Any],
    expected: Sequence[str],
    *,
    location: str,
) -> None:
    if set(document) != set(expected):
        raise OntologyValidationError(
            f"{location} keys differ: expected={sorted(expected)!r}, "
            f"observed={sorted(document)!r}"
        )


def _assert_no_sensitive_keys(value: Any, *, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold() in _SENSITIVE_KEYS:
                raise OntologyValidationError(
                    f"sensitive row/locator key is forbidden at {location}.{key}"
                )
            _assert_no_sensitive_keys(item, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_sensitive_keys(item, location=f"{location}[{index}]")


def _validate_semantics(
    catalog: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    *,
    aggregate_sha256: str,
) -> None:
    source = catalog["source"]
    if source != {
        "dataset": "AI Hub 71490",
        "aggregate_report_path": str(ALLOWED_AGGREGATE_PATH),
        "aggregate_report_sha256": aggregate_sha256,
        "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION,
        "access_scope": "privacy_safe_aggregate_only",
        "observed_error_interval_count": aggregate.get("error_interval_count"),
        "observed_unknown_error_tag_count": aggregate.get(
            "unknown_error_tag_occurrences"
        ),
    }:
        raise OntologyValidationError("catalog source pins do not match aggregate")
    if aggregate_sha256 != source["aggregate_report_sha256"]:
        raise OntologyValidationError("aggregate report SHA-256 drift")
    if not _HEX64.fullmatch(aggregate_sha256):
        raise OntologyValidationError("aggregate report SHA-256 is malformed")
    if aggregate.get("schema_version") != AGGREGATE_SCHEMA_VERSION:
        raise OntologyValidationError("aggregate schema version drift")

    raw_counts = aggregate.get("error_tag_counts")
    normalized_counts = aggregate.get("normalized_error_tag_counts")
    if not isinstance(raw_counts, dict) or not isinstance(normalized_counts, dict):
        raise OntologyValidationError("aggregate error-tag counts are missing")
    if aggregate.get("raw_error_tag_count") != 17:
        raise OntologyValidationError("aggregate does not contain exactly 17 tags")
    if aggregate.get("unknown_error_tag_occurrences") != 0:
        raise OntologyValidationError("aggregate contains unknown error tags")

    expected_pairs = [
        (tag_id.value, raw_label) for raw_label, tag_id in RAW_ERROR_TAG_TO_ID.items()
    ]
    tags = catalog["tags"]
    observed_pairs = [(item["id"], item["raw_label"]) for item in tags]
    if observed_pairs != expected_pairs:
        raise OntologyValidationError("catalog tag order or raw-label mapping drift")

    advice_ids: list[str] = []
    total = 0
    for item in tags:
        tag_id = ErrorTagId(item["id"])
        raw_label = item["raw_label"]
        observed_count = item["observed_interval_count"]
        if raw_counts.get(raw_label) != observed_count:
            raise OntologyValidationError(
                f"raw aggregate support mismatch for {raw_label!r}"
            )
        if normalized_counts.get(tag_id.value) != observed_count:
            raise OntologyValidationError(
                f"normalized aggregate support mismatch for {tag_id.value!r}"
            )
        total += observed_count

        expected_scope, expected_localization = _TAG_POLICY[tag_id]
        if (
            item["target_scope"],
            item["localization_policy"],
        ) != (expected_scope, expected_localization):
            raise OntologyValidationError(
                f"target-phone policy mismatch for {tag_id.value!r}"
            )
        if item["empirical_target_phone_support"] is not None:
            raise OntologyValidationError(
                "empirical tag-by-phone support must remain null until available"
            )

        advice = item["advice"]
        if tag_id is ErrorTagId.OTHER:
            if advice != {
                "advice_id": None,
                "action": "abstain_expert_review",
                "message_ko": None,
                "review_status": "not_applicable",
            }:
                raise OntologyValidationError("other must always abstain")
        else:
            if (
                advice["action"] != "draft_coach"
                or advice["review_status"] != "engineering_draft"
                or not isinstance(advice["advice_id"], str)
                or not advice["advice_id"].startswith("KO_")
                or not isinstance(advice["message_ko"], str)
                or not advice["message_ko"].strip()
            ):
                raise OntologyValidationError(
                    f"draft advice contract mismatch for {tag_id.value!r}"
                )
            advice_ids.append(advice["advice_id"])
    if len(advice_ids) != len(set(advice_ids)):
        raise OntologyValidationError("advice IDs must be unique")
    if total != aggregate.get("error_interval_count"):
        raise OntologyValidationError("per-tag support does not sum to total")
    if sum(raw_counts.values()) != total or sum(normalized_counts.values()) != total:
        raise OntologyValidationError("aggregate count dictionaries are inconsistent")

    separation = catalog["feature_separation"]
    if tuple(separation["allowed_label_roles"]) != _EXPECTED_LABEL_ROLES:
        raise OntologyValidationError("allowed label roles drift")
    if (
        tuple(separation["prohibited_model_input_fields"])
        != _EXPECTED_PROHIBITED_FEATURES
    ):
        raise OntologyValidationError("prohibited feature list drift")

    mapping = catalog["target_phone_mapping"]
    if mapping["empirical_status"] != "pending_privacy_safe_joint_aggregate":
        raise OntologyValidationError("empirical mapping status must remain pending")
    if tuple(mapping["interval_rules"]["tie_breakers"]) != _EXPECTED_PHONE_TIES:
        raise OntologyValidationError("phone overlap tie rules drift")

    policy = catalog["coaching_policy"]
    if policy["allowed_top_k"] != [1, 3] or policy["default_top_k"] != 1:
        raise OntologyValidationError("only deterministic top-1/top-3 are allowed")
    if tuple(policy["ranking_tie_breakers"]) != _EXPECTED_RANKING_TIES:
        raise OntologyValidationError("coaching ranking tie rules drift")
    if policy["priority_formula"] != (
        "severity*confidence*comprehensibility_impact*trainability"
    ):
        raise OntologyValidationError("priority formula drift")
    if policy["confidence_threshold_status"] != ("provisional_until_s107_calibration"):
        raise OntologyValidationError("confidence threshold must remain provisional")
    if policy["production_release"] != "prohibited_until_c03_expert_audit":
        raise OntologyValidationError("production advice release is not prohibited")
    if policy["unsupported_claim_action"] != "expert_review":
        raise OntologyValidationError("unsupported claims must abstain")

    human = catalog["human_validation"]
    if (
        human["expert_review_status"] != "pending"
        or human["c03_completed"] is not False
    ):
        raise OntologyValidationError("C03 must remain pending in this C01 catalog")


def validate_ontology_bundle(
    *,
    workspace_root: Path,
    catalog_relative_path: str,
    schema_relative_path: str,
) -> dict[str, Any]:
    """Validate schema, catalog, source hash/counts, policy, and privacy boundary."""

    catalog_path = _resolve_safe_relative(workspace_root, catalog_relative_path)
    schema_path = _resolve_safe_relative(workspace_root, schema_relative_path)
    catalog = _load_json(catalog_path)
    schema = _load_json(schema_path)
    if not isinstance(catalog, dict) or not isinstance(schema, dict):
        raise OntologyValidationError("catalog and schema must be JSON objects")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise OntologyValidationError("JSON Schema draft marker drift")
    if schema.get("$id") != SCHEMA_ID:
        raise OntologyValidationError("JSON Schema ID drift")
    _validate_json_schema_subset(catalog, schema)

    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise OntologyValidationError("catalog schema version drift")
    if catalog.get("ontology_id") != CATALOG_ID:
        raise OntologyValidationError("catalog ID drift")
    source = catalog.get("source")
    if not isinstance(source, dict):
        raise OntologyValidationError("catalog source must be an object")
    aggregate_path = _resolve_safe_relative(
        workspace_root,
        source.get("aggregate_report_path", ""),
        expected=ALLOWED_AGGREGATE_PATH,
    )
    aggregate_sha256 = sha256_file(aggregate_path)
    aggregate = _load_json(aggregate_path)
    if not isinstance(aggregate, dict):
        raise OntologyValidationError("aggregate report must be a JSON object")
    _assert_no_sensitive_keys(aggregate)
    _validate_semantics(
        catalog,
        aggregate,
        aggregate_sha256=aggregate_sha256,
    )

    tag_support = {
        item["id"]: item["observed_interval_count"] for item in catalog["tags"]
    }
    return {
        "schema_version": "voice_coach.c01_ontology_validation.v1",
        "status": "valid",
        "ontology_id": CATALOG_ID,
        "inputs": {
            "catalog_path": catalog_relative_path,
            "catalog_sha256": sha256_file(catalog_path),
            "schema_path": schema_relative_path,
            "schema_sha256": sha256_file(schema_path),
            "aggregate_report_path": str(ALLOWED_AGGREGATE_PATH),
            "aggregate_report_sha256": aggregate_sha256,
        },
        "observed_support": {
            "tag_count": 17,
            "error_interval_count": aggregate["error_interval_count"],
            "unknown_error_tag_count": aggregate["unknown_error_tag_occurrences"],
            "per_tag": tag_support,
        },
        "target_phone_mapping": {
            "deterministic_rule_implemented": True,
            "tag_by_target_phone_joint_counts_available": False,
            "empirical_status": "pending_privacy_safe_joint_aggregate",
            "automatic_target_claim_without_positive_overlap": False,
        },
        "advice_boundary": {
            "advice_status": "engineering_draft",
            "confidence_threshold_status": ("provisional_until_s107_calibration"),
            "production_release_allowed": False,
            "c03_completed": False,
        },
        "data_access": {
            "privacy_safe_aggregate_files_read": 1,
            "row_level_files_read": 0,
            "raw_files_read": 0,
            "locator_files_read": 0,
            "environment_files_read": 0,
            "identifier_values_read": 0,
        },
        "validation_checks": {
            "json_schema_subset": "passed",
            "source_sha256": "passed",
            "seventeen_tag_bijection": "passed",
            "aggregate_count_conservation": "passed",
            "feature_label_separation": "passed",
            "overlap_tie_multi_unmatched_rules": "passed",
            "top_1_top_3_confidence_abstention_policy": "passed",
            "privacy_boundary": "passed",
        },
        "completion": {
            "engineering_contract_valid": True,
            "c01_research_completion_claimed": False,
            "blocking_evidence": [
                "privacy_safe_tag_by_target_phone_joint_counts",
                "c03_expert_review_and_safety_audit",
                "s107_calibrated_confidence_threshold",
            ],
        },
        "findings": {
            "critical": 0,
            "high": 0,
            "medium": 0,
        },
    }


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_immutable_report(
    *,
    workspace_root: Path,
    output_relative_path: str,
    report: Mapping[str, Any],
) -> Path:
    """Atomically create a report, allowing only an exact idempotent rerun."""

    pure = PurePosixPath(output_relative_path)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or pure.parts[:2] != ("artifacts", "coaching")
    ):
        raise OntologyValidationError("report output must be under artifacts/coaching")
    root = workspace_root.resolve()
    output = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise OntologyValidationError("symlink report output is forbidden")
        current.mkdir(exist_ok=True)
        if not current.resolve().is_relative_to(root):
            raise OntologyValidationError("report output escapes workspace")
    if output.is_symlink():
        raise OntologyValidationError("symlink report output is forbidden")
    payload = canonical_json_bytes(report)
    if output.exists():
        if output.read_bytes() != payload:
            raise OntologyValidationError(
                "refusing to overwrite a different C01 validation report"
            )
        return output

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".part",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
