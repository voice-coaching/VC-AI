"""The local sidecar accepts only the grounded generation request shape."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

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


def test_server_completion_attests_the_loaded_revision() -> None:
    class _Runtime:
        loaded = True
        model_id = MODEL_ID
        revision = "a" * 40

        @staticmethod
        def complete(*, messages, maximum_new_tokens):  # noqa: ANN001, ANN202
            assert messages
            assert maximum_new_tokens == 128
            return "{}"

    app = server.create_app(_Runtime())
    route = next(
        route for route in app.routes if route.path == "/v1/chat/completions"
    )
    response = route.endpoint(_request())

    assert response["model"] == MODEL_ID
    assert response["revision"] == "a" * 40


@pytest.mark.parametrize("cpu_memory_gib", (8, 48))
def test_offline_loader_can_offload_a6000_weights_to_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu_memory_gib: int
) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    loaded_options = {}
    config = SimpleNamespace(text_config=SimpleNamespace())

    def load_model(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        loaded_options.update(kwargs)
        return SimpleNamespace(eval=lambda: None)

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoConfig=SimpleNamespace(from_pretrained=lambda *a, **k: config),
            AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: object()),
            AutoModelForCausalLM=SimpleNamespace(from_pretrained=load_model),
        ),
    )
    runtime = server.TransformersHyperClovaRuntime(
        model_dir=tmp_path,
        model_id=MODEL_ID,
        revision="a" * 40,
        gpu_memory_gib=40,
        cpu_memory_gib=cpu_memory_gib,
    )

    runtime.load()

    assert runtime.loaded
    assert loaded_options["max_memory"] == {
        0: "40GiB", "cpu": f"{cpu_memory_gib}GiB"
    }
    assert loaded_options["local_files_only"] is True
    assert loaded_options["device_map"] == "auto"


@pytest.mark.parametrize("cpu_memory_gib", (0, -1))
def test_loader_rejects_nonpositive_cpu_budget(
    tmp_path: Path, cpu_memory_gib: int
) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CoachingPayloadError, match="CPU memory limit"):
        server.TransformersHyperClovaRuntime(
            model_dir=tmp_path,
            model_id=MODEL_ID,
            revision="a" * 40,
            cpu_memory_gib=cpu_memory_gib,
        )


@pytest.mark.parametrize("options, expected_cpu", (([], 8), (["--cpu-memory-gib", "48"], 48)))
def test_server_passes_cpu_budget_to_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: list[str], expected_cpu: int
) -> None:
    import json

    config = tmp_path / "runtime.json"
    config.write_text(json.dumps({
        "schemaVersion": "voice_coach.local_llm_runtime.v1",
        "localDirectory": str(tmp_path),
        "modelId": MODEL_ID,
        "revision": "a" * 40,
    }), encoding="utf-8")
    captured = {}

    def runtime_factory(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return SimpleNamespace(load=lambda: None)

    monkeypatch.setattr(server, "TransformersHyperClovaRuntime", runtime_factory)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *a, **k: None))

    assert server.main(["--config", str(config), "--gpu-memory-gib", "40", *options]) == 0
    assert captured["gpu_memory_gib"] == 40
    assert captured["cpu_memory_gib"] == expected_cpu
