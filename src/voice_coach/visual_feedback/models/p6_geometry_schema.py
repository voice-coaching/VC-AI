"""Pure constants shared by AI Hub 68 admission and calibration adapters."""

AIHUB68_KEYPOINT_NAMES = (
    "outer_0_left", "outer_1", "outer_2", "outer_3_top",
    "outer_4", "outer_5", "outer_6_right", "outer_7",
    "outer_8", "outer_9_bottom", "outer_10", "outer_11",
    "inner_0_left", "inner_1", "inner_2", "inner_3_top",
    "inner_4", "inner_5", "inner_6_right", "inner_7",
    "inner_8", "inner_9_bottom", "inner_10", "inner_11",
)

MEDIAPIPE_LIP_CANDIDATE_INDICES = (
    0, 13, 14, 17, 37, 39, 40, 61, 78, 80,
    81, 82, 84, 87, 88, 91, 95, 146, 178, 181,
    185, 191, 267, 269, 270, 291, 308, 310, 311, 312,
    314, 317, 318, 321, 324, 375, 402, 405, 409, 415,
)

__all__ = ["AIHUB68_KEYPOINT_NAMES", "MEDIAPIPE_LIP_CANDIDATE_INDICES"]
