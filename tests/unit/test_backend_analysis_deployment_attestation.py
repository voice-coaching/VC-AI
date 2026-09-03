from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from voice_coach.backend_analysis.deployment_attestation import (
    DeploymentArtifactPaths,
    DeploymentAttestationError,
    create_deployment_attestation,
    prepare_artifact_group_access,
    verify_deployment_attestation,
    write_deployment_attestation,
)


_RELEASE_ID = "a" * 40
_KEY_ID = "analysis-key-2026-09"
_POLICY = "voice-analysis-consent-v1"


def test_verifies_one_release_key_policy_device_and_artifact_set(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    manifest_path = tmp_path / "deployment.json"
    write_deployment_attestation(
        manifest_path,
        create_deployment_attestation(
            release_id=_RELEASE_ID,
            authorization_key_id=_KEY_ID,
            consent_policy_revision=_POLICY,
            worker_device="cuda",
            artifact_group_id=os.getgid(),
            artifacts=artifacts,
        ),
        artifact_group_id=os.getgid(),
    )

    verify_deployment_attestation(
        _environment(tmp_path, manifest_path),
        authorization_key_ids={_KEY_ID, "retiring-key"},
    )

    assert manifest_path.stat().st_mode & 0o777 == 0o640


def test_rejects_a_model_changed_after_approval(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    manifest_path = tmp_path / "deployment.json"
    write_deployment_attestation(
        manifest_path,
        create_deployment_attestation(
            release_id=_RELEASE_ID,
            authorization_key_id=_KEY_ID,
            consent_policy_revision=_POLICY,
            worker_device="cuda",
            artifact_group_id=os.getgid(),
            artifacts=artifacts,
        ),
        artifact_group_id=os.getgid(),
    )
    (artifacts.seungun_model / "weights.bin").write_bytes(b"changed")

    with pytest.raises(
        DeploymentAttestationError,
        match="analysis_worker_deployment_attestation_invalid",
    ):
        verify_deployment_attestation(
            _environment(tmp_path, manifest_path), authorization_key_ids={_KEY_ID}
        )


@pytest.mark.parametrize(
    ("override", "allowed_keys"),
    (
        ({"AI_ANALYSIS_RELEASE_ID": "b" * 40}, {_KEY_ID}),
        ({"VOICE_COACH_BUILD_REVISION": "b" * 40}, {_KEY_ID}),
        ({"AI_ANALYSIS_ACTIVE_AUTHORIZATION_KEY_ID": "other-key"}, {_KEY_ID}),
        ({"AI_ANALYSIS_CONSENT_POLICY_REVISION": "voice-analysis-consent-v2"}, {_KEY_ID}),
        ({"AI_ANALYSIS_SEUNGUN_DEVICE": "cpu"}, {_KEY_ID}),
        ({}, {"retiring-key"}),
    ),
)
def test_rejects_a_deployment_binding_mismatch(
    tmp_path: Path,
    override: dict[str, str],
    allowed_keys: set[str],
) -> None:
    artifacts = _artifacts(tmp_path)
    manifest_path = tmp_path / "deployment.json"
    write_deployment_attestation(
        manifest_path,
        create_deployment_attestation(
            release_id=_RELEASE_ID,
            authorization_key_id=_KEY_ID,
            consent_policy_revision=_POLICY,
            worker_device="cuda",
            artifact_group_id=os.getgid(),
            artifacts=artifacts,
        ),
        artifact_group_id=os.getgid(),
    )
    environment = {**_environment(tmp_path, manifest_path), **override}

    with pytest.raises(DeploymentAttestationError):
        verify_deployment_attestation(
            environment, authorization_key_ids=allowed_keys
        )


def test_rejects_duplicate_manifest_keys_and_symlinked_artifacts(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    target = artifacts.nltk_data
    linked = tmp_path / "linked-nltk"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(DeploymentAttestationError):
        create_deployment_attestation(
            release_id=_RELEASE_ID,
            authorization_key_id=_KEY_ID,
            consent_policy_revision=_POLICY,
            worker_device="cuda",
            artifact_group_id=os.getgid(),
            artifacts=DeploymentArtifactPaths(
                detector_bundle=artifacts.detector_bundle,
                seungun_model=artifacts.seungun_model,
                seungun_processor=artifacts.seungun_processor,
                nltk_data=linked,
                visual_release=artifacts.visual_release,
                visual_landmark_model=artifacts.visual_landmark_model,
            ),
        )

    manifest_path = tmp_path / "duplicate.json"
    manifest_path.write_text(
        '{"schemaVersion":"first","schemaVersion":"second"}', encoding="ascii"
    )
    manifest_path.chmod(0o640)
    with pytest.raises(DeploymentAttestationError):
        verify_deployment_attestation(
            _environment(tmp_path, manifest_path), authorization_key_ids={_KEY_ID}
        )


def test_refuses_to_replace_an_existing_attestation(tmp_path: Path) -> None:
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"existing": True}), encoding="ascii")

    with pytest.raises(DeploymentAttestationError):
        write_deployment_attestation(
            path, {"replacement": True}, artifact_group_id=os.getgid()
        )

    assert json.loads(path.read_text(encoding="ascii")) == {"existing": True}


def test_refuses_a_group_writable_output_directory(tmp_path: Path) -> None:
    output_directory = tmp_path / "shared"
    output_directory.mkdir(mode=0o770)
    output_directory.chmod(0o770)
    path = output_directory / "deployment.json"

    with pytest.raises(DeploymentAttestationError):
        write_deployment_attestation(
            path, {"schemaVersion": "test"}, artifact_group_id=os.getgid()
        )

    assert not path.exists()


def test_prepares_private_group_access_without_world_permissions(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir(mode=0o755)
    payload = root / "payload.bin"
    payload.write_bytes(b"payload")
    payload.chmod(0o600)

    with pytest.raises(DeploymentAttestationError):
        prepare_artifact_group_access(
            [root], artifact_group_id=os.getgid(), apply=False
        )

    prepare_artifact_group_access([root], artifact_group_id=os.getgid(), apply=True)
    prepare_artifact_group_access([root], artifact_group_id=os.getgid(), apply=False)

    assert root.stat().st_mode & 0o777 == 0o750
    assert payload.stat().st_mode & 0o777 == 0o640


def _artifacts(root: Path) -> DeploymentArtifactPaths:
    paths = {
        "detector": root
        / "seungun/artifacts/error_detector/final_pronunciation_detector_v1",
        "model": root / "seungun/checkpoints/xlsr300m_full_internal_v1/best_model",
        "processor": root / "seungun/processor",
        "nltk": root / "nltk",
        "visual": root / "visual",
    }
    for name, directory in paths.items():
        directory.mkdir(parents=True)
        directory.chmod(0o750)
        (directory / "payload.bin").write_bytes(name.encode("ascii"))
        (directory / "payload.bin").chmod(0o640)
    (paths["model"] / "weights.bin").write_bytes(b"model-weights")
    (paths["model"] / "weights.bin").chmod(0o640)
    landmark = root / "landmark.task"
    landmark.write_bytes(b"landmark")
    landmark.chmod(0o640)
    return DeploymentArtifactPaths(
        detector_bundle=paths["detector"],
        seungun_model=paths["model"],
        seungun_processor=paths["processor"],
        nltk_data=paths["nltk"],
        visual_release=paths["visual"],
        visual_landmark_model=landmark,
    )


def _environment(root: Path, manifest_path: Path) -> dict[str, str]:
    return {
        "VOICE_COACH_REPOSITORY_ROOT": str(root),
        "AI_ANALYSIS_NLTK_DATA_ROOT": str(root / "nltk"),
        "AI_ANALYSIS_VISUAL_RELEASE_ROOT": str(root / "visual"),
        "AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH": str(root / "landmark.task"),
        "AI_ANALYSIS_DEPLOYMENT_MANIFEST_PATH": str(manifest_path),
        "AI_ANALYSIS_RELEASE_ID": _RELEASE_ID,
        "VOICE_COACH_BUILD_REVISION": _RELEASE_ID,
        "AI_ANALYSIS_ACTIVE_AUTHORIZATION_KEY_ID": _KEY_ID,
        "AI_ANALYSIS_CONSENT_POLICY_REVISION": _POLICY,
        "AI_ANALYSIS_SEUNGUN_DEVICE": "cuda",
        "AI_ANALYSIS_ARTIFACT_GID": str(os.getgid()),
    }
