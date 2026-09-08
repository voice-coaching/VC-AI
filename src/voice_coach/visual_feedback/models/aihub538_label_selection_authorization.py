"""V5 label-only source authorization with metadata exclusion parity."""

from __future__ import annotations

from dataclasses import dataclass

from .aihub538_balanced_cohort import (
    AIHUB538_LABEL_SELECTION_RULE,
    AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
    AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION,
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
    build_aihub538_balanced_actor_assignment,
)
from .aihub538_bbox_observation import (
    AIHUB538_BOUNDED_REVERSED_BBOX_OBSERVATION_POLICY_SHA256,
)
from .aihub538_cumulative_label_capacity import (
    Aihub538CumulativeLabelCapacityAuditReceipt,
    Aihub538LabelPrefixLineage,
)
from .aihub538_label_capacity import (
    Aihub538LabelCapacityAggregate,
    Aihub538LabelCapacityAngleReceipt,
    Aihub538LabelCapacityAuditReceipt,
    Aihub538LabelCapacityItem,
)
from .aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256,
)
from .aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from .integrity import require_sha256, sha256_document


AIHUB538_LABEL_SELECTION_AUTHORIZATION_SCHEMA_VERSION = (
    "voice_coach.aihub538_cumulative_label_selection_authorization.v5"
)
# Automated release pin issued only after the actual TL174+TL175+TL176 label
# audit and an independent strict reload.  A structurally valid/self-digested
# receipt is not sufficient to authorize source access.
AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256 = (
    "e1ae31e50a31f2efc4e6cc58e8a0fd592e568f545eecf3b1d022c63d09dbb4e2"
)


@dataclass(frozen=True, slots=True)
class Aihub538LabelSelectionAuthorizationReceipt:
    """New source gate; historical cumulative capacity evidence stays intact."""

    historical_capacity_receipt_sha256: str
    prior_prefix_receipt_sha256: str
    baseline_label_archive_sha256: str
    baseline_label_content_root_sha256: str
    baseline_admission_receipt_sha256: str
    baseline_admission_manifest_sha256: str
    prefix_lineage: tuple[Aihub538LabelPrefixLineage, ...]
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    label_count: int
    actor_count: int
    attempt_count: int
    cross_shard_path_collision_count: int
    cross_shard_actor_intersection_count: int
    cross_shard_attempt_intersection_count: int
    actor_overlap_count: int
    attempt_overlap_count: int
    development_population_actor_count: int
    calibration_population_actor_count: int
    development_actor_count: int
    calibration_actor_count: int
    actor_assignment_root_sha256: str
    pre_exclusion_development_selection_root_sha256: str
    pre_exclusion_calibration_selection_root_sha256: str
    metadata_excluded_label_count: int
    metadata_exclusion_root_sha256: str
    post_exclusion_angle_receipts: tuple[
        Aihub538LabelCapacityAngleReceipt, ...
    ]
    post_exclusion_development_selection_root_sha256: str
    post_exclusion_calibration_selection_root_sha256: str
    receipt_sha256: str

    @property
    def is_feasible(self) -> bool:
        quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
        return (
            self.development_actor_count
            >= AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
            and self.calibration_actor_count
            >= AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
            and all(
                item.development_available_count >= quota
                and item.calibration_available_count >= quota
                and item.development_selected_count == quota
                and item.calibration_selected_count == quota
                for item in self.post_exclusion_angle_receipts
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
        historical_capacity_receipt: (
            Aihub538CumulativeLabelCapacityAuditReceipt
        ),
        baseline_admission_receipt_sha256: str,
        baseline_admission_manifest_sha256: str,
    ) -> "Aihub538LabelSelectionAuthorizationReceipt":
        """Re-audit exact labels, then freeze post-exclusion selection roots."""

        expected_historical = (
            Aihub538CumulativeLabelCapacityAuditReceipt.issue_tl175_tl176_prefix(
                baseline=baseline,
                tl175=tl175,
                tl176=tl176,
                prior_tl175_receipt=prior_tl175_receipt,
                baseline_admission_receipt_sha256=(
                    baseline_admission_receipt_sha256
                ),
                baseline_admission_manifest_sha256=(
                    baseline_admission_manifest_sha256
                ),
            )
        )
        historical_capacity_receipt._validate()
        if (
            not historical_capacity_receipt.is_feasible
            or historical_capacity_receipt != expected_historical
        ):
            raise ValueError(
                "aihub538_label_selection_historical_capacity_invalid"
            )
        combined = baseline.items + tl175.items + tl176.items
        assignments = build_aihub538_balanced_actor_assignment(
            item.actor_token_sha256 for item in combined
        )
        assignment_root = aihub538_balanced_assignment_commitment(assignments)
        if (
            assignment_root
            != historical_capacity_receipt.actor_assignment_root_sha256
        ):
            raise ValueError(
                "aihub538_label_selection_actor_assignment_mismatch"
            )

        partitioned: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        excluded: list[Aihub538LabelCapacityItem] = []
        for item in combined:
            if item.metadata_bbox_observation_available:
                partitioned[assignments[item.actor_token_sha256]].append(item)
            else:
                excluded.append(item)

        selected: dict[str, list[Aihub538LabelCapacityItem]] = {
            "development": [],
            "calibration": [],
        }
        quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
        angles: list[Aihub538LabelCapacityAngleReceipt] = []
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
            angles.append(
                Aihub538LabelCapacityAngleReceipt(
                    angle=angle,
                    available_label_count=sum(map(len, by_partition.values())),
                    development_available_count=len(
                        by_partition["development"]
                    ),
                    calibration_available_count=len(
                        by_partition["calibration"]
                    ),
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
        population_actor_sets = {
            partition: {
                item.actor_token_sha256 for item in partitioned[partition]
            }
            for partition in ("development", "calibration")
        }
        selected_actor_sets = {
            partition: {
                item.actor_token_sha256 for item in selected[partition]
            }
            for partition in ("development", "calibration")
        }
        draft = cls(
            historical_capacity_receipt_sha256=(
                historical_capacity_receipt.receipt_sha256
            ),
            prior_prefix_receipt_sha256=(
                historical_capacity_receipt.prior_prefix_receipt_sha256
            ),
            baseline_label_archive_sha256=(
                historical_capacity_receipt.baseline_label_archive_sha256
            ),
            baseline_label_content_root_sha256=(
                historical_capacity_receipt.baseline_label_content_root_sha256
            ),
            baseline_admission_receipt_sha256=(
                historical_capacity_receipt.baseline_admission_receipt_sha256
            ),
            baseline_admission_manifest_sha256=(
                historical_capacity_receipt.baseline_admission_manifest_sha256
            ),
            prefix_lineage=historical_capacity_receipt.prefix_lineage,
            identity_namespace_commitment_sha256=(
                historical_capacity_receipt.identity_namespace_commitment_sha256
            ),
            member_namespace_commitment_sha256=(
                historical_capacity_receipt.member_namespace_commitment_sha256
            ),
            label_count=len(combined),
            actor_count=len(assignments),
            attempt_count=len(
                {item.attempt_token_sha256 for item in combined}
            ),
            cross_shard_path_collision_count=(
                historical_capacity_receipt.cross_shard_path_collision_count
            ),
            cross_shard_actor_intersection_count=(
                historical_capacity_receipt.cross_shard_actor_intersection_count
            ),
            cross_shard_attempt_intersection_count=(
                historical_capacity_receipt.cross_shard_attempt_intersection_count
            ),
            actor_overlap_count=historical_capacity_receipt.actor_overlap_count,
            attempt_overlap_count=(
                historical_capacity_receipt.attempt_overlap_count
            ),
            development_population_actor_count=len(
                population_actor_sets["development"]
            ),
            calibration_population_actor_count=len(
                population_actor_sets["calibration"]
            ),
            development_actor_count=len(selected_actor_sets["development"]),
            calibration_actor_count=len(selected_actor_sets["calibration"]),
            actor_assignment_root_sha256=assignment_root,
            pre_exclusion_development_selection_root_sha256=(
                historical_capacity_receipt.development_selection_root_sha256
            ),
            pre_exclusion_calibration_selection_root_sha256=(
                historical_capacity_receipt.calibration_selection_root_sha256
            ),
            metadata_excluded_label_count=len(excluded),
            metadata_exclusion_root_sha256=(
                aihub538_label_selection_root_sha256(
                    item.sample_sha256 for item in excluded
                )
            ),
            post_exclusion_angle_receipts=tuple(angles),
            post_exclusion_development_selection_root_sha256=(
                aihub538_label_selection_root_sha256(
                    item.sample_sha256 for item in selected["development"]
                )
            ),
            post_exclusion_calibration_selection_root_sha256=(
                aihub538_label_selection_root_sha256(
                    item.sample_sha256 for item in selected["calibration"]
                )
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
        return {
            "schema_version": (
                AIHUB538_LABEL_SELECTION_AUTHORIZATION_SCHEMA_VERSION
            ),
            "status": (
                "completed_feasible"
                if self.is_feasible
                else "completed_infeasible"
            ),
            "dataset_id": 538,
            "evaluated_label_prefix": ["TL175", "TL176"],
            "evaluated_label_file_key_prefix": ["54726", "54727"],
            "authorized_source_role_prefix": (
                ["TS175", "TS176"] if self.is_feasible else []
            ),
            "authorized_source_file_key_prefix": (
                ["54945", "54946"] if self.is_feasible else []
            ),
            "historical_capacity_receipt_sha256": (
                self.historical_capacity_receipt_sha256
            ),
            "prior_prefix_receipt_sha256": (
                self.prior_prefix_receipt_sha256
            ),
            "ladder_policy_sha256": (
                AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256
            ),
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
            "prefix_lineage": [
                item.to_json_object() for item in self.prefix_lineage
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
            "label_selection_rule": AIHUB538_LABEL_SELECTION_RULE,
            "actor_assignment_rule": AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
            "samples_per_angle_per_partition": (
                AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
            ),
            "minimum_actor_count_per_partition": (
                AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
            ),
            "label_count": self.label_count,
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
            "actor_overlap_count": self.actor_overlap_count,
            "attempt_overlap_count": self.attempt_overlap_count,
            "development_population_actor_count": (
                self.development_population_actor_count
            ),
            "calibration_population_actor_count": (
                self.calibration_population_actor_count
            ),
            "development_actor_count": self.development_actor_count,
            "calibration_actor_count": self.calibration_actor_count,
            "actor_assignment_root_sha256": (
                self.actor_assignment_root_sha256
            ),
            "pre_exclusion_development_selection_root_sha256": (
                self.pre_exclusion_development_selection_root_sha256
            ),
            "pre_exclusion_calibration_selection_root_sha256": (
                self.pre_exclusion_calibration_selection_root_sha256
            ),
            "metadata_excluded_label_count": (
                self.metadata_excluded_label_count
            ),
            "metadata_exclusion_root_sha256": (
                self.metadata_exclusion_root_sha256
            ),
            "post_exclusion_angle_capacity": [
                item.to_json_object()
                for item in self.post_exclusion_angle_receipts
            ],
            "post_exclusion_development_selection_root_sha256": (
                self.post_exclusion_development_selection_root_sha256
            ),
            "post_exclusion_calibration_selection_root_sha256": (
                self.post_exclusion_calibration_selection_root_sha256
            ),
            "metadata_unavailable_excluded_before_selection": True,
            "post_exclusion_exact_quota_revalidated": True,
            "bbox_endpoint_repair_allowed": False,
            "hidden_skip_allowed": False,
            "media_or_model_outcomes_observed": False,
            "source_download_authorized": self.is_feasible,
            "locked_partition_accessed": False,
            "partial_download_accessed": False,
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
    ) -> "Aihub538LabelSelectionAuthorizationReceipt":
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("prefix_lineage"), list)
            or not isinstance(value.get("post_exclusion_angle_capacity"), list)
        ):
            raise ValueError("aihub538_label_selection_receipt_invalid")
        try:
            receipt = cls(
                historical_capacity_receipt_sha256=value[
                    "historical_capacity_receipt_sha256"
                ],
                prior_prefix_receipt_sha256=value[
                    "prior_prefix_receipt_sha256"
                ],
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
                actor_overlap_count=value["actor_overlap_count"],
                attempt_overlap_count=value["attempt_overlap_count"],
                development_population_actor_count=value[
                    "development_population_actor_count"
                ],
                calibration_population_actor_count=value[
                    "calibration_population_actor_count"
                ],
                development_actor_count=value["development_actor_count"],
                calibration_actor_count=value["calibration_actor_count"],
                actor_assignment_root_sha256=value[
                    "actor_assignment_root_sha256"
                ],
                pre_exclusion_development_selection_root_sha256=value[
                    "pre_exclusion_development_selection_root_sha256"
                ],
                pre_exclusion_calibration_selection_root_sha256=value[
                    "pre_exclusion_calibration_selection_root_sha256"
                ],
                metadata_excluded_label_count=value[
                    "metadata_excluded_label_count"
                ],
                metadata_exclusion_root_sha256=value[
                    "metadata_exclusion_root_sha256"
                ],
                post_exclusion_angle_receipts=tuple(
                    Aihub538LabelCapacityAngleReceipt.from_json_object(item)
                    for item in value["post_exclusion_angle_capacity"]
                ),
                post_exclusion_development_selection_root_sha256=value[
                    "post_exclusion_development_selection_root_sha256"
                ],
                post_exclusion_calibration_selection_root_sha256=value[
                    "post_exclusion_calibration_selection_root_sha256"
                ],
                receipt_sha256=value["receipt_sha256"],
            )
            receipt._validate()
        except (KeyError, TypeError, ValueError):
            raise ValueError("aihub538_label_selection_receipt_invalid") from None
        if receipt.to_json_object() != value:
            raise ValueError("aihub538_label_selection_receipt_invalid")
        return receipt

    def _validate(self) -> None:
        for name in (
            "historical_capacity_receipt_sha256",
            "prior_prefix_receipt_sha256",
            "baseline_label_archive_sha256",
            "baseline_label_content_root_sha256",
            "baseline_admission_receipt_sha256",
            "baseline_admission_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "actor_assignment_root_sha256",
            "pre_exclusion_development_selection_root_sha256",
            "pre_exclusion_calibration_selection_root_sha256",
            "metadata_exclusion_root_sha256",
            "post_exclusion_development_selection_root_sha256",
            "post_exclusion_calibration_selection_root_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if (
            len(self.prefix_lineage) != 2
            or tuple(item.label_role_component for item in self.prefix_lineage)
            != ("TL175", "TL176")
            or tuple(item.angle for item in self.post_exclusion_angle_receipts)
            != AIHUB538_TRAINING_ANGLES
            or self.label_count <= 0
            or self.actor_count < 4
            or self.attempt_count <= 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in (
                    self.development_population_actor_count,
                    self.calibration_population_actor_count,
                    self.development_actor_count,
                    self.calibration_actor_count,
                )
            )
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value != 0
                for value in (
                    self.cross_shard_path_collision_count,
                    self.cross_shard_actor_intersection_count,
                    self.cross_shard_attempt_intersection_count,
                    self.actor_overlap_count,
                    self.attempt_overlap_count,
                )
            )
            or self.development_population_actor_count
            + self.calibration_population_actor_count
            > self.actor_count
            or self.development_actor_count
            > self.development_population_actor_count
            or self.calibration_actor_count
            > self.calibration_population_actor_count
            or self.metadata_excluded_label_count < 0
            or self.metadata_excluded_label_count > self.label_count
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
                for item in self.post_exclusion_angle_receipts
            )
        ):
            raise ValueError("aihub538_label_selection_receipt_invalid")
        if sha256_document(self.unsigned_json_object()) != self.receipt_sha256:
            raise ValueError("aihub538_label_selection_receipt_integrity_failed")


def require_aihub538_frozen_source_authorization_sha256(value: str) -> str:
    """Accept only the exact independently reloaded actual V5 release."""

    digest = require_sha256(value, "source_authorization_receipt_sha256")
    if digest != AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256:
        raise ValueError("aihub538_source_authorization_release_pin_mismatch")
    return digest


def require_aihub538_frozen_source_authorization_receipt(
    receipt: Aihub538LabelSelectionAuthorizationReceipt,
) -> Aihub538LabelSelectionAuthorizationReceipt:
    if type(receipt) is not Aihub538LabelSelectionAuthorizationReceipt:
        raise ValueError("aihub538_source_authorization_release_pin_mismatch")
    receipt._validate()
    if not receipt.is_feasible:
        raise ValueError("aihub538_source_authorization_release_pin_mismatch")
    require_aihub538_frozen_source_authorization_sha256(receipt.receipt_sha256)
    return receipt


__all__ = [
    "AIHUB538_FROZEN_SOURCE_AUTHORIZATION_RECEIPT_SHA256",
    "AIHUB538_LABEL_SELECTION_AUTHORIZATION_SCHEMA_VERSION",
    "Aihub538LabelSelectionAuthorizationReceipt",
    "require_aihub538_frozen_source_authorization_receipt",
    "require_aihub538_frozen_source_authorization_sha256",
]
