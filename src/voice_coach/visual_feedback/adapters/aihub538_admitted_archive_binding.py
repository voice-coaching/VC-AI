"""Stable descriptor binding for admitted AI Hub 538 training ZIPs.

This module is the TOCTOU boundary between the aggregate training-admission
receipt and actual-media calibration.  It deliberately maps candidate names
only by the receipt's whole-archive SHA-256 and ZIP content root.  Member names,
paths, transcripts, and participant identifiers never enter its receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import fcntl
import json
import os
import stat
from typing import Any
from weakref import WeakKeyDictionary, ref

from ..models.aihub538_training_evidence import (
    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    AIHUB538_TRAINING_ANGLES,
    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingAngleAggregate,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_TS174_TL174_SHARD,
    aihub538_nonlocked_shard_by_id,
)
from ..models.integrity import sha256_document
from ..services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
)
from .aihub538_training_archive import (
    Aihub538ArchiveError,
    Aihub538ArchiveSecurityPolicy,
    DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
    ZipValidationReceipt,
    validate_zip_descriptor,
)


_MAXIMUM_ADMISSION_RECEIPT_BYTES = 1024 * 1024
_ADMISSION_V1_SCHEMA = "voice_coach.aihub538_training_admission_receipt.v1"
_ADMISSION_V2_SCHEMA = "voice_coach.aihub538_training_admission_receipt.v2"
_ADMISSION_V1_KEYS = {
    "actual_media_decode_executed",
    "admission_manifest_sha256",
    "admission_policy_sha256",
    "aggregate_only",
    "audio_reference_count",
    "av_sync_threshold_calibrated",
    "dataset_access_permission_confirmed",
    "dataset_id",
    "dataset_partition",
    "dataset_role",
    "exact_label_video_audio_binding_validated",
    "identity_namespace_commitment_sha256",
    "individual_identity_digests_serialized",
    "label_archive_sha256",
    "label_count",
    "label_zip_content_root_sha256",
    "lip_bbox_frame_count",
    "locked_partition_accessed",
    "non_A_views_bound_to_A_audio",
    "paired_sample_count",
    "permission_grants_technical_promotion",
    "promotion_authorized",
    "qc_threshold_calibrated",
    "receipt_sha256",
    "sample_selection_sha256",
    "schema_version",
    "sentence_count",
    "source_archive_sha256",
    "source_paths_retained",
    "source_zip_content_root_sha256",
    "speaker_count",
    "speaker_identifiers_retained",
    "status",
    "transcripts_retained",
    "unique_audio_count",
    "video_count",
    "view_aggregates",
}
_ADMISSION_V2_KEYS = _ADMISSION_V1_KEYS | {
    "bbox_observation_policy_sha256",
    "face_reversed_bbox_unavailable_count",
    "label_role_component",
    "lip_reversed_bbox_unavailable_count",
    "ordered_file_keys",
    "sample_selection_rule",
    "reversed_bbox_affected_sample_count",
    "reversed_bbox_coordinates_reordered",
    "selected_unavailable_bbox_is_qc_failure",
    "shard_id",
    "source_role_component",
}


@dataclass(frozen=True, slots=True)
class Aihub538DescriptorIdentity:
    """Non-serialized identity used to detect descriptor target mutation."""

    device: int
    inode: int
    size_bytes: int
    mode: int
    owner_uid: int
    link_count: int
    modified_time_ns: int
    changed_time_ns: int


class _CapabilityToken:
    """Opaque weak-referenceable token issued only after live validation."""

    __slots__ = ("__weakref__",)


_VALIDATED_ZIP_CAPABILITIES: WeakKeyDictionary[
    _CapabilityToken,
    tuple[object, ...],
] = WeakKeyDictionary()
_ADMITTED_ARCHIVE_CAPABILITIES: WeakKeyDictionary[
    _CapabilityToken,
    tuple[object, ...],
] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Aihub538StableZipDescriptor:
    """One caller-owned descriptor after a whole-ZIP stable validation."""

    descriptor: int
    identity: Aihub538DescriptorIdentity
    validation_receipt: ZipValidationReceipt
    security_policy: Aihub538ArchiveSecurityPolicy = field(repr=False)
    _capability: _CapabilityToken = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        # A serialized validation receipt describes an earlier read; it is not
        # a live freshness capability.  Only this module's validator can attach
        # the process-local marker accepted by downstream adapters.
        try:
            issued = _VALIDATED_ZIP_CAPABILITIES.get(self._capability)
        except TypeError:
            issued = None
        if (
            issued is None
            or issued[0] != os.getpid()
            or issued[1:5]
            != (
                self.descriptor,
                self.identity,
                self.validation_receipt,
                self.security_policy,
            )
            or issued[6] is not None
            and issued[6]() is not self
            or type(self.security_policy) is not Aihub538ArchiveSecurityPolicy
        ):
            raise ValueError("aihub538_stable_zip_capability_invalid")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Aihub538AdmittedArchiveBinding:
    """Exact label/source descriptor binding to one admission receipt."""

    label: Aihub538StableZipDescriptor
    source: Aihub538StableZipDescriptor
    admission_receipt_sha256: str
    security_policy: Aihub538ArchiveSecurityPolicy = field(repr=False)
    _capability: _CapabilityToken = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            issued = _ADMITTED_ARCHIVE_CAPABILITIES.get(self._capability)
        except TypeError:
            issued = None
        if (
            issued is None
            or issued[0] != os.getpid()
            or issued[1] is not self.label
            or issued[2] is not self.source
            or issued[3:5] != (
                self.admission_receipt_sha256,
                self.security_policy,
            )
            or issued[5] is not None
            and issued[5]() is not self
            or type(self.label) is not Aihub538StableZipDescriptor
            or type(self.source) is not Aihub538StableZipDescriptor
            or self.label.descriptor == self.source.descriptor
            or self.label.security_policy != self.security_policy
            or self.source.security_policy != self.security_policy
            or len(self.admission_receipt_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.admission_receipt_sha256
            )
        ):
            raise ValueError("aihub538_admitted_archive_capability_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class Aihub538CandidateRoleBinding:
    """Runtime mapping of candidate-0/1 without trusting candidate filenames."""

    label_zip_descriptor: int
    source_zip_descriptor: int
    label_candidate_index: int
    source_candidate_index: int
    label_archive_sha256: str
    source_archive_sha256: str
    label_zip_content_root_sha256: str
    source_zip_content_root_sha256: str
    admission_receipt_sha256: str
    receipt_sha256: str
    shard_id: str = AIHUB538_TS174_TL174_SHARD.shard_id
    dataset_partition: str = AIHUB538_TS174_TL174_SHARD.dataset_partition

    def _unsigned_json_object(self) -> dict[str, object]:
        document = {
            "schema_version": (
                "voice_coach.aihub538_candidate_role_binding_receipt.v1"
            ),
            "dataset_id": 538,
            "dataset_partition": self.dataset_partition,
            "candidate_archive_count": 2,
            "label_candidate_index": self.label_candidate_index,
            "source_candidate_index": self.source_candidate_index,
            "label_archive_sha256": self.label_archive_sha256,
            "source_archive_sha256": self.source_archive_sha256,
            "label_zip_content_root_sha256": (
                self.label_zip_content_root_sha256
            ),
            "source_zip_content_root_sha256": (
                self.source_zip_content_root_sha256
            ),
            "admission_receipt_sha256": self.admission_receipt_sha256,
            "mapping_basis": "archive_sha256_and_zip_content_root_exact_match",
            "whole_zip_crc_validated": True,
            "descriptor_identity_stable": True,
            "candidate_names_trusted": False,
            "raw_member_names_serialized": False,
            "source_paths_serialized": False,
            "transcripts_serialized": False,
            "participant_identifiers_serialized": False,
            "aggregate_only": True,
            "locked_partition_accessed": False,
            "promotion_authorized": False,
        }
        if self.shard_id != AIHUB538_TS174_TL174_SHARD.shard_id:
            document["schema_version"] = (
                "voice_coach.aihub538_candidate_role_binding_receipt.v2"
            )
            document["shard_id"] = self.shard_id
        return document

    def to_json_object(self) -> dict[str, object]:
        document = self._unsigned_json_object()
        document["receipt_sha256"] = self.receipt_sha256
        return document


def load_aihub538_training_admission_receipt_descriptor(
    descriptor: int,
) -> Aihub538TrainingAdmissionReceipt:
    """Strictly reload one pipeline ``50-dataset-admission.json`` descriptor."""

    before = capture_aihub538_descriptor_identity(
        descriptor,
        require_mode=0o400,
    )
    if before.size_bytes > _MAXIMUM_ADMISSION_RECEIPT_BYTES:
        raise Aihub538DecisionCalibrationError(
            "aihub538_admission_receipt_reload_failed"
        )
    duplicate: int | None = None
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        os.lseek(duplicate, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(duplicate, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > _MAXIMUM_ADMISSION_RECEIPT_BYTES:
                raise OSError
        payload = b"".join(chunks)
        if not payload or len(payload) != before.size_bytes:
            raise OSError
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_admission_receipt_reload_failed"
        ) from None
    finally:
        if duplicate is not None:
            os.close(duplicate)
    assert_aihub538_descriptor_identity(descriptor, before)
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(document, dict):
            raise ValueError
        unsigned_document = dict(document)
        serialized_receipt_sha256 = unsigned_document.pop(
            "receipt_sha256",
            None,
        )
        if (
            not isinstance(serialized_receipt_sha256, str)
            or sha256_document(unsigned_document) != serialized_receipt_sha256
        ):
            raise ValueError
        schema_version = document.get("schema_version")
        if schema_version == _ADMISSION_V1_SCHEMA:
            if (
                set(document) != _ADMISSION_V1_KEYS
                or document.get("dataset_partition")
                != AIHUB538_TS174_TL174_SHARD.dataset_partition
                or document.get("admission_policy_sha256")
                not in {
                    AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256,
                    AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
                }
            ):
                raise ValueError
            shard_spec = AIHUB538_TS174_TL174_SHARD
            source_role_component = shard_spec.source_role_component
            label_role_component = shard_spec.label_role_component
            ordered_file_keys = shard_spec.ordered_file_keys
            sample_selection_rule = shard_spec.sample_selection_rule
            face_reversed_bbox_unavailable_count = 0
            lip_reversed_bbox_unavailable_count = 0
            reversed_bbox_affected_sample_count = 0
        elif schema_version == _ADMISSION_V2_SCHEMA:
            if set(document) != _ADMISSION_V2_KEYS:
                raise ValueError
            shard_id = document["shard_id"]
            if not isinstance(shard_id, str):
                raise ValueError
            shard_spec = aihub538_nonlocked_shard_by_id(shard_id)
            if not shard_spec.is_pair:
                raise ValueError
            source_role_component = document["source_role_component"]
            label_role_component = document["label_role_component"]
            ordered_keys_value = document["ordered_file_keys"]
            if not isinstance(ordered_keys_value, list):
                raise ValueError
            ordered_file_keys = tuple(ordered_keys_value)
            sample_selection_rule = document["sample_selection_rule"]
            face_reversed_bbox_unavailable_count = document[
                "face_reversed_bbox_unavailable_count"
            ]
            lip_reversed_bbox_unavailable_count = document[
                "lip_reversed_bbox_unavailable_count"
            ]
            reversed_bbox_affected_sample_count = document[
                "reversed_bbox_affected_sample_count"
            ]
        else:
            raise ValueError
        views = document.get("view_aggregates")
        if not isinstance(views, list):
            raise ValueError
        angle_aggregates = tuple(
            _angle_aggregate_from_json_object(item) for item in views
        )
        receipt = Aihub538TrainingAdmissionReceipt(
            label_archive_sha256=document["label_archive_sha256"],
            source_archive_sha256=document["source_archive_sha256"],
            label_zip_content_root_sha256=(
                document["label_zip_content_root_sha256"]
            ),
            source_zip_content_root_sha256=(
                document["source_zip_content_root_sha256"]
            ),
            paired_sample_count=document["paired_sample_count"],
            label_count=document["label_count"],
            video_count=document["video_count"],
            unique_audio_count=document["unique_audio_count"],
            audio_reference_count=document["audio_reference_count"],
            speaker_count=document["speaker_count"],
            lip_bbox_frame_count=document["lip_bbox_frame_count"],
            sentence_count=document["sentence_count"],
            angle_aggregates=angle_aggregates,
            sample_selection_sha256=document["sample_selection_sha256"],
            admission_manifest_sha256=document["admission_manifest_sha256"],
            identity_namespace_commitment_sha256=(
                document["identity_namespace_commitment_sha256"]
            ),
            receipt_sha256=document["receipt_sha256"],
            shard_id=shard_spec.shard_id,
            dataset_partition=document["dataset_partition"],
            source_role_component=source_role_component,
            label_role_component=label_role_component,
            ordered_file_keys=ordered_file_keys,
            admission_policy_sha256=document["admission_policy_sha256"],
            sample_selection_rule=sample_selection_rule,
            face_reversed_bbox_unavailable_count=(
                face_reversed_bbox_unavailable_count
            ),
            lip_reversed_bbox_unavailable_count=(
                lip_reversed_bbox_unavailable_count
            ),
            reversed_bbox_affected_sample_count=(
                reversed_bbox_affected_sample_count
            ),
        )
        receipt._validate()
        if receipt.to_json_object() != document:
            raise ValueError
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_admission_receipt_reload_failed"
        ) from None
    return receipt


def bind_aihub538_legacy_ts174_replay_aggregate(
    *,
    current_aggregate: Aihub538TrainingAdmissionAggregate,
    legacy_receipt: Aihub538TrainingAdmissionReceipt,
) -> Aihub538TrainingAdmissionAggregate:
    """Prove a current TS174 replay is byte-lineage equivalent to a v1 receipt.

    The completed TS174 receipt used the historical policy pin in its sample
    selection digest.  A current re-admission therefore has different
    selection and manifest digests even when every admitted sample and archive
    commitment is identical.  This function reconstructs both historical
    formulas from the current aggregate and returns an explicit legacy-bound
    view only when issuing a receipt from that view reproduces the supplied
    receipt exactly.  It never changes sample rows, archive commitments, HMAC
    sets, counts, or the supplied receipt.
    """

    try:
        if not isinstance(current_aggregate, Aihub538TrainingAdmissionAggregate):
            raise ValueError
        if not isinstance(legacy_receipt, Aihub538TrainingAdmissionReceipt):
            raise ValueError
        legacy_receipt._validate()
        _validate_current_ts174_replay_aggregate(current_aggregate)
        if (
            legacy_receipt.shard_id
            != AIHUB538_TS174_TL174_SHARD.shard_id
            or legacy_receipt.dataset_partition
            != AIHUB538_TS174_TL174_SHARD.dataset_partition
            or legacy_receipt.admission_policy_sha256
            != AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
        ):
            raise ValueError

        legacy_selection_sha256 = _ts174_sample_selection_sha256(
            current_aggregate,
            admission_policy_sha256=(
                AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
            ),
        )
        legacy_manifest_sha256 = _ts174_admission_manifest_sha256(
            current_aggregate,
            sample_selection_sha256=legacy_selection_sha256,
        )
        legacy_bound = replace(
            current_aggregate,
            admission_policy_sha256=(
                AIHUB538_TS174_LEGACY_V1_ADMISSION_POLICY_SHA256
            ),
            sample_selection_sha256=legacy_selection_sha256,
            admission_manifest_sha256=legacy_manifest_sha256,
        )
        replayed_receipt = Aihub538TrainingAdmissionReceipt.issue(
            legacy_bound
        )
        if (
            replayed_receipt != legacy_receipt
            or replayed_receipt.to_json_object()
            != legacy_receipt.to_json_object()
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_legacy_ts174_replay_binding_failed"
        ) from None
    return legacy_bound


def validate_aihub538_stable_zip_descriptor(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
) -> Aihub538StableZipDescriptor:
    """Hash, CRC-scan, content-root, and fstat-bind one complete ZIP."""

    return _validate_aihub538_zip_descriptor(descriptor,policy=policy,require_mode=0o600,mode_contract="mutable_private_v1")


def validate_aihub538_immutable_zip_descriptor(
    descriptor: int,
    *,
    policy: Aihub538ArchiveSecurityPolicy = DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY,
) -> Aihub538StableZipDescriptor:
    """Validate one owner-only immutable runtime candidate (0400, nlink=1)."""

    return _validate_aihub538_zip_descriptor(descriptor,policy=policy,require_mode=0o400,mode_contract="immutable_owner_v1")


def _validate_aihub538_zip_descriptor(descriptor:int,*,policy:Aihub538ArchiveSecurityPolicy,require_mode:int,mode_contract:str)->Aihub538StableZipDescriptor:
    before = capture_aihub538_descriptor_identity(descriptor,require_mode=require_mode)
    if mode_contract=="immutable_owner_v1" and (before.owner_uid!=os.geteuid() or before.link_count!=1):
        raise Aihub538DecisionCalibrationError("aihub538_archive_descriptor_invalid")
    try:
        validation = validate_zip_descriptor(descriptor, policy=policy)
    except Aihub538ArchiveError as error:
        raise Aihub538DecisionCalibrationError(error.reason_code) from None
    assert_aihub538_descriptor_identity(descriptor, before)
    capability = _CapabilityToken()
    _VALIDATED_ZIP_CAPABILITIES[capability] = (
        os.getpid(),
        descriptor,
        before,
        validation,
        policy,
        mode_contract,
        None,
    )
    try:
        stable = Aihub538StableZipDescriptor(
            descriptor=descriptor,
            identity=before,
            validation_receipt=validation,
            security_policy=policy,
            _capability=capability,
        )
        _VALIDATED_ZIP_CAPABILITIES[capability] = (
            os.getpid(),
            descriptor,
            before,
            validation,
            policy,
            mode_contract,
            ref(stable),
        )
        return stable
    except BaseException:
        _VALIDATED_ZIP_CAPABILITIES.pop(capability, None)
        raise


def bind_aihub538_immutable_admitted_archive_descriptors(*,label_zip_descriptor:int,source_zip_descriptor:int,admission_receipt:Aihub538TrainingAdmissionReceipt,policy:Aihub538ArchiveSecurityPolicy=DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY)->Aihub538AdmittedArchiveBinding:
    _validate_admission_receipt(admission_receipt)
    label=validate_aihub538_immutable_zip_descriptor(label_zip_descriptor,policy=policy)
    source=validate_aihub538_immutable_zip_descriptor(source_zip_descriptor,policy=policy)
    return bind_aihub538_prevalidated_immutable_archive_descriptors(label=label,source=source,admission_receipt=admission_receipt,policy=policy)


def bind_aihub538_prevalidated_immutable_archive_descriptors(*,label:Aihub538StableZipDescriptor,source:Aihub538StableZipDescriptor,admission_receipt:Aihub538TrainingAdmissionReceipt,policy:Aihub538ArchiveSecurityPolicy=DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY)->Aihub538AdmittedArchiveBinding:
    for stable in (label,source):
        try: row=_VALIDATED_ZIP_CAPABILITIES.get(stable._capability)
        except (AttributeError,TypeError): row=None
        if row is None or row[5]!="immutable_owner_v1":
            raise Aihub538DecisionCalibrationError("aihub538_immutable_archive_validation_required")
    return _bind_aihub538_prevalidated_archive_descriptors(label=label,source=source,admission_receipt=admission_receipt,policy=policy)


def bind_aihub538_admitted_archive_descriptors(
    *,
    label_zip_descriptor: int,
    source_zip_descriptor: int,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
) -> Aihub538AdmittedArchiveBinding:
    """Bind explicit label/source descriptors to exact admission ZIP roots."""

    _validate_admission_receipt(admission_receipt)
    label = validate_aihub538_stable_zip_descriptor(
        label_zip_descriptor,
        policy=policy,
    )
    source = validate_aihub538_stable_zip_descriptor(
        source_zip_descriptor,
        policy=policy,
    )
    return bind_aihub538_prevalidated_archive_descriptors(
        label=label,
        source=source,
        admission_receipt=admission_receipt,
        policy=policy,
    )


def bind_aihub538_prevalidated_archive_descriptors(
    *,
    label: Aihub538StableZipDescriptor,
    source: Aihub538StableZipDescriptor,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
) -> Aihub538AdmittedArchiveBinding:
    """Bind only live mutable-private validations to the exact admission."""

    for stable in (label, source):
        try:
            row = _VALIDATED_ZIP_CAPABILITIES.get(stable._capability)
        except (AttributeError, TypeError):
            row = None
        if row is None or row[5] != "mutable_private_v1":
            raise Aihub538DecisionCalibrationError(
                "aihub538_mutable_archive_validation_required"
            )
    return _bind_aihub538_prevalidated_archive_descriptors(
        label=label,
        source=source,
        admission_receipt=admission_receipt,
        policy=policy,
    )


def _bind_aihub538_prevalidated_archive_descriptors(
    *,
    label: Aihub538StableZipDescriptor,
    source: Aihub538StableZipDescriptor,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy,
) -> Aihub538AdmittedArchiveBinding:
    """Common binder after the caller enforces the intended mode contract."""

    _validate_admission_receipt(admission_receipt)
    for stable in (label, source):
        assert_aihub538_stable_zip_descriptor(stable, policy=policy)
    if (
        label.validation_receipt.archive_sha256
        != admission_receipt.label_archive_sha256
        or label.validation_receipt.content_root_sha256
        != admission_receipt.label_zip_content_root_sha256
        or source.validation_receipt.archive_sha256
        != admission_receipt.source_archive_sha256
        or source.validation_receipt.content_root_sha256
        != admission_receipt.source_zip_content_root_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_admitted_archive_binding_mismatch"
        )
    capability = _CapabilityToken()
    _ADMITTED_ARCHIVE_CAPABILITIES[capability] = (
        os.getpid(),
        label,
        source,
        admission_receipt.receipt_sha256,
        policy,
        None,
    )
    try:
        binding = Aihub538AdmittedArchiveBinding(
            label=label,
            source=source,
            admission_receipt_sha256=admission_receipt.receipt_sha256,
            security_policy=policy,
            _capability=capability,
        )
        _ADMITTED_ARCHIVE_CAPABILITIES[capability] = (
            os.getpid(),
            label,
            source,
            admission_receipt.receipt_sha256,
            policy,
            ref(binding),
        )
        return binding
    except BaseException:
        _ADMITTED_ARCHIVE_CAPABILITIES.pop(capability, None)
        raise


def resolve_aihub538_candidate_role_descriptors(
    *,
    candidate_0_descriptor: int,
    candidate_1_descriptor: int,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
) -> Aihub538CandidateRoleBinding:
    """Map candidate-0/1 to roles solely from the admitted ZIP commitments."""

    _validate_admission_receipt(admission_receipt)
    candidates = (
        validate_aihub538_stable_zip_descriptor(
            candidate_0_descriptor,
            policy=policy,
        ),
        validate_aihub538_stable_zip_descriptor(
            candidate_1_descriptor,
            policy=policy,
        ),
    )
    return resolve_aihub538_prevalidated_candidate_role_descriptors(
        candidates=candidates,
        admission_receipt=admission_receipt,
        policy=policy,
    )


def resolve_aihub538_immutable_candidate_role_descriptors(*,candidate_0_descriptor:int,candidate_1_descriptor:int,admission_receipt:Aihub538TrainingAdmissionReceipt,policy:Aihub538ArchiveSecurityPolicy=DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY)->Aihub538CandidateRoleBinding:
    _validate_admission_receipt(admission_receipt)
    candidates=(validate_aihub538_immutable_zip_descriptor(candidate_0_descriptor,policy=policy),validate_aihub538_immutable_zip_descriptor(candidate_1_descriptor,policy=policy))
    return resolve_aihub538_prevalidated_candidate_role_descriptors(candidates=candidates,admission_receipt=admission_receipt,policy=policy)


def resolve_aihub538_prevalidated_candidate_role_descriptors(
    *,
    candidates: tuple[
        Aihub538StableZipDescriptor,
        Aihub538StableZipDescriptor,
    ],
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy = (
        DEFAULT_AIHUB538_ARCHIVE_SECURITY_POLICY
    ),
) -> Aihub538CandidateRoleBinding:
    """Resolve roles from two live validations without rescanning either ZIP."""

    _validate_admission_receipt(admission_receipt)
    if not isinstance(candidates, tuple) or len(candidates) != 2:
        raise Aihub538DecisionCalibrationError(
            "aihub538_candidate_validation_capability_invalid"
        )
    try:
        for candidate in candidates:
            assert_aihub538_stable_zip_descriptor(candidate, policy=policy)
    except (AttributeError, TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_candidate_validation_capability_invalid"
        ) from None
    label_matches = _matching_candidate_indices(
        candidates,
        archive_sha256=admission_receipt.label_archive_sha256,
        content_root_sha256=admission_receipt.label_zip_content_root_sha256,
    )
    source_matches = _matching_candidate_indices(
        candidates,
        archive_sha256=admission_receipt.source_archive_sha256,
        content_root_sha256=admission_receipt.source_zip_content_root_sha256,
    )
    if (
        len(label_matches) != 1
        or len(source_matches) != 1
        or label_matches[0] == source_matches[0]
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_candidate_role_binding_failed"
        )
    label_index = label_matches[0]
    source_index = source_matches[0]
    draft = Aihub538CandidateRoleBinding(
        label_zip_descriptor=candidates[label_index].descriptor,
        source_zip_descriptor=candidates[source_index].descriptor,
        label_candidate_index=label_index,
        source_candidate_index=source_index,
        label_archive_sha256=admission_receipt.label_archive_sha256,
        source_archive_sha256=admission_receipt.source_archive_sha256,
        label_zip_content_root_sha256=(
            admission_receipt.label_zip_content_root_sha256
        ),
        source_zip_content_root_sha256=(
            admission_receipt.source_zip_content_root_sha256
        ),
        admission_receipt_sha256=admission_receipt.receipt_sha256,
        receipt_sha256="0" * 64,
        shard_id=admission_receipt.shard_id,
        dataset_partition=admission_receipt.dataset_partition,
    )
    return Aihub538CandidateRoleBinding(
        **{
            field: getattr(draft, field)
            for field in draft.__dataclass_fields__
            if field != "receipt_sha256"
        },
        receipt_sha256=sha256_document(draft._unsigned_json_object()),
    )


def capture_aihub538_descriptor_identity(
    descriptor: int,
    *,
    require_mode: int | None = None,
) -> Aihub538DescriptorIdentity:
    """Capture a read-only regular descriptor without changing its offset."""

    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 3:
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_descriptor_invalid"
        )
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        metadata = os.fstat(descriptor)
    except OSError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_descriptor_invalid"
        ) from None
    if (
        flags & os.O_ACCMODE != os.O_RDONLY
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or (
            require_mode is not None
            and stat.S_IMODE(metadata.st_mode) != require_mode
        )
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_descriptor_invalid"
        )
    return Aihub538DescriptorIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size_bytes=metadata.st_size,
        mode=metadata.st_mode,
        owner_uid=metadata.st_uid,
        link_count=metadata.st_nlink,
        modified_time_ns=metadata.st_mtime_ns,
        changed_time_ns=metadata.st_ctime_ns,
    )


def assert_aihub538_descriptor_identity(
    descriptor: int,
    expected: Aihub538DescriptorIdentity,
) -> None:
    """Fail closed when the original descriptor target changed after binding."""

    if capture_aihub538_descriptor_identity(descriptor) != expected:
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_descriptor_changed"
        )


def assert_aihub538_stable_zip_descriptor(
    stable: Aihub538StableZipDescriptor,
    *,
    policy: Aihub538ArchiveSecurityPolicy | None = None,
) -> None:
    if (
        type(stable) is not Aihub538StableZipDescriptor
        or policy is not None
        and stable.security_policy != policy
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_validation_capability_invalid"
        )
    try:
        stable.__post_init__()
    except ValueError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_archive_validation_capability_invalid"
        ) from None
    assert_aihub538_descriptor_identity(stable.descriptor, stable.identity)


def assert_aihub538_admitted_archive_binding(
    binding: Aihub538AdmittedArchiveBinding,
    *,
    admission_receipt: Aihub538TrainingAdmissionReceipt,
    policy: Aihub538ArchiveSecurityPolicy,
) -> None:
    """Reassert a live admission capability without trusting receipt JSON."""

    _validate_admission_receipt(admission_receipt)
    if (
        type(binding) is not Aihub538AdmittedArchiveBinding
        or binding.admission_receipt_sha256 != admission_receipt.receipt_sha256
        or binding.security_policy != policy
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_admitted_archive_capability_invalid"
        )
    try:
        binding.__post_init__()
    except ValueError:
        raise Aihub538DecisionCalibrationError(
            "aihub538_admitted_archive_capability_invalid"
        ) from None
    assert_aihub538_stable_zip_descriptor(binding.label, policy=policy)
    assert_aihub538_stable_zip_descriptor(binding.source, policy=policy)
    if (
        binding.label.validation_receipt.archive_sha256
        != admission_receipt.label_archive_sha256
        or binding.label.validation_receipt.content_root_sha256
        != admission_receipt.label_zip_content_root_sha256
        or binding.source.validation_receipt.archive_sha256
        != admission_receipt.source_archive_sha256
        or binding.source.validation_receipt.content_root_sha256
        != admission_receipt.source_zip_content_root_sha256
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_admitted_archive_binding_mismatch"
        )


def revoke_aihub538_admitted_archive_binding(
    binding: Aihub538AdmittedArchiveBinding,
) -> None:
    """Revoke archive capabilities at their owner context exit."""

    if type(binding) is not Aihub538AdmittedArchiveBinding:
        return
    _ADMITTED_ARCHIVE_CAPABILITIES.pop(binding._capability, None)
    for stable in (binding.label, binding.source):
        if type(stable) is Aihub538StableZipDescriptor:
            _VALIDATED_ZIP_CAPABILITIES.pop(stable._capability, None)
def _matching_candidate_indices(
    candidates: tuple[Aihub538StableZipDescriptor, Aihub538StableZipDescriptor],
    *,
    archive_sha256: str,
    content_root_sha256: str,
) -> tuple[int, ...]:
    return tuple(
        index
        for index, candidate in enumerate(candidates)
        if candidate.validation_receipt.archive_sha256 == archive_sha256
        and candidate.validation_receipt.content_root_sha256
        == content_root_sha256
    )


def _validate_current_ts174_replay_aggregate(
    aggregate: Aihub538TrainingAdmissionAggregate,
) -> None:
    shard = AIHUB538_TS174_TL174_SHARD
    if (
        aggregate.shard_id != shard.shard_id
        or aggregate.dataset_partition != shard.dataset_partition
        or aggregate.source_role_component != shard.source_role_component
        or aggregate.label_role_component != shard.label_role_component
        or aggregate.ordered_file_keys != shard.ordered_file_keys
        or aggregate.sample_selection_rule != shard.sample_selection_rule
        or aggregate.admission_policy_sha256
        != AIHUB538_TRAINING_ADMISSION_POLICY_SHA256
        or aggregate.restricted_member_set is None
    ):
        raise ValueError
    sample_sha256 = tuple(item.sample_sha256 for item in aggregate.samples)
    if (
        not sample_sha256
        or sample_sha256 != tuple(sorted(sample_sha256))
        or len(sample_sha256) != len(set(sample_sha256))
        or aggregate.speaker_count
        != len(aggregate.restricted_identity_set.speaker_hmac_sha256)
        or not aggregate.restricted_identity_set.session_hmac_sha256
        or any(
            item.face_reversed_bbox_unavailable_count
            or item.lip_reversed_bbox_unavailable_count
            for item in aggregate.samples
        )
    ):
        raise ValueError

    expected_angles = tuple(
        _ts174_angle_aggregate(aggregate, angle=angle)
        for angle in AIHUB538_TRAINING_ANGLES
    )
    if aggregate.angle_aggregates != expected_angles:
        raise ValueError

    expected_selection_sha256 = _ts174_sample_selection_sha256(
        aggregate,
        admission_policy_sha256=AIHUB538_TRAINING_ADMISSION_POLICY_SHA256,
    )
    if aggregate.sample_selection_sha256 != expected_selection_sha256:
        raise ValueError
    if aggregate.admission_manifest_sha256 != _ts174_admission_manifest_sha256(
        aggregate,
        sample_selection_sha256=expected_selection_sha256,
    ):
        raise ValueError
    Aihub538TrainingAdmissionReceipt.issue(aggregate)


def _ts174_sample_selection_sha256(
    aggregate: Aihub538TrainingAdmissionAggregate,
    *,
    admission_policy_sha256: str,
) -> str:
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_training_sample_selection.v1"
            ),
            "selection_rule": aggregate.sample_selection_rule,
            "admission_policy_sha256": admission_policy_sha256,
            "sample_sha256": [
                item.sample_sha256 for item in aggregate.samples
            ],
        }
    )


def _ts174_admission_manifest_sha256(
    aggregate: Aihub538TrainingAdmissionAggregate,
    *,
    sample_selection_sha256: str,
) -> str:
    return sha256_document(
        {
            "schema_version": (
                "voice_coach.aihub538_training_admission_manifest.v1"
            ),
            "label_archive_sha256": aggregate.label_archive_sha256,
            "source_archive_sha256": aggregate.source_archive_sha256,
            "label_content_root_sha256": (
                aggregate.label_zip_content_root_sha256
            ),
            "source_content_root_sha256": (
                aggregate.source_zip_content_root_sha256
            ),
            "sample_selection_sha256": sample_selection_sha256,
            "paired_sample_count": len(aggregate.samples),
            "unique_audio_count": aggregate.unique_audio_count,
            "speaker_count": aggregate.speaker_count,
            "session_count": len(
                aggregate.restricted_identity_set.session_hmac_sha256
            ),
            "identity_namespace_commitment_sha256": (
                aggregate.restricted_identity_set.namespace_commitment_sha256
            ),
            "view_aggregates": [
                item.to_json_object() for item in aggregate.angle_aggregates
            ],
        }
    )


def _ts174_angle_aggregate(
    aggregate: Aihub538TrainingAdmissionAggregate,
    *,
    angle: str,
) -> Aihub538TrainingAngleAggregate:
    samples = tuple(item for item in aggregate.samples if item.angle == angle)
    count = len(samples)
    return Aihub538TrainingAngleAggregate(
        angle=angle,
        paired_sample_count=count,
        label_count=count,
        video_count=count,
        audio_reference_count=count,
        lip_bbox_frame_count=sum(
            item.lip_bbox_frame_count for item in samples
        ),
        sentence_count=sum(item.sentence_count for item in samples),
    )


def _validate_admission_receipt(
    receipt: Aihub538TrainingAdmissionReceipt,
) -> None:
    if not isinstance(receipt, Aihub538TrainingAdmissionReceipt):
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_admission_receipt_invalid"
        )
    try:
        receipt._validate()
    except (TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_actual_media_admission_receipt_invalid"
        ) from None


def _angle_aggregate_from_json_object(
    value: Any,
) -> Aihub538TrainingAngleAggregate:
    if not isinstance(value, dict) or set(value) != {
        "angle",
        "paired_sample_count",
        "label_count",
        "video_count",
        "audio_reference_count",
        "lip_bbox_frame_count",
        "sentence_count",
    }:
        raise ValueError
    return Aihub538TrainingAngleAggregate(
        angle=value["angle"],
        paired_sample_count=value["paired_sample_count"],
        label_count=value["label_count"],
        video_count=value["video_count"],
        audio_reference_count=value["audio_reference_count"],
        lip_bbox_frame_count=value["lip_bbox_frame_count"],
        sentence_count=value["sentence_count"],
    )


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


__all__ = [
    "Aihub538AdmittedArchiveBinding",
    "Aihub538CandidateRoleBinding",
    "Aihub538DescriptorIdentity",
    "Aihub538StableZipDescriptor",
    "assert_aihub538_admitted_archive_binding",
    "assert_aihub538_descriptor_identity",
    "assert_aihub538_stable_zip_descriptor",
    "bind_aihub538_admitted_archive_descriptors",
    "bind_aihub538_immutable_admitted_archive_descriptors",
    "bind_aihub538_prevalidated_immutable_archive_descriptors",
    "bind_aihub538_prevalidated_archive_descriptors",
    "bind_aihub538_legacy_ts174_replay_aggregate",
    "capture_aihub538_descriptor_identity",
    "load_aihub538_training_admission_receipt_descriptor",
    "resolve_aihub538_candidate_role_descriptors",
    "resolve_aihub538_immutable_candidate_role_descriptors",
    "resolve_aihub538_prevalidated_candidate_role_descriptors",
    "revoke_aihub538_admitted_archive_binding",
    "validate_aihub538_stable_zip_descriptor",
    "validate_aihub538_immutable_zip_descriptor",
]
