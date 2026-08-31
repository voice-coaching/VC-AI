"""Issue one directory-bound P6 remediation label download authority."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from ..adapters.aihub538_p6_remediation_label_download import (
    Aihub538P6RemediationLabelDownloadAdapterError,
    open_aihub538_p6_remediation_label_output_directory,
    publish_aihub538_p6_remediation_label_transaction,
    require_aihub538_p6_remediation_label_transaction_output_binding,
)
from ..adapters.aihub538_p6_remediation_publish import (
    Aihub538P6RemediationPublishError,
    load_aihub538_p6_remediation_receipt_descriptor,
)
from ..models.aihub538_p6_remediation_label_download import (
    Aihub538P6RemediationLabelDownloadTransaction,
    issue_aihub538_p6_remediation_label_download_transaction,
)


class Aihub538P6RemediationLabelDownloadError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationLabelDownloadRequest:
    download_destination_directory: Path
    transaction_output_directory: Path
    prior_prefix_receipt_descriptor: int | None
    dataset_access_permission_confirmed: bool

    def validate(self) -> None:
        if (
            not isinstance(self.download_destination_directory, Path)
            or not self.download_destination_directory.is_absolute()
            or not isinstance(self.transaction_output_directory, Path)
            or not self.transaction_output_directory.is_absolute()
            or self.download_destination_directory
            == self.transaction_output_directory
            or (
                self.prior_prefix_receipt_descriptor is not None
                and (
                    isinstance(self.prior_prefix_receipt_descriptor, bool)
                    or not isinstance(
                        self.prior_prefix_receipt_descriptor,
                        int,
                    )
                    or self.prior_prefix_receipt_descriptor < 3
                )
            )
        ):
            raise Aihub538P6RemediationLabelDownloadError(
                "aihub538_remediation_label_download_request_invalid"
            )


class Aihub538P6RemediationLabelDownloadService:
    def issue(
        self,
        request: Aihub538P6RemediationLabelDownloadRequest,
    ) -> tuple[Aihub538P6RemediationLabelDownloadTransaction, Path]:
        if request.dataset_access_permission_confirmed is not True:
            raise Aihub538P6RemediationLabelDownloadError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        request.validate()
        directory_descriptor: int | None = None
        try:
            prior = (
                load_aihub538_p6_remediation_receipt_descriptor(
                    request.prior_prefix_receipt_descriptor
                )
                if request.prior_prefix_receipt_descriptor is not None
                else None
            )
            directory_descriptor, identity = (
                open_aihub538_p6_remediation_label_output_directory(
                    request.download_destination_directory
                )
            )
            if os.listdir(directory_descriptor):
                raise Aihub538P6RemediationLabelDownloadError(
                    "aihub538_remediation_label_destination_not_empty"
                )
            transaction = (
                issue_aihub538_p6_remediation_label_download_transaction(
                    prior_prefix_receipt=prior,
                    output_directory_device=identity.device,
                    output_directory_inode=identity.inode,
                    dataset_access_permission_confirmed=True,
                )
            )
            path = publish_aihub538_p6_remediation_label_transaction(
                transaction=transaction,
                output_directory=request.transaction_output_directory,
            )
            require_aihub538_p6_remediation_label_transaction_output_binding(
                transaction,
                request.download_destination_directory,
            )
            return transaction, path
        except Aihub538P6RemediationLabelDownloadError:
            raise
        except (
            Aihub538P6RemediationLabelDownloadAdapterError,
            Aihub538P6RemediationPublishError,
        ) as error:
            raise Aihub538P6RemediationLabelDownloadError(
                error.reason_code
            ) from None
        except (OSError, TypeError, ValueError) as error:
            reason = str(error)
            if not reason.startswith("aihub538_"):
                reason = "aihub538_remediation_label_download_issue_failed"
            raise Aihub538P6RemediationLabelDownloadError(reason) from None
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)


__all__ = [
    "Aihub538P6RemediationLabelDownloadError",
    "Aihub538P6RemediationLabelDownloadRequest",
    "Aihub538P6RemediationLabelDownloadService",
]
