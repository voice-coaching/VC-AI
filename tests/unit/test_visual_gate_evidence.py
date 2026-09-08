"""P6/P8 evidence validation stays digest-pinned and non-promotional."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.gate_evidence import (
    P6EvidenceValidationReceipt,
    P8ClaimConformanceReceipt,
    validate_p6_evidence_bundle,
    validate_p8_claim_conformance_bundle,
)
from voice_coach.visual_feedback.models.artifacts import (
    CalibrationRule,
    VisualCalibrationArtifact,
    VisualClaim,
    VisualClaimCatalog,
)
from voice_coach.visual_feedback.models.enums import (
    CalibrationScope,
    DeviationDirection,
    VisualCueState,
    VisualEvidenceRelation,
    VisualFeedbackStatus,
)
from voice_coach.visual_feedback.models.evidence import (
    AdapterReceipt,
    MediaDeletionReceipt,
)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.supplement import (
    SupplementalVisualActionCandidate,
    VisualCueEvidence,
    VisualFeedbackSupplement,
)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/validate_visual_gate_evidence.py"


def test_p6_validates_only_privacy_safe_evidence_and_never_promotes() -> None:
    receipt = validate_p6_evidence_bundle(_p6_bundle())
    restored = P6EvidenceValidationReceipt.from_json_object(receipt.to_json_object())

    assert restored == receipt
    assert receipt.source_dataset_id == "aihub_68"
    assert receipt.status == "evidence_validated_not_promoted"
    assert receipt.visual_reliability_authorized is False
    assert receipt.reason_codes == (
        "p8_independent_evaluation_required",
        "p8_claim_conformance_required",
    )


def test_p6_rejects_actor_overlap_even_when_all_digests_are_reissued() -> None:
    bundle = _p6_bundle()
    split = bundle["split_receipt"]
    assert isinstance(split, dict)
    split["actor_overlap_count"] = 1
    _resign(split, "receipt_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="overlap"):
        validate_p6_evidence_bundle(bundle)


@pytest.mark.parametrize(
    "field",
    ["contains_pronunciation_truth", "contains_action_truth"],
)
def test_p6_rejects_invented_label_roles(field: str) -> None:
    bundle = _p6_bundle()
    metric = bundle["metric_report"]
    assert isinstance(metric, dict)
    metric[field] = True
    _resign(metric, "report_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="must not create"):
        validate_p6_evidence_bundle(bundle)


def test_p6_rejects_reload_that_was_not_verified_in_a_fresh_process() -> None:
    bundle = _p6_bundle()
    reload_receipt = bundle["reload_receipt"]
    assert isinstance(reload_receipt, dict)
    reload_receipt["fresh_process"] = False
    _resign(reload_receipt, "receipt_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="reload evidence"):
        validate_p6_evidence_bundle(bundle)


def test_p8_issues_exact_zero_violation_receipt_without_authorizing_claims() -> None:
    bundle = _p8_bundle()

    receipt = validate_p8_claim_conformance_bundle(bundle)
    restored = P8ClaimConformanceReceipt.from_json_object(
        receipt.to_json_object()
    )

    assert restored == receipt
    assert receipt.status == "zero_violations_not_promoted"
    assert receipt.violation_count == 0
    assert receipt.case_count == 6
    assert receipt.ready_case_count == 1
    assert receipt.user_visual_claims_authorized is False
    assert receipt.read_zero_seal_status == "digest_assertion_only"
    assert receipt.p6_source_dataset_id == "aihub_68"
    assert receipt.receipt_sha256 == sha256_document(receipt.unsigned_json_object())


def test_p8_rejects_an_unregistered_same_attempt_binding() -> None:
    bundle = _p8_bundle()
    cases = bundle["replay_cases"]
    assert isinstance(cases, list)
    case = cases[0]
    assert isinstance(case, dict)
    case["registered_binding_ref"] = _sha("other_binding")
    _resign(case, "case_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="binding"):
        validate_p8_claim_conformance_bundle(bundle)


def test_p8_rejects_wrong_calibration_on_a_suppressed_conflict() -> None:
    bundle = _p8_bundle()
    cases = bundle["replay_cases"]
    assert isinstance(cases, list)
    conflict_case = next(
        case
        for case in cases
        if isinstance(case, dict) and case["scenario"] == "conflict_suppressed"
    )
    supplement = conflict_case["supplement"]
    assert isinstance(supplement, dict)
    cue_evidence = supplement["cue_evidence"]
    assert isinstance(cue_evidence, list)
    evidence = cue_evidence[0]
    assert isinstance(evidence, dict)
    evidence["calibration_artifact_sha256"] = _sha("wrong_calibration")
    _resign(conflict_case, "case_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="calibration differs"):
        validate_p8_claim_conformance_bundle(bundle)


def test_p8_rejects_forbidden_target_even_with_consistent_catalog_and_policy() -> None:
    bundle = _p8_bundle(renderer_key="visual_tongue_position")

    with pytest.raises(VisualFeedbackContractError, match="forbidden target"):
        validate_p8_claim_conformance_bundle(bundle)


def test_p8_rejects_a_candidate_in_a_suppression_scenario() -> None:
    bundle = _p8_bundle()
    cases = bundle["replay_cases"]
    assert isinstance(cases, list)
    ready_case = cases[0]
    assert isinstance(ready_case, dict)
    ready_case["scenario"] = "missing_suppressed"
    _resign(ready_case, "case_sha256")
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="suppression scenario emitted"):
        validate_p8_claim_conformance_bundle(bundle)


def test_p8_rejects_incomplete_suppression_scenario_coverage() -> None:
    bundle = _p8_bundle()
    cases = bundle["replay_cases"]
    assert isinstance(cases, list)
    cases[:] = [
        case
        for case in cases
        if isinstance(case, dict) and case["scenario"] != "low_confidence_suppressed"
    ]
    _resign(bundle, "bundle_sha256")

    with pytest.raises(VisualFeedbackContractError, match="coverage is incomplete"):
        validate_p8_claim_conformance_bundle(bundle)


def test_p8_requires_every_catalog_claim_to_be_exercised() -> None:
    p6_bundle = _p6_bundle()
    p6_receipt = validate_p6_evidence_bundle(p6_bundle)
    catalog = _catalog(extra_claim=True)
    bundle = _p8_bundle(
        p6_bundle=p6_bundle,
        p6_receipt=p6_receipt,
        catalog=catalog,
    )

    with pytest.raises(VisualFeedbackContractError, match="every frozen catalog"):
        validate_p8_claim_conformance_bundle(bundle)


@pytest.mark.parametrize("gate", ["p6", "p8"])
def test_internal_cli_emits_only_receipt_json_and_never_the_input_path(
    tmp_path: Path, gate: str
) -> None:
    bundle = _p6_bundle() if gate == "p6" else _p8_bundle()
    path = tmp_path / f"private-summary-{gate}.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(CLI), gate, "--bundle", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert str(path) not in completed.stdout
    assert str(path) not in completed.stderr
    document = json.loads(completed.stdout)
    if gate == "p6":
        assert document["visual_reliability_authorized"] is False
    else:
        assert document["user_visual_claims_authorized"] is False


def test_internal_cli_fails_closed_without_echoing_a_bad_locator(tmp_path: Path) -> None:
    missing = tmp_path / "sensitive-missing-summary.json"

    completed = subprocess.run(
        [sys.executable, str(CLI), "p6", "--bundle", str(missing)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 2
    assert str(missing) not in completed.stderr
    assert json.loads(completed.stderr) == {
        "reason_codes": ["visual_gate_evidence_validation_failed"],
        "status": "failed_closed",
    }


def test_internal_cli_supports_pathless_bundle_fd(tmp_path: Path) -> None:
    path = tmp_path / "sensitive-p6-summary.json"
    path.write_text(json.dumps(_p6_bundle(), ensure_ascii=False), encoding="utf-8")

    with path.open("rb") as source:
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "p6",
                "--bundle-fd",
                str(source.fileno()),
            ],
            cwd=ROOT,
            pass_fds=(source.fileno(),),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    assert completed.returncode == 0, completed.stderr
    assert str(path) not in completed.stdout
    assert str(path) not in completed.stderr
    assert json.loads(completed.stdout)["visual_reliability_authorized"] is False


def test_internal_cli_rejects_a_writable_bundle_fd(tmp_path: Path) -> None:
    path = tmp_path / "writable-summary.json"
    path.write_text(json.dumps(_p6_bundle(), ensure_ascii=False), encoding="utf-8")

    with path.open("r+b") as source:
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "p6",
                "--bundle-fd",
                str(source.fileno()),
            ],
            cwd=ROOT,
            pass_fds=(source.fileno(),),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["status"] == "failed_closed"


def test_internal_cli_help_exposes_both_summary_input_modes() -> None:
    completed = subprocess.run(
        [sys.executable, str(CLI), "p8", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0
    assert "--bundle" in completed.stdout
    assert "--bundle-fd" in completed.stdout


def test_internal_cli_schema_is_discoverable_without_fake_artifacts() -> None:
    completed = subprocess.run(
        [sys.executable, str(CLI), "schema", "p8"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0
    schema = json.loads(completed.stdout)
    assert schema["locked_source_dataset_id"] == "aihub_538"
    assert schema["read_zero_seal_status"] == "digest_assertion_only"
    assert schema["promotion_authorized"] is False
    assert set(schema["required_replay_scenarios"]) == {
        "ready_claim",
        "missing_suppressed",
        "low_confidence_suppressed",
        "out_of_distribution_suppressed",
        "conflict_suppressed",
        "unsupported_slice_suppressed",
    }


def _p6_bundle() -> dict[str, object]:
    artifact = _calibration_artifact()
    source_revision = "aihub68_manifest_revision_2026_08"
    rights = _signed(
        {
            "schema_version": "voice_coach.visual_dataset_rights_receipt.v1",
            "source_dataset_id": "aihub_68",
            "source_revision": source_revision,
            "admission_status": "admitted",
            "purpose_scope": "visual_geometry_calibration",
            "derived_artifact_use": "permitted",
            "raw_media_git_export": "prohibited",
            "raw_identifier_export": "prohibited",
        },
        "receipt_sha256",
    )
    split = _signed(
        {
            "schema_version": "voice_coach.visual_dataset_split_receipt.v1",
            "source_dataset_id": "aihub_68",
            "source_revision": source_revision,
            "assignment_unit": "actor_id",
            "development_partition_sha256": _sha("p6_development_partition"),
            "calibration_partition_sha256": _sha("p6_calibration_partition"),
            "development_actor_count": 12,
            "calibration_actor_count": 8,
            "actor_overlap_count": 0,
            "attempt_overlap_count": 0,
            "frozen_before_metric_access": True,
        },
        "receipt_sha256",
    )
    metric = _signed(
        {
            "schema_version": "voice_coach.visual_calibration_metric_summary.v1",
            "evaluated_sample_count": 48,
            "landmark_annotation_point_count": 24,
            "normalized_landmark_error": 0.04,
            "missing_fraction": 0.05,
            "coverage_fraction": 0.95,
            "slice_report_sha256": _sha("p6_slice_report"),
            "repeatability_report_sha256": _sha("p6_repeatability_report"),
            "allowed_cue_ids": ["lip_aperture_ratio"],
            "contains_pronunciation_truth": False,
            "contains_action_truth": False,
        },
        "report_sha256",
    )
    reload_receipt = _signed(
        {
            "schema_version": "voice_coach.visual_calibration_reload_receipt.v1",
            "artifact_sha256": artifact.artifact_sha256,
            "artifact_document_sha256": sha256_document(artifact.to_json_object()),
            "loader_revision": "strict_visual_artifact_loader_v1",
            "fresh_process": True,
            "hash_match": True,
            "output_parity": True,
        },
        "receipt_sha256",
    )
    return _signed(
        {
            "schema_version": "voice_coach.p6_evidence_bundle.v1",
            "source_dataset_id": "aihub_68",
            "source_revision": source_revision,
            "dataset_manifest_sha256": _sha("aihub68_manifest"),
            "capture_contract_id": artifact.capture_contract_id,
            "population_id_sha256": artifact.population_id_sha256,
            "rights_receipt": rights,
            "split_receipt": split,
            "pipeline_identity": {
                "tracker_revision": "mediapipe_face_landmarker_1_0_1",
                "tracker_sha256": _sha("tracker"),
                "normalizer_revision": "lip_width_normalizer_v1",
                "normalizer_sha256": _sha("normalizer"),
                "cue_extractor_revision": "visible_lip_cues_v1",
                "cue_extractor_sha256": _sha("cue_extractor"),
                "metric_protocol_sha256": _sha("metric_protocol"),
            },
            "metric_report": metric,
            "calibration_artifact": artifact.to_json_object(),
            "reload_receipt": reload_receipt,
        },
        "bundle_sha256",
    )


def _p8_bundle(
    *,
    renderer_key: str = "visual_lip_aperture_reduce",
    p6_bundle: dict[str, object] | None = None,
    p6_receipt: P6EvidenceValidationReceipt | None = None,
    catalog: VisualClaimCatalog | None = None,
) -> dict[str, object]:
    p6_bundle = p6_bundle or _p6_bundle()
    p6_receipt = p6_receipt or validate_p6_evidence_bundle(p6_bundle)
    catalog = catalog or _catalog(renderer_key=renderer_key)
    supplement = _ready_supplement(
        calibration_sha256=p6_receipt.calibration_artifact_sha256,
        catalog=catalog,
        renderer_key=renderer_key,
    )
    claim_bindings = [
        {
            "approved_claim_id": claim.approved_claim_id,
            "action_id": claim.action_id,
            "renderer_key": claim.renderer_key,
            "cue_id": claim.cue_id,
        }
        for claim in catalog.claims
    ]
    policy = _signed(
        {
            "schema_version": "voice_coach.visual_claim_policy.v1",
            "policy_revision": "visible_lip_claim_policy_v1",
            "allowed_cue_ids": sorted({claim.cue_id for claim in catalog.claims}),
            "allowed_renderer_keys": sorted(
                {claim.renderer_key for claim in catalog.claims}
            ),
            "allowed_claim_bindings": claim_bindings,
            "forbidden_identifier_tokens": [
                "airflow",
                "diagnosis",
                "glottis",
                "medical",
                "tongue",
                "velum",
            ],
        },
        "policy_sha256",
    )
    fixtures = [
        _signed(
            {
                "schema_version": "voice_coach.visual_forbidden_claim_fixture.v1",
                "fixture_id": f"forbid_{token}",
                "candidate_identifier": f"visual_{token}_target",
                "expected_result": "rejected",
            },
            "fixture_sha256",
        )
        for token in ("airflow", "diagnosis", "glottis", "medical", "tongue", "velum")
    ]
    replay_cases = [
        _replay_case("ready_claim", supplement),
        *(
            _replay_case(
                scenario,
                _suppressed_supplement(
                    scenario=scenario,
                    calibration_sha256=p6_receipt.calibration_artifact_sha256,
                    catalog=catalog,
                ),
            )
            for scenario in (
                "missing_suppressed",
                "low_confidence_suppressed",
                "out_of_distribution_suppressed",
                "conflict_suppressed",
                "unsupported_slice_suppressed",
            )
        ),
    ]
    return _signed(
        {
            "schema_version": "voice_coach.p8_conformance_bundle.v1",
            "protocol_revision": "locked_claim_replay_v1",
            "locked_source_dataset_id": "aihub_538",
            "locked_source_revision": "aihub538_locked_revision_2026_08",
            "locked_manifest_sha256": _sha("p8_locked_manifest"),
            "read_zero_seal_sha256": _sha("p8_read_zero_seal"),
            "p6_evidence_receipt": p6_receipt.to_json_object(),
            "calibration_artifact_sha256": p6_receipt.calibration_artifact_sha256,
            "claim_catalog": catalog.to_json_object(),
            "claim_policy": policy,
            "forbidden_claim_fixtures": fixtures,
            "replay_cases": replay_cases,
        },
        "bundle_sha256",
    )


def _calibration_artifact() -> VisualCalibrationArtifact:
    return VisualCalibrationArtifact.issue(
        artifact_id="aihub68_visible_lip_calibration_v1",
        revision="p6_calibration_v1",
        scope=CalibrationScope.CALIBRATED,
        capture_contract_id="aihub68_front_capture_v1",
        population_id_sha256=_sha("p6_population_summary"),
        rules=(
            CalibrationRule(
                cue_id="lip_aperture_ratio",
                measurement_unit="ratio",
                expected_phones=("ㅏ",),
                context_id="phone_center",
                reference_center=0.15,
                reference_scale=0.05,
                support_minimum=0.02,
                support_maximum=0.5,
                feedback_minimum_absolute_deviation=1.0,
            ),
        ),
    )


def _replay_case(
    scenario: str, supplement: VisualFeedbackSupplement
) -> dict[str, object]:
    return _signed(
        {
            "schema_version": "voice_coach.visual_claim_replay_case.v1",
            "case_id_sha256": _sha(f"p8_case_{scenario}"),
            "scenario": scenario,
            "expected_phone": "ㅏ",
            "context_id": "phone_center",
            "registered_binding_ref": supplement.upstream_binding_ref,
            "registered_phone_anchor_ref": supplement.upstream_phone_anchor_ref,
            "supplement": supplement.to_json_object(),
        },
        "case_sha256",
    )


def _suppressed_supplement(
    *,
    scenario: str,
    calibration_sha256: str,
    catalog: VisualClaimCatalog,
) -> VisualFeedbackSupplement:
    state_by_scenario = {
        "missing_suppressed": VisualCueState.MISSING,
        "low_confidence_suppressed": VisualCueState.LOW_CONFIDENCE,
        "out_of_distribution_suppressed": VisualCueState.OUT_OF_DISTRIBUTION,
        "conflict_suppressed": VisualCueState.OBSERVED,
        "unsupported_slice_suppressed": VisualCueState.NOT_OBSERVABLE,
    }
    status_by_scenario = {
        "missing_suppressed": VisualFeedbackStatus.VISUAL_UNCERTAIN,
        "low_confidence_suppressed": VisualFeedbackStatus.VISUAL_UNCERTAIN,
        "out_of_distribution_suppressed": VisualFeedbackStatus.VISUAL_UNSUPPORTED,
        "conflict_suppressed": VisualFeedbackStatus.VISUAL_UNCERTAIN,
        "unsupported_slice_suppressed": VisualFeedbackStatus.VISUAL_UNSUPPORTED,
    }
    state = state_by_scenario[scenario]
    observed = state is VisualCueState.OBSERVED
    backend = AdapterReceipt(
        backend_id="mediapipe_lip_tracker",
        backend_revision="1.0.1",
        status="observed" if observed else "suppressed",
        reason_codes=() if observed else (scenario,),
    )
    return VisualFeedbackSupplement(
        status=status_by_scenario[scenario],
        reason_codes=(
            "unsupported_capture_slice"
            if scenario == "unsupported_slice_suppressed"
            else scenario,
        ),
        upstream_analysis_ref=_sha(f"p8_analysis_{scenario}"),
        upstream_binding_ref=_sha(f"p8_binding_{scenario}"),
        upstream_phone_anchor_ref=_sha(f"p8_anchor_{scenario}"),
        cue_evidence=(
            VisualCueEvidence(
                evidence_id=_sha(f"p8_evidence_{scenario}"),
                cue_id="lip_aperture_ratio",
                cue_state=state,
                video_start_ms=200,
                video_end_ms=400,
                measurement_value=0.3 if observed else None,
                measurement_unit="ratio" if observed else None,
                calibrated_deviation=3.0 if observed else None,
                measurement_backend=backend,
                calibration_artifact_sha256=calibration_sha256
                if observed
                else None,
                reason_codes=() if observed else (scenario,),
            ),
        ),
        evidence_relation=(
            VisualEvidenceRelation.CONFLICTS_WITH_UPSTREAM
            if scenario == "conflict_suppressed"
            else VisualEvidenceRelation.NOT_COMPARABLE
        ),
        supplemental_action_candidate=None,
        visual_backend_receipts=(backend,),
        reference_calibration_receipts=(
            (
                AdapterReceipt(
                    backend_id="visual_calibration_artifact",
                    backend_revision="p6_calibration_v1",
                    status="loaded",
                ),
                AdapterReceipt(
                    backend_id="visual_claim_catalog",
                    backend_revision=catalog.revision,
                    status="loaded",
                ),
            )
            if observed
            else ()
        ),
        media_deletion_receipt=MediaDeletionReceipt(
            status="deleted",
            deletion_receipt_sha256=_sha(f"p8_deleted_{scenario}"),
            backend_receipt=AdapterReceipt(
                backend_id="restricted_video_lifecycle",
                backend_revision="contract_v1",
                status="deleted",
            ),
        ),
        notices=("visual_supplement_only",),
    )


def _catalog(
    *, renderer_key: str = "visual_lip_aperture_reduce", extra_claim: bool = False
) -> VisualClaimCatalog:
    claims = [
        VisualClaim(
            action_id="visual_lip_aperture_reduce_001",
            approved_claim_id="visual_claim_lip_aperture_above_reference_001",
            renderer_key=renderer_key,
            cue_id="lip_aperture_ratio",
            expected_phones=("ㅏ",),
            context_id="phone_center",
            direction=DeviationDirection.ABOVE_REFERENCE,
        )
    ]
    if extra_claim:
        claims.append(
            VisualClaim(
                action_id="visual_lip_width_expand_001",
                approved_claim_id="visual_claim_lip_width_below_reference_001",
                renderer_key="visual_lip_width_expand",
                cue_id="lip_width_ratio",
                expected_phones=("ㅣ",),
                context_id="phone_center",
                direction=DeviationDirection.BELOW_REFERENCE,
            )
        )
    return VisualClaimCatalog.issue(
        catalog_id="p8_visible_lip_claim_catalog_v1",
        revision="p8_catalog_v1",
        scope=CalibrationScope.CALIBRATED,
        claims=tuple(claims),
    )


def _ready_supplement(
    *,
    calibration_sha256: str,
    catalog: VisualClaimCatalog,
    renderer_key: str,
) -> VisualFeedbackSupplement:
    evidence_id = _sha("p8_ready_evidence")
    binding_ref = _sha("p8_registered_binding")
    anchor_ref = _sha("p8_registered_phone_anchor")
    claim = catalog.claims[0]
    return VisualFeedbackSupplement(
        status=VisualFeedbackStatus.VISUAL_FEEDBACK_READY,
        reason_codes=("calibrated_visible_cue_supports_upstream",),
        upstream_analysis_ref=_sha("p8_upstream_analysis"),
        upstream_binding_ref=binding_ref,
        upstream_phone_anchor_ref=anchor_ref,
        cue_evidence=(
            VisualCueEvidence(
                evidence_id=evidence_id,
                cue_id="lip_aperture_ratio",
                cue_state=VisualCueState.OBSERVED,
                video_start_ms=200,
                video_end_ms=400,
                measurement_value=0.3,
                measurement_unit="ratio",
                calibrated_deviation=3.0,
                measurement_backend=AdapterReceipt(
                    backend_id="mediapipe_lip_tracker",
                    backend_revision="1.0.1",
                    status="observed",
                ),
                calibration_artifact_sha256=calibration_sha256,
            ),
        ),
        evidence_relation=VisualEvidenceRelation.SUPPORTS_UPSTREAM,
        supplemental_action_candidate=SupplementalVisualActionCandidate(
            action_id=claim.action_id,
            approved_claim_id=claim.approved_claim_id,
            catalog_revision=catalog.revision,
            renderer_key=renderer_key,
            upstream_phone_anchor_ref=anchor_ref,
            supporting_visual_evidence_ids=(evidence_id,),
        ),
        visual_backend_receipts=(
            AdapterReceipt(
                backend_id="mediapipe_lip_tracker",
                backend_revision="1.0.1",
                status="observed",
            ),
        ),
        reference_calibration_receipts=(
            AdapterReceipt(
                backend_id="visual_calibration_artifact",
                backend_revision="p6_calibration_v1",
                status="loaded",
            ),
            AdapterReceipt(
                backend_id="visual_claim_catalog",
                backend_revision=catalog.revision,
                status="loaded",
            ),
        ),
        media_deletion_receipt=MediaDeletionReceipt(
            status="deleted",
            deletion_receipt_sha256=_sha("p8_media_deleted"),
            backend_receipt=AdapterReceipt(
                backend_id="restricted_video_lifecycle",
                backend_revision="contract_v1",
                status="deleted",
            ),
        ),
        notices=("visual_supplement_only",),
    )


def _signed(document: dict[str, object], digest_key: str) -> dict[str, object]:
    result = json.loads(json.dumps(document, ensure_ascii=False))
    result[digest_key] = sha256_document(result)
    return result


def _resign(document: dict[str, object], digest_key: str) -> None:
    document.pop(digest_key, None)
    document[digest_key] = sha256_document(document)


def _sha(label: str) -> str:
    return sha256_document({"visual_gate_test": label})
