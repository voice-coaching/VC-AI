"""A baseline that a constant answer could pass is not a baseline.

The failure this guards against is reporting "the evaluator got 85%" when the model
answers the same word to everything and the sample happened to be 85% that class.
Balanced accuracy and per-class counts are what make the number readable, and the
reply parser is what decides which class an answer counted for.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "zero_shot_baseline",
    ROOT / "scripts" / "measure_evaluator_zero_shot_baseline.py",
)
baseline = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = baseline
_spec.loader.exec_module(baseline)


# -- reading a free-text reply ------------------------------------------------


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("사람", "human"),
        ("합성", "synthetic"),
        ("이 음성은 사람이 녹음한 것입니다.", "human"),
        ("기계가 합성한 음성입니다.", "synthetic"),
        ("Human", "human"),
        ("TTS", "synthetic"),
    ],
)
def test_a_reply_is_mapped_to_the_class_it_names(reply: str, expected: str) -> None:
    assert baseline.classify_reply(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "잘 모르겠습니다",
        "",
        "사람인지 합성인지 판단하기 어렵습니다",
    ],
)
def test_a_reply_naming_neither_or_both_is_not_a_vote(reply: str) -> None:
    """An abstention counted as wrong would flatter whichever class is rarer."""

    assert baseline.classify_reply(reply) is None


# -- the number that cannot be inflated by a constant answer ------------------


def test_a_model_that_always_says_synthetic_scores_one_half() -> None:
    """The whole point of balanced accuracy: this must not look like success."""

    counts = {
        "human": {"correct": 0, "wrong": 20, "unparsed": 0},
        "synthetic": {"correct": 20, "wrong": 0, "unparsed": 0},
    }

    assert baseline.balanced_accuracy(counts) == pytest.approx(0.5)


def test_a_constant_answer_stays_one_half_on_an_unbalanced_sample() -> None:
    """Overall accuracy would read 90% here; balanced accuracy does not."""

    counts = {
        "human": {"correct": 0, "wrong": 4, "unparsed": 0},
        "synthetic": {"correct": 36, "wrong": 0, "unparsed": 0},
    }

    assert baseline.balanced_accuracy(counts) == pytest.approx(0.5)


def test_a_perfect_discriminator_scores_one() -> None:
    counts = {
        "human": {"correct": 10, "wrong": 0, "unparsed": 0},
        "synthetic": {"correct": 10, "wrong": 0, "unparsed": 0},
    }

    assert baseline.balanced_accuracy(counts) == pytest.approx(1.0)


def test_a_class_with_no_measured_item_yields_no_accuracy() -> None:
    """One class unmeasured means the pair of accuracies does not exist yet."""

    counts = {
        "human": {"correct": 0, "wrong": 0, "unparsed": 0},
        "synthetic": {"correct": 10, "wrong": 0, "unparsed": 0},
    }

    assert baseline.balanced_accuracy(counts) is None


def test_abstentions_do_not_count_toward_either_accuracy() -> None:
    counts = {
        "human": {"correct": 5, "wrong": 5, "unparsed": 90},
        "synthetic": {"correct": 5, "wrong": 5, "unparsed": 90},
    }

    assert baseline.balanced_accuracy(counts) == pytest.approx(0.5)


def test_the_question_is_asked_in_the_language_of_the_audio() -> None:
    """An English prompt makes the reply language another variable."""

    assert "사람" in baseline.QUESTION
    assert "합성" in baseline.QUESTION
