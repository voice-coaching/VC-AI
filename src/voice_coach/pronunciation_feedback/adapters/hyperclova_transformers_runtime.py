"""Lazy, offline Transformers runtime for the pinned HyperCLOVA X model."""

from __future__ import annotations

from collections.abc import Sequence
import os
from pathlib import Path
import threading
from typing import Any, Mapping

from ..coaching_payload import CoachingPayloadError

MAXIMUM_PROMPT_CHARACTERS = 64 * 1024
MAXIMUM_NEW_TOKENS = 256


class TransformersHyperClovaRuntime:
    """Load one local snapshot once and generate text under a process lock."""

    def __init__(
        self,
        *,
        model_dir: str | Path,
        model_id: str,
        revision: str,
        device_map: str = "auto",
        gpu_memory_gib: int | None = None,
        cpu_memory_gib: int = 8,
    ) -> None:
        directory = Path(model_dir).resolve()
        if not (directory / "config.json").is_file():
            raise CoachingPayloadError("HyperCLOVA snapshot has no config.json")
        if not model_id or len(revision) != 40:
            raise CoachingPayloadError("HyperCLOVA model and full revision are required")
        if gpu_memory_gib is not None and gpu_memory_gib <= 0:
            raise CoachingPayloadError("GPU memory limit must be positive")
        if cpu_memory_gib <= 0:
            raise CoachingPayloadError("CPU memory limit must be positive")
        self.model_dir = directory
        self.model_id = model_id
        self.revision = revision
        self.device_map = device_map
        self.gpu_memory_gib = gpu_memory_gib
        self.cpu_memory_gib = cpu_memory_gib
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.loaded:
            return
        try:
            import torch
            from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
        except ImportError as error:  # pragma: no cover - environment dependent
            raise CoachingPayloadError(
                "HyperCLOVA runtime needs torch and transformers"
            ) from error
        maximum_memory = None
        if self.gpu_memory_gib is not None:
            maximum_memory = {
                0: f"{self.gpu_memory_gib}GiB",
                "cpu": f"{self.cpu_memory_gib}GiB",
            }
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            config = AutoConfig.from_pretrained(
                self.model_dir,
                local_files_only=True,
                trust_remote_code=True,
            )
            # The upstream JSON embeds the Hub model id in the nested language
            # config.  Without replacing it, Transformers resolves the nested
            # custom class over the network even though the outer VLM came from
            # a local path.  Point both configs at the pinned local snapshot.
            config._name_or_path = str(self.model_dir)
            config.text_config._name_or_path = str(self.model_dir)
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_dir,
                config=config,
                local_files_only=True,
                trust_remote_code=True,
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.model_dir,
                local_files_only=True,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                attn_implementation="sdpa",
                device_map=self.device_map,
                max_memory=maximum_memory,
                low_cpu_mem_usage=True,
            )
            model.eval()  # type: ignore[no-untyped-call]
        except Exception as error:  # pragma: no cover - large-model boundary
            raise CoachingPayloadError(
                f"pinned HyperCLOVA snapshot failed to load: {type(error).__name__}"
            ) from error
        self._tokenizer = tokenizer
        self._model = model

    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        maximum_new_tokens: int,
    ) -> str:
        if not 0 < maximum_new_tokens <= MAXIMUM_NEW_TOKENS:
            raise CoachingPayloadError("HyperCLOVA token limit is outside policy")
        if not messages:
            raise CoachingPayloadError("HyperCLOVA messages must not be empty")
        if any(
            set(message) != {"role", "content"}
            or message["role"] not in {"system", "user", "assistant"}
            or not isinstance(message["content"], str)
            for message in messages
        ):
            raise CoachingPayloadError("HyperCLOVA message schema differs")
        character_count = sum(len(message["content"]) for message in messages)
        if character_count > MAXIMUM_PROMPT_CHARACTERS:
            raise CoachingPayloadError("HyperCLOVA prompt exceeds the size limit")
        self.load()
        assert self._tokenizer is not None and self._model is not None
        import torch

        prompt = self._tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=True,
            chat_template_kwargs={"thinking": False},
        )
        encoded = self._tokenizer(prompt, return_tensors="pt")
        input_device = self._model.get_input_embeddings().weight.device
        encoded = {key: value.to(input_device) for key, value in encoded.items()}
        input_tokens = int(encoded["input_ids"].shape[-1])
        with self._lock, torch.inference_mode():
            generated = self._model.generate(
                **encoded,
                do_sample=False,
                max_new_tokens=maximum_new_tokens,
                use_cache=True,
                pad_token_id=self._tokenizer.pad_token_id,
                eos_token_id=self._tokenizer.eos_token_id,
            )
        content = str(
            self._tokenizer.decode(
                generated[0, input_tokens:], skip_special_tokens=True
            )
        ).strip()
        if not content:
            raise CoachingPayloadError("HyperCLOVA generated an empty response")
        return content


__all__ = [
    "MAXIMUM_NEW_TOKENS",
    "MAXIMUM_PROMPT_CHARACTERS",
    "TransformersHyperClovaRuntime",
]
