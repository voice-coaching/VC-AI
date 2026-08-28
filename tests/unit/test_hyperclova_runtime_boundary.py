"""The local sidecar accepts only the grounded generation request shape."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from voice_coach.pronunciation_feedback.coaching_payload import CoachingPayloadError

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "hyperclova_server", ROOT / "scripts/serve_hyperclovax_grounded_advice.py"
)
server = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)

MODEL_ID = "naver-hyperclovax/HyperCLOVAX-SEED-Think-32B"


def _request(**overrides):  # noqa: ANN003, ANN202 - compact request fixture
    document = {
        "model": MODEL_ID,
        "messages": [{"role": "user", "content": "JSON만 반환"}],
        "max_tokens": 128,
        "temperature": 0,
        "chat_template_kwargs": {"thinking": False},
    }
    document.update(overrides)
    return document


def test_validated_request_preserves_messages_and_token_limit() -> None:
    messages, maximum_new_tokens = server._validated_request(
        _request(), expected_model=MODEL_ID
    )

    assert messages == [{"role": "user", "content": "JSON만 반환"}]
    assert maximum_new_tokens == 128


@pytest.mark.parametrize(
    "overrides",
    (
        {"model": "floating-or-different-model"},
        {"temperature": 0.7},
        {"chat_template_kwargs": {"thinking": True}},
        {"messages": "not-an-array"},
        {"max_tokens": "128"},
    ),
)
def test_server_rejects_requests_outside_grounded_policy(overrides: dict) -> None:
    with pytest.raises(CoachingPayloadError):
        server._validated_request(_request(**overrides), expected_model=MODEL_ID)
