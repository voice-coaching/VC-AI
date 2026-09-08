from __future__ import annotations

from pathlib import Path

import pytest

from voice_coach.phonology.g2pk_expected import (
    EXPECTED_CMUDICT_FILES,
    EXPECTED_G2PK_DEPENDENCIES,
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhoneError,
    G2PKExpectedPhones,
    generate_g2pk_expected_phones,
)


class _TextSubclass(str):
    pass


class _FakeG2PK:
    def __init__(
        self,
        output: object = "한 글,.",
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        text: str,
        *,
        descriptive: bool,
        verbose: bool,
        group_vowels: bool,
        to_syl: bool,
    ) -> str:
        self.calls.append(
            {
                "text": text,
                "descriptive": descriptive,
                "verbose": verbose,
                "group_vowels": group_vowels,
                "to_syl": to_syl,
            }
        )
        if self.error is not None:
            raise self.error
        return self.output  # type: ignore[return-value]


def _generate(
    backend: _FakeG2PK,
    *,
    text: object = "한 글,.",
    descriptive: object = False,
    group_vowels: object = False,
    word_boundary_policy: object = "preserve",
    allowed_punctuation: object = (",", "."),
    maximum_codepoints: object = 100,
    nltk_data_root: str | Path | None = None,
    backend_version: str | None = EXPECTED_G2PK_VERSION,
) -> G2PKExpectedPhones:
    return generate_g2pk_expected_phones(
        text,
        descriptive=descriptive,  # type: ignore[arg-type]
        group_vowels=group_vowels,  # type: ignore[arg-type]
        word_boundary_policy=word_boundary_policy,  # type: ignore[arg-type]
        allowed_punctuation=allowed_punctuation,  # type: ignore[arg-type]
        maximum_codepoints=maximum_codepoints,  # type: ignore[arg-type]
        nltk_data_root=nltk_data_root,
        backend=backend,
        backend_version=backend_version,
    )


def test_generates_canonical_expected_phones_and_forces_non_verbose_jamo() -> None:
    backend = _FakeG2PK()

    result = _generate(backend)

    assert result.g2pk_version == EXPECTED_G2PK_VERSION
    assert result.phone_tokens == ("ㅎ", "ㅏ", "ㄴ", "|", "ㄱ", "ㅡ", "ㄹ")
    assert result.phone_count == 6
    assert result.word_boundary_count == 1
    assert result.allowed_punctuation == (",", ".")
    assert len(result.token_sha256) == 64
    assert backend.calls == [
        {
            "text": "한 글,.",
            "descriptive": False,
            "verbose": False,
            "group_vowels": False,
            "to_syl": False,
        }
    ]


def test_runtime_version_and_artifact_contracts_are_immutable() -> None:
    with pytest.raises(TypeError):
        EXPECTED_G2PK_DEPENDENCIES["g2pK"] = "latest"  # type: ignore[index]
    with pytest.raises(TypeError):
        EXPECTED_CMUDICT_FILES["corpora/cmudict.zip"] = "0" * 64  # type: ignore[index]


def test_drop_boundary_policy_removes_spaces_without_dropping_phones() -> None:
    result = _generate(
        _FakeG2PK(" 한   글 "),
        word_boundary_policy="drop",
        allowed_punctuation=(),
    )

    assert result.phone_tokens == ("ㅎ", "ㅏ", "ㄴ", "ㄱ", "ㅡ", "ㄹ")
    assert result.word_boundary_count == 0


def test_preserve_policy_collapses_boundaries_and_omits_edges() -> None:
    result = _generate(
        _FakeG2PK("  가   나   "),
        allowed_punctuation=(),
    )

    assert result.phone_tokens == ("ㄱ", "ㅏ", "|", "ㄴ", "ㅏ")


def test_all_supported_conjoining_jamo_map_to_the_40_phone_inventory() -> None:
    backend_output = "ᄀᄁᄂᄃᄄᄅᄆᄇᄈᄉᄊᄋᄌᄍᄎᄏᄐᄑ하ᅢᅣᅤᅥᅦᅧᅨᅩᅪᅫᅬᅭᅮᅯᅰᅱᅲᅳᅴᅵᆨᆩᆫᆮᆯᆷᆸᆺᆻᆼᆽᆾᆿᇀᇁᇂ"
    expected = tuple(
        "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
        "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
        "ㄱㄲㄴㄷㄹㅁㅂㅅㅆㅇㅈㅊㅋㅌㅍㅎ"
    )

    result = _generate(
        _FakeG2PK(backend_output),
        word_boundary_policy="drop",
        allowed_punctuation=(),
    )

    assert result.phone_tokens == expected


def test_digest_is_deterministic_and_changes_with_scientific_options() -> None:
    first = _generate(_FakeG2PK())
    repeat = _generate(_FakeG2PK())
    descriptive = _generate(_FakeG2PK(), descriptive=True)
    grouped = _generate(_FakeG2PK(), group_vowels=True)
    dropped = _generate(_FakeG2PK(), word_boundary_policy="drop")

    assert first.token_sha256 == repeat.token_sha256
    assert (
        len(
            {
                first.token_sha256,
                descriptive.token_sha256,
                grouped.token_sha256,
                dropped.token_sha256,
            }
        )
        == 4
    )


def test_result_does_not_retain_input_or_raw_backend_output() -> None:
    result = _generate(_FakeG2PK())

    assert {"text", "input_text", "raw_output", "path", "speaker_id"}.isdisjoint(
        result.__dataclass_fields__
    )


@pytest.mark.parametrize(
    ("text", "maximum_codepoints", "message"),
    [
        (None, 10, "plain string"),
        (_TextSubclass("한글"), 10, "plain string"),
        ("", 10, "empty"),
        ("   ", 10, "whitespace-only"),
        (" 한글", 10, "leading or trailing"),
        ("한글 ", 10, "leading or trailing"),
        ("가\n나", 10, "forbidden control"),
        ("가\ue000나", 10, "private-use"),
        ("가", 10, "NFC-normalized"),
        ("한글", 0, "between 1 and 1000000"),
        ("한글", True, "between 1 and 1000000"),
        ("한글", 1_000_001, "between 1 and 1000000"),
        ("한글", 1, "exceeds"),
    ],
)
def test_input_contract_fails_before_backend_call(
    text: object,
    maximum_codepoints: object,
    message: str,
) -> None:
    backend = _FakeG2PK()

    with pytest.raises(G2PKExpectedPhoneError, match=message):
        _generate(
            backend,
            text=text,
            maximum_codepoints=maximum_codepoints,
        )

    assert backend.calls == []


@pytest.mark.parametrize("field", ["descriptive", "group_vowels"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_boolean_options_are_exact(field: str, value: object) -> None:
    backend = _FakeG2PK()
    kwargs = {field: value}

    with pytest.raises(G2PKExpectedPhoneError, match=field):
        _generate(backend, **kwargs)

    assert backend.calls == []


@pytest.mark.parametrize(
    ("word_boundary_policy", "allowed_punctuation", "message"),
    [
        ("interpolate", (",", "."), "word_boundary_policy"),
        (["drop"], (",", "."), "word_boundary_policy"),
        ("drop", [",", "."], "tuple"),
        ("drop", (".", ","), "sorted"),
        ("drop", (",", ","), "duplicates"),
        ("drop", ("..",), "single"),
        ("drop", ("a",), "Unicode punctuation"),
        ("drop", (_TextSubclass(","),), "plain-string"),
    ],
)
def test_output_policy_is_explicit_and_canonical(
    word_boundary_policy: object,
    allowed_punctuation: object,
    message: str,
) -> None:
    backend = _FakeG2PK()

    with pytest.raises(G2PKExpectedPhoneError, match=message):
        _generate(
            backend,
            word_boundary_policy=word_boundary_policy,
            allowed_punctuation=allowed_punctuation,
        )

    assert backend.calls == []


@pytest.mark.parametrize(
    ("output", "allowed_punctuation", "message"),
    [
        (None, (), "invalid"),
        ("", (), "empty"),
        ("   ", (), "no expected Korean phones"),
        ("가", (), "outside"),
        ("ᄀaᅡ", (), "outside"),
        ("ᄀᆪ", (), "outside"),
        ("ᄀ.", (), "outside"),
        ("ᄀ\ue000", (), "outside"),
    ],
)
def test_backend_output_fails_closed(
    output: object,
    allowed_punctuation: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(G2PKExpectedPhoneError, match=message):
        _generate(
            _FakeG2PK(output),
            allowed_punctuation=allowed_punctuation,
        )


def test_caller_approved_unicode_punctuation_is_dropped_only_after_approval() -> None:
    result = _generate(
        _FakeG2PK("가,나."),
        word_boundary_policy="drop",
    )

    assert result.phone_tokens == ("ㄱ", "ㅏ", "ㄴ", "ㅏ")


def test_backend_failure_does_not_echo_sensitive_exception_text_or_cause() -> None:
    backend = _FakeG2PK(error=RuntimeError("/restricted/user-17 transcript"))

    with pytest.raises(G2PKExpectedPhoneError) as captured:
        _generate(backend, text="민감한 문장")

    assert str(captured.value) == "g2pK expected-pronunciation generation failed"
    assert captured.value.__cause__ is None


@pytest.mark.parametrize("backend_version", [None, "0.9.3", "latest"])
def test_injected_backend_requires_the_exact_version(
    backend_version: str | None,
) -> None:
    backend = _FakeG2PK()

    with pytest.raises(G2PKExpectedPhoneError, match="backend"):
        _generate(backend, backend_version=backend_version)

    assert backend.calls == []


def test_injected_backend_rejects_runtime_data_root() -> None:
    with pytest.raises(G2PKExpectedPhoneError, match="must be omitted"):
        _generate(
            _FakeG2PK(),
            nltk_data_root=Path("/tmp/unused"),
        )


def test_production_backend_requires_an_absolute_existing_root() -> None:
    with pytest.raises(G2PKExpectedPhoneError, match="required"):
        generate_g2pk_expected_phones(
            "한글",
            descriptive=False,
            group_vowels=False,
            word_boundary_policy="drop",
            allowed_punctuation=(),
            maximum_codepoints=100,
        )

    with pytest.raises(G2PKExpectedPhoneError, match="absolute"):
        generate_g2pk_expected_phones(
            "한글",
            descriptive=False,
            group_vowels=False,
            word_boundary_policy="drop",
            allowed_punctuation=(),
            maximum_codepoints=100,
            nltk_data_root=Path("relative"),
        )
