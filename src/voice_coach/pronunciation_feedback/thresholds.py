"""Frozen decision thresholds and their calibration provenance.

Thresholds are data, not code constants: a gate may only fire against a
:class:`ThresholdSet` that names the dataset, split, and cohort it was
calibrated on.  A profile with no artifact is representable, but it is marked
``calibrated=False`` and the gate refuses to emit coaching for it.
"""

from __future__ import annotations

from dataclasses import dataclass

UNCALIBRATED_PROFILE = "uncalibrated"

#: Track E (autonomous): thresholds frozen from the calibration split by a rule
#: fixed before evaluation.  Results may report metrics, never validated quality.
ENGINEERING_PROVISIONAL = "engineering_provisional"
#: Track R (human-gated): preregistered thresholds, expert gold, locked test.
RESEARCH_VALIDATED = "research_validated"
CLAIM_SCOPES = (ENGINEERING_PROVISIONAL, RESEARCH_VALIDATED)


@dataclass(frozen=True, slots=True)
class ThresholdSet:
    threshold_set_id: str
    analysis_profile: str
    calibrated: bool
    cohort: str
    frozen_at: str | None = None
    dataset_id: str | None = None
    manifest_sha256: str | None = None
    min_content_score: float | None = None
    min_alignment_confidence: float | None = None
    min_s1_confidence: float | None = None
    min_s2_confidence: float | None = None
    min_issue_confidence: float | None = None
    min_issue_severity: float | None = None
    required_branches: tuple[str, ...] = ()
    claim_scope: str = ENGINEERING_PROVISIONAL
    human_gate_receipt: str | None = None

    def __post_init__(self) -> None:
        if not self.threshold_set_id:
            raise ValueError("threshold_set_id must not be empty")
        if not self.cohort:
            raise ValueError("cohort must be named, including for uncalibrated sets")
        if self.claim_scope not in CLAIM_SCOPES:
            raise ValueError(f"claim_scope must be one of {CLAIM_SCOPES}")
        if self.claim_scope == RESEARCH_VALIDATED and not self.human_gate_receipt:
            # Track R cannot be entered by recomputation: it needs the receipt of
            # a human gate (preregistration approval, expert gold, locked run).
            raise ValueError(
                "research_validated requires a human_gate_receipt identifier"
            )
        for name in (
            "min_content_score",
            "min_alignment_confidence",
            "min_s1_confidence",
            "min_s2_confidence",
            "min_issue_confidence",
            "min_issue_severity",
        ):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.calibrated:
            missing = [
                name
                for name in ("frozen_at", "dataset_id", "manifest_sha256")
                if getattr(self, name) in (None, "")
            ]
            if missing:
                raise ValueError(
                    "calibrated threshold sets require provenance: "
                    + ", ".join(missing)
                )
            required = (
                "min_content_score",
                "min_alignment_confidence",
                "min_s1_confidence",
                "min_s2_confidence",
                "min_issue_confidence",
            )
            unset = [name for name in required if getattr(self, name) is None]
            if unset:
                raise ValueError(
                    "calibrated threshold sets require thresholds: " + ", ".join(unset)
                )
            if not self.required_branches:
                raise ValueError(
                    "calibrated threshold sets must declare required_branches"
                )

    @classmethod
    def uncalibrated(cls, cohort: str = "unspecified") -> ThresholdSet:
        """A runnable-but-unverified profile.

        Used to exercise the pipeline before any calibration artifact exists.
        The gate treats it as never coaching-eligible.
        """

        return cls(
            threshold_set_id="uncalibrated-no-artifact",
            analysis_profile=UNCALIBRATED_PROFILE,
            calibrated=False,
            cohort=cohort,
        )
