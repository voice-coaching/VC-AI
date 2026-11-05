"""Read-only Native/GOP evidence adapter for the unchanged phone calculator.

The state names are calculator inputs, not human correctness labels. No legacy
detector fields are synthesized and no acoustic model or provider is called.
"""
from __future__ import annotations

import math

from . import phone_rubric
from .policy import encode, error_code, number, require, sha

REVISION = "native-phone-rubric-evidence-v2"
LEGACY_REVISION = "native-phone-rubric-evidence-v1"
OPERATIONS = {"MATCH": "matched", "SUBSTITUTION": "substituted", "DELETION": "deleted"}
STATES = ("matchedClear", "matchedFlagged", "substitutedClear", "substitutedFlagged",
          "deletedClear", "deletedFlagged")
GROUPS = (
    ("vowels", "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"),
    ("plain_stops", "ㄱㄷㅂ"), ("tense_stops", "ㄲㄸㅃ"),
    ("aspirated_stops", "ㅋㅌㅍ"), ("fricatives", "ㅅㅆㅎ"),
    ("affricates", "ㅈㅉㅊ"), ("nasals", "ㄴㅁㅇ"), ("liquid", "ㄹ"),
)


def validate_native_policy(policy: dict) -> None:
    require(type(policy) is dict and policy.get("revision") in {REVISION, LEGACY_REVISION},
            "NATIVE_RUBRIC_POLICY")
    fixed = dict(validationStatus="EXPERT_REVIEW_PENDING", sourceRevision="gop-ctc-af-sd-native-v1",
                 scope="MODEL_SUPPORT_AND_TOKEN_ALIGNMENT_NOT_HUMAN_CORRECTNESS",
                 flagRule="gop <= gopFlagThreshold",
                 thresholdBasis="PROVISIONAL_ENGINEERING_STARTING_VALUE_NOT_CALIBRATED",
                 missingEvidence="UNSCORABLE_NO_IMPUTATION",
                 silentOnset="PRESERVE_RECORDED_EXCLUSIONS",
                 mergedPhones="PRESERVE_TOKEN_EQUIVALENCE_NO_ADDED_PENALTY")
    if policy["revision"] == LEGACY_REVISION:
        fixed["alignmentAmbiguity"] = "UNSCORABLE"
    else:
        fixed.update(alignmentAmbiguity="SCORE_REPORTED_ALIGNMENT_RETAIN_WARNING",
                     referenceCountBasis="GOP_REFERENCE_SAMPLES",
                     deletionTreatment="ZERO_UNITS_IN_EXISTING_RUBRIC")
    require(set(policy) == {"revision", *fixed, "gopFlagThreshold", "logConsistencyTolerance", "maximumSamples"}
            and all(policy.get(k) == v for k, v in fixed.items()), "NATIVE_RUBRIC_POLICY_CONTRACT")
    require(number(policy["gopFlagThreshold"]) and policy["gopFlagThreshold"] < 0,
            "NATIVE_RUBRIC_THRESHOLD")
    require(number(policy["logConsistencyTolerance"]) and 0 < policy["logConsistencyTolerance"] <= 0.000001,
            "NATIVE_RUBRIC_TOLERANCE")
    require(type(policy["maximumSamples"]) is int and 1 <= policy["maximumSamples"] <= 4096,
            "NATIVE_RUBRIC_LIMIT")


def _positions(core: dict, policy: dict) -> tuple[list[dict], list[int]]:
    source = core.get("phonemeAssessment")
    require(type(source) is dict and source.get("revision") == policy["sourceRevision"],
            "NATIVE_RUBRIC_SOURCE_UNAVAILABLE")
    require(type(source.get("alignmentAmbiguous")) is bool, "NATIVE_RUBRIC_ALIGNMENT_STATUS")
    if policy["revision"] == LEGACY_REVISION:
        require(not source["alignmentAmbiguous"], "NATIVE_RUBRIC_ALIGNMENT_AMBIGUOUS")
    samples, observed, alignment = (source.get(k) for k in ("samples", "observed", "alignment"))
    require(type(samples) is list and 0 < len(samples) <= policy["maximumSamples"], "NATIVE_RUBRIC_SAMPLES")
    require(type(observed) is list and len(observed) <= 2 * policy["maximumSamples"]
            and type(alignment) is list and 0 < len(alignment) <= len(samples) + len(observed),
            "NATIVE_RUBRIC_ALIGNMENT")
    native = core.get("native") or {}
    runs = native.get("ctc_runs")
    require(type(runs) is list and all(type(r) is dict and type(r.get("is_phone")) is bool for r in runs)
            and observed == [r for r in runs if r["is_phone"]], "NATIVE_RUBRIC_OBSERVED_IDENTITY")
    require(all(type(r) is dict and type(r.get("token_id")) is int and 0 <= r["token_id"] < 40
                and type(r.get("token")) is str and r.get("is_phone") is True for r in observed),
            "NATIVE_RUBRIC_OBSERVED_TOKEN")
    excluded = source.get("excludedSilentOnsetSourceIndices")
    require(type(excluded) is list and len(excluded) <= policy["maximumSamples"]
            and all(type(i) is int and i >= 0 for i in excluded)
            and excluded == sorted(set(excluded)), "NATIVE_RUBRIC_EXCLUSIONS")
    group_for_phone = {p: k for k, phones in GROUPS for p in phones}
    source_indices = []
    for index, sample in enumerate(samples):
        require(type(sample) is dict and type(sample.get("index")) is int and sample["index"] == index
                and type(sample.get("sourceIndex")) is int and sample["sourceIndex"] >= 0,
                "NATIVE_RUBRIC_SAMPLE_IDENTITY")
        phone, role = sample.get("phone"), sample.get("role")
        require(type(phone) is str and phone in group_for_phone and role in {"onset", "nucleus", "coda"}
                and (role == "nucleus") == (phone in GROUPS[0][1])
                and not (phone == "ㅇ" and role == "onset"), "NATIVE_RUBRIC_PHONE")
        require(type(sample.get("tokenId")) is int and 0 <= sample["tokenId"] < 40
                and type(sample.get("token")) is str, "NATIVE_RUBRIC_EXPECTED_TOKEN")
        require(all(type(sample.get(k)) is int and sample[k] >= 0 for k in ("wordIndex", "syllableIndex")),
                "NATIVE_RUBRIC_LOCATION")
        equivalent = sample.get("indistinguishablePhones")
        require(type(equivalent) is list and phone in equivalent
                and all(type(p) is str and p in group_for_phone for p in equivalent)
                and len(equivalent) == len(set(equivalent)),
                "NATIVE_RUBRIC_EQUIVALENCE")
        gop, ref, alt = (sample.get(k) for k in ("gop", "logReference", "logAlternatives"))
        require(all(number(v) for v in (gop, ref, alt)) and gop <= policy["logConsistencyTolerance"]
                and math.isclose(ref - alt, gop, rel_tol=0, abs_tol=policy["logConsistencyTolerance"]),
                "NATIVE_RUBRIC_GOP_UNAVAILABLE_OR_INCONSISTENT")
        source_indices.append(sample["sourceIndex"])
    require(source_indices == sorted(set(source_indices)) and not set(source_indices).intersection(excluded)
            and sorted(source_indices + excluded) == list(range(len(samples) + len(excluded))),
            "NATIVE_RUBRIC_SOURCE_COVERAGE")
    # The legacy detector expands ㅙ/ㅞ into two labels; GOP keeps one reference
    # sample per phone. Neither count is the number of observed spoken phones.
    # V2 scores the recorded GOP reference/alignment without rewriting either
    # representation or rejecting speech because the reference counts differ.
    if policy["revision"] == LEGACY_REVISION:
        require(type(core.get("expected_phone_count")) is int and core["expected_phone_count"] == len(samples),
                "NATIVE_RUBRIC_EXPECTED_COUNT")
    positions, expected_index, observed_index, deleted = [], 0, 0, 0
    for row_index, row in enumerate(alignment):
        require(type(row) is dict and set(row) == {"expectedIndex", "observedIndex", "operation"},
                "NATIVE_RUBRIC_ALIGNMENT_ROW")
        operation = row["operation"]
        require(type(operation) is str and operation in {*OPERATIONS, "INSERTION"}, "NATIVE_RUBRIC_OPERATION")
        if operation == "INSERTION":
            require(row["expectedIndex"] is None and type(row["observedIndex"]) is int
                    and row["observedIndex"] == observed_index < len(observed), "NATIVE_RUBRIC_INSERTION")
            observed_index += 1
            continue  # Preserve insertion in CORE, without a new rubric penalty.
        require(type(row["expectedIndex"]) is int and row["expectedIndex"] == expected_index < len(samples),
                "NATIVE_RUBRIC_EXPECTED_ALIGNMENT")
        sample = samples[expected_index]
        if operation == "DELETION":
            require(row["observedIndex"] is None, "NATIVE_RUBRIC_DELETION")
            deleted += 1
        else:
            require(type(row["observedIndex"]) is int and row["observedIndex"] == observed_index < len(observed),
                    "NATIVE_RUBRIC_OBSERVED_ALIGNMENT")
            equal = sample["tokenId"] == observed[observed_index]["token_id"]
            require(equal == (operation == "MATCH"), "NATIVE_RUBRIC_TOKEN_ALIGNMENT")
            observed_index += 1
        state = OPERATIONS[operation] + ("Flagged" if sample["gop"] <= policy["gopFlagThreshold"] else "Clear")
        positions.append(dict(evidenceId="native-rubric:" + str(expected_index),
            sourcePointer="/phonemeAssessment/samples/" + str(expected_index),
            alignmentPointer="/phonemeAssessment/alignment/" + str(row_index),
            index=expected_index, sourceIndex=sample["sourceIndex"], phone=sample["phone"], role=sample["role"],
            wordIndex=sample["wordIndex"], syllableIndex=sample["syllableIndex"],
            token=sample["token"], tokenId=sample["tokenId"],
            indistinguishablePhones=list(sample["indistinguishablePhones"]),
            alignmentOperation=operation, gop=sample["gop"], state=state))
        expected_index += 1
    require(expected_index == len(samples) and observed_index == len(observed), "NATIVE_RUBRIC_ALIGNMENT_INCOMPLETE")
    coverage = source.get("coverage")
    require(coverage == dict(sampleCount=len(samples), deletedCount=deleted,
                            evidenceUnits=4 * (len(samples) - deleted), possibleUnits=4 * len(samples)),
            "NATIVE_RUBRIC_COVERAGE_MISMATCH")
    return positions, list(excluded)


def rubric_context(core: dict, rubric: dict, policy: dict, binding: dict) -> dict:
    """Derive a separately bound worksheet; never mutate the input CORE."""
    validate_native_policy(policy)
    phone_rubric.validate_policy(rubric)
    evidence = dict(policyRevision=policy["revision"], policySha256=sha(encode(policy)),
        policy=dict(policy), association=dict(binding), sourceRevision=policy["sourceRevision"],
        status="UNSCORABLE", reasonCode=None, positions=[], excludedSilentOnsetSourceIndices=[])
    try:
        require(core.get("input_validation", {}).get("status") == "ACCEPT", "NATIVE_RUBRIC_INPUT_NOT_ACCEPTED")
        positions, excluded = _positions(core, policy)
        rows = [dict(criterionId=k, sampleCount=0, **dict.fromkeys(STATES, 0)) for k, _ in (*GROUPS, ("coverage", ""))]
        for position in positions:
            for row, (key, phones) in zip(rows, (*GROUPS, ("coverage", "")), strict=True):
                if key == "coverage" or position["phone"] in phones:
                    row["sampleCount"] += 1
                    row[position["state"]] += 1
        result = phone_rubric.worksheet(rows, evidence_units=rubric["evidenceUnits"], revision=rubric["rubricRevision"])
        evidence.update(status="READY", reasonCode="NATIVE_GOP_POLICY_DERIVED_STATES", positions=positions,
                        excludedSilentOnsetSourceIndices=excluded)
        if policy["revision"] == REVISION:
            source = core["phonemeAssessment"]
            operations = {key: sum(row["operation"] == key for row in source["alignment"])
                          for key in (*OPERATIONS, "INSERTION")}
            detector_count = core.get("expected_phone_count")
            evidence.update(
                referenceCounts=dict(legacyDetectorExpectedCount=detector_count,
                    gopReferenceSampleCount=len(source["samples"]),
                    observedTokenCount=len(source["observed"]),
                    sameReferenceCount=detector_count == len(source["samples"])),
                alignment=dict(ambiguous=source["alignmentAmbiguous"],
                    policy="USE_REPORTED_CANONICAL_ALIGNMENT", operationCounts=operations,
                    confirmedLocalDeletionClaimsAllowed=False),
                warnings=(["MODEL_TOKEN_ALIGNMENT_AMBIGUOUS"] if source["alignmentAmbiguous"] else []))
    except ValueError as exc:
        evidence["reasonCode"] = error_code(exc)
        result = dict(RUBRIC_JSON=rubric, INPUT_DATA=None, RUBRIC_RESULT={"status": "unscorable"},
                      SCORING_FAILURE=evidence["reasonCode"])
    return dict(result, RUBRIC_EVIDENCE=evidence)
