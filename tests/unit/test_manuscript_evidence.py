from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from voice_coach.reproducibility.evidence import (
    EvidenceValidationError,
    validate_evidence_map,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pending_sha(markers: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(markers)).encode()).hexdigest()


def _fixture(
    root: Path,
    *,
    accessed: bool = False,
    manuscript_metric: str = "",
    registry_split: str = "dev",
) -> Path:
    (root / "docs").mkdir()
    (root / "artifacts" / "phone").mkdir(parents=True)
    summary = root / "artifacts" / "summary.json"
    summary.write_text(
        json.dumps(
            {"records": 10, "excluded": 1, "score": 0.12345, "accessed": accessed}
        ),
        encoding="utf-8",
    )
    registry = root / "artifacts" / "registry.json"
    registry.write_text(
        json.dumps({"metrics": [{"name": "macro_f1", "split": registry_split}]}),
        encoding="utf-8",
    )
    phone = root / "artifacts" / "phone" / "part-000.parquet"
    pq.write_table(
        pa.table({"binary_error_label": [0, 0, 1, 0]}),
        phone,
    )
    marker = "[PENDING: future evaluation]"
    manuscript = root / "docs" / "paper.md"
    manuscript.write_text(
        "\n".join(
            [
                "총 10건이며 점수는 0.1235다.",
                "제외 뒤 남은 수는 9건이다.",
                "오류 행은 1건(25.0%)이다.",
                marker,
                manuscript_metric,
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "schema_version": 1,
        "manuscript": "docs/paper.md",
        "artifacts": {
            "summary": {"path": "artifacts/summary.json", "sha256": _sha(summary)},
            "registry": {"path": "artifacts/registry.json", "sha256": _sha(registry)},
            "phone": {
                "path": "artifacts/phone/part-000.parquet",
                "sha256": _sha(phone),
            },
        },
        "claims": [
            {
                "id": "records",
                "source": {
                    "artifact": "summary",
                    "kind": "json_pointer",
                    "pointer": "/records",
                },
                "document": {"regex": r"총 (?P<value>[\d,]+)건"},
                "comparison": {"type": "numeric", "decimals": 0},
            },
            {
                "id": "score",
                "source": {
                    "artifact": "summary",
                    "kind": "json_pointer",
                    "pointer": "/score",
                },
                "document": {"regex": r"점수는 (?P<value>[\d.]+)"},
                "comparison": {"type": "numeric", "decimals": 4},
            },
            {
                "id": "remaining",
                "source": {
                    "artifact": "summary",
                    "kind": "json_arithmetic",
                    "operation": "subtract",
                    "pointers": ["/records", "/excluded"],
                },
                "document": {"regex": r"남은 수는 (?P<value>[\d,]+)건"},
                "comparison": {"type": "numeric", "decimals": 0},
            },
            {
                "id": "phone_error_count",
                "source": {
                    "artifact": "phone",
                    "kind": "parquet_metadata",
                    "aggregate": "count_equal",
                    "column": "binary_error_label",
                    "equals": 1,
                },
                "document": {"regex": r"오류 행은 (?P<value>[\d,]+)건"},
                "comparison": {"type": "numeric", "decimals": 0},
            },
            {
                "id": "phone_error_ratio",
                "source": {
                    "artifact": "phone",
                    "kind": "parquet_metadata",
                    "aggregate": "ratio_percent",
                    "column": "binary_error_label",
                    "equals": 1,
                },
                "document": {"regex": r"오류 행은 [\d,]+건\((?P<value>[\d.]+)%\)"},
                "comparison": {"type": "numeric", "decimals": 1},
            },
        ],
        "pending_contract": {
            "marker_regex": r"\[PENDING:[^\]]+\]",
            "expected_count": 1,
            "expected_markers_sha256": _pending_sha([marker]),
            "forbidden_numeric_regexes": [r"future result\s*[:=]\s*[\d.]+"],
        },
        "locked_test_guard": {
            "access": {
                "artifact": "summary",
                "pointer": "/accessed",
            },
            "registry": {
                "artifact": "registry",
                "array_pointer": "/metrics",
                "split_field": "split",
                "forbidden_splits": ["locked_test"],
            },
            "forbidden_document_regexes": [
                r"locked[- ]test[^\n]*(?:F1|AUPRC)[^\n]*\d+\.\d+"
            ],
        },
    }
    map_path = root / "docs" / "evidence.yaml"
    map_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return map_path


def test_validates_json_rounding_parquet_pending_and_locked_test(
    tmp_path: Path,
) -> None:
    evidence_map = _fixture(tmp_path)

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "passed"
    assert report["summary"] == {
        "artifact_count": 3,
        "artifact_passed": 3,
        "claim_count": 5,
        "claim_passed": 5,
    }
    assert report["privacy"]["raw_data_read"] is False
    assert report["pending_contract"]["status"] == "passed"
    assert report["locked_test_guard"]["registry_metric_splits"] == ["dev"]


def test_fails_when_artifact_hash_changes(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    summary = tmp_path / "artifacts" / "summary.json"
    summary.write_text(
        '{"records": 11, "excluded": 1, "score": 0.12345, "accessed": false}',
        encoding="utf-8",
    )

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    artifact = next(item for item in report["artifacts"] if item["alias"] == "summary")
    assert artifact["error"] == "artifact SHA-256 changed"


def test_fails_when_artifact_is_missing(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    (tmp_path / "artifacts" / "registry.json").unlink()

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    assert report["summary"]["artifact_passed"] == 2


def test_fails_on_claim_mismatch(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    manuscript = tmp_path / "docs" / "paper.md"
    manuscript.write_text(
        manuscript.read_text().replace("총 10건", "총 11건"), encoding="utf-8"
    )

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    claim = next(item for item in report["claims"] if item["id"] == "records")
    assert claim["status"] == "failed"


def test_fails_on_nonfinite_json_value(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    config = yaml.safe_load(evidence_map.read_text())
    summary = tmp_path / "artifacts" / "summary.json"
    summary.write_text(
        '{"records": 10, "excluded": 1, "score": NaN, "accessed": false}',
        encoding="utf-8",
    )
    config["artifacts"]["summary"]["sha256"] = _sha(summary)
    evidence_map.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    score = next(item for item in report["claims"] if item["id"] == "score")
    assert "non-finite" in score["error"]


@pytest.mark.parametrize(
    ("accessed", "metric", "registry_split"),
    [
        (True, "", "dev"),
        (False, "locked-test macro F1 = 0.75", "dev"),
        (False, "", "locked_test"),
    ],
)
def test_fails_for_forbidden_locked_test_access_or_claim(
    tmp_path: Path,
    accessed: bool,
    metric: str,
    registry_split: str,
) -> None:
    evidence_map = _fixture(
        tmp_path,
        accessed=accessed,
        manuscript_metric=metric,
        registry_split=registry_split,
    )

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    assert report["locked_test_guard"]["status"] == "failed"


def test_fails_when_unresolved_marker_is_replaced_by_number(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    manuscript = tmp_path / "docs" / "paper.md"
    manuscript.write_text(
        manuscript.read_text().replace(
            "[PENDING: future evaluation]", "future result = 0.8"
        ),
        encoding="utf-8",
    )

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "failed"
    assert report["pending_contract"]["status"] == "failed"
    assert len(report["pending_contract"]["errors"]) == 3


def test_supports_multiple_locked_test_reports_and_registries(tmp_path: Path) -> None:
    evidence_map = _fixture(tmp_path)
    config = yaml.safe_load(evidence_map.read_text())
    registry_path = tmp_path / "artifacts" / "registry.json"
    registry_value = json.loads(registry_path.read_text())
    registry_value["other_metrics"] = [{"name": "macro_f1", "split": "inner_val"}]
    registry_path.write_text(json.dumps(registry_value), encoding="utf-8")
    config["artifacts"]["registry"]["sha256"] = _sha(registry_path)
    config["locked_test_guard"] = {
        "access_checks": [
            {"artifact": "summary", "pointer": "/accessed"},
            {"artifact": "summary", "pointer": "/accessed"},
        ],
        "registries": [
            {
                "artifact": "registry",
                "array_pointer": "/other_metrics",
                "split_field": "split",
                "forbidden_splits": ["locked_test"],
            },
            {
                "artifact": "registry",
                "array_pointer": "/metrics",
                "split_field": "split",
                "forbidden_splits": ["locked_test"],
            },
        ],
        "forbidden_document_regexes": [
            r"locked[- ]test[^\n]*(?:F1|AUPRC)[^\n]*\d+\.\d+"
        ],
    }
    evidence_map.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    report = validate_evidence_map(evidence_map, workspace_root=tmp_path)

    assert report["status"] == "passed"
    assert report["locked_test_guard"]["accessed"] == {
        "summary": False,
        "summary:1": False,
    }
    assert report["locked_test_guard"]["registry_metric_splits"] == ["dev", "inner_val"]


@pytest.mark.parametrize(
    "forbidden", [".env.local", "data/raw/a.json", "data/restricted/a.json"]
)
def test_rejects_forbidden_artifact_paths(tmp_path: Path, forbidden: str) -> None:
    evidence_map = _fixture(tmp_path)
    config = yaml.safe_load(evidence_map.read_text())
    config["artifacts"]["summary"]["path"] = forbidden
    evidence_map.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(EvidenceValidationError, match="forbidden by privacy policy"):
        validate_evidence_map(evidence_map, workspace_root=tmp_path)


def test_rejects_evidence_map_in_environment_file_without_reading_it(
    tmp_path: Path,
) -> None:
    forbidden_map = tmp_path / ".env.evidence"
    forbidden_map.write_text("SECRET=must-not-be-read", encoding="utf-8")

    with pytest.raises(EvidenceValidationError, match="forbidden by privacy policy"):
        validate_evidence_map(forbidden_map, workspace_root=tmp_path)
