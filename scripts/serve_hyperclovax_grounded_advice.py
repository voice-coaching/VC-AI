#!/usr/bin/env python
"""Serve the pinned HyperCLOVA model on a loopback OpenAI-compatible endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any
import uuid

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.pronunciation_feedback.adapters.hyperclova_transformers_runtime import (  # noqa: E402
    TransformersHyperClovaRuntime,
)
from voice_coach.pronunciation_feedback.coaching_payload import (  # noqa: E402
    CoachingPayloadError,
)

DEFAULT_CONFIG = (
    REPOSITORY_ROOT
    / "configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json"
)


def _runtime_config(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != "voice_coach.local_llm_runtime.v1":
        raise CoachingPayloadError("HyperCLOVA runtime config schema differs")
    return document


def _validated_request(document: Any, *, expected_model: str) -> tuple[list[dict[str, str]], int]:
    if not isinstance(document, dict):
        raise CoachingPayloadError("chat completion request must be an object")
    if document.get("model") != expected_model:
        raise CoachingPayloadError("chat completion model id differs")
    if document.get("temperature") not in (0, 0.0):
        raise CoachingPayloadError("grounded advice requires temperature zero")
    template = document.get("chat_template_kwargs")
    if template != {"thinking": False}:
        raise CoachingPayloadError("grounded advice requires thinking=false")
    messages = document.get("messages")
    if not isinstance(messages, list):
        raise CoachingPayloadError("chat completion messages must be an array")
    maximum_new_tokens = document.get("max_tokens")
    if not isinstance(maximum_new_tokens, int):
        raise CoachingPayloadError("chat completion max_tokens must be an integer")
    return messages, maximum_new_tokens


def create_app(runtime: TransformersHyperClovaRuntime):  # noqa: ANN201
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Local grounded HyperCLOVA runtime", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ready" if runtime.loaded else "not_loaded",
            "model": runtime.model_id,
            "revision": runtime.revision,
        }

    @app.post("/v1/chat/completions")
    def chat_completions(document: dict[str, Any]) -> dict[str, Any]:
        try:
            messages, maximum_new_tokens = _validated_request(
                document, expected_model=runtime.model_id
            )
            content = runtime.complete(
                messages=messages, maximum_new_tokens=maximum_new_tokens
            )
        except CoachingPayloadError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "id": f"local-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": runtime.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }

    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-gib", type=int, default=110)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    document = _runtime_config(args.config)
    runtime = TransformersHyperClovaRuntime(
        model_dir=REPOSITORY_ROOT / document["localDirectory"],
        model_id=document["modelId"],
        revision=document["revision"],
        gpu_memory_gib=args.gpu_memory_gib,
    )
    runtime.load()
    import uvicorn

    uvicorn.run(create_app(runtime), host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
