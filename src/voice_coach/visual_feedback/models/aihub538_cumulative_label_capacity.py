"""Privacy-safe cumulative label-ladder capacity evidence for AI Hub 538."""

from __future__ import annotations

from dataclasses import dataclass

from .aihub538_balanced_cohort import (
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION,
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
)
from .aihub538_bbox_observation import (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256,
)
from .aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityAngleReceipt,
    Aihub538LabelCapacityAuditReceipt,
    Aihub538LabelCapacityItem,
)
from .aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY,
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256,
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
)
from .aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from .integrity import require_sha256, sha256_document


@dataclass(frozen=True, slots=True)
class Aihub538LabelPrefixLineage:
    """One exact label ZIP in the ordered cumulative prefix."""

    label_role_component: str
    label_file_key: str
    label_archive_sha256: str
    label_content_root_sha256: str
    label_count: int
    face_bbox_observation_count: int
    lip_bbox_observation_count: int
    face_reversed_bbox_unavailable_count: int
    lip_reversed_bbox_unavailable_count: int
    reversed_bbox_affected_label_count: int

    @classmethod
    def from_aggregate(
        cls,
        *,
        shard_spec: Aihub538NonlockedShardSpec,
        aggregate: Aihub538LabelCapacityAggregate,
    ) -> "Aihub538LabelPrefixLineage":
        return cls(
            label_role_component=shard_spec.label_role_component,
            label_file_key=shard_spec.ordered_file_keys[0],
            label_archive_sha256=aggregate.label_archive_sha256,
            label_content_root_sha256=aggregate.label_content_root_sha256,
            label_count=len(aggregate.items),
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

    def __post_init__(self) -> None:
        matches = [
            item
            for item in AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS
            if item.label_role_component == self.label_role_component
            and item.ordered_file_keys == (self.label_file_key,)
        ]
        if len(matches) != 1:
            raise ValueError("aihub538_label_prefix_lineage_role_invalid")
        require_sha256(self.label_archive_sha256, "label_archive_sha256")
        require_sha256(
            self.label_content_root_sha256,
            "label_content_root_sha256",
        )
        counts = (
            self.label_count,
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
                for value in counts
            )
            or self.label_count <= 0
            or self.face_reversed_bbox_unavailable_count
            > self.face_bbox_observation_count
            or self.lip_reversed_bbox_unavailable_count
            > self.lip_bbox_observation_count
            or self.reversed_bbox_affected_label_count > self.label_count
        ):
            raise ValueError("aihub538_label_prefix_lineage_count_invalid")

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }

    @classmethod
    def from_json_object(cls, value: object) -> "Aihub538LabelPrefixLineage":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("aihub538_label_prefix_lineage_invalid")
        try:
            return cls(**value)
        except (TypeError, ValueError):
            raise ValueError("aihub538_label_prefix_lineage_invalid") from None


@dataclass(frozen=True, slots=True)
class Aihub538CumulativeLabelCapacityAuditReceipt:
    """One immutable result for baseline TL174 plus an exact ladder prefix."""

    baseline_label_archive_sha256: str
    baseline_label_content_root_sha256: str
    baseline_admission_receipt_sha256: str
    baseline_admission_manifest_sha256: str
    prior_prefix_receipt_sha256: str
    prefix_lineage: tuple[Aihub538LabelPrefixLineage, ...]
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    label_count: int
    baseline_label_count: int
    candidate_label_count: int
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
    def issue_tl175_tl176_prefix(
        cls,
        *,
        baseline: Aihub538LabelCapacityAggregate,
        tl175: Aihub538LabelCapacityAggregate,
        tl176: Aihub538LabelCapacityAggregate,
        prior_tl175_receipt: Aihub538LabelCapacityAuditReceipt,
        baseline_admission_receipt_sha256: str,
        baseline_admission_manifest_sha256: str,
    ) -> "Aihub538CumulativeLabelCapacityAuditReceipt":
        """Recompute the frozen assignment over TL174 + TL175 + TL176."""

        prior_tl175_receipt._validate()
        # The cumulative audit reparses TL174 and TL175 under the same frozen
        # adapter contract.  Reissue the entire v4 receipt and require exact
        # equality, rather than checking only archive roots.  This binds the
        # prior actor/member namespaces, aggregate counts, balanced assignment,
        # capacity rows, and selection commitments and rejects stale receipts
        # or parser drift before TL176 can authorize source access.
        expected_prior_tl175_receipt = Aihub538LabelCapacityAuditReceipt.issue(
            baseline=baseline,
            candidate=tl175,
            baseline_admission_receipt_sha256=(
                baseline_admission_receipt_sha256
            ),
            baseline_admission_manifest_sha256=(
                baseline_admission_manifest_sha256
            ),
        )
        if (
            prior_tl175_receipt != expected_prior_tl175_receipt
            or prior_tl175_receipt.is_feasible
            or prior_tl175_receipt.label_archive_sha256
            != tl175.label_archive_sha256
            or prior_tl175_receipt.label_content_root_sha256
            != tl175.label_content_root_sha256
            or prior_tl175_receipt.baseline_label_archive_sha256
            != baseline.label_archive_sha256
            or prior_tl175_receipt.baseline_label_content_root_sha256
            != baseline.label_content_root_sha256
            or prior_tl175_receipt.baseline_admission_receipt_sha256
            != baseline_admission_receipt_sha256
            or prior_tl175_receipt.baseline_admission_manifest_sha256
            != baseline_admission_manifest_sha256
        ):
            raise ValueError("aihub538_cumulative_prior_prefix_lineage_invalid")
        aggregates = (baseline, tl175, tl176)
        namespaces = {
            item.identity_namespace_commitment_sha256 for item in aggregates
        }
        member_namespaces = {
            item.member_namespace_commitment_sha256 for item in aggregates
        }
        if len(namespaces) != 1 or len(member_namespaces) != 1:
            raise ValueError("aihub538_cumulative_capacity_namespace_mismatch")

        combined_items = tuple(
            item for aggregate in aggregates for item in aggregate.items
        )
        sample_digests = [item.sample_sha256 for item in combined_items]
        if len(sample_digests) != len(set(sample_digests)):
            raise ValueError("aihub538_cumulative_capacity_sample_collision")
        path_sets = [item.relative_path_hmac_sha256 for item in aggregates]
        actor_sets = [
            {item.actor_token_sha256 for item in aggregate.items}
            for aggregate in aggregates
        ]
        attempt_sets = [
            {item.attempt_token_sha256 for item in aggregate.items}
            for aggregate in aggregates
        ]
        path_collision = sum(map(len, path_sets)) - len(set().union(*path_sets))
        actor_intersection = sum(map(len, actor_sets)) - len(
            set().union(*actor_sets)
        )
        attempt_intersection = sum(map(len, attempt_sets)) - len(
            set().union(*attempt_sets)
        )
        actors = set().union(*actor_sets)
        assignments = {
            token: "development" if index % 2 == 0 else "calibration"
            for index, token in enumerate(sorted(actors))
        }
        partitioned: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        attempt_actor: dict[str, str] = {}
        for item in combined_items:
            previous = attempt_actor.setdefault(
                item.attempt_token_sha256,
                item.actor_token_sha256,
            )
            if previous != item.actor_token_sha256:
                raise ValueError(
                    "aihub538_cumulative_capacity_attempt_actor_mismatch"
                )
            partitioned[assignments[item.actor_token_sha256]].append(item)

        selected: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
        angle_receipts: list[Aihub538LabelCapacityAngleReceipt] = []
        for angle in AIHUB538_TRAINING_ANGLES:
            by_partition = {
                partition: sorted(
                    (
                        item
                        for item in partitioned[partition]
                        if item.angle == angle
                    ),
                    key=lambda item: item.sample_sha256,
                )
                for partition in ("development", "calibration")
            }
            for partition in ("development", "calibration"):
                selected[partition].extend(by_partition[partition][:quota])
            angle_receipts.append(
                Aihub538LabelCapacityAngleReceipt(
                    angle=angle,
                    available_label_count=sum(map(len, by_partition.values())),
                    development_available_count=len(by_partition["development"]),
                    calibration_available_count=len(by_partition["calibration"]),
                    development_selected_count=min(
                        quota, len(by_partition["development"])
                    ),
                    calibration_selected_count=min(
                        quota, len(by_partition["calibration"])
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
        lineage = (
            Aihub538LabelPrefixLineage.from_aggregate(
                shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[0],
                aggregate=tl175,
            ),
            Aihub538LabelPrefixLineage.from_aggregate(
                shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1],
                aggregate=tl176,
            ),
        )
        draft = cls(
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
            prior_prefix_receipt_sha256=prior_tl175_receipt.receipt_sha256,
            prefix_lineage=lineage,
            identity_namespace_commitment_sha256=next(iter(namespaces)),
            member_namespace_commitment_sha256=next(iter(member_namespaces)),
            label_count=len(combined_items),
            baseline_label_count=len(baseline.items),
            candidate_label_count=len(tl175.items) + len(tl176.items),
            actor_count=len(actors),
            attempt_count=len(attempt_actor),
            cross_shard_path_collision_count=path_collision,
            cross_shard_actor_intersection_count=actor_intersection,
            cross_shard_attempt_intersection_count=attempt_intersection,
            development_actor_count=len(development_actors),
            calibration_actor_count=len(calibration_actors),
            actor_overlap_count=len(development_actors & calibration_actors),
            attempt_overlap_count=len(
                development_attempts & calibration_attempts
            ),
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
        prefix_length = len(self.prefix_lineage)
        next_audit = (
            AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[prefix_length]
            if not self.is_feasible
            and prefix_length < len(AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS)
            else None
        )
        authorized_pairs = (
            AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[:prefix_length]
            if self.is_feasible
            else ()
        )
        return {
            "schema_version": (
                "voice_coach.aihub538_cumulative_label_capacity_audit.v1"
            ),
            "status": (
                "completed_feasible"
                if self.is_feasible
                else "completed_infeasible"
            ),
            "dataset_id": 538,
            "baseline_partition": (
                AIHUB538_TS174_TL174_SHARD.dataset_partition
            ),
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
            "prior_prefix_receipt_sha256": self.prior_prefix_receipt_sha256,
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
            "evaluated_prefix_length": prefix_length,
            "evaluated_label_prefix": [
                item.label_role_component for item in self.prefix_lineage
            ],
            "evaluated_label_file_key_prefix": [
                item.label_file_key for item in self.prefix_lineage
            ],
            "prefix_lineage": [
                item.to_json_object() for item in self.prefix_lineage
            ],
            "first_feasible_prefix_reached": self.is_feasible,
            "next_label_role_component": (
                next_audit.label_role_component if next_audit else None
            ),
            "next_label_file_key": (
                next_audit.ordered_file_keys[0] if next_audit else None
            ),
            "authorized_source_role_prefix": [
                item.source_role_component for item in authorized_pairs
            ],
            "authorized_source_file_key_prefix": [
                item.ordered_file_keys[1] for item in authorized_pairs
            ],
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "member_namespace_commitment_sha256": (
                self.member_namespace_commitment_sha256
            ),
            "bbox_observation_policy_sha256": (
                AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256
            ),
            "bbox_coordinates_reordered": False,
            "bbox_outcomes_used_for_capacity_quota": False,
            "label_count": self.label_count,
            "baseline_label_count": self.baseline_label_count,
            "candidate_label_count": self.candidate_label_count,
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
            "capacity_outcome_observed": True,
            "media_or_model_outcomes_observed": False,
            "source_download_authorized": self.is_feasible,
            "partial_download_accessed": False,
            "locked_partition_accessed": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "individual_hmacs_serialized": False,
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
    ) -> "Aihub538CumulativeLabelCapacityAuditReceipt":
        if not isinstance(value, dict) or not isinstance(
            value.get("prefix_lineage"), list
        ) or not isinstance(value.get("angle_capacity"), list):
            raise ValueError("aihub538_cumulative_capacity_receipt_invalid")
        try:
            receipt = cls(
                baseline_label_archive_sha256=value[
                    "baseline_label_archive_sha256"
                ],
                baseline_label_content_root_sha256=value[
                    "baseline_label_content_root_sha256"
                ],
                baseline_admission_receipt_sha256=value[
                    "baseline_admission_receipt_sha256"
                ],
                baseline_admission_manifest_sha256=value[
                    "baseline_admission_manifest_sha256"
                ],
                prior_prefix_receipt_sha256=value[
                    "prior_prefix_receipt_sha256"
                ],
                prefix_lineage=tuple(
                    Aihub538LabelPrefixLineage.from_json_object(item)
                    for item in value["prefix_lineage"]
                ),
                identity_namespace_commitment_sha256=value[
                    "identity_namespace_commitment_sha256"
                ],
                member_namespace_commitment_sha256=value[
                    "member_namespace_commitment_sha256"
                ],
                label_count=value["label_count"],
                baseline_label_count=value["baseline_label_count"],
                candidate_label_count=value["candidate_label_count"],
                actor_count=value["actor_count"],
                attempt_count=value["attempt_count"],
                cross_shard_path_collision_count=value[
                    "cross_shard_path_collision_count"
                ],
                cross_shard_actor_intersection_count=value[
                    "cross_shard_actor_intersection_count"
                ],
                cross_shard_attempt_intersection_count=value[
                    "cross_shard_attempt_intersection_count"
                ],
                development_actor_count=value["development_actor_count"],
                calibration_actor_count=value["calibration_actor_count"],
                actor_overlap_count=value["actor_overlap_count"],
                attempt_overlap_count=value["attempt_overlap_count"],
                angle_receipts=tuple(
                    Aihub538LabelCapacityAngleReceipt.from_json_object(item)
                    for item in value["angle_capacity"]
                ),
                actor_assignment_root_sha256=value[
                    "actor_assignment_root_sha256"
                ],
                development_selection_root_sha256=value[
                    "development_selection_root_sha256"
                ],
                calibration_selection_root_sha256=value[
                    "calibration_selection_root_sha256"
                ],
                receipt_sha256=value["receipt_sha256"],
            )
            receipt._validate()
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "aihub538_cumulative_capacity_receipt_invalid"
            ) from None
        if receipt.to_json_object() != value:
            raise ValueError("aihub538_cumulative_capacity_receipt_invalid")
        return receipt

    def _validate(self) -> None:
        for name in (
            "baseline_label_archive_sha256",
            "baseline_label_content_root_sha256",
            "baseline_admission_receipt_sha256",
            "baseline_admission_manifest_sha256",
            "prior_prefix_receipt_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "actor_assignment_root_sha256",
            "development_selection_root_sha256",
            "calibration_selection_root_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        expected_specs = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[
            : len(self.prefix_lineage)
        ]
        if (
            len(self.prefix_lineage) < 2
            or len(self.prefix_lineage)
            > len(AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS)
            or tuple(
                (item.label_role_component, item.label_file_key)
                for item in self.prefix_lineage
            )
            != tuple(
                (item.label_role_component, item.ordered_file_keys[0])
                for item in expected_specs
            )
            or self.label_count
            != self.baseline_label_count + self.candidate_label_count
            or self.candidate_label_count
            != sum(item.label_count for item in self.prefix_lineage)
            or self.baseline_label_count <= 0
            or self.actor_count <= 0
            or self.attempt_count <= 0
            or self.development_actor_count + self.calibration_actor_count
            != self.actor_count
            or self.actor_overlap_count != 0
            or self.attempt_overlap_count != 0
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for count in (
                    self.cross_shard_path_collision_count,
                    self.cross_shard_actor_intersection_count,
                    self.cross_shard_attempt_intersection_count,
                )
            )
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
            raise ValueError("aihub538_cumulative_capacity_receipt_invalid")
        if sha256_document(self.unsigned_json_object()) != self.receipt_sha256:
            raise ValueError("aihub538_cumulative_capacity_receipt_integrity_failed")


def _selection_root(items: list[Aihub538LabelCapacityItem]) -> str:
    return aihub538_label_selection_root_sha256(
        item.sample_sha256 for item in items
    )


__all__ = [
    "Aihub538CumulativeLabelCapacityAuditReceipt",
    "Aihub538LabelPrefixLineage",
]
