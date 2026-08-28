from __future__ import annotations

from copy import deepcopy
import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from voice_coach.evaluation.alignment_review import ReviewDataset
from voice_coach.evaluation.g04_amended_contract import (
    AmendedReviewSubmission,
    G04AmendedContractError,
    G04AmendedReviewStore,
    G04Resolution,
    G04ReviewProfile,
    G04RootCause,
    build_amended_progress,
    build_completion_attestation,
    build_i30_report,
    build_t43_manifest,
    build_t43_manifest_receipt,
    build_t43_report,
    selected_hashes_from_t43_manifest,
    validate_completion_attestation,
    validate_t43_manifest,
)
from voice_coach.evaluation.g04_amended_review_app import (
    API_PREFIX,
    FRONTEND_ROOT,
    create_amended_review_app,
)


def _write_dataset(
    root: Path,
    *,
    count: int,
    drift_indices: set[int] = frozenset(),
    balanced_error_presence: bool = False,
) -> tuple[ReviewDataset, Path, Path]:
    restricted = root / "restricted"
    restricted.mkdir(parents=True)
    (restricted / "audio.wav").write_bytes(b"RIFF-synthetic")
    fields = [
        "audit_index",
        "utterance_id",
        "split",
        "l1",
        "gender",
        "proficiency",
        "error_presence",
        "duration_seconds",
        "target_phone_count",
        "realized_phone_count",
        "audio_path",
        "alignment_detail_line",
    ]
    worksheet = root / "worksheet.tsv"
    with worksheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for index in range(1, count + 1):
            writer.writerow(
                {
                    "audit_index": index,
                    "utterance_id": f"private-{index:03d}",
                    "split": "manual_audit",
                    "l1": "Chinese",
                    "gender": "F",
                    "proficiency": "Intermediate",
                    "error_presence": str(
                        balanced_error_presence and index > count // 2
                    ).lower(),
                    "duration_seconds": "6.0",
                    "target_phone_count": 1,
                    "realized_phone_count": 1,
                    "audio_path": "audio.wav",
                    "alignment_detail_line": index,
                }
            )
    details = root / "details.jsonl"
    rows = []
    for index in range(1, count + 1):
        automatic = {
            "target_label": "ㄱ",
            "realized_label": "ㄱ",
            "start": 0.1,
            "end": 0.2,
            "overlap_seconds": 0.1,
            "overlap_ratio": 1.0,
            "operation": "match",
        }
        if index in drift_indices:
            automatic["operation"] = "substitution"
        rows.append(
            {
                "audit_index": index,
                "utterance_id": f"private-{index:03d}",
                "target_phone_intervals": [{"start": 0.1, "end": 0.2, "label": "ㄱ"}],
                "realized_phone_intervals": [{"start": 0.1, "end": 0.2, "label": "ㄱ"}],
                "automatic_phone_alignments": [automatic],
            }
        )
    details.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return (
        ReviewDataset.load(
            worksheet=worksheet,
            details=details,
            dataset_root=restricted,
        ),
        worksheet,
        details,
    )


def _t43_fixture(
    tmp_path: Path,
) -> tuple[ReviewDataset, dict[str, object], dict[str, object]]:
    dataset, _, _ = _write_dataset(
        tmp_path,
        count=43,
        drift_indices=set(range(1, 20)),
    )
    proxy_rows = {}
    for item in dataset.items:
        proxy_rows[item.item_sha256] = {
            "annotation_fidelity": (
                "realized_annotation_mismatch"
                if item.audit_index >= 18
                else "consistent"
            )
        }
    proxy_state: dict[str, object] = {
        "schema_version": "voice_coach.g04_audio_ai_proxy_state.v1",
        "source": {
            "worksheet_sha256": dataset.worksheet_sha256,
            "details_sha256": dataset.details_sha256,
            "expected_count": 43,
        },
        "rows": proxy_rows,
    }
    manifest = build_t43_manifest(
        dataset=dataset,
        proxy_state=proxy_state,
        proxy_state_sha256="a" * 64,
        alignment_policy_source_sha256="b" * 64,
        align_intervals_source_sha256="c" * 64,
    )
    return dataset, proxy_state, manifest


def _t43_submission(*, unresolved: bool = False) -> AmendedReviewSubmission:
    return AmendedReviewSubmission(
        alignment_correct=True,
        maximum_boundary_error_ms=0.0,
        incorrect_interval_count=0,
        annotation_fidelity="consistent",
        root_cause=G04RootCause.NO_DEFECT_CONFIRMED,
        resolution=(
            G04Resolution.UNRESOLVED if unresolved else G04Resolution.RETAINED_NO_DEFECT
        ),
        notes="",
    )


def test_t43_selection_reproduces_amendment_and_rejects_row_drift(
    tmp_path: Path,
) -> None:
    _, _, manifest = _t43_fixture(tmp_path)

    assert manifest["counts"] == {
        "alignment_drift_count": 19,
        "ai_non_abstain_mismatch_count": 26,
        "intersection_count": 2,
        "union_count": 43,
    }
    assert len(selected_hashes_from_t43_manifest(manifest)) == 43
    receipt = build_t43_manifest_receipt(
        manifest=manifest,
        manifest_sha256="d" * 64,
    )
    assert receipt["human_review_completed_count"] == 0
    assert receipt["privacy"]["contains_item_hashes"] is False

    tampered = deepcopy(manifest)
    drift_only = next(
        item
        for item in tampered["items"]
        if item["selection_reasons"] == ["alignment_recomputation_drift"]
    )
    drift_only["selection_reasons"] = ["ai_non_abstain_annotation_mismatch"]
    with pytest.raises(G04AmendedContractError, match="reason"):
        validate_t43_manifest(tampered)


def test_t43_store_report_resume_and_resolution_gate(tmp_path: Path) -> None:
    dataset, _, manifest = _t43_fixture(tmp_path)
    state = tmp_path / "state.json"
    store = G04AmendedReviewStore(
        path=state,
        dataset=dataset,
        profile=G04ReviewProfile.T43,
        selected_item_hashes=selected_hashes_from_t43_manifest(manifest),
        selection_source_sha256="d" * 64,
    )
    with pytest.raises(G04AmendedContractError, match="incompatible"):
        store.save_review(
            item_hash=store.items[0].item_sha256,
            reviewer_id="expert-a",
            submission=AmendedReviewSubmission(
                alignment_correct=False,
                maximum_boundary_error_ms=10.0,
                incorrect_interval_count=1,
                annotation_fidelity="consistent",
                root_cause=G04RootCause.ALIGNMENT_ALGORITHM,
                resolution=G04Resolution.RETAINED_NO_DEFECT,
                notes="",
            ),
            replace=False,
        )
    for item in store.items:
        store.save_review(
            item_hash=item.item_sha256,
            reviewer_id="expert-a",
            submission=_t43_submission(),
            replace=False,
        )
    report = build_t43_report(store=store)
    assert report["status"] == "passed"
    assert report["accuracy_denominator_allowed"] is False
    state_text = state.read_text(encoding="utf-8")
    assert "private-001" not in state_text
    assert "audio.wav" not in state_text

    resumed = G04AmendedReviewStore(
        path=state,
        dataset=dataset,
        profile=G04ReviewProfile.T43,
        selected_item_hashes=selected_hashes_from_t43_manifest(manifest),
        selection_source_sha256="d" * 64,
    )
    assert resumed.status()["completed_count"] == 43


def test_i30_phone_gate_strata_and_completion_arithmetic(tmp_path: Path) -> None:
    dataset, _, _ = _write_dataset(
        tmp_path / "i30",
        count=30,
        balanced_error_presence=True,
    )
    store = G04AmendedReviewStore(
        path=tmp_path / "i30-state.json",
        dataset=dataset,
        profile=G04ReviewProfile.I30,
        selected_item_hashes=[item.item_sha256 for item in dataset.items],
        selection_source_sha256="e" * 64,
    )
    for item in store.items:
        incorrect = 1 if item.audit_index <= 2 else 0
        store.save_review(
            item_hash=item.item_sha256,
            reviewer_id="expert-b",
            submission=AmendedReviewSubmission(
                alignment_correct=incorrect == 0,
                maximum_boundary_error_ms=10.0,
                incorrect_interval_count=incorrect,
                notes="",
            ),
            replace=False,
        )
    failed = build_i30_report(store=store)
    assert failed["status"] == "failed"
    assert failed["overall"]["phone_level_accuracy"] == pytest.approx(28 / 30)
    assert failed["error_presence_strata"]["false"]["utterance_count"] == 15

    for item in store.items[:2]:
        store.save_review(
            item_hash=item.item_sha256,
            reviewer_id="expert-b",
            submission=AmendedReviewSubmission(
                alignment_correct=True,
                maximum_boundary_error_ms=0.0,
                incorrect_interval_count=0,
                notes="",
            ),
            replace=True,
        )
    i30_report = build_i30_report(store=store)
    assert i30_report["status"] == "passed"
    assert i30_report["overall"]["phone_level_accuracy"] == 1.0

    t43_dataset, _, manifest = _t43_fixture(tmp_path / "t43")
    t43_store = G04AmendedReviewStore(
        path=tmp_path / "t43-state.json",
        dataset=t43_dataset,
        profile=G04ReviewProfile.T43,
        selected_item_hashes=selected_hashes_from_t43_manifest(manifest),
        selection_source_sha256="f" * 64,
    )
    for item in t43_store.items:
        t43_store.save_review(
            item_hash=item.item_sha256,
            reviewer_id="expert-a",
            submission=_t43_submission(),
            replace=False,
        )
    t43_report = build_t43_report(store=t43_store)
    completion = build_completion_attestation(
        t43_report=t43_report,
        t43_report_sha256="1" * 64,
        i30_report=i30_report,
        i30_report_sha256="2" * 64,
        reviewer_attestation_sha256="3" * 64,
        reviewer_qualification_confirmed=True,
        research_lead_approved=True,
    )
    validate_completion_attestation(completion)
    tampered = deepcopy(completion)
    tampered["i30"]["incorrect_phone_intervals"] = 1
    with pytest.raises(G04AmendedContractError, match="arithmetic"):
        validate_completion_attestation(tampered)


def test_progress_stays_closed_and_ui_never_exposes_raw_identifiers(
    tmp_path: Path,
) -> None:
    dataset, _, manifest = _t43_fixture(tmp_path)
    store = G04AmendedReviewStore(
        path=tmp_path / "state.json",
        dataset=dataset,
        profile=G04ReviewProfile.T43,
        selected_item_hashes=selected_hashes_from_t43_manifest(manifest),
        selection_source_sha256="4" * 64,
    )
    t43_report = build_t43_report(store=store)
    i30_stub = {
        "schema_version": "voice_coach.g04_i30_phone_report.v1",
        "status": "incomplete",
        "completed_count": 0,
        "remaining_count": 30,
        "overall": {"phone_level_accuracy": None},
    }
    progress = build_amended_progress(
        t43_report=t43_report,
        i30_report=i30_stub,
        consumer_migration_complete=True,
    )
    assert progress["status"] == "incomplete"
    assert progress["canonical_g04_completion_claim_allowed"] is False

    app = create_amended_review_app(
        store=store,
        reviewer_id="expert-a",
        access_token="synthetic-review-token",
    )
    client = TestClient(app)
    headers = {"X-Review-Token": "synthetic-review-token"}
    index = client.get("/")
    assert index.status_code == 200
    assert "T43 · I30 전문가 음성 검수" in index.text
    assert "private-001" not in index.text
    assert index.headers["content-security-policy"].startswith("default-src 'self'")
    styles = client.get("/assets/styles.css")
    frontend = client.get("/assets/app.js")
    assert styles.status_code == 200
    assert frontend.status_code == 200
    assert f'const API_PREFIX = "{API_PREFIX}"' in frontend.text
    assert "/review-items" in frontend.text
    assert "innerHTML" not in frontend.text
    assert client.get(f"{API_PREFIX}/review-session").status_code == 403

    session = client.get(f"{API_PREFIX}/review-session", headers=headers)
    assert session.status_code == 200
    assert session.json()["profile"] == "t43_root_cause"
    response = client.get(f"{API_PREFIX}/review-items", headers=headers)
    assert response.status_code == 200
    assert "private-001" not in response.text
    assert "audio.wav" not in response.text
    item_hash = store.items[0].item_sha256
    audio = client.get(
        f"{API_PREFIX}/review-items/{item_hash}/audio",
        headers=headers,
    )
    assert audio.status_code == 200
    assert audio.headers["cache-control"] == "no-store"
    saved = client.post(
        f"{API_PREFIX}/review-items/{item_hash}/review",
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 0.0,
            "incorrect_interval_count": 0,
            "annotation_fidelity": "consistent",
            "root_cause": "no_defect_confirmed",
            "resolution": "retained_no_defect",
            "notes": "",
        },
    )
    assert saved.status_code == 200
    assert "expert-a" not in saved.text


def test_i30_gui_uses_phone_fields_and_rejects_t43_fields(tmp_path: Path) -> None:
    dataset, _, _ = _write_dataset(
        tmp_path,
        count=30,
        balanced_error_presence=True,
    )
    store = G04AmendedReviewStore(
        path=tmp_path / "i30-state.json",
        dataset=dataset,
        profile=G04ReviewProfile.I30,
        selected_item_hashes=[item.item_sha256 for item in dataset.items],
        selection_source_sha256="5" * 64,
    )
    app = create_amended_review_app(
        store=store,
        reviewer_id="expert-i30",
        access_token="synthetic-review-token",
    )
    client = TestClient(app)
    headers = {"X-Review-Token": "synthetic-review-token"}
    session = client.get(f"{API_PREFIX}/review-session", headers=headers)
    assert session.json()["profile"] == "i30_phone_accuracy"
    assert session.json()["expected_count"] == 30

    item_hash = store.items[0].item_sha256
    saved = client.post(
        f"{API_PREFIX}/review-items/{item_hash}/review",
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 0.0,
            "incorrect_interval_count": 0,
            "annotation_fidelity": None,
            "root_cause": None,
            "resolution": None,
            "notes": "",
        },
    )
    assert saved.status_code == 200
    invalid = client.put(
        f"{API_PREFIX}/review-items/{item_hash}/review",
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 0.0,
            "incorrect_interval_count": 0,
            "annotation_fidelity": "consistent",
            "root_cause": "no_defect_confirmed",
            "resolution": "retained_no_defect",
            "notes": "",
        },
    )
    assert invalid.status_code == 422


def test_gui_fails_closed_when_frontend_bundle_is_missing(tmp_path: Path) -> None:
    dataset, _, manifest = _t43_fixture(tmp_path / "dataset")
    store = G04AmendedReviewStore(
        path=tmp_path / "state.json",
        dataset=dataset,
        profile=G04ReviewProfile.T43,
        selected_item_hashes=selected_hashes_from_t43_manifest(manifest),
        selection_source_sha256="6" * 64,
    )
    assert FRONTEND_ROOT.is_dir()
    with pytest.raises(FileNotFoundError, match="frontend is incomplete"):
        create_amended_review_app(
            store=store,
            reviewer_id="expert-a",
            access_token="synthetic-review-token",
            frontend_root=tmp_path / "missing-frontend",
        )
