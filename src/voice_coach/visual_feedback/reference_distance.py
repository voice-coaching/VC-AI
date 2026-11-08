"""Bounded, constrained lip trajectory comparison; this is not a diagnosis.

Coordinates must already use a stable face scale and roll (not mouth width).
Times are measured frame PTS within an MFA phone span, never synthetic frames.
Reference eligibility/calibration is owned by the calling evidence policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


class UnscorableTrajectory(ValueError):
    """A versioned policy precondition was not met; do not convert it to zero."""


@dataclass(frozen=True)
class Trajectory:
    times: tuple[float, ...]
    landmarks: tuple[tuple[tuple[float, float], ...], ...]
    features: tuple[tuple[float, ...], ...]

    def validate(self, max_frames: int, *, minimum_frames: int = 3) -> None:
        n = len(self.times)
        if not 1 <= minimum_frames <= n <= max_frames or len(self.landmarks) != n or len(self.features) != n:
            raise UnscorableTrajectory("INSUFFICIENT_OR_EXCESSIVE_FRAMES")
        if any(not math.isfinite(t) for t in self.times) or any(
                a >= b for a, b in zip(self.times, self.times[1:])):
            raise UnscorableTrajectory("INVALID_FRAME_PTS")
        k, m = len(self.landmarks[0]), len(self.features[0])
        if not 1 <= k <= 68 or m > 16:
            raise UnscorableTrajectory("INVALID_FEATURE_SHAPE")
        for points, features in zip(self.landmarks, self.features):
            if len(points) != k or len(features) != m or any(len(p) != 2 for p in points):
                raise UnscorableTrajectory("INVALID_FEATURE_SHAPE")
            if any(not math.isfinite(v) for p in points for v in p) or any(
                    not math.isfinite(v) for v in features):
                raise UnscorableTrajectory("NONFINITE_FEATURE")


@dataclass(frozen=True)
class DistancePolicy:
    revision: str
    landmark_weight: float
    feature_weights: tuple[float, ...]
    feature_scales: tuple[float, ...]
    phase_band: float
    phase_penalty: float
    max_repeat: int
    max_frames: int = 120

    def validate(self, feature_count: int) -> None:
        finite = (self.landmark_weight, self.phase_band, self.phase_penalty,
                  *self.feature_weights, *self.feature_scales)
        if not self.revision or any(not math.isfinite(v) for v in finite):
            raise UnscorableTrajectory("INVALID_DISTANCE_POLICY")
        if (not 0 <= self.landmark_weight <= 1 or not 0 <= self.phase_band <= 1
                or self.phase_penalty < 0 or type(self.max_repeat) is not int
                or not 1 <= self.max_repeat <= 8 or type(self.max_frames) is not int
                or not 3 <= self.max_frames <= 240):
            raise UnscorableTrajectory("INVALID_DISTANCE_POLICY")
        if (len(self.feature_weights) != feature_count or len(self.feature_scales) != feature_count
                or any(v < 0 for v in self.feature_weights) or any(v <= 0 for v in self.feature_scales)
                or (self.landmark_weight < 1 and sum(self.feature_weights) <= 0)):
            raise UnscorableTrajectory("UNCALIBRATED_FEATURE_SCALES")


@dataclass(frozen=True)
class DistanceResult:
    distance: float
    path_length: int
    policy_revision: str


def compare_trajectories(observed: Trajectory, reference: Trajectory,
                         policy: DistancePolicy) -> DistanceResult:
    """Minimum cumulative cost path, then path-length normalization.

    Endpoint-constrained weighted DTW with explicit consecutive horizontal and
    vertical limits. State tracks direction/run length, so a cheap invalid path
    cannot hide a valid constrained path. Ties prefer the shorter path.
    """
    policy.validate(len(observed.features[0]) if observed.features else 0)
    observed.validate(policy.max_frames)
    reference.validate(policy.max_frames)
    if (len(observed.landmarks[0]) != len(reference.landmarks[0])
            or len(observed.features[0]) != len(reference.features[0])):
        raise UnscorableTrajectory("REFERENCE_MAPPING_MISMATCH")
    def phases(times):
        return [(t - times[0]) / (times[-1] - times[0]) for t in times]
    u, v = phases(observed.times), phases(reference.times)
    def cost(i, j):
        geometric = sum((x - a)**2 + (y - b)**2 for (x, y), (a, b) in
                        zip(observed.landmarks[i], reference.landmarks[j])) / len(observed.landmarks[i])
        feature = 0.0
        if policy.landmark_weight < 1:
            feature = sum(w * ((x - y) / s)**2 for w, s, x, y in zip(
                policy.feature_weights, policy.feature_scales, observed.features[i], reference.features[j]))
            feature /= sum(policy.feature_weights)
        value = (1 + policy.phase_penalty * abs(u[i] - v[j])) * (
            policy.landmark_weight * geometric + (1 - policy.landmark_weight) * feature)
        if not math.isfinite(value):
            raise UnscorableTrajectory("NONFINITE_DISTANCE")
        return value

    previous = []
    for i in range(len(u)):
        row = []
        for j in range(len(v)):
            cell: dict[tuple[str, int], tuple[float, int]] = {}
            row.append(cell)
            if abs(u[i] - v[j]) > policy.phase_band:
                continue
            local = cost(i, j)
            if i == j == 0:
                cell[("D", 0)] = (local, 1)
                continue
            sources = []
            if i and j:
                sources.append(("D", previous[j-1]))
            if i:
                sources.append(("V", previous[j]))
            if j:
                sources.append(("H", row[j-1]))
            for direction, source in sources:
                for (old_direction, run), (total, length) in source.items():
                    repeat = 0 if direction == "D" else run + 1 if old_direction == direction else 1
                    if repeat > policy.max_repeat:
                        continue
                    key = direction, repeat
                    candidate = total + local, length + 1
                    if key not in cell or candidate < cell[key]:
                        cell[key] = candidate
        previous = row
    if not previous[-1]:
        raise UnscorableTrajectory("NO_ADMISSIBLE_DTW_PATH")
    total, length = min(previous[-1].values())
    return DistanceResult(total / length, length, policy.revision)


def reference_similarity(distance: float, *, tau: float) -> float:
    """Research similarity only; caller must supply a versioned fitted tau."""
    if not math.isfinite(distance) or distance < 0 or not math.isfinite(tau) or tau <= 0:
        raise UnscorableTrajectory("INVALID_SIMILARITY_CALIBRATION")
    return 100.0 * math.exp(-distance / tau)


def calibrated_score(distance: float, *, knots: Sequence[tuple[float, float]]) -> float:
    """Apply independently validated monotone calibration; never fit at inference."""
    if not math.isfinite(distance) or distance < 0 or len(knots) < 2:
        raise UnscorableTrajectory("CALIBRATION_MISSING")
    if any(not math.isfinite(x) or not math.isfinite(y) or x < 0 or not 0 <= y <= 1 for x, y in knots):
        raise UnscorableTrajectory("INVALID_CALIBRATION")
    if any(x >= nx or y < ny for (x, y), (nx, ny) in zip(knots, knots[1:])):
        raise UnscorableTrajectory("NONMONOTONE_CALIBRATION")
    # Out-of-domain extrapolation is not an expert-validated score.
    if not knots[0][0] <= distance <= knots[-1][0]:
        raise UnscorableTrajectory("OUTSIDE_CALIBRATION_DOMAIN")
    for (x, y), (nx, ny) in zip(knots, knots[1:]):
        if distance <= nx:
            return 100 * (y + (ny - y) * (distance - x) / (nx - x))
    raise UnscorableTrajectory("OUTSIDE_CALIBRATION_DOMAIN")
