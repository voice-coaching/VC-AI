"""Supervise the private RunPod services without logging configuration or secrets.

The root-owned JSON configuration is loaded once; restart the supervisor to
apply changes. Missing configuration and per-service environment files are
retried without restarting services whose prerequisites are already available.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


SERVICE_NAMES = frozenset({"clova", "api", "analysis-worker"})
RUNTIME_DIRECTORY = Path("/run/intelligentai")
STATUS_PATH = RUNTIME_DIRECTORY / "services.json"
LOCK_PATH = RUNTIME_DIRECTORY / "supervisor.lock"
CONFIG_SCHEMA = "intelligentai.service-supervisor.v1"
POLL_SECONDS = 2.0
RETRY_SECONDS = 5.0
MAXIMUM_ENV_BYTES = 131_072
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SECRET_NAME = re.compile(r"secret|token|password|credential|private.?key|api.?key|access.?key|(?:^|_)keys?(?:_|$)|dsn", re.I)
INHERITED_ENVIRONMENT = frozenset({
    "PATH", "LD_LIBRARY_PATH", "CUDA_HOME", "CUDA_PATH", "CUDA_VISIBLE_DEVICES",
    "CUDA_DEVICE_ORDER", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES",
    "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
})
CHILD_SETUP = """import ctypes, os, signal, sys
try:
    parent_pid = int(sys.argv[1])
    command = sys.argv[2:]
    libc = ctypes.CDLL(None, use_errno=True)
    if not command or not os.path.isabs(command[0]):
        raise ValueError
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        raise OSError
    os.execv(command[0], command)
except Exception:
    print('child_setup_failed', file=sys.stderr, flush=True)
    sys.exit(78)
"""


class ConfigurationError(Exception):
    """Only fixed reason codes are exposed to the status file and console."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def emit(service: str, event: str, reason: str) -> None:
    print(json.dumps({"atUtc": utc_now(), "service": service, "event": event, "reason": reason}), flush=True)


def absolute_path(value: object) -> Path:
    if not isinstance(value, str) or "\0" in value or not Path(value).is_absolute():
        raise ConfigurationError("absolute_path_required")
    return Path(value)


def bounded_number(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ConfigurationError("numeric_setting_invalid")
    return float(value)


def read_private_file(path: Path, maximum: int, *, owner_only: bool, owner: int = 0) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        forbidden_mode = 0o077 if owner_only else 0o022
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner
                or metadata.st_mode & forbidden_mode or metadata.st_nlink != 1
                or metadata.st_size > maximum):
            raise ConfigurationError("private_file_permissions_invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(maximum + 1)
        if len(payload) > maximum:
            raise ConfigurationError("private_file_too_large")
        return payload
    finally:
        os.close(descriptor)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("duplicate_configuration_key")
        result[key] = value
    return result


@dataclass(repr=False)
class ServiceSpec:
    name: str
    command: list[str]
    cwd: Path
    environment: dict[str, str]
    env_file: Path | None
    env_file_required: bool
    required_env: list[str]
    dependencies: list[str]
    health: dict
    startup_timeout: float
    stop_timeout: float
    log_path: Path
    log_maximum: int
    log_backups: int
    run_as_user: int | None
    run_as_group: int | None

    @classmethod
    def parse(cls, document: object) -> ServiceSpec:
        if not isinstance(document, dict):
            raise ConfigurationError("service_configuration_invalid")
        allowed = {"name", "command", "cwd", "env", "envFile", "requiredEnv", "dependsOn",
                   "health", "startupTimeoutSeconds", "stopTimeoutSeconds", "log", "runAsUser", "runAsGroup"}
        if document.keys() - allowed or document.get("name") not in SERVICE_NAMES:
            raise ConfigurationError("service_configuration_invalid")
        name = document["name"]
        run_as_user, run_as_group = document.get("runAsUser"), document.get("runAsGroup")
        if (run_as_user is not None or run_as_group is not None) and (
                type(run_as_user) is not int or type(run_as_group) is not int
                or not 1 <= run_as_user <= 2**31 - 1 or not 1 <= run_as_group <= 2**31 - 1):
            raise ConfigurationError("service_identity_invalid")
        command = document.get("command")
        if (not isinstance(command, list) or not 1 <= len(command) <= 100
                or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 8192 for arg in command)):
            raise ConfigurationError("command_arguments_invalid")
        absolute_path(command[0])
        environment = document.get("env", {})
        if (not isinstance(environment, dict)
                or any(not ENV_NAME.fullmatch(key) or not isinstance(value, str) or "\0" in value
                       for key, value in environment.items())
                or len(json.dumps(environment)) > MAXIMUM_ENV_BYTES):
            raise ConfigurationError("environment_configuration_invalid")
        env_file = document.get("envFile")
        env_path, env_required = None, True
        if env_file is not None:
            if (not isinstance(env_file, dict) or env_file.keys() - {"path", "required"}
                    or type(env_file.get("required", True)) is not bool):
                raise ConfigurationError("environment_file_configuration_invalid")
            env_path = absolute_path(env_file.get("path"))
            env_required = env_file.get("required", True)
        required_env = document.get("requiredEnv", [])
        if (not isinstance(required_env, list)
                or any(not isinstance(key, str) or not ENV_NAME.fullmatch(key) for key in required_env)):
            raise ConfigurationError("required_environment_configuration_invalid")
        dependencies = document.get("dependsOn", [])
        if (not isinstance(dependencies, list)
                or any(dependency not in SERVICE_NAMES or dependency == name for dependency in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ConfigurationError("service_dependencies_invalid")
        health = document.get("health")
        if not isinstance(health, dict) or health.get("type") not in {"http", "file"}:
            raise ConfigurationError("service_health_configuration_invalid")
        health = dict(health)
        health_allowed = {"type", "url", "path", "jsonField", "acceptedValues", "timeoutSeconds",
                          "maximumAgeSeconds", "schemaVersion", "pidField"}
        accepted = health.get("acceptedValues", ["ready", "ok"])
        if (health.keys() - health_allowed or not isinstance(health.get("jsonField", "status"), str)
                or not isinstance(accepted, list) or not accepted
                or any(type(value) not in (str, bool) for value in accepted)):
            raise ConfigurationError("service_health_configuration_invalid")
        health["acceptedValues"] = accepted
        health["jsonField"] = health.get("jsonField", "status")
        if health["type"] == "http":
            try:
                url = urlsplit(health["url"])
                if (url.scheme != "http" or not ipaddress.ip_address(url.hostname).is_loopback
                        or url.path != "/health" or url.query or url.fragment or url.username or url.password):
                    raise ValueError
                _ = url.port
            except (KeyError, TypeError, ValueError):
                raise ConfigurationError("health_url_must_be_loopback") from None
            health["timeoutSeconds"] = bounded_number(health.get("timeoutSeconds", 3), 0.1, 10)
        else:
            absolute_path(health.get("path"))
            health["maximumAgeSeconds"] = bounded_number(health.get("maximumAgeSeconds", 600), 5, 3600)
        if "schemaVersion" in health and not isinstance(health["schemaVersion"], str):
            raise ConfigurationError("service_health_configuration_invalid")
        if "pidField" in health and (not isinstance(health["pidField"], str) or not health["pidField"]):
            raise ConfigurationError("service_health_configuration_invalid")
        log = document.get("log", {})
        if not isinstance(log, dict) or log.keys() - {"path", "maximumBytes", "backupCount"}:
            raise ConfigurationError("service_log_configuration_invalid")
        return cls(
            name=name, command=command, cwd=absolute_path(document.get("cwd")),
            environment=environment, env_file=env_path, env_file_required=env_required,
            required_env=required_env, dependencies=dependencies, health=health,
            startup_timeout=bounded_number(document.get("startupTimeoutSeconds", 900), 900 if name == "clova" else 10, 3600),
            stop_timeout=bounded_number(document.get("stopTimeoutSeconds", 300), 1, 300),
            log_path=absolute_path(log.get("path", f"/var/log/intelligentai/{name}.log")),
            log_maximum=int(bounded_number(log.get("maximumBytes", 10 * 1024 * 1024), 65_536, 100 * 1024 * 1024)),
            log_backups=int(bounded_number(log.get("backupCount", 3), 1, 10)),
            run_as_user=run_as_user, run_as_group=run_as_group,
        )


def load_configuration(path: Path) -> list[ServiceSpec]:
    document = json.loads(read_private_file(path, 1_048_576, owner_only=False), object_pairs_hook=unique_object)
    if (not isinstance(document, dict) or document.keys() - {"schemaVersion", "services"}
            or document.get("schemaVersion") != CONFIG_SCHEMA or not isinstance(document.get("services"), list)
            or not 1 <= len(document["services"]) <= len(SERVICE_NAMES)):
        raise ConfigurationError("configuration_schema_invalid")
    pending = [ServiceSpec.parse(value) for value in document["services"]]
    if len({spec.name for spec in pending}) != len(pending):
        raise ConfigurationError("duplicate_service_name")
    ordered = []
    while pending:
        available = {spec.name for spec in ordered}
        candidates = [spec for spec in pending if set(spec.dependencies) <= available]
        if not candidates:
            raise ConfigurationError("service_dependency_cycle_or_missing")
        for spec in candidates:
            ordered.append(spec)
            pending.remove(spec)
    if len({spec.log_path for spec in ordered}) != len(ordered):
        raise ConfigurationError("service_log_paths_must_be_distinct")
    return ordered


def child_environment(spec: ServiceSpec) -> tuple[dict[str, str], list[bytes]]:
    environment = {key: value for key, value in os.environ.items() if key in INHERITED_ENVIRONMENT}
    environment.update(spec.environment)
    private_values = []
    if spec.env_file is not None:
        try:
            payload = read_private_file(spec.env_file, MAXIMUM_ENV_BYTES, owner_only=True).decode("utf-8")
        except FileNotFoundError:
            if spec.env_file_required:
                raise ConfigurationError("environment_file_missing") from None
            payload = ""
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not ENV_NAME.fullmatch(key):
                raise ConfigurationError("environment_file_invalid")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ConfigurationError("environment_file_invalid")
                value = value[1:-1]
            if "\0" in value:
                raise ConfigurationError("environment_file_invalid")
            # Literal values only: no shell evaluation, interpolation or escapes.
            environment[key] = value
    if any(not environment.get(key) for key in spec.required_env):
        raise ConfigurationError("required_environment_missing")
    private_values.extend(value.encode("utf-8") for key, value in environment.items()
                          if value and SECRET_NAME.search(key))
    for key, value in environment.items():
        if SECRET_NAME.search(key) and value.lstrip().startswith(("{", "[")):
            try:
                pending = [json.loads(value)]
                while pending:
                    item = pending.pop()
                    if isinstance(item, dict):
                        pending.extend(item.values())
                    elif isinstance(item, list):
                        pending.extend(item)
                    elif isinstance(item, str) and item:
                        private_values.append(item.encode("utf-8"))
            except ValueError:
                pass
    for value in environment.values():
        if "://" in value:
            try:
                if urlsplit(value).username is not None:
                    private_values.append(value.encode("utf-8"))
            except ValueError:
                pass
    environment.setdefault("PYTHONUNBUFFERED", "1")
    return environment, sorted(set(private_values), key=len, reverse=True)


class SecretRedactor:
    """Keep enough trailing bytes to redact secrets split across pipe reads."""

    def __init__(self, secrets: list[bytes]) -> None:
        self.secrets = secrets
        self.keep = max((len(value) for value in secrets), default=1) - 1
        self.pending = b""

    def feed(self, payload: bytes, *, final: bool = False) -> bytes:
        self.pending += payload
        boundary = len(self.pending) if final else max(0, len(self.pending) - self.keep)
        position = 0
        output = []
        while position < boundary:
            matches = [(self.pending.find(secret, position), secret) for secret in self.secrets]
            matches = [(index, secret) for index, secret in matches if position <= index < boundary]
            if not matches:
                output.append(self.pending[position:boundary])
                position = boundary
                break
            index, secret = min(matches, key=lambda match: (match[0], -len(match[1])))
            output.extend((self.pending[position:index], b"[REDACTED]"))
            position = index + len(secret)
        self.pending = self.pending[position:]
        return b"".join(output)


class RotatingLog:
    """Only the pump owns the file descriptor; rotation never changes child FDs."""

    def __init__(self, spec: ServiceSpec) -> None:
        self.spec = spec
        spec.log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.descriptor = self.open_file()
        self.size = os.fstat(self.descriptor).st_size
        if self.size > spec.log_maximum:
            os.ftruncate(self.descriptor, 0)
            self.size = 0

    def open_file(self) -> int:
        descriptor = os.open(self.spec.log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1:
            os.close(descriptor)
            raise ConfigurationError("log_file_permissions_invalid")
        os.fchmod(descriptor, 0o600)
        return descriptor

    def write(self, payload: bytes) -> None:
        while payload:
            if self.size >= self.spec.log_maximum:
                os.close(self.descriptor)
                self.descriptor = -1
                for index in range(self.spec.log_backups, 0, -1):
                    source = self.spec.log_path if index == 1 else Path(f"{self.spec.log_path}.{index - 1}")
                    target = Path(f"{self.spec.log_path}.{index}")
                    if source.exists():
                        os.replace(source, target)
                self.descriptor = self.open_file()
                self.size = 0
            chunk = payload[:self.spec.log_maximum - self.size]
            written = os.write(self.descriptor, chunk)
            if written <= 0:
                raise OSError("log_write_failed")
            self.size += written
            payload = payload[written:]

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def pump_log(pipe, writer: RotatingLog, secrets: list[bytes], failed: threading.Event) -> None:
    redactor = SecretRedactor(secrets)
    pending = b""
    suppress_traceback = False
    suppress_long_line = False

    def write_lines(payload: bytes, *, final: bool = False) -> None:
        nonlocal pending, suppress_traceback, suppress_long_line
        pending += payload
        while b"\n" in pending or (final and pending):
            line, separator, pending = pending.partition(b"\n")
            if suppress_long_line:
                suppress_long_line = False
                continue
            if b"Traceback (most recent call last)" in line or b"During handling of the above exception" in line:
                suppress_traceback = True
                writer.write(b"[child traceback suppressed]\n")
                continue
            if suppress_traceback:
                if line and not line[:1].isspace() and re.match(rb"[A-Za-z_][\w.]*:", line):
                    suppress_traceback = False
                continue
            if len(line) > 16_384:
                writer.write(b"[oversized child log line suppressed]\n")
                continue
            writer.write(line + (separator or b"\n"))
        if len(pending) > 16_384:
            pending = b""
            suppress_long_line = True
            writer.write(b"[oversized child log line suppressed]\n")

    try:
        while True:
            chunk = os.read(pipe.fileno(), 65_536)
            if not chunk:
                write_lines(redactor.feed(b"", final=True), final=True)
                break
            write_lines(redactor.feed(chunk))
    except Exception:
        failed.set()
        # Continue draining after log failure so the child cannot block on a full pipe.
        try:
            while os.read(pipe.fileno(), 65_536):
                pass
        except OSError:
            pass
    finally:
        pipe.close()
        try:
            writer.close()
        except OSError:
            failed.set()


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


HTTP_CLIENT = build_opener(ProxyHandler({}), NoRedirects())


def health_ready(spec: ServiceSpec, pid: int, started_at: float) -> bool:
    health = spec.health
    try:
        if health["type"] == "http":
            with HTTP_CLIENT.open(health["url"], timeout=health["timeoutSeconds"]) as response:
                if response.status != 200:
                    return False
                raw = response.read(65_537)
            if len(raw) > 65_536:
                return False
        else:
            raw = read_private_file(Path(health["path"]), 16_384, owner_only=True,
                                    owner=spec.run_as_user if spec.run_as_user is not None else 0)
        document = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(document, dict):
            return False
        if health.get("schemaVersion") and document.get("schemaVersion") != health["schemaVersion"]:
            return False
        if health.get("pidField"):
            reported_pid = document.get(health["pidField"])
            if type(reported_pid) is not int or reported_pid != pid:
                return False
        if health["type"] == "file":
            if type(document.get("pid")) is not int or document["pid"] != pid:
                return False
            last_poll = datetime.fromisoformat(document["lastPollAtUtc"].replace("Z", "+00:00"))
            if last_poll.tzinfo is None:
                return False
            timestamp = last_poll.timestamp()
            if timestamp < started_at or not 0 <= time.time() - timestamp <= health["maximumAgeSeconds"]:
                return False
        value = document
        for part in health["jsonField"].split("."):
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
        return any(type(value) is type(accepted) and value == accepted for accepted in health["acceptedValues"])
    except Exception:
        return False


@dataclass(repr=False)
class Service:
    spec: ServiceSpec
    process: subprocess.Popen | None = None
    log_thread: threading.Thread | None = None
    log_failed: threading.Event = field(default_factory=threading.Event)
    state: str = "blocked"
    reason: str = "waiting_for_start"
    failures: int = 0
    restart_count: int = 0
    next_start: float = 0
    started: float = 0
    started_wall: float = 0
    ready_since: float = 0
    health_failures: int = 0
    stop_deadline: float = 0
    restart_after_stop: bool = False
    last_exit_code: int | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.health_failures == 0

    def transition(self, state: str, reason: str) -> None:
        if (state, reason) != (self.state, self.reason):
            self.state, self.reason = state, reason
            emit(self.spec.name, state, reason)

    def start(self, environment: dict[str, str], secrets: list[bytes]) -> None:
        writer = RotatingLog(self.spec)
        try:
            command = [sys.executable, "-c", CHILD_SETUP, str(os.getpid()), *self.spec.command]
            self.started_wall = time.time()
            self.process = subprocess.Popen(command, cwd=self.spec.cwd, env=environment,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                                            user=self.spec.run_as_user, group=self.spec.run_as_group,
                                            extra_groups=[self.spec.run_as_group] if self.spec.run_as_group is not None else [],
                                            umask=0o077)
        except Exception:
            writer.close()
            raise
        self.started = time.monotonic()
        self.ready_since = 0
        self.health_failures = 0
        self.log_failed = threading.Event()
        self.log_thread = threading.Thread(target=pump_log,
                                           args=(self.process.stdout, writer, secrets, self.log_failed),
                                           name=f"{self.spec.name}-log", daemon=True)
        try:
            self.log_thread.start()
        except Exception:
            self.log_failed.set()
            self.log_thread = None
            self.process.stdout.close()
            writer.close()
            self.stop("child_log_unavailable", restart=True)
            raise
        self.transition("starting", "waiting_for_health")

    def stop(self, reason: str, *, restart: bool) -> None:
        if self.process is None or self.state == "stopping":
            return
        self.restart_after_stop = restart
        self.stop_deadline = time.monotonic() + self.spec.stop_timeout
        self.transition("stopping", reason)
        self.signal_group(signal.SIGTERM)

    def signal_group(self, signum: int) -> None:
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signum)
            except ProcessLookupError:
                pass

    def finish_stop(self) -> None:
        if self.process is None:
            return
        exit_code = self.process.poll()
        try:
            os.killpg(self.process.pid, 0)
            group_alive = True
        except ProcessLookupError:
            group_alive = False
        if time.monotonic() >= self.stop_deadline and group_alive:
            self.signal_group(signal.SIGKILL)
        if exit_code is None or group_alive:
            return
        self.last_exit_code = exit_code
        self.process = None
        if self.log_thread is not None:
            self.log_thread.join(timeout=1)
        if self.restart_after_stop:
            self.failures += 1
            self.restart_count += 1
            self.next_start = time.monotonic() + min(300, 10 * 2 ** min(self.failures - 1, 5))
            self.transition("backoff", "restart_delay")
        else:
            self.transition("blocked", self.reason)


def reap_adopted_children(services: list[Service]) -> None:
    """Reap orphaned descendants without consuming a managed Popen exit status."""
    registered_pids = {service.process.pid for service in services if service.process is not None}
    # Descendants can create their own process groups (for example Seungun).
    # Enumerate all of our threads' direct children instead of a service PGID.
    adopted_pids: set[int] = set()
    for children_file in Path("/proc/self/task").glob("*/children"):
        try:
            adopted_pids.update(int(pid) for pid in children_file.read_text(encoding="ascii").split())
        except (OSError, ValueError):
            continue  # A logging thread can disappear while /proc is scanned.
    for pid in adopted_pids - registered_pids:
        if pid > 0:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError, InterruptedError):
                pass


def write_status(services: list[Service], configuration_reason: str, stopping: bool) -> None:
    ready = bool(services) and not configuration_reason and not stopping and all(s.ready for s in services)
    status = "stopping" if stopping else "ready" if ready else "degraded" if services else "blocked"
    document = {
        "schemaVersion": "intelligentai.service-supervisor-state.v1", "pid": os.getpid(),
        "updatedAtUtc": utc_now(), "status": status, "ready": ready,
        "configuration": {"state": "blocked" if configuration_reason else "loaded", "reason": configuration_reason or "none"},
        "services": {service.spec.name: {
            "state": service.state, "ready": service.ready,
            "pid": service.process.pid if service.process else None, "reason": service.reason,
            "consecutiveHealthFailures": service.health_failures,
            "restartCount": service.restart_count, "lastExitCode": service.last_exit_code,
            "retryInSeconds": max(0, round(service.next_start - time.monotonic())),
        } for service in services},
    }
    temporary = STATUS_PATH.with_name(f".{STATUS_PATH.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as target:
            json.dump(document, target, ensure_ascii=True, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, STATUS_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def supervise(config_path: Path, parent_pid: int) -> int:
    services: list[Service] = []
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stopping.set())
    # Install the graceful handler before binding to the manager's lifetime.
    # An explicit parent PID also detects manager death during Python startup.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        emit("supervisor", "blocked", "parent_death_signal_unavailable")
        return 2
    if os.getppid() != parent_pid:
        stopping.set()
    configuration_reason = "configuration_not_loaded"
    retry_at = 0.0
    try:
        while True:
            reap_adopted_children(services)
            if not services and not stopping.is_set() and time.monotonic() >= retry_at:
                try:
                    services = [Service(spec) for spec in load_configuration(config_path)]
                    configuration_reason = ""
                except FileNotFoundError:
                    configuration_reason = "configuration_missing"
                except Exception:
                    configuration_reason = "configuration_invalid"
                retry_at = time.monotonic() + RETRY_SECONDS
            by_name = {service.spec.name: service for service in services}
            if stopping.is_set():
                for service in reversed(services):
                    service.stop("supervisor_stopping", restart=False)
            for service in services:
                if service.state == "stopping":
                    service.finish_stop()
                    continue
                if stopping.is_set():
                    continue
                dependencies_ready = all(by_name[name].ready for name in service.spec.dependencies)
                if service.process is not None:
                    if service.process.poll() is not None:
                        service.stop("child_exited", restart=True)
                    elif not dependencies_ready:
                        service.stop("dependency_not_ready", restart=False)
                    elif service.log_failed.is_set():
                        service.stop("child_log_unavailable", restart=True)
                    elif health_ready(service.spec, service.process.pid, service.started_wall):
                        service.health_failures = 0
                        service.transition("ready", "health_ready")
                        service.ready_since = service.ready_since or time.monotonic()
                        if time.monotonic() - service.ready_since >= 60:
                            service.failures = 0
                    elif service.state == "ready":
                        service.health_failures += 1
                        service.ready_since = 0
                        if service.health_failures >= 3:
                            service.stop("health_check_failed", restart=True)
                        else:
                            service.transition("ready", "health_check_retry")
                    elif time.monotonic() - service.started >= service.spec.startup_timeout:
                        service.stop("startup_timeout", restart=True)
                    continue
                if not dependencies_ready:
                    service.transition("blocked", "dependency_not_ready")
                    continue
                if time.monotonic() < service.next_start:
                    continue
                try:
                    environment, secrets = child_environment(service.spec)
                    if not service.spec.cwd.is_dir() or not os.access(service.spec.command[0], os.X_OK):
                        raise ConfigurationError("runtime_files_missing")
                    service.start(environment, secrets)
                except ConfigurationError as error:
                    service.next_start = time.monotonic() + RETRY_SECONDS
                    service.transition("blocked", str(error))
                except Exception:
                    if service.process is not None:
                        service.stop("child_start_failed", restart=True)
                    else:
                        service.failures += 1
                        service.next_start = time.monotonic() + min(300, 10 * 2 ** min(service.failures - 1, 5))
                        service.transition("backoff", "child_start_failed")
            reap_adopted_children(services)
            write_status(services, configuration_reason, stopping.is_set())
            if stopping.is_set() and all(service.process is None for service in services):
                return 0
            time.sleep(POLL_SECONDS)
    finally:
        for service in reversed(services):
            service.stop("supervisor_stopping", restart=False)
        while any(service.process is not None for service in services):
            reap_adopted_children(services)
            for service in services:
                if service.process is not None:
                    service.finish_stop()
            time.sleep(0.2)
        reap_adopted_children(services)


def main() -> int:
    initial_parent_pid = os.getppid()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--parent-pid", type=int, default=initial_parent_pid,
                        help="Expected manager PID; defaults to the current parent for standalone execution.")
    arguments = parser.parse_args()
    if os.geteuid() != 0 or arguments.config is None or not arguments.config.is_absolute():
        emit("supervisor", "blocked", "root_and_absolute_config_required")
        return 2
    if arguments.parent_pid <= 0:
        emit("supervisor", "blocked", "parent_pid_invalid")
        return 2
    RUNTIME_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = RUNTIME_DIRECTORY.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
        emit("supervisor", "blocked", "runtime_directory_permissions_invalid")
        return 2
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("supervisor", "blocked", "supervisor_already_running")
            return 0
        os.fchmod(descriptor, 0o600)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            emit("supervisor", "blocked", "child_subreaper_unavailable")
            return 2
        return supervise(arguments.config, arguments.parent_pid)
    except Exception:
        emit("supervisor", "failed", "supervisor_internal_error")
        return 1
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        emit("supervisor", "failed", "supervisor_startup_failed")
        exit_code = 1
    raise SystemExit(exit_code)
