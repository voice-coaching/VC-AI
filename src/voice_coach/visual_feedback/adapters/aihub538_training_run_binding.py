"""Strict 0700/0600/0400 binding for completed AI Hub 538 pair runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
from typing import Iterator
from weakref import WeakKeyDictionary, ref

from ..models.aihub538_label_selection_authorization import (
    Aihub538LabelSelectionAuthorizationReceipt,
)
from ..models.aihub538_multishard_admission import (
    Aihub538AuthorizedShardAdmission,
)
from ..models.aihub538_nonlocked_shards import (
    AIHUB538_TS174_TL174_SHARD,
    Aihub538NonlockedShardSpec,
    require_aihub538_nonlocked_shard_spec,
)
from ..models.aihub538_training_evidence import (
    Aihub538TrainingAdmissionAggregate,
    Aihub538TrainingAdmissionReceipt,
    Aihub538TrainingEvaluatorSmokeReceipt,
)
from ..models.integrity import sha256_document
from ..services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
)
from ..services.aihub538_training_pipeline import (
    Aihub538SemanticRoleReceipt,
    Aihub538TrainingPipelineEvidence,
)
from ..services.aihub538_training_smoke import Aihub538TrainingSmokeEvidence
from .aihub538_admitted_archive_binding import (
    Aihub538AdmittedArchiveBinding,
    Aihub538CandidateRoleBinding,
    Aihub538DescriptorIdentity,
    assert_aihub538_admitted_archive_binding,
    assert_aihub538_descriptor_identity,
    bind_aihub538_prevalidated_archive_descriptors,
    capture_aihub538_descriptor_identity,
    load_aihub538_training_admission_receipt_descriptor,
    resolve_aihub538_prevalidated_candidate_role_descriptors,
    revoke_aihub538_admitted_archive_binding,
    validate_aihub538_stable_zip_descriptor,
)
from ..aihub538_capability_lifetime import (
    AIHUB538_CAPABILITY_LIFETIME_LOCK,
)
from .aihub538_training_dataset import (
    Aihub538PrevalidatedTrainingAdmission,
    Aihub538TrainingAdmissionError,
    revoke_aihub538_prevalidated_training_admissions,
)
from .aihub538_split_tar_archive import (
    InnerTarZipRepackReceipt,
    SplitTarMergeReceipt,
)
from .aihub538_training_archive import (
    SplitZipMergeReceipt,
    TarExtractionReceipt,
    VerifiedAihub538Download,
    VerifiedAihub538NonlockedShardDownload,
    ZipValidationReceipt,
    aihub538_archive_security_policy_for_shard,
)


_BASE_COMPLETION_KEYS = {
    "aggregate_only",
    "completion_receipt_sha256",
    "dataset_admission",
    "dataset_id",
    "dataset_partition",
    "download_verification",
    "existing_output_overwrite_allowed",
    "failure_cleanup_policy",
    "fsync_completed",
    "inner_tar_repack",
    "locked_partition_accessed",
    "merged_archive_mode",
    "metadata_smoke",
    "outer_tar_extraction",
    "output_directory_mode",
    "output_directory_was_new",
    "partial_download_accessed",
    "participant_identifiers_serialized",
    "promotion_authorized",
    "raw_member_names_serialized",
    "receipt_mode",
    "schema_version",
    "semantic_role_classification",
    "source_paths_serialized",
    "split_archive_merge",
    "split_zip_merge",
    "status",
    "transcripts_serialized",
    "zip_validation",
}
_PAIR_COMPLETION_KEYS = _BASE_COMPLETION_KEYS | {
    "shard_id",
    "shard_spec_sha256",
    "capacity_audit_receipt_sha256",
    "capacity_audit_precondition_satisfied",
}
_LEGACY_BASE_COMPLETION_KEYS = _BASE_COMPLETION_KEYS - {
    "split_archive_merge",
    "inner_tar_repack",
}
_HISTORICAL_V1 = "historical_ts174_v1"
_TRANSITIONAL_V1 = "transitional_ts174_expanded_v1"
_CURRENT_BASELINE_V2 = "current_ts174_v2"
_CURRENT_PAIR_V3 = "current_ladder_pair_v3"
_COMPLETION_SAFETY_CONSTANTS = {
    "status": "completed_nonlocked_training_metadata_smoke",
    "dataset_id": 538,
    "partial_download_accessed": False,
    "locked_partition_accessed": False,
    "raw_member_names_serialized": False,
    "source_paths_serialized": False,
    "transcripts_serialized": False,
    "participant_identifiers_serialized": False,
    "output_directory_was_new": True,
    "output_directory_mode": "0700",
    "merged_archive_mode": "0600",
    "receipt_mode": "0400",
    "fsync_completed": True,
    "failure_cleanup_policy": (
        "remove_only_fresh_run_root_then_retry_same_absent_path"
    ),
    "existing_output_overwrite_allowed": False,
    "aggregate_only": True,
    "promotion_authorized": False,
}


class _AuthorizedRunCapabilityToken:
    """Opaque process-local authorization lifetime token."""

    __slots__ = ("__weakref__",)


_AUTHORIZED_RUN_CAPABILITIES: WeakKeyDictionary[
    _AuthorizedRunCapabilityToken,
    tuple[object, ...],
] = WeakKeyDictionary()
_RUN_OPEN = "OPEN"
_RUN_CONSUMING = "CONSUMING"
_RUN_CONSUMED = "CONSUMED"
_RUN_CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True, weakref_slot=True)
class Aihub538AuthorizedTrainingRunBinding:
    """Open descriptors plus the exact receipt/completion authorization."""

    shard_spec: Aihub538NonlockedShardSpec
    candidate_role_binding: Aihub538CandidateRoleBinding
    admission_receipt: Aihub538TrainingAdmissionReceipt
    pipeline_completion_receipt_sha256: str
    capacity_audit_receipt_sha256: str | None
    admitted_archive_binding: Aihub538AdmittedArchiveBinding = field(repr=False)
    _capability: _AuthorizedRunCapabilityToken = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not _authorized_run_capability_matches(self):
            raise ValueError("aihub538_authorized_training_run_capability_invalid")

    def authorize(
        self,
        admission: Aihub538PrevalidatedTrainingAdmission,
    ) -> Aihub538AuthorizedShardAdmission:
        return Aihub538AuthorizedShardAdmission._issue_live(
            owner_binding=self,
            prevalidated_admission=admission,
        )


def _authorized_run_capability_matches(
    binding: Aihub538AuthorizedTrainingRunBinding,
    *,
    allowed_states: tuple[str, ...] = (
        _RUN_OPEN,
        _RUN_CONSUMING,
        _RUN_CONSUMED,
    ),
) -> bool:
    with AIHUB538_CAPABILITY_LIFETIME_LOCK:
        try:
            record = _AUTHORIZED_RUN_CAPABILITIES.get(binding._capability)
        except (AttributeError, TypeError):
            return False
        return bool(
            record is not None
            and record[0] == os.getpid()
            and record[1] is binding.shard_spec
            and record[2] is binding.candidate_role_binding
            and record[3] is binding.admission_receipt
            and record[4] == binding.pipeline_completion_receipt_sha256
            and record[5] == binding.capacity_audit_receipt_sha256
            and record[6] is binding.admitted_archive_binding
            and (record[7] is None or record[7]() is binding)
            and record[12] in allowed_states
        )


def _assert_aihub538_authorized_training_run(
    binding: Aihub538AuthorizedTrainingRunBinding,
    *,
    admission: Aihub538PrevalidatedTrainingAdmission,
) -> Aihub538TrainingAdmissionAggregate:
    try:
        record = _AUTHORIZED_RUN_CAPABILITIES.get(binding._capability)
    except (AttributeError, TypeError):
        record = None
    if not _authorized_run_capability_matches(binding) or record is None:
        raise Aihub538DecisionCalibrationError(
            "aihub538_authorized_training_run_capability_invalid"
        )
    if type(admission) is not Aihub538PrevalidatedTrainingAdmission:
        raise Aihub538DecisionCalibrationError(
            "aihub538_prevalidated_aggregate_capability_invalid"
        )
    policy = aihub538_archive_security_policy_for_shard(binding.shard_spec)
    try:
        binding.admission_receipt._validate()
        assert_aihub538_admitted_archive_binding(
            binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            policy=policy,
        )
        role = binding.candidate_role_binding
        admitted = binding.admitted_archive_binding
        if (
            sha256_document(role._unsigned_json_object())
            != role.receipt_sha256
            or role.label_zip_descriptor != admitted.label.descriptor
            or role.source_zip_descriptor != admitted.source.descriptor
            or role.label_archive_sha256
            != binding.admission_receipt.label_archive_sha256
            or role.source_archive_sha256
            != binding.admission_receipt.source_archive_sha256
            or role.label_zip_content_root_sha256
            != binding.admission_receipt.label_zip_content_root_sha256
            or role.source_zip_content_root_sha256
            != binding.admission_receipt.source_zip_content_root_sha256
            or role.admission_receipt_sha256
            != binding.admission_receipt.receipt_sha256
            or role.shard_id != binding.shard_spec.shard_id
            or role.dataset_partition != binding.shard_spec.dataset_partition
        ):
            raise ValueError
        _require_exact_pipeline_completion_reissue(
            completion=record[8],
            original_completion=record[9],
            completion_variant=record[10],
            admission=binding.admission_receipt,
            shard_spec=binding.shard_spec,
            capacity_receipt_sha256=(
                binding.capacity_audit_receipt_sha256
            ),
            candidate_validations=record[11],
        )
        aggregate = admission.assert_authorizable(
            archive_binding=binding.admitted_archive_binding,
            admission_receipt=binding.admission_receipt,
            archive_policy=policy,
        )
    except Aihub538DecisionCalibrationError:
        raise
    except Aihub538TrainingAdmissionError as error:
        raise Aihub538DecisionCalibrationError(error.reason_code) from None
    except (AttributeError, KeyError, TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_authorized_training_run_capability_invalid"
        ) from None
    return aggregate


@contextmanager
def _aihub538_authorized_shard_issuance_transaction(
    *,
    owner_binding: object,
    prevalidated_admission: object,
) -> Iterator[
    tuple[
        Aihub538TrainingAdmissionAggregate,
        Aihub538TrainingAdmissionReceipt,
        str,
        str | None,
        object,
        Aihub538AuthorizedTrainingRunBinding,
    ]
]:
    """Atomically seal exactly one authorization before context revocation."""

    if type(owner_binding) is not Aihub538AuthorizedTrainingRunBinding:
        raise Aihub538DecisionCalibrationError(
            "aihub538_authorized_training_run_capability_invalid"
        )
    if type(prevalidated_admission) is not Aihub538PrevalidatedTrainingAdmission:
        raise Aihub538DecisionCalibrationError(
            "aihub538_prevalidated_aggregate_capability_invalid"
        )
    with AIHUB538_CAPABILITY_LIFETIME_LOCK:
        record = _AUTHORIZED_RUN_CAPABILITIES.get(owner_binding._capability)
        if (
            not _authorized_run_capability_matches(
                owner_binding,
                allowed_states=(_RUN_OPEN,),
            )
            or record is None
        ):
            if record is not None and _authorized_run_capability_matches(
                owner_binding,
                allowed_states=(_RUN_CONSUMING, _RUN_CONSUMED),
            ):
                raise Aihub538DecisionCalibrationError(
                    "aihub538_authorized_training_run_"
                    "authorization_already_consumed"
                )
            raise Aihub538DecisionCalibrationError(
                "aihub538_authorized_training_run_capability_invalid"
            )
        aggregate = _assert_aihub538_authorized_training_run(
            owner_binding,
            admission=prevalidated_admission,
        )
        _AUTHORIZED_RUN_CAPABILITIES[owner_binding._capability] = (
            *record[:12],
            _RUN_CONSUMING,
        )
        try:
            yield (
                aggregate,
                owner_binding.admission_receipt,
                owner_binding.pipeline_completion_receipt_sha256,
                owner_binding.capacity_audit_receipt_sha256,
                owner_binding._capability,
                owner_binding,
            )
        except BaseException:
            current = _AUTHORIZED_RUN_CAPABILITIES.get(
                owner_binding._capability
            )
            if current is not None and current[12] == _RUN_CONSUMING:
                _AUTHORIZED_RUN_CAPABILITIES[owner_binding._capability] = (
                    *current[:12],
                    _RUN_OPEN,
                )
            raise
        else:
            current = _AUTHORIZED_RUN_CAPABILITIES.get(
                owner_binding._capability
            )
            if current is None or current[12] != _RUN_CONSUMING:
                raise Aihub538DecisionCalibrationError(
                    "aihub538_authorized_training_run_capability_invalid"
                )
            _AUTHORIZED_RUN_CAPABILITIES[owner_binding._capability] = (
                *current[:12],
                _RUN_CONSUMED,
            )


def _assert_aihub538_authorization_owner_live(
    *,
    owner_binding: object,
    owner_context: object,
    allow_consuming: bool,
) -> None:
    with AIHUB538_CAPABILITY_LIFETIME_LOCK:
        allowed_states = (
            (_RUN_CONSUMING, _RUN_CONSUMED)
            if allow_consuming
            else (_RUN_CONSUMED,)
        )
        if (
            type(owner_binding) is not Aihub538AuthorizedTrainingRunBinding
            or owner_binding._capability is not owner_context
            or not _authorized_run_capability_matches(
                owner_binding,
                allowed_states=allowed_states,
            )
        ):
            raise Aihub538DecisionCalibrationError(
                "aihub538_authorized_training_run_capability_invalid"
            )


def _revoke_aihub538_authorized_training_run(
    binding: Aihub538AuthorizedTrainingRunBinding,
) -> None:
    with AIHUB538_CAPABILITY_LIFETIME_LOCK:
        if type(binding) is Aihub538AuthorizedTrainingRunBinding:
            record = _AUTHORIZED_RUN_CAPABILITIES.get(binding._capability)
            if record is not None:
                _AUTHORIZED_RUN_CAPABILITIES[binding._capability] = (
                    *record[:12],
                    _RUN_CLOSED,
                )
            Aihub538AuthorizedShardAdmission._revoke_owner_context(
                binding._capability
            )


@contextmanager
def open_aihub538_authorized_training_run(
    *,
    directory: Path,
    shard_spec: Aihub538NonlockedShardSpec,
    capacity_receipt: Aihub538LabelSelectionAuthorizationReceipt | None,
) -> Iterator[Aihub538AuthorizedTrainingRunBinding]:
    """Open only exact pipeline artifacts and keep every descriptor stable."""

    try:
        spec = require_aihub538_nonlocked_shard_spec(
            shard_spec,
            require_pair=True,
        )
    except ValueError as error:
        raise Aihub538DecisionCalibrationError(str(error)) from None
    is_baseline = spec == AIHUB538_TS174_TL174_SHARD
    archive_policy = aihub538_archive_security_policy_for_shard(spec)
    if is_baseline != (capacity_receipt is None):
        raise Aihub538DecisionCalibrationError(
            "aihub538_training_run_capacity_binding_invalid"
        )
    if capacity_receipt is not None:
        try:
            capacity_receipt._validate()
        except ValueError:
            raise Aihub538DecisionCalibrationError(
                "aihub538_training_run_capacity_binding_invalid"
            ) from None
        if not capacity_receipt.is_feasible:
            raise Aihub538DecisionCalibrationError(
                "aihub538_training_run_capacity_binding_invalid"
            )

    descriptors: list[int] = []
    identities: list[tuple[int, Aihub538DescriptorIdentity]] = []
    bound: Aihub538AuthorizedTrainingRunBinding | None = None
    admitted_archive_binding: Aihub538AdmittedArchiveBinding | None = None
    try:
        absolute = directory.absolute()
        if not absolute.is_absolute() or absolute.resolve(strict=True) != absolute:
            raise OSError
        directory_descriptor = os.open(
            absolute,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        descriptors.append(directory_descriptor)
        directory_metadata = os.fstat(directory_descriptor)
        if stat.S_IMODE(directory_metadata.st_mode) != 0o700:
            raise OSError
        for name, mode in (
            ("candidate-0.zip", 0o600),
            ("candidate-1.zip", 0o600),
            ("50-dataset-admission.json", 0o400),
            ("99-completion.json", 0o400),
        ):
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            descriptors.append(descriptor)
            identities.append(
                (
                    descriptor,
                    capture_aihub538_descriptor_identity(
                        descriptor,
                        require_mode=mode,
                    ),
                )
            )
        candidate_0, candidate_1, admission_descriptor, completion_descriptor = (
            descriptors[1:]
        )
        admission = load_aihub538_training_admission_receipt_descriptor(
            admission_descriptor
        )
        if admission.shard_id != spec.shard_id:
            raise ValueError
        completion = _load_completion(completion_descriptor)
        completion_variant = _completion_variant(
            completion=completion,
            is_baseline=is_baseline,
        )
        expected_keys = (
            _LEGACY_BASE_COMPLETION_KEYS
            if completion_variant == _HISTORICAL_V1
            else _BASE_COMPLETION_KEYS
            if is_baseline
            else _PAIR_COMPLETION_KEYS
        )
        if (
            set(completion) != expected_keys
            or completion["dataset_partition"] != spec.dataset_partition
            or completion["dataset_admission"] != admission.to_json_object()
            or any(
                completion.get(name) != value
                for name, value in _COMPLETION_SAFETY_CONSTANTS.items()
            )
        ):
            raise ValueError
        capacity_sha256: str | None = None
        if not is_baseline:
            assert capacity_receipt is not None
            capacity_sha256 = capacity_receipt.receipt_sha256
            if (
                completion["shard_id"] != spec.shard_id
                or completion["shard_spec_sha256"] != spec.spec_sha256
                or completion["capacity_audit_receipt_sha256"]
                != capacity_sha256
                or completion["capacity_audit_precondition_satisfied"] is not True
            ):
                raise ValueError

        normalized_completion = _normalize_completion_for_current_reissue(
            completion=completion,
            completion_variant=completion_variant,
        )
        archive_merge = normalized_completion.get("split_archive_merge")
        if not isinstance(archive_merge, dict):
            raise ValueError
        archive_kinds = archive_merge.get("archive_kinds")
        if archive_kinds == ["zip"]:
            merge_name = "20-split-zip-merge.json"
        elif archive_kinds == ["inner_posix_tar"]:
            merge_name = "20-split-archive-merge.json"
        else:
            raise ValueError
        merge_completion_key = (
            "split_zip_merge"
            if completion_variant == _HISTORICAL_V1
            else "split_archive_merge"
        )
        stage_names = (
            ("00-download-verification.json", "download_verification"),
            ("10-outer-tar-extraction.json", "outer_tar_extraction"),
            (merge_name, merge_completion_key),
            ("30-zip-validation.json", "zip_validation"),
            ("40-semantic-role-classification.json", "semantic_role_classification"),
            ("60-metadata-smoke.json", "metadata_smoke"),
        )
        for stage_name, completion_key in stage_names:
            stage_descriptor = os.open(
                stage_name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            descriptors.append(stage_descriptor)
            stage_identity = capture_aihub538_descriptor_identity(
                stage_descriptor,
                require_mode=0o400,
            )
            identities.append((stage_descriptor, stage_identity))
            if _load_json_descriptor(stage_descriptor) != completion[completion_key]:
                raise ValueError
        repack = normalized_completion.get("inner_tar_repack")
        if not isinstance(repack, dict):
            raise ValueError
        if "inner_posix_tar" in archive_kinds:
            repack_descriptor = os.open(
                "25-inner-tar-repack.json",
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            descriptors.append(repack_descriptor)
            repack_identity = capture_aihub538_descriptor_identity(
                repack_descriptor,
                require_mode=0o400,
            )
            identities.append((repack_descriptor, repack_identity))
            if _load_json_descriptor(repack_descriptor) != repack:
                raise ValueError

        candidate_validations = (
            validate_aihub538_stable_zip_descriptor(
                candidate_0,
                policy=archive_policy,
            ),
            validate_aihub538_stable_zip_descriptor(
                candidate_1,
                policy=archive_policy,
            ),
        )
        role_binding = resolve_aihub538_prevalidated_candidate_role_descriptors(
            candidates=candidate_validations,
            admission_receipt=admission,
            policy=archive_policy,
        )
        admitted_archive_binding = (
            bind_aihub538_prevalidated_archive_descriptors(
                label=candidate_validations[role_binding.label_candidate_index],
                source=candidate_validations[role_binding.source_candidate_index],
                admission_receipt=admission,
                policy=archive_policy,
            )
        )
        _require_exact_pipeline_completion_reissue(
            completion=normalized_completion,
            original_completion=completion,
            completion_variant=completion_variant,
            admission=admission,
            shard_spec=spec,
            capacity_receipt_sha256=capacity_sha256,
            candidate_validations=candidate_validations,
        )
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            capability = _AuthorizedRunCapabilityToken()
            run_record = (
                os.getpid(),
                spec,
                role_binding,
                admission,
                completion["completion_receipt_sha256"],
                capacity_sha256,
                admitted_archive_binding,
                None,
                normalized_completion,
                completion,
                completion_variant,
                candidate_validations,
                _RUN_OPEN,
            )
            _AUTHORIZED_RUN_CAPABILITIES[capability] = run_record
            try:
                bound = Aihub538AuthorizedTrainingRunBinding(
                    shard_spec=spec,
                    candidate_role_binding=role_binding,
                    admission_receipt=admission,
                    pipeline_completion_receipt_sha256=(
                        completion["completion_receipt_sha256"]
                    ),
                    capacity_audit_receipt_sha256=capacity_sha256,
                    admitted_archive_binding=admitted_archive_binding,
                    _capability=capability,
                )
                _AUTHORIZED_RUN_CAPABILITIES[capability] = (
                    *run_record[:7],
                    ref(bound),
                    *run_record[8:],
                )
            except BaseException:
                _AUTHORIZED_RUN_CAPABILITIES.pop(capability, None)
                raise
        yield bound
        for descriptor, identity in identities:
            assert_aihub538_descriptor_identity(descriptor, identity)
    except Aihub538DecisionCalibrationError:
        raise
    except (KeyError, OSError, TypeError, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_authorized_training_run_invalid"
        ) from None
    finally:
        with AIHUB538_CAPABILITY_LIFETIME_LOCK:
            if bound is not None:
                _revoke_aihub538_authorized_training_run(bound)
            if admitted_archive_binding is not None:
                revoke_aihub538_prevalidated_training_admissions(
                    admitted_archive_binding
                )
                revoke_aihub538_admitted_archive_binding(
                    admitted_archive_binding
                )
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _load_completion(descriptor: int) -> dict[str, object]:
    document = _load_json_descriptor(descriptor)
    unsigned = dict(document)
    digest = unsigned.pop("completion_receipt_sha256", None)
    if not isinstance(digest, str) or sha256_document(unsigned) != digest:
        raise ValueError
    return document


def _load_json_descriptor(descriptor: int) -> dict[str, object]:
    before = capture_aihub538_descriptor_identity(
        descriptor,
        require_mode=0o400,
    )
    if before.size_bytes > 8 * 1024 * 1024:
        raise ValueError
    payload = bytearray()
    offset = 0
    while offset < before.size_bytes:
        chunk = os.pread(
            descriptor,
            min(64 * 1024, before.size_bytes - offset),
            offset,
        )
        if not chunk:
            raise ValueError
        payload.extend(chunk)
        offset += len(chunk)
    assert_aihub538_descriptor_identity(descriptor, before)
    document = json.loads(
        bytes(payload).decode("utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if not isinstance(document, dict):
        raise ValueError
    return document


def _completion_variant(
    *,
    completion: dict[str, object],
    is_baseline: bool,
) -> str:
    schema = completion.get("schema_version")
    keys = set(completion)
    if is_baseline:
        if (
            schema == "voice_coach.aihub538_training_pipeline_receipt.v1"
            and keys == _LEGACY_BASE_COMPLETION_KEYS
        ):
            return _HISTORICAL_V1
        if (
            schema == "voice_coach.aihub538_training_pipeline_receipt.v1"
            and keys == _BASE_COMPLETION_KEYS
        ):
            return _TRANSITIONAL_V1
        if (
            schema == "voice_coach.aihub538_training_pipeline_receipt.v2"
            and keys == _BASE_COMPLETION_KEYS
        ):
            return _CURRENT_BASELINE_V2
    elif (
        schema == "voice_coach.aihub538_training_pipeline_receipt.v3"
        and keys == _PAIR_COMPLETION_KEYS
    ):
        return _CURRENT_PAIR_V3
    raise ValueError


def _normalize_completion_for_current_reissue(
    *,
    completion: dict[str, object],
    completion_variant: str,
) -> dict[str, object]:
    normalized = dict(completion)
    if completion_variant != _HISTORICAL_V1:
        return normalized
    split_zip = _mapping(completion["split_zip_merge"])
    archives = _sequence(split_zip["archives"])
    normalized["split_archive_merge"] = {
        "schema_version": "voice_coach.aihub538_split_archive_merge_stage.v2",
        "archive_count": len(archives),
        "archives": archives,
        "archive_kinds": ["zip"],
        "byte_offset_suffix_semantics": True,
        "role_assignment_deferred_until_semantic_probe": True,
        "outer_member_names_serialized": False,
        "aggregate_only": True,
    }
    normalized["inner_tar_repack"] = {
        "schema_version": "voice_coach.aihub538_inner_tar_repack_stage.v1",
        "status": "not_applicable_split_zip_input",
        "archive_count": 0,
        "archives": [],
        "raw_member_names_serialized": False,
        "aggregate_only": True,
    }
    return normalized


def _require_exact_pipeline_completion_reissue(
    *,
    completion: dict[str, object],
    original_completion: dict[str, object],
    completion_variant: str,
    admission: Aihub538TrainingAdmissionReceipt,
    shard_spec: Aihub538NonlockedShardSpec,
    capacity_receipt_sha256: str | None,
    candidate_validations: tuple[object, object],
) -> None:
    """Typed-reload every nested stage and exactly reissue completion."""

    download_document = _mapping(completion["download_verification"])
    if shard_spec == AIHUB538_TS174_TL174_SHARD:
        download = VerifiedAihub538Download(
            archive_size_bytes=_integer(download_document["archive_size_bytes"]),
            archive_sha256=_text(download_document["archive_sha256"]),
            receipt_sha256=_text(
                download_document["secure_download_receipt_sha256"]
            ),
        )
    else:
        download = VerifiedAihub538NonlockedShardDownload(
            shard_spec=shard_spec,
            archive_size_bytes=_integer(download_document["archive_size_bytes"]),
            archive_sha256=_text(download_document["archive_sha256"]),
            receipt_sha256=_text(
                download_document["secure_download_receipt_sha256"]
            ),
        )
    if download.to_json_object() != download_document:
        raise ValueError

    tar_document = _mapping(completion["outer_tar_extraction"])
    tar_extraction = TarExtractionReceipt(
        archive_sha256=_text(tar_document["archive_sha256"]),
        regular_file_count=_integer(tar_document["regular_file_count"]),
        directory_count=_integer(tar_document["directory_count"]),
        uncompressed_bytes=_integer(tar_document["uncompressed_bytes"]),
        content_root_sha256=_text(tar_document["content_root_sha256"]),
    )
    if (
        tar_extraction.to_json_object() != tar_document
        or tar_extraction.archive_sha256 != download.archive_sha256
    ):
        raise ValueError

    split_merge_receipts = tuple(
        _load_split_merge_receipt(item)
        for item in _sequence(
            _mapping(completion["split_archive_merge"])["archives"]
        )
    )
    if len(split_merge_receipts) != 2:
        raise ValueError
    repack_receipts = tuple(
        _load_repack_receipt(item)
        for item in _sequence(
            _mapping(completion["inner_tar_repack"])["archives"]
        )
    )
    validation_receipts = tuple(
        _load_zip_validation_receipt(item)
        for item in _sequence(
            _mapping(completion["zip_validation"])["archives"]
        )
    )
    if len(validation_receipts) != 2:
        raise ValueError

    actual_validations = tuple(
        item.validation_receipt for item in candidate_validations
    )
    if set(validation_receipts) != set(actual_validations):
        raise ValueError
    tar_merges = tuple(
        item
        for item in split_merge_receipts
        if isinstance(item, SplitTarMergeReceipt)
    )
    zip_merges = tuple(
        item
        for item in split_merge_receipts
        if isinstance(item, SplitZipMergeReceipt)
    )
    if (
        len(tar_merges) + len(zip_merges) != 2
        or bool(tar_merges) == bool(zip_merges)
        or len(repack_receipts) != len(tar_merges)
        or {item.merged_sha256 for item in tar_merges}
        != {item.source_tar_sha256 for item in repack_receipts}
    ):
        raise ValueError
    materialized_zip_sha256 = {
        item.merged_sha256 for item in zip_merges
    } | {
        item.repacked_zip_sha256 for item in repack_receipts
    }
    if materialized_zip_sha256 != {
        admission.label_archive_sha256,
        admission.source_archive_sha256,
    }:
        raise ValueError
    validation_by_sha = {
        item.archive_sha256: item for item in validation_receipts
    }
    if len(validation_by_sha) != 2:
        raise ValueError
    for repack_receipt in repack_receipts:
        validation = validation_by_sha.get(repack_receipt.repacked_zip_sha256)
        if (
            validation is None
            or validation.content_root_sha256
            != repack_receipt.repacked_zip_content_root_sha256
        ):
            raise ValueError
    label_validation = next(
        item
        for item in validation_receipts
        if item.archive_sha256 == admission.label_archive_sha256
    )
    source_validation = next(
        item
        for item in validation_receipts
        if item.archive_sha256 == admission.source_archive_sha256
    )
    if (
        label_validation.content_root_sha256
        != admission.label_zip_content_root_sha256
        or source_validation.content_root_sha256
        != admission.source_zip_content_root_sha256
    ):
        raise ValueError

    semantic_document = _mapping(completion["semantic_role_classification"])
    semantic = Aihub538SemanticRoleReceipt(
        label_member_count=_integer(semantic_document["label_member_count"]),
        source_member_count=_integer(semantic_document["source_member_count"]),
        source_mp4_count=_integer(semantic_document["source_mp4_count"]),
        source_wav_count=_integer(semantic_document["source_wav_count"]),
        shard_id=shard_spec.shard_id,
        dataset_partition=shard_spec.dataset_partition,
    )
    if (
        semantic.to_json_object() != semantic_document
        or semantic.label_member_count != admission.label_count
        or semantic.source_mp4_count != admission.video_count
        or semantic.source_wav_count != admission.unique_audio_count
        or semantic.source_member_count
        != semantic.source_mp4_count + semantic.source_wav_count
    ):
        raise ValueError

    smoke_document = _mapping(completion["metadata_smoke"])
    smoke = Aihub538TrainingEvaluatorSmokeReceipt(
        admission_receipt_sha256=_text(
            smoke_document["admission_receipt_sha256"]
        ),
        paired_sample_count=_integer(smoke_document["paired_sample_count"]),
        evaluated_sample_count=_integer(
            smoke_document["evaluated_sample_count"]
        ),
        maximum_audio_video_duration_delta_seconds=_number(
            smoke_document["maximum_audio_video_duration_delta_seconds"]
        ),
        maximum_bbox_frame_delta=_integer(
            smoke_document["maximum_bbox_frame_delta"]
        ),
        total_lip_bbox_frame_count=_integer(
            smoke_document["total_lip_bbox_frame_count"]
        ),
        total_sentence_count=_integer(smoke_document["total_sentence_count"]),
        sample_selection_sha256=_text(
            smoke_document["sample_selection_sha256"]
        ),
        receipt_sha256=_text(smoke_document["receipt_sha256"]),
        shard_id=shard_spec.shard_id,
        dataset_partition=shard_spec.dataset_partition,
    )
    smoke._validate()
    if (
        smoke.to_json_object() != smoke_document
        or smoke.admission_receipt_sha256 != admission.receipt_sha256
        or smoke.sample_selection_sha256 != admission.sample_selection_sha256
        or smoke.paired_sample_count != admission.paired_sample_count
        or smoke.evaluated_sample_count != admission.paired_sample_count
        or smoke.total_lip_bbox_frame_count != admission.lip_bbox_frame_count
        or smoke.total_sentence_count != admission.sentence_count
    ):
        raise ValueError

    reissued = Aihub538TrainingPipelineEvidence.issue(
        download_verification=download,
        tar_extraction=tar_extraction,
        split_merge_receipts=(
            split_merge_receipts[0],
            split_merge_receipts[1],
        ),
        inner_tar_repack_receipts=repack_receipts,
        label_zip_validation=label_validation,
        source_zip_validation=source_validation,
        semantic_role_receipt=semantic,
        smoke_evidence=Aihub538TrainingSmokeEvidence(
            admission_receipt=admission,
            evaluator_smoke_receipt=smoke,
        ),
        shard_spec=shard_spec,
        capacity_audit_receipt_sha256=capacity_receipt_sha256,
    )
    if (
        _project_current_completion_to_variant(
            document=reissued.to_json_object(),
            completion_variant=completion_variant,
        )
        != original_completion
    ):
        raise ValueError


def _project_current_completion_to_variant(
    *,
    document: dict[str, object],
    completion_variant: str,
) -> dict[str, object]:
    if completion_variant in {_CURRENT_BASELINE_V2, _CURRENT_PAIR_V3}:
        return document
    projected = dict(document)
    projected.pop("completion_receipt_sha256")
    projected["schema_version"] = (
        "voice_coach.aihub538_training_pipeline_receipt.v1"
    )
    if completion_variant == _HISTORICAL_V1:
        projected.pop("split_archive_merge")
        projected.pop("inner_tar_repack")
    elif completion_variant != _TRANSITIONAL_V1:
        raise ValueError
    projected["completion_receipt_sha256"] = sha256_document(projected)
    return projected


def _load_split_merge_receipt(value: object) -> SplitZipMergeReceipt | SplitTarMergeReceipt:
    document = _mapping(value)
    schema = document.get("schema_version")
    if schema == "voice_coach.aihub538_split_zip_merge_receipt.v2":
        receipt: SplitZipMergeReceipt | SplitTarMergeReceipt = SplitZipMergeReceipt(
            part_count=_integer(document["part_count"]),
            last_part_offset_bytes=_integer(document["last_part_offset_bytes"]),
            merged_bytes=_integer(document["merged_bytes"]),
            merged_sha256=_text(document["merged_sha256"]),
            ordered_part_root_sha256=_text(
                document["ordered_part_root_sha256"]
            ),
        )
    elif schema == "voice_coach.aihub538_split_tar_merge_receipt.v1":
        receipt = SplitTarMergeReceipt(
            part_count=_integer(document["part_count"]),
            first_part_offset_bytes=_integer(
                document["first_part_offset_bytes"]
            ),
            last_part_offset_bytes=_integer(document["last_part_offset_bytes"]),
            merged_bytes=_integer(document["merged_bytes"]),
            merged_sha256=_text(document["merged_sha256"]),
            ordered_part_root_sha256=_text(
                document["ordered_part_root_sha256"]
            ),
        )
    else:
        raise ValueError
    if receipt.to_json_object() != document:
        raise ValueError
    return receipt


def _load_repack_receipt(value: object) -> InnerTarZipRepackReceipt:
    document = _mapping(value)
    receipt = InnerTarZipRepackReceipt(
        source_tar_sha256=_text(document["source_tar_sha256"]),
        source_tar_regular_file_count=_integer(
            document["source_tar_regular_file_count"]
        ),
        source_tar_directory_count=_integer(
            document["source_tar_directory_count"]
        ),
        source_tar_uncompressed_bytes=_integer(
            document["source_tar_uncompressed_bytes"]
        ),
        source_tar_content_root_sha256=_text(
            document["source_tar_content_root_sha256"]
        ),
        repacked_zip_sha256=_text(document["repacked_zip_sha256"]),
        repacked_zip_bytes=_integer(document["repacked_zip_bytes"]),
        repacked_zip_content_root_sha256=_text(
            document["repacked_zip_content_root_sha256"]
        ),
        file_parity_root_sha256=_text(document["file_parity_root_sha256"]),
    )
    if receipt.to_json_object() != document:
        raise ValueError
    return receipt


def _load_zip_validation_receipt(value: object) -> ZipValidationReceipt:
    document = _mapping(value)
    receipt = ZipValidationReceipt(
        archive_sha256=_text(document["archive_sha256"]),
        member_count=_integer(document["member_count"]),
        regular_file_count=_integer(document["regular_file_count"]),
        directory_count=_integer(document["directory_count"]),
        compressed_bytes=_integer(document["compressed_bytes"]),
        uncompressed_bytes=_integer(document["uncompressed_bytes"]),
        content_root_sha256=_text(document["content_root_sha256"]),
    )
    if receipt.to_json_object() != document:
        raise ValueError
    return receipt


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError
    return float(value)


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


__all__ = [
    "Aihub538AuthorizedTrainingRunBinding",
    "open_aihub538_authorized_training_run",
]
