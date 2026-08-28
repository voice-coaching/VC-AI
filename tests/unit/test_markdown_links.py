"""Tests for the tracked Markdown file-link checker."""

from __future__ import annotations

from pathlib import Path

from scripts.check_markdown_links import (
    exclude_repository_prefixes,
    find_broken_links,
    iter_markdown_destinations,
)


def test_destination_parser_ignores_code_and_keeps_relative_links() -> None:
    destinations = list(
        iter_markdown_destinations(
            """
[inline](docs/current.md#section)
[query](docs/current.md?view=compact#section)
![image](assets/plot%20one.svg "plot")
[parentheses](docs/result_(final).md)
[reference]: <docs/reference.md>
`[code](docs/not-a-link.md)`
```text
[fenced](docs/not-a-link-either.md)
```
"""
        )
    )

    assert [destination.value for destination in destinations] == [
        "docs/current.md#section",
        "docs/current.md?view=compact#section",
        "assets/plot%20one.svg",
        "docs/result_(final).md",
        "docs/reference.md",
    ]


def test_find_broken_links_checks_only_portable_relative_files(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "current.md").write_text("current", encoding="utf-8")
    index = docs / "index.md"
    index.write_text(
        "\n".join(
            (
                "[current](current.md#heading)",
                "[current-query](current.md?view=compact#heading)",
                "[missing](deleted.md)",
                "[remote](https://example.invalid/deleted.md)",
                "[anchor](#local-heading)",
                "[site-root](/api/v1/health)",
            )
        ),
        encoding="utf-8",
    )

    broken = find_broken_links(tmp_path, (index,))

    assert [(item.line_number, item.destination) for item in broken] == [
        (3, "deleted.md")
    ]


def test_find_broken_links_rejects_destinations_outside_repository(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    index = repository / "index.md"
    index.write_text("[outside](../outside.md)", encoding="utf-8")

    broken = find_broken_links(repository, (index,))

    assert len(broken) == 1
    assert broken[0].destination == "../outside.md"


def test_find_broken_links_rejects_existing_but_untracked_target(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    index = docs / "index.md"
    tracked = docs / "tracked.md"
    untracked = docs / "local-only.pdf"
    index.write_text(
        "[tracked](tracked.md)\n[local-only](local-only.pdf)\n",
        encoding="utf-8",
    )
    tracked.write_text("tracked", encoding="utf-8")
    untracked.write_bytes(b"local-only")

    broken = find_broken_links(
        tmp_path,
        (index,),
        tracked_files=frozenset((index, tracked)),
    )

    assert [(item.line_number, item.destination) for item in broken] == [
        (2, "local-only.pdf")
    ]


def test_archive_exclusion_requires_an_explicit_repository_prefix(
    tmp_path: Path,
) -> None:
    current = tmp_path / "docs" / "current.md"
    archived = tmp_path / "docs" / "history" / "legacy" / "snapshot.md"
    current.parent.mkdir()
    archived.parent.mkdir(parents=True)
    current.write_text("current", encoding="utf-8")
    archived.write_text("snapshot", encoding="utf-8")

    included = exclude_repository_prefixes(
        tmp_path,
        (current, archived),
        (Path("docs/history/legacy"),),
    )

    assert included == (current,)
