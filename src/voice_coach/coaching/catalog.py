"""Legacy engineering MVP advice allowlist.

These four messages keep the C00 oracle/API contract stable.  They are not
evidence of C03 expert review and must not be described as production-approved
coaching.  The machine-checkable 17-tag C01 draft lives in
``configs/coaching/korean_error_advice_ontology_v1.json``.
"""

from __future__ import annotations

ADVICE_CATALOG: dict[str, tuple[str, str]] = {
    "final_consonant_deletion": (
        "ARTICULATION_RULE_017",
        "받침 위치에서 공기를 더 내보내지 말고 혀와 입의 접촉을 유지해 보세요.",
    ),
    "vowel_quality": (
        "ARTICULATION_RULE_021",
        "목표 모음의 혀 높이와 입술 모양을 확인한 뒤 짧게 다시 녹음해 보세요.",
    ),
    "misplaced_pause": (
        "PROSODY_RULE_006",
        "문장 의미 단위 안에서는 멈추지 말고 다음 어절까지 이어 읽어 보세요.",
    ),
    "duration_short": (
        "RHYTHM_RULE_004",
        "표시된 음절을 조금 더 충분히 유지하되 다음 음절의 시작은 늦추지 마세요.",
    ),
}
