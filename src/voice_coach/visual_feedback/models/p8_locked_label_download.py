"""Typed one-shot authority for the P8 all-label discovery download.

The existing cohort discovery contract and read-zero seal intentionally grant
no access.  This additive transition records the caller's portal-access
confirmation and consumes it for exactly one dataset-538 request containing
all 32 validation label keys.  It never authorizes a validation source key,
opening label payloads, census, evaluation, or promotion.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from .p8_locked_cohort import (
    P8_LOCKED_COHORT_DATASET_ID,
    P8_LOCKED_COHORT_DISCOVERY_CONTRACT,
    P8_LOCKED_VALIDATION_SHARD_PAIRS,
    P8LockedCohortDiscoverySeal,
)
from .p8_actual_source_state import P8ActualSourceReadZeroSeal


P8_LOCKED_LABEL_DOWNLOAD_TRANSACTION_SCHEMA_VERSION = (
    "voice_coach.p8_locked_label_download_transaction.v1"
)
P8_LOCKED_LABEL_STATE_ROOT_SEAL_SCHEMA_VERSION = (
    "voice_coach.p8_locked_label_state_root_seal.v1"
)
P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_SCHEMA_VERSION = (
    "voice_coach.p8_locked_label_download_completion.v1"
)

# Public alias used by the download boundary.  The values remain owned and
# validated by the frozen discovery contract; this does not create a second
# inventory.
AIHUB538_P8_VALIDATION_PAIRS = P8_LOCKED_VALIDATION_SHARD_PAIRS
AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS = tuple(
    pair.label_file_key for pair in AIHUB538_P8_VALIDATION_PAIRS
)
AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS = tuple(
    pair.source_file_key for pair in AIHUB538_P8_VALIDATION_PAIRS
)
AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256 = sha256_document(
    {
        "schema_version": "voice_coach.p8_locked_label_download_request.v1",
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "validation_pairs": [
            pair.to_json_object() for pair in AIHUB538_P8_VALIDATION_PAIRS
        ],
        "ordered_label_file_keys": list(
            AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
        ),
        "requested_label_file_key_count": 32,
        "requested_source_file_key_count": 0,
    }
)
P8_LOCKED_REFERENCE_COMMITMENTS_ROOT_SCHEMA_VERSION = (
    "voice_coach.p8_locked_reference_commitments_root.v1"
)


class P8LockedLabelDownloadError(VisualFeedbackContractError):
    """Stable, path-free failure at the P8 label-download boundary."""


def _validation_pairs_json() -> list[dict[str, object]]:
    return [pair.to_json_object() for pair in AIHUB538_P8_VALIDATION_PAIRS]


def p8_locked_reference_commitments_root_sha256(
    actual_source_seal: P8ActualSourceReadZeroSeal,
) -> str:
    """Bind discovery to the two production-issued reference commitments."""

    if not isinstance(actual_source_seal, P8ActualSourceReadZeroSeal):
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_actual_source_seal_required"
        )
    try:
        validated = P8ActualSourceReadZeroSeal.from_json_object(
            actual_source_seal.to_json_object()
        )
    except VisualFeedbackContractError as error:
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_actual_source_seal_invalid"
        ) from error
    return sha256_document(
        {
            "schema_version": (
                P8_LOCKED_REFERENCE_COMMITMENTS_ROOT_SCHEMA_VERSION
            ),
            "reference_commitments": [
                item.to_json_object() for item in validated.reference_commitments
            ],
        }
    )


@dataclass(frozen=True, slots=True)
class P8LockedLabelStateRootSeal:
    """Pin the sole state-directory inode before label authorization exists."""

    base_read_zero_seal_sha256: str
    actual_source_seal_sha256: str
    discovery_seal_sha256: str
    state_directory_device: int
    state_directory_inode: int
    bound_at_utc: str
    state_root_seal_sha256: str
    schema_version: str = P8_LOCKED_LABEL_STATE_ROOT_SEAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_LABEL_STATE_ROOT_SEAL_SCHEMA_VERSION:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_state_root_schema_invalid"
            )
        require_sha256(
            self.base_read_zero_seal_sha256,
            "base_read_zero_seal_sha256",
        )
        require_sha256(
            self.actual_source_seal_sha256,
            "actual_source_seal_sha256",
        )
        require_sha256(self.discovery_seal_sha256, "discovery_seal_sha256")
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
        require_utc_timestamp(self.bound_at_utc, "bound_at_utc")
        require_sha256(self.state_root_seal_sha256, "state_root_seal_sha256")
        if self.state_root_seal_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_state_root_digest_mismatch"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
            "discovery_contract_sha256": (
                P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
            ),
            "request_sha256": AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256,
            "base_read_zero_seal_sha256": self.base_read_zero_seal_sha256,
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
            "discovery_seal_sha256": self.discovery_seal_sha256,
            "state_directory_device": self.state_directory_device,
            "state_directory_inode": self.state_directory_inode,
            "bound_at_utc": self.bound_at_utc,
            "authoritative_state_root": True,
            "state_root_clone_replay_permitted": False,
            "maximum_label_authorization_consumption_count": 1,
            "source_media_open_authorized": False,
            "promotion_authorized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "state_root_seal_sha256": self.state_root_seal_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "P8LockedLabelStateRootSeal":
        document = require_mapping(value, "p8_locked_label_state_root_seal")
        require_exact_keys(
            document,
            {
                "schema_version",
                "dataset_id",
                "discovery_contract_sha256",
                "request_sha256",
                "base_read_zero_seal_sha256",
                "actual_source_seal_sha256",
                "discovery_seal_sha256",
                "state_directory_device",
                "state_directory_inode",
                "bound_at_utc",
                "authoritative_state_root",
                "state_root_clone_replay_permitted",
                "maximum_label_authorization_consumption_count",
                "source_media_open_authorized",
                "promotion_authorized",
                "state_root_seal_sha256",
            },
            "p8_locked_label_state_root_seal",
        )
        if (
            document["dataset_id"] != P8_LOCKED_COHORT_DATASET_ID
            or document["discovery_contract_sha256"]
            != P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
            or document["request_sha256"]
            != AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256
            or require_boolean(
                document["authoritative_state_root"],
                "authoritative_state_root",
            )
            is not True
            or require_boolean(
                document["state_root_clone_replay_permitted"],
                "state_root_clone_replay_permitted",
            )
            is not False
            or require_integer(
                document["maximum_label_authorization_consumption_count"],
                "maximum_label_authorization_consumption_count",
                minimum=0,
            )
            != 1
            or require_boolean(
                document["source_media_open_authorized"],
                "source_media_open_authorized",
            )
            is not False
            or require_boolean(
                document["promotion_authorized"],
                "promotion_authorized",
            )
            is not False
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_state_root_scope_invalid"
            )
        return cls(
            base_read_zero_seal_sha256=require_sha256(
                document["base_read_zero_seal_sha256"],
                "base_read_zero_seal_sha256",
            ),
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            discovery_seal_sha256=require_sha256(
                document["discovery_seal_sha256"],
                "discovery_seal_sha256",
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
            bound_at_utc=require_utc_timestamp(
                document["bound_at_utc"],
                "bound_at_utc",
            ),
            state_root_seal_sha256=require_sha256(
                document["state_root_seal_sha256"],
                "state_root_seal_sha256",
            ),
            schema_version=require_safe_identifier(
                document["schema_version"],
                "schema_version",
            ),
        )


def issue_p8_locked_label_state_root_seal(
    *,
    discovery_seal: P8LockedCohortDiscoverySeal,
    actual_source_seal: P8ActualSourceReadZeroSeal,
    state_directory_device: int,
    state_directory_inode: int,
) -> P8LockedLabelStateRootSeal:
    reference_root = p8_locked_reference_commitments_root_sha256(
        actual_source_seal
    )
    if (
        discovery_seal.reference_identity_set_commitments_sha256
        != reference_root
        or discovery_seal.state_directory_device != state_directory_device
        or discovery_seal.state_directory_inode != state_directory_inode
    ):
        raise P8LockedLabelDownloadError(
            "p8_locked_label_state_root_lineage_mismatch"
        )
    values = {
        "base_read_zero_seal_sha256": (
            actual_source_seal.base_read_zero_seal_sha256
        ),
        "actual_source_seal_sha256": (
            actual_source_seal.actual_source_seal_sha256
        ),
        "discovery_seal_sha256": discovery_seal.seal_sha256,
        "state_directory_device": state_directory_device,
        "state_directory_inode": state_directory_inode,
        "bound_at_utc": discovery_seal.sealed_at_utc,
    }
    unsigned = {
        "schema_version": P8_LOCKED_LABEL_STATE_ROOT_SEAL_SCHEMA_VERSION,
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "discovery_contract_sha256": (
            P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
        ),
        "request_sha256": AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256,
        **values,
        "authoritative_state_root": True,
        "state_root_clone_replay_permitted": False,
        "maximum_label_authorization_consumption_count": 1,
        "source_media_open_authorized": False,
        "promotion_authorized": False,
    }
    return P8LockedLabelStateRootSeal(
        **values,
        state_root_seal_sha256=sha256_document(unsigned),
    )


@dataclass(frozen=True, slots=True)
class P8LockedLabelDownloadTransaction:
    """Consumed authority for one exact, resumable all-label request."""

    discovery_seal_sha256: str
    actual_source_seal_sha256: str
    reference_identity_set_commitments_sha256: str
    state_root_seal_sha256: str
    state_directory_device: int
    state_directory_inode: int
    output_directory_device: int
    output_directory_inode: int
    authorized_at_utc: str
    transaction_sha256: str
    discovery_contract_sha256: str = (
        P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
    )
    request_sha256: str = AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256
    schema_version: str = P8_LOCKED_LABEL_DOWNLOAD_TRANSACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_LABEL_DOWNLOAD_TRANSACTION_SCHEMA_VERSION:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_transaction_schema_invalid"
            )
        if (
            self.discovery_contract_sha256
            != P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_contract_pin_mismatch"
            )
        if self.request_sha256 != AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_request_pin_mismatch"
            )
        require_sha256(self.discovery_seal_sha256, "discovery_seal_sha256")
        require_sha256(
            self.actual_source_seal_sha256, "actual_source_seal_sha256"
        )
        require_sha256(
            self.reference_identity_set_commitments_sha256,
            "reference_identity_set_commitments_sha256",
        )
        require_sha256(self.state_root_seal_sha256, "state_root_seal_sha256")
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
        require_integer(
            self.output_directory_device,
            "output_directory_device",
            minimum=0,
        )
        require_integer(
            self.output_directory_inode,
            "output_directory_inode",
            minimum=1,
        )
        require_utc_timestamp(self.authorized_at_utc, "authorized_at_utc")
        require_sha256(self.transaction_sha256, "transaction_sha256")
        if self.transaction_sha256 != sha256_document(self.unsigned_json_object()):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_transaction_digest_mismatch"
            )

    @classmethod
    def issue(
        cls,
        *,
        discovery_seal: P8LockedCohortDiscoverySeal,
        actual_source_seal: P8ActualSourceReadZeroSeal,
        state_root_seal: P8LockedLabelStateRootSeal,
        output_directory_device: int,
        output_directory_inode: int,
        authorized_at_utc: str,
        dataset_access_permission_confirmed: bool,
    ) -> "P8LockedLabelDownloadTransaction":
        return issue_p8_locked_label_download_transaction(
            discovery_seal=discovery_seal,
            actual_source_seal=actual_source_seal,
            state_root_seal=state_root_seal,
            output_directory_device=output_directory_device,
            output_directory_inode=output_directory_inode,
            authorized_at_utc=authorized_at_utc,
            dataset_access_permission_confirmed=(
                dataset_access_permission_confirmed
            ),
        )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
            "discovery_contract_sha256": self.discovery_contract_sha256,
            "discovery_seal_sha256": self.discovery_seal_sha256,
            "actual_source_seal_sha256": self.actual_source_seal_sha256,
            "reference_identity_set_commitments_sha256": (
                self.reference_identity_set_commitments_sha256
            ),
            "state_root_seal_sha256": self.state_root_seal_sha256,
            "state_directory_device": self.state_directory_device,
            "state_directory_inode": self.state_directory_inode,
            "request_sha256": self.request_sha256,
            "validation_pair_count": 32,
            "validation_pairs": _validation_pairs_json(),
            "ordered_label_file_keys": list(
                AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
            ),
            "requested_label_file_key_count": 32,
            "output_directory_device": self.output_directory_device,
            "output_directory_inode": self.output_directory_inode,
            "authorized_at_utc": self.authorized_at_utc,
            "dataset_access_permission_confirmed": True,
            "authorization_consumption_count": 1,
            "authorization_reusable": False,
            "same_request_resume_permitted": True,
            "label_download_authorized": True,
            "label_archive_open_authorized": False,
            "label_census_access_authorized": False,
            "source_download_authorized": False,
            "source_media_open_authorized": False,
            "requested_source_file_key_count": 0,
            "credential_persisted": False,
            "permission_grants_technical_promotion": False,
            "promotion_authorized": False,
            "raw_actor_attempt_sample_ids_serialized": False,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "transaction_sha256": self.transaction_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedLabelDownloadTransaction":
        document = require_mapping(value, "p8_locked_label_download_transaction")
        unsigned_keys = {
            "schema_version",
            "dataset_id",
            "discovery_contract_sha256",
            "discovery_seal_sha256",
            "actual_source_seal_sha256",
            "reference_identity_set_commitments_sha256",
            "state_root_seal_sha256",
            "state_directory_device",
            "state_directory_inode",
            "request_sha256",
            "validation_pair_count",
            "validation_pairs",
            "ordered_label_file_keys",
            "requested_label_file_key_count",
            "output_directory_device",
            "output_directory_inode",
            "authorized_at_utc",
            "dataset_access_permission_confirmed",
            "authorization_consumption_count",
            "authorization_reusable",
            "same_request_resume_permitted",
            "label_download_authorized",
            "label_archive_open_authorized",
            "label_census_access_authorized",
            "source_download_authorized",
            "source_media_open_authorized",
            "requested_source_file_key_count",
            "credential_persisted",
            "permission_grants_technical_promotion",
            "promotion_authorized",
            "raw_actor_attempt_sample_ids_serialized",
            "aggregate_only",
        }
        require_exact_keys(
            document,
            unsigned_keys | {"transaction_sha256"},
            "p8_locked_label_download_transaction",
        )
        if document["dataset_id"] != P8_LOCKED_COHORT_DATASET_ID:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_dataset_mismatch"
            )
        if document["validation_pairs"] != _validation_pairs_json():
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_pair_identity_mismatch"
            )
        if document["ordered_label_file_keys"] != list(
            AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_key_allowlist_mismatch"
            )
        if require_integer(
            document["validation_pair_count"],
            "validation_pair_count",
            minimum=0,
        ) != 32:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_pair_count_mismatch"
            )
        if require_integer(
            document["requested_label_file_key_count"],
            "requested_label_file_key_count",
            minimum=0,
        ) != 32:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_label_key_count_mismatch"
            )
        if require_integer(
            document["authorization_consumption_count"],
            "authorization_consumption_count",
            minimum=0,
        ) != 1:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_authorization_not_consumed_once"
            )
        if require_integer(
            document["requested_source_file_key_count"],
            "requested_source_file_key_count",
            minimum=0,
        ) != 0:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_source_archive_forbidden"
            )
        required_true = (
            "dataset_access_permission_confirmed",
            "same_request_resume_permitted",
            "label_download_authorized",
            "aggregate_only",
        )
        required_false = (
            "authorization_reusable",
            "label_archive_open_authorized",
            "label_census_access_authorized",
            "source_download_authorized",
            "source_media_open_authorized",
            "credential_persisted",
            "permission_grants_technical_promotion",
            "promotion_authorized",
            "raw_actor_attempt_sample_ids_serialized",
        )
        if any(
            require_boolean(document[name], name) is not True
            for name in required_true
        ) or any(
            require_boolean(document[name], name) is not False
            for name in required_false
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_authority_scope_invalid"
            )
        return cls(
            discovery_seal_sha256=require_sha256(
                document["discovery_seal_sha256"], "discovery_seal_sha256"
            ),
            actual_source_seal_sha256=require_sha256(
                document["actual_source_seal_sha256"],
                "actual_source_seal_sha256",
            ),
            reference_identity_set_commitments_sha256=require_sha256(
                document["reference_identity_set_commitments_sha256"],
                "reference_identity_set_commitments_sha256",
            ),
            state_root_seal_sha256=require_sha256(
                document["state_root_seal_sha256"],
                "state_root_seal_sha256",
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
            output_directory_device=require_integer(
                document["output_directory_device"],
                "output_directory_device",
                minimum=0,
            ),
            output_directory_inode=require_integer(
                document["output_directory_inode"],
                "output_directory_inode",
                minimum=1,
            ),
            authorized_at_utc=require_utc_timestamp(
                document["authorized_at_utc"], "authorized_at_utc"
            ),
            transaction_sha256=require_sha256(
                document["transaction_sha256"], "transaction_sha256"
            ),
            discovery_contract_sha256=require_sha256(
                document["discovery_contract_sha256"],
                "discovery_contract_sha256",
            ),
            request_sha256=require_sha256(
                document["request_sha256"], "request_sha256"
            ),
            schema_version=document["schema_version"],
        )


def issue_p8_locked_label_download_transaction(
    *,
    discovery_seal: P8LockedCohortDiscoverySeal,
    actual_source_seal: P8ActualSourceReadZeroSeal,
    state_root_seal: P8LockedLabelStateRootSeal,
    output_directory_device: int,
    output_directory_inode: int,
    authorized_at_utc: str,
    dataset_access_permission_confirmed: bool,
) -> P8LockedLabelDownloadTransaction:
    """Issue one self-digested transaction without an invalid draft."""

    if dataset_access_permission_confirmed is not True:
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_portal_access_confirmation_required"
        )
    if not isinstance(discovery_seal, P8LockedCohortDiscoverySeal):
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_discovery_seal_required"
        )
    try:
        validated_seal = P8LockedCohortDiscoverySeal.from_json_object(
            discovery_seal.to_json_object()
        )
    except VisualFeedbackContractError as error:
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_discovery_seal_invalid"
        ) from error
    reference_root = p8_locked_reference_commitments_root_sha256(
        actual_source_seal
    )
    if validated_seal.reference_identity_set_commitments_sha256 != reference_root:
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_reference_commitments_mismatch"
        )
    if not isinstance(state_root_seal, P8LockedLabelStateRootSeal) or (
        state_root_seal.base_read_zero_seal_sha256
        != actual_source_seal.base_read_zero_seal_sha256
        or state_root_seal.actual_source_seal_sha256
        != actual_source_seal.actual_source_seal_sha256
        or state_root_seal.discovery_seal_sha256
        != validated_seal.seal_sha256
        or state_root_seal.state_directory_device
        != validated_seal.state_directory_device
        or state_root_seal.state_directory_inode
        != validated_seal.state_directory_inode
    ):
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_state_root_lineage_mismatch"
        )
    unsigned = {
        "schema_version": P8_LOCKED_LABEL_DOWNLOAD_TRANSACTION_SCHEMA_VERSION,
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "discovery_contract_sha256": (
            P8_LOCKED_COHORT_DISCOVERY_CONTRACT.contract_sha256
        ),
        "discovery_seal_sha256": validated_seal.seal_sha256,
        "actual_source_seal_sha256": (
            actual_source_seal.actual_source_seal_sha256
        ),
        "reference_identity_set_commitments_sha256": (
            validated_seal.reference_identity_set_commitments_sha256
        ),
        "state_root_seal_sha256": state_root_seal.state_root_seal_sha256,
        "state_directory_device": state_root_seal.state_directory_device,
        "state_directory_inode": state_root_seal.state_directory_inode,
        "request_sha256": AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256,
        "validation_pair_count": 32,
        "validation_pairs": _validation_pairs_json(),
        "ordered_label_file_keys": list(
            AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS
        ),
        "requested_label_file_key_count": 32,
        "output_directory_device": output_directory_device,
        "output_directory_inode": output_directory_inode,
        "authorized_at_utc": authorized_at_utc,
        "dataset_access_permission_confirmed": True,
        "authorization_consumption_count": 1,
        "authorization_reusable": False,
        "same_request_resume_permitted": True,
        "label_download_authorized": True,
        "label_archive_open_authorized": False,
        "label_census_access_authorized": False,
        "source_download_authorized": False,
        "source_media_open_authorized": False,
        "requested_source_file_key_count": 0,
        "credential_persisted": False,
        "permission_grants_technical_promotion": False,
        "promotion_authorized": False,
        "raw_actor_attempt_sample_ids_serialized": False,
        "aggregate_only": True,
    }
    return P8LockedLabelDownloadTransaction(
        discovery_seal_sha256=validated_seal.seal_sha256,
        actual_source_seal_sha256=(
            actual_source_seal.actual_source_seal_sha256
        ),
        reference_identity_set_commitments_sha256=(
            validated_seal.reference_identity_set_commitments_sha256
        ),
        state_root_seal_sha256=state_root_seal.state_root_seal_sha256,
        state_directory_device=state_root_seal.state_directory_device,
        state_directory_inode=state_root_seal.state_directory_inode,
        output_directory_device=output_directory_device,
        output_directory_inode=output_directory_inode,
        authorized_at_utc=authorized_at_utc,
        transaction_sha256=sha256_document(unsigned),
    )


@dataclass(frozen=True, slots=True)
class P8LockedLabelDownloadCompletion:
    """Aggregate proof that the exact label request completed and was sealed."""

    transaction_sha256: str
    discovery_seal_sha256: str
    archive_size_bytes: int
    archive_sha256: str
    secure_download_receipt_sha256: str
    completed_at_utc: str
    completion_sha256: str
    request_sha256: str = AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256
    schema_version: str = P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_SCHEMA_VERSION:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_schema_invalid"
            )
        for name in (
            "transaction_sha256",
            "discovery_seal_sha256",
            "archive_sha256",
            "secure_download_receipt_sha256",
            "completion_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if self.request_sha256 != AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_request_mismatch"
            )
        require_integer(self.archive_size_bytes, "archive_size_bytes", minimum=1)
        require_utc_timestamp(self.completed_at_utc, "completed_at_utc")
        if self.completion_sha256 != sha256_document(self.unsigned_json_object()):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_digest_mismatch"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
            "request_sha256": self.request_sha256,
            "transaction_sha256": self.transaction_sha256,
            "discovery_seal_sha256": self.discovery_seal_sha256,
            "validation_pair_count": 32,
            "requested_label_file_key_count": 32,
            "requested_source_file_key_count": 0,
            "archive_size_bytes": self.archive_size_bytes,
            "archive_sha256": self.archive_sha256,
            "secure_download_receipt_sha256": (
                self.secure_download_receipt_sha256
            ),
            "completed_at_utc": self.completed_at_utc,
            "archive_mode": "0400",
            "archive_extracted": False,
            "label_archive_opened": False,
            "label_census_executed": False,
            "source_file_keys_requested": False,
            "payload_admitted": False,
            "label_archive_presence_verified": False,
            "source_payload_presence_verified": False,
            "source_media_opened": False,
            "credential_persisted": False,
            "permission_grants_technical_promotion": False,
            "promotion_authorized": False,
            "raw_actor_attempt_sample_ids_serialized": False,
            "aggregate_only": True,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "completion_sha256": self.completion_sha256,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> "P8LockedLabelDownloadCompletion":
        document = require_mapping(value, "p8_locked_label_download_completion")
        require_exact_keys(
            document,
            {
                "schema_version",
                "dataset_id",
                "request_sha256",
                "transaction_sha256",
                "discovery_seal_sha256",
                "validation_pair_count",
                "requested_label_file_key_count",
                "requested_source_file_key_count",
                "archive_size_bytes",
                "archive_sha256",
                "secure_download_receipt_sha256",
                "completed_at_utc",
                "archive_mode",
                "archive_extracted",
                "label_archive_opened",
                "label_census_executed",
                "source_file_keys_requested",
                "payload_admitted",
                "label_archive_presence_verified",
                "source_payload_presence_verified",
                "source_media_opened",
                "credential_persisted",
                "permission_grants_technical_promotion",
                "promotion_authorized",
                "raw_actor_attempt_sample_ids_serialized",
                "aggregate_only",
                "completion_sha256",
            },
            "p8_locked_label_download_completion",
        )
        if document["dataset_id"] != P8_LOCKED_COHORT_DATASET_ID:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_dataset_mismatch"
            )
        expected_counts = {
            "validation_pair_count": 32,
            "requested_label_file_key_count": 32,
            "requested_source_file_key_count": 0,
        }
        if any(
            require_integer(document[name], name, minimum=0) != expected
            for name, expected in expected_counts.items()
        ):
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_count_mismatch"
            )
        if require_safe_identifier(document["archive_mode"], "archive_mode") != "0400":
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_archive_mode_invalid"
            )
        required_false = (
            "archive_extracted",
            "label_archive_opened",
            "label_census_executed",
            "source_file_keys_requested",
            "payload_admitted",
            "label_archive_presence_verified",
            "source_payload_presence_verified",
            "source_media_opened",
            "credential_persisted",
            "permission_grants_technical_promotion",
            "promotion_authorized",
            "raw_actor_attempt_sample_ids_serialized",
        )
        if any(
            require_boolean(document[name], name) is not False
            for name in required_false
        ) or require_boolean(
            document["aggregate_only"], "aggregate_only"
        ) is not True:
            raise P8LockedLabelDownloadError(
                "p8_locked_label_download_completion_scope_invalid"
            )
        return cls(
            transaction_sha256=require_sha256(
                document["transaction_sha256"], "transaction_sha256"
            ),
            discovery_seal_sha256=require_sha256(
                document["discovery_seal_sha256"], "discovery_seal_sha256"
            ),
            archive_size_bytes=require_integer(
                document["archive_size_bytes"],
                "archive_size_bytes",
                minimum=1,
            ),
            archive_sha256=require_sha256(
                document["archive_sha256"], "archive_sha256"
            ),
            secure_download_receipt_sha256=require_sha256(
                document["secure_download_receipt_sha256"],
                "secure_download_receipt_sha256",
            ),
            completed_at_utc=require_utc_timestamp(
                document["completed_at_utc"], "completed_at_utc"
            ),
            completion_sha256=require_sha256(
                document["completion_sha256"], "completion_sha256"
            ),
            request_sha256=require_sha256(
                document["request_sha256"], "request_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


def issue_p8_locked_label_download_completion(
    *,
    transaction: P8LockedLabelDownloadTransaction,
    archive_size_bytes: int,
    archive_sha256: str,
    secure_download_receipt_sha256: str,
    completed_at_utc: str,
) -> P8LockedLabelDownloadCompletion:
    if not isinstance(transaction, P8LockedLabelDownloadTransaction):
        raise P8LockedLabelDownloadError(
            "p8_locked_label_download_transaction_required"
        )
    values = {
        "transaction_sha256": transaction.transaction_sha256,
        "discovery_seal_sha256": transaction.discovery_seal_sha256,
        "archive_size_bytes": archive_size_bytes,
        "archive_sha256": archive_sha256,
        "secure_download_receipt_sha256": secure_download_receipt_sha256,
        "completed_at_utc": completed_at_utc,
    }
    unsigned = {
        "schema_version": P8_LOCKED_LABEL_DOWNLOAD_COMPLETION_SCHEMA_VERSION,
        "dataset_id": P8_LOCKED_COHORT_DATASET_ID,
        "request_sha256": AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256,
        "transaction_sha256": transaction.transaction_sha256,
        "discovery_seal_sha256": transaction.discovery_seal_sha256,
        "validation_pair_count": 32,
        "requested_label_file_key_count": 32,
        "requested_source_file_key_count": 0,
        "archive_size_bytes": archive_size_bytes,
        "archive_sha256": archive_sha256,
        "secure_download_receipt_sha256": secure_download_receipt_sha256,
        "completed_at_utc": completed_at_utc,
        "archive_mode": "0400",
        "archive_extracted": False,
        "label_archive_opened": False,
        "label_census_executed": False,
        "source_file_keys_requested": False,
        "payload_admitted": False,
        "label_archive_presence_verified": False,
        "source_payload_presence_verified": False,
        "source_media_opened": False,
        "credential_persisted": False,
        "permission_grants_technical_promotion": False,
        "promotion_authorized": False,
        "raw_actor_attempt_sample_ids_serialized": False,
        "aggregate_only": True,
    }
    return P8LockedLabelDownloadCompletion(
        **values,
        completion_sha256=sha256_document(unsigned),
    )


__all__ = [
    "AIHUB538_P8_LABEL_DOWNLOAD_REQUEST_SHA256",
    "AIHUB538_P8_VALIDATION_LABEL_FILE_KEYS",
    "AIHUB538_P8_VALIDATION_PAIRS",
    "AIHUB538_P8_VALIDATION_SOURCE_FILE_KEYS",
    "P8LockedLabelDownloadCompletion",
    "P8LockedLabelDownloadError",
    "P8LockedLabelDownloadTransaction",
    "P8LockedLabelStateRootSeal",
    "issue_p8_locked_label_download_completion",
    "issue_p8_locked_label_download_transaction",
    "issue_p8_locked_label_state_root_seal",
    "p8_locked_reference_commitments_root_sha256",
]
