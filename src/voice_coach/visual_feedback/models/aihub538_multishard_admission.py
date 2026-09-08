"""Aggregate admission lineage for the frozen TL174+TL175+TL176 cohort."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from weakref import WeakKeyDictionary, ref

from ..aihub538_capability_lifetime import (
    AIHUB538_CAPABILITY_LIFETIME_LOCK,
)
from .aihub538_nonlocked_shards import (
    AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS,
    AIHUB538_TS174_TL174_SHARD,
    AIHUB538_TS175_TL175_SHARD,
    AIHUB538_TS176_TL176_SHARD,
)
from .aihub538_label_selection_authorization import (
    Aihub538LabelSelectionAuthorizationReceipt,
)
from .aihub538_decision_calibration import (
    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL,
)
from .aihub538_training_evidence import (
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
)
from .integrity import require_sha256, sha256_document


AIHUB538_MULTI_SAMPLE_SELECTION_RULE = (
    "all_exactly_admitted_ts174_tl174_ts175_tl175_ts176_tl176_pairs"
)
AIHUB538_MULTI_ADMISSION_POLICY_SHA256 = sha256_document(
    {
        "schema_version": "voice_coach.aihub538_multishard_admission_policy.v2",
        "dataset_id": 538,
        "shard_spec_sha256": [
            AIHUB538_TS174_TL174_SHARD.spec_sha256,
            AIHUB538_TS175_TL175_SHARD.spec_sha256,
            AIHUB538_TS176_TL176_SHARD.spec_sha256,
        ],
        "source_partitions": list(AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS),
        "sample_selection_rule": AIHUB538_MULTI_SAMPLE_SELECTION_RULE,
        "cross_shard_relative_path_collision_allowed": False,
        "cross_shard_actor_or_attempt_collision_allowed": False,
        "archive_content_root_collision_allowed": False,
        "locked_partition_accessed": False,
        "capacity_prefix_receipt_required": True,
        "pair_pipeline_completion_binding_required": True,
        "frozen_calibration_protocol_sha256": (
            MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
        ),
    }
)


class _AuthorizedShardCapabilityToken:
    __slots__ = ("__weakref__",)


_AUTHORIZED_SHARD_CAPABILITIES: WeakKeyDictionary[
    _AuthorizedShardCapabilityToken,
    tuple[object, ...],
] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Aihub538AuthorizedShardAdmission:
    """One replayed admission bound to an exact transactional completion."""

    aggregate: Aihub538TrainingAdmissionAggregate
    admission_receipt: Aihub538TrainingAdmissionReceipt
    pipeline_completion_receipt_sha256: str
    capacity_audit_receipt_sha256: str | None
    _capability: _AuthorizedShardCapabilityToken = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self._capability_matches():
            raise ValueError("aihub538_authorized_shard_capability_invalid")
        require_sha256(
            self.pipeline_completion_receipt_sha256,
            "pipeline_completion_receipt_sha256",
        )
        if self.capacity_audit_receipt_sha256 is not None:
            require_sha256(
                self.capacity_audit_receipt_sha256,
                "capacity_audit_receipt_sha256",
            )
        if (
            self.aggregate.shard_id != self.admission_receipt.shard_id
            or Aihub538TrainingAdmissionReceipt.issue(self.aggregate)
            != self.admission_receipt
        ):
            raise ValueError("aihub538_authorized_shard_admission_mismatch")
        if self.aggregate.shard_id == AIHUB538_TS174_TL174_SHARD.shard_id:
            if self.capacity_audit_receipt_sha256 is not None:
                raise ValueError("aihub538_authorized_shard_capacity_invalid")
        elif self.aggregate.shard_id in {
            AIHUB538_TS175_TL175_SHARD.shard_id,
            AIHUB538_TS176_TL176_SHARD.shard_id,
        }:
            if self.capacity_audit_receipt_sha256 is None:
                raise ValueError("aihub538_authorized_shard_capacity_invalid")
        else:
            raise ValueError("aihub538_authorized_shard_role_invalid")

    @classmethod
    def _issue_live(
        cls,
        *,
        owner_binding: object,
        prevalidated_admission: object,
    ) -> "Aihub538AuthorizedShardAdmission":
        # Runtime import avoids a model/adapter import cycle.  The adapter
        # consumes a live one-shot run capability and returns exact objects;
        # this model never accepts caller-supplied lineage fields for issuance.
        from ..adapters.aihub538_training_run_binding import (
            _aihub538_authorized_shard_issuance_transaction,
        )

        with _aihub538_authorized_shard_issuance_transaction(
            owner_binding=owner_binding,
            prevalidated_admission=prevalidated_admission,
        ) as payload:
            (
                aggregate,
                admission_receipt,
                pipeline_completion_receipt_sha256,
                capacity_audit_receipt_sha256,
                owner_context,
                exact_owner_binding,
            ) = payload
            capability = _AuthorizedShardCapabilityToken()
            record = (
                os.getpid(),
                owner_context,
                aggregate,
                admission_receipt,
                pipeline_completion_receipt_sha256,
                capacity_audit_receipt_sha256,
                None,
                ref(exact_owner_binding),
            )
            _AUTHORIZED_SHARD_CAPABILITIES[capability] = record
            try:
                authorization = cls(
                    aggregate=aggregate,
                    admission_receipt=admission_receipt,
                    pipeline_completion_receipt_sha256=(
                        pipeline_completion_receipt_sha256
                    ),
                    capacity_audit_receipt_sha256=(
                        capacity_audit_receipt_sha256
                    ),
                    _capability=capability,
                )
                _AUTHORIZED_SHARD_CAPABILITIES[capability] = (
                    *record[:6],
                    ref(authorization),
                    record[7],
                )
            except BaseException:
                _AUTHORIZED_SHARD_CAPABILITIES.pop(capability, None)
                raise
        return authorization

    def _capability_matches(self, *, allow_consuming: bool = True) -> bool:
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            try:
                record = _AUTHORIZED_SHARD_CAPABILITIES.get(self._capability)
            except (AttributeError, TypeError):
                return False
            if (
                record is None
                or record[0] != os.getpid()
                or record[2] is not self.aggregate
                or record[3] is not self.admission_receipt
                or record[4] != self.pipeline_completion_receipt_sha256
                or record[5] != self.capacity_audit_receipt_sha256
                or record[6] is not None
                and record[6]() is not self
                or record[7]() is None
            ):
                return False
            from ..adapters.aihub538_training_run_binding import (
                _assert_aihub538_authorization_owner_live,
            )

            try:
                _assert_aihub538_authorization_owner_live(
                    owner_binding=record[7](),
                    owner_context=record[1],
                    allow_consuming=allow_consuming,
                )
            except Exception:
                return False
            return True

    def _assert_live(self) -> None:
        if not self._capability_matches(allow_consuming=False):
            raise ValueError("aihub538_authorized_shard_capability_invalid")
        self.__post_init__()

    @classmethod
    def _revoke_owner_context(cls, owner_context: object) -> None:
        del cls
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            for capability, record in tuple(
                _AUTHORIZED_SHARD_CAPABILITIES.items()
            ):
                if record[1] is owner_context:
                    _AUTHORIZED_SHARD_CAPABILITIES.pop(capability, None)


@dataclass(frozen=True, slots=True)
class Aihub538MultiShardLineage:
    """Safe per-shard whole-archive and admission commitments."""

    shard_id: str
    admission_receipt_sha256: str
    admission_manifest_sha256: str
    label_archive_sha256: str
    source_archive_sha256: str
    label_content_root_sha256: str
    source_content_root_sha256: str
    paired_sample_count: int
    pipeline_completion_receipt_sha256: str
    capacity_audit_receipt_sha256: str | None

    def to_json_object(self) -> dict[str, object]:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class Aihub538MultiShardAdmissionReceipt:
    """One aggregate identity consumed by multi-shard calibration."""

    shard_lineage: tuple[Aihub538MultiShardLineage, ...]
    sample_selection_sha256: str
    admission_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    member_namespace_commitment_sha256: str
    paired_sample_count: int
    actor_count: int
    attempt_count: int
    cross_shard_path_collision_count: int
    cross_shard_actor_intersection_count: int
    cross_shard_attempt_intersection_count: int
    archive_content_root_collision_count: int
    identity_union_root_sha256: str
    member_union_root_sha256: str
    capacity_audit_receipt_sha256: str
    capacity_actor_assignment_root_sha256: str
    capacity_metadata_excluded_label_count: int
    capacity_metadata_exclusion_root_sha256: str
    capacity_development_label_selection_root_sha256: str
    capacity_calibration_label_selection_root_sha256: str
    frozen_calibration_protocol_sha256: str
    receipt_sha256: str
    admission_policy_sha256: str = AIHUB538_MULTI_ADMISSION_POLICY_SHA256
    sample_selection_rule: str = AIHUB538_MULTI_SAMPLE_SELECTION_RULE
    source_partitions: tuple[str, ...] = (
        AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
    )

    @classmethod
    def issue(
        cls,
        *,
        ts174: Aihub538AuthorizedShardAdmission,
        ts175: Aihub538AuthorizedShardAdmission,
        ts176: Aihub538AuthorizedShardAdmission,
        capacity_receipt: Aihub538LabelSelectionAuthorizationReceipt,
    ) -> "Aihub538MultiShardAdmissionReceipt":
        capacity_receipt._validate()
        if not capacity_receipt.is_feasible:
            raise ValueError("aihub538_multishard_capacity_infeasible")
        authorizations = (ts174, ts175, ts176)
        for authorization in authorizations:
            authorization._assert_live()
        aggregates = tuple(item.aggregate for item in authorizations)
        specs = (
            AIHUB538_TS174_TL174_SHARD,
            AIHUB538_TS175_TL175_SHARD,
            AIHUB538_TS176_TL176_SHARD,
        )
        if any(
            aggregate.shard_id != spec.shard_id
            or aggregate.restricted_member_set is None
            for aggregate, spec in zip(aggregates, specs, strict=True)
        ):
            raise ValueError("aihub538_multishard_admission_role_invalid")
        if (
            ts174.capacity_audit_receipt_sha256 is not None
            or ts175.capacity_audit_receipt_sha256
            != capacity_receipt.receipt_sha256
            or ts176.capacity_audit_receipt_sha256
            != capacity_receipt.receipt_sha256
            or capacity_receipt.baseline_label_archive_sha256
            != ts174.admission_receipt.label_archive_sha256
            or capacity_receipt.baseline_label_content_root_sha256
            != ts174.admission_receipt.label_zip_content_root_sha256
            or capacity_receipt.baseline_admission_receipt_sha256
            != ts174.admission_receipt.receipt_sha256
            or capacity_receipt.baseline_admission_manifest_sha256
            != ts174.admission_receipt.admission_manifest_sha256
            or capacity_receipt.identity_namespace_commitment_sha256
            != ts174.admission_receipt.identity_namespace_commitment_sha256
            or len(capacity_receipt.prefix_lineage) != 2
        ):
            raise ValueError("aihub538_multishard_capacity_lineage_invalid")
        for authorization, lineage in zip(
            (ts175, ts176),
            capacity_receipt.prefix_lineage,
            strict=True,
        ):
            receipt = authorization.admission_receipt
            if (
                lineage.label_role_component != receipt.label_role_component
                or lineage.label_file_key != receipt.ordered_file_keys[0]
                or lineage.label_archive_sha256 != receipt.label_archive_sha256
                or lineage.label_content_root_sha256
                != receipt.label_zip_content_root_sha256
                or lineage.label_count != receipt.label_count
                or lineage.face_reversed_bbox_unavailable_count
                != receipt.face_reversed_bbox_unavailable_count
                or lineage.lip_reversed_bbox_unavailable_count
                != receipt.lip_reversed_bbox_unavailable_count
                or lineage.reversed_bbox_affected_label_count
                != receipt.reversed_bbox_affected_sample_count
            ):
                raise ValueError("aihub538_multishard_capacity_lineage_invalid")
        _require_namespace_match(aggregates)
        receipts = tuple(item.admission_receipt for item in authorizations)
        sample_digests = [
            item.sample_sha256
            for aggregate in aggregates
            for item in aggregate.samples
        ]
        if len(sample_digests) != len(set(sample_digests)):
            raise ValueError("aihub538_multishard_sample_collision")
        member_sets = tuple(
            item.restricted_member_set.relative_label_path_hmac_sha256
            for item in aggregates
            if item.restricted_member_set is not None
        )
        actor_sets = tuple(
            item.restricted_identity_set.speaker_hmac_sha256
            for item in aggregates
        )
        attempt_sets = tuple(
            item.restricted_identity_set.session_hmac_sha256
            for item in aggregates
        )
        path_collision = sum(map(len, member_sets)) - len(
            set().union(*member_sets)
        )
        actor_intersection = sum(map(len, actor_sets)) - len(
            set().union(*actor_sets)
        )
        attempt_intersection = sum(map(len, attempt_sets)) - len(
            set().union(*attempt_sets)
        )
        archive_roots = tuple(
            root
            for item in aggregates
            for root in (
                item.label_zip_content_root_sha256,
                item.source_zip_content_root_sha256,
            )
        )
        archive_root_collisions = len(archive_roots) - len(set(archive_roots))
        if path_collision or actor_intersection or attempt_intersection:
            raise ValueError("aihub538_multishard_private_collision_detected")
        if archive_root_collisions:
            raise ValueError("aihub538_multishard_content_root_collision")

        selection_sha256 = sha256_document(
            {
                "schema_version": (
                    "voice_coach.aihub538_training_sample_selection.v3"
                ),
                "selection_rule": AIHUB538_MULTI_SAMPLE_SELECTION_RULE,
                "admission_policy_sha256": (
                    AIHUB538_MULTI_ADMISSION_POLICY_SHA256
                ),
                "sample_sha256": sorted(sample_digests),
            }
        )
        identity_union = set().union(*actor_sets)
        attempt_union = set().union(*attempt_sets)
        member_union = set().union(*member_sets)
        lineage = tuple(
            _lineage(receipt, authorization)
            for receipt, authorization in zip(
                receipts,
                authorizations,
                strict=True,
            )
        )
        identity_union_root = sha256_document(
            {
                "schema_version": (
                    "voice_coach.aihub538_identity_union_root.v1"
                ),
                "actor_hmac_sha256": sorted(identity_union),
                "attempt_hmac_sha256": sorted(attempt_union),
            }
        )
        member_union_root = sha256_document(
            {
                "schema_version": "voice_coach.aihub538_member_union_root.v1",
                "relative_path_hmac_sha256": sorted(member_union),
            }
        )
        manifest = sha256_document(
            {
                "schema_version": (
                    "voice_coach.aihub538_multishard_admission_manifest.v2"
                ),
                "admission_policy_sha256": (
                    AIHUB538_MULTI_ADMISSION_POLICY_SHA256
                ),
                "shard_lineage": [item.to_json_object() for item in lineage],
                "sample_selection_sha256": selection_sha256,
                "identity_namespace_commitment_sha256": (
                    ts174.aggregate.restricted_identity_set.namespace_commitment_sha256
                ),
                "member_namespace_commitment_sha256": (
                    ts174.aggregate.restricted_member_set.namespace_commitment_sha256
                ),
                "identity_union_root_sha256": identity_union_root,
                "member_union_root_sha256": member_union_root,
                "paired_sample_count": len(sample_digests),
                "actor_count": len(identity_union),
                "attempt_count": len(attempt_union),
                "collision_counts": [
                    path_collision,
                    actor_intersection,
                    attempt_intersection,
                    archive_root_collisions,
                ],
                "capacity_audit_receipt_sha256": (
                    capacity_receipt.receipt_sha256
                ),
                "capacity_actor_assignment_root_sha256": (
                    capacity_receipt.actor_assignment_root_sha256
                ),
                "capacity_metadata_excluded_label_count": (
                    capacity_receipt.metadata_excluded_label_count
                ),
                "capacity_metadata_exclusion_root_sha256": (
                    capacity_receipt.metadata_exclusion_root_sha256
                ),
                "capacity_development_label_selection_root_sha256": (
                    capacity_receipt.post_exclusion_development_selection_root_sha256
                ),
                "capacity_calibration_label_selection_root_sha256": (
                    capacity_receipt.post_exclusion_calibration_selection_root_sha256
                ),
                "frozen_calibration_protocol_sha256": (
                    MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
                ),
            }
        )
        draft = cls(
            shard_lineage=lineage,
            sample_selection_sha256=selection_sha256,
            admission_manifest_sha256=manifest,
            identity_namespace_commitment_sha256=(
                ts174.aggregate.restricted_identity_set.namespace_commitment_sha256
            ),
            member_namespace_commitment_sha256=(
                ts174.aggregate.restricted_member_set.namespace_commitment_sha256
            ),
            paired_sample_count=len(sample_digests),
            actor_count=len(identity_union),
            attempt_count=len(attempt_union),
            cross_shard_path_collision_count=path_collision,
            cross_shard_actor_intersection_count=actor_intersection,
            cross_shard_attempt_intersection_count=attempt_intersection,
            archive_content_root_collision_count=archive_root_collisions,
            identity_union_root_sha256=identity_union_root,
            member_union_root_sha256=member_union_root,
            capacity_audit_receipt_sha256=capacity_receipt.receipt_sha256,
            capacity_actor_assignment_root_sha256=(
                capacity_receipt.actor_assignment_root_sha256
            ),
            capacity_metadata_excluded_label_count=(
                capacity_receipt.metadata_excluded_label_count
            ),
            capacity_metadata_exclusion_root_sha256=(
                capacity_receipt.metadata_exclusion_root_sha256
            ),
            capacity_development_label_selection_root_sha256=(
                capacity_receipt.post_exclusion_development_selection_root_sha256
            ),
            capacity_calibration_label_selection_root_sha256=(
                capacity_receipt.post_exclusion_calibration_selection_root_sha256
            ),
            frozen_calibration_protocol_sha256=(
                MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
            ),
            receipt_sha256="0" * 64,
        )
        issued = cls(
            **{
                name: getattr(draft, name)
                for name in draft.__dataclass_fields__
                if name != "receipt_sha256"
            },
            receipt_sha256=sha256_document(draft.unsigned_json_object()),
        )
        issued._validate()
        return issued

    def unsigned_json_object(self) -> dict[str, object]:
        return {
            "schema_version": "voice_coach.aihub538_multishard_admission.v2",
            "status": "completed_nonlocked_multishard_admission",
            "dataset_id": 538,
            "source_partitions": list(self.source_partitions),
            "shard_lineage": [item.to_json_object() for item in self.shard_lineage],
            "admission_policy_sha256": self.admission_policy_sha256,
            "sample_selection_rule": self.sample_selection_rule,
            "sample_selection_sha256": self.sample_selection_sha256,
            "admission_manifest_sha256": self.admission_manifest_sha256,
            "identity_namespace_commitment_sha256": (
                self.identity_namespace_commitment_sha256
            ),
            "member_namespace_commitment_sha256": (
                self.member_namespace_commitment_sha256
            ),
            "paired_sample_count": self.paired_sample_count,
            "actor_count": self.actor_count,
            "attempt_count": self.attempt_count,
            "cross_shard_path_collision_count": (
                self.cross_shard_path_collision_count
            ),
            "cross_shard_actor_intersection_count": (
                self.cross_shard_actor_intersection_count
            ),
            "cross_shard_attempt_intersection_count": (
                self.cross_shard_attempt_intersection_count
            ),
            "archive_content_root_collision_count": (
                self.archive_content_root_collision_count
            ),
            "identity_union_root_sha256": self.identity_union_root_sha256,
            "member_union_root_sha256": self.member_union_root_sha256,
            "capacity_audit_receipt_sha256": (
                self.capacity_audit_receipt_sha256
            ),
            "capacity_actor_assignment_root_sha256": (
                self.capacity_actor_assignment_root_sha256
            ),
            "capacity_metadata_excluded_label_count": (
                self.capacity_metadata_excluded_label_count
            ),
            "capacity_metadata_exclusion_root_sha256": (
                self.capacity_metadata_exclusion_root_sha256
            ),
            "capacity_development_label_selection_root_sha256": (
                self.capacity_development_label_selection_root_sha256
            ),
            "capacity_calibration_label_selection_root_sha256": (
                self.capacity_calibration_label_selection_root_sha256
            ),
            "frozen_calibration_protocol_sha256": (
                self.frozen_calibration_protocol_sha256
            ),
            "capacity_prefix_receipt_bound": True,
            "pair_pipeline_completion_bound": True,
            "raw_paths_serialized": False,
            "transcripts_serialized": False,
            "actor_identifiers_serialized": False,
            "attempt_identifiers_serialized": False,
            "individual_hmacs_serialized": False,
            "locked_partition_accessed": False,
            "aggregate_only": True,
            "promotion_authorized": False,
        }

    def to_json_object(self) -> dict[str, object]:
        return {
            **self.unsigned_json_object(),
            "receipt_sha256": self.receipt_sha256,
        }

    def _validate(self) -> None:
        if (
            tuple(item.shard_id for item in self.shard_lineage)
            != (
                AIHUB538_TS174_TL174_SHARD.shard_id,
                AIHUB538_TS175_TL175_SHARD.shard_id,
                AIHUB538_TS176_TL176_SHARD.shard_id,
            )
            or self.source_partitions
            != AIHUB538_MULTI_CALIBRATION_SOURCE_PARTITIONS
            or self.admission_policy_sha256
            != AIHUB538_MULTI_ADMISSION_POLICY_SHA256
            or self.sample_selection_rule != AIHUB538_MULTI_SAMPLE_SELECTION_RULE
            or self.paired_sample_count <= 0
            or self.actor_count < 4
            or self.attempt_count <= 0
            or any(
                value != 0
                for value in (
                    self.cross_shard_path_collision_count,
                    self.cross_shard_actor_intersection_count,
                    self.cross_shard_attempt_intersection_count,
                    self.archive_content_root_collision_count,
                )
            )
        ):
            raise ValueError("aihub538_multishard_admission_invalid")
        for name in (
            "sample_selection_sha256",
            "admission_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "member_namespace_commitment_sha256",
            "identity_union_root_sha256",
            "member_union_root_sha256",
            "capacity_audit_receipt_sha256",
            "capacity_actor_assignment_root_sha256",
            "capacity_metadata_exclusion_root_sha256",
            "capacity_development_label_selection_root_sha256",
            "capacity_calibration_label_selection_root_sha256",
            "frozen_calibration_protocol_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if (
            self.frozen_calibration_protocol_sha256
            != MULTISHARD_AIHUB538_DECISION_CALIBRATION_PROTOCOL.protocol_sha256
            or any(
                lineage.pipeline_completion_receipt_sha256 is None
                for lineage in self.shard_lineage
            )
            or self.shard_lineage[0].capacity_audit_receipt_sha256 is not None
            or isinstance(self.capacity_metadata_excluded_label_count, bool)
            or not isinstance(
                self.capacity_metadata_excluded_label_count,
                int,
            )
            or self.capacity_metadata_excluded_label_count < 0
            or self.capacity_metadata_excluded_label_count
            > self.paired_sample_count
            or any(
                lineage.capacity_audit_receipt_sha256
                != self.capacity_audit_receipt_sha256
                for lineage in self.shard_lineage[1:]
            )
        ):
            raise ValueError("aihub538_multishard_authorization_invalid")
        if sha256_document(self.unsigned_json_object()) != self.receipt_sha256:
            raise ValueError("aihub538_multishard_admission_integrity_failed")


def _require_namespace_match(
    aggregates: tuple[
        Aihub538TrainingAdmissionAggregate,
        Aihub538TrainingAdmissionAggregate,
        Aihub538TrainingAdmissionAggregate,
    ],
) -> None:
    identity_namespaces = {
        item.restricted_identity_set.namespace_commitment_sha256
        for item in aggregates
    }
    member_namespaces = {
        item.restricted_member_set.namespace_commitment_sha256
        for item in aggregates
        if item.restricted_member_set is not None
    }
    if len(identity_namespaces) != 1 or len(member_namespaces) != 1:
        raise ValueError("aihub538_multishard_namespace_mismatch")


def _lineage(
    receipt: Aihub538TrainingAdmissionReceipt,
    authorization: Aihub538AuthorizedShardAdmission,
) -> Aihub538MultiShardLineage:
    return Aihub538MultiShardLineage(
        shard_id=receipt.shard_id,
        admission_receipt_sha256=receipt.receipt_sha256,
        admission_manifest_sha256=receipt.admission_manifest_sha256,
        label_archive_sha256=receipt.label_archive_sha256,
        source_archive_sha256=receipt.source_archive_sha256,
        label_content_root_sha256=receipt.label_zip_content_root_sha256,
        source_content_root_sha256=receipt.source_zip_content_root_sha256,
        paired_sample_count=receipt.paired_sample_count,
        pipeline_completion_receipt_sha256=(
            authorization.pipeline_completion_receipt_sha256
        ),
        capacity_audit_receipt_sha256=(
            authorization.capacity_audit_receipt_sha256
        ),
    )


__all__ = [
    "AIHUB538_MULTI_ADMISSION_POLICY_SHA256",
    "AIHUB538_MULTI_SAMPLE_SELECTION_RULE",
    "Aihub538AuthorizedShardAdmission",
    "Aihub538MultiShardAdmissionReceipt",
    "Aihub538MultiShardLineage",
]
