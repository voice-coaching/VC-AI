"""Deterministic calibrated decisions over raw video-QC and A/V candidates.

The measurement adapters in :mod:`video_qc` and :mod:`lip_audio_av_sync`
cannot authorize a runtime decision.  This module is the separate executable
boundary that applies an explicitly pinned decision policy.  Every decision
copies the exact raw candidate receipt and the observed values it interpreted;
there is no constructor that accepts an unexplained ``passed`` or
``synchronized`` mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

from voice_coach.visual_feedback.models.binding import SameAttemptBindingReceipt

from .lip_audio_av_sync import AvSyncAnchorCandidate, AvSyncCandidateEvidence
from .video_qc import VideoQcCandidateEvidence, VideoQcMetrics


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,191}$")

RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION = (
    "voice_coach.runtime_video_qc_decision.v2"
)
RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION = (
    "voice_coach.runtime_av_sync_decision.v2"
)

_VIDEO_QC_FAILURE_REASONS = (
    "video_qc_source_candidate_unavailable",
    "video_qc_single_face_coverage_below_policy",
    "video_qc_multiple_face_fraction_above_policy",
    "video_qc_mean_luma_below_policy",
    "video_qc_mean_luma_above_policy",
    "video_qc_underexposed_fraction_above_policy",
    "video_qc_overexposed_fraction_above_policy",
    "video_qc_lip_edge_delta_below_policy",
    "video_qc_track_transition_coverage_below_policy",
    "video_qc_single_face_run_below_policy",
    "video_qc_lip_center_step_above_policy",
)
_AV_SYNC_FAILURE_REASONS = (
    "av_sync_source_candidate_unavailable",
    "av_sync_offset_outside_policy",
    "av_sync_correlation_below_policy",
    "av_sync_anchor_count_below_policy",
    "av_sync_anchor_strength_below_policy",
)


@dataclass(frozen=True, slots=True)
class CalibratedVideoQcDecisionPolicy:
    """Frozen thresholds interpreted only by the calibrated decision wrapper."""

    policy_revision: str
    expected_measurement_policy_sha256: str
    minimum_single_face_coverage: float
    maximum_multiple_face_frame_fraction: float
    minimum_mean_luma_8bit: float
    maximum_mean_luma_8bit: float
    maximum_underexposed_pixel_fraction: float
    maximum_overexposed_pixel_fraction: float
    minimum_lip_roi_mean_neighbor_luma_delta_8bit: float
    minimum_track_transition_coverage: float
    minimum_longest_single_face_run_fraction: float
    maximum_lip_center_step_normalized: float
    schema_version: str = "voice_coach.calibrated_video_qc_decision_policy.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.calibrated_video_qc_decision_policy.v1":
            raise ValueError("calibrated video QC policy schema differs")
        _require_identifier(self.policy_revision, "policy_revision")
        _require_sha256(
            self.expected_measurement_policy_sha256,
            "expected_measurement_policy_sha256",
        )
        for name in (
            "minimum_single_face_coverage",
            "maximum_multiple_face_frame_fraction",
            "maximum_underexposed_pixel_fraction",
            "maximum_overexposed_pixel_fraction",
            "minimum_track_transition_coverage",
            "minimum_longest_single_face_run_fraction",
        ):
            _require_finite_range(getattr(self, name), name, 0.0, 1.0)
        for name in (
            "minimum_mean_luma_8bit",
            "maximum_mean_luma_8bit",
            "minimum_lip_roi_mean_neighbor_luma_delta_8bit",
        ):
            _require_finite_range(getattr(self, name), name, 0.0, 255.0)
        if self.minimum_mean_luma_8bit > self.maximum_mean_luma_8bit:
            raise ValueError("calibrated video QC luma bounds differ")
        _require_finite_range(
            self.maximum_lip_center_step_normalized,
            "maximum_lip_center_step_normalized",
            0.0,
            math.sqrt(2.0),
        )

    @property
    def policy_sha256(self) -> str:
        return _sha256_document(self.to_json_object())

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "expected_measurement_policy_sha256": (
                self.expected_measurement_policy_sha256
            ),
            "minimum_single_face_coverage": self.minimum_single_face_coverage,
            "maximum_multiple_face_frame_fraction": (
                self.maximum_multiple_face_frame_fraction
            ),
            "minimum_mean_luma_8bit": self.minimum_mean_luma_8bit,
            "maximum_mean_luma_8bit": self.maximum_mean_luma_8bit,
            "maximum_underexposed_pixel_fraction": (
                self.maximum_underexposed_pixel_fraction
            ),
            "maximum_overexposed_pixel_fraction": (
                self.maximum_overexposed_pixel_fraction
            ),
            "minimum_lip_roi_mean_neighbor_luma_delta_8bit": (
                self.minimum_lip_roi_mean_neighbor_luma_delta_8bit
            ),
            "minimum_track_transition_coverage": (
                self.minimum_track_transition_coverage
            ),
            "minimum_longest_single_face_run_fraction": (
                self.minimum_longest_single_face_run_fraction
            ),
            "maximum_lip_center_step_normalized": (
                self.maximum_lip_center_step_normalized
            ),
        }


@dataclass(frozen=True, slots=True)
class CalibratedAvSyncDecisionPolicy:
    """Frozen thresholds for promoting one raw content-sync candidate."""

    policy_revision: str
    expected_measurement_policy_sha256: str
    maximum_absolute_offset_ms: int
    minimum_correlation: float
    minimum_anchor_count: int
    minimum_anchor_strength: float
    schema_version: str = "voice_coach.calibrated_av_sync_decision_policy.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "voice_coach.calibrated_av_sync_decision_policy.v1":
            raise ValueError("calibrated A/V sync policy schema differs")
        _require_identifier(self.policy_revision, "policy_revision")
        _require_sha256(
            self.expected_measurement_policy_sha256,
            "expected_measurement_policy_sha256",
        )
        if (
            isinstance(self.maximum_absolute_offset_ms, bool)
            or not isinstance(self.maximum_absolute_offset_ms, int)
            or self.maximum_absolute_offset_ms <= 0
        ):
            raise ValueError("maximum_absolute_offset_ms must be positive")
        _require_finite_range(
            self.minimum_correlation, "minimum_correlation", -1.0, 1.0
        )
        if (
            isinstance(self.minimum_anchor_count, bool)
            or not isinstance(self.minimum_anchor_count, int)
            or self.minimum_anchor_count <= 0
        ):
            raise ValueError("minimum_anchor_count must be positive")
        _require_finite_range(
            self.minimum_anchor_strength,
            "minimum_anchor_strength",
            0.0,
            1.0,
        )

    @property
    def policy_sha256(self) -> str:
        return _sha256_document(self.to_json_object())

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "expected_measurement_policy_sha256": (
                self.expected_measurement_policy_sha256
            ),
            "maximum_absolute_offset_ms": self.maximum_absolute_offset_ms,
            "minimum_correlation": self.minimum_correlation,
            "minimum_anchor_count": self.minimum_anchor_count,
            "minimum_anchor_strength": self.minimum_anchor_strength,
        }


@dataclass(frozen=True, slots=True)
class RuntimeVideoQcDecision:
    """Typed QC decision bound to one exact raw candidate and observations."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    container_sha256: str
    landmark_evidence_sha256: str
    landmark_model_sha256: str
    source_candidate_receipt_sha256: str
    source_candidate_provenance_receipt_sha256: str
    measurement_revision: str
    measurement_policy_sha256: str
    measurement_adapter_code_sha256: str
    observed_metrics: VideoQcMetrics
    decision_policy_revision: str
    decision_policy_sha256: str
    decision_adapter_code_sha256: str
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION:
            raise ValueError("runtime video QC decision schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "container_sha256",
            "landmark_evidence_sha256",
            "landmark_model_sha256",
            "source_candidate_receipt_sha256",
            "source_candidate_provenance_receipt_sha256",
            "measurement_policy_sha256",
            "measurement_adapter_code_sha256",
            "decision_policy_sha256",
            "decision_adapter_code_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_identifier(self.measurement_revision, "measurement_revision")
        _require_identifier(self.decision_policy_revision, "decision_policy_revision")
        if not isinstance(self.observed_metrics, VideoQcMetrics):
            raise ValueError("observed_metrics must be VideoQcMetrics")
        if self.status not in {"passed", "failed"}:
            raise ValueError("runtime video QC decision status differs")
        _require_reason_codes(self.reason_codes, _VIDEO_QC_FAILURE_REASONS)
        if (self.status == "passed") != (not self.reason_codes):
            raise ValueError("runtime video QC status and reasons differ")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("runtime video QC decision digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "container_sha256": self.container_sha256,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "source_candidate_receipt_sha256": (
                self.source_candidate_receipt_sha256
            ),
            "source_candidate_provenance_receipt_sha256": (
                self.source_candidate_provenance_receipt_sha256
            ),
            "measurement_revision": self.measurement_revision,
            "measurement_policy_sha256": self.measurement_policy_sha256,
            "measurement_adapter_code_sha256": (
                self.measurement_adapter_code_sha256
            ),
            "observed_metrics": self.observed_metrics.to_json_object(),
            "decision_policy_revision": self.decision_policy_revision,
            "decision_policy_sha256": self.decision_policy_sha256,
            "decision_adapter_code_sha256": self.decision_adapter_code_sha256,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


@dataclass(frozen=True, slots=True)
class RuntimeAvSyncDecision:
    """Typed sync decision bound to candidate offset, correlation, and anchors."""

    attempt_id_sha256: str
    binding_receipt_sha256: str
    audio_source_sha256: str
    video_source_sha256: str
    landmark_evidence_sha256: str
    landmark_model_sha256: str
    source_candidate_receipt_sha256: str
    source_candidate_lineage_receipt_sha256: str
    measurement_revision: str
    measurement_policy_sha256: str
    measurement_adapter_code_sha256: str
    observed_offset_ms: int | None
    observed_correlation: float | None
    observed_anchors: tuple[AvSyncAnchorCandidate, ...]
    decision_policy_revision: str
    decision_policy_sha256: str
    decision_adapter_code_sha256: str
    status: str
    reason_codes: tuple[str, ...]
    receipt_sha256: str
    schema_version: str = RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION:
            raise ValueError("runtime A/V sync decision schema differs")
        for name in (
            "attempt_id_sha256",
            "binding_receipt_sha256",
            "audio_source_sha256",
            "video_source_sha256",
            "landmark_evidence_sha256",
            "landmark_model_sha256",
            "source_candidate_receipt_sha256",
            "source_candidate_lineage_receipt_sha256",
            "measurement_policy_sha256",
            "measurement_adapter_code_sha256",
            "decision_policy_sha256",
            "decision_adapter_code_sha256",
            "receipt_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        _require_identifier(self.measurement_revision, "measurement_revision")
        _require_identifier(self.decision_policy_revision, "decision_policy_revision")
        if self.observed_offset_ms is not None and (
            isinstance(self.observed_offset_ms, bool)
            or not isinstance(self.observed_offset_ms, int)
        ):
            raise ValueError("observed_offset_ms must be an integer or null")
        if self.observed_correlation is not None:
            _require_finite_range(
                self.observed_correlation,
                "observed_correlation",
                -1.0,
                1.0,
            )
        if any(not isinstance(anchor, AvSyncAnchorCandidate) for anchor in self.observed_anchors):
            raise ValueError("observed_anchors must contain typed anchors")
        if self.status not in {"synchronized", "failed"}:
            raise ValueError("runtime A/V sync decision status differs")
        _require_reason_codes(self.reason_codes, _AV_SYNC_FAILURE_REASONS)
        if (self.status == "synchronized") != (not self.reason_codes):
            raise ValueError("runtime A/V sync status and reasons differ")
        if self.status == "synchronized" and (
            self.observed_offset_ms is None
            or self.observed_correlation is None
            or not self.observed_anchors
        ):
            raise ValueError("synchronized decision requires observed values")
        if self.receipt_sha256 != _sha256_document(self.unsigned_json_object()):
            raise ValueError("runtime A/V sync decision digest differs")

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_id_sha256": self.attempt_id_sha256,
            "binding_receipt_sha256": self.binding_receipt_sha256,
            "audio_source_sha256": self.audio_source_sha256,
            "video_source_sha256": self.video_source_sha256,
            "landmark_evidence_sha256": self.landmark_evidence_sha256,
            "landmark_model_sha256": self.landmark_model_sha256,
            "source_candidate_receipt_sha256": (
                self.source_candidate_receipt_sha256
            ),
            "source_candidate_lineage_receipt_sha256": (
                self.source_candidate_lineage_receipt_sha256
            ),
            "measurement_revision": self.measurement_revision,
            "measurement_policy_sha256": self.measurement_policy_sha256,
            "measurement_adapter_code_sha256": (
                self.measurement_adapter_code_sha256
            ),
            "observed_offset_ms": self.observed_offset_ms,
            "observed_correlation": self.observed_correlation,
            "observed_anchors": [
                anchor.to_json_object() for anchor in self.observed_anchors
            ],
            "decision_policy_revision": self.decision_policy_revision,
            "decision_policy_sha256": self.decision_policy_sha256,
            "decision_adapter_code_sha256": self.decision_adapter_code_sha256,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}


class CalibratedVideoQcDecisionAdapter:
    """Apply one frozen QC policy to one exact typed measurement candidate."""

    def __init__(self, *, policy: CalibratedVideoQcDecisionPolicy) -> None:
        self._policy = policy

    def decide(
        self,
        *,
        candidate: VideoQcCandidateEvidence,
        binding_receipt: SameAttemptBindingReceipt,
    ) -> RuntimeVideoQcDecision:
        if not isinstance(candidate, VideoQcCandidateEvidence) or not isinstance(
            binding_receipt, SameAttemptBindingReceipt
        ):
            raise ValueError("video QC decision requires typed lineage")
        if (
            candidate.container_sha256 != binding_receipt.video_source_sha256
            or candidate.policy_receipt_sha256
            != self._policy.expected_measurement_policy_sha256
        ):
            raise ValueError("video QC candidate lineage differs from policy")

        reasons = self._reason_codes(candidate)
        status = "passed" if not reasons else "failed"
        unsigned = {
            "schema_version": RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION,
            "attempt_id_sha256": binding_receipt.attempt_id_sha256,
            "binding_receipt_sha256": binding_receipt.receipt_sha256,
            "container_sha256": candidate.container_sha256,
            "landmark_evidence_sha256": candidate.landmark_evidence_sha256,
            "landmark_model_sha256": candidate.landmark_model_sha256,
            "source_candidate_receipt_sha256": candidate.receipt_sha256,
            "source_candidate_provenance_receipt_sha256": (
                candidate.provenance_receipt_sha256
            ),
            "measurement_revision": candidate.measurement_revision,
            "measurement_policy_sha256": candidate.policy_receipt_sha256,
            "measurement_adapter_code_sha256": (
                candidate.measurement_adapter_code_sha256
            ),
            "observed_metrics": candidate.metrics.to_json_object(),
            "decision_policy_revision": self._policy.policy_revision,
            "decision_policy_sha256": self._policy.policy_sha256,
            "decision_adapter_code_sha256": decision_adapter_code_sha256(),
            "status": status,
            "reason_codes": list(reasons),
        }
        return RuntimeVideoQcDecision(
            attempt_id_sha256=binding_receipt.attempt_id_sha256,
            binding_receipt_sha256=binding_receipt.receipt_sha256,
            container_sha256=candidate.container_sha256,
            landmark_evidence_sha256=candidate.landmark_evidence_sha256,
            landmark_model_sha256=candidate.landmark_model_sha256,
            source_candidate_receipt_sha256=candidate.receipt_sha256,
            source_candidate_provenance_receipt_sha256=(
                candidate.provenance_receipt_sha256
            ),
            measurement_revision=candidate.measurement_revision,
            measurement_policy_sha256=candidate.policy_receipt_sha256,
            measurement_adapter_code_sha256=(
                candidate.measurement_adapter_code_sha256
            ),
            observed_metrics=candidate.metrics,
            decision_policy_revision=self._policy.policy_revision,
            decision_policy_sha256=self._policy.policy_sha256,
            decision_adapter_code_sha256=decision_adapter_code_sha256(),
            status=status,
            reason_codes=reasons,
            receipt_sha256=_sha256_document(unsigned),
        )

    def _reason_codes(
        self, candidate: VideoQcCandidateEvidence
    ) -> tuple[str, ...]:
        if candidate.status != "candidate_unvalidated":
            return ("video_qc_source_candidate_unavailable",)
        metrics = candidate.metrics
        checks = (
            (
                metrics.single_face_coverage
                < self._policy.minimum_single_face_coverage,
                "video_qc_single_face_coverage_below_policy",
            ),
            (
                metrics.multiple_face_frame_fraction
                > self._policy.maximum_multiple_face_frame_fraction,
                "video_qc_multiple_face_fraction_above_policy",
            ),
            (
                metrics.mean_luma_8bit < self._policy.minimum_mean_luma_8bit,
                "video_qc_mean_luma_below_policy",
            ),
            (
                metrics.mean_luma_8bit > self._policy.maximum_mean_luma_8bit,
                "video_qc_mean_luma_above_policy",
            ),
            (
                metrics.underexposed_pixel_fraction
                > self._policy.maximum_underexposed_pixel_fraction,
                "video_qc_underexposed_fraction_above_policy",
            ),
            (
                metrics.overexposed_pixel_fraction
                > self._policy.maximum_overexposed_pixel_fraction,
                "video_qc_overexposed_fraction_above_policy",
            ),
            (
                metrics.lip_roi_mean_neighbor_luma_delta_8bit is None
                or metrics.lip_roi_mean_neighbor_luma_delta_8bit
                < self._policy.minimum_lip_roi_mean_neighbor_luma_delta_8bit,
                "video_qc_lip_edge_delta_below_policy",
            ),
            (
                metrics.track_transition_coverage
                < self._policy.minimum_track_transition_coverage,
                "video_qc_track_transition_coverage_below_policy",
            ),
            (
                metrics.longest_single_face_run_fraction
                < self._policy.minimum_longest_single_face_run_fraction,
                "video_qc_single_face_run_below_policy",
            ),
            (
                metrics.maximum_lip_center_step_normalized is None
                or metrics.maximum_lip_center_step_normalized
                > self._policy.maximum_lip_center_step_normalized,
                "video_qc_lip_center_step_above_policy",
            ),
        )
        return tuple(reason for failed, reason in checks if failed)


class CalibratedAvSyncDecisionAdapter:
    """Apply one frozen sync policy to one exact typed measurement candidate."""

    def __init__(self, *, policy: CalibratedAvSyncDecisionPolicy) -> None:
        self._policy = policy

    def decide(
        self,
        *,
        candidate: AvSyncCandidateEvidence,
        binding_receipt: SameAttemptBindingReceipt,
    ) -> RuntimeAvSyncDecision:
        if not isinstance(candidate, AvSyncCandidateEvidence) or not isinstance(
            binding_receipt, SameAttemptBindingReceipt
        ):
            raise ValueError("A/V sync decision requires typed lineage")
        if (
            candidate.audio_source_sha256 != binding_receipt.audio_source_sha256
            or candidate.video_source_sha256 != binding_receipt.video_source_sha256
            or candidate.measurement_policy_sha256
            != self._policy.expected_measurement_policy_sha256
        ):
            raise ValueError("A/V sync candidate lineage differs from policy")

        reasons = self._reason_codes(candidate)
        status = "synchronized" if not reasons else "failed"
        unsigned = {
            "schema_version": RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION,
            "attempt_id_sha256": binding_receipt.attempt_id_sha256,
            "binding_receipt_sha256": binding_receipt.receipt_sha256,
            "audio_source_sha256": candidate.audio_source_sha256,
            "video_source_sha256": candidate.video_source_sha256,
            "landmark_evidence_sha256": candidate.landmark_evidence_sha256,
            "landmark_model_sha256": candidate.landmark_model_sha256,
            "source_candidate_receipt_sha256": candidate.receipt_sha256,
            "source_candidate_lineage_receipt_sha256": (
                candidate.lineage_receipt_sha256
            ),
            "measurement_revision": candidate.measurement_revision,
            "measurement_policy_sha256": candidate.measurement_policy_sha256,
            "measurement_adapter_code_sha256": (
                candidate.measurement_adapter_code_sha256
            ),
            "observed_offset_ms": candidate.candidate_offset_ms,
            "observed_correlation": candidate.correlation,
            "observed_anchors": [
                anchor.to_json_object() for anchor in candidate.anchors
            ],
            "decision_policy_revision": self._policy.policy_revision,
            "decision_policy_sha256": self._policy.policy_sha256,
            "decision_adapter_code_sha256": decision_adapter_code_sha256(),
            "status": status,
            "reason_codes": list(reasons),
        }
        return RuntimeAvSyncDecision(
            attempt_id_sha256=binding_receipt.attempt_id_sha256,
            binding_receipt_sha256=binding_receipt.receipt_sha256,
            audio_source_sha256=candidate.audio_source_sha256,
            video_source_sha256=candidate.video_source_sha256,
            landmark_evidence_sha256=candidate.landmark_evidence_sha256,
            landmark_model_sha256=candidate.landmark_model_sha256,
            source_candidate_receipt_sha256=candidate.receipt_sha256,
            source_candidate_lineage_receipt_sha256=(
                candidate.lineage_receipt_sha256
            ),
            measurement_revision=candidate.measurement_revision,
            measurement_policy_sha256=candidate.measurement_policy_sha256,
            measurement_adapter_code_sha256=(
                candidate.measurement_adapter_code_sha256
            ),
            observed_offset_ms=candidate.candidate_offset_ms,
            observed_correlation=candidate.correlation,
            observed_anchors=candidate.anchors,
            decision_policy_revision=self._policy.policy_revision,
            decision_policy_sha256=self._policy.policy_sha256,
            decision_adapter_code_sha256=decision_adapter_code_sha256(),
            status=status,
            reason_codes=reasons,
            receipt_sha256=_sha256_document(unsigned),
        )

    def _reason_codes(
        self, candidate: AvSyncCandidateEvidence
    ) -> tuple[str, ...]:
        if candidate.status != "candidate_unvalidated":
            return ("av_sync_source_candidate_unavailable",)
        offset = candidate.candidate_offset_ms
        correlation = candidate.correlation
        checks = (
            (
                offset is None
                or abs(offset) > self._policy.maximum_absolute_offset_ms,
                "av_sync_offset_outside_policy",
            ),
            (
                correlation is None
                or correlation < self._policy.minimum_correlation,
                "av_sync_correlation_below_policy",
            ),
            (
                len(candidate.anchors) < self._policy.minimum_anchor_count,
                "av_sync_anchor_count_below_policy",
            ),
            (
                not candidate.anchors
                or max(anchor.strength for anchor in candidate.anchors)
                < self._policy.minimum_anchor_strength,
                "av_sync_anchor_strength_below_policy",
            ),
        )
        return tuple(reason for failed, reason in checks if failed)


def decision_adapter_code_sha256() -> str:
    """Hash the calibrated wrapper source, never a caller-provided value."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise ValueError("calibrated decision adapter source is unavailable") from None


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _require_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _require_finite_range(
    value: object,
    name: str,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise ValueError(f"{name} must be finite in [{minimum}, {maximum}]")
    return float(value)


def _require_reason_codes(
    values: tuple[str, ...], allowlist: tuple[str, ...]
) -> None:
    if (
        not isinstance(values, tuple)
        or len(set(values)) != len(values)
        or any(value not in allowlist for value in values)
    ):
        raise ValueError("decision reason codes differ")


def _sha256_document(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CalibratedAvSyncDecisionAdapter",
    "CalibratedAvSyncDecisionPolicy",
    "CalibratedVideoQcDecisionAdapter",
    "CalibratedVideoQcDecisionPolicy",
    "RUNTIME_AV_SYNC_DECISION_SCHEMA_VERSION",
    "RUNTIME_VIDEO_QC_DECISION_SCHEMA_VERSION",
    "RuntimeAvSyncDecision",
    "RuntimeVideoQcDecision",
    "decision_adapter_code_sha256",
]
