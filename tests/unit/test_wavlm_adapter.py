from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from voice_coach.models.wavlm_adapter import (
    AdapterContractError,
    AdapterTunedWavLM,
    AdapterWrappedEncoderLayer,
    ResidualBottleneckAdapter,
    audit_trainable_parameters,
    group_trainable_parameters,
    inject_residual_adapters,
)


ALLOW_PATTERNS = [
    r"^wavlm\.encoder\.layers\.[0-9]+\.adapter\."
    r"(layer_norm|down|up)\.(weight|bias)$",
    r"^phone_head\.",
    r"^utterance_head\.",
]


class LayerWithIndex(nn.Module):
    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.linear = nn.Linear(hidden_size, hidden_size)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        position_bias: torch.Tensor | None = None,
        output_attentions: bool = False,
        index: int = 0,
    ) -> tuple[torch.Tensor, torch.Tensor, None]:
        del attention_mask, output_attentions
        bias = (
            position_bias
            if position_bias is not None
            else torch.tensor(float(index), device=hidden_states.device)
        )
        return self.linear(hidden_states), bias, None


class LayerWithoutIndex(nn.Module):
    def __init__(self, hidden_size: int) -> None:
        super().__init__()
        self.linear = nn.Linear(hidden_size, hidden_size)

    def forward(
        self,
        hidden_states: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        position_bias: torch.Tensor | None = None,
        output_attentions: bool = False,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        del attention_mask, output_attentions
        bias = (
            position_bias
            if position_bias is not None
            else torch.tensor(0.0, device=hidden_states.device)
        )
        return self.linear(hidden_states), bias


class FakeWavLM(nn.Module):
    def __init__(self, hidden_size: int = 8, layers: int = 3) -> None:
        super().__init__()
        self.input_projection = nn.Linear(1, hidden_size)
        self.encoder = nn.Module()
        self.encoder.layers = nn.ModuleList(
            LayerWithIndex(hidden_size) for _ in range(layers)
        )

    def forward(
        self,
        input_values: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
        return_dict: bool = True,
    ) -> SimpleNamespace:
        del attention_mask, return_dict
        hidden = self.input_projection(input_values.unsqueeze(-1))
        position_bias = None
        for index, layer in enumerate(self.encoder.layers):
            output = layer(hidden, position_bias=position_bias, index=index)
            hidden, position_bias = output[:2]
        return SimpleNamespace(last_hidden_state=hidden)


def test_residual_adapter_zero_initialization_is_identity() -> None:
    adapter = ResidualBottleneckAdapter(
        hidden_size=8,
        bottleneck_dim=3,
        dropout=0.0,
        zero_initialize_output=True,
    )
    hidden = torch.randn(2, 5, 8)
    assert torch.equal(adapter(hidden), hidden)
    assert torch.count_nonzero(adapter.up.weight) == 0
    assert torch.count_nonzero(adapter.up.bias) == 0


def test_wrapped_layer_preserves_tuple_for_both_wavlm_signatures() -> None:
    hidden = torch.randn(2, 4, 8)
    for base in (LayerWithIndex(8), LayerWithoutIndex(8)):
        wrapped = AdapterWrappedEncoderLayer(
            base,
            hidden_size=8,
            bottleneck_dim=2,
            dropout=0.0,
            zero_initialize_output=True,
        )
        output = wrapped(hidden, index=2)
        assert isinstance(output, tuple)
        assert len(output) >= 2
        assert output[0].shape == hidden.shape


def test_injection_freezes_backbone_and_rejects_second_injection() -> None:
    wavlm = FakeWavLM()
    selected = inject_residual_adapters(
        wavlm,
        hidden_size=8,
        bottleneck_dim=2,
        dropout=0.0,
    )
    assert selected == (0, 1, 2)
    for name, parameter in wavlm.named_parameters():
        if ".adapter." in name:
            assert parameter.requires_grad
        else:
            assert not parameter.requires_grad
    with pytest.raises(AdapterContractError, match="only run once"):
        inject_residual_adapters(
            wavlm,
            hidden_size=8,
            bottleneck_dim=2,
            dropout=0.0,
        )


def _model() -> AdapterTunedWavLM:
    return AdapterTunedWavLM(
        wavlm=FakeWavLM(),
        hidden_size=8,
        encoder_layer_count=3,
        adapter_bottleneck_dim=2,
        adapter_dropout=0.0,
        zero_initialize_adapter_output=True,
        target_vocabulary_size=4,
        target_embedding_dim=3,
        phone_hidden_dim=6,
        phone_dropout=0.0,
        utterance_hidden_dim=6,
        utterance_dropout=0.0,
        score_range=(1.0, 5.0),
    )


def test_adapter_model_trainable_allowlist_and_optimizer_groups() -> None:
    model = _model()
    audit = audit_trainable_parameters(model, allow_patterns=ALLOW_PATTERNS)
    assert audit["backbone_non_adapter_trainable_count"] == 0
    assert audit["full_fine_tuning"] is False
    assert 0 < audit["trainable_fraction"] < 1
    assert any(".adapter." in name for name in audit["trainable_parameter_names"])
    assert any(
        name.startswith("phone_head.") for name in audit["trainable_parameter_names"]
    )
    assert any(
        name.startswith("utterance_head.")
        for name in audit["trainable_parameter_names"]
    )

    groups = group_trainable_parameters(
        model,
        adapter_learning_rate=1e-4,
        head_learning_rate=3e-4,
    )
    assert [group["name"] for group in groups] == [
        "encoder_adapter",
        "task_heads",
    ]
    first = {id(item) for item in groups[0]["params"]}
    second = {id(item) for item in groups[1]["params"]}
    assert first
    assert second
    assert not first & second


def test_allowlist_rejects_accidentally_unfrozen_backbone() -> None:
    model = _model()
    model.wavlm.input_projection.weight.requires_grad_(True)
    with pytest.raises(AdapterContractError, match="non-adapter backbone"):
        audit_trainable_parameters(model, allow_patterns=ALLOW_PATTERNS)


def test_allowlist_rejects_wildcard_policy_even_with_frozen_backbone() -> None:
    model = _model()
    with pytest.raises(AdapterContractError, match="canonical allowlist"):
        audit_trainable_parameters(model, allow_patterns=[r".*"])


def test_adapter_model_encode_propagates_gradient_only_to_allowed_tensors() -> None:
    model = _model()
    hidden = model.encode(torch.randn(2, 12))
    loss = hidden.square().mean()
    loss.backward()
    audit_trainable_parameters(model, allow_patterns=ALLOW_PATTERNS)
    assert model.wavlm.input_projection.weight.grad is None
    assert any(
        parameter.grad is not None
        for name, parameter in model.named_parameters()
        if ".adapter." in name
    )


def test_adapter_rejects_wrong_hidden_shape_and_layer_count() -> None:
    adapter = ResidualBottleneckAdapter(
        hidden_size=8,
        bottleneck_dim=2,
        dropout=0.0,
    )
    with pytest.raises(AdapterContractError, match="unexpected shape"):
        adapter(torch.zeros(2, 7))
    with pytest.raises(AdapterContractError, match="layer count"):
        AdapterTunedWavLM(
            wavlm=FakeWavLM(layers=2),
            hidden_size=8,
            encoder_layer_count=3,
            adapter_bottleneck_dim=2,
            adapter_dropout=0.0,
            zero_initialize_adapter_output=True,
            target_vocabulary_size=2,
            target_embedding_dim=2,
            phone_hidden_dim=4,
            phone_dropout=0.0,
            utterance_hidden_dim=4,
            utterance_dropout=0.0,
        )


def test_adapter_wraps_real_transformers_wavlm_encoder_contract() -> None:
    from transformers import WavLMConfig, WavLMModel

    configuration = WavLMConfig(
        hidden_size=8,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=16,
        conv_dim=(4, 4, 4),
        conv_stride=(2, 2, 2),
        conv_kernel=(3, 3, 3),
        num_conv_pos_embeddings=8,
        num_conv_pos_embedding_groups=2,
        mask_time_prob=0.0,
        layerdrop=0.0,
    )
    model = AdapterTunedWavLM(
        wavlm=WavLMModel(configuration),
        hidden_size=8,
        encoder_layer_count=2,
        adapter_bottleneck_dim=2,
        adapter_dropout=0.0,
        zero_initialize_adapter_output=True,
        target_vocabulary_size=3,
        target_embedding_dim=2,
        phone_hidden_dim=4,
        phone_dropout=0.0,
        utterance_hidden_dim=4,
        utterance_dropout=0.0,
    )
    hidden = model.encode(
        torch.randn(2, 128),
        attention_mask=torch.ones(2, 128, dtype=torch.long),
    )
    assert hidden.shape == (2, 15, 8)
    audit = audit_trainable_parameters(model, allow_patterns=ALLOW_PATTERNS)
    assert audit["backbone_non_adapter_trainable_count"] == 0


def test_real_tiny_wavlm_gradient_checkpointing_backward_reaches_adapters() -> None:
    from transformers import WavLMConfig, WavLMModel

    configuration = WavLMConfig(
        hidden_size=8,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=16,
        conv_dim=(4, 4, 4),
        conv_stride=(2, 2, 2),
        conv_kernel=(3, 3, 3),
        num_conv_pos_embeddings=8,
        num_conv_pos_embedding_groups=2,
        mask_time_prob=0.0,
        layerdrop=0.0,
    )
    wavlm = WavLMModel(configuration)
    wavlm.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    wavlm.freeze_feature_encoder()
    model = AdapterTunedWavLM(
        wavlm=wavlm,
        hidden_size=8,
        encoder_layer_count=2,
        adapter_bottleneck_dim=2,
        adapter_dropout=0.0,
        zero_initialize_adapter_output=True,
        target_vocabulary_size=3,
        target_embedding_dim=2,
        phone_hidden_dim=4,
        phone_dropout=0.0,
        utterance_hidden_dim=4,
        utterance_dropout=0.0,
    )
    model.train()
    hidden = model.encode(
        torch.randn(1, 128),
        attention_mask=torch.ones(1, 128, dtype=torch.long),
    )
    hidden.square().mean().backward()

    assert all(
        layer.base_layer.gradient_checkpointing for layer in model.wavlm.encoder.layers
    )
    assert model.wavlm.feature_extractor._requires_grad is False
    assert any(
        parameter.grad is not None and torch.isfinite(parameter.grad).all()
        for name, parameter in model.named_parameters()
        if ".adapter.up." in name
    )
    assert all(
        parameter.grad is None
        for name, parameter in model.named_parameters()
        if name.startswith("wavlm.") and ".adapter." not in name
    )
