"""P6 promotion contracts, using synthetic decision evidence only in tmp dirs."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from voice_coach.visual_feedback.models.runtime_eligibility import (
    AutomatedP6PromotionReceipt,
    VisualRuntimePipelineIdentity,
)
from voice_coach.visual_feedback.models.integrity import (
    canonical_json_bytes,
    sha256_document,
)
from voice_coach.visual_feedback.models.runtime_policy_artifact import (
    AvSyncDecisionSemanticPolicy,
    P8RuntimePolicyArtifact,
    RuntimeDecisionPolicyEvidence,
    VideoQcDecisionSemanticPolicy,
    VideoQcSemanticPolicy,
    AvSyncSemanticPolicy,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_visual_runtime_p6_promotion.py"


def test_production_defaults_consume_the_multishard_calibration_bundle() -> None:
    spec = importlib.util.spec_from_file_location("p6_promotion_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = module._parser().parse_args([])
    prefix = "aihub538_ts174_tl174_ts175_tl175_ts176_tl176_"
    assert arguments.video_qc_decision_policy.name == (
        prefix + "video_qc_decision_policy_v1.json"
    )
    assert arguments.av_sync_decision_policy.name == (
        prefix + "av_sync_decision_policy_v1.json"
    )
    assert arguments.decision_policy_evidence.name == (
        prefix + "decision_policy_evidence_v2.json"
    )


def test_runtime_decision_evidence_has_no_public_issue_path() -> None:
    assert not hasattr(RuntimeDecisionPolicyEvidence, "issue")


def _run(output_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository-root",
            str(ROOT),
            "--output-dir",
            str(output_dir),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_contract_fixture_with_typed_decision_evidence_is_private_and_reloaded(
    tmp_path: Path,
) -> None:
    output = tmp_path / "p6-output"
    output.mkdir(mode=0o700)
    decision_arguments = _decision_evidence_arguments(tmp_path)

    completed = _run(output, *decision_arguments)

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == "p6_automated_promotion_authorized"
    assert summary["invalid_v1_runtime_authorized"] is False
    assert summary["p8_promotion_authorized"] is False
    assert summary["user_visual_runtime_authorized"] is False
    paths = sorted(output.iterdir())
    assert len(paths) == summary["output_file_count"] == 7
    assert all((os.stat(path).st_mode & 0o777) == 0o400 for path in paths)

    identity = VisualRuntimePipelineIdentity.from_json_object(
        json.loads((output / "visual_runtime_pipeline_identity_v3.json").read_text())
    )
    promotion = AutomatedP6PromotionReceipt.from_json_object(
        json.loads((output / "automated_p6_promotion_receipt_v4.json").read_text())
    )
    reload_receipt = json.loads(
        (output / "automated_p6_promotion_reload_receipt_v2.json").read_text()
    )
    marker = json.loads(
        (output / "aihub68_phone_reference_v1_superseded_v2.json").read_text()
    )
    assert promotion.pipeline_identity_sha256 == identity.identity_sha256
    assert reload_receipt["promotion_receipt_sha256"] == promotion.receipt_sha256
    assert reload_receipt["fresh_process"] is True
    assert reload_receipt["strict_typed_reload"] is True
    assert reload_receipt["p6_automated_promotion_authorized"] is True
    assert reload_receipt["p8_promotion_authorized"] is False
    assert reload_receipt["user_visual_runtime_authorized"] is False
    assert marker["invalid_expected_phone"] == "ㅃ"
    assert marker["replacement_expected_phone"] == "ㅓ"
    assert marker["invalid_runtime_authorized"] is False

    before = {path.name: path.read_bytes() for path in paths}
    rerun = _run(output, *decision_arguments)
    assert rerun.returncode == 2
    assert {path.name: path.read_bytes() for path in paths} == before


def test_invalid_wrong_phone_v1_cannot_be_used_as_replacement_bundle(
    tmp_path: Path,
) -> None:
    output = tmp_path / "p6-output"
    output.mkdir(mode=0o700)
    invalid_v1 = (
        ROOT
        / "artifacts/visual_feedback/p6/aihub68_phone_reference_bundle_v1.json"
    )
    decision_arguments = _decision_evidence_arguments(tmp_path)

    completed = _run(
        output,
        *decision_arguments,
        "--phone-reference-bundle",
        str(invalid_v1),
    )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert list(output.iterdir()) == []


def test_production_builder_fails_closed_without_actual_decision_evidence(
    tmp_path: Path,
) -> None:
    output = tmp_path / "p6-output"
    output.mkdir(mode=0o700)

    completed = _run(output)

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert list(output.iterdir()) == []


def test_decision_evidence_must_be_frozen_mode_0400(tmp_path: Path) -> None:
    output = tmp_path / "p6-output"
    output.mkdir(mode=0o700)
    arguments = list(_decision_evidence_arguments(tmp_path))
    policy_path = Path(arguments[1])
    policy_path.chmod(0o600)

    completed = _run(output, *arguments)

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"
    assert list(output.iterdir()) == []


def _decision_evidence_arguments(directory: Path) -> tuple[str, ...]:
    video_qc_measurement = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc",
        policy_revision="video_qc_measurement_v1",
        policy_document=VideoQcSemanticPolicy(
            underexposed_luma_max_8bit=16,
            overexposed_luma_min_8bit=239,
            lip_roi_padding_fraction=0.05,
            minimum_single_face_frames=6,
            maximum_total_rgb_bytes=64 * 1024 * 1024,
        ),
    )
    av_sync_measurement = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync",
        policy_revision="lip_audio_av_sync_policy_v1",
        policy_document=AvSyncSemanticPolicy(
            maximum_absolute_offset_ms=300,
            offset_step_ms=20,
            energy_window_ms=40,
            minimum_landmark_frames=6,
            maximum_anchor_count=4,
        ),
    )
    video_qc_decision = P8RuntimePolicyArtifact.issue(
        policy_kind="video_qc_decision",
        policy_revision="unit_calibrated_video_qc_v1",
        policy_document=VideoQcDecisionSemanticPolicy(
            policy_revision="unit_calibrated_video_qc_v1",
            expected_measurement_policy_sha256=(
                video_qc_measurement.policy_sha256
            ),
            minimum_single_face_coverage=0.90,
            maximum_multiple_face_frame_fraction=0.0,
            minimum_mean_luma_8bit=20.0,
            maximum_mean_luma_8bit=230.0,
            maximum_underexposed_pixel_fraction=0.10,
            maximum_overexposed_pixel_fraction=0.10,
            minimum_lip_roi_mean_neighbor_luma_delta_8bit=1.0,
            minimum_track_transition_coverage=0.50,
            minimum_longest_single_face_run_fraction=0.80,
            maximum_lip_center_step_normalized=0.20,
        ),
    )
    av_sync_decision = P8RuntimePolicyArtifact.issue(
        policy_kind="av_sync_decision",
        policy_revision="unit_calibrated_av_sync_v1",
        policy_document=AvSyncDecisionSemanticPolicy(
            policy_revision="unit_calibrated_av_sync_v1",
            expected_measurement_policy_sha256=(
                av_sync_measurement.policy_sha256
            ),
            maximum_absolute_offset_ms=300,
            minimum_correlation=0.80,
            minimum_anchor_count=1,
            minimum_anchor_strength=0.80,
        ),
    )
    evidence = RuntimeDecisionPolicyEvidence._issue_from_validated_actual_evaluation(
        source_manifest_sha256=sha256_document({"fixture": "source"}),
        admission_receipt_sha256=sha256_document({"fixture": "admission"}),
        evaluation_protocol_sha256=sha256_document({"fixture": "protocol"}),
        evaluator_code_sha256=sha256_document({"fixture": "evaluator"}),
        video_qc_policy=video_qc_decision,
        av_sync_policy=av_sync_decision,
        admitted_item_count=8,
        evaluated_item_count=8,
        actor_overlap_count=0,
        attempt_overlap_count=0,
        qc_decision_lineage_valid_item_count=8,
        sync_decision_lineage_valid_item_count=8,
        fresh_process_reload_receipt_sha256=sha256_document(
            {"fixture": "reload"}
        ),
    )
    documents = {
        "video-qc-decision-policy": video_qc_decision.to_json_object(),
        "av-sync-decision-policy": av_sync_decision.to_json_object(),
        "decision-policy-evidence": evidence.to_json_object(),
    }
    arguments: list[str] = []
    for option, document in documents.items():
        path = directory / f"{option}.json"
        path.write_bytes(canonical_json_bytes(document) + b"\n")
        path.chmod(0o400)
        arguments.extend((f"--{option}", str(path)))
    return tuple(arguments)
