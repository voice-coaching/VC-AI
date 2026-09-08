"""Stage C: content agreement and syllable alignment from a CTC acoustic model.

The adapter is split from the model on purpose.  A :class:`CtcPosteriorBackend`
turns one recording into a frame-by-label probability matrix; everything that
decides what the learner is told lives in
:mod:`voice_coach.pronunciation_feedback.ctc_alignment` and is exercised without
a checkpoint.  That seam is what makes the decision logic auditable.

Every backend problem — missing checkpoint, revision drift, vocabulary digest
mismatch, unusable posteriors — raises ``content_backend_unavailable``.  Stage C
never degrades to "assume the learner read the prompt": the pipeline refuses to
judge pronunciation when it cannot establish what was said.

This adapter decodes freely and edit-aligns the result to the prompt, so it
attributes a span only to a syllable the decoder already recognised.
:mod:`.ctc_content_alignment` implements the same port by forced alignment
instead, which places every expected syllable — including the mispronounced ones
this adapter leaves unattributed — and is what ``build_default_pipeline`` wires.
Keep this one for the question it answers better: how far the recording drifted
from the prompt as free text.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

from ..ctc_alignment import (
    DEFAULT_FRAME_MS,
    CtcAlignmentError,
    CtcVocabulary,
    alignment_confidence,
    attribute_spans,
    content_cer,
    content_score,
    content_wer,
    greedy_ctc_decode,
    syllable_tokens,
)
from ..domain import (
    AlignmentObservation,
    FailureCode,
    MetricState,
    PromptSpec,
    SyllableSpan,
)
from ..errors import BackendUnavailableError

BACKEND_ID = "wav2vec2-xlsr-korean-syllable-ctc"


def _fail(detail: str) -> BackendUnavailableError:
    return BackendUnavailableError(FailureCode.CONTENT_BACKEND_UNAVAILABLE, detail)


@dataclass(frozen=True, slots=True)
class PosteriorFrames:
    """Frame-by-label probabilities for one recording."""

    probabilities: tuple[tuple[float, ...], ...]
    frame_ms: float


@runtime_checkable
class CtcPosteriorBackend(Protocol):
    """Turns one recording into CTC label probabilities."""

    @property
    def backend_id(self) -> str: ...

    @property
    def revision(self) -> str: ...

    def posteriors(self, audio_path: Path) -> PosteriorFrames: ...


@dataclass(frozen=True, slots=True)
class ContentAlignmentReport:
    """Metrics reported alongside the observation, never shown to a learner."""

    hypothesis_syllable_count: int
    content_wer: float | None
    content_cer: float | None
    attributed_syllable_count: int
    unattributed_syllable_indices: tuple[int, ...]
    frame_count: int


class WavCtcContentAlignment:
    """``ContentAlignmentPort`` over a syllable-level Korean CTC model."""

    def __init__(
        self,
        *,
        backend: CtcPosteriorBackend,
        vocabulary: CtcVocabulary,
        expected_vocabulary_digest: str | None = None,
    ) -> None:
        if expected_vocabulary_digest is not None and (
            vocabulary.digest != expected_vocabulary_digest
        ):
            raise _fail(
                "CTC vocabulary digest differs from the pinned value: "
                f"expected {expected_vocabulary_digest}, got {vocabulary.digest}"
            )
        self._backend = backend
        self._vocabulary = vocabulary
        self._last_report: ContentAlignmentReport | None = None

    @property
    def backend_id(self) -> str:
        return self._backend.backend_id

    @property
    def revision(self) -> str:
        return self._backend.revision

    @property
    def last_report(self) -> ContentAlignmentReport | None:
        """Metrics from the most recent :meth:`analyze` call, or ``None``."""

        return self._last_report

    def analyze(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
    ) -> AlignmentObservation:
        prompt_syllables = syllable_tokens(prompt_spec.normalized_text)
        if not prompt_syllables:
            raise _fail("prompt has no Hangul syllable to align against")

        frames = self._backend.posteriors(audio_path)
        if not isinstance(frames, PosteriorFrames):
            raise _fail("posterior backend returned an unexpected type")
        if frames.frame_ms <= 0:
            raise _fail("posterior frame duration must be positive")
        try:
            decoding = greedy_ctc_decode(frames.probabilities, self._vocabulary)
        except CtcAlignmentError as error:
            raise _fail(f"CTC posteriors are unusable: {error}") from error

        if not decoding.segments:
            # Decoded silence. The recording is real but no content was
            # recognised, so content stays unmeasured rather than scoring 0.
            self._last_report = ContentAlignmentReport(
                hypothesis_syllable_count=0,
                content_wer=None,
                content_cer=None,
                attributed_syllable_count=0,
                unattributed_syllable_indices=tuple(range(len(prompt_syllables))),
                frame_count=decoding.frame_count,
            )
            return AlignmentObservation(
                backend_id=self.backend_id,
                revision=self.revision,
                content_score=None,
                content_state=MetricState.MISSING,
                alignment_confidence=None,
                syllable_spans=(),
            )

        reference_text = prompt_spec.normalized_text
        spans, unattributed = attribute_spans(
            decoding=decoding,
            prompt_syllables=prompt_syllables,
            frame_ms=frames.frame_ms,
        )
        confidence = alignment_confidence(
            spans=spans,
            prompt_syllable_count=len(prompt_syllables),
        )
        self._last_report = ContentAlignmentReport(
            hypothesis_syllable_count=len(decoding.syllable_segments),
            content_wer=content_wer(reference_text, decoding.hypothesis),
            content_cer=content_cer(reference_text, decoding.hypothesis),
            attributed_syllable_count=len(spans),
            unattributed_syllable_indices=unattributed,
            frame_count=decoding.frame_count,
        )
        return AlignmentObservation(
            backend_id=self.backend_id,
            revision=self.revision,
            content_score=content_score(reference_text, decoding.hypothesis),
            content_state=MetricState.OBSERVED,
            alignment_confidence=confidence,
            syllable_spans=tuple(
                SyllableSpan(
                    syllable_index=span.syllable_index,
                    start_ms=span.start_ms,
                    end_ms=span.end_ms,
                    confidence=span.confidence,
                    state=MetricState.OBSERVED,
                )
                for span in spans
            ),
        )


class UnavailableContentAlignment:
    """Explicit stand-in for environments with no CTC checkpoint.

    Declaring the gap is the point: without a content backend the pipeline must
    stop before Stage D rather than judge pronunciation against an unverified
    transcript.
    """

    def __init__(self, detail: str | None = None) -> None:
        self._detail = detail or (
            "no Korean syllable CTC checkpoint is available in this environment"
        )

    def analyze(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
    ) -> AlignmentObservation:
        del audio_path, prompt_spec
        raise _fail(self._detail)


def load_vocabulary(
    path: str | Path,
    *,
    expected_digest: str | None = None,
) -> CtcVocabulary:
    """Load a pinned CTC label set from its artifact.

    The artifact carries its own digest so a checkpoint can never be paired with
    a relabelled output layer.
    """

    vocabulary_path = Path(path)
    if not vocabulary_path.is_file():
        raise _fail("CTC vocabulary artifact is unavailable")
    try:
        document = json.loads(vocabulary_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _fail("CTC vocabulary artifact is not valid JSON") from error
    if not isinstance(document, dict):
        raise _fail("CTC vocabulary artifact must be an object")
    tokens = document.get("tokens")
    if not isinstance(tokens, list) or not all(
        isinstance(token, str) for token in tokens
    ):
        raise _fail("CTC vocabulary tokens must be an array of strings")
    try:
        vocabulary = CtcVocabulary(tokens=tuple(tokens))
    except CtcAlignmentError as error:
        raise _fail(f"CTC vocabulary is invalid: {error}") from error
    declared = document.get("digest")
    if declared is not None and declared != vocabulary.digest:
        raise _fail("CTC vocabulary artifact digest does not match its tokens")
    if expected_digest is not None and vocabulary.digest != expected_digest:
        raise _fail("CTC vocabulary digest differs from the pinned value")
    return vocabulary


class Wav2Vec2CtcPosteriorBackend:
    """Frame posteriors from a fine-tuned wav2vec2/XLS-R CTC checkpoint.

    torch and transformers are imported lazily so that the decision logic, the
    adapter, and their tests stay runnable in environments without them.
    """

    def __init__(
        self,
        *,
        checkpoint_dir: str | Path,
        revision: str,
        label_count: int,
        backend_id: str = BACKEND_ID,
        sample_rate: int = 16_000,
        frame_ms: float = DEFAULT_FRAME_MS,
        device: str = "cpu",
    ) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        if not self._checkpoint_dir.is_dir():
            raise _fail("CTC checkpoint directory is unavailable")
        if not revision:
            raise _fail("CTC checkpoint revision must be pinned")
        self._revision = revision
        self._label_count = label_count
        self._backend_id = backend_id
        self._sample_rate = sample_rate
        self._frame_ms = frame_ms
        self._device = device
        self._model = None

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def revision(self) -> str:
        return self._revision

    def _load(self):  # pragma: no cover - requires a checkpoint on disk
        if self._model is not None:
            return self._model
        try:
            import torch
            from transformers import Wav2Vec2ForCTC
        except ImportError as error:
            raise _fail("the CTC runtime is not installed") from error
        model = Wav2Vec2ForCTC.from_pretrained(
            self._checkpoint_dir,
            local_files_only=True,
        )
        if model.config.vocab_size != self._label_count:
            raise _fail(
                "checkpoint output layer does not match the pinned label count"
            )
        model.eval()
        model.to(self._device)
        self._torch = torch
        self._model = model
        return model

    def posteriors(
        self,
        audio_path: Path,
    ) -> PosteriorFrames:  # pragma: no cover - requires a checkpoint on disk
        model = self._load()
        torch = self._torch
        from voice_coach.data import decode_audio

        decoded = decode_audio(audio_path, target_sample_rate=self._sample_rate)
        waveform = getattr(decoded, "samples", decoded)
        with torch.inference_mode():
            logits = model(
                torch.as_tensor(waveform, dtype=torch.float32).unsqueeze(0)
            ).logits
            probabilities = torch.softmax(logits.float(), dim=-1)[0]
        rows: Sequence[Sequence[float]] = probabilities.tolist()
        return PosteriorFrames(
            probabilities=tuple(tuple(float(value) for value in row) for row in rows),
            frame_ms=self._frame_ms,
        )


__all__ = [
    "BACKEND_ID",
    "ContentAlignmentReport",
    "CtcPosteriorBackend",
    "PosteriorFrames",
    "UnavailableContentAlignment",
    "Wav2Vec2CtcPosteriorBackend",
    "WavCtcContentAlignment",
    "load_vocabulary",
]
