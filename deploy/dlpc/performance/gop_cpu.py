"""CTC AF-SD acoustic evidence from the existing Native forward pass.

No grade, calibration gate, second model, or server-owned RUBRIC_RESULT.
Cao et al., Interspeech 2024: sum distinct substituted/deleted transcripts,
each of whose probability already sums all CTC alignments.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import torch
from torch.nn.functional import ctc_loss

# Release-builder constants, never process-global settings changed per request.
MAX_BATCH = 64
WORKSPACE_ESTIMATE_BYTES = 64 * 1024**2
# Immutable release settings. CPU remains the historical default; CUDA devices
# are selected by the J406 builder, never silently substituted at request time.
CTC_DEVICES = ("cpu",)


def check_devices():
    """Check the complete execution topology before resident readiness."""
    if not CTC_DEVICES or len(set(CTC_DEVICES)) != len(CTC_DEVICES):
        raise ValueError("GOP_CTC_DEVICES_INVALID")
    devices = []
    for name in CTC_DEVICES:
        device = torch.device(name)
        if device.type == 'cuda':
            if device.index is None or device.index >= torch.cuda.device_count():
                raise RuntimeError("GOP_CUDA_DEVICE_UNAVAILABLE")
            torch.cuda.get_device_properties(device)
        elif name != 'cpu':
            raise ValueError("GOP_CTC_DEVICES_INVALID")
        devices.append(device)
    return devices


class _Likelihoods:
    """One bounded queue per device; one copy of the utterance per worker.

    Native acoustic inference has already finished. Only independent CTC
    transcript likelihoods are sharded; no audio/model inference is repeated.
    """

    def __init__(self, logp, batch_limit, per_candidate):
        self.logp = logp
        self.batch_limit = batch_limit
        self.per_candidate = per_candidate
        self.stack = ExitStack()
        self.workers = []
        self.pending = []

    def __enter__(self):
        try:
            for device in check_devices():
                name = str(device)
                pool = self.stack.enter_context(ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix='ctc-' + name.replace(':', '-')))
                # All CUDA operations, including allocation, happen in this
                # device's worker thread. Keep the CPU log_softmax identical.
                local = pool.submit(self._prepare, device).result()
                self.workers.append((pool, local))
            return self
        except BaseException:
            self.stack.close()
            raise

    def _prepare(self, device):
        with torch.inference_mode():
            local = self.logp.to(device)
            limit = self.batch_limit
            if device.type == 'cuda':
                free, _ = torch.cuda.mem_get_info(device)
                # Leave most free VRAM for other residents and allocator peaks.
                budget = min(WORKSPACE_ESTIMATE_BYTES, free // 4)
                limit = min(limit, budget // self.per_candidate)
                if limit < 1:
                    raise RuntimeError("GOP_CUDA_WORKSPACE_UNAVAILABLE")
            return local, limit

    @staticmethod
    def _run(local, batch):
        logp, _ = local
        with torch.inference_mode():
            # Native CUDA CTC requires targets on the same device as log_probs;
            # length vectors remain on CPU. FP64 avoids the cuDNN FP32 path.
            flat = torch.tensor([p for seq in batch for p in seq],
                                dtype=torch.long, device=logp.device)
            lengths = torch.tensor([len(seq) for seq in batch], dtype=torch.long)
            loss = ctc_loss(logp[:, None, :].expand(-1, len(batch), -1).contiguous(),
                            flat, torch.full((len(batch),), logp.shape[0], dtype=torch.long),
                            lengths, blank=42, reduction="none", zero_infinity=False)
            return (-loss).cpu().tolist()

    @property
    def limit(self):
        return min(local[1] for _, local in self.workers)

    def reference(self, target):
        pool, local = self.workers[0]
        return pool.submit(self._run, local, [target]).result()[0]

    def submit(self, candidates, destinations):
        pool, local = self.workers[len(self.pending)]
        self.pending.append((pool.submit(self._run, local, candidates), destinations))

    def collect(self, logs):
        # Preserve candidate order and fail the request on any device error.
        # No OOM retry that could conceal a different numerical execution path.
        for future, destinations in self.pending:
            for positions, value in zip(destinations, future.result()):
                for phone_index, candidate_index in positions:
                    logs[phone_index][candidate_index] = value
        self.pending.clear()

    def __exit__(self, *args):
        try:
            return self.stack.__exit__(*args)
        finally:
            self.pending.clear()
            self.workers.clear()

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
    # Expanded logits + native CTC alpha workspace + targets, with 2x margin.
    # This estimates temporary tensors, not a hard RSS cap. A single candidate
    # remains possible when its estimate exceeds the budget.
    per_candidate = 2 * (8 * frames * (logp.shape[1] + 2*len(target)+1)
                         + 8 * len(target))
    batch_limit = max(1, min(MAX_BATCH, WORKSPACE_ESTIMATE_BYTES // max(1, per_candidate)))

    def alternatives_for(i):
        return sorted({target[:i] + (token,) + target[i+1:] for token in range(40)}
                      | {target[:i] + target[i+1:]})

    # Keep only one microbatch of transcript tuples. A whole-request transcript
    # cache would grow quadratically with reference length.
    pending, logs_by_phone = {}, []

    likelihoods = _Likelihoods(logp, batch_limit, per_candidate)
    with likelihoods:
        reference = likelihoods.reference(target)

        def flush():
            if pending:
                likelihoods.submit(list(pending), list(pending.values()))
                pending.clear()
                if len(likelihoods.pending) == len(likelihoods.workers):
                    likelihoods.collect(logs_by_phone)

        for i in range(len(samples)):
            alternatives = alternatives_for(i)
            logs_by_phone.append([None] * len(alternatives))
            for j, candidate in enumerate(alternatives):
                if candidate == target:
                    logs_by_phone[i][j] = reference
                    continue
                pending.setdefault(candidate, []).append((i, j))
                if len(pending) >= likelihoods.limit:
                    flush()
        flush()
        likelihoods.collect(logs_by_phone)
    for i, sample in enumerate(samples):
        # Include the canonical transcript exactly once; no token insertion.
        alternatives = alternatives_for(i)
        logs = logs_by_phone[i]
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
