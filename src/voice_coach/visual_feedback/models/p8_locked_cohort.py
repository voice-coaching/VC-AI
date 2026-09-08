"""Read-zero, outcome-independent cohort discovery for AI Hub 538 P8.

The public AI Hub listing exposes 32 validation label/source shard pairs, but
does not publish which pair contains every required angle, noise, and capture
environment slice.  This module therefore freezes the complete public
validation inventory before any locked bytes are read.  The J/K slice is the
creator session-stem code; it is not treated as an alias for the independent
``Video_env.env`` category.  After that seal is
durable, a label-only census may select the smallest shard-pair set that covers
the frozen structural slices.  Selection can use only aggregate label metadata
and identity-overlap exclusions; decode, tracker, sync, QC, and claim outcomes
are not representable in the input contract.

This module does not open files, authorize a download, or authorize source
media access.  A separate selected-cohort source seal is required for those
actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    require_utc_timestamp,
    sha256_document,
)


P8_LOCKED_COHORT_DISCOVERY_CONTRACT_SCHEMA_VERSION = (
    "voice_coach.p8_locked_cohort_discovery_contract.v1"
)
P8_LOCKED_COHORT_DISCOVERY_SEAL_SCHEMA_VERSION = (
    "voice_coach.p8_locked_cohort_discovery_seal.v1"
)
P8_LOCKED_LABEL_CENSUS_ENTRY_SCHEMA_VERSION = (
    "voice_coach.p8_locked_label_census_entry.v1"
)
P8_LOCKED_COHORT_SELECTION_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.p8_locked_cohort_selection_receipt.v1"
)
P8_LOCKED_COHORT_SELECTION_RULE_ID = (
    "minimum_pair_full_slice_cover_then_numeric_pair_order_v1"
)
P8_LOCKED_COHORT_DATASET_ID = "538"

P8_REQUIRED_ANGLES = tuple("ABCDEFGHI")
P8_REQUIRED_NOISE_CODES = tuple(str(value) for value in range(1, 7))
P8_REQUIRED_ENVIRONMENTS = ("J", "K")
P8_REQUIRED_SLICE_KEYS = (
    tuple(("angle", value) for value in P8_REQUIRED_ANGLES)
    + tuple(("noise", value) for value in P8_REQUIRED_NOISE_CODES)
    + tuple(("environment", value) for value in P8_REQUIRED_ENVIRONMENTS)
)

_SELECTION_METADATA_FIELDS = (
    "angle",
    "noise_code",
    "session_environment_code",
    "reference_actor_hmac_membership",
    "reference_attempt_hmac_membership",
)
_FORBIDDEN_SELECTION_OUTCOME_FIELDS = (
    "decode_succeeded",
    "single_face_coverage",
    "tracker_confidence",
    "av_anchor_available",
    "sync_offset_ms",
    "video_qc_decision",
    "visual_feedback_ready",
    "claim_candidate",
    "claim_policy_result",
)

# Public AI Hub dataset-538 validation inventory, normalized from the official
# aihubshell listing.  The order is numeric by pair id rather than portal/tree
# output order so every tie break is fixed before label access.
_PUBLIC_VALIDATION_INVENTORY = (
    (1, "55112", "95_GB", "55080", "850_MB"),
    (2, "55123", "66_GB", "55091", "680_MB"),
    (3, "55134", "76_GB", "55102", "859_MB"),
    (4, "55138", "81_GB", "55106", "865_MB"),
    (5, "55139", "76_GB", "55107", "845_MB"),
    (6, "55140", "59_GB", "55108", "510_MB"),
    (7, "55141", "24_GB", "55109", "179_MB"),
    (8, "55142", "51_GB", "55110", "386_MB"),
    (9, "55143", "96_GB", "55111", "838_MB"),
    (10, "55113", "103_GB", "55081", "839_MB"),
    (11, "55114", "18_GB", "55082", "168_MB"),
    (12, "55115", "49_GB", "55083", "367_MB"),
    (13, "55116", "74_GB", "55084", "553_MB"),
    (14, "55117", "102_GB", "55085", "856_MB"),
    (15, "55118", "98_GB", "55086", "850_MB"),
    (16, "55119", "49_GB", "55087", "368_MB"),
    (17, "55120", "48_GB", "55088", "360_MB"),
    (18, "55121", "97_GB", "55089", "851_MB"),
    (19, "55122", "58_GB", "55090", "508_MB"),
    (20, "55124", "63_GB", "55092", "510_MB"),
    (21, "55125", "74_GB", "55093", "542_MB"),
    (22, "55126", "50_GB", "55094", "369_MB"),
    (23, "55127", "58_GB", "55095", "512_MB"),
    (24, "55128", "64_GB", "55096", "512_MB"),
    (25, "55129", "62_GB", "55097", "509_MB"),
    (26, "55130", "65_GB", "55098", "514_MB"),
    (27, "55131", "49_GB", "55099", "363_MB"),
    (28, "55132", "47_GB", "55100", "359_MB"),
    (29, "55133", "84_GB", "55101", "840_MB"),
    (30, "55135", "88_GB", "55103", "848_MB"),
    (31, "55136", "73_GB", "55104", "551_MB"),
    (32, "55137", "50_GB", "55105", "380_MB"),
)
_PUBLIC_VALIDATION_SIGNATURES = frozenset(_PUBLIC_VALIDATION_INVENTORY)


class P8LockedCohortContractError(VisualFeedbackContractError):
    """Stable fail-closed reason at the locked-cohort boundary."""


@dataclass(frozen=True, slots=True)
class P8LockedValidationShardPair:
    """One exact public validation label/source pair; no local locator."""

    pair_id: int
    source_file_key: str
    source_listed_size: str
    label_file_key: str
    label_listed_size: str

    def __post_init__(self) -> None:
        signature = (
            require_integer(self.pair_id, "pair_id", minimum=1),
            require_safe_identifier(self.source_file_key, "source_file_key"),
            require_safe_identifier(
                self.source_listed_size, "source_listed_size"
            ),
            require_safe_identifier(self.label_file_key, "label_file_key"),
            require_safe_identifier(
                self.label_listed_size, "label_listed_size"
            ),
        )
        if signature not in _PUBLIC_VALIDATION_SIGNATURES:
            raise P8LockedCohortContractError(
                "p8_locked_public_validation_pair_not_allowlisted"
            )

    @property
    def pair_name(self) -> str:
        return f"V{self.pair_id}"

    @property
    def source_partition_id(self) -> str:
        return f"VS{self.pair_id}"

    @property
    def label_partition_id(self) -> str:
        return f"VL{self.pair_id}"

    def to_json_object(self) -> dict[str, object]:
        return {
            "pair_id": self.pair_id,
            "pair_name": self.pair_name,
            "source": {
                "file_key": self.source_file_key,
                "partition_id": self.source_partition_id,
                "role": "locked_media_candidate",
                "listed_size": self.source_listed_size,
            },
            "labels": {
                "file_key": self.label_file_key,
                "partition_id": self.label_partition_id,
                "role": "locked_label_census_candidate",
                "listed_size": self.label_listed_size,
            },
        }


P8_LOCKED_VALIDATION_SHARD_PAIRS = tuple(
    P8LockedValidationShardPair(
        pair_id=pair_id,
        source_file_key=source_key,
        source_listed_size=source_size,
        label_file_key=label_key,
        label_listed_size=label_size,
    )
    for pair_id, source_key, source_size, label_key, label_size in (
        _PUBLIC_VALIDATION_INVENTORY
    )
)
_PAIR_BY_ID = {item.pair_id: item for item in P8_LOCKED_VALIDATION_SHARD_PAIRS}


def _discovery_contract_unsigned(
    *,
    candidate_pairs: tuple[P8LockedValidationShardPair, ...],
    required_slice_keys: tuple[tuple[str, str], ...],
    selection_rule_id: str,
    selection_metadata_fields: tuple[str, ...],
    forbidden_selection_outcome_fields: tuple[str, ...],
    label_only_census_required: bool,
    all_candidate_labels_must_be_censused: bool,
    fixed_pair_preselection_forbidden: bool,
    reselection_under_same_protocol_allowed: bool,
    label_download_authorized: bool,
    label_census_access_authorized: bool,
    source_download_authorized: bool,
    source_media_open_authorized: bool,
    dataset_id: str,
    schema_version: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "dataset_id": dataset_id,
        "candidate_pair_count": len(candidate_pairs),
        "candidate_label_archive_count": len(candidate_pairs),
        "candidate_source_archive_count": len(candidate_pairs),
        "candidate_pairs": [item.to_json_object() for item in candidate_pairs],
        "required_slice_keys": [
            {"kind": kind, "value": value}
            for kind, value in required_slice_keys
        ],
        "environment_slice_semantics": (
            "creator_session_stem_code_J_or_K_not_Video_env_category"
        ),
        "selection_rule_id": selection_rule_id,
        "selection_metadata_fields": list(selection_metadata_fields),
        "forbidden_selection_outcome_fields": list(
            forbidden_selection_outcome_fields
        ),
        "label_only_census_required": label_only_census_required,
        "all_candidate_labels_must_be_censused": (
            all_candidate_labels_must_be_censused
        ),
        "fixed_pair_preselection_forbidden": fixed_pair_preselection_forbidden,
        "reselection_under_same_protocol_allowed": (
            reselection_under_same_protocol_allowed
        ),
        "label_download_authorized": label_download_authorized,
        "label_census_access_authorized": label_census_access_authorized,
        "source_download_authorized": source_download_authorized,
        "source_media_open_authorized": source_media_open_authorized,
        "raw_paths_serialized": False,
        "transcripts_serialized": False,
        "individual_identity_hmacs_serialized": False,
        "per_item_rows_serialized": False,
        "aggregate_only": True,
    }


@dataclass(frozen=True, slots=True)
class P8LockedCohortDiscoveryContract:
    """Frozen candidate universe and metadata-only selection semantics."""

    candidate_pairs: tuple[P8LockedValidationShardPair, ...]
    required_slice_keys: tuple[tuple[str, str], ...]
    selection_rule_id: str
    selection_metadata_fields: tuple[str, ...]
    forbidden_selection_outcome_fields: tuple[str, ...]
    label_only_census_required: bool
    all_candidate_labels_must_be_censused: bool
    fixed_pair_preselection_forbidden: bool
    reselection_under_same_protocol_allowed: bool
    label_download_authorized: bool
    label_census_access_authorized: bool
    source_download_authorized: bool
    source_media_open_authorized: bool
    contract_sha256: str
    dataset_id: str = P8_LOCKED_COHORT_DATASET_ID
    schema_version: str = P8_LOCKED_COHORT_DISCOVERY_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_COHORT_DISCOVERY_CONTRACT_SCHEMA_VERSION:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_contract_schema_invalid"
            )
        if self.dataset_id != P8_LOCKED_COHORT_DATASET_ID:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_dataset_invalid"
            )
        if self.candidate_pairs != P8_LOCKED_VALIDATION_SHARD_PAIRS:
            raise P8LockedCohortContractError(
                "p8_locked_candidate_universe_incomplete"
            )
        if self.required_slice_keys != P8_REQUIRED_SLICE_KEYS:
            raise P8LockedCohortContractError(
                "p8_locked_required_slice_contract_changed"
            )
        if self.selection_rule_id != P8_LOCKED_COHORT_SELECTION_RULE_ID:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_rule_changed"
            )
        if self.selection_metadata_fields != _SELECTION_METADATA_FIELDS:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_fields_changed"
            )
        if (
            self.forbidden_selection_outcome_fields
            != _FORBIDDEN_SELECTION_OUTCOME_FIELDS
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_forbidden_outcomes_changed"
            )
        required_true = (
            self.label_only_census_required,
            self.all_candidate_labels_must_be_censused,
            self.fixed_pair_preselection_forbidden,
        )
        if any(
            require_boolean(value, "contract_boolean") is not True
            for value in required_true
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_invariant_disabled"
            )
        required_false = (
            self.reselection_under_same_protocol_allowed,
            self.label_download_authorized,
            self.label_census_access_authorized,
            self.source_download_authorized,
            self.source_media_open_authorized,
        )
        if any(
            require_boolean(value, "contract_boolean") is not False
            for value in required_false
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_premature_authorization"
            )
        require_sha256(self.contract_sha256, "contract_sha256")
        if self.contract_sha256 != sha256_document(self.unsigned_json_object()):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_contract_digest_invalid"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return _discovery_contract_unsigned(
            candidate_pairs=self.candidate_pairs,
            required_slice_keys=self.required_slice_keys,
            selection_rule_id=self.selection_rule_id,
            selection_metadata_fields=self.selection_metadata_fields,
            forbidden_selection_outcome_fields=(
                self.forbidden_selection_outcome_fields
            ),
            label_only_census_required=self.label_only_census_required,
            all_candidate_labels_must_be_censused=(
                self.all_candidate_labels_must_be_censused
            ),
            fixed_pair_preselection_forbidden=(
                self.fixed_pair_preselection_forbidden
            ),
            reselection_under_same_protocol_allowed=(
                self.reselection_under_same_protocol_allowed
            ),
            label_download_authorized=self.label_download_authorized,
            label_census_access_authorized=(
                self.label_census_access_authorized
            ),
            source_download_authorized=self.source_download_authorized,
            source_media_open_authorized=self.source_media_open_authorized,
            dataset_id=self.dataset_id,
            schema_version=self.schema_version,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "contract_sha256": self.contract_sha256,
        }


def _issue_discovery_contract() -> P8LockedCohortDiscoveryContract:
    values = {
        "candidate_pairs": P8_LOCKED_VALIDATION_SHARD_PAIRS,
        "required_slice_keys": P8_REQUIRED_SLICE_KEYS,
        "selection_rule_id": P8_LOCKED_COHORT_SELECTION_RULE_ID,
        "selection_metadata_fields": _SELECTION_METADATA_FIELDS,
        "forbidden_selection_outcome_fields": (
            _FORBIDDEN_SELECTION_OUTCOME_FIELDS
        ),
        "label_only_census_required": True,
        "all_candidate_labels_must_be_censused": True,
        "fixed_pair_preselection_forbidden": True,
        "reselection_under_same_protocol_allowed": False,
        "label_download_authorized": False,
        "label_census_access_authorized": False,
        "source_download_authorized": False,
        "source_media_open_authorized": False,
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "schema_version": P8_LOCKED_COHORT_DISCOVERY_CONTRACT_SCHEMA_VERSION,
    }
    unsigned = _discovery_contract_unsigned(**values)
    return P8LockedCohortDiscoveryContract(
        **values,
        contract_sha256=sha256_document(unsigned),
    )


P8_LOCKED_COHORT_DISCOVERY_CONTRACT = _issue_discovery_contract()


@dataclass(frozen=True, slots=True)
class P8LockedCohortDiscoverySeal:
    """Proof that the candidate universe/rule was sealed before any access."""

    seal_id: str
    protocol_revision: str
    reference_identity_set_commitments_sha256: str
    state_directory_device: int
    state_directory_inode: int
    sealed_at_utc: str
    locked_candidate_access_count: int
    sealed_before_any_locked_candidate_access: bool
    label_download_authorized: bool
    label_census_access_authorized: bool
    source_download_authorized: bool
    source_media_open_authorized: bool
    seal_sha256: str
    contract_sha256: str = (
        P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
    )
    schema_version: str = P8_LOCKED_COHORT_DISCOVERY_SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_COHORT_DISCOVERY_SEAL_SCHEMA_VERSION:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_schema_invalid"
            )
        require_safe_identifier(self.seal_id, "seal_id")
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        require_sha256(
            self.reference_identity_set_commitments_sha256,
            "reference_identity_set_commitments_sha256",
        )
        require_integer(
            self.state_directory_device,
            "state_directory_device",
            minimum=0,
        )
        require_integer(
            self.state_directory_inode,
            "state_directory_inode",
            minimum=1,
        )
        require_utc_timestamp(self.sealed_at_utc, "sealed_at_utc")
        if self.contract_sha256 != P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_contract_pin_invalid"
            )
        if require_integer(
            self.locked_candidate_access_count,
            "locked_candidate_access_count",
            minimum=0,
        ) != 0:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_not_read_zero"
            )
        if require_boolean(
            self.sealed_before_any_locked_candidate_access,
            "sealed_before_any_locked_candidate_access",
        ) is not True:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_order_invalid"
            )
        authorization_values = (
            self.label_download_authorized,
            self.label_census_access_authorized,
            self.source_download_authorized,
            self.source_media_open_authorized,
        )
        if any(
            require_boolean(value, "discovery_authorization") is not False
            for value in authorization_values
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_premature_authorization"
            )
        require_sha256(self.seal_sha256, "seal_sha256")
        if self.seal_sha256 != sha256_document(self.unsigned_json_object()):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_digest_invalid"
            )

    @classmethod
    def issue(
        cls,
        *,
        seal_id: str,
        protocol_revision: str,
        reference_identity_set_commitments_sha256: str,
        state_directory_device: int,
        state_directory_inode: int,
        sealed_at_utc: str,
    ) -> "P8LockedCohortDiscoverySeal":
        unsigned = {
            "schema_version": P8_LOCKED_COHORT_DISCOVERY_SEAL_SCHEMA_VERSION,
            "seal_id": seal_id,
            "protocol_revision": protocol_revision,
            "reference_identity_set_commitments_sha256": (
                reference_identity_set_commitments_sha256
            ),
            "state_directory_device": state_directory_device,
            "state_directory_inode": state_directory_inode,
            "contract_sha256": (
                P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
            ),
            "sealed_at_utc": sealed_at_utc,
            "locked_candidate_access_count": 0,
            "sealed_before_any_locked_candidate_access": True,
            "label_download_authorized": False,
            "label_census_access_authorized": False,
            "source_download_authorized": False,
            "source_media_open_authorized": False,
            "aggregate_only": True,
        }
        return cls(
            seal_id=seal_id,
            protocol_revision=protocol_revision,
            reference_identity_set_commitments_sha256=(
                reference_identity_set_commitments_sha256
            ),
            state_directory_device=state_directory_device,
            state_directory_inode=state_directory_inode,
            sealed_at_utc=sealed_at_utc,
            locked_candidate_access_count=0,
            sealed_before_any_locked_candidate_access=True,
            label_download_authorized=False,
            label_census_access_authorized=False,
            source_download_authorized=False,
            source_media_open_authorized=False,
            seal_sha256=sha256_document(unsigned),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "seal_id": self.seal_id,
            "protocol_revision": self.protocol_revision,
            "reference_identity_set_commitments_sha256": (
                self.reference_identity_set_commitments_sha256
            ),
            "state_directory_device": self.state_directory_device,
            "state_directory_inode": self.state_directory_inode,
            "contract_sha256": self.contract_sha256,
            "sealed_at_utc": self.sealed_at_utc,
            "locked_candidate_access_count": self.locked_candidate_access_count,
            "sealed_before_any_locked_candidate_access": (
                self.sealed_before_any_locked_candidate_access
            ),
            "label_download_authorized": self.label_download_authorized,
            "label_census_access_authorized": (
                self.label_census_access_authorized
            ),
            "source_download_authorized": self.source_download_authorized,
            "source_media_open_authorized": self.source_media_open_authorized,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "seal_sha256": self.seal_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> "P8LockedCohortDiscoverySeal":
        document = require_mapping(value, "p8_locked_cohort_discovery_seal")
        require_exact_keys(
            document,
            {
                "schema_version",
                "seal_id",
                "protocol_revision",
                "reference_identity_set_commitments_sha256",
                "state_directory_device",
                "state_directory_inode",
                "contract_sha256",
                "sealed_at_utc",
                "locked_candidate_access_count",
                "sealed_before_any_locked_candidate_access",
                "label_download_authorized",
                "label_census_access_authorized",
                "source_download_authorized",
                "source_media_open_authorized",
                "aggregate_only",
                "seal_sha256",
            },
            "p8_locked_cohort_discovery_seal",
        )
        if require_boolean(document["aggregate_only"], "aggregate_only") is not True:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_discovery_seal_not_aggregate_only"
            )
        return cls(
            seal_id=require_safe_identifier(document["seal_id"], "seal_id"),
            protocol_revision=require_safe_identifier(
                document["protocol_revision"], "protocol_revision"
            ),
            reference_identity_set_commitments_sha256=require_sha256(
                document["reference_identity_set_commitments_sha256"],
                "reference_identity_set_commitments_sha256",
            ),
            state_directory_device=require_integer(
                document["state_directory_device"],
                "state_directory_device",
                minimum=0,
            ),
            state_directory_inode=require_integer(
                document["state_directory_inode"],
                "state_directory_inode",
                minimum=1,
            ),
            contract_sha256=require_sha256(
                document["contract_sha256"], "contract_sha256"
            ),
            sealed_at_utc=require_utc_timestamp(
                document["sealed_at_utc"], "sealed_at_utc"
            ),
            locked_candidate_access_count=require_integer(
                document["locked_candidate_access_count"],
                "locked_candidate_access_count",
                minimum=0,
            ),
            sealed_before_any_locked_candidate_access=require_boolean(
                document["sealed_before_any_locked_candidate_access"],
                "sealed_before_any_locked_candidate_access",
            ),
            label_download_authorized=require_boolean(
                document["label_download_authorized"],
                "label_download_authorized",
            ),
            label_census_access_authorized=require_boolean(
                document["label_census_access_authorized"],
                "label_census_access_authorized",
            ),
            source_download_authorized=require_boolean(
                document["source_download_authorized"],
                "source_download_authorized",
            ),
            source_media_open_authorized=require_boolean(
                document["source_media_open_authorized"],
                "source_media_open_authorized",
            ),
            seal_sha256=require_sha256(document["seal_sha256"], "seal_sha256"),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedSliceCount:
    """One non-identifying structural slice count."""

    kind: str
    value: str
    eligible_item_count: int

    def __post_init__(self) -> None:
        key = (
            require_safe_identifier(self.kind, "slice_kind"),
            require_safe_identifier(self.value, "slice_value"),
        )
        if key not in P8_REQUIRED_SLICE_KEYS:
            raise P8LockedCohortContractError(
                "p8_locked_census_slice_not_allowlisted"
            )
        require_integer(
            self.eligible_item_count, "eligible_item_count", minimum=0
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "eligible_item_count": self.eligible_item_count,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8LockedSliceCount":
        document = require_mapping(value, "p8_locked_slice_count")
        require_exact_keys(
            document,
            {"kind", "value", "eligible_item_count"},
            "p8_locked_slice_count",
        )
        return cls(
            kind=require_safe_identifier(document["kind"], "kind"),
            value=require_safe_identifier(document["value"], "value"),
            eligible_item_count=require_integer(
                document["eligible_item_count"],
                "eligible_item_count",
                minimum=0,
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedLabelCensusEntry:
    """Aggregate-only census for one label archive after discovery consume."""

    pair_id: int
    label_file_key: str
    label_archive_sha256: str
    label_content_root_sha256: str
    admitted_label_count: int
    identity_overlap_exclusion_count: int
    eligible_label_count: int
    slice_counts: tuple[P8LockedSliceCount, ...]
    schema_version: str = P8_LOCKED_LABEL_CENSUS_ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_LABEL_CENSUS_ENTRY_SCHEMA_VERSION:
            raise P8LockedCohortContractError(
                "p8_locked_label_census_schema_invalid"
            )
        pair_id = require_integer(self.pair_id, "pair_id", minimum=1)
        pair = _PAIR_BY_ID.get(pair_id)
        if pair is None or self.label_file_key != pair.label_file_key:
            raise P8LockedCohortContractError(
                "p8_locked_label_census_pair_binding_invalid"
            )
        require_sha256(self.label_archive_sha256, "label_archive_sha256")
        require_sha256(
            self.label_content_root_sha256, "label_content_root_sha256"
        )
        admitted = require_integer(
            self.admitted_label_count, "admitted_label_count", minimum=1
        )
        excluded = require_integer(
            self.identity_overlap_exclusion_count,
            "identity_overlap_exclusion_count",
            minimum=0,
        )
        eligible = require_integer(
            self.eligible_label_count, "eligible_label_count", minimum=0
        )
        if excluded + eligible != admitted:
            raise P8LockedCohortContractError(
                "p8_locked_label_census_accounting_invalid"
            )
        if tuple((item.kind, item.value) for item in self.slice_counts) != (
            P8_REQUIRED_SLICE_KEYS
        ):
            raise P8LockedCohortContractError(
                "p8_locked_label_census_slice_set_incomplete"
            )
        for dimension in ("angle", "noise", "environment"):
            observed = sum(
                item.eligible_item_count
                for item in self.slice_counts
                if item.kind == dimension
            )
            if observed != eligible:
                raise P8LockedCohortContractError(
                    "p8_locked_label_census_dimension_total_invalid"
                )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pair_id": self.pair_id,
            "label_file_key": self.label_file_key,
            "label_archive_sha256": self.label_archive_sha256,
            "label_content_root_sha256": self.label_content_root_sha256,
            "admitted_label_count": self.admitted_label_count,
            "identity_overlap_exclusion_count": (
                self.identity_overlap_exclusion_count
            ),
            "eligible_label_count": self.eligible_label_count,
            "slice_counts": [item.to_json_object() for item in self.slice_counts],
            "selection_metadata_fields": list(_SELECTION_METADATA_FIELDS),
            "transcript_used_for_selection": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "individual_identity_hmacs_serialized": False,
            "per_item_rows_serialized": False,
            "evaluation_outcomes_observed": False,
            "source_media_opened": False,
            "aggregate_only": True,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8LockedLabelCensusEntry":
        document = require_mapping(value, "p8_locked_label_census_entry")
        require_exact_keys(
            document,
            {
                "schema_version",
                "pair_id",
                "label_file_key",
                "label_archive_sha256",
                "label_content_root_sha256",
                "admitted_label_count",
                "identity_overlap_exclusion_count",
                "eligible_label_count",
                "slice_counts",
                "selection_metadata_fields",
                "transcript_used_for_selection",
                "raw_paths_serialized",
                "transcripts_serialized",
                "individual_identity_hmacs_serialized",
                "per_item_rows_serialized",
                "evaluation_outcomes_observed",
                "source_media_opened",
                "aggregate_only",
            },
            "p8_locked_label_census_entry",
        )
        if document["selection_metadata_fields"] != list(
            _SELECTION_METADATA_FIELDS
        ):
            raise P8LockedCohortContractError(
                "p8_locked_label_census_selection_fields_invalid"
            )
        for field_name in (
            "transcript_used_for_selection",
            "raw_paths_serialized",
            "transcripts_serialized",
            "individual_identity_hmacs_serialized",
            "per_item_rows_serialized",
            "evaluation_outcomes_observed",
            "source_media_opened",
        ):
            if require_boolean(document[field_name], field_name) is not False:
                raise P8LockedCohortContractError(
                    "p8_locked_label_census_privacy_or_outcome_violation"
                )
        if require_boolean(document["aggregate_only"], "aggregate_only") is not True:
            raise P8LockedCohortContractError(
                "p8_locked_label_census_not_aggregate_only"
            )
        slices_value = document["slice_counts"]
        if not isinstance(slices_value, list):
            raise P8LockedCohortContractError(
                "p8_locked_label_census_slices_invalid"
            )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            pair_id=require_integer(document["pair_id"], "pair_id", minimum=1),
            label_file_key=require_safe_identifier(
                document["label_file_key"], "label_file_key"
            ),
            label_archive_sha256=require_sha256(
                document["label_archive_sha256"], "label_archive_sha256"
            ),
            label_content_root_sha256=require_sha256(
                document["label_content_root_sha256"],
                "label_content_root_sha256",
            ),
            admitted_label_count=require_integer(
                document["admitted_label_count"],
                "admitted_label_count",
                minimum=1,
            ),
            identity_overlap_exclusion_count=require_integer(
                document["identity_overlap_exclusion_count"],
                "identity_overlap_exclusion_count",
                minimum=0,
            ),
            eligible_label_count=require_integer(
                document["eligible_label_count"],
                "eligible_label_count",
                minimum=0,
            ),
            slice_counts=tuple(
                P8LockedSliceCount.from_json_object(item)
                for item in slices_value
            ),
        )


@dataclass(frozen=True, slots=True)
class P8LockedCohortSelectionReceipt:
    """Deterministic result of the sealed 32-label aggregate census."""

    discovery_seal_sha256: str
    census_entries: tuple[P8LockedLabelCensusEntry, ...]
    selected_pair_ids: tuple[int, ...]
    selected_slice_counts: tuple[P8LockedSliceCount, ...]
    selection_rule_id: str
    source_download_authorized: bool
    source_media_open_authorized: bool
    p8_evaluation_authorized: bool
    receipt_sha256: str
    contract_sha256: str = (
        P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
    )
    schema_version: str = P8_LOCKED_COHORT_SELECTION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_COHORT_SELECTION_RECEIPT_SCHEMA_VERSION:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_receipt_schema_invalid"
            )
        require_sha256(self.discovery_seal_sha256, "discovery_seal_sha256")
        if self.contract_sha256 != P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_contract_pin_invalid"
            )
        if tuple(item.pair_id for item in self.census_entries) != tuple(
            range(1, 33)
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_census_universe_incomplete"
            )
        if len(
            {item.label_archive_sha256 for item in self.census_entries}
        ) != len(self.census_entries):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_label_archive_digest_reused"
            )
        expected_selected = _deterministic_selected_pair_ids(
            self.census_entries
        )
        if self.selected_pair_ids != expected_selected:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_not_deterministic"
            )
        expected_counts = _aggregate_selected_slice_counts(
            self.census_entries, expected_selected
        )
        if self.selected_slice_counts != expected_counts or any(
            item.eligible_item_count <= 0 for item in expected_counts
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selected_slice_coverage_incomplete"
            )
        if self.selection_rule_id != P8_LOCKED_COHORT_SELECTION_RULE_ID:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_rule_invalid"
            )
        authorization_values = (
            self.source_download_authorized,
            self.source_media_open_authorized,
            self.p8_evaluation_authorized,
        )
        if any(
            require_boolean(value, "selection_authorization") is not False
            for value in authorization_values
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_premature_authorization"
            )
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_receipt_digest_invalid"
            )

    @property
    def selected_pairs(self) -> tuple[P8LockedValidationShardPair, ...]:
        return tuple(_PAIR_BY_ID[pair_id] for pair_id in self.selected_pair_ids)

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
            "contract_sha256": self.contract_sha256,
            "discovery_seal_sha256": self.discovery_seal_sha256,
            "census_candidate_count": len(self.census_entries),
            "census_entries": [
                item.to_json_object() for item in self.census_entries
            ],
            "selection_rule_id": self.selection_rule_id,
            "selected_pair_ids": list(self.selected_pair_ids),
            "selected_pairs": [
                item.to_json_object() for item in self.selected_pairs
            ],
            "selected_slice_counts": [
                item.to_json_object() for item in self.selected_slice_counts
            ],
            "all_candidate_labels_censused": True,
            "locked_label_archive_access_count": len(self.census_entries),
            "structural_slice_coverage_satisfied": True,
            "decode_tracker_sync_qc_claim_outcomes_used": False,
            "label_census_provenance_verified": False,
            "production_selection_receipt": False,
            "source_media_access_count": 0,
            "source_download_authorized": self.source_download_authorized,
            "source_media_open_authorized": self.source_media_open_authorized,
            "p8_evaluation_authorized": self.p8_evaluation_authorized,
            "reselection_under_same_protocol_allowed": False,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "individual_identity_hmacs_serialized": False,
            "per_item_rows_serialized": False,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedCohortSelectionReceipt":
        document = require_mapping(
            value, "p8_locked_cohort_selection_receipt"
        )
        require_exact_keys(
            document,
            {
                "schema_version",
                "dataset_id",
                "contract_sha256",
                "discovery_seal_sha256",
                "census_candidate_count",
                "census_entries",
                "selection_rule_id",
                "selected_pair_ids",
                "selected_pairs",
                "selected_slice_counts",
                "all_candidate_labels_censused",
                "locked_label_archive_access_count",
                "structural_slice_coverage_satisfied",
                "decode_tracker_sync_qc_claim_outcomes_used",
                "label_census_provenance_verified",
                "production_selection_receipt",
                "source_media_access_count",
                "source_download_authorized",
                "source_media_open_authorized",
                "p8_evaluation_authorized",
                "reselection_under_same_protocol_allowed",
                "raw_paths_serialized",
                "transcripts_serialized",
                "individual_identity_hmacs_serialized",
                "per_item_rows_serialized",
                "aggregate_only",
                "receipt_sha256",
            },
            "p8_locked_cohort_selection_receipt",
        )
        if document["dataset_id"] != P8_LOCKED_COHORT_DATASET_ID:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_dataset_invalid"
            )
        if require_integer(
            document["census_candidate_count"],
            "census_candidate_count",
            minimum=0,
        ) != 32:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_census_universe_incomplete"
            )
        if require_integer(
            document["locked_label_archive_access_count"],
            "locked_label_archive_access_count",
            minimum=0,
        ) != 32:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_label_access_count_invalid"
            )
        required_true_fields = (
            "all_candidate_labels_censused",
            "structural_slice_coverage_satisfied",
            "aggregate_only",
        )
        if any(
            require_boolean(document[name], name) is not True
            for name in required_true_fields
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_required_state_invalid"
            )
        required_false_fields = (
            "decode_tracker_sync_qc_claim_outcomes_used",
            "label_census_provenance_verified",
            "production_selection_receipt",
            "source_download_authorized",
            "source_media_open_authorized",
            "p8_evaluation_authorized",
            "reselection_under_same_protocol_allowed",
            "raw_paths_serialized",
            "transcripts_serialized",
            "individual_identity_hmacs_serialized",
            "per_item_rows_serialized",
        )
        if any(
            require_boolean(document[name], name) is not False
            for name in required_false_fields
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_privacy_outcome_or_authority_invalid"
            )
        if require_integer(
            document["source_media_access_count"],
            "source_media_access_count",
            minimum=0,
        ) != 0:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_source_media_was_accessed"
            )
        census_values = document["census_entries"]
        selected_values = document["selected_pair_ids"]
        slice_values = document["selected_slice_counts"]
        if (
            not isinstance(census_values, list)
            or not isinstance(selected_values, list)
            or not isinstance(slice_values, list)
        ):
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selection_array_invalid"
            )
        census_entries = tuple(
            P8LockedLabelCensusEntry.from_json_object(item)
            for item in census_values
        )
        selected_pair_ids = tuple(
            require_integer(item, "selected_pair_id", minimum=1)
            for item in selected_values
        )
        expected_pairs = [
            _PAIR_BY_ID[pair_id].to_json_object()
            for pair_id in selected_pair_ids
            if pair_id in _PAIR_BY_ID
        ]
        if document["selected_pairs"] != expected_pairs:
            raise P8LockedCohortContractError(
                "p8_locked_cohort_selected_pair_listing_invalid"
            )
        return cls(
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
            contract_sha256=require_sha256(
                document["contract_sha256"], "contract_sha256"
            ),
            discovery_seal_sha256=require_sha256(
                document["discovery_seal_sha256"],
                "discovery_seal_sha256",
            ),
            census_entries=census_entries,
            selected_pair_ids=selected_pair_ids,
            selected_slice_counts=tuple(
                P8LockedSliceCount.from_json_object(item)
                for item in slice_values
            ),
            selection_rule_id=require_safe_identifier(
                document["selection_rule_id"], "selection_rule_id"
            ),
            source_download_authorized=require_boolean(
                document["source_download_authorized"],
                "source_download_authorized",
            ),
            source_media_open_authorized=require_boolean(
                document["source_media_open_authorized"],
                "source_media_open_authorized",
            ),
            p8_evaluation_authorized=require_boolean(
                document["p8_evaluation_authorized"],
                "p8_evaluation_authorized",
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
        )


def select_p8_locked_cohort(
    *,
    discovery_seal: P8LockedCohortDiscoverySeal,
    census_entries: tuple[P8LockedLabelCensusEntry, ...],
) -> P8LockedCohortSelectionReceipt:
    """Evaluate caller aggregates; this cannot attest real label access."""

    if not isinstance(discovery_seal, P8LockedCohortDiscoverySeal):
        raise P8LockedCohortContractError(
            "p8_locked_cohort_discovery_seal_required"
        )
    # Round-trip revalidation rejects forged dataclass instances.
    validated_seal = P8LockedCohortDiscoverySeal.from_json_object(
        discovery_seal.to_json_object()
    )
    entries = tuple(census_entries)
    if tuple(item.pair_id for item in entries) != tuple(range(1, 33)):
        raise P8LockedCohortContractError(
            "p8_locked_cohort_census_universe_incomplete"
        )
    validated_entries = tuple(
        P8LockedLabelCensusEntry.from_json_object(item.to_json_object())
        for item in entries
    )
    selected_pair_ids = _deterministic_selected_pair_ids(validated_entries)
    selected_counts = _aggregate_selected_slice_counts(
        validated_entries, selected_pair_ids
    )
    unsigned = {
        "schema_version": P8_LOCKED_COHORT_SELECTION_RECEIPT_SCHEMA_VERSION,
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "contract_sha256": (
            P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
        ),
        "discovery_seal_sha256": validated_seal.seal_sha256,
        "census_candidate_count": len(validated_entries),
        "census_entries": [
            item.to_json_object() for item in validated_entries
        ],
        "selection_rule_id": P8_LOCKED_COHORT_SELECTION_RULE_ID,
        "selected_pair_ids": list(selected_pair_ids),
        "selected_pairs": [
            _PAIR_BY_ID[pair_id].to_json_object()
            for pair_id in selected_pair_ids
        ],
        "selected_slice_counts": [
            item.to_json_object() for item in selected_counts
        ],
        "all_candidate_labels_censused": True,
        "locked_label_archive_access_count": len(validated_entries),
        "structural_slice_coverage_satisfied": True,
        "decode_tracker_sync_qc_claim_outcomes_used": False,
        "label_census_provenance_verified": False,
        "production_selection_receipt": False,
        "source_media_access_count": 0,
        "source_download_authorized": False,
        "source_media_open_authorized": False,
        "p8_evaluation_authorized": False,
        "reselection_under_same_protocol_allowed": False,
        "raw_paths_serialized": False,
        "transcripts_serialized": False,
        "individual_identity_hmacs_serialized": False,
        "per_item_rows_serialized": False,
        "aggregate_only": True,
    }
    return P8LockedCohortSelectionReceipt(
        discovery_seal_sha256=validated_seal.seal_sha256,
        census_entries=validated_entries,
        selected_pair_ids=selected_pair_ids,
        selected_slice_counts=selected_counts,
        selection_rule_id=P8_LOCKED_COHORT_SELECTION_RULE_ID,
        source_download_authorized=False,
        source_media_open_authorized=False,
        p8_evaluation_authorized=False,
        receipt_sha256=sha256_document(unsigned),
    )


def require_selected_cohort_source_seal_for_production() -> NoReturn:
    """Current production guard: discovery/selection is not source authority."""

    raise P8LockedCohortContractError(
        "p8_locked_selected_cohort_source_seal_required_before_source_access"
    )


def require_label_census_production_adapter_and_consume_authorization() -> NoReturn:
    """Block real label census until a descriptor-bound consumed adapter exists."""

    raise P8LockedCohortContractError(
        "p8_locked_label_census_production_adapter_and_"
        "consume_authorization_required"
    )


def _deterministic_selected_pair_ids(
    entries: tuple[P8LockedLabelCensusEntry, ...],
) -> tuple[int, ...]:
    full_mask = (1 << len(P8_REQUIRED_SLICE_KEYS)) - 1
    # mask -> smallest tuple under (cardinality, numeric lexicographic order).
    best: dict[int, tuple[int, ...]] = {0: ()}
    for entry in entries:
        entry_mask = 0
        for index, item in enumerate(entry.slice_counts):
            if item.eligible_item_count > 0:
                entry_mask |= 1 << index
        snapshot = tuple(best.items())
        for existing_mask, selected in snapshot:
            candidate_mask = existing_mask | entry_mask
            candidate = (*selected, entry.pair_id)
            previous = best.get(candidate_mask)
            if previous is None or (len(candidate), candidate) < (
                len(previous),
                previous,
            ):
                best[candidate_mask] = candidate
    selected = best.get(full_mask)
    if selected is None:
        raise P8LockedCohortContractError(
            "p8_locked_candidate_universe_slice_coverage_incomplete_"
            "new_protocol_required"
        )
    return selected


def _aggregate_selected_slice_counts(
    entries: tuple[P8LockedLabelCensusEntry, ...],
    selected_pair_ids: tuple[int, ...],
) -> tuple[P8LockedSliceCount, ...]:
    selected = {
        item.pair_id: item
        for item in entries
        if item.pair_id in selected_pair_ids
    }
    return tuple(
        P8LockedSliceCount(
            kind=kind,
            value=value,
            eligible_item_count=sum(
                selected[pair_id].slice_counts[index].eligible_item_count
                for pair_id in selected_pair_ids
            ),
        )
        for index, (kind, value) in enumerate(P8_REQUIRED_SLICE_KEYS)
    )


__all__ = [
    "P8_LOCKED_COHORT_DISCOVERY_CONTRACT",
    "P8_LOCKED_COHORT_DISCOVERY_CONTRACT_SCHEMA_VERSION",
    "P8_LOCKED_COHORT_DISCOVERY_SEAL_SCHEMA_VERSION",
    "P8_LOCKED_COHORT_SELECTION_RECEIPT_SCHEMA_VERSION",
    "P8_LOCKED_COHORT_SELECTION_RULE_ID",
    "P8_LOCKED_LABEL_CENSUS_ENTRY_SCHEMA_VERSION",
    "P8_LOCKED_VALIDATION_SHARD_PAIRS",
    "P8_REQUIRED_ANGLES",
    "P8_REQUIRED_ENVIRONMENTS",
    "P8_REQUIRED_NOISE_CODES",
    "P8_REQUIRED_SLICE_KEYS",
    "P8LockedCohortContractError",
    "P8LockedCohortDiscoveryContract",
    "P8LockedCohortDiscoverySeal",
    "P8LockedCohortSelectionReceipt",
    "P8LockedLabelCensusEntry",
    "P8LockedSliceCount",
    "P8LockedValidationShardPair",
    "require_label_census_production_adapter_and_consume_authorization",
    "require_selected_cohort_source_seal_for_production",
    "select_p8_locked_cohort",
]
