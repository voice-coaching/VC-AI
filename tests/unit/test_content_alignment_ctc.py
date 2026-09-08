"""Stage C must establish what was said, or refuse to judge pronunciation.

The adapter is driven by a fake posterior backend so these tests cover the
behaviour the pipeline depends on without a checkpoint: matched syllables get
spans, a prompt mismatch is reported as a low content score rather than an
exception, decoded silence leaves content *unmeasured* instead of zero, and every
backend defect becomes ``content_backend_unavailable``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_coach.pronunciation_feedback.adapters.content_alignment_ctc import (
    BACKEND_ID,
    PosteriorFrames,
    UnavailableContentAlignment,
    WavCtcContentAlignment,
    load_vocabulary,
)
from voice_coach.pronunciation_feedback.ctc_alignment import (
    BLANK_TOKEN,
    WORD_DELIMITER_TOKEN,
    CtcVocabulary,
)
from voice_coach.pronunciation_feedback.domain import (
    FailureCode,
    MetricState,
    PromptSpec,
    SyllableSlot,
    TargetLanguage,
)
from voice_coach.pronunciation_feedback.errors import BackendUnavailableError

TOKENS = (BLANK_TOKEN, WORD_DELIMITER_TOKEN, "가", "나", "다")
VOCAB = CtcVocabulary(tokens=TOKENS)
AUDIO = Path("/nonexistent/recording.wav")


class _FakeBackend:
    def __init__(
        self,
        frames: PosteriorFrames | Exception | object,
        *,
        backend_id: str = BACKEND_ID,
        revision: str = "test-revision",
    ) -> None:
        self._frames = frames
        self._backend_id = backend_id
        self._revision = revision
        self.calls = 0

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def revision(self) -> str:
        return self._revision

    def posteriors(self, audio_path: Path) -> PosteriorFrames:
        self.calls += 1
        if isinstance(self._frames, Exception):
            raise self._frames
        return self._frames  # type: ignore[return-value]


def _one_hot(label: str, probability: float = 1.0) -> tuple[float, ...]:
    row = [0.0] * len(TOKENS)
    row[TOKENS.index(label)] = probability
    if probability < 1.0:
        row[0] = 1.0 - probability
    return tuple(row)


def _frames(*specs: tuple[str, int, float], frame_ms: float = 20.0) -> PosteriorFrames:
    rows: list[tuple[float, ...]] = []
    for label, count, probability in specs:
        rows.extend(_one_hot(label, probability) for _ in range(count))
    return PosteriorFrames(probabilities=tuple(rows), frame_ms=frame_ms)


def _prompt(text: str = "가나다") -> PromptSpec:
    syllables = tuple(
        SyllableSlot(
            index=index,
            char_start=index,
            char_end=index + 1,
            syllable=character,
            onset=character,
            nucleus=character,
            coda=None,
        )
        for index, character in enumerate(text)
    )
    return PromptSpec(
        prompt_id="R00-TEST",
        prompt_version="v1",
        target_language=TargetLanguage.KOREAN,
        normalized_text=text,
        normalized_text_sha256="0" * 64,
        syllables=syllables,
        syllable_map_sha256="1" * 64,
        expected_phones=None,
        expected_phone_state=MetricState.MISSING,
    )


def _adapter(frames: object, **kwargs: object) -> WavCtcContentAlignment:
    return WavCtcContentAlignment(
        backend=_FakeBackend(frames),  # type: ignore[arg-type]
        vocabulary=VOCAB,
        **kwargs,  # type: ignore[arg-type]
    )


# -- happy path ---------------------------------------------------------------


def test_correct_reading_yields_full_content_score_and_every_span() -> None:
    adapter = _adapter(
        _frames(
            ("가", 2, 1.0),
            (BLANK_TOKEN, 1, 1.0),
            ("나", 2, 1.0),
            (BLANK_TOKEN, 1, 1.0),
            ("다", 2, 1.0),
        )
    )

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    assert observation.content_state is MetricState.OBSERVED
    assert observation.content_score == pytest.approx(1.0)
    assert observation.alignment_confidence == pytest.approx(1.0)
    assert [span.syllable_index for span in observation.syllable_spans] == [0, 1, 2]
    assert all(
        span.state is MetricState.OBSERVED for span in observation.syllable_spans
    )
    assert observation.backend_id == BACKEND_ID
    assert observation.revision == "test-revision"


def test_span_times_are_derived_from_the_posterior_frames() -> None:
    adapter = _adapter(_frames((BLANK_TOKEN, 3, 1.0), ("가", 2, 0.9)))

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt("가"))

    span = observation.syllable_spans[0]
    assert (span.start_ms, span.end_ms) == (60, 100)
    assert span.confidence == pytest.approx(0.9)


def test_non_default_frame_duration_scales_the_spans() -> None:
    adapter = _adapter(_frames(("가", 2, 1.0), frame_ms=10.0))

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt("가"))

    assert (observation.syllable_spans[0].start_ms, observation.syllable_spans[0].end_ms) == (
        0,
        20,
    )


def test_report_exposes_metrics_without_putting_them_on_the_observation() -> None:
    adapter = _adapter(_frames(("가", 2, 1.0), (BLANK_TOKEN, 1, 1.0), ("나", 2, 1.0)))

    adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    report = adapter.last_report
    assert report is not None
    assert report.hypothesis_syllable_count == 2
    assert report.content_cer == pytest.approx(1 / 3)
    assert report.attributed_syllable_count == 2
    assert report.unattributed_syllable_indices == (2,)
    assert report.frame_count == 5


# -- mismatch and low-evidence paths -----------------------------------------


def test_wrong_reading_lowers_the_content_score_instead_of_raising() -> None:
    adapter = _adapter(_frames(("다", 2, 1.0), (BLANK_TOKEN, 1, 1.0), ("다", 2, 1.0)))

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    assert observation.content_state is MetricState.OBSERVED
    assert observation.content_score is not None
    assert observation.content_score < 0.5


def test_partial_reading_reduces_alignment_confidence_by_coverage() -> None:
    adapter = _adapter(_frames(("가", 2, 1.0)))

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    assert observation.alignment_confidence == pytest.approx(1 / 3)
    assert len(observation.syllable_spans) == 1


def test_decoded_silence_leaves_content_missing_not_zero() -> None:
    adapter = _adapter(_frames((BLANK_TOKEN, 6, 1.0)))

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    assert observation.content_state is MetricState.MISSING
    assert observation.content_score is None
    assert observation.alignment_confidence is None
    assert observation.syllable_spans == ()
    report = adapter.last_report
    assert report is not None
    assert report.unattributed_syllable_indices == (0, 1, 2)


def test_substituted_syllable_is_reported_as_unattributed() -> None:
    adapter = _adapter(
        _frames(
            ("가", 2, 1.0),
            (BLANK_TOKEN, 1, 1.0),
            ("다", 2, 1.0),
            (BLANK_TOKEN, 1, 1.0),
            ("다", 2, 1.0),
        )
    )

    observation = adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())

    assert 1 not in [span.syllable_index for span in observation.syllable_spans]
    report = adapter.last_report
    assert report is not None
    assert 1 in report.unattributed_syllable_indices


# -- fail-closed paths --------------------------------------------------------


def test_prompt_without_hangul_fails_closed() -> None:
    adapter = _adapter(_frames(("가", 1, 1.0)))

    with pytest.raises(BackendUnavailableError, match="no Hangul syllable"):
        adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt("abc"))


def test_backend_exception_becomes_a_content_backend_failure() -> None:
    adapter = _adapter(OSError("device is gone"))

    with pytest.raises(OSError):
        adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())


def test_wrong_backend_return_type_fails_closed() -> None:
    adapter = _adapter({"probabilities": []})

    with pytest.raises(BackendUnavailableError, match="unexpected type") as caught:
        adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())
    assert caught.value.code is FailureCode.CONTENT_BACKEND_UNAVAILABLE


def test_non_positive_frame_duration_fails_closed() -> None:
    adapter = _adapter(PosteriorFrames(probabilities=(_one_hot("가"),), frame_ms=0.0))

    with pytest.raises(BackendUnavailableError, match="frame duration"):
        adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())


@pytest.mark.parametrize(
    "rows",
    [
        (),
        ((1.0, 0.0),),
        ((0.0, 0.0, 0.0, 0.0, 2.0),),
    ],
)
def test_unusable_posteriors_fail_closed(rows: tuple[tuple[float, ...], ...]) -> None:
    adapter = _adapter(PosteriorFrames(probabilities=rows, frame_ms=20.0))

    with pytest.raises(BackendUnavailableError, match="posteriors are unusable"):
        adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt())


def test_vocabulary_digest_mismatch_is_refused_at_construction() -> None:
    with pytest.raises(BackendUnavailableError, match="digest differs"):
        _adapter(_frames(("가", 1, 1.0)), expected_vocabulary_digest="0" * 64)


def test_matching_vocabulary_digest_is_accepted() -> None:
    adapter = _adapter(
        _frames(("가", 1, 1.0)),
        expected_vocabulary_digest=VOCAB.digest,
    )

    assert adapter.analyze(audio_path=AUDIO, prompt_spec=_prompt("가")).content_score == (
        pytest.approx(1.0)
    )


def test_unavailable_stand_in_always_fails_closed() -> None:
    with pytest.raises(BackendUnavailableError, match="no Korean syllable CTC") as caught:
        UnavailableContentAlignment().analyze(audio_path=AUDIO, prompt_spec=_prompt())
    assert caught.value.code is FailureCode.CONTENT_BACKEND_UNAVAILABLE


# -- vocabulary artifact ------------------------------------------------------


def test_vocabulary_artifact_round_trips_with_its_digest(tmp_path: Path) -> None:
    path = tmp_path / "vocabulary.json"
    path.write_text(
        json.dumps({"tokens": list(TOKENS), "digest": VOCAB.digest}, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_vocabulary(path, expected_digest=VOCAB.digest)

    assert loaded.tokens == TOKENS


def test_missing_vocabulary_artifact_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(BackendUnavailableError, match="artifact is unavailable"):
        load_vocabulary(tmp_path / "absent.json")


def test_vocabulary_artifact_with_a_stale_digest_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "vocabulary.json"
    path.write_text(
        json.dumps({"tokens": list(TOKENS), "digest": "0" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(BackendUnavailableError, match="does not match its tokens"):
        load_vocabulary(path)


def test_vocabulary_artifact_with_a_relabelled_output_layer_fails_closed(
    tmp_path: Path,
) -> None:
    relabelled = (BLANK_TOKEN, WORD_DELIMITER_TOKEN, "나", "가", "다")
    path = tmp_path / "vocabulary.json"
    path.write_text(json.dumps({"tokens": list(relabelled)}), encoding="utf-8")

    with pytest.raises(BackendUnavailableError, match="digest differs from the pinned"):
        load_vocabulary(path, expected_digest=VOCAB.digest)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{not json", "not valid JSON"),
        ('["tokens"]', "must be an object"),
        ('{"tokens": "가나다"}', "array of strings"),
        ('{"tokens": ["가", "<blank>"]}', "vocabulary is invalid"),
    ],
)
def test_malformed_vocabulary_artifacts_fail_closed(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    path = tmp_path / "vocabulary.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(BackendUnavailableError, match=message):
        load_vocabulary(path)
