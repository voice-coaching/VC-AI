"""Fail-closed binding for one approved Backend analysis deployment set.

The attestation contains no secrets or artifact paths.  It binds a Git release,
the active consent signing key identifier and policy, the requested runtime
device, and content digests for every mounted inference artifact.  Operators
generate it outside the repository and mount it read-only into the worker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


SCHEMA_VERSION = "voice-coaching.analysis-deployment.v2"
HTTP_SCHEMA_VERSION = "voice-coaching.analysis-http-deployment.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^[0-9a-f]{40}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_POLICY_REVISION = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_MAXIMUM_MANIFEST_BYTES = 64 * 1024
_MAXIMUM_ARTIFACT_FILES = 100_000
_MAXIMUM_ARTIFACT_BYTES = 16 * 1024 * 1024 * 1024


class DeploymentAttestationError(ValueError):
    """Raised without disclosing protected paths or artifact fingerprints."""

    def __init__(self) -> None:
        super().__init__("analysis_worker_deployment_attestation_invalid")


@dataclass(frozen=True, slots=True)
class DeploymentArtifactPaths:
    detector_bundle: Path
    seungun_model: Path
    seungun_processor: Path
    nltk_data: Path
    visual_release: Path | None = None
    visual_landmark_model: Path | None = None
    visual_geometry_artifact: Path | None = None

    def records(self) -> tuple[tuple[str, str, Path], ...]:
        records: list[tuple[str, str, Path]] = [
            ("detectorBundle", "directory", self.detector_bundle),
            ("seungunModel", "directory", self.seungun_model),
            ("seungunProcessor", "directory", self.seungun_processor),
            ("nltkData", "directory", self.nltk_data),
        ]
        visual_presence = {
            self.visual_release is not None,
            self.visual_landmark_model is not None,
            self.visual_geometry_artifact is not None,
        }
        if len(visual_presence) != 1:
            raise DeploymentAttestationError()
        if (
            self.visual_release is not None
            and self.visual_landmark_model is not None
            and self.visual_geometry_artifact is not None
        ):
            records.extend(
                (
                    ("visualRelease", "directory", self.visual_release),
                    ("visualLandmarkModel", "file", self.visual_landmark_model),
                    ("visualGeometryArtifact", "file", self.visual_geometry_artifact),
                )
            )
        return tuple(records)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "DeploymentArtifactPaths":
        env = os.environ if environment is None else environment
        try:
            repository_root = Path(env["VOICE_COACH_REPOSITORY_ROOT"])
            visual_release_text = env.get("AI_ANALYSIS_VISUAL_RELEASE_ROOT", "")
            visual_model_text = env.get("AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH", "")
            visual_geometry_text = env.get(
                "AI_ANALYSIS_VISUAL_GEOMETRY_ARTIFACT_PATH", ""
            )
            paths = cls(
                detector_bundle=(
                    repository_root
                    / "seungun/artifacts/error_detector/final_pronunciation_detector_v1"
                ),
                seungun_model=(
                    repository_root
                    / "seungun/checkpoints/xlsr300m_full_internal_v1/best_model"
                ),
                seungun_processor=repository_root / "seungun/processor",
                nltk_data=Path(env["AI_ANALYSIS_NLTK_DATA_ROOT"]),
                visual_release=(Path(visual_release_text) if visual_release_text else None),
                visual_landmark_model=(Path(visual_model_text) if visual_model_text else None),
                visual_geometry_artifact=(
                    Path(visual_geometry_text) if visual_geometry_text else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            raise DeploymentAttestationError() from None
        if any(not path.is_absolute() for _, _, path in paths.records()):
            raise DeploymentAttestationError()
        return paths


def create_deployment_attestation(
    *,
    release_id: str,
    authorization_key_id: str,
    consent_policy_revision: str,
    worker_device: str,
    artifact_group_id: int,
    artifacts: DeploymentArtifactPaths,
) -> dict[str, object]:
    """Create an in-memory attestation for a separately approved local write."""

    _validate_bindings(
        release_id=release_id,
        authorization_key_id=authorization_key_id,
        consent_policy_revision=consent_policy_revision,
        worker_device=worker_device,
        artifact_group_id=artifact_group_id,
    )
    artifact_records: dict[str, object] = {}
    for name, kind, path in artifacts.records():
        artifact_records[name] = {
            "kind": kind,
            "sha256": _artifact_digest(
                path, kind=kind, artifact_group_id=artifact_group_id
            ),
        }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "releaseId": release_id,
        "authorizationKeyId": authorization_key_id,
        "consentPolicyRevision": consent_policy_revision,
        "workerDevice": worker_device,
        "artifactGroupId": artifact_group_id,
        "artifacts": artifact_records,
    }


def verify_deployment_attestation(
    environment: Mapping[str, str] | None = None,
    *,
    authorization_key_ids: Iterable[str],
) -> None:
    """Verify deployment identity and all mounted artifacts before external I/O."""

    env = os.environ if environment is None else environment
    try:
        manifest_path = Path(env["AI_ANALYSIS_DEPLOYMENT_MANIFEST_PATH"])
        release_id = env["AI_ANALYSIS_RELEASE_ID"]
        build_revision = env["VOICE_COACH_BUILD_REVISION"]
        authorization_key_id = env["AI_ANALYSIS_ACTIVE_AUTHORIZATION_KEY_ID"]
        consent_policy_revision = env["AI_ANALYSIS_CONSENT_POLICY_REVISION"]
        worker_device = env.get("AI_ANALYSIS_SEUNGUN_DEVICE", "cpu")
        artifact_group_id = int(env["AI_ANALYSIS_ARTIFACT_GID"])
    except (KeyError, TypeError, ValueError):
        raise DeploymentAttestationError() from None
    _validate_bindings(
        release_id=release_id,
        authorization_key_id=authorization_key_id,
        consent_policy_revision=consent_policy_revision,
        worker_device=worker_device,
        artifact_group_id=artifact_group_id,
    )
    if build_revision != release_id:
        raise DeploymentAttestationError()
    if authorization_key_id not in set(authorization_key_ids):
        raise DeploymentAttestationError()
    document = _load_manifest(
        manifest_path, artifact_group_id=artifact_group_id
    )
    expected_top_level = {
        "schemaVersion",
        "releaseId",
        "authorizationKeyId",
        "consentPolicyRevision",
        "workerDevice",
        "artifactGroupId",
        "artifacts",
    }
    if (
        set(document) != expected_top_level
        or document["schemaVersion"] != SCHEMA_VERSION
        or document["releaseId"] != release_id
        or document["authorizationKeyId"] != authorization_key_id
        or document["consentPolicyRevision"] != consent_policy_revision
        or document["workerDevice"] != worker_device
        or document["artifactGroupId"] != artifact_group_id
        or not isinstance(document["artifacts"], dict)
    ):
        raise DeploymentAttestationError()
    configured = DeploymentArtifactPaths.from_environment(env).records()
    manifest_artifacts = document["artifacts"]
    if set(manifest_artifacts) != {name for name, _, _ in configured}:
        raise DeploymentAttestationError()
    for name, kind, path in configured:
        record = manifest_artifacts.get(name)
        if (
            not isinstance(record, dict)
            or set(record) != {"kind", "sha256"}
            or record.get("kind") != kind
            or not isinstance(record.get("sha256"), str)
            or _SHA256.fullmatch(record["sha256"]) is None
            or not _constant_time_equal(
                record["sha256"],
                _artifact_digest(
                    path, kind=kind, artifact_group_id=artifact_group_id
                ),
            )
        ):
            raise DeploymentAttestationError()


def create_http_deployment_attestation(*, release_id, worker_device, artifact_group_id, artifacts):
    """Bind HTTP assets and runtime identity without Redis consent/key fields."""
    if (not isinstance(release_id, str) or _RELEASE_ID.fullmatch(release_id) is None
            or worker_device not in {"cpu", "cuda"} or type(artifact_group_id) is not int
            or not 1 <= artifact_group_id <= 2_147_483_647):
        raise DeploymentAttestationError()
    return dict(schemaVersion=HTTP_SCHEMA_VERSION, releaseId=release_id, workerDevice=worker_device,
                artifactGroupId=artifact_group_id,
                artifacts={name: dict(kind=kind, sha256=_artifact_digest(path, kind=kind,
                           artifact_group_id=artifact_group_id)) for name, kind, path in artifacts.records()})


def verify_http_deployment_attestation(environment):
    try:
        release = environment["AI_ANALYSIS_RELEASE_ID"]
        if release != environment["VOICE_COACH_BUILD_REVISION"]:
            raise DeploymentAttestationError()
        group = int(environment["AI_ANALYSIS_ARTIFACT_GID"])
        expected = create_http_deployment_attestation(release_id=release,
            worker_device=environment["AI_ANALYSIS_SEUNGUN_DEVICE"], artifact_group_id=group,
            artifacts=DeploymentArtifactPaths.from_environment(environment))
        document = _load_manifest(Path(environment["AI_ANALYSIS_DEPLOYMENT_MANIFEST_PATH"]),
                                  artifact_group_id=group)
        if document != expected:
            raise DeploymentAttestationError()
    except (KeyError, ValueError, TypeError):
        raise DeploymentAttestationError() from None


def write_deployment_attestation(
    path: Path,
    document: Mapping[str, object],
    *,
    artifact_group_id: int,
) -> None:
    """Create a private manifest without replacing an existing approval record."""

    if not path.is_absolute():
        raise DeploymentAttestationError()
    payload = (
        json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )
    if len(payload) > _MAXIMUM_MANIFEST_BYTES:
        raise DeploymentAttestationError()
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        parent_metadata = os.fstat(parent_descriptor)
        parent_mode = stat.S_IMODE(parent_metadata.st_mode)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or parent_metadata.st_gid != artifact_group_id
            or parent_mode & (stat.S_IWGRP | stat.S_IRWXO)
            or parent_mode & (stat.S_IWUSR | stat.S_IXUSR)
            != (stat.S_IWUSR | stat.S_IXUSR)
            or (
                parent_metadata.st_gid != os.getegid()
                and not parent_mode & stat.S_ISGID
            )
        ):
            raise DeploymentAttestationError()
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            0o640,
            dir_fd=parent_descriptor,
        )
        os.fchmod(descriptor, 0o640)
        if os.fstat(descriptor).st_gid != artifact_group_id:
            raise DeploymentAttestationError()
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise DeploymentAttestationError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def prepare_artifact_group_access(
    paths: Iterable[Path], *, artifact_group_id: int, apply: bool
) -> None:
    """Enforce group-read-only artifact access without making assets public."""

    if (
        not isinstance(artifact_group_id, int)
        or isinstance(artifact_group_id, bool)
        or not 1 <= artifact_group_id <= 2_147_483_647
    ):
        raise DeploymentAttestationError()
    roots = tuple(paths)
    if not roots or len(roots) > 16:
        raise DeploymentAttestationError()
    entries: list[tuple[Path, bool]] = []
    for root in roots:
        if not root.is_absolute() or root == Path("/"):
            raise DeploymentAttestationError()
        try:
            metadata = root.lstat()
        except OSError:
            raise DeploymentAttestationError() from None
        if root.is_symlink():
            raise DeploymentAttestationError()
        if stat.S_ISREG(metadata.st_mode):
            entries.append((root, False))
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise DeploymentAttestationError()
        entries.append((root, True))
        try:
            for current_root, directory_names, file_names in os.walk(
                root, topdown=True, followlinks=False
            ):
                current = Path(current_root)
                directory_names.sort()
                file_names.sort()
                for name in directory_names:
                    candidate = current / name
                    candidate_metadata = candidate.lstat()
                    if candidate.is_symlink() or not stat.S_ISDIR(
                        candidate_metadata.st_mode
                    ):
                        raise DeploymentAttestationError()
                    entries.append((candidate, True))
                for name in file_names:
                    candidate = current / name
                    candidate_metadata = candidate.lstat()
                    if candidate.is_symlink() or not stat.S_ISREG(
                        candidate_metadata.st_mode
                    ):
                        raise DeploymentAttestationError()
                    entries.append((candidate, False))
        except OSError:
            raise DeploymentAttestationError() from None
        if len(entries) > _MAXIMUM_ARTIFACT_FILES + 16:
            raise DeploymentAttestationError()

    for path, directory in entries:
        try:
            metadata = path.lstat()
            if metadata.st_gid != artifact_group_id or path.is_symlink():
                raise DeploymentAttestationError()
            if apply:
                owner_mode = stat.S_IMODE(metadata.st_mode) & stat.S_IRWXU
                group_mode = stat.S_IRGRP | (stat.S_IXGRP if directory else 0)
                if not directory and metadata.st_mode & stat.S_IXUSR:
                    group_mode |= stat.S_IXGRP
                os.chmod(path, owner_mode | group_mode, follow_symlinks=False)
                metadata = path.lstat()
            if not _secure_artifact_mode(
                metadata,
                artifact_group_id=artifact_group_id,
                directory=directory,
            ):
                raise DeploymentAttestationError()
        except OSError:
            raise DeploymentAttestationError() from None


def _validate_bindings(
    *,
    release_id: object,
    authorization_key_id: object,
    consent_policy_revision: object,
    worker_device: object,
    artifact_group_id: object,
) -> None:
    if (
        not isinstance(release_id, str)
        or _RELEASE_ID.fullmatch(release_id) is None
        or not isinstance(authorization_key_id, str)
        or _KEY_ID.fullmatch(authorization_key_id) is None
        or not isinstance(consent_policy_revision, str)
        or _POLICY_REVISION.fullmatch(consent_policy_revision) is None
        or worker_device not in {"cpu", "cuda"}
        or not isinstance(artifact_group_id, int)
        or isinstance(artifact_group_id, bool)
        or not 1 <= artifact_group_id <= 2_147_483_647
    ):
        raise DeploymentAttestationError()


def _load_manifest(path: Path, *, artifact_group_id: int) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        metadata = path.lstat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or not _secure_artifact_mode(
                metadata,
                artifact_group_id=artifact_group_id,
                directory=False,
            )
            or metadata.st_size <= 0
            or metadata.st_size > _MAXIMUM_MANIFEST_BYTES
        ):
            raise DeploymentAttestationError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if _metadata_changed(metadata, opened):
            raise DeploymentAttestationError()
        payload = bytearray()
        while len(payload) <= _MAXIMUM_MANIFEST_BYTES:
            chunk = os.read(
                descriptor,
                min(4096, _MAXIMUM_MANIFEST_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        if not payload or len(payload) > _MAXIMUM_MANIFEST_BYTES:
            raise DeploymentAttestationError()
        if _metadata_changed(opened, os.fstat(descriptor)):
            raise DeploymentAttestationError()
        document = json.loads(
            bytes(payload).decode("ascii"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DeploymentAttestationError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(document, dict):
        raise DeploymentAttestationError()
    return document


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentAttestationError()
        result[key] = value
    return result


def _artifact_digest(path: Path, *, kind: str, artifact_group_id: int) -> str:
    if not path.is_absolute():
        raise DeploymentAttestationError()
    try:
        metadata = path.lstat()
    except OSError:
        raise DeploymentAttestationError() from None
    if path.is_symlink():
        raise DeploymentAttestationError()
    if kind == "file":
        if not stat.S_ISREG(metadata.st_mode) or not _secure_artifact_mode(
            metadata, artifact_group_id=artifact_group_id, directory=False
        ):
            raise DeploymentAttestationError()
        return _regular_file_digest(path)
    if (
        kind != "directory"
        or not stat.S_ISDIR(metadata.st_mode)
        or not _secure_artifact_mode(
            metadata, artifact_group_id=artifact_group_id, directory=True
        )
    ):
        raise DeploymentAttestationError()

    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    try:
        for current_root, directory_names, file_names in os.walk(
            path, topdown=True, followlinks=False
        ):
            current = Path(current_root)
            directory_names.sort()
            file_names.sort()
            for directory_name in directory_names:
                candidate = current / directory_name
                directory_metadata = candidate.lstat()
                if candidate.is_symlink() or not stat.S_ISDIR(directory_metadata.st_mode):
                    raise DeploymentAttestationError()
                if not _secure_artifact_mode(
                    directory_metadata,
                    artifact_group_id=artifact_group_id,
                    directory=True,
                ):
                    raise DeploymentAttestationError()
                relative = candidate.relative_to(path).as_posix().encode("utf-8")
                digest.update(b"D\0" + relative + b"\n")
            for file_name in file_names:
                candidate = current / file_name
                file_metadata = candidate.lstat()
                if candidate.is_symlink() or not stat.S_ISREG(file_metadata.st_mode):
                    raise DeploymentAttestationError()
                if not _secure_artifact_mode(
                    file_metadata,
                    artifact_group_id=artifact_group_id,
                    directory=False,
                ):
                    raise DeploymentAttestationError()
                file_count += 1
                total_bytes += file_metadata.st_size
                if (
                    file_count > _MAXIMUM_ARTIFACT_FILES
                    or total_bytes > _MAXIMUM_ARTIFACT_BYTES
                ):
                    raise DeploymentAttestationError()
                relative = candidate.relative_to(path).as_posix().encode("utf-8")
                digest.update(b"F\0" + relative + b"\0")
                digest.update(str(file_metadata.st_size).encode("ascii") + b"\0")
                digest.update(_regular_file_digest(candidate).encode("ascii") + b"\n")
    except (OSError, UnicodeError, ValueError):
        raise DeploymentAttestationError() from None
    if file_count == 0:
        raise DeploymentAttestationError()
    final_metadata = path.lstat()
    if _metadata_changed(metadata, final_metadata):
        raise DeploymentAttestationError()
    return digest.hexdigest()


def _regular_file_digest(path: Path) -> str:
    descriptor: int | None = None
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAXIMUM_ARTIFACT_BYTES
        ):
            raise DeploymentAttestationError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if _metadata_changed(before, opened):
            raise DeploymentAttestationError()
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _metadata_changed(opened, after):
            raise DeploymentAttestationError()
        return digest.hexdigest()
    except OSError:
        raise DeploymentAttestationError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _metadata_changed(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_gid,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_uid,
        after.st_gid,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def _secure_artifact_mode(
    metadata: os.stat_result, *, artifact_group_id: int, directory: bool
) -> bool:
    mode = stat.S_IMODE(metadata.st_mode)
    required = stat.S_IRGRP | (stat.S_IXGRP if directory else 0)
    forbidden = stat.S_IWGRP | stat.S_IRWXO
    return (
        metadata.st_gid == artifact_group_id
        and mode & required == required
        and mode & forbidden == 0
    )


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


__all__ = [
    "DeploymentArtifactPaths",
    "DeploymentAttestationError",
    "SCHEMA_VERSION",
    "create_deployment_attestation",
    "prepare_artifact_group_access",
    "verify_deployment_attestation",
    "write_deployment_attestation",
]
