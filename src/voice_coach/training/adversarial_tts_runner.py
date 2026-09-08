"""Multi-batch training orchestration for the adversarial TTS prototype.

The runner owns epoch/batch iteration, validation-loss selection, and resumable
checkpoint bookkeeping. It deliberately receives already-filtered records and
a collator so corpus policy remains in ``data.adversarial_speech``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, replace
import math
from pathlib import Path
from typing import Any

import torch

from voice_coach.application.adversarial_tts import AdversarialTTSEngine
from voice_coach.data.adversarial_speech import (
    AdversarialSpeechPair,
    DurationMatchedPairSampler,
    NativeAudioResolver,
    collate_adversarial_pairs,
)
from voice_coach.training.adversarial_tts import (
    AdversarialLossWeights,
    AdversarialSpeechBatch,
    AdversarialOptimizers,
    AdversarialStepMetrics,
    alternating_adversarial_step,
    evaluate_adversarial_batch,
)
from voice_coach.models.adversarial_tts import AdversarialPronunciationSystem


class AdversarialTTSRunnerError(RuntimeError):
    """Raised when a multi-batch run cannot preserve its state contract."""


@dataclass(frozen=True, slots=True)
class TrainingRunResult:
    optimizer_steps: int
    epochs_completed: int
    train_pair_count: int
    validation_pair_count: int
    best_metric_name: str
    best_metric_value: float
    latest_checkpoint: str
    best_checkpoint: str
    history: tuple[dict[str, float | int], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "optimizer_steps": self.optimizer_steps,
            "epochs_completed": self.epochs_completed,
            "train_pair_count": self.train_pair_count,
            "validation_pair_count": self.validation_pair_count,
            "best_metric_name": self.best_metric_name,
            "best_metric_value": self.best_metric_value,
            "latest_checkpoint": self.latest_checkpoint,
            "best_checkpoint": self.best_checkpoint,
            "history": [dict(row) for row in self.history],
        }


def _chunks(
    values: Sequence[AdversarialSpeechPair],
    batch_size: int,
) -> tuple[tuple[AdversarialSpeechPair, ...], ...]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise AdversarialTTSRunnerError("batch_size must be a positive integer")
    return tuple(
        tuple(values[index : index + batch_size])
        for index in range(0, len(values), batch_size)
    )


def _batch_to_device(batch: AdversarialSpeechBatch, device: torch.device) -> AdversarialSpeechBatch:
    values = {
        field.name: (
            getattr(batch, field.name).to(device)
            if isinstance(getattr(batch, field.name), torch.Tensor)
            else getattr(batch, field.name)
        )
        for field in fields(batch)
    }
    return replace(batch, **values)


def _finite_metric(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdversarialTTSRunnerError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise AdversarialTTSRunnerError(f"{field_name} must be finite")
    return parsed


def run_adversarial_training(
    system: AdversarialPronunciationSystem,
    *,
    config: dict[str, Any],
    train_sampler: DurationMatchedPairSampler,
    validation_sampler: DurationMatchedPairSampler | None,
    native_audio_resolver: NativeAudioResolver,
    optimizers: AdversarialOptimizers,
    weights: AdversarialLossWeights,
    epochs: int,
    batch_size: int,
    checkpoint_root: str | Path,
    max_batches_per_epoch: int | None = None,
    start_epoch: int = 0,
    start_batch_index: int = 0,
    optimizer_step: int = 0,
    best_metric_value: float | None = None,
    best_checkpoint_path: str | Path | None = None,
    device: str | torch.device = "cpu",
) -> TrainingRunResult:
    """Train all available pairs in each epoch and publish latest/best states."""

    if isinstance(epochs, bool) or not isinstance(epochs, int) or epochs < 1:
        raise AdversarialTTSRunnerError("epochs must be a positive integer")
    if isinstance(start_epoch, bool) or not isinstance(start_epoch, int) or start_epoch < 0:
        raise AdversarialTTSRunnerError("start_epoch must be non-negative")
    if isinstance(start_batch_index, bool) or not isinstance(start_batch_index, int) or start_batch_index < 0:
        raise AdversarialTTSRunnerError("start_batch_index must be non-negative")
    if isinstance(optimizer_step, bool) or not isinstance(optimizer_step, int) or optimizer_step < 0:
        raise AdversarialTTSRunnerError("optimizer_step must be non-negative")
    if max_batches_per_epoch is not None and (
        isinstance(max_batches_per_epoch, bool)
        or not isinstance(max_batches_per_epoch, int)
        or max_batches_per_epoch < 1
    ):
        raise AdversarialTTSRunnerError("max_batches_per_epoch must be positive")
    checkpoint_dir = Path(checkpoint_root)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    target_device = torch.device(device)
    if target_device.type == "cuda" and not torch.cuda.is_available():
        raise AdversarialTTSRunnerError("CUDA was requested but is unavailable")
    system.to(target_device)
    engine = AdversarialTTSEngine(system, config, device=target_device)
    metric_name = "validation_generator_loss" if validation_sampler is not None else "train_generator_loss_proxy"
    best = (
        _finite_metric(best_metric_value, "best_metric_value")
        if best_metric_value is not None
        else math.inf
    )
    history: list[dict[str, float | int]] = []
    total_train_pairs = 0
    total_validation_pairs = 0
    latest_name = ""
    best_name = str(best_checkpoint_path) if best_checkpoint_path is not None else ""

    for epoch in range(start_epoch, start_epoch + epochs):
        train_pairs = tuple(train_sampler.iter_epoch(epoch))
        batches = _chunks(train_pairs, batch_size)
        if epoch == start_epoch and start_batch_index:
            if start_batch_index >= len(batches):
                raise AdversarialTTSRunnerError("resume batch index exceeds epoch")
            batches = batches[start_batch_index:]
        if max_batches_per_epoch is not None:
            batches = batches[:max_batches_per_epoch]
        if not batches:
            raise AdversarialTTSRunnerError("training epoch contains no batches")
        train_losses: list[float] = []
        for local_index, pairs in enumerate(batches, start=start_batch_index if epoch == start_epoch else 0):
            batch = collate_adversarial_pairs(
                pairs,
                native_audio_resolver=native_audio_resolver,
                target_sample_rate=16_000,
                pad_to_multiple_of=int(config["model"]["generator"]["hop_length"]),
            )
            batch = _batch_to_device(batch, target_device)
            metrics: AdversarialStepMetrics = alternating_adversarial_step(
                system,
                batch,
                generator_optimizer=optimizers.generator,
                primary_optimizer=optimizers.primary,
                meta_critic_optimizer=optimizers.meta_critic,
                reward_adapter_optimizer=optimizers.reward_adapter,
                weights=weights,
                monotonic_margin=float(config["training"]["monotonic_margin"]),
                max_gradient_norm=float(config["training"]["max_gradient_norm"]),
            )
            optimizer_step += 1
            total_train_pairs += len(pairs)
            train_losses.append(metrics.generator_loss)

        validation_losses: list[float] = []
        if validation_sampler is not None:
            validation_pairs = tuple(validation_sampler.iter_epoch(epoch))
            total_validation_pairs += len(validation_pairs)
            for validation_batch_pairs in _chunks(validation_pairs, batch_size):
                validation_batch = collate_adversarial_pairs(
                    validation_batch_pairs,
                    native_audio_resolver=native_audio_resolver,
                    target_sample_rate=16_000,
                    pad_to_multiple_of=int(config["model"]["generator"]["hop_length"]),
                )
                validation_batch = _batch_to_device(validation_batch, target_device)
                validation_metrics = evaluate_adversarial_batch(
                    system,
                    validation_batch,
                    weights=weights,
                    monotonic_margin=float(config["training"]["monotonic_margin"]),
                )
                validation_losses.append(validation_metrics["generator_loss"])
        metric_value = sum(validation_losses or train_losses) / len(validation_losses or train_losses)
        metric_value = _finite_metric(metric_value, metric_name)
        improved = metric_value < best
        if improved:
            best = metric_value
        epoch_record = {
            "epoch": epoch,
            "optimizer_step": optimizer_step,
            "train_pairs": total_train_pairs,
            "validation_pairs": total_validation_pairs,
            "train_generator_loss": sum(train_losses) / len(train_losses),
            metric_name: metric_value,
        }
        history.append(epoch_record)
        latest_path = checkpoint_dir / f"epoch_{epoch:04d}_step_{optimizer_step:08d}_latest.safetensors"
        candidate_best_path = checkpoint_dir / f"epoch_{epoch:04d}_step_{optimizer_step:08d}_best.safetensors"
        if improved:
            best_name = str(candidate_best_path)
        latest = engine.save_checkpoint(
            latest_path,
            optimizer_step=optimizer_step,
            optimizers={
                "generator": optimizers.generator,
                "primary": optimizers.primary,
                "meta_critic": optimizers.meta_critic,
                "reward_adapter": optimizers.reward_adapter,
            },
            epoch=epoch,
            batch_index=len(batches),
            metric_name=metric_name,
            metric_value=metric_value,
            best_metric_value=best,
            best_checkpoint_name=best_name,
            is_best=improved,
        )
        latest_name = str(latest_path)
        if improved:
            best_path = candidate_best_path
            engine.save_checkpoint(
                best_path,
                optimizer_step=optimizer_step,
                optimizers={
                    "generator": optimizers.generator,
                    "primary": optimizers.primary,
                    "meta_critic": optimizers.meta_critic,
                    "reward_adapter": optimizers.reward_adapter,
                },
                epoch=epoch,
                batch_index=len(batches),
                metric_name=metric_name,
                metric_value=metric_value,
                best_metric_value=best,
                best_checkpoint_name=best_name,
                is_best=True,
            )
            best_name = str(best_path)

    return TrainingRunResult(
        optimizer_steps=optimizer_step,
        epochs_completed=epochs,
        train_pair_count=total_train_pairs,
        validation_pair_count=total_validation_pairs,
        best_metric_name=metric_name,
        best_metric_value=best,
        latest_checkpoint=latest_name,
        best_checkpoint=best_name or latest_name,
        history=tuple(history),
    )


__all__ = [
    "AdversarialTTSRunnerError",
    "TrainingRunResult",
    "run_adversarial_training",
]
