"""Bounded all-optimal edit alignment; no arbitrary tie-breaking or path enumeration."""
from __future__ import annotations

from array import array

from .native_phone_contract import BLANK_ID, BOUNDARY_ID, ObservedRun, role_compatible, token_classes
from .phone_rubric import require

MAX_DP_CELLS = 1_000_000
MAX_FRAMES = 30_000


def collapse(ids):
    require(type(ids) is list and 0 < len(ids) <= MAX_FRAMES
            and all(type(i) is int and 0 <= i < 45 for i in ids), "NATIVE_CTC_IDS")
    runs, excluded = [], []
    start = 0
    while start < len(ids):
        end = start + 1
        while end < len(ids) and ids[end] == ids[start]:
            end += 1
        token = ids[start]
        if token in (BLANK_ID, BOUNDARY_ID):
            excluded.append(dict(tokenId=token, startFrame=start, endFrame=end))
        else:
            # UNK and special tokens remain observable barriers, not deletions.
            runs.append(ObservedRun(len(runs), token, start, end))
        start = end
    return tuple(runs), excluded


def align(expected, runs, mappings):
    n, m = len(expected), len(runs)
    require(0 < n <= 4096 and (n + 1) * (m + 1) <= MAX_DP_CELLS, "NATIVE_ALIGNMENT_RESOURCE_LIMIT")
    classes = [frozenset(token_classes(phone, mappings)) for phone in expected]
    # Two uint32 matrices: at most 8 MB; costs are bounded by N+M.
    f = [array("I", [0]) * (m + 1) for _ in range(n + 1)]
    b = [array("I", [0]) * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        f[i][0], b[i][m] = i, n - i
    for j in range(m + 1):
        f[0][j], b[n][j] = j, m - j
    for i in range(n):
        for j in range(m):
            cost = int(runs[j].token_id not in classes[i])
            f[i+1][j+1] = min(f[i][j] + cost, f[i][j+1] + 1, f[i+1][j] + 1)
    for i in range(n-1, -1, -1):
        for j in range(m-1, -1, -1):
            cost = int(runs[j].token_id not in classes[i])
            b[i][j] = min(b[i+1][j+1] + cost, b[i+1][j] + 1, b[i][j+1] + 1)
    optimum = f[n][m]
    positions, insertions = [], set()
    for i, phone in enumerate(expected):
        candidates = []
        for j in range(m + 1):
            if f[i][j] + 1 + b[i+1][j] == optimum:
                # Multiple deletion edges are the same structural outcome, but
                # anchors are checked separately before confirming a deletion.
                if ("DELETION_CANDIDATE", None) not in candidates:
                    candidates.append(("DELETION_CANDIDATE", None))
            if j < m:
                cost = int(runs[j].token_id not in classes[i])
                if f[i][j] + cost + b[i+1][j+1] == optimum:
                    candidates.append(("MATCH" if cost == 0 else "SUBSTITUTION", j))
        reasons = []
        if not classes[i]:
            reasons.append("TOKEN_MAPPING_UNVERIFIED")
        if not phone.provenance_resolved:
            reasons.append("EXPECTED_PROVENANCE_AMBIGUOUS")
        if len(candidates) != 1:
            reasons.append("MULTIPLE_OPTIMAL_ALIGNMENTS")
        operation, selected = candidates[0] if len(candidates) == 1 else ("UNKNOWN", None)
        if selected is not None:
            token = runs[selected].token_id
            if token >= 40:
                reasons.append("UNK_IN_REQUIRED_REGION")
            elif token not in mappings:
                reasons.append("TOKEN_MAPPING_UNVERIFIED")
            elif not role_compatible(token, phone.role):
                reasons.append("ROLE_CONFLICT")
        positions.append(dict(expectedIndex=i, permittedTokenClassIds=sorted(classes[i]),
                              candidates=[dict(operation=op, runId=run) for op, run in candidates],
                              alignmentOperation=operation, selectedRunId=selected, reasonCodes=reasons))
    for i in range(n + 1):
        for j in range(m):
            if f[i][j] + 1 + b[i][j+1] == optimum:
                insertions.add(j)
    return positions, sorted(insertions)
