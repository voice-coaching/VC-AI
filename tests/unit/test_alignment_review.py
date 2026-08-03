from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from fastapi.testclient import TestClient
import pytest
import soundfile as sf

from voice_coach.evaluation.alignment_review import (
    ReviewDataset,
    ReviewStore,
    ReviewSubmission,
    create_review_app,
    export_completed_reviews,
    hash_item_id,
    hash_reviewer_id,
)


SCRIPT = Path(__file__).parents[2] / "scripts" / "review_alignment_audit.py"
SCORER = Path(__file__).parents[2] / "scripts" / "score_alignment_audit.py"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dataset_root = tmp_path / "restricted"
    dataset_root.mkdir()
    samples = np.zeros(16_000, dtype=np.float32)
    samples[1_600:3_200] = 0.2
    sf.write(dataset_root / "one.wav", samples, 16_000)
    sf.write(dataset_root / "two.wav", samples, 16_000)

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
        "json_path",
        "textgrid_path",
        "alignment_detail_line",
        "reviewer_id",
        "alignment_correct",
        "maximum_boundary_error_ms",
        "notes",
    ]
    worksheet = tmp_path / "worksheet.tsv"
    with worksheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for index, raw_id in enumerate(
            ["00001-private-file-stem", "00002-private-file-stem"],
            start=1,
        ):
            writer.writerow(
                {
                    "audit_index": index,
                    "utterance_id": raw_id,
                    "split": "train" if index == 1 else "locked_test",
                    "l1": "Chinese",
                    "gender": "F",
                    "proficiency": "Intermediate",
                    "error_presence": "false",
                    "duration_seconds": "1.0",
                    "target_phone_count": 1,
                    "realized_phone_count": 1,
                    "audio_path": "one.wav" if index == 1 else "two.wav",
                    "json_path": f"private-{index}.json",
                    "textgrid_path": f"private-{index}.TextGrid",
                    "alignment_detail_line": index,
                    "reviewer_id": "",
                    "alignment_correct": "",
                    "maximum_boundary_error_ms": "",
                    "notes": "",
                }
            )
    details = tmp_path / "details.jsonl"
    details.write_text(
        "".join(
            json.dumps(
                {
                    "audit_index": index,
                    "utterance_id": raw_id,
                    "target_phone_intervals": [
                        {"start": 0.1, "end": 0.2, "label": "ㄱ"}
                    ],
                    "realized_phone_intervals": [
                        {"start": 0.1, "end": 0.2, "label": "ㄱ"}
                    ],
                    "automatic_phone_alignments": [
                        {
                            "target_label": "ㄱ",
                            "realized_label": "ㄱ",
                            "start": 0.1,
                            "end": 0.2,
                            "operation": "match",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n"
            for index, raw_id in enumerate(
                ["00001-private-file-stem", "00002-private-file-stem"],
                start=1,
            )
        ),
        encoding="utf-8",
    )
    return dataset_root, worksheet, details


def _dataset(tmp_path: Path) -> tuple[ReviewDataset, ReviewStore]:
    root, worksheet, details = _write_fixture(tmp_path)
    dataset = ReviewDataset.load(
        worksheet=worksheet,
        details=details,
        dataset_root=root,
    )
    return dataset, ReviewStore(tmp_path / "state.json", dataset)


def _client(
    dataset: ReviewDataset,
    store: ReviewStore,
) -> tuple[TestClient, dict[str, str], str]:
    token = "synthetic-access-token-123"
    reviewer_hash = hash_reviewer_id("synthetic-reviewer")
    app = create_review_app(
        dataset=dataset,
        store=store,
        reviewer_id_sha256=reviewer_hash,
        access_token=token,
    )
    return TestClient(app), {"X-Review-Token": token}, reviewer_hash


def test_browser_payload_uses_hashes_and_streams_only_selected_audio(
    tmp_path: Path,
) -> None:
    dataset, store = _dataset(tmp_path)
    client, headers, reviewer_hash = _client(dataset, store)
    raw_ids = [item.raw_utterance_id for item in dataset.items]

    shell = client.get("/")
    assert shell.status_code == 200
    assert all(raw_id not in shell.text for raw_id in raw_ids)
    assert "synthetic-access-token-123" not in shell.text
    assert client.get("/api/status").status_code == 403
    status = client.get("/api/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["reviewer_id_sha256"] == reviewer_hash
    assert status.json()["completed_count"] == 0

    response = client.get("/api/items", headers=headers)
    serialized = response.text
    assert response.status_code == 200
    assert all(raw_id not in serialized for raw_id in raw_ids)
    assert "one.wav" not in serialized
    assert "private-1.json" not in serialized

    item_hash = dataset.items[0].item_sha256
    detail = client.get(f"/api/items/{item_hash}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["item_sha256"] == item_hash
    assert raw_ids[0] not in detail.text
    assert "audio_path" not in detail.text

    audio = client.get(
        f"/api/items/{item_hash}/audio",
        headers=headers,
    )
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert item_hash in audio.headers["content-disposition"]
    assert raw_ids[0] not in audio.headers["content-disposition"]
    assert client.get("/api/items/not-a-hash/audio", headers=headers).status_code == 404


def test_atomic_review_validation_duplicate_update_and_resume(
    tmp_path: Path,
) -> None:
    dataset, store = _dataset(tmp_path)
    client, headers, reviewer_hash = _client(dataset, store)
    item = dataset.items[0]
    endpoint = f"/api/items/{item.item_sha256}/review"

    assert client.post(
        endpoint,
        headers=headers,
        json={"alignment_correct": True, "maximum_boundary_error_ms": 5.0},
    ).status_code == 422
    assert client.post(
        endpoint,
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 1_001.0,
            "notes": "",
        },
    ).status_code == 422
    assert client.post(
        endpoint,
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 5.0,
            "notes": item.raw_utterance_id,
        },
    ).status_code == 422
    created = client.post(
        endpoint,
        headers=headers,
        json={
            "alignment_correct": True,
            "maximum_boundary_error_ms": 12.5,
            "notes": " synthetic note ",
        },
    )
    assert created.status_code == 200
    assert created.json()["review"]["reviewer_id_sha256"] == reviewer_hash
    assert client.post(
        endpoint,
        headers=headers,
        json={
            "alignment_correct": False,
            "maximum_boundary_error_ms": 20.0,
            "incorrect_interval_count": 1,
            "notes": "duplicate",
        },
    ).status_code == 409
    updated = client.put(
        endpoint,
        headers=headers,
        json={
            "alignment_correct": False,
            "maximum_boundary_error_ms": 20.0,
            "incorrect_interval_count": 1,
            "notes": " corrected ",
        },
    )
    assert updated.status_code == 200

    state_text = store.path.read_text(encoding="utf-8")
    assert item.raw_utterance_id not in state_text
    assert "one.wav" not in state_text
    assert "synthetic-reviewer" not in state_text
    assert reviewer_hash in state_text
    assert not list(tmp_path.glob(".*.part-*"))
    assert store.path.stat().st_mode & 0o777 == 0o600

    resumed = ReviewStore(store.path, dataset)
    assert resumed.status()["completed_count"] == 1
    assert resumed.reviews[item.item_sha256]["alignment_correct"] is False
    assert resumed.reviews[item.item_sha256]["notes"] == "corrected"


def test_incomplete_then_completed_privacy_safe_scorer_export(
    tmp_path: Path,
) -> None:
    dataset, store = _dataset(tmp_path)
    output = tmp_path / "scorer-input.tsv"
    report_output = tmp_path / "aggregate.json"

    incomplete = export_completed_reviews(
        dataset=dataset,
        store=store,
        output=output,
        report_output=report_output,
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["completed_count"] == 0
    assert incomplete["alignment_accuracy"] is None
    assert not output.exists()

    reviewer_hash = hash_reviewer_id("expert-a")
    decisions = [True, False]
    for item, decision in zip(dataset.items, decisions, strict=True):
        store.save_review(
            item_hash=item.item_sha256,
            reviewer_hash=reviewer_hash,
            submission=ReviewSubmission(
                alignment_correct=decision,
                maximum_boundary_error_ms=10.0,
                incorrect_interval_count=0 if decision else 1,
                notes="",
            ),
            replace=False,
        )
    complete = export_completed_reviews(
        dataset=dataset,
        store=store,
        output=output,
        report_output=report_output,
        minimum_accuracy=0.5,
    )
    assert complete["status"] == "passed"
    assert complete["alignment_accuracy"] == 0.5
    with output.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert [row["utterance_id"] for row in rows] == [
        item.item_sha256 for item in dataset.items
    ]
    assert {row["reviewer_id"] for row in rows} == {reviewer_hash}
    export_text = output.read_text(encoding="utf-8")
    assert all(item.raw_utterance_id not in export_text for item in dataset.items)
    assert "one.wav" not in export_text

    scored = subprocess.run(
        [
            sys.executable,
            str(SCORER),
            str(output),
            "--output",
            str(tmp_path / "scored.json"),
            "--expected-samples",
            "2",
            "--minimum-accuracy",
            "0.5",
        ],
        capture_output=True,
        text=True,
    )
    assert scored.returncode == 0, scored.stderr
    assert json.loads((tmp_path / "scored.json").read_text())["status"] == "passed"


def test_dataset_rejects_path_traversal_and_duplicate_items(
    tmp_path: Path,
) -> None:
    root, worksheet, details = _write_fixture(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"not audio")
    text = worksheet.read_text(encoding="utf-8-sig")
    worksheet.write_text(
        text.replace("one.wav", "../outside.wav", 1),
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError, match="escapes dataset root"):
        ReviewDataset.load(
            worksheet=worksheet,
            details=details,
            dataset_root=root,
        )

    root, worksheet, details = _write_fixture(tmp_path / "duplicate")
    text = worksheet.read_text(encoding="utf-8-sig")
    worksheet.write_text(
        text.replace("00002-private-file-stem", "00001-private-file-stem"),
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError, match="duplicate/empty"):
        ReviewDataset.load(
            worksheet=worksheet,
            details=details,
            dataset_root=root,
        )


def test_cli_defaults_to_loopback_and_requires_explicit_remote_bind() -> None:
    spec = importlib.util.spec_from_file_location(
        "review_alignment_audit_cli_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    args = module.parse_args(
        [
            "serve",
            "--worksheet",
            "w.tsv",
            "--details",
            "d.jsonl",
            "--dataset-root",
            "root",
            "--state",
            "state.json",
        ]
    )
    assert args.host == "127.0.0.1"
    assert args.allow_non_loopback is False
    refused = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "serve",
            "--worksheet",
            "missing.tsv",
            "--details",
            "missing.jsonl",
            "--dataset-root",
            "missing-root",
            "--state",
            "missing-state.json",
            "--host",
            "0.0.0.0",
        ],
        capture_output=True,
        text=True,
    )
    assert refused.returncode != 0
    assert "Refusing non-loopback bind" in refused.stderr


def test_namespaced_hashes_are_deterministic_and_domain_separated() -> None:
    assert hash_item_id("same") == hash_item_id("same")
    assert hash_reviewer_id("same") == hash_reviewer_id("same")
    assert hash_item_id("same") != hash_reviewer_id("same")
