#!/usr/bin/env python
"""Download and verify the one pinned HyperCLOVA X snapshot used locally.

The model is large, so the command is resumable through ``huggingface_hub``.
It never resolves a floating branch or tag.  ``--verify-only`` performs no
network call and is the post-install gate used by the runtime smoke test.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    REPOSITORY_ROOT
    / "configs/pronunciation_feedback/hyperclovax_seed_think_32b_v1.json"
)


def _read_config(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "modelId",
        "revision",
        "expectedRepositoryBytes",
        "expectedWeightShards",
        "localDirectory",
    }
    if not isinstance(document, dict) or not required <= set(document):
        raise ValueError("HyperCLOVA runtime config is incomplete")
    revision = document["revision"]
    if not isinstance(revision, str) or len(revision) != 40:
        raise ValueError("HyperCLOVA revision must be a full commit hash")
    return document


def _model_directory(document: dict[str, Any]) -> Path:
    path = REPOSITORY_ROOT / str(document["localDirectory"])
    if not path.resolve().is_relative_to(REPOSITORY_ROOT):
        raise ValueError("HyperCLOVA local directory leaves the repository")
    return path


def verify_snapshot(document: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    required = {
        "config.json",
        "generation_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "model.safetensors.index.json",
    }
    missing = sorted(name for name in required if not (model_dir / name).is_file())
    shards = sorted(model_dir.glob("model-*-of-*.safetensors"))
    expected_shards = int(document["expectedWeightShards"])
    if len(shards) != expected_shards:
        missing.append(f"weight_shards:{len(shards)}/{expected_shards}")
    if missing:
        raise RuntimeError("snapshot is incomplete: " + ", ".join(missing))
    repository_bytes = sum(
        path.stat().st_size
        for path in model_dir.iterdir()
        if path.is_file() and path.name != ".gitignore"
    )
    expected_bytes = int(document["expectedRepositoryBytes"])
    if repository_bytes != expected_bytes:
        raise RuntimeError(
            f"snapshot byte count differs: {repository_bytes}/{expected_bytes}"
        )
    return {
        "status": "verified",
        "modelId": document["modelId"],
        "revision": document["revision"],
        "directory": str(model_dir),
        "repositoryBytes": repository_bytes,
        "weightShards": len(shards),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--max-workers", type=int, default=4)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = _read_config(args.config)
        model_dir = _model_directory(document)
        if not args.verify_only:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=str(document["modelId"]),
                revision=str(document["revision"]),
                local_dir=model_dir,
                max_workers=max(1, args.max_workers),
            )
        result = verify_snapshot(document, model_dir)
    except Exception as error:  # noqa: BLE001 - CLI boundary reports a safe type
        json.dump(
            {"status": "failed", "reason": type(error).__name__, "detail": str(error)},
            sys.stderr,
            ensure_ascii=False,
        )
        sys.stderr.write("\n")
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
