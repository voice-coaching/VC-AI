"""Root-only, detached overlay transaction for an enrolled RunPod.

Requires the repository's boot -> manager -> supervisor process topology.
Unknown topology, source drift and incomplete operations stop before mutation.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener

DEPLOY = Path("/workspace/intelligentai/deployment")
CONTROL = DEPLOY / "github-actions"
OVERLAY = DEPLOY / "int8-overlay"
APP = Path("/opt/intelligentai/app")
MANIFEST = OVERLAY / "overlay-manifest.json"
STATE = CONTROL / "state.json"
PENDING = CONTROL / "pending.json"
PREFIXES = ("src/", "seungun/src/", "configs/", "scripts/", "deploy/runpod/", "docs/contracts/")


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".github-deploy")
    with temporary.open("wb") as target:
        os.fchmod(target.fileno(), mode)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def json_write(path, document):
    atomic(path, (json.dumps(document, sort_keys=True) + "\n").encode())


def safe_path(root, name):
    p = PurePosixPath(name)
    require(not p.is_absolute() and ".." not in p.parts and str(p) == name
            and name.startswith(PREFIXES) and "\\" not in name
            and all(part not in {".env", ".git", ".ssh", "__pycache__"} for part in p.parts),
            "unsafe_source_path")
    path = root / name
    require(path.parent.resolve().is_relative_to(root.resolve()), "source_parent_escape")
    return path


def snapshot(path):
    if path.is_symlink():
        return dict(mode="120000", content=base64.b64encode(os.readlink(path).encode()).decode())
    if not path.exists():
        return None
    require(path.is_file() and path.stat().st_size <= 8 * 1024 * 1024, "unexpected_source_file")
    return dict(mode="100755" if path.stat().st_mode & 0o111 else "100644",
                content=base64.b64encode(path.read_bytes()).decode())


def content(record):
    if record is None:
        return None
    require(record["mode"] in {"100644", "100755", "120000"}, "source_mode_invalid")
    data = base64.b64decode(record["content"], validate=True)
    require(len(data) <= 8 * 1024 * 1024, "source_too_large")
    if "sha256" in record:
        require(digest(data) == record["sha256"], "source_hash_invalid")
    if record["mode"] == "120000":
        name = data.decode("utf-8")
        require(re.fullmatch(r"[A-Za-z0-9_-]+\.md", name) is not None, "source_link_invalid")
    return data


def equivalent(first, second):
    if first is None or second is None:
        return first is second
    return first["mode"] == second["mode"] and content(first) == content(second)


def install(path, record, *, overlay=False):
    data = content(record)
    if data is None:
        path.unlink(missing_ok=True)
        return
    if record["mode"] == "120000" and not overlay:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.symlink_to(data.decode())
    else:
        atomic(path, data, 0o750 if record["mode"] == "100755" else 0o640)
        if not overlay:
            os.chown(path, 0, 10001)


def process(pid):
    try:
        root = Path("/proc") / str(pid)
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        return dict(pid=pid, ppid=int(fields[1]), start=fields[19], state=fields[0],
                    args=(root / "cmdline").read_bytes().split(b"\0"))
    except (FileNotFoundError, ProcessLookupError):
        return None


def alive(original):
    current = process(original["pid"])
    return bool(current and current["start"] == original["start"] and current["state"] != "Z")


def send(original, signum):
    require(alive(original), "process_identity_changed")
    os.kill(original["pid"], signum)


def topology():
    commands = {
        "boot": str(OVERLAY / "deploy/runpod/boot.sh").encode(),
        "manager": str(APP / "deploy/runpod/run_services.sh").encode(),
        "supervisor": str(APP / "deploy/runpod/supervise_services.py").encode(),
    }
    matches = {name: [] for name in commands}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        p = process(int(entry.name))
        if p is None or p["state"] == "Z":
            continue
        for name, command in commands.items():
            if command in p["args"]:
                matches[name].append(p)
    require(all(len(found) == 1 for found in matches.values()), "unreviewed_service_topology")
    found = {name: rows[0] for name, rows in matches.items()}
    require(found["boot"]["pid"] > 1
            and found["manager"]["ppid"] == found["boot"]["pid"]
            and found["supervisor"]["ppid"] == found["manager"]["pid"], "unreviewed_service_parentage")
    return found


def stop_managed(found):
    # Suspend the respawn loop without killing image SSH or losing its env.
    send(found["boot"], signal.SIGSTOP)
    status = json.loads(Path("/run/intelligentai/services.json").read_text())
    require(status["pid"] == found["supervisor"]["pid"], "supervisor_state_mismatch")
    children = [process(s["pid"]) for s in status["services"].values() if s.get("pid")]
    send(found["manager"], signal.SIGTERM)
    deadline = time.monotonic() + 360
    tracked = [found["manager"], found["supervisor"], *[p for p in children if p]]
    while any(alive(p) for p in tracked):
        require(time.monotonic() < deadline, "graceful_stop_timeout")
        time.sleep(2)


def boot_environment(boot):
    data = (Path("/proc") / str(boot["pid"]) / "environ").read_bytes()
    env = {}
    for item in data.split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            env[key.decode()] = value.decode()
    require(env.get("AI_ANALYSIS_TRANSPORT") == "runpod_http", "http_transport_required")
    return env


def prepare(env):
    subprocess.run(["python3", str(OVERLAY / "deploy/runpod/prepare_runtime.py")],
                   env=env, check=True, timeout=600)


def ready(revision, timeout=300):
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            # Read only the required token; never print the environment/body.
            private = Path("/run/intelligentai/analysis-http.env").read_text().splitlines()
            token = next(line.split("=", 1)[1] for line in private if line.startswith("AI_ANALYSIS_API_TOKEN="))
            request = Request("http://127.0.0.1:8080/health/services",
                              headers={"Authorization": "Bearer " + token})
            with opener.open(request, timeout=5) as response:
                state = json.load(response)
            if (state.get("status") == "ready" and state.get("executorReady") is True
                    and state.get("workerRevision") == revision):
                return
        except Exception:
            pass
        time.sleep(5)
    raise RuntimeError("service_readiness_timeout")


def enroll(base, pod):
    require(re.fullmatch(r"[0-9a-f]{40}", base) and re.fullmatch(r"[a-z0-9]{10,32}", pod),
            "enrollment_identity_invalid")
    require(not STATE.exists() and not PENDING.exists(), "already_enrolled_or_pending")
    found = topology()
    boot_environment(found["boot"])
    revision = json.loads(Path("/opt/intelligentai/deployment.json").read_text())["sourceRevision"]
    ready(revision, timeout=10)
    json_write(STATE, dict(podId=pod, gitRevision=base, runtimeRevision=revision,
                          overlaySha256=digest(MANIFEST.read_bytes())))
    print("Deployment target enrolled; no source or service changed.")


def apply(job, pod):
    require(not PENDING.exists(), "previous_operation_requires_recovery")
    state = json.loads(STATE.read_text())
    payload = job / "bundle.json"
    require(payload.stat().st_size <= 48 * 1024 * 1024, "bundle_too_large")
    document = json.loads(payload.read_bytes())
    target = document["targetRevision"]
    require(document["schemaVersion"] == "intelligentai.github-deploy.v1"
            and re.fullmatch(r"[0-9a-f]{40}", target)
            and document["baseRevision"] == state["gitRevision"]
            and state["podId"] == pod, "deployment_base_mismatch")
    original_manifest = MANIFEST.read_bytes()
    require(digest(original_manifest) == state["overlaySha256"], "overlay_changed_since_enrollment")
    manifest = json.loads(original_manifest)
    for entry in manifest["files"]:
        relative = PurePosixPath(entry["path"])
        origin = OVERLAY / relative
        require(not relative.is_absolute() and ".." not in relative.parts
                and origin.resolve().is_relative_to(OVERLAY.resolve())
                and origin.is_file() and digest(origin.read_bytes()) == entry["sha256"],
                "existing_overlay_integrity_invalid")
    old_entries = {entry["path"]: entry for entry in manifest["files"]}
    new_entries = dict(old_entries)
    deleted = set(manifest.get("deletedFiles", []))
    backups, names = {}, set()
    for change in document["changes"]:
        name = change["path"]
        require(name not in names, "duplicate_source_path")
        names.add(name)
        require(name not in {"deploy/runpod/boot.sh", "deploy/runpod/run_services.sh",
                             "deploy/runpod/http-requirements.lock"}, "bootstrap_change_requires_review")
        live, persistent = safe_path(APP, name), safe_path(OVERLAY, name)
        before, after = change["before"], change["after"]
        content(before)
        data = content(after)
        actual = snapshot(live)
        require(equivalent(actual, before) or equivalent(actual, after), "runtime_source_drift:" + name)
        if name in old_entries:
            require(persistent.is_file() and digest(persistent.read_bytes()) == old_entries[name]["sha256"],
                    "overlay_source_drift:" + name)
        else:
            require(not persistent.exists() and not persistent.is_symlink(), "unmanaged_overlay_file:" + name)
        backups[name] = dict(runtime=actual, overlay=snapshot(persistent))
        if data is None:
            new_entries.pop(name, None)
            deleted.add(name)
        else:
            if name.endswith(".py"):
                compile(data, name, "exec")
            if name.endswith(".json"):
                json.loads(data)
            if name.endswith(".sh"):
                subprocess.run(["bash", "-n"], input=data, check=True)
            new_entries[name] = dict(path=name, sha256=digest(data), bytes=len(data), mode=after["mode"])
            if after["mode"] == "120000":
                new_entries[name]["symlinkTarget"] = data.decode()
            deleted.discard(name)
    # Full preparation copies the base release first. Persisted tombstones
    # must be understood before any tracked deletion can be deployed.
    preparation = next((content(c["after"]) for c in document["changes"]
                        if c["path"] == "deploy/runpod/prepare_runtime.py"),
                       (OVERLAY / "deploy/runpod/prepare_runtime.py").read_bytes())
    require(not deleted or (preparation and b'deletedFiles' in preparation), "deletion_support_missing")
    found = topology()
    environment = boot_environment(found["boot"])
    ready(state["runtimeRevision"], timeout=15)
    json_write(job / "backup.json", dict(manifest=base64.b64encode(original_manifest).decode(), files=backups))
    json_write(PENDING, dict(job=job.name, revision=target, startedAt=datetime.now(timezone.utc).isoformat()))
    mutated, resumed, succeeded = False, False, False
    try:
        stop_managed(found)
        mutated = True
        for change in document["changes"]:
            install(safe_path(OVERLAY, change["path"]), change["after"], overlay=True)
        manifest.update(sourceRevision=target, files=list(new_entries.values()), deletedFiles=sorted(deleted))
        json_write(MANIFEST, manifest)
        prepare(environment)
        for change in document["changes"]:
            require(equivalent(snapshot(safe_path(APP, change["path"])), change["after"]),
                    "applied_source_hash_mismatch:" + change["path"])
        send(found["boot"], signal.SIGCONT)
        resumed = True
        ready(target)
        json_write(STATE, dict(podId=pod, gitRevision=target, runtimeRevision=target,
                              overlaySha256=digest(MANIFEST.read_bytes())))
        succeeded = True
        PENDING.unlink()
    except Exception:
        if mutated:
            # If preparation failed, the old generation is already stopped;
            # restore it without ever starting the incomplete new release.
            if resumed:
                deadline = time.monotonic() + 100
                rollback_topology = None
                while time.monotonic() < deadline:
                    try:
                        rollback_topology = topology()
                        break
                    except RuntimeError:
                        time.sleep(3)
                require(rollback_topology is not None, "rollback_topology_unavailable_inspect_pending")
                stop_managed(rollback_topology)
            for name, saved in backups.items():
                install(safe_path(OVERLAY, name), saved["overlay"], overlay=True)
            atomic(MANIFEST, original_manifest)
            prepare(environment)
            # Remove new files absent from the old base/overlay; restore exact
            # previous runtime mode/link as rsync deliberately does not delete.
            for name, saved in backups.items():
                install(safe_path(APP, name), saved["runtime"])
            send(found["boot"], signal.SIGCONT)
            ready(state["runtimeRevision"])
            PENDING.unlink()
        else:
            # Failed to stop safely; do not prepare over a still-live service.
            PENDING.unlink(missing_ok=True)
        raise
    finally:
        if alive(found["boot"]):
            send(found["boot"], signal.SIGCONT)
    require(succeeded, "deployment_not_complete")
    return target


def main():
    require(os.geteuid() == 0, "root_required")
    os.umask(0o077)
    CONTROL.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) == 4 and sys.argv[1] == "--enroll":
        with (CONTROL / "deployment.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            enroll(sys.argv[2], sys.argv[3])
        return
    require(len(sys.argv) == 2 and re.fullmatch(r"[a-z0-9]{10,32}", sys.argv[1]), "pod_id_required")
    job = Path(__file__).resolve().parent
    require(job.parent == CONTROL / "jobs", "deployment_job_path_invalid")
    try:
        with (CONTROL / "deployment.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            target = apply(job, sys.argv[1])
            result = dict(status="succeeded", revision=target)
    except Exception as error:
        reason = str(error) if type(error) is RuntimeError else type(error).__name__
        result = dict(status="failed", reason=reason)
    json_write(job / "result.json", result)
    require(result["status"] == "succeeded", "deployment_failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Deployment stopped: " + (str(error) if type(error) is RuntimeError else type(error).__name__))
        raise SystemExit(1)
