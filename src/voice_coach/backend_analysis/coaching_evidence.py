"""Versioned evidence admission for teaching, separate from frozen diagnosis.

No weights, vocabulary or detector threshold are modified here. CTC operations
remain model observations, not a transcript or a diagnosis of articulation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

from voice_coach.pronunciation_feedback.feedback_evidence import (
    build_feedback_evidence, decompose_syllables,
)

POLICY = "pronunciation-coaching-evidence-v1"
MAX_ALIGNMENT_CELLS = 262_144


def ambiguous_alignment_indices(expected, observed):
    """All reference positions with alternative minimum-edit assignments.

    Prefix/suffix distances enumerate optimal edges, not just one tie-broken
    traceback. Oversized inputs are explicitly unassessed, never presumed clear.
    """
    n, m = len(expected), len(observed)
    if (n + 1) * (m + 1) > MAX_ALIGNMENT_CELLS:
        return set(range(n)), "LIMIT_EXCEEDED"
    prefix = [list(range(m + 1))] + [[i] + [0] * m for i in range(1, n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            prefix[i][j] = min(prefix[i-1][j] + 1, prefix[i][j-1] + 1,
                               prefix[i-1][j-1] + (expected[i-1] != observed[j-1]))
    suffix = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n, -1, -1):
        suffix[i][m] = n - i
    for j in range(m, -1, -1):
        suffix[n][j] = m - j
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            suffix[i][j] = min(suffix[i+1][j] + 1, suffix[i][j+1] + 1,
                               suffix[i+1][j+1] + (expected[i] != observed[j]))
    optimum, ambiguous = prefix[n][m], set()
    for i in range(n):
        assignments = set()
        for j in range(m + 1):
            if prefix[i][j] + 1 + suffix[i+1][j] == optimum:
                assignments.add(None)
            if j < m and prefix[i][j] + (expected[i] != observed[j]) + suffix[i+1][j+1] == optimum:
                assignments.add(j)
        if len(assignments) != 1:
            ambiguous.add(i)
    return ambiguous, "CHECKED"


def build_coaching_input(*, analysis, text, duration_ms, visual_state="NOT_PROVIDED",
                         visual_observations=()):
    """Retain all rows internally; allow only bounded, justified model claims."""
    raw = analysis.analysis_evidence
    if not isinstance(raw, dict) or not raw.get("phones"):
        raise ValueError("coaching_analysis_evidence_missing")
    selected = analysis.pronunciation_evidence
    bundle = build_feedback_evidence(analysis=raw, target_text=text,
        selected_index=selected.selected_expected_index if selected else 0,
        maximum_alignment_cells=MAX_ALIGNMENT_CELLS)
    rows = bundle["audio"]["phones"]
    expected = bundle["audio"]["expectedPhones"]
    ambiguous, alignment_status = ambiguous_alignment_indices(expected, bundle["audio"]["ctcHypothesis"])
    anchors = bundle["audio"]["textAnchors"]
    written = [getattr(slot, role) for slot in decompose_syllables(text)
               for role in ("onset", "nucleus", "coda") if getattr(slot, role)]
    mapped_indices = [i for i, phone in enumerate(expected) if phone != "|"]
    ambiguous_written, written_status = ambiguous_alignment_indices([expected[i] for i in mapped_indices], written)
    ambiguous_written = {mapped_indices[i] for i in ambiguous_written}
    if written_status != "CHECKED":
        alignment_status = written_status
    facts, groups = [], {}
    for i, row in enumerate(rows):
        anchor = anchors[i]
        reasons = []
        if row["alignment_operation"] == "deletion":
            reasons.append("DEFAULT_DELETION_FEATURES")
        if i in ambiguous:
            reasons.append("ALIGNMENT_AMBIGUOUS")
        if anchor[-1] != "match":
            # edit_alignment uses match/substitution/deletion/insertion.
            reasons.append("WRITTEN_ROLE_UNVERIFIED")
        if i in ambiguous_written:
            reasons.append("WRITTEN_ROLE_AMBIGUOUS")
        if row["expected_phone"] == "ㅇ" and anchor[5] == "onset":
            reasons.append("SILENT_ONSET_TOKEN_CONVENTION")
        has_span = (row["timingProvenance"] == "ctc_nonblank_span"
                    and row["end_ms"] <= duration_ms)
        if not has_span:
            reasons.append("LOCAL_ACOUSTIC_SPAN_UNAVAILABLE")
        if row["alignment_operation"] == "substitution" and not row["observed_candidate"]:
            reasons.append("OBSERVED_CANDIDATE_UNAVAILABLE")
        disposition = "REVIEW_ONLY" if reasons else "ACTIONABLE" if row["is_error"] else "OBSERVED"
        fact = dict(evidenceId=f"phone-{i}", expectedIndex=i,
            expectedPhone=row["expected_phone"], observedCandidate=row["observed_candidate"],
            operation=row["alignment_operation"], flagged=row["is_error"],
            evidenceOrigin="DEFAULT_DELETION_FEATURES" if row["alignment_operation"] == "deletion" else "CTC_ACOUSTIC_FEATURES",
            disposition=disposition, reasonCodes=reasons,
            location=dict(word=anchor[1] if anchor[1] is None or len(anchor[1]) <= 1000 else None,
                syllable=anchor[2], charStart=anchor[3], charEnd=anchor[4],
                writtenRole=anchor[5], roleSemantics="written_not_post_phonology",
                startMs=row["start_ms"] if has_span else None, endMs=row["end_ms"] if has_span else None,
                timingProvenance="CTC_NONBLANK_SPAN" if has_span else "UNAVAILABLE"),
            detectorScore=row["error_score"], operatingThreshold=row["operating_threshold"],
            scoreSemantics=row["score_semantics"], softFeatures=deepcopy(row["soft_features"]))
        facts.append(fact)
        if disposition == "ACTIONABLE":
            key = (row["expected_phone"], row["observed_candidate"], anchor[5])
            groups.setdefault(key, []).append(fact)
    candidates = []
    for members in groups.values():
        first = members[0]
        candidates.append(dict(candidateId=f"candidate-{first['expectedIndex']}",
            evidenceIds=[f["evidenceId"] for f in members],
            expectedPhone=first["expectedPhone"], observedCandidate=first["observedCandidate"],
            writtenRole=first["location"]["writtenRole"], repetitionCount=len(members),
            claimScope="MODEL_OBSERVATION_NOT_CONFIRMED_ARTICULATION", disposition="ACTIONABLE"))
    # Repetition and first occurrence are inspectable; uncalibrated detector
    # rankings across phones do not define pedagogical importance.
    candidates.sort(key=lambda c: (-c["repetitionCount"], int(c["candidateId"].split('-')[-1])))
    review = [f for f in facts if f["disposition"] == "REVIEW_ONLY"]
    return dict(schemaVersion="voice-coaching.coaching-input.v1", policyRevision=POLICY,
        task=dict(mode="COACHING", language="ko", objective="practice_and_check_improvement"),
        utterance=dict(text=text, durationMs=duration_ms, durationSemantics="file_duration_not_speaking_rate",
                       expectedPhones=expected, ctcHypothesis=bundle["audio"]["ctcHypothesis"]),
        coverage=dict(expectedPhoneCount=len(rows), actionableCandidateCount=len(candidates),
            reviewOnlyPhoneCount=len(review), alignmentCheck=alignment_status,
            includedCandidateIds=[c["candidateId"] for c in candidates], omittedCandidateIds=[],
            selectionPolicy="repetition_then_first_occurrence", omittedReason=None),
        facts=facts, candidates=candidates,
        visual=dict(status=visual_state, observations=list(visual_observations),
                    referenceStatus="NOT_VALIDATED", correctiveClaimsAllowed=False),
        score=dict(validity="INSUFFICIENT_EVIDENCE" if review else "NOT_CALIBRATED",
                   overallScore=None, reasonCodes=sorted({r for f in review for r in f["reasonCodes"]}) or ["EXPERT_CALIBRATION_REQUIRED"]),
        previousAttempt=None)


def project_candidates(source, count):
    """Each included candidate keeps all its supporting/counter evidence."""
    view = deepcopy(source)
    view["candidates"] = view["candidates"][:count]
    ids = {i for c in view["candidates"] for i in c["evidenceIds"]}
    # Preserve review counts/reasons globally; only bounded examples in the LLM
    # input. They cannot become coaching items.
    review = [f for f in view["facts"] if f["disposition"] == "REVIEW_ONLY"]
    view["reviewReasonCounts"] = dict(Counter(r for f in review for r in f["reasonCodes"]))
    view["facts"] = [f for f in view["facts"] if f["evidenceId"] in ids]
    all_ids = [c["candidateId"] for c in source["candidates"]]
    view["coverage"].update(includedCandidateIds=all_ids[:count], omittedCandidateIds=all_ids[count:],
                            omittedReason="INPUT_BUDGET" if count < len(all_ids) else None)
    return view
