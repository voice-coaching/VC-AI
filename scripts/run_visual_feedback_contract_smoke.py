#!/usr/bin/env python3
"""Run synthetic-only Seungun/visual sidecar contract scenarios.

This script does not open a camera or video file and does not invoke Seungun or
a landmark model.  It validates adapter ordering, receipts, fail-closed states,
artifact scope, cleanup, and public supplement serialization.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path

from voice_coach.visual_feedback.models.binding import SameAttemptBindingReceipt
from voice_coach.visual_feedback.models.enums import (
    ConsentDecision,
    ExecutionScope,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import SyncAnchor
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.supplement import VisualFeedbackSupplement
from voice_coach.visual_feedback.testing import build_internal_contract_fixture


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic visual sidecar contract smoke tests."
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent the JSON report",
    )
    return parser.parse_args()


def run_contract_smoke(*, repository_root: Path) -> dict[str, object]:
    scenarios: list[dict[str, object]] = []

    ready_fixture = build_internal_contract_fixture(
        repository_root=repository_root,
        execution_scope=ExecutionScope.INTERNAL_CONTRACT_TEST,
    )
    ready = ready_fixture.coordinator.create_visual_feedback(ready_fixture.request)
    round_trip = VisualFeedbackSupplement.from_json(ready.to_json())
    scenarios.append(
        _scenario(
            name="internal_precomputed_ready",
            expected=VisualFeedbackStatus.VISUAL_FEEDBACK_READY,
            actual=ready.status,
            deletion_status=ready.media_deletion_receipt.status,
            extra_ok=(
                ready.supplemental_action_candidate is not None
                and round_trip == ready
            ),
        )
    )

    user_fixture = build_internal_contract_fixture(
        repository_root=repository_root,
        execution_scope=ExecutionScope.USER_RUNTIME,
    )
    user_result = user_fixture.coordinator.create_visual_feedback(user_fixture.request)
    scenarios.append(
        _scenario(
            name="user_runtime_rejects_synthetic_artifacts",
            expected=VisualFeedbackStatus.VISUAL_UNCERTAIN,
            actual=user_result.status,
            deletion_status=user_result.media_deletion_receipt.status,
            extra_ok=user_result.supplemental_action_candidate is None,
        )
    )

    denied_fixture = build_internal_contract_fixture(
        repository_root=repository_root,
        consent_decision=ConsentDecision.DENIED,
    )
    denied = denied_fixture.coordinator.create_visual_feedback(
        denied_fixture.request
    )
    scenarios.append(
        _scenario(
            name="consent_denied_before_video_processing",
            expected=VisualFeedbackStatus.VISUAL_FAILED_CLOSED,
            actual=denied.status,
            deletion_status=denied.media_deletion_receipt.status,
            extra_ok=(
                denied_fixture.video_store.access_count == 0
                and denied_fixture.quality_adapter.observation_count == 0
                and denied_fixture.tracking_adapter.tracking_count == 0
                and denied_fixture.sync_adapter.measurement_count == 0
            ),
        )
    )

    blur_fixture = build_internal_contract_fixture(
        repository_root=repository_root,
        blur_score=0.1,
    )
    blur = blur_fixture.coordinator.create_visual_feedback(blur_fixture.request)
    scenarios.append(
        _scenario(
            name="recoverable_blur_requires_rerecord",
            expected=VisualFeedbackStatus.VISUAL_RERECORD_REQUIRED,
            actual=blur.status,
            deletion_status=blur.media_deletion_receipt.status,
            extra_ok=blur.supplemental_action_candidate is None,
        )
    )

    sync_fixture = build_internal_contract_fixture(
        repository_root=repository_root,
        sync_anchors=(
            SyncAnchor(audio_ms=0, video_ms=100),
            SyncAnchor(audio_ms=500, video_ms=500),
            SyncAnchor(audio_ms=1_000, video_ms=900),
        ),
    )
    sync = sync_fixture.coordinator.create_visual_feedback(sync_fixture.request)
    scenarios.append(
        _scenario(
            name="uncertain_av_sync_blocks_action",
            expected=VisualFeedbackStatus.VISUAL_UNCERTAIN,
            actual=sync.status,
            deletion_status=sync.media_deletion_receipt.status,
            extra_ok=sync.supplemental_action_candidate is None,
        )
    )

    mismatch_fixture = build_internal_contract_fixture(
        repository_root=repository_root
    )
    original = mismatch_fixture.request.binding_receipt
    mismatched_binding = SameAttemptBindingReceipt.issue(
        attempt_id_sha256=original.attempt_id_sha256,
        prompt_id_sha256=original.prompt_id_sha256,
        prompt_revision=original.prompt_revision,
        expected_phone_sha256=original.expected_phone_sha256,
        analysis_sha256=original.analysis_sha256,
        audio_source_sha256=original.audio_source_sha256,
        audio_derivation_receipt_sha256=(
            original.audio_derivation_receipt_sha256
        ),
        video_source_sha256=sha256_document(
            {"internal_visual_fixture": "different_video_source"}
        ),
        audio_timebase=original.audio_timebase,
        video_timebase=original.video_timebase,
        upstream_reliability_receipt_sha256=(
            original.upstream_reliability_receipt_sha256
        ),
        consent_receipt_sha256=original.consent_receipt_sha256,
        issued_at_utc=original.issued_at_utc,
    )
    mismatch_request = replace(
        mismatch_fixture.request,
        binding_receipt=mismatched_binding,
    )
    mismatch = mismatch_fixture.coordinator.create_visual_feedback(mismatch_request)
    scenarios.append(
        _scenario(
            name="same_attempt_binding_mismatch_stops_dependency",
            expected=VisualFeedbackStatus.DEPENDENCY_UNAVAILABLE,
            actual=mismatch.status,
            deletion_status=mismatch.media_deletion_receipt.status,
            extra_ok=(
                mismatch_fixture.video_store.access_count == 0
                and mismatch.supplemental_action_candidate is None
            ),
        )
    )

    passed = all(bool(scenario["passed"]) for scenario in scenarios)
    return {
        "schema_version": "voice_coach.visual_contract_smoke.v1",
        "status": "passed" if passed else "failed",
        "scope": "synthetic_internal_contract_only",
        "real_video_decoded": False,
        "landmark_model_invoked": False,
        "seungun_detector_invoked": False,
        "user_runtime_ready_claim_verified": False,
        "scenarios": scenarios,
    }


def _scenario(
    *,
    name: str,
    expected: VisualFeedbackStatus,
    actual: VisualFeedbackStatus,
    deletion_status: str,
    extra_ok: bool,
) -> dict[str, object]:
    return {
        "name": name,
        "expected_status": expected.value,
        "actual_status": actual.value,
        "deletion_status": deletion_status,
        "passed": actual is expected and deletion_status == "deleted" and extra_ok,
    }


def main() -> int:
    args = _arguments()
    repository_root = Path(__file__).resolve().parents[1]
    report = run_contract_smoke(repository_root=repository_root)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
