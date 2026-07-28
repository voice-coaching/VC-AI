from korean_phone_ctc.data.phone_vocab import build_vocabulary, encode_phones


def test_vocabulary_is_deterministic_and_pad_is_ctc_blank() -> None:
    first, counts = build_vocabulary([["ㅏ", "ㄱ"], ["ㄱ"]])
    second, _ = build_vocabulary([["ㄱ"], ["ㅏ", "ㄱ"]])
    assert first == second
    assert first["<pad>"] == 0
    assert first["<unk>"] == 1
    assert "|" not in first
    assert counts == {"ㅏ": 1, "ㄱ": 2}


def test_unknown_phone_encoding() -> None:
    vocabulary, _ = build_vocabulary([["ㄱ"]])
    assert encode_phones(["ㄱ", "X"], vocabulary) == [
        vocabulary["ㄱ"],
        vocabulary["<unk>"],
    ]
