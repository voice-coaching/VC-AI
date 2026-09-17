#!/usr/bin/env python3
"""Validate repository-relative destinations in tracked Markdown files.

The checker intentionally validates file destinations only. Fragment/heading
validation is left out because GitHub's slug rules can change independently of
the repository and because duplicate headings need renderer-specific handling.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


_FENCE_PATTERN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_REFERENCE_PATTERN = re.compile(
    # GFM footnote definitions contain prose, not reference-link destinations.
    # Inline links in their body are still inspected by _inline_destinations.
    r"^\s{0,3}\[(?!\^)[^\]]+\]:\s*(?:<(?P<angled>[^>]+)>|(?P<plain>\S+))"
)
_REMOTE_SCHEMES = frozenset(
    {"data", "ftp", "ftps", "http", "https", "irc", "mailto", "ssh", "tel"}
)


@dataclass(frozen=True, slots=True)
class MarkdownDestination:
    """One link destination and its source line in a Markdown file."""

    value: str
    line_number: int


@dataclass(frozen=True, slots=True)
class BrokenMarkdownLink:
    """A tracked Markdown link whose local file destination does not exist."""

    source: Path
    line_number: int
    destination: str


def _strip_inline_code(line: str) -> str:
    """Replace inline code spans while preserving positions outside the spans."""

    result = list(line)
    cursor = 0
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue

        delimiter_end = cursor
        while delimiter_end < len(line) and line[delimiter_end] == "`":
            delimiter_end += 1
        delimiter = line[cursor:delimiter_end]
        closing = line.find(delimiter, delimiter_end)
        if closing < 0:
            break

        for index in range(cursor, closing + len(delimiter)):
            result[index] = " "
        cursor = closing + len(delimiter)

    return "".join(result)


def _destination_from_parenthesized_body(body: str) -> str | None:
    """Return the destination portion of a Markdown ``](...)`` body."""

    stripped = body.strip()
    if not stripped:
        return None
    if stripped.startswith("<"):
        closing = stripped.find(">", 1)
        return stripped[1:closing] if closing >= 0 else None

    destination: list[str] = []
    escaped = False
    for character in stripped:
        if escaped:
            destination.append(character)
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character.isspace():
            break
        destination.append(character)
    return "".join(destination) or None


def _inline_destinations(line: str, line_number: int) -> Iterator[MarkdownDestination]:
    """Yield inline link and image destinations from one non-fenced line."""

    searchable = _strip_inline_code(line)
    cursor = 0
    while True:
        opening = searchable.find("](", cursor)
        if opening < 0:
            return

        body_start = opening + 2
        depth = 1
        escaped = False
        body_end: int | None = None
        for index in range(body_start, len(searchable)):
            character = searchable[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    body_end = index
                    break

        if body_end is None:
            return

        destination = _destination_from_parenthesized_body(
            line[body_start:body_end]
        )
        if destination is not None:
            yield MarkdownDestination(destination, line_number)
        cursor = body_end + 1


def iter_markdown_destinations(markdown: str) -> Iterator[MarkdownDestination]:
    """Yield link destinations outside fenced and inline code blocks."""

    fence_character: str | None = None
    fence_length = 0
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        fence_match = _FENCE_PATTERN.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue

        reference_match = _REFERENCE_PATTERN.match(line)
        if reference_match is not None:
            destination = reference_match.group("angled") or reference_match.group(
                "plain"
            )
            yield MarkdownDestination(destination, line_number)

        yield from _inline_destinations(line, line_number)


def _relative_file_path(destination: str) -> str | None:
    """Map a relative Markdown destination to a decoded local file path."""

    if destination.startswith(("#", "//", "/")):
        return None

    parsed = urlsplit(destination)
    if parsed.scheme.lower() in _REMOTE_SCHEMES or parsed.netloc:
        return None
    if parsed.scheme:
        # Includes Windows drive paths and unknown URI schemes. Neither is a
        # portable repository-relative Markdown destination.
        return None

    decoded = unquote(parsed.path)
    return decoded or None


def find_broken_links(
    repository_root: Path,
    markdown_files: Iterable[Path],
    *,
    tracked_files: Collection[Path] | None = None,
) -> list[BrokenMarkdownLink]:
    """Return broken repository-relative links for the supplied Markdown files.

    When ``tracked_files`` is supplied, an existing but untracked local target
    is broken too: it would disappear in a clean clone. Directory links remain
    valid when at least one tracked file is contained below that directory.
    """

    resolved_root = repository_root.resolve()
    resolved_tracked = (
        frozenset(path.resolve() for path in tracked_files)
        if tracked_files is not None
        else None
    )
    broken: list[BrokenMarkdownLink] = []
    for markdown_file in markdown_files:
        source = markdown_file.resolve()
        content = source.read_text(encoding="utf-8")
        for destination in iter_markdown_destinations(content):
            relative_path = _relative_file_path(destination.value)
            if relative_path is None:
                continue

            target = (source.parent / relative_path).resolve()
            try:
                target.relative_to(resolved_root)
            except ValueError:
                target_exists = False
            else:
                if resolved_tracked is None:
                    target_exists = target.exists()
                else:
                    target_exists = target in resolved_tracked or any(
                        tracked.is_relative_to(target)
                        for tracked in resolved_tracked
                    )
            if not target_exists:
                broken.append(
                    BrokenMarkdownLink(
                        source=source.relative_to(resolved_root),
                        line_number=destination.line_number,
                        destination=destination.value,
                    )
                )

    return broken


def tracked_markdown_files(repository_root: Path) -> tuple[Path, ...]:
    """Return tracked Markdown files that exist in the current worktree."""

    completed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return tuple(
        path
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
        for path in (repository_root / Path(raw_path.decode("utf-8")),)
        if path.is_file()
    )


def tracked_repository_files(repository_root: Path) -> frozenset[Path]:
    """Return every Git-tracked file used to model a clean clone."""

    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repository_root,
        check=True,
        capture_output=True,
    )
    return frozenset(
        repository_root / Path(raw_path.decode("utf-8"))
        for raw_path in completed.stdout.split(b"\0")
        if raw_path
    )


def exclude_repository_prefixes(
    repository_root: Path,
    markdown_files: Iterable[Path],
    excluded_prefixes: Iterable[Path],
) -> tuple[Path, ...]:
    """Exclude explicit repository-relative archive prefixes from validation."""

    excluded = tuple(
        (repository_root / prefix).resolve() for prefix in excluded_prefixes
    )
    included: list[Path] = []
    for markdown_file in markdown_files:
        resolved_file = markdown_file.resolve()
        if any(
            resolved_file == prefix or resolved_file.is_relative_to(prefix)
            for prefix in excluded
        ):
            continue
        included.append(markdown_file)
    return tuple(included)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="check file destinations in tracked relative Markdown links"
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git worktree root (default: inferred from this script)",
    )
    parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=[],
        type=Path,
        help=(
            "explicit repository-relative archive prefix to skip; "
            "may be repeated"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tracked-Markdown link check and return a process exit code."""

    args = _build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    tracked_files = tracked_markdown_files(repository_root)
    repository_files = tracked_repository_files(repository_root)
    markdown_files = exclude_repository_prefixes(
        repository_root,
        tracked_files,
        args.exclude_prefix,
    )
    broken_links = find_broken_links(
        repository_root,
        markdown_files,
        tracked_files=repository_files,
    )

    if broken_links:
        for broken in broken_links:
            print(
                f"{broken.source}:{broken.line_number}: "
                f"missing relative destination {broken.destination!r}",
                file=sys.stderr,
            )
        print(
            f"broken Markdown links: {len(broken_links)}",
            file=sys.stderr,
        )
        return 1

    print(
        f"checked {len(markdown_files)} tracked Markdown files: OK "
        f"({len(tracked_files) - len(markdown_files)} explicitly excluded)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
