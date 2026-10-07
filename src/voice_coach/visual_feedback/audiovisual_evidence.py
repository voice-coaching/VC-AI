"""Same-attempt MFA/PTS join and bounded reference comparison.

No IO, frame interpolation, phoneme recognition, LLM call, or calibration fitting.
The caller provides verified frame extraction and reference catalog identities.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from .reference_distance import (
    DistancePolicy, Trajectory, UnscorableTrajectory, calibrated_score,
    compare_trajectories, reference_similarity,
)


@dataclass(frozen=True)
class PhoneAnchor:
    expected_index: int
    expected_phone: str
    role: str
    context: str
    start_s: float
    end_s: float
    timing_source: str

    def validate(self):
        if (type(self.expected_index) is not int or self.expected_index < 0
                or not self.expected_phone or not self.role or not self.context
                or self.timing_source != "MFA_LOCATION_ONLY"
                or not math.isfinite(self.start_s) or not math.isfinite(self.end_s)
                or not 0 <= self.start_s < self.end_s):
            raise UnscorableTrajectory("ALIGNMENT_AMBIGUOUS")


@dataclass(frozen=True)
class LipFrame:
    pts_s: float
    landmarks: tuple[tuple[float, float], ...]
    features: tuple[float, ...]
    observable: bool


@dataclass(frozen=True)
class VerifiedTimeMap:
    """Maps prepared PCM-local seconds to canonical video presentation time."""
    prepared_pcm_sha256: str
    video_sha256: str
    scale: float
    offset_s: float
    provenance: str

    def require(self, pcm: str, video: str):
        if (self.prepared_pcm_sha256 != pcm or self.video_sha256 != video
                or not self.provenance or not math.isfinite(self.scale) or self.scale <= 0
                or not math.isfinite(self.offset_s)):
            raise UnscorableTrajectory("SYNC_UNCERTAIN")


@dataclass(frozen=True)
class LipReference:
    reference_id: str
    video_sha256: str
    expected_phone: str
    role: str
    context: str
    mapping_revision: str
    trajectory: Trajectory
    expert_review_id: str


@dataclass(frozen=True)
class Calibration:
    revision: str
    validation_receipt: str
    expected_phone: str
    role: str
    context: str
    mapping_revision: str
    distance_policy_revision: str
    reference_ids: tuple[str, ...]
    # Supply one validated score mapping, not two competing numeric meanings.
    tau: float | None = None
    knots: tuple[tuple[float, float], ...] = ()

    def score(self, distance, anchor, mapping, policy, references):
        if (not self.revision or not self.validation_receipt
                or (self.expected_phone, self.role, self.context) !=
                (anchor.expected_phone, anchor.role, anchor.context)
                or self.mapping_revision != mapping or self.distance_policy_revision != policy
                or sorted(self.reference_ids) != sorted(references)
                or (self.tau is None) == (not self.knots)):
            raise UnscorableTrajectory("CALIBRATION_MISSING")
        if self.tau is not None:
            return reference_similarity(distance, tau=self.tau), "REFERENCE_SIMILARITY"
        return calibrated_score(distance, knots=self.knots), "CALIBRATED"


def assess_phone(anchor: PhoneAnchor, *, frames: tuple[LipFrame, ...],
                 pcm_sha256: str, video_sha256: str, time_map: VerifiedTimeMap | None,
                 mapping_revision: str, references: tuple[LipReference, ...],
                 policy: DistancePolicy, calibration: Calibration | None = None) -> dict:
    """Null with an explicit reason is a valid outcome; no corrective action is inferred."""
    result = dict(expectedIndex=anchor.expected_index, expectedPhone=anchor.expected_phone,
        role=anchor.role, status="UNSCORABLE", score=None, scoreKind="NONE",
        referenceDistance=None, referenceIds=[], calibrationRevision=None,
        audioStartSeconds=None, audioEndSeconds=None, videoStartSeconds=None,
        videoEndSeconds=None, frameCount=0, reasonCodes=[], observations=[], allowedActionIds=[])
    try:
        anchor.validate()
        result.update(audioStartSeconds=anchor.start_s, audioEndSeconds=anchor.end_s)
        if time_map is None:
            raise UnscorableTrajectory("SYNC_UNCERTAIN")
        time_map.require(pcm_sha256, video_sha256)
        start = anchor.start_s * time_map.scale + time_map.offset_s
        end = anchor.end_s * time_map.scale + time_map.offset_s
        if not math.isfinite(start) or not math.isfinite(end) or start >= end:
            raise UnscorableTrajectory("SYNC_UNCERTAIN")
        result.update(videoStartSeconds=start, videoEndSeconds=end)
        if len(frames) > 18000 or any(not math.isfinite(f.pts_s) for f in frames) or any(
                a.pts_s >= b.pts_s for a, b in zip(frames, frames[1:])):
            raise UnscorableTrajectory("INVALID_FRAME_PTS")
        selected = [frame for frame in frames if start <= frame.pts_s < end]
        # Dropping occluded frames can silently hide the closure gesture.
        if not selected or any(not frame.observable for frame in selected):
            raise UnscorableTrajectory("LOW_VISIBILITY")
        result["frameCount"] = len(selected)
        observed = Trajectory(tuple(f.pts_s for f in selected), tuple(f.landmarks for f in selected),
                              tuple(f.features for f in selected))
        policy.validate(len(observed.features[0]))
        observed.validate(policy.max_frames)
        result["status"] = "OBSERVED"
        eligible = [r for r in references if r.expert_review_id and
            (r.expected_phone, r.role, r.context, r.mapping_revision) ==
            (anchor.expected_phone, anchor.role, anchor.context, mapping_revision)]
        if not eligible:
            raise UnscorableTrajectory("REFERENCE_MISSING")
        if len(eligible) > 32 or len({r.reference_id for r in eligible}) != len(eligible):
            raise UnscorableTrajectory("REFERENCE_CATALOG_INVALID")
        if any(r.video_sha256 == video_sha256 for r in eligible):
            raise UnscorableTrajectory("REFERENCE_SELF_COMPARISON")
        distances = [compare_trajectories(observed, ref.trajectory, policy).distance for ref in eligible]
        distance = statistics.median(distances)
        ids = sorted(r.reference_id for r in eligible)
        result.update(status="REFERENCE_ONLY", referenceDistance=distance, referenceIds=ids)
        if calibration is None:
            raise UnscorableTrajectory("CALIBRATION_MISSING")
        score, kind = calibration.score(distance, anchor, mapping_revision, policy.revision, ids)
        result.update(score=score, scoreKind=kind, calibrationRevision=calibration.revision,
                      status="SCORED" if kind == "CALIBRATED" else "REFERENCE_ONLY")
    except UnscorableTrajectory as error:
        result["reasonCodes"] = [str(error)]
    return result


def aggregate(assessments: list[dict], *, target_count: int, visually_eligible_count: int,
              minimum_coverage: float) -> dict:
    """Aggregate calibrated scores only, with explicit coverage. Missing is not zero."""
    indices = [row["expectedIndex"] for row in assessments]
    if (type(target_count) is not int or type(visually_eligible_count) is not int
            or not 0 <= len(assessments) <= visually_eligible_count <= target_count
            or len(indices) != len(set(indices)) or not math.isfinite(minimum_coverage)
            or not 0 <= minimum_coverage <= 1):
        raise UnscorableTrajectory("INVALID_COVERAGE")
    scored = [r["score"] for r in assessments if r["status"] == "SCORED" and r["scoreKind"] == "CALIBRATED"]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in scored):
        raise UnscorableTrajectory("INVALID_SCORE")
    coverage = len(scored) / visually_eligible_count if visually_eligible_count else None
    return dict(score=statistics.mean(scored) if scored and coverage >= minimum_coverage else None,
                targetCount=target_count, visuallyEligibleCount=visually_eligible_count,
                observedCount=sum(r["status"] in {"OBSERVED", "REFERENCE_ONLY", "SCORED"}
                                  and r["frameCount"] >= 3 for r in assessments),
                scoredCount=len(scored), coverage=coverage)
