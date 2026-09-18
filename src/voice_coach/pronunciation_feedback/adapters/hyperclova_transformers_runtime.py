"""Lazy, offline Transformers runtime for the pinned HyperCLOVA X model."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager, nullcontext
import importlib
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from ..coaching_payload import CoachingPayloadError

MAXIMUM_PROMPT_CHARACTERS = 64 * 1024
# Bounded multi-item coaching needs more output than the legacy single message.
# Legacy callers still request 512; the wall-clock generation deadline is unchanged.
MAXIMUM_NEW_TOKENS = 2048
MAXIMUM_GENERATION_SECONDS = 150.0


class HyperClovaBusyError(CoachingPayloadError):
    """The single GPU is already serving another completion."""


class HyperClovaDeadlineError(CoachingPayloadError):
    """Generation stopped before the caller's HTTP timeout."""


class _Int8CastWarningOnce(logging.Filter):
    """Keep the known BF16 cast warning once, without hiding other warnings."""

    def __init__(self) -> None:
        super().__init__()
        self._seen = False

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == "MatMul8bitLt: inputs will be cast from %s to float16 during quantization":
            if self._seen:
                return False
            self._seen = True
        return True


@contextmanager
def _pinned_sdpa_model_class(model_dir: Path, config: Any):
    """Honor SDPA while the pinned VLM constructs its otherwise unused vision tower.

    The snapshot's GPU-name helper unconditionally promotes A6000 vision
    attention to FlashAttention2. Suppress only that promotion during startup;
    keep the snapshot files and model weights unchanged and restore the helper.
    Runtime loading occurs once before the HTTP server accepts requests.
    """
    from transformers.dynamic_module_utils import get_class_from_dynamic_module

    model_class = get_class_from_dynamic_module(
        config.auto_map["AutoModelForCausalLM"], str(model_dir), local_files_only=True
    )
    module = importlib.import_module(model_class.__module__)
    automatic_flash_selector = module.is_ampere_or_newer
    config.vision_config._attn_implementation = "sdpa"
    module.is_ampere_or_newer = lambda: False
    try:
        yield model_class
    finally:
        module.is_ampere_or_newer = automatic_flash_selector


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
        quantization: str = "bf16",
        maximum_input_tokens: int | None = None,
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
        if quantization not in {"bf16", "bnb-int8"}:
            raise CoachingPayloadError("Unsupported HyperCLOVA quantization")
        if maximum_input_tokens is not None and maximum_input_tokens <= 0:
            raise CoachingPayloadError("Input token limit must be positive")
        self.model_dir = directory
        self.model_id = model_id
        self.revision = revision
        self.device_map = device_map
        self.gpu_memory_gib = gpu_memory_gib
        self.cpu_memory_gib = cpu_memory_gib
        self.quantization = quantization
        self.maximum_input_tokens = maximum_input_tokens
        self.quantized_linear_modules = 0
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._lock = threading.Lock()
        self.last_completion: dict[str, Any] | None = None

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
            quantization_options: dict[str, Any] = {}
            if self.quantization == "bnb-int8":
                from transformers import BitsAndBytesConfig

                if not torch.cuda.is_available():
                    raise CoachingPayloadError("HyperCLOVA INT8 requires CUDA")
                quantization_options["quantization_config"] = BitsAndBytesConfig(
                    load_in_8bit=True,
                    llm_int8_threshold=6.0,
                    llm_int8_enable_fp32_cpu_offload=False,
                    llm_int8_skip_modules=["lm_head", "vision_model", "mm_projector"],
                )
            loader = (
                _pinned_sdpa_model_class(self.model_dir, config)
                if self.quantization == "bnb-int8"
                else nullcontext(AutoModelForCausalLM)
            )
            with loader as model_class:
                model = model_class.from_pretrained(
                    self.model_dir,
                    config=config,
                    local_files_only=True,
                    trust_remote_code=True,
                    torch_dtype=torch.bfloat16,
                    attn_implementation="sdpa",
                    device_map=self.device_map,
                    max_memory=maximum_memory,
                    low_cpu_mem_usage=True,
                    **quantization_options,
                )
            if self.quantization == "bnb-int8":
                from bitsandbytes.nn import Linear8bitLt

                int8_logger = logging.getLogger("bitsandbytes.autograd._functions")
                if not any(isinstance(item, _Int8CastWarningOnce) for item in int8_logger.filters):
                    int8_logger.addFilter(_Int8CastWarningOnce())

                self.quantized_linear_modules = sum(
                    isinstance(module, Linear8bitLt) for module in model.modules()
                )
                if not self.quantized_linear_modules:
                    raise CoachingPayloadError("HyperCLOVA INT8 conversion was not applied")
                if any(
                    str(device) in {"cpu", "disk"}
                    for device in getattr(model, "hf_device_map", {}).values()
                ):
                    raise CoachingPayloadError("HyperCLOVA INT8 must remain on GPU")
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
        from transformers import StoppingCriteria, StoppingCriteriaList

        prompt = self._tokenizer.apply_chat_template(
            list(messages),
            tokenize=False,
            add_generation_prompt=True,
            thinking=False,
        )
        encoded = self._tokenizer(prompt, return_tensors="pt")
        input_tokens = int(encoded["input_ids"].shape[-1])
        if self.maximum_input_tokens is not None and input_tokens > self.maximum_input_tokens:
            raise CoachingPayloadError("HyperCLOVA prompt exceeds the input token limit")
        input_device = self._model.get_input_embeddings().weight.device
        encoded = {key: value.to(input_device) for key, value in encoded.items()}
        if not self._lock.acquire(blocking=False):
            raise HyperClovaBusyError("HyperCLOVA is already processing a request")
        started = time.monotonic()

        class Deadline(StoppingCriteria):
            expired = False

            def __call__(self, input_ids, scores, **kwargs):  # noqa: ANN001, ANN202
                self.expired = time.monotonic() - started >= MAXIMUM_GENERATION_SECONDS
                return self.expired

        deadline = Deadline()
        try:
            with torch.inference_mode():
                generated = self._model.generate(
                    **encoded,
                    do_sample=False,
                    max_new_tokens=maximum_new_tokens,
                    use_cache=True,
                    stopping_criteria=StoppingCriteriaList([deadline]),
                    pad_token_id=self._tokenizer.pad_token_id,
                    eos_token_id=self._tokenizer.eos_token_id,
                )
            output_tokens = int(generated.shape[-1]) - input_tokens
            stopped_on_eos = int(generated[0, -1]) == self._tokenizer.eos_token_id
            self.last_completion = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "generation_seconds": round(time.monotonic() - started, 3),
                "finish_reason": "deadline" if deadline.expired else ("stop" if stopped_on_eos else "length"),
            }
            if deadline.expired:
                raise HyperClovaDeadlineError("HyperCLOVA exceeded its generation deadline")
            if not stopped_on_eos:
                raise CoachingPayloadError("HyperCLOVA response reached the output token limit")
        finally:
            self._lock.release()
        content = str(
            self._tokenizer.decode(
                generated[0, input_tokens:], skip_special_tokens=True
            )
        ).strip()
        if not content:
            raise CoachingPayloadError("HyperCLOVA generated an empty response")
        return content


__all__ = [
    "HyperClovaBusyError",
    "HyperClovaDeadlineError",
    "MAXIMUM_GENERATION_SECONDS",
    "MAXIMUM_NEW_TOKENS",
    "MAXIMUM_PROMPT_CHARACTERS",
    "TransformersHyperClovaRuntime",
]
