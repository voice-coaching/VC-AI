"""Where resyllabification (연음) makes a written syllable untestable.

**The alignment target is the written form, and that was settled by
measurement, not by argument.**  It is tempting to reason that a syllable
acoustic model hears surface forms — ``그릇에`` is read [그르세] — and should
therefore be aligned against surface syllables.  Three targets were compared on
60 recordings from the R01 v5 bank, matched against their own sentence and
mismatched against another:

===================  ========  =========  ======  =========
target               matched   mismatch   margin  segmental
===================  ========  =========  ======  =========
written               0.8220     0.2984   0.5236     0.2550
연음 only             0.8131     0.2954   0.5178     0.1112
g2pK surface          0.7933     0.2986   0.4947     0.0661
===================  ========  =========  ======  =========

The written form wins on every column, and by a wide margin on the goodness-of-
pronunciation score.  The reason is that the pinned CTC model was trained on
*orthographic* transcripts: its labels are spellings, so it has already learned
the surface-to-spelling mapping and emits ``국물`` for [궁물].  Aligning against
a phonetic form would ask it for something it was never trained to produce.  A
model trained on phonetic transcripts would need the opposite choice.

What survives is narrower.  At a 연음 site the written syllable's coda is not
pronounced in that syllable at all, so a *coda deletion* test there is asking
whether the speaker pronounced something Korean does not pronounce.  Measured on
the same 60 recordings, at the margin the gate coaches from:

===============  ====  ============  ==========
site             n     mean margin   fires
===============  ====  ============  ==========
연음              69       -3.123       30.4%
elsewhere        289      -10.122        7.6%
===============  ====  ============  ==========

Four times the firing rate on recordings that are reading the text correctly, so
the exclusion is carrying real false positives rather than tidying an edge case.

The vowel detector is left alone, and that too was checked rather than assumed:
10.3% at 연음 sites against 7.8% elsewhere.  Resyllabification moves consonants
and leaves nuclei where they were, so there is nothing there to correct for.
"""

from __future__ import annotations

from ..domain import PromptSpec

#: The null onset.  A syllable that begins with it takes the previous
#: syllable's coda.
NULL_ONSET = "ㅇ"
#: The velar nasal coda does not move — ``콩을`` is read [콩을], not [코응] —
#: even though it is written with the same jamo as the null onset.
VELAR_NASAL_CODA = "ㅇ"


def resyllabification_sites(prompt_spec: PromptSpec) -> frozenset[int]:
    """Prompt syllables whose coda is not pronounced in that syllable.

    A coda before a null-onset syllable inside the same eojeol moves onto that
    syllable (``닫아요`` → [다다요]), or, for ``ㅎ``, is deleted (``놓아`` →
    [노아]).  Either way the written coda is absent where it was written, so a
    coda-deletion finding there would be about the language rather than the
    learner.  ``ㅇ`` is the one coda that stays.

    Across an eojeol boundary the coda is realized in the careful read speech
    this catalog asks for, so those sites stay testable.
    """

    text = prompt_spec.normalized_text
    slots = prompt_spec.syllables
    sites: set[int] = set()
    for position in range(len(slots) - 1):
        left, right = slots[position], slots[position + 1]
        if left.coda is None or left.coda == VELAR_NASAL_CODA:
            continue
        if right.onset != NULL_ONSET:
            continue
        if " " in text[left.char_end : right.char_start]:
            continue
        sites.add(left.index)
    return frozenset(sites)
