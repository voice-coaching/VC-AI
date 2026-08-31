"""Production-only P8 one-shot facade.

This is the public composition boundary.  It intentionally has no
``frozen_pipeline`` argument: the exact concrete pipeline is strict-loaded
before the deferred locked source can be opened or the one-shot authorization
can be consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..adapters.aihub538_training_archive import (
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
)
from ..adapters.p8_production_frozen_pipeline import (
    ProductionP8ArtifactDescriptors,
    load_production_p8_frozen_pipeline,
    require_exact_production_p8_pipeline,
)
from ..adapters.p8_seal_store import P8SealStateStore
from ..models.p8_locked_evaluation import P8LockedEvaluationReport
from .p8_aihub538_source_evaluator import P8Aihub538OneShotRunner


@dataclass(frozen=True, slots=True)
class P8ProductionRunRequest:
    confirm_irreversible_consumption: bool
    input_directory: Path
    identity_key_descriptor: int
    restricted_reference_directory_descriptor: int
    artifact_descriptors: ProductionP8ArtifactDescriptors
    output_directory: Path
    expected_seal_sha256: str
    expected_actual_source_seal_sha256: str
    transaction_id: str
    consumed_at_utc: str
    report_id: str
    pending_at_utc: str
    cleanup_recorded_at_utc: str
    reload_verified_at_utc: str
    committed_at_utc: str


class P8ProductionOneShotFacade:
    """Concrete-only facade; pipeline assembly always precedes consumption."""

    def __init__(
        self,
        *,
        state_store: P8SealStateStore,
        archive_policy: Aihub538ArchiveSecurityPolicy = (
            DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
        ),
    ) -> None:
        if type(state_store) is not P8SealStateStore:
            raise TypeError("p8_production_state_store_concrete_required")
        self._state_store = state_store
        self._runner = P8Aihub538OneShotRunner(
            state_store=state_store, archive_policy=archive_policy
        )

    def execute_from_completed_directory(
        self, request: P8ProductionRunRequest
    ) -> P8LockedEvaluationReport:
        if type(request) is not P8ProductionRunRequest:
            raise TypeError("p8_production_run_request_invalid")
        # All JSON/model/code reads happen here.  input_directory is merely a
        # deferred Path and has not been stat'ed or opened at this point.
        seal = self._state_store.load_read_zero_seal()
        pipeline = require_exact_production_p8_pipeline(
            load_production_p8_frozen_pipeline(
                seal=seal, descriptors=request.artifact_descriptors
            )
        )
        try:
            return self._runner.execute_from_completed_directory(
                confirm_irreversible_consumption=(
                    request.confirm_irreversible_consumption
                ),
                input_directory=request.input_directory,
                identity_key_descriptor=request.identity_key_descriptor,
                restricted_reference_directory_descriptor=(
                    request.restricted_reference_directory_descriptor
                ),
                frozen_pipeline=pipeline,
                output_directory=request.output_directory,
                expected_seal_sha256=request.expected_seal_sha256,
                expected_actual_source_seal_sha256=(
                    request.expected_actual_source_seal_sha256
                ),
                transaction_id=request.transaction_id,
                consumed_at_utc=request.consumed_at_utc,
                report_id=request.report_id,
                pending_at_utc=request.pending_at_utc,
                cleanup_recorded_at_utc=request.cleanup_recorded_at_utc,
                reload_verified_at_utc=request.reload_verified_at_utc,
                committed_at_utc=request.committed_at_utc,
            )
        finally:
            close_pipeline = getattr(pipeline, "close", None)
            if callable(close_pipeline):
                close_pipeline()


__all__ = ["P8ProductionOneShotFacade", "P8ProductionRunRequest"]
