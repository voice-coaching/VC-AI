import json
from pathlib import Path
import shutil

import pytest

from voice_coach.coaching.ontology import (
    OntologyValidationError,
    validate_ontology_bundle,
    write_immutable_report,
)


WORKSPACE = Path(__file__).resolve().parents[2]
CATALOG = "configs/coaching/korean_error_advice_ontology_v1.json"
SCHEMA = "configs/coaching/korean_error_advice_ontology_v1.schema.json"
AGGREGATE = "data/processed/aihub71490/manifests/aihub71490_data_card.json"


def test_real_aggregate_bundle_validates_deterministically_without_row_access() -> None:
    first = validate_ontology_bundle(
        workspace_root=WORKSPACE,
        catalog_relative_path=CATALOG,
        schema_relative_path=SCHEMA,
    )
    second = validate_ontology_bundle(
        workspace_root=WORKSPACE,
        catalog_relative_path=CATALOG,
        schema_relative_path=SCHEMA,
    )

    assert first == second
    assert first["status"] == "valid"
    assert first["observed_support"]["tag_count"] == 17
    assert first["observed_support"]["error_interval_count"] == 176_523
    assert sum(first["observed_support"]["per_tag"].values()) == 176_523
    assert first["observed_support"]["unknown_error_tag_count"] == 0
    assert first["data_access"] == {
        "privacy_safe_aggregate_files_read": 1,
        "row_level_files_read": 0,
        "raw_files_read": 0,
        "locator_files_read": 0,
        "environment_files_read": 0,
        "identifier_values_read": 0,
    }


def test_bundle_does_not_claim_empirical_phone_mapping_or_human_completion() -> None:
    report = validate_ontology_bundle(
        workspace_root=WORKSPACE,
        catalog_relative_path=CATALOG,
        schema_relative_path=SCHEMA,
    )

    assert (
        report["target_phone_mapping"]["tag_by_target_phone_joint_counts_available"]
        is False
    )
    assert report["advice_boundary"]["production_release_allowed"] is False
    assert report["advice_boundary"]["c03_completed"] is False
    assert report["completion"]["c01_research_completion_claimed"] is False
    assert report["completion"]["blocking_evidence"] == [
        "privacy_safe_tag_by_target_phone_joint_counts",
        "c03_expert_review_and_safety_audit",
        "s107_calibrated_confidence_threshold",
    ]


def test_aggregate_sha_drift_fails_closed(tmp_path: Path) -> None:
    for relative in (CATALOG, SCHEMA, AGGREGATE):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WORKSPACE / relative, destination)
    aggregate_path = tmp_path / AGGREGATE
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    aggregate["error_interval_count"] += 1
    aggregate_path.write_text(
        json.dumps(aggregate, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(OntologyValidationError, match="source pins|SHA-256"):
        validate_ontology_bundle(
            workspace_root=tmp_path,
            catalog_relative_path=CATALOG,
            schema_relative_path=SCHEMA,
        )


def test_catalog_rejects_non_aggregate_or_protected_source(tmp_path: Path) -> None:
    for relative in (CATALOG, SCHEMA):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WORKSPACE / relative, destination)
    catalog_path = tmp_path / CATALOG
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["source"]["aggregate_report_path"] = "data/raw/labels.json"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(OntologyValidationError):
        validate_ontology_bundle(
            workspace_root=tmp_path,
            catalog_relative_path=CATALOG,
            schema_relative_path=SCHEMA,
        )


def test_catalog_tag_support_drift_fails_closed(tmp_path: Path) -> None:
    for relative in (CATALOG, SCHEMA, AGGREGATE):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(WORKSPACE / relative, destination)
    catalog_path = tmp_path / CATALOG
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["tags"][0]["observed_interval_count"] += 1
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(OntologyValidationError, match="support mismatch"):
        validate_ontology_bundle(
            workspace_root=tmp_path,
            catalog_relative_path=CATALOG,
            schema_relative_path=SCHEMA,
        )


def test_report_writer_is_idempotent_and_refuses_different_overwrite(
    tmp_path: Path,
) -> None:
    relative = "artifacts/coaching/validation.json"
    first = write_immutable_report(
        workspace_root=tmp_path,
        output_relative_path=relative,
        report={"status": "valid"},
    )
    second = write_immutable_report(
        workspace_root=tmp_path,
        output_relative_path=relative,
        report={"status": "valid"},
    )

    assert first == second
    with pytest.raises(OntologyValidationError, match="overwrite"):
        write_immutable_report(
            workspace_root=tmp_path,
            output_relative_path=relative,
            report={"status": "invalid"},
        )


def test_other_is_the_only_catalog_tag_without_draft_advice() -> None:
    catalog = json.loads((WORKSPACE / CATALOG).read_text(encoding="utf-8"))
    abstentions = [
        item for item in catalog["tags"] if item["advice"]["advice_id"] is None
    ]

    assert [item["id"] for item in abstentions] == ["other"]
    assert abstentions[0]["advice"]["action"] == "abstain_expert_review"
    assert all(
        item["empirical_target_phone_support"] is None for item in catalog["tags"]
    )
