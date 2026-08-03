#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from voice_coach.reproducibility.evidence import (
    EvidenceValidationError,
    validate_evidence_map,
    write_json_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate manuscript claims against hashed aggregate artifacts."
    )
    parser.add_argument(
        "--evidence-map",
        default="docs/paper/evidence-map.yaml",
        help="workspace-relative YAML or JSON evidence map",
    )
    parser.add_argument(
        "--workspace-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="workspace root (defaults to the repository root)",
    )
    parser.add_argument(
        "--output",
        default="artifacts/reproducibility/manuscript_evidence_validation.json",
        help="workspace-relative privacy-safe JSON report",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.workspace_root).resolve()
    map_path = Path(args.evidence_map)
    if not map_path.is_absolute():
        map_path = root / map_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path = output_path.resolve()
    try:
        output_relative = output_path.relative_to(root)
    except ValueError:
        print("configuration error: output must be inside workspace", file=sys.stderr)
        return 2
    lowered_parts = [part.lower() for part in output_relative.parts]
    if any(
        part in {"raw", "restricted", ".env"} or part.startswith(".env")
        for part in lowered_parts
    ):
        print(
            "configuration error: output path is forbidden by privacy policy",
            file=sys.stderr,
        )
        return 2
    try:
        report = validate_evidence_map(map_path, workspace_root=root)
        write_json_atomic(output_path, report)
    except EvidenceValidationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    print(f"status={report['status']} report={output_path}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
