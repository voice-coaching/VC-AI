"""Production visual releases are private, complete, and cross-bound."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from voice_coach.pronunciation_attempts.adapters.calibrated_runtime_decisions import (
    CalibratedAvSyncDecisionPolicy,
    CalibratedVideoQcDecisionPolicy,
)
from voice_coach.visual_feedback.adapters import production_visual_release as release
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes


def _payloads() -> dict[str, bytes]:
    return {
        "runtime_bundle": canonical_json_bytes({"kind": "bundle"}),
        "video_qc_decision_policy": canonical_json_bytes({"kind": "qc"}),
        "av_sync_decision_policy": canonical_json_bytes({"kind": "sync"}),
    }


def _write_release(root: Path, *, private: bool = True) -> None:
    root.mkdir(mode=0o700)
    payloads = _payloads()
    manifest = release.build_release_manifest(
        release_id="visual_release_v1", artifact_payloads=payloads
    )
    files = {release.MANIFEST_FILENAME: canonical_json_bytes(manifest)}
    files.update(
        {
            release.ARTIFACT_FILENAMES[name]: payload
            for name, payload in payloads.items()
        }
    )
    for filename, payload in files.items():
        path = root / filename
        path.write_bytes(payload)
        path.chmod(0o400 if private else 0o644)


def _write_sources(root: Path) -> dict[str, Path]:
    root.mkdir(mode=0o700)
    paths: dict[str, Path] = {}
    for name, payload in _payloads().items():
        path = root / f"{name}.source.json"
        path.write_bytes(payload)
        path.chmod(0o600)
        paths[name] = path
    return paths


def _patch_typed_loaders(
    monkeypatch: pytest.MonkeyPatch, *, expected_phones: tuple[str, ...] = ("ㅓ",)
) -> None:
    qc = CalibratedVideoQcDecisionPolicy(
        policy_revision="qc_v1",
        expected_measurement_policy_sha256="a" * 64,
        minimum_single_face_coverage=0.9,
        maximum_multiple_face_frame_fraction=0.0,
        minimum_mean_luma_8bit=20.0,
        maximum_mean_luma_8bit=230.0,
        maximum_underexposed_pixel_fraction=0.1,
        maximum_overexposed_pixel_fraction=0.1,
        minimum_lip_roi_mean_neighbor_luma_delta_8bit=1.0,
        minimum_track_transition_coverage=0.5,
        minimum_longest_single_face_run_fraction=0.8,
        maximum_lip_center_step_normalized=0.2,
    )
    sync = CalibratedAvSyncDecisionPolicy(
        policy_revision="sync_v1",
        expected_measurement_policy_sha256="b" * 64,
        maximum_absolute_offset_ms=120,
        minimum_correlation=0.8,
        minimum_anchor_count=1,
        minimum_anchor_strength=0.8,
    )
    bundle = SimpleNamespace(
        pipeline_identity=SimpleNamespace(
            video_qc_decision_policy_sha256=qc.policy_sha256,
            av_sync_decision_policy_sha256=sync.policy_sha256,
        ),
        claim_catalog=SimpleNamespace(
            claims=(SimpleNamespace(expected_phones=expected_phones),)
        ),
    )
    class _PolicyDocument:
        def __init__(self, value: object) -> None:
            self._value = value

        def to_runtime_policy(self) -> object:
            return self._value

    class _Artifact:
        def __init__(self, kind: str, value: object) -> None:
            self.policy_kind = kind
            self.policy_document = _PolicyDocument(value)

    class _ArtifactType:
        @staticmethod
        def from_json_object(value: object) -> _Artifact:
            assert isinstance(value, dict)
            if value["kind"] == "qc":
                return _Artifact("video_qc_decision", qc)
            return _Artifact("av_sync_decision", sync)

    monkeypatch.setattr(release, "load_visual_runtime_bundle", lambda _value: bundle)
    monkeypatch.setattr(release, "P8RuntimePolicyArtifact", _ArtifactType)


def test_loads_one_private_release_and_returns_typed_runtime_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    _write_release(root)
    _patch_typed_loaders(monkeypatch)

    loaded = release.load_production_visual_release(root)

    assert loaded.release_id == "visual_release_v1"
    assert loaded.status == "enabled"
    assert loaded.reason_codes == ()
    assert loaded.video_qc_decision_policy.policy_revision == "qc_v1"
    assert loaded.av_sync_decision_policy.policy_revision == "sync_v1"


def test_loads_a_release_shared_only_with_an_explicit_deployment_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    _write_release(root)
    root.chmod(0o750)
    for path in root.iterdir():
        path.chmod(0o440)
    _patch_typed_loaders(monkeypatch)

    loaded = release.load_production_visual_release(
        root, trusted_group_id=os.getgid()
    )

    assert loaded.status == "enabled"
    with pytest.raises(VisualFeedbackContractError, match="not private"):
        release.load_production_visual_release(root)


def test_creates_and_loads_disabled_production_release(tmp_path: Path) -> None:
    parent = tmp_path / "releases"
    parent.mkdir(mode=0o700)

    loaded = release.create_disabled_production_visual_release(
        output_directory=parent / "production_visual_v1",
        release_id="production_visual_v1",
        reason_codes=("production_evidence_unavailable",),
    )

    assert loaded.status == "disabled"
    assert loaded.reason_codes == ("production_evidence_unavailable",)
    assert loaded.runtime_bundle is None
    assert loaded.video_qc_decision_policy is None
    assert loaded.av_sync_decision_policy is None
    assert sorted(path.name for path in (parent / "production_visual_v1").iterdir()) == [
        "release.json"
    ]


def test_rejects_world_readable_release_before_model_loading(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _write_release(root, private=False)

    with pytest.raises(VisualFeedbackContractError, match="file identity"):
        release.load_production_visual_release(root)


def test_rejects_artifact_digest_drift(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _write_release(root)
    target = root / release.ARTIFACT_FILENAMES["runtime_bundle"]
    target.chmod(0o600)
    target.write_text(json.dumps({"kind": "changed"}), encoding="utf-8")
    target.chmod(0o400)

    with pytest.raises(VisualFeedbackContractError, match="artifact digest differs"):
        release.load_production_visual_release(root)


def test_rejects_symlinked_release_directory(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _write_release(root)
    alias = tmp_path / "release-alias"
    os.symlink(root, alias)

    with pytest.raises(VisualFeedbackContractError, match="not private"):
        release.load_production_visual_release(alias)


def test_rejects_release_directory_replaced_between_lstat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    replacement = tmp_path / "replacement"
    _write_release(root)
    _write_release(replacement)
    original_open = release.os.open
    swapped = False

    def replacing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if Path(path) == root and not swapped:
            swapped = True
            root.rename(tmp_path / "original")
            replacement.rename(root)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(release.os, "open", replacing_open)

    with pytest.raises(VisualFeedbackContractError, match="directory identity"):
        release.load_production_visual_release(root)


def test_packages_promoted_sources_atomically_as_private_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _write_sources(tmp_path / "sources")
    destination = tmp_path / "release"
    _patch_typed_loaders(monkeypatch)

    loaded = release.package_enabled_production_visual_release(
        output_directory=destination,
        release_id="visual_release_v1",
        artifact_sources=sources,
    )

    assert loaded.release_id == "visual_release_v1"
    assert destination.stat().st_mode & 0o777 == 0o700
    assert {
        path.name: path.stat().st_mode & 0o777 for path in destination.iterdir()
    } == {
        release.MANIFEST_FILENAME: 0o400,
        **{filename: 0o400 for filename in release.ARTIFACT_FILENAMES.values()},
    }
    assert not list(tmp_path.glob(".release.staging-*"))


def test_packager_does_not_replace_an_existing_destination(
    tmp_path: Path,
) -> None:
    sources = _write_sources(tmp_path / "sources")
    destination = tmp_path / "release"
    destination.mkdir(mode=0o700)

    with pytest.raises(VisualFeedbackContractError, match="already exists"):
        release.package_enabled_production_visual_release(
            output_directory=destination,
            release_id="visual_release_v1",
            artifact_sources=sources,
        )


def test_packager_rejects_world_readable_sources(tmp_path: Path) -> None:
    sources = _write_sources(tmp_path / "sources")
    sources["runtime_bundle"].chmod(0o644)

    with pytest.raises(VisualFeedbackContractError, match="source identity"):
        release.package_enabled_production_visual_release(
            output_directory=tmp_path / "release",
            release_id="visual_release_v1",
            artifact_sources=sources,
        )


def test_packager_requires_claim_coverage_for_the_deployment_phone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _write_sources(tmp_path / "sources")
    _patch_typed_loaders(monkeypatch, expected_phones=("ㅓ",))

    with pytest.raises(VisualFeedbackContractError, match="phone coverage"):
        release.package_enabled_production_visual_release(
            output_directory=tmp_path / "release",
            release_id="visual_release_v1",
            artifact_sources=sources,
            required_expected_phones=("ㅅ",),
        )

    assert not (tmp_path / "release").exists()
    assert not list(tmp_path.glob(".release.staging-*"))
