"""Prepare one exact TL177--TL180 label-only archive for P6 audit."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from ..adapters.aihub538_identity_key_store import (
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key_descriptor,
)
from ..adapters.aihub538_admitted_archive_binding import (
    assert_aihub538_descriptor_identity,
    capture_aihub538_descriptor_identity,
)
from ..adapters.aihub538_label_capacity import (
    Aihub538LabelCapacityAdapter,
    Aihub538LabelCapacityError,
)
from ..adapters.aihub538_p6_label_preparation import (
    Aihub538P6LabelPreparationAdapterError,
    Aihub538P6LabelPreparationOutput,
    assert_aihub538_p6_completed_download_binding,
    capture_aihub538_p6_completed_download_binding,
    close_aihub538_p6_split_group,
    discover_aihub538_p6_label_split_group,
)
from ..adapters.aihub538_split_tar_archive import (
    merge_split_tar_parts,
    repack_verified_inner_tar_to_zip,
)
from ..adapters.aihub538_training_archive import (
    AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY,
    Aihub538ArchiveError,
    aihub538_archive_security_policy_for_shard,
    extract_verified_tar_descriptor,
    merge_split_zip_parts,
    validate_zip_descriptor,
    verify_aihub538_nonlocked_shard_download,
)
from ..models.aihub538_nonlocked_shards import (
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.aihub538_p6_label_preparation import (
    AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS,
    Aihub538P6LabelPreparationReceipt,
)
from ..models.integrity import sha256_document
from .aihub538_training_pipeline import (
    Aihub538TrainingPipelineError,
    open_completed_aihub538_download_directory,
)


class Aihub538P6LabelPreparationError(RuntimeError):
    """Aggregate-only application failure without restricted values."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538P6LabelPreparationRequest:
    """Descriptor-only request after permission and shard selection."""

    archive_descriptor: int
    receipt_descriptor: int
    identity_key_descriptor: int
    output_directory: Path
    dataset_access_permission_confirmed: bool


class Aihub538P6LabelPreparationService:
    """Verify, reconstruct, audit, and publish one remediation label ZIP."""

    def __init__(self, *, shard_spec: Aihub538NonlockedShardSpec) -> None:
        try:
            validated = require_aihub538_nonlocked_shard_spec(
                shard_spec,
                require_label_audit=True,
            )
        except ValueError as error:
            raise Aihub538P6LabelPreparationError(str(error)) from None
        if validated not in AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS:
            raise Aihub538P6LabelPreparationError(
                "aihub538_p6_remediation_label_shard_not_allowed"
            )
        self._shard_spec = validated
        self._archive_policy = aihub538_archive_security_policy_for_shard(
            validated
        )
        if self._archive_policy is not (
            AIHUB538_P6_REMEDIATION_ARCHIVE_SECURITY_POLICY
        ):
            raise Aihub538P6LabelPreparationError(
                "aihub538_p6_remediation_archive_policy_invalid"
            )
        self._label_adapter = Aihub538LabelCapacityAdapter(
            shard_spec=validated,
            archive_policy=self._archive_policy,
        )

    @property
    def shard_spec(self) -> Aihub538NonlockedShardSpec:
        return self._shard_spec

    def prepare_from_completed_directory(
        self,
        *,
        input_directory: Path,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
    ) -> Aihub538P6LabelPreparationReceipt:
        """Open exact completed names only after the permission gate passes."""

        if dataset_access_permission_confirmed is not True:
            raise Aihub538P6LabelPreparationError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        try:
            with open_completed_aihub538_download_directory(
                input_directory
            ) as opened:
                archive_descriptor, receipt_descriptor = opened
                return self.prepare(
                    Aihub538P6LabelPreparationRequest(
                        archive_descriptor=archive_descriptor,
                        receipt_descriptor=receipt_descriptor,
                        identity_key_descriptor=identity_key_descriptor,
                        output_directory=output_directory,
                        dataset_access_permission_confirmed=True,
                    )
                )
        except Aihub538P6LabelPreparationError:
            raise
        except Aihub538TrainingPipelineError as error:
            raise Aihub538P6LabelPreparationError(
                error.reason_code
            ) from None

    def run_from_completed_directory(
        self,
        *,
        input_directory: Path,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
    ) -> Aihub538P6LabelPreparationReceipt:
        """Compatibility entrypoint using the repository's pipeline naming."""

        return self.prepare_from_completed_directory(
            input_directory=input_directory,
            identity_key_descriptor=identity_key_descriptor,
            output_directory=output_directory,
            dataset_access_permission_confirmed=(
                dataset_access_permission_confirmed
            ),
        )

    def run(
        self,
        *,
        archive_descriptor: int,
        receipt_descriptor: int,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
    ) -> Aihub538P6LabelPreparationReceipt:
        """Descriptor entrypoint matching other AI Hub pipeline services."""

        return self.prepare(
            Aihub538P6LabelPreparationRequest(
                archive_descriptor=archive_descriptor,
                receipt_descriptor=receipt_descriptor,
                identity_key_descriptor=identity_key_descriptor,
                output_directory=output_directory,
                dataset_access_permission_confirmed=(
                    dataset_access_permission_confirmed
                ),
            )
        )

    def prepare(
        self,
        request: Aihub538P6LabelPreparationRequest,
    ) -> Aihub538P6LabelPreparationReceipt:
        """Execute transactionally; any failure removes the fresh output."""

        if type(request) is not Aihub538P6LabelPreparationRequest:
            raise Aihub538P6LabelPreparationError(
                "aihub538_p6_label_preparation_request_invalid"
            )
        if request.dataset_access_permission_confirmed is not True:
            raise Aihub538P6LabelPreparationError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        descriptors = (
            request.archive_descriptor,
            request.receipt_descriptor,
            request.identity_key_descriptor,
        )
        if (
            any(
                isinstance(descriptor, bool)
                or not isinstance(descriptor, int)
                or descriptor < 3
                for descriptor in descriptors
            )
            or len(set(descriptors)) != len(descriptors)
        ):
            raise Aihub538P6LabelPreparationError(
                "aihub538_p6_label_preparation_descriptor_invalid"
            )

        try:
            input_binding = capture_aihub538_p6_completed_download_binding(
                archive_descriptor=request.archive_descriptor,
                receipt_descriptor=request.receipt_descriptor,
            )
            identity = load_aihub538_identity_key_descriptor(
                request.identity_key_descriptor
            )
            download = verify_aihub538_nonlocked_shard_download(
                archive_descriptor=request.archive_descriptor,
                receipt_descriptor=request.receipt_descriptor,
                shard_spec=self._shard_spec,
                policy=self._archive_policy,
            )
            assert_aihub538_p6_completed_download_binding(
                archive_descriptor=request.archive_descriptor,
                receipt_descriptor=request.receipt_descriptor,
                binding=input_binding,
            )
        except (
            Aihub538ArchiveError,
            Aihub538IdentityKeyStoreError,
            Aihub538P6LabelPreparationAdapterError,
        ) as error:
            raise Aihub538P6LabelPreparationError(error.reason_code) from None

        output: Aihub538P6LabelPreparationOutput | None = None
        split_group = None
        label_descriptor: int | None = None
        try:
            output = Aihub538P6LabelPreparationOutput(
                request.output_directory
            )
            download_stage = download.to_json_object()
            output.persist_receipt(
                "00-download-verification.json",
                download_stage,
            )

            extraction = extract_verified_tar_descriptor(
                archive_descriptor=request.archive_descriptor,
                destination=request.output_directory / "outer",
                policy=self._archive_policy,
            )
            if extraction.archive_sha256 != download.archive_sha256:
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_outer_lineage_mismatch"
                )
            assert_aihub538_p6_completed_download_binding(
                archive_descriptor=request.archive_descriptor,
                receipt_descriptor=request.receipt_descriptor,
                binding=input_binding,
            )
            output.assert_identity()
            extraction_stage = extraction.to_json_object()
            output.persist_receipt(
                "10-outer-tar-extraction.json",
                extraction_stage,
            )

            outer_descriptor = output.open_outer_directory()
            try:
                split_group = discover_aihub538_p6_label_split_group(
                    outer_descriptor,
                    policy=self._archive_policy,
                )
            finally:
                os.close(outer_descriptor)
            try:
                split_part_bindings = tuple(
                    capture_aihub538_descriptor_identity(
                        descriptor,
                        require_mode=0o600,
                    )
                    for descriptor in split_group.descriptors
                )
                if any(
                    os.fstat(descriptor).st_uid != os.geteuid()
                    or os.fstat(descriptor).st_nlink != 1
                    for descriptor in split_group.descriptors
                ):
                    raise OSError
            except Exception:
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_split_descriptor_invalid"
                ) from None

            if split_group.kind == "zip":
                merge_receipt = merge_split_zip_parts(
                    parts=split_group.zip_parts,
                    destination=request.output_directory / "label.zip",
                    policy=self._archive_policy,
                )
                repack_receipt = None
            else:
                merge_receipt = merge_split_tar_parts(
                    parts=split_group.tar_parts,
                    destination=request.output_directory / "label.tar",
                    policy=self._archive_policy,
                )
                merged_tar_descriptor = os.open(
                    "label.tar",
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=output.root_descriptor,
                )
                try:
                    repack_receipt, _ = repack_verified_inner_tar_to_zip(
                        archive_descriptor=merged_tar_descriptor,
                        destination=request.output_directory / "label.zip",
                        policy=self._archive_policy,
                    )
                finally:
                    os.close(merged_tar_descriptor)
                if (
                    repack_receipt.source_tar_sha256
                    != merge_receipt.merged_sha256
                ):
                    raise Aihub538P6LabelPreparationError(
                        "aihub538_p6_label_preparation_repack_lineage_mismatch"
                    )
                output.remove_file("label.tar")
            try:
                for descriptor, binding in zip(
                    split_group.descriptors,
                    split_part_bindings,
                    strict=True,
                ):
                    assert_aihub538_descriptor_identity(
                        descriptor,
                        binding,
                    )
            except Exception:
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_split_changed"
                ) from None
            output.assert_identity()
            output.persist_receipt(
                "20-split-label-merge.json",
                merge_receipt.to_json_object(),
            )
            if repack_receipt is not None:
                output.persist_receipt(
                    "25-inner-tar-repack.json",
                    repack_receipt.to_json_object(),
                )

            label_descriptor = output.open_label_zip()
            label_binding = capture_aihub538_descriptor_identity(
                label_descriptor,
                require_mode=0o600,
            )
            validation = validate_zip_descriptor(
                label_descriptor,
                policy=self._archive_policy,
            )
            expected_label_sha256 = (
                merge_receipt.merged_sha256
                if repack_receipt is None
                else repack_receipt.repacked_zip_sha256
            )
            if validation.archive_sha256 != expected_label_sha256:
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_zip_lineage_mismatch"
                )
            validation_stage = validation.to_json_object()
            output.persist_receipt(
                "30-label-zip-validation.json",
                validation_stage,
            )

            aggregate = self._label_adapter.audit(
                label_zip_descriptor=label_descriptor,
                identity_hash_key=identity.key,
            )
            if (
                aggregate.label_archive_sha256
                != validation.archive_sha256
                or aggregate.label_content_root_sha256
                != validation.content_root_sha256
                or len(aggregate.items) != validation.regular_file_count
            ):
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_admission_lineage_mismatch"
                )
            actor_count = len(
                {item.actor_token_sha256 for item in aggregate.items}
            )
            attempt_count = len(
                {item.attempt_token_sha256 for item in aggregate.items}
            )
            metadata_excluded_count = sum(
                not item.metadata_bbox_observation_available
                for item in aggregate.items
            )
            admission_unsigned: dict[str, object] = {
                "schema_version": (
                    "voice_coach.aihub538_p6_remediation_label_admission.v2"
                ),
                "status": "completed_label_only_aggregate_admission",
                "dataset_id": 538,
                "shard_id": self._shard_spec.shard_id,
                "label_role_component": (
                    self._shard_spec.label_role_component
                ),
                "label_file_key": self._shard_spec.ordered_file_keys[0],
                "shard_spec_sha256": self._shard_spec.spec_sha256,
                "label_archive_sha256": aggregate.label_archive_sha256,
                "label_content_root_sha256": (
                    aggregate.label_content_root_sha256
                ),
                "label_member_count": len(aggregate.items),
                "actor_count": actor_count,
                "attempt_count": attempt_count,
                "metadata_excluded_label_count": metadata_excluded_count,
                "terminal_media_overrun_label_count": (
                    aggregate.terminal_media_overrun_label_count
                ),
                "terminal_frame_tail_exception_label_count": (
                    aggregate.terminal_frame_tail_exception_label_count
                ),
                "terminal_frame_tail_policy_sha256": (
                    aggregate.terminal_frame_tail_policy_sha256
                ),
                "maximum_complete_tail_frames_observed": (
                    aggregate.maximum_complete_tail_frames_observed
                ),
                "nonterminal_media_overrun_label_count": (
                    aggregate.nonterminal_media_overrun_label_count
                ),
                "sentence_overlap_label_count": (
                    aggregate.sentence_overlap_label_count
                ),
                "invalid_time_label_count": aggregate.invalid_time_label_count,
                "nonconforming_fps_label_count": (
                    aggregate.nonconforming_fps_label_count
                ),
                "identity_namespace_commitment_sha256": (
                    aggregate.identity_namespace_commitment_sha256
                ),
                "member_namespace_commitment_sha256": (
                    aggregate.member_namespace_commitment_sha256
                ),
                "source_partition_accessed": False,
                "locked_partition_accessed": False,
                "raw_member_names_serialized": False,
                "participant_identifiers_serialized": False,
                "transcripts_serialized": False,
                "sample_digests_serialized": False,
                "promotion_authorized": False,
                "aggregate_only": True,
            }
            admission_receipt_sha256 = sha256_document(admission_unsigned)
            admission_stage = {
                **admission_unsigned,
                "receipt_sha256": admission_receipt_sha256,
            }
            output.persist_receipt(
                "40-label-role-admission.json",
                admission_stage,
            )

            receipt = Aihub538P6LabelPreparationReceipt.issue(
                shard_spec=self._shard_spec,
                download_archive_sha256=download.archive_sha256,
                secure_download_receipt_sha256=download.receipt_sha256,
                download_verification_stage_sha256=sha256_document(
                    download_stage
                ),
                outer_content_root_sha256=extraction.content_root_sha256,
                outer_extraction_stage_sha256=sha256_document(
                    extraction_stage
                ),
                split_archive_kind=split_group.kind,
                split_merge_receipt_sha256=sha256_document(
                    merge_receipt.to_json_object()
                ),
                inner_tar_repack_receipt_sha256=(
                    None
                    if repack_receipt is None
                    else sha256_document(repack_receipt.to_json_object())
                ),
                label_zip_sha256=validation.archive_sha256,
                label_zip_content_root_sha256=validation.content_root_sha256,
                label_zip_validation_stage_sha256=sha256_document(
                    validation_stage
                ),
                label_admission_receipt_sha256=(
                    admission_receipt_sha256
                ),
                label_member_count=len(aggregate.items),
                actor_count=actor_count,
                attempt_count=attempt_count,
                metadata_excluded_label_count=metadata_excluded_count,
                terminal_media_overrun_label_count=(
                    aggregate.terminal_media_overrun_label_count
                ),
                terminal_frame_tail_exception_label_count=(
                    aggregate.terminal_frame_tail_exception_label_count
                ),
                terminal_frame_tail_policy_sha256=(
                    aggregate.terminal_frame_tail_policy_sha256
                ),
                maximum_complete_tail_frames_observed=(
                    aggregate.maximum_complete_tail_frames_observed
                ),
                nonterminal_media_overrun_label_count=(
                    aggregate.nonterminal_media_overrun_label_count
                ),
                sentence_overlap_label_count=(
                    aggregate.sentence_overlap_label_count
                ),
                invalid_time_label_count=aggregate.invalid_time_label_count,
                nonconforming_fps_label_count=(
                    aggregate.nonconforming_fps_label_count
                ),
                identity_namespace_commitment_sha256=(
                    aggregate.identity_namespace_commitment_sha256
                ),
                member_namespace_commitment_sha256=(
                    aggregate.member_namespace_commitment_sha256
                ),
            )
            output.remove_outer_workspace()
            output.persist_receipt(
                "99-completion.json",
                receipt.to_json_object(),
            )
            assert_aihub538_descriptor_identity(
                label_descriptor,
                label_binding,
            )
            assert_aihub538_p6_completed_download_binding(
                archive_descriptor=request.archive_descriptor,
                receipt_descriptor=request.receipt_descriptor,
                binding=input_binding,
            )
            output.commit()
            return receipt
        except BaseException as error:
            cleanup_failed = False
            if output is not None:
                try:
                    output.abort()
                except Aihub538P6LabelPreparationAdapterError:
                    cleanup_failed = True
            if cleanup_failed:
                raise Aihub538P6LabelPreparationError(
                    "aihub538_p6_label_preparation_failure_cleanup_failed"
                ) from None
            if isinstance(error, Aihub538P6LabelPreparationError):
                raise
            if isinstance(
                error,
                (
                    Aihub538ArchiveError,
                    Aihub538IdentityKeyStoreError,
                    Aihub538LabelCapacityError,
                    Aihub538P6LabelPreparationAdapterError,
                ),
            ):
                raise Aihub538P6LabelPreparationError(
                    error.reason_code
                ) from None
            if isinstance(error, ValueError) and str(error).startswith(
                "aihub538_"
            ):
                raise Aihub538P6LabelPreparationError(str(error)) from None
            if not isinstance(error, Exception):
                raise
            raise Aihub538P6LabelPreparationError(
                "aihub538_p6_label_preparation_internal_failure"
            ) from None
        finally:
            if label_descriptor is not None:
                os.close(label_descriptor)
            if split_group is not None:
                close_aihub538_p6_split_group(split_group)
            if output is not None:
                output.close()


__all__ = [
    "Aihub538P6LabelPreparationError",
    "Aihub538P6LabelPreparationRequest",
    "Aihub538P6LabelPreparationService",
]
