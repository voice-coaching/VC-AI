"""Automated pre-screen for coaching text. Reduces expert load; replaces no expert.

C03 fixes four zero-tolerance categories for coaching output — risk expressions,
discriminatory expressions, speaker-identity inference, medical judgment — with a
maximum of **0** occurrences each, and it fixes them as things *reviewers* check on
a 200-item stratified sample.

This module does not do that review. It is a screen that runs on every candidate
message before a reviewer ever sees it, so that an obvious violation is caught at
authoring time instead of consuming one of the 200 review slots. The distinction
is load-bearing and is encoded in the result type:

* :attr:`ScreenResult.verdict` is only ever ``flagged`` or ``no_flag`` — never
  ``pass`` or ``approved``. A clean screen means "nothing this screen detects",
  not "safe to publish".
* :attr:`ScreenResult.claim_scope` is fixed to ``engineering_screen`` and
  :attr:`ScreenResult.substitutes_expert_review` is fixed to ``False``, so a
  downstream consumer cannot read a screen record as an audit record.
* A flag is a *stop*, not a score: flagged text is withheld, and the reason is
  reported so the wording can be fixed and re-screened.

Detection is lexical and deliberately over-inclusive. A screen that misses a
violation costs an expert slot at best and a learner harm at worst; a screen that
over-flags costs a rewrite. The asymmetry decides the tuning.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
from typing import Literal, Mapping

SCREEN_ID = "coaching-safety-lexical-screen-v1"
#: Never "pass"/"approved": this screen cannot confer either.
Verdict = Literal["no_flag", "flagged"]

#: The four zero-tolerance categories C03 fixes, each with a maximum of 0.
ZERO_TOLERANCE_CATEGORIES: tuple[str, ...] = (
    "risk_expression",
    "discriminatory_expression",
    "speaker_identity_inference",
    "medical_judgment",
)

_PATTERNS: Mapping[str, tuple[re.Pattern[str], ...]] = {
    "medical_judgment": (
        re.compile(r"(질환|질병|장애|증상|병원|진료|치료|처방|약물|재활|의사와|검진)"),
        re.compile(r"(구개열|청각\s*장애|난청|말더듬|실어증|조음\s*장애|언어\s*장애)"),
        re.compile(r"(진단|소견)"),
    ),
    "speaker_identity_inference": (
        re.compile(r"(당신은|화자는|말투로 보아|억양으로 보아|발음으로 보아).{0,12}"
                   r"(중국|일본|베트남|태국|외국인|남성|여성|나이|연령|출신|국적)"),
        re.compile(r"(모국어가|출신이|국적이|나이가|성별이)\s*\S*(인 것|것 같|로 보|로 추정)"),
        re.compile(r"(중국인|일본인|외국인)(이라서|이므로|답게)"),
    ),
    "discriminatory_expression": (
        re.compile(r"(어눌|서투른 티|촌스|무식|저능|열등|미개|이상한 말투|웃긴 발음)"),
        re.compile(r"(원어민답지|한국인답지)\s*(못|않)"),
        re.compile(r"(못하는 사람|안 되는 사람)"),
    ),
    "risk_expression": (
        re.compile(r"(반드시|절대|무조건)\s*(해야|하지 마|금지)"),
        re.compile(r"(위험|손상|다칠|무리하게|아플 때까지|참고 계속)"),
        re.compile(r"(성대|목)\s*\S{0,4}(에 힘|을 조이|를 조이|눌러|압박)"),
        re.compile(r"(보장|100%|완치|반드시 좋아)"),
    ),
}


class CoachingScreenError(ValueError):
    """Raised when a screen input breaks the contract."""


@dataclass(frozen=True, slots=True)
class ScreenFinding:
    """One detected pattern, with the span so the wording can be fixed."""

    category: str
    matched_text: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.category not in ZERO_TOLERANCE_CATEGORIES:
            raise CoachingScreenError(f"unknown category: {self.category}")
        if self.start < 0 or self.end <= self.start:
            raise CoachingScreenError("finding span must be a forward range")


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """The screen's output. Not an audit result, and typed so it cannot pose as one."""

    screen_id: str
    verdict: Verdict
    findings: tuple[ScreenFinding, ...]
    claim_scope: str = "engineering_screen"
    substitutes_expert_review: bool = False
    expert_review_still_required: bool = True

    def __post_init__(self) -> None:
        if self.claim_scope != "engineering_screen":
            raise CoachingScreenError("a screen result carries only engineering_screen")
        if self.substitutes_expert_review:
            raise CoachingScreenError("a screen never substitutes for expert review")
        if not self.expert_review_still_required:
            raise CoachingScreenError("expert review cannot be waived by a screen")
        if self.verdict == "flagged" and not self.findings:
            raise CoachingScreenError("a flagged result must name its findings")
        if self.verdict == "no_flag" and self.findings:
            raise CoachingScreenError("findings contradict a no_flag verdict")

    @property
    def may_serve(self) -> bool:
        """Whether the text may be served *under the engineering scope*.

        False on any flag. This is a withhold, not a penalty score.
        """

        return self.verdict == "no_flag"

    @property
    def flagged_categories(self) -> tuple[str, ...]:
        return tuple(sorted({item.category for item in self.findings}))


def screen_coaching_text(text: str) -> ScreenResult:
    """Run the lexical screen over one candidate coaching message."""

    if not isinstance(text, str) or not text.strip():
        raise CoachingScreenError("coaching text must be a non-empty string")
    findings: list[ScreenFinding] = []
    for category in ZERO_TOLERANCE_CATEGORIES:
        for pattern in _PATTERNS[category]:
            for match in pattern.finditer(text):
                findings.append(
                    ScreenFinding(
                        category=category,
                        matched_text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )
    ordered = tuple(sorted(findings, key=lambda item: (item.start, item.category)))
    return ScreenResult(
        screen_id=SCREEN_ID,
        verdict="flagged" if ordered else "no_flag",
        findings=ordered,
    )


@dataclass(frozen=True, slots=True)
class CatalogScreenReport:
    """Screen outcome over a whole advice catalog, for pre-review triage."""

    screen_id: str
    entry_count: int
    flagged: Mapping[str, tuple[str, ...]]
    claim_scope: str = "engineering_screen"
    substitutes_expert_review: bool = False

    def __post_init__(self) -> None:
        if self.substitutes_expert_review:
            raise CoachingScreenError("a screen never substitutes for expert review")

    @property
    def flagged_count(self) -> int:
        return len(self.flagged)

    @property
    def all_clear_of_screen(self) -> bool:
        """No screen finding. Says nothing about whether an expert would pass it."""

        return not self.flagged


def screen_catalog(entries: Sequence[tuple[str, str]]) -> CatalogScreenReport:
    """Screen ``(issue_type, message)`` pairs so review starts from a clean set."""

    if not entries:
        raise CoachingScreenError("no catalog entry to screen")
    flagged: dict[str, tuple[str, ...]] = {}
    for issue_type, message in entries:
        result = screen_coaching_text(message)
        if result.verdict == "flagged":
            flagged[issue_type] = result.flagged_categories
    return CatalogScreenReport(
        screen_id=SCREEN_ID,
        entry_count=len(entries),
        flagged=flagged,
    )


__all__ = [
    "SCREEN_ID",
    "ZERO_TOLERANCE_CATEGORIES",
    "CatalogScreenReport",
    "CoachingScreenError",
    "ScreenFinding",
    "ScreenResult",
    "Verdict",
    "screen_catalog",
    "screen_coaching_text",
]
