"""Phone vocabulary construction."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

SPECIAL_TOKENS = ("<pad>", "<unk>")


def build_vocabulary(
    sequences: Iterable[Sequence[str]],
    *,
    include_word_boundary: bool = False,
) -> tuple[dict[str, int], dict[str, int]]:
    counts: Counter[str] = Counter()
    for sequence in sequences:
        counts.update(sequence)
    tokens = list(SPECIAL_TOKENS)
    if include_word_boundary:
        tokens.append("|")
    tokens.extend(sorted(phone for phone in counts if phone not in tokens))
    return {token: index for index, token in enumerate(tokens)}, dict(counts)


def encode_phones(phones: Sequence[str], vocabulary: dict[str, int]) -> list[int]:
    unknown = vocabulary["<unk>"]
    return [vocabulary.get(phone, unknown) for phone in phones]
