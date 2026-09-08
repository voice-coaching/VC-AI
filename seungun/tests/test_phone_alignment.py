from korean_phone_ctc.evaluation.phone_alignment import align_phones


def test_alignment_classifies_all_operation_types() -> None:
    aligned = align_phones(["ㄱ", "ㅏ", "ㄴ"], ["ㄱ", "ㅓ", "ㄴ", "ㄷ"])
    assert [item.operation for item in aligned] == [
        "correct",
        "substitution",
        "correct",
        "insertion",
    ]


def test_alignment_deletion() -> None:
    aligned = align_phones(["ㄱ", "ㅏ"], ["ㄱ"])
    assert aligned[-1].operation == "deletion"
