#!/usr/bin/env python3
"""Run resumable multi-batch adversarial TTS training on approved train data.

The default duration bound keeps the execution bounded for the current
prototype. Set ``--max-duration-seconds`` explicitly to widen the eligible
cohort. Validation is a speaker-disjoint subset of the allowed learner train
split; protected dev/locked splits are never opened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from voice_coach.training.adversarial_tts_contract import (
    load_adversarial_tts_config,
    preflight_adversarial_tts,
)

ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/experiments/adversarial_tts_feedback_v1.yaml"))
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--learner-dataset-root", type=Path, default=Path("data/restricted/aihub/dataset-71490"))
    parser.add_argument("--prompt-reservation", type=Path, default=Path("data/processed/aihub71490/manifests/aihub71490_prompt_challenge.json"))
    parser.add_argument("--native-corpus-root", type=Path, default=Path("data/raw/research-noncommercial/kss"))
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-batches-per-epoch", type=int)
    parser.add_argument("--max-duration-seconds", type=float, default=2.0)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--checkpoint-root", type=Path, default=Path("artifacts/experiments/adversarial_tts_feedback_v1/full_checkpoints"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/experiments/adversarial_tts_feedback_v1/full_training_report.json"))
    return parser


def _contained(
    workspace: Path,
    candidate: Path,
    *,
    field: str,
    directory: bool,
    reject_protected: bool = False,
) -> Path:
    resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve(strict=directory)
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{field} escapes workspace") from exc
    lowered = {part.casefold() for part in relative.parts}
    if reject_protected and any(
        part in {"raw", "restricted"} or part == ".env" or part.startswith(".env.")
        for part in lowered
    ):
        raise ValueError(f"{field} violates privacy boundary")
    if directory and not resolved.is_dir():
        raise ValueError(f"{field} must be a directory")
    return resolved


def _split_speaker_disjoint(records: tuple[Any, ...], *, fraction: float, seed: int) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    if not 0 < fraction < 1:
        raise ValueError("validation_fraction must be in (0, 1)")
    groups: dict[str, list[Any]] = {}
    for record in records:
        speaker = getattr(record, "speaker_id_sha256", None)
        if not isinstance(speaker, str) or len(speaker) != 64:
            raise ValueError("speaker metadata is required for validation split")
        groups.setdefault(speaker, []).append(record)
    if len(groups) < 2:
        raise ValueError("at least two speakers are required for validation split")
    ordered = sorted(
        groups,
        key=lambda speaker: hashlib.sha256(f"{seed}:{speaker}".encode()).hexdigest(),
    )
    validation_count = max(1, min(len(ordered) - 1, round(len(ordered) * fraction)))
    validation_speakers = set(ordered[:validation_count])
    train = tuple(record for record in records if record.speaker_id_sha256 not in validation_speakers)
    validation = tuple(record for record in records if record.speaker_id_sha256 in validation_speakers)
    if not train or not validation:
        raise ValueError("speaker split produced an empty partition")
    return train, validation


def main() -> int:
    args = _parser().parse_args()
    workspace = args.workspace_root.resolve(strict=True)
    preflight = preflight_adversarial_tts(workspace_root=workspace, config_path=args.config)
    config_path = args.config if args.config.is_absolute() else workspace / args.config
    config = load_adversarial_tts_config(config_path.resolve(strict=True))
    checkpoint_root = _contained(
        workspace,
        args.checkpoint_root,
        field="checkpoint_root",
        directory=False,
        reject_protected=True,
    )
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    report_path = (args.report if args.report.is_absolute() else workspace / args.report).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    learner_root = _contained(workspace, args.learner_dataset_root, field="learner_dataset_root", directory=True)
    reservation = _contained(workspace, args.prompt_reservation, field="prompt_reservation", directory=False)
    native_root = _contained(workspace, args.native_corpus_root, field="native_corpus_root", directory=True)
    manifest = (workspace / config["data"]["learner"]["manifest"]).resolve(strict=True)
    native_manifest = (workspace / config["data"]["native"]["manifest"]).resolve(strict=True)

    import torch

    from voice_coach.application.adversarial_tts import AdversarialTTSEngine
    from voice_coach.data.adversarial_speech import (
        DurationMatchedPairSampler,
        NativeAudioResolver,
        load_aihub_learner_records,
        load_native_speech_records,
        native_policy_from_catalog,
    )
    from voice_coach.training.adversarial_tts import (
        build_adversarial_optimizers,
        build_adversarial_system,
        loss_weights_from_mapping,
    )
    from voice_coach.training.adversarial_tts_runner import run_adversarial_training

    torch.manual_seed(int(config["training"]["seed"]))
    torch.set_num_threads(1)
    learner_result = load_aihub_learner_records(
        manifest,
        dataset_root=learner_root,
        prompt_reservation_path=reservation,
        allowed_splits=("train",),
        max_duration_seconds=args.max_duration_seconds,
    )
    train_records, validation_records = _split_speaker_disjoint(
        learner_result.records,
        fraction=args.validation_fraction,
        seed=int(config["training"]["seed"]),
    )
    native_result = load_native_speech_records(
        native_manifest,
        policies={"kss": native_policy_from_catalog("kss")},
        vocabulary_size=int(config["model"]["vocabulary_size"]),
        max_duration_seconds=args.max_duration_seconds,
    )
    train_sampler = DurationMatchedPairSampler(train_records, native_result.records, seed=int(config["training"]["seed"]))
    validation_sampler = DurationMatchedPairSampler(validation_records, native_result.records, seed=int(config["training"]["seed"]) + 1)
    resolver = NativeAudioResolver({"kss": native_root})
    resume_metadata: dict[str, object] = {}
    resume_best_checkpoint: str | None = None
    resume_best_checkpoint: Path | None = None
    if args.resume is not None:
        resume = _contained(
            workspace,
            args.resume,
            field="resume",
            directory=False,
            reject_protected=True,
        )
        resume_engine = AdversarialTTSEngine.from_checkpoint(
            resume,
            config,
            device=args.device,
        )
        system = resume_engine.system
        optimizers = build_adversarial_optimizers(system, config)
        optimizer_map = {
            "generator": optimizers.generator,
            "primary": optimizers.primary,
            "meta_critic": optimizers.meta_critic,
            "reward_adapter": optimizers.reward_adapter,
        }
        resume_engine.load_optimizer_checkpoint(resume, optimizer_map)
        resume_best_checkpoint = resume.with_name(
            resume.name.replace("_latest.safetensors", "_best.safetensors")
        )
        if not resume_best_checkpoint.is_file():
            resume_best_checkpoint = None
        resume_metadata = json.loads(resume.with_suffix(resume.suffix + ".json").read_text(encoding="utf-8"))
        saved_best = resume_metadata.get("best_checkpoint_name")
        if isinstance(saved_best, str) and saved_best:
            resume_best_checkpoint = saved_best
        elif resume_metadata.get("is_best") is True:
            resume_best_checkpoint = str(resume)
    else:
        system = build_adversarial_system(config)
        optimizers = build_adversarial_optimizers(system, config)
    result = run_adversarial_training(
        system,
        config=config,
        train_sampler=train_sampler,
        validation_sampler=validation_sampler,
        native_audio_resolver=resolver,
        optimizers=optimizers,
        weights=loss_weights_from_mapping(config["training"]["loss_weights"]),
        epochs=args.epochs,
        batch_size=args.batch_size,
        checkpoint_root=checkpoint_root,
        max_batches_per_epoch=args.max_batches_per_epoch,
        start_epoch=int(resume_metadata.get("epoch", -1)) + 1,
        optimizer_step=int(resume_metadata.get("optimizer_step", 0)),
        best_metric_value=resume_metadata.get("best_metric_value"),
        best_checkpoint_path=resume_best_checkpoint,
        device=args.device,
    )
    document = {
        "status": "multi_batch_training_completed",
        "research_result": False,
        "preflight": preflight,
        "learner_records_loaded": len(learner_result.records),
        "learner_train_records": len(train_records),
        "learner_validation_records": len(validation_records),
        "learner_train_speakers": len({record.speaker_id_sha256 for record in train_records}),
        "learner_validation_speakers": len({record.speaker_id_sha256 for record in validation_records}),
        "native_records_loaded": len(native_result.records),
        "learner_exclusions": learner_result.exclusion_counts(),
        "native_exclusions": native_result.exclusion_counts(),
        "resume": resume_metadata,
        "run": result.to_dict(),
    }
    from voice_coach.reproducibility.evidence import write_json_atomic

    write_json_atomic(report_path, document)
    print(json.dumps({"report": report_path.relative_to(workspace).as_posix(), **result.to_dict()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
