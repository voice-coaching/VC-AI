"""Transactional AI Hub 538 non-locked training archive smoke pipeline.

Only a completed ``download.tar`` plus its credential-free secure-download
receipt may enter this service.  The service deliberately has no partial-file
or locked-partition discovery code.  Archive-controlled names remain private
inside descriptor traversal and are never copied into returned or persisted
receipts.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from ..adapters.aihub538_identity_key_store import (
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key_descriptor,
)
from ..adapters.aihub538_split_tar_archive import (
    InnerTarZipRepackReceipt,
    NamedSplitTarPart,
    SplitTarMergeReceipt,
    merge_split_tar_parts,
    repack_verified_inner_tar_to_zip,
)
from ..adapters.aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    NamedSplitZipPart,
    SplitZipMergeReceipt,
    TarExtractionReceipt,
    VerifiedAihub538Download,
    VerifiedAihub538NonlockedShardDownload,
    ZipValidationReceipt,
    aihub538_archive_security_policy_for_shard,
    extract_verified_tar_descriptor,
    merge_split_zip_parts,
    open_validated_zip_descriptor,
    validate_zip_descriptor,
    verify_aihub538_training_download,
    verify_aihub538_nonlocked_shard_download,
)
from ..adapters.aihub538_training_dataset import Aihub538TrainingDatasetAdapter
from ..models.aihub538_label_selection_authorization import (
    Aihub538LabelSelectionAuthorizationReceipt,
    require_aihub538_frozen_source_authorization_receipt,
)
from ..models.aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from ..models.aihub538_p6_remediation import Aihub538P6RemediationAuditReceipt
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.integrity import sha256_document
from .aihub538_training_smoke import (
    Aihub538TrainingSmokeError,
    Aihub538TrainingSmokeEvidence,
    Aihub538TrainingSmokeService,
)


_COMPLETED_ARCHIVE_NAME = "download.tar"
_COMPLETED_RECEIPT_NAME = "download.receipt.json"
_SPLIT_PART_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.zip)\.part(?P<offset>[0-9]+)$"
)
_SPLIT_ARCHIVE_PATTERN = re.compile(
    r"^(?P<stem>[^/\\\x00]+\.(?P<kind>zip|tar))\.part(?P<offset>[0-9]+)$"
)
_LABEL_TOP_LEVEL_KEYS = {
    "Audio_env",
    "Audio_info",
    "Bounding_box_info",
    "Sentence_info",
    "Video_env",
    "Video_info",
    "dataSet",
    "speaker_info",
}
_LABEL_ROLE_COMPONENT = "TL174"
_SOURCE_ROLE_COMPONENT = "TS174"
_MAXIMUM_ROLE_PROBE_LABEL_BYTES = 32 * 1024 * 1024
_RECEIPT_FILE_NAMES = (
    "00-download-verification.json",
    "10-outer-tar-extraction.json",
    "20-split-zip-merge.json",
    "30-zip-validation.json",
    "40-semantic-role-classification.json",
    "50-dataset-admission.json",
    "60-metadata-smoke.json",
    "99-completion.json",
)

_Aihub538CapacityReceipt = (
    Aihub538LabelSelectionAuthorizationReceipt | Aihub538P6RemediationAuditReceipt
)
_GENERIC_MERGE_RECEIPT_NAME = "20-split-archive-merge.json"
_INNER_TAR_REPACK_RECEIPT_NAME = "25-inner-tar-repack.json"


class Aihub538TrainingPipelineError(RuntimeError):
    """Path-free, restricted-data-free pipeline failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538SemanticRoleReceipt:
    """Aggregate proof that exactly one label and one source ZIP were found."""

    label_member_count: int
    source_member_count: int
    source_mp4_count: int
    source_wav_count: int
    shard_id: str = AIHUB538_TS174_TL174_SHARD.shard_id
    dataset_partition: str = AIHUB538_TS174_TL174_SHARD.dataset_partition

    def to_json_object(self) -> dict[str, object]:
        document = {
            "schema_version": (
                "voice_coach.aihub538_semantic_archive_role_receipt.v1"
            ),
            "candidate_archive_count": 2,
            "label_archive_count": 1,
            "source_archive_count": 1,
            "label_member_count": self.label_member_count,
            "source_member_count": self.source_member_count,
            "source_mp4_count": self.source_mp4_count,
            "source_wav_count": self.source_wav_count,
            "role_assignment_basis": (
                "validated_zip_payload_schema_and_media_envelope"
            ),
            "dataset_role_components_validated_privately": True,
            "outer_member_names_serialized": False,
            "zip_member_names_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "participant_identifiers_serialized": False,
            "aggregate_only": True,
            "locked_partition_accessed": False,
        }
        if self.shard_id != AIHUB538_TS174_TL174_SHARD.shard_id:
            document["schema_version"] = (
                "voice_coach.aihub538_semantic_archive_role_receipt.v2"
            )
            document["shard_id"] = self.shard_id
            document["dataset_partition"] = self.dataset_partition
        return document


@dataclass(frozen=True, slots=True)
class Aihub538TrainingPipelineEvidence:
    """All aggregate receipts issued by one completed transactional run."""

    download_verification: (
        VerifiedAihub538Download | VerifiedAihub538NonlockedShardDownload
    )
    tar_extraction: TarExtractionReceipt
    split_merge_receipts: tuple[
        SplitZipMergeReceipt | SplitTarMergeReceipt,
        SplitZipMergeReceipt | SplitTarMergeReceipt,
    ]
    inner_tar_repack_receipts: tuple[InnerTarZipRepackReceipt, ...]
    label_zip_validation: ZipValidationReceipt
    source_zip_validation: ZipValidationReceipt
    semantic_role_receipt: Aihub538SemanticRoleReceipt
    smoke_evidence: Aihub538TrainingSmokeEvidence
    completion_receipt_sha256: str
    shard_spec: Aihub538NonlockedShardSpec = AIHUB538_TS174_TL174_SHARD
    capacity_audit_receipt_sha256: str | None = None

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json_object()
        document["completion_receipt_sha256"] = self.completion_receipt_sha256
        return document

    def _unsigned_json_object(self) -> dict[str, object]:
        document = {
            "schema_version": "voice_coach.aihub538_training_pipeline_receipt.v2",
            "status": "completed_nonlocked_training_metadata_smoke",
            "dataset_id": 538,
            "dataset_partition": self.shard_spec.dataset_partition,
            "download_verification": self.download_verification.to_json_object(),
            "outer_tar_extraction": self.tar_extraction.to_json_object(),
            "split_archive_merge": _merge_stage_document(
                self.split_merge_receipts
            ),
            "split_zip_merge": _legacy_zip_merge_stage_document(
                self.split_merge_receipts
            ),
            "inner_tar_repack": _repack_stage_document(
                self.inner_tar_repack_receipts
            ),
            "zip_validation": _validation_stage_document(
                (
                    self.label_zip_validation,
                    self.source_zip_validation,
                )
            ),
            "semantic_role_classification": (
                self.semantic_role_receipt.to_json_object()
            ),
            "dataset_admission": (
                self.smoke_evidence.admission_receipt.to_json_object()
            ),
            "metadata_smoke": (
                self.smoke_evidence.evaluator_smoke_receipt.to_json_object()
            ),
            "partial_download_accessed": False,
            "locked_partition_accessed": False,
            "raw_member_names_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "participant_identifiers_serialized": False,
            "output_directory_was_new": True,
            "output_directory_mode": "0700",
            "merged_archive_mode": "0600",
            "receipt_mode": "0400",
            "fsync_completed": True,
            "failure_cleanup_policy": (
                "remove_only_fresh_run_root_then_retry_same_absent_path"
            ),
            "existing_output_overwrite_allowed": False,
            "aggregate_only": True,
            "promotion_authorized": False,
        }
        if self.shard_spec != AIHUB538_TS174_TL174_SHARD:
            document.update(
                {
                    "schema_version": (
                        "voice_coach.aihub538_training_pipeline_receipt.v3"
                    ),
                    "shard_id": self.shard_spec.shard_id,
                    "shard_spec_sha256": self.shard_spec.spec_sha256,
                    "capacity_audit_receipt_sha256": (
                        self.capacity_audit_receipt_sha256
                    ),
                    "capacity_audit_precondition_satisfied": True,
                }
            )
        return document

    @classmethod
    def issue(
        cls,
        *,
        download_verification: (
            VerifiedAihub538Download | VerifiedAihub538NonlockedShardDownload
        ),
        tar_extraction: TarExtractionReceipt,
        split_merge_receipts: tuple[
            SplitZipMergeReceipt | SplitTarMergeReceipt,
            SplitZipMergeReceipt | SplitTarMergeReceipt,
        ],
        inner_tar_repack_receipts: tuple[InnerTarZipRepackReceipt, ...],
        label_zip_validation: ZipValidationReceipt,
        source_zip_validation: ZipValidationReceipt,
        semantic_role_receipt: Aihub538SemanticRoleReceipt,
        smoke_evidence: Aihub538TrainingSmokeEvidence,
        shard_spec: Aihub538NonlockedShardSpec = AIHUB538_TS174_TL174_SHARD,
        capacity_audit_receipt_sha256: str | None = None,
    ) -> "Aihub538TrainingPipelineEvidence":
        draft = cls(
            download_verification=download_verification,
            tar_extraction=tar_extraction,
            split_merge_receipts=split_merge_receipts,
            inner_tar_repack_receipts=inner_tar_repack_receipts,
            label_zip_validation=label_zip_validation,
            source_zip_validation=source_zip_validation,
            semantic_role_receipt=semantic_role_receipt,
            smoke_evidence=smoke_evidence,
            completion_receipt_sha256="0" * 64,
            shard_spec=shard_spec,
            capacity_audit_receipt_sha256=capacity_audit_receipt_sha256,
        )
        return cls(
            download_verification=download_verification,
            tar_extraction=tar_extraction,
            split_merge_receipts=split_merge_receipts,
            inner_tar_repack_receipts=inner_tar_repack_receipts,
            label_zip_validation=label_zip_validation,
            source_zip_validation=source_zip_validation,
            semantic_role_receipt=semantic_role_receipt,
            smoke_evidence=smoke_evidence,
            completion_receipt_sha256=sha256_document(
                draft._unsigned_json_object()
            ),
            shard_spec=shard_spec,
            capacity_audit_receipt_sha256=capacity_audit_receipt_sha256,
        )


@dataclass(frozen=True, slots=True)
class _RoleProbe:
    role: str
    regular_member_count: int
    mp4_count: int = 0
    wav_count: int = 0


@dataclass(frozen=True, slots=True)
class _MergedCandidate:
    descriptor: int
    merge_receipt: SplitZipMergeReceipt | SplitTarMergeReceipt
    repack_receipt: InnerTarZipRepackReceipt | None
    validation_receipt: ZipValidationReceipt
    role_probe: _RoleProbe


@dataclass(frozen=True, slots=True)
class _ArchiveSplitGroup:
    kind: str
    zip_parts: tuple[NamedSplitZipPart, ...] = ()
    tar_parts: tuple[NamedSplitTarPart, ...] = ()


class _DuplicateJsonKeyError(ValueError):
    pass


class Aihub538TrainingPipeline:
    """Run archive verification through admission/smoke as one new output."""

    def __init__(
        self,
        *,
        shard_spec: Aihub538NonlockedShardSpec = AIHUB538_TS174_TL174_SHARD,
        archive_policy: Aihub538ArchiveSecurityPolicy | None = None,
    ) -> None:
        try:
            self._shard_spec = require_aihub538_nonlocked_shard_spec(
                shard_spec,
                require_pair=True,
            )
        except ValueError as error:
            raise Aihub538TrainingPipelineError(str(error)) from None
        self._archive_policy = (
            aihub538_archive_security_policy_for_shard(self._shard_spec)
            if archive_policy is None
            else archive_policy
        )
        self._smoke_service = Aihub538TrainingSmokeService(
            Aihub538TrainingDatasetAdapter(
                archive_policy=self._archive_policy,
                shard_spec=self._shard_spec,
                nonlocked_label_schema_policy_sha256=(
                    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
                    if self._shard_spec
                    in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:]
                    else None
                ),
            )
        )

    def run_from_completed_directory(
        self,
        *,
        input_directory: Path,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
        capacity_audit_receipt: _Aihub538CapacityReceipt | None = None,
    ) -> Aihub538TrainingPipelineEvidence:
        """Open only the two exact completed-download files in a private dir."""

        with open_completed_aihub538_download_directory(input_directory) as opened:
            archive_descriptor, receipt_descriptor = opened
            return self.run(
                archive_descriptor=archive_descriptor,
                receipt_descriptor=receipt_descriptor,
                identity_key_descriptor=identity_key_descriptor,
                output_directory=output_directory,
                dataset_access_permission_confirmed=(
                    dataset_access_permission_confirmed
                ),
                capacity_audit_receipt=capacity_audit_receipt,
            )

    def run(
        self,
        *,
        archive_descriptor: int,
        receipt_descriptor: int,
        identity_key_descriptor: int,
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
        capacity_audit_receipt: _Aihub538CapacityReceipt | None = None,
    ) -> Aihub538TrainingPipelineEvidence:
        """Execute all stages or remove only this call's newly-created root."""

        if dataset_access_permission_confirmed is not True:
            raise Aihub538TrainingPipelineError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        capacity_receipt_sha256 = _validate_capacity_precondition(
            shard_spec=self._shard_spec,
            receipt=capacity_audit_receipt,
        )
        try:
            identity_key = load_aihub538_identity_key_descriptor(
                identity_key_descriptor
            )
            if self._shard_spec == AIHUB538_TS174_TL174_SHARD:
                verified_download = verify_aihub538_training_download(
                    archive_descriptor=archive_descriptor,
                    receipt_descriptor=receipt_descriptor,
                    policy=self._archive_policy,
                )
            else:
                verified_download = verify_aihub538_nonlocked_shard_download(
                    archive_descriptor=archive_descriptor,
                    receipt_descriptor=receipt_descriptor,
                    shard_spec=self._shard_spec,
                    policy=self._archive_policy,
                )
        except (Aihub538ArchiveError, Aihub538IdentityKeyStoreError) as error:
            raise Aihub538TrainingPipelineError(error.reason_code) from None

        root_descriptor: int | None = None
        parent_descriptor: int | None = None
        root_name: str | None = None
        try:
            root_descriptor, parent_descriptor, root_name = _create_run_root(
                output_directory
            )
            _persist_json_receipt(
                root_descriptor,
                _RECEIPT_FILE_NAMES[0],
                verified_download.to_json_object(),
            )

            tar_receipt = extract_verified_tar_descriptor(
                archive_descriptor=archive_descriptor,
                destination=output_directory / "outer",
                policy=self._archive_policy,
            )
            if tar_receipt.archive_sha256 != verified_download.archive_sha256:
                raise Aihub538TrainingPipelineError(
                    "aihub538_verified_tar_lineage_mismatch"
                )
            _persist_json_receipt(
                root_descriptor,
                _RECEIPT_FILE_NAMES[1],
                tar_receipt.to_json_object(),
            )

            candidates = self._merge_validate_and_classify(
                root_descriptor=root_descriptor,
                output_directory=output_directory,
            )
            try:
                ordered_merge_receipts = tuple(
                    sorted(
                        (item.merge_receipt for item in candidates),
                        key=lambda receipt: receipt.merged_sha256,
                    )
                )
                if len(ordered_merge_receipts) != 2:
                    raise AssertionError("unreachable candidate count")
                split_merge_receipts = (
                    ordered_merge_receipts[0],
                    ordered_merge_receipts[1],
                )
                merge_receipt_name = (
                    _RECEIPT_FILE_NAMES[2]
                    if all(
                        isinstance(receipt, SplitZipMergeReceipt)
                        for receipt in split_merge_receipts
                    )
                    else _GENERIC_MERGE_RECEIPT_NAME
                )
                _persist_json_receipt(
                    root_descriptor,
                    merge_receipt_name,
                    _merge_stage_document(split_merge_receipts),
                )

                inner_tar_repack_receipts = tuple(
                    sorted(
                        (
                            item.repack_receipt
                            for item in candidates
                            if item.repack_receipt is not None
                        ),
                        key=lambda receipt: receipt.repacked_zip_sha256,
                    )
                )
                if inner_tar_repack_receipts:
                    _persist_json_receipt(
                        root_descriptor,
                        _INNER_TAR_REPACK_RECEIPT_NAME,
                        _repack_stage_document(inner_tar_repack_receipts),
                    )

                validations = tuple(
                    sorted(
                        (item.validation_receipt for item in candidates),
                        key=lambda receipt: receipt.archive_sha256,
                    )
                )
                _persist_json_receipt(
                    root_descriptor,
                    _RECEIPT_FILE_NAMES[3],
                    _validation_stage_document(validations),
                )

                label = _one_candidate_for_role(candidates, "label")
                source = _one_candidate_for_role(candidates, "source")
                role_receipt = Aihub538SemanticRoleReceipt(
                    label_member_count=label.role_probe.regular_member_count,
                    source_member_count=source.role_probe.regular_member_count,
                    source_mp4_count=source.role_probe.mp4_count,
                    source_wav_count=source.role_probe.wav_count,
                    shard_id=self._shard_spec.shard_id,
                    dataset_partition=self._shard_spec.dataset_partition,
                )
                _persist_json_receipt(
                    root_descriptor,
                    _RECEIPT_FILE_NAMES[4],
                    role_receipt.to_json_object(),
                )

                smoke_evidence = self._smoke_service.evaluate(
                    label_zip_descriptor=label.descriptor,
                    source_zip_descriptor=source.descriptor,
                    dataset_access_permission_confirmed=True,
                    identity_hash_key=identity_key.key,
                )
                expected_capacity_lineage = _capacity_label_lineage(
                    shard_spec=self._shard_spec,
                    receipt=capacity_audit_receipt,
                )
                if expected_capacity_lineage is not None and (
                    expected_capacity_lineage[0]
                    != smoke_evidence.admission_receipt.label_archive_sha256
                    or expected_capacity_lineage[1]
                    != smoke_evidence.admission_receipt.label_zip_content_root_sha256
                    or expected_capacity_lineage[2]
                    != smoke_evidence.admission_receipt.identity_namespace_commitment_sha256
                ):
                    raise Aihub538TrainingPipelineError(
                        "aihub538_capacity_admission_lineage_mismatch"
                    )
                _persist_json_receipt(
                    root_descriptor,
                    _RECEIPT_FILE_NAMES[5],
                    smoke_evidence.admission_receipt.to_json_object(),
                )
                _persist_json_receipt(
                    root_descriptor,
                    _RECEIPT_FILE_NAMES[6],
                    smoke_evidence.evaluator_smoke_receipt.to_json_object(),
                )
                evidence = Aihub538TrainingPipelineEvidence.issue(
                    download_verification=verified_download,
                    tar_extraction=tar_receipt,
                    split_merge_receipts=split_merge_receipts,
                    inner_tar_repack_receipts=inner_tar_repack_receipts,
                    label_zip_validation=label.validation_receipt,
                    source_zip_validation=source.validation_receipt,
                    semantic_role_receipt=role_receipt,
                    smoke_evidence=smoke_evidence,
                    shard_spec=self._shard_spec,
                    capacity_audit_receipt_sha256=capacity_receipt_sha256,
                )
                _persist_json_receipt(
                    root_descriptor,
                    _RECEIPT_FILE_NAMES[7],
                    evidence.to_json_object(),
                )
                os.fsync(root_descriptor)
            finally:
                for candidate in candidates:
                    os.close(candidate.descriptor)
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
                except Aihub538TrainingPipelineError:
                    cleanup_failed = True
            if root_descriptor is not None:
                os.close(root_descriptor)
                root_descriptor = None
            if parent_descriptor is not None:
                os.close(parent_descriptor)
                parent_descriptor = None
            if cleanup_failed:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_failure_cleanup_failed"
                ) from None
            if isinstance(error, Aihub538TrainingPipelineError):
                raise error
            if isinstance(
                error,
                (
                    Aihub538ArchiveError,
                    Aihub538IdentityKeyStoreError,
                    Aihub538TrainingSmokeError,
                ),
            ):
                raise Aihub538TrainingPipelineError(error.reason_code) from None
            if not isinstance(error, Exception):
                raise
            raise Aihub538TrainingPipelineError(
                "aihub538_training_pipeline_internal_failure"
            ) from None
        else:
            assert root_descriptor is not None
            assert parent_descriptor is not None
            os.close(root_descriptor)
            os.close(parent_descriptor)
            return evidence

    def _merge_validate_and_classify(
        self,
        *,
        root_descriptor: int,
        output_directory: Path,
    ) -> tuple[_MergedCandidate, _MergedCandidate]:
        outer_descriptor = _open_private_directory_at(root_descriptor, "outer")
        groups: tuple[_ArchiveSplitGroup, ...] = ()
        try:
            groups = _discover_exact_archive_groups(
                outer_descriptor,
                policy=self._archive_policy,
            )
        finally:
            os.close(outer_descriptor)
        candidate_descriptors: list[int] = []
        candidates: list[_MergedCandidate] = []
        try:
            for index, group in enumerate(groups):
                repack_receipt: InnerTarZipRepackReceipt | None = None
                if group.kind == "zip":
                    merged_path = output_directory / f"candidate-{index}.zip"
                    merge_receipt = merge_split_zip_parts(
                        parts=group.zip_parts,
                        destination=merged_path,
                        policy=self._archive_policy,
                    )
                elif group.kind == "tar":
                    merged_tar_path = output_directory / f"candidate-{index}.tar"
                    merge_receipt = merge_split_tar_parts(
                        parts=group.tar_parts,
                        destination=merged_tar_path,
                        policy=self._archive_policy,
                    )
                    tar_descriptor = os.open(
                        f"candidate-{index}.tar",
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=root_descriptor,
                    )
                    try:
                        repack_receipt, repack_validation = (
                            repack_verified_inner_tar_to_zip(
                                archive_descriptor=tar_descriptor,
                                destination=(
                                    output_directory / f"candidate-{index}.zip"
                                ),
                                policy=self._archive_policy,
                            )
                        )
                    finally:
                        os.close(tar_descriptor)
                    if (
                        repack_receipt.source_tar_sha256
                        != merge_receipt.merged_sha256
                    ):
                        raise Aihub538TrainingPipelineError(
                            "aihub538_merged_tar_lineage_mismatch"
                        )
                    merged_path = output_directory / f"candidate-{index}.zip"
                else:
                    raise AssertionError("unreachable split archive kind")
                descriptor = os.open(
                    f"candidate-{index}.zip",
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=root_descriptor,
                )
                candidate_descriptors.append(descriptor)
                validation = validate_zip_descriptor(
                    descriptor,
                    policy=self._archive_policy,
                )
                if group.kind == "zip" and (
                    validation.archive_sha256 != merge_receipt.merged_sha256
                ):
                    raise Aihub538TrainingPipelineError(
                        "aihub538_merged_zip_lineage_mismatch"
                    )
                if group.kind == "tar" and (
                    repack_receipt is None
                    or validation != repack_validation
                    or validation.archive_sha256
                    != repack_receipt.repacked_zip_sha256
                ):
                    raise Aihub538TrainingPipelineError(
                        "aihub538_repacked_zip_lineage_mismatch"
                    )
                candidates.append(
                    _MergedCandidate(
                        descriptor=descriptor,
                        merge_receipt=merge_receipt,
                        repack_receipt=repack_receipt,
                        validation_receipt=validation,
                        role_probe=_probe_semantic_role(
                            descriptor,
                            policy=self._archive_policy,
                            label_role_component=(
                                self._shard_spec.label_role_component
                            ),
                            source_role_component=(
                                self._shard_spec.source_role_component
                            ),
                        ),
                    )
                )
            if sorted(item.role_probe.role for item in candidates) != [
                "label",
                "source",
            ]:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_semantic_role_pair_invalid"
                )
            result = (candidates[0], candidates[1])
            candidate_descriptors.clear()
            return result
        finally:
            for group in groups:
                for part in group.zip_parts + group.tar_parts:
                    os.close(part.descriptor)
            for descriptor in candidate_descriptors:
                os.close(descriptor)


@contextmanager
def open_completed_aihub538_download_directory(
    input_directory: Path,
) -> Iterator[tuple[int, int]]:
    """Open exact completed names without listing or probing partial/locked files."""

    if not input_directory.is_absolute():
        raise Aihub538TrainingPipelineError(
            "aihub538_completed_input_directory_invalid"
        )
    directory_descriptor: int | None = None
    archive_descriptor: int | None = None
    receipt_descriptor: int | None = None
    try:
        if input_directory.resolve(strict=True) != input_directory:
            raise Aihub538TrainingPipelineError(
                "aihub538_completed_input_directory_invalid"
            )
        directory_descriptor = os.open(
            input_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        metadata = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise Aihub538TrainingPipelineError(
                "aihub538_completed_input_directory_invalid"
            )
        archive_descriptor = os.open(
            _COMPLETED_ARCHIVE_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        receipt_descriptor = os.open(
            _COMPLETED_RECEIPT_NAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        yield archive_descriptor, receipt_descriptor
    except Aihub538TrainingPipelineError:
        raise
    except OSError:
        raise Aihub538TrainingPipelineError(
            "aihub538_completed_download_missing_or_invalid"
        ) from None
    finally:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
        if archive_descriptor is not None:
            os.close(archive_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _merge_stage_document(
    receipts: Sequence[SplitZipMergeReceipt | SplitTarMergeReceipt],
) -> dict[str, object]:
    ordered = sorted(receipts, key=lambda receipt: receipt.merged_sha256)
    return {
        "schema_version": "voice_coach.aihub538_split_archive_merge_stage.v2",
        "archive_count": len(ordered),
        "archives": [receipt.to_json_object() for receipt in ordered],
        "archive_kinds": sorted(
            {
                "inner_posix_tar"
                if isinstance(receipt, SplitTarMergeReceipt)
                else "zip"
                for receipt in ordered
            }
        ),
        "byte_offset_suffix_semantics": True,
        "role_assignment_deferred_until_semantic_probe": True,
        "outer_member_names_serialized": False,
        "aggregate_only": True,
    }


def _legacy_zip_merge_stage_document(
    receipts: Sequence[SplitZipMergeReceipt | SplitTarMergeReceipt],
) -> dict[str, object]:
    if all(isinstance(receipt, SplitZipMergeReceipt) for receipt in receipts):
        ordered = sorted(receipts, key=lambda receipt: receipt.merged_sha256)
        return {
            "schema_version": "voice_coach.aihub538_split_zip_merge_stage.v1",
            "archive_count": len(ordered),
            "archives": [receipt.to_json_object() for receipt in ordered],
            "role_assignment_deferred_until_semantic_probe": True,
            "outer_member_names_serialized": False,
            "aggregate_only": True,
        }
    return {
        "schema_version": "voice_coach.aihub538_split_zip_merge_stage.v1",
        "status": "not_applicable_split_inner_tar_input",
        "archive_count": 0,
        "archives": [],
        "outer_member_names_serialized": False,
        "aggregate_only": True,
    }


def _repack_stage_document(
    receipts: Sequence[InnerTarZipRepackReceipt],
) -> dict[str, object]:
    ordered = sorted(receipts, key=lambda receipt: receipt.repacked_zip_sha256)
    return {
        "schema_version": "voice_coach.aihub538_inner_tar_repack_stage.v1",
        "status": (
            "completed" if ordered else "not_applicable_split_zip_input"
        ),
        "archive_count": len(ordered),
        "archives": [receipt.to_json_object() for receipt in ordered],
        "raw_member_names_serialized": False,
        "aggregate_only": True,
    }


def _validation_stage_document(
    receipts: Sequence[ZipValidationReceipt],
) -> dict[str, object]:
    ordered = sorted(receipts, key=lambda receipt: receipt.archive_sha256)
    return {
        "schema_version": "voice_coach.aihub538_zip_validation_stage.v1",
        "archive_count": len(ordered),
        "archives": [receipt.to_json_object() for receipt in ordered],
        "member_names_serialized": False,
        "aggregate_only": True,
    }


def _probe_semantic_role(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
    label_role_component: str = _LABEL_ROLE_COMPONENT,
    source_role_component: str = _SOURCE_ROLE_COMPONENT,
) -> _RoleProbe:
    try:
        with open_validated_zip_descriptor(descriptor, policy=policy) as opened:
            archive, infos, names, _ = opened
            regular = tuple(
                (info, PurePosixPath(name))
                for info, name in zip(infos, names, strict=True)
                if not info.is_dir()
            )
            suffixes = {path.suffix for _, path in regular}
            if suffixes == {".json"} and all(
                path.parts.count(label_role_component) == 1
                for _, path in regular
            ):
                for info, _ in regular:
                    if not 0 < info.file_size <= _MAXIMUM_ROLE_PROBE_LABEL_BYTES:
                        return _RoleProbe("unsupported", len(regular))
                    with archive.open(info, "r") as source:
                        payload = source.read(_MAXIMUM_ROLE_PROBE_LABEL_BYTES + 1)
                    if len(payload) != info.file_size or not _is_label_record(payload):
                        return _RoleProbe("unsupported", len(regular))
                return _RoleProbe("label", len(regular))
            if suffixes <= {".mp4", ".wav"} and all(
                path.parts.count(source_role_component) == 1
                for _, path in regular
            ):
                mp4_count = 0
                wav_count = 0
                for info, path in regular:
                    with archive.open(info, "r") as source:
                        if path.suffix == ".mp4":
                            envelope = source.read(16)
                            if len(envelope) < 12 or envelope[4:8] != b"ftyp":
                                return _RoleProbe("unsupported", len(regular))
                            mp4_count += 1
                        elif path.suffix == ".wav":
                            envelope = source.read(12)
                            if (
                                len(envelope) != 12
                                or envelope[:4] not in {b"RIFF", b"RF64"}
                                or envelope[8:12] != b"WAVE"
                            ):
                                return _RoleProbe("unsupported", len(regular))
                            wav_count += 1
                if mp4_count > 0 and wav_count > 0:
                    return _RoleProbe(
                        "source",
                        len(regular),
                        mp4_count=mp4_count,
                        wav_count=wav_count,
                    )
    except (Aihub538ArchiveError, OSError, RuntimeError, ValueError):
        return _RoleProbe("unsupported", 0)
    return _RoleProbe("unsupported", 0)


def _is_label_record(payload: bytes) -> bool:
    def reject_duplicate_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKeyError(key)
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
        ValueError,
    ):
        return False
    return (
        isinstance(document, list)
        and len(document) == 1
        and isinstance(document[0], dict)
        and set(document[0]) == _LABEL_TOP_LEVEL_KEYS
    )


def _one_candidate_for_role(
    candidates: Sequence[_MergedCandidate],
    role: str,
) -> _MergedCandidate:
    selected = [item for item in candidates if item.role_probe.role == role]
    if len(selected) != 1:
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_semantic_role_pair_invalid"
        )
    return selected[0]


def _discover_exact_archive_groups(
    outer_descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> tuple[_ArchiveSplitGroup, _ArchiveSplitGroup]:
    zip_groups: dict[
        tuple[tuple[str, ...], str],
        list[NamedSplitZipPart],
    ] = {}
    tar_groups: dict[
        tuple[tuple[str, ...], str],
        list[NamedSplitTarPart],
    ] = {}
    opened: list[int] = []

    def visit(directory_descriptor: int, parents: tuple[str, ...]) -> None:
        try:
            names = sorted(os.listdir(directory_descriptor))
        except OSError:
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_outer_traversal_failed"
            ) from None
        for name in names:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_traversal_failed"
                ) from None
            if stat.S_ISDIR(metadata.st_mode):
                child = _open_private_directory_at(directory_descriptor, name)
                try:
                    visit(child, parents + (name,))
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_member_type_invalid"
                )
            match = _SPLIT_ARCHIVE_PATTERN.fullmatch(name)
            if match is None:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_split_part_invalid"
                )
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_split_part_invalid"
                ) from None
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
        group_count = len(zip_groups) + len(tar_groups)
        if group_count != 2 or (zip_groups and tar_groups):
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_split_archive_pair_invalid"
            )
        if zip_groups:
            if any(
                not 1 <= len(parts) <= policy.maximum_split_part_count
                for parts in zip_groups.values()
            ):
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_split_archive_pair_invalid"
                )
            ordered = tuple(
                _ArchiveSplitGroup(kind="zip", zip_parts=tuple(zip_groups[key]))
                for key in sorted(zip_groups)
            )
        else:
            if any(
                not 1 <= len(parts) <= policy.maximum_split_part_count
                for parts in tar_groups.values()
            ):
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_split_archive_pair_invalid"
                )
            ordered = tuple(
                _ArchiveSplitGroup(kind="tar", tar_parts=tuple(tar_groups[key]))
                for key in sorted(tar_groups)
            )
        result = (ordered[0], ordered[1])
        opened.clear()
        return result
    finally:
        for descriptor in opened:
            os.close(descriptor)


def _discover_exact_split_groups(
    outer_descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> tuple[tuple[NamedSplitZipPart, ...], tuple[NamedSplitZipPart, ...]]:
    groups: dict[tuple[tuple[str, ...], str], list[NamedSplitZipPart]] = {}
    opened: list[int] = []

    def visit(directory_descriptor: int, parents: tuple[str, ...]) -> None:
        try:
            names = sorted(os.listdir(directory_descriptor))
        except OSError:
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_outer_traversal_failed"
            ) from None
        for name in names:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_traversal_failed"
                ) from None
            if stat.S_ISDIR(metadata.st_mode):
                child = _open_private_directory_at(directory_descriptor, name)
                try:
                    visit(child, parents + (name,))
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_member_type_invalid"
                )
            match = _SPLIT_PART_PATTERN.fullmatch(name)
            if match is None:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_split_part_invalid"
                )
            try:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise Aihub538TrainingPipelineError(
                    "aihub538_pipeline_outer_split_part_invalid"
                ) from None
            opened.append(descriptor)
            key = (parents, match.group("stem"))
            groups.setdefault(key, []).append(
                NamedSplitZipPart(name=name, descriptor=descriptor)
            )

    try:
        visit(outer_descriptor, ())
        if len(groups) != 2:
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_split_archive_pair_invalid"
            )
        if any(
            not 1 <= len(parts) <= policy.maximum_split_part_count
            for parts in groups.values()
        ):
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_split_archive_pair_invalid"
            )
        # Sorting is private and only makes neutral candidate output stable.  The
        # merge primitive, not this traversal, interprets byte-offset suffixes.
        ordered_groups = tuple(
            tuple(groups[key]) for key in sorted(groups)
        )
        result = (ordered_groups[0], ordered_groups[1])
        opened.clear()
        return result
    finally:
        for descriptor in opened:
            os.close(descriptor)


def _create_run_root(destination: Path) -> tuple[int, int, str]:
    if not destination.is_absolute() or destination.name in {"", ".", ".."}:
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_output_directory_invalid"
        )
    parent_descriptor: int | None = None
    root_descriptor: int | None = None
    created = False
    try:
        if destination.parent.resolve(strict=True) != destination.parent:
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_output_directory_invalid"
            )
        parent_descriptor = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        os.mkdir(destination.name, 0o700, dir_fd=parent_descriptor)
        created = True
        root_descriptor = os.open(
            destination.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        os.fchmod(root_descriptor, 0o700)
        os.fsync(parent_descriptor)
        return root_descriptor, parent_descriptor, destination.name
    except Aihub538TrainingPipelineError:
        raise
    except OSError:
        if root_descriptor is not None:
            os.close(root_descriptor)
        if created and parent_descriptor is not None:
            try:
                os.rmdir(destination.name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except OSError:
                pass
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_output_directory_not_new"
        ) from None


def _persist_json_receipt(
    root_descriptor: int,
    name: str,
    document: Mapping[str, object],
) -> None:
    payload = (
        json.dumps(
            dict(document),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_descriptor,
        )
        created = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        os.fsync(root_descriptor)
    except (OSError, TypeError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created:
            try:
                os.unlink(name, dir_fd=root_descriptor)
                os.fsync(root_descriptor)
            except OSError:
                pass
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_receipt_write_failed"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _open_private_directory_at(parent_descriptor: int, name: str) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            os.close(descriptor)
            raise Aihub538TrainingPipelineError(
                "aihub538_pipeline_private_directory_invalid"
            )
        return descriptor
    except Aihub538TrainingPipelineError:
        raise
    except OSError:
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_private_directory_invalid"
        ) from None


def _remove_created_run_root(
    *,
    root_descriptor: int,
    parent_descriptor: int,
    root_name: str,
) -> None:
    try:
        _remove_directory_contents(root_descriptor)
        os.rmdir(root_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        raise Aihub538TrainingPipelineError(
            "aihub538_pipeline_failure_cleanup_failed"
        ) from None


def _remove_directory_contents(directory_descriptor: int) -> None:
    for name in os.listdir(directory_descriptor):
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            try:
                _remove_directory_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
        else:
            os.unlink(name, dir_fd=directory_descriptor)
    os.fsync(directory_descriptor)


def _validate_capacity_precondition(
    *,
    shard_spec: Aihub538NonlockedShardSpec,
    receipt: _Aihub538CapacityReceipt | None,
) -> str | None:
    if shard_spec == AIHUB538_TS174_TL174_SHARD:
        if receipt is not None:
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_receipt_not_applicable"
            )
        return None
    if (
        shard_spec not in AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS
        or receipt is None
    ):
        raise Aihub538TrainingPipelineError(
            "aihub538_capacity_audit_required"
        )
    try:
        receipt._validate()
    except ValueError:
        raise Aihub538TrainingPipelineError(
            "aihub538_capacity_audit_invalid"
        ) from None
    if not receipt.is_feasible:
        raise Aihub538TrainingPipelineError(
            "aihub538_capacity_audit_infeasible"
        )
    _capacity_label_lineage(shard_spec=shard_spec, receipt=receipt)
    return receipt.receipt_sha256


def _capacity_label_lineage(
    *,
    shard_spec: Aihub538NonlockedShardSpec,
    receipt: _Aihub538CapacityReceipt | None,
) -> tuple[str, str, str] | None:
    if shard_spec == AIHUB538_TS174_TL174_SHARD:
        return None
    if isinstance(receipt, Aihub538LabelSelectionAuthorizationReceipt):
        try:
            require_aihub538_frozen_source_authorization_receipt(receipt)
        except ValueError:
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_audit_release_pin_mismatch"
            ) from None
        if not receipt.is_feasible:
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_shard_not_authorized"
            )
        try:
            index = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS.index(
                shard_spec
            )
            lineage = receipt.prefix_lineage[index]
        except (ValueError, IndexError):
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_shard_not_authorized"
            ) from None
        if (
            lineage.label_role_component != shard_spec.label_role_component
            or lineage.label_file_key != shard_spec.ordered_file_keys[0]
        ):
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_shard_not_authorized"
            )
        return (
            lineage.label_archive_sha256,
            lineage.label_content_root_sha256,
            receipt.identity_namespace_commitment_sha256,
        )
    if isinstance(receipt, Aihub538P6RemediationAuditReceipt):
        try:
            receipt._validate()
            if not receipt.is_feasible:
                raise ValueError
            index = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS.index(shard_spec)
            lineage = receipt.evaluated_prefix_lineage[index - 2]
        except (ValueError, IndexError):
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_shard_not_authorized"
            ) from None
        if (
            index < 2
            or lineage.label_role_component != shard_spec.label_role_component
            or lineage.label_file_key != shard_spec.ordered_file_keys[0]
        ):
            raise Aihub538TrainingPipelineError(
                "aihub538_capacity_shard_not_authorized"
            )
        return (
            lineage.label_archive_sha256,
            lineage.label_content_root_sha256,
            receipt.identity_namespace_commitment_sha256,
        )
    raise Aihub538TrainingPipelineError(
        "aihub538_capacity_audit_invalid"
    )


__all__ = [
    "Aihub538SemanticRoleReceipt",
    "Aihub538TrainingPipeline",
    "Aihub538TrainingPipelineError",
    "Aihub538TrainingPipelineEvidence",
    "open_completed_aihub538_download_directory",
]
