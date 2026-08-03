from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import torch

from voice_coach.application.adversarial_tts import (
    GeneratedAudioError,
    write_generated_waveform,
)
from voice_coach.models.adversarial_tts import (
    AdversarialPronunciationSystem,
    AdversarialTTSContractError,
    CorrectiveTTSGenerator,
    PrimaryPronunciationEvaluator,
    ProgressMetaCritic,
    calibrate_primary_feedback,
)


def _system() -> AdversarialPronunciationSystem:
    return AdversarialPronunciationSystem(
        generator=CorrectiveTTSGenerator(
            vocabulary_size=32,
            hidden_dim=16,
            feedback_dim=6,
            acoustic_dim=8,
            hop_length=4,
            residual_blocks=2,
            dropout=0.0,
            max_edit_amplitude=0.2,
        ),
        primary_evaluator=PrimaryPronunciationEvaluator(
            vocabulary_size=32,
            hidden_dim=16,
            embedding_dim=16,
            aspect_count=4,
            feedback_dim=6,
        ),
        meta_critic=ProgressMetaCritic(
            hidden_dim=16,
            embedding_dim=16,
            feedback_dim=6,
        ),
        max_meta_adjustment=0.2,
    )


def _inputs() -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    torch.manual_seed(3)
    source = torch.tanh(torch.randn(2, 128) * 0.2)
    native = torch.tanh(torch.randn(2, 160) * 0.2)
    tokens = torch.tensor([[1, 2, 3, 0], [4, 5, 6, 7]])
    mask = torch.ones_like(source, dtype=torch.bool)
    mask[0, 112:] = False
    source[0, 112:] = 0
    return source, native, tokens, mask


def test_feedback_system_produces_bounded_two_pass_waveforms() -> None:
    system = _system()
    source, native, tokens, mask = _inputs()

    trajectory = system(
        source,
        tokens,
        native,
        source_mask=mask,
    )

    assert trajectory.draft.waveform.shape == source.shape
    assert trajectory.refined.waveform.shape == source.shape
    assert trajectory.draft.acoustic_frames.shape[:2] == (2, 32)
    assert trajectory.refined_evaluation.aspect_scores.shape == (2, 4)
    assert trajectory.applied_feedback.shape == (2, 6)
    assert not trajectory.applied_feedback.requires_grad
    assert torch.all(trajectory.refined.waveform[~mask] == 0)
    assert torch.max(torch.abs(trajectory.refined.waveform)) <= 1
    assert torch.isfinite(trajectory.progress_evaluation.native_distance).all()


def test_generator_and_both_evaluators_share_no_parameter_storage() -> None:
    system = _system()
    groups = [
        {id(parameter) for parameter in module.parameters()}
        for module in (
            system.generator,
            system.primary_evaluator,
            system.meta_critic,
        )
    ]
    assert not groups[0] & groups[1]
    assert not groups[0] & groups[2]
    assert not groups[1] & groups[2]

    meta_signature = inspect.signature(system.meta_critic.forward)
    assert "evaluator1" not in " ".join(meta_signature.parameters)
    assert "logit" not in " ".join(meta_signature.parameters)


def test_meta_reward_parameters_are_disjoint_from_evaluator_core() -> None:
    evaluator = _system().primary_evaluator
    core = {id(parameter) for parameter in evaluator.core_parameters()}
    meta = {id(parameter) for parameter in evaluator.meta_parameters()}
    all_parameters = {id(parameter) for parameter in evaluator.parameters()}

    assert core
    assert meta
    assert not core & meta
    assert core | meta == all_parameters


def test_feedback_adjustment_is_bounded_and_rejects_shape_drift() -> None:
    primary = torch.full((2, 3), 0.9)
    adjustment = torch.full((2, 3), 100.0)

    calibrated = calibrate_primary_feedback(
        primary,
        adjustment,
        max_adjustment=0.25,
    )

    assert torch.all(calibrated <= 1)
    assert torch.all(calibrated >= -1)
    with pytest.raises(AdversarialTTSContractError, match="finite peers"):
        calibrate_primary_feedback(primary, adjustment[:, :2])


@pytest.mark.parametrize(
    "waveform,tokens,error",
    [
        (torch.zeros(2, 15), torch.ones(2, 2, dtype=torch.long), "samples"),
        (
            torch.full((2, 32), float("nan")),
            torch.ones(2, 2, dtype=torch.long),
            "finite",
        ),
        (
            torch.zeros(2, 32),
            torch.tensor([[1, 2], [1, 32]]),
            "out-of-vocabulary",
        ),
    ],
)
def test_generator_fails_closed_on_invalid_audio_or_tokens(
    waveform: torch.Tensor,
    tokens: torch.Tensor,
    error: str,
) -> None:
    generator = _system().generator
    with pytest.raises(AdversarialTTSContractError, match=error):
        generator(waveform, tokens)


def test_feedback_is_detached_but_final_waveform_keeps_generator_gradient() -> None:
    system = _system()
    source, native, tokens, mask = _inputs()
    trajectory = system(
        source,
        tokens,
        native,
        source_mask=mask,
        detach_feedback=True,
    )

    assert not trajectory.applied_feedback.requires_grad
    trajectory.refined.waveform.square().mean().backward()
    assert any(
        parameter.grad is not None for parameter in system.generator.parameters()
    )


def test_generated_waveform_writer_adds_mandatory_provenance_sidecar(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reference.wav"
    artifact = write_generated_waveform(
        output,
        torch.linspace(-0.5, 0.5, 160),
    )

    sidecar = output.with_suffix(".wav.generated.json")
    assert output.is_file()
    assert sidecar.is_file()
    assert artifact.media_label == "AI-generated pronunciation reference"
    assert artifact.sample_count == 160
    assert len(artifact.sha256) == 64
    serialized = sidecar.read_text(encoding="utf-8")
    assert "source_path" not in serialized
    assert "speaker" not in serialized
    with pytest.raises(GeneratedAudioError, match="already exists"):
        write_generated_waveform(output, torch.zeros(160))
