from __future__ import annotations

from pathlib import Path
import sys
import threading
import time

import pytest

from voice_coach.pronunciation_attempts.adapters.persistent_seungun import (
    PersistentIsolatedFrozenSeungunDetectorAdapter,
)
from voice_coach.pronunciation_attempts.adapters.runtime import (
    PronunciationAttemptRuntimeError,
)
from voice_coach.pronunciation_attempts.adapters.seungun_runtime_server import (
    _SERVER_PROTOCOL_VERSION,
    _server_request,
)
from voice_coach.pronunciation_attempts.adapters.seungun_runtime_worker import (
    _WorkerError,
)


_SERVER = r'''from __future__ import annotations
import json
import os
import sys

protocol = "voice_coach.seungun_runtime_server.v1"
instance = str(os.getpid())
print(json.dumps({
    "protocol_version": protocol,
    "status": "ready",
    "network_isolation": "seccomp_socket_denied",
}, separators=(",", ":")), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        "protocol_version": protocol,
        "request_id": request["request_id"],
        "status": "completed",
        "network_isolation": "seccomp_socket_denied",
        "analysis": {
            "instance": instance,
            "expected_phones": request["expected_phones"],
        },
    }, separators=(",", ":")), flush=True)
'''

_HANGING_SERVER = r'''from __future__ import annotations
import json
import sys
import time

print(json.dumps({
    "protocol_version": "voice_coach.seungun_runtime_server.v1",
    "status": "ready",
    "network_isolation": "seccomp_socket_denied",
}, separators=(",", ":")), flush=True)
for line in sys.stdin:
    time.sleep(60)
'''


def _server(tmp_path: Path, source: str) -> Path:
    target = tmp_path / "synthetic_server.py"
    target.write_text(source, encoding="utf-8")
    return target


def test_reuses_warm_process_then_recycles_at_the_reviewed_request_cap(
    tmp_path: Path,
) -> None:
    target = _server(tmp_path, _SERVER)
    adapter = PersistentIsolatedFrozenSeungunDetectorAdapter(
        repository_root=tmp_path,
        device="cpu",
        worker_python=sys.executable,
        startup_timeout_seconds=5,
        request_timeout_seconds=5,
        maximum_requests_per_process=2,
        start_immediately=True,
        server_target=(sys.executable, str(target)),
    )
    try:
        first = adapter.analyze(audio_path=tmp_path / "one.wav", expected_phones=("ㄱ",))
        second = adapter.analyze(audio_path=tmp_path / "two.wav", expected_phones=("ㄴ",))
        third = adapter.analyze(audio_path=tmp_path / "three.wav", expected_phones=("ㄷ",))
    finally:
        adapter.close()

    assert first["instance"] == second["instance"]
    assert third["instance"] != first["instance"]
    assert third["expected_phones"] == ["ㄷ"]


def test_request_timeout_kills_the_hung_isolated_process(tmp_path: Path) -> None:
    target = _server(tmp_path, _HANGING_SERVER)
    adapter = PersistentIsolatedFrozenSeungunDetectorAdapter(
        repository_root=tmp_path,
        device="cpu",
        worker_python=sys.executable,
        startup_timeout_seconds=5,
        request_timeout_seconds=1,
        server_target=(sys.executable, str(target)),
    )
    try:
        with pytest.raises(PronunciationAttemptRuntimeError) as error:
            adapter.analyze(
                audio_path=tmp_path / "attempt.wav",
                expected_phones=("ㄱ",),
            )
    finally:
        adapter.close()

    assert error.value.code == "seungun_worker_failed"


def test_cancel_current_interrupts_a_running_inference(tmp_path: Path) -> None:
    target = _server(tmp_path, _HANGING_SERVER)
    adapter = PersistentIsolatedFrozenSeungunDetectorAdapter(
        repository_root=tmp_path,
        device="cpu",
        worker_python=sys.executable,
        startup_timeout_seconds=5,
        request_timeout_seconds=30,
        server_target=(sys.executable, str(target)),
    )
    failures: list[BaseException] = []

    def analyze() -> None:
        try:
            adapter.analyze(
                audio_path=tmp_path / "attempt.wav",
                expected_phones=("ㄱ",),
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=analyze)
    thread.start()
    deadline = time.monotonic() + 5
    while adapter._process is None and time.monotonic() < deadline:
        time.sleep(0.01)
    adapter.cancel_current()
    thread.join(timeout=5)
    adapter.close()

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], PronunciationAttemptRuntimeError)


def test_server_accepts_only_owner_read_only_absolute_audio(tmp_path: Path) -> None:
    audio = tmp_path / "attempt.wav"
    audio.write_bytes(b"synthetic")
    audio.chmod(0o400)
    request = {
        "protocol_version": _SERVER_PROTOCOL_VERSION,
        "request_id": "4adfe173-0691-4e89-b94e-a5c5c5085826",
        "audio_path": str(audio),
        "expected_phones": ["ㄱ"],
    }

    request_id, accepted, phones = _server_request(request, device="cpu")

    assert request_id == request["request_id"]
    assert accepted == audio
    assert phones == ("ㄱ",)

    audio.chmod(0o600)
    with pytest.raises(_WorkerError) as error:
        _server_request(request, device="cpu")
    assert error.value.reason_code == "restricted_audio_unavailable"
