"""What the pair renderer must not do to its two classes.

The renderer decides what the evaluator sees as "human" and as "synthetic". Two
asymmetries are deliberate and easy to lose in a later edit, so they are pinned
here: the synthetic side is gated, the human side is not, and no output path
carries a corpus file name.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location(
    "render_pairs", ROOT / "scripts" / "render_human_tts_pairs.py"
)
render_pairs = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = render_pairs
_spec.loader.exec_module(render_pairs)


def _corpus_zip(tmp_path: Path, names: list[str], *, prefix: str) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "VS.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in names:
            path = staging / name
            t = np.arange(48_000 * 2) / 48_000
            sf.write(path, (0.2 * np.sin(2 * np.pi * 190 * t)).astype("float32"), 48_000)
            handle.write(path, f"{prefix}/{name}")
    return archive


# -- reading the corpus in place ----------------------------------------------


def test_a_member_is_extracted_from_the_archive_without_unpacking_it(
    tmp_path: Path,
) -> None:
    """The corpus archives are tens of gigabytes; only the paired members move."""

    archive = _corpus_zip(
        tmp_path, ["0049_G1A2E7_JHJ_000001.wav"], prefix="VS1/1.남성/2400문장/0049"
    )
    corpus = render_pairs.CorpusAudio([archive])

    target = tmp_path / "out" / "extracted.wav"
    assert corpus.extract("0049_G1A2E7_JHJ_000001.wav", target) is True
    assert sf.info(str(target)).samplerate == 48_000


def test_a_member_absent_from_every_archive_is_reported_not_guessed(
    tmp_path: Path,
) -> None:
    archive = _corpus_zip(tmp_path, ["present.wav"], prefix="VS1/x")
    corpus = render_pairs.CorpusAudio([archive])

    assert corpus.extract("absent.wav", tmp_path / "out" / "a.wav") is False
    assert not (tmp_path / "out" / "a.wav").exists()


def test_several_archives_are_searched_together(tmp_path: Path) -> None:
    """Male and female audio live in different archives; a pair set spans both."""

    male = _corpus_zip(tmp_path / "m", ["m1.wav"], prefix="VS1/1.남성")
    female = _corpus_zip(tmp_path / "f", ["f1.wav"], prefix="VS5/2.여성")
    corpus = render_pairs.CorpusAudio([male, female])

    assert corpus.extract("m1.wav", tmp_path / "o" / "m.wav") is True
    assert corpus.extract("f1.wav", tmp_path / "o" / "f.wav") is True


# -- the two asymmetries ------------------------------------------------------


def test_an_empty_pair_manifest_fails_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "pairs.json"
    manifest.write_text(json.dumps({"pair_rows": []}), encoding="utf-8")

    with pytest.raises(render_pairs.PairRenderError, match="no row"):
        render_pairs.render(
            manifest, [], tmp_path / "out", limit=None, workers=1
        )


def test_the_band_is_read_from_the_measured_artifact_not_a_constant() -> None:
    """The gate that decides which synthesis is usable must stay measured."""

    low, high = render_pairs._band()
    measured = json.loads(
        (ROOT / render_pairs.RATE_BAND).read_text(encoding="utf-8")
    )["band"]

    assert low == pytest.approx(measured["low_syllables_per_second"])
    assert high == pytest.approx(measured["high_syllables_per_second"])
    assert 0 < low < high


def test_the_renderer_shares_the_generation_gate_measurement() -> None:
    """Screening, generation and pair rendering must measure the same quantity.

    If they diverge, a rendering can pass one and fail another for no reason a
    reader could reconstruct.
    """

    generate = sys.modules["generate_r01"]

    assert render_pairs.speech_region_seconds is generate.speech_region_seconds
    assert render_pairs.rate_bounds is generate.rate_bounds
