"""Parameter-efficient residual adapters for a pinned WavLM encoder.

This module deliberately contains no data loading and never downloads a model.
It only transforms an already loaded WavLM-like module after freezing every
existing parameter.  The caller must validate the model revision separately.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import inspect
import re
from typing import Any

import torch
from torch import nn

from voice_coach.models.wavlm_frozen_head import (
    FrozenPhoneHead,
    FrozenUtteranceHead,
)


CANONICAL_TRAINABLE_NAME_PATTERNS = (
    (
        r"^wavlm\.encoder\.layers\.[0-9]+\.adapter\."
        r"(layer_norm|down|up)\.(weight|bias)$"
    ),
    r"^phone_head\.",
    r"^utterance_head\.",
)


class AdapterContractError(ValueError):
    """Raised when adapter injection or a trainable-parameter audit fails."""


class ResidualBottleneckAdapter(nn.Module):
    """Layer-normalized bottleneck adapter with an identity initialization."""

    def __init__(
        self,
        *,
        hidden_size: int,
        bottleneck_dim: int,
        dropout: float,
        zero_initialize_output: bool = True,
    ) -> None:
        super().__init__()
        if hidden_size <= 0 or bottleneck_dim <= 0:
            raise AdapterContractError("adapter dimensions must be positive")
        if not 0 <= dropout < 1:
            raise AdapterContractError("adapter dropout must be in [0, 1)")
        self.hidden_size = int(hidden_size)
        self.layer_norm = nn.LayerNorm(self.hidden_size)
        self.down = nn.Linear(self.hidden_size, int(bottleneck_dim))
        self.activation = nn.GELU()
        self.dropout = nn.Dropout(float(dropout))
        self.up = nn.Linear(int(bottleneck_dim), self.hidden_size)
        if zero_initialize_output:
            nn.init.zeros_(self.up.weight)
            nn.init.zeros_(self.up.bias)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        if hidden_states.ndim != 3 or hidden_states.shape[-1] != self.hidden_size:
            raise AdapterContractError("adapter hidden states have an unexpected shape")
        update = self.up(
            self.dropout(self.activation(self.down(self.layer_norm(hidden_states))))
        )
        return hidden_states + update


class AdapterWrappedEncoderLayer(nn.Module):
    """Preserve a WavLM layer's tuple contract and adapt its hidden output."""

    def __init__(
        self,
        base_layer: nn.Module,
        *,
        hidden_size: int,
        bottleneck_dim: int,
        dropout: float,
        zero_initialize_output: bool,
    ) -> None:
        super().__init__()
        self.base_layer = base_layer
        self.adapter = ResidualBottleneckAdapter(
            hidden_size=hidden_size,
            bottleneck_dim=bottleneck_dim,
            dropout=dropout,
            zero_initialize_output=zero_initialize_output,
        )
        signature = inspect.signature(base_layer.forward)
        self._base_accepts_index = "index" in signature.parameters

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        position_bias: torch.Tensor | None = None,
        output_attentions: bool = False,
        index: int = 0,
    ) -> tuple[Any, ...]:
        keyword_arguments: dict[str, Any] = {
            "attention_mask": attention_mask,
            "position_bias": position_bias,
            "output_attentions": output_attentions,
        }
        if self._base_accepts_index:
            keyword_arguments["index"] = index
        output = self.base_layer(hidden_states, **keyword_arguments)
        if not isinstance(output, tuple) or len(output) < 2:
            raise AdapterContractError(
                "wrapped WavLM encoder layer must return a tuple of length >= 2"
            )
        adapted = self.adapter(output[0])
        return (adapted, *output[1:])


def _encoder_layers(wavlm: nn.Module) -> nn.ModuleList:
    encoder = getattr(wavlm, "encoder", None)
    layers = getattr(encoder, "layers", None)
    if not isinstance(layers, nn.ModuleList):
        raise AdapterContractError("WavLM encoder.layers must be a ModuleList")
    return layers


def freeze_existing_parameters(module: nn.Module) -> None:
    """Freeze every parameter that existed before adapter/head construction."""

    for parameter in module.parameters():
        parameter.requires_grad_(False)


def inject_residual_adapters(
    wavlm: nn.Module,
    *,
    hidden_size: int,
    bottleneck_dim: int,
    dropout: float,
    zero_initialize_output: bool = True,
    layer_indices: Iterable[int] | None = None,
) -> tuple[int, ...]:
    """Wrap exactly the selected encoder layers after freezing the backbone."""

    freeze_existing_parameters(wavlm)
    layers = _encoder_layers(wavlm)
    selected = (
        tuple(range(len(layers)))
        if layer_indices is None
        else tuple(sorted(set(layer_indices)))
    )
    if not selected:
        raise AdapterContractError("at least one adapter layer is required")
    if selected[0] < 0 or selected[-1] >= len(layers):
        raise AdapterContractError("adapter layer index is out of range")
    for index in selected:
        if isinstance(layers[index], AdapterWrappedEncoderLayer):
            raise AdapterContractError("adapter injection may only run once")
        layers[index] = AdapterWrappedEncoderLayer(
            layers[index],
            hidden_size=hidden_size,
            bottleneck_dim=bottleneck_dim,
            dropout=dropout,
            zero_initialize_output=zero_initialize_output,
        )
    return selected


class AdapterTunedWavLM(nn.Module):
    """Pinned WavLM plus adapter-only encoder updates and simple task heads."""

    def __init__(
        self,
        *,
        wavlm: nn.Module,
        hidden_size: int,
        encoder_layer_count: int,
        adapter_bottleneck_dim: int,
        adapter_dropout: float,
        zero_initialize_adapter_output: bool,
        target_vocabulary_size: int,
        target_embedding_dim: int,
        phone_hidden_dim: int,
        phone_dropout: float,
        utterance_hidden_dim: int,
        utterance_dropout: float,
        score_range: Sequence[float] = (1.0, 5.0),
    ) -> None:
        super().__init__()
        if len(score_range) != 2:
            raise AdapterContractError("score_range must contain two values")
        layers = _encoder_layers(wavlm)
        if len(layers) != encoder_layer_count:
            raise AdapterContractError(
                "loaded WavLM encoder layer count differs from the pin"
            )
        self.wavlm = wavlm
        self.adapter_layer_indices = inject_residual_adapters(
            self.wavlm,
            hidden_size=hidden_size,
            bottleneck_dim=adapter_bottleneck_dim,
            dropout=adapter_dropout,
            zero_initialize_output=zero_initialize_adapter_output,
        )
        self.phone_head = FrozenPhoneHead(
            embedding_dim=hidden_size,
            target_vocabulary_size=target_vocabulary_size,
            target_embedding_dim=target_embedding_dim,
            hidden_dim=phone_hidden_dim,
            dropout=phone_dropout,
        )
        self.utterance_head = FrozenUtteranceHead(
            embedding_dim=hidden_size * 2,
            hidden_dim=utterance_hidden_dim,
            dropout=utterance_dropout,
            outcome_count=3,
            score_minimum=float(score_range[0]),
            score_maximum=float(score_range[1]),
        )

    def encode(
        self,
        input_values: torch.Tensor,
        *,
        attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        output = self.wavlm(
            input_values=input_values,
            attention_mask=attention_mask,
            return_dict=True,
        )
        hidden = getattr(output, "last_hidden_state", None)
        if not isinstance(hidden, torch.Tensor) or hidden.ndim != 3:
            raise AdapterContractError("WavLM output lacks 3-D last_hidden_state")
        return hidden


def compile_trainable_name_patterns(
    patterns: Sequence[str],
) -> tuple[re.Pattern[str], ...]:
    if tuple(patterns) != CANONICAL_TRAINABLE_NAME_PATTERNS:
        raise AdapterContractError(
            "trainable parameter patterns differ from the canonical allowlist"
        )
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            raise AdapterContractError(
                "trainable parameter patterns must be non-empty strings"
            )
        try:
            compiled.append(re.compile(pattern))
        except re.error as error:
            raise AdapterContractError(
                f"invalid trainable parameter pattern: {pattern!r}"
            ) from error
    return tuple(compiled)


def audit_trainable_parameters(
    model: nn.Module,
    *,
    allow_patterns: Sequence[str],
) -> dict[str, Any]:
    """Reject every trainable tensor outside the predeclared allowlist."""

    compiled = compile_trainable_name_patterns(allow_patterns)
    trainable: list[tuple[str, nn.Parameter]] = []
    frozen_count = 0
    total_count = 0
    backbone_non_adapter_trainable_count = 0
    for name, parameter in model.named_parameters():
        count = int(parameter.numel())
        total_count += count
        if parameter.requires_grad:
            if name.startswith("wavlm.") and ".adapter." not in name:
                backbone_non_adapter_trainable_count += count
                raise AdapterContractError(
                    f"non-adapter backbone parameter is trainable: {name}"
                )
            if not any(pattern.search(name) for pattern in compiled):
                raise AdapterContractError(
                    f"trainable parameter is outside the allowlist: {name}"
                )
            trainable.append((name, parameter))
        else:
            frozen_count += count
    if not trainable:
        raise AdapterContractError("no trainable parameters remain")
    names = [name for name, _ in trainable]
    required_prefixes = (
        "wavlm.encoder.layers.",
        "phone_head.",
        "utterance_head.",
    )
    for prefix in required_prefixes:
        if not any(name.startswith(prefix) for name in names):
            raise AdapterContractError(
                f"required trainable parameter role is absent: {prefix}"
            )
    trainable_count = sum(int(parameter.numel()) for _, parameter in trainable)
    return {
        "total_parameter_count": total_count,
        "trainable_parameter_count": trainable_count,
        "frozen_parameter_count": frozen_count,
        "trainable_fraction": trainable_count / total_count,
        "trainable_tensor_count": len(trainable),
        "trainable_parameter_names": names,
        "allow_patterns": list(allow_patterns),
        "backbone_non_adapter_trainable_count": (backbone_non_adapter_trainable_count),
        "full_fine_tuning": False,
    }


def group_trainable_parameters(
    model: nn.Module,
    *,
    adapter_learning_rate: float,
    head_learning_rate: float,
) -> list[Mapping[str, Any]]:
    """Build disjoint optimizer groups after the allowlist audit."""

    if adapter_learning_rate <= 0 or head_learning_rate <= 0:
        raise AdapterContractError("learning rates must be positive")
    adapter_parameters: list[nn.Parameter] = []
    head_parameters: list[nn.Parameter] = []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        if name.startswith("wavlm.encoder.layers.") and ".adapter." in name:
            adapter_parameters.append(parameter)
        elif name.startswith(("phone_head.", "utterance_head.")):
            head_parameters.append(parameter)
        else:
            raise AdapterContractError(
                f"cannot assign trainable parameter to optimizer group: {name}"
            )
    if not adapter_parameters or not head_parameters:
        raise AdapterContractError("adapter and head optimizer groups are required")
    if {id(item) for item in adapter_parameters} & {
        id(item) for item in head_parameters
    }:
        raise AdapterContractError("optimizer parameter groups overlap")
    return [
        {
            "name": "encoder_adapter",
            "params": adapter_parameters,
            "lr": float(adapter_learning_rate),
        },
        {
            "name": "task_heads",
            "params": head_parameters,
            "lr": float(head_learning_rate),
        },
    ]
