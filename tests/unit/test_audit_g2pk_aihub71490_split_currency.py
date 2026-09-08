from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "audit_g2pk_aihub71490_split_currency.py"
)
SPEC = importlib.util.spec_from_file_location(
    "audit_g2pk_aihub71490_split_currency",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _selection_digest(
    rows: list[dict[str, str]],
    *,
    sample_size: int,
) -> str:
    selected = sorted(
        (
            MODULE._selection_score(
                seed=MODULE.FIXED_SEED,
                row_identity=row["id"],
                target_reference=row["json_path"],
            )
            for row in rows
            if row["split"] == "dev"
        )
    )[:sample_size]
    digest = hashlib.sha256()
    for score in selected:
        MODULE._update_framed(digest, score)
    return digest.hexdigest()


def _write_fixture(
    tmp_path: Path,
    *,
    duplicate_current_identity: bool = False,
) -> tuple[ModuleType, Any, list[dict[str, str]]]:
    rows = [
        {
            "schema_version": MODULE.CURRENT_MANIFEST_SCHEMA_VERSION,
            "id": f"private-row-{index:02d}",
            "split": "dev",
            "json_path": f"private/label-{index:02d}.json",
        }
        for index in range(20)
    ]
    current_rows = [
        {
            **row,
            "split": "train",
        }
        for row in rows
    ]
    if duplicate_current_identity:
        current_rows[-1]["id"] = current_rows[0]["id"]
    current_manifest = (tmp_path / "current.parquet").resolve()
    pq.write_table(
        pa.Table.from_pylist(current_rows),
        current_manifest,
        compression="NONE",
    )
    legacy_manifest = (tmp_path / "legacy.jsonl").resolve()
    legacy_payloads = []
    for row in rows:
        legacy_payloads.append(
            {
                "schema_version": MODULE.LEGACY_MANIFEST_SCHEMA_VERSION,
                "dataset_id": MODULE.EXPECTED_DATASET_ID,
                "id": row["id"],
                "split": row["split"],
                "source_paths": {"json": row["json_path"]},
                "phone_sources": {
                    "target": {
                        "path": row["json_path"],
                        "format": "aihub_pronunciation_json",
                        "json_pointer": "/RecordingMetadata/phonemic/phones",
                    }
                },
            }
        )
    legacy_manifest.write_text(
        "".join(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
            for payload in legacy_payloads
        ),
        encoding="utf-8",
    )
    expected_selection = _selection_digest(
        rows,
        sample_size=MODULE.FIXED_SAMPLE_SIZE,
    )
    legacy_artifact = (tmp_path / "legacy-artifact.json").resolve()
    legacy_artifact.write_text(
        json.dumps(
            {
                "schema_version": MODULE.LEGACY_ARTIFACT_SCHEMA_VERSION,
                "scope": {
                    "split": "dev",
                    "development_only": True,
                },
                "provenance": {
                    "sample_selection_sha256": expected_selection,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = (tmp_path / "audit.json").resolve()
    configuration = MODULE.SplitCurrencyAuditConfiguration(
        legacy_manifest_path=legacy_manifest,
        expected_legacy_manifest_sha256=_sha256(legacy_manifest),
        current_manifest_path=current_manifest,
        expected_current_manifest_sha256=_sha256(current_manifest),
        legacy_artifact_path=legacy_artifact,
        expected_legacy_artifact_sha256=_sha256(legacy_artifact),
        expected_audit_runner_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["audit_runner"]
        ),
        expected_legacy_smoke_runner_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["legacy_smoke_runner"]
        ),
        expected_modeling_manifest_builder_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["modeling_manifest_builder"]
        ),
        expected_scorer_environment_lock_sha256=_sha256(
            MODULE._IMPLEMENTATION_PATHS["scorer_environment_lock"]
        ),
        output_path=output,
        seed=MODULE.FIXED_SEED,
        sample_size=MODULE.FIXED_SAMPLE_SIZE,
        expected_legacy_selection_sha256=expected_selection,
    )
    return MODULE, configuration, rows


def test_complete_audit_proves_join_confusion_and_zero_corpus_access(
    tmp_path: Path,
) -> None:
    module, configuration, rows = _write_fixture(tmp_path)

    report = module.run_split_currency_audit(configuration)

    assert report["status"] == "split_currency_audit_complete"
    join = report["join_audit"]
    assert join["legacy_row_count"] == 20
    assert join["current_row_count"] == 20
    assert join["legacy_unique_identity_count"] == 20
    assert join["current_unique_identity_count"] == 20
    assert join["joined_row_count"] == 20
    assert join["structural_error_count"] == 0
    assert join["legacy_to_current_confusion_matrix"]["dev"]["train"] == 20
    replay = report["legacy_selection_replay"]
    assert replay["selection_evidence_matches"] is True
    assert replay["selected_current_split_counts"]["train"] == 16
    assert replay["selected_current_non_dev_count"] == 16
    assert (
        report["legacy_evidence_disposition"]["disposition"]
        == "superseded_current_split_drift"
    )
    access = report["source_access_audit"]
    assert access["dataset_root_argument_supported"] is False
    assert access["expected_label_json_file_access_count"] == 0
    assert access["audio_file_access_count"] == 0
    assert access["textgrid_file_access_count"] == 0

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for row in rows:
        assert row["id"] not in serialized
        assert row["json_path"] not in serialized


def test_duplicate_current_identity_makes_audit_incomplete(
    tmp_path: Path,
) -> None:
    module, configuration, _ = _write_fixture(
        tmp_path,
        duplicate_current_identity=True,
    )

    report = module.run_split_currency_audit(configuration)

    assert report["status"] == "split_currency_audit_incomplete"
    assert report["join_audit"]["structural_error_count"] > 0
    assert report["join_audit"]["current_duplicate_identity_count"] == 1


def test_input_digest_mismatch_fails_before_metadata_scan(
    tmp_path: Path,
) -> None:
    module, configuration, _ = _write_fixture(tmp_path)
    invalid = module.SplitCurrencyAuditConfiguration(
        **{
            **{
                field: getattr(configuration, field)
                for field in configuration.__dataclass_fields__
            },
            "expected_current_manifest_sha256": "0" * 64,
        }
    )

    with pytest.raises(module.SplitCurrencyAuditError, match="input digest"):
        module.run_split_currency_audit(invalid)


def test_atomic_writer_uses_private_mode_and_round_trips(
    tmp_path: Path,
) -> None:
    module, configuration, _ = _write_fixture(tmp_path)
    report = module.run_split_currency_audit(configuration)

    module._write_json_atomic(configuration.output_path, report)

    assert json.loads(configuration.output_path.read_text(encoding="utf-8")) == report
    assert os.stat(configuration.output_path).st_mode & 0o777 == 0o600
