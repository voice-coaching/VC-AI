"""``ContentAlignmentPort`` over the pinned Korean syllable-CTC backend.

Two different questions get two different measurements here, because collapsing
them is the mistake that makes a coach unusable:

``content_score`` answers *did the learner read this sentence?*  It compares the
model's free decode with the prompt at the jamo level, so a syllable read with
one wrong vowel still counts as mostly the right syllable while a different
sentence collapses the score.  A pronunciation-derived number would fail an L2
learner for reading the right text badly, and the gate turns a low content score
into "read text does not match the prompt" — the one message that must never be
shown to someone who read exactly what was asked.

``alignment_confidence`` answers *do I trust these time boundaries?*  It is
built from vocabulary coverage and whether the aligner had room to place each
syllable, not from how well the syllables were pronounced.  Otherwise a
struggling learner would be told to re-record because the boundaries were
"uncertain", when the boundaries were fine and the pronunciation was the finding.

Syllables outside the model's 1,203-syllable vocabulary are reported as
unmeasured rather than mapped to ``[UNK]``: the unknown token's posterior means
"a syllable I was never trained on", which is not evidence about this one.

Why this exists next to :mod:`.content_alignment_ctc`
-----------------------------------------------------
That module implements the same port over the same model family by decoding
freely and then edit-aligning the hypothesis to the prompt.  It is the better
answer when the question is *what did the recording say* — but it can only place
a span on a syllable the decoder already got right, and marks the rest
unattributed.  The syllables this pipeline most needs to locate are exactly the
ones the decoder got wrong, because those are the ones worth coaching.  Forced
alignment places every expected syllable whether or not the decoder agreed, and
scores each one by how much the frames argued against it, so a mispronounced
syllable ends up with a span *and* a reason.  Both modules are kept: this one is
what ``build_default_pipeline`` wires.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path

from ..domain import (
    AlignmentObservation,
    FailureCode,
    MetricState,
    PromptSpec,
    SyllableSpan,
)
from ..errors import BackendUnavailableError
from .ctc_acoustic_backend import WORD_DELIMITER, CtcAcousticBackend
from .ctc_alignment import (
    ForcedAlignment,
    ForcedAlignmentError,
    ctc_forced_alignment,
    expand_spans,
    greedy_collapse,
)
from .rate_band import DEFAULT_RATE_BAND_PATH, slowest_syllable_seconds

BACKEND_ID = "wav2vec2-xlsr-korean-ctc-alignment"
#: A syllable whose slot came out shorter than this was crammed: at the slow
#: edge of the measured band a syllable runs ~260 ms, and no Korean syllable is
#: read in under 60 ms, so a slot this short means the aligner had nowhere to
#: put it — the boundary is not supported even if the identity is.
MINIMUM_SUPPORTED_MS = 60
_HANGUL_FIRST = "가"
_HANGUL_LAST = "힣"
_ONSETS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_NUCLEI = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_CODAS = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def jamo_sequence(text: str) -> str:
    """Decompose Hangul syllables to compatibility jamo, keeping other chars.

    Comparison happens here rather than on whole syllables so that one wrong
    vowel costs one third of a syllable instead of all of it.
    """

    out: list[str] = []
    for char in text:
        if _HANGUL_FIRST <= char <= _HANGUL_LAST:
            remainder = ord(char) - ord(_HANGUL_FIRST)
            out.append(_ONSETS[remainder // (28 * 21)])
            out.append(_NUCLEI[(remainder // 28) % 21])
            coda = _CODAS[remainder % 28]
            if coda != " ":
                out.append(coda)
        elif not char.isspace():
            out.append(char)
    return "".join(out)


def jamo_similarity(hypothesis: str, reference: str) -> float:
    """Normalized jamo agreement in ``[0, 1]``; 1.0 is an exact match."""

    reference_jamo = jamo_sequence(reference)
    if not reference_jamo:
        return 0.0
    return float(
        difflib.SequenceMatcher(
            None, jamo_sequence(hypothesis), reference_jamo, autojunk=False
        ).ratio()
    )


@dataclass(frozen=True, slots=True)
class AlignedPrompt:
    """The alignment plus the bookkeeping the S1 scorer needs to reuse it."""

    alignment: ForcedAlignment
    #: ``token_index -> prompt syllable index``; delimiters have no entry.
    syllable_of_token: dict[int, int]
    #: Prompt syllable indices the vocabulary does not cover.
    unmapped_syllables: tuple[int, ...]
    transcript: str
    #: Per token, the contiguous ``(start_frame, end_frame)`` slot after blank
    #: absorption.  Timing reads these; identity comparisons read the anchors on
    #: ``alignment`` instead, because a blank frame is evidence about no
    #: syllable and averaging it in would shrink every margin alike.
    spans: tuple[tuple[int, int], ...]


class CtcContentAlignment:
    """``ContentAlignmentPort`` implementation over frame posteriors."""

    def __init__(
        self,
        backend: CtcAcousticBackend,
        *,
        repository_root: str | Path | None = None,
        rate_band_path: str | Path | None = None,
    ) -> None:
        self._backend = backend
        band = Path(rate_band_path or DEFAULT_RATE_BAND_PATH)
        if not band.is_absolute():
            if repository_root is None:
                raise ValueError(
                    "repository_root is required for a relative rate_band_path"
                )
            band = Path(repository_root) / band
        self._rate_band_path = band

    @property
    def backend_id(self) -> str:
        return BACKEND_ID

    @property
    def revision(self) -> str:
        return self._backend.revision

    # ------------------------------------------------------------- targeting

    def _target_tokens(
        self, prompt_spec: PromptSpec
    ) -> tuple[list[int], dict[int, int], list[int]]:
        """Prompt syllables as vocabulary ids, with word delimiters between words."""

        delimiter = self._backend.token_id(WORD_DELIMITER)
        text = prompt_spec.normalized_text
        tokens: list[int] = []
        syllable_of_token: dict[int, int] = {}
        unmapped: list[int] = []
        previous_end: int | None = None
        for slot in prompt_spec.syllables:
            if (
                delimiter is not None
                and previous_end is not None
                and " " in text[previous_end : slot.char_start]
            ):
                tokens.append(delimiter)
            previous_end = slot.char_end
            token_id = self._backend.token_id(slot.syllable)
            if token_id is None:
                unmapped.append(slot.index)
                continue
            syllable_of_token[len(tokens)] = slot.index
            tokens.append(token_id)
        return tokens, syllable_of_token, unmapped

    def align(self, *, audio_path: Path, prompt_spec: PromptSpec) -> AlignedPrompt:
        """Force-align the prompt and keep the per-token bookkeeping.

        Exposed separately from :meth:`analyze` so the S1 scorer reaches the
        same alignment the learner's spans came from.  It recomputes rather than
        caches, which is safe because both inputs are fixed: the posteriors come
        from the backend's content-addressed cache and the Viterbi pass is
        deterministic.  What must never happen is a *second* forward pass, which
        is why the backend is shared rather than constructed twice.
        """

        posteriors = self._backend.posteriors(audio_path)
        tokens, syllable_of_token, unmapped = self._target_tokens(prompt_spec)
        if not tokens:
            raise BackendUnavailableError(
                FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                "no prompt syllable is covered by the acoustic vocabulary",
            )
        try:
            alignment = ctc_forced_alignment(
                posteriors.log_posteriors, tokens, blank_id=self._backend.blank_id
            )
        except ForcedAlignmentError as exc:
            raise BackendUnavailableError(
                FailureCode.ALIGNMENT_LOW_CONFIDENCE,
                f"prompt could not be aligned to the recording: {exc}",
            ) from exc
        vocabulary = self._backend.vocabulary()
        by_id = {value: key for key, value in vocabulary.items()}
        transcript = "".join(
            by_id.get(token, "") if by_id.get(token) != WORD_DELIMITER else " "
            for token in greedy_collapse(
                posteriors.log_posteriors, blank_id=self._backend.blank_id
            )
        ).strip()
        absorbed_frames = int(
            round(
                slowest_syllable_seconds(self._rate_band_path)
                / posteriors.hop_seconds
            )
        )
        return AlignedPrompt(
            alignment=alignment,
            syllable_of_token=syllable_of_token,
            unmapped_syllables=tuple(unmapped),
            transcript=transcript,
            spans=expand_spans(
                alignment, maximum_absorbed_frames=absorbed_frames
            ),
        )

    # --------------------------------------------------------------- the port

    def analyze(
        self, *, audio_path: Path, prompt_spec: PromptSpec
    ) -> AlignmentObservation:
        aligned = self.align(audio_path=audio_path, prompt_spec=prompt_spec)
        posteriors = self._backend.posteriors(audio_path)

        spans: list[SyllableSpan] = []
        supported = 0
        for position, token in enumerate(aligned.alignment.tokens):
            syllable_index = aligned.syllable_of_token.get(token.token_index)
            if syllable_index is None:
                continue
            start_frame, end_frame = aligned.spans[position]
            start_ms = posteriors.frame_start_ms(start_frame)
            end_ms = posteriors.frame_end_ms(end_frame)
            slot_is_supported = (end_ms - start_ms) >= MINIMUM_SUPPORTED_MS
            supported += int(slot_is_supported)
            spans.append(
                SyllableSpan(
                    syllable_index=syllable_index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    confidence=token.posterior_ratio,
                    state=(
                        MetricState.OBSERVED
                        if slot_is_supported
                        else MetricState.LOW_CONFIDENCE
                    ),
                )
            )

        total_syllables = len(prompt_spec.syllables)
        coverage = len(spans) / total_syllables if total_syllables else 0.0
        boundary_support = supported / len(spans) if spans else 0.0
        alignment_confidence = coverage * boundary_support

        content_score = jamo_similarity(
            aligned.transcript, prompt_spec.normalized_text
        )
        return AlignmentObservation(
            backend_id=BACKEND_ID,
            revision=self._backend.revision,
            content_score=content_score,
            content_state=MetricState.OBSERVED,
            alignment_confidence=alignment_confidence,
            syllable_spans=tuple(spans),
        )
