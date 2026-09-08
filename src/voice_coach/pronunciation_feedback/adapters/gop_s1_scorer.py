"""``S1ScorerPort`` over CTC goodness-of-pronunciation and the alignment's timing.

Every number here comes from the alignment the learner's highlight is drawn
from, so a score and the span it justifies can never describe different frames.

The four detectors are chosen to match the frozen advice allowlist, and each one
is a comparison the syllable vocabulary makes directly answerable:

``final_consonant_deletion``
    The vocabulary holds whole syllables, so the coda-less counterpart of an
    expected syllable is itself a vocabulary entry.  Asking whether ``바`` beat
    ``받`` on the frames aligned to ``받`` is a direct test for a dropped coda,
    not an inference from a phone posterior.
``vowel_quality``
    Same comparison against the counterparts that keep the onset and coda and
    change only the nucleus; the winning competitor names the vowel that was
    heard instead.
``misplaced_pause``
    A silence the aligner had to absorb *between two syllables of one eojeol* —
    a place the prompt gives no boundary for.
``duration_short``
    A syllable far shorter than the same speaker's own median in the same
    recording, which is a within-utterance comparison and so does not import a
    reference speaking rate.

One exclusion is load-bearing.  At a resyllabification site — ``닫아요`` read
[다다요] — the written coda is not pronounced in that syllable at all, so asking
whether it was deleted has no correct answer and the finding would be about
Korean rather than about the learner.  :mod:`.korean_surface` names those sites
and the coda detector skips them; it also records why the *alignment* target
stays the written form, which is a measured result rather than a preference.

Two conventions keep the drafts readable.  ``severity`` is ``1 - exp(-margin)``:
zero when the evidence is neutral and saturating as it grows, so no clipping
constant decides the top of the scale.  ``confidence`` is the share of the
two-way posterior mass sitting on the error hypothesis, ``sigmoid(margin)``,
which is 0.5 at neutral evidence and rises with it.  Neither is a tuned
threshold; the frozen ``min_issue_*`` thresholds in the gate do the cutting.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np

from ..domain import (
    AlignmentObservation,
    CtcSoftInformation,
    DiagnosticIssueDraft,
    FailureCode,
    MetricState,
    PromptSpec,
    S1Branch,
    S1ScoreBundle,
)
from ..errors import BackendUnavailableError
from .ctc_acoustic_backend import CtcAcousticBackend
from .ctc_alignment import TokenAlignment
from .ctc_content_alignment import CtcContentAlignment
from .korean_surface import resyllabification_sites
from .rate_band import DEFAULT_RATE_BAND_PATH, read_rate_band

PROSODY_BACKEND_ID = "alignment-timing-prosody-v1"
PROSODY_REVISION = "2026-08-09"
SEGMENTAL_BRANCH = "segmental"
FLUENCY_BRANCH = "fluency"
RHYTHM_BRANCH = "rhythm"
#: A gap this long between two syllables of one eojeol is a pause a reader put
#: there, not the closure of a stop.  Korean stop closures run well under 150 ms
#: at read-speech rates, and the shortest band rate here implies a syllable every
#: ~260 ms, so this sits below one syllable slot and above a closure.
INTRA_WORD_PAUSE_MS = 180
#: Below this many aligned syllables the within-utterance median is too thin to
#: call any one syllable short.
MINIMUM_SYLLABLES_FOR_MEDIAN = 6
_HANGUL_BASE = ord("가")
_NUCLEUS_COUNT = 21
_CODA_COUNT = 28


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _severity(margin: float) -> float:
    """Saturating ``[0, 1)`` reading of a positive log-odds margin."""

    return float(1.0 - math.exp(-max(0.0, margin)))


def compose_syllable(onset: int, nucleus: int, coda: int) -> str:
    return chr(_HANGUL_BASE + (onset * _NUCLEUS_COUNT + nucleus) * _CODA_COUNT + coda)


def decompose_syllable(syllable: str) -> tuple[int, int, int]:
    remainder = ord(syllable) - _HANGUL_BASE
    return (
        remainder // (_NUCLEUS_COUNT * _CODA_COUNT),
        (remainder // _CODA_COUNT) % _NUCLEUS_COUNT,
        remainder % _CODA_COUNT,
    )


@dataclass(frozen=True, slots=True)
class Competitor:
    """A vocabulary syllable that could have been heard instead."""

    syllable: str
    token_id: int
    margin: float


@dataclass(frozen=True, slots=True)
class ScoredSyllable:
    """One prompt syllable with both of its frame ranges.

    ``anchor`` is where the model committed to this syllable's identity and is
    what every posterior comparison uses.  ``start_ms``/``end_ms`` are the slot
    after blank absorption and are what timing and highlighting use.  Keeping
    them apart is the point: averaging identity posteriors over absorbed silence
    would weaken every margin, and reporting the anchor as the extent would make
    every syllable look 25 ms long.
    """

    syllable_index: int
    anchor: TokenAlignment
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class GopS1Scorer:
    """``S1ScorerPort`` implementation over the CTC alignment."""

    def __init__(
        self,
        *,
        backend: CtcAcousticBackend,
        content_alignment: CtcContentAlignment,
        repository_root: str | Path,
        rate_band_path: str | Path | None = None,
    ) -> None:
        self._backend = backend
        self._content_alignment = content_alignment
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        self._root = root
        self._rate_band_path = Path(rate_band_path or DEFAULT_RATE_BAND_PATH)
        if not self._rate_band_path.is_absolute():
            self._rate_band_path = root / self._rate_band_path

    # ------------------------------------------------------------ competitors

    def _competitor(
        self,
        *,
        expected: str,
        frames: slice,
        log_posteriors: np.ndarray,
        expected_token_id: int,
        candidates: list[str],
    ) -> Competitor | None:
        """Best candidate syllable on these frames, if any beat the expected one."""

        window = log_posteriors[frames]
        expected_mean = float(window[:, expected_token_id].mean())
        best: Competitor | None = None
        for syllable in candidates:
            token_id = self._backend.token_id(syllable)
            if token_id is None:
                continue
            margin = float(window[:, token_id].mean()) - expected_mean
            if margin > 0.0 and (best is None or margin > best.margin):
                best = Competitor(syllable=syllable, token_id=token_id, margin=margin)
        return best

    def _coda_deletion(
        self, *, expected: str, frames: slice, log_posteriors: np.ndarray, token_id: int
    ) -> Competitor | None:
        onset, nucleus, coda = decompose_syllable(expected)
        if coda == 0:
            return None
        return self._competitor(
            expected=expected,
            frames=frames,
            log_posteriors=log_posteriors,
            expected_token_id=token_id,
            candidates=[compose_syllable(onset, nucleus, 0)],
        )

    def _vowel_substitution(
        self, *, expected: str, frames: slice, log_posteriors: np.ndarray, token_id: int
    ) -> Competitor | None:
        onset, nucleus, coda = decompose_syllable(expected)
        candidates = [
            compose_syllable(onset, other, coda)
            for other in range(_NUCLEUS_COUNT)
            if other != nucleus
        ]
        return self._competitor(
            expected=expected,
            frames=frames,
            log_posteriors=log_posteriors,
            expected_token_id=token_id,
            candidates=candidates,
        )

    # ---------------------------------------------------------------- the port

    def score(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        alignment: AlignmentObservation,
    ) -> S1ScoreBundle:
        aligned = self._content_alignment.align(
            audio_path=audio_path, prompt_spec=prompt_spec
        )
        posteriors = self._backend.posteriors(audio_path)
        log_posteriors = posteriors.log_posteriors

        scored: list[ScoredSyllable] = []
        for position, token in enumerate(aligned.alignment.tokens):
            syllable_index = aligned.syllable_of_token.get(token.token_index)
            if syllable_index is None:
                continue
            start_frame, end_frame = aligned.spans[position]
            scored.append(
                ScoredSyllable(
                    syllable_index=syllable_index,
                    anchor=token,
                    start_ms=posteriors.frame_start_ms(start_frame),
                    end_ms=posteriors.frame_end_ms(end_frame),
                )
            )
        if not scored:
            raise BackendUnavailableError(
                FailureCode.S1_REQUIRED_METRIC_MISSING,
                "no prompt syllable survived alignment",
            )

        # How decisively the acoustic model committed to *any* syllable on the
        # anchor frames.  This is confidence in the evidence, not in the
        # pronunciation: a clearly articulated wrong syllable is decisive, and a
        # correct syllable buried in noise is not.  Keeping the two apart is
        # what stops a struggling learner from being told to re-record because
        # the analysis was "uncertain" when it was in fact quite certain.
        anchor_frames = np.concatenate(
            [
                np.arange(item.anchor.start_frame, item.anchor.end_frame + 1)
                for item in scored
            ]
        )
        decisiveness = float(np.exp(log_posteriors[anchor_frames].max(axis=1)).mean())
        coverage = len(scored) / max(1, len(prompt_spec.syllables))

        branches = [
            self._segmental_branch(scored, decisiveness=decisiveness),
            *self._timing_branches(
                scored=scored,
                prompt_spec=prompt_spec,
                decisiveness=decisiveness,
                coverage=coverage,
            ),
        ]
        issues = self._issues(
            scored=scored,
            prompt_spec=prompt_spec,
            log_posteriors=log_posteriors,
        )

        confidences = [
            branch.confidence
            for branch in branches
            if branch.state is MetricState.OBSERVED and branch.confidence is not None
        ]
        overall = min(confidences) if confidences else None
        return S1ScoreBundle(
            branches=tuple(branches),
            overall_confidence=overall,
            confidence_state=(
                MetricState.OBSERVED if overall is not None else MetricState.MISSING
            ),
            issues=issues,
            soft_information=self._soft_information(
                scored=scored, prompt_spec=prompt_spec
            ),
        )

    # --------------------------------------------------------------- branches

    def _soft_information(
        self,
        *,
        scored: list[ScoredSyllable],
        prompt_spec: PromptSpec,
    ) -> tuple[CtcSoftInformation, ...]:
        """Expose XLS-R distribution summaries without recalibrating the gate."""

        summaries: list[CtcSoftInformation] = []
        for item in scored:
            slot = prompt_spec.syllable(item.syllable_index)
            if slot is None:
                continue
            summaries.append(
                CtcSoftInformation(
                    syllable_index=item.syllable_index,
                    target_syllable=slot.syllable,
                    target_log_posterior=item.anchor.mean_log_posterior,
                    gop_margin=item.anchor.gop_margin,
                    posterior_entropy=item.anchor.posterior_entropy,
                    target_is_top1=item.anchor.target_is_top1,
                    frame_count=item.anchor.frame_count,
                    backend_id=self._backend.backend_id,
                    revision=self._backend.revision,
                )
            )
        return tuple(summaries)

    @staticmethod
    def _soft_evidence_tags(item: ScoredSyllable) -> tuple[str, ...]:
        """Stable soft-distribution tags for a selected segmental issue."""

        return (
            f"target_log_posterior={item.anchor.mean_log_posterior:.6f}",
            f"gop_margin={item.anchor.gop_margin:.6f}",
            f"posterior_entropy={item.anchor.posterior_entropy:.6f}",
            f"target_is_top1={str(item.anchor.target_is_top1).lower()}",
        )

    def _segmental_branch(
        self, scored: list[ScoredSyllable], *, decisiveness: float
    ) -> S1Branch:
        """Geometric mean of the per-syllable posterior ratio."""

        ratios = np.array([item.anchor.posterior_ratio for item in scored])
        score = float(np.exp(np.log(np.maximum(ratios, 1e-12)).mean()))
        # Two things have to hold for this average to mean anything: enough
        # syllables to average over, and frames the model was actually sure
        # about.  The weaker of the two governs.
        confidence = float(
            min(len(scored) / MINIMUM_SYLLABLES_FOR_MEDIAN, decisiveness)
        )
        return S1Branch(
            name=SEGMENTAL_BRANCH,
            score=score,
            confidence=confidence,
            state=MetricState.OBSERVED,
            backend_id=self._backend.backend_id,
            revision=self._backend.revision,
        )

    def _timing_branches(
        self,
        *,
        scored: list[ScoredSyllable],
        prompt_spec: PromptSpec,
        decisiveness: float,
        coverage: float,
    ) -> list[S1Branch]:
        low, high = read_rate_band(self._rate_band_path)
        first = scored[0].start_ms
        last = scored[-1].end_ms
        spoken_seconds = max(0.0, (last - first) / 1000.0)
        syllable_count = len(prompt_spec.syllables)

        if spoken_seconds <= 0.0:
            fluency = S1Branch(
                name=FLUENCY_BRANCH,
                score=None,
                confidence=None,
                state=MetricState.MISSING,
                backend_id=PROSODY_BACKEND_ID,
                revision=PROSODY_REVISION,
            )
        else:
            rate = syllable_count / spoken_seconds
            centre = math.sqrt(low * high)
            # Distance in log-rate, so being twice too fast and half too fast
            # cost the same, and the band edges score 1.0.
            half_width = math.log(high / low) / 2.0
            excess = max(0.0, abs(math.log(rate / centre)) - half_width)
            fluency = S1Branch(
                name=FLUENCY_BRANCH,
                score=float(math.exp(-excess)),
                # The rate divides the *prompt's* syllable count by the aligned
                # span, so a syllable that never got a slot makes the rate less
                # trustworthy in direct proportion.
                confidence=float(min(coverage, decisiveness)),
                state=MetricState.OBSERVED,
                backend_id=PROSODY_BACKEND_ID,
                revision=PROSODY_REVISION,
                config_hash=f"band={low:.3f}-{high:.3f}",
            )

        durations = np.array([item.duration_ms for item in scored], dtype=np.float64)
        if durations.size < MINIMUM_SYLLABLES_FOR_MEDIAN or durations.mean() <= 0:
            rhythm = S1Branch(
                name=RHYTHM_BRANCH,
                score=None,
                confidence=None,
                state=MetricState.LOW_CONFIDENCE,
                backend_id=PROSODY_BACKEND_ID,
                revision=PROSODY_REVISION,
            )
        else:
            # Coefficient of variation of syllable duration: read speech keeps
            # it low, halting speech raises it.  Reported as 1/(1+cv) so the
            # axis reads in the same direction as the others.
            variation = float(durations.std() / durations.mean())
            rhythm = S1Branch(
                name=RHYTHM_BRANCH,
                score=float(1.0 / (1.0 + variation)),
                confidence=float(min(durations.size / 10.0, decisiveness)),
                state=MetricState.OBSERVED,
                backend_id=PROSODY_BACKEND_ID,
                revision=PROSODY_REVISION,
            )
        return [fluency, rhythm]

    # ----------------------------------------------------------------- issues

    def _issues(
        self,
        *,
        scored: list[ScoredSyllable],
        prompt_spec: PromptSpec,
        log_posteriors: np.ndarray,
    ) -> tuple[DiagnosticIssueDraft, ...]:
        drafts: list[DiagnosticIssueDraft] = []
        text = prompt_spec.normalized_text
        resyllabified = resyllabification_sites(prompt_spec)

        for item in scored:
            slot = prompt_spec.syllable(item.syllable_index)
            if slot is None:
                continue
            token = item.anchor
            syllable_index = item.syllable_index
            frames = slice(token.start_frame, token.end_frame + 1)
            start_ms, end_ms = item.start_ms, item.end_ms

            deletion = (
                None
                if syllable_index in resyllabified
                else self._coda_deletion(
                    expected=slot.syllable,
                    frames=frames,
                    log_posteriors=log_posteriors,
                    token_id=token.token_id,
                )
            )
            if deletion is not None:
                drafts.append(
                    DiagnosticIssueDraft(
                        issue_type="final_consonant_deletion",
                        syllable_index=syllable_index,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        severity=_severity(deletion.margin),
                        confidence=_sigmoid(deletion.margin),
                        evidence=(
                            f"ctc_posterior_margin={deletion.margin:.3f}",
                            *self._soft_evidence_tags(item),
                            f"expected_syllable={slot.syllable}",
                            f"heard_syllable={deletion.syllable}",
                            f"dropped_coda={slot.coda}",
                        ),
                    )
                )
                continue  # a dropped coda already explains this syllable

            substitution = self._vowel_substitution(
                expected=slot.syllable,
                frames=frames,
                log_posteriors=log_posteriors,
                token_id=token.token_id,
            )
            if substitution is not None:
                _, heard_nucleus, _ = decompose_syllable(substitution.syllable)
                drafts.append(
                    DiagnosticIssueDraft(
                        issue_type="vowel_quality",
                        syllable_index=syllable_index,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        severity=_severity(substitution.margin),
                        confidence=_sigmoid(substitution.margin),
                        evidence=(
                            f"ctc_posterior_margin={substitution.margin:.3f}",
                            *self._soft_evidence_tags(item),
                            f"expected_syllable={slot.syllable}",
                            f"heard_syllable={substitution.syllable}",
                            f"expected_nucleus={slot.nucleus}",
                        ),
                    )
                )

        drafts.extend(
            self._timing_issues(scored=scored, prompt_spec=prompt_spec, text=text)
        )
        return tuple(drafts)

    def _timing_issues(
        self,
        *,
        scored: list[ScoredSyllable],
        prompt_spec: PromptSpec,
        text: str,
    ) -> list[DiagnosticIssueDraft]:
        drafts: list[DiagnosticIssueDraft] = []
        durations = [item.duration_ms for item in scored]
        median = float(np.median(durations)) if durations else 0.0

        for position in range(len(scored) - 1):
            item = scored[position]
            following = scored[position + 1]
            left = prompt_spec.syllable(item.syllable_index)
            right = prompt_spec.syllable(following.syllable_index)
            if left is None or right is None:
                continue
            if " " in text[left.char_end : right.char_start]:
                continue  # the prompt licenses a boundary here
            gap_ms = following.start_ms - item.end_ms
            if gap_ms <= INTRA_WORD_PAUSE_MS:
                continue
            # Margin in units of the threshold, so a gap twice the threshold
            # reads the same however long syllables are in this recording.
            margin = gap_ms / INTRA_WORD_PAUSE_MS - 1.0
            drafts.append(
                DiagnosticIssueDraft(
                    issue_type="misplaced_pause",
                    syllable_index=following.syllable_index,
                    start_ms=item.end_ms,
                    end_ms=following.start_ms,
                    severity=_severity(margin),
                    confidence=_sigmoid(margin),
                    evidence=(
                        f"intra_word_gap_ms={gap_ms}",
                        f"threshold_ms={INTRA_WORD_PAUSE_MS}",
                        f"between_syllables={left.syllable}{right.syllable}",
                    ),
                )
            )

        if median > 0.0 and len(scored) >= MINIMUM_SYLLABLES_FOR_MEDIAN:
            for item, duration in zip(scored, durations):
                slot = prompt_spec.syllable(item.syllable_index)
                if slot is None or duration >= median:
                    continue
                # Half the speaker's own median is the point where a syllable
                # stops reading as natural variation and starts reading as one
                # that was cut short.
                if (median - duration) / median < 0.5:
                    continue
                margin = math.log(median / max(duration, 1.0))
                drafts.append(
                    DiagnosticIssueDraft(
                        issue_type="duration_short",
                        syllable_index=item.syllable_index,
                        start_ms=item.start_ms,
                        end_ms=item.end_ms,
                        severity=_severity(margin),
                        confidence=_sigmoid(margin),
                        evidence=(
                            f"syllable_ms={duration}",
                            f"utterance_median_ms={median:.0f}",
                            f"expected_syllable={slot.syllable}",
                        ),
                    )
                )
        return drafts
