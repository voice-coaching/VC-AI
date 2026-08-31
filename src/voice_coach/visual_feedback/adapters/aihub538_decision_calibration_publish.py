"""Exclusive publisher for the AI Hub 538 decision-calibration bundle."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from ..models.aihub538_decision_calibration import (
    AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION,
    AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION,
    Aihub538DecisionCalibrationFailureArtifact,
    Aihub538DecisionCalibrationFailureArtifactV2,
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from ..models.aihub538_multishard_admission import (
    Aihub538MultiShardAdmissionReceipt,
)
from ..models.aihub538_label_selection_authorization import (
    require_aihub538_frozen_source_authorization_sha256,
)
from ..models.aihub538_nonlocked_shards import AIHUB538_TS174_TL174_SHARD
from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    Aihub538TrainingAdmissionReceipt,
)
from ..models.integrity import canonical_json_bytes
from ..models.runtime_policy_artifact import RuntimeDecisionPolicyEvidence
from ..services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
    Aihub538DecisionCalibrationResult,
)
from .aihub538_decision_calibration_reload import (
    fresh_process_reload_aihub538_calibration,
)


AIHUB538_CALIBRATION_OUTPUT_NAMES = {
    "video_qc": "aihub538_ts174_tl174_video_qc_decision_policy_v1.json",
    "av_sync": "aihub538_ts174_tl174_av_sync_decision_policy_v1.json",
    "calibration": "aihub538_ts174_tl174_decision_calibration_evidence_v1.json",
    "reload": "aihub538_ts174_tl174_decision_calibration_reload_receipt_v1.json",
    "evidence": "aihub538_ts174_tl174_decision_policy_evidence_v1.json",
}
AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES = {
    "video_qc": (
        "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
        "video_qc_decision_policy_v1.json"
    ),
    "av_sync": (
        "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
        "av_sync_decision_policy_v1.json"
    ),
    "calibration": (
        "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
        "decision_calibration_evidence_v2.json"
    ),
    "reload": (
        "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
        "decision_calibration_reload_receipt_v1.json"
    ),
    "evidence": (
        "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
        "decision_policy_evidence_v2.json"
    ),
}
AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME = (
    "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
    "decision_calibration_failure_v1.json"
)
AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME = (
    "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
    "decision_calibration_failure_v2.json"
)
# Current publication name.  The explicit v1 constant remains load-compatible.
AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME = (
    AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME
)
AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES = (
    AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME,
    AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME,
)
_MAXIMUM_FAILURE_ARTIFACT_BYTES = 256 * 1024


def _pending_failure_name(output_name: str) -> str:
    return f".{output_name}.pending"


def _failure_output_name(
    failure_artifact: (
        Aihub538DecisionCalibrationFailureArtifact
        | Aihub538DecisionCalibrationFailureArtifactV2
    ),
) -> str:
    if type(failure_artifact) is Aihub538DecisionCalibrationFailureArtifact:
        return AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME
    if type(failure_artifact) is Aihub538DecisionCalibrationFailureArtifactV2:
        return AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME
    raise Aihub538DecisionCalibrationError(
        "aihub538_calibration_failure_publication_scope_mismatch"
    )


def publish_aihub538_decision_calibration_failure(
    *,
    output_dir: Path,
    failure_artifact: (
        Aihub538DecisionCalibrationFailureArtifact
        | Aihub538DecisionCalibrationFailureArtifactV2
    ),
) -> Path:
    """Atomically publish exactly one non-promotable aggregate failure file."""

    output_name = _failure_output_name(failure_artifact)
    pending_name = _pending_failure_name(output_name)
    payload = canonical_json_bytes(failure_artifact.to_json_object()) + b"\n"
    if len(payload) > _MAXIMUM_FAILURE_ARTIFACT_BYTES:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_artifact_too_large"
        )
    directory_descriptor: int | None = None
    artifact_identity: tuple[int, int] | None = None
    try:
        directory_descriptor = os.open(
            output_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_status = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_status.st_mode)
            or directory_status.st_uid != os.getuid()
            or directory_status.st_mode & 0o022
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_failure_output_directory_unsafe"
            )
        _acquire_output_directory_lock(
            directory_descriptor,
            reason_code=(
                "aihub538_calibration_failure_output_lock_unavailable"
            ),
        )
        _require_output_directory_path_identity(
            output_dir,
            directory_status,
            reason_code=(
                "aihub538_calibration_failure_output_directory_changed"
            ),
        )
        if any(
            _entry_exists_at(directory_descriptor, name)
            or _entry_exists_at(
                directory_descriptor,
                _pending_failure_name(name),
            )
            for name in AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_failure_output_already_exists"
            )
        if any(
            _entry_exists_at(directory_descriptor, name)
            for name in AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES.values()
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_failure_conflicts_with_success_output"
            )
        artifact_identity = _write_pending_failure_at(
            directory_descriptor,
            pending_name,
            payload,
        )
        _require_entry_identity_at(
            directory_descriptor,
            pending_name,
            artifact_identity,
            expected_link_count=1,
        )
        reloaded = _load_failure_at(
            directory_descriptor,
            pending_name,
            expected_output_name=output_name,
        )
        if reloaded != failure_artifact:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_failure_strict_reload_failed"
            )
        _require_entry_identity_at(
            directory_descriptor,
            pending_name,
            artifact_identity,
            expected_link_count=1,
        )
        os.link(
            pending_name,
            output_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(
            pending_name,
            dir_fd=directory_descriptor,
        )
        _require_entry_identity_at(
            directory_descriptor,
            output_name,
            artifact_identity,
            expected_link_count=1,
        )
        os.fsync(directory_descriptor)
        reloaded = _load_failure_at(
            directory_descriptor,
            output_name,
            expected_output_name=output_name,
        )
        if (
            reloaded != failure_artifact
            or any(
                _entry_exists_at(directory_descriptor, name)
                for name in AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES.values()
            )
            or any(
                (name != output_name and _entry_exists_at(directory_descriptor, name))
                or _entry_exists_at(
                    directory_descriptor,
                    _pending_failure_name(name),
                )
                for name in AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES
            )
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_failure_strict_reload_failed"
            )
        _require_output_directory_path_identity(
            output_dir,
            directory_status,
            reason_code=(
                "aihub538_calibration_failure_output_directory_changed"
            ),
        )
    except Aihub538DecisionCalibrationError:
        if directory_descriptor is not None and artifact_identity is not None:
            _unlink_same_inode_at(
                directory_descriptor,
                output_name,
                artifact_identity,
            )
            _unlink_same_inode_at(
                directory_descriptor,
                pending_name,
                artifact_identity,
            )
            try:
                os.fsync(directory_descriptor)
            except OSError:
                pass
        raise
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        if directory_descriptor is not None and artifact_identity is not None:
            _unlink_same_inode_at(
                directory_descriptor,
                output_name,
                artifact_identity,
            )
            _unlink_same_inode_at(
                directory_descriptor,
                pending_name,
                artifact_identity,
            )
            try:
                os.fsync(directory_descriptor)
            except OSError:
                pass
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_exclusive_publish_failed"
        ) from None
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return output_dir / output_name


def load_aihub538_decision_calibration_failure_artifact(
    path: Path,
) -> (
    Aihub538DecisionCalibrationFailureArtifact
    | Aihub538DecisionCalibrationFailureArtifactV2
):
    """Strictly reload one owner-only, regular, single-link failure artifact."""

    directory_descriptor: int | None = None
    try:
        if path.name not in AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES:
            raise ValueError("failure artifact filename differs")
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        return _load_failure_at(directory_descriptor, path.name)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_strict_reload_failed"
        ) from None
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _write_pending_failure_at(
    directory_descriptor: int,
    pending_name: str,
    payload: bytes,
) -> tuple[int, int]:
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            pending_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, 0o400)
        before = os.fstat(descriptor)
        identity = before.st_dev, before.st_ino
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
        ):
            raise OSError
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != identity
            or after.st_size != len(payload)
            or stat.S_IMODE(after.st_mode) != 0o400
            or after.st_nlink != 1
        ):
            raise OSError
        return identity
    except OSError:
        if identity is None and descriptor is not None:
            try:
                current = os.fstat(descriptor)
                identity = current.st_dev, current.st_ino
            except OSError:
                pass
        if identity is not None:
            _unlink_same_inode_at(
                directory_descriptor,
                pending_name,
                identity,
            )
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_failure_at(
    directory_descriptor: int,
    name: str,
    *,
    expected_output_name: str | None = None,
) -> (
    Aihub538DecisionCalibrationFailureArtifact
    | Aihub538DecisionCalibrationFailureArtifactV2
):
    output_name = expected_output_name or name
    if output_name not in AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES:
        raise ValueError("failure artifact filename differs")
    descriptor = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_descriptor,
    )
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > _MAXIMUM_FAILURE_ARTIFACT_BYTES
        ):
            raise ValueError("unsafe failure artifact")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ValueError("truncated failure artifact")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("oversized failure artifact")
        after = os.fstat(descriptor)
        identity_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            for field in identity_fields
        ):
            raise ValueError("failure artifact changed during reload")
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    document = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(document, dict):
        raise ValueError("failure artifact must be an object")
    expected_schema, artifact_type = {
        AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME: (
            AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_SCHEMA_VERSION,
            Aihub538DecisionCalibrationFailureArtifact,
        ),
        AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME: (
            AIHUB538_MULTI_DECISION_CALIBRATION_FAILURE_V2_SCHEMA_VERSION,
            Aihub538DecisionCalibrationFailureArtifactV2,
        ),
    }[output_name]
    if document.get("schema_version") != expected_schema:
        raise ValueError("failure artifact filename and schema differ")
    artifact = artifact_type.from_json_object(document)
    if payload != canonical_json_bytes(artifact.to_json_object()) + b"\n":
        raise ValueError("failure artifact is not canonical")
    return artifact


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _entry_exists_at(directory_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _require_entry_identity_at(
    directory_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
    *,
    expected_link_count: int,
) -> None:
    current = os.stat(
        name,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(current.st_mode)
        or (current.st_dev, current.st_ino) != expected_identity
        or current.st_uid != os.getuid()
        or stat.S_IMODE(current.st_mode) != 0o400
        or current.st_nlink != expected_link_count
    ):
        raise ValueError("failure artifact inode identity differs")


def _unlink_same_inode_at(
    directory_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        current = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (current.st_dev, current.st_ino) == expected_identity:
            os.unlink(name, dir_fd=directory_descriptor)
    except OSError:
        pass


def publish_aihub538_decision_calibration_bundle(
    *,
    output_dir: Path,
    workspace_root: Path,
    repository_root: Path,
    result: Aihub538DecisionCalibrationResult,
    admission_receipt: (
        Aihub538TrainingAdmissionReceipt | Aihub538MultiShardAdmissionReceipt
    ),
) -> tuple[RuntimeDecisionPolicyEvidence, tuple[Path, ...]]:
    """Fresh-reload, then publish five 0400 artifacts with O_EXCL and fsync."""

    output_names = _validate_publication_scope(
        result=result,
        admission_receipt=admission_receipt,
    )
    targets = {
        key: output_dir / name
        for key, name in output_names.items()
    }
    directory_descriptor: int | None = None
    try:
        directory_descriptor = os.open(
            output_dir,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_status = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_status.st_mode)
            or directory_status.st_uid != os.getuid()
            or directory_status.st_mode & 0o022
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_output_directory_unsafe"
            )
        _acquire_output_directory_lock(
            directory_descriptor,
            reason_code="aihub538_calibration_output_lock_unavailable",
        )
        _require_output_directory_path_identity(
            output_dir,
            directory_status,
            reason_code="aihub538_calibration_output_directory_changed",
        )
        if (
            output_names == AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES
            and any(
                _entry_exists_at(directory_descriptor, name)
                or _entry_exists_at(
                    directory_descriptor,
                    _pending_failure_name(name),
                )
                for name in AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES
            )
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_success_conflicts_with_failure_output"
            )
        if any(
            _entry_exists_at(directory_descriptor, name)
            for name in output_names.values()
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_output_already_exists"
            )
        with tempfile.TemporaryDirectory(
            prefix="reload-stage-", dir=workspace_root
        ) as stage_name:
            stage = Path(stage_name)
            os.chmod(stage, 0o700)
            stage_paths = {
                "video_qc": stage / output_names["video_qc"],
                "av_sync": stage / output_names["av_sync"],
                "calibration": stage / output_names["calibration"],
            }
            _write_exclusive(
                stage_paths["video_qc"],
                result.video_qc_decision_policy.to_json_object(),
            )
            _write_exclusive(
                stage_paths["av_sync"],
                result.av_sync_decision_policy.to_json_object(),
            )
            _write_exclusive(
                stage_paths["calibration"],
                result.calibration_evidence.to_json_object(),
            )
            reload_receipt = fresh_process_reload_aihub538_calibration(
                video_qc_policy_path=stage_paths["video_qc"],
                av_sync_policy_path=stage_paths["av_sync"],
                calibration_evidence_path=stage_paths["calibration"],
                repository_root=repository_root,
            )
            calibration_evidence = result.calibration_evidence
            is_multi = type(admission_receipt) is Aihub538MultiShardAdmissionReceipt
            decision_evidence = (
                RuntimeDecisionPolicyEvidence._issue_from_validated_actual_evaluation(
                    source_manifest_sha256=(
                        result.calibration_evidence.source_manifest_sha256
                    ),
                    admission_receipt_sha256=admission_receipt.receipt_sha256,
                    evaluation_protocol_sha256=(
                        result.calibration_evidence.evaluation_protocol_sha256
                    ),
                    evaluator_code_sha256=(
                        result.calibration_evidence.evaluator_code_sha256
                    ),
                    video_qc_policy=result.video_qc_decision_policy,
                    av_sync_policy=result.av_sync_decision_policy,
                    admitted_item_count=(
                        calibration_evidence.admitted_item_count
                        if is_multi
                        else calibration_evidence.calibration_selected_count
                    ),
                    evaluated_item_count=(
                        calibration_evidence.calibration_selected_count
                    ),
                    actor_overlap_count=(
                        result.calibration_evidence.actor_overlap_count
                    ),
                    attempt_overlap_count=(
                        result.calibration_evidence.attempt_overlap_count
                    ),
                    qc_decision_lineage_valid_item_count=(
                        calibration_evidence.qc_decision_lineage_valid_item_count
                    ),
                    sync_decision_lineage_valid_item_count=(
                        calibration_evidence.sync_decision_lineage_valid_item_count
                    ),
                    fresh_process_reload_receipt_sha256=(
                        reload_receipt.receipt_sha256
                    ),
                    source_partitions=(
                        result.calibration_evidence.source_partitions
                    ),
                    eligible_item_count=(
                        calibration_evidence.eligible_item_count
                        if is_multi
                        else 0
                    ),
                    metadata_excluded_item_count=(
                        calibration_evidence.metadata_excluded_item_count
                        if is_multi
                        else 0
                    ),
                    development_selected_count=(
                        calibration_evidence.development_selected_count
                        if is_multi
                        else 0
                    ),
                    calibration_selected_count=(
                        calibration_evidence.calibration_selected_count
                        if is_multi
                        else 0
                    ),
                )
            )
            documents = {
                "video_qc": result.video_qc_decision_policy.to_json_object(),
                "av_sync": result.av_sync_decision_policy.to_json_object(),
                "calibration": result.calibration_evidence.to_json_object(),
                "reload": reload_receipt.to_json_object(),
                "evidence": decision_evidence.to_json_object(),
            }

        created: list[tuple[str, tuple[int, int]]] = []
        try:
            # The final promotion prerequisite is deliberately written last.
            for key in (
                "video_qc",
                "av_sync",
                "calibration",
                "reload",
                "evidence",
            ):
                identity = _write_exclusive_document_at(
                    directory_descriptor,
                    output_names[key],
                    documents[key],
                )
                created.append((output_names[key], identity))
            _require_output_directory_path_identity(
                output_dir,
                directory_status,
                reason_code="aihub538_calibration_output_directory_changed",
            )
            os.fsync(directory_descriptor)
            _require_output_directory_path_identity(
                output_dir,
                directory_status,
                reason_code="aihub538_calibration_output_directory_changed",
            )
        except BaseException:
            for name, identity in reversed(created):
                _unlink_same_inode_at(
                    directory_descriptor,
                    name,
                    identity,
                )
            try:
                os.fsync(directory_descriptor)
            except OSError:
                pass
            raise
        return decision_evidence, tuple(
            targets[key]
            for key in ("video_qc", "av_sync", "calibration", "reload", "evidence")
        )
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _acquire_output_directory_lock(
    directory_descriptor: int,
    *,
    reason_code: str,
) -> None:
    """Acquire the shared publication lock without adding a persistent file."""

    try:
        fcntl.flock(
            directory_descriptor,
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except (OSError, ValueError):
        raise Aihub538DecisionCalibrationError(reason_code) from None


def _require_output_directory_path_identity(
    output_dir: Path,
    expected: os.stat_result,
    *,
    reason_code: str,
) -> None:
    """Bind the user-facing path to the same safe inode held by the dirfd."""

    try:
        current = os.stat(output_dir, follow_symlinks=False)
    except OSError:
        raise Aihub538DecisionCalibrationError(reason_code) from None
    if (
        not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
    ):
        raise Aihub538DecisionCalibrationError(reason_code)


def _write_exclusive_document_at(
    directory_descriptor: int,
    name: str,
    document: object,
) -> tuple[int, int]:
    """Publish one frozen success document relative to the locked dirfd."""

    payload = canonical_json_bytes(document) + b"\n"
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=directory_descriptor,
        )
        os.fchmod(descriptor, 0o400)
        before = os.fstat(descriptor)
        identity = before.st_dev, before.st_ino
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_nlink != 1
        ):
            raise OSError
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino) != identity
            or after.st_size != len(payload)
            or stat.S_IMODE(after.st_mode) != 0o400
            or after.st_nlink != 1
        ):
            raise OSError
        return identity
    except OSError:
        if identity is None and descriptor is not None:
            try:
                current = os.fstat(descriptor)
                identity = current.st_dev, current.st_ino
            except OSError:
                pass
        if identity is not None:
            _unlink_same_inode_at(directory_descriptor, name, identity)
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_exclusive_publish_failed"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_publication_scope(
    *,
    result: Aihub538DecisionCalibrationResult,
    admission_receipt: (
        Aihub538TrainingAdmissionReceipt | Aihub538MultiShardAdmissionReceipt
    ),
) -> dict[str, str]:
    """Bind one evidence schema to its exact admission and frozen protocol."""

    if type(result) is not Aihub538DecisionCalibrationResult or type(
        admission_receipt
    ) not in {
        Aihub538TrainingAdmissionReceipt,
        Aihub538MultiShardAdmissionReceipt,
    }:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_publication_scope_mismatch"
        )
    evidence = result.calibration_evidence
    is_multi_evidence = (
        evidence.schema_version
        == AIHUB538_MULTI_DECISION_CALIBRATION_SCHEMA_VERSION
    )
    try:
        admission_receipt._validate()
    except (TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_publication_scope_mismatch"
        ) from None

    if is_multi_evidence:
        if type(admission_receipt) is not Aihub538MultiShardAdmissionReceipt:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_publication_scope_mismatch"
            )
        if (
            evidence.source_partitions
            != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.source_partitions
            or evidence.evaluation_protocol_sha256
            != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
            or admission_receipt.frozen_calibration_protocol_sha256
            != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_publication_scope_mismatch"
            )
        try:
            require_aihub538_frozen_source_authorization_sha256(
                admission_receipt.capacity_audit_receipt_sha256
            )
        except ValueError:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_publication_scope_mismatch"
            ) from None
        output_names = AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES
    else:
        if (
            evidence.schema_version
            != AIHUB538_DECISION_CALIBRATION_SCHEMA_VERSION
            or evidence.source_partitions != ("TS174", "TL174")
            or type(admission_receipt) is not Aihub538TrainingAdmissionReceipt
            or admission_receipt.shard_id
            != AIHUB538_TS174_TL174_SHARD.shard_id
            or admission_receipt.dataset_partition
            != AIHUB538_TS174_TL174_SHARD.dataset_partition
            or admission_receipt.source_role_component
            != AIHUB538_TS174_TL174_SHARD.source_role_component
            or admission_receipt.label_role_component
            != AIHUB538_TS174_TL174_SHARD.label_role_component
            or admission_receipt.ordered_file_keys
            != AIHUB538_TS174_TL174_SHARD.ordered_file_keys
            or admission_receipt.sample_selection_rule
            != AIHUB538_TS174_TL174_SHARD.sample_selection_rule
            or admission_receipt.admission_policy_sha256
            not in {
                AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
                AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
            }
            or evidence.evaluation_protocol_sha256
            != DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_publication_scope_mismatch"
            )
        output_names = AIHUB538_CALIBRATION_OUTPUT_NAMES

    if (
        evidence.admission_receipt_sha256 != admission_receipt.receipt_sha256
        or evidence.identity_namespace_commitment_sha256
        != admission_receipt.identity_namespace_commitment_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_publication_scope_mismatch"
        )
    return output_names


def _write_exclusive(path: Path, document: object) -> None:
    payload = canonical_json_bytes(document) + b"\n"
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        created = True
        os.fchmod(descriptor, 0o400)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError
            offset += written
        os.fsync(descriptor)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_exclusive_publish_failed"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = [
    "AIHUB538_CALIBRATION_OUTPUT_NAMES",
    "AIHUB538_MULTI_CALIBRATION_OUTPUT_NAMES",
    "AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAME",
    "AIHUB538_MULTI_CALIBRATION_FAILURE_OUTPUT_NAMES",
    "AIHUB538_MULTI_CALIBRATION_FAILURE_V1_OUTPUT_NAME",
    "AIHUB538_MULTI_CALIBRATION_FAILURE_V2_OUTPUT_NAME",
    "load_aihub538_decision_calibration_failure_artifact",
    "publish_aihub538_decision_calibration_failure",
    "publish_aihub538_decision_calibration_bundle",
]
