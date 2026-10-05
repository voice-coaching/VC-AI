"""Full-attempt Native evidence service, with replayable decisions and bindings."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import math
from uuid import UUID

from .native_phone_alignment import MAX_FRAMES, align, collapse
from .native_phone_assessment import assess, digest, margin_features, policy_contract, sha256_json, validate_vocabulary
from .native_phone_contract import (
    ALIGNMENT_REVISION, ASSESSMENT_REVISION, EVIDENCE_REVISION, EXPECTED_REVISION,
    FEATURE_REVISION, INVENTORY_REVISION, MODEL_REVISION, MODEL_WEIGHTS,
    NUMPY_VERSION, RUBRIC_REVISION, TOKENS, ObservedRun, expected_from_metadata,
)
from .phone_rubric import CRITERIA, STATES, require

BINDING_KEYS = {"requestId", "executionId", "sourceAudioSha256", "preparedWavSha256",
                "scriptSha256", "coreManifestSha256", "vocabularySha256"}


def validate_binding(binding):
    require(type(binding) is dict and set(binding) == BINDING_KEYS, "NATIVE_BINDING_SHAPE")
    for key in ("requestId", "executionId"):
        require(type(binding[key]) is str and str(UUID(binding[key])) == binding[key], "NATIVE_ATTEMPT_ID")
    require(all(digest(binding[k]) for k in BINDING_KEYS - {"requestId", "executionId"}), "NATIVE_BINDING_DIGEST")


def frame_region(index, expected, positions, runs):
    position = positions[index]
    selected = position["selectedRunId"]
    if selected is not None:
        run = runs[selected]
        return run.start_frame, run.end_frame
    if position["alignmentOperation"] != "DELETION_CANDIDATE" or not 0 < index < len(expected)-1:
        return None
    left, right = positions[index-1], positions[index+1]
    if not (expected[index-1].word_index == expected[index].word_index == expected[index+1].word_index
            and left["alignmentOperation"] == right["alignmentOperation"] == "MATCH"
            and not left["reasonCodes"] and not right["reasonCodes"]
            and left["selectedRunId"] is not None and right["selectedRunId"] is not None):
        return None
    # Consecutive observed anchors exclude intervening insertions/UNK/specials.
    if right["selectedRunId"] != left["selectedRunId"] + 1:
        return None
    start, end = runs[left["selectedRunId"]].end_frame, runs[right["selectedRunId"]].start_frame
    return (start, end) if start < end else None


def word_span(phone, words):
    matches = [word for word in words if word.get("wordIndex") == phone.word_index]
    if len(matches) != 1:
        return None
    word = matches[0]
    if (set(word) != {"wordIndex", "startSeconds", "endSeconds"}
            or type(word["wordIndex"]) is not int
            or not all(type(word[k]) in (float, int) and math.isfinite(word[k])
                       for k in ("startSeconds", "endSeconds"))
            or not 0 <= word["startSeconds"] < word["endSeconds"]):
        return None
    return dict(word)


def localized(region, span):
    if region is None or span is None:
        return False
    # Use frame centers from the pinned Native stride/receptive-field contract.
    first = (region[0] * 320 + 199.5) / 16000
    last = ((region[1] - 1) * 320 + 199.5) / 16000
    return span["startSeconds"] <= first <= last <= span["endSeconds"]


def build_evidence(*, logits, ctc_ids, vocabulary, metadata, words, binding, policy):
    """Called with the existing forward result. Does not import or load a model."""
    import numpy as np

    require(np.__version__ == NUMPY_VERSION, "NATIVE_NUMERIC_RUNTIME_VERSION")
    validate_binding(binding)
    require(validate_vocabulary(vocabulary) == binding["vocabularySha256"], "NATIVE_VOCABULARY_BINDING")
    require(isinstance(logits, np.ndarray) and logits.dtype == np.dtype("float32")
            and logits.ndim == 2 and 0 < logits.shape[0] <= MAX_FRAMES and logits.shape[1] == 45
            and np.isfinite(logits).all(), "NATIVE_LOGITS_CONTRACT")
    require(logits.argmax(axis=1).tolist() == ctc_ids, "NATIVE_CTC_LOGITS_MISMATCH")
    mappings = policy_contract(policy)
    expected, exclusions = expected_from_metadata(metadata)
    runs, nonphones = collapse(ctc_ids)
    positions, insertions = align(expected, runs, mappings)
    require(type(words) is list and len(words) <= 4096, "NATIVE_MFA_WORDS")
    for index, (phone, position) in enumerate(zip(expected, positions, strict=True)):
        region, span = frame_region(index, expected, positions, runs), word_span(phone, words)
        features = None
        if not localized(region, span):
            position["reasonCodes"].append("FRAME_REGION_UNAVAILABLE")
        elif position["permittedTokenClassIds"]:
            features = margin_features(logits, position["permittedTokenClassIds"], *region)
        acoustic, rule, reasons = assess(phone, position, features, policy)
        position.update(expectedPhone=phone.phone, criterionId=phone.criterion, role=phone.role,
                        sourceIndex=phone.source_index, wordIndex=phone.word_index, syllableIndex=phone.syllable_index,
                        acousticAssessment=acoustic, assessmentRuleId=rule, reasonCodes=reasons,
                        nativeFrameSpan=list(region) if region else None, mfaWordSpan=span, features=features)
    ready = all(p["acousticAssessment"] != "UNKNOWN" for p in positions)
    return dict(schemaVersion=EVIDENCE_REVISION, status="READY" if ready else "UNSCORABLE",
                binding=dict(binding), modelRevision=MODEL_REVISION, modelWeightsSha256=MODEL_WEIGHTS,
                expectedPhoneRevision=EXPECTED_REVISION, inventoryRevision=INVENTORY_REVISION,
                alignmentRevision=ALIGNMENT_REVISION, assessmentRevision=ASSESSMENT_REVISION,
                assessmentPolicySha256=sha256_json(policy), rubricRevision=RUBRIC_REVISION,
                device="cpu", dtype="float32", inferenceCount=1, expectedCount=len(expected),
                frameCount=len(ctc_ids), logitsSha256=hashlib.sha256(logits.astype("<f4", copy=False).tobytes(order="C")).hexdigest(),
                sourceMetadata=metadata, excludedExpectedPositions=exclusions, observedRuns=[asdict(r) for r in runs],
                nonPhoneRuns=nonphones, possibleInsertions=insertions, mfaWords=words, positions=positions,
                failureReasons=sorted({reason for p in positions for reason in p["reasonCodes"]}))


def evidence_rows(evidence, *, binding, policy):
    """Replay structural/acoustic decisions before scoring; no trusted READY flag.

    Full numerical feature replay additionally needs the digest-bound private
    logits artifact; that is an archive verifier task, never another inference.
    """
    validate_binding(binding)
    mappings = policy_contract(policy, approved=True)
    require(type(evidence) is dict and evidence.get("status") == "READY"
            and evidence.get("binding") == binding, "NATIVE_EVIDENCE_NOT_READY_OR_BINDING")
    identities = dict(schemaVersion=EVIDENCE_REVISION, modelRevision=MODEL_REVISION,
                      modelWeightsSha256=MODEL_WEIGHTS, expectedPhoneRevision=EXPECTED_REVISION,
                      inventoryRevision=INVENTORY_REVISION, alignmentRevision=ALIGNMENT_REVISION,
                      assessmentRevision=ASSESSMENT_REVISION, rubricRevision=RUBRIC_REVISION,
                      assessmentPolicySha256=sha256_json(policy), device="cpu", dtype="float32", inferenceCount=1)
    require(all(evidence.get(k) == v for k, v in identities.items())
            and type(evidence.get("inferenceCount")) is int, "NATIVE_EVIDENCE_IDENTITY")
    expected, exclusions = expected_from_metadata(evidence["sourceMetadata"])
    require(type(evidence.get("expectedCount")) is int and evidence["expectedCount"] == len(expected)
            and evidence.get("excludedExpectedPositions") == exclusions, "NATIVE_EXPECTED_IDENTITY")
    count = evidence.get("frameCount")
    require(type(count) is int and 0 < count <= MAX_FRAMES and digest(evidence.get("logitsSha256")), "NATIVE_FRAME_COUNT")
    raw_runs = evidence.get("observedRuns")
    require(type(raw_runs) is list and len(raw_runs) <= count, "NATIVE_RUNS")
    runs = []
    last = 0
    for i, row in enumerate(raw_runs):
        require(type(row) is dict and set(row) == {"index", "token_id", "start_frame", "end_frame"}
                and all(type(v) is int for v in row.values()) and row["index"] == i
                and 0 <= row["token_id"] < 45 and row["token_id"] not in {40, 42}
                and last <= row["start_frame"] < row["end_frame"] <= count, "NATIVE_RUN_IDENTITY")
        runs.append(ObservedRun(**row))
        last = row["end_frame"]
    derived, insertions = align(expected, runs, mappings)
    require(evidence.get("possibleInsertions") == insertions and evidence.get("failureReasons") == [], "NATIVE_ALIGNMENT_IDENTITY")
    positions, words = evidence.get("positions"), evidence.get("mfaWords")
    require(type(positions) is list and len(positions) == len(expected)
            and type(words) is list and len(words) <= 4096, "NATIVE_POSITIONS")
    rows = [dict(criterionId=key, sampleCount=0, **dict.fromkeys(STATES, 0)) for key, _, _ in CRITERIA]
    for i, (phone, actual, structural) in enumerate(zip(expected, positions, derived, strict=True)):
        require(type(actual) is dict and all(actual.get(k) == v for k, v in structural.items()), "NATIVE_ALIGNMENT_REPLAY")
        region, span = frame_region(i, expected, derived, runs), word_span(phone, words)
        require(localized(region, span) and actual.get("nativeFrameSpan") == list(region)
                and actual.get("mfaWordSpan") == span and actual.get("expectedPhone") == phone.phone
                and actual.get("criterionId") == phone.criterion and actual.get("role") == phone.role
                and actual.get("sourceIndex") == phone.source_index
                and actual.get("wordIndex") == phone.word_index and actual.get("syllableIndex") == phone.syllable_index,
                "NATIVE_POSITION_REPLAY")
        features = actual.get("features")
        require(type(features) is dict and features.get("definition") == FEATURE_REVISION
                and features.get("startFrame") == region[0] and features.get("endFrame") == region[1]
                and features.get("frameCount") == region[1]-region[0]
                and features.get("expectedClassIds") == structural["permittedTokenClassIds"]
                and features.get("competitorClassIds") == sorted(set(range(40))-set(structural["permittedTokenClassIds"]))
                and type(features.get("margin")) in (int, float) and math.isfinite(features["margin"])
                and digest(features.get("logitsWindowSha256")), "NATIVE_FEATURE_REPLAY")
        acoustic, rule, reasons = assess(phone, structural, features, policy)
        require(acoustic != "UNKNOWN" and actual.get("acousticAssessment") == acoustic
                and actual.get("assessmentRuleId") == rule and actual.get("reasonCodes") == reasons,
                "NATIVE_ASSESSMENT_REPLAY")
        state = ("deletedClear" if acoustic == "DELETED" else
                 ("matched" if structural["alignmentOperation"] == "MATCH" else "substituted")
                 + ("Flagged" if acoustic == "FLAGGED" else "Clear"))
        for row in rows:
            if row["criterionId"] in {phone.criterion, "coverage"}:
                row["sampleCount"] += 1
                row[state] += 1
    return rows


def verify_archived_logits(evidence, *, logits, ctc_ids, vocabulary, binding, policy):
    rebuilt = build_evidence(logits=logits, ctc_ids=ctc_ids, vocabulary=vocabulary,
                             metadata=evidence["sourceMetadata"], words=evidence["mfaWords"], binding=binding, policy=policy)
    require(rebuilt == evidence, "NATIVE_ARCHIVE_EVIDENCE_MISMATCH")


def verify_archived_core(core, *, binding, policy):
    """Archive-worker hook. Runs no model and must not precede result publication."""
    import base64
    import numpy as np

    require(np.__version__ == NUMPY_VERSION, "NATIVE_NUMERIC_RUNTIME_VERSION")
    record = core.get("nativeScoringLogits")
    require(type(record) is dict and set(record) == {"schemaVersion", "encoding", "rows", "columns", "sha256", "data"}
            and record["schemaVersion"] == "native-logits-fp32-v1" and record["encoding"] == "base64-le-f32"
            and type(record["rows"]) is int and 0 < record["rows"] <= MAX_FRAMES
            and type(record["columns"]) is int and record["columns"] == 45
            and type(record["data"]) is str and len(record["data"]) <= MAX_FRAMES*45*4*4//3,
            "NATIVE_ARCHIVED_LOGITS_SHAPE")
    raw = base64.b64decode(record["data"], validate=True)
    require(len(raw) == record["rows"] * 45 * 4 and hashlib.sha256(raw).hexdigest() == record["sha256"],
            "NATIVE_ARCHIVED_LOGITS_DIGEST")
    logits = np.frombuffer(raw, dtype="<f4").reshape(record["rows"], 45)
    verify_archived_logits(core["nativeScoringEvidence"], logits=logits, ctc_ids=logits.argmax(axis=1).tolist(),
                           vocabulary={token: i for i, token in enumerate(TOKENS)}, binding=binding, policy=policy)
