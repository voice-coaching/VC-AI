from __future__ import annotations

from dataclasses import fields, replace
import inspect
import json
import re

import pytest

from voice_coach.phonology.expected_phone_agreement import (
    AIHUB_EXPECTED_SOURCE_ROLE,
    EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS,
    EXPECTED_PHONE_AGREEMENT_POLICY_ID,
    G2PK_EXPECTED_SOURCE_ROLE,
    ExpectedPhoneAgreementError,
    evaluate_expected_phone_agreement,
)
from voice_coach.phonology.g2pk_expected import (
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhones,
    generate_g2pk_expected_phones,
)


class _FakeG2PK:
    def __init__(self, output: str) -> None:
        self.output = output

    def __call__(
        self,
        text: str,
        *,
        descriptive: bool,
        verbose: bool,
        group_vowels: bool,
        to_syl: bool,
    ) -> str:
        del text, descriptive, verbose, group_vowels, to_syl
        return self.output


class _StringSubclass(str):
    pass


def _expected(output: str = "간") -> G2PKExpectedPhones:
    return generate_g2pk_expected_phones(
        "synthetic",
        descriptive=False,
        group_vowels=False,
        word_boundary_policy="drop",
        allowed_punctuation=(),
        maximum_codepoints=100,
        backend=_FakeG2PK(output),
        backend_version=EXPECTED_G2PK_VERSION,
    )


def _evaluate(
    g2pk_expected: G2PKExpectedPhones | None = None,
    aihub_expected_phone_tokens: tuple[str, ...] = ("ㄱ", "ㅏ", "ㄴ"),
    *,
    max_alignment_cells: int = 100,
):
    return evaluate_expected_phone_agreement(
        g2pk_expected=_expected() if g2pk_expected is None else g2pk_expected,
        aihub_expected_phone_tokens=aihub_expected_phone_tokens,
        max_alignment_cells=max_alignment_cells,
    )


def test_identical_expected_sequences_report_descriptive_exact_agreement() -> None:
    result = _evaluate()

    assert result.evaluation_policy_id == EXPECTED_PHONE_AGREEMENT_POLICY_ID
    assert result.g2pk_source_role == G2PK_EXPECTED_SOURCE_ROLE
    assert result.aihub_source_role == AIHUB_EXPECTED_SOURCE_ROLE
    assert result.g2pk_expected_phone_count == 3
    assert result.aihub_expected_phone_count == 3
    assert result.max_alignment_cells == 100
    assert result.alignment_cell_count == 9
    assert result.levenshtein_distance == 0
    assert result.normalized_edit_agreement_rate == 1.0
    assert result.is_exact_match is True
    assert re.fullmatch(r"[0-9a-f]{64}", result.result_digest_sha256)


@pytest.mark.parametrize(
    ("g2pk_output", "annotation", "distance", "agreement"),
    [
        ("간", ("ㄱ", "ㅏ", "ㅁ"), 1, 2.0 / 3.0),
        ("간", ("ㄱ", "ㅏ", "ㄴ", "ㅏ"), 1, 3.0 / 4.0),
        ("간ᅡ", ("ㄱ", "ㅏ", "ㄴ"), 1, 3.0 / 4.0),
    ],
)
def test_unit_cost_edit_cases_have_symmetric_length_normalization(
    g2pk_output: str,
    annotation: tuple[str, ...],
    distance: int,
    agreement: float,
) -> None:
    result = _evaluate(_expected(g2pk_output), annotation)

    assert result.levenshtein_distance == distance
    assert result.normalized_edit_agreement_rate == pytest.approx(agreement)
    assert result.is_exact_match is False


def test_source_order_does_not_change_edit_distance_or_normalized_rate() -> None:
    forward = _evaluate(_expected("간ᅡ"), ("ㄱ", "ㅏ", "ㄴ"))
    reverse = _evaluate(_expected("간"), ("ㄱ", "ㅏ", "ㄴ", "ㅏ"))

    assert forward.levenshtein_distance == reverse.levenshtein_distance == 1
    assert forward.normalized_edit_agreement_rate == pytest.approx(
        reverse.normalized_edit_agreement_rate
    )


def test_result_digest_is_deterministic_and_sequence_sensitive() -> None:
    first = _evaluate()
    repeat = _evaluate()
    changed = _evaluate(
        _expected("난"),
        ("ㄴ", "ㅏ", "ㄴ"),
    )

    assert first.result_digest_sha256 == repeat.result_digest_sha256
    assert first.result_digest_sha256 != changed.result_digest_sha256


def test_resource_policy_is_preserved_and_changes_the_result_digest() -> None:
    smaller = _evaluate(max_alignment_cells=9)
    larger = _evaluate(max_alignment_cells=100)

    assert smaller.max_alignment_cells == 9
    assert larger.max_alignment_cells == 100
    assert smaller.levenshtein_distance == larger.levenshtein_distance
    assert smaller.result_digest_sha256 != larger.result_digest_sha256


def test_to_dict_is_json_safe_and_has_no_input_sequence_fields() -> None:
    result = _evaluate()
    payload = result.to_dict()

    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
    field_names = {field.name for field in fields(result)}
    assert {
        "text",
        "transcript",
        "phone_tokens",
        "aihub_expected_phone_tokens",
        "row_id",
        "speaker_id",
        "source_path",
    }.isdisjoint(field_names)


def test_g2pk_scientific_options_are_preserved_as_descriptive_metadata() -> None:
    expected = generate_g2pk_expected_phones(
        "synthetic",
        descriptive=True,
        group_vowels=True,
        word_boundary_policy="drop",
        allowed_punctuation=("!", "."),
        maximum_codepoints=100,
        backend=_FakeG2PK("가"),
        backend_version=EXPECTED_G2PK_VERSION,
    )

    result = _evaluate(expected, ("ㄱ", "ㅏ"))

    assert result.g2pk_descriptive is True
    assert result.g2pk_group_vowels is True
    assert result.g2pk_word_boundary_policy == "drop"
    assert result.g2pk_allowed_punctuation_count == 2


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        (),
        ("ㄱ", 1),
        ("sil",),
        ("가",),
        ("|",),
    ],
)
def test_aihub_annotation_requires_exact_nonempty_canonical_tuple(
    invalid: object,
) -> None:
    with pytest.raises(ExpectedPhoneAgreementError):
        evaluate_expected_phone_agreement(
            g2pk_expected=_expected(),
            aihub_expected_phone_tokens=invalid,  # type: ignore[arg-type]
            max_alignment_cells=100,
        )


def test_g2pk_word_boundaries_are_rejected_instead_of_silently_dropped() -> None:
    expected = generate_g2pk_expected_phones(
        "synthetic",
        descriptive=False,
        group_vowels=False,
        word_boundary_policy="preserve",
        allowed_punctuation=(),
        maximum_codepoints=100,
        backend=_FakeG2PK("가 나"),
        backend_version=EXPECTED_G2PK_VERSION,
    )

    with pytest.raises(ExpectedPhoneAgreementError, match="drop word-boundary"):
        _evaluate(expected, ("ㄱ", "ㅏ", "ㄴ", "ㅏ"))


@pytest.mark.parametrize(
    "mutated",
    [
        lambda value: replace(value, g2pk_version="latest"),
        lambda value: replace(value, phone_count=value.phone_count + 1),
        lambda value: replace(value, phone_count=True),
        lambda value: replace(value, word_boundary_count=1),
        lambda value: replace(value, word_boundary_count=False),
        lambda value: replace(value, token_sha256="0" * 64),
        lambda value: replace(value, phone_tokens=("sil",)),
        lambda value: replace(value, allowed_punctuation=(".", ".")),
        lambda value: replace(value, allowed_punctuation=(".", "!")),
        lambda value: replace(value, allowed_punctuation=("A",)),
        lambda value: replace(value, allowed_punctuation=("..",)),
        lambda value: replace(
            value,
            allowed_punctuation=(_StringSubclass("."),),
        ),
    ],
)
def test_constructed_g2pk_values_must_preserve_adapter_contract(
    mutated,
) -> None:
    with pytest.raises(ExpectedPhoneAgreementError):
        _evaluate(mutated(_expected()))


@pytest.mark.parametrize("invalid", [True, 0, -1, 1_000_001, 1.5, "100"])
def test_alignment_cell_limit_is_an_explicit_bounded_integer(
    invalid: object,
) -> None:
    with pytest.raises(ExpectedPhoneAgreementError):
        evaluate_expected_phone_agreement(
            g2pk_expected=_expected(),
            aihub_expected_phone_tokens=("ㄱ", "ㅏ", "ㄴ"),
            max_alignment_cells=invalid,  # type: ignore[arg-type]
        )


def test_alignment_fails_before_allocating_above_caller_cap() -> None:
    with pytest.raises(ExpectedPhoneAgreementError, match="exceeds"):
        _evaluate(max_alignment_cells=8)


def test_hard_cell_limit_is_part_of_the_public_resource_contract() -> None:
    assert EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS == 1_000_000


def test_all_public_arguments_require_explicit_caller_values() -> None:
    signature = inspect.signature(evaluate_expected_phone_agreement)

    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in signature.parameters.values()
    )
