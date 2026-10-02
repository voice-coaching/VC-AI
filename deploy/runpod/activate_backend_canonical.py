"""Approved Backend activation: role reader, immutable verifier, existing V33-V36.

Run on AWS as root with staged JAR/control schema. No fixtures, inference, table
changes, media deletion, default-profile changes, or FE operations.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

BASE_SHA = "86c2aa795348c716ff0319b9d014089620ae11505db703b55ee2890c63ea9f65"
BASE_VERIFIER = Path("/opt/alpha-canonical/releases/fb32b7e2d28e9f574178d8f8717086810c4cfbcbf8c4530dc50d3f14042574a3")
PYTHON = "/opt/alpha-canonical/venvs/py312-jsonschema4251-rfc014/bin/python"
ENV = Path("/etc/alpha-canonical-secrets/backend-runtime.env")
DROPIN = Path("/etc/systemd/system/alpha-backend.service.d/40-canonical.conf")


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def service_environment():
    pid = subprocess.check_output(["systemctl", "show", "alpha-backend.service", "-p", "MainPID", "--value"], text=True).strip()
    require(pid.isdigit() and int(pid) > 1, "SERVICE_NOT_RUNNING")
    names = {"DB_URL", "DB_USERNAME", "DB_PASSWORD", "AI_ANALYSIS_CALLBACK_TOKEN"}
    values = {}
    for pair in Path("/proc", pid, "environ").read_bytes().split(b"\0"):
        key, sep, value = pair.partition(b"=")
        if sep and key.decode() in names:
            values[key.decode()] = value.decode()
    return values


def query(values, sql):
    url = urllib.parse.urlsplit(values["DB_URL"].removeprefix("jdbc:"))
    require(url.scheme in ("postgres", "postgresql"), "DB_SCHEME")
    env = dict(os.environ, PGHOST=url.hostname, PGPORT=str(url.port or 5432),
        PGDATABASE=url.path.lstrip("/"), PGUSER=values["DB_USERNAME"], PGPASSWORD=values["DB_PASSWORD"],
        PGCONNECT_TIMEOUT="10", PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=10000")
    params = urllib.parse.parse_qs(url.query)
    if "sslmode" in params:
        env["PGSSLMODE"] = params["sslmode"][0]
    result = subprocess.run(["psql", "-X", "-w", "-A", "-t", "-c", sql],
        env=env, capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, "DB_READ_FAILED")
    return result.stdout.strip()


def atomic(path, raw, mode=0o600):
    temporary = path.with_name("." + path.name + ".canonical-activation")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def verifier_release(schema):
    identities = json.loads((BASE_VERIFIER / "deployment.json").read_text())
    contents = {}
    for name, digest in identities.items():
        path = BASE_VERIFIER / name
        require(path.resolve().is_relative_to(BASE_VERIFIER) and sha(path) == digest, "VERIFIER_BASE_CHANGED")
        contents[name] = path.read_bytes()
    contents["app/docs/contracts/runpod_http_control_v1_2.schema.json"] = schema.read_bytes()
    identities = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(contents.items())}
    manifest = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
    root = BASE_VERIFIER.parent / hashlib.sha256(manifest).hexdigest()
    if root.exists():
        require((root / "deployment.json").read_bytes() == manifest and
                all(sha(root / n) == d for n, d in identities.items()), "VERIFIER_RELEASE_CONFLICT")
    else:
        root.mkdir(mode=0o755)
        for name, raw in contents.items():
            path = root / name
            path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o444)
        (root / "deployment.json").write_bytes(manifest)
        (root / "deployment.json").chmod(0o444)
        for p in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            p.chmod(0o555)
        root.chmod(0o555)
    import pwd
    account = pwd.getpwnam("ec2-user")
    check = subprocess.run([PYTHON, "-I", str(root / "app/deploy/runpod/canonical_verify.py"),
        "--core-root", str(root / "frozen/core"), "--llm-root", str(root / "frozen/llm"),
        "--recordings-prefix", "recordings/", "--check-installation"],
        user=account.pw_uid, group=account.pw_gid, extra_groups=[],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, capture_output=True, timeout=20)
    require(check.returncode == 0 and json.loads(check.stdout).get("status") == "READY", "VERIFIER_NOT_READY")
    return root


def main():
    require(os.geteuid() == 0 and len(sys.argv) == 4, "ROOT_JAR_SHA_SCHEMA_REQUIRED")
    os.umask(0o077)
    jar, expected, schema = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    require(jar.parent == Path("/tmp/canonical-activation-20261002") and sha(jar) == expected, "STAGED_JAR")
    live = Path("/opt/alpha/app.jar")
    require(sha(live) == BASE_SHA, "LIVE_BASE_CHANGED")
    require(not ENV.exists() and not DROPIN.exists(), "ACTIVATION_ALREADY_CONFIGURED_RECONCILE")
    values = service_environment()
    require(query(values, "SELECT max(version::integer) FROM flyway_schema_history WHERE success") == "36", "MIGRATION_BASE")
    require(query(values, "SELECT count(*) FROM analysis_results WHERE status IN ('PENDING','PROCESSING') AND claim_expires_at>CURRENT_TIMESTAMP") == "0", "ACTIVE_WORKER_LEASE")
    require(shutil.disk_usage("/").free > 2 * 1024**3, "DISK_HEADROOM")
    # Existing read-only role only. No master/writer fallback.
    source = Path("/etc/alpha-canonical-secrets/backend-reader.json")
    require(source.stat().st_uid == 0 and source.stat().st_mode & 0o777 == 0o600, "READER_PERMISSIONS")
    secret = json.loads(source.read_text())
    require(secret["applicationKeyId"] == "0055c17e18c6bc70000000006"
            and secret["namePrefix"] == "canonical-evidence/v1/" and len(secret["bucketIds"]) == 1
            and "readFiles" in secret["capabilities"]
            and not {"writeFiles", "deleteFiles"}.intersection(secret["capabilities"])
            and secret["expirationTimestamp"] > time.time() * 1000, "READER_SCOPE")
    root = verifier_release(schema)
    settings = {
        "ANALYSIS_CANONICAL_ADMISSION_ENABLED": "true",
        "ANALYSIS_CANONICAL_EVIDENCE_B2_ENDPOINT": "https://s3.us-east-005.backblazeb2.com",
        "ANALYSIS_CANONICAL_EVIDENCE_B2_REGION": "us-east-005",
        "ANALYSIS_CANONICAL_EVIDENCE_B2_BUCKET": "speakai2026",
        "ANALYSIS_CANONICAL_EVIDENCE_B2_PREFIX": "canonical-evidence/v1/",
        "ANALYSIS_CANONICAL_EVIDENCE_B2_KEY_ID": secret["applicationKeyId"],
        "ANALYSIS_CANONICAL_EVIDENCE_B2_APPLICATION_KEY": secret["applicationKey"],
        "ANALYSIS_CANONICAL_EVIDENCE_JOURNAL_STAGING_BUDGET_BYTES": str(256 * 1024**2),
        "ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_PYTHON": PYTHON,
        "ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_SCRIPT": str(root / "app/deploy/runpod/canonical_verify.py"),
        "ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_CORE_ROOT": str(root / "frozen/core"),
        "ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_LLM_ROOT": str(root / "frozen/llm"),
        "ANALYSIS_CANONICAL_EVIDENCE_SEMANTIC_RECORDINGS_PREFIX": "recordings/",
    }
    for name in ("REGISTRATION", "VERIFIER", "CALLBACK", "CALLBACK_VERIFIER", "JOURNAL", "CALLBACK_APPLY"):
        settings["ANALYSIS_CANONICAL_EVIDENCE_" + name + "_ENABLED"] = "true"
    for name in ("RETENTION_DAYS", "DELETION_GRACE_HOURS", "ORPHAN_GRACE_HOURS"):
        settings["ANALYSIS_CANONICAL_EVIDENCE_" + name] = "INDEFINITE"
    require(all("\n" not in v and "\r" not in v and "\x00" not in v for v in settings.values()), "ENV_VALUE")
    release = Path("/opt/alpha/releases") / ("canonical-active-" + expected + ".jar")
    if release.exists():
        require(sha(release) == expected, "RELEASE_CONFLICT")
    else:
        shutil.copyfile(jar, release)
        release.chmod(0o644)
    old = live.resolve()
    backup = Path("/var/backups/alpha-canonical") / ("activation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(mode=0o700)
    (backup / "receipt.json").write_text(json.dumps({"previousJar": str(old), "previousSha256": BASE_SHA,
        "targetJar": str(release), "targetSha256": expected, "verifier": str(root)}))
    atomic(ENV, "".join(k + "=" + v + "\n" for k, v in sorted(settings.items())).encode())
    DROPIN.parent.mkdir(mode=0o755, exist_ok=True)
    atomic(DROPIN, ("[Service]\nEnvironmentFile=" + str(ENV) + "\n").encode(), 0o644)
    temporary = live.with_name(".app.jar.canonical-activation")
    require(not temporary.exists() and not temporary.is_symlink(), "JAR_SWITCH_PENDING")
    temporary.symlink_to(release)
    os.replace(temporary, live)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "alpha-backend.service"], check=True)
    print(json.dumps({"status": "BACKEND_ACTIVATION_INSTALLED", "jarSha256": expected,
        "verifier": str(root), "journalBudgetBytes": 256 * 1024**2, "backup": str(backup),
        "requiresLiveReadinessConfirmation": True}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print exception messages from secret readers, subprocess or SDKs.
        print(json.dumps({"status": "ACTIVATION_REQUIRES_RECONCILIATION", "errorType": type(error).__name__}), flush=True)
        raise SystemExit(1)
