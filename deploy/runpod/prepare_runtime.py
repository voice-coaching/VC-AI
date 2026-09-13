"""Rebuild ephemeral runtime from the verified RunPod migration volume.

Run as root after installing the system dependencies described in README.md.
Does not start the Redis worker or generate deployment authorization secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

WORKSPACE = Path("/workspace/intelligentai")
RUNTIME = Path("/opt/intelligentai")
REVISION = "a6cdfd3464d1b767259cad23e164eaf39d3e3960"


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("RunPod preparation requires root")
    source = (WORKSPACE / "current").resolve(strict=True)
    if not source.is_relative_to(WORKSPACE / "releases"):
        raise SystemExit("Unexpected release location")
    RUNTIME.mkdir(mode=0o750, exist_ok=True)
    (RUNTIME / "envs").mkdir(mode=0o750, exist_ok=True)
    for name in ("api", "g2pk", "scorer", "hyperclova"):
        subprocess.run(
            ["rsync", "-a", str(WORKSPACE / "envs" / name) + "/",
             str(RUNTIME / "envs" / name) + "/"], check=True,
        )
    app = RUNTIME / "app"
    app.mkdir(mode=0o750, exist_ok=True)
    subprocess.run(
        ["rsync", "-a", "--exclude=/data/models/", str(source) + "/", str(app) + "/"],
        check=True,
    )
    overlay = WORKSPACE / "deployment/int8-overlay"
    manifest_bytes = (overlay / "overlay-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit("Unsafe overlay path")
        content = (overlay / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise SystemExit("Overlay checksum differs")
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if os.environ.get("AI_ANALYSIS_TRANSPORT") == "runpod_http":
        subprocess.run(["python3", "-m", "pip", "install", "--no-index", "--require-hashes",
            "--upgrade", "--target", str(RUNTIME / "http-python"),
            "--find-links", str(WORKSPACE / "deployment/http-dependencies-v1_1"),
            "-r", str(app / "deploy/runpod/http-requirements.lock")], check=True)
    assets = WORKSPACE / "runtime-assets"
    asset_manifest = json.loads((assets / "migration-manifest.json").read_text())
    for entry in asset_manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise SystemExit("Unsafe runtime asset path")
        origin = assets / relative
        digest = hashlib.sha256()
        with origin.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
        if origin.stat().st_size != entry["size"] or digest.hexdigest() != entry["sha256"]:
            raise SystemExit("Runtime asset checksum differs")
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    for label, name in ((".venv", "scorer"), (".venv-paper-g2pk", "g2pk")):
        link = app / label
        expected = RUNTIME / "envs" / name
        if link.is_symlink() and link.resolve() != expected:
            raise SystemExit("Unexpected existing environment symlink")
        if not link.exists():
            link.symlink_to(expected, target_is_directory=True)
    for parent, directories, files in os.walk(RUNTIME, followlinks=False):
        os.chown(parent, 0, 10001)
        os.chmod(parent, 0o750)
        for name in files:
            path = Path(parent) / name
            if not path.is_symlink():
                executable = bool(path.stat().st_mode & stat.S_IXUSR)
                os.chown(path, 0, 10001)
                path.chmod(0o750 if executable else 0o640)
    config = json.loads((app / "configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json").read_text())
    config["localDirectory"] = str(source / "data/models/hyperclovax-seed-think-32b" / REVISION)
    config_file = RUNTIME / "hyperclova-runtime.json"
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    config_file.chmod(0o640)
    visual_release = app / "artifacts/visual_feedback/releases/production_visual"
    if visual_release.is_dir():
        # This loader requires immutable group-readable files, not general 0640.
        for name in ("release.json", "runtime_bundle.json", "video_qc_decision_policy.json", "av_sync_decision_policy.json"):
            target = visual_release / name
            if target.is_symlink() or not target.is_file():
                raise SystemExit("Visual release file is unavailable")
            target.chmod(0o440)
    state_root = Path("/run/intelligentai")
    state_root.mkdir(mode=0o750, exist_ok=True)
    os.chown(state_root, 0, 10001)
    state_root.chmod(0o750)
    for name in ("worker", "worker/work", "worker/tmp"):
        target = state_root / name
        target.mkdir(mode=0o700, exist_ok=True)
        os.chown(target, 10001, 10001)
        target.chmod(0o700)
    deployment = RUNTIME / "deployment.json"
    deployment.write_text(json.dumps({
        "sourceRevision": manifest.get("sourceRevision"),
        "overlaySha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "baseRelease": manifest["base_release"],
    }, sort_keys=True) + "\n")
    os.chown(deployment, 0, 10001)
    deployment.chmod(0o640)
    mode = os.environ.get("AI_ANALYSIS_TRANSPORT", "redis_stream")
    if mode not in {"runpod_http", "redis_stream", "disabled"}:
        raise SystemExit("analysis_transport_invalid")
    worker_preparation = app / ("deploy/runpod/prepare_http.py" if mode == "runpod_http"
                                else "deploy/runpod/prepare_worker.py")
    if mode != "disabled" and worker_preparation.is_file():
        subprocess.run([str(RUNTIME / "envs/scorer/bin/python"), str(worker_preparation)], check=True)
    print("Ephemeral runtime prepared; no worker or inference requests started", flush=True)


if __name__ == "__main__":
    main()
