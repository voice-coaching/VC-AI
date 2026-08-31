"""Aggregate contract for preparing one remediation label-only archive."""

from __future__ import annotations

from dataclasses import dataclass

from .aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from .aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from .integrity import require_sha256, sha256_document


AIHUB538_P6_LABEL_PREPARATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_p6_remediation_label_preparation.v2"
)
AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS = (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:]
)


@dataclass(frozen=True, slots=True)
class Aihub538P6LabelPreparationReceipt:
    """Privacy-safe binding from one completed download to ``label.zip``."""

    shard_id: str
    label_role_component: str
    label_file_key: str
    shard_spec_sha256: str
    download_archive_sha256: str
    secure_download_receipt_sha256: str
    download_verification_stage_sha256: str
    outer_content_root_sha256: str
    outer_extraction_stage_sha256: str
    split_archive_kind: str
    split_merge_receipt_sha256: str
    inner_tar_repack_receipt_sha256: str | None
    label_zip_sha256: str
    label_zip_content_root_sha256: str
    label_zip_validation_stage_sha256: str
    label_admission_receipt_sha256: str
    label_member_count: int
    actor_count: int
    attempt_count: int
    metadata_excluded_label_count: int
    terminal_media_overrun_label_count: int
    terminal_frame_tail_exception_label_count: int
    maximum_complete_tail_frames_observed: int
    nonterminal_media_overrun_label_count: int
    sentence_overlap_label_count: int
    invalid_time_label_count: int
    nonconforming_fps_label_count: int
    terminal_frame_tail_policy_sha256: str
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        shard_spec: Aihub538NonlockedShardSpec,
        download_archive_sha256: str,
        secure_download_receipt_sha256: str,
        download_verification_stage_sha256: str,
        outer_content_root_sha256: str,
        outer_extraction_stage_sha256: str,
        split_archive_kind: str,
        split_merge_receipt_sha256: str,
        inner_tar_repack_receipt_sha256: str | None,
        label_zip_sha256: str,
        label_zip_content_root_sha256: str,
        label_zip_validation_stage_sha256: str,
        label_admission_receipt_sha256: str,
        label_member_count: int,
        actor_count: int,
        attempt_count: int,
        metadata_excluded_label_count: int,
        terminal_media_overrun_label_count: int,
        terminal_frame_tail_exception_label_count: int,
        maximum_complete_tail_frames_observed: int,
        nonterminal_media_overrun_label_count: int,
        sentence_overlap_label_count: int,
        invalid_time_label_count: int,
        nonconforming_fps_label_count: int,
        terminal_frame_tail_policy_sha256: str,
        identity_namespace_commitment_sha256: str,
        member_namespace_commitment_sha256: str,
    ) -> "Aihub538P6LabelPreparationReceipt":
        validated = require_aihub538_nonlocked_shard_spec(
            shard_spec,
            require_label_audit=True,
        )
        if validated not in AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS:
            raise ValueError("aihub538_remediation_label_shard_not_allowed")
        draft = cls(
            shard_id=validated.shard_id,
            label_role_component=validated.label_role_component,
            label_file_key=validated.ordered_file_keys[0],
            shard_spec_sha256=validated.spec_sha256,
            download_archive_sha256=download_archive_sha256,
            secure_download_receipt_sha256=(
                secure_download_receipt_sha256
            ),
            download_verification_stage_sha256=(
                download_verification_stage_sha256
            ),
            outer_content_root_sha256=outer_content_root_sha256,
            outer_extraction_stage_sha256=outer_extraction_stage_sha256,
            split_archive_kind=split_archive_kind,
            split_merge_receipt_sha256=split_merge_receipt_sha256,
            inner_tar_repack_receipt_sha256=(
                inner_tar_repack_receipt_sha256
            ),
            label_zip_sha256=label_zip_sha256,
            label_zip_content_root_sha256=label_zip_content_root_sha256,
            label_zip_validation_stage_sha256=(
                label_zip_validation_stage_sha256
            ),
            label_admission_receipt_sha256=(
                label_admission_receipt_sha256
            ),
            label_member_count=label_member_count,
            actor_count=actor_count,
            attempt_count=attempt_count,
            metadata_excluded_label_count=metadata_excluded_label_count,
            terminal_media_overrun_label_count=(
                terminal_media_overrun_label_count
            ),
            terminal_frame_tail_exception_label_count=(
                terminal_frame_tail_exception_label_count
            ),
            maximum_complete_tail_frames_observed=(
                maximum_complete_tail_frames_observed
            ),
            nonterminal_media_overrun_label_count=(
                nonterminal_media_overrun_label_count
            ),
            sentence_overlap_label_count=sentence_overlap_label_count,
            invalid_time_label_count=invalid_time_label_count,
            nonconforming_fps_label_count=nonconforming_fps_label_count,
            terminal_frame_tail_policy_sha256=(
                terminal_frame_tail_policy_sha256
            ),
            identity_namespace_commitment_sha256=(
                identity_namespace_commitment_sha256
            ),
            member_namespace_commitment_sha256=(
                member_namespace_commitment_sha256
            ),
            receipt_sha256="0" * 64,
        )
        issued = cls(
            **{
                name: getattr(draft, name)
                for name in draft.__dataclass_fields__
                if name != "receipt_sha256"
            },
            receipt_sha256=sha256_document(draft.unsigned_json_object()),
        )
        issued._validate()
        return issued

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str)
            for value in (
                self.shard_id,
                self.label_role_component,
                self.label_file_key,
                self.split_archive_kind,
            )
        ):
            raise ValueError("aihub538_remediation_label_preparation_invalid")
        for name in (
            "shard_spec_sha256",
            "download_archive_sha256",
            "secure_download_receipt_sha256",
            "download_verification_stage_sha256",
            "outer_content_root_sha256",
            "outer_extraction_stage_sha256",
            "split_merge_receipt_sha256",
            "label_zip_sha256",
            "label_zip_content_root_sha256",
            "label_zip_validation_stage_sha256",
            "label_admission_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "terminal_frame_tail_policy_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.inner_tar_repack_receipt_sha256 is not None:
            require_sha256(
                self.inner_tar_repack_receipt_sha256,
                "inner_tar_repack_receipt_sha256",
            )
        counts = (
            self.label_member_count,
            self.actor_count,
            self.attempt_count,
            self.metadata_excluded_label_count,
            self.terminal_media_overrun_label_count,
            self.terminal_frame_tail_exception_label_count,
            self.maximum_complete_tail_frames_observed,
            self.nonterminal_media_overrun_label_count,
            self.sentence_overlap_label_count,
            self.invalid_time_label_count,
            self.nonconforming_fps_label_count,
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in counts
            )
            or self.label_member_count == 0
            or self.actor_count == 0
            or self.attempt_count == 0
            or self.actor_count > self.label_member_count
            or self.attempt_count > self.label_member_count
            or self.metadata_excluded_label_count > self.label_member_count
            or self.terminal_media_overrun_label_count
            > self.label_member_count
            or self.terminal_frame_tail_exception_label_count
            > self.terminal_media_overrun_label_count
            or self.maximum_complete_tail_frames_observed < 0
            or self.nonterminal_media_overrun_label_count != 0
            or self.sentence_overlap_label_count != 0
            or self.invalid_time_label_count != 0
            or self.nonconforming_fps_label_count != 0
            or self.terminal_frame_tail_policy_sha256
            != AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
        ):
            raise ValueError("aihub538_remediation_label_preparation_invalid")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": AIHUB538_P6_LABEL_PREPARATION_SCHEMA_VERSION,
            "status": "completed_remediation_label_preparation",
            "dataset_id": 538,
            "shard_id": self.shard_id,
            "label_role_component": self.label_role_component,
            "label_file_key": self.label_file_key,
            "shard_spec_sha256": self.shard_spec_sha256,
            "download_archive_sha256": self.download_archive_sha256,
            "secure_download_receipt_sha256": (
                self.secure_download_receipt_sha256
            ),
            "download_verification_stage_sha256": (
                self.download_verification_stage_sha256
            ),
            "outer_content_root_sha256": self.outer_content_root_sha256,
            "outer_extraction_stage_sha256": (
                self.outer_extraction_stage_sha256
            ),
            "split_archive_kind": self.split_archive_kind,
            "split_merge_receipt_sha256": self.split_merge_receipt_sha256,
            "inner_tar_repack_receipt_sha256": (
                self.inner_tar_repack_receipt_sha256
            ),
            "label_zip_sha256": self.label_zip_sha256,
            "label_zip_content_root_sha256": (
                self.label_zip_content_root_sha256
            ),
            "label_zip_validation_stage_sha256": (
                self.label_zip_validation_stage_sha256
            ),
            "label_admission_receipt_sha256": (
                self.label_admission_receipt_sha256
            ),
            "label_member_count": self.label_member_count,
            "actor_count": self.actor_count,
            "attempt_count": self.attempt_count,
            "metadata_excluded_label_count": (
                self.metadata_excluded_label_count
            ),
            "terminal_media_overrun_label_count": (
                self.terminal_media_overrun_label_count
            ),
            "terminal_frame_tail_exception_label_count": (
                self.terminal_frame_tail_exception_label_count
            ),
            "maximum_complete_tail_frames_observed": (
                self.maximum_complete_tail_frames_observed
            ),
            "nonterminal_media_overrun_label_count": (
                self.nonterminal_media_overrun_label_count
            ),
            "sentence_overlap_label_count": self.sentence_overlap_label_count,
            "invalid_time_label_count": self.invalid_time_label_count,
            "nonconforming_fps_label_count": (
                self.nonconforming_fps_label_count
            ),
            "terminal_frame_tail_policy_sha256": (
                self.terminal_frame_tail_policy_sha256
            ),
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "member_namespace_commitment_sha256": (
                self.member_namespace_commitment_sha256
            ),
            "output_directory_mode": "0700",
            "label_zip_mode": "0600",
            "receipt_mode": "0400",
            "output_directory_was_new": True,
            "existing_output_overwrite_allowed": False,
            "outer_extraction_workspace_removed": True,
            "partial_download_accessed": False,
            "source_partition_accessed": False,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
            "raw_member_names_serialized": False,
            "participant_identifiers_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "sample_digests_serialized": False,
            "fsync_completed": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    def _validate(self) -> None:
        matches = tuple(
            item
            for item in AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS
            if item.shard_id == self.shard_id
        )
        if len(matches) != 1:
            raise ValueError("aihub538_remediation_label_preparation_invalid")
        shard = matches[0]
        if (
            self.label_role_component != shard.label_role_component
            or self.label_file_key != shard.ordered_file_keys[0]
            or self.shard_spec_sha256 != shard.spec_sha256
            or self.split_archive_kind not in {"zip", "tar"}
            or (
                self.split_archive_kind == "zip"
                and self.inner_tar_repack_receipt_sha256 is not None
            )
            or (
                self.split_archive_kind == "tar"
                and self.inner_tar_repack_receipt_sha256 is None
            )
            or self.receipt_sha256
            != sha256_document(self.unsigned_json_object())
        ):
            raise ValueError("aihub538_remediation_label_preparation_invalid")

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538P6LabelPreparationReceipt":
        if not isinstance(value, dict):
            raise ValueError("aihub538_remediation_label_preparation_invalid")
        expected = {
            "schema_version",
            "status",
            "dataset_id",
            "shard_id",
            "label_role_component",
            "label_file_key",
            "shard_spec_sha256",
            "download_archive_sha256",
            "secure_download_receipt_sha256",
            "download_verification_stage_sha256",
            "outer_content_root_sha256",
            "outer_extraction_stage_sha256",
            "split_archive_kind",
            "split_merge_receipt_sha256",
            "inner_tar_repack_receipt_sha256",
            "label_zip_sha256",
            "label_zip_content_root_sha256",
            "label_zip_validation_stage_sha256",
            "label_admission_receipt_sha256",
            "label_member_count",
            "actor_count",
            "attempt_count",
            "metadata_excluded_label_count",
            "terminal_media_overrun_label_count",
            "terminal_frame_tail_exception_label_count",
            "maximum_complete_tail_frames_observed",
            "nonterminal_media_overrun_label_count",
            "sentence_overlap_label_count",
            "invalid_time_label_count",
            "nonconforming_fps_label_count",
            "terminal_frame_tail_policy_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "output_directory_mode",
            "label_zip_mode",
            "receipt_mode",
            "output_directory_was_new",
            "existing_output_overwrite_allowed",
            "outer_extraction_workspace_removed",
            "partial_download_accessed",
            "source_partition_accessed",
            "locked_partition_accessed",
            "promotion_authorized",
            "raw_member_names_serialized",
            "participant_identifiers_serialized",
            "source_paths_serialized",
            "transcripts_serialized",
            "sample_digests_serialized",
            "fsync_completed",
            "receipt_sha256",
        }
        boolean_fields = {
            "output_directory_was_new",
            "existing_output_overwrite_allowed",
            "outer_extraction_workspace_removed",
            "partial_download_accessed",
            "source_partition_accessed",
            "locked_partition_accessed",
            "promotion_authorized",
            "raw_member_names_serialized",
            "participant_identifiers_serialized",
            "source_paths_serialized",
            "transcripts_serialized",
            "sample_digests_serialized",
            "fsync_completed",
        }
        if (
            set(value) != expected
            or value["schema_version"]
            != AIHUB538_P6_LABEL_PREPARATION_SCHEMA_VERSION
            or value["status"]
            != "completed_remediation_label_preparation"
            or value["dataset_id"] != 538
            or value["output_directory_mode"] != "0700"
            or value["label_zip_mode"] != "0600"
            or value["receipt_mode"] != "0400"
            or any(not isinstance(value[name], bool) for name in boolean_fields)
        ):
            raise ValueError("aihub538_remediation_label_preparation_invalid")
        receipt = cls(
            **{
                name: value[name]
                for name in cls.__dataclass_fields__
            }
        )
        receipt._validate()
        if receipt.to_json_object() != value:
            raise ValueError("aihub538_remediation_label_preparation_invalid")
        return receipt


__all__ = [
    "AIHUB538_P6_LABEL_PREPARATION_SCHEMA_VERSION",
    "AIHUB538_P6_REMEDIATION_LABEL_AUDIT_SHARDS",
    "Aihub538P6LabelPreparationReceipt",
]
