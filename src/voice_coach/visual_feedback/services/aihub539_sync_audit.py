"""Application service for non-promotional AI Hub 539 label evidence."""

from __future__ import annotations

from pathlib import Path

from ..adapters.aihub539_sync_dataset import (
    Aihub539SyncAuditError,
    Aihub539SyncDatasetAdapter,
)
from ..models.aihub539_sync_evidence import Aihub539SyncLabelAuditReceipt


class Aihub539SyncLabelAuditService:
    """Require portal access confirmation, then issue aggregate schema evidence."""

    def __init__(self, dataset_adapter: Aihub539SyncDatasetAdapter) -> None:
        self._dataset_adapter = dataset_adapter

    def audit(
        self,
        *,
        labels_root: Path,
        dataset_access_permission_confirmed: bool,
    ) -> Aihub539SyncLabelAuditReceipt:
        """Audit labels without interpreting portal permission as promotion."""

        if dataset_access_permission_confirmed is not True:
            raise Aihub539SyncAuditError(
                "aihub539_dataset_access_permission_not_confirmed"
            )
        aggregate = self._dataset_adapter.admit(labels_root=labels_root)
        return Aihub539SyncLabelAuditReceipt.issue(aggregate)


__all__ = ["Aihub539SyncLabelAuditService"]
