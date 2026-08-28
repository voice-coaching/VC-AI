"""The cross-interpreter expected-phone frontend must fail closed, never fall back.

The pinned g2pK stack lives in another virtualenv, so the frontend crosses a
process boundary.  Everything that can go wrong at that boundary — missing
interpreter, crash, garbage on stdout, wrong protocol, wrong g2pK revision, a
digest that does not match the tokens — must surface as
``expected_phones_unavailable`` rather than as a plausible-looking phone stream.

The protocol tests drive a stub worker so they need no g2pK installed; the one
real-stack test skips when the pinned interpreter is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import textwrap

import pytest

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    expected_phone_token_digest,
)
from voice_coach.pronunciation_feedback.adapters.expected_phones_subprocess import (
    DEFAULT_INTERPRETER_RELATIVE,
    TRACK_E_ALLOWED_PUNCTUATION,
    WORKER_PROTOCOL_VERSION,
    SubprocessG2PKExpectedPhoneFrontend,
)
from voice_coach.pronunciation_feedback.domain import FailureCode
from voice_coach.pronunciation_feedback.errors import BackendUnavailableError

ROOT = Path(__file__).resolve().parents[2]
TOKENS = ("ㅎ", "ㅏ", "ㄱ", "|", "ㄴ", "ㅏ")


def _digest(
    tokens: tuple[str, ...] = TOKENS,
    *,
    version: str = EXPECTED_G2PK_VERSION,
) -> str:
    return expected_phone_token_digest(
        g2pk_version=version,
        descriptive=False,
        group_vowels=False,
        word_boundary_policy="preserve",
        allowed_punctuation=TRACK_E_ALLOWED_PUNCTUATION,
        phone_tokens=tokens,
    )


def _stub_worker(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stub_worker.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _frontend(
    tmp_path: Path,
    worker: Path,
    **overrides: object,
) -> SubprocessG2PKExpectedPhoneFrontend:
    options: dict[str, object] = {
        "nltk_data_root": tmp_path / "nltk_data",
        "repository_root": ROOT,
        "interpreter": Path(sys.executable),
        "worker_script": worker,
        "cache_results": True,
    }
    options.update(overrides)
    return SubprocessG2PKExpectedPhoneFrontend(**options)  # type: ignore[arg-type]


_OK_BODY = """
    import json, sys
    request = json.loads(sys.stdin.read())
    record = {
        "phone_tokens": %r,
        "phone_count": %d,
        "word_boundary_count": 1,
        "token_sha256": %r,
        "g2pk_version": %r,
    }
    json.dump(
        {
            "status": "ok",
            "protocol_version": request["protocol_version"],
            "expected_g2pk_version": %r,
            "results": [record for _ in request["texts"]],
        },
        sys.stdout,
        ensure_ascii=False,
    )
""" % (
    list(TOKENS),
    len(TOKENS) - 1,
    _digest(),
    EXPECTED_G2PK_VERSION,
    EXPECTED_G2PK_VERSION,
)


def test_stub_round_trip_returns_a_verified_sequence(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, _stub_worker(tmp_path, _OK_BODY))

    result = frontend.expected_phones("학교")

    assert result.tokens == TOKENS
    assert result.token_sha256 == _digest()
    assert result.revision == EXPECTED_G2PK_VERSION
    assert result.backend_id == "g2pk-expected-phones"


def test_batch_preserves_request_order_and_duplicates(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, _stub_worker(tmp_path, _OK_BODY))

    results = frontend.expected_phones_batch(["가", "나", "가"])

    assert len(results) == 3
    assert results[0].token_sha256 == results[2].token_sha256


def test_request_carries_the_declared_options(tmp_path: Path) -> None:
    captured = tmp_path / "request.json"
    recorder = _stub_worker(
        tmp_path,
        f"""
        import json, sys
        from pathlib import Path
        request = json.loads(sys.stdin.read())
        Path({str(captured)!r}).write_text(
            json.dumps(request, ensure_ascii=False), encoding="utf-8"
        )
        record = {{
            "phone_tokens": {list(TOKENS)!r},
            "phone_count": {len(TOKENS) - 1},
            "word_boundary_count": 1,
            "token_sha256": {_digest()!r},
            "g2pk_version": {EXPECTED_G2PK_VERSION!r},
        }}
        json.dump(
            {{
                "status": "ok",
                "protocol_version": request["protocol_version"],
                "expected_g2pk_version": {EXPECTED_G2PK_VERSION!r},
                "results": [record for _ in request["texts"]],
            }},
            sys.stdout,
            ensure_ascii=False,
        )
        """,
    )
    _frontend(tmp_path, recorder).expected_phones("가")

    request = json.loads(captured.read_text(encoding="utf-8"))
    assert request["protocol_version"] == WORKER_PROTOCOL_VERSION
    assert request["allowed_punctuation"] == list(TRACK_E_ALLOWED_PUNCTUATION)
    assert request["word_boundary_policy"] == "preserve"
    assert request["descriptive"] is False


def test_missing_interpreter_fails_closed(tmp_path: Path) -> None:
    frontend = _frontend(
        tmp_path,
        _stub_worker(tmp_path, _OK_BODY),
        interpreter=tmp_path / "no-such-python",
    )

    with pytest.raises(BackendUnavailableError, match="interpreter is unavailable"):
        frontend.expected_phones("가")


def test_missing_worker_script_fails_closed(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, tmp_path / "no-such-worker.py")

    with pytest.raises(BackendUnavailableError, match="worker script is unavailable"):
        frontend.expected_phones("가")


def test_worker_crash_fails_closed(tmp_path: Path) -> None:
    worker = _stub_worker(tmp_path, "import sys\nsys.exit(9)\n")
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="invalid JSON"):
        frontend.expected_phones("가")


def test_worker_reported_failure_is_surfaced(tmp_path: Path) -> None:
    worker = _stub_worker(
        tmp_path,
        """
        import json, sys
        json.dump({"status": "failed_closed", "error": "runtime unavailable"}, sys.stdout)
        sys.exit(1)
        """,
    )
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="runtime unavailable") as caught:
        frontend.expected_phones("가")
    assert caught.value.code is FailureCode.EXPECTED_PHONES_UNAVAILABLE


def test_protocol_version_drift_fails_closed(tmp_path: Path) -> None:
    worker = _stub_worker(
        tmp_path,
        f"""
        import json, sys
        json.dump(
            {{
                "status": "ok",
                "protocol_version": "voice_coach.g2pk_expected_phones_worker.v0",
                "expected_g2pk_version": {EXPECTED_G2PK_VERSION!r},
                "results": [],
            }},
            sys.stdout,
        )
        """,
    )
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="protocol version differs"):
        frontend.expected_phones("가")


def test_g2pk_revision_drift_fails_closed(tmp_path: Path) -> None:
    worker = _stub_worker(
        tmp_path,
        """
        import json, sys
        request = json.loads(sys.stdin.read())
        json.dump(
            {
                "status": "ok",
                "protocol_version": request["protocol_version"],
                "expected_g2pk_version": "0.0.1",
                "results": [],
            },
            sys.stdout,
        )
        """,
    )
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="g2pK pin differs"):
        frontend.expected_phones("가")


def test_tampered_tokens_fail_the_digest_check(tmp_path: Path) -> None:
    worker = _stub_worker(
        tmp_path,
        f"""
        import json, sys
        request = json.loads(sys.stdin.read())
        json.dump(
            {{
                "status": "ok",
                "protocol_version": request["protocol_version"],
                "expected_g2pk_version": {EXPECTED_G2PK_VERSION!r},
                "results": [
                    {{
                        "phone_tokens": ["ㅎ", "ㅏ", "ㄴ"],
                        "phone_count": 3,
                        "word_boundary_count": 0,
                        "token_sha256": {_digest()!r},
                        "g2pk_version": {EXPECTED_G2PK_VERSION!r},
                    }}
                ],
            }},
            sys.stdout,
            ensure_ascii=False,
        )
        """,
    )
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="digest does not match"):
        frontend.expected_phones("가")


def test_result_count_mismatch_fails_closed(tmp_path: Path) -> None:
    worker = _stub_worker(
        tmp_path,
        f"""
        import json, sys
        request = json.loads(sys.stdin.read())
        json.dump(
            {{
                "status": "ok",
                "protocol_version": request["protocol_version"],
                "expected_g2pk_version": {EXPECTED_G2PK_VERSION!r},
                "results": [],
            }},
            sys.stdout,
        )
        """,
    )
    frontend = _frontend(tmp_path, worker)

    with pytest.raises(BackendUnavailableError, match="wrong result count"):
        frontend.expected_phones("가")


def test_timeout_fails_closed(tmp_path: Path) -> None:
    worker = _stub_worker(tmp_path, "import time\ntime.sleep(30)\n")
    frontend = _frontend(tmp_path, worker, timeout_seconds=0.5)

    with pytest.raises(BackendUnavailableError, match="timed out"):
        frontend.expected_phones("가")


def test_empty_input_is_rejected(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, _stub_worker(tmp_path, _OK_BODY))

    with pytest.raises(BackendUnavailableError, match="must not be empty"):
        frontend.expected_phones_batch([])
    with pytest.raises(BackendUnavailableError, match="non-empty strings"):
        frontend.expected_phones_batch([""])


def test_relative_repository_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        SubprocessG2PKExpectedPhoneFrontend(
            nltk_data_root=tmp_path,
            repository_root=Path("relative/path"),
        )


@pytest.mark.skipif(
    not (ROOT / DEFAULT_INTERPRETER_RELATIVE).is_file()
    or not (Path.home() / "nltk_data" / "corpora" / "cmudict.zip").is_file(),
    reason="the pinned g2pK interpreter or NLTK data root is not present",
)
def test_pinned_stack_produces_stable_tokens() -> None:
    frontend = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=Path.home() / "nltk_data",
        repository_root=ROOT,
    )

    first = frontend.expected_phones("학교에 갔습니다.")
    second = SubprocessG2PKExpectedPhoneFrontend(
        nltk_data_root=Path.home() / "nltk_data",
        repository_root=ROOT,
    ).expected_phones("학교에 갔습니다.")

    assert first.tokens
    assert first.token_sha256 == second.token_sha256
    assert first.revision == EXPECTED_G2PK_VERSION


@pytest.mark.skipif(
    shutil.which("bash") is None, reason="a shell is required to fake the interpreter"
)
def test_unstartable_interpreter_fails_closed(tmp_path: Path) -> None:
    fake = tmp_path / "not-executable"
    fake.write_text("not a program", encoding="utf-8")
    frontend = _frontend(
        tmp_path,
        _stub_worker(tmp_path, _OK_BODY),
        interpreter=fake,
    )

    with pytest.raises(BackendUnavailableError, match="could not be started"):
        frontend.expected_phones("가")
