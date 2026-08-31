"""Pure P6 v4 photometric calibration over fresh, disjoint observations.

No archive, decoder, tracker, filesystem, or network work occurs here.  The
service accepts restricted in-process actor/attempt tokens and emits only
aggregate evidence.  Existing v3 policies and publishers are not imported.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from threading import RLock
from weakref import WeakKeyDictionary
from ..adapters.aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceStore
from ..adapters.aihub538_ffmpeg_colorimetry import require_adapter_issued_color_lineage
from ..adapters.aihub538_photometric_source_admission import Aihub538MemberSourceMappingCapabilityV4, assert_aihub538_member_source_mapping

from ...pronunciation_attempts.adapters.video_qc import (
    VideoQcCandidateEvidence,
    VideoQcMetrics,
)
from ..errors import VisualFeedbackContractError
from ..models.aihub538_photometric_calibration_v4 import (
    AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS,
    Aihub538ActorClusteredOneSidedCiV4,
    Aihub538ActorOutcomeCellV4,
    Aihub538ActorOutcomeDistributionV4,
    Aihub538FreshCohortSeparationReceiptV4,
    Aihub538FullFrameExposureAggregateV4,
    Aihub538HardSafetyEnvelopeV4,
    Aihub538LipRoiExposureVisibilityAggregateV4,
    Aihub538PhotometricCalibrationEvidenceV4,
    Aihub538PhotometricCalibrationProtocolV4,
    Aihub538PhotometricMetricNormalizerV4,
    Aihub538PhotometricPartitionAggregateV4,
    Aihub538PhotometricPolicyV4,
    FfmpegRgbColorimetryReceipt,
    FfmpegRgbSourceConversionReceiptV4,
    FfmpegSourceColorProbeReceiptV4,
)
from ..models.aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityItem,
)
from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from ..models.aihub538_balanced_cohort import (
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
)
from ..models.aihub538_p6_remediation import (
    AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256,
    AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,
    AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION,
    AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION,
    Aihub538P6RemediationAuditReceipt,
    aihub538_p6_full_member_tuple_commitment_sha256,
)
from ..models.aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from ..models.integrity import (
    require_safe_identifier,
    require_sha256,
    sha256_document,
)


class Aihub538PhotometricCalibrationV4Error(RuntimeError):
    """Stable fail-closed reason without restricted cohort identifiers."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricCohortMemberV4:
    """Private selected member, issued only from the remediation cohort."""

    partition_role: str
    item: Aihub538LabelCapacityItem = field(repr=False)
    label_archive_sha256: str
    label_content_root_sha256: str
    source_manifest_sha256: str
    admission_receipt_sha256: str

@dataclass(frozen=True, slots=True)
class Aihub538PhotometricPairLineageV4:
    label_archive_sha256: str
    label_content_root_sha256: str
    source_archive_sha256: str
    source_content_root_sha256: str
    source_manifest_sha256: str
    admission_receipt_sha256: str
    admission_policy_sha256: str
    production_execution_policy_sha256: str
    def __post_init__(self)->None:
        for name in self.__dataclass_fields__: require_sha256(getattr(self,name),name)
        from ..models.aihub538_label_capacity import AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
        from ..models.aihub538_training_evidence import aihub538_shard_admission_policy_sha256
        from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
        from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
        # Future remediation pairs must carry the v4 policy digest; the exact
        # shard is resolved by the ordered label archive outside this object.
        if self.admission_policy_sha256 == AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256:
            raise VisualFeedbackContractError("photometric pair admission policy invalid")


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricMeasurementV4:
    """One inseparable source-probe -> RGB -> QC lineage."""

    member: Aihub538PhotometricCohortMemberV4 = field(repr=False)
    source_probe_receipt: FfmpegSourceColorProbeReceiptV4
    source_conversion_receipt: FfmpegRgbSourceConversionReceiptV4
    candidate: VideoQcCandidateEvidence = field(repr=False)
    source_mapping: Aihub538MemberSourceMappingCapabilityV4 = field(repr=False)
    sampling_bundle: object|None = field(default=None,repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.member) is not Aihub538PhotometricCohortMemberV4
            or type(self.source_probe_receipt) is not FfmpegSourceColorProbeReceiptV4
            or type(self.source_conversion_receipt)
            is not FfmpegRgbSourceConversionReceiptV4
            or type(self.candidate) is not VideoQcCandidateEvidence
            or self.candidate.status != "candidate_unvalidated"
            or self.source_probe_receipt.receipt_sha256
            != self.source_conversion_receipt.source_probe_receipt_sha256
            or self.candidate.container_sha256
            != self.source_probe_receipt.container_sha256
            or self.candidate.video_stream_index
            != self.source_probe_receipt.video_stream_index
            or self.candidate.decoded_rgb_evidence_sha256
            != self.source_conversion_receipt.decoded_rgb_evidence_sha256
            or self.candidate.container_sha256
            != self.source_conversion_receipt.container_sha256
            or self.candidate.video_stream_index
            != self.source_conversion_receipt.video_stream_index
            or type(self.source_mapping) is not Aihub538MemberSourceMappingCapabilityV4
            or self.source_mapping.sample_sha256!=self.member.item.sample_sha256 or self.source_mapping.angle!=self.member.item.angle
            or self.source_mapping.actor_token_sha256!=self.member.item.actor_token_sha256 or self.source_mapping.attempt_token_sha256!=self.member.item.attempt_token_sha256
            or self.source_mapping.label_archive_sha256!=self.member.label_archive_sha256 or self.source_mapping.label_content_root_sha256!=self.member.label_content_root_sha256
            or self.source_mapping.admission_receipt_sha256!=self.member.admission_receipt_sha256 or self.source_mapping.container_sha256!=self.candidate.container_sha256
        ):
            raise VisualFeedbackContractError(
                "photometric one-shot measurement lineage differs"
            )
        if self.sampling_bundle is not None:
            from ..adapters.aihub538_photometric_v4_sampling import Aihub538PhotometricV4SampleBundle,consume_aihub538_photometric_v4_sample_bundle
            from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
            bundle=self.sampling_bundle
            if (type(bundle) is not Aihub538PhotometricV4SampleBundle or bundle.source_probe_receipt is not self.source_probe_receipt or bundle.source_conversion_receipt is not self.source_conversion_receipt or bundle.sampling_receipt.production_execution_policy_sha256!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256 or bundle.sample.container_sha256!=self.candidate.container_sha256 or bundle.source_conversion_receipt.decoded_rgb_evidence_sha256!=self.candidate.decoded_rgb_evidence_sha256):
                raise VisualFeedbackContractError("photometric production sampling lineage differs")
            try: consume_aihub538_photometric_v4_sample_bundle(bundle)
            except RuntimeError: raise VisualFeedbackContractError("photometric production sampling receipt invalid") from None
        try: assert_aihub538_member_source_mapping(self.source_mapping)
        except ValueError: raise VisualFeedbackContractError("photometric source admission authority differs") from None


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class Aihub538PhotometricCohortCapabilityV4:
    remediation_receipt: Aihub538P6RemediationAuditReceipt = field(repr=False)
    remediation_receipt_sha256: str
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    source_manifest_sha256: str
    admission_receipt_sha256: str
    members: tuple[Aihub538PhotometricCohortMemberV4, ...] = field(repr=False)
    issuance_store: Aihub538PhotometricV4IssuanceStore = field(repr=False)
    durable_capability_receipt_sha256: str
    pair_count:int
    ordered_pair_lineage_commitment_sha256:str
    selected_member_commitment_sha256:str


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class Aihub538PhotometricCalibrationInputV4:
    capability: Aihub538PhotometricCohortCapabilityV4 = field(repr=False)
    measurements: tuple[Aihub538PhotometricMeasurementV4, ...] = field(repr=False)


_CAPABILITIES: WeakKeyDictionary[Aihub538PhotometricCohortCapabilityV4, bool] = (
    WeakKeyDictionary()
)
_INPUTS: WeakKeyDictionary[Aihub538PhotometricCalibrationInputV4, bool] = (
    WeakKeyDictionary()
)
_AUTHORITY_LOCK = RLock()


def issue_aihub538_photometric_cohort_capability_v4(
    *,
    remediation_receipt: Aihub538P6RemediationAuditReceipt,
    candidate_aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
    source_manifest_sha256: str,
    admission_receipt_sha256: str,
    issuance_store: Aihub538PhotometricV4IssuanceStore,
    pair_lineages: tuple[Aihub538PhotometricPairLineageV4,...]|None=None,
) -> Aihub538PhotometricCohortCapabilityV4:
    """Reconstruct the frozen remediation selection; never accept raw tokens."""

    if type(remediation_receipt) is not Aihub538P6RemediationAuditReceipt:
        raise Aihub538PhotometricCalibrationV4Error("p6_v4_remediation_receipt_required")
    remediation_receipt._validate()
    if (
        not remediation_receipt.is_feasible
        or remediation_receipt.design_failure_artifact_receipt_sha256
        != AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
        or len(candidate_aggregates)
        != len(remediation_receipt.evaluated_prefix_lineage)
    ):
        raise Aihub538PhotometricCalibrationV4Error("p6_v4_remediation_lineage_invalid")
    require_sha256(source_manifest_sha256, "source_manifest_sha256")
    require_sha256(admission_receipt_sha256, "admission_receipt_sha256")
    if pair_lineages is None:
        if len(candidate_aggregates)!=1: raise Aihub538PhotometricCalibrationV4Error("p6_v4_ordered_pair_lineage_required")
        pair_lineages=(Aihub538PhotometricPairLineageV4(candidate_aggregates[0].label_archive_sha256,candidate_aggregates[0].label_content_root_sha256,"0"*64,"0"*64,source_manifest_sha256,admission_receipt_sha256,"0"*64,sha256_document({"mode":"legacy_nonproduction_contract"})),)
    if len(pair_lineages)!=len(candidate_aggregates): raise Aihub538PhotometricCalibrationV4Error("p6_v4_ordered_pair_lineage_invalid")
    recomputed_source_vector=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_source_manifests.v1","length":len(pair_lineages),"values":[pair.source_manifest_sha256 for pair in pair_lineages]})
    recomputed_admission_vector=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_admission_receipts.v1","length":len(pair_lineages),"values":[pair.admission_receipt_sha256 for pair in pair_lineages]})
    if len(pair_lineages)>1 and (len(pair_lineages)!=3 or any(type(pair) is not Aihub538PhotometricPairLineageV4 for pair in pair_lineages) or source_manifest_sha256!=recomputed_source_vector or admission_receipt_sha256!=recomputed_admission_vector): raise Aihub538PhotometricCalibrationV4Error("p6_v4_ordered_pair_lineage_invalid")
    for pair_index,(aggregate, lineage, pair) in enumerate(zip(
        candidate_aggregates, remediation_receipt.evaluated_prefix_lineage, pair_lineages, strict=True
    )):
        from ..models.aihub538_nonlocked_shards import aihub538_nonlocked_shard_by_id
        from ..models.aihub538_training_evidence import aihub538_shard_admission_policy_sha256
        expected_pair_policy=aihub538_shard_admission_policy_sha256(aihub538_nonlocked_shard_by_id(f"ts{AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[pair_index]['label_role_component'][2:]}_tl{AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[pair_index]['label_role_component'][2:]}"))
        if (
            type(aggregate) is not Aihub538LabelCapacityAggregate
            or aggregate.identity_namespace_commitment_sha256
            != remediation_receipt.identity_namespace_commitment_sha256
            or aggregate.member_namespace_commitment_sha256
            != remediation_receipt.member_namespace_commitment_sha256
            or aggregate.label_archive_sha256 != lineage.label_archive_sha256
            or aggregate.label_content_root_sha256 != lineage.label_content_root_sha256
            or pair.label_archive_sha256 != aggregate.label_archive_sha256
            or pair.label_content_root_sha256 != aggregate.label_content_root_sha256
            or (len(candidate_aggregates)>1 and pair.admission_policy_sha256!=expected_pair_policy)
            or (len(candidate_aggregates)>1 and pair.production_execution_policy_sha256!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256)
            or len(aggregate.items) != lineage.label_count
            or aihub538_p6_full_member_tuple_commitment_sha256(aggregate)
            != lineage.full_member_tuple_commitment_sha256
        ):
            raise Aihub538PhotometricCalibrationV4Error("p6_v4_remediation_lineage_invalid")
    items = tuple(item for aggregate in candidate_aggregates for item in aggregate.items)
    actors = sorted({item.actor_token_sha256 for item in items})
    assignments = {
        actor: "fresh_development" if index % 2 == 0 else "fresh_calibration"
        for index, actor in enumerate(actors)
    }
    selected: list[Aihub538PhotometricCohortMemberV4] = []
    archive_by_sample = {
        item.sample_sha256: aggregate for aggregate in candidate_aggregates
        for item in aggregate.items
    }
    pair_by_archive={pair.label_archive_sha256:pair for pair in pair_lineages}
    for role in ("fresh_development", "fresh_calibration"):
        for angle in AIHUB538_TRAINING_ANGLES:
            eligible = sorted(
                (
                    item for item in items
                    if item.metadata_bbox_observation_available
                    and assignments[item.actor_token_sha256] == role
                    and item.angle == angle
                ),
                key=lambda item: item.sample_sha256,
            )[:AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION]
            selected.extend(Aihub538PhotometricCohortMemberV4(role, item, archive_by_sample[item.sample_sha256].label_archive_sha256, archive_by_sample[item.sample_sha256].label_content_root_sha256, pair_by_archive[archive_by_sample[item.sample_sha256].label_archive_sha256].source_manifest_sha256, pair_by_archive[archive_by_sample[item.sample_sha256].label_archive_sha256].admission_receipt_sha256) for item in eligible)
    role_actors = {
        role: {member.item.actor_token_sha256 for member in selected if member.partition_role == role}
        for role in ("fresh_development", "fresh_calibration")
    }
    decision = remediation_receipt.prefix_decisions[-1]
    assignment_root = aihub538_balanced_assignment_commitment(
        {
            actor: ("development" if role == "fresh_development" else "calibration")
            for actor, role in assignments.items()
        }
    )
    selection_roots = {
        role: aihub538_label_selection_root_sha256(
            member.item.sample_sha256
            for member in selected if member.partition_role == role
        )
        for role in ("fresh_development", "fresh_calibration")
    }
    if any(
        len(role_actors[role]) < AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION
        for role in role_actors
    ) or (
        assignment_root != decision.actor_assignment_root_sha256
        or selection_roots["fresh_development"]
        != decision.development_selection_root_sha256
        or selection_roots["fresh_calibration"]
        != decision.calibration_selection_root_sha256
    ):
        raise Aihub538PhotometricCalibrationV4Error("p6_v4_private_cohort_too_small")
    if type(issuance_store) is not Aihub538PhotometricV4IssuanceStore:
        raise Aihub538PhotometricCalibrationV4Error("p6_v4_durable_issuance_store_required")
    capability_payload_sha256 = sha256_document({
        "schema_version": "voice_coach.p6_v4_capability_payload.v1",
        "remediation_receipt_sha256": remediation_receipt.receipt_sha256,
        "full_member_tuple_commitments": [lineage.full_member_tuple_commitment_sha256 for lineage in remediation_receipt.evaluated_prefix_lineage],
        "ordered_pair_lineage": [pair.__dict__ if hasattr(pair,"__dict__") else {name:getattr(pair,name) for name in pair.__dataclass_fields__} for pair in pair_lineages],
    })
    pair_commitment=aihub538_photometric_v4_ordered_pair_lineage_commitment(pair_lineages)
    selected_commitment=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_selected_members.v1","length":len(selected),"values":[{"partition_role":member.partition_role,"sample_sha256":member.item.sample_sha256,"actor_token_sha256":member.item.actor_token_sha256,"attempt_token_sha256":member.item.attempt_token_sha256,"angle":member.item.angle,"label_archive_sha256":member.label_archive_sha256,"label_content_root_sha256":member.label_content_root_sha256,"source_manifest_sha256":member.source_manifest_sha256,"admission_receipt_sha256":member.admission_receipt_sha256} for member in selected]})
    durable_receipt = issuance_store.claim(kind="capability", key_sha256=remediation_receipt.receipt_sha256, payload_sha256=capability_payload_sha256)
    capability = Aihub538PhotometricCohortCapabilityV4(
        remediation_receipt=remediation_receipt,
        remediation_receipt_sha256=remediation_receipt.receipt_sha256,
        identity_namespace_commitment_sha256=remediation_receipt.identity_namespace_commitment_sha256,
        member_namespace_commitment_sha256=remediation_receipt.member_namespace_commitment_sha256,
        source_manifest_sha256=recomputed_source_vector,
        admission_receipt_sha256=recomputed_admission_vector,
        members=tuple(selected),
        issuance_store=issuance_store,
        durable_capability_receipt_sha256=durable_receipt,
        pair_count=len(pair_lineages),ordered_pair_lineage_commitment_sha256=pair_commitment,selected_member_commitment_sha256=selected_commitment,
    )
    with _AUTHORITY_LOCK:
        _CAPABILITIES[capability] = False
    return capability

def aihub538_photometric_v4_ordered_pair_lineage_commitment(pair_lineages:tuple[Aihub538PhotometricPairLineageV4,...])->str:
    if type(pair_lineages) is not tuple or any(type(pair) is not Aihub538PhotometricPairLineageV4 for pair in pair_lineages):
        raise Aihub538PhotometricCalibrationV4Error("p6_v4_ordered_pair_lineage_invalid")
    return sha256_document({"schema_version":"voice_coach.p6_v4_ordered_full_pair_lineage.v1","length":len(pair_lineages),"values":[{name:getattr(pair,name) for name in pair.__dataclass_fields__} for pair in pair_lineages]})


def bind_aihub538_photometric_measurements_v4(
    *, capability: Aihub538PhotometricCohortCapabilityV4,
    measurements: tuple[Aihub538PhotometricMeasurementV4, ...],
) -> Aihub538PhotometricCalibrationInputV4:
    with _AUTHORITY_LOCK:
        if _CAPABILITIES.get(capability) is not False:
            raise Aihub538PhotometricCalibrationV4Error("p6_v4_capability_invalid_or_consumed")
        if len(measurements) != len(capability.members) or any(
            measurement.member is not member
            for measurement, member in zip(measurements, capability.members, strict=True)
        ):
            raise Aihub538PhotometricCalibrationV4Error("p6_v4_measurement_membership_invalid")
        containers=[measurement.candidate.container_sha256 for measurement in measurements]
        conversions=[measurement.source_conversion_receipt.receipt_sha256 for measurement in measurements]
        if len(set(containers))!=len(containers) or len(set(conversions))!=len(conversions):
            raise Aihub538PhotometricCalibrationV4Error("p6_v4_member_media_reuse_forbidden")
        for measurement in measurements:
            try:
                require_adapter_issued_color_lineage(
                    probe=measurement.source_probe_receipt,
                    conversion=measurement.source_conversion_receipt,
                )
            except RuntimeError:
                raise Aihub538PhotometricCalibrationV4Error("p6_v4_source_receipt_not_adapter_issued") from None
        value = Aihub538PhotometricCalibrationInputV4(capability, measurements)
        _CAPABILITIES[capability] = True
        _INPUTS[value] = False
        return value


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricObservationV4:
    """One restricted observation; identity tokens are never serialized."""

    partition_role: str
    actor_token_sha256: str = field(repr=False)
    attempt_token_sha256: str = field(repr=False)
    angle: str
    source_candidate_available: bool
    metrics: VideoQcMetrics = field(repr=False)
    video_qc_measurement_policy_sha256: str
    video_qc_measurement_revision: str
    video_qc_measurement_adapter_code_sha256: str
    colorimetry_receipt_sha256: str
    source_color_metadata_verified: bool
    explicit_color_conversion_applied: bool

    def __post_init__(self) -> None:
        if self.partition_role not in {"fresh_development", "fresh_calibration"}:
            raise VisualFeedbackContractError(
                "photometric observation partition differs"
            )
        require_sha256(self.actor_token_sha256, "actor_token_sha256")
        require_sha256(self.attempt_token_sha256, "attempt_token_sha256")
        require_sha256(
            self.video_qc_measurement_policy_sha256,
            "video_qc_measurement_policy_sha256",
        )
        require_safe_identifier(
            self.video_qc_measurement_revision,
            "video_qc_measurement_revision",
        )
        require_sha256(
            self.video_qc_measurement_adapter_code_sha256,
            "video_qc_measurement_adapter_code_sha256",
        )
        require_sha256(
            self.colorimetry_receipt_sha256,
            "colorimetry_receipt_sha256",
        )
        if not isinstance(
            self.source_color_metadata_verified, bool
        ) or not isinstance(self.explicit_color_conversion_applied, bool):
            raise VisualFeedbackContractError(
                "photometric source colorimetry assertions differ"
            )
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise VisualFeedbackContractError(
                "photometric observation angle differs"
            )
        if not isinstance(self.source_candidate_available, bool):
            raise VisualFeedbackContractError(
                "photometric source candidate availability differs"
            )
        if not isinstance(self.metrics, VideoQcMetrics):
            raise VisualFeedbackContractError(
                "photometric observation metrics differ"
            )


class Aihub538PhotometricCalibrationV4Service:
    """Derive soft photometric fit on fresh dev, then evaluate fresh actors."""

    def __init__(
        self,
        *,
        protocol: Aihub538PhotometricCalibrationProtocolV4,
        colorimetry_receipt: FfmpegRgbColorimetryReceipt,
    ) -> None:
        if type(protocol) is not Aihub538PhotometricCalibrationProtocolV4:
            raise TypeError("photometric v4 protocol type differs")
        if type(colorimetry_receipt) is not FfmpegRgbColorimetryReceipt:
            raise TypeError("ffmpeg colorimetry receipt type differs")
        try:
            require_adapter_issued_color_lineage(color=colorimetry_receipt)
        except RuntimeError:
            raise TypeError("ffmpeg colorimetry receipt was not adapter-issued") from None
        self._protocol = protocol
        self._colorimetry_receipt = colorimetry_receipt

    def calibrate(
        self,
        *,
        calibration_input: Aihub538PhotometricCalibrationInputV4,
    ) -> Aihub538PhotometricCalibrationEvidenceV4:
        """Consume exactly one authority-issued, fully-bound input."""

        with _AUTHORITY_LOCK:
            if _INPUTS.get(calibration_input) is not False:
                raise Aihub538PhotometricCalibrationV4Error(
                    "p6_v4_input_invalid_or_duplicate_release"
                )
            _INPUTS[calibration_input] = True
        capability = calibration_input.capability
        remediation = capability.remediation_receipt
        observations = tuple(
            Aihub538PhotometricObservationV4(
                partition_role=measurement.member.partition_role,
                actor_token_sha256=measurement.member.item.actor_token_sha256,
                attempt_token_sha256=measurement.member.item.attempt_token_sha256,
                angle=measurement.member.item.angle,
                source_candidate_available=True,
                metrics=measurement.candidate.metrics,
                video_qc_measurement_policy_sha256=measurement.candidate.policy_receipt_sha256,
                video_qc_measurement_revision=measurement.candidate.measurement_revision,
                video_qc_measurement_adapter_code_sha256=measurement.candidate.measurement_adapter_code_sha256,
                colorimetry_receipt_sha256=measurement.source_conversion_receipt.adapter_colorimetry_receipt_sha256,
                source_color_metadata_verified=True,
                explicit_color_conversion_applied=True,
            )
            for measurement in calibration_input.measurements
        )
        development, calibration = self._validate_fresh_cohorts(
            observations=observations,
            prior_v3_actor_tokens_sha256=(),
            prior_v3_attempt_tokens_sha256=(),
        )
        development_actor_tokens = {
            item.actor_token_sha256 for item in development
        }
        calibration_actor_tokens = {
            item.actor_token_sha256 for item in calibration
        }
        development_attempt_tokens = {
            item.attempt_token_sha256 for item in development
        }
        calibration_attempt_tokens = {
            item.attempt_token_sha256 for item in calibration
        }
        separation = Aihub538FreshCohortSeparationReceiptV4.issue(
            identity_namespace_commitment_sha256=capability.identity_namespace_commitment_sha256,
            prior_v3_actor_set_commitment_sha256=sha256_document({
                "domain": "actual_v3_design_actor_aggregate",
                "failure_artifact_sha256": remediation.design_failure_artifact_receipt_sha256,
                "actor_count": remediation.design_used_actor_count,
            }),
            prior_v3_attempt_set_commitment_sha256=sha256_document({
                "domain": "actual_v3_design_attempt_aggregate",
                "failure_artifact_sha256": remediation.design_failure_artifact_receipt_sha256,
                "attempt_count": remediation.design_used_attempt_count,
            }),
            fresh_development_actor_set_commitment_sha256=(
                _identity_set_commitment(
                    "fresh_development_actor", development_actor_tokens
                )
            ),
            fresh_development_attempt_set_commitment_sha256=(
                _identity_set_commitment(
                    "fresh_development_attempt", development_attempt_tokens
                )
            ),
            fresh_calibration_actor_set_commitment_sha256=(
                _identity_set_commitment(
                    "fresh_calibration_actor", calibration_actor_tokens
                )
            ),
            fresh_calibration_attempt_set_commitment_sha256=(
                _identity_set_commitment(
                    "fresh_calibration_attempt", calibration_attempt_tokens
                )
            ),
            prior_v3_actor_count=remediation.design_used_actor_count,
            prior_v3_attempt_count=remediation.design_used_attempt_count,
            development_actor_count=len(development_actor_tokens),
            development_attempt_count=len(development_attempt_tokens),
            calibration_actor_count=len(calibration_actor_tokens),
            calibration_attempt_count=len(calibration_attempt_tokens),
        )

        policy = self._derive_policy(
            development=development,
            development_actor_set_commitment_sha256=(
                separation.fresh_development_actor_set_commitment_sha256
            ),
        )
        development_results = _evaluate_partition(
            observations=development,
            envelope=self._protocol.hard_safety_envelope,
            policy=policy,
        )
        calibration_results = _evaluate_partition(
            observations=calibration,
            envelope=self._protocol.hard_safety_envelope,
            policy=policy,
        )
        development_aggregate = _partition_aggregate(
            role="fresh_development",
            results=development_results,
        )
        calibration_aggregate = _partition_aggregate(
            role="fresh_calibration",
            results=calibration_results,
        )
        actor_ci = _actor_clustered_ci(
            calibration_results,
            protocol=self._protocol,
        )
        production_bundles=tuple(measurement.sampling_bundle for measurement in calibration_input.measurements)
        if all(bundle is not None for bundle in production_bundles):
            from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
            production_policy_sha=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
            sampling_commitment=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_sampling_receipts.v1","length":len(production_bundles),"values":[bundle.sampling_receipt.receipt_sha256 for bundle in production_bundles]})
        elif all(bundle is None for bundle in production_bundles):
            production_policy_sha=sha256_document({"mode":"legacy_nonproduction_contract"})
            sampling_commitment=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_sampling_receipts.v1","length":0,"values":[]})
        else:
            raise Aihub538PhotometricCalibrationV4Error("p6_v4_mixed_sampling_lineage_invalid")
        evidence = Aihub538PhotometricCalibrationEvidenceV4.issue(
            protocol=self._protocol,
            colorimetry_receipt=self._colorimetry_receipt,
            policy=policy,
            cohort_separation_receipt=separation,
            source_manifest_sha256=capability.source_manifest_sha256,
            admission_receipt_sha256=capability.admission_receipt_sha256,
            development_aggregate=development_aggregate,
            calibration_aggregate=calibration_aggregate,
            actor_clustered_one_sided_ci=actor_ci,
            production_execution_policy_sha256=production_policy_sha,
            ordered_sampling_receipt_commitment_sha256=sampling_commitment,
            pair_count=capability.pair_count,
            remediation_receipt_sha256=capability.remediation_receipt_sha256,
            ordered_pair_lineage_commitment_sha256=capability.ordered_pair_lineage_commitment_sha256,
            selected_member_commitment_sha256=capability.selected_member_commitment_sha256,
            selected_member_count=len(capability.members),
            ordered_sampling_receipt_count=sum(bundle is not None for bundle in production_bundles),
        )
        capability.issuance_store.claim(
            kind="artifact",
            key_sha256=capability.remediation_receipt_sha256,
            payload_sha256=evidence.artifact_sha256,
        )
        return evidence

    def _validate_fresh_cohorts(
        self,
        *,
        observations: tuple[Aihub538PhotometricObservationV4, ...],
        prior_v3_actor_tokens_sha256: tuple[str, ...],
        prior_v3_attempt_tokens_sha256: tuple[str, ...],
    ) -> tuple[
        tuple[Aihub538PhotometricObservationV4, ...],
        tuple[Aihub538PhotometricObservationV4, ...],
    ]:
        if not observations or any(
            type(item) is not Aihub538PhotometricObservationV4
            for item in observations
        ):
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_observations_invalid"
            )
        # Prior-v3 private tokens are deliberately unavailable here. Their exact
        # disjointness was already established by the authority-checked
        # remediation receipt and cannot be replaced by caller tuples.
        prior_actors: set[str] = set()
        prior_attempts: set[str] = set()
        attempts = [item.attempt_token_sha256 for item in observations]
        if len(set(attempts)) != len(attempts):
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_attempt_duplicate"
            )
        development = tuple(
            item
            for item in observations
            if item.partition_role == "fresh_development"
        )
        calibration = tuple(
            item
            for item in observations
            if item.partition_role == "fresh_calibration"
        )
        development_actors = {item.actor_token_sha256 for item in development}
        calibration_actors = {item.actor_token_sha256 for item in calibration}
        development_attempts = {
            item.attempt_token_sha256 for item in development
        }
        calibration_attempts = {
            item.attempt_token_sha256 for item in calibration
        }
        if development_actors & calibration_actors:
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_development_calibration_actor_overlap"
            )
        if development_attempts & calibration_attempts:
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_development_calibration_attempt_overlap"
            )
        fresh_actors = development_actors | calibration_actors
        fresh_attempts = development_attempts | calibration_attempts
        if prior_actors & fresh_actors:
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_prior_actor_overlap"
            )
        if prior_attempts & fresh_attempts:
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_prior_attempt_overlap"
            )
        if (
            len(development_actors)
            < self._protocol.minimum_development_actor_count
            or len(calibration_actors)
            < self._protocol.minimum_calibration_actor_count
        ):
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_actor_count_insufficient"
            )
        for partition in (development, calibration):
            counts: dict[str, int] = defaultdict(int)
            for item in partition:
                counts[item.actor_token_sha256] += 1
                if (
                    item.video_qc_measurement_policy_sha256
                    != self._protocol.video_qc_measurement_policy_sha256
                    or item.video_qc_measurement_revision
                    != self._protocol.video_qc_measurement_revision
                    or item.video_qc_measurement_adapter_code_sha256
                    != self._protocol.video_qc_measurement_adapter_code_sha256
                    or item.colorimetry_receipt_sha256
                    != self._colorimetry_receipt.receipt_sha256
                    or not item.source_color_metadata_verified
                    or not item.explicit_color_conversion_applied
                ):
                    raise Aihub538PhotometricCalibrationV4Error(
                        "aihub538_photometric_v4_measurement_lineage_mismatch"
                    )
            if any(
                count < self._protocol.minimum_attempts_per_actor
                for count in counts.values()
            ):
                raise Aihub538PhotometricCalibrationV4Error(
                    "aihub538_photometric_v4_attempt_count_insufficient"
                )
        return development, calibration

    def _derive_policy(
        self,
        *,
        development: tuple[Aihub538PhotometricObservationV4, ...],
        development_actor_set_commitment_sha256: str,
    ) -> Aihub538PhotometricPolicyV4:
        envelope = self._protocol.hard_safety_envelope
        usable = tuple(item for item in development if _hard_safety_pass(item, envelope))
        actors_with_usable = {item.actor_token_sha256 for item in usable}
        all_actors = {item.actor_token_sha256 for item in development}
        safe_counts: dict[str, int] = defaultdict(int)
        for item in usable:
            safe_counts[item.actor_token_sha256] += 1
        if actors_with_usable != all_actors or any(
            safe_counts[actor] < self._protocol.minimum_attempts_per_actor
            for actor in all_actors
        ):
            raise Aihub538PhotometricCalibrationV4Error(
                "aihub538_photometric_v4_development_actor_safe_attempts_insufficient"
            )
        metric_rows = tuple(_metric_values(item.metrics) for item in usable)
        normalizers: list[Aihub538PhotometricMetricNormalizerV4] = []
        for spec in AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS:
            metric_name = str(spec["metric_name"])
            values = [row[metric_name] for row in metric_rows]
            median = _quantile(values, 0.5)
            mad = _quantile([abs(value - median) for value in values], 0.5)
            normalizers.append(
                Aihub538PhotometricMetricNormalizerV4(
                    metric_name=metric_name,
                    group=str(spec["group"]),
                    direction=str(spec["direction"]),
                    development_median=median,
                    development_mad_scale=max(
                        1.4826 * mad,
                        float(spec["scale_floor"]),
                    ),
                )
            )
        normalizer_tuple = tuple(normalizers)
        development_scores = [
            _soft_score(
                row,
                normalizers=normalizer_tuple,
                contribution_cap=self._protocol.soft_metric_contribution_cap,
                full_frame_group_weight=self._protocol.full_frame_group_weight,
                lip_roi_group_weight=self._protocol.lip_roi_group_weight,
            )
            for row in metric_rows
        ]
        threshold = (
            _quantile(
                development_scores,
                self._protocol.soft_score_upper_quantile,
            )
            + self._protocol.soft_score_margin
        )
        return Aihub538PhotometricPolicyV4.issue(
            protocol_sha256=self._protocol.protocol_sha256,
            colorimetry_receipt_sha256=self._colorimetry_receipt.receipt_sha256,
            video_qc_measurement_policy_sha256=(
                self._protocol.video_qc_measurement_policy_sha256
            ),
            video_qc_measurement_revision=(
                self._protocol.video_qc_measurement_revision
            ),
            video_qc_measurement_adapter_code_sha256=(
                self._protocol.video_qc_measurement_adapter_code_sha256
            ),
            development_actor_set_commitment_sha256=(
                development_actor_set_commitment_sha256
            ),
            development_actor_count=len(all_actors),
            development_observation_count=len(usable),
            hard_safety_envelope=envelope,
            metric_normalizers=normalizer_tuple,
            soft_score_threshold=threshold,
            soft_score_upper_quantile=self._protocol.soft_score_upper_quantile,
            soft_score_margin=self._protocol.soft_score_margin,
            soft_metric_contribution_cap=(
                self._protocol.soft_metric_contribution_cap
            ),
            full_frame_group_weight=self._protocol.full_frame_group_weight,
            lip_roi_group_weight=self._protocol.lip_roi_group_weight,
        )


@dataclass(frozen=True, slots=True)
class _EvaluationRow:
    observation: Aihub538PhotometricObservationV4
    hard_safety_available: bool
    hard_safety_passed: bool
    soft_score: float | None
    soft_score_passed: bool


def _evaluate_partition(
    *,
    observations: tuple[Aihub538PhotometricObservationV4, ...],
    envelope: Aihub538HardSafetyEnvelopeV4,
    policy: Aihub538PhotometricPolicyV4,
) -> tuple[_EvaluationRow, ...]:
    results: list[_EvaluationRow] = []
    for observation in observations:
        available = _hard_safety_available(observation)
        hard_passed = _hard_safety_pass(observation, envelope)
        score = None
        soft_passed = False
        if hard_passed:
            score = _soft_score(
                _metric_values(observation.metrics),
                normalizers=policy.metric_normalizers,
                contribution_cap=policy.soft_metric_contribution_cap,
                full_frame_group_weight=policy.full_frame_group_weight,
                lip_roi_group_weight=policy.lip_roi_group_weight,
            )
            soft_passed = score <= policy.soft_score_threshold
        results.append(
            _EvaluationRow(
                observation=observation,
                hard_safety_available=available,
                hard_safety_passed=hard_passed,
                soft_score=score,
                soft_score_passed=soft_passed,
            )
        )
    return tuple(results)


def _hard_safety_available(observation: Aihub538PhotometricObservationV4) -> bool:
    metrics = observation.metrics
    return (
        observation.source_candidate_available
        and metrics.lip_roi_frame_count > 0
        and metrics.lip_roi_mean_luma_8bit is not None
        and metrics.lip_roi_underexposed_pixel_fraction is not None
        and metrics.lip_roi_overexposed_pixel_fraction is not None
        and metrics.maximum_lip_center_step_normalized is not None
    )


def _hard_safety_pass(
    observation: Aihub538PhotometricObservationV4,
    envelope: Aihub538HardSafetyEnvelopeV4,
) -> bool:
    if not _hard_safety_available(observation):
        return False
    metrics = observation.metrics
    assert metrics.lip_roi_underexposed_pixel_fraction is not None
    assert metrics.lip_roi_overexposed_pixel_fraction is not None
    assert metrics.maximum_lip_center_step_normalized is not None
    visibility = metrics.lip_roi_frame_count / metrics.frame_count
    return (
        metrics.single_face_coverage >= envelope.minimum_single_face_coverage
        and metrics.multiple_face_frame_fraction
        <= envelope.maximum_multiple_face_frame_fraction
        and metrics.underexposed_pixel_fraction
        <= envelope.maximum_catastrophic_underexposed_pixel_fraction
        and metrics.overexposed_pixel_fraction
        <= envelope.maximum_catastrophic_overexposed_pixel_fraction
        and metrics.lip_roi_underexposed_pixel_fraction
        <= envelope.maximum_catastrophic_underexposed_pixel_fraction
        and metrics.lip_roi_overexposed_pixel_fraction
        <= envelope.maximum_catastrophic_overexposed_pixel_fraction
        and visibility >= envelope.minimum_lip_roi_visibility_fraction
        and metrics.track_transition_coverage
        >= envelope.minimum_track_transition_coverage
        and metrics.longest_single_face_run_fraction
        >= envelope.minimum_longest_single_face_run_fraction
        and metrics.maximum_lip_center_step_normalized
        <= envelope.maximum_lip_center_step_normalized
    )


def _metric_values(metrics: VideoQcMetrics) -> dict[str, float]:
    if (
        metrics.lip_roi_mean_luma_8bit is None
        or metrics.lip_roi_underexposed_pixel_fraction is None
        or metrics.lip_roi_overexposed_pixel_fraction is None
    ):
        raise Aihub538PhotometricCalibrationV4Error(
            "aihub538_photometric_v4_lip_roi_measurement_unavailable"
        )
    return {
        "full_frame_mean_luma_8bit": metrics.mean_luma_8bit,
        "full_frame_underexposed_pixel_fraction": (
            metrics.underexposed_pixel_fraction
        ),
        "full_frame_overexposed_pixel_fraction": (
            metrics.overexposed_pixel_fraction
        ),
        "lip_roi_visibility_fraction": (
            metrics.lip_roi_frame_count / metrics.frame_count
        ),
        "lip_roi_mean_luma_8bit": metrics.lip_roi_mean_luma_8bit,
        "lip_roi_underexposed_pixel_fraction": (
            metrics.lip_roi_underexposed_pixel_fraction
        ),
        "lip_roi_overexposed_pixel_fraction": (
            metrics.lip_roi_overexposed_pixel_fraction
        ),
    }


def _soft_score(
    values: dict[str, float],
    *,
    normalizers: tuple[Aihub538PhotometricMetricNormalizerV4, ...],
    contribution_cap: float,
    full_frame_group_weight: float,
    lip_roi_group_weight: float,
) -> float:
    groups: dict[str, list[float]] = {"full_frame": [], "lip_roi": []}
    for normalizer in normalizers:
        observed = values[normalizer.metric_name]
        centered = observed - normalizer.development_median
        if normalizer.direction == "upper_only":
            deviation = max(0.0, centered)
        elif normalizer.direction == "lower_only":
            deviation = max(0.0, -centered)
        else:
            deviation = abs(centered)
        groups[normalizer.group].append(
            min(contribution_cap, deviation / normalizer.development_mad_scale)
        )
    full_score = sum(groups["full_frame"]) / len(groups["full_frame"])
    lip_score = sum(groups["lip_roi"]) / len(groups["lip_roi"])
    return full_frame_group_weight * full_score + lip_roi_group_weight * lip_score


def _partition_aggregate(
    *,
    role: str,
    results: tuple[_EvaluationRow, ...],
) -> Aihub538PhotometricPartitionAggregateV4:
    observations = tuple(item.observation for item in results)
    metrics = tuple(item.metrics for item in observations)
    lip_metrics = tuple(item for item in metrics if item.lip_roi_mean_luma_8bit is not None)
    by_actor: dict[str, list[_EvaluationRow]] = defaultdict(list)
    for row in results:
        by_actor[row.observation.actor_token_sha256].append(row)
    grouped: dict[tuple[int, int], int] = defaultdict(int)
    for actor_rows in by_actor.values():
        grouped[(len(actor_rows), sum(row.soft_score_passed for row in actor_rows))] += 1
    published = tuple(
        Aihub538ActorOutcomeCellV4(attempts, passed, count)
        for (attempts, passed), count in sorted(grouped.items()) if count >= 4
    )
    suppressed_groups = tuple(
        (attempts, passed, count)
        for (attempts, passed), count in grouped.items() if count < 4
    )
    distribution = Aihub538ActorOutcomeDistributionV4(
        actor_count=len(by_actor),
        attempt_count=len(results),
        passed_attempt_count=sum(row.soft_score_passed for row in results),
        passed_actor_count=sum(
            sum(row.soft_score_passed for row in rows) / len(rows) >= 0.75
            for rows in by_actor.values()
        ),
        published_cells=published,
        suppressed_actor_count=sum(count for _, _, count in suppressed_groups),
        suppressed_attempt_count=sum(attempts * count for attempts, _, count in suppressed_groups),
        suppressed_passed_attempt_count=sum(passed * count for _, passed, count in suppressed_groups),
        suppressed_passed_actor_count=sum(
            count for attempts, passed, count in suppressed_groups
            if passed / attempts >= 0.75
        ),
    )
    return Aihub538PhotometricPartitionAggregateV4(
        partition_role=role,
        selected_count=len(results),
        actor_count=len({item.actor_token_sha256 for item in observations}),
        hard_safety_available_count=sum(
            item.hard_safety_available for item in results
        ),
        colorimetry_verified_count=sum(
            item.observation.source_color_metadata_verified
            and item.observation.explicit_color_conversion_applied
            for item in results
        ),
        hard_safety_passed_count=sum(item.hard_safety_passed for item in results),
        soft_score_evaluated_count=sum(
            item.soft_score is not None for item in results
        ),
        soft_score_passed_count=sum(item.soft_score_passed for item in results),
        full_frame_exposure=Aihub538FullFrameExposureAggregateV4(
            measurement_count=len(metrics),
            mean_luma_8bit=_rounded_mean(tuple(item.mean_luma_8bit for item in metrics), 1.0),
            mean_underexposed_pixel_fraction=_rounded_mean(
                tuple(item.underexposed_pixel_fraction for item in metrics)
            , 0.01),
            mean_overexposed_pixel_fraction=_rounded_mean(
                tuple(item.overexposed_pixel_fraction for item in metrics)
            , 0.01),
        ),
        lip_roi_exposure_visibility=(
            Aihub538LipRoiExposureVisibilityAggregateV4(
                visibility_measurement_count=len(metrics),
                exposure_measurement_count=len(lip_metrics),
                mean_visibility_fraction=_rounded_mean(
                    tuple(item.lip_roi_frame_count / item.frame_count for item in metrics)
                , 0.01),
                mean_luma_8bit=_rounded_mean(
                    tuple(
                        item.lip_roi_mean_luma_8bit
                        for item in lip_metrics
                        if item.lip_roi_mean_luma_8bit is not None
                    ), 1.0
                ),
                mean_underexposed_pixel_fraction=_rounded_mean(
                    tuple(
                        item.lip_roi_underexposed_pixel_fraction
                        for item in lip_metrics
                        if item.lip_roi_underexposed_pixel_fraction is not None
                    ), 0.01
                ),
                mean_overexposed_pixel_fraction=_rounded_mean(
                    tuple(
                        item.lip_roi_overexposed_pixel_fraction
                        for item in lip_metrics
                        if item.lip_roi_overexposed_pixel_fraction is not None
                    ), 0.01
                ),
            )
        ),
        actor_outcome_distribution=distribution,
    )


def _actor_clustered_ci(
    results: tuple[_EvaluationRow, ...],
    *,
    protocol: Aihub538PhotometricCalibrationProtocolV4,
) -> Aihub538ActorClusteredOneSidedCiV4:
    by_actor: dict[str, list[_EvaluationRow]] = defaultdict(list)
    for item in results:
        by_actor[item.observation.actor_token_sha256].append(item)
    passed_actor_count = sum(
        sum(row.soft_score_passed for row in actor_rows) / len(actor_rows)
        >= protocol.minimum_actor_attempt_pass_fraction
        for actor_rows in by_actor.values()
    )
    actor_count = len(by_actor)
    observed = passed_actor_count / actor_count
    z_score = protocol.actor_ci_z_score
    denominator = 1.0 + z_score * z_score / actor_count
    center = observed + z_score * z_score / (2.0 * actor_count)
    radius = z_score * math.sqrt(
        observed * (1.0 - observed) / actor_count
        + z_score * z_score / (4.0 * actor_count * actor_count)
    )
    lower_bound = max(0.0, (center - radius) / denominator)
    return Aihub538ActorClusteredOneSidedCiV4(
        actor_count=actor_count,
        passed_actor_count=passed_actor_count,
        minimum_actor_attempt_pass_fraction=(
            protocol.minimum_actor_attempt_pass_fraction
        ),
        confidence_level=protocol.actor_ci_confidence_level,
        z_score=z_score,
        observed_actor_pass_fraction=observed,
        lower_bound=lower_bound,
        minimum_required_lower_bound=protocol.minimum_actor_ci_lower_bound,
        passed=lower_bound >= protocol.minimum_actor_ci_lower_bound,
    )


def _validated_identity_set(
    values: tuple[str, ...], reason_code: str
) -> set[str]:
    if not values:
        raise Aihub538PhotometricCalibrationV4Error(reason_code)
    try:
        result = {require_sha256(value, "identity_token_sha256") for value in values}
    except VisualFeedbackContractError:
        raise Aihub538PhotometricCalibrationV4Error(reason_code) from None
    if len(result) != len(values):
        raise Aihub538PhotometricCalibrationV4Error(reason_code)
    return result


def _identity_set_commitment(domain: str, values: set[str]) -> str:
    if not values:
        raise Aihub538PhotometricCalibrationV4Error(
            "aihub538_photometric_v4_identity_set_empty"
        )
    return sha256_document(
        {
            "schema_version": "voice_coach.identity_set_commitment.v1",
            "domain": domain,
            "count": len(values),
            "sorted_token_commitments": sorted(values),
        }
    )


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else math.fsum(values) / len(values)


def _rounded_mean(values: tuple[float, ...], step: float) -> float | None:
    mean = _mean(values)
    return None if mean is None else round(mean / step) * step


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise Aihub538PhotometricCalibrationV4Error(
            "aihub538_photometric_v4_quantile_population_empty"
        )
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + fraction * (
        ordered[upper_index] - ordered[lower_index]
    )


__all__ = [
    "Aihub538PhotometricCalibrationInputV4",
    "Aihub538PhotometricCalibrationV4Error",
    "Aihub538PhotometricCalibrationV4Service",
    "Aihub538PhotometricCohortCapabilityV4",
    "Aihub538PhotometricCohortMemberV4",
    "Aihub538PhotometricMeasurementV4",
    "bind_aihub538_photometric_measurements_v4",
    "issue_aihub538_photometric_cohort_capability_v4",
]
