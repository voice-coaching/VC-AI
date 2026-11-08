"""Measured lip/reference proximity. Temporary scales are not population SDs.

Only matching reviewed or explicitly designated trajectories supply targets. Missing references,
occluded frames and unobservable gestures stay null, never fabricated means.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
import statistics

from .reference_distance import Trajectory
from .feature_protocol import MAPPING

REVISION = "lip-reference-components-provisional-v1"
LABELS = {"lip_aperture": "입술 벌림", "lip_rounding": "입술 원순 형상",
          "lip_closure": "입술 폐쇄", "lip_timing": "입술 동작 타이밍"}
BILABIALS = frozenset("ㅁㅂㅃㅍ")
VOWELS = frozenset("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")


@dataclass(frozen=True)
class LipScorePolicy:
    revision: str = REVISION
    aperture_scale: float = 0.04
    width_scale: float = 0.04
    closure_scale: float = 0.02
    timing_scale: float = 0.20
    shape_tolerance: float = 0.01
    closure_tolerance: float = 0.005
    timing_tolerance: float = 0.10
    minimum_motion: float = 0.01
    decay: float = 0.5

    def validate(self):
        values = vars(self)
        if self.revision != REVISION or any(type(v) not in (int, float)
                or not math.isfinite(v) or v < 0 or v == 0 and not k.endswith("tolerance")
                for k, v in values.items() if k != "revision"):
            raise ValueError("LIP_SCORE_POLICY_INVALID")


def display(value):
    return float(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _metrics(trajectory, phone, role, policy, *, allow_sparse_frames=False, static_only=False):
    trajectory.validate(240, minimum_frames=1 if allow_sparse_frames else 3)
    if any(len(row) != 3 or any(v < 0 for v in row) for row in trajectory.features):
        raise ValueError("LIP_FEATURE_SHAPE_INVALID")
    aperture = [row[0] for row in trajectory.features]
    width = [row[1] for row in trajectory.features]
    bilabial = phone in BILABIALS and role in {"onset", "coda"}
    vowel = phone in VOWELS and role == "nucleus"
    result = {}
    if vowel:
        result["lip_aperture"] = statistics.median(aperture)
    if vowel and phone in "ㅗㅜㅛㅠ":
        # 2-D width relative to fixed eye scale, never inferred 3-D protrusion.
        result["lip_rounding"] = statistics.median(width)
    if bilabial and len(aperture) >= 2 and not static_only:
        result["lip_closure"] = min(aperture)
    if not static_only and len(aperture) >= 3 and (bilabial or vowel) and max(aperture) - min(aperture) >= policy.minimum_motion:
        event = min(aperture) if bilabial else max(aperture)
        # Plateaus use their median time; zero lag to the audio is NOT a target.
        event_time = statistics.median(t for t, value in zip(trajectory.times, aperture) if value == event)
        result["lip_timing"] = (event_time - trajectory.times[0]) / (trajectory.times[-1] - trajectory.times[0])
    return result


def score_phone(anchor, frames, references, *, policy, cross_word=False, allow_sparse_frames=False,
                static_only=False):
    """Equal-context reference medians, dead band, squared-distance exponential."""
    policy.validate()
    observed = Trajectory(tuple(f.pts_s for f in frames), tuple(f.landmarks for f in frames),
                          tuple(f.features for f in frames))
    values = _metrics(observed, anchor.expected_phone, anchor.role, policy,
                      allow_sparse_frames=allow_sparse_frames, static_only=static_only)
    ref_values = [_metrics(r.trajectory, anchor.expected_phone, anchor.role, policy,
                          allow_sparse_frames=allow_sparse_frames) for r in references]
    rows = []
    for key, label in LABELS.items():
        candidates = [r[key] for r in ref_values if key in r]
        row = dict(criterionId=key, label=label, score=None, status="NOT_APPLICABLE",
                   reasonCode="GESTURE_NOT_OBSERVABLE_IN_THIS_CONTEXT", measurements=[])
        if static_only and key in {'lip_closure','lip_timing'}:
            row.update(status='UNSCORABLE',reasonCode='PARTIAL_VISIBILITY_STATIC_ONLY')
        elif cross_word and key == 'lip_timing':
            # Different word/coarticulation contexts do not define the same
            # temporal gesture. Shape proximity is provisional; timing is not.
            row.update(status='UNSCORABLE',reasonCode='CROSS_WORD_TIMING_UNSUPPORTED')
        elif (allow_sparse_frames and key == 'lip_timing' and len(frames) < 3
              and (anchor.expected_phone in VOWELS or anchor.expected_phone in BILABIALS)):
            row.update(status='UNSCORABLE', reasonCode='INSUFFICIENT_MOTION_FRAMES')
        elif (allow_sparse_frames and key == 'lip_closure' and len(frames) < 2
              and anchor.expected_phone in BILABIALS):
            row.update(status='UNSCORABLE', reasonCode='INSUFFICIENT_CLOSURE_FRAMES')
        elif key in values and candidates:
            center = statistics.median(candidates)
            floor = {"lip_aperture": policy.aperture_scale, "lip_rounding": policy.width_scale,
                     "lip_closure": policy.closure_scale, "lip_timing": policy.timing_scale}[key]
            spread = statistics.stdev(candidates) if len(candidates) > 1 else 0.0
            scale = max(floor, spread)
            tolerance = (policy.timing_tolerance if key == "lip_timing" else
                         policy.closure_tolerance if key == "lip_closure" else policy.shape_tolerance)
            z = max(0.0, abs(values[key] - center) - tolerance) / scale
            score = 100 * math.exp(-policy.decay * z * z)
            row.update(score=display(score), status="SCORED", reasonCode="PROVISIONAL_REFERENCE_PROXIMITY",
                measurements=[dict(metricId=key, value=values[key], referenceMedian=center,
                    referenceScale=scale, referenceCount=len(candidates), tolerance=tolerance,
                    unit="PHONE_PHASE" if key == "lip_timing" else "FACE_SCALE_NORMALIZED")])
        elif key in values:
            row.update(status="UNSCORABLE", reasonCode="REFERENCE_GESTURE_UNAVAILABLE")
        rows.append(row)
    available = [r["score"] for r in rows if r["score"] is not None]
    return rows, display(statistics.mean(available)) if available else None


def enrich_phone(row, anchor, frames, *, references, policy, video_sha256, source_sha256,
                 allow_cross_word=False, allow_sparse_frames=False):
    """Add scores only to a valid measured join; preserve identity rejections."""
    blocked = {"SYNC_UNCERTAIN", "LOW_VISIBILITY", "REFERENCE_SELF_COMPARISON",
               "REFERENCE_CATALOG_INVALID", "INVALID_FRAME_PTS"}
    if row["frameCount"] < (1 if allow_sparse_frames else 3) or blocked.intersection(row["reasonCodes"]):
        return
    eligible = [r for r in references if (r.expert_review_id or r.authority == "USER_DESIGNATED_EXEMPLAR")
        and r.mapping_revision == MAPPING.revision and
        (r.expected_phone, r.role, r.context) == (anchor.expected_phone, anchor.role, anchor.context)]
    cross_word = False
    if not eligible and allow_cross_word:
        eligible = [r for r in references if (r.expert_review_id or r.authority == 'USER_DESIGNATED_EXEMPLAR')
            and r.mapping_revision == MAPPING.revision
            and (r.expected_phone,r.role)==(anchor.expected_phone,anchor.role)]
        cross_word = bool(eligible)
    if not eligible:
        return
    if len(eligible)>32:
        row['reasonCodes']=['REFERENCE_CATALOG_INVALID']
        return
    if any(r.video_sha256 == video_sha256 or r.source_sha256 == source_sha256 for r in eligible):
        row["reasonCodes"] = ["REFERENCE_SELF_COMPARISON"]
        return
    selected = [f for f in frames if row["videoStartSeconds"] <= f.pts_s < row["videoEndSeconds"]]
    occluded = any(not f.observable for f in selected)
    if allow_sparse_frames:
        selected = [f for f in selected if f.observable]
    if not selected or occluded and not allow_sparse_frames:
        return
    try:
        subscores, score = score_phone(anchor, selected, eligible, policy=policy,cross_word=cross_word,
                                      allow_sparse_frames=allow_sparse_frames,static_only=occluded)
    except ValueError:
        # A component with no observable event cannot cancel the audio result.
        return
    row["subScores"] = subscores
    if score is not None and row["scoreKind"] != "CALIBRATED":
        row.update(score=score, status="SCORED", scoreKind="PROVISIONAL",
            referenceIds=sorted(r.reference_id for r in eligible), calibrationRevision=None,
            reasonCodes=(["CROSS_WORD_PROVISIONAL_REFERENCE"] if cross_word else [])+
                        ["EXPERT_REVIEW_PENDING"] +
                        (["SPARSE_STATIC_OBSERVATION"] if len(selected) < 3 else []) +
                        (["PARTIAL_VISIBILITY_STATIC_ONLY"] if occluded else []),
            allowedActionIds=["COMPARE_REFERENCE"])


def summarize_subscores(rows):
    result = []
    for key, label in LABELS.items():
        scores = [s["score"] for row in rows for s in row.get("subScores", [])
                  if s["criterionId"] == key and s["status"] == "SCORED"]
        result.append(dict(criterionId=key, label=label,
            score=display(statistics.mean(scores)) if scores else None,
            status="SCORED" if scores else "UNSCORABLE",
            reasonCode="PROVISIONAL_REFERENCE_PROXIMITY" if scores else "NO_OBSERVABLE_REFERENCE_PAIRS"))
    return result
