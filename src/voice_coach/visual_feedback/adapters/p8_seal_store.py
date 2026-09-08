"""Durable O_EXCL-backed state for the one-shot P8 locked evaluation."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any

from ..errors import VisualFeedbackContractError
from ..models.integrity import canonical_json_bytes, require_sha256
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
    issue_p8_state_bound_read_zero_document,
    load_p8_state_bound_read_zero_document,
)
from ..models.p8_locked_cohort import P8LockedCohortDiscoverySeal
from ..models.p8_locked_label_download import (
    P8LockedLabelDownloadTransaction,
    P8LockedLabelStateRootSeal,
    issue_p8_locked_label_state_root_seal,
    p8_locked_reference_commitments_root_sha256,
)
from ..models.p8_locked_source_manifest import P8LockedSourceManifest

_SEAL_FILENAME = "p8_read_zero_seal.json"
_ACCESS_FILENAME = "p8_access_transaction.json"
_SOURCE_MANIFEST_FILENAME = "p8_locked_source_manifest.json"
_METRICS_FILENAME = "p8_aggregate_evaluation_metrics.json"
_REPORT_FILENAME = "p8_locked_evaluation_report.json"
_ACTUAL_SOURCE_SEAL_FILENAME = "p8_actual_source_read_zero_seal.json"
_ACTUAL_SOURCE_GATE_PENDING_FILENAME = "p8_actual_source_gate_pending.json"
_ACTUAL_SOURCE_CLEANUP_FILENAME = "p8_actual_source_cleanup_state.json"
_ACTUAL_SOURCE_RELOAD_FILENAME = "p8_actual_source_fresh_reload.json"
_ACTUAL_SOURCE_COMMIT_FILENAME = "p8_actual_source_final_commit.json"
_LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME = (
    "p8_locked_label_download_transaction.json"
)
_LOCKED_COHORT_DISCOVERY_SEAL_FILENAME = (
    "p8_locked_cohort_discovery_seal.json"
)
_LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME = (
    "p8_locked_label_state_root_seal.json"
)
_MAX_STATE_BYTES = 512 * 1024


class P8SealStateError(VisualFeedbackContractError):
    """The durable one-shot state is absent, malformed, or already consumed."""


class P8SealStateStore:
    """Persist immutable P8 state without replacing any prior generation.

    Creation of the access transaction is the authorization-consumption point.
    A short write, fsync failure, or process crash may leave an unreadable file,
    but that file is deliberately retained and blocks every later access attempt.
    """

    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory

    def _state_directory_for_isolated_reload(self) -> Path:
        """Internal locator; it must never enter a receipt or exception."""

        return self._state_directory

    def create_read_zero_seal(self, seal: P8ReadZeroSeal) -> None:
        """Create and fsync the initial access_count=0 seal exactly once."""

        if not isinstance(seal, P8ReadZeroSeal):
            raise P8SealStateError("p8_read_zero_seal_invalid")
        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory()
        )
        try:
            metadata = os.fstat(directory_descriptor)
            self._create_exclusive_json(
                directory_descriptor,
                _SEAL_FILENAME,
                issue_p8_state_bound_read_zero_document(
                    seal=seal,
                    state_directory_device=metadata.st_dev,
                    state_directory_inode=metadata.st_ino,
                ),
                collision_reason="p8_read_zero_seal_already_exists",
            )
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def load_read_zero_seal(self) -> P8ReadZeroSeal:
        """Load and validate the immutable seal without accessing source media."""

        directory_descriptor = self._open_state_directory()
        try:
            return self._load_seal_at(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def create_actual_source_read_zero_seal(
        self, seal: P8ActualSourceReadZeroSeal
    ) -> None:
        """Persist the companion seal before authorization consumption."""

        if not isinstance(seal, P8ActualSourceReadZeroSeal):
            raise P8SealStateError("p8_actual_source_seal_invalid")
        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory()
        )
        try:
            base = self._load_seal_at(directory_descriptor)
            if seal.base_read_zero_seal_sha256 != base.seal_sha256:
                raise P8SealStateError("p8_actual_source_base_seal_pin_mismatch")
            if (
                seal.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
            ):
                raise P8SealStateError(
                    "p8_actual_source_p6_evidence_pin_mismatch"
                )
            if self._entry_exists_at(directory_descriptor, _ACCESS_FILENAME):
                raise P8SealStateError(
                    "p8_actual_source_seal_after_consumption_forbidden"
                )
            self._create_exclusive_json(
                directory_descriptor,
                _ACTUAL_SOURCE_SEAL_FILENAME,
                seal.to_json_object(),
                collision_reason="p8_actual_source_seal_already_exists",
            )
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def load_actual_source_read_zero_seal(
        self,
    ) -> P8ActualSourceReadZeroSeal:
        directory_descriptor = self._open_state_directory()
        try:
            seal = self._load_actual_source_seal_at(directory_descriptor)
            base = self._load_seal_at(directory_descriptor)
            if seal.base_read_zero_seal_sha256 != base.seal_sha256:
                raise P8SealStateError(
                    "p8_actual_source_base_seal_pin_mismatch"
                )
            if (
                seal.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
            ):
                raise P8SealStateError(
                    "p8_actual_source_p6_evidence_pin_mismatch"
                )
            return seal
        finally:
            os.close(directory_descriptor)

    def create_locked_label_download_transaction(
        self,
        transaction: P8LockedLabelDownloadTransaction,
        *,
        discovery_seal: P8LockedCohortDiscoverySeal,
        state_directory_descriptor: int | None = None,
    ) -> None:
        """Consume the all-label authority before the first network request."""

        if not isinstance(transaction, P8LockedLabelDownloadTransaction) or not isinstance(
            discovery_seal, P8LockedCohortDiscoverySeal
        ):
            raise P8SealStateError(
                "p8_locked_label_download_transaction_invalid"
            )
        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory(
                state_directory_descriptor
            )
        )
        try:
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            durable_discovery = self._load_locked_cohort_discovery_seal_at(
                directory_descriptor
            )
            state_root = self._load_locked_label_state_root_seal_at(
                directory_descriptor
            )
            state_metadata = os.fstat(directory_descriptor)
            if (
                actual.base_read_zero_seal_sha256 != base.seal_sha256
                or actual.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
                or transaction.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or transaction.discovery_seal_sha256
                != discovery_seal.seal_sha256
                or durable_discovery != discovery_seal
                or transaction.reference_identity_set_commitments_sha256
                != p8_locked_reference_commitments_root_sha256(actual)
                or discovery_seal.reference_identity_set_commitments_sha256
                != transaction.reference_identity_set_commitments_sha256
                or discovery_seal.state_directory_device
                != state_metadata.st_dev
                or discovery_seal.state_directory_inode
                != state_metadata.st_ino
                or state_root.base_read_zero_seal_sha256 != base.seal_sha256
                or state_root.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or state_root.discovery_seal_sha256
                != durable_discovery.seal_sha256
                or state_root.state_directory_device
                != state_metadata.st_dev
                or state_root.state_directory_inode
                != state_metadata.st_ino
                or transaction.state_root_seal_sha256
                != state_root.state_root_seal_sha256
                or transaction.state_directory_device
                != state_metadata.st_dev
                or transaction.state_directory_inode
                != state_metadata.st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_label_download_transaction_lineage_mismatch"
                )
            if self._entry_exists_at(directory_descriptor, _ACCESS_FILENAME):
                raise P8SealStateError(
                    "p8_locked_label_download_after_source_consumption_forbidden"
                )
            self._create_exclusive_json(
                directory_descriptor,
                _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                transaction.to_json_object(),
                collision_reason=(
                    "p8_locked_label_download_authorization_already_consumed"
                ),
            )
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def create_locked_cohort_discovery_seal(
        self, seal: P8LockedCohortDiscoverySeal
    ) -> None:
        """Persist the all-32 read-zero discovery seal before label access."""

        if not isinstance(seal, P8LockedCohortDiscoverySeal):
            raise P8SealStateError(
                "p8_locked_cohort_discovery_seal_invalid"
            )
        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory()
        )
        try:
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            if (
                actual.base_read_zero_seal_sha256 != base.seal_sha256
                or actual.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
                or seal.reference_identity_set_commitments_sha256
                != p8_locked_reference_commitments_root_sha256(actual)
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_seal_lineage_mismatch"
                )
            if self._entry_exists_at(directory_descriptor, _ACCESS_FILENAME):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_after_source_consumption_forbidden"
                )
            if self._entry_exists_at(
                directory_descriptor,
                _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_after_label_consumption_forbidden"
                )
            state_metadata = os.fstat(directory_descriptor)
            if (
                seal.state_directory_device != state_metadata.st_dev
                or seal.state_directory_inode != state_metadata.st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_state_root_mismatch"
                )
            state_root_seal = issue_p8_locked_label_state_root_seal(
                discovery_seal=seal,
                actual_source_seal=actual,
                state_directory_device=state_metadata.st_dev,
                state_directory_inode=state_metadata.st_ino,
            )
            self._create_exclusive_json(
                directory_descriptor,
                _LOCKED_COHORT_DISCOVERY_SEAL_FILENAME,
                seal.to_json_object(),
                collision_reason=(
                    "p8_locked_cohort_discovery_seal_already_exists"
                ),
            )
            self._create_exclusive_json(
                directory_descriptor,
                _LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME,
                state_root_seal.to_json_object(),
                collision_reason=(
                    "p8_locked_label_state_root_seal_already_exists"
                ),
            )
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def recover_incomplete_locked_cohort_discovery_state(
        self,
        *,
        expected_seal_id: str,
        expected_protocol_revision: str,
    ) -> P8LockedCohortDiscoverySeal | None:
        """Finish only a discovery-durable/root-absent write on the same inode."""

        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory()
        )
        try:
            discovery_exists = self._entry_exists_at(
                directory_descriptor,
                _LOCKED_COHORT_DISCOVERY_SEAL_FILENAME,
            )
            state_root_exists = self._entry_exists_at(
                directory_descriptor,
                _LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME,
            )
            if not discovery_exists:
                if state_root_exists:
                    raise P8SealStateError(
                        "p8_locked_label_state_root_orphaned"
                    )
                return None
            if state_root_exists:
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_state_complete"
                )
            if self._entry_exists_at(directory_descriptor, _ACCESS_FILENAME) or (
                self._entry_exists_at(
                    directory_descriptor,
                    _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                )
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_recovery_after_consumption_forbidden"
                )
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            discovery = self._load_locked_cohort_discovery_seal_at(
                directory_descriptor
            )
            metadata = os.fstat(directory_descriptor)
            if (
                discovery.seal_id != expected_seal_id
                or discovery.protocol_revision != expected_protocol_revision
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_recovery_identity_mismatch"
                )
            if (
                actual.base_read_zero_seal_sha256 != base.seal_sha256
                or actual.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
                or discovery.reference_identity_set_commitments_sha256
                != p8_locked_reference_commitments_root_sha256(actual)
                or discovery.state_directory_device != metadata.st_dev
                or discovery.state_directory_inode != metadata.st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_recovery_lineage_mismatch"
                )
            state_root = issue_p8_locked_label_state_root_seal(
                discovery_seal=discovery,
                actual_source_seal=actual,
                state_directory_device=metadata.st_dev,
                state_directory_inode=metadata.st_ino,
            )
            self._create_exclusive_json(
                directory_descriptor,
                _LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME,
                state_root.to_json_object(),
                collision_reason=(
                    "p8_locked_label_state_root_seal_already_exists"
                ),
            )
            return discovery
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def load_locked_cohort_discovery_seal(
        self,
    ) -> P8LockedCohortDiscoverySeal:
        directory_descriptor = self._open_state_directory()
        try:
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            seal = self._load_locked_cohort_discovery_seal_at(
                directory_descriptor
            )
            if (
                actual.base_read_zero_seal_sha256 != base.seal_sha256
                or actual.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
                or seal.reference_identity_set_commitments_sha256
                != p8_locked_reference_commitments_root_sha256(actual)
                or seal.state_directory_device != os.fstat(directory_descriptor).st_dev
                or seal.state_directory_inode != os.fstat(directory_descriptor).st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_cohort_discovery_seal_lineage_mismatch"
                )
            return seal
        finally:
            os.close(directory_descriptor)

    def load_locked_label_state_root_seal(
        self,
    ) -> P8LockedLabelStateRootSeal:
        """Load the pre-authorization canonical state-directory binding."""

        directory_descriptor = self._open_state_directory()
        try:
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            discovery = self._load_locked_cohort_discovery_seal_at(
                directory_descriptor
            )
            state_root = self._load_locked_label_state_root_seal_at(
                directory_descriptor
            )
            metadata = os.fstat(directory_descriptor)
            if (
                state_root.base_read_zero_seal_sha256 != base.seal_sha256
                or state_root.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or state_root.discovery_seal_sha256 != discovery.seal_sha256
                or discovery.state_directory_device != metadata.st_dev
                or discovery.state_directory_inode != metadata.st_ino
                or state_root.state_directory_device != metadata.st_dev
                or state_root.state_directory_inode != metadata.st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_label_state_root_identity_mismatch"
                )
            return state_root
        finally:
            os.close(directory_descriptor)

    def load_locked_label_download_transaction(
        self,
    ) -> P8LockedLabelDownloadTransaction:
        """Reload the consumed request and verify its production lineage."""

        directory_descriptor = self._open_state_directory()
        try:
            transaction = self._load_locked_label_download_transaction_at(
                directory_descriptor
            )
            discovery = self._load_locked_cohort_discovery_seal_at(
                directory_descriptor
            )
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            state_root = self._load_locked_label_state_root_seal_at(
                directory_descriptor
            )
            state_metadata = os.fstat(directory_descriptor)
            if (
                actual.base_read_zero_seal_sha256 != base.seal_sha256
                or actual.p6_evidence_receipt_sha256
                != base.p6_evidence_receipt_sha256
                or transaction.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or transaction.reference_identity_set_commitments_sha256
                != p8_locked_reference_commitments_root_sha256(actual)
                or transaction.discovery_seal_sha256
                != discovery.seal_sha256
                or transaction.reference_identity_set_commitments_sha256
                != discovery.reference_identity_set_commitments_sha256
                or discovery.state_directory_device != state_metadata.st_dev
                or discovery.state_directory_inode != state_metadata.st_ino
                or state_root.base_read_zero_seal_sha256 != base.seal_sha256
                or state_root.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or state_root.discovery_seal_sha256 != discovery.seal_sha256
                or state_root.state_directory_device
                != state_metadata.st_dev
                or state_root.state_directory_inode
                != state_metadata.st_ino
                or transaction.state_root_seal_sha256
                != state_root.state_root_seal_sha256
                or transaction.state_directory_device
                != state_metadata.st_dev
                or transaction.state_directory_inode
                != state_metadata.st_ino
            ):
                raise P8SealStateError(
                    "p8_locked_label_download_transaction_lineage_mismatch"
                )
            return transaction
        finally:
            os.close(directory_descriptor)

    def open_locked_label_download_transaction_descriptor(self) -> int:
        """Return the exact durable 0400 transaction for child inheritance."""

        directory_descriptor = self._open_state_directory()
        descriptor: int | None = None
        try:
            expected = self._load_locked_label_download_transaction_at(
                directory_descriptor
            )
            flags = (
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(
                _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                flags,
                dir_fd=directory_descriptor,
            )
            metadata = os.fstat(descriptor)
            named_metadata = os.stat(
                _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_uid != os.geteuid()
                or (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
                != (
                    named_metadata.st_dev,
                    named_metadata.st_ino,
                    named_metadata.st_size,
                    named_metadata.st_mtime_ns,
                    named_metadata.st_ctime_ns,
                )
            ):
                raise P8SealStateError(
                    "p8_locked_label_download_transaction_invalid"
                )
            observed = P8LockedLabelDownloadTransaction.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                )
            )
            if observed != expected:
                raise P8SealStateError(
                    "p8_locked_label_download_transaction_changed"
                )
            after = os.fstat(descriptor)
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ):
                raise P8SealStateError(
                    "p8_locked_label_download_transaction_changed"
                )
            result = descriptor
            descriptor = None
            return result
        except (OSError, VisualFeedbackContractError) as error:
            if isinstance(error, P8SealStateError):
                raise
            raise P8SealStateError(
                "p8_locked_label_download_transaction_invalid"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_descriptor)

    def create_actual_source_gate_pending(
        self, pending: P8ActualSourceGatePending
    ) -> None:
        """Durably arm cleanup before the first completed-directory open."""

        if not isinstance(pending, P8ActualSourceGatePending):
            raise P8SealStateError("p8_actual_source_gate_pending_invalid")
        directory_descriptor = self._open_state_directory()
        try:
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
            if (
                pending.base_read_zero_seal_sha256 != base.seal_sha256
                or pending.actual_source_seal_sha256
                != actual.actual_source_seal_sha256
                or pending.access_transaction_sha256
                != transaction.transaction_sha256
            ):
                raise P8SealStateError(
                    "p8_actual_source_gate_pending_lineage_mismatch"
                )
            self._create_exclusive_json(
                directory_descriptor,
                _ACTUAL_SOURCE_GATE_PENDING_FILENAME,
                pending.to_json_object(),
                collision_reason="p8_actual_source_gate_pending_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_actual_source_gate_pending(self) -> P8ActualSourceGatePending:
        directory_descriptor = self._open_state_directory()
        try:
            return self._load_actual_source_gate_pending_at(
                directory_descriptor
            )
        finally:
            os.close(directory_descriptor)

    def create_actual_source_cleanup_state(
        self, cleanup: P8ActualSourceCleanupState
    ) -> None:
        """Write the one immutable cleanup terminal, including incomplete."""

        if not isinstance(cleanup, P8ActualSourceCleanupState):
            raise P8SealStateError("p8_actual_source_cleanup_state_invalid")
        directory_descriptor = self._open_state_directory()
        try:
            pending = self._load_actual_source_gate_pending_at(
                directory_descriptor
            )
            if (
                cleanup.gate_pending_sha256 != pending.gate_pending_sha256
                or cleanup.access_transaction_sha256
                != pending.access_transaction_sha256
            ):
                raise P8SealStateError(
                    "p8_actual_source_cleanup_lineage_mismatch"
                )
            self._create_exclusive_json(
                directory_descriptor,
                _ACTUAL_SOURCE_CLEANUP_FILENAME,
                cleanup.to_json_object(),
                collision_reason="p8_actual_source_cleanup_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_actual_source_cleanup_state(
        self,
    ) -> P8ActualSourceCleanupState:
        directory_descriptor = self._open_state_directory()
        try:
            return self._load_actual_source_cleanup_at(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def create_actual_source_fresh_reload_receipt(
        self, receipt: P8ActualSourceFreshProcessReloadReceipt
    ) -> None:
        if not isinstance(receipt, P8ActualSourceFreshProcessReloadReceipt):
            raise P8SealStateError("p8_actual_source_reload_receipt_invalid")
        directory_descriptor = self._open_state_directory()
        try:
            self._validate_actual_source_reload_at(
                directory_descriptor, receipt
            )
            self._create_exclusive_json(
                directory_descriptor,
                _ACTUAL_SOURCE_RELOAD_FILENAME,
                receipt.to_json_object(),
                collision_reason="p8_actual_source_reload_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_actual_source_fresh_reload_receipt(
        self,
    ) -> P8ActualSourceFreshProcessReloadReceipt:
        directory_descriptor = self._open_state_directory()
        try:
            receipt = self._load_actual_source_reload_at(directory_descriptor)
            self._validate_actual_source_reload_at(
                directory_descriptor, receipt
            )
            return receipt
        finally:
            os.close(directory_descriptor)

    def create_actual_source_final_commit(
        self, commit: P8ActualSourceFinalResultCommit
    ) -> None:
        """Last-write marker; only this makes an actual-source result canonical."""

        if not isinstance(commit, P8ActualSourceFinalResultCommit):
            raise P8SealStateError("p8_actual_source_final_commit_invalid")
        directory_descriptor = self._open_state_directory()
        try:
            self._validate_actual_source_commit_at(
                directory_descriptor, commit
            )
            self._create_exclusive_json(
                directory_descriptor,
                _ACTUAL_SOURCE_COMMIT_FILENAME,
                commit.to_json_object(),
                collision_reason="p8_actual_source_final_commit_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_actual_source_final_commit(
        self,
    ) -> P8ActualSourceFinalResultCommit:
        directory_descriptor = self._open_state_directory()
        try:
            commit = self._load_actual_source_commit_at(directory_descriptor)
            self._validate_actual_source_commit_at(
                directory_descriptor, commit
            )
            return commit
        finally:
            os.close(directory_descriptor)

    def consume_access_authorization(
        self,
        *,
        expected_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
    ) -> P8AccessTransaction:
        """Durably consume the one-shot authorization before any source open."""

        require_sha256(expected_seal_sha256, "expected_seal_sha256")
        directory_descriptor, owns_directory_descriptor = (
            self._acquire_state_transition_directory()
        )
        try:
            if any(
                self._entry_exists_at(directory_descriptor, filename)
                for filename in (
                    _LOCKED_COHORT_DISCOVERY_SEAL_FILENAME,
                    _LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME,
                    _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                )
            ):
                raise P8SealStateError(
                    "p8_selected_cohort_source_authorization_required"
                )
            seal = self._load_seal_at(directory_descriptor)
            if seal.seal_sha256 != expected_seal_sha256:
                raise P8SealStateError("p8_read_zero_seal_pin_mismatch")
            transaction = P8AccessTransaction.issue(
                transaction_id=transaction_id,
                seal=seal,
                consumed_at_utc=consumed_at_utc,
            )
            self._create_exclusive_json(
                directory_descriptor,
                _ACCESS_FILENAME,
                transaction.to_json_object(),
                collision_reason="p8_access_authorization_already_consumed",
            )
            return transaction
        finally:
            if owns_directory_descriptor:
                os.close(directory_descriptor)

    def load_access_transaction(self) -> P8AccessTransaction:
        """Load the consumed authorization and verify that it pins the seal."""

        directory_descriptor = self._open_state_directory()
        try:
            return self._load_access_at(directory_descriptor)
        finally:
            os.close(directory_descriptor)

    def publish_locked_result(
        self,
        report: P8LockedEvaluationReport,
        *,
        source_manifest: P8LockedSourceManifest | None,
        metrics: P8AggregateEvaluationMetrics | None,
    ) -> None:
        """Publish manifest, metrics, then report without replacing evidence."""

        directory_descriptor = self._open_state_directory()
        try:
            seal = self._load_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
            if self._entry_exists_at(
                directory_descriptor, _ACTUAL_SOURCE_SEAL_FILENAME
            ):
                pending = self._load_actual_source_gate_pending_at(
                    directory_descriptor
                )
                cleanup = self._load_actual_source_cleanup_at(
                    directory_descriptor
                )
                if (
                    pending.access_transaction_sha256
                    != transaction.transaction_sha256
                    or cleanup.gate_pending_sha256
                    != pending.gate_pending_sha256
                ):
                    raise P8SealStateError(
                        "p8_actual_source_result_state_lineage_mismatch"
                    )
                if cleanup.status is P8ActualSourceCleanupStatus.INCOMPLETE and (
                    source_manifest is not None
                    or metrics is not None
                    or report.status is not P8LockedEvaluationStatus.FAILED_CLOSED
                    or report.reason_codes
                    != ("p8_locked_source_evaluation_failed",)
                ):
                    raise P8SealStateError(
                        "p8_actual_source_cleanup_incomplete_result_invalid"
                    )
            self._validate_report_references(report, seal, transaction)
            self._validate_report_metrics(
                report,
                seal,
                transaction,
                source_manifest,
                metrics,
            )
            if source_manifest is not None:
                self._create_exclusive_json(
                    directory_descriptor,
                    _SOURCE_MANIFEST_FILENAME,
                    source_manifest.to_json_object(),
                    collision_reason="p8_locked_source_manifest_already_exists",
                )
            elif self._entry_exists_at(
                directory_descriptor, _SOURCE_MANIFEST_FILENAME
            ):
                raise P8SealStateError("p8_unexpected_locked_source_manifest")
            if metrics is not None:
                self._create_exclusive_json(
                    directory_descriptor,
                    _METRICS_FILENAME,
                    metrics.to_json_object(),
                    collision_reason="p8_aggregate_metrics_already_exists",
                )
            elif self._entry_exists_at(directory_descriptor, _METRICS_FILENAME):
                raise P8SealStateError("p8_unexpected_aggregate_metrics")
            self._create_exclusive_json(
                directory_descriptor,
                _REPORT_FILENAME,
                report.to_json_object(),
                collision_reason="p8_locked_report_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_locked_source_manifest(self) -> P8LockedSourceManifest:
        """Freshly parse and bind the actual post-access archive manifest."""

        directory_descriptor = self._open_state_directory()
        try:
            if self._entry_exists_at(
                directory_descriptor, _ACTUAL_SOURCE_SEAL_FILENAME
            ):
                self._require_actual_source_commit_at(directory_descriptor)
            seal = self._load_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
            manifest = self._load_source_manifest_at(directory_descriptor)
            self._validate_source_manifest(manifest, seal, transaction)
            return manifest
        finally:
            os.close(directory_descriptor)

    def load_aggregate_metrics(self) -> P8AggregateEvaluationMetrics:
        """Freshly parse the immutable aggregate metric evidence."""

        directory_descriptor = self._open_state_directory()
        try:
            if self._entry_exists_at(
                directory_descriptor, _ACTUAL_SOURCE_SEAL_FILENAME
            ):
                self._require_actual_source_commit_at(directory_descriptor)
            seal = self._load_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
            source_manifest = self._load_source_manifest_at(
                directory_descriptor
            )
            metrics = self._load_metrics_at(directory_descriptor)
            self._validate_source_manifest(
                source_manifest, seal, transaction
            )
            if (
                metrics.evaluation_thresholds_sha256
                != seal.evaluation_thresholds_sha256
            ):
                raise P8SealStateError("p8_metric_threshold_pin_mismatch")
            if (
                metrics.locked_source_manifest_sha256
                != source_manifest.manifest_sha256
            ):
                raise P8SealStateError("p8_metric_source_manifest_pin_mismatch")
            P8SealStateStore._validate_manifest_metric_completeness(
                source_manifest,
                metrics,
            )
            return metrics
        finally:
            os.close(directory_descriptor)

    def load_locked_report(self) -> P8LockedEvaluationReport:
        """Load the terminal report and verify every durable reference."""

        report, _, _ = self.load_locked_evaluation_result()
        return report

    def load_locked_evaluation_result(
        self,
    ) -> tuple[
        P8LockedEvaluationReport,
        P8LockedSourceManifest | None,
        P8AggregateEvaluationMetrics | None,
    ]:
        """Freshly reload and cross-check report, actual manifest, and metrics."""

        directory_descriptor = self._open_state_directory()
        try:
            result = self._load_locked_evaluation_result_at(
                directory_descriptor
            )
            if self._entry_exists_at(
                directory_descriptor, _ACTUAL_SOURCE_SEAL_FILENAME
            ):
                commit = self._require_actual_source_commit_at(
                    directory_descriptor
                )
                report, source_manifest, metrics = result
                if (
                    commit.canonical_report_sha256 != report.report_sha256
                    or commit.locked_source_manifest_sha256
                    != (
                        source_manifest.manifest_sha256
                        if source_manifest is not None
                        else None
                    )
                    or commit.aggregate_metrics_sha256
                    != (
                        metrics.metrics_sha256 if metrics is not None else None
                    )
                ):
                    raise P8SealStateError(
                        "p8_actual_source_commit_result_pin_mismatch"
                    )
            return result
        finally:
            os.close(directory_descriptor)

    def load_staged_actual_source_result_for_reload(
        self,
    ) -> tuple[
        P8LockedEvaluationReport,
        P8LockedSourceManifest | None,
        P8AggregateEvaluationMetrics | None,
    ]:
        """Reload staged v3 documents before the final marker exists.

        This method is intentionally limited to the fresh-process verifier.  It
        requires the actual-source companion, durable pending gate, and cleanup
        terminal, but deliberately does not make the result canonical.
        """

        directory_descriptor = self._open_state_directory()
        try:
            self._load_actual_source_seal_at(directory_descriptor)
            self._load_actual_source_gate_pending_at(directory_descriptor)
            self._load_actual_source_cleanup_at(directory_descriptor)
            return self._load_locked_evaluation_result_at(
                directory_descriptor
            )
        finally:
            os.close(directory_descriptor)

    def _load_locked_evaluation_result_at(
        self, directory_descriptor: int
    ) -> tuple[
        P8LockedEvaluationReport,
        P8LockedSourceManifest | None,
        P8AggregateEvaluationMetrics | None,
    ]:
        seal = self._load_seal_at(directory_descriptor)
        transaction = self._load_access_at(directory_descriptor)
        try:
            report = P8LockedEvaluationReport.from_json_object(
                self._read_json_at(directory_descriptor, _REPORT_FILENAME)
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_locked_report_invalid") from error
        self._validate_report_references(report, seal, transaction)
        source_manifest = (
            self._load_source_manifest_at(directory_descriptor)
            if report.locked_source_manifest_sha256 is not None
            else None
        )
        metrics = (
            self._load_metrics_at(directory_descriptor)
            if report.aggregate_metrics_sha256 is not None
            else None
        )
        if metrics is None and self._entry_exists_at(
            directory_descriptor, _METRICS_FILENAME
        ):
            raise P8SealStateError("p8_unexpected_aggregate_metrics")
        if source_manifest is None and self._entry_exists_at(
            directory_descriptor, _SOURCE_MANIFEST_FILENAME
        ):
            raise P8SealStateError("p8_unexpected_locked_source_manifest")
        self._validate_report_metrics(
            report,
            seal,
            transaction,
            source_manifest,
            metrics,
        )
        return report, source_manifest, metrics

    @staticmethod
    def _validate_report_references(
        report: P8LockedEvaluationReport,
        seal: P8ReadZeroSeal,
        transaction: P8AccessTransaction,
    ) -> None:
        if report.seal_sha256 != seal.seal_sha256:
            raise P8SealStateError("p8_report_seal_pin_mismatch")
        if report.protocol_revision != seal.protocol_revision:
            raise P8SealStateError("p8_report_protocol_pin_mismatch")
        if report.access_transaction_sha256 != transaction.transaction_sha256:
            raise P8SealStateError("p8_report_transaction_pin_mismatch")

    @staticmethod
    def _validate_report_metrics(
        report: P8LockedEvaluationReport,
        seal: P8ReadZeroSeal,
        transaction: P8AccessTransaction,
        source_manifest: P8LockedSourceManifest | None,
        metrics: P8AggregateEvaluationMetrics | None,
    ) -> None:
        if source_manifest is None:
            if report.locked_source_manifest_sha256 is not None:
                raise P8SealStateError("p8_report_source_manifest_missing")
        else:
            P8SealStateStore._validate_source_manifest(
                source_manifest, seal, transaction
            )
            if (
                report.locked_source_manifest_sha256
                != source_manifest.manifest_sha256
            ):
                raise P8SealStateError("p8_report_source_manifest_pin_mismatch")
        if metrics is None:
            if report.aggregate_metrics_sha256 is not None:
                raise P8SealStateError("p8_report_aggregate_metrics_missing")
            if report.status is P8LockedEvaluationStatus.FAILED_CLOSED and (
                report.admitted_item_count != 0
                or report.paired_item_count != 0
                or report.evaluated_item_count != 0
                or report.evaluation_exclusion_count != 0
                or report.successful_item_count != 0
                or report.failed_item_count != 0
                or report.reason_codes != ("p8_locked_source_evaluation_failed",)
            ):
                raise P8SealStateError("p8_report_missing_metric_evidence")
            return
        if source_manifest is None:
            raise P8SealStateError("p8_metric_source_manifest_missing")
        if report.aggregate_metrics_sha256 != metrics.metrics_sha256:
            raise P8SealStateError("p8_report_aggregate_metrics_pin_mismatch")
        if (
            metrics.evaluation_thresholds_sha256
            != seal.evaluation_thresholds_sha256
        ):
            raise P8SealStateError("p8_metric_threshold_pin_mismatch")
        if (
            metrics.locked_source_manifest_sha256
            != source_manifest.manifest_sha256
        ):
            raise P8SealStateError("p8_metric_source_manifest_pin_mismatch")
        P8SealStateStore._validate_manifest_metric_completeness(
            source_manifest,
            metrics,
        )
        reason_codes = metrics.threshold_failure_reason_codes(
            seal.evaluation_thresholds
        )
        expected_status = (
            P8LockedEvaluationStatus.FAILED_CLOSED
            if reason_codes
            else P8LockedEvaluationStatus.COMPLETED
        )
        if report.status is not expected_status or report.reason_codes != reason_codes:
            raise P8SealStateError("p8_report_threshold_replay_mismatch")
        if (
            report.admitted_item_count != metrics.admitted_item_count
            or report.paired_item_count != metrics.paired_item_count
            or report.evaluated_item_count != metrics.evaluated_item_count
            or report.evaluation_exclusion_count
            != metrics.evaluation_exclusion_count
            or report.evaluation_exclusion_policy
            != metrics.evaluation_exclusion_policy
            or report.successful_item_count != metrics.successful_item_count
            or report.failed_item_count != metrics.failed_item_count
        ):
            raise P8SealStateError("p8_report_aggregate_counts_mismatch")

    @staticmethod
    def _validate_manifest_metric_completeness(
        source_manifest: P8LockedSourceManifest,
        metrics: P8AggregateEvaluationMetrics,
    ) -> None:
        if source_manifest.admitted_item_count != metrics.admitted_item_count:
            raise P8SealStateError("p8_metric_admission_count_pin_mismatch")
        if (
            source_manifest.evaluation_exclusion_policy
            != metrics.evaluation_exclusion_policy
        ):
            raise P8SealStateError("p8_metric_exclusion_policy_pin_mismatch")

    @staticmethod
    def _validate_source_manifest(
        source_manifest: P8LockedSourceManifest,
        seal: P8ReadZeroSeal,
        transaction: P8AccessTransaction,
    ) -> None:
        if (
            source_manifest.access_transaction_sha256
            != transaction.transaction_sha256
        ):
            raise P8SealStateError("p8_source_manifest_transaction_pin_mismatch")
        if source_manifest.source_revision != seal.source_revision:
            raise P8SealStateError("p8_source_manifest_revision_pin_mismatch")
        if source_manifest.dataset_id != seal.dataset_id:
            raise P8SealStateError("p8_source_manifest_dataset_pin_mismatch")
        if source_manifest.source_listing_sha256 != seal.source_listing_sha256:
            raise P8SealStateError("p8_source_manifest_listing_pin_mismatch")

    def _load_seal_at(self, directory_descriptor: int) -> P8ReadZeroSeal:
        try:
            metadata = os.fstat(directory_descriptor)
            return load_p8_state_bound_read_zero_document(
                self._read_json_at(directory_descriptor, _SEAL_FILENAME),
                state_directory_device=metadata.st_dev,
                state_directory_inode=metadata.st_ino,
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_read_zero_seal_invalid") from error

    def _load_actual_source_seal_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceReadZeroSeal:
        try:
            return P8ActualSourceReadZeroSeal.from_json_object(
                self._read_json_at(
                    directory_descriptor, _ACTUAL_SOURCE_SEAL_FILENAME
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_actual_source_seal_invalid") from error

    def _load_locked_label_download_transaction_at(
        self, directory_descriptor: int
    ) -> P8LockedLabelDownloadTransaction:
        try:
            return P8LockedLabelDownloadTransaction.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _LOCKED_LABEL_DOWNLOAD_TRANSACTION_FILENAME,
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_locked_label_download_transaction_invalid"
            ) from error

    def _load_locked_label_state_root_seal_at(
        self, directory_descriptor: int
    ) -> P8LockedLabelStateRootSeal:
        try:
            return P8LockedLabelStateRootSeal.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _LOCKED_LABEL_STATE_ROOT_SEAL_FILENAME,
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_locked_label_state_root_seal_invalid"
            ) from error

    def _load_locked_cohort_discovery_seal_at(
        self, directory_descriptor: int
    ) -> P8LockedCohortDiscoverySeal:
        try:
            return P8LockedCohortDiscoverySeal.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _LOCKED_COHORT_DISCOVERY_SEAL_FILENAME,
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_locked_cohort_discovery_seal_invalid"
            ) from error

    def _load_actual_source_gate_pending_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceGatePending:
        try:
            pending = P8ActualSourceGatePending.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _ACTUAL_SOURCE_GATE_PENDING_FILENAME,
                )
            )
            base = self._load_seal_at(directory_descriptor)
            actual = self._load_actual_source_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_actual_source_gate_pending_invalid"
            ) from error
        if (
            pending.base_read_zero_seal_sha256 != base.seal_sha256
            or pending.actual_source_seal_sha256
            != actual.actual_source_seal_sha256
            or pending.access_transaction_sha256
            != transaction.transaction_sha256
        ):
            raise P8SealStateError(
                "p8_actual_source_gate_pending_lineage_mismatch"
            )
        return pending

    def _load_actual_source_cleanup_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceCleanupState:
        try:
            cleanup = P8ActualSourceCleanupState.from_json_object(
                self._read_json_at(
                    directory_descriptor, _ACTUAL_SOURCE_CLEANUP_FILENAME
                )
            )
            pending = self._load_actual_source_gate_pending_at(
                directory_descriptor
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_actual_source_cleanup_state_invalid"
            ) from error
        if (
            cleanup.gate_pending_sha256 != pending.gate_pending_sha256
            or cleanup.access_transaction_sha256
            != pending.access_transaction_sha256
        ):
            raise P8SealStateError(
                "p8_actual_source_cleanup_lineage_mismatch"
            )
        return cleanup

    def _load_actual_source_reload_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceFreshProcessReloadReceipt:
        try:
            return P8ActualSourceFreshProcessReloadReceipt.from_json_object(
                self._read_json_at(
                    directory_descriptor, _ACTUAL_SOURCE_RELOAD_FILENAME
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_actual_source_reload_receipt_invalid"
            ) from error

    def _load_actual_source_commit_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceFinalResultCommit:
        try:
            return P8ActualSourceFinalResultCommit.from_json_object(
                self._read_json_at(
                    directory_descriptor, _ACTUAL_SOURCE_COMMIT_FILENAME
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError(
                "p8_actual_source_final_commit_invalid"
            ) from error

    def _validate_actual_source_reload_at(
        self,
        directory_descriptor: int,
        receipt: P8ActualSourceFreshProcessReloadReceipt,
    ) -> None:
        actual = self._load_actual_source_seal_at(directory_descriptor)
        pending = self._load_actual_source_gate_pending_at(
            directory_descriptor
        )
        cleanup = self._load_actual_source_cleanup_at(directory_descriptor)
        report, source_manifest, metrics = self._load_locked_evaluation_result_at(
            directory_descriptor
        )
        expected_status = (
            P8ActualSourceResultStatus.COMPLETED
            if report.status is P8LockedEvaluationStatus.COMPLETED
            else P8ActualSourceResultStatus.FAILED_CLOSED
        )
        if report.status is P8LockedEvaluationStatus.NOT_EXECUTED:
            raise P8SealStateError(
                "p8_actual_source_not_executed_result_invalid"
            )
        expected_manifest_sha256 = (
            source_manifest.manifest_sha256
            if source_manifest is not None
            else None
        )
        expected_metrics_sha256 = (
            metrics.metrics_sha256 if metrics is not None else None
        )
        if (
            receipt.actual_source_seal_sha256
            != actual.actual_source_seal_sha256
            or receipt.gate_pending_sha256 != pending.gate_pending_sha256
            or receipt.access_transaction_sha256
            != pending.access_transaction_sha256
            or receipt.cleanup_state_sha256 != cleanup.cleanup_state_sha256
            or receipt.canonical_report_sha256 != report.report_sha256
            or receipt.locked_source_manifest_sha256
            != expected_manifest_sha256
            or receipt.aggregate_metrics_sha256 != expected_metrics_sha256
            or receipt.result_status is not expected_status
            or receipt.cleanup_status is not cleanup.status
            or receipt.restricted_work_directory_absent
            is not cleanup.restricted_work_directory_absent
            or receipt.verifier_code_sha256
            != actual.fresh_reload_worker_code_sha256
        ):
            raise P8SealStateError(
                "p8_actual_source_reload_lineage_mismatch"
            )
        if cleanup.status is P8ActualSourceCleanupStatus.INCOMPLETE and (
            expected_status is not P8ActualSourceResultStatus.FAILED_CLOSED
            or source_manifest is not None
            or metrics is not None
        ):
            raise P8SealStateError(
                "p8_actual_source_incomplete_cleanup_reload_invalid"
            )
        if source_manifest is not None and receipt.aggregate_lineage_sha256 is None:
            raise P8SealStateError(
                "p8_actual_source_reload_lineage_receipt_missing"
            )
        if source_manifest is None and receipt.aggregate_lineage_sha256 is not None:
            raise P8SealStateError(
                "p8_actual_source_reload_lineage_receipt_unexpected"
            )

    def _validate_actual_source_commit_at(
        self,
        directory_descriptor: int,
        commit: P8ActualSourceFinalResultCommit,
    ) -> None:
        receipt = self._load_actual_source_reload_at(directory_descriptor)
        self._validate_actual_source_reload_at(directory_descriptor, receipt)
        expected = {
            "actual_source_seal_sha256": receipt.actual_source_seal_sha256,
            "gate_pending_sha256": receipt.gate_pending_sha256,
            "access_transaction_sha256": receipt.access_transaction_sha256,
            "cleanup_state_sha256": receipt.cleanup_state_sha256,
            "canonical_report_sha256": receipt.canonical_report_sha256,
            "locked_source_manifest_sha256": (
                receipt.locked_source_manifest_sha256
            ),
            "aggregate_metrics_sha256": receipt.aggregate_metrics_sha256,
            "aggregate_lineage_sha256": receipt.aggregate_lineage_sha256,
            "result_status": receipt.result_status,
            "cleanup_status": receipt.cleanup_status,
            "restricted_work_directory_absent": (
                receipt.restricted_work_directory_absent
            ),
            "fresh_process_reload_receipt_sha256": receipt.receipt_sha256,
        }
        if any(getattr(commit, name) != value for name, value in expected.items()):
            raise P8SealStateError(
                "p8_actual_source_final_commit_lineage_mismatch"
            )

    def _require_actual_source_commit_at(
        self, directory_descriptor: int
    ) -> P8ActualSourceFinalResultCommit:
        if not self._entry_exists_at(
            directory_descriptor, _ACTUAL_SOURCE_COMMIT_FILENAME
        ):
            raise P8SealStateError(
                "p8_actual_source_final_commit_required"
            )
        commit = self._load_actual_source_commit_at(directory_descriptor)
        self._validate_actual_source_commit_at(directory_descriptor, commit)
        return commit

    def _load_access_at(self, directory_descriptor: int) -> P8AccessTransaction:
        try:
            transaction = P8AccessTransaction.from_json_object(
                self._read_json_at(directory_descriptor, _ACCESS_FILENAME)
            )
            seal = self._load_seal_at(directory_descriptor)
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_access_transaction_invalid") from error
        if transaction.seal_sha256 != seal.seal_sha256:
            raise P8SealStateError("p8_access_transaction_seal_pin_mismatch")
        return transaction

    def _load_metrics_at(
        self, directory_descriptor: int
    ) -> P8AggregateEvaluationMetrics:
        try:
            return P8AggregateEvaluationMetrics.from_json_object(
                self._read_json_at(directory_descriptor, _METRICS_FILENAME)
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_aggregate_metrics_invalid") from error

    def _load_source_manifest_at(
        self, directory_descriptor: int
    ) -> P8LockedSourceManifest:
        try:
            return P8LockedSourceManifest.from_json_object(
                self._read_json_at(
                    directory_descriptor,
                    _SOURCE_MANIFEST_FILENAME,
                )
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_locked_source_manifest_invalid") from error

    def _open_state_directory(self) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self._state_directory, flags)
        except OSError as error:
            raise P8SealStateError("p8_state_directory_unavailable") from error
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise P8SealStateError("p8_state_directory_not_regular")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            os.close(descriptor)
            raise P8SealStateError("p8_state_directory_permissions_unsafe")
        return descriptor

    def _acquire_state_transition_directory(
        self,
        borrowed_descriptor: int | None = None,
    ) -> tuple[int, bool]:
        """Serialize source/label phase transitions on one canonical dirfd."""

        owns_descriptor = borrowed_descriptor is None
        descriptor = (
            self._open_state_directory()
            if borrowed_descriptor is None
            else borrowed_descriptor
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != os.geteuid()
            ):
                raise P8SealStateError(
                    "p8_state_transition_directory_invalid"
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise P8SealStateError("p8_state_operation_in_use") from None
            named_descriptor = self._open_state_directory()
            try:
                named_metadata = os.fstat(named_descriptor)
                if (
                    named_metadata.st_dev,
                    named_metadata.st_ino,
                ) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    raise P8SealStateError(
                        "p8_state_transition_directory_changed"
                    )
            finally:
                os.close(named_descriptor)
            return descriptor, owns_descriptor
        except Exception:
            if owns_descriptor:
                os.close(descriptor)
            raise

    @staticmethod
    def _create_exclusive_json(
        directory_descriptor: int,
        filename: str,
        document: dict[str, object],
        *,
        collision_reason: str,
    ) -> None:
        payload = canonical_json_bytes(document) + b"\n"
        if len(payload) > _MAX_STATE_BYTES:
            raise P8SealStateError("p8_state_document_too_large")
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                filename,
                flags,
                0o400,
                dir_fd=directory_descriptor,
            )
        except FileExistsError as error:
            raise P8SealStateError(collision_reason) from error
        except OSError as error:
            raise P8SealStateError("p8_state_create_failed") from error
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise P8SealStateError("p8_state_short_write")
                offset += written
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        except OSError as error:
            raise P8SealStateError("p8_state_durability_failed") from error
        finally:
            os.close(descriptor)
        try:
            os.fsync(directory_descriptor)
        except OSError as error:
            raise P8SealStateError("p8_state_durability_failed") from error

    @staticmethod
    def _read_json_at(directory_descriptor: int, filename: str) -> object:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
        except OSError as error:
            raise P8SealStateError("p8_state_document_unavailable") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise P8SealStateError("p8_state_document_not_regular")
            if (
                stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_uid != os.geteuid()
            ):
                raise P8SealStateError("p8_state_document_permissions_unsafe")
            if metadata.st_size <= 0 or metadata.st_size > _MAX_STATE_BYTES:
                raise P8SealStateError("p8_state_document_size_invalid")
            before = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            payload = bytearray()
            while len(payload) <= _MAX_STATE_BYTES:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, _MAX_STATE_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) > _MAX_STATE_BYTES:
                raise P8SealStateError("p8_state_document_too_large")
            after_metadata = os.fstat(descriptor)
            after = (
                after_metadata.st_dev,
                after_metadata.st_ino,
                after_metadata.st_size,
                after_metadata.st_mtime_ns,
            )
            if before != after or len(payload) != metadata.st_size:
                raise P8SealStateError("p8_state_document_changed_during_read")
        finally:
            os.close(descriptor)
        try:
            value: Any = json.loads(
                bytes(payload), object_pairs_hook=_unique_state_json_object
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise P8SealStateError("p8_state_document_invalid_json") from error
        return value

    @staticmethod
    def _entry_exists_at(directory_descriptor: int, filename: str) -> bool:
        try:
            os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as error:
            raise P8SealStateError("p8_state_entry_inspection_failed") from error
        return True


def _unique_state_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise P8SealStateError("p8_state_document_duplicate_key")
        document[key] = value
    return document


__all__ = ["P8SealStateError", "P8SealStateStore"]
