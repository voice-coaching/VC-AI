"""What ``build_default_pipeline`` must actually connect.

For most of this project's life the default pipeline had no analysis backends,
so every attempt ended in ``failed_closed`` with "analysis backends not
connected".  That is a correct answer to give when the backends are missing and
a useless one to give when they are present, and the difference is invisible
from the outside: both are a terminal result with a failure code.  So the wiring
is pinned here.

The models load lazily, so nothing in this file puts a checkpoint in memory.
"""

from __future__ import annotations

from pathlib import Path

from voice_coach.pronunciation_feedback import (
    REPOSITORY_ROOT,
    build_default_pipeline,
)
from voice_coach.pronunciation_feedback.ports import (
    ContentAlignmentPort,
    ReferenceBankPort,
    S1ScorerPort,
    S2AuditorPort,
)


def test_the_repository_root_constant_points_at_the_repository() -> None:
    """Every backend resolves its pinned model relative to this."""

    assert (REPOSITORY_ROOT / "src" / "voice_coach").is_dir()
    assert (REPOSITORY_ROOT / "scripts").is_dir()


def test_the_three_analysis_ports_are_connected_by_default() -> None:
    pipeline = build_default_pipeline()

    assert isinstance(pipeline._content_alignment, ContentAlignmentPort)
    assert isinstance(pipeline._s1_scorer, S1ScorerPort)
    assert isinstance(pipeline._s2_auditor, S2AuditorPort)


def test_the_auditor_is_not_one_of_the_s1_backends() -> None:
    """The independence receipt is checked at run time, but a default wiring
    that pointed both sides at one model would only reveal that at run time."""

    pipeline = build_default_pipeline()

    s1_ids = {
        pipeline._content_alignment.backend_id,
        pipeline._s1_scorer._backend.backend_id,
    }
    assert pipeline._s2_auditor.backend_id not in s1_ids


def test_both_s1_stages_share_one_acoustic_backend() -> None:
    """A span and the score attached to it have to come from the same frames.
    Two backends would decode the recording twice and could disagree."""

    pipeline = build_default_pipeline()

    assert pipeline._s1_scorer._backend is pipeline._content_alignment._backend


def test_the_backends_can_be_left_unconnected_on_purpose() -> None:
    pipeline = build_default_pipeline(connect_analysis_backends=False)

    assert pipeline._content_alignment is None
    assert pipeline._s1_scorer is None
    assert pipeline._s2_auditor is None


def test_a_bank_manifest_that_exists_is_served(tmp_path: Path) -> None:
    manifest = REPOSITORY_ROOT / "artifacts/reference/r01/multi_speaker_v5/bank.json"
    if not manifest.is_file():  # pragma: no cover - depends on a generation run
        return

    pipeline = build_default_pipeline()

    assert isinstance(pipeline._reference_bank, ReferenceBankPort)


def test_a_missing_bank_manifest_leaves_the_port_unset(tmp_path: Path) -> None:
    """An absent bank is a valid state — the learner is told there is no
    reference audio — but it must not be faked by an empty store either."""

    pipeline = build_default_pipeline(
        reference_bank_manifest=tmp_path / "absent.json"
    )

    assert pipeline._reference_bank is None


def test_no_model_is_loaded_just_by_building_the_pipeline() -> None:
    """Listing prompts must not cost a 16 GB checkpoint load."""

    pipeline = build_default_pipeline()

    assert pipeline._s2_auditor._model is None
    assert pipeline._s1_scorer._backend._model is None
    assert pipeline._advice_generator is None


def test_an_explicit_grounded_generator_is_injected_without_becoming_default() -> None:
    class Generator:
        generator_id = "fixture-generator"
        revision = "fixture-revision"

        def generate(self, payload):  # noqa: ANN001, ANN201 - wiring-only double
            raise AssertionError("building the pipeline must not call the generator")

    generator = Generator()
    pipeline = build_default_pipeline(advice_generator=generator)

    assert pipeline._advice_generator is generator
