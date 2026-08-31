"""One-label download authority for the staged P6 remediation ladder."""

from __future__ import annotations

from dataclasses import dataclass

from .aihub538_p6_remediation import (
    AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,
    AIHUB538_P6_REMEDIATION_POLICY_SHA256,
    Aihub538P6RemediationAuditReceipt,
)
from .integrity import require_sha256, sha256_document


AIHUB538_P6_REMEDIATION_LABEL_DOWNLOAD_SCHEMA_VERSION = (
    "voice_coach.aihub538_p6_remediation_label_download_transaction.v1"
)


def _unsigned_transaction(
    *,
    label_role_component: str,
    label_file_key: str,
    prefix_sequence_number: int,
    prior_prefix_receipt_sha256: str | None,
    output_directory_device: int,
    output_directory_inode: int,
) -> dict[str, object]:
    return {
        "schema_version": (
            AIHUB538_P6_REMEDIATION_LABEL_DOWNLOAD_SCHEMA_VERSION
        ),
        "dataset_id": 538,
        "policy_sha256": AIHUB538_P6_REMEDIATION_POLICY_SHA256,
        "label_role_component": label_role_component,
        "label_file_key": label_file_key,
        "prefix_sequence_number": prefix_sequence_number,
        "prior_prefix_receipt_sha256": prior_prefix_receipt_sha256,
        "requested_label_file_key_count": 1,
        "requested_source_file_key_count": 0,
        "output_directory_device": output_directory_device,
        "output_directory_inode": output_directory_inode,
        "dataset_access_permission_confirmed": True,
        "authorization_consumption_count": 1,
        "authorization_reusable": False,
        "same_request_resume_permitted": True,
        "label_download_authorized": True,
        "source_download_authorized": False,
        "locked_partition_accessed": False,
        "promotion_authorized": False,
        "aggregate_only": True,
    }


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationLabelDownloadTransaction:
    """Self-digested authority for one label key and one directory inode."""

    label_role_component: str
    label_file_key: str
    prefix_sequence_number: int
    prior_prefix_receipt_sha256: str | None
    output_directory_device: int
    output_directory_inode: int
    transaction_sha256: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.prefix_sequence_number, bool)
            or not isinstance(self.prefix_sequence_number, int)
            or not 1 <= self.prefix_sequence_number <= len(
                AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS
            )
            or isinstance(self.output_directory_device, bool)
            or not isinstance(self.output_directory_device, int)
            or self.output_directory_device < 0
            or isinstance(self.output_directory_inode, bool)
            or not isinstance(self.output_directory_inode, int)
            or self.output_directory_inode < 1
        ):
            raise ValueError(
                "aihub538_remediation_label_transaction_invalid"
            )
        row = AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[
            self.prefix_sequence_number - 1
        ]
        if (
            self.label_role_component != row["label_role_component"]
            or self.label_file_key != row["label_file_key"]
            or (self.prefix_sequence_number == 1)
            != (self.prior_prefix_receipt_sha256 is None)
        ):
            raise ValueError(
                "aihub538_remediation_label_transaction_invalid"
            )
        if self.prior_prefix_receipt_sha256 is not None:
            require_sha256(
                self.prior_prefix_receipt_sha256,
                "prior_prefix_receipt_sha256",
            )
        require_sha256(self.transaction_sha256, "transaction_sha256")
        if self.transaction_sha256 != sha256_document(
            self.unsigned_json_object()
        ):
            raise ValueError(
                "aihub538_remediation_label_transaction_integrity_failed"
            )

    def unsigned_json_object(self) -> dict[str, object]:
        return _unsigned_transaction(
            label_role_component=self.label_role_component,
            label_file_key=self.label_file_key,
            prefix_sequence_number=self.prefix_sequence_number,
            prior_prefix_receipt_sha256=self.prior_prefix_receipt_sha256,
            output_directory_device=self.output_directory_device,
            output_directory_inode=self.output_directory_inode,
        )

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "transaction_sha256": self.transaction_sha256,
        }

    @classmethod
    def from_json_object(
        cls,
        value: object,
    ) -> "Aihub538P6RemediationLabelDownloadTransaction":
        if not isinstance(value, dict):
            raise ValueError(
                "aihub538_remediation_label_transaction_invalid"
            )
        expected = {
            "schema_version",
            "dataset_id",
            "policy_sha256",
            "label_role_component",
            "label_file_key",
            "prefix_sequence_number",
            "prior_prefix_receipt_sha256",
            "requested_label_file_key_count",
            "requested_source_file_key_count",
            "output_directory_device",
            "output_directory_inode",
            "dataset_access_permission_confirmed",
            "authorization_consumption_count",
            "authorization_reusable",
            "same_request_resume_permitted",
            "label_download_authorized",
            "source_download_authorized",
            "locked_partition_accessed",
            "promotion_authorized",
            "aggregate_only",
            "transaction_sha256",
        }
        if (
            set(value) != expected
            or value["schema_version"]
            != AIHUB538_P6_REMEDIATION_LABEL_DOWNLOAD_SCHEMA_VERSION
            or value["dataset_id"] != 538
            or value["policy_sha256"]
            != AIHUB538_P6_REMEDIATION_POLICY_SHA256
            or type(value["requested_label_file_key_count"]) is not int
            or value["requested_label_file_key_count"] != 1
            or type(value["requested_source_file_key_count"]) is not int
            or value["requested_source_file_key_count"] != 0
            or value["dataset_access_permission_confirmed"] is not True
            or type(value["authorization_consumption_count"]) is not int
            or value["authorization_consumption_count"] != 1
            or value["authorization_reusable"] is not False
            or value["same_request_resume_permitted"] is not True
            or value["label_download_authorized"] is not True
            or value["source_download_authorized"] is not False
            or value["locked_partition_accessed"] is not False
            or value["promotion_authorized"] is not False
            or value["aggregate_only"] is not True
        ):
            raise ValueError(
                "aihub538_remediation_label_transaction_invalid"
            )
        transaction = cls(
            label_role_component=value["label_role_component"],
            label_file_key=value["label_file_key"],
            prefix_sequence_number=value["prefix_sequence_number"],
            prior_prefix_receipt_sha256=value[
                "prior_prefix_receipt_sha256"
            ],
            output_directory_device=value["output_directory_device"],
            output_directory_inode=value["output_directory_inode"],
            transaction_sha256=value["transaction_sha256"],
        )
        if transaction.to_json_object() != value:
            raise ValueError(
                "aihub538_remediation_label_transaction_invalid"
            )
        return transaction


def issue_aihub538_p6_remediation_label_download_transaction(
    *,
    prior_prefix_receipt: Aihub538P6RemediationAuditReceipt | None,
    output_directory_device: int,
    output_directory_inode: int,
    dataset_access_permission_confirmed: bool,
) -> Aihub538P6RemediationLabelDownloadTransaction:
    """Authorize TL177 initially, then only an infeasible receipt successor."""

    if dataset_access_permission_confirmed is not True:
        raise ValueError("aihub538_dataset_access_permission_not_confirmed")
    if prior_prefix_receipt is None:
        sequence = 1
        prior_sha256 = None
    else:
        if not isinstance(
            prior_prefix_receipt,
            Aihub538P6RemediationAuditReceipt,
        ):
            raise ValueError(
                "aihub538_remediation_prior_prefix_receipt_invalid"
            )
        prior_prefix_receipt._validate()
        sequence = len(prior_prefix_receipt.evaluated_prefix_lineage) + 1
        document = prior_prefix_receipt.to_json_object()
        if (
            prior_prefix_receipt.is_feasible
            or sequence > len(AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS)
            or document["next_label_role_component"]
            != AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[sequence - 1][
                "label_role_component"
            ]
            or document["next_label_file_key"]
            != AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[sequence - 1][
                "label_file_key"
            ]
        ):
            raise ValueError(
                "aihub538_remediation_prior_prefix_receipt_invalid"
            )
        prior_sha256 = prior_prefix_receipt.receipt_sha256
    row = AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[sequence - 1]
    values = {
        "label_role_component": row["label_role_component"],
        "label_file_key": row["label_file_key"],
        "prefix_sequence_number": sequence,
        "prior_prefix_receipt_sha256": prior_sha256,
        "output_directory_device": output_directory_device,
        "output_directory_inode": output_directory_inode,
    }
    return Aihub538P6RemediationLabelDownloadTransaction(
        **values,
        transaction_sha256=sha256_document(_unsigned_transaction(**values)),
    )


__all__ = [
    "AIHUB538_P6_REMEDIATION_LABEL_DOWNLOAD_SCHEMA_VERSION",
    "Aihub538P6RemediationLabelDownloadTransaction",
    "issue_aihub538_p6_remediation_label_download_transaction",
]
