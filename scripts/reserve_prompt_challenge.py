#!/usr/bin/env python3
"""Create a reproducible, privacy-safe prompt-disjoint reservation artifact.

Only ``prompt_sha256`` and ``split`` are projected from the source Parquet.
The artifact reserves prompts that occur in ``locked_test`` without reading
prompt text, identifiers, labels, predictions, or evaluation metrics.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

import pyarrow.parquet as pq


SCHEMA_VERSION = "voice_coach.prompt_challenge_reservation.v1"
DEFAULT_SEED = "voice-coach.aihub71490.prompt-challenge.v1"
DEFAULT_PROMPT_COUNT = 20
LOCKED_TEST_SPLIT = "locked_test"
PROJECTED_COLUMNS = ("prompt_sha256", "split")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reserve a deterministic prompt-disjoint challenge using only "
            "prompt_sha256 and split columns."
        )
    )
    parser.add_argument("--modeling-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt-count", type=int, default=DEFAULT_PROMPT_COUNT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--batch-size", type=int, default=65_536)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def selection_rank_sha256(seed: str, prompt_sha256: str) -> str:
    """Return the documented deterministic ranking digest for one prompt."""

    payload = seed.encode("utf-8") + b"\0" + prompt_sha256.encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def select_reserved_prompts(
    candidate_prompt_sha256: Iterable[str],
    *,
    seed: str,
    prompt_count: int,
) -> list[tuple[str, str]]:
    """Return ``(prompt_sha256, rank_sha256)`` pairs in fixed rank order."""

    if prompt_count <= 0:
        raise ValueError("prompt count must be positive")
    candidates = set(candidate_prompt_sha256)
    if len(candidates) < prompt_count:
        raise ValueError(
            "not enough unique locked_test prompt hashes: "
            f"need {prompt_count}, found {len(candidates)}"
        )
    ranked = sorted(
        (
            (selection_rank_sha256(seed, prompt_sha256), prompt_sha256)
            for prompt_sha256 in candidates
        ),
        key=lambda item: (item[0], item[1]),
    )
    return [
        (prompt_sha256, rank_sha256)
        for rank_sha256, prompt_sha256 in ranked[:prompt_count]
    ]


def scan_projected_columns(
    manifest_path: Path,
    *,
    batch_size: int,
) -> tuple[Counter[str], dict[str, Counter[str]]]:
    """Count source rows while projecting exactly the two allowed columns."""

    if batch_size <= 0:
        raise ValueError("batch size must be positive")
    parquet = pq.ParquetFile(manifest_path)
    missing = [
        name for name in PROJECTED_COLUMNS if name not in parquet.schema_arrow.names
    ]
    if missing:
        raise ValueError(
            "modeling manifest is missing required projected column(s): "
            + ", ".join(missing)
        )

    source_split_counts: Counter[str] = Counter()
    prompt_split_counts: dict[str, Counter[str]] = defaultdict(Counter)
    row_number = 0
    for batch in parquet.iter_batches(
        batch_size=batch_size,
        columns=list(PROJECTED_COLUMNS),
    ):
        prompt_hashes = batch.column(0).to_pylist()
        splits = batch.column(1).to_pylist()
        for prompt_sha256, split in zip(prompt_hashes, splits, strict=True):
            row_number += 1
            if not isinstance(prompt_sha256, str) or not SHA256_PATTERN.fullmatch(
                prompt_sha256
            ):
                raise ValueError(
                    f"malformed prompt_sha256 in projected row {row_number}"
                )
            if not isinstance(split, str) or not split:
                raise ValueError(f"invalid split in projected row {row_number}")
            source_split_counts[split] += 1
            prompt_split_counts[prompt_sha256][split] += 1
    return source_split_counts, dict(prompt_split_counts)


def build_artifact(
    manifest_path: Path,
    *,
    prompt_count: int = DEFAULT_PROMPT_COUNT,
    seed: str = DEFAULT_SEED,
    batch_size: int = 65_536,
) -> dict:
    source_split_counts, prompt_split_counts = scan_projected_columns(
        manifest_path,
        batch_size=batch_size,
    )
    candidates = {
        prompt_sha256
        for prompt_sha256, split_counts in prompt_split_counts.items()
        if split_counts.get(LOCKED_TEST_SPLIT, 0) > 0
    }
    selected = select_reserved_prompts(
        candidates,
        seed=seed,
        prompt_count=prompt_count,
    )
    selected_hashes = {prompt_sha256 for prompt_sha256, _ in selected}

    reserved_by_source_split: Counter[str] = Counter()
    reserved_prompts = []
    prompt_challenge_rows = 0
    excluded_rows = 0
    for prompt_sha256, rank_sha256 in selected:
        split_counts = prompt_split_counts[prompt_sha256]
        ordered_split_counts = dict(sorted(split_counts.items()))
        reserved_by_source_split.update(split_counts)
        challenge_count = split_counts.get(LOCKED_TEST_SPLIT, 0)
        excluded_count = sum(split_counts.values()) - challenge_count
        prompt_challenge_rows += challenge_count
        excluded_rows += excluded_count
        reserved_prompts.append(
            {
                "prompt_sha256": prompt_sha256,
                "selection_rank_sha256": rank_sha256,
                "source_split_counts": ordered_split_counts,
                "prompt_challenge_row_count": challenge_count,
                "exclude_from_training_and_tuning_row_count": excluded_count,
            }
        )

    total_rows = sum(source_split_counts.values())
    reserved_rows = prompt_challenge_rows + excluded_rows
    # This assertion guards against accidentally counting non-reserved prompts
    # while the artifact is assembled.
    if len(selected_hashes) != len(reserved_prompts):
        raise AssertionError("reserved prompt hashes must be unique")

    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "modeling_manifest_sha256": sha256_file(manifest_path),
            "row_count": total_rows,
            "projected_columns": list(PROJECTED_COLUMNS),
            "non_projected_value_columns_read": False,
        },
        "selection": {
            "candidate_rule": (
                "unique prompt_sha256 with at least one source split == "
                "'locked_test'"
            ),
            "ranking_algorithm": (
                "ascending SHA-256(UTF-8(seed) || 0x00 || "
                "ASCII(prompt_sha256)); prompt_sha256 ascending tie-break"
            ),
            "seed": seed,
            "requested_prompt_count": prompt_count,
            "selected_prompt_count": len(reserved_prompts),
            "locked_test_candidate_prompt_count": len(candidates),
        },
        "row_disposition_rules": [
            {
                "priority": 1,
                "when": {
                    "prompt_sha256": "is_reserved",
                    "source_split": LOCKED_TEST_SPLIT,
                },
                "disposition": "prompt_challenge",
                "eligible_for_training": False,
                "eligible_for_tuning": False,
            },
            {
                "priority": 2,
                "when": {
                    "prompt_sha256": "is_reserved",
                    "source_split": f"not_{LOCKED_TEST_SPLIT}",
                },
                "disposition": "exclude_from_training_and_tuning",
                "eligible_for_training": False,
                "eligible_for_tuning": False,
            },
            {
                "priority": 3,
                "when": {"prompt_sha256": "is_not_reserved"},
                "disposition": "preserve_source_split",
            },
        ],
        "counts": {
            "source_rows_by_split": dict(sorted(source_split_counts.items())),
            "reserved_rows_by_source_split": dict(
                sorted(reserved_by_source_split.items())
            ),
            "row_dispositions": {
                "prompt_challenge": prompt_challenge_rows,
                "exclude_from_training_and_tuning": excluded_rows,
                "preserve_source_split": total_rows - reserved_rows,
            },
        },
        "reserved_prompts": reserved_prompts,
    }


def write_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    artifact = build_artifact(
        args.modeling_manifest,
        prompt_count=args.prompt_count,
        seed=args.seed,
        batch_size=args.batch_size,
    )
    write_artifact(args.output, artifact)
    summary = {
        "schema_version": artifact["schema_version"],
        "source_manifest_sha256": artifact["source"][
            "modeling_manifest_sha256"
        ],
        "selected_prompt_count": artifact["selection"][
            "selected_prompt_count"
        ],
        "row_dispositions": artifact["counts"]["row_dispositions"],
        "output_sha256": sha256_file(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
