"""Frozen label-only ladder for a fresh P6 photometric-remediation cohort.

The original TS/TL174--176 cohort is design-used by the actual-v3 run and is
therefore never eligible for this cohort.  Private actor, attempt, path, and
sample HMACs are used only while evaluating the gate; none is serialized.
"""

from __future__ import annotations

from dataclasses import dataclass

from .aihub538_balanced_cohort import (
    AIHUB538_LABEL_SELECTION_RULE,
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
)
from .aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityItem,
)
from .aihub538_decision_calibration import (
    Aihub538DecisionCalibrationFailureArtifact,
    Aihub538DecisionCalibrationFailureArtifactV2,
)
from .aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
)
from .aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from .integrity import require_sha256, sha256_document


AIHUB538_P6_REMEDIATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_p6_photometric_remediation_ladder_receipt.v3"
)
AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION = 4
AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION = (
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
)
AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256 = (
    "75577cdb92e9f82ec92e1fbfe2d12d5b2abd09a36a6b3b3e626a0fb0e367a2bb"
)
AIHUB538_P6_ACTUAL_V3_FAILURE_SUMMARY_SHA256 = (
    "54ccd33235a0653e26bf082b31b844d0f6e4ab5a319e95af91bc9287bae43882"
)
AIHUB538_P6_ACTUAL_V3_SOURCE_MANIFEST_SHA256 = (
    "f1b419f24fadfd06d565f14663459c23861a2194a07062dca21c71ec4a8e5903"
)
AIHUB538_P6_ACTUAL_V3_ADMISSION_RECEIPT_SHA256 = (
    "673f35e1214a5babb41628ec41778912e784cb055a092ddb6ebf2cf8f7b78eec"
)
AIHUB538_P6_ACTUAL_V3_ADMISSION_MANIFEST_SHA256 = (
    "613992bf80dbb6da1093f045488a803f061b72667a5432477bbea8778512a8db"
)
AIHUB538_P6_ACTUAL_V3_CAPACITY_RECEIPT_SHA256 = (
    "e1ae31e50a31f2efc4e6cc58e8a0fd592e568f545eecf3b1d022c63d09dbb4e2"
)
AIHUB538_P6_ACTUAL_V3_EVALUATOR_CODE_SHA256 = (
    "c3a2d453a0b255b038820eeeea177188f72ec517cf86538b08703729b82474e6"
)
AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256 = (
    "6840c3f03930aac1fd38d60c99f23004e30151f03a02bebd1b5b39541c8027ea"
)
AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256 = (
    "5ebd2923fa06d2c0243c8562f416d04885d9773cec5c9c1c7f4f081913787e91"
)
AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE = (
    {
        "label_role_component": "TL174",
        "label_file_key": "54725",
        "label_archive_sha256": (
            "6a0bbb94589b7ab01810923797c84e4d383e6148e5cbd8d72f9dd73cc2d3c222"
        ),
        "label_content_root_sha256": (
            "74360a18f4a8ed9c2f9b12c273ac8cdd4c4297183107b42bae67f93a7ee26a82"
        ),
        "label_count": 60,
        "actor_count": 1,
        "attempt_count": 12,
        "metadata_excluded_label_count": 0,
        "terminal_media_overrun_label_count": 5,
        "terminal_frame_tail_exception_label_count": 0,
        "maximum_complete_tail_frames_observed": 10,
        "nonterminal_media_overrun_label_count": 0,
        "sentence_overlap_label_count": 0,
        "invalid_time_label_count": 0,
        "nonconforming_fps_label_count": 0,
        "terminal_frame_tail_policy_sha256": (
            "098e87d4be82ab0b30ce41dba84ca46a8886388271f91f2f2dae90e3b953e4a9"
        ),
        "full_member_tuple_commitment_sha256": (
            "8acefb077d107f5061a1100266bd45d484cb200332dd079b5eb6a08722a9205e"
        ),
        "relative_path_hmac_set_commitment_sha256": (
            "7413a3182e2d94edd4862cd3103a2003b5f63f60b962995b504de6357cf9a65d"
        ),
        "face_bbox_observation_count": 556066,
        "lip_bbox_observation_count": 556066,
        "face_reversed_bbox_unavailable_count": 0,
        "lip_reversed_bbox_unavailable_count": 0,
        "reversed_bbox_affected_label_count": 0,
    },
    {
        "label_role_component": "TL175",
        "label_file_key": "54726",
        "label_archive_sha256": (
            "f7e04181132cff1d592bb3088135d7af22f593c04a4ad965791942f40273133f"
        ),
        "label_content_root_sha256": (
            "3522f70b2a183fddb2fca23b7bb750c1188f2124f7bf67aa2c5d79eca3e253dd"
        ),
        "label_count": 240,
        "actor_count": 4,
        "attempt_count": 48,
        "metadata_excluded_label_count": 1,
        "terminal_media_overrun_label_count": 10,
        "terminal_frame_tail_exception_label_count": 0,
        "maximum_complete_tail_frames_observed": 6,
        "nonterminal_media_overrun_label_count": 0,
        "sentence_overlap_label_count": 0,
        "invalid_time_label_count": 0,
        "nonconforming_fps_label_count": 0,
        "terminal_frame_tail_policy_sha256": (
            "098e87d4be82ab0b30ce41dba84ca46a8886388271f91f2f2dae90e3b953e4a9"
        ),
        "full_member_tuple_commitment_sha256": (
            "e046529770563eea72e77f68f5de05d3632e148314c395009f7591b5f50107c3"
        ),
        "relative_path_hmac_set_commitment_sha256": (
            "a607500b53a2d3df6eb3ba87fbe18aac3e986c5f62eff50ec60bc9cd90ca6d32"
        ),
        "face_bbox_observation_count": 2391880,
        "lip_bbox_observation_count": 2391880,
        "face_reversed_bbox_unavailable_count": 0,
        "lip_reversed_bbox_unavailable_count": 19,
        "reversed_bbox_affected_label_count": 1,
    },
    {
        "label_role_component": "TL176",
        "label_file_key": "54727",
        "label_archive_sha256": (
            "eb059c9b2c70b27104831296a81cb590b0e09a229c76147823d39e5143df206a"
        ),
        "label_content_root_sha256": (
            "b44ecc4f956e056fd85968aa302ec8f98b826d03777b0581e7733b33e407b452"
        ),
        "label_count": 180,
        "actor_count": 3,
        "attempt_count": 36,
        "metadata_excluded_label_count": 2,
        "terminal_media_overrun_label_count": 0,
        "terminal_frame_tail_exception_label_count": 0,
        "maximum_complete_tail_frames_observed": 0,
        "nonterminal_media_overrun_label_count": 0,
        "sentence_overlap_label_count": 0,
        "invalid_time_label_count": 0,
        "nonconforming_fps_label_count": 0,
        "terminal_frame_tail_policy_sha256": (
            "098e87d4be82ab0b30ce41dba84ca46a8886388271f91f2f2dae90e3b953e4a9"
        ),
        "full_member_tuple_commitment_sha256": (
            "94efafbb58f3d4c993c3905c025552d705e06dde3f78708583df09fa86d3b936"
        ),
        "relative_path_hmac_set_commitment_sha256": (
            "43da393e41b13c8b42b17d95552d9b33b18ac2d3cfc28f33e3b01d0703e97735"
        ),
        "face_bbox_observation_count": 1783965,
        "lip_bbox_observation_count": 1783965,
        "face_reversed_bbox_unavailable_count": 0,
        "lip_reversed_bbox_unavailable_count": 46,
        "reversed_bbox_affected_label_count": 2,
    },
)


def _row(
    *,
    label_role_component: str,
    label_file_key: str,
    source_role_component: str,
    source_file_key: str,
) -> dict[str, str]:
    return {
        "label_role_component": label_role_component,
        "label_file_key": label_file_key,
        "source_role_component": source_role_component,
        "source_file_key": source_file_key,
    }


AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS = (
    _row(
        label_role_component=AIHUB538_TS174_TL174_SHARD.label_role_component,
        label_file_key=AIHUB538_TS174_TL174_SHARD.ordered_file_keys[0],
        source_role_component=AIHUB538_TS174_TL174_SHARD.source_role_component,
        source_file_key=AIHUB538_TS174_TL174_SHARD.ordered_file_keys[1],
    ),
    _row(
        label_role_component=AIHUB538_TS175_TL175_SHARD.label_role_component,
        label_file_key=AIHUB538_TS175_TL175_SHARD.ordered_file_keys[0],
        source_role_component=AIHUB538_TS175_TL175_SHARD.source_role_component,
        source_file_key=AIHUB538_TS175_TL175_SHARD.ordered_file_keys[1],
    ),
    _row(
        label_role_component=AIHUB538_TS176_TL176_SHARD.label_role_component,
        label_file_key=AIHUB538_TS176_TL176_SHARD.ordered_file_keys[0],
        source_role_component=AIHUB538_TS176_TL176_SHARD.source_role_component,
        source_file_key=AIHUB538_TS176_TL176_SHARD.ordered_file_keys[1],
    ),
)

# The public numeric remediation ladder is deliberately a separate slice of
# the old 175--180 registry.  Its first row is 177, never 175 or 176.
AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS = tuple(
    _row(
        label_role_component=audit.label_role_component,
        label_file_key=audit.ordered_file_keys[0],
        source_role_component=pair.source_role_component,
        source_file_key=pair.ordered_file_keys[1],
    )
    for audit, pair in zip(
        AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:],
        AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:],
        strict=True,
    )
)

AIHUB538_P6_REMEDIATION_POLICY = {
    "schema_version": "voice_coach.aihub538_p6_remediation_policy.v3",
    "policy_state": "frozen",
    "dataset_id": 538,
    "protocol_revision": "p6_photometric_remediation_after_actual_v3_v3",
    "design_used_run_id": "aihub538-multishard-actual-run-v3",
    "design_failure_artifact_sha256": (
        AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
    ),
    "design_failure_summary_sha256": (
        AIHUB538_P6_ACTUAL_V3_FAILURE_SUMMARY_SHA256
    ),
    "design_source_manifest_sha256": (
        AIHUB538_P6_ACTUAL_V3_SOURCE_MANIFEST_SHA256
    ),
    "design_admission_receipt_sha256": (
        AIHUB538_P6_ACTUAL_V3_ADMISSION_RECEIPT_SHA256
    ),
    "design_admission_manifest_sha256": (
        AIHUB538_P6_ACTUAL_V3_ADMISSION_MANIFEST_SHA256
    ),
    "design_capacity_receipt_sha256": (
        AIHUB538_P6_ACTUAL_V3_CAPACITY_RECEIPT_SHA256
    ),
    "design_evaluator_code_sha256": (
        AIHUB538_P6_ACTUAL_V3_EVALUATOR_CODE_SHA256
    ),
    "design_identity_namespace_commitment_sha256": (
        AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256
    ),
    "design_member_namespace_commitment_sha256": (
        AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256
    ),
    "design_admitted_item_count": 480,
    "design_eligible_item_count": 477,
    "design_development_selected_count": 72,
    "design_calibration_selected_count": 72,
    "design_used_rows": list(AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS),
    "design_used_label_lineage": list(
        AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE
    ),
    "nonlocked_label_schema_policy_sha256": (
        AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
    ),
    "ordered_candidate_rows": list(AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS),
    "label_audit_rule": "public_numeric_contiguous_prefix_tl177_through_tl180",
    "stopping_rule": (
        "one_new_label_per_transaction_with_exact_prior_infeasible_receipt"
    ),
    "initial_label_download_file_key": "54728",
    "subsequent_label_download_rule": (
        "strict_prior_infeasible_receipt_exact_next_label_only"
    ),
    "actor_assignment_rule": AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    "label_selection_rule": AIHUB538_LABEL_SELECTION_RULE,
    "minimum_actors_per_partition": (
        AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION
    ),
    "samples_per_angle_per_partition": (
        AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION
    ),
    "prior_design_actor_intersection_required": 0,
    "prior_design_attempt_intersection_required": 0,
    "development_calibration_actor_disjoint_required": True,
    "development_calibration_attempt_disjoint_required": True,
    "candidate_outcomes_observed": False,
    "source_download_rule": (
        "only_exact_label_source_pairs_in_first_feasible_prefix"
    ),
    "source_download_before_first_feasible_prefix_allowed": False,
    "locked_partition_accessed": False,
    "promotion_authorized": False,
}
AIHUB538_P6_REMEDIATION_POLICY_SHA256 = sha256_document(
    AIHUB538_P6_REMEDIATION_POLICY
)


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationLineage:
    """Aggregate-only lineage for one label archive."""

    label_role_component: str
    label_file_key: str
    label_archive_sha256: str
    label_content_root_sha256: str
    label_count: int
    actor_count: int
    attempt_count: int
    metadata_excluded_label_count: int
    terminal_media_overrun_label_count: int
    terminal_frame_tail_exception_label_count: int
    maximum_complete_tail_frames_observed: int
    nonterminal_media_overrun_label_count: int
    sentence_overlap_label_count: int
    invalid_time_label_count: int
    nonconforming_fps_label_count: int
    terminal_frame_tail_policy_sha256: str
    full_member_tuple_commitment_sha256: str
    relative_path_hmac_set_commitment_sha256: str
    face_bbox_observation_count: int
    lip_bbox_observation_count: int
    face_reversed_bbox_unavailable_count: int
    lip_reversed_bbox_unavailable_count: int
    reversed_bbox_affected_label_count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.label_role_component, str)
            or not self.label_role_component.startswith("TL")
        ):
            raise ValueError("aihub538_remediation_lineage_invalid")
        if (
            not isinstance(self.label_file_key, str)
            or not self.label_file_key.isdecimal()
        ):
            raise ValueError("aihub538_remediation_lineage_invalid")
        for name in (
            "label_archive_sha256",
            "label_content_root_sha256",
            "full_member_tuple_commitment_sha256",
            "relative_path_hmac_set_commitment_sha256",
            "terminal_frame_tail_policy_sha256",
        ):
            require_sha256(getattr(self, name), name)
        values = (
            self.label_count,
            self.actor_count,
            self.attempt_count,
            self.metadata_excluded_label_count,
            self.terminal_media_overrun_label_count,
            self.terminal_frame_tail_exception_label_count,
            self.maximum_complete_tail_frames_observed,
            self.nonterminal_media_overrun_label_count,
            self.sentence_overlap_label_count,
            self.invalid_time_label_count,
            self.nonconforming_fps_label_count,
            self.face_bbox_observation_count,
            self.lip_bbox_observation_count,
            self.face_reversed_bbox_unavailable_count,
            self.lip_reversed_bbox_unavailable_count,
            self.reversed_bbox_affected_label_count,
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in values
            )
            or self.label_count == 0
            or self.actor_count == 0
            or self.attempt_count == 0
            or self.metadata_excluded_label_count > self.label_count
            or self.terminal_media_overrun_label_count > self.label_count
            or self.terminal_frame_tail_exception_label_count
            > self.terminal_media_overrun_label_count
            or self.nonterminal_media_overrun_label_count != 0
            or self.sentence_overlap_label_count != 0
            or self.invalid_time_label_count != 0
            or self.nonconforming_fps_label_count != 0
            or self.face_reversed_bbox_unavailable_count
            > self.face_bbox_observation_count
            or self.lip_reversed_bbox_unavailable_count
            > self.lip_bbox_observation_count
            or self.reversed_bbox_affected_label_count > self.label_count
            or (
                self.face_reversed_bbox_unavailable_count
                + self.lip_reversed_bbox_unavailable_count
                == 0
            )
            != (self.reversed_bbox_affected_label_count == 0)
        ):
            raise ValueError("aihub538_remediation_lineage_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(cls, value: object) -> "Aihub538P6RemediationLineage":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("aihub538_remediation_lineage_invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationAngleReceipt:
    """One predeclared-angle availability and exact selection result."""

    angle: str
    development_available_count: int
    calibration_available_count: int
    development_selected_count: int
    calibration_selected_count: int

    def __post_init__(self) -> None:
        values = (
            self.development_available_count,
            self.calibration_available_count,
            self.development_selected_count,
            self.calibration_selected_count,
        )
        if self.angle not in AIHUB538_TRAINING_ANGLES or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in values
        ):
            raise ValueError("aihub538_remediation_angle_receipt_invalid")
        if (
            self.development_selected_count > self.development_available_count
            or self.calibration_selected_count > self.calibration_available_count
        ):
            raise ValueError("aihub538_remediation_angle_receipt_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538P6RemediationAngleReceipt":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("aihub538_remediation_angle_receipt_invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationPrefixDecision:
    """Privacy-safe decision for one cumulative remediation prefix."""

    prefix_length: int
    current_label_role_component: str
    current_label_file_key: str
    label_count: int
    actor_count: int
    attempt_count: int
    metadata_excluded_label_count: int
    development_population_actor_count: int
    calibration_population_actor_count: int
    development_selected_actor_count: int
    calibration_selected_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    angle_receipts: tuple[Aihub538P6RemediationAngleReceipt, ...]
    actor_assignment_root_sha256: str
    development_selection_root_sha256: str
    calibration_selection_root_sha256: str
    is_feasible: bool
    decision_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.prefix_length, bool)
            or not isinstance(self.prefix_length, int)
            or not 1 <= self.prefix_length <= len(
                AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
            )
        ):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        expected_row = AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
            self.prefix_length - 1
        ]
        if (
            self.current_label_role_component
            != expected_row["label_role_component"]
            or self.current_label_file_key != expected_row["label_file_key"]
            or not isinstance(self.is_feasible, bool)
        ):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        counts = (
            self.label_count,
            self.actor_count,
            self.attempt_count,
            self.metadata_excluded_label_count,
            self.development_population_actor_count,
            self.calibration_population_actor_count,
            self.development_selected_actor_count,
            self.calibration_selected_actor_count,
            self.actor_overlap_count,
            self.attempt_overlap_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        ):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        if tuple(item.angle for item in self.angle_receipts) != tuple(
            AIHUB538_TRAINING_ANGLES
        ):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        for name in (
            "actor_assignment_root_sha256",
            "development_selection_root_sha256",
            "calibration_selection_root_sha256",
            "decision_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.is_feasible != self._derived_feasible():
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        if self.decision_sha256 != sha256_document(self.unsigned_json_object()):
            raise ValueError("aihub538_remediation_prefix_decision_integrity_failed")

    def _derived_feasible(self) -> bool:
        quota = AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION
        minimum_actors = AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION
        return (
            self.development_selected_actor_count >= minimum_actors
            and self.calibration_selected_actor_count >= minimum_actors
            and self.actor_overlap_count == 0
            and self.attempt_overlap_count == 0
            and all(
                item.development_available_count >= quota
                and item.calibration_available_count >= quota
                and item.development_selected_count == quota
                and item.calibration_selected_count == quota
                for item in self.angle_receipts
            )
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_p6_remediation_prefix_decision.v1",
            "policy_sha256": AIHUB538_P6_REMEDIATION_POLICY_SHA256,
            "prefix_length": self.prefix_length,
            "current_label_role_component": self.current_label_role_component,
            "current_label_file_key": self.current_label_file_key,
            "label_count": self.label_count,
            "actor_count": self.actor_count,
            "attempt_count": self.attempt_count,
            "metadata_excluded_label_count": self.metadata_excluded_label_count,
            "development_population_actor_count": (
                self.development_population_actor_count
            ),
            "calibration_population_actor_count": (
                self.calibration_population_actor_count
            ),
            "development_selected_actor_count": (
                self.development_selected_actor_count
            ),
            "calibration_selected_actor_count": (
                self.calibration_selected_actor_count
            ),
            "actor_overlap_count": self.actor_overlap_count,
            "attempt_overlap_count": self.attempt_overlap_count,
            "angle_receipts": [
                item.to_json_object() for item in self.angle_receipts
            ],
            "actor_assignment_root_sha256": self.actor_assignment_root_sha256,
            "development_selection_root_sha256": (
                self.development_selection_root_sha256
            ),
            "calibration_selection_root_sha256": (
                self.calibration_selection_root_sha256
            ),
            "is_feasible": self.is_feasible,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "decision_sha256": self.decision_sha256}

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538P6RemediationPrefixDecision":
        if not isinstance(value, dict):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        expected = {
            "schema_version",
            "policy_sha256",
            "prefix_length",
            "current_label_role_component",
            "current_label_file_key",
            "label_count",
            "actor_count",
            "attempt_count",
            "metadata_excluded_label_count",
            "development_population_actor_count",
            "calibration_population_actor_count",
            "development_selected_actor_count",
            "calibration_selected_actor_count",
            "actor_overlap_count",
            "attempt_overlap_count",
            "angle_receipts",
            "actor_assignment_root_sha256",
            "development_selection_root_sha256",
            "calibration_selection_root_sha256",
            "is_feasible",
            "decision_sha256",
        }
        if (
            set(value) != expected
            or value["schema_version"]
            != "voice_coach.aihub538_p6_remediation_prefix_decision.v1"
            or value["policy_sha256"]
            != AIHUB538_P6_REMEDIATION_POLICY_SHA256
            or not isinstance(value["angle_receipts"], list)
        ):
            raise ValueError("aihub538_remediation_prefix_decision_invalid")
        return cls(
            **{
                key: item
                for key, item in value.items()
                if key not in {"schema_version", "policy_sha256", "angle_receipts"}
            },
            angle_receipts=tuple(
                Aihub538P6RemediationAngleReceipt.from_json_object(item)
                for item in value["angle_receipts"]
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationAuditReceipt:
    """Final aggregate receipt for the first-feasible or exhausted ladder."""

    design_failure_artifact_receipt_sha256: str
    prior_prefix_receipt_sha256: str | None
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    design_used_lineage: tuple[Aihub538P6RemediationLineage, ...]
    evaluated_prefix_lineage: tuple[Aihub538P6RemediationLineage, ...]
    design_used_actor_count: int
    design_used_attempt_count: int
    prior_design_actor_intersection_count: int
    prior_design_attempt_intersection_count: int
    cross_archive_path_collision_count: int
    prefix_decisions: tuple[Aihub538P6RemediationPrefixDecision, ...]
    receipt_sha256: str

    @property
    def is_feasible(self) -> bool:
        return self.prefix_decisions[-1].is_feasible

    @property
    def authorized_rows(self) -> tuple[dict[str, str], ...]:
        if not self.is_feasible:
            return ()
        return AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
            : len(self.evaluated_prefix_lineage)
        ]

    @classmethod
    def issue(
        cls,
        *,
        design_used_aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
        evaluated_candidate_aggregates: tuple[
            Aihub538LabelCapacityAggregate, ...
        ],
        design_failure_artifact: Aihub538DecisionCalibrationFailureArtifactV2,
        prior_prefix_receipt: "Aihub538P6RemediationAuditReceipt | None" = None,
    ) -> "Aihub538P6RemediationAuditReceipt":
        design_failure_artifact_receipt_sha256 = (
            require_aihub538_p6_actual_v3_failure_artifact(
                design_failure_artifact
            )
        )
        if len(design_used_aggregates) != len(
            AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS
        ) or not 1 <= len(evaluated_candidate_aggregates) <= len(
            AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
        ):
            raise ValueError("aihub538_remediation_prefix_shape_invalid")
        require_aihub538_p6_actual_v3_design_label_lineage(
            design_used_aggregates
        )
        all_aggregates = design_used_aggregates + evaluated_candidate_aggregates
        identity_namespaces = {
            item.identity_namespace_commitment_sha256 for item in all_aggregates
        }
        member_namespaces = {
            item.member_namespace_commitment_sha256 for item in all_aggregates
        }
        if (
            identity_namespaces
            != {AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256}
            or member_namespaces
            != {AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256}
        ):
            raise ValueError("aihub538_remediation_namespace_mismatch")

        design_items = tuple(
            item
            for aggregate in design_used_aggregates
            for item in aggregate.items
        )
        design_actors = {item.actor_token_sha256 for item in design_items}
        design_attempts = {item.attempt_token_sha256 for item in design_items}
        candidate_items = tuple(
            item
            for aggregate in evaluated_candidate_aggregates
            for item in aggregate.items
        )
        candidate_actors = {item.actor_token_sha256 for item in candidate_items}
        candidate_attempts = {
            item.attempt_token_sha256 for item in candidate_items
        }
        actor_intersection = len(design_actors & candidate_actors)
        attempt_intersection = len(design_attempts & candidate_attempts)
        if actor_intersection or attempt_intersection:
            raise ValueError("aihub538_remediation_prior_design_overlap")

        all_paths = [
            path
            for aggregate in all_aggregates
            for path in aggregate.relative_path_hmac_sha256
        ]
        path_collision_count = len(all_paths) - len(set(all_paths))
        if path_collision_count:
            raise ValueError("aihub538_remediation_cross_archive_path_collision")

        decisions = tuple(
            _build_prefix_decision(evaluated_candidate_aggregates[:length])
            for length in range(1, len(evaluated_candidate_aggregates) + 1)
        )
        if any(item.is_feasible for item in decisions[:-1]):
            raise ValueError("aihub538_remediation_post_feasible_prefix_accessed")

        prefix_length = len(evaluated_candidate_aggregates)
        if prefix_length == 1:
            if prior_prefix_receipt is not None:
                raise ValueError(
                    "aihub538_remediation_prior_prefix_receipt_unexpected"
                )
            prior_prefix_receipt_sha256 = None
        else:
            if not isinstance(
                prior_prefix_receipt,
                Aihub538P6RemediationAuditReceipt,
            ):
                raise ValueError(
                    "aihub538_remediation_prior_prefix_receipt_required"
                )
            prior_prefix_receipt._validate()
            current_design_lineage = tuple(
                _lineage(row, aggregate)
                for row, aggregate in zip(
                    AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS,
                    design_used_aggregates,
                    strict=True,
                )
            )
            current_prefix_lineage = tuple(
                _lineage(row, aggregate)
                for row, aggregate in zip(
                    AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,
                    evaluated_candidate_aggregates,
                    strict=False,
                )
            )
            expected_next = AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
                prefix_length - 1
            ]
            prior_document = prior_prefix_receipt.to_json_object()
            if (
                prior_prefix_receipt.is_feasible
                or len(prior_prefix_receipt.evaluated_prefix_lineage)
                != prefix_length - 1
                or prior_prefix_receipt.design_failure_artifact_receipt_sha256
                != design_failure_artifact_receipt_sha256
                or prior_prefix_receipt.identity_namespace_commitment_sha256
                != next(iter(identity_namespaces))
                or prior_prefix_receipt.member_namespace_commitment_sha256
                != next(iter(member_namespaces))
                or prior_prefix_receipt.design_used_lineage
                != current_design_lineage
                or prior_prefix_receipt.evaluated_prefix_lineage
                != current_prefix_lineage[:-1]
                or prior_prefix_receipt.design_used_actor_count
                != len(design_actors)
                or prior_prefix_receipt.design_used_attempt_count
                != len(design_attempts)
                or prior_prefix_receipt.prefix_decisions != decisions[:-1]
                or prior_document["next_label_role_component"]
                != expected_next["label_role_component"]
                or prior_document["next_label_file_key"]
                != expected_next["label_file_key"]
            ):
                raise ValueError(
                    "aihub538_remediation_prior_prefix_receipt_chain_invalid"
                )
            prior_prefix_receipt_sha256 = prior_prefix_receipt.receipt_sha256

        draft = cls(
            design_failure_artifact_receipt_sha256=(
                design_failure_artifact_receipt_sha256
            ),
            prior_prefix_receipt_sha256=prior_prefix_receipt_sha256,
            identity_namespace_commitment_sha256=next(iter(identity_namespaces)),
            member_namespace_commitment_sha256=next(iter(member_namespaces)),
            design_used_lineage=tuple(
                _lineage(row, aggregate)
                for row, aggregate in zip(
                    AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS,
                    design_used_aggregates,
                    strict=True,
                )
            ),
            evaluated_prefix_lineage=tuple(
                _lineage(row, aggregate)
                for row, aggregate in zip(
                    AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,
                    evaluated_candidate_aggregates,
                    strict=False,
                )
            ),
            design_used_actor_count=len(design_actors),
            design_used_attempt_count=len(design_attempts),
            prior_design_actor_intersection_count=actor_intersection,
            prior_design_attempt_intersection_count=attempt_intersection,
            cross_archive_path_collision_count=path_collision_count,
            prefix_decisions=decisions,
            receipt_sha256="0" * 64,
        )
        issued = cls(
            **{
                name: getattr(draft, name)
                for name in draft.__dataclass_fields__
                if name != "receipt_sha256"
            },
            receipt_sha256=sha256_document(draft.unsigned_json_object()),
        )
        issued._validate()
        return issued

    def __post_init__(self) -> None:
        for name in (
            "design_failure_artifact_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.prior_prefix_receipt_sha256 is not None:
            require_sha256(
                self.prior_prefix_receipt_sha256,
                "prior_prefix_receipt_sha256",
            )
        counts = (
            self.design_used_actor_count,
            self.design_used_attempt_count,
            self.prior_design_actor_intersection_count,
            self.prior_design_attempt_intersection_count,
            self.cross_archive_path_collision_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in counts
        ):
            raise ValueError("aihub538_remediation_receipt_invalid")

    def unsigned_json_object(self) -> dict[str, object]:
        authorized = self.authorized_rows
        next_row = (
            AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
                len(self.evaluated_prefix_lineage)
            ]
            if (
                not self.is_feasible
                and len(self.evaluated_prefix_lineage)
                < len(AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS)
            )
            else None
        )
        return {
            "schema_version": AIHUB538_P6_REMEDIATION_SCHEMA_VERSION,
            "status": (
                "completed_first_feasible_prefix"
                if self.is_feasible
                else (
                    "completed_ladder_exhausted_infeasible"
                    if next_row is None
                    else "completed_prefix_infeasible"
                )
            ),
            "dataset_id": 538,
            "protocol_revision": AIHUB538_P6_REMEDIATION_POLICY[
                "protocol_revision"
            ],
            "policy_sha256": AIHUB538_P6_REMEDIATION_POLICY_SHA256,
            "policy_state": "frozen",
            "design_used_run_id": AIHUB538_P6_REMEDIATION_POLICY[
                "design_used_run_id"
            ],
            "design_failure_artifact_receipt_sha256": (
                self.design_failure_artifact_receipt_sha256
            ),
            "prior_prefix_receipt_sha256": self.prior_prefix_receipt_sha256,
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "member_namespace_commitment_sha256": (
                self.member_namespace_commitment_sha256
            ),
            "design_used_lineage": [
                item.to_json_object() for item in self.design_used_lineage
            ],
            "evaluated_prefix_lineage": [
                item.to_json_object() for item in self.evaluated_prefix_lineage
            ],
            "evaluated_label_prefix": [
                item.label_role_component
                for item in self.evaluated_prefix_lineage
            ],
            "evaluated_label_file_key_prefix": [
                item.label_file_key for item in self.evaluated_prefix_lineage
            ],
            "design_used_actor_count": self.design_used_actor_count,
            "design_used_attempt_count": self.design_used_attempt_count,
            "prior_design_actor_intersection_count": (
                self.prior_design_actor_intersection_count
            ),
            "prior_design_attempt_intersection_count": (
                self.prior_design_attempt_intersection_count
            ),
            "cross_archive_path_collision_count": (
                self.cross_archive_path_collision_count
            ),
            "prefix_decisions": [
                item.to_json_object() for item in self.prefix_decisions
            ],
            "first_feasible_prefix_reached": self.is_feasible,
            "next_label_role_component": (
                next_row["label_role_component"]
                if next_row is not None
                else None
            ),
            "next_label_file_key": (
                next_row["label_file_key"] if next_row is not None else None
            ),
            "authorized_label_role_prefix": [
                item["label_role_component"] for item in authorized
            ],
            "authorized_label_file_key_prefix": [
                item["label_file_key"] for item in authorized
            ],
            "authorized_source_role_prefix": [
                item["source_role_component"] for item in authorized
            ],
            "authorized_source_file_key_prefix": [
                item["source_file_key"] for item in authorized
            ],
            "source_download_authorized": self.is_feasible,
            "candidate_outcomes_observed": False,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
            "user_visual_claims_authorized": False,
            "raw_identifiers_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "sample_digests_serialized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    def _validate(self) -> None:
        expected_design = tuple(
            (
                row["label_role_component"],
                row["label_file_key"],
                row["label_archive_sha256"],
                row["label_content_root_sha256"],
                row["label_count"],
                row["actor_count"],
                row["attempt_count"],
                row["metadata_excluded_label_count"],
                row["terminal_media_overrun_label_count"],
                row["terminal_frame_tail_exception_label_count"],
                row["maximum_complete_tail_frames_observed"],
                row["nonterminal_media_overrun_label_count"],
                row["sentence_overlap_label_count"],
                row["invalid_time_label_count"],
                row["nonconforming_fps_label_count"],
                row["terminal_frame_tail_policy_sha256"],
                row["full_member_tuple_commitment_sha256"],
                row["relative_path_hmac_set_commitment_sha256"],
                row["face_bbox_observation_count"],
                row["lip_bbox_observation_count"],
                row["face_reversed_bbox_unavailable_count"],
                row["lip_reversed_bbox_unavailable_count"],
                row["reversed_bbox_affected_label_count"],
            )
            for row in AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE
        )
        actual_design = tuple(
            (
                item.label_role_component,
                item.label_file_key,
                item.label_archive_sha256,
                item.label_content_root_sha256,
                item.label_count,
                item.actor_count,
                item.attempt_count,
                item.metadata_excluded_label_count,
                item.terminal_media_overrun_label_count,
                item.terminal_frame_tail_exception_label_count,
                item.maximum_complete_tail_frames_observed,
                item.nonterminal_media_overrun_label_count,
                item.sentence_overlap_label_count,
                item.invalid_time_label_count,
                item.nonconforming_fps_label_count,
                item.terminal_frame_tail_policy_sha256,
                item.full_member_tuple_commitment_sha256,
                item.relative_path_hmac_set_commitment_sha256,
                item.face_bbox_observation_count,
                item.lip_bbox_observation_count,
                item.face_reversed_bbox_unavailable_count,
                item.lip_reversed_bbox_unavailable_count,
                item.reversed_bbox_affected_label_count,
            )
            for item in self.design_used_lineage
        )
        expected_prefix = tuple(
            (row["label_role_component"], row["label_file_key"])
            for row in AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
                : len(self.evaluated_prefix_lineage)
            ]
        )
        actual_prefix = tuple(
            (item.label_role_component, item.label_file_key)
            for item in self.evaluated_prefix_lineage
        )
        lineage_reconciles = all(
            decision.label_count
            == sum(
                item.label_count
                for item in self.evaluated_prefix_lineage[
                    : decision.prefix_length
                ]
            )
            and decision.metadata_excluded_label_count
            == sum(
                item.metadata_excluded_label_count
                for item in self.evaluated_prefix_lineage[
                    : decision.prefix_length
                ]
            )
            and 0 < decision.actor_count
            <= sum(
                item.actor_count
                for item in self.evaluated_prefix_lineage[
                    : decision.prefix_length
                ]
            )
            and 0 < decision.attempt_count
            <= sum(
                item.attempt_count
                for item in self.evaluated_prefix_lineage[
                    : decision.prefix_length
                ]
            )
            and (
                decision.development_population_actor_count
                + decision.calibration_population_actor_count
                <= decision.actor_count
            )
            and decision.development_selected_actor_count
            <= decision.development_population_actor_count
            and decision.calibration_selected_actor_count
            <= decision.calibration_population_actor_count
            for decision in self.prefix_decisions
        )
        counts_are_monotonic = all(
            current.label_count > previous.label_count
            and current.actor_count >= previous.actor_count
            and current.attempt_count >= previous.attempt_count
            and current.metadata_excluded_label_count
            >= previous.metadata_excluded_label_count
            for previous, current in zip(
                self.prefix_decisions,
                self.prefix_decisions[1:],
            )
        )
        if (
            actual_design != expected_design
            or not self.evaluated_prefix_lineage
            or actual_prefix != expected_prefix
            or len(self.prefix_decisions) != len(self.evaluated_prefix_lineage)
            or tuple(item.prefix_length for item in self.prefix_decisions)
            != tuple(range(1, len(self.prefix_decisions) + 1))
            or any(item.is_feasible for item in self.prefix_decisions[:-1])
            or self.prior_design_actor_intersection_count != 0
            or self.prior_design_attempt_intersection_count != 0
            or self.cross_archive_path_collision_count != 0
            or self.design_failure_artifact_receipt_sha256
            != AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
            or self.identity_namespace_commitment_sha256
            != AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256
            or self.member_namespace_commitment_sha256
            != AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256
            or (
                len(self.evaluated_prefix_lineage) == 1
                and self.prior_prefix_receipt_sha256 is not None
            )
            or (
                len(self.evaluated_prefix_lineage) > 1
                and self.prior_prefix_receipt_sha256 is None
            )
            or not 0 < self.design_used_actor_count <= sum(
                item.actor_count for item in self.design_used_lineage
            )
            or not 0 < self.design_used_attempt_count <= sum(
                item.attempt_count for item in self.design_used_lineage
            )
            or not lineage_reconciles
            or not counts_are_monotonic
            or self.receipt_sha256 != sha256_document(self.unsigned_json_object())
        ):
            raise ValueError("aihub538_remediation_receipt_invalid")

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538P6RemediationAuditReceipt":
        if not isinstance(value, dict):
            raise ValueError("aihub538_remediation_receipt_invalid")
        expected = {
            "schema_version",
            "status",
            "dataset_id",
            "protocol_revision",
            "policy_sha256",
            "policy_state",
            "design_used_run_id",
            "design_failure_artifact_receipt_sha256",
            "prior_prefix_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "design_used_lineage",
            "evaluated_prefix_lineage",
            "evaluated_label_prefix",
            "evaluated_label_file_key_prefix",
            "design_used_actor_count",
            "design_used_attempt_count",
            "prior_design_actor_intersection_count",
            "prior_design_attempt_intersection_count",
            "cross_archive_path_collision_count",
            "prefix_decisions",
            "first_feasible_prefix_reached",
            "next_label_role_component",
            "next_label_file_key",
            "authorized_label_role_prefix",
            "authorized_label_file_key_prefix",
            "authorized_source_role_prefix",
            "authorized_source_file_key_prefix",
            "source_download_authorized",
            "candidate_outcomes_observed",
            "locked_partition_accessed",
            "promotion_authorized",
            "user_visual_claims_authorized",
            "raw_identifiers_serialized",
            "source_paths_serialized",
            "transcripts_serialized",
            "sample_digests_serialized",
            "receipt_sha256",
        }
        if (
            set(value) != expected
            or value["schema_version"] != AIHUB538_P6_REMEDIATION_SCHEMA_VERSION
            or value["dataset_id"] != 538
            or value["policy_sha256"] != AIHUB538_P6_REMEDIATION_POLICY_SHA256
            or value["policy_state"] != "frozen"
            or value["protocol_revision"]
            != AIHUB538_P6_REMEDIATION_POLICY["protocol_revision"]
            or value["design_used_run_id"]
            != AIHUB538_P6_REMEDIATION_POLICY["design_used_run_id"]
            or not isinstance(value["design_used_lineage"], list)
            or not isinstance(value["evaluated_prefix_lineage"], list)
            or not isinstance(value["prefix_decisions"], list)
            or any(
                not isinstance(value[name], bool)
                for name in (
                    "first_feasible_prefix_reached",
                    "source_download_authorized",
                    "candidate_outcomes_observed",
                    "locked_partition_accessed",
                    "promotion_authorized",
                    "user_visual_claims_authorized",
                    "raw_identifiers_serialized",
                    "source_paths_serialized",
                    "transcripts_serialized",
                    "sample_digests_serialized",
                )
            )
        ):
            raise ValueError("aihub538_remediation_receipt_invalid")
        receipt = cls(
            design_failure_artifact_receipt_sha256=value[
                "design_failure_artifact_receipt_sha256"
            ],
            prior_prefix_receipt_sha256=value["prior_prefix_receipt_sha256"],
            identity_namespace_commitment_sha256=value[
                "identity_namespace_commitment_sha256"
            ],
            member_namespace_commitment_sha256=value[
                "member_namespace_commitment_sha256"
            ],
            design_used_lineage=tuple(
                Aihub538P6RemediationLineage.from_json_object(item)
                for item in value["design_used_lineage"]
            ),
            evaluated_prefix_lineage=tuple(
                Aihub538P6RemediationLineage.from_json_object(item)
                for item in value["evaluated_prefix_lineage"]
            ),
            design_used_actor_count=value["design_used_actor_count"],
            design_used_attempt_count=value["design_used_attempt_count"],
            prior_design_actor_intersection_count=value[
                "prior_design_actor_intersection_count"
            ],
            prior_design_attempt_intersection_count=value[
                "prior_design_attempt_intersection_count"
            ],
            cross_archive_path_collision_count=value[
                "cross_archive_path_collision_count"
            ],
            prefix_decisions=tuple(
                Aihub538P6RemediationPrefixDecision.from_json_object(item)
                for item in value["prefix_decisions"]
            ),
            receipt_sha256=value["receipt_sha256"],
        )
        receipt._validate()
        if receipt.to_json_object() != value:
            raise ValueError("aihub538_remediation_receipt_invalid")
        return receipt


def _lineage(
    row: dict[str, str],
    aggregate: Aihub538LabelCapacityAggregate,
) -> Aihub538P6RemediationLineage:
    return Aihub538P6RemediationLineage(
        label_role_component=row["label_role_component"],
        label_file_key=row["label_file_key"],
        label_archive_sha256=aggregate.label_archive_sha256,
        label_content_root_sha256=aggregate.label_content_root_sha256,
        label_count=len(aggregate.items),
        actor_count=len({item.actor_token_sha256 for item in aggregate.items}),
        attempt_count=len(
            {item.attempt_token_sha256 for item in aggregate.items}
        ),
        metadata_excluded_label_count=sum(
            not item.metadata_bbox_observation_available
            for item in aggregate.items
        ),
        terminal_media_overrun_label_count=(
            aggregate.terminal_media_overrun_label_count
        ),
        terminal_frame_tail_exception_label_count=(
            aggregate.terminal_frame_tail_exception_label_count
        ),
        maximum_complete_tail_frames_observed=(
            aggregate.maximum_complete_tail_frames_observed
        ),
        nonterminal_media_overrun_label_count=(
            aggregate.nonterminal_media_overrun_label_count
        ),
        sentence_overlap_label_count=aggregate.sentence_overlap_label_count,
        invalid_time_label_count=aggregate.invalid_time_label_count,
        nonconforming_fps_label_count=(
            aggregate.nonconforming_fps_label_count
        ),
        terminal_frame_tail_policy_sha256=(
            aggregate.terminal_frame_tail_policy_sha256
        ),
        full_member_tuple_commitment_sha256=(
            aihub538_p6_full_member_tuple_commitment_sha256(aggregate)
        ),
        relative_path_hmac_set_commitment_sha256=(
            aihub538_p6_relative_path_hmac_set_commitment_sha256(aggregate)
        ),
        face_bbox_observation_count=aggregate.face_bbox_observation_count,
        lip_bbox_observation_count=aggregate.lip_bbox_observation_count,
        face_reversed_bbox_unavailable_count=(
            aggregate.face_reversed_bbox_unavailable_count
        ),
        lip_reversed_bbox_unavailable_count=(
            aggregate.lip_reversed_bbox_unavailable_count
        ),
        reversed_bbox_affected_label_count=(
            aggregate.reversed_bbox_affected_label_count
        ),
    )


def aihub538_p6_full_member_tuple_commitment_sha256(
    aggregate: Aihub538LabelCapacityAggregate,
) -> str:
    """Bind every private item dimension used by remediation decisions."""

    if not isinstance(aggregate, Aihub538LabelCapacityAggregate):
        raise ValueError("aihub538_remediation_aggregate_invalid")
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_private_full_member_tuple_root.v2"
            ),
            "members": sorted(
                [
                    {
                        "sample_sha256": item.sample_sha256,
                        "angle": item.angle,
                        "actor_token_sha256": item.actor_token_sha256,
                        "attempt_token_sha256": item.attempt_token_sha256,
                        "metadata_bbox_observation_available": (
                            item.metadata_bbox_observation_available
                        ),
                        "terminal_media_overrun": (
                            item.terminal_media_overrun
                        ),
                        "terminal_frame_tail_exception_applied": (
                            item.terminal_frame_tail_exception_applied
                        ),
                        "terminal_complete_tail_frames": (
                            item.terminal_complete_tail_frames
                        ),
                    }
                    for item in aggregate.items
                ],
                key=lambda row: (
                    row["sample_sha256"],
                    row["actor_token_sha256"],
                    row["attempt_token_sha256"],
                    row["angle"],
                    row["metadata_bbox_observation_available"],
                    row["terminal_media_overrun"],
                    row["terminal_frame_tail_exception_applied"],
                    row["terminal_complete_tail_frames"],
                ),
            ),
        }
    )


def aihub538_p6_relative_path_hmac_set_commitment_sha256(
    aggregate: Aihub538LabelCapacityAggregate,
) -> str:
    """Bind the complete private path-HMAC set without serializing its rows."""

    if not isinstance(aggregate, Aihub538LabelCapacityAggregate):
        raise ValueError("aihub538_remediation_aggregate_invalid")
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_private_relative_path_hmac_set.v1"
            ),
            "relative_path_hmac_sha256": sorted(
                aggregate.relative_path_hmac_sha256
            ),
        }
    )


def _build_prefix_decision(
    aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
) -> Aihub538P6RemediationPrefixDecision:
    items = tuple(item for aggregate in aggregates for item in aggregate.items)
    sample_tokens = [item.sample_sha256 for item in items]
    if len(sample_tokens) != len(set(sample_tokens)):
        raise ValueError("aihub538_remediation_sample_collision")
    actors = sorted({item.actor_token_sha256 for item in items})
    assignments = {
        actor: "development" if index % 2 == 0 else "calibration"
        for index, actor in enumerate(actors)
    }
    attempt_actor: dict[str, str] = {}
    eligible: dict[str, list[Aihub538LabelCapacityItem]] = {
        "development": [],
        "calibration": [],
    }
    metadata_excluded = 0
    for item in items:
        previous_actor = attempt_actor.setdefault(
            item.attempt_token_sha256,
            item.actor_token_sha256,
        )
        if previous_actor != item.actor_token_sha256:
            raise ValueError("aihub538_remediation_attempt_actor_mismatch")
        if not item.metadata_bbox_observation_available:
            metadata_excluded += 1
            continue
        eligible[assignments[item.actor_token_sha256]].append(item)

    selected: dict[str, list[Aihub538LabelCapacityItem]] = {
        "development": [],
        "calibration": [],
    }
    quota = AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION
    angle_receipts: list[Aihub538P6RemediationAngleReceipt] = []
    for angle in AIHUB538_TRAINING_ANGLES:
        by_partition = {
            partition: sorted(
                (
                    item
                    for item in eligible[partition]
                    if item.angle == angle
                ),
                key=lambda item: item.sample_sha256,
            )
            for partition in ("development", "calibration")
        }
        for partition in ("development", "calibration"):
            selected[partition].extend(by_partition[partition][:quota])
        angle_receipts.append(
            Aihub538P6RemediationAngleReceipt(
                angle=angle,
                development_available_count=len(by_partition["development"]),
                calibration_available_count=len(by_partition["calibration"]),
                development_selected_count=min(
                    quota,
                    len(by_partition["development"]),
                ),
                calibration_selected_count=min(
                    quota,
                    len(by_partition["calibration"]),
                ),
            )
        )
    population_actors = {
        partition: {item.actor_token_sha256 for item in eligible[partition]}
        for partition in ("development", "calibration")
    }
    selected_actors = {
        partition: {item.actor_token_sha256 for item in selected[partition]}
        for partition in ("development", "calibration")
    }
    selected_attempts = {
        partition: {item.attempt_token_sha256 for item in selected[partition]}
        for partition in ("development", "calibration")
    }
    unsigned = {
        "prefix_length": len(aggregates),
        "current_label_role_component": (
            AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[len(aggregates) - 1][
                "label_role_component"
            ]
        ),
        "current_label_file_key": (
            AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[len(aggregates) - 1][
                "label_file_key"
            ]
        ),
        "label_count": len(items),
        "actor_count": len(actors),
        "attempt_count": len(attempt_actor),
        "metadata_excluded_label_count": metadata_excluded,
        "development_population_actor_count": len(
            population_actors["development"]
        ),
        "calibration_population_actor_count": len(
            population_actors["calibration"]
        ),
        "development_selected_actor_count": len(
            selected_actors["development"]
        ),
        "calibration_selected_actor_count": len(
            selected_actors["calibration"]
        ),
        "actor_overlap_count": len(
            selected_actors["development"] & selected_actors["calibration"]
        ),
        "attempt_overlap_count": len(
            selected_attempts["development"]
            & selected_attempts["calibration"]
        ),
        "angle_receipts": tuple(angle_receipts),
        "actor_assignment_root_sha256": (
            aihub538_balanced_assignment_commitment(assignments)
        ),
        "development_selection_root_sha256": (
            aihub538_label_selection_root_sha256(
                item.sample_sha256 for item in selected["development"]
            )
        ),
        "calibration_selection_root_sha256": (
            aihub538_label_selection_root_sha256(
                item.sample_sha256 for item in selected["calibration"]
            )
        ),
    }
    quota_met = all(
        item.development_available_count >= quota
        and item.calibration_available_count >= quota
        and item.development_selected_count == quota
        and item.calibration_selected_count == quota
        for item in angle_receipts
    )
    feasible = (
        len(selected_actors["development"])
        >= AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION
        and len(selected_actors["calibration"])
        >= AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION
        and unsigned["actor_overlap_count"] == 0
        and unsigned["attempt_overlap_count"] == 0
        and quota_met
    )
    decision_unsigned = {
        "schema_version": "voice_coach.aihub538_p6_remediation_prefix_decision.v1",
        "policy_sha256": AIHUB538_P6_REMEDIATION_POLICY_SHA256,
        **{
            name: (
                [item.to_json_object() for item in value]
                if name == "angle_receipts"
                else value
            )
            for name, value in unsigned.items()
        },
        "is_feasible": feasible,
    }
    return Aihub538P6RemediationPrefixDecision(
        **unsigned,
        is_feasible=feasible,
        decision_sha256=sha256_document(decision_unsigned),
    )


def is_aihub538_p6_remediation_prefix_feasible(
    *,
    design_used_aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
    evaluated_candidate_aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
) -> bool:
    """Evaluate a lazy prefix without authorizing or serializing it."""

    decision = _build_prefix_decision(evaluated_candidate_aggregates)
    design_items = tuple(
        item for aggregate in design_used_aggregates for item in aggregate.items
    )
    candidate_items = tuple(
        item
        for aggregate in evaluated_candidate_aggregates
        for item in aggregate.items
    )
    if (
        {item.actor_token_sha256 for item in design_items}
        & {item.actor_token_sha256 for item in candidate_items}
        or {item.attempt_token_sha256 for item in design_items}
        & {item.attempt_token_sha256 for item in candidate_items}
    ):
        raise ValueError("aihub538_remediation_prior_design_overlap")
    return decision.is_feasible


def require_aihub538_p6_remediation_source_pair(
    receipt: Aihub538P6RemediationAuditReceipt,
    requested_file_keys: tuple[str, ...],
) -> None:
    """Authorize one exact pair only when it belongs to the selected prefix."""

    if not isinstance(receipt, Aihub538P6RemediationAuditReceipt):
        raise ValueError("aihub538_remediation_source_authorization_invalid")
    receipt._validate()
    allowed_pairs = {
        (row["label_file_key"], row["source_file_key"])
        for row in receipt.authorized_rows
    }
    if (
        not receipt.is_feasible
        or not isinstance(requested_file_keys, tuple)
        or len(requested_file_keys) != 2
        or any(
            not isinstance(value, str) or not value.isdecimal()
            for value in requested_file_keys
        )
        or requested_file_keys not in allowed_pairs
        or {"55082", "55114"}.intersection(requested_file_keys)
    ):
        raise ValueError("aihub538_remediation_source_pair_not_authorized")


def require_aihub538_p6_actual_v3_failure_artifact(
    artifact: object,
) -> str:
    """Bind remediation design use to the exact observed actual-v3 artifact.

    This immutable release pin prevents substituting another self-consistent
    failure document.  It is not an external signature or issuer credential.
    """

    if type(artifact) is not Aihub538DecisionCalibrationFailureArtifactV2:
        raise ValueError("aihub538_remediation_actual_v3_failure_required")
    summary = artifact.failure_summary
    if (
        type(summary) is not Aihub538DecisionCalibrationFailureArtifact
        or artifact.artifact_sha256
        != AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
        or summary.artifact_sha256
        != AIHUB538_P6_ACTUAL_V3_FAILURE_SUMMARY_SHA256
        or summary.reason_code
        != "aihub538_calibration_per_angle_stop_policy_failed"
        or summary.source_manifest_sha256
        != AIHUB538_P6_ACTUAL_V3_SOURCE_MANIFEST_SHA256
        or summary.admission_receipt_sha256
        != AIHUB538_P6_ACTUAL_V3_ADMISSION_RECEIPT_SHA256
        or summary.admission_manifest_sha256
        != AIHUB538_P6_ACTUAL_V3_ADMISSION_MANIFEST_SHA256
        or summary.capacity_audit_receipt_sha256
        != AIHUB538_P6_ACTUAL_V3_CAPACITY_RECEIPT_SHA256
        or summary.evaluator_code_sha256
        != AIHUB538_P6_ACTUAL_V3_EVALUATOR_CODE_SHA256
        or summary.admitted_item_count != 480
        or summary.eligible_item_count != 477
        or summary.development_selected_count != 72
        or summary.calibration_selected_count != 72
    ):
        raise ValueError(
            "aihub538_remediation_actual_v3_failure_binding_invalid"
        )
    return artifact.artifact_sha256


def require_aihub538_p6_actual_v3_design_label_lineage(
    design_used_aggregates: tuple[Aihub538LabelCapacityAggregate, ...],
) -> None:
    """Require the exact ordered TL174--TL176 archives used by actual-v3.

    The pins were independently reproduced from the controlled label ZIP
    descriptors and their full CRC/content-root validation receipts.  This is
    an immutable local release binding, not an external signature.
    """

    if (
        not isinstance(design_used_aggregates, tuple)
        or len(design_used_aggregates)
        != len(AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE)
        or any(
            not isinstance(item, Aihub538LabelCapacityAggregate)
            for item in design_used_aggregates
        )
    ):
        raise ValueError(
            "aihub538_remediation_actual_v3_design_lineage_invalid"
        )
    actual = tuple(
        (
            row["label_role_component"],
            row["label_file_key"],
            aggregate.label_archive_sha256,
            aggregate.label_content_root_sha256,
            len(aggregate.items),
            len({item.actor_token_sha256 for item in aggregate.items}),
            len({item.attempt_token_sha256 for item in aggregate.items}),
            sum(
                not item.metadata_bbox_observation_available
                for item in aggregate.items
            ),
            aggregate.terminal_media_overrun_label_count,
            aggregate.terminal_frame_tail_exception_label_count,
            aggregate.maximum_complete_tail_frames_observed,
            aggregate.nonterminal_media_overrun_label_count,
            aggregate.sentence_overlap_label_count,
            aggregate.invalid_time_label_count,
            aggregate.nonconforming_fps_label_count,
            aggregate.terminal_frame_tail_policy_sha256,
            aihub538_p6_full_member_tuple_commitment_sha256(aggregate),
            aihub538_p6_relative_path_hmac_set_commitment_sha256(aggregate),
            aggregate.face_bbox_observation_count,
            aggregate.lip_bbox_observation_count,
            aggregate.face_reversed_bbox_unavailable_count,
            aggregate.lip_reversed_bbox_unavailable_count,
            aggregate.reversed_bbox_affected_label_count,
        )
        for row, aggregate in zip(
            AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS,
            design_used_aggregates,
            strict=True,
        )
    )
    expected = tuple(
        (
            row["label_role_component"],
            row["label_file_key"],
            row["label_archive_sha256"],
            row["label_content_root_sha256"],
            row["label_count"],
            row["actor_count"],
            row["attempt_count"],
            row["metadata_excluded_label_count"],
            row["terminal_media_overrun_label_count"],
            row["terminal_frame_tail_exception_label_count"],
            row["maximum_complete_tail_frames_observed"],
            row["nonterminal_media_overrun_label_count"],
            row["sentence_overlap_label_count"],
            row["invalid_time_label_count"],
            row["nonconforming_fps_label_count"],
            row["terminal_frame_tail_policy_sha256"],
            row["full_member_tuple_commitment_sha256"],
            row["relative_path_hmac_set_commitment_sha256"],
            row["face_bbox_observation_count"],
            row["lip_bbox_observation_count"],
            row["face_reversed_bbox_unavailable_count"],
            row["lip_reversed_bbox_unavailable_count"],
            row["reversed_bbox_affected_label_count"],
        )
        for row in AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE
    )
    if (
        actual != expected
        or {
            item.identity_namespace_commitment_sha256
            for item in design_used_aggregates
        }
        != {AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256}
        or {
            item.member_namespace_commitment_sha256
            for item in design_used_aggregates
        }
        != {AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256}
    ):
        raise ValueError(
            "aihub538_remediation_actual_v3_design_lineage_invalid"
        )


__all__ = [
    "AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS",
    "AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256",
    "AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE",
    "AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256",
    "AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256",
    "AIHUB538_P6_REMEDIATION_DESIGN_USED_ROWS",
    "AIHUB538_P6_REMEDIATION_MINIMUM_ACTORS_PER_PARTITION",
    "AIHUB538_P6_REMEDIATION_POLICY",
    "AIHUB538_P6_REMEDIATION_POLICY_SHA256",
    "AIHUB538_P6_REMEDIATION_SAMPLES_PER_ANGLE_PER_PARTITION",
    "AIHUB538_P6_REMEDIATION_SCHEMA_VERSION",
    "Aihub538P6RemediationAngleReceipt",
    "Aihub538P6RemediationAuditReceipt",
    "Aihub538P6RemediationLineage",
    "aihub538_p6_full_member_tuple_commitment_sha256",
    "aihub538_p6_relative_path_hmac_set_commitment_sha256",
    "Aihub538P6RemediationPrefixDecision",
    "is_aihub538_p6_remediation_prefix_feasible",
    "require_aihub538_p6_remediation_source_pair",
    "require_aihub538_p6_actual_v3_failure_artifact",
    "require_aihub538_p6_actual_v3_design_label_lineage",
]
