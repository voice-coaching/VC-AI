"""Application service for consuming and executing the one-shot P8 gate."""

from __future__ import annotations

from collections.abc import Callable

from ..adapters.p8_seal_store import P8SealStateStore
from ..errors import VisualFeedbackContractError
from ..models.p8_aggregate_metrics import P8AggregateEvaluationMetrics
from ..models.p8_locked_evaluation import (
    P8AccessTransaction,
    P8LockedEvaluationReport,
    P8LockedEvaluationStatus,
    P8ReadZeroSeal,
)
from ..models.p8_locked_source_manifest import (
    P8LockedSourceEvaluationResult,
    P8LockedSourceManifest,
)


P8LockedSourceEvaluation = Callable[
    [P8AccessTransaction], P8LockedSourceEvaluationResult
]


class P8LockedEvaluationService:
    """Order one irreversible authorization consumption before source access."""

    def __init__(self, state_store: P8SealStateStore) -> None:
        self._state_store = state_store

    def create_read_zero_seal(self, seal: P8ReadZeroSeal) -> None:
        """Persist an immutable seal before constructing any source evaluator."""

        self._state_store.create_read_zero_seal(seal)

    def execute_locked_evaluation(
        self,
        *,
        expected_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
        report_id: str,
        open_and_evaluate_aggregate: P8LockedSourceEvaluation,
    ) -> P8LockedEvaluationReport:
        """Consume first, then call the only boundary allowed to open the source.

        Any exception after the durable transaction exists is mapped to a generic
        aggregate-only failure report.  The transaction is never removed, so the
        same seal cannot be retried after either success or failure.
        """

        transaction = self._state_store.consume_access_authorization(
            expected_seal_sha256=expected_seal_sha256,
            transaction_id=transaction_id,
            consumed_at_utc=consumed_at_utc,
        )
        seal = self._state_store.load_read_zero_seal()
        source_manifest: P8LockedSourceManifest | None = None
        metrics: P8AggregateEvaluationMetrics | None = None
        try:
            evaluation_result = open_and_evaluate_aggregate(transaction)
            if not isinstance(evaluation_result, P8LockedSourceEvaluationResult):
                raise VisualFeedbackContractError(
                    "P8 evaluator returned an unsupported locked result contract"
                )
            source_manifest = evaluation_result.source_manifest
            metrics = evaluation_result.metrics
            if (
                source_manifest.access_transaction_sha256
                != transaction.transaction_sha256
                or source_manifest.source_revision != seal.source_revision
                or source_manifest.source_listing_sha256
                != seal.source_listing_sha256
            ):
                raise VisualFeedbackContractError(
                    "P8 source manifest does not resolve to consumed authorization"
                )
            reason_codes = metrics.threshold_failure_reason_codes(
                seal.evaluation_thresholds
            )
            status = (
                P8LockedEvaluationStatus.FAILED_CLOSED
                if reason_codes
                else P8LockedEvaluationStatus.COMPLETED
            )
            report = P8LockedEvaluationReport.issue(
                report_id=report_id,
                protocol_revision=seal.protocol_revision,
                transaction=transaction,
                status=status,
                source_open_attempted=True,
                admitted_item_count=metrics.admitted_item_count,
                paired_item_count=metrics.paired_item_count,
                evaluated_item_count=metrics.evaluated_item_count,
                evaluation_exclusion_count=(
                    metrics.evaluation_exclusion_count
                ),
                successful_item_count=metrics.successful_item_count,
                failed_item_count=metrics.failed_item_count,
                locked_source_manifest_sha256=(
                    source_manifest.manifest_sha256
                ),
                aggregate_metrics_sha256=metrics.metrics_sha256,
                reason_codes=reason_codes,
            )
        except Exception:  # The locked error detail must not enter aggregate state.
            source_manifest = None
            metrics = None
            report = P8LockedEvaluationReport.issue(
                report_id=report_id,
                protocol_revision=seal.protocol_revision,
                transaction=transaction,
                status=P8LockedEvaluationStatus.FAILED_CLOSED,
                source_open_attempted=True,
                admitted_item_count=0,
                paired_item_count=0,
                evaluated_item_count=0,
                evaluation_exclusion_count=0,
                successful_item_count=0,
                failed_item_count=0,
                locked_source_manifest_sha256=None,
                aggregate_metrics_sha256=None,
                reason_codes=("p8_locked_source_evaluation_failed",),
            )
        self._state_store.publish_locked_result(
            report,
            source_manifest=source_manifest,
            metrics=metrics,
        )
        return report

    def consume_without_source_open(
        self,
        *,
        expected_seal_sha256: str,
        transaction_id: str,
        consumed_at_utc: str,
        report_id: str,
    ) -> P8LockedEvaluationReport:
        """Intentionally burn a seal and publish a non-execution skeleton."""

        transaction = self._state_store.consume_access_authorization(
            expected_seal_sha256=expected_seal_sha256,
            transaction_id=transaction_id,
            consumed_at_utc=consumed_at_utc,
        )
        seal = self._state_store.load_read_zero_seal()
        report = P8LockedEvaluationReport.issue(
            report_id=report_id,
            protocol_revision=seal.protocol_revision,
            transaction=transaction,
            status=P8LockedEvaluationStatus.NOT_EXECUTED,
            source_open_attempted=False,
            admitted_item_count=0,
            paired_item_count=0,
            evaluated_item_count=0,
            evaluation_exclusion_count=0,
            successful_item_count=0,
            failed_item_count=0,
            locked_source_manifest_sha256=None,
            aggregate_metrics_sha256=None,
            reason_codes=("source_evaluation_not_executed",),
        )
        self._state_store.publish_locked_result(
            report,
            source_manifest=None,
            metrics=None,
        )
        return report


__all__ = [
    "P8AggregateEvaluationMetrics",
    "P8LockedEvaluationService",
    "P8LockedSourceEvaluationResult",
    "P8LockedSourceEvaluation",
]
