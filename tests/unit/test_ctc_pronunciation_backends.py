"""What the three analysis adapters must keep separate.

These run against a hand-built posterior matrix rather than the 317M-parameter
model, because the properties worth pinning are decisions the adapters make, not
things the model knows.  Three of them would be invisible failures in
production:

* a content score built from pronunciation would tell a learner who read the
  right sentence badly that they read the wrong sentence;
* an alignment confidence built from pronunciation would send that same learner
  away to re-record instead of coaching them;
* an independence receipt that only compares names would let one model audit
  itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from voice_coach.pronunciation_feedback.adapters.ctc_acoustic_backend import (
    CtcPosteriors,
)
from voice_coach.pronunciation_feedback.adapters.ctc_alignment import log_softmax
from voice_coach.pronunciation_feedback.adapters.ctc_content_alignment import (
    CtcContentAlignment,
    jamo_sequence,
    jamo_similarity,
)
from voice_coach.pronunciation_feedback.adapters.gop_s1_scorer import (
    GopS1Scorer,
    compose_syllable,
    decompose_syllable,
)
from voice_coach.pronunciation_feedback.adapters.qwen_audio_auditor import (
    BACKEND_ID as AUDITOR_BACKEND_ID,
    build_independence_receipt,
)
from voice_coach.pronunciation_feedback.adapters.syllables import (
    decompose_syllables,
    syllable_map_sha256,
)
from voice_coach.pronunciation_feedback.domain import (
    MetricState, PromptSpec, TargetLanguage
)

ROOT = Path(__file__).resolve().parents[2]
BLANK_TOKEN = "[PAD]"
DELIMITER_TOKEN = "|"
#: 20 ms hop, 25 ms window — the real backend's frame geometry.
HOP_SECONDS = 0.02
WINDOW_SECONDS = 0.025


@dataclass
class FakeAcousticBackend:
    """A posterior matrix with a vocabulary, and nothing else.

    Only the surface ``CtcContentAlignment`` and ``GopS1Scorer`` actually use is
    implemented, so a change that makes them reach for more of the model shows
    up here as an ``AttributeError`` rather than as a silent extra dependency.
    """

    tokens: tuple[str, ...]
    log_posteriors: np.ndarray
    backend_id: str = "fake-ctc"
    revision: str = "test"

    def vocabulary(self) -> dict[str, int]:
        return {token: index for index, token in enumerate(self.tokens)}

    @property
    def blank_id(self) -> int:
        return self.tokens.index(BLANK_TOKEN)

    def token_id(self, token: str) -> int | None:
        return self.vocabulary().get(token)

    def posteriors(self, audio_path) -> CtcPosteriors:
        return CtcPosteriors(
            log_posteriors=self.log_posteriors,
            audio_sha256="0" * 64,
            sample_rate=16_000,
            duration_seconds=self.log_posteriors.shape[0] * HOP_SECONDS,
            hop_seconds=HOP_SECONDS,
            window_seconds=WINDOW_SECONDS,
        )


def _prompt_spec(text: str) -> PromptSpec:
    slots = decompose_syllables(text)
    return PromptSpec(
        prompt_id="TEST-01",
        prompt_version="test",
        target_language=TargetLanguage.KOREAN,
        normalized_text=text,
        normalized_text_sha256="0" * 64,
        syllables=slots,
        syllable_map_sha256=syllable_map_sha256(slots),
        expected_phones=None,
        expected_phone_state=MetricState.BACKEND_UNAVAILABLE,
    )


def _backend(
    text: str,
    *,
    spoken: str | None = None,
    frames_per_syllable: int = 10,
    extra_tokens: tuple[str, ...] = (),
) -> FakeAcousticBackend:
    """Frames that spike on ``spoken`` at evenly spaced anchors."""

    written = [char for char in text if char.isalpha()]
    heard = (
        [char for char in spoken if char.isalpha()] if spoken is not None else written
    )
    vocabulary = [BLANK_TOKEN, DELIMITER_TOKEN]
    for syllable in (*written, *heard, *extra_tokens):
        if syllable not in vocabulary:
            vocabulary.append(syllable)

    frames = len(written) * frames_per_syllable
    logits = np.zeros((frames, len(vocabulary)))
    logits[:, 0] = 6.0
    for position, syllable in enumerate(heard):
        anchor = position * frames_per_syllable + frames_per_syllable // 2
        logits[anchor, 0] = 0.0
        logits[anchor, vocabulary.index(syllable)] = 6.0
    return FakeAcousticBackend(
        tokens=tuple(vocabulary), log_posteriors=log_softmax(logits)
    )


def _adapters(backend: FakeAcousticBackend, tmp_path: Path):
    root = ROOT
    content = CtcContentAlignment(
        backend,
        rate_band_path=root / "artifacts/reference/r01"
        "/korean_read_speech_rate_band_v1.json",
    )
    scorer = GopS1Scorer(
        backend=backend,
        content_alignment=content,
        repository_root=root,
    )
    return content, scorer


# -- jamo comparison ----------------------------------------------------------


def test_a_syllable_decomposes_to_its_written_jamo() -> None:
    assert jamo_sequence("한글") == "ㅎㅏㄴㄱㅡㄹ"


def test_one_wrong_vowel_costs_far_less_than_a_different_sentence() -> None:
    """The gate turns a low content score into "you read the wrong text", so a
    single substituted vowel must not look like a different sentence."""

    one_vowel_off = jamo_similarity("구소해요", "구수해요")
    different_sentence = jamo_similarity("창문을 열어요", "구수해요")

    # One substituted jamo out of eight: 7/8 of the pair still matches.
    assert one_vowel_off > 0.85
    assert different_sentence < 0.5
    assert one_vowel_off > 2 * different_sentence


def test_an_empty_reference_scores_zero_rather_than_dividing_by_it() -> None:
    assert jamo_similarity("무엇", "") == 0.0


# -- content versus pronunciation ---------------------------------------------


def test_reading_the_prompt_badly_still_scores_as_the_prompt(tmp_path: Path) -> None:
    """The one failure mode this adapter exists to avoid."""

    backend = _backend("구수해요", spoken="구소해요")
    content, _ = _adapters(backend, tmp_path)

    observation = content.analyze(
        audio_path=tmp_path / "x.wav", prompt_spec=_prompt_spec("구수해요")
    )

    assert observation.content_score > 0.85
    assert observation.content_state is MetricState.OBSERVED


def test_reading_a_different_sentence_collapses_the_content_score(
    tmp_path: Path,
) -> None:
    backend = _backend("구수해요", spoken="창문열어")
    content, _ = _adapters(backend, tmp_path)

    observation = content.analyze(
        audio_path=tmp_path / "x.wav", prompt_spec=_prompt_spec("구수해요")
    )

    assert observation.content_score < 0.5


def test_alignment_confidence_survives_a_mispronounced_syllable(tmp_path: Path) -> None:
    """Boundaries and pronunciation are different questions.  If they were the
    same, a struggling learner would be told to re-record rather than coached."""

    backend = _backend("구수해요", spoken="구소해요")
    content, _ = _adapters(backend, tmp_path)

    observation = content.analyze(
        audio_path=tmp_path / "x.wav", prompt_spec=_prompt_spec("구수해요")
    )

    assert observation.alignment_confidence == pytest.approx(1.0)


def test_a_syllable_outside_the_vocabulary_is_left_unmeasured(tmp_path: Path) -> None:
    """``[UNK]`` means "a syllable I was never trained on", which is not
    evidence about this one, so the syllable gets no span at all."""

    backend = _backend("가나다")
    backend.tokens = tuple(token for token in backend.tokens if token != "나")
    backend.log_posteriors = backend.log_posteriors[:, : len(backend.tokens)]
    content, _ = _adapters(backend, tmp_path)

    observation = content.analyze(
        audio_path=tmp_path / "x.wav", prompt_spec=_prompt_spec("가나다")
    )

    assert {span.syllable_index for span in observation.syllable_spans} == {0, 2}
    assert observation.alignment_confidence < 1.0


def test_spans_are_wide_enough_to_highlight(tmp_path: Path) -> None:
    backend = _backend("구수해요")
    content, _ = _adapters(backend, tmp_path)

    observation = content.analyze(
        audio_path=tmp_path / "x.wav", prompt_spec=_prompt_spec("구수해요")
    )

    assert all(span.end_ms - span.start_ms >= 60 for span in observation.syllable_spans)


# -- syllable arithmetic the detectors rely on --------------------------------


@pytest.mark.parametrize("syllable", ["닫", "가", "왕", "쀍"])
def test_a_syllable_survives_a_decompose_compose_round_trip(syllable: str) -> None:
    assert compose_syllable(*decompose_syllable(syllable)) == syllable


def test_dropping_the_coda_names_the_syllable_a_deletion_would_sound_like() -> None:
    onset, nucleus, _ = decompose_syllable("닫")

    assert compose_syllable(onset, nucleus, 0) == "다"


# -- the detectors ------------------------------------------------------------


def test_a_dropped_coda_is_reported_as_a_dropped_coda(tmp_path: Path) -> None:
    """Both codas here are followed by a real onset, so both are realized in a
    correct reading and their absence is an error."""

    backend = _backend("받침 닫고", spoken="바침 다고")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("받침 닫고")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    deletions = [
        issue
        for issue in bundle.issues
        if issue.issue_type == "final_consonant_deletion"
    ]
    assert {issue.syllable_index for issue in deletions} == {0, 2}
    assert any("dropped_coda=" in tag for tag in deletions[0].evidence)


def test_resyllabification_is_not_reported_as_a_dropped_coda(tmp_path: Path) -> None:
    """The expensive false positive.  ``닫아요`` is read [다다요]; a model hearing
    ``다`` for ``닫`` is agreeing with a correct reading, and coaching the learner
    to keep the coda there would teach them to say something no Korean speaker
    says."""

    backend = _backend("닫아요", spoken="다다요")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("닫아요")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    assert not [
        issue
        for issue in bundle.issues
        if issue.issue_type == "final_consonant_deletion"
    ]


def test_the_resyllabification_rule_reads_the_prompt_not_the_audio() -> None:
    """Whether a coda is expected to move is a property of the sentence, so it
    has to be decidable before any recording exists."""

    from voice_coach.pronunciation_feedback.adapters.korean_surface import (
        resyllabification_sites,
    )

    # 닫(0) has coda ㄷ and 아(1) has the null onset -> the coda moves.
    assert resyllabification_sites(_prompt_spec("닫아요")) == frozenset({0})
    # 받(0) is followed by 침, a real onset -> the coda stays.
    assert resyllabification_sites(_prompt_spec("받침 닫고")) == frozenset()
    # Across an eojeol boundary careful read speech realizes the coda.
    assert resyllabification_sites(_prompt_spec("밥 아니야")) == frozenset()
    # ㅇ is the coda that stays: 콩을 is [콩을], not [코응].
    assert resyllabification_sites(_prompt_spec("콩을")) == frozenset()


def test_a_substituted_vowel_names_the_vowel_that_was_heard(tmp_path: Path) -> None:
    backend = _backend("구수해요", spoken="구소해요")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("구수해요")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    vowels = [issue for issue in bundle.issues if issue.issue_type == "vowel_quality"]
    assert [issue.syllable_index for issue in vowels] == [1]
    assert "heard_syllable=소" in vowels[0].evidence


def test_a_correct_reading_raises_no_segmental_issue(tmp_path: Path) -> None:
    """False positives are the expensive failure: they coach a learner on a
    syllable they said correctly."""

    backend = _backend("구수해요")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("구수해요")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    assert not [
        issue
        for issue in bundle.issues
        if issue.issue_type in {"vowel_quality", "final_consonant_deletion"}
    ]


def test_every_issue_carries_evidence_and_a_location(tmp_path: Path) -> None:
    """The gate drops issues with no evidence, and feedback drops spans it
    cannot resolve; a detector that emitted either would just go silent."""

    backend = _backend("받침 닫고", spoken="바침 다고")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("받침 닫고")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    for issue in bundle.issues:
        assert issue.evidence
        if issue.issue_type in {"final_consonant_deletion", "vowel_quality"}:
            assert any(tag.startswith("posterior_entropy=") for tag in issue.evidence)
        assert issue.syllable_index is not None
        assert issue.start_ms is not None and issue.end_ms is not None
        assert issue.end_ms > issue.start_ms
        assert 0.0 <= issue.severity <= 1.0
        assert 0.0 <= issue.confidence <= 1.0


def test_the_scored_branches_are_the_ones_the_profile_can_require(
    tmp_path: Path,
) -> None:
    backend = _backend("받침 닫고 문을 열어")
    content, scorer = _adapters(backend, tmp_path)
    spec = _prompt_spec("받침 닫고 문을 열어")

    bundle = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    )

    assert set(bundle.observed_names()) == {"segmental", "fluency", "rhythm"}
    assert bundle.confidence_state is MetricState.OBSERVED
    assert bundle.overall_confidence is not None
    assert len(bundle.soft_information) == len(spec.syllables)
    first = bundle.soft_information[0]
    assert isinstance(first.target_log_posterior, float)
    assert isinstance(first.gop_margin, float)
    assert isinstance(first.posterior_entropy, float)
    assert isinstance(first.target_is_top1, bool)
    assert first.backend_id == backend.backend_id


def test_s1_confidence_does_not_saturate_on_a_bad_reading(tmp_path: Path) -> None:
    """A confidence that is 1.0 for everything makes its frozen threshold a
    trap: any recording that dips below it fails, and none of them ever vary."""

    clean = _backend("받침 닫고 문을 열어")
    content, scorer = _adapters(clean, tmp_path)
    spec = _prompt_spec("받침 닫고 문을 열어")
    confident = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    ).overall_confidence

    muddy = _backend("받침 닫고 문을 열어")
    # Flatten the anchors: the model commits to nothing in particular.
    muddy.log_posteriors = log_softmax(np.zeros_like(muddy.log_posteriors))
    content, scorer = _adapters(muddy, tmp_path)
    unsure = scorer.score(
        audio_path=tmp_path / "x.wav",
        prompt_spec=spec,
        alignment=content.analyze(audio_path=tmp_path / "x.wav", prompt_spec=spec),
    ).overall_confidence

    assert unsure < confident


# -- independence -------------------------------------------------------------


def test_the_auditor_is_independent_of_the_ctc_stack() -> None:
    receipt = build_independence_receipt(
        ("wav2vec2-xlsr-korean-ctc-alignment", "alignment-timing-prosody-v1")
    )

    assert receipt.independent is True
    assert "wav2vec2" in receipt.reason and "Qwen2" in receipt.reason


def test_an_auditor_that_is_also_an_s1_backend_is_refused() -> None:
    """Without this the audit would be the same model agreeing with itself."""

    receipt = build_independence_receipt(("s1-thing", AUDITOR_BACKEND_ID))

    assert receipt.independent is False
    assert receipt.reason


def test_the_receipt_records_which_s1_backends_it_was_checked_against() -> None:
    receipt = build_independence_receipt(("a", "b"))

    assert receipt.s1_backend_ids == ("a", "b")
    assert receipt.s2_backend_id == AUDITOR_BACKEND_ID
