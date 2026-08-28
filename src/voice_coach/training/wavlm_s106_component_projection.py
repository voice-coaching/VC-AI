"""Label-free component projection guards for the S106 stack bundle.

This module deliberately does not open the stack manifest or any label
matrix.  It supplies the B07 checkpoint replay adapter and the exact
four-component bundle-document builder used by a reviewed S106 projection
runner.  A production bundle must also bind the canonical generator commit,
feature-freeze receipt, and immutable producer identity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import numpy as np


COMPONENT_BUNDLE_SCHEMA_VERSION = "voice_coach.wavlm_s106_component_bundle_commit.v3"
PREDICTION_RECORD_SCHEMA_VERSION = (
    "voice_coach.wavlm_s106_stack_component_predictions.v2"
)
EXECUTION_LOCK_SCHEMA_VERSION = "voice_coach.wavlm_s106_execution_lock.v2"
EXECUTION_LOCK_RELATIVE_PATH = (
    "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1/execution_lock.json"
)
COMPONENT_PREDICTION_ROOT = (
    "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1/"
    "stack_component_predictions"
)
COMPONENTS = (
    "explicit",
    "s104_adapter",
    "s105_selected_hierarchy",
    "gop_acoustic_posterior",
)
SEEDS = (42, 43, 44)
MAXIMUM_PROJECTION_BATCH_ROWS = 8_192
PREDICTION_COLUMNS = (
    "utterance_id_sha256",
    "speaker_id_sha256",
    "phone_index",
    "split",
    "probability",
)
B07_CATEGORICAL_COLUMNS = (
    "target_label",
    "previous_target_label",
    "next_target_label",
)
B07_EXPLICIT_NUMERIC_COLUMNS = (
    "oracle_duration_seconds",
    "segment_rms",
    "segment_zero_crossing_rate",
)
FORBIDDEN_PROJECTION_NAMES = frozenset(
    {
        "reference_binary_error_label",
        "binary_error_label",
        "y",
        "y_true",
        "realized_phone",
        "realized_phoneme",
        "alignment_operation",
        "phone_alignment_operation",
        "error_tag",
        "error_type",
        "substitution_tag",
        "deletion_tag",
        "insertion_tag",
    }
)
FORBIDDEN_PROJECTION_FRAGMENTS = (
    "binary_error",
    "ground_truth",
    "realized",
    "operation",
    "error_tag",
    "error_type",
    "substitution",
    "deletion",
    "insertion",
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BASE_FILE_RECORD_KEYS = frozenset({"path", "sha256", "size_bytes", "row_count"})
IMMUTABLE_FILE_RECORD_KEYS = frozenset({"path", "sha256", "size_bytes"})
PREDICTION_RECORD_KEYS = frozenset(
    {
        *BASE_FILE_RECORD_KEYS,
        "schema_version",
        "component",
        "seed",
        "split",
        "source_lineage_sha256",
        "source_checkpoint_sha256s",
        "projection_attestation",
    }
)
PROJECTION_REPRESENTATIONS = {
    "explicit": "committed_explicit_phone_features",
    "s104_adapter": "adapter_applied_target_interval_pooled",
    "s105_selected_hierarchy": (
        "adapter_applied_all_encoder_layers_target_interval_pooled"
    ),
    "gop_acoustic_posterior": "serialized_acoustic_then_error_checkpoint",
}


class S106ProjectionError(ValueError):
    """Raised before a label or protected split can reach projection code."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise S106ProjectionError(f"{name} must be a mapping")
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _pretty_canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _binding(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise S106ProjectionError(f"{name} must be lowercase SHA-256")
    return value


def _relative_path(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise S106ProjectionError(f"{name} must be a non-empty path")
    path = Path(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.casefold().startswith(".env") for part in path.parts)
    ):
        raise S106ProjectionError(f"{name} must be workspace-relative")
    return value


def _file_record(value: object, name: str) -> Mapping[str, Any]:
    record = _mapping(value, name)
    if set(record) != BASE_FILE_RECORD_KEYS:
        raise S106ProjectionError(f"{name} file record schema differs")
    path = _relative_path(record.get("path"), f"{name}.path")
    if "dev" in {part.casefold() for part in Path(path).parts} or any(
        fragment in path.casefold()
        for fragment in ("dev_prediction", "train_prediction", "locked_test")
    ):
        raise S106ProjectionError("non-stack predictions cannot feed S106")
    _sha256(record.get("sha256"), f"{name}.sha256")
    for field in ("size_bytes", "row_count"):
        number = record.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise S106ProjectionError(f"{name}.{field} must be positive")
    return dict(record)


def _immutable_file_record(
    value: object,
    *,
    name: str,
    expected_path: str,
) -> Mapping[str, Any]:
    record = _mapping(value, name)
    if set(record) != IMMUTABLE_FILE_RECORD_KEYS:
        raise S106ProjectionError(f"{name} file record schema differs")
    path = _relative_path(record.get("path"), f"{name}.path")
    if path != expected_path:
        raise S106ProjectionError(f"{name} canonical path differs")
    _sha256(record.get("sha256"), f"{name}.sha256")
    size = record.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise S106ProjectionError(f"{name}.size_bytes must be positive")
    return dict(record)


def _production_producer_identity(value: object) -> Mapping[str, Any]:
    identity = _mapping(value, "component generator producer identity")
    expected_keys = {
        "mode",
        "production_schema_allowed",
        "dependency_injection_allowed",
        "backend_factory",
        "preflight",
        "bundle_builder",
        "pinned_sources",
    }
    pinned = _mapping(identity.get("pinned_sources"), "producer pinned sources")
    roles = (
        "component_projection_module",
        "component_generator_config",
        "component_generator_script",
        "component_generator_module",
    )
    if (
        set(identity) != expected_keys
        or identity.get("mode") != "production"
        or identity.get("production_schema_allowed") is not True
        or identity.get("dependency_injection_allowed") is not False
        or identity.get("backend_factory")
        != (
            "voice_coach.training.wavlm_s106_component_generator."
            "production_backend_factory"
        )
        or identity.get("preflight")
        != "voice_coach.training.wavlm_s106_fusion_contract.prepare_s106_execution"
        or identity.get("bundle_builder")
        != (
            "voice_coach.training.wavlm_s106_component_projection."
            "build_component_bundle_document"
        )
        or tuple(pinned) != roles
    ):
        raise S106ProjectionError("component generator producer identity differs")
    normalized_sources: dict[str, Any] = {}
    for role in roles:
        record = _mapping(pinned.get(role), f"producer pinned source {role}")
        if set(record) != {"path", "sha256"}:
            raise S106ProjectionError(f"producer pinned source {role} differs")
        normalized_sources[role] = {
            "path": _relative_path(record.get("path"), f"{role}.path"),
            "sha256": _sha256(record.get("sha256"), f"{role}.sha256"),
        }
    return {
        **{key: identity[key] for key in expected_keys - {"pinned_sources"}},
        "pinned_sources": normalized_sources,
    }


def _projection_attestation(
    value: object,
    *,
    component: str,
    name: str,
) -> Mapping[str, Any]:
    attestation = _mapping(value, name)
    expected = {
        "label_free": True,
        "stack_only": True,
        "binary_error_label_used": False,
        "realized_phone_identity_used": False,
        "dev_prediction_reused": False,
        "representation": PROJECTION_REPRESENTATIONS[component],
    }
    if dict(attestation) != expected:
        raise S106ProjectionError(f"{name} differs")
    return dict(attestation)


def _checkpoint_hashes_for_seed(
    frozen_component: Mapping[str, Any],
    *,
    component: str,
    seed: int,
) -> tuple[str, ...]:
    records = frozen_component.get("projection_checkpoints")
    if not isinstance(records, list):
        raise S106ProjectionError(
            f"{component} frozen projection checkpoints are absent"
        )
    matches = [
        _mapping(record, f"{component} checkpoint record")
        for record in records
        if isinstance(record, Mapping) and record.get("seed") == seed
    ]
    if len(matches) != 1:
        raise S106ProjectionError(f"{component} seed {seed} checkpoint binding differs")
    record = matches[0]
    if component == "gop_acoustic_posterior":
        hashes = tuple(
            _sha256(
                _mapping(
                    record.get(f"{kind}_checkpoint"),
                    f"{component} seed {seed} {kind} checkpoint",
                ).get("sha256"),
                f"{component} seed {seed} {kind} checkpoint SHA-256",
            )
            for kind in ("acoustic", "error")
        )
    else:
        checkpoint = _mapping(
            record.get("checkpoint"),
            f"{component} seed {seed} checkpoint",
        )
        hashes = (
            _sha256(
                checkpoint.get("sha256"),
                f"{component} seed {seed} checkpoint SHA-256",
            ),
        )
    if len(set(hashes)) != len(hashes):
        raise S106ProjectionError(
            f"{component} seed {seed} checkpoint hashes are duplicated"
        )
    return hashes


def _source_lineage_sha256(
    frozen_component: Mapping[str, Any],
    component: str,
) -> str:
    if component == "explicit":
        sources = _mapping(
            frozen_component.get("sources"),
            "explicit frozen sources",
        )
        registry = _mapping(
            sources.get("registry"),
            "explicit frozen registry source",
        )
        return _sha256(
            registry.get("sha256"),
            "explicit frozen registry SHA-256",
        )
    return _sha256(
        frozen_component.get("source_commit_sha256"),
        f"{component} frozen source commit SHA-256",
    )


def _prediction_record(
    value: object,
    *,
    component: str,
    seed: int,
    frozen_component: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    record = _mapping(value, name)
    if set(record) != PREDICTION_RECORD_KEYS:
        raise S106ProjectionError(f"{name} prediction record schema differs")
    path = _relative_path(record.get("path"), f"{name}.path")
    expected_path = (
        f"{COMPONENT_PREDICTION_ROOT}/{component}/seed_{seed}_stack_predictions.parquet"
    )
    expected_checkpoints = _checkpoint_hashes_for_seed(
        frozen_component,
        component=component,
        seed=seed,
    )
    raw_checkpoints = record.get("source_checkpoint_sha256s")
    if not isinstance(raw_checkpoints, list):
        raise S106ProjectionError(f"{name}.source_checkpoint_sha256s must be a list")
    checkpoints = tuple(
        _sha256(value, f"{name}.source_checkpoint_sha256s") for value in raw_checkpoints
    )
    if (
        path != expected_path
        or record.get("schema_version") != PREDICTION_RECORD_SCHEMA_VERSION
        or record.get("component") != component
        or record.get("seed") != seed
        or record.get("split") != "stack"
        or record.get("source_lineage_sha256")
        != _source_lineage_sha256(frozen_component, component)
        or checkpoints != expected_checkpoints
    ):
        raise S106ProjectionError(f"{name} stack provenance differs")
    _sha256(record.get("sha256"), f"{name}.sha256")
    for field in ("size_bytes", "row_count"):
        number = record.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise S106ProjectionError(f"{name}.{field} must be positive")
    attestation = _projection_attestation(
        record.get("projection_attestation"),
        component=component,
        name=f"{name}.projection_attestation",
    )
    return {
        **dict(record),
        "source_checkpoint_sha256s": list(checkpoints),
        "projection_attestation": attestation,
    }


def validate_label_free_projection_columns(columns: Sequence[str]) -> None:
    """Reject labels, realized-phone identity, and error operations by name."""

    normalized = tuple(str(value).casefold() for value in columns)
    if len(normalized) != len(set(normalized)):
        raise S106ProjectionError("projection columns contain a duplicate")
    for column in normalized:
        if column in FORBIDDEN_PROJECTION_NAMES or any(
            fragment in column for fragment in FORBIDDEN_PROJECTION_FRAGMENTS
        ):
            raise S106ProjectionError(f"forbidden projection column: {column}")


def project_explicit_label_free_features(
    *,
    checkpoint: Mapping[str, Any],
    feature_rows: np.ndarray,
) -> np.ndarray:
    """Run the committed explicit phone pipeline without accepting a target."""

    phone = _mapping(checkpoint.get("phone"), "explicit phone checkpoint")
    expected_features = (
        "target_label",
        "previous_target_label",
        "next_target_label",
        "oracle_duration_seconds",
        "segment_rms",
        "segment_zero_crossing_rate",
    )
    if (
        checkpoint.get("schema_version")
        != "voice_coach.explicit_baseline_checkpoint.v1"
        or tuple(phone.get("features", ())) != expected_features
    ):
        raise S106ProjectionError("explicit checkpoint feature contract differs")
    validate_label_free_projection_columns(expected_features)
    matrix = np.asarray(feature_rows, dtype=object)
    if (
        matrix.ndim != 2
        or matrix.shape[0] <= 0
        or matrix.shape[0] > MAXIMUM_PROJECTION_BATCH_ROWS
        or matrix.shape[1] != len(expected_features)
    ):
        raise S106ProjectionError("explicit projection matrix shape differs")
    pipeline = phone.get("pipeline")
    if pipeline is None or not callable(getattr(pipeline, "predict_proba", None)):
        raise S106ProjectionError("explicit phone pipeline is unavailable")
    classes = np.asarray(getattr(pipeline, "classes_", ()))
    if classes.shape != (2,) or classes.tolist() != [0, 1]:
        raise S106ProjectionError("explicit phone pipeline class order differs")
    probabilities = np.asarray(pipeline.predict_proba(matrix), dtype=np.float64)
    if probabilities.shape != (matrix.shape[0], 2):
        raise S106ProjectionError("explicit probability matrix shape differs")
    output = probabilities[:, 1]
    if not np.isfinite(output).all() or np.any((output < 0.0) | (output > 1.0)):
        raise S106ProjectionError("explicit probability is invalid")
    return output


def project_s104_label_free_embeddings(
    *,
    model: Any,
    phone_embeddings: Any,
    target_indices: Any,
    representation_attestation: Mapping[str, Any],
) -> np.ndarray:
    """Run a hash-validated S104 model head on already pooled stack phones."""

    expected_attestation = {
        "representation": "adapter_applied_target_interval_pooled",
        "adapter_applied": True,
        "target_interval_pooling": True,
        "label_free": True,
    }
    if dict(representation_attestation) != expected_attestation:
        raise S106ProjectionError(
            "S104 projection requires adapter-applied pooled representations"
        )
    try:
        import torch

        if (
            not isinstance(phone_embeddings, torch.Tensor)
            or not isinstance(target_indices, torch.Tensor)
            or phone_embeddings.ndim != 2
            or target_indices.shape != (phone_embeddings.shape[0],)
            or target_indices.dtype != torch.long
            or phone_embeddings.shape[0] <= 0
            or phone_embeddings.shape[0] > MAXIMUM_PROJECTION_BATCH_ROWS
            or not callable(
                getattr(getattr(model, "phone_head", None), "forward", None)
            )
        ):
            raise S106ProjectionError("S104 label-free projection input differs")
        model.eval()
        with torch.inference_mode():
            logits = model.phone_head(phone_embeddings, target_indices)
            probabilities = (
                torch.sigmoid(logits)
                .detach()
                .to(device="cpu", dtype=torch.float64)
                .numpy()
            )
    except S106ProjectionError:
        raise
    except Exception as error:
        raise S106ProjectionError("S104 label-free projection failed") from error
    if (
        probabilities.shape != (phone_embeddings.shape[0],)
        or not np.isfinite(probabilities).all()
        or np.any((probabilities < 0.0) | (probabilities > 1.0))
    ):
        raise S106ProjectionError("S104 projected probability is invalid")
    return probabilities


def project_s105_label_free_layers(
    *,
    head: Any,
    phone_layers: Any,
    word_layers: Any,
    utterance_layers: Any,
    phone_to_word: Any,
    target_indices: Any,
    representation_attestation: Mapping[str, Any],
) -> np.ndarray:
    """Run one committed selected S105 hierarchy head without phone targets."""

    expected_attestation = {
        "representation": ("adapter_applied_all_encoder_layers_target_interval_pooled"),
        "adapter_applied": True,
        "all_encoder_layers": True,
        "target_interval_pooling": True,
        "label_free": True,
    }
    if dict(representation_attestation) != expected_attestation:
        raise S106ProjectionError(
            "S105 projection requires adapter-applied all-layer pooled representations"
        )
    try:
        import torch

        if (
            not isinstance(phone_layers, torch.Tensor)
            or phone_layers.ndim != 3
            or phone_layers.shape[1] <= 0
            or phone_layers.shape[1] > MAXIMUM_PROJECTION_BATCH_ROWS
            or not isinstance(target_indices, torch.Tensor)
            or target_indices.shape != (phone_layers.shape[1],)
            or target_indices.dtype != torch.long
            or not callable(getattr(head, "forward", None))
        ):
            raise S106ProjectionError("S105 label-free projection input differs")
        head.eval()
        with torch.inference_mode():
            output = head(
                phone_layers=phone_layers,
                word_layers=word_layers,
                utterance_layers=utterance_layers,
                phone_to_word=phone_to_word,
                target_indices=target_indices,
            )
            logits = getattr(output, "phone_logits", None)
            if not isinstance(logits, torch.Tensor):
                raise S106ProjectionError("S105 head output lacks phone logits")
            probabilities = (
                torch.sigmoid(logits)
                .detach()
                .to(device="cpu", dtype=torch.float64)
                .numpy()
            )
    except S106ProjectionError:
        raise
    except Exception as error:
        raise S106ProjectionError("S105 label-free projection failed") from error
    if (
        probabilities.shape != (phone_layers.shape[1],)
        or not np.isfinite(probabilities).all()
        or np.any((probabilities < 0.0) | (probabilities > 1.0))
    ):
        raise S106ProjectionError("S105 projected probability is invalid")
    return probabilities


def _validate_identifiers(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows or len(rows) > MAXIMUM_PROJECTION_BATCH_ROWS:
        raise S106ProjectionError("projection batch row count is outside the bound")
    seen: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        if set(row) != {
            "utterance_id_sha256",
            "speaker_id_sha256",
            "phone_index",
            "split",
        }:
            raise S106ProjectionError("projection identifier schema differs")
        utterance = _sha256(
            row.get("utterance_id_sha256"),
            f"projection row {index} utterance",
        )
        _sha256(
            row.get("speaker_id_sha256"),
            f"projection row {index} speaker",
        )
        phone_index = row.get("phone_index")
        if (
            isinstance(phone_index, bool)
            or not isinstance(phone_index, int)
            or phone_index < 0
            or row.get("split") != "stack"
        ):
            raise S106ProjectionError("projection row is not a valid stack row")
        key = (utterance, phone_index)
        if key in seen:
            raise S106ProjectionError("projection row key is duplicated")
        seen.add(key)


def project_b07_label_free_batch(
    *,
    seed: int,
    identifiers: Sequence[Mapping[str, Any]],
    embeddings: np.ndarray,
    categorical_rows: Mapping[str, Sequence[str | None]],
    explicit_numeric_rows: np.ndarray,
    acoustic_checkpoint: Mapping[str, Any],
    error_checkpoint: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Project one bounded stack batch from serialized B07 checkpoints.

    The API has no label argument.  Target labels are pronunciation targets,
    not realized-phone identities, and are already required by the acoustic
    posterior checkpoint.  The B07 dev prediction Parquet files are never
    accepted or opened.
    """

    validate_b07_projection_checkpoints(
        acoustic_checkpoint=acoustic_checkpoint,
        error_checkpoint=error_checkpoint,
        expected_seed=seed,
    )
    _validate_identifiers(identifiers)
    validate_label_free_projection_columns(
        (
            *identifiers[0].keys(),
            *categorical_rows.keys(),
            *B07_EXPLICIT_NUMERIC_COLUMNS,
            "embedding_mean",
        )
    )
    if tuple(categorical_rows) != B07_CATEGORICAL_COLUMNS:
        raise S106ProjectionError("B07 categorical projection order differs")
    row_count = len(identifiers)
    embedding_array = np.asarray(embeddings, dtype=np.float32)
    numeric_array = np.asarray(explicit_numeric_rows, dtype=np.float64)
    if (
        embedding_array.ndim != 2
        or embedding_array.shape[0] != row_count
        or embedding_array.shape[1] != 768
        or numeric_array.shape != (row_count, len(B07_EXPLICIT_NUMERIC_COLUMNS))
        or not np.isfinite(embedding_array).all()
        or not np.isfinite(numeric_array).all()
    ):
        raise S106ProjectionError("B07 projection array shape or finiteness differs")
    for name in B07_CATEGORICAL_COLUMNS:
        if len(categorical_rows[name]) != row_count:
            raise S106ProjectionError("B07 categorical row count differs")
    if (
        acoustic_checkpoint.get("schema_version")
        != "voice_coach.b07_gop_acoustic_checkpoint.v1"
        or error_checkpoint.get("schema_version")
        != "voice_coach.b07_gop_error_checkpoint.v1"
        or acoustic_checkpoint.get("stack_inference_sufficient") is not True
        or error_checkpoint.get("stack_inference_sufficient") is not True
    ):
        raise S106ProjectionError("B07 checkpoint is not stack-inference sufficient")

    from voice_coach.training.b07_gop_contract import GOP_NUMERIC_FEATURES
    from voice_coach.training.b07_gop_training import (
        predict_serialized_detector_rows,
        project_serialized_acoustic_checkpoint,
    )

    try:
        gop_features = project_serialized_acoustic_checkpoint(
            embeddings=embedding_array,
            target_labels=[str(value) for value in categorical_rows["target_label"]],
            document=acoustic_checkpoint,
        )
        if gop_features.shape != (row_count, len(GOP_NUMERIC_FEATURES)):
            raise S106ProjectionError("B07 acoustic projection shape differs")
        probabilities = predict_serialized_detector_rows(
            categorical_rows=categorical_rows,
            numeric_rows=np.column_stack((numeric_array, gop_features)),
            document=error_checkpoint,
        )
    except Exception as error:
        if isinstance(error, S106ProjectionError):
            raise
        raise S106ProjectionError("serialized B07 stack projection failed") from error
    probability_array = np.asarray(probabilities, dtype=np.float64)
    if (
        probability_array.shape != (row_count,)
        or not np.isfinite(probability_array).all()
        or np.any((probability_array < 0.0) | (probability_array > 1.0))
    ):
        raise S106ProjectionError("B07 projected probability is invalid")
    return tuple(
        {
            **dict(row),
            "probability": float(probability),
        }
        for row, probability in zip(identifiers, probability_array, strict=True)
    )


def validate_b07_projection_checkpoints(
    *,
    acoustic_checkpoint: Mapping[str, Any],
    error_checkpoint: Mapping[str, Any],
    expected_seed: int,
) -> Mapping[str, Any]:
    """Prove that the two serialized documents contain stack replay state."""

    if (
        isinstance(expected_seed, bool)
        or expected_seed not in SEEDS
        or acoustic_checkpoint.get("seed") != expected_seed
        or error_checkpoint.get("seed") != expected_seed
        or acoustic_checkpoint.get("schema_version")
        != "voice_coach.b07_gop_acoustic_checkpoint.v1"
        or acoustic_checkpoint.get("stack_inference_sufficient") is not True
        or error_checkpoint.get("schema_version")
        != "voice_coach.b07_gop_error_checkpoint.v1"
        or error_checkpoint.get("stack_inference_sufficient") is not True
        or not isinstance(acoustic_checkpoint.get("inventory"), list)
        or len(acoustic_checkpoint["inventory"]) != 38
        or not isinstance(error_checkpoint.get("feature_order"), list)
        or len(error_checkpoint["feature_order"]) != 10
        or "explicit_refit_control" not in error_checkpoint
    ):
        raise S106ProjectionError("B07 serialized checkpoint contract differs")
    forbidden = {
        key.casefold()
        for document in (acoustic_checkpoint, error_checkpoint)
        for key in document
        if key.casefold() in FORBIDDEN_PROJECTION_NAMES
    }
    if forbidden:
        raise S106ProjectionError("B07 checkpoint exposes a forbidden stack input")
    return {
        "status": "projection_ready",
        "label_argument_present": False,
        "dev_prediction_reused": False,
        "realized_phone_identity_used": False,
        "acoustic_schema_version": acoustic_checkpoint["schema_version"],
        "error_schema_version": error_checkpoint["schema_version"],
        "seed": expected_seed,
    }


def build_component_bundle_document(
    *,
    workspace_root: Path,
    execution_lock: Mapping[str, Any],
    execution_lock_record: Mapping[str, Any],
    stack_source_record: Mapping[str, Any],
    feature_identity_matrix_record: Mapping[str, Any],
    label_matrix_record: Mapping[str, Any],
    component_records: Mapping[str, Mapping[str, Mapping[str, Any]]],
    generator_commit_record: Mapping[str, Any],
    feature_freeze_receipt_record: Mapping[str, Any],
    producer_identity: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Build the exact signed v3 bundle document from 12 prediction records."""

    from voice_coach.training.wavlm_s106_fusion_contract import (
        S106ContractError,
        validate_predecessor_canonical_sources,
    )

    try:
        validate_predecessor_canonical_sources(
            workspace_root=workspace_root,
            execution_lock=execution_lock,
        )
    except S106ContractError as error:
        raise S106ProjectionError(
            "component bundle predecessor canonical-source binding differs"
        ) from error
    if tuple(component_records) != COMPONENTS:
        raise S106ProjectionError("component bundle order differs")
    if (
        execution_lock.get("schema_version") != EXECUTION_LOCK_SCHEMA_VERSION
        or execution_lock.get("status") != "ready"
    ):
        raise S106ProjectionError("execution-lock document is not ready v2")
    frozen = _mapping(execution_lock.get("frozen_components"), "frozen components")
    if frozen.get("order") != list(COMPONENTS):
        raise S106ProjectionError("execution-lock component order differs")
    normalized_components: dict[str, Any] = {}
    component_row_counts: dict[str, int] = {}
    for component in COMPONENTS:
        seeds = _mapping(component_records[component], f"{component} seeds")
        expected_seed_keys = tuple(f"seed_{seed}" for seed in SEEDS)
        if tuple(seeds) != expected_seed_keys:
            raise S106ProjectionError(f"{component} seed record order differs")
        frozen_component = _mapping(
            frozen.get(component),
            f"frozen component {component}",
        )
        normalized: dict[str, Any] = {}
        seed_row_counts: set[int] = set()
        for seed in SEEDS:
            key = f"seed_{seed}"
            record = _prediction_record(
                seeds[key],
                component=component,
                seed=seed,
                frozen_component=frozen_component,
                name=f"{component}.{key}",
            )
            seed_row_counts.add(int(record["row_count"]))
            normalized[key] = record
        if len(seed_row_counts) != 1:
            raise S106ProjectionError(f"{component} seed prediction row counts differ")
        component_row_counts[component] = next(iter(seed_row_counts))
        normalized_components[component] = normalized
    identity = _file_record(feature_identity_matrix_record, "feature identity matrix")
    labels = _file_record(label_matrix_record, "label matrix")
    identity_row_count = int(identity["row_count"])
    if int(labels["row_count"]) != identity_row_count or any(
        count != identity_row_count for count in component_row_counts.values()
    ):
        raise S106ProjectionError("bundle row counts differ from the stack universe")
    lock_record = _mapping(execution_lock_record, "execution lock record")
    stack_record = _mapping(stack_source_record, "stack source record")
    for name, record in (
        ("execution lock", lock_record),
        ("stack source", stack_record),
    ):
        if set(record) != {"path", "sha256"}:
            raise S106ProjectionError(f"{name} record schema differs")
        _relative_path(record.get("path"), f"{name}.path")
        _sha256(record.get("sha256"), f"{name}.sha256")
    expected_lock_sha256 = hashlib.sha256(
        _pretty_canonical_json_bytes(execution_lock)
    ).hexdigest()
    locked_stack = _mapping(
        execution_lock.get("stack_source"),
        "execution lock stack source",
    )
    if (
        lock_record.get("path") != EXECUTION_LOCK_RELATIVE_PATH
        or lock_record.get("sha256") != expected_lock_sha256
        or stack_record.get("path") != locked_stack.get("path")
        or stack_record.get("sha256") != locked_stack.get("sha256")
    ):
        raise S106ProjectionError(
            "bundle source records do not bind the supplied execution lock"
        )
    generator_commit = _immutable_file_record(
        generator_commit_record,
        name="component generator commit",
        expected_path=(
            "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1/"
            "component_input_commit.json"
        ),
    )
    feature_freeze_receipt = _immutable_file_record(
        feature_freeze_receipt_record,
        name="component feature-freeze receipt",
        expected_path=(
            "artifacts/experiments/aihub71490_wavlm_s106_stack_fusion_v1/"
            "component_feature_freeze_receipt.json"
        ),
    )
    production_identity = _production_producer_identity(producer_identity)
    document: dict[str, Any] = {
        "schema_version": COMPONENT_BUNDLE_SCHEMA_VERSION,
        "status": "committed",
        "generator_commit": generator_commit,
        "feature_freeze_receipt": feature_freeze_receipt,
        "producer_identity": production_identity,
        "execution_lock": dict(lock_record),
        "stack_source": dict(stack_record),
        "frozen_components": dict(frozen),
        "feature_identity_matrix": identity,
        "label_matrix": labels,
        "components": normalized_components,
        "projection_audit": {
            "component_order": list(COMPONENTS),
            "seed_order": list(SEEDS),
            "prediction_file_count": len(COMPONENTS) * len(SEEDS),
            "seed_aggregation_occurs_before_join": True,
            "feature_identity_built_before_label_open": True,
            "label_content_and_computed_hash_deferred_until_feature_freeze": True,
            "feature_builder_received_labels": False,
            "alignment_confidence_source": "absent_from_modeling_manifest",
            "alignment_confidence_non_null_allowed": False,
            "alignment_confidence_missing_indicator_forced": True,
            "b07_dev_predictions_reused": False,
            "realized_phone_identity_used": False,
            "binary_error_label_used_by_projectors": False,
        },
    }
    document["binding_sha256"] = _binding(document)
    return document


def production_projection_blockers(workspace_root: Path) -> tuple[str, ...]:
    """Return current external blockers without opening stack or environment files."""

    blockers: list[str] = []
    for stage, relative in (
        (
            "s104",
            "artifacts/experiments/aihub71490_wavlm_s104_adapter_v2_current_code/commit.json",
        ),
        (
            "s105",
            "artifacts/experiments/aihub71490_wavlm_s105_hierarchy_v5_stream_cardinality/commit.json",
        ),
    ):
        if not (workspace_root / relative).is_file():
            blockers.append(f"{stage}_validated_commit_absent")
    return tuple(blockers)


__all__ = [
    "B07_CATEGORICAL_COLUMNS",
    "B07_EXPLICIT_NUMERIC_COLUMNS",
    "COMPONENTS",
    "COMPONENT_BUNDLE_SCHEMA_VERSION",
    "COMPONENT_PREDICTION_ROOT",
    "EXECUTION_LOCK_RELATIVE_PATH",
    "EXECUTION_LOCK_SCHEMA_VERSION",
    "FORBIDDEN_PROJECTION_FRAGMENTS",
    "FORBIDDEN_PROJECTION_NAMES",
    "MAXIMUM_PROJECTION_BATCH_ROWS",
    "PREDICTION_COLUMNS",
    "PREDICTION_RECORD_SCHEMA_VERSION",
    "PROJECTION_REPRESENTATIONS",
    "SEEDS",
    "S106ProjectionError",
    "build_component_bundle_document",
    "project_explicit_label_free_features",
    "project_s104_label_free_embeddings",
    "project_s105_label_free_layers",
    "production_projection_blockers",
    "project_b07_label_free_batch",
    "validate_b07_projection_checkpoints",
    "validate_label_free_projection_columns",
]
