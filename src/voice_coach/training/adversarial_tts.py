"""Alternating optimization for the three-player pronunciation GAN.

The update order is explicit:

1. evaluator 1 learns from native QC targets, real L2 labels, and detached fake;
2. evaluator 2 learns an independent native/progression preference;
3. evaluator 2 distils only evaluator 1's small meta-reward adapter;
4. the generator follows detached evaluator-1 feedback.

Evaluator 2 never receives evaluator-1 outputs and never sends a gradient to
the generator.  This keeps the requested feedback hierarchy while avoiding a
colluding pair of reward models.  The repository's production S2 auditor is a
separate component and is not involved here.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from math import isfinite
from typing import Any

import torch
from torch import nn
from torch.nn import functional as F

from voice_coach.models.adversarial_tts import (
    AdversarialPronunciationSystem,
    AdversarialTTSContractError,
    PrimaryEvaluatorOutput,
)


class AdversarialTTSTrainingError(RuntimeError):
    """Raised when a batch, optimizer, loss, or gradient is unsafe."""


@dataclass(frozen=True, slots=True)
class AdversarialSpeechBatch:
    """One unpaired, conditionally balanced learner/native training batch.

    Learner quality and aspect targets must come from the actual AI Hub phone
    annotations and 1--5 human ratings.  They must not be filled with zeros
    merely because the speaker is an L2 learner.

    Native targets are QC-derived reference quality values.  The learner and
    native waveforms are unpaired by default; no cross-speaker waveform
    reconstruction loss is computed.
    """

    learner_waveform: torch.Tensor
    learner_text_tokens: torch.Tensor
    learner_quality_target: torch.Tensor
    learner_aspect_targets: torch.Tensor
    native_waveform: torch.Tensor
    native_text_tokens: torch.Tensor
    native_quality_target: torch.Tensor
    native_aspect_targets: torch.Tensor
    learner_mask: torch.Tensor | None = None
    native_mask: torch.Tensor | None = None
    mismatched_learner_text_tokens: torch.Tensor | None = None
    mismatched_native_text_tokens: torch.Tensor | None = None
    learner_aspect_mask: torch.Tensor | None = None
    native_aspect_mask: torch.Tensor | None = None
    learner_content_mask: torch.Tensor | None = None
    native_content_mask: torch.Tensor | None = None


@dataclass(frozen=True, slots=True)
class AdversarialLossWeights:
    primary_adversarial: float = 1.0
    primary_content: float = 1.0
    primary_aspects: float = 1.0
    primary_meta: float = 0.25
    meta_domain: float = 1.0
    meta_progress: float = 1.0
    meta_adjustment_regularization: float = 0.01
    generator_adversarial: float = 1.0
    generator_content: float = 1.0
    generator_reward: float = 1.0
    generator_monotonic: float = 0.5
    generator_identity: float = 1.0
    generator_minimal_edit: float = 0.1
    generator_native_identity: float = 1.0

    def __post_init__(self) -> None:
        for field in fields(self):
            name = field.name
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise AdversarialTTSTrainingError(
                    f"loss weight {name} must be finite and non-negative"
                )


@dataclass(frozen=True, slots=True)
class AdversarialStepMetrics:
    primary_loss: float
    meta_critic_loss: float
    primary_meta_loss: float
    generator_loss: float
    generator_reward_before: float
    generator_reward_after: float
    meta_progress_probability: float
    generator_gradient_norm: float
    primary_gradient_norm: float
    meta_gradient_norm: float
    reward_adapter_gradient_norm: float

    def to_dict(self) -> dict[str, float]:
        return {field.name: float(getattr(self, field.name)) for field in fields(self)}


def loss_weights_from_mapping(
    values: Mapping[str, Any],
) -> AdversarialLossWeights:
    expected = set(AdversarialLossWeights.__dataclass_fields__)
    if set(values) != expected:
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise AdversarialTTSTrainingError(
            f"loss weights differ from the contract: missing={missing}, extra={extra}"
        )
    try:
        return AdversarialLossWeights(
            **{name: float(values[name]) for name in expected}
        )
    except (TypeError, ValueError) as exc:
        raise AdversarialTTSTrainingError("loss weights must be numeric") from exc


def _finite_unit_interval(
    values: torch.Tensor,
    *,
    shape: Sequence[int],
    field_name: str,
) -> None:
    if (
        not isinstance(values, torch.Tensor)
        or tuple(values.shape) != tuple(shape)
        or not torch.is_floating_point(values)
        or not torch.isfinite(values).all()
        or not torch.all((values >= 0) & (values <= 1))
    ):
        raise AdversarialTTSTrainingError(
            f"{field_name} must be a finite {tuple(shape)} tensor in [0, 1]"
        )


def _binary_supervision_mask(
    values: torch.Tensor | None,
    *,
    shape: Sequence[int],
    field_name: str,
) -> None:
    if values is None:
        return
    if not isinstance(values, torch.Tensor) or tuple(values.shape) != tuple(shape):
        raise AdversarialTTSTrainingError(
            f"{field_name} must have shape {tuple(shape)}"
        )
    if torch.is_floating_point(values) and not torch.isfinite(values).all():
        raise AdversarialTTSTrainingError(f"{field_name} contains NaN or Inf")
    if not torch.all((values == 0) | (values == 1)):
        raise AdversarialTTSTrainingError(f"{field_name} must be binary")


def validate_adversarial_batch(
    batch: AdversarialSpeechBatch,
    *,
    aspect_count: int,
) -> None:
    if aspect_count <= 0:
        raise AdversarialTTSTrainingError("aspect_count must be positive")
    if batch.learner_waveform.ndim != 2 or batch.native_waveform.ndim != 2:
        raise AdversarialTTSTrainingError(
            "learner and native waveforms must have shape [batch, samples]"
        )
    learner_batch = int(batch.learner_waveform.shape[0])
    native_batch = int(batch.native_waveform.shape[0])
    if learner_batch <= 0 or learner_batch != native_batch:
        raise AdversarialTTSTrainingError(
            "learner and native batches must have the same positive size"
        )
    if batch.learner_text_tokens.shape[0] != learner_batch:
        raise AdversarialTTSTrainingError("learner text batch size differs")
    if batch.native_text_tokens.shape[0] != native_batch:
        raise AdversarialTTSTrainingError("native text batch size differs")
    if (
        batch.learner_mask is not None
        and batch.learner_mask.shape != batch.learner_waveform.shape
    ):
        raise AdversarialTTSTrainingError("learner mask shape differs")
    if (
        batch.native_mask is not None
        and batch.native_mask.shape != batch.native_waveform.shape
    ):
        raise AdversarialTTSTrainingError("native mask shape differs")
    _finite_unit_interval(
        batch.learner_quality_target,
        shape=(learner_batch,),
        field_name="learner_quality_target",
    )
    _finite_unit_interval(
        batch.native_quality_target,
        shape=(native_batch,),
        field_name="native_quality_target",
    )
    _finite_unit_interval(
        batch.learner_aspect_targets,
        shape=(learner_batch, aspect_count),
        field_name="learner_aspect_targets",
    )
    _finite_unit_interval(
        batch.native_aspect_targets,
        shape=(native_batch, aspect_count),
        field_name="native_aspect_targets",
    )
    _binary_supervision_mask(
        batch.learner_aspect_mask,
        shape=(learner_batch, aspect_count),
        field_name="learner_aspect_mask",
    )
    _binary_supervision_mask(
        batch.native_aspect_mask,
        shape=(native_batch, aspect_count),
        field_name="native_aspect_mask",
    )
    _binary_supervision_mask(
        batch.learner_content_mask,
        shape=(learner_batch,),
        field_name="learner_content_mask",
    )
    _binary_supervision_mask(
        batch.native_content_mask,
        shape=(native_batch,),
        field_name="native_content_mask",
    )
    mismatch_pairs = (
        (
            batch.mismatched_learner_text_tokens,
            batch.learner_text_tokens,
            "mismatched_learner_text_tokens",
        ),
        (
            batch.mismatched_native_text_tokens,
            batch.native_text_tokens,
            "mismatched_native_text_tokens",
        ),
    )
    for mismatch, matched, name in mismatch_pairs:
        if mismatch is not None and mismatch.shape != matched.shape:
            raise AdversarialTTSTrainingError(
                f"{name} must match its canonical token shape"
            )


def _parameter_ids(parameters: Iterable[nn.Parameter]) -> set[int]:
    return {id(parameter) for parameter in parameters}


def _optimizer_parameter_ids(optimizer: torch.optim.Optimizer) -> set[int]:
    return {
        id(parameter)
        for group in optimizer.param_groups
        for parameter in group["params"]
    }


def audit_optimizer_partition(
    system: AdversarialPronunciationSystem,
    *,
    generator_optimizer: torch.optim.Optimizer,
    primary_optimizer: torch.optim.Optimizer,
    meta_critic_optimizer: torch.optim.Optimizer,
    reward_adapter_optimizer: torch.optim.Optimizer,
) -> None:
    expected = {
        "generator": _parameter_ids(system.generator.parameters()),
        "primary": _parameter_ids(system.primary_evaluator.core_parameters()),
        "meta_critic": _parameter_ids(system.meta_critic.parameters()),
        "reward_adapter": _parameter_ids(system.primary_evaluator.meta_parameters()),
    }
    actual = {
        "generator": _optimizer_parameter_ids(generator_optimizer),
        "primary": _optimizer_parameter_ids(primary_optimizer),
        "meta_critic": _optimizer_parameter_ids(meta_critic_optimizer),
        "reward_adapter": _optimizer_parameter_ids(reward_adapter_optimizer),
    }
    for name in expected:
        if not expected[name] or actual[name] != expected[name]:
            raise AdversarialTTSTrainingError(
                f"{name} optimizer does not own exactly its parameter role"
            )
    names = tuple(expected)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if actual[left] & actual[right]:
                raise AdversarialTTSTrainingError(
                    f"optimizer roles overlap: {left}, {right}"
                )


def _assert_loss_finite(loss: torch.Tensor, *, field_name: str) -> None:
    if not isinstance(loss, torch.Tensor) or loss.ndim != 0 or not torch.isfinite(loss):
        raise AdversarialTTSTrainingError(f"{field_name} must be a finite scalar")


def _gradient_norm(
    parameters: Iterable[nn.Parameter],
    *,
    field_name: str,
) -> float:
    squared = 0.0
    found = False
    for parameter in parameters:
        if parameter.grad is None:
            continue
        found = True
        if not torch.isfinite(parameter.grad).all():
            raise AdversarialTTSTrainingError(
                f"{field_name} contains a non-finite gradient"
            )
        squared += float(parameter.grad.detach().float().square().sum())
    if not found:
        raise AdversarialTTSTrainingError(f"{field_name} did not receive a gradient")
    return squared**0.5


@contextmanager
def _frozen(parameters: Iterable[nn.Parameter]) -> Iterator[None]:
    materialized = tuple(parameters)
    previous = tuple(parameter.requires_grad for parameter in materialized)
    try:
        for parameter in materialized:
            parameter.requires_grad_(False)
        yield
    finally:
        for parameter, state in zip(materialized, previous, strict=True):
            parameter.requires_grad_(state)


def _primary_output(
    system: AdversarialPronunciationSystem,
    waveform: torch.Tensor,
    tokens: torch.Tensor,
    mask: torch.Tensor | None,
) -> PrimaryEvaluatorOutput:
    return system.primary_evaluator(
        waveform,
        tokens,
        waveform_mask=mask,
    )


def _masked_elementwise_mean(
    values: torch.Tensor,
    mask: torch.Tensor | None,
) -> torch.Tensor:
    """Average only observed targets without inventing values for missing labels."""

    if mask is None:
        return values.mean()
    weights = mask.to(device=values.device, dtype=values.dtype)
    return (values * weights).sum() / weights.sum().clamp_min(1.0)


def _primary_supervised_loss(
    system: AdversarialPronunciationSystem,
    batch: AdversarialSpeechBatch,
    detached_fake: torch.Tensor,
    weights: AdversarialLossWeights,
) -> torch.Tensor:
    learner = _primary_output(
        system,
        batch.learner_waveform,
        batch.learner_text_tokens,
        batch.learner_mask,
    )
    native = _primary_output(
        system,
        batch.native_waveform,
        batch.native_text_tokens,
        batch.native_mask,
    )
    fake = _primary_output(
        system,
        detached_fake,
        batch.learner_text_tokens,
        batch.learner_mask,
    )
    adversarial = (
        F.binary_cross_entropy_with_logits(
            learner.native_logit,
            batch.learner_quality_target,
        )
        + F.binary_cross_entropy_with_logits(
            native.native_logit,
            batch.native_quality_target,
        )
        + F.binary_cross_entropy_with_logits(
            fake.native_logit,
            torch.zeros_like(fake.native_logit),
        )
    )
    content = _masked_elementwise_mean(
        F.binary_cross_entropy_with_logits(
            learner.content_logit,
            torch.ones_like(learner.content_logit),
            reduction="none",
        ),
        batch.learner_content_mask,
    ) + _masked_elementwise_mean(
        F.binary_cross_entropy_with_logits(
            native.content_logit,
            torch.ones_like(native.content_logit),
            reduction="none",
        ),
        batch.native_content_mask,
    )
    if batch.mismatched_learner_text_tokens is not None:
        mismatch = _primary_output(
            system,
            batch.learner_waveform,
            batch.mismatched_learner_text_tokens,
            batch.learner_mask,
        )
        content = content + _masked_elementwise_mean(
            F.binary_cross_entropy_with_logits(
                mismatch.content_logit,
                torch.zeros_like(mismatch.content_logit),
                reduction="none",
            ),
            batch.learner_content_mask,
        )
    if batch.mismatched_native_text_tokens is not None:
        mismatch = _primary_output(
            system,
            batch.native_waveform,
            batch.mismatched_native_text_tokens,
            batch.native_mask,
        )
        content = content + _masked_elementwise_mean(
            F.binary_cross_entropy_with_logits(
                mismatch.content_logit,
                torch.zeros_like(mismatch.content_logit),
                reduction="none",
            ),
            batch.native_content_mask,
        )
    aspects = _masked_elementwise_mean(
        (learner.aspect_scores - batch.learner_aspect_targets).square(),
        batch.learner_aspect_mask,
    ) + _masked_elementwise_mean(
        (native.aspect_scores - batch.native_aspect_targets).square(),
        batch.native_aspect_mask,
    )
    return (
        weights.primary_adversarial * adversarial
        + weights.primary_content * content
        + weights.primary_aspects * aspects
    )


def _meta_critic_supervised_loss(
    system: AdversarialPronunciationSystem,
    batch: AdversarialSpeechBatch,
    weights: AdversarialLossWeights,
) -> torch.Tensor:
    positive = system.meta_critic(
        batch.learner_waveform,
        batch.learner_waveform,
        batch.native_waveform,
        batch.native_waveform,
        source_mask=batch.learner_mask,
        previous_mask=batch.learner_mask,
        current_mask=batch.native_mask,
        native_reference_mask=batch.native_mask,
    )
    negative = system.meta_critic(
        batch.learner_waveform,
        batch.native_waveform,
        batch.learner_waveform,
        batch.native_waveform,
        source_mask=batch.learner_mask,
        previous_mask=batch.native_mask,
        current_mask=batch.learner_mask,
        native_reference_mask=batch.native_mask,
    )
    domain = F.binary_cross_entropy_with_logits(
        positive.current_native_logit,
        batch.native_quality_target,
    ) + F.binary_cross_entropy_with_logits(
        negative.current_native_logit,
        batch.learner_quality_target,
    )
    progress = F.binary_cross_entropy_with_logits(
        positive.progress_logit,
        torch.ones_like(positive.progress_logit),
    ) + F.binary_cross_entropy_with_logits(
        negative.progress_logit,
        torch.zeros_like(negative.progress_logit),
    )
    regularization = (
        positive.evaluator1_adjustment.square().mean()
        + negative.evaluator1_adjustment.square().mean()
    )
    return (
        weights.meta_domain * domain
        + weights.meta_progress * progress
        + weights.meta_adjustment_regularization * regularization
    )


def _primary_meta_distillation_loss(
    system: AdversarialPronunciationSystem,
    batch: AdversarialSpeechBatch,
    *,
    draft: torch.Tensor,
    refined: torch.Tensor,
    weights: AdversarialLossWeights,
) -> tuple[torch.Tensor, torch.Tensor]:
    with torch.no_grad():
        evaluator2 = system.meta_critic(
            batch.learner_waveform,
            draft,
            refined,
            batch.native_waveform,
            source_mask=batch.learner_mask,
            previous_mask=batch.learner_mask,
            current_mask=batch.learner_mask,
            native_reference_mask=batch.native_mask,
        )
        target_preference = torch.sigmoid(evaluator2.progress_logit)
    draft_primary = system.primary_evaluator(
        draft,
        batch.learner_text_tokens,
        waveform_mask=batch.learner_mask,
        detach_core_for_reward=True,
    )
    refined_primary = system.primary_evaluator(
        refined,
        batch.learner_text_tokens,
        waveform_mask=batch.learner_mask,
        detach_core_for_reward=True,
    )
    draft_logit = torch.logit(draft_primary.reward_score.clamp(1e-5, 1 - 1e-5))
    refined_logit = torch.logit(refined_primary.reward_score.clamp(1e-5, 1 - 1e-5))
    loss = weights.primary_meta * F.binary_cross_entropy_with_logits(
        refined_logit - draft_logit,
        target_preference,
    )
    return loss, target_preference


def _masked_l1(
    left: torch.Tensor,
    right: torch.Tensor,
    mask: torch.Tensor | None,
) -> torch.Tensor:
    if left.shape != right.shape:
        raise AdversarialTTSTrainingError("waveform reconstruction shapes differ")
    if mask is None:
        return F.l1_loss(left, right)
    weights = mask.to(dtype=left.dtype)
    return ((left - right).abs() * weights).sum() / weights.sum().clamp_min(1)


def _generator_loss(
    system: AdversarialPronunciationSystem,
    batch: AdversarialSpeechBatch,
    weights: AdversarialLossWeights,
    *,
    monotonic_margin: float,
) -> tuple[torch.Tensor, Mapping[str, torch.Tensor]]:
    trajectory = system(
        batch.learner_waveform,
        batch.learner_text_tokens,
        batch.native_waveform,
        source_mask=batch.learner_mask,
        native_reference_mask=batch.native_mask,
        detach_feedback=True,
    )
    source_evaluation = system.primary_evaluator(
        batch.learner_waveform,
        batch.learner_text_tokens,
        waveform_mask=batch.learner_mask,
    )
    native_identity = system.generator(
        batch.native_waveform,
        batch.native_text_tokens,
        source_mask=batch.native_mask,
    )
    refined = trajectory.refined_evaluation
    draft = trajectory.draft_evaluation
    adversarial = F.binary_cross_entropy_with_logits(
        refined.native_logit,
        torch.ones_like(refined.native_logit),
    )
    content = F.binary_cross_entropy_with_logits(
        refined.content_logit,
        torch.ones_like(refined.content_logit),
    )
    reward = 1.0 - refined.reward_score.mean()
    monotonic = F.relu(
        float(monotonic_margin) - (refined.reward_score - draft.reward_score)
    ).mean()
    identity = (
        1.0
        - F.cosine_similarity(
            source_evaluation.acoustic_embedding,
            refined.acoustic_embedding,
            dim=1,
        )
    ).mean()
    minimal_edit = _masked_l1(
        trajectory.refined.waveform,
        batch.learner_waveform,
        batch.learner_mask,
    )
    native_reconstruction = _masked_l1(
        native_identity.waveform,
        batch.native_waveform,
        batch.native_mask,
    )
    total = (
        weights.generator_adversarial * adversarial
        + weights.generator_content * content
        + weights.generator_reward * reward
        + weights.generator_monotonic * monotonic
        + weights.generator_identity * identity
        + weights.generator_minimal_edit * minimal_edit
        + weights.generator_native_identity * native_reconstruction
    )
    components = {
        "reward_before": draft.reward_score.mean(),
        "reward_after": refined.reward_score.mean(),
        "meta_progress": torch.sigmoid(
            trajectory.progress_evaluation.progress_logit
        ).mean(),
    }
    return total, components


def alternating_adversarial_step(
    system: AdversarialPronunciationSystem,
    batch: AdversarialSpeechBatch,
    *,
    generator_optimizer: torch.optim.Optimizer,
    primary_optimizer: torch.optim.Optimizer,
    meta_critic_optimizer: torch.optim.Optimizer,
    reward_adapter_optimizer: torch.optim.Optimizer,
    weights: AdversarialLossWeights | None = None,
    monotonic_margin: float = 0.02,
    max_gradient_norm: float = 5.0,
) -> AdversarialStepMetrics:
    """Run one finite, optimizer-isolated four-stage training iteration."""

    weights = weights or AdversarialLossWeights()
    validate_adversarial_batch(
        batch,
        aspect_count=system.primary_evaluator.aspect_count,
    )
    if not isfinite(monotonic_margin) or monotonic_margin < 0:
        raise AdversarialTTSTrainingError(
            "monotonic_margin must be finite and non-negative"
        )
    if not isfinite(max_gradient_norm) or max_gradient_norm <= 0:
        raise AdversarialTTSTrainingError(
            "max_gradient_norm must be finite and positive"
        )
    audit_optimizer_partition(
        system,
        generator_optimizer=generator_optimizer,
        primary_optimizer=primary_optimizer,
        meta_critic_optimizer=meta_critic_optimizer,
        reward_adapter_optimizer=reward_adapter_optimizer,
    )
    system.train()

    with torch.no_grad():
        detached_trajectory = system(
            batch.learner_waveform,
            batch.learner_text_tokens,
            batch.native_waveform,
            source_mask=batch.learner_mask,
            native_reference_mask=batch.native_mask,
            detach_feedback=True,
        )
        detached_draft = detached_trajectory.draft.waveform.detach()
        detached_refined = detached_trajectory.refined.waveform.detach()

    primary_optimizer.zero_grad(set_to_none=True)
    primary_loss = _primary_supervised_loss(
        system,
        batch,
        detached_refined,
        weights,
    )
    _assert_loss_finite(primary_loss, field_name="primary_loss")
    primary_loss.backward()
    primary_parameters = tuple(system.primary_evaluator.core_parameters())
    primary_gradient_norm = _gradient_norm(
        primary_parameters,
        field_name="primary evaluator",
    )
    torch.nn.utils.clip_grad_norm_(primary_parameters, max_gradient_norm)
    primary_optimizer.step()

    meta_critic_optimizer.zero_grad(set_to_none=True)
    meta_loss = _meta_critic_supervised_loss(system, batch, weights)
    _assert_loss_finite(meta_loss, field_name="meta_critic_loss")
    meta_loss.backward()
    meta_parameters = tuple(system.meta_critic.parameters())
    meta_gradient_norm = _gradient_norm(
        meta_parameters,
        field_name="meta critic",
    )
    torch.nn.utils.clip_grad_norm_(meta_parameters, max_gradient_norm)
    meta_critic_optimizer.step()

    reward_adapter_optimizer.zero_grad(set_to_none=True)
    primary_meta_loss, meta_preference = _primary_meta_distillation_loss(
        system,
        batch,
        draft=detached_draft,
        refined=detached_refined,
        weights=weights,
    )
    _assert_loss_finite(
        primary_meta_loss,
        field_name="primary_meta_loss",
    )
    primary_meta_loss.backward()
    reward_parameters = tuple(system.primary_evaluator.meta_parameters())
    reward_gradient_norm = _gradient_norm(
        reward_parameters,
        field_name="reward adapter",
    )
    torch.nn.utils.clip_grad_norm_(reward_parameters, max_gradient_norm)
    reward_adapter_optimizer.step()

    # Remove gradients from the three evaluator stages before the generator
    # stage.  Frozen evaluator parameters must remain grad-free even though
    # their differentiable input path supplies a gradient to the waveform.
    system.primary_evaluator.zero_grad(set_to_none=True)
    system.meta_critic.zero_grad(set_to_none=True)
    generator_optimizer.zero_grad(set_to_none=True)
    with (
        _frozen(system.primary_evaluator.parameters()),
        _frozen(system.meta_critic.parameters()),
    ):
        generator_loss, generator_components = _generator_loss(
            system,
            batch,
            weights,
            monotonic_margin=monotonic_margin,
        )
        _assert_loss_finite(generator_loss, field_name="generator_loss")
        generator_loss.backward()
    generator_parameters = tuple(system.generator.parameters())
    generator_gradient_norm = _gradient_norm(
        generator_parameters,
        field_name="generator",
    )
    torch.nn.utils.clip_grad_norm_(generator_parameters, max_gradient_norm)
    generator_optimizer.step()

    return AdversarialStepMetrics(
        primary_loss=float(primary_loss.detach()),
        meta_critic_loss=float(meta_loss.detach()),
        primary_meta_loss=float(primary_meta_loss.detach()),
        generator_loss=float(generator_loss.detach()),
        generator_reward_before=float(generator_components["reward_before"].detach()),
        generator_reward_after=float(generator_components["reward_after"].detach()),
        meta_progress_probability=float(meta_preference.mean().detach()),
        generator_gradient_norm=generator_gradient_norm,
        primary_gradient_norm=primary_gradient_norm,
        meta_gradient_norm=meta_gradient_norm,
        reward_adapter_gradient_norm=reward_gradient_norm,
    )


def build_adversarial_system(
    config: Mapping[str, Any],
) -> AdversarialPronunciationSystem:
    """Build the three modules from an already validated configuration."""

    try:
        model = config["model"]
        generator_config = model["generator"]
        primary_config = model["evaluator1"]
        meta_config = model["evaluator2_meta_critic"]
        from voice_coach.models.adversarial_tts import (
            CorrectiveTTSGenerator,
            PrimaryPronunciationEvaluator,
            ProgressMetaCritic,
        )

        generator = CorrectiveTTSGenerator(
            vocabulary_size=int(model["vocabulary_size"]),
            hidden_dim=int(generator_config["hidden_dim"]),
            feedback_dim=int(model["feedback_dim"]),
            acoustic_dim=int(generator_config["acoustic_dim"]),
            hop_length=int(generator_config["hop_length"]),
            residual_blocks=int(generator_config["residual_blocks"]),
            dropout=float(generator_config["dropout"]),
            max_edit_amplitude=float(generator_config["max_edit_amplitude"]),
        )
        primary = PrimaryPronunciationEvaluator(
            vocabulary_size=int(model["vocabulary_size"]),
            hidden_dim=int(primary_config["hidden_dim"]),
            embedding_dim=int(primary_config["embedding_dim"]),
            aspect_count=int(model["aspect_count"]),
            feedback_dim=int(model["feedback_dim"]),
        )
        meta = ProgressMetaCritic(
            hidden_dim=int(meta_config["hidden_dim"]),
            embedding_dim=int(meta_config["embedding_dim"]),
            feedback_dim=int(model["feedback_dim"]),
        )
        return AdversarialPronunciationSystem(
            generator=generator,
            primary_evaluator=primary,
            meta_critic=meta,
            max_meta_adjustment=float(model["max_meta_adjustment"]),
        )
    except (KeyError, TypeError, ValueError, AdversarialTTSContractError) as exc:
        raise AdversarialTTSTrainingError(
            "validated adversarial TTS model configuration is inconsistent"
        ) from exc
