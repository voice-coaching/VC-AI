"""Aggregate-only manifest for the bytes opened by the one-shot P8 run.

The read-zero seal can pin only the public AI Hub file listing because the
locked archives have not been opened yet.  This model is deliberately issued
after access authorization is durably consumed and records only archive-level
digests and sizes; it contains no path, member name, actor, prompt, or sample
identifier.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from .p8_aggregate_metrics import P8AggregateEvaluationMetrics
from .p8_evaluation_completeness import (
    P8_EVALUATION_EXCLUSION_POLICY,
    require_p8_evaluation_exclusion_policy,
)
from .p8_locked_evaluation import (
    P8AccessTransaction,
    P8_LOCKED_DATASET_ID,
    P8_LOCKED_SOURCE_LISTING_INPUTS,
    P8_LOCKED_SOURCE_LISTING_SHA256,
)

P8_LOCKED_SOURCE_ARCHIVE_DIGEST_SCHEMA_VERSION = (
    "voice_coach.p8_locked_source_archive_digest.v1"
)
P8_LOCKED_SOURCE_MANIFEST_SCHEMA_VERSION = (
    "voice_coach.p8_locked_source_manifest.v2"
)
P8_LOCKED_SOURCE_EVALUATION_RESULT_SCHEMA_VERSION = (
    "voice_coach.p8_locked_source_evaluation_result.v2"
)

_EXPECTED_SOURCE_ROLES = {
    entry.file_key: (entry.partition_id, entry.role)
    for entry in P8_LOCKED_SOURCE_LISTING_INPUTS
}


@dataclass(frozen=True, slots=True)
class P8LockedSourceArchiveDigest:
    """One post-access archive digest without a filesystem locator."""

    file_key: str
    partition_id: str
    role: str
    byte_count: int
    archive_sha256: str
    schema_version: str = P8_LOCKED_SOURCE_ARCHIVE_DIGEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_SOURCE_ARCHIVE_DIGEST_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 locked source archive schema differs"
            )
        file_key = require_safe_identifier(self.file_key, "file_key")
        require_safe_identifier(self.partition_id, "partition_id")
        require_safe_identifier(self.role, "role")
        expected = _EXPECTED_SOURCE_ROLES.get(file_key)
        if expected is None or (self.partition_id, self.role) != expected:
            raise VisualFeedbackContractError(
                "P8 locked source archive does not match the sealed listing"
            )
        require_integer(self.byte_count, "byte_count", minimum=1)
        require_sha256(self.archive_sha256, "archive_sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "file_key": self.file_key,
            "partition_id": self.partition_id,
            "role": self.role,
            "byte_count": self.byte_count,
            "archive_sha256": self.archive_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8LockedSourceArchiveDigest:
        document = require_mapping(value, "p8_locked_source_archive_digest")
        require_exact_keys(
            document,
            {
                "schema_version",
                "file_key",
                "partition_id",
                "role",
                "byte_count",
                "archive_sha256",
            },
            "p8_locked_source_archive_digest",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            file_key=require_safe_identifier(document["file_key"], "file_key"),
            partition_id=require_safe_identifier(
                document["partition_id"], "partition_id"
            ),
            role=require_safe_identifier(document["role"], "role"),
            byte_count=require_integer(
                document["byte_count"], "byte_count", minimum=1
            ),
            archive_sha256=require_sha256(
                document["archive_sha256"], "archive_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedSourceManifest:
    """Self-digested proof of the exact two archives opened by P8."""

    source_revision: str
    access_transaction_sha256: str
    archives: tuple[P8LockedSourceArchiveDigest, ...]
    total_byte_count: int
    admitted_item_count: int
    evaluation_exclusion_policy: str
    manifest_sha256: str
    dataset_id: str = P8_LOCKED_DATASET_ID
    source_listing_sha256: str = P8_LOCKED_SOURCE_LISTING_SHA256
    aggregate_only: bool = True
    schema_version: str = P8_LOCKED_SOURCE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_SOURCE_MANIFEST_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 locked source manifest schema differs"
            )
        require_safe_identifier(self.source_revision, "source_revision")
        require_sha256(
            self.access_transaction_sha256, "access_transaction_sha256"
        )
        if self.dataset_id != P8_LOCKED_DATASET_ID:
            raise VisualFeedbackContractError(
                "P8 locked source manifest dataset differs"
            )
        if self.source_listing_sha256 != P8_LOCKED_SOURCE_LISTING_SHA256:
            raise VisualFeedbackContractError(
                "P8 locked source manifest listing differs"
            )
        expected_keys = tuple(
            entry.file_key for entry in P8_LOCKED_SOURCE_LISTING_INPUTS
        )
        if tuple(entry.file_key for entry in self.archives) != expected_keys:
            raise VisualFeedbackContractError(
                "P8 locked source manifest archive set differs"
            )
        if len({entry.archive_sha256 for entry in self.archives}) != len(
            self.archives
        ):
            raise VisualFeedbackContractError(
                "P8 locked source archives must have distinct digests"
            )
        total = require_integer(
            self.total_byte_count, "total_byte_count", minimum=1
        )
        if total != sum(entry.byte_count for entry in self.archives):
            raise VisualFeedbackContractError(
                "P8 locked source manifest byte total differs"
            )
        require_integer(
            self.admitted_item_count,
            "admitted_item_count",
            minimum=1,
        )
        require_p8_evaluation_exclusion_policy(
            self.evaluation_exclusion_policy
        )
        if require_boolean(self.aggregate_only, "aggregate_only") is not True:
            raise VisualFeedbackContractError(
                "P8 locked source manifest must be aggregate-only"
            )
        require_sha256(self.manifest_sha256, "manifest_sha256")
        if self.manifest_sha256 == self.source_listing_sha256:
            raise VisualFeedbackContractError(
                "P8 actual manifest and public listing digests must differ"
            )
        if self.manifest_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 locked source manifest digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        source_revision: str,
        transaction: P8AccessTransaction,
        archives: tuple[P8LockedSourceArchiveDigest, ...],
        admitted_item_count: int,
    ) -> P8LockedSourceManifest:
        if transaction.dataset_id != P8_LOCKED_DATASET_ID:
            raise VisualFeedbackContractError(
                "P8 locked source transaction dataset differs"
            )
        total_byte_count = sum(entry.byte_count for entry in archives)
        unsigned = {
            "schema_version": P8_LOCKED_SOURCE_MANIFEST_SCHEMA_VERSION,
            "source_revision": source_revision,
            "dataset_id": P8_LOCKED_DATASET_ID,
            "source_listing_sha256": transaction.source_listing_sha256,
            "access_transaction_sha256": transaction.transaction_sha256,
            "archives": [entry.to_json_object() for entry in archives],
            "total_byte_count": total_byte_count,
            "admitted_item_count": admitted_item_count,
            "evaluation_exclusion_policy": P8_EVALUATION_EXCLUSION_POLICY,
            "aggregate_only": True,
        }
        return cls(
            source_revision=source_revision,
            access_transaction_sha256=transaction.transaction_sha256,
            archives=archives,
            total_byte_count=total_byte_count,
            admitted_item_count=admitted_item_count,
            evaluation_exclusion_policy=P8_EVALUATION_EXCLUSION_POLICY,
            manifest_sha256=sha256_document(unsigned),
            dataset_id=P8_LOCKED_DATASET_ID,
            source_listing_sha256=transaction.source_listing_sha256,
            aggregate_only=True,
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_revision": self.source_revision,
            "dataset_id": self.dataset_id,
            "source_listing_sha256": self.source_listing_sha256,
            "access_transaction_sha256": self.access_transaction_sha256,
            "archives": [entry.to_json_object() for entry in self.archives],
            "total_byte_count": self.total_byte_count,
            "admitted_item_count": self.admitted_item_count,
            "evaluation_exclusion_policy": self.evaluation_exclusion_policy,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "manifest_sha256": self.manifest_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8LockedSourceManifest:
        document = require_mapping(value, "p8_locked_source_manifest")
        require_exact_keys(
            document,
            {
                "schema_version",
                "source_revision",
                "dataset_id",
                "source_listing_sha256",
                "access_transaction_sha256",
                "archives",
                "total_byte_count",
                "admitted_item_count",
                "evaluation_exclusion_policy",
                "aggregate_only",
                "manifest_sha256",
            },
            "p8_locked_source_manifest",
        )
        archives_value = document["archives"]
        if isinstance(archives_value, (str, bytes)) or not isinstance(
            archives_value, Sequence
        ):
            raise VisualFeedbackContractError("archives must be an array")
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            source_revision=require_safe_identifier(
                document["source_revision"], "source_revision"
            ),
            dataset_id=require_safe_identifier(
                document["dataset_id"], "dataset_id"
            ),
            source_listing_sha256=require_sha256(
                document["source_listing_sha256"], "source_listing_sha256"
            ),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            archives=tuple(
                P8LockedSourceArchiveDigest.from_json_object(entry)
                for entry in archives_value
            ),
            total_byte_count=require_integer(
                document["total_byte_count"], "total_byte_count", minimum=1
            ),
            admitted_item_count=require_integer(
                document["admitted_item_count"],
                "admitted_item_count",
                minimum=1,
            ),
            evaluation_exclusion_policy=(
                require_p8_evaluation_exclusion_policy(
                    document["evaluation_exclusion_policy"]
                )
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            manifest_sha256=require_sha256(
                document["manifest_sha256"], "manifest_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedSourceEvaluationResult:
    """Strict callback result binding actual archive bytes to aggregate metrics."""

    source_manifest: P8LockedSourceManifest
    metrics: P8AggregateEvaluationMetrics
    result_sha256: str
    aggregate_only: bool = True
    schema_version: str = P8_LOCKED_SOURCE_EVALUATION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_SOURCE_EVALUATION_RESULT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 locked source evaluation result schema differs"
            )
        if (
            self.metrics.locked_source_manifest_sha256
            != self.source_manifest.manifest_sha256
        ):
            raise VisualFeedbackContractError(
                "P8 locked source metrics do not bind the actual manifest"
            )
        if (
            self.metrics.admitted_item_count
            != self.source_manifest.admitted_item_count
        ):
            raise VisualFeedbackContractError(
                "P8 locked source admission count differs"
            )
        if (
            self.metrics.evaluation_exclusion_policy
            != self.source_manifest.evaluation_exclusion_policy
        ):
            raise VisualFeedbackContractError(
                "P8 locked source exclusion policy differs"
            )
        if require_boolean(self.aggregate_only, "aggregate_only") is not True:
            raise VisualFeedbackContractError(
                "P8 locked source evaluation result must be aggregate-only"
            )
        require_sha256(self.result_sha256, "result_sha256")
        if self.result_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 locked source evaluation result digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        source_manifest: P8LockedSourceManifest,
        metrics: P8AggregateEvaluationMetrics,
    ) -> P8LockedSourceEvaluationResult:
        unsigned = {
            "schema_version": P8_LOCKED_SOURCE_EVALUATION_RESULT_SCHEMA_VERSION,
            "source_manifest": source_manifest.to_json_object(),
            "metrics": metrics.to_json_object(),
            "aggregate_only": True,
        }
        return cls(
            source_manifest=source_manifest,
            metrics=metrics,
            result_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_manifest": self.source_manifest.to_json_object(),
            "metrics": self.metrics.to_json_object(),
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "result_sha256": self.result_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8LockedSourceEvaluationResult:
        document = require_mapping(value, "p8_locked_source_evaluation_result")
        require_exact_keys(
            document,
            {
                "schema_version",
                "source_manifest",
                "metrics",
                "aggregate_only",
                "result_sha256",
            },
            "p8_locked_source_evaluation_result",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            source_manifest=P8LockedSourceManifest.from_json_object(
                document["source_manifest"]
            ),
            metrics=P8AggregateEvaluationMetrics.from_json_object(
                document["metrics"]
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            result_sha256=require_sha256(
                document["result_sha256"], "result_sha256"
            ),
        )


__all__ = [
    "P8_LOCKED_SOURCE_ARCHIVE_DIGEST_SCHEMA_VERSION",
    "P8_LOCKED_SOURCE_EVALUATION_RESULT_SCHEMA_VERSION",
    "P8_LOCKED_SOURCE_MANIFEST_SCHEMA_VERSION",
    "P8LockedSourceArchiveDigest",
    "P8LockedSourceEvaluationResult",
    "P8LockedSourceManifest",
]
