"""Immutable companion state for the real-source P8 one-shot gate.

These contracts extend the actual-source execution without changing the
existing P8 read-zero seal v4/report v3 schemas.  They contain aggregate digests
and counts only: no path, participant identifier, transcript, or individual
identity HMAC is part of their serialized surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    require_utc_timestamp,
    sha256_document,
)


P8_CANONICAL_RESTRICTED_IDENTITY_COMMITMENT_SCHEMA_VERSION = (
    "voice_coach.p8_canonical_restricted_identity_commitment.v1"
)
P8_ACTUAL_SOURCE_READ_ZERO_SEAL_SCHEMA_VERSION = (
    "voice_coach.p8_actual_source_read_zero_seal.v1"
)
P8_ACTUAL_SOURCE_GATE_PENDING_SCHEMA_VERSION = (
    "voice_coach.p8_actual_source_gate_pending.v1"
)
P8_ACTUAL_SOURCE_CLEANUP_STATE_SCHEMA_VERSION = (
    "voice_coach.p8_actual_source_cleanup_state.v1"
)
P8_ACTUAL_SOURCE_FRESH_PROCESS_RELOAD_SCHEMA_VERSION = (
    "voice_coach.p8_actual_source_fresh_process_reload.v2"
)
P8_ACTUAL_SOURCE_FINAL_RESULT_COMMIT_SCHEMA_VERSION = (
    "voice_coach.p8_actual_source_final_result_commit.v1"
)
P8_ACTUAL_SOURCE_NETWORK_ISOLATION = "seccomp_socket_denied"


class P8ReferenceIdentityRole(str, Enum):
    """Exact pre-access reference populations pinned by the companion seal."""

    P6_CALIBRATION = "p6_calibration"
    TS174_TRAINING = "ts174_training"


class P8ActualSourceCleanupStatus(str, Enum):
    """Sticky terminal cleanup evidence for one actual-source gate."""

    COMPLETED = "cleanup_completed"
    INCOMPLETE = "cleanup_incomplete"


class P8ActualSourceResultStatus(str, Enum):
    """Canonical P8 report outcomes permitted by an actual-source commit."""

    COMPLETED = "completed_aggregate_only"
    FAILED_CLOSED = "failed_closed"


def _optional_sha256(value: object, name: str) -> str | None:
    if value is None:
        return None
    return require_sha256(value, name)


def _reference_role(value: object) -> P8ReferenceIdentityRole:
    try:
        return P8ReferenceIdentityRole(
            require_safe_identifier(value, "reference_identity_role")
        )
    except ValueError as error:
        raise VisualFeedbackContractError(
            "P8 reference identity role differs"
        ) from error


def _cleanup_status(value: object) -> P8ActualSourceCleanupStatus:
    try:
        return P8ActualSourceCleanupStatus(
            require_safe_identifier(value, "cleanup_status")
        )
    except ValueError as error:
        raise VisualFeedbackContractError(
            "P8 actual-source cleanup status differs"
        ) from error


def _result_status(value: object) -> P8ActualSourceResultStatus:
    try:
        return P8ActualSourceResultStatus(
            require_safe_identifier(value, "result_status")
        )
    except ValueError as error:
        raise VisualFeedbackContractError(
            "P8 actual-source result status differs"
        ) from error


@dataclass(frozen=True, slots=True)
class P8CanonicalRestrictedIdentityCommitment:
    """Public root/count/namespace commitment to one private identity set."""

    role: P8ReferenceIdentityRole
    strict_admission_schema_version: str
    strict_admission_receipt_sha256: str
    strict_admission_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    identity_set_root_sha256: str
    actor_hmac_count: int
    attempt_hmac_count: int
    commitment_sha256: str
    schema_version: str = (
        P8_CANONICAL_RESTRICTED_IDENTITY_COMMITMENT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != P8_CANONICAL_RESTRICTED_IDENTITY_COMMITMENT_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError(
                "P8 identity commitment schema differs"
            )
        if not isinstance(self.role, P8ReferenceIdentityRole):
            raise VisualFeedbackContractError(
                "P8 reference identity role differs"
            )
        require_safe_identifier(
            self.strict_admission_schema_version,
            "strict_admission_schema_version",
        )
        for name in (
            "strict_admission_receipt_sha256",
            "strict_admission_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "identity_set_root_sha256",
            "commitment_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_integer(self.actor_hmac_count, "actor_hmac_count", minimum=1)
        require_integer(
            self.attempt_hmac_count, "attempt_hmac_count", minimum=1
        )
        if self.commitment_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise VisualFeedbackContractError(
                "P8 identity commitment digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        role: P8ReferenceIdentityRole,
        strict_admission_schema_version: str,
        strict_admission_receipt_sha256: str,
        strict_admission_manifest_sha256: str,
        identity_namespace_commitment_sha256: str,
        identity_set_root_sha256: str,
        actor_hmac_count: int,
        attempt_hmac_count: int,
    ) -> "P8CanonicalRestrictedIdentityCommitment":
        unsigned = {
            "schema_version": (
                P8_CANONICAL_RESTRICTED_IDENTITY_COMMITMENT_SCHEMA_VERSION
            ),
            "role": role.value,
            "strict_admission_schema_version": (
                strict_admission_schema_version
            ),
            "strict_admission_receipt_sha256": (
                strict_admission_receipt_sha256
            ),
            "strict_admission_manifest_sha256": (
                strict_admission_manifest_sha256
            ),
            "identity_namespace_commitment_sha256": (
                identity_namespace_commitment_sha256
            ),
            "identity_set_root_sha256": identity_set_root_sha256,
            "actor_hmac_count": actor_hmac_count,
            "attempt_hmac_count": attempt_hmac_count,
            "individual_identity_hmacs_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            role=role,
            strict_admission_schema_version=strict_admission_schema_version,
            strict_admission_receipt_sha256=(
                strict_admission_receipt_sha256
            ),
            strict_admission_manifest_sha256=(
                strict_admission_manifest_sha256
            ),
            identity_namespace_commitment_sha256=(
                identity_namespace_commitment_sha256
            ),
            identity_set_root_sha256=identity_set_root_sha256,
            actor_hmac_count=actor_hmac_count,
            attempt_hmac_count=attempt_hmac_count,
            commitment_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "role": self.role.value,
            "strict_admission_schema_version": (
                self.strict_admission_schema_version
            ),
            "strict_admission_receipt_sha256": (
                self.strict_admission_receipt_sha256
            ),
            "strict_admission_manifest_sha256": (
                self.strict_admission_manifest_sha256
            ),
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "identity_set_root_sha256": self.identity_set_root_sha256,
            "actor_hmac_count": self.actor_hmac_count,
            "attempt_hmac_count": self.attempt_hmac_count,
            "individual_identity_hmacs_serialized": False,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "commitment_sha256": self.commitment_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8CanonicalRestrictedIdentityCommitment":
        document = require_mapping(value, "p8_identity_commitment")
        require_exact_keys(
            document,
            {
                "schema_version",
                "role",
                "strict_admission_schema_version",
                "strict_admission_receipt_sha256",
                "strict_admission_manifest_sha256",
                "identity_namespace_commitment_sha256",
                "identity_set_root_sha256",
                "actor_hmac_count",
                "attempt_hmac_count",
                "individual_identity_hmacs_serialized",
                "aggregate_only",
                "commitment_sha256",
            },
            "p8_identity_commitment",
        )
        if require_boolean(
            document["individual_identity_hmacs_serialized"],
            "individual_identity_hmacs_serialized",
        ):
            raise VisualFeedbackContractError(
                "P8 identity commitment must not serialize identity HMACs"
            )
        if not require_boolean(document["aggregate_only"], "aggregate_only"):
            raise VisualFeedbackContractError(
                "P8 identity commitment must be aggregate-only"
            )
        return cls(
            role=_reference_role(document["role"]),
            strict_admission_schema_version=require_safe_identifier(
                document["strict_admission_schema_version"],
                "strict_admission_schema_version",
            ),
            strict_admission_receipt_sha256=require_sha256(
                document["strict_admission_receipt_sha256"],
                "strict_admission_receipt_sha256",
            ),
            strict_admission_manifest_sha256=require_sha256(
                document["strict_admission_manifest_sha256"],
                "strict_admission_manifest_sha256",
            ),
            identity_namespace_commitment_sha256=require_sha256(
                document["identity_namespace_commitment_sha256"],
                "identity_namespace_commitment_sha256",
            ),
            identity_set_root_sha256=require_sha256(
                document["identity_set_root_sha256"],
                "identity_set_root_sha256",
            ),
            actor_hmac_count=require_integer(
                document["actor_hmac_count"],
                "actor_hmac_count",
                minimum=1,
            ),
            attempt_hmac_count=require_integer(
                document["attempt_hmac_count"],
                "attempt_hmac_count",
                minimum=1,
            ),
            commitment_sha256=require_sha256(
                document["commitment_sha256"], "commitment_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ActualSourceReadZeroSeal:
    """Companion seal that makes an existing P8 v4 seal source-capable."""

    base_read_zero_seal_sha256: str
    p6_evidence_receipt_sha256: str
    reference_commitments: tuple[
        P8CanonicalRestrictedIdentityCommitment,
        P8CanonicalRestrictedIdentityCommitment,
    ]
    fresh_reload_worker_code_sha256: str
    sealed_at_utc: str
    access_count: int
    sealed_before_source_access: bool
    aggregate_only: bool
    actual_source_seal_sha256: str
    schema_version: str = P8_ACTUAL_SOURCE_READ_ZERO_SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_ACTUAL_SOURCE_READ_ZERO_SEAL_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 actual-source seal schema differs"
            )
        for name in (
            "base_read_zero_seal_sha256",
            "p6_evidence_receipt_sha256",
            "fresh_reload_worker_code_sha256",
            "actual_source_seal_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.base_read_zero_seal_sha256 == self.actual_source_seal_sha256:
            raise VisualFeedbackContractError(
                "P8 actual-source seal must differ from the base seal"
            )
        if tuple(item.role for item in self.reference_commitments) != (
            P8ReferenceIdentityRole.P6_CALIBRATION,
            P8ReferenceIdentityRole.TS174_TRAINING,
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source reference commitments differ"
            )
        if (
            self.reference_commitments[0].strict_admission_receipt_sha256
            != self.p6_evidence_receipt_sha256
        ):
            raise VisualFeedbackContractError(
                "P8 P6 admission receipt binding differs"
            )
        if len(
            {
                item.identity_namespace_commitment_sha256
                for item in self.reference_commitments
            }
        ) != 1:
            raise VisualFeedbackContractError(
                "P8 actual-source identity namespaces differ"
            )
        require_utc_timestamp(self.sealed_at_utc, "sealed_at_utc")
        if require_integer(self.access_count, "access_count", minimum=0) != 0:
            raise VisualFeedbackContractError(
                "P8 actual-source seal access_count must be zero"
            )
        if not require_boolean(
            self.sealed_before_source_access, "sealed_before_source_access"
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source seal must precede source access"
            )
        if not require_boolean(self.aggregate_only, "aggregate_only"):
            raise VisualFeedbackContractError(
                "P8 actual-source seal must be aggregate-only"
            )
        if self.actual_source_seal_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source seal digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        base_read_zero_seal_sha256: str,
        p6_evidence_receipt_sha256: str,
        reference_commitments: tuple[
            P8CanonicalRestrictedIdentityCommitment,
            P8CanonicalRestrictedIdentityCommitment,
        ],
        fresh_reload_worker_code_sha256: str,
        sealed_at_utc: str,
    ) -> "P8ActualSourceReadZeroSeal":
        unsigned = {
            "schema_version": P8_ACTUAL_SOURCE_READ_ZERO_SEAL_SCHEMA_VERSION,
            "base_read_zero_seal_sha256": base_read_zero_seal_sha256,
            "p6_evidence_receipt_sha256": p6_evidence_receipt_sha256,
            "reference_commitments": [
                item.to_json_object() for item in reference_commitments
            ],
            "fresh_reload_worker_code_sha256": (
                fresh_reload_worker_code_sha256
            ),
            "sealed_at_utc": sealed_at_utc,
            "access_count": 0,
            "sealed_before_source_access": True,
            "individual_identity_hmacs_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            base_read_zero_seal_sha256=base_read_zero_seal_sha256,
            p6_evidence_receipt_sha256=p6_evidence_receipt_sha256,
            reference_commitments=reference_commitments,
            fresh_reload_worker_code_sha256=(
                fresh_reload_worker_code_sha256
            ),
            sealed_at_utc=sealed_at_utc,
            access_count=0,
            sealed_before_source_access=True,
            aggregate_only=True,
            actual_source_seal_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "base_read_zero_seal_sha256": self.base_read_zero_seal_sha256,
            "p6_evidence_receipt_sha256": self.p6_evidence_receipt_sha256,
            "reference_commitments": [
                item.to_json_object() for item in self.reference_commitments
            ],
            "fresh_reload_worker_code_sha256": (
                self.fresh_reload_worker_code_sha256
            ),
            "sealed_at_utc": self.sealed_at_utc,
            "access_count": self.access_count,
            "sealed_before_source_access": self.sealed_before_source_access,
            "individual_identity_hmacs_serialized": False,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8ActualSourceReadZeroSeal":
        document = require_mapping(value, "p8_actual_source_read_zero_seal")
        require_exact_keys(
            document,
            {
                "schema_version",
                "base_read_zero_seal_sha256",
                "p6_evidence_receipt_sha256",
                "reference_commitments",
                "fresh_reload_worker_code_sha256",
                "sealed_at_utc",
                "access_count",
                "sealed_before_source_access",
                "individual_identity_hmacs_serialized",
                "aggregate_only",
                "actual_source_seal_sha256",
            },
            "p8_actual_source_read_zero_seal",
        )
        commitments_value = document["reference_commitments"]
        if isinstance(commitments_value, (str, bytes)) or not isinstance(
            commitments_value, Sequence
        ):
            raise VisualFeedbackContractError(
                "P8 reference commitments must be an array"
            )
        commitments = tuple(
            P8CanonicalRestrictedIdentityCommitment.from_json_object(item)
            for item in commitments_value
        )
        if len(commitments) != 2:
            raise VisualFeedbackContractError(
                "P8 actual-source reference commitments differ"
            )
        if require_boolean(
            document["individual_identity_hmacs_serialized"],
            "individual_identity_hmacs_serialized",
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source seal must not serialize identity HMACs"
            )
        return cls(
            base_read_zero_seal_sha256=require_sha256(
                document["base_read_zero_seal_sha256"],
                "base_read_zero_seal_sha256",
            ),
            p6_evidence_receipt_sha256=require_sha256(
                document["p6_evidence_receipt_sha256"],
                "p6_evidence_receipt_sha256",
            ),
            reference_commitments=(commitments[0], commitments[1]),
            fresh_reload_worker_code_sha256=require_sha256(
                document["fresh_reload_worker_code_sha256"],
                "fresh_reload_worker_code_sha256",
            ),
            sealed_at_utc=require_utc_timestamp(
                document["sealed_at_utc"], "sealed_at_utc"
            ),
            access_count=require_integer(
                document["access_count"], "access_count", minimum=0
            ),
            sealed_before_source_access=require_boolean(
                document["sealed_before_source_access"],
                "sealed_before_source_access",
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ActualSourceGatePending:
    """Durable source-open gate written after consume and before any open."""

    base_read_zero_seal_sha256: str
    actual_source_seal_sha256: str
    access_transaction_sha256: str
    report_id: str
    frozen_pipeline_pins_sha256: str
    pending_at_utc: str
    cleanup_obligation_armed: bool
    source_open_allowed_only_after_fsync: bool
    aggregate_only: bool
    gate_pending_sha256: str
    schema_version: str = P8_ACTUAL_SOURCE_GATE_PENDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_ACTUAL_SOURCE_GATE_PENDING_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 actual-source gate-pending schema differs"
            )
        for name in (
            "base_read_zero_seal_sha256",
            "actual_source_seal_sha256",
            "access_transaction_sha256",
            "frozen_pipeline_pins_sha256",
            "gate_pending_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.base_read_zero_seal_sha256 == self.actual_source_seal_sha256:
            raise VisualFeedbackContractError(
                "P8 actual-source gate seal pins differ"
            )
        require_safe_identifier(self.report_id, "report_id")
        require_utc_timestamp(self.pending_at_utc, "pending_at_utc")
        if not require_boolean(
            self.cleanup_obligation_armed, "cleanup_obligation_armed"
        ):
            raise VisualFeedbackContractError(
                "P8 cleanup obligation must be armed"
            )
        if not require_boolean(
            self.source_open_allowed_only_after_fsync,
            "source_open_allowed_only_after_fsync",
        ):
            raise VisualFeedbackContractError(
                "P8 source open must follow a durable pending gate"
            )
        if not require_boolean(self.aggregate_only, "aggregate_only"):
            raise VisualFeedbackContractError(
                "P8 actual-source gate must be aggregate-only"
            )
        if self.gate_pending_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source gate-pending digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        actual_source_seal: P8ActualSourceReadZeroSeal,
        access_transaction_sha256: str,
        report_id: str,
        frozen_pipeline_pins_sha256: str,
        pending_at_utc: str,
    ) -> "P8ActualSourceGatePending":
        unsigned = {
            "schema_version": P8_ACTUAL_SOURCE_GATE_PENDING_SCHEMA_VERSION,
            "status": "gate_pending",
            "base_read_zero_seal_sha256": (
                actual_source_seal.base_read_zero_seal_sha256
            ),
            "actual_source_seal_sha256": (
                actual_source_seal.actual_source_seal_sha256
            ),
            "access_transaction_sha256": access_transaction_sha256,
            "report_id": report_id,
            "frozen_pipeline_pins_sha256": frozen_pipeline_pins_sha256,
            "pending_at_utc": pending_at_utc,
            "cleanup_obligation_armed": True,
            "source_open_allowed_only_after_fsync": True,
            "raw_paths_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            base_read_zero_seal_sha256=(
                actual_source_seal.base_read_zero_seal_sha256
            ),
            actual_source_seal_sha256=(
                actual_source_seal.actual_source_seal_sha256
            ),
            access_transaction_sha256=access_transaction_sha256,
            report_id=report_id,
            frozen_pipeline_pins_sha256=frozen_pipeline_pins_sha256,
            pending_at_utc=pending_at_utc,
            cleanup_obligation_armed=True,
            source_open_allowed_only_after_fsync=True,
            aggregate_only=True,
            gate_pending_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": "gate_pending",
            "base_read_zero_seal_sha256": self.base_read_zero_seal_sha256,
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
            "access_transaction_sha256": self.access_transaction_sha256,
            "report_id": self.report_id,
            "frozen_pipeline_pins_sha256": (
                self.frozen_pipeline_pins_sha256
            ),
            "pending_at_utc": self.pending_at_utc,
            "cleanup_obligation_armed": self.cleanup_obligation_armed,
            "source_open_allowed_only_after_fsync": (
                self.source_open_allowed_only_after_fsync
            ),
            "raw_paths_serialized": False,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "gate_pending_sha256": self.gate_pending_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8ActualSourceGatePending":
        document = require_mapping(value, "p8_actual_source_gate_pending")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "base_read_zero_seal_sha256",
                "actual_source_seal_sha256",
                "access_transaction_sha256",
                "report_id",
                "frozen_pipeline_pins_sha256",
                "pending_at_utc",
                "cleanup_obligation_armed",
                "source_open_allowed_only_after_fsync",
                "raw_paths_serialized",
                "aggregate_only",
                "gate_pending_sha256",
            },
            "p8_actual_source_gate_pending",
        )
        if document["status"] != "gate_pending":
            raise VisualFeedbackContractError(
                "P8 actual-source gate-pending status differs"
            )
        if require_boolean(
            document["raw_paths_serialized"], "raw_paths_serialized"
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source gate must not serialize paths"
            )
        return cls(
            base_read_zero_seal_sha256=require_sha256(
                document["base_read_zero_seal_sha256"],
                "base_read_zero_seal_sha256",
            ),
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            report_id=require_safe_identifier(
                document["report_id"], "report_id"
            ),
            frozen_pipeline_pins_sha256=require_sha256(
                document["frozen_pipeline_pins_sha256"],
                "frozen_pipeline_pins_sha256",
            ),
            pending_at_utc=require_utc_timestamp(
                document["pending_at_utc"], "pending_at_utc"
            ),
            cleanup_obligation_armed=require_boolean(
                document["cleanup_obligation_armed"],
                "cleanup_obligation_armed",
            ),
            source_open_allowed_only_after_fsync=require_boolean(
                document["source_open_allowed_only_after_fsync"],
                "source_open_allowed_only_after_fsync",
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            gate_pending_sha256=require_sha256(
                document["gate_pending_sha256"], "gate_pending_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ActualSourceCleanupState:
    """Immutable proof of removal, or sticky proof that removal is incomplete."""

    gate_pending_sha256: str
    access_transaction_sha256: str
    status: P8ActualSourceCleanupStatus
    restricted_work_directory_absent: bool
    restricted_bytes_may_remain: bool
    removed_regular_file_count: int | None
    removed_directory_count: int | None
    workspace_cleanup_receipt_sha256: str | None
    recorded_at_utc: str
    aggregate_only: bool
    cleanup_state_sha256: str
    schema_version: str = P8_ACTUAL_SOURCE_CLEANUP_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_ACTUAL_SOURCE_CLEANUP_STATE_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "P8 actual-source cleanup-state schema differs"
            )
        require_sha256(self.gate_pending_sha256, "gate_pending_sha256")
        require_sha256(
            self.access_transaction_sha256, "access_transaction_sha256"
        )
        if not isinstance(self.status, P8ActualSourceCleanupStatus):
            raise VisualFeedbackContractError(
                "P8 actual-source cleanup status differs"
            )
        require_boolean(
            self.restricted_work_directory_absent,
            "restricted_work_directory_absent",
        )
        require_boolean(
            self.restricted_bytes_may_remain,
            "restricted_bytes_may_remain",
        )
        if self.status is P8ActualSourceCleanupStatus.COMPLETED:
            if (
                self.restricted_work_directory_absent is not True
                or self.restricted_bytes_may_remain is not False
                or self.removed_regular_file_count is None
                or self.removed_directory_count is None
                or self.workspace_cleanup_receipt_sha256 is None
            ):
                raise VisualFeedbackContractError(
                    "P8 completed cleanup invariants differ"
                )
            require_integer(
                self.removed_regular_file_count,
                "removed_regular_file_count",
                minimum=0,
            )
            require_integer(
                self.removed_directory_count,
                "removed_directory_count",
                minimum=0,
            )
            require_sha256(
                self.workspace_cleanup_receipt_sha256,
                "workspace_cleanup_receipt_sha256",
            )
        elif (
            self.restricted_bytes_may_remain
            is self.restricted_work_directory_absent
            or self.removed_regular_file_count is not None
            or self.removed_directory_count is not None
            or self.workspace_cleanup_receipt_sha256 is not None
        ):
            raise VisualFeedbackContractError(
                "P8 incomplete cleanup invariants differ"
            )
        require_utc_timestamp(self.recorded_at_utc, "recorded_at_utc")
        if not require_boolean(self.aggregate_only, "aggregate_only"):
            raise VisualFeedbackContractError(
                "P8 cleanup state must be aggregate-only"
            )
        require_sha256(self.cleanup_state_sha256, "cleanup_state_sha256")
        if self.cleanup_state_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source cleanup-state digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        gate_pending: P8ActualSourceGatePending,
        status: P8ActualSourceCleanupStatus,
        recorded_at_utc: str,
        removed_regular_file_count: int | None = None,
        removed_directory_count: int | None = None,
        workspace_cleanup_receipt_sha256: str | None = None,
        restricted_work_directory_absent: bool | None = None,
    ) -> "P8ActualSourceCleanupState":
        completed = status is P8ActualSourceCleanupStatus.COMPLETED
        work_absent = (
            completed
            if restricted_work_directory_absent is None
            else require_boolean(
                restricted_work_directory_absent,
                "restricted_work_directory_absent",
            )
        )
        unsigned = {
            "schema_version": P8_ACTUAL_SOURCE_CLEANUP_STATE_SCHEMA_VERSION,
            "gate_pending_sha256": gate_pending.gate_pending_sha256,
            "access_transaction_sha256": (
                gate_pending.access_transaction_sha256
            ),
            "status": status.value,
            "restricted_work_directory_absent": work_absent,
            "restricted_bytes_may_remain": not work_absent,
            "removed_regular_file_count": removed_regular_file_count,
            "removed_directory_count": removed_directory_count,
            "workspace_cleanup_receipt_sha256": (
                workspace_cleanup_receipt_sha256
            ),
            "recorded_at_utc": recorded_at_utc,
            "raw_paths_serialized": False,
            "aggregate_only": True,
        }
        return cls(
            gate_pending_sha256=gate_pending.gate_pending_sha256,
            access_transaction_sha256=gate_pending.access_transaction_sha256,
            status=status,
            restricted_work_directory_absent=work_absent,
            restricted_bytes_may_remain=not work_absent,
            removed_regular_file_count=removed_regular_file_count,
            removed_directory_count=removed_directory_count,
            workspace_cleanup_receipt_sha256=(
                workspace_cleanup_receipt_sha256
            ),
            recorded_at_utc=recorded_at_utc,
            aggregate_only=True,
            cleanup_state_sha256=sha256_document(unsigned),
        )

    @classmethod
    def issue_completed(
        cls,
        *,
        gate_pending: P8ActualSourceGatePending,
        removed_regular_file_count: int,
        removed_directory_count: int,
        workspace_cleanup_receipt_sha256: str,
        recorded_at_utc: str,
    ) -> "P8ActualSourceCleanupState":
        return cls.issue(
            gate_pending=gate_pending,
            status=P8ActualSourceCleanupStatus.COMPLETED,
            removed_regular_file_count=removed_regular_file_count,
            removed_directory_count=removed_directory_count,
            workspace_cleanup_receipt_sha256=(
                workspace_cleanup_receipt_sha256
            ),
            recorded_at_utc=recorded_at_utc,
        )

    @classmethod
    def issue_incomplete(
        cls,
        *,
        gate_pending: P8ActualSourceGatePending,
        restricted_work_directory_absent: bool,
        recorded_at_utc: str,
    ) -> "P8ActualSourceCleanupState":
        return cls.issue(
            gate_pending=gate_pending,
            status=P8ActualSourceCleanupStatus.INCOMPLETE,
            restricted_work_directory_absent=(
                restricted_work_directory_absent
            ),
            recorded_at_utc=recorded_at_utc,
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "gate_pending_sha256": self.gate_pending_sha256,
            "access_transaction_sha256": self.access_transaction_sha256,
            "status": self.status.value,
            "restricted_work_directory_absent": (
                self.restricted_work_directory_absent
            ),
            "restricted_bytes_may_remain": self.restricted_bytes_may_remain,
            "removed_regular_file_count": self.removed_regular_file_count,
            "removed_directory_count": self.removed_directory_count,
            "workspace_cleanup_receipt_sha256": (
                self.workspace_cleanup_receipt_sha256
            ),
            "recorded_at_utc": self.recorded_at_utc,
            "raw_paths_serialized": False,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "cleanup_state_sha256": self.cleanup_state_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8ActualSourceCleanupState":
        document = require_mapping(value, "p8_actual_source_cleanup_state")
        require_exact_keys(
            document,
            {
                "schema_version",
                "gate_pending_sha256",
                "access_transaction_sha256",
                "status",
                "restricted_work_directory_absent",
                "restricted_bytes_may_remain",
                "removed_regular_file_count",
                "removed_directory_count",
                "workspace_cleanup_receipt_sha256",
                "recorded_at_utc",
                "raw_paths_serialized",
                "aggregate_only",
                "cleanup_state_sha256",
            },
            "p8_actual_source_cleanup_state",
        )
        if require_boolean(
            document["raw_paths_serialized"], "raw_paths_serialized"
        ):
            raise VisualFeedbackContractError(
                "P8 cleanup state must not serialize paths"
            )
        regular_count_value = document["removed_regular_file_count"]
        directory_count_value = document["removed_directory_count"]
        workspace_cleanup_receipt_value = document[
            "workspace_cleanup_receipt_sha256"
        ]
        return cls(
            gate_pending_sha256=require_sha256(
                document["gate_pending_sha256"], "gate_pending_sha256"
            ),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            status=_cleanup_status(document["status"]),
            restricted_work_directory_absent=require_boolean(
                document["restricted_work_directory_absent"],
                "restricted_work_directory_absent",
            ),
            restricted_bytes_may_remain=require_boolean(
                document["restricted_bytes_may_remain"],
                "restricted_bytes_may_remain",
            ),
            removed_regular_file_count=(
                None
                if regular_count_value is None
                else require_integer(
                    regular_count_value,
                    "removed_regular_file_count",
                    minimum=0,
                )
            ),
            removed_directory_count=(
                None
                if directory_count_value is None
                else require_integer(
                    directory_count_value,
                    "removed_directory_count",
                    minimum=0,
                )
            ),
            workspace_cleanup_receipt_sha256=_optional_sha256(
                workspace_cleanup_receipt_value,
                "workspace_cleanup_receipt_sha256",
            ),
            recorded_at_utc=require_utc_timestamp(
                document["recorded_at_utc"], "recorded_at_utc"
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            cleanup_state_sha256=require_sha256(
                document["cleanup_state_sha256"], "cleanup_state_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ActualSourceFreshProcessReloadReceipt:
    """Strict isolated reload proof issued before the final commit marker."""

    actual_source_seal_sha256: str
    gate_pending_sha256: str
    frozen_pipeline_pins_sha256: str
    access_transaction_sha256: str
    cleanup_state_sha256: str
    canonical_report_sha256: str
    locked_source_manifest_sha256: str | None
    aggregate_metrics_sha256: str | None
    aggregate_lineage_sha256: str | None
    result_status: P8ActualSourceResultStatus
    cleanup_status: P8ActualSourceCleanupStatus
    verifier_code_sha256: str
    verified_at_utc: str
    restricted_work_directory_absent: bool
    fresh_process: bool
    strict_reload: bool
    all_references_match: bool
    threshold_replay_match: bool
    cleanup_invariant_match: bool
    network_isolation: str
    promotion_authorized: bool
    aggregate_only: bool
    receipt_sha256: str
    schema_version: str = (
        P8_ACTUAL_SOURCE_FRESH_PROCESS_RELOAD_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != P8_ACTUAL_SOURCE_FRESH_PROCESS_RELOAD_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source reload schema differs"
            )
        for name in (
            "actual_source_seal_sha256",
            "gate_pending_sha256",
            "frozen_pipeline_pins_sha256",
            "access_transaction_sha256",
            "cleanup_state_sha256",
            "canonical_report_sha256",
            "verifier_code_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        for name in (
            "locked_source_manifest_sha256",
            "aggregate_metrics_sha256",
            "aggregate_lineage_sha256",
        ):
            _optional_sha256(getattr(self, name), name)
        evidence_presence = (
            self.locked_source_manifest_sha256 is not None,
            self.aggregate_metrics_sha256 is not None,
            self.aggregate_lineage_sha256 is not None,
        )
        if len(set(evidence_presence)) != 1:
            raise VisualFeedbackContractError(
                "P8 actual-source reload evidence pins differ"
            )
        if not isinstance(self.result_status, P8ActualSourceResultStatus):
            raise VisualFeedbackContractError(
                "P8 actual-source result status differs"
            )
        if not isinstance(self.cleanup_status, P8ActualSourceCleanupStatus):
            raise VisualFeedbackContractError(
                "P8 actual-source cleanup status differs"
            )
        require_boolean(
            self.restricted_work_directory_absent,
            "restricted_work_directory_absent",
        )
        if self.cleanup_status is P8ActualSourceCleanupStatus.COMPLETED:
            if self.restricted_work_directory_absent is not True:
                raise VisualFeedbackContractError(
                    "P8 completed cleanup reload did not prove work absence"
                )
        elif self.result_status is not P8ActualSourceResultStatus.FAILED_CLOSED:
            raise VisualFeedbackContractError(
                "P8 incomplete cleanup reload invariants differ"
            )
        if self.result_status is P8ActualSourceResultStatus.COMPLETED and (
            self.cleanup_status is not P8ActualSourceCleanupStatus.COMPLETED
            or not all(evidence_presence)
        ):
            raise VisualFeedbackContractError(
                "P8 completed reload evidence is incomplete"
            )
        require_utc_timestamp(self.verified_at_utc, "verified_at_utc")
        for name in (
            "fresh_process",
            "strict_reload",
            "all_references_match",
            "threshold_replay_match",
            "cleanup_invariant_match",
            "aggregate_only",
        ):
            if not require_boolean(getattr(self, name), name):
                raise VisualFeedbackContractError(
                    "P8 actual-source reload proof is incomplete"
                )
        if require_boolean(
            self.promotion_authorized, "promotion_authorized"
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source reload cannot authorize promotion"
            )
        if self.network_isolation != P8_ACTUAL_SOURCE_NETWORK_ISOLATION:
            raise VisualFeedbackContractError(
                "P8 actual-source reload isolation differs"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 actual-source reload receipt digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        actual_source_seal: P8ActualSourceReadZeroSeal,
        gate_pending: P8ActualSourceGatePending,
        cleanup_state: P8ActualSourceCleanupState,
        canonical_report_sha256: str,
        locked_source_manifest_sha256: str | None,
        aggregate_metrics_sha256: str | None,
        aggregate_lineage_sha256: str | None,
        result_status: P8ActualSourceResultStatus,
        verified_at_utc: str,
    ) -> "P8ActualSourceFreshProcessReloadReceipt":
        if (
            gate_pending.actual_source_seal_sha256
            != actual_source_seal.actual_source_seal_sha256
            or gate_pending.base_read_zero_seal_sha256
            != actual_source_seal.base_read_zero_seal_sha256
            or cleanup_state.gate_pending_sha256
            != gate_pending.gate_pending_sha256
            or cleanup_state.access_transaction_sha256
            != gate_pending.access_transaction_sha256
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source reload lineage differs"
            )
        unsigned = {
            "schema_version": (
                P8_ACTUAL_SOURCE_FRESH_PROCESS_RELOAD_SCHEMA_VERSION
            ),
            "actual_source_seal_sha256": (
                actual_source_seal.actual_source_seal_sha256
            ),
            "gate_pending_sha256": gate_pending.gate_pending_sha256,
            "frozen_pipeline_pins_sha256": (
                gate_pending.frozen_pipeline_pins_sha256
            ),
            "access_transaction_sha256": (
                gate_pending.access_transaction_sha256
            ),
            "cleanup_state_sha256": cleanup_state.cleanup_state_sha256,
            "canonical_report_sha256": canonical_report_sha256,
            "locked_source_manifest_sha256": locked_source_manifest_sha256,
            "aggregate_metrics_sha256": aggregate_metrics_sha256,
            "aggregate_lineage_sha256": aggregate_lineage_sha256,
            "result_status": result_status.value,
            "cleanup_status": cleanup_state.status.value,
            "verifier_code_sha256": (
                actual_source_seal.fresh_reload_worker_code_sha256
            ),
            "verified_at_utc": verified_at_utc,
            "restricted_work_directory_absent": (
                cleanup_state.restricted_work_directory_absent
            ),
            "fresh_process": True,
            "strict_reload": True,
            "all_references_match": True,
            "threshold_replay_match": True,
            "cleanup_invariant_match": True,
            "network_isolation": P8_ACTUAL_SOURCE_NETWORK_ISOLATION,
            "restricted_error_detail_serialized": False,
            "raw_paths_serialized": False,
            "promotion_authorized": False,
            "aggregate_only": True,
        }
        return cls(
            actual_source_seal_sha256=(
                actual_source_seal.actual_source_seal_sha256
            ),
            gate_pending_sha256=gate_pending.gate_pending_sha256,
            frozen_pipeline_pins_sha256=(
                gate_pending.frozen_pipeline_pins_sha256
            ),
            access_transaction_sha256=gate_pending.access_transaction_sha256,
            cleanup_state_sha256=cleanup_state.cleanup_state_sha256,
            canonical_report_sha256=canonical_report_sha256,
            locked_source_manifest_sha256=locked_source_manifest_sha256,
            aggregate_metrics_sha256=aggregate_metrics_sha256,
            aggregate_lineage_sha256=aggregate_lineage_sha256,
            result_status=result_status,
            cleanup_status=cleanup_state.status,
            verifier_code_sha256=(
                actual_source_seal.fresh_reload_worker_code_sha256
            ),
            verified_at_utc=verified_at_utc,
            restricted_work_directory_absent=(
                cleanup_state.restricted_work_directory_absent
            ),
            fresh_process=True,
            strict_reload=True,
            all_references_match=True,
            threshold_replay_match=True,
            cleanup_invariant_match=True,
            network_isolation=P8_ACTUAL_SOURCE_NETWORK_ISOLATION,
            promotion_authorized=False,
            aggregate_only=True,
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
            "gate_pending_sha256": self.gate_pending_sha256,
            "frozen_pipeline_pins_sha256": (
                self.frozen_pipeline_pins_sha256
            ),
            "access_transaction_sha256": self.access_transaction_sha256,
            "cleanup_state_sha256": self.cleanup_state_sha256,
            "canonical_report_sha256": self.canonical_report_sha256,
            "locked_source_manifest_sha256": (
                self.locked_source_manifest_sha256
            ),
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "aggregate_lineage_sha256": self.aggregate_lineage_sha256,
            "result_status": self.result_status.value,
            "cleanup_status": self.cleanup_status.value,
            "verifier_code_sha256": self.verifier_code_sha256,
            "verified_at_utc": self.verified_at_utc,
            "restricted_work_directory_absent": (
                self.restricted_work_directory_absent
            ),
            "fresh_process": self.fresh_process,
            "strict_reload": self.strict_reload,
            "all_references_match": self.all_references_match,
            "threshold_replay_match": self.threshold_replay_match,
            "cleanup_invariant_match": self.cleanup_invariant_match,
            "network_isolation": self.network_isolation,
            "restricted_error_detail_serialized": False,
            "raw_paths_serialized": False,
            "promotion_authorized": self.promotion_authorized,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8ActualSourceFreshProcessReloadReceipt":
        document = require_mapping(value, "p8_actual_source_fresh_reload")
        expected_keys = {
            "schema_version",
            "actual_source_seal_sha256",
            "gate_pending_sha256",
            "frozen_pipeline_pins_sha256",
            "access_transaction_sha256",
            "cleanup_state_sha256",
            "canonical_report_sha256",
            "locked_source_manifest_sha256",
            "aggregate_metrics_sha256",
            "aggregate_lineage_sha256",
            "result_status",
            "cleanup_status",
            "verifier_code_sha256",
            "verified_at_utc",
            "restricted_work_directory_absent",
            "fresh_process",
            "strict_reload",
            "all_references_match",
            "threshold_replay_match",
            "cleanup_invariant_match",
            "network_isolation",
            "restricted_error_detail_serialized",
            "raw_paths_serialized",
            "promotion_authorized",
            "aggregate_only",
            "receipt_sha256",
        }
        require_exact_keys(
            document, expected_keys, "p8_actual_source_fresh_reload"
        )
        for name in (
            "restricted_error_detail_serialized",
            "raw_paths_serialized",
        ):
            if require_boolean(document[name], name):
                raise VisualFeedbackContractError(
                    "P8 actual-source reload serialized restricted detail"
                )
        return cls(
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            gate_pending_sha256=require_sha256(
                document["gate_pending_sha256"], "gate_pending_sha256"
            ),
            frozen_pipeline_pins_sha256=require_sha256(
                document["frozen_pipeline_pins_sha256"],
                "frozen_pipeline_pins_sha256",
            ),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            cleanup_state_sha256=require_sha256(
                document["cleanup_state_sha256"], "cleanup_state_sha256"
            ),
            canonical_report_sha256=require_sha256(
                document["canonical_report_sha256"],
                "canonical_report_sha256",
            ),
            locked_source_manifest_sha256=_optional_sha256(
                document["locked_source_manifest_sha256"],
                "locked_source_manifest_sha256",
            ),
            aggregate_metrics_sha256=_optional_sha256(
                document["aggregate_metrics_sha256"],
                "aggregate_metrics_sha256",
            ),
            aggregate_lineage_sha256=_optional_sha256(
                document["aggregate_lineage_sha256"],
                "aggregate_lineage_sha256",
            ),
            result_status=_result_status(document["result_status"]),
            cleanup_status=_cleanup_status(document["cleanup_status"]),
            verifier_code_sha256=require_sha256(
                document["verifier_code_sha256"], "verifier_code_sha256"
            ),
            verified_at_utc=require_utc_timestamp(
                document["verified_at_utc"], "verified_at_utc"
            ),
            restricted_work_directory_absent=require_boolean(
                document["restricted_work_directory_absent"],
                "restricted_work_directory_absent",
            ),
            fresh_process=require_boolean(
                document["fresh_process"], "fresh_process"
            ),
            strict_reload=require_boolean(
                document["strict_reload"], "strict_reload"
            ),
            all_references_match=require_boolean(
                document["all_references_match"], "all_references_match"
            ),
            threshold_replay_match=require_boolean(
                document["threshold_replay_match"], "threshold_replay_match"
            ),
            cleanup_invariant_match=require_boolean(
                document["cleanup_invariant_match"],
                "cleanup_invariant_match",
            ),
            network_isolation=require_safe_identifier(
                document["network_isolation"], "network_isolation"
            ),
            promotion_authorized=require_boolean(
                document["promotion_authorized"], "promotion_authorized"
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8ActualSourceFinalResultCommit:
    """Last-write marker that makes one freshly reloaded report canonical."""

    actual_source_seal_sha256: str
    gate_pending_sha256: str
    access_transaction_sha256: str
    cleanup_state_sha256: str
    canonical_report_sha256: str
    locked_source_manifest_sha256: str | None
    aggregate_metrics_sha256: str | None
    aggregate_lineage_sha256: str | None
    source_terminal_sha256: str
    result_status: P8ActualSourceResultStatus
    cleanup_status: P8ActualSourceCleanupStatus
    restricted_work_directory_absent: bool
    fresh_process_reload_receipt_sha256: str
    committed_at_utc: str
    committed_after_fresh_reload: bool
    promotion_authorized: bool
    aggregate_only: bool
    commit_sha256: str
    schema_version: str = P8_ACTUAL_SOURCE_FINAL_RESULT_COMMIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != P8_ACTUAL_SOURCE_FINAL_RESULT_COMMIT_SCHEMA_VERSION
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source final commit schema differs"
            )
        for name in (
            "actual_source_seal_sha256",
            "gate_pending_sha256",
            "access_transaction_sha256",
            "cleanup_state_sha256",
            "canonical_report_sha256",
            "source_terminal_sha256",
            "fresh_process_reload_receipt_sha256",
            "commit_sha256",
        ):
            require_sha256(getattr(self, name), name)
        for name in (
            "locked_source_manifest_sha256",
            "aggregate_metrics_sha256",
            "aggregate_lineage_sha256",
        ):
            _optional_sha256(getattr(self, name), name)
        evidence_presence = (
            self.locked_source_manifest_sha256 is not None,
            self.aggregate_metrics_sha256 is not None,
            self.aggregate_lineage_sha256 is not None,
        )
        if len(set(evidence_presence)) != 1:
            raise VisualFeedbackContractError(
                "P8 final commit evidence pins differ"
            )
        if not isinstance(self.result_status, P8ActualSourceResultStatus):
            raise VisualFeedbackContractError(
                "P8 actual-source result status differs"
            )
        if not isinstance(self.cleanup_status, P8ActualSourceCleanupStatus):
            raise VisualFeedbackContractError(
                "P8 actual-source cleanup status differs"
            )
        require_boolean(
            self.restricted_work_directory_absent,
            "restricted_work_directory_absent",
        )
        if self.cleanup_status is P8ActualSourceCleanupStatus.COMPLETED:
            if self.restricted_work_directory_absent is not True:
                raise VisualFeedbackContractError(
                    "P8 final commit cleanup proof differs"
                )
        elif self.result_status is not P8ActualSourceResultStatus.FAILED_CLOSED:
            raise VisualFeedbackContractError(
                "P8 incomplete cleanup final commit differs"
            )
        if self.result_status is P8ActualSourceResultStatus.COMPLETED and (
            self.cleanup_status is not P8ActualSourceCleanupStatus.COMPLETED
            or not all(evidence_presence)
        ):
            raise VisualFeedbackContractError(
                "P8 completed final commit evidence is incomplete"
            )
        require_utc_timestamp(self.committed_at_utc, "committed_at_utc")
        if not require_boolean(
            self.committed_after_fresh_reload,
            "committed_after_fresh_reload",
        ):
            raise VisualFeedbackContractError(
                "P8 final result must follow fresh reload"
            )
        if require_boolean(
            self.promotion_authorized, "promotion_authorized"
        ):
            raise VisualFeedbackContractError(
                "P8 final result cannot authorize promotion"
            )
        if not require_boolean(self.aggregate_only, "aggregate_only"):
            raise VisualFeedbackContractError(
                "P8 final result must be aggregate-only"
            )
        if self.commit_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "P8 actual-source final commit digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        fresh_reload_receipt: P8ActualSourceFreshProcessReloadReceipt,
        source_terminal_sha256: str,
        committed_at_utc: str,
    ) -> "P8ActualSourceFinalResultCommit":
        unsigned = {
            "schema_version": (
                P8_ACTUAL_SOURCE_FINAL_RESULT_COMMIT_SCHEMA_VERSION
            ),
            "status": "canonical_final_report_committed",
            "actual_source_seal_sha256": (
                fresh_reload_receipt.actual_source_seal_sha256
            ),
            "gate_pending_sha256": fresh_reload_receipt.gate_pending_sha256,
            "access_transaction_sha256": (
                fresh_reload_receipt.access_transaction_sha256
            ),
            "cleanup_state_sha256": (
                fresh_reload_receipt.cleanup_state_sha256
            ),
            "canonical_report_sha256": (
                fresh_reload_receipt.canonical_report_sha256
            ),
            "locked_source_manifest_sha256": (
                fresh_reload_receipt.locked_source_manifest_sha256
            ),
            "aggregate_metrics_sha256": (
                fresh_reload_receipt.aggregate_metrics_sha256
            ),
            "aggregate_lineage_sha256": (
                fresh_reload_receipt.aggregate_lineage_sha256
            ),
            "source_terminal_sha256": source_terminal_sha256,
            "result_status": fresh_reload_receipt.result_status.value,
            "cleanup_status": fresh_reload_receipt.cleanup_status.value,
            "restricted_work_directory_absent": (
                fresh_reload_receipt.restricted_work_directory_absent
            ),
            "fresh_process_reload_receipt_sha256": (
                fresh_reload_receipt.receipt_sha256
            ),
            "committed_at_utc": committed_at_utc,
            "committed_after_fresh_reload": True,
            "raw_paths_serialized": False,
            "promotion_authorized": False,
            "aggregate_only": True,
        }
        return cls(
            actual_source_seal_sha256=(
                fresh_reload_receipt.actual_source_seal_sha256
            ),
            gate_pending_sha256=fresh_reload_receipt.gate_pending_sha256,
            access_transaction_sha256=(
                fresh_reload_receipt.access_transaction_sha256
            ),
            cleanup_state_sha256=fresh_reload_receipt.cleanup_state_sha256,
            canonical_report_sha256=(
                fresh_reload_receipt.canonical_report_sha256
            ),
            locked_source_manifest_sha256=(
                fresh_reload_receipt.locked_source_manifest_sha256
            ),
            aggregate_metrics_sha256=(
                fresh_reload_receipt.aggregate_metrics_sha256
            ),
            aggregate_lineage_sha256=(
                fresh_reload_receipt.aggregate_lineage_sha256
            ),
            source_terminal_sha256=source_terminal_sha256,
            result_status=fresh_reload_receipt.result_status,
            cleanup_status=fresh_reload_receipt.cleanup_status,
            restricted_work_directory_absent=(
                fresh_reload_receipt.restricted_work_directory_absent
            ),
            fresh_process_reload_receipt_sha256=(
                fresh_reload_receipt.receipt_sha256
            ),
            committed_at_utc=committed_at_utc,
            committed_after_fresh_reload=True,
            promotion_authorized=False,
            aggregate_only=True,
            commit_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": "canonical_final_report_committed",
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
            "gate_pending_sha256": self.gate_pending_sha256,
            "access_transaction_sha256": self.access_transaction_sha256,
            "cleanup_state_sha256": self.cleanup_state_sha256,
            "canonical_report_sha256": self.canonical_report_sha256,
            "locked_source_manifest_sha256": (
                self.locked_source_manifest_sha256
            ),
            "aggregate_metrics_sha256": self.aggregate_metrics_sha256,
            "aggregate_lineage_sha256": self.aggregate_lineage_sha256,
            "source_terminal_sha256": self.source_terminal_sha256,
            "result_status": self.result_status.value,
            "cleanup_status": self.cleanup_status.value,
            "restricted_work_directory_absent": (
                self.restricted_work_directory_absent
            ),
            "fresh_process_reload_receipt_sha256": (
                self.fresh_process_reload_receipt_sha256
            ),
            "committed_at_utc": self.committed_at_utc,
            "committed_after_fresh_reload": self.committed_after_fresh_reload,
            "raw_paths_serialized": False,
            "promotion_authorized": self.promotion_authorized,
            "aggregate_only": self.aggregate_only,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "commit_sha256": self.commit_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8ActualSourceFinalResultCommit":
        document = require_mapping(value, "p8_actual_source_final_commit")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "actual_source_seal_sha256",
                "gate_pending_sha256",
                "access_transaction_sha256",
                "cleanup_state_sha256",
                "canonical_report_sha256",
                "locked_source_manifest_sha256",
                "aggregate_metrics_sha256",
                "aggregate_lineage_sha256",
                "source_terminal_sha256",
                "result_status",
                "cleanup_status",
                "restricted_work_directory_absent",
                "fresh_process_reload_receipt_sha256",
                "committed_at_utc",
                "committed_after_fresh_reload",
                "raw_paths_serialized",
                "promotion_authorized",
                "aggregate_only",
                "commit_sha256",
            },
            "p8_actual_source_final_commit",
        )
        if document["status"] != "canonical_final_report_committed":
            raise VisualFeedbackContractError(
                "P8 actual-source final commit status differs"
            )
        if require_boolean(
            document["raw_paths_serialized"], "raw_paths_serialized"
        ):
            raise VisualFeedbackContractError(
                "P8 actual-source final commit serialized paths"
            )
        return cls(
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            gate_pending_sha256=require_sha256(
                document["gate_pending_sha256"], "gate_pending_sha256"
            ),
            access_transaction_sha256=require_sha256(
                document["access_transaction_sha256"],
                "access_transaction_sha256",
            ),
            cleanup_state_sha256=require_sha256(
                document["cleanup_state_sha256"], "cleanup_state_sha256"
            ),
            canonical_report_sha256=require_sha256(
                document["canonical_report_sha256"],
                "canonical_report_sha256",
            ),
            locked_source_manifest_sha256=_optional_sha256(
                document["locked_source_manifest_sha256"],
                "locked_source_manifest_sha256",
            ),
            aggregate_metrics_sha256=_optional_sha256(
                document["aggregate_metrics_sha256"],
                "aggregate_metrics_sha256",
            ),
            aggregate_lineage_sha256=_optional_sha256(
                document["aggregate_lineage_sha256"],
                "aggregate_lineage_sha256",
            ),
            source_terminal_sha256=require_sha256(
                document["source_terminal_sha256"],
                "source_terminal_sha256",
            ),
            result_status=_result_status(document["result_status"]),
            cleanup_status=_cleanup_status(document["cleanup_status"]),
            restricted_work_directory_absent=require_boolean(
                document["restricted_work_directory_absent"],
                "restricted_work_directory_absent",
            ),
            fresh_process_reload_receipt_sha256=require_sha256(
                document["fresh_process_reload_receipt_sha256"],
                "fresh_process_reload_receipt_sha256",
            ),
            committed_at_utc=require_utc_timestamp(
                document["committed_at_utc"], "committed_at_utc"
            ),
            committed_after_fresh_reload=require_boolean(
                document["committed_after_fresh_reload"],
                "committed_after_fresh_reload",
            ),
            promotion_authorized=require_boolean(
                document["promotion_authorized"], "promotion_authorized"
            ),
            aggregate_only=require_boolean(
                document["aggregate_only"], "aggregate_only"
            ),
            commit_sha256=require_sha256(
                document["commit_sha256"], "commit_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


__all__ = [
    "P8_ACTUAL_SOURCE_CLEANUP_STATE_SCHEMA_VERSION",
    "P8_ACTUAL_SOURCE_FINAL_RESULT_COMMIT_SCHEMA_VERSION",
    "P8_ACTUAL_SOURCE_FRESH_PROCESS_RELOAD_SCHEMA_VERSION",
    "P8_ACTUAL_SOURCE_GATE_PENDING_SCHEMA_VERSION",
    "P8_ACTUAL_SOURCE_NETWORK_ISOLATION",
    "P8_ACTUAL_SOURCE_READ_ZERO_SEAL_SCHEMA_VERSION",
    "P8_CANONICAL_RESTRICTED_IDENTITY_COMMITMENT_SCHEMA_VERSION",
    "P8ActualSourceCleanupState",
    "P8ActualSourceCleanupStatus",
    "P8ActualSourceFinalResultCommit",
    "P8ActualSourceFreshProcessReloadReceipt",
    "P8ActualSourceGatePending",
    "P8ActualSourceReadZeroSeal",
    "P8ActualSourceResultStatus",
    "P8CanonicalRestrictedIdentityCommitment",
    "P8ReferenceIdentityRole",
]
