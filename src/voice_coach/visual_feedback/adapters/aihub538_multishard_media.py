"""Composite actual-media source for three admitted non-locked 538 shards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models.aihub538_multishard_admission import (
    Aihub538AuthorizedShardAdmission,
    Aihub538MultiShardAdmissionReceipt,
)
from ..models.aihub538_label_selection_authorization import (
    Aihub538LabelSelectionAuthorizationReceipt,
)
from ..models.aihub538_balanced_cohort import (
    AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION,
    aihub538_balanced_assignment_commitment,
    aihub538_label_selection_root_sha256,
    build_aihub538_balanced_actor_assignment,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.aihub538_training_evidence import AIHUB538_TRAINING_ANGLES
from ..services.aihub538_decision_calibration import (
    Aihub538CalibrationMeasurement,
    Aihub538CalibrationMediaItem,
    Aihub538DecisionCalibrationError,
)


class Aihub538CalibrationInventoryPort(Protocol):
    """Narrow interface implemented by each actual-media shard adapter."""

    @property
    def inventory(self) -> tuple[Aihub538CalibrationMediaItem, ...]: ...

    def measure(
        self,
        item: Aihub538CalibrationMediaItem,
    ) -> Aihub538CalibrationMeasurement: ...


@dataclass(frozen=True, slots=True)
class Aihub538CalibrationShardSource:
    """One exact shard aggregate, receipt, inventory, and measurement owner."""

    shard_spec: Aihub538NonlockedShardSpec
    authorization: Aihub538AuthorizedShardAdmission
    media_source: Aihub538CalibrationInventoryPort

    def __post_init__(self) -> None:
        try:
            spec = require_aihub538_nonlocked_shard_spec(
                self.shard_spec,
                require_pair=True,
            )
        except ValueError as error:
            raise Aihub538DecisionCalibrationError(str(error)) from None
        if (
            self.authorization.aggregate.shard_id != spec.shard_id
            or self.authorization.admission_receipt.shard_id != spec.shard_id
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_shard_admission_mismatch"
            )


class Aihub538CompositeCalibrationMediaSource:
    """Route each selected digest only to its exact owning shard adapter."""

    def __init__(
        self,
        *,
        sources: tuple[
            Aihub538CalibrationShardSource,
            Aihub538CalibrationShardSource,
            Aihub538CalibrationShardSource,
        ],
        admission_receipt: Aihub538MultiShardAdmissionReceipt,
        capacity_receipt: Aihub538LabelSelectionAuthorizationReceipt,
    ) -> None:
        by_id = {source.shard_spec.shard_id: source for source in sources}
        expected_ids = {
            AIHUB538_TS174_TL174_SHARD.shard_id,
            AIHUB538_TS175_TL175_SHARD.shard_id,
            AIHUB538_TS176_TL176_SHARD.shard_id,
        }
        if set(by_id) != expected_ids or len(by_id) != len(sources):
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_source_set_invalid"
            )
        expected_receipt = Aihub538MultiShardAdmissionReceipt.issue(
            ts174=by_id[
                AIHUB538_TS174_TL174_SHARD.shard_id
            ].authorization,
            ts175=by_id[
                AIHUB538_TS175_TL175_SHARD.shard_id
            ].authorization,
            ts176=by_id[
                AIHUB538_TS176_TL176_SHARD.shard_id
            ].authorization,
            capacity_receipt=capacity_receipt,
        )
        if expected_receipt != admission_receipt:
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_admission_receipt_mismatch"
            )
        owners: dict[
            str,
            tuple[Aihub538CalibrationMediaItem, Aihub538CalibrationInventoryPort],
        ] = {}
        for source in sources:
            aggregate = source.authorization.aggregate
            aggregate_samples = {
                item.sample_sha256 for item in aggregate.samples
            }
            inventory = source.media_source.inventory
            inventory_samples = {item.sample_sha256 for item in inventory}
            aggregate_availability = {
                item.sample_sha256: not bool(
                    item.face_reversed_bbox_unavailable_count
                    or item.lip_reversed_bbox_unavailable_count
                )
                for item in aggregate.samples
            }
            aggregate_label_tokens = {
                item.sample_sha256: item.label_selection_token_sha256
                for item in aggregate.samples
            }
            if (
                len(inventory_samples) != len(inventory)
                or inventory_samples != aggregate_samples
                or {item.actor_token_sha256 for item in inventory}
                != aggregate.restricted_identity_set.speaker_hmac_sha256
                or {item.attempt_token_sha256 for item in inventory}
                != aggregate.restricted_identity_set.session_hmac_sha256
                or any(
                    item.metadata_bbox_observation_available
                    != aggregate_availability[item.sample_sha256]
                    for item in inventory
                )
                or any(
                    item.label_selection_token_sha256 is None
                    or item.label_selection_token_sha256
                    != aggregate_label_tokens[item.sample_sha256]
                    for item in inventory
                )
            ):
                raise Aihub538DecisionCalibrationError(
                    "aihub538_composite_inventory_admission_mismatch"
                )
            for item in inventory:
                if item.sample_sha256 in owners:
                    raise Aihub538DecisionCalibrationError(
                        "aihub538_composite_sample_owner_collision"
                    )
                owners[item.sample_sha256] = (item, source.media_source)
        if len(owners) != admission_receipt.paired_sample_count:
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_inventory_count_mismatch"
            )
        _validate_capacity_selection_lineage(
            inventory=tuple(item[0] for item in owners.values()),
            admission_receipt=admission_receipt,
        )
        self._admission_receipt = admission_receipt
        self._owners = owners
        self._inventory = tuple(
            owners[digest][0] for digest in sorted(owners)
        )

    @property
    def admission_receipt(self) -> Aihub538MultiShardAdmissionReceipt:
        return self._admission_receipt

    @property
    def inventory(self) -> tuple[Aihub538CalibrationMediaItem, ...]:
        return self._inventory

    def measure(
        self,
        item: Aihub538CalibrationMediaItem,
    ) -> Aihub538CalibrationMeasurement:
        owner = self._owners.get(item.sample_sha256)
        if owner is None or owner[0] != item:
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_item_owner_mismatch"
            )
        measured = owner[1].measure(item)
        if measured.item != item:
            raise Aihub538DecisionCalibrationError(
                "aihub538_composite_measurement_owner_mismatch"
            )
        return measured


def _validate_capacity_selection_lineage(
    *,
    inventory: tuple[Aihub538CalibrationMediaItem, ...],
    admission_receipt: Aihub538MultiShardAdmissionReceipt,
) -> None:
    try:
        assignments = build_aihub538_balanced_actor_assignment(
            item.actor_token_sha256 for item in inventory
        )
    except ValueError as error:
        raise Aihub538DecisionCalibrationError(str(error)) from None
    if (
        aihub538_balanced_assignment_commitment(assignments)
        != admission_receipt.capacity_actor_assignment_root_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_composite_capacity_actor_assignment_mismatch"
        )
    excluded_tokens = [
        item.label_selection_token_sha256
        for item in inventory
        if not item.metadata_bbox_observation_available
        and item.label_selection_token_sha256 is not None
    ]
    if (
        len(excluded_tokens)
        != admission_receipt.capacity_metadata_excluded_label_count
        or aihub538_label_selection_root_sha256(excluded_tokens)
        != admission_receipt.capacity_metadata_exclusion_root_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_composite_capacity_metadata_exclusion_mismatch"
        )
    selected: dict[str, list[str]] = {
        "development": [],
        "calibration": [],
    }
    quota = AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION
    for partition in ("development", "calibration"):
        for angle in AIHUB538_TRAINING_ANGLES:
            candidates = sorted(
                item.label_selection_token_sha256
                for item in inventory
                if assignments[item.actor_token_sha256] == partition
                and item.angle == angle
                and item.metadata_bbox_observation_available
                and item.label_selection_token_sha256 is not None
            )
            if len(candidates) < quota:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_composite_capacity_quota_mismatch"
                )
            selected[partition].extend(candidates[:quota])
    if (
        aihub538_label_selection_root_sha256(selected["development"])
        != admission_receipt.capacity_development_label_selection_root_sha256
        or aihub538_label_selection_root_sha256(selected["calibration"])
        != admission_receipt.capacity_calibration_label_selection_root_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_composite_capacity_selection_mismatch"
        )


__all__ = [
    "Aihub538CalibrationInventoryPort",
    "Aihub538CalibrationShardSource",
    "Aihub538CompositeCalibrationMediaSource",
]
