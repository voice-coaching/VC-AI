from __future__ import annotations

import pytest
import torch

from voice_coach.models.wavlm_hierarchy import (
    HierarchyContractError,
    IncrementalHierarchyHead,
    LayerWeightedSum,
    MultiAspectAttentionPool,
    VARIANT_ORDER,
)


def _inputs() -> dict[str, torch.Tensor]:
    generator = torch.Generator().manual_seed(7)
    return {
        "phone_layers": torch.randn(3, 5, 8, generator=generator),
        "word_layers": torch.randn(3, 3, 8, generator=generator),
        "utterance_layers": torch.randn(3, 8, generator=generator),
        "phone_to_word": torch.tensor([0, 0, 1, -1, 2], dtype=torch.long),
        "target_indices": torch.tensor([0, 1, 2, 1, 0], dtype=torch.long),
    }


def _model(variant: str) -> IncrementalHierarchyHead:
    return IncrementalHierarchyHead(
        variant=variant,  # type: ignore[arg-type]
        hidden_size=8,
        encoder_layer_count=3,
        projection_dim=6,
        target_vocabulary_size=3,
        target_embedding_dim=2,
        attention_hidden_dim=4,
        attention_aspects=2,
        dropout=0.0,
    )


@pytest.mark.parametrize("variant", VARIANT_ORDER)
def test_every_incremental_variant_outputs_only_two_supervised_tasks(
    variant: str,
) -> None:
    model = _model(variant)
    output = model(**_inputs())

    assert output.phone_logits.shape == (5,)
    assert output.utterance_scores.shape == (3,)
    assert torch.all((output.utterance_scores >= 1) & (output.utterance_scores <= 5))
    assert not hasattr(output, "word_predictions")
    assert not any("word_head" in name for name, _ in model.named_parameters())

    (output.phone_logits.sum() + output.utterance_scores.sum()).backward()
    missing_gradients = [
        name
        for name, parameter in model.named_parameters()
        if parameter.requires_grad and parameter.grad is None
    ]
    assert missing_gradients == []


def test_layerwise_variants_use_all_layers_and_receive_layer_weight_gradient() -> None:
    model = _model("layerwise_weighted_hierarchy")
    output = model(**_inputs())
    output.phone_logits.sum().backward()

    assert model.layer_mixture is not None
    assert model.layer_mixture.logits.grad is not None
    assert torch.any(model.layer_mixture.logits.grad != 0)


def test_final_layer_variant_is_invariant_to_earlier_layer_changes() -> None:
    model = _model("final_layer_hierarchy")
    model.eval()
    first = _inputs()
    second = {key: value.clone() for key, value in first.items()}
    second["phone_layers"][:-1] += 100
    second["word_layers"][:-1] -= 100
    second["utterance_layers"][:-1] += 50

    with torch.no_grad():
        first_output = model(**first)
        second_output = model(**second)

    assert torch.equal(first_output.phone_logits, second_output.phone_logits)
    assert torch.equal(
        first_output.utterance_scores,
        second_output.utterance_scores,
    )


def test_layer_weighted_sum_starts_as_uniform_mean() -> None:
    mixture = LayerWeightedSum(3)
    values = torch.tensor([[1.0], [4.0], [7.0]])
    assert mixture(values).item() == pytest.approx(4.0)


def test_multi_aspect_attention_is_permutation_invariant_over_rows() -> None:
    pool = MultiAspectAttentionPool(4, 3, 2)
    values = torch.arange(20, dtype=torch.float32).reshape(5, 4)
    permutation = torch.tensor([3, 0, 4, 1, 2])
    assert torch.allclose(pool(values), pool(values[permutation]), atol=1e-6)


def test_hierarchy_rejects_invalid_mapping_and_handles_zero_word_utterance() -> None:
    model = _model("final_layer_hierarchy")
    values = _inputs()
    values["phone_to_word"] = torch.tensor([0, 0, 1, -2, 2])
    with pytest.raises(HierarchyContractError, match="out of range"):
        model(**values)

    values = _inputs()
    values["word_layers"] = torch.empty((3, 0, 8))
    values["phone_to_word"] = torch.full((5,), -1, dtype=torch.long)
    output = model(**values)
    assert output.phone_logits.shape == (5,)
    assert output.utterance_scores.shape == (3,)


def test_simple_control_has_independent_phone_and_utterance_projections() -> None:
    simple = _model("simple_head_control")
    flat = _model("flat_shared_multitask")

    assert simple.simple_phone_projection is not None
    assert simple.simple_utterance_projection is not None
    simple_phone_ids = {
        id(value) for value in simple.simple_phone_projection.parameters()
    }
    simple_utterance_ids = {
        id(value) for value in simple.simple_utterance_projection.parameters()
    }
    assert not simple_phone_ids & simple_utterance_ids
    assert flat.simple_phone_projection is None
    assert flat.simple_utterance_projection is None
    assert simple.word_projection is None
    assert simple.phone_to_word_pool is None
