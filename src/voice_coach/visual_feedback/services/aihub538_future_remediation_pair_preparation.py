"""Prepare the exact feasible TS177+ remediation source prefix for P6 v4."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat

from ..adapters.aihub538_admitted_archive_binding import (
    load_aihub538_training_admission_receipt_descriptor,
)
from ..adapters.aihub538_identity_key_store import load_aihub538_identity_key_descriptor
from ..adapters.aihub538_label_capacity import Aihub538LabelCapacityAdapter
from ..adapters.aihub538_p6_label_preparation import (
    open_aihub538_p6_prepared_label_directory,
)
from ..adapters.aihub538_p6_remediation_publish import (
    load_aihub538_p6_remediation_receipt_descriptor,
)
from ..adapters.aihub538_training_archive import (
    aihub538_archive_security_policy_for_shard,
    validate_zip_descriptor,
)
from ..models.aihub538_label_capacity import (
    AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
)
from ..models.aihub538_photometric_v4_production_policy import (
    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,
    AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS,
)
from ..models.aihub538_training_evidence import (
    aihub538_shard_admission_policy_sha256,
)
from ..models.integrity import sha256_document
from .aihub538_training_pipeline import (
    Aihub538TrainingPipeline,
    _create_run_root,
    _remove_created_run_root,
)


_EXACT_PROTOCOL_SHARDS = AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:5]
_EXACT_PROTOCOL_AUDIT_SHARDS = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2:5]


class Aihub538FuturePairPreparationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class Aihub538FuturePairInput:
    download_directory: Path
    prepared_label_directory: Path


@dataclass(frozen=True, slots=True)
class _StableCandidate:
    descriptor: int
    path: Path
    stat_tuple: tuple[int, int, int, int, int, int, int]
    validation: object
    expected_archive_sha256: str
    expected_content_root_sha256: str


@dataclass(frozen=True, slots=True)
class _StableAdmission:
    descriptor: int
    path: Path
    stat_tuple: tuple[int, int, int, int, int, int, int]
    receipt: object
    receipt_sha256: str


class Aihub538FutureRemediationPairPreparationService:
    """Transactional aggregate-only preparation without locked-data access."""

    def run(
        self,
        *,
        remediation_receipt_descriptor: int,
        identity_key_descriptor: int,
        pairs: tuple[Aihub538FuturePairInput, ...],
        output_directory: Path,
        dataset_access_permission_confirmed: bool,
    ) -> dict[str, object]:
        if dataset_access_permission_confirmed is not True:
            raise Aihub538FuturePairPreparationError(
                "aihub538_future_pair_permission_not_confirmed"
            )
        destination_root_fd: int | None = None
        destination_parent_fd: int | None = None
        destination_root_name: str | None = None
        destination_identity: tuple[int, int] | None = None
        destination_owned = False
        stable_candidates: list[_StableCandidate] = []
        stable_admissions: list[_StableAdmission] = []
        failure_stage = "preflight"
        try:
            receipt = load_aihub538_p6_remediation_receipt_descriptor(
                remediation_receipt_descriptor
            )
            receipt._validate()
            receipt_document = receipt.to_json_object()
            expected_roles = [shard.label_role_component for shard in _EXACT_PROTOCOL_SHARDS]
            expected_label_keys = [shard.ordered_file_keys[0] for shard in _EXACT_PROTOCOL_SHARDS]
            expected_source_roles = [shard.source_role_component for shard in _EXACT_PROTOCOL_SHARDS]
            expected_source_keys = [shard.ordered_file_keys[1] for shard in _EXACT_PROTOCOL_SHARDS]
            if (
                not receipt.is_feasible
                or len(pairs) != len(_EXACT_PROTOCOL_SHARDS)
                or len(receipt.evaluated_prefix_lineage) != len(_EXACT_PROTOCOL_SHARDS)
                or receipt_document.get("authorized_label_role_prefix") != expected_roles
                or receipt_document.get("authorized_label_file_key_prefix") != expected_label_keys
                or receipt_document.get("authorized_source_role_prefix") != expected_source_roles
                or receipt_document.get("authorized_source_file_key_prefix") != expected_source_keys
                or len({item.download_directory.absolute() for item in pairs})
                != len(pairs)
                or len({item.prepared_label_directory.absolute() for item in pairs})
                != len(pairs)
            ):
                raise ValueError
            identity = load_aihub538_identity_key_descriptor(
                identity_key_descriptor
            )
            failure_stage = "output_creation"
            destination = output_directory.absolute()
            (
                destination_root_fd,
                destination_parent_fd,
                destination_root_name,
            ) = _create_run_root(destination)
            destination_owned = True
            root_metadata = os.fstat(destination_root_fd)
            destination_identity = (root_metadata.st_dev, root_metadata.st_ino)
            rows: list[dict[str, object]] = []
            for index, (pair_input, lineage) in enumerate(
                zip(pairs, receipt.evaluated_prefix_lineage, strict=True)
            ):
                shard = _EXACT_PROTOCOL_SHARDS[index]
                audit_shard = _EXACT_PROTOCOL_AUDIT_SHARDS[index]
                failure_stage = f"pair_{index}_lineage"
                if (
                    lineage.label_role_component != shard.label_role_component
                    or lineage.label_file_key != shard.ordered_file_keys[0]
                ):
                    raise ValueError
                shard_output = destination / shard.shard_id
                failure_stage = f"pair_{index}_training_pipeline"
                evidence = Aihub538TrainingPipeline(shard_spec=shard).run_from_completed_directory(
                    input_directory=pair_input.download_directory.absolute(),
                    identity_key_descriptor=identity_key_descriptor,
                    output_directory=shard_output,
                    dataset_access_permission_confirmed=True,
                    capacity_audit_receipt=receipt,
                )
                admission_path = shard_output / "50-dataset-admission.json"
                failure_stage = f"pair_{index}_admission_reload"
                admission_fd = os.open(admission_path, os.O_RDONLY | os.O_NOFOLLOW)
                admission_metadata = os.fstat(admission_fd)
                if (
                    not stat.S_ISREG(admission_metadata.st_mode)
                    or stat.S_IMODE(admission_metadata.st_mode) != 0o400
                    or admission_metadata.st_nlink != 1
                ):
                    os.close(admission_fd)
                    raise ValueError
                admission = load_aihub538_training_admission_receipt_descriptor(
                    admission_fd
                )
                stable_admission = _StableAdmission(
                    descriptor=admission_fd,
                    path=admission_path,
                    stat_tuple=self._stat_tuple(admission_metadata),
                    receipt=admission,
                    receipt_sha256=admission.receipt_sha256,
                )
                stable_admissions.append(stable_admission)
                candidate_fds_list: list[int] = []
                try:
                    failure_stage = f"pair_{index}_candidate_open"
                    for candidate_index in range(2):
                        candidate_path = shard_output / f"candidate-{candidate_index}.zip"
                        os.chmod(candidate_path, 0o400, follow_symlinks=False)
                        candidate_fds_list.append(
                            os.open(candidate_path, os.O_RDONLY | os.O_NOFOLLOW)
                        )
                    candidate_fds = tuple(candidate_fds_list)
                    candidate_identities = tuple(os.fstat(fd) for fd in candidate_fds)
                    if any(
                        not stat.S_ISREG(metadata.st_mode)
                        or stat.S_IMODE(metadata.st_mode) != 0o400
                        or metadata.st_nlink != 1
                        for metadata in candidate_identities
                    ):
                        raise ValueError
                    failure_stage = f"pair_{index}_candidate_validation"
                    validations = tuple(
                        validate_zip_descriptor(
                            fd,
                            policy=aihub538_archive_security_policy_for_shard(shard),
                        )
                        for fd in candidate_fds
                    )
                    label_index = next(
                        i
                        for i, validation in enumerate(validations)
                        if validation.archive_sha256 == admission.label_archive_sha256
                    )
                    source_index = 1 - label_index
                    if (
                        validations[label_index].archive_sha256
                        != admission.label_archive_sha256
                        or validations[label_index].content_root_sha256
                        != admission.label_zip_content_root_sha256
                        or validations[source_index].archive_sha256
                        != admission.source_archive_sha256
                        or validations[source_index].content_root_sha256
                        != admission.source_zip_content_root_sha256
                        or validations[label_index]
                        != evidence.label_zip_validation
                        or validations[source_index]
                        != evidence.source_zip_validation
                    ):
                        raise ValueError
                    label_aggregate = Aihub538LabelCapacityAdapter(
                        shard_spec=shard,
                        archive_policy=aihub538_archive_security_policy_for_shard(shard),
                    ).audit(
                        label_zip_descriptor=candidate_fds[label_index],
                        identity_hash_key=identity.key,
                    )
                    failure_stage = f"pair_{index}_prepared_label_binding"
                    with open_aihub538_p6_prepared_label_directory(
                        pair_input.prepared_label_directory.absolute(),
                        identity_hash_key=identity.key,
                    ) as (_, prepared):
                        if (
                            prepared.shard_id != audit_shard.shard_id
                            or prepared.label_zip_sha256
                            != label_aggregate.label_archive_sha256
                            or prepared.label_zip_content_root_sha256
                            != label_aggregate.label_content_root_sha256
                            or prepared.member_namespace_commitment_sha256
                            != label_aggregate.member_namespace_commitment_sha256
                            or prepared.terminal_frame_tail_policy_sha256
                            != AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
                            or admission.admission_policy_sha256
                            != aihub538_shard_admission_policy_sha256(shard)
                            or admission.label_archive_sha256
                            != lineage.label_archive_sha256
                            or admission.label_zip_content_root_sha256
                            != lineage.label_content_root_sha256
                        ):
                            raise ValueError
                    row_unsigned = {
                        "schema_version": "voice_coach.aihub538_future_pair_handoff.v1",
                        "dataset_id": 538,
                        "shard_id": shard.shard_id,
                        "ordered_file_keys": list(shard.ordered_file_keys),
                        "label_candidate_index": label_index,
                        "source_candidate_index": source_index,
                        "admission_receipt_sha256": admission.receipt_sha256,
                        "admission_policy_sha256": admission.admission_policy_sha256,
                        "label_archive_sha256": admission.label_archive_sha256,
                        "label_content_root_sha256": admission.label_zip_content_root_sha256,
                        "source_archive_sha256": admission.source_archive_sha256,
                        "source_content_root_sha256": admission.source_zip_content_root_sha256,
                        "prepared_label_receipt_sha256": prepared.receipt_sha256,
                        "terminal_frame_tail_policy_sha256": prepared.terminal_frame_tail_policy_sha256,
                        "production_execution_policy_sha256": (
                            AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
                        ),
                        "member_namespace_commitment_sha256": prepared.member_namespace_commitment_sha256,
                        "pipeline_completion_receipt_sha256": evidence.completion_receipt_sha256,
                        "candidate_files": ["candidate-0.zip", "candidate-1.zip"],
                        "admission_receipt_file": "50-dataset-admission.json",
                        "raw_identifiers_serialized": False,
                        "member_names_serialized": False,
                        "source_paths_serialized": False,
                        "locked_partition_accessed": False,
                        "p8_accessed": False,
                        "promotion_authorized": False,
                    }
                    row = {
                        **row_unsigned,
                        "receipt_sha256": sha256_document(row_unsigned),
                    }
                    shard_candidates = tuple(
                        _StableCandidate(
                            descriptor=fd,
                            path=shard_output / f"candidate-{candidate_index}.zip",
                            stat_tuple=self._stat_tuple(metadata),
                            validation=validations[candidate_index],
                            expected_archive_sha256=(
                                admission.label_archive_sha256
                                if candidate_index == label_index
                                else admission.source_archive_sha256
                            ),
                            expected_content_root_sha256=(
                                admission.label_zip_content_root_sha256
                                if candidate_index == label_index
                                else admission.source_zip_content_root_sha256
                            ),
                        )
                        for candidate_index, (fd, metadata) in enumerate(
                            zip(candidate_fds, candidate_identities, strict=True)
                        )
                    )
                    failure_stage = f"pair_{index}_handoff_prewrite_stability"
                    for candidate in shard_candidates:
                        self._assert_candidate_stable(candidate, shard=shard)
                    self._assert_admission_stable(stable_admission)
                    self._write_receipt(shard_output / "70-photometric-v4-handoff.json", row)
                    failure_stage = f"pair_{index}_handoff_postwrite_stability"
                    for candidate in shard_candidates:
                        self._assert_candidate_stable(candidate, shard=shard)
                    self._assert_admission_stable(stable_admission)
                    stable_candidates.extend(shard_candidates)
                    candidate_fds_list.clear()
                    rows.append(row)
                finally:
                    for fd in candidate_fds_list:
                        os.close(fd)
            failure_stage = "prefix_receipt_assembly"
            unsigned = {
                "schema_version": "voice_coach.aihub538_future_pair_prefix.v1",
                "status": "completed_future_remediation_pair_preparation",
                "dataset_id": 538,
                "remediation_receipt_sha256": receipt.receipt_sha256,
                "pair_handoff_receipt_sha256": [row["receipt_sha256"] for row in rows],
                "ordered_shard_ids": [row["shard_id"] for row in rows],
                "identity_namespace_commitment_sha256": receipt.identity_namespace_commitment_sha256,
                "production_execution_policy_sha256": (
                    AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
                ),
                "raw_identifiers_serialized": False,
                "member_names_serialized": False,
                "source_paths_serialized": False,
                "locked_partition_accessed": False,
                "p8_accessed": False,
                "promotion_authorized": False,
            }
            completion = {**unsigned, "receipt_sha256": sha256_document(unsigned)}
            failure_stage = "prefix_prewrite_stability"
            for candidate in stable_candidates:
                shard = next(
                    item
                    for item in _EXACT_PROTOCOL_SHARDS
                    if item.shard_id == candidate.path.parent.name
                )
                self._assert_candidate_stable(candidate, shard=shard)
            for admission in stable_admissions:
                self._assert_admission_stable(admission)
            self._write_receipt(destination / "99-completion.json", completion)
            failure_stage = "prefix_postwrite_stability"
            for candidate in stable_candidates:
                shard = next(
                    item
                    for item in _EXACT_PROTOCOL_SHARDS
                    if item.shard_id == candidate.path.parent.name
                )
                self._assert_candidate_stable(candidate, shard=shard)
            for admission in stable_admissions:
                self._assert_admission_stable(admission)
            destination_owned = False
            return completion
        except Exception as error:
            try:
                if destination_owned:
                    assert destination_root_fd is not None
                    assert destination_parent_fd is not None
                    assert destination_root_name is not None
                    assert destination_identity is not None
                    named = os.stat(
                        destination_root_name,
                        dir_fd=destination_parent_fd,
                        follow_symlinks=False,
                    )
                    if (named.st_dev, named.st_ino) != destination_identity:
                        raise OSError
                    _remove_created_run_root(
                        root_descriptor=destination_root_fd,
                        parent_descriptor=destination_parent_fd,
                        root_name=destination_root_name,
                    )
            except (OSError, RuntimeError):
                raise Aihub538FuturePairPreparationError(
                    "aihub538_future_pair_cleanup_failed"
                ) from None
            if isinstance(error, Aihub538FuturePairPreparationError):
                raise
            raise Aihub538FuturePairPreparationError(
                getattr(
                    error,
                    "reason_code",
                    f"aihub538_future_pair_{failure_stage}_failed",
                )
            ) from None
        finally:
            for candidate in stable_candidates:
                try:
                    os.close(candidate.descriptor)
                except OSError:
                    pass
            for admission in stable_admissions:
                try:
                    os.close(admission.descriptor)
                except OSError:
                    pass
            if destination_root_fd is not None:
                os.close(destination_root_fd)
            if destination_parent_fd is not None:
                os.close(destination_parent_fd)

    @staticmethod
    def _stat_tuple(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_nlink,
        )

    @classmethod
    def _assert_candidate_stable(cls, candidate: _StableCandidate, *, shard) -> None:
        descriptor_metadata = os.fstat(candidate.descriptor)
        named_metadata = os.stat(candidate.path, follow_symlinks=False)
        if (
            cls._stat_tuple(descriptor_metadata) != candidate.stat_tuple
            or cls._stat_tuple(named_metadata) != candidate.stat_tuple
            or not os.path.samestat(descriptor_metadata, named_metadata)
        ):
            raise ValueError
        validation = validate_zip_descriptor(
            candidate.descriptor,
            policy=aihub538_archive_security_policy_for_shard(shard),
        )
        if (
            validation != candidate.validation
            or validation.archive_sha256 != candidate.expected_archive_sha256
            or validation.content_root_sha256 != candidate.expected_content_root_sha256
        ):
            raise ValueError

    @classmethod
    def _assert_admission_stable(cls, admission: _StableAdmission) -> None:
        descriptor_metadata = os.fstat(admission.descriptor)
        named_metadata = os.stat(admission.path, follow_symlinks=False)
        if (
            cls._stat_tuple(descriptor_metadata) != admission.stat_tuple
            or cls._stat_tuple(named_metadata) != admission.stat_tuple
            or not os.path.samestat(descriptor_metadata, named_metadata)
        ):
            raise ValueError
        reloaded = load_aihub538_training_admission_receipt_descriptor(
            admission.descriptor
        )
        if (
            reloaded != admission.receipt
            or reloaded.receipt_sha256 != admission.receipt_sha256
        ):
            raise ValueError

    @staticmethod
    def _write_receipt(path: Path, document: dict[str, object]) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
            os.fchmod(fd, 0o400)
            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)


__all__ = [
    "Aihub538FuturePairInput",
    "Aihub538FuturePairPreparationError",
    "Aihub538FutureRemediationPairPreparationService",
]
