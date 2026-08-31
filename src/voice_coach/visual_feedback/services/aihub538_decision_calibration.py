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
    AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE,
    AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
    AIHUB538_QC_DECISION_FAILURE_REASON_CODES,
    AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS,
    Aihub538AggregateReasonCount,
    Aihub538AngleQcFailureDiagnostic,
    Aihub538CalibrationAngleEvidence,
    Aihub538DecisionCalibrationEvidence,
    Aihub538DecisionCalibrationFailureArtifact,
    Aihub538DecisionCalibrationFailureArtifactV2,
    Aihub538DecisionCalibrationProtocol,
    Aihub538QcOptionalMetricMissingCombinations,
    Aihub538QcThresholdMarginHistogram,
    DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from ..models.aihub538_balanced_cohort import (
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
    build_aihub538_balanced_actor_assignment,
)
from ..models.aihub538_multishard_admission import (
    Aihub538MultiShardAdmissionReceipt,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
    AIHUB538_TS174_TL174_SHARD,
)
from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
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

    def __init__(
        self,
        reason_code: str,
        *,
        failure_artifact: (
            Aihub538DecisionCalibrationFailureArtifact
            | Aihub538DecisionCalibrationFailureArtifactV2
            | None
        ) = None,
    ) -> None:
        if failure_artifact is not None and type(failure_artifact) not in {
            Aihub538DecisionCalibrationFailureArtifact,
            Aihub538DecisionCalibrationFailureArtifactV2,
        }:
            raise TypeError("failure_artifact type differs")
        self.reason_code = reason_code
        self.failure_artifact = failure_artifact
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationMediaItem:
    """Restricted in-process inventory row; identity tokens are never serialized."""

    sample_sha256: str
    actor_token_sha256: str = field(repr=False)
    attempt_token_sha256: str = field(repr=False)
    angle: str
    metadata_bbox_observation_available: bool = True
    label_selection_token_sha256: str | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        require_sha256(self.sample_sha256, "sample_sha256")
        require_sha256(self.actor_token_sha256, "actor_token_sha256")
        require_sha256(self.attempt_token_sha256, "attempt_token_sha256")
        if self.label_selection_token_sha256 is not None:
            require_sha256(
                self.label_selection_token_sha256,
                "label_selection_token_sha256",
            )
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise VisualFeedbackContractError("calibration media angle differs")
        if not isinstance(self.metadata_bbox_observation_available, bool):
            raise VisualFeedbackContractError(
                "calibration metadata bbox availability differs"
            )


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
    admitted_item_count: int
    metadata_excluded_item_count: int


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
        admission_receipt: (
            Aihub538TrainingAdmissionReceipt
            | Aihub538MultiShardAdmissionReceipt
        ),
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
        development_failure = _development_availability_failure_reason(
            development,
            self._protocol,
        )
        if development_failure is not None:
            raise _stop_policy_error(
                reason_code=development_failure,
                protocol=self._protocol,
                split=split,
                admission_receipt=admission_receipt,
                development=development,
                calibration=(),
                decisions=(),
                angle_diagnostics=_development_angle_diagnostics(
                    split=split,
                    development=development,
                ),
                video_qc_measurement_policy=video_qc_measurement_policy,
                av_sync_measurement_policy=av_sync_measurement_policy,
                video_qc_decision_policy=None,
                av_sync_decision_policy=None,
            )
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
        calibration_failure = _calibration_stop_policy_reason(
            angle_evidence,
            self._protocol,
        )
        if calibration_failure is not None:
            raise _stop_policy_error(
                reason_code=calibration_failure,
                protocol=self._protocol,
                split=split,
                admission_receipt=admission_receipt,
                development=development,
                calibration=calibration,
                decisions=decisions,
                angle_diagnostics=angle_evidence,
                video_qc_measurement_policy=video_qc_measurement_policy,
                av_sync_measurement_policy=av_sync_measurement_policy,
                video_qc_decision_policy=video_qc_decision_policy,
                av_sync_decision_policy=av_sync_decision_policy,
            )

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
            source_partitions=self._protocol.source_partitions,
            admitted_item_count=(
                split.admitted_item_count
                if self._protocol.source_partitions
                == AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
                else 0
            ),
            metadata_excluded_item_count=(
                split.metadata_excluded_item_count
                if self._protocol.source_partitions
                == AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
                else 0
            ),
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
    admission_receipt: (
        Aihub538TrainingAdmissionReceipt
        | Aihub538MultiShardAdmissionReceipt
    ),
    protocol: Aihub538DecisionCalibrationProtocol,
) -> Aihub538CalibrationSplit:
    """Select exact per-angle quotas after deterministic actor partitioning."""

    if not items:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_inventory_empty"
        )
    if not isinstance(
        admission_receipt,
        (
            Aihub538TrainingAdmissionReceipt,
            Aihub538MultiShardAdmissionReceipt,
        ),
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_admission_receipt_invalid"
        )
    try:
        admission_receipt._validate()
    except (TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_admission_receipt_invalid"
        ) from None
    is_multi = isinstance(
        admission_receipt,
        Aihub538MultiShardAdmissionReceipt,
    )
    if is_multi and (
        protocol != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL
        or protocol.protocol_sha256
        != admission_receipt.frozen_calibration_protocol_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_frozen_protocol_mismatch"
        )
    if not is_multi and (
        protocol != DEFAULT_AIHUB538_DECISION_CALIBRATION_PROTOCOL
        or admission_receipt.shard_id
        != AIHUB538_TS174_TL174_SHARD.shard_id
        or admission_receipt.dataset_partition
        != AIHUB538_TS174_TL174_SHARD.dataset_partition
        or admission_receipt.source_role_component
        != AIHUB538_TS174_TL174_SHARD.source_role_component
        or admission_receipt.label_role_component
        != AIHUB538_TS174_TL174_SHARD.label_role_component
        or admission_receipt.ordered_file_keys
        != AIHUB538_TS174_TL174_SHARD.ordered_file_keys
        or admission_receipt.sample_selection_rule
        != AIHUB538_TS174_TL174_SHARD.sample_selection_rule
        or admission_receipt.admission_policy_sha256
        not in {
            AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
            AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
        }
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_single_shard_scope_invalid"
        )
    sample_digests = [item.sample_sha256 for item in items]
    if len(set(sample_digests)) != len(sample_digests):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_sample_duplicate"
        )
    selection_schema = (
        "voice_coach.aihub538_training_sample_selection.v3"
        if is_multi
        else "voice_coach.aihub538_training_sample_selection.v1"
    )
    expected_selection_sha256 = sha256_document(
        {
            "schema_version": selection_schema,
            "selection_rule": admission_receipt.sample_selection_rule,
            "admission_policy_sha256": (
                admission_receipt.admission_policy_sha256
            ),
            "sample_sha256": sorted(sample_digests),
        }
    )
    if expected_selection_sha256 != admission_receipt.sample_selection_sha256:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_inventory_admission_mismatch"
        )
    if is_multi:
        label_tokens = [
            item.label_selection_token_sha256 for item in items
        ]
        if any(token is None for token in label_tokens) or len(
            set(label_tokens)
        ) != len(label_tokens):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_label_selection_token_invalid"
            )

    attempt_actors: dict[str, str] = {}
    partitions: dict[str, list[Aihub538CalibrationMediaItem]] = {
        "development": [],
        "calibration": [],
    }
    actor_assignments: dict[str, str] | None = None
    if protocol.actor_split_rule == AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE:
        try:
            actor_assignments = build_aihub538_balanced_actor_assignment(
                item.actor_token_sha256 for item in items
            )
        except ValueError as error:
            raise Aihub538DecisionCalibrationError(str(error)) from None
        assert actor_assignments is not None
        if (
            aihub538_balanced_assignment_commitment(actor_assignments)
            != admission_receipt.capacity_actor_assignment_root_sha256
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_capacity_actor_assignment_mismatch"
            )
    exclude_metadata_unavailable = (
        protocol.metadata_bbox_eligibility_rule
        == AIHUB538_MULTI_METADATA_BBOX_ELIGIBILITY_RULE
    )
    metadata_excluded: list[Aihub538CalibrationMediaItem] = []
    eligible_items: list[Aihub538CalibrationMediaItem] = []
    for item in items:
        actor = attempt_actors.setdefault(
            item.attempt_token_sha256, item.actor_token_sha256
        )
        if actor != item.actor_token_sha256:
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_attempt_actor_mismatch"
            )
        if (
            exclude_metadata_unavailable
            and not item.metadata_bbox_observation_available
        ):
            metadata_excluded.append(item)
            continue
        if (
            not exclude_metadata_unavailable
            and not item.metadata_bbox_observation_available
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_metadata_bbox_unavailable"
            )
        eligible_items.append(item)
        partition = (
            actor_assignments[item.actor_token_sha256]
            if actor_assignments is not None
            else _actor_partition(
                actor_token_sha256=item.actor_token_sha256,
                protocol_sha256=protocol.protocol_sha256,
            )
        )
        partitions[partition].append(item)

    selected = {
        partition: _balanced_angle_selection(
            items=tuple(partition_items),
            partition=partition,
            quota=protocol.samples_per_angle_per_partition,
            protocol_sha256=protocol.protocol_sha256,
            quota_unavailable_reason=(
                "aihub538_calibration_angle_quota_unavailable_after_"
                "metadata_exclusion"
                if exclude_metadata_unavailable
                else "aihub538_calibration_angle_quota_unavailable"
            ),
            use_label_selection_token=is_multi,
        )
        for partition, partition_items in partitions.items()
    }
    development = selected["development"]
    calibration = selected["calibration"]
    if is_multi:
        excluded_tokens = tuple(
            item.label_selection_token_sha256
            for item in metadata_excluded
            if item.label_selection_token_sha256 is not None
        )
        development_tokens = tuple(
            item.label_selection_token_sha256
            for item in development
            if item.label_selection_token_sha256 is not None
        )
        calibration_tokens = tuple(
            item.label_selection_token_sha256
            for item in calibration
            if item.label_selection_token_sha256 is not None
        )
        if (
            len(excluded_tokens)
            != admission_receipt.capacity_metadata_excluded_label_count
            or aihub538_label_selection_root_sha256(excluded_tokens)
            != admission_receipt.capacity_metadata_exclusion_root_sha256
            or aihub538_label_selection_root_sha256(development_tokens)
            != admission_receipt.capacity_development_label_selection_root_sha256
            or aihub538_label_selection_root_sha256(calibration_tokens)
            != admission_receipt.capacity_calibration_label_selection_root_sha256
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_calibration_capacity_selection_mismatch"
            )
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
    if (
        len(development_actors) < protocol.minimum_actor_count_per_partition
        or len(calibration_actors) < protocol.minimum_actor_count_per_partition
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_actor_quota_unavailable"
        )

    source_manifest_document: dict[str, object] = {
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
    if is_multi:
        source_manifest_document.update(
            {
                "schema_version": (
                    "voice_coach.aihub538_calibration_source_manifest.v3"
                ),
                "source_partitions": list(protocol.source_partitions),
                "actor_split_rule": protocol.actor_split_rule,
                "minimum_actor_count_per_partition": (
                    protocol.minimum_actor_count_per_partition
                ),
                "admitted_item_count": len(items),
                "eligible_item_count": len(eligible_items),
                "metadata_excluded_item_count": len(metadata_excluded),
                "metadata_bbox_eligibility_rule": (
                    protocol.metadata_bbox_eligibility_rule
                ),
                "metadata_exclusion_root_sha256": _selection_root(
                    tuple(metadata_excluded)
                ),
                "metadata_unavailable_excluded_before_selection": True,
                "post_exclusion_exact_quota_revalidated": True,
                "hidden_skip_allowed": False,
                "bbox_endpoint_repair_allowed": False,
                "capacity_audit_receipt_sha256": (
                    admission_receipt.capacity_audit_receipt_sha256
                ),
                "capacity_actor_assignment_root_sha256": (
                    admission_receipt.capacity_actor_assignment_root_sha256
                ),
                "capacity_metadata_exclusion_root_sha256": (
                    admission_receipt.capacity_metadata_exclusion_root_sha256
                ),
                "capacity_development_label_selection_root_sha256": (
                    admission_receipt.capacity_development_label_selection_root_sha256
                ),
                "capacity_calibration_label_selection_root_sha256": (
                    admission_receipt.capacity_calibration_label_selection_root_sha256
                ),
                "label_selection_root_parity_verified": True,
            }
        )
    source_manifest_sha256 = sha256_document(source_manifest_document)
    return Aihub538CalibrationSplit(
        development=development,
        calibration=calibration,
        eligible_item_count=len(eligible_items),
        development_actor_count=len(development_actors),
        calibration_actor_count=len(calibration_actors),
        actor_overlap_count=actor_overlap,
        attempt_overlap_count=attempt_overlap,
        source_manifest_sha256=source_manifest_sha256,
        admitted_item_count=len(items),
        metadata_excluded_item_count=len(metadata_excluded),
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
    quota_unavailable_reason: str,
    use_label_selection_token: bool,
) -> tuple[Aihub538CalibrationMediaItem, ...]:
    selected: list[Aihub538CalibrationMediaItem] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        candidates = [item for item in items if item.angle == angle]
        candidates.sort(
            key=(
                lambda item: _required_label_selection_token(item)
                if use_label_selection_token
                else _selection_score(
                    item=item,
                    partition=partition,
                    protocol_sha256=protocol_sha256,
                )
            )
        )
        if len(candidates) < quota:
            raise Aihub538DecisionCalibrationError(quota_unavailable_reason)
        selected.extend(candidates[:quota])
    return tuple(selected)


def _required_label_selection_token(
    item: Aihub538CalibrationMediaItem,
) -> str:
    token = item.label_selection_token_sha256
    if token is None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_label_selection_token_invalid"
        )
    return token


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
    reason_code = _development_availability_failure_reason(
        measurements,
        protocol,
    )
    if reason_code is not None:
        raise Aihub538DecisionCalibrationError(reason_code)


def _development_availability_failure_reason(
    measurements: tuple[Aihub538CalibrationMeasurement, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
) -> str | None:
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
            return (
                "aihub538_development_candidate_availability_below_stop_policy"
            )
    return None


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


def _development_angle_diagnostics(
    *,
    split: Aihub538CalibrationSplit,
    development: tuple[Aihub538CalibrationMeasurement, ...],
) -> tuple[Aihub538CalibrationAngleEvidence, ...]:
    """Represent unexecuted calibration fields as zero, never as sample rows."""

    result: list[Aihub538CalibrationAngleEvidence] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        dev = tuple(item for item in development if item.item.angle == angle)
        calibration_selected_count = sum(
            item.angle == angle for item in split.calibration
        )
        result.append(
            Aihub538CalibrationAngleEvidence(
                angle=angle,
                development_selected_count=len(dev),
                calibration_selected_count=calibration_selected_count,
                development_qc_available_count=sum(
                    _usable_qc(item.video_qc_candidate) for item in dev
                ),
                development_sync_available_count=sum(
                    item.av_sync_candidate.status == "candidate_unvalidated"
                    for item in dev
                ),
                calibration_qc_available_count=0,
                calibration_sync_available_count=0,
                calibration_qc_passed_count=0,
                calibration_sync_passed_count=0,
                qc_lineage_valid_count=0,
                sync_lineage_valid_count=0,
            )
        )
    return tuple(result)


def _aggregate_reason_counts(
    *,
    reason_sequences: tuple[tuple[str, ...], ...],
    catalog: tuple[str, ...],
) -> tuple[Aihub538AggregateReasonCount, ...]:
    counts = {reason_code: 0 for reason_code in catalog}
    for reasons in reason_sequences:
        for reason_code in reasons:
            if reason_code not in counts:
                raise VisualFeedbackContractError(
                    "AI Hub 538 QC diagnostic reason catalog differs"
                )
            counts[reason_code] += 1
    return tuple(
        Aihub538AggregateReasonCount(
            reason_code=reason_code,
            occurrence_count=counts[reason_code],
        )
        for reason_code in catalog
    )


def _qc_candidate_unavailable_reason_codes(
    candidate: VideoQcCandidateEvidence,
) -> tuple[str, ...]:
    """Map every evaluator-unusable candidate to a fixed aggregate reason."""

    if _usable_qc(candidate):
        return ()
    if candidate.status != "candidate_unvalidated":
        return candidate.reason_codes
    reasons: list[str] = []
    if candidate.metrics.lip_roi_mean_neighbor_luma_delta_8bit is None:
        reasons.append("video_qc_lip_edge_metric_unavailable")
    if candidate.metrics.maximum_lip_center_step_normalized is None:
        reasons.append("video_qc_lip_center_step_metric_unavailable")
    if not reasons:
        raise VisualFeedbackContractError(
            "AI Hub 538 QC diagnostic availability semantics differ"
        )
    return tuple(reasons)


def _qc_optional_metric_missing_combinations(
    measurements: tuple[Aihub538CalibrationMeasurement, ...],
) -> Aihub538QcOptionalMetricMissingCombinations:
    edge_only = 0
    center_only = 0
    both = 0
    for measurement in measurements:
        candidate = measurement.video_qc_candidate
        if candidate.status != "candidate_unvalidated":
            continue
        edge_missing = (
            candidate.metrics.lip_roi_mean_neighbor_luma_delta_8bit is None
        )
        center_missing = (
            candidate.metrics.maximum_lip_center_step_normalized is None
        )
        if edge_missing and center_missing:
            both += 1
        elif edge_missing:
            edge_only += 1
        elif center_missing:
            center_only += 1
    return Aihub538QcOptionalMetricMissingCombinations(
        lip_edge_only_missing_count=edge_only,
        lip_center_step_only_missing_count=center_only,
        both_optional_metrics_missing_count=both,
    )


def _qc_threshold_margin_histograms(
    *,
    measurements: tuple[Aihub538CalibrationMeasurement, ...],
    policy: VideoQcDecisionSemanticPolicy,
) -> tuple[Aihub538QcThresholdMarginHistogram, ...]:
    result: list[Aihub538QcThresholdMarginHistogram] = []
    for (
        reason_code,
        metric_name,
        comparison,
        policy_threshold_name,
        diagnostic_margin,
    ) in AIHUB538_QC_THRESHOLD_DIAGNOSTIC_SPECS:
        threshold = getattr(policy, policy_threshold_name)
        bins = [0, 0, 0, 0]
        for measurement in measurements:
            candidate = measurement.video_qc_candidate
            if candidate.status != "candidate_unvalidated":
                continue
            observed = getattr(
                candidate.metrics,
                metric_name,
            )
            if observed is None:
                continue
            signed_margin = (
                float(observed) - float(threshold)
                if comparison == "minimum"
                else float(threshold) - float(observed)
            )
            if signed_margin < -diagnostic_margin:
                bins[0] += 1
            elif signed_margin < 0.0:
                bins[1] += 1
            elif signed_margin <= diagnostic_margin:
                bins[2] += 1
            else:
                bins[3] += 1
        result.append(
            Aihub538QcThresholdMarginHistogram(
                reason_code=reason_code,
                failed_beyond_margin_count=bins[0],
                failed_within_margin_count=bins[1],
                passed_within_margin_count=bins[2],
                passed_beyond_margin_count=bins[3],
            )
        )
    return tuple(result)


def _qc_failure_diagnostics(
    *,
    development: tuple[Aihub538CalibrationMeasurement, ...],
    calibration: tuple[Aihub538CalibrationMeasurement, ...],
    decisions: tuple[_CalibrationDecisionPair, ...],
    video_qc_decision_policy: P8RuntimePolicyArtifact | None,
) -> tuple[Aihub538AngleQcFailureDiagnostic, ...]:
    if tuple(item.measurement for item in decisions) != calibration:
        raise VisualFeedbackContractError(
            "AI Hub 538 QC diagnostic decision population differs"
        )
    policy_document: VideoQcDecisionSemanticPolicy | None = None
    if video_qc_decision_policy is not None:
        if (
            video_qc_decision_policy.policy_kind != "video_qc_decision"
            or not isinstance(
                video_qc_decision_policy.policy_document,
                VideoQcDecisionSemanticPolicy,
            )
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 QC diagnostic policy type differs"
            )
        policy_document = video_qc_decision_policy.policy_document
    result: list[Aihub538AngleQcFailureDiagnostic] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        development_rows = tuple(
            item for item in development if item.item.angle == angle
        )
        calibration_rows = tuple(
            item for item in calibration if item.item.angle == angle
        )
        decision_rows = tuple(
            item for item in decisions if item.measurement.item.angle == angle
        )
        result.append(
            Aihub538AngleQcFailureDiagnostic(
                angle=angle,
                development_source_candidate_available_count=sum(
                    item.video_qc_candidate.status == "candidate_unvalidated"
                    for item in development_rows
                ),
                calibration_source_candidate_available_count=sum(
                    item.video_qc_candidate.status == "candidate_unvalidated"
                    for item in calibration_rows
                ),
                development_optional_metric_missing_combinations=(
                    _qc_optional_metric_missing_combinations(development_rows)
                ),
                calibration_optional_metric_missing_combinations=(
                    _qc_optional_metric_missing_combinations(calibration_rows)
                ),
                development_candidate_unavailable_reason_counts=(
                    _aggregate_reason_counts(
                        reason_sequences=tuple(
                            _qc_candidate_unavailable_reason_codes(
                                item.video_qc_candidate
                            )
                            for item in development_rows
                        ),
                        catalog=AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
                    )
                ),
                calibration_candidate_unavailable_reason_counts=(
                    _aggregate_reason_counts(
                        reason_sequences=tuple(
                            _qc_candidate_unavailable_reason_codes(
                                item.video_qc_candidate
                            )
                            for item in calibration_rows
                        ),
                        catalog=AIHUB538_QC_CANDIDATE_UNAVAILABLE_REASON_CODES,
                    )
                ),
                calibration_decision_failure_reason_counts=(
                    _aggregate_reason_counts(
                        reason_sequences=tuple(
                            item.qc_decision.reason_codes
                            for item in decision_rows
                        ),
                        catalog=AIHUB538_QC_DECISION_FAILURE_REASON_CODES,
                    )
                ),
                development_threshold_margin_histograms=(
                    _qc_threshold_margin_histograms(
                        measurements=development_rows,
                        policy=policy_document,
                    )
                    if policy_document is not None
                    else ()
                ),
                calibration_threshold_margin_histograms=(
                    _qc_threshold_margin_histograms(
                        measurements=calibration_rows,
                        policy=policy_document,
                    )
                    if policy_document is not None
                    else ()
                ),
            )
        )
    return tuple(result)


def _stop_policy_error(
    *,
    reason_code: str,
    protocol: Aihub538DecisionCalibrationProtocol,
    split: Aihub538CalibrationSplit,
    admission_receipt: (
        Aihub538TrainingAdmissionReceipt | Aihub538MultiShardAdmissionReceipt
    ),
    development: tuple[Aihub538CalibrationMeasurement, ...],
    calibration: tuple[Aihub538CalibrationMeasurement, ...],
    decisions: tuple[_CalibrationDecisionPair, ...],
    angle_diagnostics: tuple[Aihub538CalibrationAngleEvidence, ...],
    video_qc_measurement_policy: P8RuntimePolicyArtifact,
    av_sync_measurement_policy: P8RuntimePolicyArtifact,
    video_qc_decision_policy: P8RuntimePolicyArtifact | None,
    av_sync_decision_policy: P8RuntimePolicyArtifact | None,
) -> Aihub538DecisionCalibrationError:
    if (
        protocol != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL
        or type(admission_receipt) is not Aihub538MultiShardAdmissionReceipt
    ):
        return Aihub538DecisionCalibrationError(reason_code)
    all_measurements = development + calibration
    landmark_models = {
        measurement.video_qc_candidate.landmark_model_sha256
        for measurement in all_measurements
    } | {
        measurement.av_sync_candidate.landmark_model_sha256
        for measurement in all_measurements
    }
    qc_adapter_codes = {
        measurement.video_qc_candidate.measurement_adapter_code_sha256
        for measurement in all_measurements
    }
    sync_adapter_codes = {
        measurement.av_sync_candidate.measurement_adapter_code_sha256
        for measurement in all_measurements
    }
    if (
        len(landmark_models) != 1
        or len(qc_adapter_codes) != 1
        or len(sync_adapter_codes) != 1
        or any(
            measurement.video_qc_candidate.policy_receipt_sha256
            != video_qc_measurement_policy.policy_sha256
            or measurement.av_sync_candidate.measurement_policy_sha256
            != av_sync_measurement_policy.policy_sha256
            for measurement in all_measurements
        )
    ):
        return Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_diagnostic_lineage_invalid"
        )
    if (video_qc_decision_policy is None) != (
        av_sync_decision_policy is None
    ):
        return Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_diagnostic_lineage_invalid"
        )
    decision_policies_derived = video_qc_decision_policy is not None
    totals = {
        name: sum(getattr(item, name) for item in angle_diagnostics)
        for name in (
            "development_selected_count",
            "calibration_selected_count",
            "development_qc_available_count",
            "development_sync_available_count",
            "calibration_qc_available_count",
            "calibration_sync_available_count",
            "calibration_qc_passed_count",
            "calibration_sync_passed_count",
            "qc_lineage_valid_count",
            "sync_lineage_valid_count",
        )
    }
    calibration_executed = reason_code != (
        "aihub538_development_candidate_availability_below_stop_policy"
    )
    failure_summary = Aihub538DecisionCalibrationFailureArtifact.issue(
        reason_code=reason_code,
        failure_stage={
            "aihub538_development_candidate_availability_below_stop_policy": (
                "development_availability"
            ),
            "aihub538_calibration_per_angle_stop_policy_failed": (
                "per_angle_stop_policy"
            ),
            "aihub538_calibration_global_stop_policy_failed": (
                "global_stop_policy"
            ),
        }[reason_code],
        protocol_revision=protocol.protocol_revision,
        source_manifest_sha256=split.source_manifest_sha256,
        admission_receipt_sha256=admission_receipt.receipt_sha256,
        admission_manifest_sha256=admission_receipt.admission_manifest_sha256,
        admission_policy_sha256=admission_receipt.admission_policy_sha256,
        capacity_audit_receipt_sha256=(
            admission_receipt.capacity_audit_receipt_sha256
        ),
        evaluation_protocol_sha256=protocol.protocol_sha256,
        evaluator_code_sha256=decision_calibration_evaluator_code_sha256(),
        landmark_model_sha256=next(iter(landmark_models)),
        video_qc_measurement_adapter_code_sha256=next(
            iter(qc_adapter_codes)
        ),
        av_sync_measurement_adapter_code_sha256=next(
            iter(sync_adapter_codes)
        ),
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
        decision_policies_derived=decision_policies_derived,
        video_qc_decision_policy_artifact_sha256=(
            video_qc_decision_policy.artifact_sha256
            if video_qc_decision_policy is not None
            else None
        ),
        video_qc_decision_policy_sha256=(
            video_qc_decision_policy.policy_sha256
            if video_qc_decision_policy is not None
            else None
        ),
        av_sync_decision_policy_artifact_sha256=(
            av_sync_decision_policy.artifact_sha256
            if av_sync_decision_policy is not None
            else None
        ),
        av_sync_decision_policy_sha256=(
            av_sync_decision_policy.policy_sha256
            if av_sync_decision_policy is not None
            else None
        ),
        admitted_item_count=split.admitted_item_count,
        eligible_item_count=split.eligible_item_count,
        metadata_excluded_item_count=split.metadata_excluded_item_count,
        development_measurement_count=len(development),
        calibration_measurement_count=len(calibration),
        decision_evaluated_count=(
            len(calibration) if calibration_executed else 0
        ),
        **totals,
        development_qc_available_fraction=(
            totals["development_qc_available_count"]
            / totals["development_selected_count"]
        ),
        development_sync_available_fraction=(
            totals["development_sync_available_count"]
            / totals["development_selected_count"]
        ),
        calibration_qc_pass_fraction=(
            totals["calibration_qc_passed_count"]
            / totals["calibration_selected_count"]
            if calibration_executed
            else None
        ),
        calibration_sync_pass_fraction=(
            totals["calibration_sync_passed_count"]
            / totals["calibration_selected_count"]
            if calibration_executed
            else None
        ),
        samples_per_angle_per_partition_threshold=(
            protocol.samples_per_angle_per_partition
        ),
        minimum_available_per_angle_threshold=(
            protocol.minimum_available_per_angle
        ),
        minimum_per_angle_pass_fraction_threshold=(
            protocol.minimum_per_angle_pass_fraction
        ),
        minimum_global_pass_fraction_threshold=(
            protocol.minimum_calibration_pass_fraction
        ),
        angle_diagnostics=angle_diagnostics,
    )
    try:
        artifact = Aihub538DecisionCalibrationFailureArtifactV2.issue(
            failure_summary=failure_summary,
            diagnostic_video_qc_decision_policy=video_qc_decision_policy,
            angle_qc_diagnostics=_qc_failure_diagnostics(
                development=development,
                calibration=calibration,
                decisions=decisions,
                video_qc_decision_policy=video_qc_decision_policy,
            ),
        )
    except (TypeError, ValueError):
        return Aihub538DecisionCalibrationError(
            "aihub538_calibration_failure_diagnostic_semantics_invalid"
        )
    return Aihub538DecisionCalibrationError(
        reason_code,
        failure_artifact=artifact,
    )


def _require_calibration_stop_policy(
    angle_evidence: tuple[Aihub538CalibrationAngleEvidence, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
) -> None:
    reason_code = _calibration_stop_policy_reason(angle_evidence, protocol)
    if reason_code is not None:
        raise Aihub538DecisionCalibrationError(reason_code)


def _calibration_stop_policy_reason(
    angle_evidence: tuple[Aihub538CalibrationAngleEvidence, ...],
    protocol: Aihub538DecisionCalibrationProtocol,
) -> str | None:
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
            return "aihub538_calibration_per_angle_stop_policy_failed"
    selected_total = sum(item.calibration_selected_count for item in angle_evidence)
    if (
        sum(item.calibration_qc_passed_count for item in angle_evidence)
        / selected_total
        < protocol.minimum_calibration_pass_fraction
        or sum(item.calibration_sync_passed_count for item in angle_evidence)
        / selected_total
        < protocol.minimum_calibration_pass_fraction
    ):
        return "aihub538_calibration_global_stop_policy_failed"
    return None


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
