"""Pure authorization contract for AI Hub 539 non-locked training media.

This contract grants one narrowly scoped acquisition request.  It does not
admit or open archive payloads and it grants no sync validation, locked-test,
P8, runtime, or promotion capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from .integrity import sha256_document


AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_SCHEMA_VERSION = (
    "voice_coach.aihub539_actual_media_acquisition_authorization.v1"
)
AIHUB539_ACTUAL_MEDIA_DATASET_ID = 539
AIHUB539_ACTUAL_MEDIA_PARTITION = "Training"
AIHUB539_ACTUAL_MEDIA_ORDERED_ARCHIVES = (
    ("TL8.zip", "54575", "training_label_metadata"),
    ("TS8.zip", "54620", "training_actual_media"),
)
AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS = tuple(
    file_key
    for _archive_name, file_key, _role in (
        AIHUB539_ACTUAL_MEDIA_ORDERED_ARCHIVES
    )
)


class Aihub539ActualMediaAuthorizationError(ValueError):
    """Stable, privacy-safe failure at the pure authorization boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _ordered_archives_json() -> list[dict[str, str]]:
    return [
        {
            "archive_name": archive_name,
            "file_key": file_key,
            "role": role,
        }
        for archive_name, file_key, role in (
            AIHUB539_ACTUAL_MEDIA_ORDERED_ARCHIVES
        )
    ]


AIHUB539_ACTUAL_MEDIA_ACQUISITION_POLICY = {
    "schema_version": (
        "voice_coach.aihub539_actual_media_acquisition_policy.v1"
    ),
    "dataset_id": AIHUB539_ACTUAL_MEDIA_DATASET_ID,
    "partition": AIHUB539_ACTUAL_MEDIA_PARTITION,
    "ordered_archives": _ordered_archives_json(),
    "ordered_file_keys": list(AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS),
    "requested_label_archive_count": 1,
    "requested_actual_media_archive_count": 1,
    "locked_partition_authorized": False,
    "actual_media_sync_validation_authorized": False,
    "p8_authorized": False,
    "promotion_authorized": False,
}
AIHUB539_ACTUAL_MEDIA_ACQUISITION_POLICY_SHA256 = sha256_document(
    AIHUB539_ACTUAL_MEDIA_ACQUISITION_POLICY
)


def _unsigned_authorization(
    *,
    output_directory_device: int,
    output_directory_inode: int,
) -> dict[str, object]:
    return {
        "schema_version": (
            AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_SCHEMA_VERSION
        ),
        "status": "authorized_nonlocked_training_acquisition",
        "dataset_id": AIHUB539_ACTUAL_MEDIA_DATASET_ID,
        "partition": AIHUB539_ACTUAL_MEDIA_PARTITION,
        "policy_sha256": (
            AIHUB539_ACTUAL_MEDIA_ACQUISITION_POLICY_SHA256
        ),
        "ordered_archives": _ordered_archives_json(),
        "ordered_file_keys": list(
            AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS
        ),
        "requested_label_archive_count": 1,
        "requested_actual_media_archive_count": 1,
        "output_directory_device": output_directory_device,
        "output_directory_inode": output_directory_inode,
        "dataset_access_permission_confirmed": True,
        "exact_request_resume_permitted": True,
        "archive_download_authorized": True,
        "archive_admission_status": "not_started",
        "archive_admission_authorized": False,
        "archive_open_authorized": False,
        "actual_media_decode_authorized": False,
        "actual_media_sync_validation_authorized": False,
        "actual_media_sync_validated": False,
        "locked_partition_accessed": False,
        "locked_partition_authorized": False,
        "validation_partition_authorized": False,
        "p8_authorized": False,
        "promotion_authorized": False,
        "restricted_payload_metadata_serialized": False,
    }


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaAuthorizationReceipt:
    """Self-digested, directory-bound authority for the exact TS8 request."""

    output_directory_device: int
    output_directory_inode: int
    authorization_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.output_directory_device, bool)
            or not isinstance(self.output_directory_device, int)
            or self.output_directory_device < 0
            or isinstance(self.output_directory_inode, bool)
            or not isinstance(self.output_directory_inode, int)
            or self.output_directory_inode < 1
            or not isinstance(self.authorization_sha256, str)
            or len(self.authorization_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.authorization_sha256
            )
        ):
            raise Aihub539ActualMediaAuthorizationError(
                "aihub539_actual_media_authorization_invalid"
            )
        if self.authorization_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise Aihub539ActualMediaAuthorizationError(
                "aihub539_actual_media_authorization_integrity_failed"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return _unsigned_authorization(
            output_directory_device=self.output_directory_device,
            output_directory_inode=self.output_directory_inode,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "authorization_sha256": self.authorization_sha256,
        }

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub539ActualMediaAuthorizationReceipt":
        if not isinstance(value, dict):
            raise Aihub539ActualMediaAuthorizationError(
                "aihub539_actual_media_authorization_invalid"
            )
        expected_keys = set(
            _unsigned_authorization(
                output_directory_device=0,
                output_directory_inode=1,
            )
        ) | {"authorization_sha256"}
        if set(value) != expected_keys:
            raise Aihub539ActualMediaAuthorizationError(
                "aihub539_actual_media_authorization_invalid"
            )
        receipt = cls(
            output_directory_device=value["output_directory_device"],
            output_directory_inode=value["output_directory_inode"],
            authorization_sha256=value["authorization_sha256"],
        )
        if receipt.to_json_object() != value:
            raise Aihub539ActualMediaAuthorizationError(
                "aihub539_actual_media_authorization_invalid"
            )
        return receipt


def issue_aihub539_actual_media_authorization(
    *,
    dataset_id: int,
    partition: str,
    ordered_file_keys: tuple[str, ...],
    output_directory_device: int,
    output_directory_inode: int,
    dataset_access_permission_confirmed: bool,
    locked_partition_requested: bool,
    validation_requested: bool,
) -> Aihub539ActualMediaAuthorizationReceipt:
    """Issue only the exact non-locked Training TL8/TS8 acquisition."""

    require_aihub539_actual_media_authorization_scope(
        dataset_id=dataset_id,
        partition=partition,
        ordered_file_keys=ordered_file_keys,
        dataset_access_permission_confirmed=(
            dataset_access_permission_confirmed
        ),
        locked_partition_requested=locked_partition_requested,
        validation_requested=validation_requested,
    )
    unsigned = _unsigned_authorization(
        output_directory_device=output_directory_device,
        output_directory_inode=output_directory_inode,
    )
    return Aihub539ActualMediaAuthorizationReceipt(
        output_directory_device=output_directory_device,
        output_directory_inode=output_directory_inode,
        authorization_sha256=sha256_document(unsigned),
    )


def require_aihub539_actual_media_authorization_scope(
    *,
    dataset_id: int,
    partition: str,
    ordered_file_keys: tuple[str, ...],
    dataset_access_permission_confirmed: bool,
    locked_partition_requested: bool,
    validation_requested: bool,
) -> None:
    """Validate authorization scope without accessing filesystem or network."""

    if dataset_access_permission_confirmed is not True:
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_dataset_access_permission_not_confirmed"
        )
    if locked_partition_requested is not False:
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_locked_partition_request_forbidden"
        )
    if validation_requested is not False:
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_validation_request_forbidden"
        )
    if dataset_id != AIHUB539_ACTUAL_MEDIA_DATASET_ID:
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_actual_media_dataset_mismatch"
        )
    if partition != AIHUB539_ACTUAL_MEDIA_PARTITION:
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_actual_media_partition_not_authorized"
        )
    if (
        not isinstance(ordered_file_keys, tuple)
        or not all(isinstance(item, str) for item in ordered_file_keys)
    ):
        raise Aihub539ActualMediaAuthorizationError(
            "aihub539_actual_media_file_keys_invalid"
        )
    if ordered_file_keys != AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS:
        if (
            len(ordered_file_keys)
            == len(AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS)
            and set(ordered_file_keys)
            == set(AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS)
        ):
            reason_code = "aihub539_actual_media_file_key_order_invalid"
        else:
            reason_code = "aihub539_actual_media_file_keys_invalid"
        raise Aihub539ActualMediaAuthorizationError(reason_code)


__all__ = [
    "AIHUB539_ACTUAL_MEDIA_ACQUISITION_POLICY_SHA256",
    "AIHUB539_ACTUAL_MEDIA_AUTHORIZATION_SCHEMA_VERSION",
    "AIHUB539_ACTUAL_MEDIA_DATASET_ID",
    "AIHUB539_ACTUAL_MEDIA_ORDERED_ARCHIVES",
    "AIHUB539_ACTUAL_MEDIA_ORDERED_FILE_KEYS",
    "AIHUB539_ACTUAL_MEDIA_PARTITION",
    "Aihub539ActualMediaAuthorizationError",
    "Aihub539ActualMediaAuthorizationReceipt",
    "issue_aihub539_actual_media_authorization",
    "require_aihub539_actual_media_authorization_scope",
]
