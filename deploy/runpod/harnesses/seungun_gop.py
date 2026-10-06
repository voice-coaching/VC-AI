"""CTC AF-SD acoustic evidence from the existing Native forward pass.

No grade, calibration gate, second model, or server-owned RUBRIC_RESULT.
Cao et al., Interspeech 2024: sum distinct substituted/deleted transcripts,
each of whose probability already sums all CTC alignments.
"""
from __future__ import annotations

import math
import time

import torch
from torch.nn.functional import ctc_loss

TOKENS = "A B BB CHh D DD E EO EU G GG H I J JJ Kh L M N NG O Ph R S SS Th U euI iA iE iEO iO iU k oA oE p t uEO uI | [UNK] [PAD] <s> </s>".split()
VOWELS = dict(zip("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ",
                  "A E iA iE EO E iEO iE O oA oE oE iO U uEO oE uI iU EU euI I".split()))
CONSONANTS = dict(zip("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ",
                      "G GG N D DD R M B BB S SS NG J JJ CHh Kh Th Ph H".split()))
CODAS = {"ㄱ": "k", "ㄷ": "t", "ㅂ": "p", "ㄹ": "L"}
MERGED = {"E": ["ㅐ", "ㅔ"], "iE": ["ㅒ", "ㅖ"], "oE": ["ㅙ", "ㅚ", "ㅞ"]}


def _alignment(expected, observed):
    """Unit edit alignment; substitutions are covered, deletions are uncovered.

    Fixed tie order: diagonal, deletion, insertion. Ambiguous alignments are
    disclosed; this is CTC token coverage, not a human phonetic correctness label.
    """
    n, m = len(expected), len(observed)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i-1][j-1] + (expected[i-1] != observed[j-1]),
                             cost[i-1][j] + 1, cost[i][j-1] + 1)
    i, j, rows, ambiguous = n, m, [], False
    while i or j:
        options = []
        if i and j and cost[i][j] == cost[i-1][j-1] + (expected[i-1] != observed[j-1]):
            options.append((i-1, j-1, "MATCH" if expected[i-1] == observed[j-1] else "SUBSTITUTION"))
        if i and cost[i][j] == cost[i-1][j] + 1:
            options.append((i-1, j, "DELETION"))
        if j and cost[i][j] == cost[i][j-1] + 1:
            options.append((i, j-1, "INSERTION"))
        ambiguous |= len(options) > 1
        ni, nj, operation = options[0]
        rows.append(dict(expectedIndex=i-1 if operation != "INSERTION" else None,
                         observedIndex=j-1 if operation != "DELETION" else None, operation=operation))
        i, j = ni, nj
    return list(reversed(rows)), ambiguous


def extract(native, metadata, vocabulary):
    started = time.perf_counter()
    if {name: i for i, name in enumerate(TOKENS)} != vocabulary:
        raise ValueError("GOP_VOCABULARY_IDENTITY")
    samples, excluded = [], []
    for row in metadata:
        if row["phone"] == "ㅇ" and row["role"] == "onset":
            excluded.append(row["expected_index"])
            continue
        phone, role = row["phone"], row["role"]
        token = VOWELS.get(phone) if role == "nucleus" else (
            CODAS.get(phone, CONSONANTS.get(phone)) if role == "coda" else CONSONANTS.get(phone))
        if token is None:
            raise ValueError("GOP_EXPECTED_TOKEN_UNREPRESENTABLE")
        samples.append(dict(index=len(samples), sourceIndex=row["expected_index"], phone=phone,
                            role=role, wordIndex=row["original_word_index"], syllableIndex=row["syllable_index"],
                            token=token, tokenId=vocabulary[token], indistinguishablePhones=MERGED.get(token, [phone])))
    target = tuple(row["tokenId"] for row in samples)
    if not target:
        raise ValueError("GOP_EMPTY_REFERENCE")
    # Float64 log-space CTC prevents underflow for complete-utterance likelihoods.
    # Each batch reuses logits; there is no additional acoustic inference.
    logits = torch.as_tensor(native["arr"], dtype=torch.float64)
    logp = logits.log_softmax(-1)
    frames = logp.shape[0]

    def likelihood(sequences):
        values = []
        for start in range(0, len(sequences), 32):
            batch = sequences[start:start+32]
            lengths = torch.tensor([len(seq) for seq in batch], dtype=torch.long)
            flat = torch.tensor([p for seq in batch for p in seq], dtype=torch.long)
            with torch.inference_mode():
                loss = ctc_loss(logp[:, None, :].expand(-1, len(batch), -1).contiguous(), flat,
                                torch.full((len(batch),), frames, dtype=torch.long), lengths,
                                blank=42, reduction="none", zero_infinity=False)
            values.extend((-loss).tolist())
        return values

    reference = likelihood([target])[0]
    for i, sample in enumerate(samples):
        # Include the canonical transcript exactly once; no token insertion.
        alternatives = sorted({target[:i] + (token,) + target[i+1:] for token in range(40)}
                              | {target[:i] + target[i+1:]})
        logs = likelihood(alternatives)
        denominator = torch.logsumexp(torch.tensor(logs, dtype=torch.float64), 0).item()
        valid = math.isfinite(reference) and math.isfinite(denominator)
        best = max(range(len(logs)), key=logs.__getitem__)
        winner = alternatives[best]
        sample.update(gop=reference-denominator if valid else None,
                      logReference=reference if math.isfinite(reference) else None,
                      logAlternatives=denominator if math.isfinite(denominator) else None,
                      bestAlternative="DELETION" if len(winner) < len(target) else TOKENS[winner[i]],
                      alternativesCount=len(alternatives))
    observed = [r for r in native["runs"] if r["is_phone"]]
    alignment, ambiguous = _alignment(target, [r["token_id"] for r in observed])
    deleted = sum(r["operation"] == "DELETION" for r in alignment)
    return dict(revision="gop-ctc-af-sd-native-v1", formula="log P_CTC(L|X) - log sum_{Lprime in unique SD(i)} P_CTC(Lprime|X)",
                reference="https://www.isca-archive.org/interspeech_2024/cao24b_interspeech.pdf",
                referenceConvention="full g2pK phones; silent onset excluded; no word-boundary tokens",
                samples=samples, excludedSilentOnsetSourceIndices=excluded, observed=observed,
                alignment=alignment, alignmentAmbiguous=ambiguous,
                coverage=dict(sampleCount=len(samples), deletedCount=deleted,
                              evidenceUnits=4*(len(samples)-deleted), possibleUnits=4*len(samples)),
                elapsedSeconds=time.perf_counter()-started)
