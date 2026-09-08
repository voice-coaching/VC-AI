#!/usr/bin/env python3
"""Validate privacy-safe P6 or P8 visual-gate evidence JSON.

The command accepts summary documents only.  It does not download or inspect
raw media and never emits input paths or source identifiers.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from voice_coach.visual_feedback.errors import (  # noqa: E402
    VisualFeedbackContractError,
)
from voice_coach.visual_feedback.gate_evidence import (  # noqa: E402
    validate_p6_evidence_bundle,
    validate_p8_claim_conformance_bundle,
    visual_gate_schema_summary,
)
from voice_coach.visual_feedback.models.integrity import (  # noqa: E402
    canonical_json_bytes,
)

_MAX_SUMMARY_BYTES = 8 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a privacy-safe visual P6/P8 evidence summary",
    )
    subparsers = parser.add_subparsers(dest="gate", required=True)
    for gate in ("p6", "p8"):
        gate_parser = subparsers.add_parser(gate)
        source = gate_parser.add_mutually_exclusive_group(required=True)
        source.add_argument(
            "--bundle",
            type=Path,
            help=(
                "privacy-safe JSON bundle; its locator is visible in argv, so use "
                "--bundle-fd when the filename is sensitive"
            ),
        )
        source.add_argument(
            "--bundle-fd",
            type=_summary_file_descriptor,
            help="already-open read-only regular-file descriptor >= 3 (pathless)",
        )
    schema_parser = subparsers.add_parser(
        "schema",
        help="print schema identifiers, required fields, and non-promotion semantics",
    )
    schema_parser.add_argument("schema_gate", choices=("p6", "p8"))
    return parser


def _summary_file_descriptor(value: str) -> int:
    try:
        descriptor = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("bundle fd must be an integer >= 3") from None
    if descriptor < 3:
        raise argparse.ArgumentTypeError("bundle fd must be an integer >= 3")
    return descriptor


def _read_json_document(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return _read_json_descriptor(descriptor)


def _read_json_fd(descriptor: int) -> object:
    try:
        duplicate = os.dup(descriptor)
    except OSError as error:
        raise VisualFeedbackContractError("evidence bundle fd is unavailable") from error
    return _read_json_descriptor(duplicate)


def _read_json_descriptor(descriptor: int) -> object:
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise VisualFeedbackContractError("evidence bundle must be a regular file")
        access_mode = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
        if access_mode != os.O_RDONLY:
            raise VisualFeedbackContractError("evidence bundle fd must be read-only")
        if metadata.st_size <= 0 or metadata.st_size > _MAX_SUMMARY_BYTES:
            raise VisualFeedbackContractError("evidence bundle size is outside policy")
        payload = bytearray()
        while len(payload) <= _MAX_SUMMARY_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, _MAX_SUMMARY_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_SUMMARY_BYTES:
            raise VisualFeedbackContractError("evidence bundle exceeds size policy")
    finally:
        os.close(descriptor)
    try:
        return json.loads(bytes(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisualFeedbackContractError("evidence bundle is not valid UTF-8 JSON") from error


def _emit(value: object, *, stream) -> None:  # noqa: ANN001
    stream.write(canonical_json_bytes(value).decode("utf-8"))
    stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.gate == "schema":
        _emit(visual_gate_schema_summary(arguments.schema_gate), stream=sys.stdout)
        return 0
    try:
        document = (
            _read_json_document(arguments.bundle)
            if arguments.bundle is not None
            else _read_json_fd(arguments.bundle_fd)
        )
        receipt = (
            validate_p6_evidence_bundle(document)
            if arguments.gate == "p6"
            else validate_p8_claim_conformance_bundle(document)
        )
    except (OSError, VisualFeedbackContractError):
        _emit(
            {
                "status": "failed_closed",
                "reason_codes": ["visual_gate_evidence_validation_failed"],
            },
            stream=sys.stderr,
        )
        return 2
    _emit(receipt.to_json_object(), stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
