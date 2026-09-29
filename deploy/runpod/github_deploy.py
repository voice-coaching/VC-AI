"""GitHub-hosted runner: resolve the existing Pod and send a commit delta.

No Pod create/start/reset, no private media, no model inference, no QA jobs.
Only committed source is sent. API and SSH credentials remain on the runner.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
REMOTE = "/workspace/intelligentai/deployment/github-actions"
PREFIXES = ("src/", "seungun/src/", "configs/", "scripts/", "deploy/runpod/", "docs/contracts/")
DEPENDENCIES = ("requirements/", "pyproject.toml", "seungun/pyproject.toml", "uv.lock")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("missing_configuration:" + name)
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("api_redirect_rejected")


def resolve_pod(pod_id):
    request = Request("https://rest.runpod.io/v1/pods/" + pod_id,
                      headers={"Authorization": "Bearer " + required("RUNPOD_API_KEY")})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        pod = json.load(response)
    if pod.get("id") != pod_id or pod.get("desiredStatus") != "RUNNING":
        raise RuntimeError("target_pod_not_running")
    host = str(ipaddress.ip_address(pod["publicIp"]))
    if not ipaddress.ip_address(host).is_global:
        raise RuntimeError("target_address_invalid")
    port = int(pod["portMappings"]["22"])
    if not 1 <= port <= 65535:
        raise RuntimeError("target_port_invalid")
    return host, port


def tree(revision):
    result = {}
    for entry in git("ls-tree", "-rz", revision).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        path = name.decode("utf-8")
        if path.startswith("deploy/runpod/") and path.endswith(".md"):
            continue
        if path.startswith(PREFIXES) or path.startswith(DEPENDENCIES):
            if kind != "blob" or mode not in {"100644", "100755", "120000"}:
                raise RuntimeError("unsupported_git_entry")
            result[path] = (mode, blob)
    return result


def blob(entry):
    if entry is None:
        return None
    return git("cat-file", "blob", entry[1])


def record(entry):
    content = blob(entry)
    if content is None:
        return None
    if len(content) > 8 * 1024 * 1024:
        raise RuntimeError("source_file_too_large")
    return dict(mode=entry[0], sha256=hashlib.sha256(content).hexdigest(),
                content=base64.b64encode(content).decode("ascii"))


def bundle(base, target):
    old, new = tree(base), tree(target)
    changes = []
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) == new.get(path):
            continue
        # Existing offline environments are not rebuilt from arbitrary PyPI.
        if path.startswith(DEPENDENCIES) or path == "deploy/runpod/http-requirements.lock":
            raise RuntimeError("dependency_change_requires_environment_rollout")
        if path in {"deploy/runpod/boot.sh", "deploy/runpod/run_services.sh"}:
            raise RuntimeError("boot_change_requires_bootstrap_rollout")
        changes.append(dict(path=path, before=record(old.get(path)), after=record(new.get(path))))
    document = dict(schemaVersion="intelligentai.github-deploy.v1", baseRevision=base,
                    targetRevision=target, changes=changes)
    data = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode()
    if len(data) > 48 * 1024 * 1024:
        raise RuntimeError("deployment_bundle_too_large")
    return data


def main():
    os.umask(0o077)
    pod_id, target = required("RUNPOD_POD_ID"), required("DEPLOY_SHA")
    if not re.fullmatch(r"[a-z0-9]{10,32}", pod_id) or not re.fullmatch(r"[0-9a-f]{40}", target):
        raise RuntimeError("deployment_identity_invalid")
    if git("rev-parse", "HEAD").decode().strip() != target:
        raise RuntimeError("checkout_revision_mismatch")
    host, port = resolve_pod(pod_id)
    job_id = target + "-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="runpod-deploy-") as directory:
        temporary = Path(directory)
        key = temporary / "identity"
        key.write_text(required("RUNPOD_SSH_PRIVATE_KEY") + "\n")
        key.chmod(0o600)
        host_key = required("RUNPOD_SSH_HOST_KEY")
        if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,3}", host_key):
            raise RuntimeError("host_key_must_be_pinned_ed25519")
        hosts = temporary / "known_hosts"
        hosts.write_text("runpod-" + pod_id + " " + host_key + "\n")
        options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                   "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=yes",
                   "-o", "UserKnownHostsFile=" + str(hosts), "-o", "HostKeyAlias=runpod-" + pod_id,
                   "-o", "ControlMaster=auto", "-o", "ControlPersist=60",
                   "-o", "ControlPath=" + str(temporary / "control")]

        def ssh(command):
            return subprocess.check_output(["ssh", *options, "-p", str(port), "root@" + host,
                                            command], timeout=45)

        try:
            state = json.loads(ssh("cat " + REMOTE + "/state.json"))
            base = state["gitRevision"]
            if not re.fullmatch(r"[0-9a-f]{40}", base) or state.get("podId") != pod_id:
                raise RuntimeError("deployment_enrollment_invalid")
            if base == target:
                print("This commit is already deployed; no restart requested.")
                return
            # Never silently deploy an older queued run over a newer release.
            subprocess.run(["git", "merge-base", "--is-ancestor", base, target], cwd=ROOT, check=True)
            payload = temporary / "bundle.json"
            payload.write_bytes(bundle(base, target))
            remote_job = REMOTE + "/jobs/" + job_id
            ssh("umask 077 && mkdir -p " + remote_job)
            for local, name in ((payload, "bundle.json"),
                                (ROOT / "deploy/runpod/github_deploy_remote.py", "deploy.py")):
                subprocess.run(["scp", *options, "-P", str(port), str(local),
                                "root@" + host + ":" + remote_job + "/" + name], check=True, timeout=120)
            # Detached operation survives runner/SSH cancellation. Its flock and
            # pending marker prevent a later run from racing an unfinished one.
            ssh("cd " + remote_job + " && (nohup python3 deploy.py " + pod_id
                + " > operation.log 2>&1 < /dev/null &)")
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                try:
                    raw = ssh("if test -f " + remote_job + "/result.json; then cat "
                              + remote_job + "/result.json; else printf '{}'; fi")
                    result = json.loads(raw)
                except (subprocess.SubprocessError, ValueError):
                    # Reconnect only; never re-submit an operation of unknown status.
                    time.sleep(15)
                    continue
                if result:
                    if result.get("status") != "succeeded" or result.get("revision") != target:
                        raise RuntimeError("remote_deployment_failed:" + result.get("reason", "unknown"))
                    print("Deployed " + target + " to Pod " + pod_id + "; health/readiness verified.")
                    summary = os.environ.get("GITHUB_STEP_SUMMARY")
                    if summary:
                        with open(summary, "a", encoding="utf-8") as output:
                            output.write(f"Deployed `{target}` to `{pod_id}`. Service readiness verified; no inference or QA executed.\n")
                    return
                time.sleep(15)
            raise RuntimeError("deployment_status_unknown_inspect_remote_job:" + job_id)
        finally:
            subprocess.run(["ssh", *options, "-p", str(port), "-O", "exit", "root@" + host],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print HTTP bodies, environment values, command argv or secrets.
        reason = str(error) if type(error) is RuntimeError else type(error).__name__
        print("Deployment stopped: " + reason, flush=True)
        raise SystemExit(1)
