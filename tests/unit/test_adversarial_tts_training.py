from __future__ import annotations

from dataclasses import replace
from math import isfinite

import pytest
import torch

from voice_coach.models.adversarial_tts import (
    AdversarialPronunciationSystem,
    CorrectiveTTSGenerator,
    PrimaryPronunciationEvaluator,
    ProgressMetaCritic,
)
from voice_coach.training.adversarial_tts import (
    AdversarialLossWeights,
    AdversarialSpeechBatch,
    AdversarialTTSTrainingError,
    _primary_supervised_loss,
    alternating_adversarial_step,
    audit_optimizer_partition,
    validate_adversarial_batch,
)


def _system() -> AdversarialPronunciationSystem:
    return AdversarialPronunciationSystem(
        generator=CorrectiveTTSGenerator(
            vocabulary_size=32,
            hidden_dim=12,
            feedback_dim=6,
            acoustic_dim=8,
            hop_length=4,
            residual_blocks=1,
            dropout=0.0,
            max_edit_amplitude=0.2,
        ),
        primary_evaluator=PrimaryPronunciationEvaluator(
            vocabulary_size=32,
            hidden_dim=12,
            embedding_dim=12,
            aspect_count=4,
            feedback_dim=6,
        ),
        meta_critic=ProgressMetaCritic(
            hidden_dim=12,
            embedding_dim=12,
            feedback_dim=6,
        ),
    )


def _batch() -> AdversarialSpeechBatch:
    generator = torch.Generator().manual_seed(11)
    learner = torch.tanh(torch.randn(2, 96, generator=generator) * 0.2)
    native = torch.tanh(torch.randn(2, 112, generator=generator) * 0.2)
    learner_tokens = torch.tensor([[1, 2, 3, 0], [4, 5, 6, 0]])
    native_tokens = torch.tensor([[7, 8, 9, 0], [10, 11, 12, 0]])
    return AdversarialSpeechBatch(
        learner_waveform=learner,
        learner_text_tokens=learner_tokens,
        learner_quality_target=torch.tensor([0.25, 0.85]),
        learner_aspect_targets=torch.tensor(
            [[0.2, 0.4, 0.5, 0.6], [0.9, 0.8, 0.8, 0.9]]
        ),
        native_waveform=native,
        native_text_tokens=native_tokens,
        native_quality_target=torch.tensor([0.95, 0.90]),
        native_aspect_targets=torch.tensor(
            [[0.9, 0.8, 0.9, 0.9], [0.8, 0.9, 0.8, 0.9]]
        ),
        mismatched_learner_text_tokens=learner_tokens.flip(0),
        mismatched_native_text_tokens=native_tokens.flip(0),
    )


def _optimizers(
    system: AdversarialPronunciationSystem,
) -> tuple[torch.optim.Optimizer, ...]:
    return (
        torch.optim.Adam(system.generator.parameters(), lr=1e-3),
        torch.optim.Adam(system.primary_evaluator.core_parameters(), lr=1e-3),
        torch.optim.Adam(system.meta_critic.parameters(), lr=1e-3),
        torch.optim.Adam(system.primary_evaluator.meta_parameters(), lr=1e-3),
    )


def _state(module: torch.nn.Module) -> dict[str, torch.Tensor]:
    return {name: value.detach().clone() for name, value in module.state_dict().items()}


def _changed(
    before: dict[str, torch.Tensor],
    after: dict[str, torch.Tensor],
) -> bool:
    return any(not torch.equal(before[name], after[name]) for name in before)


def test_one_alternating_step_updates_all_four_disjoint_roles() -> None:
    torch.manual_seed(5)
    system = _system()
    optimizers = _optimizers(system)
    before = {
        "generator": _state(system.generator),
        "primary": _state(system.primary_evaluator),
        "meta": _state(system.meta_critic),
    }

    metrics = alternating_adversarial_step(
        system,
        _batch(),
        generator_optimizer=optimizers[0],
        primary_optimizer=optimizers[1],
        meta_critic_optimizer=optimizers[2],
        reward_adapter_optimizer=optimizers[3],
    )

    assert all(isfinite(value) for value in metrics.to_dict().values())
    assert metrics.generator_gradient_norm > 0
    assert metrics.primary_gradient_norm > 0
    assert metrics.meta_gradient_norm > 0
    assert metrics.reward_adapter_gradient_norm > 0
    assert _changed(before["generator"], _state(system.generator))
    assert _changed(before["primary"], _state(system.primary_evaluator))
    assert _changed(before["meta"], _state(system.meta_critic))
    assert all(
        parameter.grad is None for parameter in system.primary_evaluator.parameters()
    )
    assert all(parameter.grad is None for parameter in system.meta_critic.parameters())


def test_optimizer_audit_rejects_generator_parameter_in_evaluator_role() -> None:
    system = _system()
    optimizers = list(_optimizers(system))
    optimizers[1] = torch.optim.Adam(
        list(system.primary_evaluator.core_parameters())
        + [next(iter(system.generator.parameters()))],
        lr=1e-3,
    )

    with pytest.raises(AdversarialTTSTrainingError, match="primary optimizer"):
        audit_optimizer_partition(
            system,
            generator_optimizer=optimizers[0],
            primary_optimizer=optimizers[1],
            meta_critic_optimizer=optimizers[2],
            reward_adapter_optimizer=optimizers[3],
        )


def test_batch_rejects_nonfinite_soft_quality_targets() -> None:
    system = _system()
    optimizers = _optimizers(system)
    batch = replace(
        _batch(),
        learner_quality_target=torch.tensor([0.0, float("nan")]),
    )

    with pytest.raises(
        AdversarialTTSTrainingError,
        match="learner_quality_target",
    ):
        alternating_adversarial_step(
            system,
            batch,
            generator_optimizer=optimizers[0],
            primary_optimizer=optimizers[1],
            meta_critic_optimizer=optimizers[2],
            reward_adapter_optimizer=optimizers[3],
        )


def test_missing_aspect_targets_are_ignored_instead_of_treated_as_zero() -> None:
    torch.manual_seed(13)
    system = _system()
    batch = replace(
        _batch(),
        learner_aspect_mask=torch.tensor(
            [[True, False, True, True], [True, False, True, True]]
        ),
        native_aspect_mask=torch.zeros((2, 4), dtype=torch.bool),
        native_content_mask=torch.zeros(2, dtype=torch.bool),
    )
    changed = replace(
        batch,
        learner_aspect_targets=batch.learner_aspect_targets.clone(),
        native_aspect_targets=torch.ones_like(batch.native_aspect_targets),
    )
    changed.learner_aspect_targets[:, 1] = 1.0
    detached_fake = batch.learner_waveform.detach()

    first = _primary_supervised_loss(
        system,
        batch,
        detached_fake,
        AdversarialLossWeights(),
    )
    second = _primary_supervised_loss(
        system,
        changed,
        detached_fake,
        AdversarialLossWeights(),
    )

    torch.testing.assert_close(first, second)


def test_supervision_masks_must_be_binary_and_match_target_shape() -> None:
    batch = replace(
        _batch(),
        learner_aspect_mask=torch.ones((2, 3), dtype=torch.bool),
    )
    with pytest.raises(
        AdversarialTTSTrainingError,
        match="learner_aspect_mask",
    ):
        validate_adversarial_batch(batch, aspect_count=4)

    batch = replace(
        _batch(),
        native_content_mask=torch.tensor([0.0, 0.5]),
    )
    with pytest.raises(
        AdversarialTTSTrainingError,
        match="native_content_mask must be binary",
    ):
        validate_adversarial_batch(batch, aspect_count=4)
