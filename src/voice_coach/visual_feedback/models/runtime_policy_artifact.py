"""Strict semantic policy artifacts used to pin the P8 runtime pipeline.

Raw JSON bytes are not a policy identity: harmless serialization changes would
change a file hash while leaving policy semantics unchanged.  These contracts
parse the two supported policy documents, digest their canonical semantics,
and separately self-digest the revision-bearing artifact wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)

P8_RUNTIME_POLICY_ARTIFACT_SCHEMA_VERSION = (
    "voice_coach.p8_runtime_policy_artifact.v3"
)
RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION = (
    "voice_coach.runtime_decision_policy_evidence.v1"
)
RUNTIME_DECISION_POLICY_SOURCE_PARTITIONS = ("TS174", "TL174")
VIDEO_QC_POLICY_DOCUMENT_SCHEMA_VERSION = (
    "voice_coach.video_qc_measurement_policy.v1"
)
AV_SYNC_POLICY_DOCUMENT_SCHEMA_VERSION = (
    "voice_coach.lip_audio_av_sync_policy.v1"
)
PHONE_WINDOW_POLICY_DOCUMENT_SCHEMA_VERSION = (
    "voice_coach.phone_window_decode_policy.v1"
)
VIDEO_QC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION = (
    "voice_coach.calibrated_video_qc_decision_policy.v1"
)
AV_SYNC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION = (
    "voice_coach.calibrated_av_sync_decision_policy.v1"
)


@dataclass(frozen=True, slots=True)
class VideoQcSemanticPolicy:
    """Exact semantic input consumed by the video-QC measurement adapter."""

    underexposed_luma_max_8bit: int
    overexposed_luma_min_8bit: int
    lip_roi_padding_fraction: float
    minimum_single_face_frames: int
    maximum_total_rgb_bytes: int
    schema_version: str = VIDEO_QC_POLICY_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VIDEO_QC_POLICY_DOCUMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("video QC policy schema differs")
        underexposed = require_integer(
            self.underexposed_luma_max_8bit,
            "underexposed_luma_max_8bit",
            minimum=0,
        )
        overexposed = require_integer(
            self.overexposed_luma_min_8bit,
            "overexposed_luma_min_8bit",
            minimum=0,
        )
        if underexposed > 255 or overexposed > 255 or underexposed >= overexposed:
            raise VisualFeedbackContractError("video QC luma thresholds differ")
        padding = require_finite_number(
            self.lip_roi_padding_fraction, "lip_roi_padding_fraction"
        )
        if not 0.0 <= padding <= 0.25:
            raise VisualFeedbackContractError(
                "video QC lip ROI padding is outside policy"
            )
        require_integer(
            self.minimum_single_face_frames,
            "minimum_single_face_frames",
            minimum=1,
        )
        require_integer(
            self.maximum_total_rgb_bytes,
            "maximum_total_rgb_bytes",
            minimum=1,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "underexposed_luma_max_8bit": self.underexposed_luma_max_8bit,
            "overexposed_luma_min_8bit": self.overexposed_luma_min_8bit,
            "lip_roi_padding_fraction": self.lip_roi_padding_fraction,
            "minimum_single_face_frames": self.minimum_single_face_frames,
            "maximum_total_rgb_bytes": self.maximum_total_rgb_bytes,
        }

    @classmethod
    def from_json_object(cls, value: object) -> VideoQcSemanticPolicy:
        document = require_mapping(value, "video_qc_semantic_policy")
        require_exact_keys(
            document,
            {
                "schema_version",
                "underexposed_luma_max_8bit",
                "overexposed_luma_min_8bit",
                "lip_roi_padding_fraction",
                "minimum_single_face_frames",
                "maximum_total_rgb_bytes",
            },
            "video_qc_semantic_policy",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            underexposed_luma_max_8bit=require_integer(
                document["underexposed_luma_max_8bit"],
                "underexposed_luma_max_8bit",
                minimum=0,
            ),
            overexposed_luma_min_8bit=require_integer(
                document["overexposed_luma_min_8bit"],
                "overexposed_luma_min_8bit",
                minimum=0,
            ),
            lip_roi_padding_fraction=require_finite_number(
                document["lip_roi_padding_fraction"],
                "lip_roi_padding_fraction",
            ),
            minimum_single_face_frames=require_integer(
                document["minimum_single_face_frames"],
                "minimum_single_face_frames",
                minimum=1,
            ),
            maximum_total_rgb_bytes=require_integer(
                document["maximum_total_rgb_bytes"],
                "maximum_total_rgb_bytes",
                minimum=1,
            ),
        )


@dataclass(frozen=True, slots=True)
class AvSyncSemanticPolicy:
    """Exact semantic input consumed by the lip/audio sync adapter."""

    maximum_absolute_offset_ms: int
    offset_step_ms: int
    energy_window_ms: int
    minimum_landmark_frames: int
    maximum_anchor_count: int
    schema_version: str = AV_SYNC_POLICY_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "maximum_absolute_offset_ms",
            "offset_step_ms",
            "energy_window_ms",
            "minimum_landmark_frames",
            "maximum_anchor_count",
        ):
            require_integer(getattr(self, name), name, minimum=1)
        if self.schema_version != AV_SYNC_POLICY_DOCUMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("A/V sync policy schema differs")
        if self.offset_step_ms > self.maximum_absolute_offset_ms:
            raise VisualFeedbackContractError(
                "A/V sync offset step exceeds search range"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "maximum_absolute_offset_ms": self.maximum_absolute_offset_ms,
            "offset_step_ms": self.offset_step_ms,
            "energy_window_ms": self.energy_window_ms,
            "minimum_landmark_frames": self.minimum_landmark_frames,
            "maximum_anchor_count": self.maximum_anchor_count,
        }

    @classmethod
    def from_json_object(cls, value: object) -> AvSyncSemanticPolicy:
        document = require_mapping(value, "av_sync_semantic_policy")
        require_exact_keys(
            document,
            {
                "schema_version",
                "maximum_absolute_offset_ms",
                "offset_step_ms",
                "energy_window_ms",
                "minimum_landmark_frames",
                "maximum_anchor_count",
            },
            "av_sync_semantic_policy",
        )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            maximum_absolute_offset_ms=require_integer(
                document["maximum_absolute_offset_ms"],
                "maximum_absolute_offset_ms",
                minimum=1,
            ),
            offset_step_ms=require_integer(
                document["offset_step_ms"], "offset_step_ms", minimum=1
            ),
            energy_window_ms=require_integer(
                document["energy_window_ms"], "energy_window_ms", minimum=1
            ),
            minimum_landmark_frames=require_integer(
                document["minimum_landmark_frames"],
                "minimum_landmark_frames",
                minimum=1,
            ),
            maximum_anchor_count=require_integer(
                document["maximum_anchor_count"],
                "maximum_anchor_count",
                minimum=1,
            ),
        )


@dataclass(frozen=True, slots=True)
class VideoQcDecisionSemanticPolicy:
    """Exact calibrated policy interpreted by the QC decision wrapper."""

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
    schema_version: str = VIDEO_QC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != VIDEO_QC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "calibrated video QC decision policy schema differs"
            )
        require_safe_identifier(self.policy_revision, "policy_revision")
        require_sha256(
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
            value = require_finite_number(getattr(self, name), name)
            if not 0.0 <= value <= 1.0:
                raise VisualFeedbackContractError(
                    f"{name} must be finite in [0, 1]"
                )
        for name in (
            "minimum_mean_luma_8bit",
            "maximum_mean_luma_8bit",
            "minimum_lip_roi_mean_neighbor_luma_delta_8bit",
        ):
            value = require_finite_number(getattr(self, name), name)
            if not 0.0 <= value <= 255.0:
                raise VisualFeedbackContractError(
                    f"{name} must be finite in [0, 255]"
                )
        if self.minimum_mean_luma_8bit > self.maximum_mean_luma_8bit:
            raise VisualFeedbackContractError(
                "calibrated video QC luma bounds differ"
            )
        maximum_step = require_finite_number(
            self.maximum_lip_center_step_normalized,
            "maximum_lip_center_step_normalized",
        )
        if not 0.0 <= maximum_step <= 2.0**0.5:
            raise VisualFeedbackContractError(
                "maximum_lip_center_step_normalized is outside policy"
            )

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

    def to_runtime_policy(self) -> object:
        """Strictly recreate the exact production decision-policy type."""

        from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
            CalibratedVideoQcDecisionPolicy,
        )

        policy = CalibratedVideoQcDecisionPolicy(
            **{
                key: value
                for key, value in self.to_json_object().items()
                if key != "schema_version"
            }
        )
        if policy.policy_sha256 != sha256_document(self.to_json_object()):
            raise VisualFeedbackContractError(
                "video QC decision policy runtime conversion differs"
            )
        return policy

    @classmethod
    def from_json_object(cls, value: object) -> VideoQcDecisionSemanticPolicy:
        document = require_mapping(value, "video_qc_decision_semantic_policy")
        fields = {
            "schema_version",
            "policy_revision",
            "expected_measurement_policy_sha256",
            "minimum_single_face_coverage",
            "maximum_multiple_face_frame_fraction",
            "minimum_mean_luma_8bit",
            "maximum_mean_luma_8bit",
            "maximum_underexposed_pixel_fraction",
            "maximum_overexposed_pixel_fraction",
            "minimum_lip_roi_mean_neighbor_luma_delta_8bit",
            "minimum_track_transition_coverage",
            "minimum_longest_single_face_run_fraction",
            "maximum_lip_center_step_normalized",
        }
        require_exact_keys(document, fields, "video_qc_decision_semantic_policy")
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            policy_revision=require_safe_identifier(
                document["policy_revision"], "policy_revision"
            ),
            expected_measurement_policy_sha256=require_sha256(
                document["expected_measurement_policy_sha256"],
                "expected_measurement_policy_sha256",
            ),
            **{
                name: require_finite_number(document[name], name)
                for name in fields
                if name
                not in {
                    "schema_version",
                    "policy_revision",
                    "expected_measurement_policy_sha256",
                }
            },
        )


@dataclass(frozen=True, slots=True)
class AvSyncDecisionSemanticPolicy:
    """Exact calibrated policy interpreted by the sync decision wrapper."""

    policy_revision: str
    expected_measurement_policy_sha256: str
    maximum_absolute_offset_ms: int
    minimum_correlation: float
    minimum_anchor_count: int
    minimum_anchor_strength: float
    schema_version: str = AV_SYNC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AV_SYNC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "calibrated A/V sync decision policy schema differs"
            )
        require_safe_identifier(self.policy_revision, "policy_revision")
        require_sha256(
            self.expected_measurement_policy_sha256,
            "expected_measurement_policy_sha256",
        )
        require_integer(
            self.maximum_absolute_offset_ms,
            "maximum_absolute_offset_ms",
            minimum=1,
        )
        correlation = require_finite_number(
            self.minimum_correlation, "minimum_correlation"
        )
        if not -1.0 <= correlation <= 1.0:
            raise VisualFeedbackContractError(
                "minimum_correlation must be finite in [-1, 1]"
            )
        require_integer(self.minimum_anchor_count, "minimum_anchor_count", minimum=1)
        strength = require_finite_number(
            self.minimum_anchor_strength, "minimum_anchor_strength"
        )
        if not 0.0 <= strength <= 1.0:
            raise VisualFeedbackContractError(
                "minimum_anchor_strength must be finite in [0, 1]"
            )

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

    def to_runtime_policy(self) -> object:
        """Strictly recreate the exact production decision-policy type."""

        from ...pronunciation_attempts.adapters.calibrated_runtime_decisions import (
            CalibratedAvSyncDecisionPolicy,
        )

        policy = CalibratedAvSyncDecisionPolicy(
            **{
                key: value
                for key, value in self.to_json_object().items()
                if key != "schema_version"
            }
        )
        if policy.policy_sha256 != sha256_document(self.to_json_object()):
            raise VisualFeedbackContractError(
                "A/V sync decision policy runtime conversion differs"
            )
        return policy

    @classmethod
    def from_json_object(cls, value: object) -> AvSyncDecisionSemanticPolicy:
        document = require_mapping(value, "av_sync_decision_semantic_policy")
        fields = {
            "schema_version",
            "policy_revision",
            "expected_measurement_policy_sha256",
            "maximum_absolute_offset_ms",
            "minimum_correlation",
            "minimum_anchor_count",
            "minimum_anchor_strength",
        }
        require_exact_keys(document, fields, "av_sync_decision_semantic_policy")
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            policy_revision=require_safe_identifier(
                document["policy_revision"], "policy_revision"
            ),
            expected_measurement_policy_sha256=require_sha256(
                document["expected_measurement_policy_sha256"],
                "expected_measurement_policy_sha256",
            ),
            maximum_absolute_offset_ms=require_integer(
                document["maximum_absolute_offset_ms"],
                "maximum_absolute_offset_ms",
                minimum=1,
            ),
            minimum_correlation=require_finite_number(
                document["minimum_correlation"], "minimum_correlation"
            ),
            minimum_anchor_count=require_integer(
                document["minimum_anchor_count"],
                "minimum_anchor_count",
                minimum=1,
            ),
            minimum_anchor_strength=require_finite_number(
                document["minimum_anchor_strength"], "minimum_anchor_strength"
            ),
        )


@dataclass(frozen=True, slots=True)
class PhoneWindowSemanticPolicy:
    """Exact semantic input consumed by the dense selected-phone decoder."""

    policy_revision: str
    expected_av_sync_policy_sha256: str
    context_contract_sha256: str
    maximum_absolute_offset_ms: int
    padding_before_ms: int
    padding_after_ms: int
    minimum_frame_count: int
    maximum_frame_count: int
    maximum_window_duration_ms: int
    maximum_rgb_bytes: int
    schema_version: str = PHONE_WINDOW_POLICY_DOCUMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PHONE_WINDOW_POLICY_DOCUMENT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("phone-window policy schema differs")
        require_safe_identifier(self.policy_revision, "policy_revision")
        require_sha256(
            self.expected_av_sync_policy_sha256,
            "expected_av_sync_policy_sha256",
        )
        require_sha256(self.context_contract_sha256, "context_contract_sha256")
        for name, minimum in (
            ("maximum_absolute_offset_ms", 1),
            ("padding_before_ms", 0),
            ("padding_after_ms", 0),
            ("minimum_frame_count", 1),
            ("maximum_frame_count", 1),
            ("maximum_window_duration_ms", 1),
            ("maximum_rgb_bytes", 1),
        ):
            require_integer(getattr(self, name), name, minimum=minimum)
        if self.maximum_frame_count < self.minimum_frame_count:
            raise VisualFeedbackContractError(
                "phone-window maximum frame count differs"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "expected_av_sync_policy_sha256": (
                self.expected_av_sync_policy_sha256
            ),
            "context_contract_sha256": self.context_contract_sha256,
            "maximum_absolute_offset_ms": self.maximum_absolute_offset_ms,
            "padding_before_ms": self.padding_before_ms,
            "padding_after_ms": self.padding_after_ms,
            "minimum_frame_count": self.minimum_frame_count,
            "maximum_frame_count": self.maximum_frame_count,
            "maximum_window_duration_ms": self.maximum_window_duration_ms,
            "maximum_rgb_bytes": self.maximum_rgb_bytes,
        }

    @classmethod
    def from_json_object(cls, value: object) -> PhoneWindowSemanticPolicy:
        document = require_mapping(value, "phone_window_semantic_policy")
        fields = {
            "schema_version",
            "policy_revision",
            "expected_av_sync_policy_sha256",
            "context_contract_sha256",
            "maximum_absolute_offset_ms",
            "padding_before_ms",
            "padding_after_ms",
            "minimum_frame_count",
            "maximum_frame_count",
            "maximum_window_duration_ms",
            "maximum_rgb_bytes",
        }
        require_exact_keys(document, fields, "phone_window_semantic_policy")
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            policy_revision=require_safe_identifier(
                document["policy_revision"], "policy_revision"
            ),
            expected_av_sync_policy_sha256=require_sha256(
                document["expected_av_sync_policy_sha256"],
                "expected_av_sync_policy_sha256",
            ),
            context_contract_sha256=require_sha256(
                document["context_contract_sha256"],
                "context_contract_sha256",
            ),
            **{
                name: require_integer(
                    document[name], name, minimum=0 if "padding" in name else 1
                )
                for name in fields
                if name
                not in {
                    "schema_version",
                    "policy_revision",
                    "expected_av_sync_policy_sha256",
                    "context_contract_sha256",
                }
            },
        )


SemanticPolicyDocument = (
    VideoQcSemanticPolicy
    | AvSyncSemanticPolicy
    | VideoQcDecisionSemanticPolicy
    | AvSyncDecisionSemanticPolicy
    | PhoneWindowSemanticPolicy
)


@dataclass(frozen=True, slots=True)
class P8RuntimePolicyArtifact:
    """Revision-bearing wrapper around one canonical runtime policy."""

    policy_kind: str
    policy_revision: str
    policy_document: SemanticPolicyDocument
    policy_sha256: str
    artifact_sha256: str
    schema_version: str = P8_RUNTIME_POLICY_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_RUNTIME_POLICY_ARTIFACT_SCHEMA_VERSION:
            raise VisualFeedbackContractError("runtime policy artifact schema differs")
        require_safe_identifier(self.policy_revision, "policy_revision")
        expected_type = {
            "video_qc": VideoQcSemanticPolicy,
            "av_sync": AvSyncSemanticPolicy,
            "video_qc_decision": VideoQcDecisionSemanticPolicy,
            "av_sync_decision": AvSyncDecisionSemanticPolicy,
            "phone_window": PhoneWindowSemanticPolicy,
        }.get(self.policy_kind)
        if expected_type is None or not isinstance(self.policy_document, expected_type):
            raise VisualFeedbackContractError(
                "runtime policy kind and semantic document differ"
            )
        if (
            isinstance(
                self.policy_document,
                (
                    PhoneWindowSemanticPolicy,
                    VideoQcDecisionSemanticPolicy,
                    AvSyncDecisionSemanticPolicy,
                ),
            )
            and self.policy_revision != self.policy_document.policy_revision
        ):
            raise VisualFeedbackContractError(
                "runtime policy wrapper revision differs"
            )
        require_sha256(self.policy_sha256, "policy_sha256")
        if self.policy_sha256 != sha256_document(
            self.policy_document.to_json_object()
        ):
            raise VisualFeedbackContractError("runtime semantic policy digest differs")
        require_sha256(self.artifact_sha256, "artifact_sha256")
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("runtime policy artifact digest differs")

    @classmethod
    def issue(
        cls,
        *,
        policy_kind: str,
        policy_revision: str,
        policy_document: SemanticPolicyDocument,
    ) -> P8RuntimePolicyArtifact:
        policy_sha256 = sha256_document(policy_document.to_json_object())
        unsigned = {
            "schema_version": P8_RUNTIME_POLICY_ARTIFACT_SCHEMA_VERSION,
            "policy_kind": policy_kind,
            "policy_revision": policy_revision,
            "policy_document": policy_document.to_json_object(),
            "policy_sha256": policy_sha256,
        }
        return cls(
            policy_kind=policy_kind,
            policy_revision=policy_revision,
            policy_document=policy_document,
            policy_sha256=policy_sha256,
            artifact_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_kind": self.policy_kind,
            "policy_revision": self.policy_revision,
            "policy_document": self.policy_document.to_json_object(),
            "policy_sha256": self.policy_sha256,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> P8RuntimePolicyArtifact:
        document = require_mapping(value, "p8_runtime_policy_artifact")
        require_exact_keys(
            document,
            {
                "schema_version",
                "policy_kind",
                "policy_revision",
                "policy_document",
                "policy_sha256",
                "artifact_sha256",
            },
            "p8_runtime_policy_artifact",
        )
        policy_kind = require_safe_identifier(
            document["policy_kind"], "policy_kind"
        )
        if policy_kind == "video_qc":
            policy_document: SemanticPolicyDocument = (
                VideoQcSemanticPolicy.from_json_object(
                    document["policy_document"]
                )
            )
        elif policy_kind == "av_sync":
            policy_document = AvSyncSemanticPolicy.from_json_object(
                document["policy_document"]
            )
        elif policy_kind == "video_qc_decision":
            policy_document = VideoQcDecisionSemanticPolicy.from_json_object(
                document["policy_document"]
            )
        elif policy_kind == "av_sync_decision":
            policy_document = AvSyncDecisionSemanticPolicy.from_json_object(
                document["policy_document"]
            )
        elif policy_kind == "phone_window":
            policy_document = PhoneWindowSemanticPolicy.from_json_object(
                document["policy_document"]
            )
        else:
            raise VisualFeedbackContractError("runtime policy kind differs")
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            policy_kind=policy_kind,
            policy_revision=require_safe_identifier(
                document["policy_revision"], "policy_revision"
            ),
            policy_document=policy_document,
            policy_sha256=require_sha256(
                document["policy_sha256"], "policy_sha256"
            ),
            artifact_sha256=require_sha256(
                document["artifact_sha256"], "artifact_sha256"
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeDecisionPolicyEvidence:
    """Aggregate proof that actual TS174/TL174 media calibrated both decisions."""

    source_manifest_sha256: str
    admission_receipt_sha256: str
    evaluation_protocol_sha256: str
    evaluator_code_sha256: str
    video_qc_policy_artifact_sha256: str
    video_qc_policy_sha256: str
    av_sync_policy_artifact_sha256: str
    av_sync_policy_sha256: str
    admitted_item_count: int
    evaluated_item_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    qc_decision_lineage_valid_item_count: int
    sync_decision_lineage_valid_item_count: int
    fresh_process_reload_receipt_sha256: str
    frozen: bool
    fresh_process_reload_passed: bool
    status: str
    receipt_sha256: str
    source_dataset_id: str = "aihub_538"
    source_partitions: tuple[str, ...] = RUNTIME_DECISION_POLICY_SOURCE_PARTITIONS
    schema_version: str = RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "runtime decision policy evidence schema differs"
            )
        if self.source_dataset_id != "aihub_538":
            raise VisualFeedbackContractError(
                "runtime decision policy source dataset differs"
            )
        if self.source_partitions != RUNTIME_DECISION_POLICY_SOURCE_PARTITIONS:
            raise VisualFeedbackContractError(
                "runtime decision policy source partitions differ"
            )
        for name in (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "video_qc_policy_artifact_sha256",
            "video_qc_policy_sha256",
            "av_sync_policy_artifact_sha256",
            "av_sync_policy_sha256",
            "fresh_process_reload_receipt_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        admitted = require_integer(
            self.admitted_item_count, "admitted_item_count", minimum=1
        )
        evaluated = require_integer(
            self.evaluated_item_count, "evaluated_item_count", minimum=1
        )
        if admitted != evaluated:
            raise VisualFeedbackContractError(
                "runtime decision policy evaluation is incomplete"
            )
        for name in ("actor_overlap_count", "attempt_overlap_count"):
            if require_integer(getattr(self, name), name, minimum=0) != 0:
                raise VisualFeedbackContractError(
                    "runtime decision policy partition overlap detected"
                )
        for name in (
            "qc_decision_lineage_valid_item_count",
            "sync_decision_lineage_valid_item_count",
        ):
            if require_integer(getattr(self, name), name, minimum=0) != evaluated:
                raise VisualFeedbackContractError(
                    "runtime decision policy lineage evaluation is incomplete"
                )
        if require_boolean(self.frozen, "frozen") is not True:
            raise VisualFeedbackContractError(
                "runtime decision policy evidence is not frozen"
            )
        if (
            require_boolean(
                self.fresh_process_reload_passed,
                "fresh_process_reload_passed",
            )
            is not True
        ):
            raise VisualFeedbackContractError(
                "runtime decision policy fresh reload did not pass"
            )
        if self.status != "actual_media_calibrated":
            raise VisualFeedbackContractError(
                "runtime decision policy evidence status differs"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "runtime decision policy evidence digest differs"
            )

    @classmethod
    def _issue_from_validated_actual_evaluation(
        cls,
        *,
        source_manifest_sha256: str,
        admission_receipt_sha256: str,
        evaluation_protocol_sha256: str,
        evaluator_code_sha256: str,
        video_qc_policy: P8RuntimePolicyArtifact,
        av_sync_policy: P8RuntimePolicyArtifact,
        admitted_item_count: int,
        evaluated_item_count: int,
        actor_overlap_count: int,
        attempt_overlap_count: int,
        qc_decision_lineage_valid_item_count: int,
        sync_decision_lineage_valid_item_count: int,
        fresh_process_reload_receipt_sha256: str,
    ) -> RuntimeDecisionPolicyEvidence:
        if video_qc_policy.policy_kind != "video_qc_decision":
            raise VisualFeedbackContractError(
                "QC decision policy artifact kind differs"
            )
        if av_sync_policy.policy_kind != "av_sync_decision":
            raise VisualFeedbackContractError(
                "A/V decision policy artifact kind differs"
            )
        unsigned = {
            "schema_version": RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION,
            "source_dataset_id": "aihub_538",
            "source_partitions": list(RUNTIME_DECISION_POLICY_SOURCE_PARTITIONS),
            "source_manifest_sha256": source_manifest_sha256,
            "admission_receipt_sha256": admission_receipt_sha256,
            "evaluation_protocol_sha256": evaluation_protocol_sha256,
            "evaluator_code_sha256": evaluator_code_sha256,
            "video_qc_policy_artifact_sha256": video_qc_policy.artifact_sha256,
            "video_qc_policy_sha256": video_qc_policy.policy_sha256,
            "av_sync_policy_artifact_sha256": av_sync_policy.artifact_sha256,
            "av_sync_policy_sha256": av_sync_policy.policy_sha256,
            "admitted_item_count": admitted_item_count,
            "evaluated_item_count": evaluated_item_count,
            "actor_overlap_count": actor_overlap_count,
            "attempt_overlap_count": attempt_overlap_count,
            "qc_decision_lineage_valid_item_count": (
                qc_decision_lineage_valid_item_count
            ),
            "sync_decision_lineage_valid_item_count": (
                sync_decision_lineage_valid_item_count
            ),
            "fresh_process_reload_receipt_sha256": (
                fresh_process_reload_receipt_sha256
            ),
            "frozen": True,
            "fresh_process_reload_passed": True,
            "status": "actual_media_calibrated",
        }
        return cls(
            source_manifest_sha256=source_manifest_sha256,
            admission_receipt_sha256=admission_receipt_sha256,
            evaluation_protocol_sha256=evaluation_protocol_sha256,
            evaluator_code_sha256=evaluator_code_sha256,
            video_qc_policy_artifact_sha256=video_qc_policy.artifact_sha256,
            video_qc_policy_sha256=video_qc_policy.policy_sha256,
            av_sync_policy_artifact_sha256=av_sync_policy.artifact_sha256,
            av_sync_policy_sha256=av_sync_policy.policy_sha256,
            admitted_item_count=admitted_item_count,
            evaluated_item_count=evaluated_item_count,
            actor_overlap_count=actor_overlap_count,
            attempt_overlap_count=attempt_overlap_count,
            qc_decision_lineage_valid_item_count=(
                qc_decision_lineage_valid_item_count
            ),
            sync_decision_lineage_valid_item_count=(
                sync_decision_lineage_valid_item_count
            ),
            fresh_process_reload_receipt_sha256=(
                fresh_process_reload_receipt_sha256
            ),
            frozen=True,
            fresh_process_reload_passed=True,
            status="actual_media_calibrated",
            receipt_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_dataset_id": self.source_dataset_id,
            "source_partitions": list(self.source_partitions),
            "source_manifest_sha256": self.source_manifest_sha256,
            "admission_receipt_sha256": self.admission_receipt_sha256,
            "evaluation_protocol_sha256": self.evaluation_protocol_sha256,
            "evaluator_code_sha256": self.evaluator_code_sha256,
            "video_qc_policy_artifact_sha256": (
                self.video_qc_policy_artifact_sha256
            ),
            "video_qc_policy_sha256": self.video_qc_policy_sha256,
            "av_sync_policy_artifact_sha256": self.av_sync_policy_artifact_sha256,
            "av_sync_policy_sha256": self.av_sync_policy_sha256,
            "admitted_item_count": self.admitted_item_count,
            "evaluated_item_count": self.evaluated_item_count,
            "actor_overlap_count": self.actor_overlap_count,
            "attempt_overlap_count": self.attempt_overlap_count,
            "qc_decision_lineage_valid_item_count": (
                self.qc_decision_lineage_valid_item_count
            ),
            "sync_decision_lineage_valid_item_count": (
                self.sync_decision_lineage_valid_item_count
            ),
            "fresh_process_reload_receipt_sha256": (
                self.fresh_process_reload_receipt_sha256
            ),
            "frozen": self.frozen,
            "fresh_process_reload_passed": self.fresh_process_reload_passed,
            "status": self.status,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> RuntimeDecisionPolicyEvidence:
        document = require_mapping(value, "runtime_decision_policy_evidence")
        fields = {
            "schema_version",
            "source_dataset_id",
            "source_partitions",
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "evaluation_protocol_sha256",
            "evaluator_code_sha256",
            "video_qc_policy_artifact_sha256",
            "video_qc_policy_sha256",
            "av_sync_policy_artifact_sha256",
            "av_sync_policy_sha256",
            "admitted_item_count",
            "evaluated_item_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "qc_decision_lineage_valid_item_count",
            "sync_decision_lineage_valid_item_count",
            "fresh_process_reload_receipt_sha256",
            "frozen",
            "fresh_process_reload_passed",
            "status",
            "receipt_sha256",
        }
        require_exact_keys(document, fields, "runtime_decision_policy_evidence")
        partitions = document["source_partitions"]
        if not isinstance(partitions, list):
            raise VisualFeedbackContractError(
                "runtime decision policy source partitions must be an array"
            )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            source_dataset_id=require_safe_identifier(
                document["source_dataset_id"], "source_dataset_id"
            ),
            source_partitions=tuple(
                require_safe_identifier(item, "source_partitions[]")
                for item in partitions
            ),
            status=require_safe_identifier(document["status"], "status"),
            admitted_item_count=require_integer(
                document["admitted_item_count"], "admitted_item_count", minimum=1
            ),
            evaluated_item_count=require_integer(
                document["evaluated_item_count"], "evaluated_item_count", minimum=1
            ),
            actor_overlap_count=require_integer(
                document["actor_overlap_count"], "actor_overlap_count", minimum=0
            ),
            attempt_overlap_count=require_integer(
                document["attempt_overlap_count"],
                "attempt_overlap_count",
                minimum=0,
            ),
            qc_decision_lineage_valid_item_count=require_integer(
                document["qc_decision_lineage_valid_item_count"],
                "qc_decision_lineage_valid_item_count",
                minimum=0,
            ),
            sync_decision_lineage_valid_item_count=require_integer(
                document["sync_decision_lineage_valid_item_count"],
                "sync_decision_lineage_valid_item_count",
                minimum=0,
            ),
            frozen=require_boolean(document["frozen"], "frozen"),
            fresh_process_reload_passed=require_boolean(
                document["fresh_process_reload_passed"],
                "fresh_process_reload_passed",
            ),
            **{
                name: require_sha256(document[name], name)
                for name in fields
                if name.endswith("_sha256")
            },
        )


__all__ = [
    "AV_SYNC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION",
    "AV_SYNC_POLICY_DOCUMENT_SCHEMA_VERSION",
    "P8_RUNTIME_POLICY_ARTIFACT_SCHEMA_VERSION",
    "RUNTIME_DECISION_POLICY_EVIDENCE_SCHEMA_VERSION",
    "RUNTIME_DECISION_POLICY_SOURCE_PARTITIONS",
    "PHONE_WINDOW_POLICY_DOCUMENT_SCHEMA_VERSION",
    "VIDEO_QC_POLICY_DOCUMENT_SCHEMA_VERSION",
    "VIDEO_QC_DECISION_POLICY_DOCUMENT_SCHEMA_VERSION",
    "AvSyncDecisionSemanticPolicy",
    "AvSyncSemanticPolicy",
    "P8RuntimePolicyArtifact",
    "RuntimeDecisionPolicyEvidence",
    "PhoneWindowSemanticPolicy",
    "VideoQcDecisionSemanticPolicy",
    "VideoQcSemanticPolicy",
]
