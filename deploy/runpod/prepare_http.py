"""Prepare a diagnostic listener even when private analysis dependencies are absent."""
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from prepare_worker import _pairs, _settings, _write_private
from supervise_services import read_private_file

RUNTIME = Path("/opt/intelligentai")
SOURCE = Path("/root/.config/intelligentai/analysis-http.env")
GENERATED = Path("/run/intelligentai/analysis-http.env")


def main():
    if os.geteuid() != 0:
        raise SystemExit("http_preparation_requires_root")
    os.umask(0o077)
    config = json.loads((RUNTIME / "app/deploy/runpod/services.http.json").read_text())
    values = dict(next(s for s in config["services"] if s["name"] == "api")["env"])
    values["AI_ANALYSIS_HTTP_BOOTSTRAP_READY"] = "false"
    try:
        injected = os.environ.get("RUNPOD_ANALYSIS_HTTP_ENV_JSON")
        if injected is not None:
            if len(injected.encode()) > 131072:
                raise ValueError()
            settings = _settings(json.loads(injected, object_pairs_hook=_pairs))
            if any(any(word in key for word in ("AUTHORIZATION", "CONSENT", "REDIS")) for key in settings):
                raise ValueError()
            SOURCE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            SOURCE.parent.chmod(0o700)
            _write_private(SOURCE, settings)
        if SOURCE.exists():
            raw = read_private_file(SOURCE, 131072, owner_only=True).decode("utf-8")
            pairs = [tuple(line.split("=", 1)) for line in raw.splitlines()
                     if line.strip() and not line.lstrip().startswith("#")]
            settings = _settings(_pairs(pairs))
            if any(any(word in key for word in ("AUTHORIZATION", "CONSENT", "REDIS")) for key in settings):
                raise ValueError()
            values.update(settings)
        revision = json.loads((RUNTIME / "deployment.json").read_text())["sourceRevision"]
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError()
        values["VOICE_COACH_BUILD_REVISION"] = values["AI_ANALYSIS_RELEASE_ID"] = revision
        values = {key: value for key, value in values.items() if not key.startswith("AI_ANALYSIS_CLOVA_")}
        sys.path.insert(0, str(RUNTIME / "app/src"))
        from voice_coach.backend_analysis.coaching_generator import openai_configuration
        openai_configuration(values)
        from voice_coach.backend_analysis.deployment_attestation import (
            DeploymentArtifactPaths, create_http_deployment_attestation, write_deployment_attestation)
        group = int(values["AI_ANALYSIS_ARTIFACT_GID"])
        document = create_http_deployment_attestation(release_id=revision,
            worker_device=values["AI_ANALYSIS_SEUNGUN_DEVICE"], artifact_group_id=group,
            artifacts=DeploymentArtifactPaths.from_environment(values))
        parent = RUNTIME / "http-deployment"
        parent.mkdir(mode=0o2750, exist_ok=True)
        os.chown(parent, 0, group)
        parent.chmod(0o2750)
        temporary = parent / (".manifest-" + uuid4().hex)
        write_deployment_attestation(temporary, document, artifact_group_id=group)
        os.replace(temporary, parent / "manifest.json")
        values["AI_ANALYSIS_DEPLOYMENT_MANIFEST_PATH"] = str(parent / "manifest.json")
        values["AI_ANALYSIS_HTTP_BOOTSTRAP_READY"] = "true"
        print("analysis_http_assets_prepared")
    except Exception:
        values["AI_ANALYSIS_HTTP_BOOTSTRAP_READY"] = "false"
        print("analysis_http_bootstrap_not_ready")
    try:
        from prepare_canonical import prepare
        if values["AI_ANALYSIS_HTTP_BOOTSTRAP_READY"] == "true":
            prepare(values)
    except Exception:
        values["AI_CANONICAL_BOOTSTRAP_READY"] = "false"
        values["AI_CANONICAL_ADMISSION_ENABLED"] = "false"
        print("canonical_bootstrap_not_ready")
    if values.get("AI_DIRECT_ENABLED") == "true":
        from prepare_direct import prepare as prepare_direct
        prepare_direct(values)
    _write_private(GENERATED, values)
    print("analysis_http_listener_environment_prepared")


if __name__ == "__main__":
    main()
