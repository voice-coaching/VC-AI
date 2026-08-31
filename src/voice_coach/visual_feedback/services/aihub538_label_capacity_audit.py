"""Transactional completed-download pipeline for the TL175 label-only audit."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

from ..adapters.aihub538_identity_key_store import (
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key_descriptor,
)
from ..adapters.aihub538_label_capacity import (
    Aihub538LabelCapacityAdapter,
    Aihub538LabelCapacityError,
)
from ..adapters.aihub538_split_tar_archive import (
    NamedSplitTarPart,
    merge_split_tar_parts,
    repack_verified_inner_tar_to_zip,
)
from ..adapters.aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    NamedSplitZipPart,
    extract_verified_tar_descriptor,
    merge_split_zip_parts,
    validate_zip_descriptor,
    verify_aihub538_nonlocked_shard_download,
)
from ..models.aihub538_cumulative_label_capacity import (
    Aihub538CumulativeLabelCapacityAuditReceipt,
)
from ..models.aihub538_label_selection_authorization import (
    AIHUB538_LABEL_SELECTION_AUTHORIZATION_SCHEMA_VERSION,
    Aihub538LabelSelectionAuthorizationReceipt,
)
from ..models.aihub538_label_capacity import Aihub538LabelCapacityAuditReceipt
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.integrity import sha256_document
from ..adapters.aihub538_admitted_archive_binding import (
    assert_aihub538_descriptor_identity,
    assert_aihub538_stable_zip_descriptor,
    capture_aihub538_descriptor_identity,
    load_aihub538_training_admission_receipt_descriptor,
    resolve_aihub538_candidate_role_descriptors,
    validate_aihub538_stable_zip_descriptor,
)
from ..models.aihub538_training_evidence import Aihub538TrainingAdmissionReceipt
from .aihub538_training_pipeline import (
    _create_run_root,
    _open_private_directory_at,
    _persist_json_receipt,
    _remove_created_run_root,
    open_completed_aihub538_download_directory,
)
from .aihub538_decision_calibration import Aihub538DecisionCalibrationError


_SPLIT_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.(?P<kind>zip|tar))\.part(?P<offset>[0-9]+)$"
)
_CAPACITY_RECEIPT_NAME = "50-label-capacity-audit.json"
_BASELINE_COMPLETION_KEYS = {
    "aggregate_only",
    "completion_receipt_sha256",
    "dataset_admission",
    "dataset_id",
    "dataset_partition",
    "download_verification",
    "existing_output_overwrite_allowed",
    "failure_cleanup_policy",
    "fsync_completed",
    "inner_tar_repack",
    "locked_partition_accessed",
    "merged_archive_mode",
    "metadata_smoke",
    "outer_tar_extraction",
    "output_directory_mode",
    "output_directory_was_new",
    "partial_download_accessed",
    "participant_identifiers_serialized",
    "promotion_authorized",
    "raw_member_names_serialized",
    "receipt_mode",
    "schema_version",
    "semantic_role_classification",
    "source_paths_serialized",
    "split_archive_merge",
    "split_zip_merge",
    "status",
    "transcripts_serialized",
    "zip_validation",
}
_HISTORICAL_BASELINE_COMPLETION_KEYS = _BASELINE_COMPLETION_KEYS - {
    "split_archive_merge",
    "inner_tar_repack",
}
_LABEL_CAPACITY_COMPLETION_KEYS = {
    "aggregate_only",
    "baseline_admission_receipt_sha256",
    "baseline_completion_receipt_sha256",
    "capacity_audit_receipt_sha256",
    "capacity_outcome_persisted",
    "download_verification_sha256",
    "label_zip_content_root_sha256",
    "locked_partition_accessed",
    "outer_content_root_sha256",
    "partial_download_accessed",
    "promotion_authorized",
    "receipt_sha256",
    "schema_version",
    "source_download_authorized",
    "source_partition_accessed",
    "status",
    "ts175_pair_admission_precondition_satisfied",
}


class Aihub538LabelCapacityPipelineError(RuntimeError):
    """Aggregate-only failure that never includes a restricted source value."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class _SingleSplitGroup:
    kind: str
    zip_parts: tuple[NamedSplitZipPart, ...] = ()
    tar_parts: tuple[NamedSplitTarPart, ...] = ()


@dataclass(frozen=True, slots=True)
class _BaselineAdmission:
    label_zip_descriptor: int
    admission_receipt: Aihub538TrainingAdmissionReceipt
    completion_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class _PriorPrefixAdmission:
    label_zip_descriptor: int
    capacity_receipt: Aihub538LabelCapacityAuditReceipt
    completion_receipt_sha256: str


class Aihub538LabelCapacityAuditPipeline:
    """Verify 54726, rebuild one label ZIP, audit capacity, publish receipts."""

    def __init__(
        self,
        *,
        audit_shard_spec: Aihub538NonlockedShardSpec = (
            AIHUB538_TL175_CAPACITY_AUDIT_SHARD
        ),
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        try:
            self._audit_shard_spec = require_aihub538_nonlocked_shard_spec(
                audit_shard_spec,
                require_label_audit=True,
            )
        except ValueError as error:
            raise Aihub538LabelCapacityPipelineError(str(error)) from None
        if self._audit_shard_spec not in (
            AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[:2]
        ):
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_capacity_prefix_stage_not_implemented"
            )
        self._archive_policy = archive_policy
        self._adapter = Aihub538LabelCapacityAdapter(
            archive_policy=archive_policy,
            shard_spec=self._audit_shard_spec,
        )
        self._baseline_adapter = Aihub538LabelCapacityAdapter(
            archive_policy=archive_policy,
            shard_spec=AIHUB538_TS174_TL174_SHARD,
        )
        self._tl175_adapter = Aihub538LabelCapacityAdapter(
            archive_policy=archive_policy,
            shard_spec=AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
        )

    def run_from_completed_directory(
        self,
        *,
        input_directory: Path,
        baseline_admission_directory: Path,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
        prior_prefix_audit_directory: Path | None = None,
    ) -> (
        Aihub538LabelCapacityAuditReceipt
        | Aihub538LabelSelectionAuthorizationReceipt
    ):
        """Open only completed names; partial/source/locked paths are absent."""

        with ExitStack() as stack:
            baseline = stack.enter_context(
                open_aihub538_ts174_baseline_admission_directory(
                    baseline_admission_directory,
                    policy=self._archive_policy,
                )
            )
            prior = (
                stack.enter_context(
                    open_aihub538_prior_label_capacity_audit_directory(
                        prior_prefix_audit_directory,
                        policy=self._archive_policy,
                    )
                )
                if prior_prefix_audit_directory is not None
                else None
            )
            with open_completed_aihub538_download_directory(input_directory) as opened:
                archive_descriptor, receipt_descriptor = opened
                return self.run(
                    archive_descriptor=archive_descriptor,
                    receipt_descriptor=receipt_descriptor,
                    baseline_label_zip_descriptor=(
                        baseline.label_zip_descriptor
                    ),
                    baseline_admission_receipt=(
                        baseline.admission_receipt
                    ),
                    baseline_completion_receipt_sha256=(
                        baseline.completion_receipt_sha256
                    ),
                    identity_key_descriptor=identity_key_descriptor,
                    output_directory=output_directory,
                    dataset_access_permission_confirmed=(
                        dataset_access_permission_confirmed
                    ),
                    prior_prefix_admission=prior,
                )

    def run(
        self,
        *,
        archive_descriptor: int,
        receipt_descriptor: int,
        baseline_label_zip_descriptor: int,
        baseline_admission_receipt: Aihub538TrainingAdmissionReceipt,
        baseline_completion_receipt_sha256: str,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
        prior_prefix_admission: _PriorPrefixAdmission | None = None,
    ) -> (
        Aihub538LabelCapacityAuditReceipt
        | Aihub538LabelSelectionAuthorizationReceipt
    ):
        if dataset_access_permission_confirmed is not True:
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        if (
            baseline_admission_receipt.shard_id
            != AIHUB538_TS174_TL174_SHARD.shard_id
        ):
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_capacity_baseline_invalid"
            )
        is_tl175 = (
            self._audit_shard_spec == AIHUB538_TL175_CAPACITY_AUDIT_SHARD
        )
        if is_tl175 != (prior_prefix_admission is None):
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_capacity_prior_prefix_binding_invalid"
            )
        try:
            identity = load_aihub538_identity_key_descriptor(
                identity_key_descriptor
            )
            download = verify_aihub538_nonlocked_shard_download(
                archive_descriptor=archive_descriptor,
                receipt_descriptor=receipt_descriptor,
                shard_spec=self._audit_shard_spec,
                policy=self._archive_policy,
            )
        except (Aihub538ArchiveError, Aihub538IdentityKeyStoreError) as error:
            raise Aihub538LabelCapacityPipelineError(error.reason_code) from None

        root_descriptor: int | None = None
        parent_descriptor: int | None = None
        root_name: str | None = None
        part_descriptors: list[int] = []
        label_descriptor: int | None = None
        try:
            root_descriptor, parent_descriptor, root_name = _create_run_root(
                output_directory
            )
            _persist_json_receipt(
                root_descriptor,
                "00-download-verification.json",
                download.to_json_object(),
            )
            tar_receipt = extract_verified_tar_descriptor(
                archive_descriptor=archive_descriptor,
                destination=output_directory / "outer",
                policy=self._archive_policy,
            )
            if tar_receipt.archive_sha256 != download.archive_sha256:
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_outer_lineage_mismatch"
                )
            _persist_json_receipt(
                root_descriptor,
                "10-outer-tar-extraction.json",
                tar_receipt.to_json_object(),
            )
            outer = _open_private_directory_at(root_descriptor, "outer")
            try:
                group = _discover_one_split_group(
                    outer,
                    policy=self._archive_policy,
                )
            finally:
                os.close(outer)
            part_descriptors.extend(
                part.descriptor for part in group.zip_parts + group.tar_parts
            )
            if group.kind == "zip":
                merge_receipt = merge_split_zip_parts(
                    parts=group.zip_parts,
                    destination=output_directory / "label.zip",
                    policy=self._archive_policy,
                )
                repack_receipt = None
            else:
                merge_receipt = merge_split_tar_parts(
                    parts=group.tar_parts,
                    destination=output_directory / "label.tar",
                    policy=self._archive_policy,
                )
                merged_tar = os.open(
                    "label.tar",
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=root_descriptor,
                )
                try:
                    repack_receipt, _ = repack_verified_inner_tar_to_zip(
                        archive_descriptor=merged_tar,
                        destination=output_directory / "label.zip",
                        policy=self._archive_policy,
                    )
                finally:
                    os.close(merged_tar)
                if repack_receipt.source_tar_sha256 != merge_receipt.merged_sha256:
                    raise Aihub538LabelCapacityPipelineError(
                        "aihub538_label_audit_repack_lineage_mismatch"
                    )
                os.unlink("label.tar", dir_fd=root_descriptor)
                os.fsync(root_descriptor)
            _persist_json_receipt(
                root_descriptor,
                "20-split-label-merge.json",
                merge_receipt.to_json_object(),
            )
            if repack_receipt is not None:
                _persist_json_receipt(
                    root_descriptor,
                    "25-inner-tar-repack.json",
                    repack_receipt.to_json_object(),
                )
            label_descriptor = os.open(
                "label.zip",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=root_descriptor,
            )
            validation = validate_zip_descriptor(
                label_descriptor,
                policy=self._archive_policy,
            )
            if (
                group.kind == "zip"
                and validation.archive_sha256 != merge_receipt.merged_sha256
            ) or (
                group.kind == "tar"
                and (
                    repack_receipt is None
                    or validation.archive_sha256
                    != repack_receipt.repacked_zip_sha256
                )
            ):
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_zip_lineage_mismatch"
                )
            _persist_json_receipt(
                root_descriptor,
                "30-label-zip-validation.json",
                validation.to_json_object(),
            )
            aggregate = self._adapter.audit(
                label_zip_descriptor=label_descriptor,
                identity_hash_key=identity.key,
            )
            baseline_aggregate = self._baseline_adapter.audit(
                label_zip_descriptor=baseline_label_zip_descriptor,
                identity_hash_key=identity.key,
            )
            if (
                aggregate.label_archive_sha256 != validation.archive_sha256
                or aggregate.label_content_root_sha256
                != validation.content_root_sha256
                or baseline_aggregate.label_archive_sha256
                != baseline_admission_receipt.label_archive_sha256
                or baseline_aggregate.label_content_root_sha256
                != baseline_admission_receipt.label_zip_content_root_sha256
                or len(baseline_aggregate.items)
                != baseline_admission_receipt.label_count
                or len(
                    {
                        item.actor_token_sha256
                        for item in baseline_aggregate.items
                    }
                )
                != baseline_admission_receipt.speaker_count
                or baseline_aggregate.identity_namespace_commitment_sha256
                != baseline_admission_receipt.identity_namespace_commitment_sha256
            ):
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_admission_lineage_mismatch"
                )
            if is_tl175:
                capacity: (
                    Aihub538LabelCapacityAuditReceipt
                    | Aihub538LabelSelectionAuthorizationReceipt
                ) = Aihub538LabelCapacityAuditReceipt.issue(
                    baseline=baseline_aggregate,
                    candidate=aggregate,
                    baseline_admission_receipt_sha256=(
                        baseline_admission_receipt.receipt_sha256
                    ),
                    baseline_admission_manifest_sha256=(
                        baseline_admission_receipt.admission_manifest_sha256
                    ),
                )
            else:
                assert prior_prefix_admission is not None
                prior_aggregate = self._tl175_adapter.audit(
                    label_zip_descriptor=(
                        prior_prefix_admission.label_zip_descriptor
                    ),
                    identity_hash_key=identity.key,
                )
                prior_receipt = prior_prefix_admission.capacity_receipt
                if (
                    prior_aggregate.label_archive_sha256
                    != prior_receipt.label_archive_sha256
                    or prior_aggregate.label_content_root_sha256
                    != prior_receipt.label_content_root_sha256
                ):
                    raise Aihub538LabelCapacityPipelineError(
                        "aihub538_label_capacity_prior_prefix_lineage_mismatch"
                    )
                historical_capacity = (
                    Aihub538CumulativeLabelCapacityAuditReceipt.issue_tl175_tl176_prefix(
                        baseline=baseline_aggregate,
                        tl175=prior_aggregate,
                        tl176=aggregate,
                        prior_tl175_receipt=prior_receipt,
                        baseline_admission_receipt_sha256=(
                            baseline_admission_receipt.receipt_sha256
                        ),
                        baseline_admission_manifest_sha256=(
                            baseline_admission_receipt.admission_manifest_sha256
                        ),
                    )
                )
                capacity = (
                    Aihub538LabelSelectionAuthorizationReceipt.issue_tl175_tl176_prefix(
                        baseline=baseline_aggregate,
                        tl175=prior_aggregate,
                        tl176=aggregate,
                        prior_tl175_receipt=prior_receipt,
                        historical_capacity_receipt=historical_capacity,
                        baseline_admission_receipt_sha256=(
                            baseline_admission_receipt.receipt_sha256
                        ),
                        baseline_admission_manifest_sha256=(
                            baseline_admission_receipt.admission_manifest_sha256
                        ),
                    )
                )
            _persist_json_receipt(
                root_descriptor,
                _CAPACITY_RECEIPT_NAME,
                capacity.to_json_object(),
            )
            completion_unsigned: dict[str, object] = {
                "schema_version": (
                    "voice_coach.aihub538_tl175_capacity_pipeline.v2"
                    if is_tl175
                    else "voice_coach.aihub538_label_selection_pipeline.v5"
                ),
                "status": (
                    "completed_feasible"
                    if capacity.is_feasible
                    else "completed_infeasible"
                ),
                "download_verification_sha256": download.receipt_sha256,
                "outer_content_root_sha256": tar_receipt.content_root_sha256,
                "label_zip_content_root_sha256": validation.content_root_sha256,
                "capacity_audit_receipt_sha256": capacity.receipt_sha256,
                "baseline_completion_receipt_sha256": (
                    baseline_completion_receipt_sha256
                ),
                "baseline_admission_receipt_sha256": (
                    baseline_admission_receipt.receipt_sha256
                ),
                "source_partition_accessed": False,
                "partial_download_accessed": False,
                "locked_partition_accessed": False,
                "aggregate_only": True,
                "ts175_pair_admission_precondition_satisfied": True,
                "source_download_authorized": capacity.is_feasible,
                "capacity_outcome_persisted": True,
                "promotion_authorized": False,
            }
            completion_unsigned[
                "ts175_pair_admission_precondition_satisfied"
            ] = capacity.is_feasible
            if not is_tl175:
                assert prior_prefix_admission is not None
                completion_unsigned.update(
                    {
                        "evaluated_label_prefix": ["TL175", "TL176"],
                        "evaluated_label_file_key_prefix": [
                            "54726",
                            "54727",
                        ],
                        "prior_prefix_receipt_sha256": (
                            prior_prefix_admission.capacity_receipt.receipt_sha256
                        ),
                        "prior_prefix_completion_receipt_sha256": (
                            prior_prefix_admission.completion_receipt_sha256
                        ),
                        "current_label_role_component": (
                            self._audit_shard_spec.label_role_component
                        ),
                        "current_label_file_key": (
                            self._audit_shard_spec.ordered_file_keys[0]
                        ),
                        "historical_capacity_receipt_sha256": (
                            capacity.historical_capacity_receipt_sha256
                        ),
                    }
                )
            _persist_json_receipt(
                root_descriptor,
                "99-completion.json",
                {
                    **completion_unsigned,
                    "receipt_sha256": sha256_document(completion_unsigned),
                },
            )
            os.fsync(root_descriptor)
        except BaseException as error:
            cleanup_failed = False
            if (
                root_descriptor is not None
                and parent_descriptor is not None
                and root_name is not None
            ):
                try:
                    _remove_created_run_root(
                        root_descriptor=root_descriptor,
                        parent_descriptor=parent_descriptor,
                        root_name=root_name,
                    )
                except Exception:
                    cleanup_failed = True
            if cleanup_failed:
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_failure_cleanup_failed"
                ) from None
            if isinstance(error, Aihub538LabelCapacityPipelineError):
                raise
            if isinstance(
                error,
                (
                    Aihub538ArchiveError,
                    Aihub538IdentityKeyStoreError,
                    Aihub538LabelCapacityError,
                ),
            ):
                raise Aihub538LabelCapacityPipelineError(
                    error.reason_code
                ) from None
            if isinstance(error, ValueError) and str(error).startswith("aihub538_"):
                raise Aihub538LabelCapacityPipelineError(str(error)) from None
            if not isinstance(error, Exception):
                raise
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_capacity_pipeline_internal_failure"
            ) from None
        finally:
            if label_descriptor is not None:
                os.close(label_descriptor)
            for descriptor in part_descriptors:
                os.close(descriptor)
            if root_descriptor is not None:
                os.close(root_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)
        return capacity


@contextmanager
def open_aihub538_ts174_baseline_admission_directory(
    directory: Path,
    *,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
):
    """Resolve the label candidate by exact 50 receipt commitments."""

    directory_descriptor: int | None = None
    candidate_0_descriptor: int | None = None
    candidate_1_descriptor: int | None = None
    admission_descriptor: int | None = None
    completion_descriptor: int | None = None
    try:
        if (
            not directory.is_absolute()
            or directory.resolve(strict=True) != directory
        ):
            raise OSError
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        directory_metadata = os.fstat(directory_descriptor)
        if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
            raise OSError
        candidate_0_descriptor = os.open(
            "candidate-0.zip",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        candidate_1_descriptor = os.open(
            "candidate-1.zip",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        admission_descriptor = os.open(
            "50-dataset-admission.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        completion_descriptor = os.open(
            "99-completion.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        admission = load_aihub538_training_admission_receipt_descriptor(
            admission_descriptor
        )
        if admission.shard_id != AIHUB538_TS174_TL174_SHARD.shard_id:
            raise ValueError
        role_binding = resolve_aihub538_candidate_role_descriptors(
            candidate_0_descriptor=candidate_0_descriptor,
            candidate_1_descriptor=candidate_1_descriptor,
            admission_receipt=admission,
            policy=policy,
        )
        label_descriptor = role_binding.label_zip_descriptor
        stable = validate_aihub538_stable_zip_descriptor(
            label_descriptor,
            policy=policy,
        )
        if (
            stable.validation_receipt.archive_sha256
            != admission.label_archive_sha256
            or stable.validation_receipt.content_root_sha256
            != admission.label_zip_content_root_sha256
        ):
            raise ValueError
        completion = _load_strict_baseline_completion(completion_descriptor)
        if completion["dataset_admission"] != admission.to_json_object():
            raise ValueError
        validations = completion["zip_validation"]
        if not isinstance(validations, dict):
            raise ValueError
        archives = validations.get("archives")
        if not isinstance(archives, list):
            raise ValueError
        label_matches = [
            item for item in archives
            if isinstance(item, dict)
            and item.get("archive_sha256") == admission.label_archive_sha256
            and item.get("content_root_sha256")
            == admission.label_zip_content_root_sha256
        ]
        if len(label_matches) != 1:
            raise ValueError
        completion_sha256 = completion["completion_receipt_sha256"]
        if not isinstance(completion_sha256, str):
            raise ValueError
        baseline = _BaselineAdmission(
            label_zip_descriptor=label_descriptor,
            admission_receipt=admission,
            completion_receipt_sha256=completion_sha256,
        )
    except (OSError, ValueError, Aihub538DecisionCalibrationError):
        for descriptor in (
            completion_descriptor,
            admission_descriptor,
            candidate_1_descriptor,
            candidate_0_descriptor,
            directory_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_label_capacity_baseline_invalid"
        ) from None
    try:
        yield baseline
        assert_aihub538_stable_zip_descriptor(stable)
    finally:
        for descriptor in (
            completion_descriptor,
            admission_descriptor,
            candidate_1_descriptor,
            candidate_0_descriptor,
            directory_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)


def load_aihub538_label_capacity_receipt_descriptor(
    descriptor: int,
) -> Aihub538LabelCapacityAuditReceipt:
    """Strictly reload one 0400 capacity receipt without source access."""

    try:
        document = _read_strict_json_descriptor(
            descriptor,
            maximum_bytes=1024 * 1024,
            require_mode=0o400,
        )
        return Aihub538LabelCapacityAuditReceipt.from_json_object(document)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        Aihub538DecisionCalibrationError,
    ):
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_label_capacity_receipt_reload_failed"
        ) from None


def load_aihub538_cumulative_label_capacity_receipt_descriptor(
    descriptor: int,
) -> Aihub538CumulativeLabelCapacityAuditReceipt:
    """Strictly reload one 0400 cumulative-prefix receipt."""

    try:
        document = _read_strict_json_descriptor(
            descriptor,
            maximum_bytes=2 * 1024 * 1024,
            require_mode=0o400,
        )
        return Aihub538CumulativeLabelCapacityAuditReceipt.from_json_object(
            document
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        Aihub538DecisionCalibrationError,
    ):
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_cumulative_capacity_receipt_reload_failed"
        ) from None


def load_aihub538_label_selection_authorization_receipt_descriptor(
    descriptor: int,
) -> Aihub538LabelSelectionAuthorizationReceipt:
    """Strictly reload one new v5 post-exclusion source authorization."""

    try:
        document = _read_strict_json_descriptor(
            descriptor,
            maximum_bytes=2 * 1024 * 1024,
            require_mode=0o400,
        )
        return Aihub538LabelSelectionAuthorizationReceipt.from_json_object(
            document
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        Aihub538DecisionCalibrationError,
    ):
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_label_selection_authorization_reload_failed"
        ) from None


def load_aihub538_capacity_receipt_descriptor(
    descriptor: int,
) -> (
        Aihub538LabelCapacityAuditReceipt
        | Aihub538CumulativeLabelCapacityAuditReceipt
        | Aihub538LabelSelectionAuthorizationReceipt
):
    """Strict schema dispatch without accepting caller self-attestation."""

    try:
        document = _read_strict_json_descriptor(
            descriptor,
            maximum_bytes=2 * 1024 * 1024,
            require_mode=0o400,
        )
        schema = document.get("schema_version")
        if schema == "voice_coach.aihub538_label_capacity_audit.v4":
            return Aihub538LabelCapacityAuditReceipt.from_json_object(document)
        if schema == (
            "voice_coach.aihub538_cumulative_label_capacity_audit.v1"
        ):
            return Aihub538CumulativeLabelCapacityAuditReceipt.from_json_object(
                document
            )
        if schema == AIHUB538_LABEL_SELECTION_AUTHORIZATION_SCHEMA_VERSION:
            return Aihub538LabelSelectionAuthorizationReceipt.from_json_object(
                document
            )
        raise ValueError
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        Aihub538DecisionCalibrationError,
    ):
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_capacity_receipt_reload_failed"
        ) from None


@contextmanager
def open_aihub538_prior_label_capacity_audit_directory(
    directory: Path,
    *,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
):
    """Open exact TL175 label/receipt lineage for the TL176 prefix audit."""

    directory_descriptor: int | None = None
    label_descriptor: int | None = None
    receipt_descriptor: int | None = None
    completion_descriptor: int | None = None
    stable = None
    try:
        if (
            not directory.is_absolute()
            or directory.resolve(strict=True) != directory
        ):
            raise OSError
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        if stat.S_IMODE(os.fstat(directory_descriptor).st_mode) != 0o700:
            raise OSError
        label_descriptor = os.open(
            "label.zip",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        if stat.S_IMODE(os.fstat(label_descriptor).st_mode) != 0o600:
            raise OSError
        receipt_descriptor = os.open(
            _CAPACITY_RECEIPT_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        completion_descriptor = os.open(
            "99-completion.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        receipt = load_aihub538_label_capacity_receipt_descriptor(
            receipt_descriptor
        )
        if receipt.is_feasible:
            raise ValueError
        stable = validate_aihub538_stable_zip_descriptor(
            label_descriptor,
            policy=policy,
        )
        if (
            stable.validation_receipt.archive_sha256
            != receipt.label_archive_sha256
            or stable.validation_receipt.content_root_sha256
            != receipt.label_content_root_sha256
        ):
            raise ValueError
        completion = _read_strict_json_descriptor(
            completion_descriptor,
            maximum_bytes=1024 * 1024,
            require_mode=0o400,
        )
        if (
            set(completion) != _LABEL_CAPACITY_COMPLETION_KEYS
            or completion["schema_version"]
            != "voice_coach.aihub538_tl175_capacity_pipeline.v2"
            or completion["status"] != "completed_infeasible"
            or completion["capacity_audit_receipt_sha256"]
            != receipt.receipt_sha256
            or completion["label_zip_content_root_sha256"]
            != receipt.label_content_root_sha256
            or completion["source_download_authorized"] is not False
            or completion[
                "ts175_pair_admission_precondition_satisfied"
            ]
            is not False
        ):
            raise ValueError
        completion_unsigned = dict(completion)
        completion_sha256 = completion_unsigned.pop("receipt_sha256")
        if completion_sha256 != sha256_document(completion_unsigned):
            raise ValueError
        prior = _PriorPrefixAdmission(
            label_zip_descriptor=label_descriptor,
            capacity_receipt=receipt,
            completion_receipt_sha256=completion_sha256,
        )
    except (
        OSError,
        ValueError,
        Aihub538DecisionCalibrationError,
        Aihub538LabelCapacityPipelineError,
    ):
        for descriptor in (
            completion_descriptor,
            receipt_descriptor,
            label_descriptor,
            directory_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_label_capacity_prior_prefix_invalid"
        ) from None
    try:
        yield prior
        assert stable is not None
        assert_aihub538_stable_zip_descriptor(stable)
    finally:
        for descriptor in (
            completion_descriptor,
            receipt_descriptor,
            label_descriptor,
            directory_descriptor,
        ):
            if descriptor is not None:
                os.close(descriptor)


def _load_strict_baseline_completion(descriptor: int) -> dict[str, object]:
    document = _read_strict_json_descriptor(
        descriptor,
        maximum_bytes=4 * 1024 * 1024,
        require_mode=0o400,
    )
    if not isinstance(document, dict):
        raise ValueError
    schema = document.get("schema_version")
    keys = set(document)
    if not (
        (
            schema == "voice_coach.aihub538_training_pipeline_receipt.v1"
            and frozenset(keys)
            in {
                frozenset(_HISTORICAL_BASELINE_COMPLETION_KEYS),
                frozenset(_BASELINE_COMPLETION_KEYS),
            }
        )
        or (
            schema == "voice_coach.aihub538_training_pipeline_receipt.v2"
            and keys == _BASELINE_COMPLETION_KEYS
        )
    ):
        raise ValueError
    if (
        document["status"]
        != "completed_nonlocked_training_metadata_smoke"
        or document["dataset_id"] != 538
        or document["dataset_partition"]
        != AIHUB538_TS174_TL174_SHARD.dataset_partition
        or document["partial_download_accessed"] is not False
        or document["locked_partition_accessed"] is not False
    ):
        raise ValueError
    unsigned = dict(document)
    receipt_sha256 = unsigned.pop("completion_receipt_sha256")
    if receipt_sha256 != sha256_document(unsigned):
        raise ValueError
    return document


def _read_strict_json_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    require_mode: int,
) -> dict[str, object]:
    before = capture_aihub538_descriptor_identity(
        descriptor,
        require_mode=require_mode,
    )
    if before.size_bytes > maximum_bytes:
        raise ValueError
    payload = bytearray()
    offset = 0
    while offset < before.size_bytes:
        chunk = os.pread(
            descriptor,
            min(64 * 1024, before.size_bytes - offset),
            offset,
        )
        if not chunk:
            raise ValueError
        payload.extend(chunk)
        offset += len(chunk)
    assert_aihub538_descriptor_identity(descriptor, before)
    document = json.loads(
        bytes(payload).decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(document, dict):
        raise ValueError
    return document


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _discover_one_split_group(
    outer_descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> _SingleSplitGroup:
    zip_groups: dict[tuple[tuple[str, ...], str], list[NamedSplitZipPart]] = {}
    tar_groups: dict[tuple[tuple[str, ...], str], list[NamedSplitTarPart]] = {}
    opened: list[int] = []

    def visit(directory_descriptor: int, parents: tuple[str, ...]) -> None:
        try:
            names = sorted(os.listdir(directory_descriptor))
        except OSError:
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_audit_outer_traversal_failed"
            ) from None
        for name in names:
            metadata = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISDIR(metadata.st_mode):
                child = _open_private_directory_at(directory_descriptor, name)
                try:
                    visit(child, parents + (name,))
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_outer_member_invalid"
                )
            match = _SPLIT_PATTERN.fullmatch(name)
            if match is None:
                raise Aihub538LabelCapacityPipelineError(
                    "aihub538_label_audit_outer_member_invalid"
                )
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            opened.append(descriptor)
            key = (parents, match.group("stem"))
            if match.group("kind") == "zip":
                zip_groups.setdefault(key, []).append(
                    NamedSplitZipPart(name=name, descriptor=descriptor)
                )
            else:
                tar_groups.setdefault(key, []).append(
                    NamedSplitTarPart(name=name, descriptor=descriptor)
                )

    try:
        visit(outer_descriptor, ())
        if len(zip_groups) + len(tar_groups) != 1 or (zip_groups and tar_groups):
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_audit_split_group_invalid"
            )
        groups = zip_groups or tar_groups
        parts = next(iter(groups.values()))
        if not 1 <= len(parts) <= policy.maximum_split_part_count:
            raise Aihub538LabelCapacityPipelineError(
                "aihub538_label_audit_split_group_invalid"
            )
        if zip_groups:
            result = _SingleSplitGroup(kind="zip", zip_parts=tuple(parts))
        else:
            result = _SingleSplitGroup(kind="tar", tar_parts=tuple(parts))
        opened.clear()
        return result
    except OSError:
        raise Aihub538LabelCapacityPipelineError(
            "aihub538_label_audit_outer_traversal_failed"
        ) from None
    finally:
        for descriptor in opened:
            os.close(descriptor)


__all__ = [
    "Aihub538LabelCapacityAuditPipeline",
    "Aihub538LabelCapacityPipelineError",
    "load_aihub538_cumulative_label_capacity_receipt_descriptor",
    "load_aihub538_capacity_receipt_descriptor",
    "load_aihub538_label_capacity_receipt_descriptor",
    "open_aihub538_prior_label_capacity_audit_directory",
]
