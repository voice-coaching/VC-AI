#!/usr/bin/env python3
"""Run or read-only validate the commit-gated WP4 S104 adapter experiment.

The immutable CPU preflight finishes before this process imports torch,
initializes CUDA, or imports Transformers. No dotenv/environment file is read.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


CUBLAS_WORKSPACE_CONFIG = ":4096:8"
_configured_workspace = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
if _configured_workspace not in (None, CUBLAS_WORKSPACE_CONFIG):
    raise RuntimeError("deterministic cuBLAS workspace configuration differs")
os.environ["CUBLAS_WORKSPACE_CONFIG"] = CUBLAS_WORKSPACE_CONFIG
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
_TORCH_IMPORTED_BEFORE_PREFLIGHT = "torch" in sys.modules

from voice_coach.training.wavlm_adapter_contract import (
    load_config,
    prepare_s104_execution,
    sha256_file,
)


SCRIPT_PATH = Path(__file__).resolve()
REPOSITORY_ROOT = SCRIPT_PATH.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute three-seed, development-only online WavLM adapter "
            "training after the immutable S103 branch preflight."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/experiments/wavlm_s104_adapter_v1.yaml"),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument(
        "--validate-commit-only",
        action="store_true",
        help="Read-only validate the completed commit/registry/artifact hash chain.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        workspace_root = args.workspace_root.resolve(strict=True)
        config_path = (
            args.config if args.config.is_absolute() else workspace_root / args.config
        ).resolve(strict=True)
        if args.validate_commit_only:
            from voice_coach.training.wavlm_adapter_training import (
                validate_s104_commit,
            )

            validation = validate_s104_commit(
                workspace_root=workspace_root,
                config_path=config_path,
            )
            print(
                json.dumps(
                    validation,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        # This call is CPU-only and must complete before the heavy import below.
        execution_lock, execution_lock_path = prepare_s104_execution(
            workspace_root=workspace_root,
            config_path=config_path,
        )
        if _TORCH_IMPORTED_BEFORE_PREFLIGHT or "torch" in sys.modules:
            raise RuntimeError("torch was imported before S104 preflight completed")
        config = load_config(config_path)
        from voice_coach.training.wavlm_adapter_training import (
            run_online_training,
        )

        commit = run_online_training(
            workspace_root=workspace_root,
            config_path=config_path,
            config=config,
            execution_lock_path=execution_lock_path,
            execution_lock=execution_lock,
            argv=sys.argv,
        )
        commit_path = (
            workspace_root / str(config["outputs"]["commit_manifest"])
        ).resolve(strict=True)
    except Exception as error:
        # Never print exception text: it can contain a raw locator or identifier.
        print(
            "S104 training failed: "
            f"{type(error).__module__}.{type(error).__qualname__}",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": commit["status"],
                "generation_id": commit["generation_id"],
                "artifact_count": len(commit["artifacts"]),
                "commit_manifest": commit_path.relative_to(workspace_root).as_posix(),
                "commit_sha256": sha256_file(commit_path),
                "development_only": True,
                "locked_test_accessed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
