"""Application service for the fresh P6 remediation label ladder."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..adapters.aihub538_identity_key_store import (
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key_descriptor,
)
from ..adapters.aihub538_label_capacity import (
    Aihub538LabelCapacityAdapter,
    Aihub538LabelCapacityError,
)
from ..adapters.aihub538_p6_remediation_publish import (
    Aihub538P6RemediationPublishError,
    load_aihub538_p6_remediation_receipt_descriptor,
    publish_aihub538_p6_remediation_receipt,
)
from ..models.aihub538_label_capacity import Aihub538LabelCapacityAggregate
from ..models.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationFailureArtifactV2,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
    Aihub538NonlockedShardSpec,
)
from ..models.aihub538_p6_remediation import (
    Aihub538P6RemediationAuditReceipt,
    require_aihub538_p6_actual_v3_design_label_lineage,
    require_aihub538_p6_actual_v3_failure_artifact,
)


_DESIGN_USED_SPECS = (
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
)
_REMEDIATION_AUDIT_SPECS = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:]


class Aihub538P6RemediationError(RuntimeError):
    """Stable aggregate-only application failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class Aihub538P6RemediationLabelPort(Protocol):
    """Narrow label-only archive boundary used by the service."""

    def audit(
        self,
        *,
        label_zip_descriptor: int,
        identity_hash_key: bytes,
    ) -> Aihub538LabelCapacityAggregate:
        """Return private HMAC rows and aggregate commitments only."""


@dataclass(frozen=True, slots=True)
class Aihub538P6RemediationAuditRequest:
    """Descriptor-only request; role order is fixed by the frozen policy."""

    design_used_label_zip_descriptors: tuple[int, int, int]
    prior_candidate_label_zip_descriptors: tuple[int, ...]
    new_candidate_label_zip_descriptor: int
    prior_prefix_receipt_descriptor: int | None
    identity_key_descriptor: int
    output_directory: Path
    design_failure_artifact: Aihub538DecisionCalibrationFailureArtifactV2
    dataset_access_permission_confirmed: bool

    def validate(self) -> None:
        if (
            not isinstance(self.design_used_label_zip_descriptors, tuple)
            or not isinstance(
                self.prior_candidate_label_zip_descriptors,
                tuple,
            )
            or not isinstance(self.output_directory, Path)
        ):
            raise Aihub538P6RemediationError(
                "aihub538_remediation_descriptor_contract_invalid"
            )
        descriptors = (
            *self.design_used_label_zip_descriptors,
            *self.prior_candidate_label_zip_descriptors,
            self.new_candidate_label_zip_descriptor,
            self.identity_key_descriptor,
            *(
                (self.prior_prefix_receipt_descriptor,)
                if self.prior_prefix_receipt_descriptor is not None
                else ()
            ),
        )
        prior_count = len(self.prior_candidate_label_zip_descriptors)
        if (
            len(self.design_used_label_zip_descriptors) != 3
            or not 0 <= prior_count <= 3
            or (prior_count == 0)
            != (self.prior_prefix_receipt_descriptor is None)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 3
                for value in descriptors
            )
            or len(set(descriptors)) != len(descriptors)
        ):
            raise Aihub538P6RemediationError(
                "aihub538_remediation_descriptor_contract_invalid"
            )
        if not self.output_directory.is_absolute():
            raise Aihub538P6RemediationError(
                "aihub538_remediation_output_directory_invalid"
            )
        if type(self.design_failure_artifact) is not (
            Aihub538DecisionCalibrationFailureArtifactV2
        ):
            raise Aihub538P6RemediationError(
                "aihub538_remediation_design_evidence_invalid"
            )


class Aihub538P6RemediationAuditService:
    """Audit exactly one newly authorized label and its immutable prefix."""

    def __init__(
        self,
        *,
        adapter_factory: Callable[
            [Aihub538NonlockedShardSpec],
            Aihub538P6RemediationLabelPort,
        ] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory or (
            lambda spec: Aihub538LabelCapacityAdapter(shard_spec=spec)
        )

    def audit_first_feasible_prefix(
        self,
        request: Aihub538P6RemediationAuditRequest,
    ) -> Aihub538P6RemediationAuditReceipt:
        """Audit exact contiguous labels and persist one fail-closed receipt."""

        if request.dataset_access_permission_confirmed is not True:
            raise Aihub538P6RemediationError(
                "aihub538_dataset_access_permission_not_confirmed"
            )
        request.validate()
        try:
            design_failure_sha256 = (
                require_aihub538_p6_actual_v3_failure_artifact(
                    request.design_failure_artifact
                )
            )
            prior_receipt = self._load_prior_stage_receipt(
                request,
                design_failure_sha256=design_failure_sha256,
            )
            identity = load_aihub538_identity_key_descriptor(
                request.identity_key_descriptor
            )
            design_used = tuple(
                self._adapter_factory(spec).audit(
                    label_zip_descriptor=descriptor,
                    identity_hash_key=identity.key,
                )
                for spec, descriptor in zip(
                    _DESIGN_USED_SPECS,
                    request.design_used_label_zip_descriptors,
                    strict=True,
                )
            )
            require_aihub538_p6_actual_v3_design_label_lineage(design_used)
            prior_candidates = tuple(
                self._adapter_factory(spec).audit(
                    label_zip_descriptor=descriptor,
                    identity_hash_key=identity.key,
                )
                for spec, descriptor in zip(
                    _REMEDIATION_AUDIT_SPECS,
                    request.prior_candidate_label_zip_descriptors,
                    strict=False,
                )
            )
            next_spec = _REMEDIATION_AUDIT_SPECS[len(prior_candidates)]
            new_candidate = self._adapter_factory(next_spec).audit(
                label_zip_descriptor=(
                    request.new_candidate_label_zip_descriptor
                ),
                identity_hash_key=identity.key,
            )
            candidates = (*prior_candidates, new_candidate)
            receipt = Aihub538P6RemediationAuditReceipt.issue(
                design_used_aggregates=design_used,
                evaluated_candidate_aggregates=candidates,
                design_failure_artifact=request.design_failure_artifact,
                prior_prefix_receipt=prior_receipt,
            )
            publish_aihub538_p6_remediation_receipt(
                receipt=receipt,
                output_directory=request.output_directory,
            )
            return receipt
        except Aihub538P6RemediationError:
            raise
        except (
            Aihub538IdentityKeyStoreError,
            Aihub538LabelCapacityError,
            Aihub538P6RemediationPublishError,
        ) as error:
            raise Aihub538P6RemediationError(error.reason_code) from None
        except (OSError, TypeError, ValueError) as error:
            reason_code = str(error)
            if not reason_code.startswith("aihub538_"):
                reason_code = "aihub538_remediation_audit_failed"
            raise Aihub538P6RemediationError(reason_code) from None

    @staticmethod
    def _load_prior_stage_receipt(
        request: Aihub538P6RemediationAuditRequest,
        *,
        design_failure_sha256: str,
    ) -> Aihub538P6RemediationAuditReceipt | None:
        """Authorize only the one successor named by an infeasible receipt."""

        prior_count = len(request.prior_candidate_label_zip_descriptors)
        if prior_count == 0:
            return None
        assert request.prior_prefix_receipt_descriptor is not None
        receipt = load_aihub538_p6_remediation_receipt_descriptor(
            request.prior_prefix_receipt_descriptor
        )
        document = receipt.to_json_object()
        expected_next = _REMEDIATION_AUDIT_SPECS[prior_count]
        if (
            receipt.is_feasible
            or len(receipt.evaluated_prefix_lineage) != prior_count
            or receipt.design_failure_artifact_receipt_sha256
            != design_failure_sha256
            or document["next_label_role_component"]
            != expected_next.label_role_component
            or document["next_label_file_key"]
            != expected_next.ordered_file_keys[0]
        ):
            raise Aihub538P6RemediationError(
                "aihub538_remediation_prior_prefix_receipt_invalid"
            )
        return receipt


__all__ = [
    "Aihub538P6RemediationAuditRequest",
    "Aihub538P6RemediationAuditService",
    "Aihub538P6RemediationError",
    "Aihub538P6RemediationLabelPort",
]
