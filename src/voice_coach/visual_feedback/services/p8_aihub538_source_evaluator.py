"""Legacy-fixture AI Hub 538 VS11/VL11 ingestion and P8 evaluation.

The fixed VS11/VL11 shape remains only for privacy/security fixture coverage.
The production entrypoint is fail-closed until the all-32 label discovery
contract has produced a selected-cohort source seal.  A small public-listing
shard is not assumed to contain all required P8 slices.

The only public source entry mode is a deferred completed-download directory.
Directory traversal, receipt reading, TAR extraction, split-archive merging,
and normalized ZIP opening happen only inside
the callback invoked after :class:`P8SealStateStore` has durably persisted the
access transaction.

The service keeps a small aggregate-only run directory.  Restricted extracted
and merged bytes live below a private ``work`` child and are deleted before a
success result returns.  A generic terminal receipt is retained even when the
source evaluator fails; the canonical P8 state store independently retains the
irreversible failed-closed report.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Literal

from ..adapters.aihub538_locked_dataset import (
    Aihub538LockedDatasetEvaluator,
    P8ClaimPolicyReplayReceipt,
    P8FrozenLockedPipeline,
    P8LockedDatasetEvaluationAggregate,
    P8LockedEvaluationCompletenessError,
    p8_frozen_pipeline_pins_sha256,
)
from ..adapters.p8_reference_identity import (
    P8SealedReferenceBundle,
    load_p8_sealed_reference_bundle,
)
from ..adapters.aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    SplitZipMergeReceipt,
    TarExtractionReceipt,
    VerifiedAihub538LockedDownload,
    ZipValidationReceipt,
    extract_verified_tar_descriptor,
    merge_split_zip_parts,
    open_validated_zip_descriptor,
    validate_zip_descriptor,
    verify_aihub538_locked_download,
)
from ..adapters.aihub538_split_tar_archive import (
    InnerTarZipRepackReceipt,
    SplitTarMergeReceipt,
    merge_split_tar_parts,
    repack_verified_inner_tar_to_zip,
)
from .aihub538_training_pipeline import (
    Aihub538TrainingPipelineError,
    _create_run_root,
    _discover_exact_archive_groups,
    _open_private_directory_at,
    _persist_json_receipt,
    open_completed_aihub538_download_directory,
)
from ..adapters.p8_seal_store import P8SealStateStore
from ..models.integrity import (
    require_exact_keys,
    require_integer,
    require_mapping,
    require_sha256,
    sha256_document,
)
from ..models.p8_aggregate_metrics import P8AggregateEvaluationMetrics
from ..models.p8_actual_source_state import (
    P8ActualSourceCleanupState,
    P8ActualSourceCleanupStatus,
    P8ActualSourceFinalResultCommit,
    P8ActualSourceFreshProcessReloadReceipt,
    P8ActualSourceGatePending,
    P8ActualSourceReadZeroSeal,
    P8ActualSourceResultStatus,
)
from ..models.p8_locked_evaluation import (
    P8AccessTransaction,
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from ..models.p8_locked_cohort import (
    require_selected_cohort_source_seal_for_production,
)
from ..models.p8_locked_source_manifest import (
    P8LockedSourceArchiveDigest,
    P8LockedSourceEvaluationResult,
    P8LockedSourceManifest,
)
from .p8_locked_evaluation import P8LockedEvaluationService


_LABEL_ROLE_COMPONENT = "VL11"
_SOURCE_ROLE_COMPONENT = "VS11"
_WORK_DIRECTORY_NAME = "work"
_OUTER_DIRECTORY_NAME = "outer"
_GATE_PENDING_RECEIPT_NAME = "00-gate-pending.json"
_CLEANUP_RECEIPT_NAME = "10-cleanup.json"
_FAILURE_COMPLETENESS_RECEIPT_NAME = "15-failure-completeness.json"
_LINEAGE_RECEIPT_NAME = "20-aggregate-lineage.json"
_TERMINAL_RECEIPT_NAME = "99-terminal.json"
_MAXIMUM_SOURCE_RECEIPT_BYTES = 8 * 1024 * 1024


class P8Aihub538SourceError(RuntimeError):
    """Public path-free failure from the one-shot source boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True, repr=False)
class DeferredP8LockedSource:
    """Directory locator retained privately and opened only after consumption."""

    _input_directory: Path = field(repr=False)

    @classmethod
    def from_completed_directory(cls, directory: Path) -> "DeferredP8LockedSource":
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise P8Aihub538SourceError(
                "p8_locked_completed_input_directory_invalid"
            )
        # Deliberately do not resolve, stat, list, or open the locked directory.
        return cls(_input_directory=directory)

    @contextmanager
    def open_after_consumption(self) -> Iterator[tuple[int, int]]:
        """Open the exact completed names after durable one-shot consumption."""

        try:
            with open_completed_aihub538_download_directory(
                self._input_directory
            ) as opened:
                yield opened
        except Aihub538TrainingPipelineError as error:
            raise P8Aihub538SourceError(
                "p8_locked_completed_download_missing_or_invalid"
            ) from error


@dataclass(frozen=True, slots=True)
class P8LockedWorkspaceCleanupReceipt:
    """Aggregate proof that only the fresh restricted work child was removed."""

    access_transaction_sha256: str
    removed_regular_file_count: int
    removed_directory_count: int
    cleanup_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        access_transaction_sha256: str,
        removed_regular_file_count: int,
        removed_directory_count: int,
    ) -> "P8LockedWorkspaceCleanupReceipt":
        unsigned = {
            "schema_version": "voice_coach.p8_locked_workspace_cleanup.v1",
            "status": "restricted_work_removed",
            "access_transaction_sha256": access_transaction_sha256,
            "removed_regular_file_count": removed_regular_file_count,
            "removed_directory_count": removed_directory_count,
            "restricted_work_directory_removed": True,
            "restricted_media_retained": False,
            "raw_paths_serialized": False,
            "fsync_completed": True,
            "aggregate_only": True,
        }
        return cls(
            access_transaction_sha256=access_transaction_sha256,
            removed_regular_file_count=removed_regular_file_count,
            removed_directory_count=removed_directory_count,
            cleanup_sha256=sha256_document(unsigned),
        )

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json_object()
        document["cleanup_sha256"] = self.cleanup_sha256
        return document

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedWorkspaceCleanupReceipt":
        document = require_mapping(value, "p8_locked_workspace_cleanup")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "access_transaction_sha256",
                "removed_regular_file_count",
                "removed_directory_count",
                "restricted_work_directory_removed",
                "restricted_media_retained",
                "raw_paths_serialized",
                "fsync_completed",
                "aggregate_only",
                "cleanup_sha256",
            },
            "p8_locked_workspace_cleanup",
        )
        if (
            document["schema_version"]
            != "voice_coach.p8_locked_workspace_cleanup.v1"
            or document["status"] != "restricted_work_removed"
            or document["restricted_work_directory_removed"] is not True
            or document["restricted_media_retained"] is not False
            or document["raw_paths_serialized"] is not False
            or document["fsync_completed"] is not True
            or document["aggregate_only"] is not True
        ):
            raise ValueError("p8_locked_cleanup_contract_invalid")
        return cls(
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            removed_regular_file_count=require_integer(
                document["removed_regular_file_count"],
                "removed_regular_file_count",
                minimum=0,
            ),
            removed_directory_count=require_integer(
                document["removed_directory_count"],
                "removed_directory_count",
                minimum=0,
            ),
            cleanup_sha256=require_sha256(
                document["cleanup_sha256"], "cleanup_sha256"
            ),
        )

    def _unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.p8_locked_workspace_cleanup.v1",
            "status": "restricted_work_removed",
            "access_transaction_sha256": self.access_transaction_sha256,
            "removed_regular_file_count": self.removed_regular_file_count,
            "removed_directory_count": self.removed_directory_count,
            "restricted_work_directory_removed": True,
            "restricted_media_retained": False,
            "raw_paths_serialized": False,
            "fsync_completed": True,
            "aggregate_only": True,
        }

    def __post_init__(self) -> None:
        require_sha256(
            self.access_transaction_sha256, "access_transaction_sha256"
        )
        require_sha256(self.cleanup_sha256, "cleanup_sha256")
        for value in (
            self.removed_regular_file_count,
            self.removed_directory_count,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("p8_locked_cleanup_count_invalid")
        if self.cleanup_sha256 != sha256_document(self._unsigned_json_object()):
            raise ValueError("p8_locked_cleanup_digest_invalid")


@dataclass(frozen=True, slots=True)
class P8LockedSourceFailureCompletenessReceipt:
    """N/0/N aggregate evidence when every admitted item is excluded."""

    access_transaction_sha256: str
    admitted_item_count: int
    paired_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        access_transaction_sha256: str,
        admitted_item_count: int,
    ) -> "P8LockedSourceFailureCompletenessReceipt":
        unsigned = {
            "schema_version": (
                "voice_coach.p8_locked_source_failure_completeness.v1"
            ),
            "status": "all_admitted_items_excluded_failed_closed",
            "access_transaction_sha256": access_transaction_sha256,
            "admitted_item_count": admitted_item_count,
            "paired_item_count": 0,
            "evaluated_item_count": 0,
            "evaluation_exclusion_count": admitted_item_count,
            "per_item_outcomes_serialized": False,
            "restricted_error_detail_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            access_transaction_sha256=access_transaction_sha256,
            admitted_item_count=admitted_item_count,
            paired_item_count=0,
            evaluated_item_count=0,
            evaluation_exclusion_count=admitted_item_count,
            receipt_sha256=sha256_document(unsigned),
        )

    def __post_init__(self) -> None:
        require_sha256(
            self.access_transaction_sha256, "access_transaction_sha256"
        )
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if (
            isinstance(self.admitted_item_count, bool)
            or not isinstance(self.admitted_item_count, int)
            or self.admitted_item_count <= 0
            or self.paired_item_count != 0
            or self.evaluated_item_count != 0
            or self.evaluation_exclusion_count != self.admitted_item_count
        ):
            raise ValueError("p8_locked_failure_completeness_invalid")
        if self.receipt_sha256 != sha256_document(self._unsigned_json_object()):
            raise ValueError("p8_locked_failure_completeness_digest_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            **self._unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    def _unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": (
                "voice_coach.p8_locked_source_failure_completeness.v1"
            ),
            "status": "all_admitted_items_excluded_failed_closed",
            "access_transaction_sha256": self.access_transaction_sha256,
            "admitted_item_count": self.admitted_item_count,
            "paired_item_count": self.paired_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "evaluation_exclusion_count": self.evaluation_exclusion_count,
            "per_item_outcomes_serialized": False,
            "restricted_error_detail_serialized": False,
            "aggregate_only": True,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedSourceFailureCompletenessReceipt":
        document = require_mapping(
            value, "p8_locked_source_failure_completeness"
        )
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "access_transaction_sha256",
                "admitted_item_count",
                "paired_item_count",
                "evaluated_item_count",
                "evaluation_exclusion_count",
                "per_item_outcomes_serialized",
                "restricted_error_detail_serialized",
                "aggregate_only",
                "receipt_sha256",
            },
            "p8_locked_source_failure_completeness",
        )
        if (
            document["schema_version"]
            != "voice_coach.p8_locked_source_failure_completeness.v1"
            or document["status"]
            != "all_admitted_items_excluded_failed_closed"
            or document["per_item_outcomes_serialized"] is not False
            or document["restricted_error_detail_serialized"] is not False
            or document["aggregate_only"] is not True
        ):
            raise ValueError("p8_locked_failure_completeness_contract_invalid")
        return cls(
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "admitted_item_count",
                minimum=1,
            ),
            paired_item_count=require_integer(
                document["paired_item_count"],
                "paired_item_count",
                minimum=0,
            ),
            evaluated_item_count=require_integer(
                document["evaluated_item_count"],
                "evaluated_item_count",
                minimum=0,
            ),
            evaluation_exclusion_count=require_integer(
                document["evaluation_exclusion_count"],
                "evaluation_exclusion_count",
                minimum=1,
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class _LockedCandidate:
    role: Literal["locked_media", "locked_labels"]
    descriptor: int = field(repr=False)
    merge_receipt: SplitZipMergeReceipt | SplitTarMergeReceipt
    repack_receipt: InnerTarZipRepackReceipt | None
    validation_receipt: ZipValidationReceipt


@dataclass(frozen=True, slots=True)
class P8LockedSourceAggregateLineageReceipt:
    """Separate v1 receipt extending, but not changing, P8 v3/v4 evidence."""

    access_transaction_sha256: str
    frozen_pipeline_pins_sha256: str
    source_manifest_sha256: str
    aggregate_metrics_sha256: str
    download_verification: VerifiedAihub538LockedDownload
    tar_extraction: TarExtractionReceipt
    media_merge: SplitZipMergeReceipt | SplitTarMergeReceipt
    label_merge: SplitZipMergeReceipt | SplitTarMergeReceipt
    media_repack: InnerTarZipRepackReceipt | None
    label_repack: InnerTarZipRepackReceipt | None
    media_validation: ZipValidationReceipt
    label_validation: ZipValidationReceipt
    dataset_evaluation: P8LockedDatasetEvaluationAggregate
    cleanup_receipt_sha256: str
    lineage_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        access_transaction_sha256: str,
        frozen_pipeline_pins_sha256: str,
        source_manifest_sha256: str,
        aggregate_metrics_sha256: str,
        download_verification: VerifiedAihub538LockedDownload,
        tar_extraction: TarExtractionReceipt,
        media_merge: SplitZipMergeReceipt | SplitTarMergeReceipt,
        label_merge: SplitZipMergeReceipt | SplitTarMergeReceipt,
        media_repack: InnerTarZipRepackReceipt | None,
        label_repack: InnerTarZipRepackReceipt | None,
        media_validation: ZipValidationReceipt,
        label_validation: ZipValidationReceipt,
        dataset_evaluation: P8LockedDatasetEvaluationAggregate,
        cleanup_receipt_sha256: str,
    ) -> "P8LockedSourceAggregateLineageReceipt":
        # Build the unsigned document without constructing an invalid
        # placeholder instance.  ``__post_init__`` deliberately verifies the
        # digest, so instantiating the dataclass with a zero digest would fail
        # before ``issue`` had a chance to calculate the real value.
        unsigned = {
            "schema_version": (
                "voice_coach.p8_locked_source_aggregate_lineage.v1"
            ),
            "status": "locked_source_evaluated_aggregate_only",
            "access_transaction_sha256": access_transaction_sha256,
            "frozen_pipeline_pins_sha256": frozen_pipeline_pins_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "aggregate_metrics_sha256": aggregate_metrics_sha256,
            "download_verification": download_verification.to_json_object(),
            "outer_tar_extraction": tar_extraction.to_json_object(),
            "merged_archives": [
                {
                    "role": "locked_media",
                    "merge": media_merge.to_json_object(),
                    "repack": (
                        media_repack.to_json_object()
                        if media_repack is not None
                        else None
                    ),
                    "validation": media_validation.to_json_object(),
                },
                {
                    "role": "locked_labels",
                    "merge": label_merge.to_json_object(),
                    "repack": (
                        label_repack.to_json_object()
                        if label_repack is not None
                        else None
                    ),
                    "validation": label_validation.to_json_object(),
                },
            ],
            "dataset_evaluation": (
                dataset_evaluation.to_aggregate_json_object()
            ),
            "cleanup_receipt_sha256": cleanup_receipt_sha256,
            "source_paths_serialized": False,
            "member_names_serialized": False,
            "speaker_identifiers_serialized": False,
            "transcripts_serialized": False,
            "sample_digests_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            access_transaction_sha256=access_transaction_sha256,
            frozen_pipeline_pins_sha256=frozen_pipeline_pins_sha256,
            source_manifest_sha256=source_manifest_sha256,
            aggregate_metrics_sha256=aggregate_metrics_sha256,
            download_verification=download_verification,
            tar_extraction=tar_extraction,
            media_merge=media_merge,
            label_merge=label_merge,
            media_repack=media_repack,
            label_repack=label_repack,
            media_validation=media_validation,
            label_validation=label_validation,
            dataset_evaluation=dataset_evaluation,
            cleanup_receipt_sha256=cleanup_receipt_sha256,
            lineage_sha256=sha256_document(unsigned),
        )

    def __post_init__(self) -> None:
        for name in (
            "access_transaction_sha256",
            "frozen_pipeline_pins_sha256",
            "source_manifest_sha256",
            "aggregate_metrics_sha256",
            "cleanup_receipt_sha256",
            "lineage_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if (
            not _merge_repack_validation_match(
                self.media_merge,
                self.media_repack,
                self.media_validation,
            )
            or not _merge_repack_validation_match(
                self.label_merge,
                self.label_repack,
                self.label_validation,
            )
            or self.dataset_evaluation.source_archive_sha256
            != self.media_validation.archive_sha256
            or self.dataset_evaluation.label_archive_sha256
            != self.label_validation.archive_sha256
            or self.dataset_evaluation.claim_replay.pipeline_pins_sha256
            != self.frozen_pipeline_pins_sha256
        ):
            raise ValueError("p8_locked_archive_lineage_invalid")
        if self.lineage_sha256 != sha256_document(self._unsigned_json_object()):
            raise ValueError("p8_locked_aggregate_lineage_digest_invalid")

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json_object()
        document["lineage_sha256"] = self.lineage_sha256
        return document

    def _unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.p8_locked_source_aggregate_lineage.v1",
            "status": "locked_source_evaluated_aggregate_only",
            "access_transaction_sha256": self.access_transaction_sha256,
            "frozen_pipeline_pins_sha256": self.frozen_pipeline_pins_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "download_verification": self.download_verification.to_json_object(),
            "outer_tar_extraction": self.tar_extraction.to_json_object(),
            "merged_archives": [
                {
                    "role": "locked_media",
                    "merge": self.media_merge.to_json_object(),
                    "repack": (
                        self.media_repack.to_json_object()
                        if self.media_repack is not None
                        else None
                    ),
                    "validation": self.media_validation.to_json_object(),
                },
                {
                    "role": "locked_labels",
                    "merge": self.label_merge.to_json_object(),
                    "repack": (
                        self.label_repack.to_json_object()
                        if self.label_repack is not None
                        else None
                    ),
                    "validation": self.label_validation.to_json_object(),
                },
            ],
            "dataset_evaluation": (
                self.dataset_evaluation.to_aggregate_json_object()
            ),
            "cleanup_receipt_sha256": self.cleanup_receipt_sha256,
            "source_paths_serialized": False,
            "member_names_serialized": False,
            "speaker_identifiers_serialized": False,
            "transcripts_serialized": False,
            "sample_digests_serialized": False,
            "aggregate_only": True,
        }


@dataclass(frozen=True, slots=True)
class P8ReloadedLockedSourceLineage:
    """Strictly reloaded, aggregate-only projection of the source lineage."""

    access_transaction_sha256: str
    frozen_pipeline_pins_sha256: str
    source_manifest_sha256: str
    aggregate_metrics_sha256: str
    cleanup_receipt_sha256: str
    admitted_item_count: int
    evaluated_item_count: int
    evaluation_exclusion_count: int
    claim_replay: P8ClaimPolicyReplayReceipt
    lineage_sha256: str

    @classmethod
    def from_json_object(
        cls,
        value: object,
        *,
        expected_lineage_sha256: str | None = None,
    ) -> "P8ReloadedLockedSourceLineage":
        document = require_mapping(
            value, "p8_locked_source_aggregate_lineage"
        )
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "access_transaction_sha256",
                "frozen_pipeline_pins_sha256",
                "source_manifest_sha256",
                "aggregate_metrics_sha256",
                "download_verification",
                "outer_tar_extraction",
                "merged_archives",
                "dataset_evaluation",
                "cleanup_receipt_sha256",
                "source_paths_serialized",
                "member_names_serialized",
                "speaker_identifiers_serialized",
                "transcripts_serialized",
                "sample_digests_serialized",
                "aggregate_only",
                "lineage_sha256",
            },
            "p8_locked_source_aggregate_lineage",
        )
        if (
            document["schema_version"]
            != "voice_coach.p8_locked_source_aggregate_lineage.v1"
            or document["status"]
            != "locked_source_evaluated_aggregate_only"
            or document["source_paths_serialized"] is not False
            or document["member_names_serialized"] is not False
            or document["speaker_identifiers_serialized"] is not False
            or document["transcripts_serialized"] is not False
            or document["sample_digests_serialized"] is not False
            or document["aggregate_only"] is not True
        ):
            raise ValueError("p8_locked_lineage_contract_invalid")
        lineage_sha256 = require_sha256(
            document["lineage_sha256"], "lineage_sha256"
        )
        unsigned = dict(document)
        del unsigned["lineage_sha256"]
        if lineage_sha256 != sha256_document(unsigned):
            raise ValueError("p8_locked_aggregate_lineage_digest_invalid")
        if (
            expected_lineage_sha256 is not None
            and lineage_sha256
            != require_sha256(
                expected_lineage_sha256, "expected_lineage_sha256"
            )
        ):
            raise ValueError("p8_locked_aggregate_lineage_pin_mismatch")
        _strict_validate_download_receipt(document["download_verification"])
        _strict_validate_tar_extraction(document["outer_tar_extraction"])
        _strict_validate_merged_archives(document["merged_archives"])
        dataset = _strict_dataset_evaluation(document["dataset_evaluation"])
        replay = P8ClaimPolicyReplayReceipt.from_json_object(
            dataset["claim_replay"]
        )
        admitted = require_integer(
            dataset["admitted_item_count"],
            "admitted_item_count",
            minimum=1,
        )
        evaluated = require_integer(
            dataset["evaluated_item_count"],
            "evaluated_item_count",
            minimum=1,
        )
        exclusions = require_integer(
            dataset["evaluation_exclusion_count"],
            "evaluation_exclusion_count",
            minimum=0,
        )
        if admitted != evaluated + exclusions:
            raise ValueError("p8_locked_lineage_completeness_invalid")
        return cls(
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            frozen_pipeline_pins_sha256=require_sha256(
                document["frozen_pipeline_pins_sha256"],
                "frozen_pipeline_pins_sha256",
            ),
            source_manifest_sha256=require_sha256(
                document["source_manifest_sha256"],
                "source_manifest_sha256",
            ),
            aggregate_metrics_sha256=require_sha256(
                document["aggregate_metrics_sha256"],
                "aggregate_metrics_sha256",
            ),
            cleanup_receipt_sha256=require_sha256(
                document["cleanup_receipt_sha256"],
                "cleanup_receipt_sha256",
            ),
            admitted_item_count=admitted,
            evaluated_item_count=evaluated,
            evaluation_exclusion_count=exclusions,
            claim_replay=replay,
            lineage_sha256=lineage_sha256,
        )


def _strict_validate_download_receipt(value: object) -> None:
    document = require_mapping(value, "p8_locked_download_verification")
    require_exact_keys(
        document,
        {
            "schema_version",
            "dataset_id",
            "dataset_partition",
            "file_keys",
            "archive_size_bytes",
            "archive_sha256",
            "secure_download_receipt_sha256",
            "credential_persisted",
            "locked_partition_accessed",
            "aggregate_only",
        },
        "p8_locked_download_verification",
    )
    if (
        document["schema_version"]
        != "voice_coach.aihub538_locked_download_verification.v1"
        or document["dataset_id"] != 538
        or document["dataset_partition"] != "locked_evaluation_vs11_vl11"
        or document["file_keys"] != ["55114", "55082"]
        or document["credential_persisted"] is not False
        or document["locked_partition_accessed"] is not True
        or document["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_download_receipt_invalid")
    require_integer(
        document["archive_size_bytes"], "archive_size_bytes", minimum=1
    )
    require_sha256(document["archive_sha256"], "archive_sha256")
    require_sha256(
        document["secure_download_receipt_sha256"],
        "secure_download_receipt_sha256",
    )


def _strict_validate_tar_extraction(value: object) -> None:
    document = require_mapping(value, "p8_locked_tar_extraction")
    require_exact_keys(
        document,
        {
            "schema_version",
            "archive_sha256",
            "regular_file_count",
            "directory_count",
            "uncompressed_bytes",
            "content_root_sha256",
            "destination_was_new",
            "file_mode",
            "directory_mode",
            "fsync_completed",
            "aggregate_only",
        },
        "p8_locked_tar_extraction",
    )
    if (
        document["schema_version"]
        != "voice_coach.aihub538_tar_extraction_receipt.v2"
        or document["destination_was_new"] is not True
        or document["file_mode"] != "0600"
        or document["directory_mode"] != "0700"
        or document["fsync_completed"] is not True
        or document["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_tar_extraction_receipt_invalid")
    for name, minimum in (
        ("regular_file_count", 1),
        ("directory_count", 0),
        ("uncompressed_bytes", 1),
    ):
        require_integer(document[name], name, minimum=minimum)
    require_sha256(document["archive_sha256"], "archive_sha256")
    require_sha256(document["content_root_sha256"], "content_root_sha256")


def _strict_validate_merged_archives(value: object) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("p8_locked_merged_archive_set_invalid")
    expected_roles = ("locked_media", "locked_labels")
    for entry, expected_role in zip(value, expected_roles, strict=True):
        document = require_mapping(entry, "p8_locked_merged_archive")
        require_exact_keys(
            document,
            {"role", "merge", "repack", "validation"},
            "p8_locked_merged_archive",
        )
        if document["role"] != expected_role:
            raise ValueError("p8_locked_merged_archive_role_invalid")
        merge = require_mapping(document["merge"], "p8_locked_merge")
        schema = merge.get("schema_version")
        if schema == "voice_coach.aihub538_split_zip_merge_receipt.v2":
            _strict_validate_split_zip_merge(merge)
            if document["repack"] is not None:
                raise ValueError("p8_locked_zip_repack_unexpected")
        elif schema == "voice_coach.aihub538_split_tar_merge_receipt.v1":
            _strict_validate_split_tar_merge(merge)
            _strict_validate_tar_repack(document["repack"])
        else:
            raise ValueError("p8_locked_merge_schema_invalid")
        _strict_validate_zip_validation(document["validation"])


def _strict_validate_split_zip_merge(document: object) -> None:
    value = require_mapping(document, "p8_locked_split_zip_merge")
    require_exact_keys(
        value,
        {
            "schema_version",
            "part_count",
            "first_part_offset_bytes",
            "last_part_offset_bytes",
            "same_stem_validated",
            "offset_continuity_validated",
            "merged_bytes",
            "merged_sha256",
            "ordered_part_root_sha256",
            "destination_was_new",
            "file_mode",
            "fsync_completed",
            "aggregate_only",
        },
        "p8_locked_split_zip_merge",
    )
    if (
        value["schema_version"]
        != "voice_coach.aihub538_split_zip_merge_receipt.v2"
        or value["first_part_offset_bytes"] != 0
        or value["same_stem_validated"] is not True
        or value["offset_continuity_validated"] is not True
        or value["destination_was_new"] is not True
        or value["file_mode"] != "0600"
        or value["fsync_completed"] is not True
        or value["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_split_zip_merge_invalid")
    _strict_validate_merge_counts_and_digests(value)


def _strict_validate_split_tar_merge(document: object) -> None:
    value = require_mapping(document, "p8_locked_split_tar_merge")
    require_exact_keys(
        value,
        {
            "schema_version",
            "archive_kind",
            "part_count",
            "first_part_offset_bytes",
            "last_part_offset_bytes",
            "offset_continuity_validated",
            "same_stem_validated",
            "merged_bytes",
            "merged_sha256",
            "ordered_part_root_sha256",
            "destination_was_new",
            "file_mode",
            "fsync_completed",
            "raw_part_names_serialized",
            "aggregate_only",
        },
        "p8_locked_split_tar_merge",
    )
    if (
        value["schema_version"]
        != "voice_coach.aihub538_split_tar_merge_receipt.v1"
        or value["archive_kind"] != "inner_posix_tar"
        or value["first_part_offset_bytes"] != 0
        or value["offset_continuity_validated"] is not True
        or value["same_stem_validated"] is not True
        or value["destination_was_new"] is not True
        or value["file_mode"] != "0600"
        or value["fsync_completed"] is not True
        or value["raw_part_names_serialized"] is not False
        or value["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_split_tar_merge_invalid")
    _strict_validate_merge_counts_and_digests(value)


def _strict_validate_merge_counts_and_digests(
    document: dict[str, object],
) -> None:
    for name, minimum in (
        ("part_count", 1),
        ("last_part_offset_bytes", 0),
        ("merged_bytes", 1),
    ):
        require_integer(document[name], name, minimum=minimum)
    require_sha256(document["merged_sha256"], "merged_sha256")
    require_sha256(
        document["ordered_part_root_sha256"], "ordered_part_root_sha256"
    )


def _strict_validate_tar_repack(value: object) -> None:
    document = require_mapping(value, "p8_locked_tar_repack")
    require_exact_keys(
        document,
        {
            "schema_version",
            "status",
            "source_archive_kind",
            "destination_archive_kind",
            "source_tar_sha256",
            "source_tar_regular_file_count",
            "source_tar_directory_count",
            "source_tar_uncompressed_bytes",
            "source_tar_content_root_sha256",
            "repacked_zip_sha256",
            "repacked_zip_bytes",
            "repacked_zip_content_root_sha256",
            "file_parity_root_sha256",
            "regular_file_count_parity",
            "normalized_path_hash_size_sha256_parity",
            "zip_compression",
            "zip64_forced_for_streamed_members",
            "fixed_zip_timestamp",
            "directory_entries_omitted",
            "destination_was_new",
            "file_mode",
            "fsync_completed",
            "raw_member_names_serialized",
            "source_paths_serialized",
            "transcripts_serialized",
            "participant_identifiers_serialized",
            "aggregate_only",
        },
        "p8_locked_tar_repack",
    )
    expected = {
        "schema_version": "voice_coach.aihub538_inner_tar_zip_repack.v1",
        "status": "verified_inner_tar_repacked_deterministically",
        "source_archive_kind": "inner_posix_tar",
        "destination_archive_kind": "zip_stored_zip64",
        "regular_file_count_parity": True,
        "normalized_path_hash_size_sha256_parity": True,
        "zip_compression": "stored",
        "zip64_forced_for_streamed_members": True,
        "fixed_zip_timestamp": "1980-01-01T00:00:00",
        "directory_entries_omitted": True,
        "destination_was_new": True,
        "file_mode": "0600",
        "fsync_completed": True,
        "raw_member_names_serialized": False,
        "source_paths_serialized": False,
        "transcripts_serialized": False,
        "participant_identifiers_serialized": False,
        "aggregate_only": True,
    }
    if any(document[name] != expected_value for name, expected_value in expected.items()):
        raise ValueError("p8_locked_tar_repack_invalid")
    for name, minimum in (
        ("source_tar_regular_file_count", 1),
        ("source_tar_directory_count", 0),
        ("source_tar_uncompressed_bytes", 1),
        ("repacked_zip_bytes", 1),
    ):
        require_integer(document[name], name, minimum=minimum)
    for name in (
        "source_tar_sha256",
        "source_tar_content_root_sha256",
        "repacked_zip_sha256",
        "repacked_zip_content_root_sha256",
        "file_parity_root_sha256",
    ):
        require_sha256(document[name], name)


def _strict_validate_zip_validation(value: object) -> None:
    document = require_mapping(value, "p8_locked_zip_validation")
    require_exact_keys(
        document,
        {
            "schema_version",
            "archive_sha256",
            "member_count",
            "regular_file_count",
            "directory_count",
            "compressed_bytes",
            "uncompressed_bytes",
            "content_root_sha256",
            "encrypted_members",
            "links_or_special_members",
            "duplicate_or_case_colliding_members",
            "crc_validated",
            "aggregate_only",
        },
        "p8_locked_zip_validation",
    )
    if (
        document["schema_version"]
        != "voice_coach.aihub538_zip_validation_receipt.v2"
        or document["encrypted_members"] != 0
        or document["links_or_special_members"] != 0
        or document["duplicate_or_case_colliding_members"] != 0
        or document["crc_validated"] is not True
        or document["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_zip_validation_invalid")
    for name, minimum in (
        ("member_count", 1),
        ("regular_file_count", 1),
        ("directory_count", 0),
        ("compressed_bytes", 1),
        ("uncompressed_bytes", 1),
    ):
        require_integer(document[name], name, minimum=minimum)
    require_sha256(document["archive_sha256"], "archive_sha256")
    require_sha256(document["content_root_sha256"], "content_root_sha256")


def _strict_dataset_evaluation(value: object) -> dict[str, object]:
    document = require_mapping(value, "p8_locked_dataset_evaluation")
    require_exact_keys(
        document,
        {
            "schema_version",
            "status",
            "label_archive_sha256",
            "source_archive_sha256",
            "label_content_root_sha256",
            "source_content_root_sha256",
            "locked_identity_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "identity_intersections",
            "admitted_item_count",
            "paired_item_count",
            "evaluated_item_count",
            "evaluation_exclusion_count",
            "successful_item_count",
            "failed_item_count",
            "qc_decision_lineage_valid_item_count",
            "decode_success_rate",
            "supported_clip_count",
            "supported_clip_below_single_face_threshold_count",
            "aggregate_supported_slice_coverage",
            "av_anchor_availability_rate",
            "ready_case_count",
            "ready_sync_decision_lineage_valid_case_count",
            "maximum_ready_absolute_sync_offset_ms",
            "slice_aggregates",
            "claim_replay",
            "source_paths_serialized",
            "member_names_serialized",
            "speaker_identifiers_serialized",
            "transcripts_serialized",
            "sample_digests_serialized",
            "individual_identity_digests_serialized",
            "per_item_outcomes_serialized",
            "aggregate_only",
        },
        "p8_locked_dataset_evaluation",
    )
    if (
        document["schema_version"]
        != "voice_coach.aihub538_p8_locked_dataset_evaluation.v1"
        or document["status"] != "all_admitted_items_accounted_for"
        or any(
            document[name] is not False
            for name in (
                "source_paths_serialized",
                "member_names_serialized",
                "speaker_identifiers_serialized",
                "transcripts_serialized",
                "sample_digests_serialized",
                "individual_identity_digests_serialized",
                "per_item_outcomes_serialized",
            )
        )
        or document["aggregate_only"] is not True
    ):
        raise ValueError("p8_locked_dataset_evaluation_invalid")
    intersections = document["identity_intersections"]
    if not isinstance(intersections, list) or len(intersections) != 2:
        raise ValueError("p8_locked_identity_intersection_set_invalid")
    slices = document["slice_aggregates"]
    if not isinstance(slices, list) or len(slices) != 17:
        raise ValueError("p8_locked_slice_aggregate_set_invalid")
    return dict(document)


@dataclass(frozen=True, slots=True)
class P8LockedSourceTerminalReceipt:
    """Final sidecar pinned to report, cleanup, and frozen pipeline code."""

    access_transaction_sha256: str
    frozen_pipeline_pins_sha256: str
    canonical_report_sha256: str
    report_status: Literal["completed_aggregate_only", "failed_closed"]
    cleanup_state_sha256: str
    cleanup_status: Literal["cleanup_completed", "cleanup_incomplete"]
    aggregate_lineage_sha256: str | None
    failure_completeness_sha256: str | None
    reason_codes: tuple[str, ...]
    terminal_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        report: P8LockedEvaluationReport,
        frozen_pipeline_pins_sha256: str,
        cleanup_state: P8ActualSourceCleanupState,
        aggregate_lineage_sha256: str | None,
        failure_completeness_sha256: str | None,
    ) -> "P8LockedSourceTerminalReceipt":
        if report.status is P8LockedEvaluationStatus.NOT_EXECUTED:
            raise ValueError("p8_locked_terminal_report_status_invalid")
        report_status = report.status.value
        unsigned = {
            "schema_version": "voice_coach.p8_locked_source_terminal.v3",
            "status": "canonical_report_staged_for_fresh_reload",
            "access_transaction_sha256": report.access_transaction_sha256,
            "frozen_pipeline_pins_sha256": frozen_pipeline_pins_sha256,
            "canonical_report_sha256": report.report_sha256,
            "report_status": report_status,
            "source_open_attempted": True,
            "cleanup_state_sha256": cleanup_state.cleanup_state_sha256,
            "cleanup_status": cleanup_state.status.value,
            "aggregate_lineage_sha256": aggregate_lineage_sha256,
            "failure_completeness_sha256": failure_completeness_sha256,
            "reason_codes": list(report.reason_codes),
            "restricted_error_detail_serialized": False,
            "source_paths_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            access_transaction_sha256=report.access_transaction_sha256,
            frozen_pipeline_pins_sha256=frozen_pipeline_pins_sha256,
            canonical_report_sha256=report.report_sha256,
            report_status=report_status,
            cleanup_state_sha256=cleanup_state.cleanup_state_sha256,
            cleanup_status=cleanup_state.status.value,
            aggregate_lineage_sha256=aggregate_lineage_sha256,
            failure_completeness_sha256=failure_completeness_sha256,
            reason_codes=report.reason_codes,
            terminal_sha256=sha256_document(unsigned),
        )

    def __post_init__(self) -> None:
        for name in (
            "access_transaction_sha256",
            "frozen_pipeline_pins_sha256",
            "canonical_report_sha256",
            "cleanup_state_sha256",
            "terminal_sha256",
        ):
            require_sha256(getattr(self, name), name)
        for name in (
            "aggregate_lineage_sha256",
            "failure_completeness_sha256",
        ):
            value = getattr(self, name)
            if value is not None:
                require_sha256(value, name)
        if self.report_status not in {
            "completed_aggregate_only",
            "failed_closed",
        } or self.cleanup_status not in {
            "cleanup_completed",
            "cleanup_incomplete",
        }:
            raise ValueError("p8_locked_terminal_status_invalid")
        if self.report_status == "completed_aggregate_only" and (
            self.cleanup_status != "cleanup_completed"
            or self.aggregate_lineage_sha256 is None
            or self.failure_completeness_sha256 is not None
            or self.reason_codes
        ):
            raise ValueError("p8_locked_terminal_completion_invalid")
        if self.aggregate_lineage_sha256 is not None and (
            self.failure_completeness_sha256 is not None
        ):
            raise ValueError("p8_locked_terminal_evidence_ambiguous")
        if self.cleanup_status == "cleanup_incomplete" and (
            self.report_status != "failed_closed"
        ):
            raise ValueError("p8_locked_terminal_cleanup_invalid")
        if self.report_status == "failed_closed" and not self.reason_codes:
            raise ValueError("p8_locked_terminal_failure_reason_missing")
        if self.terminal_sha256 != sha256_document(self._unsigned_json_object()):
            raise ValueError("p8_locked_terminal_digest_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            **self._unsigned_json_object(),
            "terminal_sha256": self.terminal_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedSourceTerminalReceipt":
        document = require_mapping(value, "p8_locked_source_terminal")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "access_transaction_sha256",
                "frozen_pipeline_pins_sha256",
                "canonical_report_sha256",
                "report_status",
                "source_open_attempted",
                "cleanup_state_sha256",
                "cleanup_status",
                "aggregate_lineage_sha256",
                "failure_completeness_sha256",
                "reason_codes",
                "restricted_error_detail_serialized",
                "source_paths_serialized",
                "aggregate_only",
                "terminal_sha256",
            },
            "p8_locked_source_terminal",
        )
        if (
            document["schema_version"]
            != "voice_coach.p8_locked_source_terminal.v3"
            or document["status"]
            != "canonical_report_staged_for_fresh_reload"
            or document["source_open_attempted"] is not True
            or document["restricted_error_detail_serialized"] is not False
            or document["source_paths_serialized"] is not False
            or document["aggregate_only"] is not True
        ):
            raise ValueError("p8_locked_terminal_contract_invalid")
        reasons = document["reason_codes"]
        if not isinstance(reasons, list) or not all(
            isinstance(item, str) for item in reasons
        ):
            raise ValueError("p8_locked_terminal_reason_codes_invalid")

        def optional_sha(name: str) -> str | None:
            return (
                None
                if document[name] is None
                else require_sha256(document[name], name)
            )

        return cls(
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            frozen_pipeline_pins_sha256=require_sha256(
                document["frozen_pipeline_pins_sha256"],
                "frozen_pipeline_pins_sha256",
            ),
            canonical_report_sha256=require_sha256(
                document["canonical_report_sha256"],
                "canonical_report_sha256",
            ),
            report_status=document["report_status"],
            cleanup_state_sha256=require_sha256(
                document["cleanup_state_sha256"], "cleanup_state_sha256"
            ),
            cleanup_status=document["cleanup_status"],
            aggregate_lineage_sha256=optional_sha(
                "aggregate_lineage_sha256"
            ),
            failure_completeness_sha256=optional_sha(
                "failure_completeness_sha256"
            ),
            reason_codes=tuple(reasons),
            terminal_sha256=require_sha256(
                document["terminal_sha256"], "terminal_sha256"
            ),
        )

    def _unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.p8_locked_source_terminal.v3",
            "status": "canonical_report_staged_for_fresh_reload",
            "access_transaction_sha256": self.access_transaction_sha256,
            "frozen_pipeline_pins_sha256": (
                self.frozen_pipeline_pins_sha256
            ),
            "canonical_report_sha256": self.canonical_report_sha256,
            "report_status": self.report_status,
            "source_open_attempted": True,
            "cleanup_state_sha256": self.cleanup_state_sha256,
            "cleanup_status": self.cleanup_status,
            "aggregate_lineage_sha256": self.aggregate_lineage_sha256,
            "failure_completeness_sha256": (
                self.failure_completeness_sha256
            ),
            "reason_codes": list(self.reason_codes),
            "restricted_error_detail_serialized": False,
            "source_paths_serialized": False,
            "aggregate_only": True,
        }


class P8LockedSourceReceiptStore:
    """Descriptor-safe strict reload boundary for aggregate output receipts."""

    def __init__(self, output_directory: Path) -> None:
        self._output_directory = output_directory

    def load_cleanup(self) -> P8LockedWorkspaceCleanupReceipt:
        descriptor = self._open_directory()
        try:
            return P8LockedWorkspaceCleanupReceipt.from_json_object(
                _read_strict_json_at(descriptor, _CLEANUP_RECEIPT_NAME)
            )
        finally:
            os.close(descriptor)

    def persist_terminal(
        self, terminal: P8LockedSourceTerminalReceipt
    ) -> None:
        if not isinstance(terminal, P8LockedSourceTerminalReceipt):
            raise P8Aihub538SourceError(
                "p8_locked_source_terminal_invalid"
            )
        descriptor = self._open_directory()
        try:
            _persist_json_receipt(
                descriptor,
                _TERMINAL_RECEIPT_NAME,
                terminal.to_json_object(),
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def load_lineage(
        self, *, expected_lineage_sha256: str | None = None
    ) -> P8ReloadedLockedSourceLineage:
        descriptor = self._open_directory()
        try:
            return P8ReloadedLockedSourceLineage.from_json_object(
                _read_strict_json_at(descriptor, _LINEAGE_RECEIPT_NAME),
                expected_lineage_sha256=expected_lineage_sha256,
            )
        finally:
            os.close(descriptor)

    def load_failure_completeness(
        self,
    ) -> P8LockedSourceFailureCompletenessReceipt:
        descriptor = self._open_directory()
        try:
            return P8LockedSourceFailureCompletenessReceipt.from_json_object(
                _read_strict_json_at(
                    descriptor, _FAILURE_COMPLETENESS_RECEIPT_NAME
                )
            )
        finally:
            os.close(descriptor)

    def load_failure_completeness_if_present(
        self,
    ) -> P8LockedSourceFailureCompletenessReceipt | None:
        descriptor = self._open_directory()
        try:
            try:
                os.stat(
                    _FAILURE_COMPLETENESS_RECEIPT_NAME,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            except OSError:
                raise P8Aihub538SourceError(
                    "p8_locked_source_receipt_inspection_failed"
                ) from None
            return P8LockedSourceFailureCompletenessReceipt.from_json_object(
                _read_strict_json_at(
                    descriptor, _FAILURE_COMPLETENESS_RECEIPT_NAME
                )
            )
        finally:
            os.close(descriptor)

    def load_terminal(self) -> P8LockedSourceTerminalReceipt:
        descriptor = self._open_directory()
        try:
            return P8LockedSourceTerminalReceipt.from_json_object(
                _read_strict_json_at(descriptor, _TERMINAL_RECEIPT_NAME)
            )
        finally:
            os.close(descriptor)

    def restricted_work_directory_absent(self) -> bool:
        descriptor = self._open_directory()
        try:
            try:
                os.stat(
                    _WORK_DIRECTORY_NAME,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return True
            except OSError:
                raise P8Aihub538SourceError(
                    "p8_locked_work_absence_check_failed"
                ) from None
            return False
        finally:
            os.close(descriptor)

    def _open_directory(self) -> int:
        try:
            descriptor = os.open(
                self._output_directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            metadata = os.fstat(descriptor)
        except OSError:
            raise P8Aihub538SourceError(
                "p8_locked_output_receipt_directory_unavailable"
            ) from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            os.close(descriptor)
            raise P8Aihub538SourceError(
                "p8_locked_output_receipt_directory_unsafe"
            )
        return descriptor


class P8Aihub538LockedSourceEvaluator:
    """Callable injected into :class:`P8LockedEvaluationService`."""

    def __init__(
        self,
        *,
        state_store: P8SealStateStore,
        source: DeferredP8LockedSource,
        output_directory: Path,
        sealed_references: P8SealedReferenceBundle,
        actual_source_seal: P8ActualSourceReadZeroSeal,
        frozen_pipeline: P8FrozenLockedPipeline,
        report_id: str,
        pending_at_utc: str,
        cleanup_recorded_at_utc: str,
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        self._state_store = state_store
        self._source = source
        self._output_directory = output_directory
        self._sealed_references = sealed_references
        self._actual_source_seal = actual_source_seal
        self._frozen_pipeline = frozen_pipeline
        self._report_id = report_id
        self._pending_at_utc = pending_at_utc
        self._cleanup_recorded_at_utc = cleanup_recorded_at_utc
        self._archive_policy = archive_policy
        self._dataset_evaluator = Aihub538LockedDatasetEvaluator(
            archive_policy=archive_policy
        )

    def __call__(
        self, transaction: P8AccessTransaction
    ) -> P8LockedSourceEvaluationResult:
        persisted = self._state_store.load_access_transaction()
        seal = self._state_store.load_read_zero_seal()
        if persisted != transaction:
            raise P8Aihub538SourceError(
                "p8_locked_access_transaction_not_durable"
            )
        self._validate_preaccess_inputs(seal)
        gate_pending = P8ActualSourceGatePending.issue(
            actual_source_seal=self._actual_source_seal,
            access_transaction_sha256=transaction.transaction_sha256,
            report_id=self._report_id,
            frozen_pipeline_pins_sha256=(
                p8_frozen_pipeline_pins_sha256(seal)
            ),
            pending_at_utc=self._pending_at_utc,
        )
        # This O_EXCL+fsync state write is the only transition that permits
        # the deferred completed directory to be opened.
        self._state_store.create_actual_source_gate_pending(gate_pending)

        root_descriptor: int | None = None
        parent_descriptor: int | None = None
        work_descriptor: int | None = None
        candidates: tuple[_LockedCandidate, _LockedCandidate] = ()
        cleanup: P8LockedWorkspaceCleanupReceipt | None = None
        cleanup_state_written = False
        try:
            root_descriptor, parent_descriptor, _ = _create_run_root(
                self._output_directory
            )
            _persist_json_receipt(
                root_descriptor,
                _GATE_PENDING_RECEIPT_NAME,
                gate_pending.to_json_object(),
            )
            work_descriptor = _create_private_child(
                root_descriptor, _WORK_DIRECTORY_NAME
            )
            with self._source.open_after_consumption() as (
                archive_descriptor,
                receipt_descriptor,
            ):
                verified = verify_aihub538_locked_download(
                    archive_descriptor=archive_descriptor,
                    receipt_descriptor=receipt_descriptor,
                    policy=self._archive_policy,
                )
                tar_receipt = extract_verified_tar_descriptor(
                    archive_descriptor=archive_descriptor,
                    destination=(
                        self._output_directory
                        / _WORK_DIRECTORY_NAME
                        / _OUTER_DIRECTORY_NAME
                    ),
                    policy=self._archive_policy,
                )
                if tar_receipt.archive_sha256 != verified.archive_sha256:
                    raise P8Aihub538SourceError(
                        "p8_locked_verified_tar_lineage_mismatch"
                    )
                candidates = self._merge_and_classify(
                    work_descriptor=work_descriptor
                )
                media = _one_role(candidates, "locked_media")
                labels = _one_role(candidates, "locked_labels")
                from ..adapters.p8_production_frozen_pipeline import (
                    bind_production_p8_media_workspace,
                )

                with bind_production_p8_media_workspace(
                    self._frozen_pipeline,
                    workspace_descriptor=work_descriptor,
                    access_transaction_sha256=transaction.transaction_sha256,
                    issued_at_utc=transaction.consumed_at_utc,
                ):
                    dataset_evaluation = self._dataset_evaluator.evaluate(
                        label_zip_descriptor=labels.descriptor,
                        source_zip_descriptor=media.descriptor,
                        sealed_references=self._sealed_references,
                        frozen_pipeline=self._frozen_pipeline,
                        seal=seal,
                    )
                source_manifest = P8LockedSourceManifest.issue(
                    source_revision=seal.source_revision,
                    transaction=transaction,
                    admitted_item_count=dataset_evaluation.admitted_item_count,
                    archives=(
                        P8LockedSourceArchiveDigest(
                            file_key="55114",
                            partition_id="VS11",
                            role="locked_media",
                            byte_count=_normalized_candidate_bytes(media),
                            archive_sha256=(
                                media.validation_receipt.archive_sha256
                            ),
                        ),
                        P8LockedSourceArchiveDigest(
                            file_key="55082",
                            partition_id="VL11",
                            role="locked_labels",
                            byte_count=_normalized_candidate_bytes(labels),
                            archive_sha256=(
                                labels.validation_receipt.archive_sha256
                            ),
                        ),
                    ),
                )
                metrics = _issue_metrics(
                    dataset_evaluation=dataset_evaluation,
                    source_manifest=source_manifest,
                )
                result = P8LockedSourceEvaluationResult.issue(
                    source_manifest=source_manifest,
                    metrics=metrics,
                )

            for candidate in candidates:
                os.close(candidate.descriptor)
            candidates = ()
            assert work_descriptor is not None
            assert root_descriptor is not None
            removed_files, removed_directories = _remove_private_child(
                parent_descriptor=root_descriptor,
                child_descriptor=work_descriptor,
                child_name=_WORK_DIRECTORY_NAME,
            )
            os.close(work_descriptor)
            work_descriptor = None
            cleanup = P8LockedWorkspaceCleanupReceipt.issue(
                access_transaction_sha256=transaction.transaction_sha256,
                removed_regular_file_count=removed_files,
                removed_directory_count=removed_directories,
            )
            _persist_json_receipt(
                root_descriptor,
                _CLEANUP_RECEIPT_NAME,
                cleanup.to_json_object(),
            )
            cleanup_state = P8ActualSourceCleanupState.issue_completed(
                gate_pending=gate_pending,
                removed_regular_file_count=removed_files,
                removed_directory_count=removed_directories,
                workspace_cleanup_receipt_sha256=cleanup.cleanup_sha256,
                recorded_at_utc=self._cleanup_recorded_at_utc,
            )
            self._state_store.create_actual_source_cleanup_state(cleanup_state)
            cleanup_state_written = True
            lineage = P8LockedSourceAggregateLineageReceipt.issue(
                access_transaction_sha256=transaction.transaction_sha256,
                frozen_pipeline_pins_sha256=(
                    p8_frozen_pipeline_pins_sha256(seal)
                ),
                source_manifest_sha256=source_manifest.manifest_sha256,
                aggregate_metrics_sha256=metrics.metrics_sha256,
                download_verification=verified,
                tar_extraction=tar_receipt,
                media_merge=media.merge_receipt,
                label_merge=labels.merge_receipt,
                media_repack=media.repack_receipt,
                label_repack=labels.repack_receipt,
                media_validation=media.validation_receipt,
                label_validation=labels.validation_receipt,
                dataset_evaluation=dataset_evaluation,
                cleanup_receipt_sha256=cleanup.cleanup_sha256,
            )
            _persist_json_receipt(
                root_descriptor,
                _LINEAGE_RECEIPT_NAME,
                lineage.to_json_object(),
            )
            os.fsync(root_descriptor)
            return result
        except BaseException as error:
            for candidate in candidates:
                try:
                    os.close(candidate.descriptor)
                except OSError:
                    pass
            if (
                not cleanup_state_written
                and work_descriptor is not None
                and root_descriptor is not None
            ):
                try:
                    removed_files, removed_directories = _remove_private_child(
                        parent_descriptor=root_descriptor,
                        child_descriptor=work_descriptor,
                        child_name=_WORK_DIRECTORY_NAME,
                    )
                    cleanup = P8LockedWorkspaceCleanupReceipt.issue(
                        access_transaction_sha256=transaction.transaction_sha256,
                        removed_regular_file_count=removed_files,
                        removed_directory_count=removed_directories,
                    )
                    _persist_json_receipt(
                        root_descriptor,
                        _CLEANUP_RECEIPT_NAME,
                        cleanup.to_json_object(),
                    )
                    cleanup_state = (
                        P8ActualSourceCleanupState.issue_completed(
                            gate_pending=gate_pending,
                            removed_regular_file_count=removed_files,
                            removed_directory_count=removed_directories,
                            workspace_cleanup_receipt_sha256=(
                                cleanup.cleanup_sha256
                            ),
                            recorded_at_utc=self._cleanup_recorded_at_utc,
                        )
                    )
                    self._state_store.create_actual_source_cleanup_state(
                        cleanup_state
                    )
                    cleanup_state_written = True
                except Exception:
                    cleanup = None
                finally:
                    try:
                        os.close(work_descriptor)
                    except OSError:
                        pass
                    work_descriptor = None
            if not cleanup_state_written:
                try:
                    work_absent = (
                        root_descriptor is None
                        or _entry_absent_at(
                            root_descriptor, _WORK_DIRECTORY_NAME
                        )
                    )
                    incomplete = P8ActualSourceCleanupState.issue_incomplete(
                        gate_pending=gate_pending,
                        restricted_work_directory_absent=work_absent,
                        recorded_at_utc=self._cleanup_recorded_at_utc,
                    )
                    self._state_store.create_actual_source_cleanup_state(
                        incomplete
                    )
                    cleanup_state_written = True
                except Exception:
                    pass
            if (
                isinstance(error, P8LockedEvaluationCompletenessError)
                and root_descriptor is not None
            ):
                try:
                    completeness = P8LockedSourceFailureCompletenessReceipt.issue(
                        access_transaction_sha256=(
                            transaction.transaction_sha256
                        ),
                        admitted_item_count=error.admitted_item_count,
                    )
                    _persist_json_receipt(
                        root_descriptor,
                        _FAILURE_COMPLETENESS_RECEIPT_NAME,
                        completeness.to_json_object(),
                    )
                except Exception:
                    pass
            raise P8Aihub538SourceError(
                "p8_locked_source_ingestion_or_evaluation_failed"
            ) from None
        finally:
            if work_descriptor is not None:
                os.close(work_descriptor)
            if root_descriptor is not None:
                os.close(root_descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)

    def _validate_preaccess_inputs(self, seal: P8ReadZeroSeal) -> None:
        expected_pins = p8_frozen_pipeline_pins_sha256(seal)
        if (
            getattr(self._frozen_pipeline, "pipeline_pins_sha256", None)
            != expected_pins
        ):
            raise P8Aihub538SourceError(
                "p8_locked_frozen_pipeline_pin_mismatch"
            )
        persisted_actual = self._state_store.load_actual_source_read_zero_seal()
        if (
            type(self._sealed_references) is not P8SealedReferenceBundle
            or persisted_actual != self._actual_source_seal
            or self._actual_source_seal.base_read_zero_seal_sha256
            != seal.seal_sha256
            or self._sealed_references.actual_source_seal_sha256
            != self._actual_source_seal.actual_source_seal_sha256
        ):
            raise P8Aihub538SourceError(
                "p8_locked_preaccess_identity_evidence_invalid"
            )

    def _merge_and_classify(
        self, *, work_descriptor: int
    ) -> tuple[_LockedCandidate, _LockedCandidate]:
        outer_descriptor = _open_private_directory_at(
            work_descriptor, _OUTER_DIRECTORY_NAME
        )
        groups = ()
        opened_candidates: list[int] = []
        try:
            groups = _discover_exact_archive_groups(
                outer_descriptor,
                policy=self._archive_policy,
            )
        finally:
            os.close(outer_descriptor)
        candidates: list[_LockedCandidate] = []
        try:
            for index, group in enumerate(groups):
                zip_name = f"candidate-{index}.zip"
                repack: InnerTarZipRepackReceipt | None = None
                if group.kind == "zip":
                    merge = merge_split_zip_parts(
                        parts=group.zip_parts,
                        destination=(
                            self._output_directory
                            / _WORK_DIRECTORY_NAME
                            / zip_name
                        ),
                        policy=self._archive_policy,
                    )
                elif group.kind == "tar":
                    tar_name = f"candidate-{index}.tar"
                    merge = merge_split_tar_parts(
                        parts=group.tar_parts,
                        destination=(
                            self._output_directory
                            / _WORK_DIRECTORY_NAME
                            / tar_name
                        ),
                        policy=self._archive_policy,
                    )
                    tar_descriptor = os.open(
                        tar_name,
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=work_descriptor,
                    )
                    try:
                        repack, validation = repack_verified_inner_tar_to_zip(
                            archive_descriptor=tar_descriptor,
                            destination=(
                                self._output_directory
                                / _WORK_DIRECTORY_NAME
                                / zip_name
                            ),
                            policy=self._archive_policy,
                        )
                    finally:
                        os.close(tar_descriptor)
                    if repack.source_tar_sha256 != merge.merged_sha256:
                        raise P8Aihub538SourceError(
                            "p8_locked_merged_tar_lineage_mismatch"
                        )
                else:
                    raise P8Aihub538SourceError(
                        "p8_locked_split_archive_kind_invalid"
                    )
                descriptor = os.open(
                    zip_name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=work_descriptor,
                )
                opened_candidates.append(descriptor)
                observed_validation = validate_zip_descriptor(
                    descriptor,
                    policy=self._archive_policy,
                )
                if group.kind == "zip":
                    validation = observed_validation
                    if validation.archive_sha256 != merge.merged_sha256:
                        raise P8Aihub538SourceError(
                            "p8_locked_merged_zip_lineage_mismatch"
                        )
                elif (
                    repack is None
                    or validation != observed_validation
                    or validation.archive_sha256 != repack.repacked_zip_sha256
                ):
                    raise P8Aihub538SourceError(
                        "p8_locked_repacked_zip_lineage_mismatch"
                    )
                role = _probe_locked_role(
                    descriptor, policy=self._archive_policy
                )
                candidates.append(
                    _LockedCandidate(
                        role=role,
                        descriptor=descriptor,
                        merge_receipt=merge,
                        repack_receipt=repack,
                        validation_receipt=validation,
                    )
                )
            if sorted(item.role for item in candidates) != [
                "locked_labels",
                "locked_media",
            ]:
                raise P8Aihub538SourceError(
                    "p8_locked_semantic_role_pair_invalid"
                )
            result = (candidates[0], candidates[1])
            opened_candidates.clear()
            return result
        finally:
            for group in groups:
                for part in group.zip_parts + group.tar_parts:
                    os.close(part.descriptor)
            for descriptor in opened_candidates:
                os.close(descriptor)


class P8Aihub538OneShotRunner:
    """Internal entrypoint that opens only a deferred completed directory."""

    def __init__(
        self,
        *,
        state_store: P8SealStateStore,
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        self._state_store = state_store
        self._archive_policy = archive_policy

    def execute_from_completed_directory(
        self,
        *,
        confirm_irreversible_consumption: bool,
        input_directory: Path,
        identity_key_descriptor: int,
        restricted_reference_directory_descriptor: int,
        frozen_pipeline: P8FrozenLockedPipeline,
        output_directory: Path,
        expected_seal_sha256: str,
        expected_actual_source_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
        report_id: str,
        pending_at_utc: str,
        cleanup_recorded_at_utc: str,
        reload_verified_at_utc: str,
        committed_at_utc: str,
    ) -> P8LockedEvaluationReport:
        from ..adapters.p8_production_frozen_pipeline import (
            require_exact_production_p8_pipeline,
        )

        concrete_pipeline = require_exact_production_p8_pipeline(
            frozen_pipeline
        )
        # Preserve the stronger existing prerequisite ordering: a missing
        # companion seal is reported before the cohort-stage blocker.  Loading
        # canonical state does not inspect the deferred locked input.
        self._state_store.load_actual_source_read_zero_seal()
        # This check intentionally precedes deferred-directory construction and
        # irreversible authorization consumption.  The VS11/VL11 executor below
        # is retained for fixture security tests, not as a production cohort.
        require_selected_cohort_source_seal_for_production()
        return self._execute_from_completed_directory(
            confirm_irreversible_consumption=(
                confirm_irreversible_consumption
            ),
            input_directory=input_directory,
            identity_key_descriptor=identity_key_descriptor,
            restricted_reference_directory_descriptor=(
                restricted_reference_directory_descriptor
            ),
            frozen_pipeline=concrete_pipeline,
            output_directory=output_directory,
            expected_seal_sha256=expected_seal_sha256,
            expected_actual_source_seal_sha256=(
                expected_actual_source_seal_sha256
            ),
            transaction_id=transaction_id,
            consumed_at_utc=consumed_at_utc,
            report_id=report_id,
            pending_at_utc=pending_at_utc,
            cleanup_recorded_at_utc=cleanup_recorded_at_utc,
            reload_verified_at_utc=reload_verified_at_utc,
            committed_at_utc=committed_at_utc,
        )

    def _execute_fixture_from_completed_directory(
        self,
        *,
        confirm_irreversible_consumption: bool,
        input_directory: Path,
        identity_key_descriptor: int,
        restricted_reference_directory_descriptor: int,
        frozen_pipeline: P8FrozenLockedPipeline,
        output_directory: Path,
        expected_seal_sha256: str,
        expected_actual_source_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
        report_id: str,
        pending_at_utc: str,
        cleanup_recorded_at_utc: str,
        reload_verified_at_utc: str,
        committed_at_utc: str,
    ) -> P8LockedEvaluationReport:
        """Fixture-only hook; production callers must use the concrete method."""

        return self._execute_from_completed_directory(
            confirm_irreversible_consumption=(
                confirm_irreversible_consumption
            ),
            input_directory=input_directory,
            identity_key_descriptor=identity_key_descriptor,
            restricted_reference_directory_descriptor=(
                restricted_reference_directory_descriptor
            ),
            frozen_pipeline=frozen_pipeline,
            output_directory=output_directory,
            expected_seal_sha256=expected_seal_sha256,
            expected_actual_source_seal_sha256=(
                expected_actual_source_seal_sha256
            ),
            transaction_id=transaction_id,
            consumed_at_utc=consumed_at_utc,
            report_id=report_id,
            pending_at_utc=pending_at_utc,
            cleanup_recorded_at_utc=cleanup_recorded_at_utc,
            reload_verified_at_utc=reload_verified_at_utc,
            committed_at_utc=committed_at_utc,
        )

    def _execute_from_completed_directory(
        self,
        *,
        confirm_irreversible_consumption: bool,
        input_directory: Path,
        identity_key_descriptor: int,
        restricted_reference_directory_descriptor: int,
        frozen_pipeline: P8FrozenLockedPipeline,
        output_directory: Path,
        expected_seal_sha256: str,
        expected_actual_source_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
        report_id: str,
        pending_at_utc: str,
        cleanup_recorded_at_utc: str,
        reload_verified_at_utc: str,
        committed_at_utc: str,
    ) -> P8LockedEvaluationReport:
        if confirm_irreversible_consumption is not True:
            raise P8Aihub538SourceError(
                "p8_locked_irreversible_consumption_confirmation_required"
            )
        source = DeferredP8LockedSource.from_completed_directory(
            input_directory
        )
        seal = self._state_store.load_read_zero_seal()
        actual_source_seal = (
            self._state_store.load_actual_source_read_zero_seal()
        )
        if (
            seal.seal_sha256 != expected_seal_sha256
            or actual_source_seal.actual_source_seal_sha256
            != expected_actual_source_seal_sha256
        ):
            raise P8Aihub538SourceError("p8_read_zero_seal_pin_mismatch")
        from ..adapters.p8_actual_source_reload import (
            p8_actual_source_reload_worker_code_sha256,
        )

        if (
            actual_source_seal.fresh_reload_worker_code_sha256
            != p8_actual_source_reload_worker_code_sha256()
        ):
            raise P8Aihub538SourceError(
                "p8_actual_source_reload_worker_pin_mismatch"
            )
        sealed_references = load_p8_sealed_reference_bundle(
            restricted_state_directory_descriptor=(
                restricted_reference_directory_descriptor
            ),
            identity_key_descriptor=identity_key_descriptor,
            base_read_zero_seal=seal,
            actual_source_seal=actual_source_seal,
        )
        evaluator = P8Aihub538LockedSourceEvaluator(
            state_store=self._state_store,
            source=source,
            output_directory=output_directory,
            sealed_references=sealed_references,
            actual_source_seal=actual_source_seal,
            frozen_pipeline=frozen_pipeline,
            report_id=report_id,
            pending_at_utc=pending_at_utc,
            cleanup_recorded_at_utc=cleanup_recorded_at_utc,
            archive_policy=self._archive_policy,
        )
        evaluator._validate_preaccess_inputs(seal)
        report = P8LockedEvaluationService(
            self._state_store
        ).execute_locked_evaluation(
            expected_seal_sha256=expected_seal_sha256,
            transaction_id=transaction_id,
            consumed_at_utc=consumed_at_utc,
            report_id=report_id,
            open_and_evaluate_aggregate=evaluator,
        )
        receipt_store = P8LockedSourceReceiptStore(output_directory)
        cleanup_state = self._state_store.load_actual_source_cleanup_state()
        lineage_sha256: str | None = None
        if report.locked_source_manifest_sha256 is not None:
            lineage = receipt_store.load_lineage()
            if (
                lineage.source_manifest_sha256
                != report.locked_source_manifest_sha256
                or lineage.aggregate_metrics_sha256
                != report.aggregate_metrics_sha256
            ):
                raise P8Aihub538SourceError(
                    "p8_locked_final_lineage_report_mismatch"
                )
            lineage_sha256 = lineage.lineage_sha256
        completeness = receipt_store.load_failure_completeness_if_present()
        terminal = P8LockedSourceTerminalReceipt.issue(
            report=report,
            frozen_pipeline_pins_sha256=p8_frozen_pipeline_pins_sha256(seal),
            cleanup_state=cleanup_state,
            aggregate_lineage_sha256=lineage_sha256,
            failure_completeness_sha256=(
                completeness.receipt_sha256
                if completeness is not None
                else None
            ),
        )
        receipt_store.persist_terminal(terminal)
        if receipt_store.load_terminal() != terminal:
            raise P8Aihub538SourceError(
                "p8_locked_source_terminal_reload_mismatch"
            )
        from ..adapters.p8_actual_source_reload import (
            P8ActualSourceFreshReloadAdapter,
        )

        reload_receipt, observed_terminal_sha256 = (
            P8ActualSourceFreshReloadAdapter().reload(
                state_store=self._state_store,
                output_directory=output_directory,
                verified_at_utc=reload_verified_at_utc,
            )
        )
        if observed_terminal_sha256 != terminal.terminal_sha256:
            raise P8Aihub538SourceError(
                "p8_locked_source_terminal_fresh_reload_mismatch"
            )
        self._state_store.create_actual_source_fresh_reload_receipt(
            reload_receipt
        )
        commit = P8ActualSourceFinalResultCommit.issue(
            fresh_reload_receipt=reload_receipt,
            source_terminal_sha256=terminal.terminal_sha256,
            committed_at_utc=committed_at_utc,
        )
        self._state_store.create_actual_source_final_commit(commit)
        return self._state_store.load_locked_report()


def _issue_metrics(
    *,
    dataset_evaluation: P8LockedDatasetEvaluationAggregate,
    source_manifest: P8LockedSourceManifest,
) -> P8AggregateEvaluationMetrics:
    replay = dataset_evaluation.claim_replay
    return P8AggregateEvaluationMetrics.issue(
        locked_source_manifest_sha256=source_manifest.manifest_sha256,
        admitted_item_count=dataset_evaluation.admitted_item_count,
        paired_item_count=dataset_evaluation.paired_item_count,
        evaluated_item_count=dataset_evaluation.evaluated_item_count,
        evaluation_exclusion_count=(
            dataset_evaluation.evaluation_exclusion_count
        ),
        successful_item_count=dataset_evaluation.successful_item_count,
        failed_item_count=dataset_evaluation.failed_item_count,
        qc_decision_lineage_valid_item_count=(
            dataset_evaluation.qc_decision_lineage_valid_item_count
        ),
        decode_success_rate=dataset_evaluation.decode_success_rate,
        supported_clip_count=dataset_evaluation.supported_clip_count,
        supported_clip_below_single_face_threshold_count=(
            dataset_evaluation.supported_clip_below_single_face_threshold_count
        ),
        aggregate_supported_slice_coverage=(
            dataset_evaluation.aggregate_supported_slice_coverage
        ),
        av_anchor_availability_rate=(
            dataset_evaluation.av_anchor_availability_rate
        ),
        ready_case_count=dataset_evaluation.ready_case_count,
        ready_sync_decision_lineage_valid_case_count=(
            dataset_evaluation.ready_sync_decision_lineage_valid_case_count
        ),
        maximum_ready_absolute_sync_offset_ms=(
            dataset_evaluation.maximum_ready_absolute_sync_offset_ms
        ),
        claim_policy_violation_count=replay.claim_policy_violation_count,
        forbidden_claim_fixture_violation_count=(
            replay.forbidden_claim_fixture_violation_count
        ),
    )


def _merge_repack_validation_match(
    merge: SplitZipMergeReceipt | SplitTarMergeReceipt,
    repack: InnerTarZipRepackReceipt | None,
    validation: ZipValidationReceipt,
) -> bool:
    if isinstance(merge, SplitZipMergeReceipt):
        return repack is None and merge.merged_sha256 == validation.archive_sha256
    return (
        isinstance(merge, SplitTarMergeReceipt)
        and repack is not None
        and merge.merged_sha256 == repack.source_tar_sha256
        and repack.repacked_zip_sha256 == validation.archive_sha256
        and repack.repacked_zip_content_root_sha256
        == validation.content_root_sha256
    )


def _normalized_candidate_bytes(candidate: _LockedCandidate) -> int:
    if candidate.repack_receipt is not None:
        return candidate.repack_receipt.repacked_zip_bytes
    return candidate.merge_receipt.merged_bytes


def _probe_locked_role(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy,
) -> Literal["locked_media", "locked_labels"]:
    try:
        with open_validated_zip_descriptor(descriptor, policy=policy) as opened:
            _archive, infos, names, _receipt = opened
            regular = tuple(
                (info, PurePosixPath(name))
                for info, name in zip(infos, names, strict=True)
                if not info.is_dir()
            )
            suffixes = {path.suffix for _, path in regular}
            if regular and suffixes == {".json"} and all(
                path.parts.count(_LABEL_ROLE_COMPONENT) == 1
                for _, path in regular
            ):
                return "locked_labels"
            if (
                regular
                and suffixes <= {".mp4", ".wav"}
                and {".mp4", ".wav"} <= suffixes
                and all(
                    path.parts.count(_SOURCE_ROLE_COMPONENT) == 1
                    for _, path in regular
                )
            ):
                return "locked_media"
    except (Aihub538ArchiveError, OSError, RuntimeError, ValueError):
        pass
    raise P8Aihub538SourceError("p8_locked_semantic_role_pair_invalid")


def _one_role(
    candidates: Sequence[_LockedCandidate],
    role: Literal["locked_media", "locked_labels"],
) -> _LockedCandidate:
    selected = [candidate for candidate in candidates if candidate.role == role]
    if len(selected) != 1:
        raise P8Aihub538SourceError("p8_locked_semantic_role_pair_invalid")
    return selected[0]


def _create_private_child(parent_descriptor: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o700)
        os.fsync(parent_descriptor)
        return descriptor
    except OSError:
        raise P8Aihub538SourceError(
            "p8_locked_work_directory_creation_failed"
        ) from None


def _remove_private_child(
    *,
    parent_descriptor: int,
    child_descriptor: int,
    child_name: str,
) -> tuple[int, int]:
    try:
        files, directories = _remove_contents_counted(child_descriptor)
        os.rmdir(child_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return files, directories + 1
    except OSError:
        raise P8Aihub538SourceError("p8_locked_workspace_cleanup_failed") from None


def _remove_contents_counted(directory_descriptor: int) -> tuple[int, int]:
    regular_files = 0
    directories = 0
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
                child_files, child_directories = _remove_contents_counted(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
            regular_files += child_files
            directories += child_directories + 1
        elif stat.S_ISREG(metadata.st_mode):
            os.unlink(name, dir_fd=directory_descriptor)
            regular_files += 1
        else:
            raise P8Aihub538SourceError(
                "p8_locked_workspace_member_type_invalid"
            )
    os.fsync(directory_descriptor)
    return regular_files, directories


def _entry_absent_at(directory_descriptor: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        raise P8Aihub538SourceError(
            "p8_locked_workspace_absence_check_failed"
        ) from None
    return False


def _read_strict_json_at(
    directory_descriptor: int, filename: str
) -> object:
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or not 0 < metadata.st_size <= _MAXIMUM_SOURCE_RECEIPT_BYTES
        ):
            raise P8Aihub538SourceError(
                "p8_locked_source_receipt_metadata_invalid"
            )
        before = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        payload = os.pread(descriptor, metadata.st_size, 0)
        after_metadata = os.fstat(descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mtime_ns,
        )
        if len(payload) != metadata.st_size or before != after:
            raise P8Aihub538SourceError(
                "p8_locked_source_receipt_changed_during_read"
            )
    except P8Aihub538SourceError:
        raise
    except OSError:
        raise P8Aihub538SourceError(
            "p8_locked_source_receipt_unavailable"
        ) from None
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    try:
        return json.loads(payload, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise P8Aihub538SourceError(
            "p8_locked_source_receipt_invalid_json"
        ) from None


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise P8Aihub538SourceError(
                "p8_locked_source_receipt_duplicate_key"
            )
        document[key] = value
    return document


__all__ = [
    "DeferredP8LockedSource",
    "P8Aihub538LockedSourceEvaluator",
    "P8Aihub538OneShotRunner",
    "P8Aihub538SourceError",
    "P8LockedSourceAggregateLineageReceipt",
    "P8LockedSourceReceiptStore",
    "P8LockedSourceTerminalReceipt",
    "P8ReloadedLockedSourceLineage",
    "P8LockedWorkspaceCleanupReceipt",
]
