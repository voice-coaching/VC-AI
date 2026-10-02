"""Private integration with the immutable canonical STEP7 expression bridge.

Not an HTTP DTO or a replacement for the legacy production factory. The caller
must be the admitted executor, after a trusted canonical producer has finished.
The full core remains private; no coercion into callback v3 is provided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import sys
import threading
from types import ModuleType
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .http_models import AnalysisInput
    from .ports import MaterializedAudio


CORE_MANIFEST_SHA256 = "95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2"
LLM_MANIFEST_SHA256 = "1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73"
LLM_LOCK_SHA256 = "d8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01"
MAX_CORE_BYTES = 16 * 1024 * 1024
_IMPORT_LOCK = threading.Lock()


class CanonicalIntegrationError(ValueError):
    """Stable code only: exception text must not carry a script, path or key."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CanonicalIntegrationError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def finite(value):
        result = float(value)
        _require(math.isfinite(result), "NONFINITE_JSON")
        return result

    def invalid(_):
        raise CanonicalIntegrationError("NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_float=finite, parse_constant=invalid)


def _contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    _require(path.is_relative_to(root) and path.is_file(), "ARTIFACT_PATH_INVALID")
    return path


def _pinned_json(root: Path, relative: str, expected: str):
    raw = _contained_file(root, relative).read_bytes()
    _require(_sha(raw) == expected, "FROZEN_MANIFEST_MISMATCH")
    return _json(raw)


@dataclass(frozen=True, slots=True)
class FrozenCanonicalArtifacts:
    """Roots are server configuration, never request-supplied local paths."""

    core_root: Path = field(repr=False)
    llm_root: Path = field(repr=False)

    def verify(self) -> dict:
        core = self.core_root.resolve(strict=True)
        llm = self.llm_root.resolve(strict=True)
        manifest = _pinned_json(core, "release_manifest.json", CORE_MANIFEST_SHA256)
        _pinned_json(llm, "contracts/llm/llm_contract_manifest.json", LLM_MANIFEST_SHA256)
        lock = _pinned_json(llm, "contracts/llm/dependency_lock.json", LLM_LOCK_SHA256)
        for root, files in ((core, manifest["file_sha256"]), (llm, lock["files"])):
            for relative, expected in files.items():
                _require(_file_sha(_contained_file(root, relative)) == expected,
                         "FROZEN_DEPENDENCY_MISMATCH")
        # This verifies packaged code/contracts, not external weights or a model run.
        return lock


@dataclass(frozen=True, slots=True)
class CanonicalExpressionResult:
    """Private result. Its bytes/provenance are NOT safe public callback fields.

    Retention/approved projection is the responsibility of the next versioned
    service boundary. Keeping this object in memory is not durable persistence.
    """

    core_bytes: bytes = field(repr=False)
    binding_bytes: bytes = field(repr=False)
    association_bytes: bytes = field(repr=False)
    expression_bytes: bytes = field(repr=False)
    # Exact compact serialization of the frozen prepare view, including its
    # utterance and source identity. None means prepare produced no view; it
    # never means an invented empty candidate selection.
    selection_bytes: bytes | None = field(default=None, repr=False)


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


class FrozenCanonicalCoaching:
    """Bind a trusted producer's bytes to the admitted job, then dispatch STEP7.

    Authentication, lease, deadline and canonical process ownership remain with
    the calling executor. No network occurs in construction/verification.
    """

    def __init__(self, artifacts: FrozenCanonicalArtifacts):
        self.artifacts = artifacts

    def _bridge(self):
        # Frozen STEP7 used this dependency. Do not silently run it against the
        # production app's newer validator; provision a separate pinned worker.
        _require(sys.version_info[:2] == (3, 12) and version("jsonschema") == "4.25.1",
                 "FROZEN_BRIDGE_RUNTIME_MISMATCH")
        lock = self.artifacts.verify()
        root = self.artifacts.llm_root.resolve(strict=True)
        # Load the exact verified bytes, not an ambient `bridge` on sys.path or
        # a cached pyc. Relative legacy_pure import retains frozen semantics.
        source = {}
        for name in ("legacy_pure", "adapter"):
            relative = f"bridge/{name}.py"
            raw = _contained_file(root, relative).read_bytes()
            _require(_sha(raw) == lock["files"][relative], "FROZEN_IMPORT_MISMATCH")
            source[name] = raw
        package_name = "_voice_coach_frozen_step7_" + LLM_LOCK_SHA256
        with _IMPORT_LOCK:
            names = [package_name, package_name + ".legacy_pure", package_name + ".adapter"]
            previous = {name: sys.modules.get(name) for name in names}
            try:
                package = ModuleType(package_name)
                package.__path__ = []
                sys.modules[package_name] = package
                for name, raw in source.items():
                    full_name = package_name + "." + name
                    module = ModuleType(full_name)
                    module.__package__ = package_name
                    module.__file__ = str(root / "bridge" / (name + ".py"))
                    sys.modules[full_name] = module
                    exec(compile(raw, module.__file__, "exec"), module.__dict__)
                return module.Bridge(root, expected_lock_sha256=LLM_LOCK_SHA256)
            finally:
                for name in names:
                    if previous[name] is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous[name]

    def express_admitted(
        self,
        request: AnalysisInput,
        *,
        audio: MaterializedAudio,
        core_bytes: bytes,
        prepared_wav: Path | None,
        producer_workspace: Path,
        transport: Callable,
    ) -> CanonicalExpressionResult:
        """Only call on a freshly completed, server-owned canonical execution.

        The producer must use str(request.event_id) as its case ID and keep the
        prepared WAV inside producer_workspace until this method returns. This
        is not an endpoint accepting an arbitrary client core JSON + hashes.
        """
        _require(type(core_bytes) is bytes and 0 < len(core_bytes) <= MAX_CORE_BYTES,
                 "CORE_SIZE_INVALID")
        _require(audio.sha256 == request.audio_sha256
                 and audio.byte_count == request.file_size_bytes
                 and audio.path.stat().st_size == audio.byte_count
                 and _file_sha(audio.path) == request.audio_sha256, "ADMITTED_AUDIO_MISMATCH")
        _require(_sha(request.script_text.encode("utf-8")) == request.script_sha256,
                 "ADMITTED_SCRIPT_MISMATCH")
        bridge = self._bridge()
        core = _json(core_bytes)
        _require(isinstance(core, dict), "CORE_SHAPE_INVALID")
        _require(core.get("request_id") == str(request.event_id)
                 and core.get("source_audio_sha256") == request.audio_sha256,
                 "CORE_ATTEMPT_MISMATCH")
        prepared_sha = core.get("prepared_wav_sha256")
        prepared_identity = None
        if prepared_sha is not None:
            _require(prepared_wav is not None, "PREPARED_AUDIO_MISSING")
            workspace = producer_workspace.resolve(strict=True)
            prepared = prepared_wav.resolve(strict=True)
            _require(prepared.is_relative_to(workspace) and prepared.is_file(),
                     "PREPARED_AUDIO_PATH_INVALID")
            from .canonical_media import measure_prepared_wav
            prepared_identity = measure_prepared_wav(prepared, prepared_sha)
        else:
            _require(prepared_wav is None, "UNBOUND_PREPARED_AUDIO")
        # The core owns its analysis UUID; never rewrite it to the Backend integer.
        analysis_id = core.get("core_result", {}).get("metadata", {}).get("analysis_id")
        _require(isinstance(analysis_id, str) and bool(analysis_id), "CORE_ANALYSIS_ID_MISSING")
        binding = dict(parent_manifest_sha256=CORE_MANIFEST_SHA256,
                       core_sha256=_sha(core_bytes), script_sha256=request.script_sha256,
                       request_id=str(request.event_id), analysis_id=analysis_id,
                       source_audio_sha256=request.audio_sha256,
                       prepared_wav_sha256=prepared_sha,
                       provenance_source="admitted-http-canonical-producer-v1")
        # Preserve all three axes and the full original bytes. STEP7 itself owns
        # non-ACCEPT dispatch=0, ranking/order, permission and deterministic fallback.
        selection_bytes = None
        try:
            _, view, _, _ = bridge.prepare(core_bytes, request.script_text, binding)
            if view is not None:
                selection_bytes = _encode(view)
        except Exception:
            # dispatch still owns fail-closed/errorCode semantics and performs
            # its original prepare call. This extra deterministic preparation
            # never invokes transport or changes the frozen prompt/model.
            pass
        result = bridge.dispatch(core_bytes, request.script_text, binding, transport=transport)
        association = dict(requestId=str(request.event_id), executionId=str(request.execution_id),
                           analysisId=request.analysis_id, recordingId=request.recording_id,
                           contentId=request.content_id, canonicalAnalysisId=analysis_id,
                           coreSha256=binding["core_sha256"],
                           parentManifestSha256=CORE_MANIFEST_SHA256,
                           llmManifestSha256=LLM_MANIFEST_SHA256,
                           llmDependencyLockSha256=LLM_LOCK_SHA256,
                           scriptSha256=request.script_sha256, audioSha256=request.audio_sha256,
                           preparedWavSha256=prepared_sha,
                           preparedAudio=prepared_identity.to_document() if prepared_identity is not None else None,
                           visualStatus="NOT_CONNECTED", visualCorrectiveClaimsAllowed=False)
        return CanonicalExpressionResult(core_bytes, _encode(binding),
                                         _encode(association), _encode(result), selection_bytes)
