"""Strict allowlist for non-locked AI Hub 538 calibration shards.

The registry is intentionally closed: callers cannot construct an arbitrary
file-key/role combination and thereby route a locked or unaudited partition
through the training/calibration boundary.  TL175 is admitted in two distinct
stages: an outcome-independent numeric label ladder first, then only the exact
source pairs belonging to the first feasible cumulative label prefix.
"""

from __future__ import annotations

from dataclasses import dataclass

from .integrity import sha256_document


AIHUB538_LOCKED_FILE_KEYS = frozenset({"55082", "55114"})

_LABEL_SOURCE_LADDER_ROWS = (
    ("175", "54726", "54945"),
    ("176", "54727", "54946"),
    ("177", "54728", "54947"),
    ("178", "54729", "54948"),
    ("179", "54730", "54949"),
    ("180", "54732", "54951"),
)

_ALLOWED_SIGNATURES = frozenset(
    {
        (
            "ts174_tl174",
            "paired_training_calibration",
            "TS174",
            "TL174",
            ("54725", "54944"),
        ),
        *(
            (
                f"tl{number}_capacity_audit",
                "label_capacity_audit_only",
                "",
                f"TL{number}",
                (label_key,),
            )
            for number, label_key, _source_key in _LABEL_SOURCE_LADDER_ROWS
        ),
        *(
            (
                f"ts{number}_tl{number}",
                "paired_training_calibration",
                f"TS{number}",
                f"TL{number}",
                (label_key, source_key),
            )
            for number, label_key, source_key in _LABEL_SOURCE_LADDER_ROWS
        ),
    }
)


@dataclass(frozen=True, slots=True)
class Aihub538NonlockedShardSpec:
    """One exact approved non-locked dataset role and ordered key contract."""

    shard_id: str
    stage: str
    source_role_component: str
    label_role_component: str
    ordered_file_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        signature = (
            self.shard_id,
            self.stage,
            self.source_role_component,
            self.label_role_component,
            self.ordered_file_keys,
        )
        if signature not in _ALLOWED_SIGNATURES:
            raise ValueError("aihub538_nonlocked_shard_not_allowlisted")
        if AIHUB538_LOCKED_FILE_KEYS.intersection(self.ordered_file_keys):
            raise ValueError("aihub538_locked_key_rejected")

    @property
    def is_pair(self) -> bool:
        return self.stage == "paired_training_calibration"

    @property
    def is_label_capacity_audit(self) -> bool:
        return self.stage == "label_capacity_audit_only"

    @property
    def dataset_partition(self) -> str:
        if self.shard_id == "ts174_tl174":
            return "nonlocked_training_ts174_tl174"
        if self.is_pair:
            return f"nonlocked_training_{self.shard_id}"
        return f"nonlocked_{self.label_role_component.lower()}_capacity_audit"

    @property
    def spec_sha256(self) -> str:
        return sha256_document(self.to_json_object())

    @property
    def sample_selection_rule(self) -> str:
        if not self.is_pair:
            raise ValueError("aihub538_shard_has_no_paired_sample_selection")
        return f"all_exactly_admitted_{self.shard_id}_pairs"

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_nonlocked_shard_spec.v1",
            "dataset_id": 538,
            "shard_id": self.shard_id,
            "stage": self.stage,
            "source_role_component": self.source_role_component,
            "label_role_component": self.label_role_component,
            "ordered_file_keys": list(self.ordered_file_keys),
            "locked_partition": False,
            "strict_allowlist": True,
        }


AIHUB538_TS174_TL174_SHARD = Aihub538NonlockedShardSpec(
    shard_id="ts174_tl174",
    stage="paired_training_calibration",
    source_role_component="TS174",
    label_role_component="TL174",
    ordered_file_keys=("54725", "54944"),
)

AIHUB538_TL175_CAPACITY_AUDIT_SHARD = Aihub538NonlockedShardSpec(
    shard_id="tl175_capacity_audit",
    stage="label_capacity_audit_only",
    source_role_component="",
    label_role_component="TL175",
    ordered_file_keys=("54726",),
)

AIHUB538_TS175_TL175_SHARD = Aihub538NonlockedShardSpec(
    shard_id="ts175_tl175",
    stage="paired_training_calibration",
    source_role_component="TS175",
    label_role_component="TL175",
    ordered_file_keys=("54726", "54945"),
)


def _ladder_audit_spec(
    number: str,
    label_key: str,
) -> Aihub538NonlockedShardSpec:
    return Aihub538NonlockedShardSpec(
        shard_id=f"tl{number}_capacity_audit",
        stage="label_capacity_audit_only",
        source_role_component="",
        label_role_component=f"TL{number}",
        ordered_file_keys=(label_key,),
    )


def _ladder_pair_spec(
    number: str,
    label_key: str,
    source_key: str,
) -> Aihub538NonlockedShardSpec:
    return Aihub538NonlockedShardSpec(
        shard_id=f"ts{number}_tl{number}",
        stage="paired_training_calibration",
        source_role_component=f"TS{number}",
        label_role_component=f"TL{number}",
        ordered_file_keys=(label_key, source_key),
    )


AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS = tuple(
    _ladder_audit_spec(number, label_key)
    for number, label_key, _source_key in _LABEL_SOURCE_LADDER_ROWS
)
AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS = tuple(
    _ladder_pair_spec(number, label_key, source_key)
    for number, label_key, source_key in _LABEL_SOURCE_LADDER_ROWS
)
# Preserve the named TS175 objects used by existing call sites while proving
# they are exactly the first frozen ladder row.
assert AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[0] == (
    AIHUB538_TL175_CAPACITY_AUDIT_SHARD
)
assert AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[0] == (
    AIHUB538_TS175_TL175_SHARD
)
AIHUB538_TL176_CAPACITY_AUDIT_SHARD = (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[1]
)
AIHUB538_TS176_TL176_SHARD = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[1]

AIHUB538_LABEL_CAPACITY_LADDER_POLICY = {
    "schema_version": "voice_coach.aihub538_label_capacity_ladder_policy.v1",
    "dataset_id": 538,
    "baseline_label_role_component": "TL174",
    "baseline_label_file_key": "54725",
    "ordered_rows": [
        {
            "label_role_component": audit.label_role_component,
            "label_file_key": audit.ordered_file_keys[0],
            "source_role_component": pair.source_role_component,
            "source_file_key": pair.ordered_file_keys[1],
        }
        for audit, pair in zip(
            AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
            AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
            strict=True,
        )
    ],
    "audit_rule": "public_numeric_order_cumulative_label_prefix",
    "stopping_rule": (
        "first_prefix_with_actor_minimum_2_per_partition_and_exact_"
        "quota_8_per_angle_per_partition"
    ),
    "actor_assignment_rule": (
        "sorted_actor_hmac_alternating_development_calibration_v1"
    ),
    "outcome_dependent_shard_cherry_pick_allowed": False,
    "source_download_before_first_feasible_prefix_allowed": False,
    "source_download_rule": (
        "only_exact_source_keys_for_every_row_in_first_feasible_prefix"
    ),
    "locked_partition_accessed": False,
    "promotion_authorized": False,
}
AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256 = sha256_document(
    AIHUB538_LABEL_CAPACITY_LADDER_POLICY
)

AIHUB538_APPROVED_NONLOCKED_SHARDS = (
    AIHUB538_TS174_TL174_SHARD,
    *AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    *AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
)

AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS = (
    "TS174",
    "TL174",
    "TS175",
    "TL175",
    "TS176",
    "TL176",
)


def require_aihub538_nonlocked_shard_spec(
    value: Aihub538NonlockedShardSpec,
    *,
    require_pair: bool = False,
    require_label_audit: bool = False,
) -> Aihub538NonlockedShardSpec:
    """Revalidate a caller-supplied spec against the closed registry."""

    if not isinstance(value, Aihub538NonlockedShardSpec):
        raise ValueError("aihub538_nonlocked_shard_spec_invalid")
    # Reconstructing catches forged instances produced by object.__new__ or
    # deserializers that bypass dataclass initialization.
    validated = Aihub538NonlockedShardSpec(
        shard_id=value.shard_id,
        stage=value.stage,
        source_role_component=value.source_role_component,
        label_role_component=value.label_role_component,
        ordered_file_keys=value.ordered_file_keys,
    )
    if require_pair and not validated.is_pair:
        raise ValueError("aihub538_paired_shard_required")
    if require_label_audit and not validated.is_label_capacity_audit:
        raise ValueError("aihub538_label_audit_shard_required")
    return validated


def aihub538_nonlocked_shard_by_id(shard_id: str) -> Aihub538NonlockedShardSpec:
    """Resolve only one literal allowlisted shard identifier."""

    matches = [
        item for item in AIHUB538_APPROVED_NONLOCKED_SHARDS
        if item.shard_id == shard_id
    ]
    if len(matches) != 1:
        raise ValueError("aihub538_nonlocked_shard_not_allowlisted")
    return matches[0]


__all__ = [
    "AIHUB538_APPROVED_NONLOCKED_SHARDS",
    "AIHUB538_LOCKED_FILE_KEYS",
    "AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS",
    "AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS",
    "AIHUB538_LABEL_CAPACITY_LADDER_POLICY",
    "AIHUB538_LABEL_CAPACITY_LADDER_POLICY_SHA256",
    "AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS",
    "AIHUB538_TL175_CAPACITY_AUDIT_SHARD",
    "AIHUB538_TL176_CAPACITY_AUDIT_SHARD",
    "AIHUB538_TS174_TL174_SHARD",
    "AIHUB538_TS175_TL175_SHARD",
    "AIHUB538_TS176_TL176_SHARD",
    "Aihub538NonlockedShardSpec",
    "aihub538_nonlocked_shard_by_id",
    "require_aihub538_nonlocked_shard_spec",
]
