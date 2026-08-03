#!/usr/bin/env python3
"""Benchmark frozen WavLM inference on synthetic 16 kHz audio."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import torch
from transformers import WavLMModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default="microsoft/wavlm-base-plus")
    parser.add_argument("--lengths", nargs="+", type=int, default=[8, 12, 20])
    parser.add_argument("--warmup-runs", type=int, default=2)
    parser.add_argument("--measured-runs", type=int, default=10)
    parser.add_argument("--precision", choices=["bf16", "fp32"], default="bf16")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def benchmark_length(
    model: WavLMModel,
    seconds: int,
    warmup_runs: int,
    measured_runs: int,
    precision: str,
) -> dict[str, Any]:
    waveform = torch.randn(1, seconds * 16_000, device="cuda")
    durations: list[float] = []
    oom_count = 0

    torch.cuda.reset_peak_memory_stats()
    context = torch.autocast("cuda", dtype=torch.bfloat16, enabled=precision == "bf16")
    with torch.inference_mode(), context:
        for _ in range(warmup_runs):
            model(waveform).last_hidden_state
            torch.cuda.synchronize()

        for _ in range(measured_runs):
            started = time.perf_counter()
            try:
                output = model(waveform).last_hidden_state
                torch.cuda.synchronize()
            except torch.OutOfMemoryError:
                oom_count += 1
                torch.cuda.empty_cache()
                continue
            durations.append(time.perf_counter() - started)
            if not torch.isfinite(output).all():
                raise RuntimeError(f"non-finite output for {seconds}s input")

    if not durations:
        return {"seconds": seconds, "oom_count": oom_count, "status": "failed"}

    memory = psutil.virtual_memory()
    return {
        "seconds": seconds,
        "runs": len(durations),
        "oom_count": oom_count,
        "latency_mean_sec": statistics.fmean(durations),
        "latency_p95_sec": percentile(durations, 0.95),
        "rtf_p95": percentile(durations, 0.95) / seconds,
        "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(),
        "system_available_memory_bytes": memory.available,
        "status": "passed" if oom_count == 0 else "failed",
    }


def main() -> int:
    args = parse_args()
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available")
    if args.precision == "bf16" and not torch.cuda.is_bf16_supported():
        raise SystemExit("BF16 is not supported")

    model = WavLMModel.from_pretrained(args.model_id).eval().to("cuda")
    for parameter in model.parameters():
        parameter.requires_grad_(False)

    result = {
        "captured_at": datetime.now().astimezone().isoformat(),
        "model_id": args.model_id,
        "model_revision": getattr(model.config, "_commit_hash", None),
        "precision": args.precision,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "python": platform.python_version(),
        "gpu": torch.cuda.get_device_name(0),
        "compute_capability": ".".join(map(str, torch.cuda.get_device_capability(0))),
        "results": [
            benchmark_length(
                model,
                seconds,
                args.warmup_runs,
                args.measured_runs,
                args.precision,
            )
            for seconds in args.lengths
        ],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if all(item["status"] == "passed" for item in result["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
