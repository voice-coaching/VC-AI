"""Production loader accepts only private, stable P6 artifact files."""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_coach.visual_feedback.adapters.p6_geometry_reload import (
    P6GeometryReloadError,
    load_p6_geometry_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "artifacts/visual_feedback/p6/aihub68_geometry_artifact_v2.json"


def test_loads_and_fresh_process_reproduces_private_geometry_artifact(
    tmp_path: Path,
) -> None:
    target = tmp_path / "geometry.json"
    target.write_bytes(SOURCE.read_bytes())
    target.chmod(0o400)

    artifact = load_p6_geometry_artifact(target, repository_root=ROOT)

    assert artifact.artifact_id == "aihub68_mediapipe_24point_geometry_v2"
    assert artifact.to_json_object()["contains_pronunciation_truth"] is False
    assert artifact.to_json_object()["contains_action_truth"] is False


def test_rejects_group_writable_geometry_artifact(tmp_path: Path) -> None:
    target = tmp_path / "geometry.json"
    target.write_bytes(SOURCE.read_bytes())
    target.chmod(0o620)

    with pytest.raises(
        P6GeometryReloadError,
        match="p6_artifact_file_not_private",
    ):
        load_p6_geometry_artifact(target, repository_root=ROOT)
