from korean_phone_ctc.data.speaker_split import speaker_disjoint_split


def test_split_is_deterministic_and_speaker_disjoint() -> None:
    records = [
        {
            "speaker_id": f"s{speaker}",
            "language": ["Thai", "Vietnamese", "Chinese"][speaker % 3],
            "proficiency": ["A", "B"][speaker % 2],
        }
        for speaker in range(30)
        for _ in range(2)
    ]
    kwargs = {
        "ratios": {"train": 0.8, "dev": 0.1, "test": 0.1},
        "stratify_keys": ["language", "proficiency"],
        "seed": 42,
    }
    first = speaker_disjoint_split(records, **kwargs)
    second = speaker_disjoint_split(reversed(records), **kwargs)
    assert first == second
    assert set(first) == {f"s{speaker}" for speaker in range(30)}
    assert set(first.values()) == {"train", "dev", "test"}


def test_empty_split() -> None:
    assert (
        speaker_disjoint_split([], ratios={"train": 0.8, "test": 0.2}, stratify_keys=[])
        == {}
    )
