"""Aggregate-only contracts for the staged TL175 label capacity audit."""

from __future__ import annotations

from dataclasses import dataclass, field

from .aihub538_balanced_cohort import (
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION,
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
)
from .aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY,
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256,
    AIHUB538_TL175_CAPACITY_AUDIT_SHARD,
)
from .aihub538_bbox_observation import (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY,
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256,
)
from .aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from .integrity import require_sha256, sha256_document


AIHUB538_LABEL_CAPACITY_BBOX_OBSERVATION_POLICY = (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY
)
AIHUB538_LABEL_CAPACITY_BBOX_OBSERVATION_POLICY_SHA256 = (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256
)
AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY = {
    "schema_version": "voice_coach.aihub538_terminal_frame_tail_policy.v1",
    "scope": "nonlocked_label_schema",
    "terminal_sentence_only": True,
    "required_frames_per_second": 30.0,
    "maximum_complete_tail_frames": 15,
    "frame_integer_epsilon": 1e-9,
    "sentence_overlap_tolerance_seconds": 0.0,
    "sentence_overlap_comparison_epsilon": 1e-9,
    "legacy_training_overlap_tolerance_seconds": 0.5,
    "ordinary_media_tail_tolerance_seconds": 0.5,
    "timestamps_rewritten": False,
    "ordinary_training_admission_changed": False,
}
AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256 = sha256_document(
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY
)


@dataclass(frozen=True, slots=True)
class Aihub538LabelCapacityItem:
    """One private label row; tokens never appear in serialized evidence."""

    sample_sha256: str
    angle: str
    actor_token_sha256: str = field(repr=False)
    attempt_token_sha256: str = field(repr=False)
    metadata_bbox_observation_available: bool = True
    terminal_media_overrun: bool = False
    terminal_frame_tail_exception_applied: bool = False
    terminal_complete_tail_frames: int = 0

    def __post_init__(self) -> None:
        require_sha256(self.sample_sha256, "sample_sha256")
        require_sha256(self.actor_token_sha256, "actor_token_sha256")
        require_sha256(self.attempt_token_sha256, "attempt_token_sha256")
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise ValueError("aihub538_label_capacity_angle_invalid")
        if not isinstance(self.metadata_bbox_observation_available, bool):
            raise ValueError("aihub538_label_capacity_bbox_availability_invalid")
        if (
            not isinstance(self.terminal_media_overrun, bool)
            or not isinstance(self.terminal_frame_tail_exception_applied, bool)
            or (
                self.terminal_frame_tail_exception_applied
                and not self.terminal_media_overrun
            )
            or isinstance(self.terminal_complete_tail_frames, bool)
            or not isinstance(self.terminal_complete_tail_frames, int)
            or self.terminal_complete_tail_frames < 0
            or (
                not self.terminal_media_overrun
                and self.terminal_complete_tail_frames != 0
            )
        ):
            raise ValueError("aihub538_label_capacity_terminal_tail_invalid")


@dataclass(frozen=True, slots=True)
class Aihub538LabelCapacityAggregate:
    """Private adapter result bound to one fully validated TL175 ZIP."""

    label_archive_sha256: str
    label_content_root_sha256: str
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    items: tuple[Aihub538LabelCapacityItem, ...]
    relative_path_hmac_sha256: frozenset[str] = field(repr=False)
    face_bbox_observation_count: int = 0
    lip_bbox_observation_count: int = 0
    face_reversed_bbox_unavailable_count: int = 0
    lip_reversed_bbox_unavailable_count: int = 0
    reversed_bbox_affected_label_count: int = 0
    terminal_media_overrun_label_count: int = 0
    terminal_frame_tail_exception_label_count: int = 0
    maximum_complete_tail_frames_observed: int = 0
    nonterminal_media_overrun_label_count: int = 0
    sentence_overlap_label_count: int = 0
    invalid_time_label_count: int = 0
    nonconforming_fps_label_count: int = 0
    terminal_frame_tail_policy_sha256: str = (
        AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
    )

    def __post_init__(self) -> None:
        for name in (
            "label_archive_sha256",
            "label_content_root_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "terminal_frame_tail_policy_sha256",
        ):
            require_sha256(getattr(self, name), name)
        counts = (
            self.face_bbox_observation_count,
            self.lip_bbox_observation_count,
            self.face_reversed_bbox_unavailable_count,
            self.lip_reversed_bbox_unavailable_count,
            self.reversed_bbox_affected_label_count,
            self.terminal_media_overrun_label_count,
            self.terminal_frame_tail_exception_label_count,
            self.maximum_complete_tail_frames_observed,
            self.nonterminal_media_overrun_label_count,
            self.sentence_overlap_label_count,
            self.invalid_time_label_count,
            self.nonconforming_fps_label_count,
        )
        if (
            not self.items
            or len(self.relative_path_hmac_sha256) != len(self.items)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in counts
            )
            or self.face_reversed_bbox_unavailable_count
            > self.face_bbox_observation_count
            or self.lip_reversed_bbox_unavailable_count
            > self.lip_bbox_observation_count
            or self.reversed_bbox_affected_label_count > len(self.items)
            or self.terminal_media_overrun_label_count > len(self.items)
            or self.terminal_frame_tail_exception_label_count
            > self.terminal_media_overrun_label_count
            or self.maximum_complete_tail_frames_observed
            != max(
                (item.terminal_complete_tail_frames for item in self.items),
                default=0,
            )
            or self.nonterminal_media_overrun_label_count != 0
            or self.sentence_overlap_label_count != 0
            or self.invalid_time_label_count != 0
            or self.nonconforming_fps_label_count != 0
            or self.terminal_media_overrun_label_count
            != sum(item.terminal_media_overrun for item in self.items)
            or self.terminal_frame_tail_exception_label_count
            != sum(
                item.terminal_frame_tail_exception_applied
                for item in self.items
            )
            or self.terminal_frame_tail_policy_sha256
            != AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
            or (
                self.face_reversed_bbox_unavailable_count
                + self.lip_reversed_bbox_unavailable_count
                == 0
            )
            != (self.reversed_bbox_affected_label_count == 0)
        ):
            raise ValueError("aihub538_label_capacity_bbox_aggregate_invalid")
        for value in self.relative_path_hmac_sha256:
            require_sha256(value, "relative_path_hmac_sha256")


@dataclass(frozen=True, slots=True)
class Aihub538LabelCapacityAngleReceipt:
    """Per-angle availability and exact preselected quota counts."""

    angle: str
    available_label_count: int
    development_available_count: int
    calibration_available_count: int
    development_selected_count: int
    calibration_selected_count: int

    def __post_init__(self) -> None:
        if self.angle not in AIHUB538_TRAINING_ANGLES:
            raise ValueError("aihub538_label_capacity_angle_invalid")
        values = (
            self.available_label_count,
            self.development_available_count,
            self.calibration_available_count,
            self.development_selected_count,
            self.calibration_selected_count,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("aihub538_label_capacity_angle_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538LabelCapacityAngleReceipt":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("aihub538_label_capacity_angle_invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Aihub538LabelCapacityAuditReceipt:
    """0400-safe proof that TL175 can support the frozen balanced protocol."""

    label_archive_sha256: str
    label_content_root_sha256: str
    baseline_label_archive_sha256: str
    baseline_label_content_root_sha256: str
    baseline_admission_receipt_sha256: str
    baseline_admission_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    label_count: int
    baseline_label_count: int
    candidate_label_count: int
    baseline_face_bbox_observation_count: int
    baseline_lip_bbox_observation_count: int
    candidate_face_bbox_observation_count: int
    candidate_lip_bbox_observation_count: int
    baseline_face_reversed_bbox_unavailable_count: int
    baseline_lip_reversed_bbox_unavailable_count: int
    candidate_face_reversed_bbox_unavailable_count: int
    candidate_lip_reversed_bbox_unavailable_count: int
    baseline_reversed_bbox_affected_label_count: int
    candidate_reversed_bbox_affected_label_count: int
    actor_count: int
    attempt_count: int
    cross_shard_path_collision_count: int
    cross_shard_actor_intersection_count: int
    cross_shard_attempt_intersection_count: int
    development_actor_count: int
    calibration_actor_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    angle_receipts: tuple[Aihub538LabelCapacityAngleReceipt, ...]
    actor_assignment_root_sha256: str
    development_selection_root_sha256: str
    calibration_selection_root_sha256: str
    receipt_sha256: str

    @property
    def is_feasible(self) -> bool:
        quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
        return (
            self.cross_shard_path_collision_count == 0
            and self.cross_shard_actor_intersection_count == 0
            and self.cross_shard_attempt_intersection_count == 0
            and self.development_actor_count
            >= AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
            and self.calibration_actor_count
            >= AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
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

    @classmethod
    def issue(
        cls,
        *,
        baseline: Aihub538LabelCapacityAggregate,
        candidate: Aihub538LabelCapacityAggregate,
        baseline_admission_receipt_sha256: str,
        baseline_admission_manifest_sha256: str,
    ) -> "Aihub538LabelCapacityAuditReceipt":
        if not baseline.items or not candidate.items:
            raise ValueError("aihub538_label_capacity_population_empty")
        if (
            baseline.identity_namespace_commitment_sha256
            != candidate.identity_namespace_commitment_sha256
            or baseline.member_namespace_commitment_sha256
            != candidate.member_namespace_commitment_sha256
        ):
            raise ValueError("aihub538_label_capacity_namespace_mismatch")
        path_collision = len(
            baseline.relative_path_hmac_sha256
            & candidate.relative_path_hmac_sha256
        )
        baseline_actors = {item.actor_token_sha256 for item in baseline.items}
        candidate_actors = {item.actor_token_sha256 for item in candidate.items}
        baseline_attempts = {item.attempt_token_sha256 for item in baseline.items}
        candidate_attempts = {item.attempt_token_sha256 for item in candidate.items}
        actor_intersection = len(baseline_actors & candidate_actors)
        attempt_intersection = len(baseline_attempts & candidate_attempts)
        combined_items = baseline.items + candidate.items
        sample_digests = [item.sample_sha256 for item in combined_items]
        if len(sample_digests) != len(set(sample_digests)):
            raise ValueError("aihub538_label_capacity_sample_collision")
        actors = baseline_actors | candidate_actors
        assignments = _balanced_assignment_for_audit(actors)
        attempt_actor: dict[str, str] = {}
        partitioned: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        for item in combined_items:
            previous = attempt_actor.setdefault(
                item.attempt_token_sha256,
                item.actor_token_sha256,
            )
            if previous != item.actor_token_sha256:
                raise ValueError("aihub538_label_capacity_attempt_actor_mismatch")
            partitioned[assignments[item.actor_token_sha256]].append(item)

        selected: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        angle_receipts: list[Aihub538LabelCapacityAngleReceipt] = []
        quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
        for angle in AIHUB538_TRAINING_ANGLES:
            by_partition: dict[str, list[Aihub538LabelCapacityItem]] = {}
            for partition in ("development", "calibration"):
                candidates = sorted(
                    (
                        item for item in partitioned[partition]
                        if item.angle == angle
                    ),
                    key=lambda item: item.sample_sha256,
                )
                by_partition[partition] = candidates
                selected[partition].extend(candidates[:quota])
            angle_receipts.append(
                Aihub538LabelCapacityAngleReceipt(
                    angle=angle,
                    available_label_count=(
                        len(by_partition["development"])
                        + len(by_partition["calibration"])
                    ),
                    development_available_count=len(by_partition["development"]),
                    calibration_available_count=len(by_partition["calibration"]),
                    development_selected_count=min(
                        len(by_partition["development"]), quota
                    ),
                    calibration_selected_count=min(
                        len(by_partition["calibration"]), quota
                    ),
                )
            )

        development_actors = {
            item.actor_token_sha256 for item in partitioned["development"]
        }
        calibration_actors = {
            item.actor_token_sha256 for item in partitioned["calibration"]
        }
        development_attempts = {
            item.attempt_token_sha256 for item in partitioned["development"]
        }
        calibration_attempts = {
            item.attempt_token_sha256 for item in partitioned["calibration"]
        }
        actor_overlap = len(development_actors & calibration_actors)
        attempt_overlap = len(development_attempts & calibration_attempts)
        if actor_overlap or attempt_overlap:
            raise ValueError("aihub538_label_capacity_partition_overlap")
        draft = cls(
            label_archive_sha256=candidate.label_archive_sha256,
            label_content_root_sha256=candidate.label_content_root_sha256,
            baseline_label_archive_sha256=baseline.label_archive_sha256,
            baseline_label_content_root_sha256=(
                baseline.label_content_root_sha256
            ),
            baseline_admission_receipt_sha256=(
                baseline_admission_receipt_sha256
            ),
            baseline_admission_manifest_sha256=(
                baseline_admission_manifest_sha256
            ),
            identity_namespace_commitment_sha256=(
                candidate.identity_namespace_commitment_sha256
            ),
            member_namespace_commitment_sha256=(
                candidate.member_namespace_commitment_sha256
            ),
            label_count=len(combined_items),
            baseline_label_count=len(baseline.items),
            candidate_label_count=len(candidate.items),
            baseline_face_bbox_observation_count=(
                baseline.face_bbox_observation_count
            ),
            baseline_lip_bbox_observation_count=(
                baseline.lip_bbox_observation_count
            ),
            candidate_face_bbox_observation_count=(
                candidate.face_bbox_observation_count
            ),
            candidate_lip_bbox_observation_count=(
                candidate.lip_bbox_observation_count
            ),
            baseline_face_reversed_bbox_unavailable_count=(
                baseline.face_reversed_bbox_unavailable_count
            ),
            baseline_lip_reversed_bbox_unavailable_count=(
                baseline.lip_reversed_bbox_unavailable_count
            ),
            candidate_face_reversed_bbox_unavailable_count=(
                candidate.face_reversed_bbox_unavailable_count
            ),
            candidate_lip_reversed_bbox_unavailable_count=(
                candidate.lip_reversed_bbox_unavailable_count
            ),
            baseline_reversed_bbox_affected_label_count=(
                baseline.reversed_bbox_affected_label_count
            ),
            candidate_reversed_bbox_affected_label_count=(
                candidate.reversed_bbox_affected_label_count
            ),
            actor_count=len(actors),
            attempt_count=len(attempt_actor),
            cross_shard_path_collision_count=path_collision,
            cross_shard_actor_intersection_count=actor_intersection,
            cross_shard_attempt_intersection_count=attempt_intersection,
            development_actor_count=len(development_actors),
            calibration_actor_count=len(calibration_actors),
            actor_overlap_count=actor_overlap,
            attempt_overlap_count=attempt_overlap,
            angle_receipts=tuple(angle_receipts),
            actor_assignment_root_sha256=(
                aihub538_balanced_assignment_commitment(assignments)
            ),
            development_selection_root_sha256=_selection_root(
                selected["development"]
            ),
            calibration_selection_root_sha256=_selection_root(
                selected["calibration"]
            ),
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

    def unsigned_json_object(self) -> dict[str, object]:
        first_audit = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[0]
        first_pair = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[0]
        next_audit = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1]
        return {
            "schema_version": "voice_coach.aihub538_label_capacity_audit.v4",
            "status": (
                "completed_feasible"
                if self.is_feasible
                else "completed_infeasible"
            ),
            "dataset_id": 538,
            "dataset_partition": (
                AIHUB538_TL175_CAPACITY_AUDIT_SHARD.dataset_partition
            ),
            "shard_spec_sha256": (
                AIHUB538_TL175_CAPACITY_AUDIT_SHARD.spec_sha256
            ),
            "file_keys": list(
                AIHUB538_TL175_CAPACITY_AUDIT_SHARD.ordered_file_keys
            ),
            "label_role_component": (
                AIHUB538_TL175_CAPACITY_AUDIT_SHARD.label_role_component
            ),
            "ladder_policy_sha256": (
                AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256
            ),
            "ladder_audit_rule": (
                AIHUB538_LABEL_CAPACITY_LADDER_POLICY["audit_rule"]
            ),
            "ladder_stopping_rule": (
                AIHUB538_LABEL_CAPACITY_LADDER_POLICY["stopping_rule"]
            ),
            "outcome_dependent_shard_cherry_pick_allowed": False,
            "evaluated_label_prefix": [first_audit.label_role_component],
            "evaluated_label_file_key_prefix": [
                first_audit.ordered_file_keys[0]
            ],
            "evaluated_prefix_length": 1,
            "first_feasible_prefix_reached": self.is_feasible,
            "next_label_role_component": (
                None if self.is_feasible else next_audit.label_role_component
            ),
            "next_label_file_key": (
                None if self.is_feasible else next_audit.ordered_file_keys[0]
            ),
            "authorized_source_role_prefix": (
                [first_pair.source_role_component] if self.is_feasible else []
            ),
            "authorized_source_file_key_prefix": (
                [first_pair.ordered_file_keys[1]] if self.is_feasible else []
            ),
            "label_archive_sha256": self.label_archive_sha256,
            "label_content_root_sha256": self.label_content_root_sha256,
            "baseline_partition": "nonlocked_training_ts174_tl174",
            "baseline_label_role_component": "TL174",
            "baseline_label_archive_sha256": (
                self.baseline_label_archive_sha256
            ),
            "baseline_label_content_root_sha256": (
                self.baseline_label_content_root_sha256
            ),
            "baseline_admission_receipt_sha256": (
                self.baseline_admission_receipt_sha256
            ),
            "baseline_admission_manifest_sha256": (
                self.baseline_admission_manifest_sha256
            ),
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "member_namespace_commitment_sha256": (
                self.member_namespace_commitment_sha256
            ),
            "label_count": self.label_count,
            "baseline_label_count": self.baseline_label_count,
            "candidate_label_count": self.candidate_label_count,
            "bbox_observation_policy_sha256": (
                AIHUB538_LABEL_CAPACITY_BBOX_OBSERVATION_POLICY_SHA256
            ),
            "bbox_observation_policy": (
                "strict_numeric_bounds_reversed_bbox_unavailable_v1"
            ),
            "baseline_face_bbox_observation_count": (
                self.baseline_face_bbox_observation_count
            ),
            "baseline_lip_bbox_observation_count": (
                self.baseline_lip_bbox_observation_count
            ),
            "candidate_face_bbox_observation_count": (
                self.candidate_face_bbox_observation_count
            ),
            "candidate_lip_bbox_observation_count": (
                self.candidate_lip_bbox_observation_count
            ),
            "baseline_face_reversed_bbox_unavailable_count": (
                self.baseline_face_reversed_bbox_unavailable_count
            ),
            "baseline_lip_reversed_bbox_unavailable_count": (
                self.baseline_lip_reversed_bbox_unavailable_count
            ),
            "candidate_face_reversed_bbox_unavailable_count": (
                self.candidate_face_reversed_bbox_unavailable_count
            ),
            "candidate_lip_reversed_bbox_unavailable_count": (
                self.candidate_lip_reversed_bbox_unavailable_count
            ),
            "baseline_reversed_bbox_affected_label_count": (
                self.baseline_reversed_bbox_affected_label_count
            ),
            "candidate_reversed_bbox_affected_label_count": (
                self.candidate_reversed_bbox_affected_label_count
            ),
            "bounded_reversed_bbox_count_limit": None,
            "reversed_bbox_coordinates_reordered": False,
            "bbox_outcomes_used_for_capacity_quota": False,
            "actual_media_calibration_requires_usable_bbox": True,
            "actor_count": self.actor_count,
            "attempt_count": self.attempt_count,
            "cross_shard_path_collision_count": (
                self.cross_shard_path_collision_count
            ),
            "cross_shard_actor_intersection_count": (
                self.cross_shard_actor_intersection_count
            ),
            "cross_shard_attempt_intersection_count": (
                self.cross_shard_attempt_intersection_count
            ),
            "actor_assignment_rule": AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
            "minimum_actor_count_per_partition": (
                AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
            ),
            "samples_per_angle_per_partition": (
                AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
            ),
            "development_actor_count": self.development_actor_count,
            "calibration_actor_count": self.calibration_actor_count,
            "actor_overlap_count": self.actor_overlap_count,
            "attempt_overlap_count": self.attempt_overlap_count,
            "angle_capacity": [
                item.to_json_object() for item in self.angle_receipts
            ],
            "actor_assignment_root_sha256": self.actor_assignment_root_sha256,
            "development_selection_root_sha256": (
                self.development_selection_root_sha256
            ),
            "calibration_selection_root_sha256": (
                self.calibration_selection_root_sha256
            ),
            "outcomes_observed": False,
            "capacity_outcome_observed": True,
            "baseline_source_partition_accessed": False,
            "candidate_source_partition_accessed": False,
            "partial_download_accessed": False,
            "locked_partition_accessed": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "individual_hmacs_serialized": False,
            "ts175_pair_admission_precondition_satisfied": self.is_feasible,
            "source_download_authorized": self.is_feasible,
            "promotion_authorized": False,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538LabelCapacityAuditReceipt":
        if not isinstance(value, dict):
            raise ValueError("aihub538_label_capacity_receipt_invalid")
        angles = value.get("angle_capacity")
        if not isinstance(angles, list):
            raise ValueError("aihub538_label_capacity_receipt_invalid")
        try:
            receipt = cls(
                label_archive_sha256=value["label_archive_sha256"],
                label_content_root_sha256=value["label_content_root_sha256"],
                baseline_label_archive_sha256=(
                    value["baseline_label_archive_sha256"]
                ),
                baseline_label_content_root_sha256=(
                    value["baseline_label_content_root_sha256"]
                ),
                baseline_admission_receipt_sha256=(
                    value["baseline_admission_receipt_sha256"]
                ),
                baseline_admission_manifest_sha256=(
                    value["baseline_admission_manifest_sha256"]
                ),
                identity_namespace_commitment_sha256=(
                    value["identity_namespace_commitment_sha256"]
                ),
                member_namespace_commitment_sha256=(
                    value["member_namespace_commitment_sha256"]
                ),
                label_count=value["label_count"],
                baseline_label_count=value["baseline_label_count"],
                candidate_label_count=value["candidate_label_count"],
                baseline_face_bbox_observation_count=(
                    value["baseline_face_bbox_observation_count"]
                ),
                baseline_lip_bbox_observation_count=(
                    value["baseline_lip_bbox_observation_count"]
                ),
                candidate_face_bbox_observation_count=(
                    value["candidate_face_bbox_observation_count"]
                ),
                candidate_lip_bbox_observation_count=(
                    value["candidate_lip_bbox_observation_count"]
                ),
                baseline_face_reversed_bbox_unavailable_count=(
                    value["baseline_face_reversed_bbox_unavailable_count"]
                ),
                baseline_lip_reversed_bbox_unavailable_count=(
                    value["baseline_lip_reversed_bbox_unavailable_count"]
                ),
                candidate_face_reversed_bbox_unavailable_count=(
                    value["candidate_face_reversed_bbox_unavailable_count"]
                ),
                candidate_lip_reversed_bbox_unavailable_count=(
                    value["candidate_lip_reversed_bbox_unavailable_count"]
                ),
                baseline_reversed_bbox_affected_label_count=(
                    value["baseline_reversed_bbox_affected_label_count"]
                ),
                candidate_reversed_bbox_affected_label_count=(
                    value["candidate_reversed_bbox_affected_label_count"]
                ),
                actor_count=value["actor_count"],
                attempt_count=value["attempt_count"],
                cross_shard_path_collision_count=(
                    value["cross_shard_path_collision_count"]
                ),
                cross_shard_actor_intersection_count=(
                    value["cross_shard_actor_intersection_count"]
                ),
                cross_shard_attempt_intersection_count=(
                    value["cross_shard_attempt_intersection_count"]
                ),
                development_actor_count=value["development_actor_count"],
                calibration_actor_count=value["calibration_actor_count"],
                actor_overlap_count=value["actor_overlap_count"],
                attempt_overlap_count=value["attempt_overlap_count"],
                angle_receipts=tuple(
                    Aihub538LabelCapacityAngleReceipt.from_json_object(item)
                    for item in angles
                ),
                actor_assignment_root_sha256=(
                    value["actor_assignment_root_sha256"]
                ),
                development_selection_root_sha256=(
                    value["development_selection_root_sha256"]
                ),
                calibration_selection_root_sha256=(
                    value["calibration_selection_root_sha256"]
                ),
                receipt_sha256=value["receipt_sha256"],
            )
            receipt._validate()
        except (KeyError, TypeError, ValueError):
            raise ValueError("aihub538_label_capacity_receipt_invalid") from None
        if receipt.to_json_object() != value:
            raise ValueError("aihub538_label_capacity_receipt_invalid")
        return receipt

    def _validate(self) -> None:
        for name in (
            "label_archive_sha256",
            "label_content_root_sha256",
            "baseline_label_archive_sha256",
            "baseline_label_content_root_sha256",
            "baseline_admission_receipt_sha256",
            "baseline_admission_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "actor_assignment_root_sha256",
            "development_selection_root_sha256",
            "calibration_selection_root_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if (
            self.label_count <= 0
            or self.baseline_label_count <= 0
            or self.candidate_label_count <= 0
            or self.label_count
            != self.baseline_label_count + self.candidate_label_count
            or self.actor_count <= 0
            or self.attempt_count <= 0
            or self.development_actor_count < 0
            or self.calibration_actor_count < 0
            or self.development_actor_count + self.calibration_actor_count
            != self.actor_count
            or self.actor_overlap_count != 0
            or self.attempt_overlap_count != 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in (
                    self.cross_shard_path_collision_count,
                    self.cross_shard_actor_intersection_count,
                    self.cross_shard_attempt_intersection_count,
                )
            )
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in (
                    self.baseline_face_bbox_observation_count,
                    self.baseline_lip_bbox_observation_count,
                    self.candidate_face_bbox_observation_count,
                    self.candidate_lip_bbox_observation_count,
                    self.baseline_face_reversed_bbox_unavailable_count,
                    self.baseline_lip_reversed_bbox_unavailable_count,
                    self.candidate_face_reversed_bbox_unavailable_count,
                    self.candidate_lip_reversed_bbox_unavailable_count,
                    self.baseline_reversed_bbox_affected_label_count,
                    self.candidate_reversed_bbox_affected_label_count,
                )
            )
            or self.baseline_face_reversed_bbox_unavailable_count
            > self.baseline_face_bbox_observation_count
            or self.baseline_lip_reversed_bbox_unavailable_count
            > self.baseline_lip_bbox_observation_count
            or self.candidate_face_reversed_bbox_unavailable_count
            > self.candidate_face_bbox_observation_count
            or self.candidate_lip_reversed_bbox_unavailable_count
            > self.candidate_lip_bbox_observation_count
            or self.baseline_reversed_bbox_affected_label_count
            > self.baseline_label_count
            or self.candidate_reversed_bbox_affected_label_count
            > self.candidate_label_count
            or tuple(item.angle for item in self.angle_receipts)
            != AIHUB538_TRAINING_ANGLES
            or any(
                item.development_selected_count
                != min(
                    item.development_available_count,
                    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
                )
                or item.calibration_selected_count
                != min(
                    item.calibration_available_count,
                    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
                )
                for item in self.angle_receipts
            )
        ):
            raise ValueError("aihub538_label_capacity_receipt_invalid")
        if sha256_document(self.unsigned_json_object()) != self.receipt_sha256:
            raise ValueError("aihub538_label_capacity_receipt_integrity_failed")


def _selection_root(items: list[Aihub538LabelCapacityItem]) -> str:
    return aihub538_label_selection_root_sha256(
        item.sample_sha256 for item in items
    )


def _balanced_assignment_for_audit(
    actors: set[str],
) -> dict[str, str]:
    """Apply the frozen ordering even when the observed prefix is infeasible."""

    return {
        token: "development" if index % 2 == 0 else "calibration"
        for index, token in enumerate(sorted(actors))
    }


__all__ = [
    "AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY",
    "AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256",
    "AIHUB538_LABEL_CAPACITY_BBOX_OBSERVATION_POLICY",
    "AIHUB538_LABEL_CAPACITY_BBOX_OBSERVATION_POLICY_SHA256",
    "Aihub538LabelCapacityAggregate",
    "Aihub538LabelCapacityAngleReceipt",
    "Aihub538LabelCapacityAuditReceipt",
    "Aihub538LabelCapacityItem",
]
