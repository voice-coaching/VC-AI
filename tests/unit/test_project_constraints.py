"""Tests for the consolidated repository constraint checker."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from scripts.check_project_constraints import (
    find_document_reference_issues,
    find_removed_document_issues,
    find_protected_tracking_issues,
)


def test_removed_evidence_document_is_rejected_but_seungun_is_excluded(
    tmp_path: Path,
) -> None:
    retired = PurePosixPath("docs/paper/result.md")
    excluded = PurePosixPath("seungun/docs/result.md")

    issues = find_removed_document_issues(tmp_path, (retired, excluded))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("removed_document_present", retired)
    ]


def test_surviving_document_cannot_reference_retired_document(tmp_path: Path) -> None:
    document = PurePosixPath("docs/api/index.md")
    path = tmp_path / Path(document)
    path.parent.mkdir(parents=True)
    path.write_text("See docs/evaluation/old-result.md\n", encoding="utf-8")

    issues = find_document_reference_issues(tmp_path, (document,))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("retired_document_reference", document)
    ]


def test_tracked_restricted_media_is_rejected(tmp_path: Path) -> None:
    protected = tmp_path / "data" / "restricted" / "participant.wav"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"not real audio")

    issues = find_protected_tracking_issues(tmp_path, frozenset({protected}))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("protected_path_tracked", PurePosixPath("data/restricted/participant.wav"))
    ]
