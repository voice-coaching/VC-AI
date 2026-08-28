"""Explicit generator-family adapters for the PDF reference-TTS chain.

CosyVoice2, StyleTTS2 and F5-TTS are optional local backends. This module never
downloads weights or silently substitutes one family for another. A backend is
usable only when the caller injects a local callable with an attested family
identity. The checked-in corrective prototype remains available separately.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import torch

from voice_coach.application.adversarial_tts import AdversarialTTSEngine


class GeneratorAdapterError(RuntimeError):
    """Raised when a generator family is unavailable or returns invalid audio."""


class GeneratorFamily(StrEnum):
    CORRECTIVE_PROTOTYPE = "corrective_prototype"
    COSYVOICE2 = "cosyvoice2"
    STYLETTS2 = "styletts2"
    F5_TTS = "f5_tts"


@dataclass(frozen=True, slots=True)
class GeneratorCandidate:
    waveform: torch.Tensor
    family: GeneratorFamily
    backend_id: str
    metadata: Mapping[str, object]


class GeneratorAdapter(Protocol):
    family: GeneratorFamily
    backend_id: str

    def generate(
        self,
        learner_waveform: torch.Tensor,
        learner_text_tokens: torch.Tensor,
        native_reference_waveform: torch.Tensor,
    ) -> GeneratorCandidate: ...


def _validate_waveform(waveform: object, family: GeneratorFamily) -> torch.Tensor:
    if not isinstance(waveform, torch.Tensor) or waveform.ndim != 2:
        raise GeneratorAdapterError(f"{family.value} backend must return [batch, samples]")
    if not torch.is_floating_point(waveform) or not torch.isfinite(waveform).all():
        raise GeneratorAdapterError(f"{family.value} backend returned non-finite audio")
    if torch.max(waveform.detach().abs()) > 1:
        raise GeneratorAdapterError(f"{family.value} backend returned unbounded audio")
    return waveform


class CorrectivePrototypeAdapter:
    family = GeneratorFamily.CORRECTIVE_PROTOTYPE

    def __init__(self, engine: AdversarialTTSEngine, *, backend_id: str = "voice_coach.corrective_prototype.v1") -> None:
        self.engine = engine
        self.backend_id = backend_id

    def generate(self, learner_waveform: torch.Tensor, learner_text_tokens: torch.Tensor, native_reference_waveform: torch.Tensor) -> GeneratorCandidate:
        trajectory = self.engine.generate(learner_waveform, learner_text_tokens, native_reference_waveform)
        waveform = _validate_waveform(trajectory.refined.waveform, self.family)
        return GeneratorCandidate(
            waveform=waveform,
            family=self.family,
            backend_id=self.backend_id,
            metadata={"passes": 2, "source_conditioned": True},
        )


ExternalGeneratorCallable = Callable[[torch.Tensor, torch.Tensor, torch.Tensor], torch.Tensor]


class OptionalExternalGeneratorAdapter:
    """Adapter for an explicitly injected local CosyVoice2/StyleTTS2/F5 callable."""

    def __init__(
        self,
        family: GeneratorFamily,
        backend_id: str,
        backend: ExternalGeneratorCallable | None,
    ) -> None:
        if family is GeneratorFamily.CORRECTIVE_PROTOTYPE:
            raise GeneratorAdapterError("corrective prototype uses its dedicated adapter")
        if not backend_id or backend_id == "unresolved":
            raise GeneratorAdapterError("external backend_id must be attested")
        self.family = family
        self.backend_id = backend_id
        self._backend = backend

    def generate(self, learner_waveform: torch.Tensor, learner_text_tokens: torch.Tensor, native_reference_waveform: torch.Tensor) -> GeneratorCandidate:
        if self._backend is None:
            raise GeneratorAdapterError(
                f"{self.family.value} backend is unavailable; inject a local, licensed callable"
            )
        waveform = _validate_waveform(
            self._backend(learner_waveform, learner_text_tokens, native_reference_waveform),
            self.family,
        )
        return GeneratorCandidate(
            waveform=waveform,
            family=self.family,
            backend_id=self.backend_id,
            metadata={"external_backend_injected": True},
        )


class GeneratorFamilyChain:
    """Run a caller-selected family list and preserve family identity."""

    def __init__(self, adapters: Mapping[GeneratorFamily, GeneratorAdapter]) -> None:
        self._adapters = dict(adapters)

    def generate_candidates(
        self,
        learner_waveform: torch.Tensor,
        learner_text_tokens: torch.Tensor,
        native_reference_waveform: torch.Tensor,
        *,
        families: Sequence[GeneratorFamily],
    ) -> tuple[GeneratorCandidate, ...]:
        if not families:
            raise GeneratorAdapterError("at least one generator family is required")
        candidates: list[GeneratorCandidate] = []
        for family in families:
            try:
                adapter = self._adapters[family]
            except KeyError as exc:
                raise GeneratorAdapterError(f"generator family is not configured: {family.value}") from exc
            candidate = adapter.generate(
                learner_waveform,
                learner_text_tokens,
                native_reference_waveform,
            )
            if candidate.family is not family:
                raise GeneratorAdapterError("generator backend family identity drifted")
            candidates.append(candidate)
        return tuple(candidates)


__all__ = [
    "CorrectivePrototypeAdapter",
    "ExternalGeneratorCallable",
    "GeneratorAdapterError",
    "GeneratorCandidate",
    "GeneratorFamily",
    "GeneratorFamilyChain",
    "OptionalExternalGeneratorAdapter",
]
