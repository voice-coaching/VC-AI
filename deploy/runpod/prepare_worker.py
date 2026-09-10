"""Restore private worker settings and bind the verified runtime to its release.

Missing connection settings leave the worker pending while Clova can start.
RUNPOD_ANALYSIS_WORKER_ENV_JSON is supplied by the Pod environment, never Git.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4

RUNTIME = Path("/opt/intelligentai")
SOURCE_ENV = Path("/root/.config/intelligentai/analysis-worker.env")
GENERATED_ENV = Path("/run/intelligentai/analysis-worker.env")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate_setting")
        result[name] = value
    return result


def _settings(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("invalid_settings")
    result = {}
    for name, item in value.items():
        if (not isinstance(name, str) or re.fullmatch(r"(?:AI_ANALYSIS|AWS)_[A-Z0-9_]+", name) is None
                or not isinstance(item, str) or any(c in item for c in "\r\n\x00")
                or item != item.strip() or item.startswith(("'", '"'))):
            raise ValueError("invalid_setting")
        result[name] = item
    return result


def _write_private(path: Path, values: dict[str, str]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        temporary.chmod(0o600)
        for name, value in sorted(values.items()):
            stream.write(f"{name}={value}\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("worker_preparation_requires_root")
    os.umask(0o077)
    GENERATED_ENV.unlink(missing_ok=True)
    try:
        injected = os.environ.get("RUNPOD_ANALYSIS_WORKER_ENV_JSON")
        if injected is not None:
            if len(injected.encode()) > 131072:
                raise ValueError("settings_too_large")
            values = _settings(json.loads(injected, object_pairs_hook=_pairs))
            SOURCE_ENV.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            SOURCE_ENV.parent.chmod(0o700)
            _write_private(SOURCE_ENV, values)
        if not SOURCE_ENV.exists():
            print("analysis_worker_settings_pending")
            return 0
        metadata = SOURCE_ENV.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or SOURCE_ENV.is_symlink()
                or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > 131072):
            raise ValueError("private_settings_invalid")
        pairs = []
        for line in SOURCE_ENV.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                raise ValueError("setting_invalid")
            pairs.append(tuple(line.split("=", 1)))
        settings = _settings(_pairs(pairs))
        config = json.loads((RUNTIME / "app/deploy/runpod/services.json").read_text())
        service = next(value for value in config["services"] if value["name"] == "analysis-worker")
        values = dict(service["env"])
        values.update(settings)
        if any(not values.get(name) for name in service["requiredEnv"]):
            print("analysis_worker_required_settings_pending")
            return 0
        deployment = json.loads((RUNTIME / "deployment.json").read_text())
        revision = deployment["sourceRevision"]
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError("release_invalid")
        if values.get("AI_ANALYSIS_RELEASE_ID", revision) != revision:
            raise ValueError("release_mismatch")
        values["AI_ANALYSIS_RELEASE_ID"] = revision
        values["VOICE_COACH_BUILD_REVISION"] = revision
        sys.path.insert(0, str(RUNTIME / "app/src"))
        from voice_coach.backend_analysis.adapters.hmac_authorization import HmacAuthorizationSettings
        from voice_coach.backend_analysis.deployment_attestation import (
            DeploymentArtifactPaths, create_deployment_attestation, write_deployment_attestation,
        )
        keys = HmacAuthorizationSettings.from_environment(values)
        key_id = values["AI_ANALYSIS_ACTIVE_AUTHORIZATION_KEY_ID"]
        if key_id not in keys.keys:
            raise ValueError("active_key_missing")
        group_id = int(values["AI_ANALYSIS_ARTIFACT_GID"])
        document = create_deployment_attestation(
            release_id=revision, authorization_key_id=key_id,
            consent_policy_revision=values["AI_ANALYSIS_CONSENT_POLICY_REVISION"],
            worker_device=values["AI_ANALYSIS_SEUNGUN_DEVICE"], artifact_group_id=group_id,
            artifacts=DeploymentArtifactPaths.from_environment(values),
        )
        parent = RUNTIME / "worker-deployment"
        parent.mkdir(mode=0o2750, exist_ok=True)
        os.chown(parent, 0, group_id)
        parent.chmod(0o2750)
        digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        record = parent / f"manifest-{digest}.json"
        if not record.exists():
            write_deployment_attestation(record, document, artifact_group_id=group_id)
        elif json.loads(record.read_text()) != document or record.is_symlink():
            raise ValueError("manifest_record_invalid")
        temporary = parent / f".manifest-{uuid4().hex}.tmp"
        temporary.write_bytes(record.read_bytes())
        os.chown(temporary, 0, group_id)
        temporary.chmod(0o640)
        os.replace(temporary, parent / "manifest.json")
        values["AI_ANALYSIS_DEPLOYMENT_MANIFEST_PATH"] = str(parent / "manifest.json")
        _write_private(GENERATED_ENV, values)
        print("analysis_worker_environment_prepared")
    except Exception:
        # No exception text, paths, settings, or authentication response is logged.
        print("analysis_worker_preparation_blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
