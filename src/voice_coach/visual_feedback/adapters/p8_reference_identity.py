"""Restricted descriptor loader for the P8 pre-access identity boundary.

The two private manifests are opened by fixed name below a caller-owned 0700
directory descriptor.  Individual keyed identity values exist only while this
adapter constructs the non-serializable sealed bundle; public state retains
only the companion seal's root, count, namespace, and admission digests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import fcntl
import json
import os
import stat

from .aihub538_identity_key_store import (
    Aihub538IdentityKeyMaterial,
    Aihub538IdentityKeyStoreError,
    load_aihub538_identity_key_descriptor,
)
from .aihub538_locked_dataset import P8RestrictedReferencePopulation
from ..errors import VisualFeedbackContractError
from ..models.aihub538_training_evidence import (
    Aihub538RestrictedIdentitySet,
)
from ..models.integrity import (
    require_boolean,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)
from ..models.p8_actual_source_state import (
    P8ActualSourceReadZeroSeal,
    P8CanonicalRestrictedIdentityCommitment,
    P8ReferenceIdentityRole,
)
from ..models.p8_locked_evaluation import P8ReadZeroSeal


P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION = (
    "voice_coach.p8_restricted_reference_identity_manifest.v1"
)
P8_RESTRICTED_IDENTITY_SET_ROOT_SCHEMA_VERSION = (
    "voice_coach.p8_restricted_identity_set_root.v1"
)
P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME = (
    "p6-calibration-restricted-identities-v1.json"
)
P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME = (
    "ts174-training-restricted-identities-v1.json"
)

_MAXIMUM_RESTRICTED_MANIFEST_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 256 * 1024
_BUNDLE_CONSTRUCTION_CAPABILITY = object()
_MANIFEST_FILENAMES = (
    P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
    P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME,
)
_REFERENCE_ROLES = (
    P8ReferenceIdentityRole.P6_CALIBRATION,
    P8ReferenceIdentityRole.TS174_TRAINING,
)


class P8ReferenceIdentityError(RuntimeError):
    """Secret-free failure from the restricted identity boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True, repr=False)
class _P8RestrictedIdentityManifest:
    """Private parsed manifest; no serializer or generated repr is provided."""

    role: P8ReferenceIdentityRole
    strict_admission_schema_version: str
    strict_admission_receipt_sha256: str
    strict_admission_manifest_sha256: str
    identity_namespace_commitment_sha256: str
    actor_hmac_sha256: tuple[str, ...] = field(repr=False)
    attempt_hmac_sha256: tuple[str, ...] = field(repr=False)
    identity_set_root_sha256: str
    restricted_manifest_sha256: str

    def restricted_identity_set(self) -> Aihub538RestrictedIdentitySet:
        """Return the private existing-domain representation for intersection."""

        return Aihub538RestrictedIdentitySet(
            namespace_commitment_sha256=(
                self.identity_namespace_commitment_sha256
            ),
            speaker_hmac_sha256=frozenset(self.actor_hmac_sha256),
            session_hmac_sha256=frozenset(self.attempt_hmac_sha256),
        )


@dataclass(frozen=True, slots=True, repr=False)
class P8SealedReferenceBundle:
    """Factory-only private capability binding the key and exact references."""

    _identity_key_material: Aihub538IdentityKeyMaterial = field(repr=False)
    _reference_populations: tuple[
        P8RestrictedReferencePopulation,
        P8RestrictedReferencePopulation,
    ] = field(repr=False)
    _actual_source_seal: P8ActualSourceReadZeroSeal = field(repr=False)
    _construction_capability: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._construction_capability is not _BUNDLE_CONSTRUCTION_CAPABILITY:
            raise P8ReferenceIdentityError(
                "p8_reference_bundle_factory_required"
            )
        if not isinstance(
            self._identity_key_material, Aihub538IdentityKeyMaterial
        ) or not isinstance(self._actual_source_seal, P8ActualSourceReadZeroSeal):
            raise P8ReferenceIdentityError(
                "p8_reference_bundle_input_invalid"
            )
        if tuple(
            population.role for population in self._reference_populations
        ) != tuple(role.value for role in _REFERENCE_ROLES):
            raise P8ReferenceIdentityError(
                "p8_reference_bundle_role_order_invalid"
            )
        if (
            self._identity_key_material.namespace_commitment_sha256
            != self._actual_source_seal.reference_commitments[
                0
            ].identity_namespace_commitment_sha256
        ):
            raise P8ReferenceIdentityError(
                "p8_reference_bundle_namespace_invalid"
            )
        for population, commitment in zip(
            self._reference_populations,
            self._actual_source_seal.reference_commitments,
            strict=True,
        ):
            _validate_population_against_commitment(population, commitment)

    @property
    def actual_source_seal_sha256(self) -> str:
        """Return the only public identifier for this private capability."""

        return self._actual_source_seal.actual_source_seal_sha256

    def _identity_key_for_locked_evaluation(self) -> Aihub538IdentityKeyMaterial:
        """Internal integration hook; callers must not log or persist the value."""

        return self._identity_key_material

    def _populations_for_locked_evaluation(
        self,
    ) -> tuple[
        P8RestrictedReferencePopulation,
        P8RestrictedReferencePopulation,
    ]:
        """Internal integration hook returning only the already sealed pair."""

        return self._reference_populations

    def __repr__(self) -> str:
        return "P8SealedReferenceBundle(<restricted>)"

    __str__ = __repr__

    def __reduce__(self) -> object:
        raise TypeError("p8_reference_bundle_serialization_forbidden")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("p8_reference_bundle_serialization_forbidden")


def canonical_restricted_identity_set_root_sha256(
    *,
    identity_namespace_commitment_sha256: str,
    actor_hmac_sha256: Sequence[str],
    attempt_hmac_sha256: Sequence[str],
) -> str:
    """Compute a domain-separated canonical root without retaining its leaves."""

    namespace = require_sha256(
        identity_namespace_commitment_sha256,
        "identity_namespace_commitment_sha256",
    )
    actors = _canonical_hmac_values(actor_hmac_sha256, "actor_hmac_sha256")
    attempts = _canonical_hmac_values(
        attempt_hmac_sha256, "attempt_hmac_sha256"
    )
    if set(actors) & set(attempts):
        raise VisualFeedbackContractError(
            "restricted identity HMAC domains overlap"
        )
    return sha256_document(
        {
            "schema_version": P8_RESTRICTED_IDENTITY_SET_ROOT_SCHEMA_VERSION,
            "identity_namespace_commitment_sha256": namespace,
            "actor_hmac_sha256": list(actors),
            "attempt_hmac_sha256": list(attempts),
        }
    )


def load_p8_sealed_reference_bundle(
    *,
    restricted_state_directory_descriptor: int,
    identity_key_descriptor: int,
    base_read_zero_seal: P8ReadZeroSeal,
    actual_source_seal: P8ActualSourceReadZeroSeal,
) -> P8SealedReferenceBundle:
    """Load, validate, and seal exactly P6 then TS174 before source access."""

    try:
        if not isinstance(base_read_zero_seal, P8ReadZeroSeal) or not isinstance(
            actual_source_seal, P8ActualSourceReadZeroSeal
        ):
            raise P8ReferenceIdentityError(
                "p8_reference_seal_input_invalid"
            )
        if (
            actual_source_seal.base_read_zero_seal_sha256
            != base_read_zero_seal.seal_sha256
            or actual_source_seal.p6_evidence_receipt_sha256
            != base_read_zero_seal.p6_evidence_receipt_sha256
        ):
            raise P8ReferenceIdentityError(
                "p8_reference_base_seal_binding_invalid"
            )
        identity_key_material, manifests = _load_reference_inputs(
            restricted_state_directory_descriptor=(
                restricted_state_directory_descriptor
            ),
            identity_key_descriptor=identity_key_descriptor,
        )
        if len(manifests) != 2:
            raise P8ReferenceIdentityError(
                "p8_reference_manifest_pair_invalid"
            )
        populations: list[P8RestrictedReferencePopulation] = []
        for manifest, commitment in zip(
            manifests,
            actual_source_seal.reference_commitments,
            strict=True,
        ):
            _validate_manifest_against_commitment(
                manifest,
                commitment=commitment,
                expected_namespace_sha256=(
                    identity_key_material.namespace_commitment_sha256
                ),
            )
            populations.append(
                P8RestrictedReferencePopulation(
                    role=manifest.role.value,
                    manifest_sha256=(
                        manifest.strict_admission_manifest_sha256
                    ),
                    identities=manifest.restricted_identity_set(),
                )
            )
        return P8SealedReferenceBundle(
            _identity_key_material=identity_key_material,
            _reference_populations=(populations[0], populations[1]),
            _actual_source_seal=actual_source_seal,
            _construction_capability=_BUNDLE_CONSTRUCTION_CAPABILITY,
        )
    except P8ReferenceIdentityError:
        raise
    except (
        Aihub538IdentityKeyStoreError,
        OSError,
        TypeError,
        ValueError,
        VisualFeedbackContractError,
    ):
        raise P8ReferenceIdentityError(
            "p8_reference_identity_admission_failed"
        ) from None


def prepare_p8_actual_source_read_zero_seal(
    *,
    restricted_state_directory_descriptor: int,
    identity_key_descriptor: int,
    base_read_zero_seal: P8ReadZeroSeal,
    fresh_reload_worker_code_sha256: str,
    sealed_at_utc: str,
) -> P8ActualSourceReadZeroSeal:
    """Issue the companion seal without opening any locked source."""

    try:
        if not isinstance(base_read_zero_seal, P8ReadZeroSeal):
            raise P8ReferenceIdentityError(
                "p8_reference_seal_input_invalid"
            )
        identity_key_material, manifests = _load_reference_inputs(
            restricted_state_directory_descriptor=(
                restricted_state_directory_descriptor
            ),
            identity_key_descriptor=identity_key_descriptor,
        )
        commitments = tuple(
            P8CanonicalRestrictedIdentityCommitment.issue(
                role=manifest.role,
                strict_admission_schema_version=(
                    manifest.strict_admission_schema_version
                ),
                strict_admission_receipt_sha256=(
                    manifest.strict_admission_receipt_sha256
                ),
                strict_admission_manifest_sha256=(
                    manifest.strict_admission_manifest_sha256
                ),
                identity_namespace_commitment_sha256=(
                    manifest.identity_namespace_commitment_sha256
                ),
                identity_set_root_sha256=manifest.identity_set_root_sha256,
                actor_hmac_count=len(manifest.actor_hmac_sha256),
                attempt_hmac_count=len(manifest.attempt_hmac_sha256),
            )
            for manifest in manifests
        )
        if (
            len(commitments) != 2
            or any(
                commitment.identity_namespace_commitment_sha256
                != identity_key_material.namespace_commitment_sha256
                for commitment in commitments
            )
        ):
            raise P8ReferenceIdentityError(
                "p8_reference_manifest_namespace_invalid"
            )
        return P8ActualSourceReadZeroSeal.issue(
            base_read_zero_seal_sha256=base_read_zero_seal.seal_sha256,
            p6_evidence_receipt_sha256=(
                base_read_zero_seal.p6_evidence_receipt_sha256
            ),
            reference_commitments=(commitments[0], commitments[1]),
            fresh_reload_worker_code_sha256=(
                fresh_reload_worker_code_sha256
            ),
            sealed_at_utc=sealed_at_utc,
        )
    except P8ReferenceIdentityError:
        raise
    except (
        Aihub538IdentityKeyStoreError,
        OSError,
        TypeError,
        ValueError,
        VisualFeedbackContractError,
    ):
        raise P8ReferenceIdentityError(
            "p8_reference_seal_preparation_failed"
        ) from None


def _load_reference_inputs(
    *,
    restricted_state_directory_descriptor: int,
    identity_key_descriptor: int,
) -> tuple[
    Aihub538IdentityKeyMaterial,
    tuple[_P8RestrictedIdentityManifest, _P8RestrictedIdentityManifest],
]:
    identity_key_material = load_aihub538_identity_key_descriptor(
        identity_key_descriptor
    )
    directory_descriptor, directory_identity = (
        _duplicate_private_directory_descriptor(
            restricted_state_directory_descriptor
        )
    )
    try:
        loaded = tuple(
            _load_manifest_at(
                directory_descriptor,
                filename=filename,
                expected_role=role,
            )
            for filename, role in zip(
                _MANIFEST_FILENAMES, _REFERENCE_ROLES, strict=True
            )
        )
        if _directory_identity(directory_descriptor) != directory_identity:
            raise P8ReferenceIdentityError(
                "p8_reference_directory_changed"
            )
    finally:
        os.close(directory_descriptor)
    if len(loaded) != 2:
        raise P8ReferenceIdentityError("p8_reference_manifest_pair_invalid")
    return identity_key_material, (loaded[0], loaded[1])


def _load_manifest_at(
    directory_descriptor: int,
    *,
    filename: str,
    expected_role: P8ReferenceIdentityRole,
) -> _P8RestrictedIdentityManifest:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
    except OSError:
        raise P8ReferenceIdentityError(
            "p8_reference_manifest_open_failed"
        ) from None
    try:
        document = _read_private_json_descriptor(descriptor)
    finally:
        os.close(descriptor)
    try:
        return _parse_restricted_manifest(
            document, expected_role=expected_role
        )
    except (TypeError, ValueError, VisualFeedbackContractError):
        raise P8ReferenceIdentityError(
            "p8_reference_manifest_invalid"
        ) from None


def _parse_restricted_manifest(
    value: object,
    *,
    expected_role: P8ReferenceIdentityRole,
) -> _P8RestrictedIdentityManifest:
    document = require_mapping(value, "p8_restricted_identity_manifest")
    require_exact_keys(
        document,
        {
            "schema_version",
            "role",
            "strict_admission_schema_version",
            "strict_admission_receipt_sha256",
            "strict_admission_manifest_sha256",
            "identity_namespace_commitment_sha256",
            "actor_hmac_count",
            "attempt_hmac_count",
            "actor_hmac_sha256",
            "attempt_hmac_sha256",
            "identity_set_root_sha256",
            "individual_raw_identifiers_serialized",
            "source_paths_serialized",
            "transcripts_serialized",
            "restricted_private_artifact",
            "restricted_manifest_sha256",
        },
        "p8_restricted_identity_manifest",
    )
    if (
        document["schema_version"]
        != P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION
    ):
        raise VisualFeedbackContractError(
            "restricted identity manifest schema differs"
        )
    try:
        role = P8ReferenceIdentityRole(
            require_safe_identifier(document["role"], "role")
        )
    except ValueError as error:
        raise VisualFeedbackContractError(
            "restricted identity role differs"
        ) from error
    if role is not expected_role:
        raise VisualFeedbackContractError(
            "restricted identity role order differs"
        )
    for name in (
        "individual_raw_identifiers_serialized",
        "source_paths_serialized",
        "transcripts_serialized",
    ):
        if require_boolean(document[name], name):
            raise VisualFeedbackContractError(
                "restricted identity manifest contains forbidden content"
            )
    if not require_boolean(
        document["restricted_private_artifact"],
        "restricted_private_artifact",
    ):
        raise VisualFeedbackContractError(
            "restricted identity manifest privacy boundary differs"
        )
    actors = _strict_sorted_hmac_array(
        document["actor_hmac_sha256"], "actor_hmac_sha256"
    )
    attempts = _strict_sorted_hmac_array(
        document["attempt_hmac_sha256"], "attempt_hmac_sha256"
    )
    if set(actors) & set(attempts):
        raise VisualFeedbackContractError(
            "restricted identity HMAC domains overlap"
        )
    actor_count = require_integer(
        document["actor_hmac_count"], "actor_hmac_count", minimum=1
    )
    attempt_count = require_integer(
        document["attempt_hmac_count"], "attempt_hmac_count", minimum=1
    )
    if actor_count != len(actors) or attempt_count != len(attempts):
        raise VisualFeedbackContractError(
            "restricted identity counts differ"
        )
    namespace = require_sha256(
        document["identity_namespace_commitment_sha256"],
        "identity_namespace_commitment_sha256",
    )
    expected_root = canonical_restricted_identity_set_root_sha256(
        identity_namespace_commitment_sha256=namespace,
        actor_hmac_sha256=actors,
        attempt_hmac_sha256=attempts,
    )
    root = require_sha256(
        document["identity_set_root_sha256"],
        "identity_set_root_sha256",
    )
    if root != expected_root:
        raise VisualFeedbackContractError(
            "restricted identity root differs"
        )
    manifest_digest = require_sha256(
        document["restricted_manifest_sha256"],
        "restricted_manifest_sha256",
    )
    unsigned = dict(document)
    del unsigned["restricted_manifest_sha256"]
    if manifest_digest != sha256_document(unsigned):
        raise VisualFeedbackContractError(
            "restricted identity manifest digest differs"
        )
    return _P8RestrictedIdentityManifest(
        role=role,
        strict_admission_schema_version=require_safe_identifier(
            document["strict_admission_schema_version"],
            "strict_admission_schema_version",
        ),
        strict_admission_receipt_sha256=require_sha256(
            document["strict_admission_receipt_sha256"],
            "strict_admission_receipt_sha256",
        ),
        strict_admission_manifest_sha256=require_sha256(
            document["strict_admission_manifest_sha256"],
            "strict_admission_manifest_sha256",
        ),
        identity_namespace_commitment_sha256=namespace,
        actor_hmac_sha256=actors,
        attempt_hmac_sha256=attempts,
        identity_set_root_sha256=root,
        restricted_manifest_sha256=manifest_digest,
    )


def _validate_manifest_against_commitment(
    manifest: _P8RestrictedIdentityManifest,
    *,
    commitment: P8CanonicalRestrictedIdentityCommitment,
    expected_namespace_sha256: str,
) -> None:
    if (
        manifest.role is not commitment.role
        or manifest.strict_admission_schema_version
        != commitment.strict_admission_schema_version
        or manifest.strict_admission_receipt_sha256
        != commitment.strict_admission_receipt_sha256
        or manifest.strict_admission_manifest_sha256
        != commitment.strict_admission_manifest_sha256
        or manifest.identity_namespace_commitment_sha256
        != commitment.identity_namespace_commitment_sha256
        or manifest.identity_namespace_commitment_sha256
        != expected_namespace_sha256
        or manifest.identity_set_root_sha256
        != commitment.identity_set_root_sha256
        or len(manifest.actor_hmac_sha256) != commitment.actor_hmac_count
        or len(manifest.attempt_hmac_sha256) != commitment.attempt_hmac_count
    ):
        raise P8ReferenceIdentityError(
            "p8_reference_manifest_seal_binding_invalid"
        )


def _validate_population_against_commitment(
    population: P8RestrictedReferencePopulation,
    commitment: P8CanonicalRestrictedIdentityCommitment,
) -> None:
    identities = population.identities
    try:
        root = canonical_restricted_identity_set_root_sha256(
            identity_namespace_commitment_sha256=(
                identities.namespace_commitment_sha256
            ),
            actor_hmac_sha256=tuple(identities.speaker_hmac_sha256),
            attempt_hmac_sha256=tuple(identities.session_hmac_sha256),
        )
    except VisualFeedbackContractError:
        raise P8ReferenceIdentityError(
            "p8_reference_bundle_identity_invalid"
        ) from None
    if (
        population.role != commitment.role.value
        or population.manifest_sha256
        != commitment.strict_admission_manifest_sha256
        or identities.namespace_commitment_sha256
        != commitment.identity_namespace_commitment_sha256
        or len(identities.speaker_hmac_sha256) != commitment.actor_hmac_count
        or len(identities.session_hmac_sha256) != commitment.attempt_hmac_count
        or root != commitment.identity_set_root_sha256
    ):
        raise P8ReferenceIdentityError(
            "p8_reference_bundle_commitment_invalid"
        )


def _canonical_hmac_values(
    value: Sequence[str], name: str
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisualFeedbackContractError(f"{name} must be an array")
    values = tuple(require_sha256(item, f"{name}[]") for item in value)
    if not values or len(set(values)) != len(values):
        raise VisualFeedbackContractError(
            "restricted identity HMAC set is empty or duplicated"
        )
    return tuple(sorted(values))


def _strict_sorted_hmac_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise VisualFeedbackContractError(f"{name} must be an array")
    values = tuple(require_sha256(item, f"{name}[]") for item in value)
    if (
        not values
        or len(set(values)) != len(values)
        or tuple(sorted(values)) != values
    ):
        raise VisualFeedbackContractError(
            "restricted identity HMAC array is not canonical"
        )
    return values


def _duplicate_private_directory_descriptor(
    descriptor: int,
) -> tuple[int, tuple[int, ...]]:
    if (
        isinstance(descriptor, bool)
        or not isinstance(descriptor, int)
        or descriptor < 3
    ):
        raise P8ReferenceIdentityError(
            "p8_reference_directory_descriptor_invalid"
        )
    duplicate: int | None = None
    try:
        duplicate = os.dup(descriptor)
        os.set_inheritable(duplicate, False)
        flags = fcntl.fcntl(duplicate, fcntl.F_GETFL)
        metadata = os.fstat(duplicate)
        if (
            flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise OSError
        return duplicate, _directory_identity(duplicate)
    except OSError:
        if duplicate is not None:
            try:
                os.close(duplicate)
            except OSError:
                pass
        raise P8ReferenceIdentityError(
            "p8_reference_directory_descriptor_invalid"
        ) from None


def _directory_identity(descriptor: int) -> tuple[int, ...]:
    metadata = os.fstat(descriptor)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_private_json_descriptor(descriptor: int) -> object:
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        if (
            flags & os.O_ACCMODE != os.O_RDONLY
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or not 0 < metadata.st_size <= _MAXIMUM_RESTRICTED_MANIFEST_BYTES
        ):
            raise OSError
        before = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        payload = bytearray()
        offset = 0
        while offset < metadata.st_size:
            chunk = os.pread(
                descriptor,
                min(_READ_CHUNK_BYTES, metadata.st_size - offset),
                offset,
            )
            if not chunk:
                raise OSError
            payload.extend(chunk)
            offset += len(chunk)
        after_metadata = os.fstat(descriptor)
        after = (
            after_metadata.st_dev,
            after_metadata.st_ino,
            after_metadata.st_size,
            after_metadata.st_mode,
            after_metadata.st_uid,
            after_metadata.st_mtime_ns,
            after_metadata.st_ctime_ns,
        )
        if before != after or len(payload) != metadata.st_size:
            raise OSError
    except OSError:
        raise P8ReferenceIdentityError(
            "p8_reference_manifest_descriptor_invalid"
        ) from None

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        return json.loads(
            bytes(payload),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("constant")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise P8ReferenceIdentityError(
            "p8_reference_manifest_json_invalid"
        ) from None


__all__ = [
    "P8_P6_RESTRICTED_IDENTITY_MANIFEST_FILENAME",
    "P8_RESTRICTED_IDENTITY_SET_ROOT_SCHEMA_VERSION",
    "P8_RESTRICTED_REFERENCE_IDENTITY_MANIFEST_SCHEMA_VERSION",
    "P8_TS174_RESTRICTED_IDENTITY_MANIFEST_FILENAME",
    "P8ReferenceIdentityError",
    "P8SealedReferenceBundle",
    "canonical_restricted_identity_set_root_sha256",
    "load_p8_sealed_reference_bundle",
    "prepare_p8_actual_source_read_zero_seal",
]
