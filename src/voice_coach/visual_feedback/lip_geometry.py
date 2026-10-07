"""Face-scale/roll normalization and visible 2-D lip measurements.

This adapter-independent geometry never estimates tongue position or 3-D
protrusion. Face visibility/head pose gates must precede this calculation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .reference_distance import UnscorableTrajectory


@dataclass(frozen=True)
class LipMapping:
    revision: str
    lip_indices: tuple[int, ...]
    left_face_anchor: int
    right_face_anchor: int
    left_corner: int
    right_corner: int
    upper_inner: int
    lower_inner: int
    inner_contour: tuple[int, ...]

    def validate(self, size):
        indices = (*self.lip_indices, self.left_face_anchor, self.right_face_anchor,
                   self.left_corner, self.right_corner, self.upper_inner, self.lower_inner,
                   *self.inner_contour)
        if (not self.revision or not 3 <= len(self.lip_indices) <= 68
                or len(set(self.lip_indices)) != len(self.lip_indices)
                or not 3 <= len(self.inner_contour) <= 68
                or len(set(self.inner_contour)) != len(self.inner_contour)
                or any(type(i) is not int or not 0 <= i < size for i in indices)
                or self.left_face_anchor == self.right_face_anchor
                or self.left_face_anchor in self.lip_indices
                or self.right_face_anchor in self.lip_indices):
            raise UnscorableTrajectory("INVALID_LANDMARK_MAPPING")


def normalize_lips(landmarks: tuple[tuple[float, float], ...], *, width: int,
                   height: int, mapping: LipMapping, minimum_face_scale_px: float):
    """Return normalized lip points and aperture/width/area features.

    Input coordinates are unit image coordinates. Convert both axes to pixels
    before rotating so non-square images don't distort shape. No instantaneous
    lip-width normalization, nonrigid morphing, or reference fitting is used.
    """
    mapping.validate(len(landmarks))
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or not math.isfinite(minimum_face_scale_px) or minimum_face_scale_px <= 0
            or any(len(p) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in p)
                   for p in landmarks)):
        raise UnscorableTrajectory("INVALID_LANDMARK_GEOMETRY")
    pixels = [(x * width, y * height) for x, y in landmarks]
    lx, ly = pixels[mapping.left_face_anchor]
    rx, ry = pixels[mapping.right_face_anchor]
    dx, dy = rx - lx, ry - ly
    scale = math.hypot(dx, dy)
    if scale < minimum_face_scale_px:
        raise UnscorableTrajectory("LOW_VISIBILITY")
    c, s = dx / scale, dy / scale
    cx, cy = (lx + rx) / 2, (ly + ry) / 2
    normalized = [((c * (x - cx) + s * (y - cy)) / scale,
                   (-s * (x - cx) + c * (y - cy)) / scale) for x, y in pixels]
    left, right = normalized[mapping.left_corner], normalized[mapping.right_corner]
    upper, lower = normalized[mapping.upper_inner], normalized[mapping.lower_inner]
    aperture = abs(lower[1] - upper[1])
    mouth_width = math.hypot(right[0] - left[0], right[1] - left[1])
    contour = [normalized[i] for i in mapping.inner_contour]
    area = abs(sum(x * ny - nx * y for (x, y), (nx, ny) in
                   zip(contour, contour[1:] + contour[:1]))) / 2
    # No aperture/width division near closure; feature policy can select only
    # dimensions calibrated for the relevant phoneme/context.
    return tuple(normalized[i] for i in mapping.lip_indices), (aperture, mouth_width, area)
