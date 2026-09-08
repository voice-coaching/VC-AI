"""Load frozen thresholds from a calibration artifact.

No artifact means no calibrated profile: the loader raises rather than invent
defaults.  The one exception is the explicitly named ``uncalibrated`` profile,
which returns a marked set that the gate refuses to coach from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..domain import FailureCode
from ..errors import BackendUnavailableError
from ..thresholds import UNCALIBRATED_PROFILE, ThresholdSet

DEFAULT_THRESHOLD_DIR = Path("configs/pronunciation_feedback/thresholds")
_FLOAT_FIELDS = (
    "min_content_score",
    "min_alignment_confidence",
    "min_s1_confidence",
    "min_s2_confidence",
    "min_issue_confidence",
    "min_issue_severity",
)


def _fail(detail: str) -> BackendUnavailableError:
    return BackendUnavailableError(FailureCode.THRESHOLDS_UNAVAILABLE, detail)


def _optional_float(record: Mapping[str, Any], field_name: str) -> float | None:
    value = record.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{field_name} must be a number")
    return float(value)


class JsonThresholdStore:
    """``ThresholdSetPort`` implementation over ``<profile>.json`` artifacts."""

    def __init__(
        self,
        threshold_dir: str | Path = DEFAULT_THRESHOLD_DIR,
        *,
        uncalibrated_cohort: str = "unspecified",
    ) -> None:
        self._threshold_dir = Path(threshold_dir)
        self._uncalibrated_cohort = uncalibrated_cohort

    def load(self, analysis_profile: str) -> ThresholdSet:
        if not analysis_profile:
            raise _fail("analysis_profile must not be empty")
        if analysis_profile == UNCALIBRATED_PROFILE:
            return ThresholdSet.uncalibrated(cohort=self._uncalibrated_cohort)
        path = self._threshold_dir / f"{analysis_profile}.json"
        if not path.is_file():
            raise _fail(
                f"no calibration artifact for profile {analysis_profile!r}"
            )
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(f"threshold artifact is not valid JSON: {path.name}") from exc
        if not isinstance(record, Mapping):
            raise _fail("threshold artifact must be an object")
        declared_profile = record.get("analysis_profile")
        if declared_profile != analysis_profile:
            raise _fail(
                "threshold artifact profile mismatch: "
                f"{declared_profile!r} != {analysis_profile!r}"
            )
        required_branches = record.get("required_branches", [])
        if not isinstance(required_branches, list) or not all(
            isinstance(item, str) and item for item in required_branches
        ):
            raise _fail("required_branches must be an array of names")
        try:
            return ThresholdSet(
                threshold_set_id=str(record.get("threshold_set_id") or ""),
                analysis_profile=analysis_profile,
                calibrated=bool(record.get("calibrated", True)),
                cohort=str(record.get("cohort") or ""),
                frozen_at=record.get("frozen_at"),
                dataset_id=record.get("dataset_id"),
                manifest_sha256=record.get("manifest_sha256"),
                required_branches=tuple(required_branches),
                **{name: _optional_float(record, name) for name in _FLOAT_FIELDS},
            )
        except ValueError as exc:
            raise _fail(f"invalid threshold artifact {path.name}: {exc}") from exc
