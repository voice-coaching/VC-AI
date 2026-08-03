from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from voice_coach.reproducibility import (
    s104_s105_b07_paper_amendment as amendment,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPOSITORY_ROOT / "configs/experiments/s104_s105_b07_paper_amendment_v1.yaml"


def test_config_only_is_zero_access_and_preserves_pending_contract() -> None:
    result = amendment.validate_config_only(MANIFEST)

    assert result == {
        "status": "valid",
        "mode": "config_only",
        "actual_metric_values_read": 0,
        "actual_artifact_or_registry_files_read": 0,
        "actual_canonical_files_read": 0,
        "actual_outputs_written": 0,
        "pending_marker_count": 18,
        "pending_markers_sha256": amendment.CURRENT_PENDING_MARKERS_SHA256,
    }


def test_prepare_rejects_blocked_manifest_before_runner_or_output(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("called")
        return {"status": "valid"}

    output = tmp_path / "artifacts/reproducibility/blocked"
    with pytest.raises(
        amendment.PaperAmendmentError,
        match="manifest_not_ready",
    ):
        amendment.prepare_paper_amendment(
            manifest_path=MANIFEST,
            workspace_root=REPOSITORY_ROOT,
            output_root=output,
            validator_runner=runner,
        )

    assert calls == []
    assert not output.exists()


def test_config_only_rejects_pending_hash_drift(tmp_path: Path) -> None:
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    document["contract"]["pending_markers_sha256"] = "0" * 64
    candidate = tmp_path / "manifest.yaml"
    candidate.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(
        amendment.PaperAmendmentError,
        match="contract_value",
    ):
        amendment.validate_config_only(candidate)


def test_null_projection_is_literal_na_and_never_zero() -> None:
    assert amendment._paper_value(None, code="fixture") == "NA"  # noqa: SLF001
    claims = amendment._MarkdownClaims()  # noqa: SLF001
    cell = claims.cell("fixture_null", None)

    assert cell == "NA <!-- evidence:fixture_null -->"
    assert claims.values["fixture_null"] == "NA"
    assert claims.values["fixture_null"] != 0
    assert claims.specs["fixture_null"]["comparison"] == "exact"
