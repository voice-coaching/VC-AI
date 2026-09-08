"""Content agreement and syllable alignment derived from CTC posteriors.

This module holds the decoding, scoring, and span-derivation logic with no torch
or filesystem dependency, so the arithmetic that decides whether a learner is
told "you read the wrong text" is testable without a checkpoint.

Two rules shape the design:

* A span is only emitted for a prompt syllable the hypothesis actually matched.
  Guessing a time range for a syllable the model never emitted would put a
  learner-facing highlight on evidence that does not exist.
* Confidence is never manufactured. If a syllable has no frames, or the
  posterior rows are unusable, the result carries ``None`` with a
  :class:`MetricState`, not ``0.0``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Literal, Mapping
import unicodedata

BLANK_TOKEN = "<blank>"
WORD_DELIMITER_TOKEN = "|"
#: wav2vec2-style feature extractors stride 320 samples, i.e. 20 ms at 16 kHz.
DEFAULT_FRAME_MS = 20.0

EditOperation = Literal["match", "substitute", "insert", "delete"]


class CtcAlignmentError(ValueError):
    """Raised when a posterior matrix or vocabulary breaks the contract."""


@dataclass(frozen=True, slots=True)
class CtcVocabulary:
    """Ordered CTC label set. Index 0 is always blank."""

    tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.tokens:
            raise CtcAlignmentError("vocabulary must not be empty")
        if self.tokens[0] != BLANK_TOKEN:
            raise CtcAlignmentError("vocabulary index 0 must be the blank token")
        if len(set(self.tokens)) != len(self.tokens):
            raise CtcAlignmentError("vocabulary contains duplicate tokens")
        if any(not token for token in self.tokens):
            raise CtcAlignmentError("vocabulary contains an empty token")

    @property
    def blank_index(self) -> int:
        return 0

    @property
    def size(self) -> int:
        return len(self.tokens)

    def index_of(self, token: str) -> int:
        try:
            return self.tokens.index(token)
        except ValueError as error:
            raise CtcAlignmentError(f"token is outside the vocabulary: {token!r}") from error

    def token_at(self, index: int) -> str:
        if not 0 <= index < len(self.tokens):
            raise CtcAlignmentError(f"label index out of range: {index}")
        return self.tokens[index]

    @property
    def digest(self) -> str:
        payload = json.dumps(
            list(self.tokens),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_texts(cls, texts: Iterable[str]) -> CtcVocabulary:
        """Build a deterministic syllable vocabulary from normalized prompt text.

        Sorting by codepoint rather than by frequency keeps the label set — and
        therefore the checkpoint's output layer — reproducible from the corpus
        alone, with no dependence on iteration order.
        """

        syllables: set[str] = set()
        for text in texts:
            for character in unicodedata.normalize("NFC", text):
                if character.isspace():
                    continue
                if _is_hangul_syllable(character):
                    syllables.add(character)
        if not syllables:
            raise CtcAlignmentError("no Hangul syllables found in the corpus")
        return cls(
            tokens=(BLANK_TOKEN, WORD_DELIMITER_TOKEN, *sorted(syllables)),
        )


@dataclass(frozen=True, slots=True)
class FrameSegment:
    """One decoded label and the contiguous frame range that emitted it."""

    label: str
    start_frame: int
    end_frame: int
    mean_probability: float

    def start_ms(self, frame_ms: float = DEFAULT_FRAME_MS) -> int:
        return int(round(self.start_frame * frame_ms))

    def end_ms(self, frame_ms: float = DEFAULT_FRAME_MS) -> int:
        return int(round((self.end_frame + 1) * frame_ms))


@dataclass(frozen=True, slots=True)
class CtcDecoding:
    """Greedy decode output with the frame provenance of every label."""

    hypothesis: str
    segments: tuple[FrameSegment, ...]
    frame_count: int

    @property
    def syllable_segments(self) -> tuple[FrameSegment, ...]:
        return tuple(
            segment
            for segment in self.segments
            if segment.label != WORD_DELIMITER_TOKEN
        )


@dataclass(frozen=True, slots=True)
class EditStep:
    """One alignment operation between reference and hypothesis positions."""

    operation: EditOperation
    reference_index: int | None
    hypothesis_index: int | None


def _is_hangul_syllable(character: str) -> bool:
    return "가" <= character <= "힣"


def _validate_posteriors(
    posteriors: Sequence[Sequence[float]],
    vocabulary: CtcVocabulary,
) -> None:
    if not posteriors:
        raise CtcAlignmentError("posterior matrix has no frames")
    for row_index, row in enumerate(posteriors):
        if len(row) != vocabulary.size:
            raise CtcAlignmentError(
                f"frame {row_index} has {len(row)} labels, expected {vocabulary.size}"
            )
        for value in row:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise CtcAlignmentError(f"frame {row_index} holds a non-numeric value")
            if value != value:  # NaN
                raise CtcAlignmentError(f"frame {row_index} holds NaN")
            if not 0.0 <= float(value) <= 1.0:
                raise CtcAlignmentError(
                    f"frame {row_index} holds a value outside [0, 1]; "
                    "pass probabilities, not logits"
                )


def greedy_ctc_decode(
    posteriors: Sequence[Sequence[float]],
    vocabulary: CtcVocabulary,
) -> CtcDecoding:
    """Collapse the argmax path, keeping each label's frame range.

    Standard CTC collapsing: repeats merge, blanks separate. The frame range is
    retained because it is the only evidence available for a time span.
    """

    _validate_posteriors(posteriors, vocabulary)
    segments: list[FrameSegment] = []
    current_index: int | None = None
    current_start = 0
    current_probabilities: list[float] = []

    def flush(end_frame: int) -> None:
        if current_index is None or current_index == vocabulary.blank_index:
            return
        segments.append(
            FrameSegment(
                label=vocabulary.token_at(current_index),
                start_frame=current_start,
                end_frame=end_frame,
                mean_probability=(
                    sum(current_probabilities) / len(current_probabilities)
                ),
            )
        )

    for frame_index, row in enumerate(posteriors):
        best_index = max(range(len(row)), key=lambda index: (row[index], -index))
        if best_index != current_index:
            flush(frame_index - 1)
            current_index = best_index
            current_start = frame_index
            current_probabilities = []
        current_probabilities.append(float(row[best_index]))
    flush(len(posteriors) - 1)

    hypothesis = "".join(
        " " if segment.label == WORD_DELIMITER_TOKEN else segment.label
        for segment in segments
    ).strip()
    return CtcDecoding(
        hypothesis=hypothesis,
        segments=tuple(segments),
        frame_count=len(posteriors),
    )


def edit_alignment(
    reference: Sequence[str],
    hypothesis: Sequence[str],
) -> tuple[tuple[EditStep, ...], int]:
    """Levenshtein alignment with backtrace, returning steps and edit distance.

    The steps are what make a span attributable: a substituted or deleted prompt
    syllable must not inherit the time range of whatever the model emitted
    instead.
    """

    rows = len(reference) + 1
    columns = len(hypothesis) + 1
    costs = [[0] * columns for _ in range(rows)]
    for row in range(1, rows):
        costs[row][0] = row
    for column in range(1, columns):
        costs[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            if reference[row - 1] == hypothesis[column - 1]:
                costs[row][column] = costs[row - 1][column - 1]
                continue
            costs[row][column] = 1 + min(
                costs[row - 1][column - 1],  # substitute
                costs[row - 1][column],  # delete
                costs[row][column - 1],  # insert
            )

    steps: list[EditStep] = []
    row, column = len(reference), len(hypothesis)
    while row > 0 or column > 0:
        # Equal-cost alignments are resolved toward the earliest reference
        # position: when a trailing reference symbol could either match or be
        # deleted at the same cost, delete it. Otherwise a repeated phone late in
        # the prompt could claim a decoded segment that belongs to an earlier
        # syllable, and a syllable the learner never produced would get a span.
        if row > 0 and costs[row][column] == costs[row - 1][column] + 1:
            steps.append(EditStep("delete", row - 1, None))
            row -= 1
            continue
        if (
            row > 0
            and column > 0
            and reference[row - 1] == hypothesis[column - 1]
            and costs[row][column] == costs[row - 1][column - 1]
        ):
            steps.append(EditStep("match", row - 1, column - 1))
            row, column = row - 1, column - 1
            continue
        if (
            row > 0
            and column > 0
            and costs[row][column] == costs[row - 1][column - 1] + 1
        ):
            steps.append(EditStep("substitute", row - 1, column - 1))
            row, column = row - 1, column - 1
            continue
        steps.append(EditStep("insert", None, column - 1))
        column -= 1
    steps.reverse()
    return tuple(steps), costs[len(reference)][len(hypothesis)]


def error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float | None:
    """Edit distance over reference length; ``None`` when there is no reference."""

    if not reference:
        return None
    _, distance = edit_alignment(reference, hypothesis)
    return distance / len(reference)


def word_tokens(text: str) -> tuple[str, ...]:
    return tuple(token for token in unicodedata.normalize("NFC", text).split() if token)


def syllable_tokens(text: str) -> tuple[str, ...]:
    """Precomposed Hangul syllables, whitespace and punctuation dropped."""

    return tuple(
        character
        for character in unicodedata.normalize("NFC", text)
        if _is_hangul_syllable(character)
    )


def content_wer(reference_text: str, hypothesis_text: str) -> float | None:
    return error_rate(word_tokens(reference_text), word_tokens(hypothesis_text))


def content_cer(reference_text: str, hypothesis_text: str) -> float | None:
    return error_rate(syllable_tokens(reference_text), syllable_tokens(hypothesis_text))


def content_score(reference_text: str, hypothesis_text: str) -> float | None:
    """Agreement in [0, 1] as ``1 - CER``, floored at 0.

    Expressed as a score because the gate compares it against a frozen minimum;
    it is the same measurement as the CER reported in metrics.
    """

    cer = content_cer(reference_text, hypothesis_text)
    if cer is None:
        return None
    return max(0.0, 1.0 - cer)


@dataclass(frozen=True, slots=True)
class AttributedSpan:
    """A prompt syllable index tied to the frames that emitted it."""

    syllable_index: int
    start_ms: int
    end_ms: int
    confidence: float


def attribute_spans(
    *,
    decoding: CtcDecoding,
    prompt_syllables: Sequence[str],
    frame_ms: float = DEFAULT_FRAME_MS,
) -> tuple[tuple[AttributedSpan, ...], tuple[int, ...]]:
    """Map matched prompt syllables onto frame ranges.

    Returns the attributable spans and the prompt syllable indices that could
    not be attributed (substituted or deleted). Only ``match`` steps produce a
    span: a substitution means the learner said something else there, so the
    emitted frames belong to that other syllable, not to the prompt's.
    """

    segments = decoding.syllable_segments
    hypothesis_syllables = tuple(segment.label for segment in segments)
    steps, _ = edit_alignment(prompt_syllables, hypothesis_syllables)

    spans: list[AttributedSpan] = []
    unattributed: list[int] = []
    for step in steps:
        if step.operation == "match":
            assert step.reference_index is not None
            assert step.hypothesis_index is not None
            segment = segments[step.hypothesis_index]
            spans.append(
                AttributedSpan(
                    syllable_index=step.reference_index,
                    start_ms=segment.start_ms(frame_ms),
                    end_ms=segment.end_ms(frame_ms),
                    confidence=segment.mean_probability,
                )
            )
        elif step.operation in ("substitute", "delete"):
            assert step.reference_index is not None
            unattributed.append(step.reference_index)
    return tuple(spans), tuple(unattributed)


def written_jamo_groups(
    syllables: Sequence[tuple[str, str, str | None]],
) -> tuple[tuple[str, ...], ...]:
    """Per-syllable written jamo, in onset/nucleus/coda order.

    These are the *written* jamo, not expected pronunciation: they exist only to
    establish which prompt syllable owns each expected phone.
    """

    groups: list[tuple[str, ...]] = []
    for onset, nucleus, coda in syllables:
        if not onset or not nucleus:
            raise CtcAlignmentError("a syllable needs an onset and a nucleus")
        groups.append((onset, nucleus) if not coda else (onset, nucleus, coda))
    if not groups:
        raise CtcAlignmentError("prompt has no syllable")
    return tuple(groups)


def expected_token_owners(
    *,
    written_groups: Sequence[Sequence[str]],
    expected_tokens: Sequence[str],
) -> tuple[int | None, ...]:
    """Assign each expected phone to the prompt syllable it belongs to.

    Expected phones come from a rule-applied grapheme-to-phoneme pass, so they do
    not line up one-to-one with the written jamo: a rule can insert, drop, or
    change a phone.  Aligning the two streams recovers ownership, which is what
    lets a *correct* rule-driven pronunciation still receive a span.

    An expected phone inserted by a rule inherits the syllable of the next
    written jamo it precedes — a tensification like 학교 -> ㅎㅏㄱㄲㅛ belongs to
    the syllable whose onset it strengthens — and falls back to the previous
    owner at the end of the stream.  Word-boundary markers own nothing.
    """

    written: list[str] = []
    owners_by_position: list[int] = []
    for index, group in enumerate(written_groups):
        for jamo in group:
            written.append(jamo)
            owners_by_position.append(index)
    if not written:
        raise CtcAlignmentError("prompt has no written jamo")

    phones = [token for token in expected_tokens if token != WORD_DELIMITER_TOKEN]
    steps, _ = edit_alignment(written, phones)
    owners: list[int | None] = [None] * len(phones)
    for step in steps:
        if step.operation in ("match", "substitute"):
            assert step.reference_index is not None
            assert step.hypothesis_index is not None
            owners[step.hypothesis_index] = owners_by_position[step.reference_index]
    # Inserted expected phones: attach to the next owned position, else the
    # previous one. Resolving forward first keeps a tensified onset with the
    # syllable it belongs to.
    for index in range(len(owners) - 1, -1, -1):
        if owners[index] is None and index + 1 < len(owners):
            owners[index] = owners[index + 1]
    for index in range(len(owners)):
        if owners[index] is None and index > 0:
            owners[index] = owners[index - 1]

    result: list[int | None] = []
    cursor = 0
    for token in expected_tokens:
        if token == WORD_DELIMITER_TOKEN:
            result.append(None)
            continue
        result.append(owners[cursor])
        cursor += 1
    return tuple(result)


def attribute_jamo_spans(
    *,
    decoding: CtcDecoding,
    expected_tokens: Sequence[str],
    owners: Sequence[int | None],
    syllable_count: int,
    frame_ms: float = DEFAULT_FRAME_MS,
) -> tuple[tuple[AttributedSpan, ...], tuple[int, ...]]:
    """Fold jamo-level CTC output into one span per prompt syllable.

    A syllable is attributable when at least one of its expected phones aligned
    to a decoded segment, whether that alignment was a match or a substitution:
    a substituted phone still tells us *when* the learner produced that syllable,
    which is exactly what coaching needs to highlight.  A syllable whose phones
    were all deleted has no frames and therefore no span.

    Confidence combines how confident the acoustic model was on the contributing
    segments with how much of the syllable aligned at all, so a syllable known
    from one phone out of three does not report the confidence of that one phone.
    """

    if len(owners) != len(expected_tokens):
        raise CtcAlignmentError("owner count must match the expected token count")
    if syllable_count <= 0:
        raise CtcAlignmentError("syllable_count must be positive")

    phones: list[str] = []
    phone_owners: list[int | None] = []
    for token, owner in zip(expected_tokens, owners, strict=True):
        if token == WORD_DELIMITER_TOKEN:
            continue
        phones.append(token)
        phone_owners.append(owner)
    if not phones:
        raise CtcAlignmentError("expected token stream has no phone")

    segments = decoding.syllable_segments
    steps, _ = edit_alignment(phones, [segment.label for segment in segments])

    contributions: dict[int, list[FrameSegment]] = {}
    expected_per_syllable: dict[int, int] = {}
    for owner in phone_owners:
        if owner is not None:
            expected_per_syllable[owner] = expected_per_syllable.get(owner, 0) + 1
    for step in steps:
        if step.operation not in ("match", "substitute"):
            continue
        assert step.reference_index is not None
        assert step.hypothesis_index is not None
        owner = phone_owners[step.reference_index]
        if owner is None:
            continue
        contributions.setdefault(owner, []).append(segments[step.hypothesis_index])

    spans: list[AttributedSpan] = []
    unattributed: list[int] = []
    for index in range(syllable_count):
        aligned = contributions.get(index)
        if not aligned:
            unattributed.append(index)
            continue
        expected_count = expected_per_syllable.get(index, len(aligned))
        coverage = min(1.0, len(aligned) / expected_count) if expected_count else 0.0
        mean_probability = sum(
            segment.mean_probability for segment in aligned
        ) / len(aligned)
        spans.append(
            AttributedSpan(
                syllable_index=index,
                start_ms=min(segment.start_ms(frame_ms) for segment in aligned),
                end_ms=max(segment.end_ms(frame_ms) for segment in aligned),
                confidence=max(0.0, min(1.0, mean_probability * coverage)),
            )
        )
    return tuple(spans), tuple(unattributed)


def alignment_confidence(
    *,
    spans: Sequence[AttributedSpan],
    prompt_syllable_count: int,
) -> float | None:
    """Mean span confidence scaled by prompt coverage.

    Both factors matter: high per-span confidence over a third of the prompt is
    not a confident alignment. ``None`` when nothing was attributed, because an
    empty alignment has no confidence value — not a zero one.
    """

    if prompt_syllable_count <= 0:
        raise CtcAlignmentError("prompt_syllable_count must be positive")
    if not spans:
        return None
    coverage = len(spans) / prompt_syllable_count
    mean_confidence = sum(span.confidence for span in spans) / len(spans)
    return max(0.0, min(1.0, mean_confidence * coverage))


def expected_phone_agreement(
    *,
    expected_tokens: Sequence[str],
    scorable_inventory: Iterable[str],
) -> Mapping[str, object]:
    """Report expected phones that the scoring inventory cannot label.

    A label the acoustic model has no class for cannot be scored, and silently
    merging it into a neighbouring class would change what is being judged.  The
    caller decides; this only makes the gap explicit.
    """

    inventory = set(scorable_inventory)
    outside = sorted(
        {
            token
            for token in expected_tokens
            if token != WORD_DELIMITER_TOKEN and token not in inventory
        }
    )
    return {
        "expected_token_count": len(expected_tokens),
        "labels_outside_inventory": tuple(outside),
        "agrees": not outside,
    }


__all__ = [
    "BLANK_TOKEN",
    "DEFAULT_FRAME_MS",
    "WORD_DELIMITER_TOKEN",
    "AttributedSpan",
    "CtcAlignmentError",
    "CtcDecoding",
    "CtcVocabulary",
    "EditStep",
    "FrameSegment",
    "alignment_confidence",
    "attribute_jamo_spans",
    "attribute_spans",
    "expected_token_owners",
    "written_jamo_groups",
    "content_cer",
    "content_score",
    "content_wer",
    "edit_alignment",
    "error_rate",
    "expected_phone_agreement",
    "greedy_ctc_decode",
    "syllable_tokens",
    "word_tokens",
]
