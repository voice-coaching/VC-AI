#!/usr/bin/env python3
"""Validate or train the source-conditioned adversarial TTS prototype.

``--synthetic-smoke`` proves model/loss/gradient wiring with random tensors.
``--real-data-smoke`` opens a bounded AI Hub/KSS batch.  ``--train-steps``
repeats that explicit batch and can publish a model-only safetensors checkpoint;
this produces an executable prototype artifact, not a paper result claim.
"""

from __future__ import annotations

import argparse
from itertools import islice
import json
from pathlib import Path
import sys
from typing import Any

from voice_coach.training.adversarial_tts_contract import (
    load_adversarial_tts_config,
    preflight_adversarial_tts,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]
TORCH_IMPORTED_BEFORE_PREFLIGHT = "torch" in sys.modules


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Torch-free validate the pronunciation GAN contract or execute "
            "bounded prototype training."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/adversarial_tts_feedback_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    smoke_group = parser.add_mutually_exclusive_group()
    smoke_group.add_argument(
        "--synthetic-smoke",
        action="store_true",
        help=(
            "Run synthetic CPU training without opening corpus audio."
        ),
    )
    smoke_group.add_argument(
        "--real-data-smoke",
        action="store_true",
        help=(
            "Run bounded CPU training on actual AI Hub/KSS audio; it is "
            "still not a paper result."
        ),
    )
    parser.add_argument(
        "--learner-dataset-root",
        type=Path,
        default=Path("data/restricted/aihub/dataset-71490"),
    )
    parser.add_argument(
        "--prompt-reservation",
        type=Path,
        default=Path(
            "data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json"
        ),
    )
    parser.add_argument(
        "--native-corpus-root",
        type=Path,
        default=Path("data/raw/research-noncommercial/kss"),
    )
    parser.add_argument(
        "--real-record-limit",
        type=int,
        default=16,
        help="Maximum eligible records indexed per domain for real-data smoke.",
    )
    parser.add_argument(
        "--real-batch-size",
        type=int,
        default=2,
        help="Real-data smoke batch size; restricted to 1--4.",
    )
    parser.add_argument(
        "--real-max-duration-seconds",
        type=float,
        default=2.0,
        help="Reject longer utterances before real-data smoke decoding.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional workspace-relative aggregate-only JSON report.",
    )
    parser.add_argument(
        "--train-steps",
        type=int,
        default=1,
        help=(
            "Number of alternating updates on the selected smoke batch; "
            "must be positive."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help=(
            "Optional workspace-relative .safetensors model checkpoint. "
            "Existing outputs are never overwritten."
        ),
    )
    return parser.parse_args()


def _synthetic_smoke(
    config: dict[str, Any],
    *,
    train_steps: int,
    checkpoint_path: Path | None,
) -> dict[str, object]:
    import torch

    from voice_coach.application.adversarial_tts import AdversarialTTSEngine
    from voice_coach.training.adversarial_tts import (
        AdversarialSpeechBatch,
        alternating_adversarial_step,
        build_adversarial_optimizers,
        build_adversarial_system,
        loss_weights_from_mapping,
    )

    torch.manual_seed(int(config["training"]["seed"]))  # type: ignore[index]
    torch.set_num_threads(1)
    system = build_adversarial_system(config)
    learner = torch.tanh(torch.randn(2, 256) * 0.15)
    native = torch.tanh(torch.randn(2, 288) * 0.15)
    learner_tokens = torch.tensor(
        [[1, 2, 3, 4, 0], [5, 6, 7, 8, 0]],
        dtype=torch.long,
    )
    native_tokens = torch.tensor(
        [[9, 10, 11, 12, 0], [13, 14, 15, 16, 0]],
        dtype=torch.long,
    )
    batch = AdversarialSpeechBatch(
        learner_waveform=learner,
        learner_text_tokens=learner_tokens,
        learner_quality_target=torch.tensor([0.25, 0.80]),
        learner_aspect_targets=torch.tensor(
            [[0.2, 0.3, 0.5, 0.5], [0.8, 0.7, 0.8, 0.9]]
        ),
        native_waveform=native,
        native_text_tokens=native_tokens,
        native_quality_target=torch.tensor([0.90, 0.95]),
        native_aspect_targets=torch.tensor(
            [[0.9, 0.8, 0.9, 0.9], [0.9, 0.9, 0.8, 0.9]]
        ),
        mismatched_learner_text_tokens=learner_tokens.flip(0),
        mismatched_native_text_tokens=native_tokens.flip(0),
    )
    optimizers = build_adversarial_optimizers(system, config)
    weights = loss_weights_from_mapping(config["training"]["loss_weights"])  # type: ignore[index]
    metrics = None
    for _ in range(train_steps):
        metrics = alternating_adversarial_step(
            system,
            batch,
            generator_optimizer=optimizers.generator,
            primary_optimizer=optimizers.primary,
            meta_critic_optimizer=optimizers.meta_critic,
            reward_adapter_optimizer=optimizers.reward_adapter,
            weights=weights,
            monotonic_margin=float(config["training"]["monotonic_margin"]),  # type: ignore[index]
            max_gradient_norm=float(config["training"]["max_gradient_norm"]),  # type: ignore[index]
        )
    if metrics is None:  # pragma: no cover - guarded by main validation
        raise RuntimeError("synthetic training did not execute")
    engine = AdversarialTTSEngine(system, config, device="cpu")
    checkpoint = None
    if checkpoint_path is not None:
        checkpoint = engine.save_checkpoint(
            checkpoint_path,
            optimizer_step=train_steps,
        )
    trajectory = engine.generate(learner, learner_tokens, native)
    return {
        "status": (
            "synthetic_training_completed"
            if train_steps > 1 or checkpoint is not None
            else "synthetic_smoke_completed"
        ),
        "device": "cpu",
        "corpus_audio_opened": False,
        "checkpoint_published": checkpoint is not None,
        "checkpoint": checkpoint,
        "research_result": False,
        "optimizer_steps": train_steps,
        "refined_waveform_shape": list(trajectory.refined.waveform.shape),
        "metrics": metrics.to_dict(),
    }


def _resolve_workspace_input(
    workspace: Path,
    candidate: Path,
    *,
    field_name: str,
    require_directory: bool,
) -> Path:
    resolved = candidate if candidate.is_absolute() else workspace / candidate
    resolved = resolved.resolve(strict=True)
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{field_name} escapes the workspace") from exc
    lowered = {part.lower() for part in relative.parts}
    if any(part == ".env" or part.startswith(".env.") for part in lowered):
        raise ValueError(f"{field_name} may not reference an environment file")
    if require_directory and not resolved.is_dir():
        raise ValueError(f"{field_name} must be a directory")
    if not require_directory and not resolved.is_file():
        raise ValueError(f"{field_name} must be a file")
    return resolved


def _real_data_smoke(
    config: dict[str, Any],
    *,
    workspace: Path,
    learner_dataset_root: Path,
    prompt_reservation: Path,
    native_corpus_root: Path,
    record_limit: int,
    batch_size: int,
    max_duration_seconds: float,
    train_steps: int,
    checkpoint_path: Path | None,
) -> dict[str, object]:
    if not 1 <= batch_size <= 4:
        raise ValueError("real_batch_size must be between 1 and 4")
    if record_limit < batch_size or record_limit > 128:
        raise ValueError(
            "real_record_limit must cover the batch and may not exceed 128"
        )
    if not 0 < max_duration_seconds <= 10:
        raise ValueError("real_max_duration_seconds must be in the interval (0, 10]")

    learner_root = _resolve_workspace_input(
        workspace,
        learner_dataset_root,
        field_name="learner_dataset_root",
        require_directory=True,
    )
    reservation_path = _resolve_workspace_input(
        workspace,
        prompt_reservation,
        field_name="prompt_reservation",
        require_directory=False,
    )
    native_root = _resolve_workspace_input(
        workspace,
        native_corpus_root,
        field_name="native_corpus_root",
        require_directory=True,
    )
    learner_manifest = _resolve_workspace_input(
        workspace,
        Path(config["data"]["learner"]["manifest"]),
        field_name="learner_manifest",
        require_directory=False,
    )
    native_manifest = _resolve_workspace_input(
        workspace,
        Path(config["data"]["native"]["manifest"]),
        field_name="native_manifest",
        require_directory=False,
    )

    import torch

    from voice_coach.data.adversarial_speech import (
        DurationMatchedPairSampler,
        NativeAudioResolver,
        collate_adversarial_pairs,
        load_aihub_learner_records,
        load_native_speech_records,
        native_policy_from_catalog,
    )
    from voice_coach.application.adversarial_tts import AdversarialTTSEngine
    from voice_coach.training.adversarial_tts import (
        alternating_adversarial_step,
        build_adversarial_optimizers,
        build_adversarial_system,
        loss_weights_from_mapping,
    )

    torch.manual_seed(int(config["training"]["seed"]))
    torch.set_num_threads(1)
    learner_result = load_aihub_learner_records(
        learner_manifest,
        dataset_root=learner_root,
        prompt_reservation_path=reservation_path,
        allowed_splits=("train",),
        max_duration_seconds=max_duration_seconds,
        limit=record_limit,
    )
    native_policy = native_policy_from_catalog("kss")
    native_result = load_native_speech_records(
        native_manifest,
        policies={"kss": native_policy},
        vocabulary_size=int(config["model"]["vocabulary_size"]),
        max_duration_seconds=max_duration_seconds,
        limit=record_limit,
    )
    sampler = DurationMatchedPairSampler(
        learner_result.records,
        native_result.records,
        seed=int(config["training"]["seed"]),
    )
    pairs = tuple(islice(sampler, batch_size))
    if len(pairs) != batch_size:
        raise RuntimeError("real-data sampler did not produce the requested batch")
    batch = collate_adversarial_pairs(
        pairs,
        native_audio_resolver=NativeAudioResolver({"kss": native_root}),
        target_sample_rate=16_000,
        pad_to_multiple_of=int(config["model"]["generator"]["hop_length"]),
    )

    system = build_adversarial_system(config)
    optimizers = build_adversarial_optimizers(system, config)
    weights = loss_weights_from_mapping(config["training"]["loss_weights"])
    metrics = None
    for _ in range(train_steps):
        metrics = alternating_adversarial_step(
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
    if metrics is None:  # pragma: no cover - guarded by main validation
        raise RuntimeError("real-data training did not execute")
    engine = AdversarialTTSEngine(system, config, device="cpu")
    checkpoint = None
    if checkpoint_path is not None:
        checkpoint = engine.save_checkpoint(
            checkpoint_path,
            optimizer_step=train_steps,
        )
    learner_aspect_mask = batch.learner_aspect_mask
    native_aspect_mask = batch.native_aspect_mask
    learner_content_mask = batch.learner_content_mask
    native_content_mask = batch.native_content_mask
    if (
        learner_aspect_mask is None
        or native_aspect_mask is None
        or learner_content_mask is None
        or native_content_mask is None
    ):
        raise RuntimeError("real-data collation omitted supervision masks")
    return {
        "status": (
            "real_data_training_completed"
            if train_steps > 1 or checkpoint is not None
            else "real_data_wiring_smoke_completed"
        ),
        "device": "cpu",
        "cuda_visible_to_process": bool(torch.cuda.is_available()),
        "corpus_audio_opened": True,
        "checkpoint_published": checkpoint is not None,
        "checkpoint": checkpoint,
        "research_result": False,
        "windowed_or_cropped_audio": False,
        "optimizer_steps": train_steps,
        "batch_size": batch_size,
        "max_duration_seconds": max_duration_seconds,
        "learner_records_loaded": len(learner_result.records),
        "native_records_loaded": len(native_result.records),
        "learner_rows_scanned": learner_result.source_rows,
        "native_rows_scanned": native_result.source_rows,
        "learner_exclusions": learner_result.exclusion_counts(),
        "native_exclusions": native_result.exclusion_counts(),
        "learner_waveform_shape": list(batch.learner_waveform.shape),
        "native_waveform_shape": list(batch.native_waveform.shape),
        "learner_aspect_target_count": int(learner_aspect_mask.sum().item()),
        "native_aspect_target_count": int(native_aspect_mask.sum().item()),
        "learner_content_supervised_count": int(learner_content_mask.sum().item()),
        "native_content_supervised_count": int(native_content_mask.sum().item()),
        "metrics": metrics.to_dict(),
    }



def _resolve_workspace_output(
    workspace: Path,
    candidate: Path,
    *,
    field_name: str,
) -> Path:
    resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{field_name} escapes the workspace") from exc
    lowered = {part.lower() for part in relative.parts}
    if any(
        part in {"raw", "restricted"}
        or part == ".env"
        or part.startswith(".env.")
        for part in lowered
    ):
        raise ValueError(f"{field_name} violates the privacy boundary")
    if resolved.suffix != ".safetensors":
        raise ValueError(f"{field_name} must end with .safetensors")
    metadata_path = resolved.with_suffix(resolved.suffix + ".json")
    if resolved.exists() or metadata_path.exists():
        raise ValueError(f"{field_name} already exists; choose a new output")
    return resolved

def _write_report(
    workspace: Path,
    report_path: Path,
    document: dict[str, object],
) -> Path:
    from voice_coach.reproducibility.evidence import write_json_atomic

    candidate = report_path if report_path.is_absolute() else workspace / report_path
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("report path escapes the workspace") from exc
    lowered = [part.lower() for part in relative.parts]
    if any(
        part in {"raw", "restricted", ".env"} or part.startswith(".env.")
        for part in lowered
    ):
        raise ValueError("report path violates the privacy boundary")
    write_json_atomic(resolved, document)
    return resolved


def main() -> int:
    args = parse_args()
    workspace = args.workspace_root.resolve(strict=True)
    preflight = preflight_adversarial_tts(
        workspace_root=workspace,
        config_path=args.config,
    )
    if TORCH_IMPORTED_BEFORE_PREFLIGHT or "torch" in sys.modules:
        raise RuntimeError("Torch was imported before the contract preflight")
    if args.train_steps < 1:
        raise ValueError("train_steps must be positive")
    if args.checkpoint is not None and not (args.synthetic_smoke or args.real_data_smoke):
        raise ValueError("--checkpoint requires --synthetic-smoke or --real-data-smoke")
    if not args.synthetic_smoke and not args.real_data_smoke:
        print(json.dumps(preflight, ensure_ascii=False, sort_keys=True))
        return 0
    checkpoint_path = None
    if args.checkpoint is not None:
        checkpoint_path = _resolve_workspace_output(
            workspace,
            args.checkpoint,
            field_name="checkpoint",
        )
    config_path = args.config
    if not config_path.is_absolute():
        config_path = workspace / config_path
    config = load_adversarial_tts_config(config_path.resolve(strict=True))
    if args.real_data_smoke:
        smoke = _real_data_smoke(
            config,
            workspace=workspace,
            learner_dataset_root=args.learner_dataset_root,
            prompt_reservation=args.prompt_reservation,
            native_corpus_root=args.native_corpus_root,
            record_limit=args.real_record_limit,
            batch_size=args.real_batch_size,
            max_duration_seconds=args.real_max_duration_seconds,
            train_steps=args.train_steps,
            checkpoint_path=checkpoint_path,
        )
    else:
        smoke = _synthetic_smoke(
            config,
            train_steps=args.train_steps,
            checkpoint_path=checkpoint_path,
        )
    result: dict[str, object] = {"preflight": preflight, "smoke": smoke}
    if args.report is not None:
        written = _write_report(workspace, args.report, result)
        result["report"] = written.relative_to(workspace).as_posix()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
