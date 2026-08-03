#!/usr/bin/env python3
"""Render verified WavLM development metrics into manuscript-safe tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from voice_coach.reproducibility.wavlm_results import (
    ResultsValidationError,
    load_committed_wavlm_results,
    render_wavlm_results,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate completed Base+, Large, and committed S103 evidence, "
            "then transactionally commit aggregate-only Markdown and JSON."
        )
    )
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--base-registry", type=Path, required=True)
    parser.add_argument("--large-report", type=Path, required=True)
    parser.add_argument("--large-registry", type=Path, required=True)
    parser.add_argument("--comparison-commit", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-commit", type=Path, required=True)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = render_wavlm_results(
            base_report_path=args.base_report,
            base_registry_path=args.base_registry,
            large_report_path=args.large_report,
            large_registry_path=args.large_registry,
            comparison_commit_path=args.comparison_commit,
            output_markdown_path=args.output_markdown,
            output_json_path=args.output_json,
            output_commit_path=args.output_commit,
            workspace_root=args.workspace_root,
        )
    except ResultsValidationError as exc:
        print(f"validation failed: {exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("output publication failed", file=sys.stderr)
        return 2

    bootstrap = result["primary_comparison"]["paired_speaker_bootstrap"]
    gate = result["primary_comparison"]["gate"]
    resolved_output_commit = (
        args.output_commit
        if args.output_commit.is_absolute()
        else args.workspace_root / args.output_commit
    )
    _, _, commit = load_committed_wavlm_results(
        commit_manifest_path=resolved_output_commit,
        workspace_root=args.workspace_root,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "comparison_report_sha256": result["provenance"][
                    "comparison_report_sha256"
                ],
                "paired_estimate": bootstrap["estimate"],
                "confidence_lower": bootstrap["confidence_lower"],
                "confidence_upper": bootstrap["confidence_upper"],
                "gate_status": gate["status"],
                "output_commit": resolved_output_commit.resolve().as_posix(),
                "markdown": commit["outputs"]["markdown"]["path"],
                "json": commit["outputs"]["json"]["path"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
