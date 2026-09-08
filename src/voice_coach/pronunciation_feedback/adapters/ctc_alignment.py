"""CTC forced alignment and goodness-of-pronunciation, in numpy alone.

This module holds the arithmetic and none of the model: it takes frame
log-posteriors that some acoustic backend already produced and answers two
questions about them.

*Where is each expected syllable?*  A Viterbi pass over the standard CTC lattice
(blank-interleaved states, self/advance/skip transitions) assigns every frame to
one lattice state, which turns into a time range per expected token.

*How well was it pronounced?*  Goodness of pronunciation, in the posterior form
used by the GOP literature: for each frame the backend assigned to token ``k``,

    gop(t) = log p(expected_k | x_t) - max_v log p(v | x_t)

which is 0 when the expected token is also the most likely one and grows
negative as the frame drifts away from it.  Exponentiating a mean gop gives the
geometric mean of the posterior ratio, a number in ``(0, 1]`` that needs no
tuned constant to be readable — 1.0 means "the backend never preferred anything
else", 0.1 means "the expected token held a tenth of the winner's posterior".

Keeping this file free of torch is deliberate: the alignment is the part that
decides where a learner-facing highlight lands, and it must be testable against
hand-written lattices rather than only against a 317M-parameter model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Log-domain stand-in for "unreachable".  Using ``-inf`` would make the
#: ``argmax`` of an all-unreachable column meaningless and raise warnings on the
#: additions; a large finite floor keeps the recursion total.
NEGATIVE_INFINITY = -1.0e30


class ForcedAlignmentError(ValueError):
    """Raised when the requested alignment cannot exist."""


@dataclass(frozen=True, slots=True)
class TokenAlignment:
    """One expected token's frame range and its complete soft evidence.

    ``mean_gop`` preserves the legacy expected-versus-frame-winner ratio.
    ``gop_margin``, entropy, and top-1 retain distribution information that a
    hard CTC decode would discard.
    """

    token_index: int
    token_id: int
    start_frame: int
    end_frame: int
    mean_gop: float
    mean_log_posterior: float
    gop_margin: float
    posterior_entropy: float
    target_is_top1: bool

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1

    @property
    def posterior_ratio(self) -> float:
        """``exp(mean_gop)`` — the readable ``(0, 1]`` form of the gop."""

        return float(np.exp(self.mean_gop))


@dataclass(frozen=True, slots=True)
class ForcedAlignment:
    """Full-utterance alignment of one expected token sequence."""

    tokens: tuple[TokenAlignment, ...]
    total_frames: int
    #: Geometric mean of the per-frame posterior ratio over aligned frames.
    utterance_posterior_ratio: float
    #: Mean per-frame gop in nats, over the same frames.
    utterance_mean_gop: float

    def token(self, index: int) -> TokenAlignment | None:
        for item in self.tokens:
            if item.token_index == index:
                return item
        return None


def expand_spans(
    alignment: ForcedAlignment, *, maximum_absorbed_frames: int
) -> tuple[tuple[int, int], ...]:
    """Turn CTC spikes into contiguous spans by absorbing the blanks around them.

    A CTC head trained with the usual loss is peaky: it commits to a token on
    one or two frames and emits blank everywhere else.  Those spikes are
    excellent *anchors* — they are where the acoustic evidence for an identity
    sits, and every posterior comparison in this package uses them unchanged —
    but they are not boundaries.  Reporting a 25 ms span as a syllable's extent
    would make every duration measurement wrong and every learner-facing
    highlight land on a sliver of the syllable it names.

    So a blank run between two anchors is split between its neighbours, up to
    ``maximum_absorbed_frames`` in total.  A run longer than that is *not*
    fully absorbed: the middle stays unattributed, which is what leaves a real
    pause visible instead of silently inflating the two syllables around it.
    """

    if maximum_absorbed_frames < 0:
        raise ValueError("maximum_absorbed_frames must not be negative")
    tokens = alignment.tokens
    if not tokens:
        return ()
    half = maximum_absorbed_frames // 2
    starts = [token.start_frame for token in tokens]
    ends = [token.end_frame for token in tokens]

    spans: list[tuple[int, int]] = []
    for position, token in enumerate(tokens):
        start = token.start_frame
        end = token.end_frame
        if position == 0:
            start -= min(half, token.start_frame)
        else:
            gap = starts[position] - ends[position - 1] - 1
            take = min(gap, maximum_absorbed_frames)
            start -= take - take // 2
        if position == len(tokens) - 1:
            remaining = alignment.total_frames - 1 - token.end_frame
            end += min(half, remaining)
        else:
            gap = starts[position + 1] - ends[position] - 1
            take = min(gap, maximum_absorbed_frames)
            end += take // 2
        spans.append((max(0, start), min(alignment.total_frames - 1, end)))
    return tuple(spans)


def _lattice_states(tokens: np.ndarray, blank_id: int) -> np.ndarray:
    """``[blank, t0, blank, t1, ..., blank]`` — the standard CTC state chain."""

    states = np.full(2 * tokens.size + 1, blank_id, dtype=np.int64)
    states[1::2] = tokens
    return states


def _minimum_frames(tokens: np.ndarray, blank_id: int) -> int:
    """Shortest frame count that could emit ``tokens``.

    Every token needs one frame, and two identical neighbours additionally need
    a blank between them or CTC collapses them into one.
    """

    if tokens.size == 0:
        return 0
    repeats = int(np.count_nonzero(tokens[1:] == tokens[:-1]))
    return int(tokens.size) + repeats


def ctc_forced_alignment(
    log_posteriors: np.ndarray,
    tokens: np.ndarray | list[int],
    *,
    blank_id: int,
) -> ForcedAlignment:
    """Align ``tokens`` to ``log_posteriors`` and score each one.

    ``log_posteriors`` is ``(frames, vocabulary)`` in the log domain — already
    normalized, because the gop subtracts the frame maximum and any per-frame
    constant would cancel anyway.

    Raises :class:`ForcedAlignmentError` when the token sequence cannot be
    emitted in the available frames, rather than returning a truncated or
    padded alignment that would silently mislocate every later syllable.
    """

    posteriors = np.asarray(log_posteriors, dtype=np.float64)
    if posteriors.ndim != 2:
        raise ForcedAlignmentError("log_posteriors must be (frames, vocabulary)")
    if not np.isfinite(posteriors).all():
        raise ForcedAlignmentError("log_posteriors contain NaN or Inf")
    target = np.asarray(tokens, dtype=np.int64)
    if target.ndim != 1 or target.size == 0:
        raise ForcedAlignmentError("tokens must be a non-empty 1-D sequence")
    frame_count, vocabulary_size = posteriors.shape
    if not 0 <= blank_id < vocabulary_size:
        raise ForcedAlignmentError("blank_id is outside the vocabulary")
    if target.min() < 0 or target.max() >= vocabulary_size:
        raise ForcedAlignmentError("a token id is outside the vocabulary")
    needed = _minimum_frames(target, blank_id)
    if frame_count < needed:
        raise ForcedAlignmentError(
            f"{frame_count} frames cannot emit {target.size} tokens "
            f"(at least {needed} required)"
        )

    states = _lattice_states(target, blank_id)
    state_count = states.size
    emission = posteriors[:, states]  # (frames, states)

    # A skip over a blank is legal only into a real token that differs from the
    # token two states back; otherwise CTC would merge the two into one.
    can_skip = np.zeros(state_count, dtype=bool)
    if state_count > 2:
        can_skip[2:] = (states[2:] != blank_id) & (states[2:] != states[:-2])

    scores = np.full(state_count, NEGATIVE_INFINITY)
    scores[0] = emission[0, 0]
    if state_count > 1:
        scores[1] = emission[0, 1]
    backpointer = np.zeros((frame_count, state_count), dtype=np.int8)

    for frame in range(1, frame_count):
        stay = scores
        advance = np.concatenate(([NEGATIVE_INFINITY], scores[:-1]))
        skip = np.full(state_count, NEGATIVE_INFINITY)
        if state_count > 2:
            skip[2:] = np.where(can_skip[2:], scores[:-2], NEGATIVE_INFINITY)
        stacked = np.vstack((stay, advance, skip))
        backpointer[frame] = stacked.argmax(axis=0)
        scores = stacked.max(axis=0) + emission[frame]

    # A valid path ends in the final token or the trailing blank after it.
    terminal = state_count - 1
    if state_count > 1 and scores[state_count - 2] > scores[state_count - 1]:
        terminal = state_count - 2
    if scores[terminal] <= NEGATIVE_INFINITY / 2:
        raise ForcedAlignmentError("no CTC path reaches the end of the token sequence")

    path = np.empty(frame_count, dtype=np.int64)
    state = terminal
    for frame in range(frame_count - 1, -1, -1):
        path[frame] = state
        state -= int(backpointer[frame, state])

    frame_best = posteriors.max(axis=1)
    aligned: list[TokenAlignment] = []
    total_gop = 0.0
    total_aligned_frames = 0
    for token_index in range(target.size):
        lattice_state = 2 * token_index + 1
        frames = np.flatnonzero(path == lattice_state)
        if frames.size == 0:
            # Unreachable given the feasibility check above, but a silently
            # empty span would place a highlight at 0 ms.
            raise ForcedAlignmentError(
                f"token {token_index} received no frame in the decoded path"
            )
        token_id = int(target[token_index])
        token_window = posteriors[frames]
        token_logp = token_window[:, token_id]
        gop = token_logp - frame_best[frames]
        mean_by_class = token_window.mean(axis=0)
        competitor_logp = mean_by_class.copy()
        competitor_logp[token_id] = NEGATIVE_INFINITY
        probabilities = np.exp(token_window)
        entropy_by_frame = -np.sum(probabilities * token_window, axis=1)
        total_gop += float(gop.sum())
        total_aligned_frames += int(frames.size)
        aligned.append(
            TokenAlignment(
                token_index=token_index,
                token_id=token_id,
                start_frame=int(frames[0]),
                end_frame=int(frames[-1]),
                mean_gop=float(gop.mean()),
                mean_log_posterior=float(token_logp.mean()),
                gop_margin=float(mean_by_class[token_id] - competitor_logp.max()),
                posterior_entropy=float(entropy_by_frame.mean()),
                target_is_top1=bool(np.argmax(mean_by_class) == token_id),
            )
        )

    mean_gop = total_gop / total_aligned_frames if total_aligned_frames else 0.0
    return ForcedAlignment(
        tokens=tuple(aligned),
        total_frames=frame_count,
        utterance_posterior_ratio=float(np.exp(mean_gop)),
        utterance_mean_gop=float(mean_gop),
    )


def log_softmax(logits: np.ndarray) -> np.ndarray:
    """Row-wise log-softmax, stable for the magnitudes CTC heads produce."""

    values = np.asarray(logits, dtype=np.float64)
    shifted = values - values.max(axis=-1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))


def greedy_collapse(log_posteriors: np.ndarray, *, blank_id: int) -> tuple[int, ...]:
    """Standard CTC greedy decode: argmax, drop repeats, drop blanks."""

    best = np.asarray(log_posteriors).argmax(axis=-1)
    collapsed: list[int] = []
    previous = -1
    for token in best.tolist():
        if token != previous and token != blank_id:
            collapsed.append(int(token))
        previous = token
    return tuple(collapsed)
