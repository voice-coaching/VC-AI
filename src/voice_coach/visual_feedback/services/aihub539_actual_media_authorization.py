"""Issue the exact non-locked AI Hub 539 training acquisition authority."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from ..adapters.aihub539_actual_media_download import (
    Aihub539ActualMediaDownloadAdapterError,
    open_aihub539_actual_media_output_directory,
    publish_aihub539_actual_media_authorization,
    require_aihub539_actual_media_authorization_output_binding,
)
from ..models.aihub539_actual_media_authorization import (
    Aihub539ActualMediaAuthorizationError,
    Aihub539ActualMediaAuthorizationReceipt,
    issue_aihub539_actual_media_authorization,
    require_aihub539_actual_media_authorization_scope,
)


class Aihub539ActualMediaAuthorizationServiceError(RuntimeError):
    """Stable application-layer failure without restricted source details."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub539ActualMediaAuthorizationRequest:
    dataset_id: int
    partition: str
    ordered_file_keys: tuple[str, ...]
    download_destination_directory: Path
    authorization_output_directory: Path
    dataset_access_permission_confirmed: bool
    locked_partition_requested: bool = False
    validation_requested: bool = False

    def validate(self) -> None:
        if (
            isinstance(self.dataset_id, bool)
            or not isinstance(self.dataset_id, int)
            or not isinstance(self.partition, str)
            or not isinstance(self.ordered_file_keys, tuple)
            or not all(
                isinstance(file_key, str)
                for file_key in self.ordered_file_keys
            )
            or not isinstance(self.download_destination_directory, Path)
            or not self.download_destination_directory.is_absolute()
            or not isinstance(self.authorization_output_directory, Path)
            or not self.authorization_output_directory.is_absolute()
            or self.download_destination_directory
            == self.authorization_output_directory
            or not isinstance(
                self.dataset_access_permission_confirmed,
                bool,
            )
            or not isinstance(self.locked_partition_requested, bool)
            or not isinstance(self.validation_requested, bool)
        ):
            raise Aihub539ActualMediaAuthorizationServiceError(
                "aihub539_actual_media_authorization_request_invalid"
            )


class Aihub539ActualMediaAuthorizationService:
    """Bind exact public file pins to one empty owner-only destination."""

    def issue(
        self,
        request: Aihub539ActualMediaAuthorizationRequest,
    ) -> tuple[Aihub539ActualMediaAuthorizationReceipt, Path]:
        if not isinstance(
            request,
            Aihub539ActualMediaAuthorizationRequest,
        ):
            raise Aihub539ActualMediaAuthorizationServiceError(
                "aihub539_actual_media_authorization_request_invalid"
            )
        if request.dataset_access_permission_confirmed is not True:
            raise Aihub539ActualMediaAuthorizationServiceError(
                "aihub539_dataset_access_permission_not_confirmed"
            )
        request.validate()
        try:
            require_aihub539_actual_media_authorization_scope(
                dataset_id=request.dataset_id,
                partition=request.partition,
                ordered_file_keys=request.ordered_file_keys,
                dataset_access_permission_confirmed=True,
                locked_partition_requested=(
                    request.locked_partition_requested
                ),
                validation_requested=request.validation_requested,
            )
        except Aihub539ActualMediaAuthorizationError as error:
            raise Aihub539ActualMediaAuthorizationServiceError(
                error.reason_code
            ) from None
        destination_descriptor: int | None = None
        try:
            destination_descriptor, identity = (
                open_aihub539_actual_media_output_directory(
                    request.download_destination_directory
                )
            )
            if os.listdir(destination_descriptor):
                raise Aihub539ActualMediaAuthorizationServiceError(
                    "aihub539_actual_media_destination_not_empty"
                )
            authorization = issue_aihub539_actual_media_authorization(
                dataset_id=request.dataset_id,
                partition=request.partition,
                ordered_file_keys=request.ordered_file_keys,
                output_directory_device=identity.device,
                output_directory_inode=identity.inode,
                dataset_access_permission_confirmed=(
                    request.dataset_access_permission_confirmed
                ),
                locked_partition_requested=(
                    request.locked_partition_requested
                ),
                validation_requested=request.validation_requested,
            )
            authorization_path = (
                publish_aihub539_actual_media_authorization(
                    authorization=authorization,
                    output_directory=(
                        request.authorization_output_directory
                    ),
                )
            )
            require_aihub539_actual_media_authorization_output_binding(
                authorization,
                request.download_destination_directory,
            )
            return authorization, authorization_path
        except Aihub539ActualMediaAuthorizationServiceError:
            raise
        except Aihub539ActualMediaAuthorizationError as error:
            raise Aihub539ActualMediaAuthorizationServiceError(
                error.reason_code
            ) from None
        except Aihub539ActualMediaDownloadAdapterError as error:
            raise Aihub539ActualMediaAuthorizationServiceError(
                error.reason_code
            ) from None
        except (OSError, TypeError, ValueError):
            raise Aihub539ActualMediaAuthorizationServiceError(
                "aihub539_actual_media_authorization_issue_failed"
            ) from None
        finally:
            if destination_descriptor is not None:
                os.close(destination_descriptor)


__all__ = [
    "Aihub539ActualMediaAuthorizationRequest",
    "Aihub539ActualMediaAuthorizationService",
    "Aihub539ActualMediaAuthorizationServiceError",
]
