"""Three-player, source-conditioned pronunciation correction prototype.

The module contains only differentiable PyTorch components.  It does not read
data, download checkpoints, write audio, or claim that a generated waveform is
production quality.  The generator is deliberately source-conditioned: its
goal is to preserve the learner's content and voice while applying a bounded
correction, not to imitate a particular Korean speaker.

``PrimaryPronunciationEvaluator`` is the user's evaluator 1.  It produces
structured, detached feedback for a second generator pass.

``ProgressMetaCritic`` is the user's evaluator 2.  It owns an independent
waveform encoder and observes only waveform trajectories.  It never consumes
evaluator-1 logits, labels, alignments, or embeddings.  Its preference becomes
a target for evaluator 1's small ``MetaRewardAdapter``; evaluator 2 does not
backpropagate directly into the generator.

This training-internal meta-critic is intentionally separate from the
repository's production S2 auditor, which must remain an independent frozen
holdout check.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

import torch
from torch import nn
from torch.nn import functional as F


class AdversarialTTSContractError(ValueError):
    """Raised when a tensor or architecture violates the public contract."""


class EncodedWaveform(NamedTuple):
    sequence: torch.Tensor
    pooled: torch.Tensor


@dataclass(frozen=True, slots=True)
class GeneratorOutput:
    """Waveform and acoustic frame output from one generator pass."""

    waveform: torch.Tensor
    acoustic_frames: torch.Tensor


@dataclass(frozen=True, slots=True)
class PrimaryEvaluatorOutput:
    """Evaluator-1 scores and structured correction feedback."""

    native_logit: torch.Tensor
    content_logit: torch.Tensor
    aspect_scores: torch.Tensor
    feedback: torch.Tensor
    acoustic_embedding: torch.Tensor
    reward_score: torch.Tensor


@dataclass(frozen=True, slots=True)
class MetaCriticOutput:
    """Evaluator-2 assessment of one before/after trajectory."""

    progress_logit: torch.Tensor
    current_native_logit: torch.Tensor
    native_distance: torch.Tensor
    evaluator1_adjustment: torch.Tensor


@dataclass(frozen=True, slots=True)
class FeedbackTrajectory:
    """Draft, evaluator feedback, and refined waveform for one request."""

    draft: GeneratorOutput
    refined: GeneratorOutput
    draft_evaluation: PrimaryEvaluatorOutput
    refined_evaluation: PrimaryEvaluatorOutput
    progress_evaluation: MetaCriticOutput
    applied_feedback: torch.Tensor


def _validate_waveform(
    waveform: torch.Tensor,
    mask: torch.Tensor | None,
    *,
    field_name: str,
) -> tuple[torch.Tensor, torch.Tensor]:
    if not isinstance(waveform, torch.Tensor):
        raise AdversarialTTSContractError(f"{field_name} must be a tensor")
    if waveform.ndim != 2 or waveform.shape[0] <= 0 or waveform.shape[1] < 16:
        raise AdversarialTTSContractError(
            f"{field_name} must have shape [batch, samples>=16]"
        )
    if not torch.is_floating_point(waveform) or not torch.isfinite(waveform).all():
        raise AdversarialTTSContractError(
            f"{field_name} must contain finite floating-point samples"
        )
    if float(waveform.detach().abs().max()) > 1.5:
        raise AdversarialTTSContractError(
            f"{field_name} exceeds the accepted normalized sample range"
        )
    if mask is None:
        validated_mask = torch.ones_like(waveform, dtype=torch.bool)
    else:
        if (
            not isinstance(mask, torch.Tensor)
            or mask.shape != waveform.shape
            or mask.ndim != 2
        ):
            raise AdversarialTTSContractError(
                f"{field_name}_mask must match the waveform shape"
            )
        if torch.is_floating_point(mask):
            if not torch.isfinite(mask).all():
                raise AdversarialTTSContractError(
                    f"{field_name}_mask contains NaN or Inf"
                )
            if not torch.all((mask == 0) | (mask == 1)):
                raise AdversarialTTSContractError(f"{field_name}_mask must be binary")
        validated_mask = mask.to(dtype=torch.bool)
    if not validated_mask.any(dim=1).all():
        raise AdversarialTTSContractError(
            f"{field_name}_mask must retain at least one sample per item"
        )
    return waveform, validated_mask


def _validate_text_tokens(
    tokens: torch.Tensor,
    *,
    vocabulary_size: int,
    field_name: str = "text_tokens",
) -> torch.Tensor:
    if (
        not isinstance(tokens, torch.Tensor)
        or tokens.ndim != 2
        or tokens.shape[0] <= 0
        or tokens.shape[1] <= 0
    ):
        raise AdversarialTTSContractError(
            f"{field_name} must have shape [batch, tokens]"
        )
    if tokens.dtype not in {
        torch.int8,
        torch.int16,
        torch.int32,
        torch.int64,
        torch.uint8,
    }:
        raise AdversarialTTSContractError(f"{field_name} must contain integers")
    if int(tokens.min()) < 0 or int(tokens.max()) >= vocabulary_size:
        raise AdversarialTTSContractError(
            f"{field_name} contains an out-of-vocabulary ID"
        )
    if not (tokens != 0).any(dim=1).all():
        raise AdversarialTTSContractError(
            f"{field_name} must contain a non-padding token per item"
        )
    return tokens.to(dtype=torch.long)


def _masked_mean(sequence: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    weights = mask.to(dtype=sequence.dtype).unsqueeze(-1)
    denominator = weights.sum(dim=1).clamp_min(1.0)
    return (sequence * weights).sum(dim=1) / denominator


class TextConditioner(nn.Module):
    """Embed canonical Korean phone/text IDs without retaining raw text."""

    def __init__(self, *, vocabulary_size: int, hidden_dim: int) -> None:
        super().__init__()
        if vocabulary_size <= 1 or hidden_dim <= 0:
            raise AdversarialTTSContractError(
                "text vocabulary and hidden dimension must be positive"
            )
        self.vocabulary_size = int(vocabulary_size)
        self.hidden_dim = int(hidden_dim)
        self.embedding = nn.Embedding(
            self.vocabulary_size,
            self.hidden_dim,
            padding_idx=0,
        )
        self.projection = nn.Sequential(
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.GELU(),
            nn.LayerNorm(self.hidden_dim),
        )

    def forward(
        self,
        tokens: torch.Tensor,
        *,
        frame_count: int,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        tokens = _validate_text_tokens(
            tokens,
            vocabulary_size=self.vocabulary_size,
        )
        if frame_count <= 0:
            raise AdversarialTTSContractError("frame_count must be positive")
        valid = tokens != 0
        embedded = self.projection(self.embedding(tokens))
        pooled = _masked_mean(embedded, valid)
        aligned: list[torch.Tensor] = []
        for row in range(tokens.shape[0]):
            item = embedded[row, valid[row]].transpose(0, 1).unsqueeze(0)
            aligned.append(
                F.interpolate(
                    item,
                    size=frame_count,
                    mode="linear",
                    align_corners=False,
                ).squeeze(0)
            )
        return torch.stack(aligned, dim=0), pooled


class WaveformEncoder(nn.Module):
    """Small independent waveform encoder with padding-aware pooling."""

    def __init__(self, *, hidden_dim: int, embedding_dim: int) -> None:
        super().__init__()
        if hidden_dim <= 0 or embedding_dim <= 0:
            raise AdversarialTTSContractError(
                "waveform encoder dimensions must be positive"
            )
        self.embedding_dim = int(embedding_dim)
        channels = (int(hidden_dim), int(hidden_dim), self.embedding_dim)
        layers: list[nn.Module] = []
        input_channels = 1
        for output_channels in channels:
            layers.extend(
                [
                    nn.Conv1d(
                        input_channels,
                        output_channels,
                        kernel_size=7,
                        stride=2,
                        padding=3,
                    ),
                    nn.GroupNorm(1, output_channels),
                    nn.GELU(),
                ]
            )
            input_channels = output_channels
        self.network = nn.Sequential(*layers)

    def forward(
        self,
        waveform: torch.Tensor,
        *,
        mask: torch.Tensor | None = None,
        field_name: str = "waveform",
    ) -> EncodedWaveform:
        waveform, validated_mask = _validate_waveform(
            waveform,
            mask,
            field_name=field_name,
        )
        encoded = self.network(
            (waveform * validated_mask.to(dtype=waveform.dtype)).unsqueeze(1)
        ).transpose(1, 2)
        encoded_mask = (
            F.interpolate(
                validated_mask.to(dtype=encoded.dtype).unsqueeze(1),
                size=encoded.shape[1],
                mode="nearest",
            ).squeeze(1)
            > 0.5
        )
        pooled = _masked_mean(encoded, encoded_mask)
        return EncodedWaveform(sequence=encoded, pooled=pooled)


class ResidualConvBlock(nn.Module):
    def __init__(self, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.GroupNorm(1, hidden_dim),
            nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        return values + self.network(values)


class CorrectiveTTSGenerator(nn.Module):
    """Text-conditioned bounded waveform editor with feedback refinement."""

    def __init__(
        self,
        *,
        vocabulary_size: int,
        hidden_dim: int,
        feedback_dim: int,
        acoustic_dim: int,
        hop_length: int = 4,
        residual_blocks: int = 3,
        dropout: float = 0.1,
        max_edit_amplitude: float = 0.35,
    ) -> None:
        super().__init__()
        if (
            hidden_dim <= 0
            or feedback_dim <= 0
            or acoustic_dim <= 0
            or hop_length <= 0
            or residual_blocks <= 0
        ):
            raise AdversarialTTSContractError("generator dimensions must be positive")
        if not 0 <= dropout < 1:
            raise AdversarialTTSContractError("dropout must be in [0, 1)")
        if not 0 < max_edit_amplitude <= 1:
            raise AdversarialTTSContractError("max_edit_amplitude must be in (0, 1]")
        self.feedback_dim = int(feedback_dim)
        self.hop_length = int(hop_length)
        self.max_edit_amplitude = float(max_edit_amplitude)
        self.text_conditioner = TextConditioner(
            vocabulary_size=vocabulary_size,
            hidden_dim=hidden_dim,
        )
        self.source_encoder = nn.Sequential(
            nn.Conv1d(1, hidden_dim, kernel_size=7, padding=3),
            nn.GELU(),
            nn.Conv1d(
                hidden_dim,
                hidden_dim,
                kernel_size=self.hop_length,
                stride=self.hop_length,
            ),
        )
        self.text_projection = nn.Conv1d(hidden_dim, hidden_dim, kernel_size=1)
        self.feedback_projection = nn.Linear(self.feedback_dim, hidden_dim)
        self.blocks = nn.Sequential(
            *(
                ResidualConvBlock(hidden_dim, float(dropout))
                for _ in range(int(residual_blocks))
            )
        )
        self.acoustic_head = nn.Conv1d(
            hidden_dim,
            int(acoustic_dim),
            kernel_size=1,
        )
        self.waveform_head = nn.Sequential(
            nn.GroupNorm(1, hidden_dim),
            nn.GELU(),
            nn.ConvTranspose1d(
                hidden_dim,
                1,
                kernel_size=self.hop_length,
                stride=self.hop_length,
            ),
            nn.Tanh(),
        )

    def forward(
        self,
        source_waveform: torch.Tensor,
        text_tokens: torch.Tensor,
        feedback: torch.Tensor | None = None,
        *,
        source_mask: torch.Tensor | None = None,
    ) -> GeneratorOutput:
        source_waveform, validated_mask = _validate_waveform(
            source_waveform,
            source_mask,
            field_name="source_waveform",
        )
        text_tokens = _validate_text_tokens(
            text_tokens,
            vocabulary_size=self.text_conditioner.vocabulary_size,
        )
        if text_tokens.shape[0] != source_waveform.shape[0]:
            raise AdversarialTTSContractError(
                "text and source waveform batch sizes differ"
            )
        batch_size, sample_count = source_waveform.shape
        if feedback is None:
            feedback = source_waveform.new_zeros((batch_size, self.feedback_dim))
        if (
            not isinstance(feedback, torch.Tensor)
            or feedback.shape != (batch_size, self.feedback_dim)
            or not torch.is_floating_point(feedback)
            or not torch.isfinite(feedback).all()
        ):
            raise AdversarialTTSContractError(
                "feedback must be a finite [batch, feedback_dim] float tensor"
            )

        padded_count = (
            (sample_count + self.hop_length - 1) // self.hop_length
        ) * self.hop_length
        padding = padded_count - sample_count
        padded_source = F.pad(source_waveform, (0, padding))
        padded_mask = F.pad(validated_mask, (0, padding), value=False)
        masked_source = padded_source * padded_mask.to(dtype=padded_source.dtype)
        source_frames = self.source_encoder(masked_source.unsqueeze(1))
        text_frames, _ = self.text_conditioner(
            text_tokens,
            frame_count=source_frames.shape[-1],
        )
        feedback_frames = self.feedback_projection(feedback).unsqueeze(-1)
        hidden = source_frames + self.text_projection(text_frames) + feedback_frames
        hidden = self.blocks(hidden)
        acoustic_frames = self.acoustic_head(hidden).transpose(1, 2)
        edit = self.waveform_head(hidden).squeeze(1)
        corrected = torch.clamp(
            padded_source + self.max_edit_amplitude * edit,
            -1.0,
            1.0,
        )
        corrected = corrected * padded_mask.to(dtype=corrected.dtype)
        return GeneratorOutput(
            waveform=corrected[:, :sample_count],
            acoustic_frames=acoustic_frames,
        )


class MetaRewardAdapter(nn.Module):
    """Auditable non-negative weighting over evaluator-1 quality signals."""

    def __init__(self, signal_count: int) -> None:
        super().__init__()
        if signal_count <= 0:
            raise AdversarialTTSContractError("reward signal count must be positive")
        self.signal_count = int(signal_count)
        self.weight_logits = nn.Parameter(torch.zeros(self.signal_count))
        self.bias = nn.Parameter(torch.zeros(()))

    def forward(self, signals: torch.Tensor) -> torch.Tensor:
        if (
            signals.ndim != 2
            or signals.shape[1] != self.signal_count
            or not torch.isfinite(signals).all()
        ):
            raise AdversarialTTSContractError(
                "reward signals have an invalid shape or value"
            )
        weights = torch.softmax(self.weight_logits, dim=0)
        bounded = (signals * weights).sum(dim=1).clamp(1e-5, 1 - 1e-5)
        return torch.logit(bounded) + self.bias


class PrimaryPronunciationEvaluator(nn.Module):
    """Independent evaluator 1 with structured feedback and meta reward."""

    def __init__(
        self,
        *,
        vocabulary_size: int,
        hidden_dim: int,
        embedding_dim: int,
        aspect_count: int,
        feedback_dim: int,
    ) -> None:
        super().__init__()
        if aspect_count <= 0 or feedback_dim < aspect_count:
            raise AdversarialTTSContractError(
                "feedback_dim must cover every evaluator aspect"
            )
        self.aspect_count = int(aspect_count)
        self.feedback_dim = int(feedback_dim)
        self.waveform_encoder = WaveformEncoder(
            hidden_dim=hidden_dim,
            embedding_dim=embedding_dim,
        )
        self.text_conditioner = TextConditioner(
            vocabulary_size=vocabulary_size,
            hidden_dim=embedding_dim,
        )
        joint_dim = embedding_dim * 3
        self.joint = nn.Sequential(
            nn.LayerNorm(joint_dim),
            nn.Linear(joint_dim, hidden_dim),
            nn.GELU(),
        )
        self.native_head = nn.Linear(hidden_dim, 1)
        self.content_head = nn.Linear(hidden_dim, 1)
        self.aspect_head = nn.Linear(hidden_dim, self.aspect_count)
        latent_feedback_dim = self.feedback_dim - self.aspect_count
        self.latent_feedback_head: nn.Module | None = (
            nn.Linear(hidden_dim, latent_feedback_dim) if latent_feedback_dim else None
        )
        self.embedding_projection = nn.Linear(
            embedding_dim,
            embedding_dim,
        )
        self.meta_reward_adapter = MetaRewardAdapter(2 + self.aspect_count)

    def core_parameters(self) -> Iterator[nn.Parameter]:
        meta_ids = {
            id(parameter) for parameter in self.meta_reward_adapter.parameters()
        }
        for parameter in self.parameters():
            if id(parameter) not in meta_ids:
                yield parameter

    def meta_parameters(self) -> Iterator[nn.Parameter]:
        yield from self.meta_reward_adapter.parameters()

    def reward_signals(
        self,
        *,
        native_logit: torch.Tensor,
        content_logit: torch.Tensor,
        aspect_scores: torch.Tensor,
    ) -> torch.Tensor:
        return torch.cat(
            (
                torch.sigmoid(native_logit).unsqueeze(1),
                torch.sigmoid(content_logit).unsqueeze(1),
                aspect_scores,
            ),
            dim=1,
        )

    def forward(
        self,
        waveform: torch.Tensor,
        text_tokens: torch.Tensor,
        *,
        waveform_mask: torch.Tensor | None = None,
        detach_core_for_reward: bool = False,
    ) -> PrimaryEvaluatorOutput:
        encoded = self.waveform_encoder(
            waveform,
            mask=waveform_mask,
            field_name="evaluated_waveform",
        )
        text_tokens = _validate_text_tokens(
            text_tokens,
            vocabulary_size=self.text_conditioner.vocabulary_size,
        )
        if text_tokens.shape[0] != waveform.shape[0]:
            raise AdversarialTTSContractError(
                "text and evaluated waveform batch sizes differ"
            )
        _, text_pooled = self.text_conditioner(text_tokens, frame_count=1)
        joint = self.joint(
            torch.cat(
                (
                    encoded.pooled,
                    text_pooled,
                    torch.abs(encoded.pooled - text_pooled),
                ),
                dim=1,
            )
        )
        native_logit = self.native_head(joint).squeeze(1)
        content_logit = self.content_head(joint).squeeze(1)
        aspect_scores = torch.sigmoid(self.aspect_head(joint))
        aspect_feedback = 1.0 - 2.0 * aspect_scores
        if self.latent_feedback_head is None:
            feedback = aspect_feedback
        else:
            feedback = torch.cat(
                (
                    aspect_feedback,
                    torch.tanh(self.latent_feedback_head(joint)),
                ),
                dim=1,
            )
        acoustic_embedding = F.normalize(
            self.embedding_projection(encoded.pooled),
            dim=1,
        )
        reward_signals = self.reward_signals(
            native_logit=native_logit,
            content_logit=content_logit,
            aspect_scores=aspect_scores,
        )
        if detach_core_for_reward:
            reward_signals = reward_signals.detach()
        reward_score = torch.sigmoid(self.meta_reward_adapter(reward_signals))
        return PrimaryEvaluatorOutput(
            native_logit=native_logit,
            content_logit=content_logit,
            aspect_scores=aspect_scores,
            feedback=feedback,
            acoustic_embedding=acoustic_embedding,
            reward_score=reward_score,
        )


class ProgressMetaCritic(nn.Module):
    """Independent evaluator 2 for waveform progression and meta feedback."""

    def __init__(
        self,
        *,
        hidden_dim: int,
        embedding_dim: int,
        feedback_dim: int,
    ) -> None:
        super().__init__()
        if feedback_dim <= 0:
            raise AdversarialTTSContractError(
                "meta-critic feedback dimension must be positive"
            )
        self.feedback_dim = int(feedback_dim)
        self.waveform_encoder = WaveformEncoder(
            hidden_dim=hidden_dim,
            embedding_dim=embedding_dim,
        )
        trajectory_dim = embedding_dim * 6
        self.trajectory = nn.Sequential(
            nn.LayerNorm(trajectory_dim),
            nn.Linear(trajectory_dim, hidden_dim),
            nn.GELU(),
        )
        self.progress_head = nn.Linear(hidden_dim, 1)
        self.native_head = nn.Linear(embedding_dim, 1)
        self.adjustment_head = nn.Linear(hidden_dim, self.feedback_dim)

    def forward(
        self,
        source_waveform: torch.Tensor,
        previous_waveform: torch.Tensor,
        current_waveform: torch.Tensor,
        native_reference_waveform: torch.Tensor,
        *,
        source_mask: torch.Tensor | None = None,
        previous_mask: torch.Tensor | None = None,
        current_mask: torch.Tensor | None = None,
        native_reference_mask: torch.Tensor | None = None,
    ) -> MetaCriticOutput:
        source = self.waveform_encoder(
            source_waveform,
            mask=source_mask,
            field_name="meta_source_waveform",
        ).pooled
        previous = self.waveform_encoder(
            previous_waveform,
            mask=previous_mask,
            field_name="meta_previous_waveform",
        ).pooled
        current = self.waveform_encoder(
            current_waveform,
            mask=current_mask,
            field_name="meta_current_waveform",
        ).pooled
        reference = self.waveform_encoder(
            native_reference_waveform,
            mask=native_reference_mask,
            field_name="meta_native_reference_waveform",
        ).pooled
        if not (
            source.shape[0]
            == previous.shape[0]
            == current.shape[0]
            == reference.shape[0]
        ):
            raise AdversarialTTSContractError("meta-critic waveform batch sizes differ")
        hidden = self.trajectory(
            torch.cat(
                (
                    source,
                    previous,
                    current,
                    reference,
                    current - previous,
                    reference - current,
                ),
                dim=1,
            )
        )
        progress_logit = self.progress_head(hidden).squeeze(1)
        current_native_logit = self.native_head(current).squeeze(1)
        native_distance = 1.0 - F.cosine_similarity(
            current,
            reference,
            dim=1,
        )
        adjustment = torch.tanh(self.adjustment_head(hidden))
        return MetaCriticOutput(
            progress_logit=progress_logit,
            current_native_logit=current_native_logit,
            native_distance=native_distance,
            evaluator1_adjustment=adjustment,
        )


def calibrate_primary_feedback(
    primary_feedback: torch.Tensor,
    evaluator2_adjustment: torch.Tensor,
    *,
    max_adjustment: float = 0.25,
) -> torch.Tensor:
    """Bound evaluator-2's adjustment before evaluator 1 coaches the generator."""

    if (
        primary_feedback.shape != evaluator2_adjustment.shape
        or primary_feedback.ndim != 2
        or not torch.isfinite(primary_feedback).all()
        or not torch.isfinite(evaluator2_adjustment).all()
    ):
        raise AdversarialTTSContractError(
            "primary feedback and evaluator-2 adjustment must be finite peers"
        )
    if not 0 <= max_adjustment <= 1:
        raise AdversarialTTSContractError("max_adjustment must be in [0, 1]")
    return torch.clamp(
        primary_feedback + float(max_adjustment) * torch.tanh(evaluator2_adjustment),
        -1.0,
        1.0,
    )


class AdversarialPronunciationSystem(nn.Module):
    """Compose generator, evaluator 1, and the independent meta-critic."""

    def __init__(
        self,
        *,
        generator: CorrectiveTTSGenerator,
        primary_evaluator: PrimaryPronunciationEvaluator,
        meta_critic: ProgressMetaCritic,
        max_meta_adjustment: float = 0.25,
    ) -> None:
        super().__init__()
        if not 0 <= max_meta_adjustment <= 1:
            raise AdversarialTTSContractError("max_meta_adjustment must be in [0, 1]")
        if not (
            generator.feedback_dim
            == primary_evaluator.feedback_dim
            == meta_critic.feedback_dim
        ):
            raise AdversarialTTSContractError("all feedback dimensions must match")
        self.generator = generator
        self.primary_evaluator = primary_evaluator
        self.meta_critic = meta_critic
        self.max_meta_adjustment = float(max_meta_adjustment)
        self._assert_parameter_independence()

    def _assert_parameter_independence(self) -> None:
        groups = [
            {id(parameter) for parameter in module.parameters()}
            for module in (
                self.generator,
                self.primary_evaluator,
                self.meta_critic,
            )
        ]
        if groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2]:
            raise AdversarialTTSContractError(
                "generator and evaluators must not share parameters"
            )

    def forward(
        self,
        source_waveform: torch.Tensor,
        text_tokens: torch.Tensor,
        native_reference_waveform: torch.Tensor,
        *,
        source_mask: torch.Tensor | None = None,
        native_reference_mask: torch.Tensor | None = None,
        detach_feedback: bool = True,
    ) -> FeedbackTrajectory:
        draft = self.generator(
            source_waveform,
            text_tokens,
            source_mask=source_mask,
        )
        draft_evaluation = self.primary_evaluator(
            draft.waveform,
            text_tokens,
            waveform_mask=source_mask,
        )
        # Evaluator 2 never conditions the generator per example.  Its
        # progression preference is distilled into evaluator 1's auditable
        # MetaRewardAdapter during the separate meta-training stage.  This is
        # crucial for the default unpaired native sampling strategy: a random
        # native reference must not steer a learner toward that speaker.
        applied_feedback = draft_evaluation.feedback
        if detach_feedback:
            applied_feedback = applied_feedback.detach()
        refined = self.generator(
            source_waveform,
            text_tokens,
            applied_feedback,
            source_mask=source_mask,
        )
        refined_evaluation = self.primary_evaluator(
            refined.waveform,
            text_tokens,
            waveform_mask=source_mask,
        )
        progress_evaluation = self.meta_critic(
            source_waveform,
            draft.waveform,
            refined.waveform,
            native_reference_waveform,
            source_mask=source_mask,
            previous_mask=source_mask,
            current_mask=source_mask,
            native_reference_mask=native_reference_mask,
        )
        return FeedbackTrajectory(
            draft=draft,
            refined=refined,
            draft_evaluation=draft_evaluation,
            refined_evaluation=refined_evaluation,
            progress_evaluation=progress_evaluation,
            applied_feedback=applied_feedback,
        )
