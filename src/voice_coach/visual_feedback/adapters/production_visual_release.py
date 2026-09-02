"""Load one immutable, deployment-scoped visual-feedback release.

P6/P8 validation is summarized inside the runtime bundle. This loader packages
that bundle and both calibrated decision policies behind one release directory. Per-attempt code receives
typed values only after every file, digest, schema, and cross-binding passes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence

from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionPolicy,
    CalibratedVideoQcDecisionPolicy,
)

from ..errors import VisualFeedbackContractError
from ..models.integrity import (
    require_exact_keys,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    require_text_token,
    sha256_document,
)
from ..models.runtime_contract import (
    VisualRuntimeBundle,
    load_visual_runtime_bundle,
)
from ..models.runtime_policy_artifact import P8RuntimePolicyArtifact


PRODUCTION_VISUAL_RELEASE_SCHEMA_VERSION = "voice_coach.production_visual_release.v3"
MANIFEST_FILENAME = "release.json"
ARTIFACT_FILENAMES = {
    "runtime_bundle": "runtime_bundle.json",
    "video_qc_decision_policy": "video_qc_decision_policy.json",
    "av_sync_decision_policy": "av_sync_decision_policy.json",
}
_MAXIMUM_FILE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProductionVisualRelease:
    """Strictly loaded values consumed by the production composition root."""

    release_id: str
    release_sha256: str
    status: str
    reason_codes: tuple[str, ...]
    runtime_bundle: VisualRuntimeBundle | None
    video_qc_decision_policy: CalibratedVideoQcDecisionPolicy | None
    av_sync_decision_policy: CalibratedAvSyncDecisionPolicy | None


def _json_no_duplicates(payload: bytes, *, name: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise VisualFeedbackContractError(f"{name} contains duplicate keys")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise VisualFeedbackContractError(f"{name} is not strict JSON") from error


def _open_release_directory(path: Path) -> int:
    absolute = path.absolute()
    try:
        before = os.lstat(absolute)
    except OSError as error:
        raise VisualFeedbackContractError("visual release directory is unavailable") from error
    if (
        not stat.S_ISDIR(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
    ):
        raise VisualFeedbackContractError("visual release directory is not private")
    try:
        descriptor = os.open(
            absolute,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise VisualFeedbackContractError("visual release directory cannot be opened") from error
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(after.st_mode)
            or after.st_uid != os.geteuid()
            or stat.S_IMODE(after.st_mode) & 0o077
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise VisualFeedbackContractError("visual release directory identity differs")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _read_private_file(directory_fd: int, filename: str) -> bytes:
    if filename not in {MANIFEST_FILENAME, *ARTIFACT_FILENAMES.values()}:
        raise VisualFeedbackContractError("visual release filename is not allowlisted")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
    except OSError as error:
        raise VisualFeedbackContractError("visual release file is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_size <= 0
            or before.st_size > _MAXIMUM_FILE_BYTES
        ):
            raise VisualFeedbackContractError("visual release file identity differs")
        payload = os.pread(descriptor, before.st_size, 0)
        after = os.fstat(descriptor)
        identity = lambda item: (  # noqa: E731 - compact immutable tuple
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if len(payload) != before.st_size or identity(before) != identity(after):
            raise VisualFeedbackContractError("visual release file changed while reading")
        return payload
    finally:
        os.close(descriptor)


def build_release_manifest(
    *, release_id: str, artifact_payloads: Mapping[str, bytes]
) -> dict[str, object]:
    """Build the canonical manifest for already serialized release artifacts."""

    identifier = require_safe_identifier(release_id, "release_id")
    if set(artifact_payloads) != set(ARTIFACT_FILENAMES):
        raise VisualFeedbackContractError("visual release artifact set differs")
    artifacts = {
        name: {
            "filename": ARTIFACT_FILENAMES[name],
            "sha256": hashlib.sha256(artifact_payloads[name]).hexdigest(),
        }
        for name in ARTIFACT_FILENAMES
    }
    unsigned = {
        "schema_version": PRODUCTION_VISUAL_RELEASE_SCHEMA_VERSION,
        "release_id": identifier,
        "status": "enabled",
        "reason_codes": [],
        "artifacts": artifacts,
    }
    return {**unsigned, "release_sha256": sha256_document(unsigned)}


def _validate_manifest(
    value: object,
) -> tuple[str, str, str, tuple[str, ...], Mapping[str, object]]:
    manifest = require_mapping(value, "production_visual_release")
    require_exact_keys(
        manifest,
        {
            "schema_version",
            "release_id",
            "status",
            "reason_codes",
            "artifacts",
            "release_sha256",
        },
        "production_visual_release",
    )
    if manifest["schema_version"] != PRODUCTION_VISUAL_RELEASE_SCHEMA_VERSION:
        raise VisualFeedbackContractError("visual release schema differs")
    release_id = require_safe_identifier(manifest["release_id"], "release_id")
    release_sha256 = require_sha256(
        manifest["release_sha256"], "release_sha256"
    )
    status = require_safe_identifier(manifest["status"], "release status")
    if status not in {"enabled", "disabled"}:
        raise VisualFeedbackContractError("production visual release status differs")
    raw_reasons = manifest["reason_codes"]
    if not isinstance(raw_reasons, list):
        raise VisualFeedbackContractError(
            "production visual release reasons must be an array"
        )
    reasons = tuple(
        require_safe_identifier(item, "release reason_codes[]")
        for item in raw_reasons
    )
    if len(set(reasons)) != len(reasons):
        raise VisualFeedbackContractError(
            "production visual release reasons contain duplicates"
        )
    artifacts = require_mapping(manifest["artifacts"], "release artifacts")
    expected_artifacts = set(ARTIFACT_FILENAMES) if status == "enabled" else set()
    if set(artifacts) != expected_artifacts:
        raise VisualFeedbackContractError("visual release artifact set differs")
    if (status == "enabled") == bool(reasons):
        raise VisualFeedbackContractError(
            "production visual release status and reasons differ"
        )
    for name, expected_filename in (
        ARTIFACT_FILENAMES.items() if status == "enabled" else ()
    ):
        entry = require_mapping(artifacts[name], f"release artifact {name}")
        require_exact_keys(entry, {"filename", "sha256"}, f"release artifact {name}")
        if entry["filename"] != expected_filename:
            raise VisualFeedbackContractError("visual release artifact filename differs")
        require_sha256(entry["sha256"], f"release artifact {name} sha256")
    unsigned = {
        "schema_version": manifest["schema_version"],
        "release_id": release_id,
        "status": status,
        "reason_codes": list(reasons),
        "artifacts": artifacts,
    }
    if release_sha256 != sha256_document(unsigned):
        raise VisualFeedbackContractError("visual release manifest digest differs")
    return release_id, release_sha256, status, reasons, artifacts


def load_production_visual_release(path: str | Path) -> ProductionVisualRelease:
    """Strictly load one complete release or fail before video source access."""

    directory_fd = _open_release_directory(Path(path))
    try:
        manifest_payload = _read_private_file(directory_fd, MANIFEST_FILENAME)
        release_id, release_sha256, status, reasons, entries = _validate_manifest(
            _json_no_duplicates(manifest_payload, name="visual release manifest")
        )
        documents: dict[str, object] = {}
        for name, filename in (
            ARTIFACT_FILENAMES.items() if status == "enabled" else ()
        ):
            payload = _read_private_file(directory_fd, filename)
            entry = require_mapping(entries[name], f"release artifact {name}")
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise VisualFeedbackContractError("visual release artifact digest differs")
            documents[name] = _json_no_duplicates(
                payload, name=f"visual release artifact {name}"
            )
    finally:
        os.close(directory_fd)

    if status == "disabled":
        return ProductionVisualRelease(
            release_id=release_id,
            release_sha256=release_sha256,
            status=status,
            reason_codes=reasons,
            runtime_bundle=None,
            video_qc_decision_policy=None,
            av_sync_decision_policy=None,
        )

    bundle = load_visual_runtime_bundle(documents["runtime_bundle"])
    qc_artifact = P8RuntimePolicyArtifact.from_json_object(
        documents["video_qc_decision_policy"]
    )
    sync_artifact = P8RuntimePolicyArtifact.from_json_object(
        documents["av_sync_decision_policy"]
    )
    if (
        qc_artifact.policy_kind != "video_qc_decision"
        or sync_artifact.policy_kind != "av_sync_decision"
    ):
        raise VisualFeedbackContractError("visual release decision policy kind differs")
    qc_policy = qc_artifact.policy_document.to_runtime_policy()
    sync_policy = sync_artifact.policy_document.to_runtime_policy()
    if (
        not isinstance(qc_policy, CalibratedVideoQcDecisionPolicy)
        or not isinstance(sync_policy, CalibratedAvSyncDecisionPolicy)
        or bundle.pipeline_identity.video_qc_decision_policy_sha256
        != qc_policy.policy_sha256
        or bundle.pipeline_identity.av_sync_decision_policy_sha256
        != sync_policy.policy_sha256
    ):
        raise VisualFeedbackContractError("visual release policy identity differs")
    return ProductionVisualRelease(
        release_id=release_id,
        release_sha256=release_sha256,
        status=status,
        reason_codes=reasons,
        runtime_bundle=bundle,
        video_qc_decision_policy=qc_policy,
        av_sync_decision_policy=sync_policy,
    )


def classify_production_visual_release_error(error: Exception) -> str:
    """Map internal loader details to a stable, path-free public reason."""

    detail = str(error).lower()
    if "digest" in detail:
        return "production_visual_release_digest_mismatch"
    if "policy" in detail or "schema" in detail or "artifact set" in detail:
        return "production_visual_release_policy_mismatch"
    if "private" in detail or "identity" in detail or "symlink" in detail:
        return "production_visual_release_not_private"
    return "production_visual_release_unavailable"


def _read_private_source(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VisualFeedbackContractError(
            "visual release source is unavailable"
        ) from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size <= 0
            or before.st_size > _MAXIMUM_FILE_BYTES
        ):
            raise VisualFeedbackContractError(
                "visual release source identity differs"
            )
        payload = os.pread(descriptor, before.st_size, 0)
        after = os.fstat(descriptor)
        identity = lambda item: (  # noqa: E731 - compact immutable tuple
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if len(payload) != before.st_size or identity(before) != identity(after):
            raise VisualFeedbackContractError(
                "visual release source changed while reading"
            )
        return payload
    finally:
        os.close(descriptor)


def _write_private_file(directory: Path, filename: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    descriptor = os.open(directory / filename, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - defensive OS boundary
                raise OSError("visual release write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_disabled_production_visual_release(
    *,
    output_directory: str | Path,
    release_id: str,
    reason_codes: Sequence[str],
) -> ProductionVisualRelease:
    """Create a production-safe release that explicitly disables visual claims."""

    reasons = tuple(
        require_safe_identifier(item, "reason_codes[]") for item in reason_codes
    )
    if not reasons or len(set(reasons)) != len(reasons):
        raise VisualFeedbackContractError(
            "disabled production visual release reasons differ"
        )
    destination = Path(output_directory).absolute()
    parent = destination.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as error:
        raise VisualFeedbackContractError(
            "visual release parent is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise VisualFeedbackContractError("visual release parent is not trusted")
    if os.path.lexists(destination):
        raise VisualFeedbackContractError("visual release destination already exists")
    identifier = require_safe_identifier(release_id, "release_id")
    unsigned = {
        "schema_version": PRODUCTION_VISUAL_RELEASE_SCHEMA_VERSION,
        "release_id": identifier,
        "status": "disabled",
        "reason_codes": list(reasons),
        "artifacts": {},
    }
    manifest = {**unsigned, "release_sha256": sha256_document(unsigned)}
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=parent))
    try:
        os.chmod(staging, 0o700)
        _write_private_file(staging, MANIFEST_FILENAME, payload)
        loaded = load_production_visual_release(staging)
        os.rename(staging, destination)
        return load_production_visual_release(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def package_enabled_production_visual_release(
    *,
    output_directory: str | Path,
    release_id: str,
    artifact_sources: Mapping[str, str | Path],
    required_expected_phones: Sequence[str] = (),
) -> ProductionVisualRelease:
    """Atomically package and reload one already-promoted visual release.

    This function deliberately has no calibration or evaluation inputs.  It
    can only package the three frozen runtime outputs, and the
    strict loader must accept the staged release before it becomes visible at
    the requested destination.
    """

    if set(artifact_sources) != set(ARTIFACT_FILENAMES):
        raise VisualFeedbackContractError("visual release source set differs")
    required_phones = tuple(
        require_text_token(phone, "required_expected_phones[]")
        for phone in required_expected_phones
    )
    if len(set(required_phones)) != len(required_phones):
        raise VisualFeedbackContractError(
            "visual release required phones must be unique"
        )
    destination = Path(output_directory).absolute()
    parent = destination.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as error:
        raise VisualFeedbackContractError(
            "visual release parent is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise VisualFeedbackContractError("visual release parent is not trusted")
    if os.path.lexists(destination):
        raise VisualFeedbackContractError("visual release destination already exists")

    payloads = {
        name: _read_private_source(Path(artifact_sources[name]))
        for name in ARTIFACT_FILENAMES
    }
    manifest = build_release_manifest(
        release_id=release_id,
        artifact_payloads=payloads,
    )
    manifest_payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=parent))
    try:
        os.chmod(staging, 0o700)
        for name, filename in ARTIFACT_FILENAMES.items():
            _write_private_file(staging, filename, payloads[name])
        _write_private_file(staging, MANIFEST_FILENAME, manifest_payload)
        directory_fd = os.open(staging, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        loaded = load_production_visual_release(staging)
        if loaded.release_id != release_id or loaded.status != "enabled":
            raise VisualFeedbackContractError("visual release identity differs")
        if loaded.runtime_bundle is None:  # pragma: no cover - status invariant
            raise VisualFeedbackContractError("visual release bundle is unavailable")
        covered_phones = {
            phone
            for claim in loaded.runtime_bundle.claim_catalog.claims
            for phone in claim.expected_phones
        }
        if set(required_phones) - covered_phones:
            raise VisualFeedbackContractError(
                "visual release required phone coverage differs"
            )
        os.rename(staging, destination)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return load_production_visual_release(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = [
    "ARTIFACT_FILENAMES",
    "MANIFEST_FILENAME",
    "PRODUCTION_VISUAL_RELEASE_SCHEMA_VERSION",
    "ProductionVisualRelease",
    "build_release_manifest",
    "classify_production_visual_release_error",
    "create_disabled_production_visual_release",
    "load_production_visual_release",
    "package_enabled_production_visual_release",
]
