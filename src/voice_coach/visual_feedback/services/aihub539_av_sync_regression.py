"""Application service for non-P8 AI Hub 539 differential sync regression.

The service receives already extracted, ephemeral anchor pairs.  It performs no
filesystem, network, portal, decode, or restricted-media operation.  Each
baseline and injected anchor set is fitted independently; only the recovered
parameter *difference* is compared with the frozen injected change.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..models.aihub539_av_sync_regression import (
    Aihub539AvSyncAnchor,
    Aihub539AvSyncCaseAggregate,
    Aihub539AvSyncDifferentialMeasurementPair,
    Aihub539AvSyncInjectionCase,
    Aihub539AvSyncRegressionError,
    Aihub539AvSyncRegressionProtocol,
    Aihub539AvSyncRegressionResult,
    Aihub539AvSyncRegressionRunIdentity,
    _issue_completion_receipt,
    _issue_temporal_evidence,
)


@dataclass(frozen=True, slots=True)
class _AffineFit:
    offset_ms: float
    drift_parts_per_million: float
    root_mean_square_error_ms: float
    anchor_count: int
    anchor_span_ms: float


@dataclass(frozen=True, slots=True)
class _PairEvaluation:
    absolute_offset_change_error_ms: float
    absolute_drift_change_error_parts_per_million: float
    minimum_anchor_count: int
    minimum_anchor_span_ms: float
    maximum_affine_fit_root_mean_square_error_ms: float


class Aihub539AvSyncDifferentialRegressionService:
    """Evaluate every pre-observation case and issue disabled aggregate evidence."""

    def __init__(self, protocol: Aihub539AvSyncRegressionProtocol) -> None:
        if (
            not isinstance(protocol, Aihub539AvSyncRegressionProtocol)
            or protocol.to_json_object()
            != Aihub539AvSyncRegressionProtocol.frozen().to_json_object()
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_protocol_invalid"
            )
        self._protocol = protocol

    def evaluate(
        self,
        *,
        identity: Aihub539AvSyncRegressionRunIdentity,
        measurement_pairs: tuple[
            Aihub539AvSyncDifferentialMeasurementPair, ...
        ],
    ) -> Aihub539AvSyncRegressionResult:
        """Fit baseline/injected clocks and compare only their recovered change."""

        if not isinstance(identity, Aihub539AvSyncRegressionRunIdentity):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_run_identity_invalid"
            )
        if not isinstance(measurement_pairs, tuple):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_pair_identity_malformed"
            )

        grouped_pairs = self._group_and_validate_pair_identities(
            measurement_pairs
        )
        case_aggregates = tuple(
            self._evaluate_case(
                injection_case=injection_case,
                measurement_pairs=grouped_pairs[injection_case.case_id],
            )
            for injection_case in self._protocol.injection_cases
        )
        evidence = _issue_temporal_evidence(
            identity=identity,
            case_aggregates=case_aggregates,
        )
        completion = _issue_completion_receipt(evidence)
        return Aihub539AvSyncRegressionResult(
            evidence=evidence,
            completion_receipt=completion,
        )

    def _group_and_validate_pair_identities(
        self,
        measurement_pairs: tuple[
            Aihub539AvSyncDifferentialMeasurementPair, ...
        ],
    ) -> dict[str, tuple[Aihub539AvSyncDifferentialMeasurementPair, ...]]:
        expected_case_ids = tuple(
            injection_case.case_id
            for injection_case in self._protocol.injection_cases
        )
        grouped: dict[str, list[Aihub539AvSyncDifferentialMeasurementPair]] = {
            case_id: [] for case_id in expected_case_ids
        }
        for pair in measurement_pairs:
            if not isinstance(pair, Aihub539AvSyncDifferentialMeasurementPair):
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_pair_identity_malformed"
                )
            if not isinstance(pair.case_id, str) or pair.case_id not in grouped:
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_case_identity_malformed"
                )
            if (
                isinstance(pair.pair_ordinal, bool)
                or not isinstance(pair.pair_ordinal, int)
                or pair.pair_ordinal < 0
            ):
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_pair_identity_malformed"
                )
            grouped[pair.case_id].append(pair)

        ordered: dict[
            str, tuple[Aihub539AvSyncDifferentialMeasurementPair, ...]
        ] = {}
        for case_id in expected_case_ids:
            case_pairs = sorted(
                grouped[case_id], key=lambda pair: pair.pair_ordinal
            )
            if len(case_pairs) < self._protocol.minimum_measurement_pairs_per_case:
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_case_set_incomplete"
                )
            if [pair.pair_ordinal for pair in case_pairs] != list(
                range(len(case_pairs))
            ):
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_pair_identity_malformed"
                )
            ordered[case_id] = tuple(case_pairs)
        return ordered

    def _evaluate_case(
        self,
        *,
        injection_case: Aihub539AvSyncInjectionCase,
        measurement_pairs: tuple[
            Aihub539AvSyncDifferentialMeasurementPair, ...
        ],
    ) -> Aihub539AvSyncCaseAggregate:
        evaluations = tuple(
            self._evaluate_pair(
                injection_case=injection_case,
                measurement_pair=measurement_pair,
            )
            for measurement_pair in measurement_pairs
        )
        return Aihub539AvSyncCaseAggregate(
            case_id=injection_case.case_id,
            injected_offset_change_ms=(
                injection_case.injected_offset_change_ms
            ),
            injected_drift_change_parts_per_million=(
                injection_case.injected_drift_change_parts_per_million
            ),
            measurement_pair_count=len(evaluations),
            passing_measurement_pair_count=len(evaluations),
            maximum_absolute_offset_change_error_ms=max(
                evaluation.absolute_offset_change_error_ms
                for evaluation in evaluations
            ),
            maximum_absolute_drift_change_error_parts_per_million=max(
                evaluation.absolute_drift_change_error_parts_per_million
                for evaluation in evaluations
            ),
            minimum_anchor_count=min(
                evaluation.minimum_anchor_count for evaluation in evaluations
            ),
            minimum_anchor_span_ms=min(
                evaluation.minimum_anchor_span_ms for evaluation in evaluations
            ),
            maximum_affine_fit_root_mean_square_error_ms=max(
                evaluation.maximum_affine_fit_root_mean_square_error_ms
                for evaluation in evaluations
            ),
            thresholds_passed=True,
        )

    def _evaluate_pair(
        self,
        *,
        injection_case: Aihub539AvSyncInjectionCase,
        measurement_pair: Aihub539AvSyncDifferentialMeasurementPair,
    ) -> _PairEvaluation:
        if measurement_pair.case_id != injection_case.case_id:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_case_identity_malformed"
            )
        baseline_anchors = self._validate_anchor_sequence(
            measurement_pair.baseline_anchors
        )
        injected_anchors = self._validate_anchor_sequence(
            measurement_pair.injected_anchors
        )
        if len(baseline_anchors) != len(injected_anchors) or any(
            baseline.audio_time_ms != injected.audio_time_ms
            for baseline, injected in zip(
                baseline_anchors, injected_anchors, strict=False
            )
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchor_identity_mismatch"
            )

        baseline_fit = self._fit_affine_clock(baseline_anchors)
        injected_fit = self._fit_affine_clock(injected_anchors)
        maximum_fit_error = max(
            baseline_fit.root_mean_square_error_ms,
            injected_fit.root_mean_square_error_ms,
        )
        if (
            maximum_fit_error
            > self._protocol.maximum_affine_fit_root_mean_square_error_ms
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_affine_fit_error_threshold_failed"
            )

        recovered_offset_change = (
            injected_fit.offset_ms - baseline_fit.offset_ms
        )
        recovered_drift_change = (
            injected_fit.drift_parts_per_million
            - baseline_fit.drift_parts_per_million
        )
        offset_error = abs(
            recovered_offset_change
            - injection_case.injected_offset_change_ms
        )
        drift_error = abs(
            recovered_drift_change
            - injection_case.injected_drift_change_parts_per_million
        )
        if not math.isfinite(offset_error) or not math.isfinite(drift_error):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_recovered_change_nonfinite"
            )
        if offset_error > self._protocol.offset_change_absolute_tolerance_ms:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_offset_change_threshold_failed"
            )
        if (
            drift_error
            > self._protocol.drift_change_absolute_tolerance_parts_per_million
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_drift_change_threshold_failed"
            )
        return _PairEvaluation(
            absolute_offset_change_error_ms=offset_error,
            absolute_drift_change_error_parts_per_million=drift_error,
            minimum_anchor_count=min(
                baseline_fit.anchor_count, injected_fit.anchor_count
            ),
            minimum_anchor_span_ms=min(
                baseline_fit.anchor_span_ms, injected_fit.anchor_span_ms
            ),
            maximum_affine_fit_root_mean_square_error_ms=maximum_fit_error,
        )

    def _validate_anchor_sequence(
        self, anchors: tuple[Aihub539AvSyncAnchor, ...]
    ) -> tuple[Aihub539AvSyncAnchor, ...]:
        if not isinstance(anchors, tuple):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchor_identity_malformed"
            )
        if len(anchors) < self._protocol.minimum_anchor_count_per_measurement:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchor_count_insufficient"
            )
        for anchor in anchors:
            if not isinstance(anchor, Aihub539AvSyncAnchor):
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_anchor_identity_malformed"
                )
            if (
                isinstance(anchor.audio_time_ms, bool)
                or not isinstance(anchor.audio_time_ms, (int, float))
                or isinstance(anchor.video_time_ms, bool)
                or not isinstance(anchor.video_time_ms, (int, float))
                or not math.isfinite(float(anchor.audio_time_ms))
                or not math.isfinite(float(anchor.video_time_ms))
            ):
                raise Aihub539AvSyncRegressionError(
                    "aihub539_av_sync_anchor_nonfinite"
                )
        audio_times = tuple(float(anchor.audio_time_ms) for anchor in anchors)
        if any(
            later <= earlier
            for earlier, later in zip(audio_times, audio_times[1:], strict=False)
        ):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchors_ambiguous"
            )
        if audio_times[-1] - audio_times[0] < self._protocol.minimum_anchor_span_ms:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchors_ambiguous"
            )
        return anchors

    @staticmethod
    def _fit_affine_clock(
        anchors: tuple[Aihub539AvSyncAnchor, ...]
    ) -> _AffineFit:
        audio_times = tuple(float(anchor.audio_time_ms) for anchor in anchors)
        video_times = tuple(float(anchor.video_time_ms) for anchor in anchors)
        mean_audio = math.fsum(audio_times) / len(audio_times)
        mean_video = math.fsum(video_times) / len(video_times)
        centered_audio = tuple(value - mean_audio for value in audio_times)
        denominator = math.fsum(value * value for value in centered_audio)
        if not math.isfinite(denominator) or denominator <= 0.0:
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_anchors_ambiguous"
            )
        slope = math.fsum(
            audio_delta * (video_time - mean_video)
            for audio_delta, video_time in zip(
                centered_audio, video_times, strict=True
            )
        ) / denominator
        offset = mean_video - slope * mean_audio
        drift_parts_per_million = (slope - 1.0) * 1_000_000.0
        residuals = tuple(
            video_time - (offset + slope * audio_time)
            for audio_time, video_time in zip(
                audio_times, video_times, strict=True
            )
        )
        root_mean_square_error = math.sqrt(
            math.fsum(residual * residual for residual in residuals)
            / len(residuals)
        )
        values = (offset, drift_parts_per_million, root_mean_square_error)
        if any(not math.isfinite(value) for value in values):
            raise Aihub539AvSyncRegressionError(
                "aihub539_av_sync_recovered_change_nonfinite"
            )
        return _AffineFit(
            offset_ms=offset,
            drift_parts_per_million=drift_parts_per_million,
            root_mean_square_error_ms=root_mean_square_error,
            anchor_count=len(anchors),
            anchor_span_ms=audio_times[-1] - audio_times[0],
        )


__all__ = [
    "Aihub539AvSyncDifferentialRegressionService",
    "Aihub539AvSyncRegressionError",
]
