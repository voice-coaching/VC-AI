#!/usr/bin/env python3
"""Gate-protected XLS-R observed-phone CTC smoke and full training."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCTC,
    Trainer,
    TrainingArguments,
    Wav2Vec2Processor,
)
from transformers.trainer_utils import EvalPrediction

from korean_phone_ctc.data.training_manifest import load_training_and_dev_records
from korean_phone_ctc.io_utils import atomic_write_json
from korean_phone_ctc.training_utils import (
    collapse_ctc_ids,
    score_phone_sequences,
    validate_manual_alignment_gate,
)

MODEL_ID = "facebook/wav2vec2-xls-r-300m"
MODEL_REVISION = "1a640f32ac3e39899438a2931f9924c02f080a54"
PROJECT_ROOT = Path(__file__).absolute().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--processor-dir", type=Path, required=True)
    parser.add_argument("--manual-audit-result", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--resume-from-checkpoint")
    return parser.parse_args()


class AudioPhoneDataset(Dataset[dict[str, Any]]):
    def __init__(
        self,
        records: Sequence[dict[str, Any]],
        *,
        dataset_root: Path,
        vocabulary: dict[str, int],
    ) -> None:
        self.records = list(records)
        self.dataset_root = dataset_root
        self.vocabulary = vocabulary

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self.records[index]
        path = self.dataset_root.joinpath(*Path(record["audio_path"]).parts)
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        waveform = audio.mean(axis=1)
        if rate != 16_000:
            divisor = math.gcd(int(rate), 16_000)
            waveform = resample_poly(
                waveform,
                16_000 // divisor,
                int(rate) // divisor,
            ).astype(np.float32, copy=False)
        labels = [self.vocabulary.get(phone, 1) for phone in record["phones"]]
        if 1 in labels:
            raise ValueError(f"unknown phone in {record['utterance_id']}")
        return {"input_values": waveform, "labels": labels}


class CtcCollator:
    def __init__(self, processor: Wav2Vec2Processor) -> None:
        self.processor = processor

    def __call__(self, features: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        inputs = self.processor.feature_extractor.pad(
            [{"input_values": item["input_values"]} for item in features],
            padding=True,
            return_tensors="pt",
        )
        max_labels = max(len(item["labels"]) for item in features)
        labels = torch.full((len(features), max_labels), -100, dtype=torch.long)
        for row, item in enumerate(features):
            values = torch.tensor(item["labels"], dtype=torch.long)
            labels[row, : len(values)] = values
        inputs["labels"] = labels
        return inputs


class MetricComputer:
    def __init__(
        self,
        vocabulary: dict[str, int],
        records: Sequence[dict[str, Any]],
    ) -> None:
        self.id_to_token = {value: key for key, value in vocabulary.items()}
        self.records = records
        self.last_details: dict[str, Any] = {}

    def tokens(self, ids: Sequence[int]) -> list[str]:
        return [
            self.id_to_token.get(token_id, "<unk>")
            for token_id in collapse_ctc_ids(ids, blank_id=0)
        ]

    def __call__(self, prediction: EvalPrediction) -> dict[str, float]:
        predicted = np.asarray(prediction.predictions)
        labels = np.asarray(prediction.label_ids)
        references: list[list[str]] = []
        hypotheses: list[list[str]] = []
        for predicted_ids, label_ids in zip(predicted, labels, strict=True):
            hypotheses.append(self.tokens([int(value) for value in predicted_ids]))
            references.append(
                [
                    self.id_to_token[int(value)]
                    for value in label_ids
                    if int(value) != -100
                ]
            )
        metrics, confusion = score_phone_sequences(references, hypotheses)
        examples: list[dict[str, Any]] = []
        normal = error = 0
        for record, reference, hypothesis in zip(
            self.records, references, hypotheses, strict=True
        ):
            wanted_error = bool(record["error_presence"])
            if wanted_error and error >= 5:
                continue
            if not wanted_error and normal >= 5:
                continue
            item_metrics, _ = score_phone_sequences([reference], [hypothesis])
            examples.append(
                {
                    "utterance_id": record["utterance_id"],
                    "speaker_id": record["speaker_id"],
                    "prompt": record["prompt"],
                    "reference": reference,
                    "hypothesis": hypothesis,
                    "error_presence": wanted_error,
                    "per": item_metrics.per,
                }
            )
            error += int(wanted_error)
            normal += int(not wanted_error)
            if error >= 5 and normal >= 5:
                break
        self.last_details = {
            "metrics": metrics.as_dict(),
            "confusion_matrix": [
                {"reference": pair[0], "hypothesis": pair[1], "count": count}
                for pair, count in confusion.most_common()
            ],
            "representative_predictions": examples,
        }
        return {
            key: float(value)
            for key, value in metrics.as_dict().items()
            if key
            in {
                "per",
                "substitution_rate",
                "deletion_rate",
                "insertion_rate",
                "empty_decode_rate",
            }
        }


def argmax_logits(
    logits: torch.Tensor | tuple[torch.Tensor, ...], labels: torch.Tensor
) -> torch.Tensor:
    del labels
    value = logits[0] if isinstance(logits, tuple) else logits
    return value.argmax(dim=-1)


def load_model(vocab_size: int) -> torch.nn.Module:
    model = AutoModelForCTC.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        vocab_size=vocab_size,
        pad_token_id=0,
        ctc_loss_reduction="mean",
        ctc_zero_infinity=True,
        ignore_mismatched_sizes=True,
    )
    model.freeze_feature_encoder()
    model.config.use_cache = False
    return model


def main() -> int:
    args = parse_args()
    gate = json.loads(args.manual_audit_result.read_text(encoding="utf-8"))
    validate_manual_alignment_gate(gate)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    limits: Mapping[str, int | None] = (
        {"train": 5_000, "dev": 1_000}
        if args.mode == "smoke"
        else {"train": None, "dev": None}
    )
    manifest_bundle = load_training_and_dev_records(
        args.manifest_dir,
        workspace_root=PROJECT_ROOT,
        train_limit=limits["train"],
        dev_limit=limits["dev"],
    )
    processor = Wav2Vec2Processor.from_pretrained(args.processor_dir)
    vocabulary = manifest_bundle.vocabulary
    records = {
        "train": manifest_bundle.train,
        "dev": manifest_bundle.dev,
    }
    datasets = {
        split: AudioPhoneDataset(
            split_records,
            dataset_root=args.dataset_root,
            vocabulary=vocabulary,
        )
        for split, split_records in records.items()
    }
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metric = MetricComputer(vocabulary, records["dev"])
    model = load_model(len(vocabulary))
    smoke = args.mode == "smoke"
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        do_train=True,
        do_eval=True,
        max_steps=200 if smoke else -1,
        num_train_epochs=1 if smoke else 3,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=1e-4 if smoke else 5e-5,
        warmup_ratio=0.1,
        bf16=True,
        bf16_full_eval=True,
        tf32=True,
        gradient_checkpointing=True,
        eval_strategy="steps" if smoke else "epoch",
        save_strategy="steps" if smoke else "epoch",
        eval_steps=50 if smoke else None,
        save_steps=50 if smoke else 500,
        logging_steps=10 if smoke else 100,
        logging_first_step=True,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="per",
        greater_is_better=False,
        dataloader_num_workers=args.num_workers,
        dataloader_persistent_workers=args.num_workers > 0,
        dataloader_prefetch_factor=2 if args.num_workers > 0 else None,
        eval_accumulation_steps=16,
        remove_unused_columns=False,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        skip_memory_metrics=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=datasets["train"],
        eval_dataset=datasets["dev"],
        data_collator=CtcCollator(processor),
        processing_class=processor.feature_extractor,
        compute_metrics=metric,
        preprocess_logits_for_metrics=argmax_logits,
    )
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    train_result = trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
    elapsed = time.monotonic() - started
    trainer.save_model(output_dir / "best_model")
    processor.save_pretrained(output_dir / "best_model")
    dev_result = trainer.evaluate(datasets["dev"], metric_key_prefix="dev")
    atomic_write_json(output_dir / "dev_evaluation.json", metric.last_details)
    atomic_write_json(
        output_dir / "dev_confusion_matrix.json",
        metric.last_details["confusion_matrix"],
    )
    first = datasets["dev"][0]
    batch = CtcCollator(processor)([first])
    device = trainer.model.device
    trainer.model.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        before = (
            trainer.model(
                input_values=batch["input_values"].to(device),
                attention_mask=batch["attention_mask"].to(device),
            )
            .logits.argmax(dim=-1)
            .cpu()
        )
    reloaded = AutoModelForCTC.from_pretrained(output_dir / "best_model").to(device)
    reloaded.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        after = (
            reloaded(
                input_values=batch["input_values"].to(device),
                attention_mask=batch["attention_mask"].to(device),
            )
            .logits.argmax(dim=-1)
            .cpu()
        )
    log_history = trainer.state.log_history
    losses = [item["loss"] for item in log_history if "loss" in item]
    report = {
        "mode": args.mode,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "record_counts": {key: len(value) for key, value in records.items()},
        "elapsed_seconds": elapsed,
        "peak_gpu_memory_bytes": torch.cuda.max_memory_allocated(),
        "first_logged_loss": losses[0] if losses else None,
        "final_logged_loss": losses[-1] if losses else None,
        "train_metrics": train_result.metrics,
        "dev_metrics": dev_result,
        "best_checkpoint": trainer.state.best_model_checkpoint,
        "checkpoint_reload_succeeded": True,
        "reload_argmax_identical": bool(torch.equal(before, after)),
        "nan_or_inf_loss": any(not math.isfinite(float(value)) for value in losses),
        "manual_audit_result": str(args.manual_audit_result.resolve()),
        "locked_test_accessed": False,
    }
    atomic_write_json(output_dir / "training_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
