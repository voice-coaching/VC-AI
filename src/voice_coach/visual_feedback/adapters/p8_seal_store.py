"""Durable O_EXCL-backed state for the one-shot P8 locked evaluation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from ..errors import VisualFeedbackContractError
from ..models.integrity import canonical_json_bytes, require_sha256
from ..models.p8_aggregate_metrics import P8AggregateEvaluationMetrics
from ..models.p8_locked_evaluation import (
    P8AccessTransaction,
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from ..models.p8_locked_source_manifest import P8LockedSourceManifest

_SEAL_FILENAME = "p8_read_zero_seal.json"
_ACCESS_FILENAME = "p8_access_transaction.json"
_SOURCE_MANIFEST_FILENAME = "p8_locked_source_manifest.json"
_METRICS_FILENAME = "p8_aggregate_evaluation_metrics.json"
_REPORT_FILENAME = "p8_locked_evaluation_report.json"
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

    def create_read_zero_seal(self, seal: P8ReadZeroSeal) -> None:
        """Create and fsync the initial access_count=0 seal exactly once."""

        directory_descriptor = self._open_state_directory()
        try:
            self._create_exclusive_json(
                directory_descriptor,
                _SEAL_FILENAME,
                seal.to_json_object(),
                collision_reason="p8_read_zero_seal_already_exists",
            )
        finally:
            os.close(directory_descriptor)

    def load_read_zero_seal(self) -> P8ReadZeroSeal:
        """Load and validate the immutable seal without accessing source media."""

        directory_descriptor = self._open_state_directory()
        try:
            return self._load_seal_at(directory_descriptor)
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
        directory_descriptor = self._open_state_directory()
        try:
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
            seal = self._load_seal_at(directory_descriptor)
            transaction = self._load_access_at(directory_descriptor)
            report = P8LockedEvaluationReport.from_json_object(
                self._read_json_at(directory_descriptor, _REPORT_FILENAME)
            )
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
        finally:
            os.close(directory_descriptor)

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
            return P8ReadZeroSeal.from_json_object(
                self._read_json_at(directory_descriptor, _SEAL_FILENAME)
            )
        except P8SealStateError:
            raise
        except VisualFeedbackContractError as error:
            raise P8SealStateError("p8_read_zero_seal_invalid") from error

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
            if stat.S_IMODE(metadata.st_mode) != 0o400:
                raise P8SealStateError("p8_state_document_permissions_unsafe")
            if metadata.st_size <= 0 or metadata.st_size > _MAX_STATE_BYTES:
                raise P8SealStateError("p8_state_document_size_invalid")
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
        finally:
            os.close(descriptor)
        try:
            value: Any = json.loads(bytes(payload))
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


__all__ = ["P8SealStateError", "P8SealStateStore"]
