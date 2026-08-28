"""What the aligner must not get wrong about where a syllable is.

Every learner-facing highlight and every duration measurement in this package
comes out of this file's arithmetic, so the properties pinned here are the ones
whose failure would be invisible in the output: a span that silently lands on
the wrong syllable still looks like a span.
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_coach.pronunciation_feedback.adapters.ctc_alignment import (
    ForcedAlignmentError,
    ctc_forced_alignment,
    expand_spans,
    greedy_collapse,
    log_softmax,
)

BLANK = 0


def _peaky(frames: int, spikes: dict[int, int], vocabulary: int = 4) -> np.ndarray:
    """Frames that are blank except where ``spikes`` names a token."""

    logits = np.zeros((frames, vocabulary))
    logits[:, BLANK] = 6.0
    for frame, token in spikes.items():
        logits[frame, BLANK] = 0.0
        logits[frame, token] = 6.0
    return log_softmax(logits)


# -- the path itself ----------------------------------------------------------


def test_each_token_lands_on_the_frame_that_supports_it() -> None:
    posteriors = _peaky(10, {2: 1, 6: 2})

    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    assert [token.start_frame for token in alignment.tokens] == [2, 6]
    assert [token.end_frame for token in alignment.tokens] == [2, 6]


def test_token_order_is_preserved_even_when_the_audio_argues_otherwise() -> None:
    """Forced alignment aligns the *expected* order; it does not reorder it.

    If it reordered, a highlight would land on whichever syllable the model
    liked best rather than the one being coached.
    """

    posteriors = _peaky(10, {2: 2, 6: 1})

    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    assert alignment.tokens[0].token_id == 1
    assert alignment.tokens[1].token_id == 2
    assert alignment.tokens[0].start_frame < alignment.tokens[1].start_frame


def test_two_identical_neighbours_are_kept_apart_by_a_blank() -> None:
    """Without the blank CTC would collapse them, losing one syllable's span."""

    posteriors = _peaky(6, {1: 1, 4: 1})

    alignment = ctc_forced_alignment(posteriors, [1, 1], blank_id=BLANK)

    first, second = alignment.tokens
    assert second.start_frame > first.end_frame + 1


def test_a_sequence_that_cannot_fit_is_refused_not_truncated() -> None:
    """A truncated alignment would mislocate every syllable after the cut."""

    posteriors = _peaky(2, {0: 1, 1: 2})

    with pytest.raises(ForcedAlignmentError, match="cannot emit"):
        ctc_forced_alignment(posteriors, [1, 2, 1, 2], blank_id=BLANK)


def test_repeated_tokens_need_room_for_the_separating_blank() -> None:
    posteriors = _peaky(2, {0: 1, 1: 1})

    with pytest.raises(ForcedAlignmentError, match="at least 3"):
        ctc_forced_alignment(posteriors, [1, 1], blank_id=BLANK)


def test_a_token_id_outside_the_vocabulary_is_refused() -> None:
    posteriors = _peaky(6, {1: 1})

    with pytest.raises(ForcedAlignmentError, match="outside the vocabulary"):
        ctc_forced_alignment(posteriors, [1, 99], blank_id=BLANK)


def test_every_token_receives_at_least_one_frame() -> None:
    """An empty span would resolve to a 0 ms highlight at the start of the file."""

    posteriors = _peaky(12, {3: 1})

    alignment = ctc_forced_alignment(posteriors, [1, 2, 1], blank_id=BLANK)

    assert all(token.frame_count >= 1 for token in alignment.tokens)


# -- goodness of pronunciation ------------------------------------------------


def test_a_token_the_audio_supports_scores_a_ratio_of_one() -> None:
    posteriors = _peaky(8, {2: 1, 5: 2})

    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    assert alignment.tokens[0].mean_gop == pytest.approx(0.0)
    assert alignment.tokens[0].posterior_ratio == pytest.approx(1.0)


def test_a_token_the_audio_contradicts_scores_below_one() -> None:
    """This is the whole diagnostic signal: the ratio has to fall when the
    expected syllable is not what the frames support."""

    posteriors = _peaky(8, {2: 1, 5: 2})

    alignment = ctc_forced_alignment(posteriors, [1, 3], blank_id=BLANK)

    contradicted = alignment.tokens[1]
    assert contradicted.mean_gop < 0.0
    assert 0.0 < contradicted.posterior_ratio < 1.0


def test_the_utterance_ratio_lies_between_its_tokens() -> None:
    posteriors = _peaky(10, {2: 1, 6: 2})

    alignment = ctc_forced_alignment(posteriors, [1, 3], blank_id=BLANK)

    ratios = [token.posterior_ratio for token in alignment.tokens]
    assert min(ratios) <= alignment.utterance_posterior_ratio <= max(ratios)


def test_soft_information_keeps_more_than_the_hard_top1() -> None:
    posteriors = _peaky(8, {2: 1, 5: 2})

    supported = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK).tokens[1]
    contradicted = ctc_forced_alignment(posteriors, [1, 3], blank_id=BLANK).tokens[1]

    assert supported.target_is_top1 is True
    assert supported.gop_margin > 0.0
    assert supported.posterior_entropy >= 0.0
    assert np.isfinite(supported.mean_log_posterior)
    assert contradicted.target_is_top1 is False
    assert contradicted.gop_margin < 0.0


def test_nonfinite_soft_posteriors_fail_closed() -> None:
    posteriors = _peaky(8, {2: 1})
    posteriors[0, 0] = np.nan

    with pytest.raises(ForcedAlignmentError, match="NaN or Inf"):
        ctc_forced_alignment(posteriors, [1], blank_id=BLANK)


# -- turning spikes into spans ------------------------------------------------


def test_absorbing_blanks_makes_the_spans_contiguous() -> None:
    """A 25 ms spike reported as a syllable's extent would make every duration
    measurement wrong and every highlight land on a sliver."""

    posteriors = _peaky(12, {2: 1, 6: 2})
    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    spans = expand_spans(alignment, maximum_absorbed_frames=13)

    assert spans[0][1] + 1 == spans[1][0]
    assert spans[0][1] > spans[0][0]


def test_a_gap_longer_than_the_budget_stays_visible() -> None:
    """Absorbing an arbitrarily long silence would hide exactly the pause the
    misplaced-pause detector exists to find."""

    posteriors = _peaky(40, {2: 1, 35: 2})
    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    spans = expand_spans(alignment, maximum_absorbed_frames=4)

    assert spans[1][0] - spans[0][1] > 1


def test_spans_never_leave_the_recording() -> None:
    posteriors = _peaky(8, {0: 1, 7: 2})
    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    spans = expand_spans(alignment, maximum_absorbed_frames=13)

    assert spans[0][0] >= 0
    assert spans[-1][1] <= 7


def test_absorbing_nothing_returns_the_anchors_unchanged() -> None:
    posteriors = _peaky(10, {2: 1, 6: 2})
    alignment = ctc_forced_alignment(posteriors, [1, 2], blank_id=BLANK)

    spans = expand_spans(alignment, maximum_absorbed_frames=0)

    assert spans == ((2, 2), (6, 6))


# -- the free decode ----------------------------------------------------------


def test_greedy_decoding_drops_blanks_and_collapses_repeats() -> None:
    posteriors = _peaky(9, {1: 1, 2: 1, 5: 2})

    assert greedy_collapse(posteriors, blank_id=BLANK) == (1, 2)
