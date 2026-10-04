"""Additive analysis-only overlay transaction; preserve gateway, TTS and R/S7.

Caller must confirm Backend has no active worker lease before invoking. No
generation or automated acceptance tests. Admission is gated by Backend readiness.
"""
import base64
from datetime import datetime, UTC
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener

APP = Path("/opt/intelligentai/app")
DEPLOY = Path("/workspace/intelligentai/deployment")
OVERLAY = DEPLOY / "int8-overlay"
MANIFEST = OVERLAY / "overlay-manifest.json"
CONTROL = DEPLOY / "github-actions"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def canonical_ready(timeout=120):
    """Authenticated operational readiness only; never sends an analysis job."""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            token = next(line.split("=", 1)[1] for line in
                Path("/run/intelligentai/analysis-http.env").read_text().splitlines()
                if line.startswith("AI_ANALYSIS_API_TOKEN="))
            with opener.open(Request("http://127.0.0.1:8080/health/handoff",
                    headers={"Authorization": "Bearer " + token}), timeout=5) as response:
                state = json.load(response)
            if state.get("executorConfigured") is True and state.get("admissionEnabled") is True:
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("canonical_readiness_timeout")


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 3, "root_bundle_digest_required")
    os.umask(0o077)
    raw = Path(sys.argv[1]).read_bytes()
    require(sha(raw) == sys.argv[2], "bundle_digest_changed")
    bundle = json.loads(raw)
    require(re.fullmatch(r"[0-9a-f]{40}", bundle["sourceGitRevision"]) is not None,
            "source_git_revision_required")
    spec = importlib.util.spec_from_file_location("deployer", APP / "deploy/runpod/github_deploy_remote.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    with (CONTROL / "deployment.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not (CONTROL / "pending.json").exists(), "automatic_deployment_pending")
        old_manifest = MANIFEST.read_bytes()
        require(sha(old_manifest) == bundle["baseOverlaySha256"], "base_overlay_changed")
        manifest = json.loads(old_manifest)
        require(manifest["sourceRevision"] == bundle["baseRevision"], "base_revision_changed")
        records = {}
        for row in bundle["files"]:
            path = row["path"]
            require(path not in records and path.startswith(
                ("src/voice_coach/backend_analysis/", "deploy/runpod/", "docs/contracts/"))
                and ".." not in Path(path).parts and not Path(path).is_absolute(), "source_path_invalid")
            data = base64.b64decode(row["content"], validate=True)
            require(sha(data) == row["sha256"], "source_digest_invalid")
            for root in (APP, OVERLAY):
                previous = row["beforeSha256"]
                target = root / path
                if previous is None:
                    require(not target.exists() and not target.is_symlink(), "new_source_exists")
                else:
                    require(target.is_file() and not target.is_symlink()
                            and sha(target.read_bytes()) == previous, "source_drift")
            if path.endswith(".py"):
                compile(data, path, "exec")
            records[path] = data
        old_deployment = Path("/opt/intelligentai/deployment.json").read_bytes()
        old_state = (CONTROL / "state.json").read_bytes()
        original = {}
        for path in records:
            original[path] = {}
            for kind, root in (("app", APP), ("overlay", OVERLAY)):
                p = root / path
                require(not p.is_symlink(), "unexpected_symlink")
                original[path][kind] = base64.b64encode(p.read_bytes()).decode() if p.exists() else None
        backup = DEPLOY / ("canonical-runtime-backup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
        backup.mkdir(exist_ok=False)
        (backup / "sources.json").write_text(json.dumps(original))
        (backup / "overlay-manifest.json").write_bytes(old_manifest)
        (backup / "deployment.json").write_bytes(old_deployment)
        (backup / "state.json").write_bytes(old_state)
        (backup / "target-bundle.json").write_bytes(raw)
        topology = helper.topology()
        boot_env = helper.boot_environment(topology["boot"])
        stopped = False
        resumed = False
        try:
            helper.stop_managed(topology)
            stopped = True
            for path, data in records.items():
                for root in (OVERLAY, APP):
                    helper.atomic(root / path, data, 0o640)
                    if root == APP:
                        os.chown(root / path, 0, 10001)
            entries = {entry["path"]: entry for entry in manifest["files"]}
            for path, data in records.items():
                entries[path] = {"path": path, "sha256": sha(data), "mode": "100644"}
            manifest["files"] = [entries[p] for p in sorted(entries)]
            manifest["canonicalIntegration"] = {
                "bundleSha256": sha(raw), "admissionMode": "BACKEND_READINESS_GATED",
                "baseGitRevision": bundle["baseRevision"],
                "sourceGitRevision": bundle["sourceGitRevision"],
                "deployedAt": datetime.now(UTC).isoformat()}
            helper.atomic(MANIFEST, (json.dumps(manifest, sort_keys=True) + "\n").encode(), 0o640)
            metadata = json.loads(old_deployment)
            metadata["overlaySha256"] = sha(MANIFEST.read_bytes())
            metadata["canonicalIntegration"] = manifest["canonicalIntegration"]
            helper.atomic(Path("/opt/intelligentai/deployment.json"),
                          (json.dumps(metadata, sort_keys=True) + "\n").encode(), 0o640)
            os.chown("/opt/intelligentai/deployment.json", 0, 10001)
            result = subprocess.run(["/opt/intelligentai/envs/scorer/bin/python",
                str(APP / "deploy/runpod/prepare_http.py")], env=boot_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
            require(result.returncode == 0, "http_preparation_failed")
            env = dict(line.split("=", 1) for line in
                Path("/run/intelligentai/analysis-http.env").read_text().splitlines() if "=" in line)
            require(env.get("AI_ANALYSIS_HTTP_BOOTSTRAP_READY") == "true", "legacy_bootstrap_failed")
            require(env.get("AI_CANONICAL_BOOTSTRAP_READY") == "true", "canonical_bootstrap_failed")
            state = json.loads(old_state)
            state["overlaySha256"] = metadata["overlaySha256"]
            helper.atomic(CONTROL / "state.json", (json.dumps(state, sort_keys=True) + "\n").encode())
            helper.send(topology["boot"], signal.SIGCONT)
            resumed = True
            helper.ready(bundle["baseRevision"], timeout=300)
            canonical_ready()
            for path, data in records.items():
                require((APP / path).read_bytes() == data and (OVERLAY / path).read_bytes() == data,
                        "installed_source_mismatch")
            print(json.dumps({"status": "DEPLOYED_BACKEND_GATED",
                "canonicalBootstrapReady": env.get("AI_CANONICAL_BOOTSTRAP_READY") == "true",
                "handoffConfigured": env.get("AI_CANONICAL_HANDOFF_ENABLED") == "true",
                "overlaySha256": metadata["overlaySha256"], "bundleSha256": sha(raw),
                "backup": str(backup), "files": len(records)}), flush=True)
        except BaseException:
            # Never restore files over a live new generation after readiness failure.
            if stopped:
                if resumed:
                    helper.stop_managed(helper.topology())
                for path, versions in original.items():
                    for kind, root in (("app", APP), ("overlay", OVERLAY)):
                        if versions[kind] is None:
                            (root / path).unlink(missing_ok=True)
                        else:
                            helper.atomic(root / path, base64.b64decode(versions[kind]), 0o640)
                            if kind == "app":
                                os.chown(root / path, 0, 10001)
                helper.atomic(MANIFEST, old_manifest, 0o640)
                helper.atomic(Path("/opt/intelligentai/deployment.json"), old_deployment, 0o640)
                os.chown("/opt/intelligentai/deployment.json", 0, 10001)
                helper.atomic(CONTROL / "state.json", old_state)
                subprocess.run(["/opt/intelligentai/envs/scorer/bin/python",
                    str(APP / "deploy/runpod/prepare_http.py")], env=boot_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=True)
                helper.send(topology["boot"], signal.SIGCONT)
                helper.ready(bundle["baseRevision"], timeout=300)
            raise
        finally:
            if helper.alive(topology["boot"]):
                helper.send(topology["boot"], signal.SIGCONT)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "DEPLOYMENT_REQUIRES_RECONCILIATION",
                          "errorType": type(error).__name__,
                          "reason": str(error) if type(error) is RuntimeError and
                          re.fullmatch(r"[a-z_]{1,80}", str(error)) else None}), flush=True)
        raise SystemExit(1)
