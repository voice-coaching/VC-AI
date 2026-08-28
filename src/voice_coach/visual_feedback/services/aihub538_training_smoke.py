"""Application service for non-locked AI Hub 538 admission and smoke audit."""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.aihub538_training_dataset import (
    Aihub538TrainingAdmissionError,
    Aihub538TrainingDatasetAdapter,
)
from ..models.aihub538_training_evidence import (
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingEvaluatorSmokeReceipt,
    Aihub538TrainingSampleAggregate,
)


_MAXIMUM_METADATA_AV_DURATION_DELTA_SECONDS = 0.25


class Aihub538TrainingSmokeError(RuntimeError):
    """Aggregate-only service failure with no restricted values."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538TrainingSmokeEvidence:
    """Two non-promotional receipts produced from the same admitted aggregate."""

    admission_receipt: Aihub538TrainingAdmissionReceipt
    evaluator_smoke_receipt: Aihub538TrainingEvaluatorSmokeReceipt


class Aihub538TrainingSmokeService:
    """Admit all pairs, run bounded metadata checks, and issue no thresholds."""

    def __init__(self, dataset_adapter: Aihub538TrainingDatasetAdapter) -> None:
        self._dataset_adapter = dataset_adapter

    def evaluate(
        self,
        *,
        label_zip_descriptor: int,
        source_zip_descriptor: int,
        dataset_access_permission_confirmed: bool,
        identity_hash_key: bytes,
    ) -> Aihub538TrainingSmokeEvidence:
        """Execute the complete fixture/training smoke without touching P8 data."""

        if dataset_access_permission_confirmed is not True:
            raise Aihub538TrainingSmokeError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        try:
            aggregate = self._dataset_adapter.admit(
                label_zip_descriptor=label_zip_descriptor,
                source_zip_descriptor=source_zip_descriptor,
                identity_hash_key=identity_hash_key,
            )
        except Aihub538TrainingAdmissionError as error:
            raise Aihub538TrainingSmokeError(error.reason_code) from None
        _validate_complete_smoke_population(aggregate.samples)
        admission_receipt = Aihub538TrainingAdmissionReceipt.issue(aggregate)
        evaluator_receipt = Aihub538TrainingEvaluatorSmokeReceipt.issue(
            admission_receipt=admission_receipt,
            aggregate=aggregate,
        )
        return Aihub538TrainingSmokeEvidence(
            admission_receipt=admission_receipt,
            evaluator_smoke_receipt=evaluator_receipt,
        )


def _validate_complete_smoke_population(
    samples: tuple[Aihub538TrainingSampleAggregate, ...],
) -> None:
    if not samples:
        raise Aihub538TrainingSmokeError("aihub538_training_smoke_population_empty")
    for sample in samples:
        duration_delta = abs(
            sample.video_duration_seconds - sample.audio_duration_seconds
        )
        if duration_delta > _MAXIMUM_METADATA_AV_DURATION_DELTA_SECONDS:
            raise Aihub538TrainingSmokeError(
                "aihub538_training_metadata_av_duration_mismatch"
            )
        if (
            sample.lip_bbox_frame_count != sample.face_bbox_frame_count
            or sample.sentence_count <= 0
            or sample.video_size_bytes < 12
            or sample.audio_size_bytes < 12
        ):
            raise Aihub538TrainingSmokeError(
                "aihub538_training_smoke_sample_incomplete"
            )


__all__ = [
    "Aihub538TrainingSmokeError",
    "Aihub538TrainingSmokeEvidence",
    "Aihub538TrainingSmokeService",
]
