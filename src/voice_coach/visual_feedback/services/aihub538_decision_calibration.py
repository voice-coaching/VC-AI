"""Calibrate runtime QC/sync decisions on non-locked AI Hub 538 media.

This service owns only deterministic selection, pure threshold mathematics,
typed measurement-to-decision lineage, and aggregate stop gates.  Archive,
ffmpeg, tracker, PCM, filesystem, and subprocess work stay behind injected
ports/adapters.  A failed gate raises a stable reason before any caller can
publish ``RuntimeDecisionPolicyEvidence``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
from typing import Protocol

from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionAdapter,
    CalibratedVideoQcDecisionAdapter,
    RuntimeAvSyncDecision,
    RuntimeVideoQcDecision,
)
from ...pronunciation_attempts.adapters.lip_audio_av_sync import (
    AvSyncCandidateEvidence,
)
from ...pronunciation_attempts.adapters.video_qc import VideoQcCandidateEvidence
from ..errors import VisualFeedbackContractError
from ..models.aihub538_decision_calibration import (
    Aihub538CalibrationAngleEvidence,
    Aihub538DecisionCalibrationEvidence,
    Aihub538DecisionCalibrationProtocol,
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    Aihub538TrainingAdmissionReceipt,
)
from ..models.binding import SameAttemptBindingReceipt
from ..models.integrity import require_sha256, sha256_document
from ..models.runtime_policy_artifact import (
    AvSyncDecisionSemanticPolicy,
    AvSyncSemanticPolicy,
    P8RuntimePolicyArtifact,
    VideoQcDecisionSemanticPolicy,
    VideoQcSemanticPolicy,
)


_ACTOR_SPLIT_DOMAIN = b"voice_coach.aihub538.actor_split.v1\x00"
_SELECTION_DOMAIN = b"voice_coach.aihub538.angle_selection.v1\x00"


class Aihub538DecisionCalibrationError(RuntimeError):
    """Stable aggregate-only failure that never embeds a restricted value."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationMediaItem:
    """Restricted in-process inventory row; identity tokens are never serialized."""

    sample_sha256: str
    actor_token_sha256: str = field(repr=False)
    attempt_token_sha256: str = field(repr=False)
    angle: str

    def __post_init__(self) -> None:
        require_sha256(self.sample_sha256, "sample_sha256")
        require_sha256(self.actor_token_sha256, "actor_token_sha256")
        require_sha256(self.attempt_token_sha256, "attempt_token_sha256")
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise VisualFeedbackContractError("calibration media angle differs")


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationMeasurement:
    """Typed candidates and binding returned from one actual-media execution."""

    item: Aihub538CalibrationMediaItem
    binding_receipt: SameAttemptBindingReceipt
    video_qc_candidate: VideoQcCandidateEvidence
    av_sync_candidate: AvSyncCandidateEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.item, Aihub538CalibrationMediaItem):
            raise VisualFeedbackContractError("calibration measurement item differs")
        if not isinstance(self.binding_receipt, SameAttemptBindingReceipt):
            raise VisualFeedbackContractError("calibration binding type differs")
        if not isinstance(self.video_qc_candidate, VideoQcCandidateEvidence):
            raise VisualFeedbackContractError("calibration QC candidate type differs")
        if not isinstance(self.av_sync_candidate, AvSyncCandidateEvidence):
            raise VisualFeedbackContractError("calibration sync candidate type differs")
        binding = self.binding_receipt
        qc = self.video_qc_candidate
        sync = self.av_sync_candidate
        if (
            binding.attempt_id_sha256 != self.item.attempt_token_sha256
            or binding.video_source_sha256 != qc.container_sha256
            or binding.video_source_sha256 != sync.video_source_sha256
            or binding.audio_source_sha256 != sync.audio_source_sha256
            or binding.audio_derivation_receipt_sha256
            != sync.audio_derivation_receipt_sha256
            or qc.landmark_evidence_sha256 != sync.landmark_evidence_sha256
            or qc.landmark_model_sha256 != sync.landmark_model_sha256
        ):
            raise VisualFeedbackContractError(
                "actual-media measurement lineage differs"
            )


class Aihub538CalibrationMeasurementPort(Protocol):
    """Measure one selected real TS174/TL174 item behind a restricted adapter."""

    def measure(
        self, item: Aihub538CalibrationMediaItem
    ) -> Aihub538CalibrationMeasurement: ...


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationSplit:
    """Private deterministic split; tokens remain inside the service process."""

    development: tuple[Aihub538CalibrationMediaItem, ...]
    calibration: tuple[Aihub538CalibrationMediaItem, ...]
    eligible_item_count: int
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    source_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class Aihub538DecisionCalibrationResult:
    """In-memory policies and aggregate evidence ready for atomic publication."""

    video_qc_decision_policy: P8RuntimePolicyArtifact
    av_sync_decision_policy: P8RuntimePolicyArtifact
    calibration_evidence: Aihub538DecisionCalibrationEvidence


class Aihub538DecisionCalibrationService:
    """Execute predeclared selection, derivation, calibration, and stop gates."""

    def __init__(
        self,
        *,
        measurement_port: Aihub538CalibrationMeasurementPort,
        protocol: Aihub538DecisionCalibrationProtocol = (
            DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL
        ),
    ) -> None:
        self._measurement_port = measurement_port
        self._protocol = protocol

    def calibrate(
        self,
        *,
        items: tuple[Aihub538CalibrationMediaItem, ...],
        admission_receipt: Aihub538TrainingAdmissionReceipt,
        video_qc_measurement_policy: P8RuntimePolicyArtifact,
        av_sync_measurement_policy: P8RuntimePolicyArtifact,
    ) -> Aihub538DecisionCalibrationResult:
        """Calibrate from dev and validate on actor-disjoint calibration media."""

        _validate_measurement_policy_artifacts(
            video_qc=video_qc_measurement_policy,
            av_sync=av_sync_measurement_policy,
        )
        split = build_aihub538_calibration_split(
            items=items,
            admission_receipt=admission_receipt,
            protocol=self._protocol,
        )
        development = self._measure_all(split.development)
        _require_development_availability(development, self._protocol)
        video_qc_decision_policy, av_sync_decision_policy = _derive_policies(
            development=development,
            protocol=self._protocol,
            video_qc_measurement_policy=video_qc_measurement_policy,
            av_sync_measurement_policy=av_sync_measurement_policy,
        )

        calibration = self._measure_all(split.calibration)
        decisions = _decide_all(
            calibration=calibration,
            video_qc_decision_policy=video_qc_decision_policy,
            av_sync_decision_policy=av_sync_decision_policy,
        )
        angle_evidence = _angle_evidence(
            development=development,
            calibration=calibration,
            decisions=decisions,
        )
        _require_calibration_stop_policy(angle_evidence, self._protocol)

        evidence = Aihub538DecisionCalibrationEvidence.issue(
            source_manifest_sha256=split.source_manifest_sha256,
            admission_receipt_sha256=admission_receipt.receipt_sha256,
            identity_namespace_commitment_sha256=(
                admission_receipt.identity_namespace_commitment_sha256
            ),
            evaluation_protocol_sha256=self._protocol.protocol_sha256,
            evaluator_code_sha256=decision_calibration_evaluator_code_sha256(),
            video_qc_measurement_policy_artifact_sha256=(
                video_qc_measurement_policy.artifact_sha256
            ),
            video_qc_measurement_policy_sha256=(
                video_qc_measurement_policy.policy_sha256
            ),
            av_sync_measurement_policy_artifact_sha256=(
                av_sync_measurement_policy.artifact_sha256
            ),
            av_sync_measurement_policy_sha256=(
                av_sync_measurement_policy.policy_sha256
            ),
            video_qc_decision_policy_artifact_sha256=(
                video_qc_decision_policy.artifact_sha256
            ),
            video_qc_decision_policy_sha256=video_qc_decision_policy.policy_sha256,
            av_sync_decision_policy_artifact_sha256=(
                av_sync_decision_policy.artifact_sha256
            ),
            av_sync_decision_policy_sha256=av_sync_decision_policy.policy_sha256,
            eligible_item_count=split.eligible_item_count,
            development_selected_count=len(split.development),
            calibration_selected_count=len(split.calibration),
            development_actor_count=split.development_actor_count,
            calibration_actor_count=split.calibration_actor_count,
            actor_overlap_count=split.actor_overlap_count,
            attempt_overlap_count=split.attempt_overlap_count,
            qc_decision_lineage_valid_item_count=sum(
                item.qc_lineage_valid for item in decisions
            ),
            sync_decision_lineage_valid_item_count=sum(
                item.sync_lineage_valid for item in decisions
            ),
            angle_evidence=angle_evidence,
        )
        return Aihub538DecisionCalibrationResult(
            video_qc_decision_policy=video_qc_decision_policy,
            av_sync_decision_policy=av_sync_decision_policy,
            calibration_evidence=evidence,
        )

    def _measure_all(
        self, items: tuple[Aihub538CalibrationMediaItem, ...]
    ) -> tuple[Aihub538CalibrationMeasurement, ...]:
        measured: list[Aihub538CalibrationMeasurement] = []
        for item in items:
            try:
                measurement = self._measurement_port.measure(item)
            except Aihub538DecisionCalibrationError:
                raise
            except Exception:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_actual_media_measurement_failed"
                ) from None
            if measurement.item != item:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_measurement_item_binding_mismatch"
                )
            measured.append(measurement)
        return tuple(measured)


def build_aihub538_calibration_split(
    *,
    items: tuple[Aihub538CalibrationMediaItem, ...],
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    protocol: Aihub538DecisionCalibrationProtocol,
) -> Aihub538CalibrationSplit:
    """Select exact per-angle quotas after deterministic actor partitioning."""

    if not items:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_inventory_empty"
        )
    if not isinstance(admission_receipt, Aihub538TrainingAdmissionReceipt):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_admission_receipt_invalid"
        )
    sample_digests = [item.sample_sha256 for item in items]
    if len(set(sample_digests)) != len(sample_digests):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_sample_duplicate"
        )
    expected_selection_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_training_sample_selection.v1",
            "selection_rule": "all_exactly_admitted_ts174_tl174_pairs",
            "admission_policy_sha256": AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
            "sample_sha256": sorted(sample_digests),
        }
    )
    if expected_selection_sha256 != admission_receipt.sample_selection_sha256:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_inventory_admission_mismatch"
        )

    attempt_actors: dict[str, str] = {}
    partitions: dict[str, list[Aihub538CalibrationMediaItem]] = {
        "development": [],
        "calibration": [],
    }
    for item in items:
        actor = attempt_actors.setdefault(
            item.attempt_token_sha256, item.actor_token_sha256
        )
        if actor != item.actor_token_sha256:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_attempt_actor_mismatch"
            )
        partition = _actor_partition(
            actor_token_sha256=item.actor_token_sha256,
            protocol_sha256=protocol.protocol_sha256,
        )
        partitions[partition].append(item)

    selected = {
        partition: _balanced_angle_selection(
            items=tuple(partition_items),
            partition=partition,
            quota=protocol.samples_per_angle_per_partition,
            protocol_sha256=protocol.protocol_sha256,
        )
        for partition, partition_items in partitions.items()
    }
    development = selected["development"]
    calibration = selected["calibration"]
    development_actors = {item.actor_token_sha256 for item in development}
    calibration_actors = {item.actor_token_sha256 for item in calibration}
    development_attempts = {item.attempt_token_sha256 for item in development}
    calibration_attempts = {item.attempt_token_sha256 for item in calibration}
    actor_overlap = len(development_actors & calibration_actors)
    attempt_overlap = len(development_attempts & calibration_attempts)
    if actor_overlap or attempt_overlap:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_partition_overlap_detected"
        )

    source_manifest_sha256 = sha256_document(
        {
            "schema_version": "voice_coach.aihub538_calibration_source_manifest.v1",
            "admission_receipt_sha256": admission_receipt.receipt_sha256,
            "admission_manifest_sha256": admission_receipt.admission_manifest_sha256,
            "identity_namespace_commitment_sha256": (
                admission_receipt.identity_namespace_commitment_sha256
            ),
            "evaluation_protocol_sha256": protocol.protocol_sha256,
            "eligible_item_count": len(items),
            "development_selection_root_sha256": _selection_root(development),
            "calibration_selection_root_sha256": _selection_root(calibration),
            "development_selected_count": len(development),
            "calibration_selected_count": len(calibration),
            "development_actor_count": len(development_actors),
            "calibration_actor_count": len(calibration_actors),
            "actor_overlap_count": actor_overlap,
            "attempt_overlap_count": attempt_overlap,
            "angles": list(AIHUB538_TRAINING_ANGLES),
            "individual_identifiers_serialized": False,
        }
    )
    return Aihub538CalibrationSplit(
        development=development,
        calibration=calibration,
        eligible_item_count=len(items),
        development_actor_count=len(development_actors),
        calibration_actor_count=len(calibration_actors),
        actor_overlap_count=actor_overlap,
        attempt_overlap_count=attempt_overlap,
        source_manifest_sha256=source_manifest_sha256,
    )


@dataclass(frozen=True, slots=True)
class _CalibrationDecisionPair:
    measurement: Aihub538CalibrationMeasurement
    qc_decision: RuntimeVideoQcDecision
    sync_decision: RuntimeAvSyncDecision
    qc_lineage_valid: int
    sync_lineage_valid: int


def _actor_partition(*, actor_token_sha256: str, protocol_sha256: str) -> str:
    digest = hashlib.sha256(
        _ACTOR_SPLIT_DOMAIN
        + bytes.fromhex(protocol_sha256)
        + bytes.fromhex(actor_token_sha256)
    ).digest()
    return "development" if digest[0] & 1 == 0 else "calibration"


def _balanced_angle_selection(
    *,
    items: tuple[Aihub538CalibrationMediaItem, ...],
    partition: str,
    quota: int,
    protocol_sha256: str,
) -> tuple[Aihub538CalibrationMediaItem, ...]:
    selected: list[Aihub538CalibrationMediaItem] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        candidates = [item for item in items if item.angle == angle]
        candidates.sort(
            key=lambda item: _selection_score(
                item=item,
                partition=partition,
                protocol_sha256=protocol_sha256,
            )
        )
        if len(candidates) < quota:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_angle_quota_unavailable"
            )
        selected.extend(candidates[:quota])
    return tuple(selected)


def _selection_score(
    *,
    item: Aihub538CalibrationMediaItem,
    partition: str,
    protocol_sha256: str,
) -> str:
    return hashlib.sha256(
        _SELECTION_DOMAIN
        + partition.encode("ascii")
        + b"\x00"
        + item.angle.encode("ascii")
        + b"\x00"
        + bytes.fromhex(protocol_sha256)
        + bytes.fromhex(item.sample_sha256)
    ).hexdigest()


def _selection_root(items: tuple[Aihub538CalibrationMediaItem, ...]) -> str:
    return sha256_document(
        {
            "schema_version": "voice_coach.aihub538_private_selection_root.v1",
            "sample_sha256": sorted(item.sample_sha256 for item in items),
        }
    )


def _validate_measurement_policy_artifacts(
    *, video_qc: P8RuntimePolicyArtifact, av_sync: P8RuntimePolicyArtifact
) -> None:
    if (
        not isinstance(video_qc, P8RuntimePolicyArtifact)
        or video_qc.policy_kind != "video_qc"
        or not isinstance(video_qc.policy_document, VideoQcSemanticPolicy)
        or not isinstance(av_sync, P8RuntimePolicyArtifact)
        or av_sync.policy_kind != "av_sync"
        or not isinstance(av_sync.policy_document, AvSyncSemanticPolicy)
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_measurement_policy_invalid"
        )


def _require_development_availability(
    measurements: tuple[Aihub538CalibrationMeasurement, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
) -> None:
    for angle in AIHUB538_TRAINING_ANGLES:
        rows = tuple(item for item in measurements if item.item.angle == angle)
        qc_available = sum(_usable_qc(item.video_qc_candidate) for item in rows)
        sync_available = sum(
            item.av_sync_candidate.status == "candidate_unvalidated" for item in rows
        )
        if (
            qc_available < protocol.minimum_available_per_angle
            or sync_available < protocol.minimum_available_per_angle
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_development_candidate_availability_below_stop_policy"
            )


def _derive_policies(
    *,
    development: tuple[Aihub538CalibrationMeasurement, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
    video_qc_measurement_policy: P8RuntimePolicyArtifact,
    av_sync_measurement_policy: P8RuntimePolicyArtifact,
) -> tuple[P8RuntimePolicyArtifact, P8RuntimePolicyArtifact]:
    qc_rows = [
        item.video_qc_candidate.metrics
        for item in development
        if _usable_qc(item.video_qc_candidate)
    ]
    sync_rows = [
        item.av_sync_candidate
        for item in development
        if item.av_sync_candidate.status == "candidate_unvalidated"
    ]
    if not qc_rows or not sync_rows:
        raise Aihub538DecisionCalibrationError(
            "aihub538_development_candidates_empty"
        )

    lower = protocol.lower_quantile
    upper = protocol.upper_quantile
    fraction_margin = protocol.qc_fraction_margin
    qc_document = VideoQcDecisionSemanticPolicy(
        policy_revision=f"{protocol.protocol_revision}.video_qc",
        expected_measurement_policy_sha256=(
            video_qc_measurement_policy.policy_sha256
        ),
        minimum_single_face_coverage=_rounded_clamp(
            _quantile([row.single_face_coverage for row in qc_rows], lower)
            - fraction_margin,
            0.0,
            1.0,
        ),
        maximum_multiple_face_frame_fraction=_rounded_clamp(
            _quantile(
                [row.multiple_face_frame_fraction for row in qc_rows], upper
            )
            + fraction_margin,
            0.0,
            1.0,
        ),
        minimum_mean_luma_8bit=_rounded_clamp(
            _quantile([row.mean_luma_8bit for row in qc_rows], lower)
            - protocol.qc_luma_margin_8bit,
            0.0,
            255.0,
        ),
        maximum_mean_luma_8bit=_rounded_clamp(
            _quantile([row.mean_luma_8bit for row in qc_rows], upper)
            + protocol.qc_luma_margin_8bit,
            0.0,
            255.0,
        ),
        maximum_underexposed_pixel_fraction=_rounded_clamp(
            _quantile(
                [row.underexposed_pixel_fraction for row in qc_rows], upper
            )
            + fraction_margin,
            0.0,
            1.0,
        ),
        maximum_overexposed_pixel_fraction=_rounded_clamp(
            _quantile(
                [row.overexposed_pixel_fraction for row in qc_rows], upper
            )
            + fraction_margin,
            0.0,
            1.0,
        ),
        minimum_lip_roi_mean_neighbor_luma_delta_8bit=_rounded_clamp(
            _quantile(
                [
                    _present(row.lip_roi_mean_neighbor_luma_delta_8bit)
                    for row in qc_rows
                ],
                lower,
            )
            - protocol.qc_edge_margin_8bit,
            0.0,
            255.0,
        ),
        minimum_track_transition_coverage=_rounded_clamp(
            _quantile([row.track_transition_coverage for row in qc_rows], lower)
            - fraction_margin,
            0.0,
            1.0,
        ),
        minimum_longest_single_face_run_fraction=_rounded_clamp(
            _quantile(
                [row.longest_single_face_run_fraction for row in qc_rows], lower
            )
            - fraction_margin,
            0.0,
            1.0,
        ),
        maximum_lip_center_step_normalized=_rounded_clamp(
            _quantile(
                [_present(row.maximum_lip_center_step_normalized) for row in qc_rows],
                upper,
            )
            + protocol.qc_center_step_margin,
            0.0,
            math.sqrt(2.0),
        ),
    )
    measurement_sync = av_sync_measurement_policy.policy_document
    assert isinstance(measurement_sync, AvSyncSemanticPolicy)
    maximum_offset = math.ceil(
        (
            _quantile(
                [abs(_present_int(row.candidate_offset_ms)) for row in sync_rows],
                upper,
            )
            + protocol.sync_offset_margin_ms
        )
        / measurement_sync.offset_step_ms
    ) * measurement_sync.offset_step_ms
    sync_document = AvSyncDecisionSemanticPolicy(
        policy_revision=f"{protocol.protocol_revision}.av_sync",
        expected_measurement_policy_sha256=av_sync_measurement_policy.policy_sha256,
        maximum_absolute_offset_ms=max(
            1,
            min(measurement_sync.maximum_absolute_offset_ms, maximum_offset),
        ),
        minimum_correlation=_rounded_clamp(
            _quantile([_present(row.correlation) for row in sync_rows], lower)
            - protocol.sync_correlation_margin,
            -1.0,
            1.0,
        ),
        minimum_anchor_count=max(
            1,
            min(
                measurement_sync.maximum_anchor_count,
                math.floor(
                    _quantile([len(row.anchors) for row in sync_rows], lower)
                ),
            ),
        ),
        minimum_anchor_strength=_rounded_clamp(
            _quantile(
                [max(anchor.strength for anchor in row.anchors) for row in sync_rows],
                lower,
            )
            - protocol.sync_anchor_strength_margin,
            0.0,
            1.0,
        ),
    )
    return (
        P8RuntimePolicyArtifact.issue(
            policy_kind="video_qc_decision",
            policy_revision=qc_document.policy_revision,
            policy_document=qc_document,
        ),
        P8RuntimePolicyArtifact.issue(
            policy_kind="av_sync_decision",
            policy_revision=sync_document.policy_revision,
            policy_document=sync_document,
        ),
    )


def _decide_all(
    *,
    calibration: tuple[Aihub538CalibrationMeasurement, ...],
    video_qc_decision_policy: P8RuntimePolicyArtifact,
    av_sync_decision_policy: P8RuntimePolicyArtifact,
) -> tuple[_CalibrationDecisionPair, ...]:
    qc_runtime_policy = video_qc_decision_policy.policy_document.to_runtime_policy()
    sync_runtime_policy = av_sync_decision_policy.policy_document.to_runtime_policy()
    qc_adapter = CalibratedVideoQcDecisionAdapter(policy=qc_runtime_policy)
    sync_adapter = CalibratedAvSyncDecisionAdapter(policy=sync_runtime_policy)
    decisions: list[_CalibrationDecisionPair] = []
    for measurement in calibration:
        qc = qc_adapter.decide(
            candidate=measurement.video_qc_candidate,
            binding_receipt=measurement.binding_receipt,
        )
        sync = sync_adapter.decide(
            candidate=measurement.av_sync_candidate,
            binding_receipt=measurement.binding_receipt,
        )
        qc_lineage_valid = int(_qc_lineage_valid(measurement, qc))
        sync_lineage_valid = int(_sync_lineage_valid(measurement, sync))
        if not qc_lineage_valid or not sync_lineage_valid:
            raise Aihub538DecisionCalibrationError(
                "aihub538_measurement_decision_lineage_invalid"
            )
        decisions.append(
            _CalibrationDecisionPair(
                measurement=measurement,
                qc_decision=qc,
                sync_decision=sync,
                qc_lineage_valid=qc_lineage_valid,
                sync_lineage_valid=sync_lineage_valid,
            )
        )
    return tuple(decisions)


def _qc_lineage_valid(
    measurement: Aihub538CalibrationMeasurement,
    decision: RuntimeVideoQcDecision,
) -> bool:
    candidate = measurement.video_qc_candidate
    binding = measurement.binding_receipt
    return (
        decision.attempt_id_sha256 == binding.attempt_id_sha256
        and decision.binding_receipt_sha256 == binding.receipt_sha256
        and decision.container_sha256 == candidate.container_sha256
        and decision.source_candidate_receipt_sha256 == candidate.receipt_sha256
        and decision.source_candidate_provenance_receipt_sha256
        == candidate.provenance_receipt_sha256
        and decision.measurement_policy_sha256 == candidate.policy_receipt_sha256
        and decision.measurement_revision == candidate.measurement_revision
        and decision.measurement_adapter_code_sha256
        == candidate.measurement_adapter_code_sha256
        and decision.observed_metrics == candidate.metrics
    )


def _sync_lineage_valid(
    measurement: Aihub538CalibrationMeasurement,
    decision: RuntimeAvSyncDecision,
) -> bool:
    candidate = measurement.av_sync_candidate
    binding = measurement.binding_receipt
    return (
        decision.attempt_id_sha256 == binding.attempt_id_sha256
        and decision.binding_receipt_sha256 == binding.receipt_sha256
        and decision.audio_source_sha256 == candidate.audio_source_sha256
        and decision.video_source_sha256 == candidate.video_source_sha256
        and decision.source_candidate_receipt_sha256 == candidate.receipt_sha256
        and decision.source_candidate_lineage_receipt_sha256
        == candidate.lineage_receipt_sha256
        and decision.measurement_policy_sha256
        == candidate.measurement_policy_sha256
        and decision.measurement_revision == candidate.measurement_revision
        and decision.measurement_adapter_code_sha256
        == candidate.measurement_adapter_code_sha256
        and decision.observed_offset_ms == candidate.candidate_offset_ms
        and decision.observed_correlation == candidate.correlation
        and decision.observed_anchors == candidate.anchors
    )


def _angle_evidence(
    *,
    development: tuple[Aihub538CalibrationMeasurement, ...],
    calibration: tuple[Aihub538CalibrationMeasurement, ...],
    decisions: tuple[_CalibrationDecisionPair, ...],
) -> tuple[Aihub538CalibrationAngleEvidence, ...]:
    result: list[Aihub538CalibrationAngleEvidence] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        dev = tuple(item for item in development if item.item.angle == angle)
        cal = tuple(item for item in calibration if item.item.angle == angle)
        angle_decisions = tuple(
            item for item in decisions if item.measurement.item.angle == angle
        )
        result.append(
            Aihub538CalibrationAngleEvidence(
                angle=angle,
                development_selected_count=len(dev),
                calibration_selected_count=len(cal),
                development_qc_available_count=sum(
                    _usable_qc(item.video_qc_candidate) for item in dev
                ),
                development_sync_available_count=sum(
                    item.av_sync_candidate.status == "candidate_unvalidated"
                    for item in dev
                ),
                calibration_qc_available_count=sum(
                    _usable_qc(item.video_qc_candidate) for item in cal
                ),
                calibration_sync_available_count=sum(
                    item.av_sync_candidate.status == "candidate_unvalidated"
                    for item in cal
                ),
                calibration_qc_passed_count=sum(
                    item.qc_decision.status == "passed" for item in angle_decisions
                ),
                calibration_sync_passed_count=sum(
                    item.sync_decision.status == "synchronized"
                    for item in angle_decisions
                ),
                qc_lineage_valid_count=sum(
                    item.qc_lineage_valid for item in angle_decisions
                ),
                sync_lineage_valid_count=sum(
                    item.sync_lineage_valid for item in angle_decisions
                ),
            )
        )
    return tuple(result)


def _require_calibration_stop_policy(
    angle_evidence: tuple[Aihub538CalibrationAngleEvidence, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
) -> None:
    for item in angle_evidence:
        selected = item.calibration_selected_count
        if (
            item.development_selected_count
            != protocol.samples_per_angle_per_partition
            or selected != protocol.samples_per_angle_per_partition
            or item.calibration_qc_available_count
            < protocol.minimum_available_per_angle
            or item.calibration_sync_available_count
            < protocol.minimum_available_per_angle
            or item.qc_lineage_valid_count != selected
            or item.sync_lineage_valid_count != selected
            or item.calibration_qc_passed_count / selected
            < protocol.minimum_per_angle_pass_fraction
            or item.calibration_sync_passed_count / selected
            < protocol.minimum_per_angle_pass_fraction
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_per_angle_stop_policy_failed"
            )
    selected_total = sum(item.calibration_selected_count for item in angle_evidence)
    if (
        sum(item.calibration_qc_passed_count for item in angle_evidence)
        / selected_total
        < protocol.minimum_calibration_pass_fraction
        or sum(item.calibration_sync_passed_count for item in angle_evidence)
        / selected_total
        < protocol.minimum_calibration_pass_fraction
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_global_stop_policy_failed"
        )


def _usable_qc(candidate: VideoQcCandidateEvidence) -> bool:
    return (
        candidate.status == "candidate_unvalidated"
        and candidate.metrics.lip_roi_mean_neighbor_luma_delta_8bit is not None
        and candidate.metrics.maximum_lip_center_step_normalized is not None
    )


def _quantile(values: list[float | int], probability: float) -> float:
    """Return the deterministic Hyndman-Fan type-7 linear quantile."""

    if not values:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_quantile_population_empty"
        )
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) for value in ordered):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_quantile_value_invalid"
        )
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    weight = position - lower_index
    return ordered[lower_index] * (1.0 - weight) + ordered[upper_index] * weight


def _rounded_clamp(value: float, minimum: float, maximum: float) -> float:
    return round(min(max(float(value), minimum), maximum), 12)


def _present(value: float | None) -> float:
    if value is None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_required_measurement_missing"
        )
    return value


def _present_int(value: int | None) -> int:
    if value is None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_required_measurement_missing"
        )
    return value


def decision_calibration_evaluator_code_sha256() -> str:
    """Hash this exact evaluator implementation for aggregate lineage."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_evaluator_source_unavailable"
        ) from None


__all__ = [
    "Aihub538CalibrationMeasurement",
    "Aihub538CalibrationMeasurementPort",
    "Aihub538CalibrationMediaItem",
    "Aihub538CalibrationSplit",
    "Aihub538DecisionCalibrationError",
    "Aihub538DecisionCalibrationResult",
    "Aihub538DecisionCalibrationService",
    "build_aihub538_calibration_split",
    "decision_calibration_evaluator_code_sha256",
]
