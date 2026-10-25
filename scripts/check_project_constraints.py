#!/usr/bin/env python3
"""Enforce the small set of repository-wide constraints that can be automated.

This checker intentionally covers repository hygiene only. Model, dataset,
calibration, evaluation, and human-review gates remain owned by their runtime
contracts and cannot be inferred from static files.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
import sys

if __package__:
    from scripts.check_markdown_links import (
        exclude_repository_prefixes,
        find_broken_links,
        tracked_markdown_files,
        tracked_repository_files,
    )
else:
    from check_markdown_links import (  # type: ignore[no-redef]
        exclude_repository_prefixes,
        find_broken_links,
        tracked_markdown_files,
        tracked_repository_files,
    )


REMOVED_DOCUMENT_PREFIXES = (
    PurePosixPath(".codex/ref_docs"),
    PurePosixPath("artifacts/public"),
    PurePosixPath("docs/architecture"),
    PurePosixPath("docs/audit"),
    PurePosixPath("docs/evaluation"),
    PurePosixPath("docs/history"),
    PurePosixPath("docs/paper"),
)
REMOVED_DOCUMENT_PATHS = frozenset(
    {
        PurePosixPath(".codex/hooks.json"),
        PurePosixPath(".codex/prompt_refiner_context.md"),
        PurePosixPath("data/README.md"),
        PurePosixPath("docs/PROJECT_AUDIT_AND_VERIFICATION_2026-08-28_KO.md"),
        PurePosixPath("docs/WORK_LOG_AND_STATUS_2026-08-30_KO.md"),
        PurePosixPath("requirements/README.md"),
    }
)
REMOVED_REFERENCE_MARKERS = (
    "artifacts/public/README.md",
    "docs/audit/",
    "docs/evaluation/",
    "docs/history/",
    "docs/paper/",
    "PROJECT_AUDIT_AND_VERIFICATION_2026-08-28_KO.md",
    "WORK_LOG_AND_STATUS_2026-08-30_KO.md",
)
DOCUMENT_SUFFIXES = frozenset({".md", ".rst", ".txt"})
PROTECTED_TRACKED_PREFIXES = (
    PurePosixPath("artifacts/restricted"),
    PurePosixPath("checkpoints"),
    PurePosixPath("data/cache"),
    PurePosixPath("data/models"),
    PurePosixPath("data/processed"),
    PurePosixPath("data/raw"),
    PurePosixPath("data/restricted"),
    PurePosixPath("models"),
)
PROTECTED_TRACKED_SUFFIXES = frozenset(
    {
        ".ckpt",
        ".flac",
        ".m4a",
        ".mov",
        ".mp3",
        ".mp4",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
        ".wav",
        ".webm",
    }
)
EXCLUDED_SCOPE = PurePosixPath("seungun")


@dataclass(frozen=True, slots=True)
class ConstraintIssue:
    """One actionable repository-hygiene violation."""

    code: str
    path: PurePosixPath
    detail: str


def _is_below(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or prefix in path.parents


def _current_git_files(repository_root: Path) -> tuple[PurePosixPath, ...]:
    """Return tracked or untracked, non-ignored files that exist now."""

    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    paths: list[PurePosixPath] = []
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = PurePosixPath(raw_path.decode("utf-8"))
        if (repository_root / Path(relative)).is_file():
            paths.append(relative)
    return tuple(sorted(set(paths)))


def find_removed_document_issues(
    repository_root: Path,
    current_files: Iterable[PurePosixPath],
) -> list[ConstraintIssue]:
    """Reject files under retired evidence and rule-document locations."""

    issues: list[ConstraintIssue] = []
    for relative in current_files:
        if _is_below(relative, EXCLUDED_SCOPE):
            continue
        if relative in REMOVED_DOCUMENT_PATHS or any(
            _is_below(relative, prefix) for prefix in REMOVED_DOCUMENT_PREFIXES
        ):
            issues.append(
                ConstraintIssue(
                    code="removed_document_present",
                    path=relative,
                    detail="retired evidence/rule document location is present",
                )
            )
    return issues


def find_document_reference_issues(
    repository_root: Path,
    current_files: Iterable[PurePosixPath],
) -> list[ConstraintIssue]:
    """Reject surviving text documents that point at retired documents."""

    issues: list[ConstraintIssue] = []
    for relative in current_files:
        if _is_below(relative, EXCLUDED_SCOPE):
            continue
        path = repository_root / Path(relative)
        if path.suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(
                ConstraintIssue(
                    code="document_not_utf8",
                    path=relative,
                    detail="text document is not valid UTF-8",
                )
            )
            continue
        marker = next(
            (candidate for candidate in REMOVED_REFERENCE_MARKERS if candidate in text),
            None,
        )
        if marker is not None:
            issues.append(
                ConstraintIssue(
                    code="retired_document_reference",
                    path=relative,
                    detail=f"contains retired document reference {marker!r}",
                )
            )
    return issues


def find_protected_tracking_issues(
    repository_root: Path,
    tracked_files: Collection[Path],
) -> list[ConstraintIssue]:
    """Reject tracked secrets, restricted data, local media, and model weights."""

    issues: list[ConstraintIssue] = []
    for absolute in sorted(tracked_files):
        if not absolute.is_file():
            continue
        relative = PurePosixPath(absolute.relative_to(repository_root).as_posix())
        if _is_below(relative, EXCLUDED_SCOPE):
            continue
        lowered = relative.as_posix().casefold()
        if lowered == ".env.local" or any(
            _is_below(relative, prefix) for prefix in PROTECTED_TRACKED_PREFIXES
        ):
            issues.append(
                ConstraintIssue(
                    code="protected_path_tracked",
                    path=relative,
                    detail="secret or restricted-data path is tracked",
                )
            )
        elif relative.suffix.casefold() in PROTECTED_TRACKED_SUFFIXES:
            issues.append(
                ConstraintIssue(
                    code="protected_file_tracked",
                    path=relative,
                    detail="local media, checkpoint, or model file is tracked",
                )
            )
    return issues


def find_markdown_link_issues(repository_root: Path) -> list[ConstraintIssue]:
    """Validate current tracked Markdown links outside the excluded subtree."""

    tracked_files = tracked_repository_files(repository_root)
    markdown_files = tuple(
        path for path in tracked_markdown_files(repository_root) if path.is_file()
    )
    included = exclude_repository_prefixes(
        repository_root,
        markdown_files,
        (Path(EXCLUDED_SCOPE),),
    )
    return [
        ConstraintIssue(
            code="broken_markdown_link",
            path=PurePosixPath(broken.source.as_posix()),
            detail=(
                f"line {broken.line_number} has missing destination "
                f"{broken.destination!r}"
            ),
        )
        for broken in find_broken_links(
            repository_root,
            included,
            tracked_files=tracked_files,
        )
    ]


def check_repository(repository_root: Path) -> tuple[ConstraintIssue, ...]:
    """Run every automated repository-level constraint."""

    root = repository_root.resolve()
    current_files = _current_git_files(root)
    tracked_files = tracked_repository_files(root)
    issues = [
        *find_removed_document_issues(root, current_files),
        *find_document_reference_issues(root, current_files),
        *find_protected_tracking_issues(root, tracked_files),
        *find_markdown_link_issues(root),
    ]
    return tuple(sorted(issues, key=lambda item: (item.path.as_posix(), item.code)))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check executable repository hygiene constraints"
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git worktree root (default: inferred from this script)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print violations and return nonzero when a constraint fails."""

    args = _build_parser().parse_args(argv)
    issues = check_repository(args.repository_root)
    for issue in issues:
        print(
            f"{issue.path}: {issue.code}: {issue.detail}",
            file=sys.stderr,
        )
    if issues:
        print(f"project constraint failures: {len(issues)}", file=sys.stderr)
        return 1
    print("project constraints passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
