"""Frozen, outcome-independent actor assignment for AI Hub 538 calibration."""

from __future__ import annotations

from collections.abc import Iterable

from .integrity import require_sha256, sha256_document


AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION = 8
AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION = 2
AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE = (
    "sorted_actor_hmac_alternating_development_calibration_v1"
)
AIHUB538_LABEL_SELECTION_RULE = (
    "lowest_label_selection_token_exact_quota_per_angle_v1"
)


def build_aihub538_balanced_actor_assignment(
    actor_tokens_sha256: Iterable[str],
) -> dict[str, str]:
    """Assign actors before outcomes using one deterministic alternating rule."""

    ordered = sorted(set(actor_tokens_sha256))
    for token in ordered:
        require_sha256(token, "actor_token_sha256")
    minimum_total = 2 * AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
    if len(ordered) < minimum_total:
        raise ValueError("aihub538_balanced_actor_capacity_insufficient")
    assignments = {
        token: "development" if index % 2 == 0 else "calibration"
        for index, token in enumerate(ordered)
    }
    counts = {
        partition: sum(value == partition for value in assignments.values())
        for partition in ("development", "calibration")
    }
    if any(
        count < AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION
        for count in counts.values()
    ):
        raise ValueError("aihub538_balanced_actor_capacity_insufficient")
    return assignments


def aihub538_balanced_assignment_commitment(
    assignments: dict[str, str],
) -> str:
    """Commit private assignments without serializing individual actor HMACs."""

    return sha256_document(
        {
            "schema_version": "voice_coach.aihub538_actor_assignment_root.v1",
            "assignment_rule": AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE,
            "rows": [
                {"actor_token_sha256": token, "partition": assignments[token]}
                for token in sorted(assignments)
            ],
        }
    )


def aihub538_label_selection_token_sha256(
    *,
    private_relative_key_sha256: str,
    label_payload_sha256: str,
    label_archive_sha256: str,
    angle: str,
) -> str:
    """Stable label-only key reused after exact paired-source admission."""

    for value in (
        private_relative_key_sha256,
        label_payload_sha256,
        label_archive_sha256,
    ):
        require_sha256(value, "aihub538_label_selection_token_input")
    if angle not in tuple("ABCDEFGHI"):
        raise ValueError("aihub538_label_selection_angle_invalid")
    # Keep the v1 document byte-for-byte compatible with already issued TL175
    # and cumulative TL176 capacity receipts.
    return sha256_document(
        {
            "schema_version": "voice_coach.aihub538_tl175_label_binding.v1",
            "private_relative_key_sha256": private_relative_key_sha256,
            "label_payload_sha256": label_payload_sha256,
            "label_archive_sha256": label_archive_sha256,
            "angle": angle,
        }
    )


def aihub538_label_selection_root_sha256(
    label_selection_tokens_sha256: Iterable[str],
) -> str:
    """Commit a private capacity cohort without serializing row tokens."""

    tokens = sorted(label_selection_tokens_sha256)
    for token in tokens:
        require_sha256(token, "label_selection_token_sha256")
    return sha256_document(
        {
            "schema_version": "voice_coach.aihub538_label_selection_root.v1",
            "sample_sha256": tokens,
        }
    )


__all__ = [
    "AIHUB538_MULTI_ACTOR_ASSIGNMENT_RULE",
    "AIHUB538_LABEL_SELECTION_RULE",
    "AIHUB538_MULTI_MINIMUM_ACTORS_PER_PARTITION",
    "AIHUB538_MULTI_SAMPLES_PER_ANGLE_PER_PARTITION",
    "aihub538_balanced_assignment_commitment",
    "aihub538_label_selection_root_sha256",
    "aihub538_label_selection_token_sha256",
    "build_aihub538_balanced_actor_assignment",
]
