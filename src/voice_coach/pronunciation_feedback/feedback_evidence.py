"""Loss-accounted evidence projection for feedback; never diagnoses new errors."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
import re

from .adapters.syllables import decompose_syllables
from .ctc_alignment import edit_alignment
from .coaching_payload import CoachingPayloadError

SCHEMA = "voice_coach.feedback_evidence.v1"
PHONE_FIELDS = ("expected_index", "expected_phone", "observed_candidate",
                "alignment_operation", "start_ms", "end_ms", "error_score",
                "operating_threshold", "is_error", "score_semantics", "soft_features")
SOFT_FIELDS = ("blank_prob", "competitor_prob", "entropy", "expected_margin",
               "expected_prob", "hard_error", "has_predicted_span", "predicted_prob", "span_seconds")
INTERNAL_FIELDS = ("backbone_name", "backbone_tree_sha256", "detector_config_sha256", "detector_version")
PHONE_COLUMNS = PHONE_FIELDS[:-2]  # semantics is shared; features have a separate table
ANCHOR_COLUMNS = ("expected_index", "word", "syllable", "charStart", "charEnd",
                  "writtenRole", "writtenJamo", "mappingOperation")
_PHONE = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ|]+$")


def _tokens(value):
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(x, str) or not _PHONE.fullmatch(x) for x in value
    ):
        raise CoachingPayloadError("feedback phone sequence invalid")
    return list(value)


def _anchors(text, expected):
    slots = decompose_syllables(text)
    words = list(re.finditer(r"[가-힣]+", text))
    written, positions = [], []
    for slot in slots:
        word = next((m.group() for m in words if m.start() <= slot.char_start < m.end()), slot.syllable)
        for role in ("onset", "nucleus", "coda"):
            token = getattr(slot, role)
            if token:
                written.append(token)
                positions.append([word, slot.syllable, slot.char_start, slot.char_end, role, token])
    # Written roles are text alignment metadata, not proof of a phonetic role
    # after liaison/assimilation. Inserted expected tokens get no invented role.
    indices = [i for i,x in enumerate(expected) if x != "|"]
    steps, _ = edit_alignment(written, [expected[i] for i in indices])
    result = {}
    for step in steps:
        if step.hypothesis_index is not None:
            i = indices[step.hypothesis_index]
            values = positions[step.reference_index] if step.reference_index is not None else [None]*6
            result[i] = [i, *values, step.operation]
    return [result.get(i, [i, *([None]*6), "boundary"]) for i in range(len(expected))]


def build_feedback_evidence(*, analysis, target_text, selected_index,
                            visual_evidence=None, visual_state="NOT_PROVIDED",
                            overall_score=None, scoring_evidence=None):
    """Preserve every supported source row in memory before token selection."""
    if analysis.get("schema_version") != "korean_phone_ctc.pronunciation_analysis.v1":
        raise CoachingPayloadError("feedback evidence schema differs")
    expected = _tokens(analysis.get("expected_phones"))
    rows = analysis.get("phones")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(expected):
        raise CoachingPayloadError("feedback evidence phone count differs")
    phones, unknown = [], set()
    for i,row in enumerate(rows):
        if row.get("expected_index") != i or row.get("expected_phone") != expected[i]:
            raise CoachingPayloadError("feedback evidence index mismatch")
        if row.get("alignment_operation") not in {"correct", "substitution", "deletion"}:
            raise CoachingPayloadError("feedback alignment invalid")
        if row.get("observed_candidate") is not None:
            _tokens([row["observed_candidate"]])
        if type(row.get("is_error")) is not bool:
            raise CoachingPayloadError("feedback flag invalid")
        for name in ("error_score", "operating_threshold"):
            if type(row.get(name)) not in (int,float) or not math.isfinite(row[name]) or not 0 <= row[name] <= 1:
                raise CoachingPayloadError("feedback evidence score invalid")
        start,end = row.get("start_ms"), row.get("end_ms")
        if (start is None) != (end is None) or (start is not None and
                (type(start) is not int or type(end) is not int or not 0 <= start < end)):
            raise CoachingPayloadError("feedback timing invalid")
        soft = row.get("soft_features")
        if not isinstance(soft, dict):
            raise CoachingPayloadError("feedback features invalid")
        for name in SOFT_FIELDS:
            value = soft.get(name)
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise CoachingPayloadError("feedback feature value invalid")
        phone = {k: deepcopy(row.get(k)) for k in PHONE_FIELDS if k != "soft_features"}
        phone["soft_features"] = {k:soft.get(k) for k in SOFT_FIELDS}
        if phone["score_semantics"] != "detector_ranking_score_not_calibrated_correctness_confidence":
            raise CoachingPayloadError("feedback score semantics differ")
        phone["timingProvenance"] = (
            "unavailable" if start is None else "ctc_nonblank_span"
            if row["alignment_operation"] != "deletion" and soft.get("has_predicted_span") == 1
            else "unverified_not_acoustic_duration")
        phones.append(phone)
        unknown.update(set(row)-set(PHONE_FIELDS))
        unknown.update("soft_features."+k for k in set(soft)-set(SOFT_FIELDS))
    if type(selected_index) is not int or not 0 <= selected_index < len(phones):
        raise CoachingPayloadError("feedback selected index invalid")
    # Notices are upstream fixed analysis notices. Do not forward arbitrary IDs
    # or paths from metadata, visual evidence IDs, or scoring provenance hashes.
    notices = analysis.get("notices", [])
    if not isinstance(notices,(list,tuple)) or any(not isinstance(x,str) or len(x)>500 for x in notices):
        raise CoachingPayloadError("feedback notices invalid")
    cues=[]
    if visual_evidence is not None:
        for row in visual_evidence.get("cue_evidence",[]):
            backend=row.get("measurement_backend",{})
            cues.append({k:deepcopy(row.get(k)) for k in (
                "cue_id","cue_state","video_start_ms","video_end_ms",
                "measurement_value","measurement_unit","calibrated_deviation","reason_codes")}
                | {"backend":{k:backend.get(k) for k in ("backend_id","backend_revision","status","reason_codes")},
                   "sampleCount":None,"sampleTimesMs":None,
                   "samplingProvenance":"not_supplied_by_aggregate_contract"})
    score=None
    if scoring_evidence is not None:
        score={k:deepcopy(scoring_evidence.get(k)) for k in
               ("rubricRevision","criteria","phoneCriteria","attentionCriterionIds")}
        score["overallScore"]=overall_score
        score["source"]="validated_scoring_result_do_not_recalculate"
    return dict(schemaVersion=SCHEMA, selectedExpectedIndex=selected_index,
        audio=dict(expectedPhones=expected, ctcHypothesis=_tokens(analysis.get("ctc_hypothesis")),
                   insertions=_tokens(analysis.get("insertions")), phones=phones,
                   notices=list(notices), textAnchors=_anchors(target_text,expected)),
        visual=dict(status=visual_state,cues=cues,selectedExpectedIndex=selected_index if cues else None),
        scoring=score,
        accounting=dict(internalOnlyFields=list(INTERNAL_FIELDS), unknownPhoneFieldCount=len(unknown),
            unknownRootFieldCount=len(set(analysis)-set(INTERNAL_FIELDS)-
                {"schema_version","expected_phones","ctc_hypothesis","insertions","phones","notices"})))


def _ranges(indices):
    ranges=[]
    for i in sorted(indices):
        if ranges and i == ranges[-1][1]+1: ranges[-1][1]=i
        else: ranges.append([i,i])
    return ranges


def project_evidence(bundle, included, feature_indices, *, include_sequences=True, include_scores=True):
    """Compact lossless columns for included rows; explicit accounting elsewhere."""
    audio=bundle["audio"]; rows=audio["phones"]; selected=bundle["selectedExpectedIndex"]
    included=sorted(included); feature_indices=sorted(feature_indices)
    all_indices=set(range(len(rows)))
    score=bundle["scoring"]
    if score is not None:
        score={"overallScore":score["overallScore"],"rubricRevision":score["rubricRevision"],
               "source":score["source"],"attentionCriterionIds":score["attentionCriterionIds"],
               "criterionColumns":["criterionId","sampleCount","level"],
               "criteria":[[r.get("criterionId"),r.get("sampleCount"),r.get("level")]
                           for r in (bundle["scoring"].get("criteria") or [])],
               "phoneCriteria":[[r.get("criterionId"),r.get("sampleCount"),r.get("level")]
                           for r in (bundle["scoring"].get("phoneCriteria") or [])] if include_scores else [],
               "phoneCriteriaOmitted":not include_scores,
               "nullLevelMeaning":"NOT_APPLICABLE_when_sampleCount_zero; never_zero_score"}
    return dict(schemaVersion=SCHEMA,selectedExpectedIndex=selected,
        audio=dict(totalPhoneCount=len(rows), phoneColumns=list(PHONE_COLUMNS),
            phones=[[rows[i][k] for k in PHONE_COLUMNS] for i in included],
            anchorColumns=list(ANCHOR_COLUMNS),textAnchors=[audio["textAnchors"][i] for i in included],
            timingProvenance={kind:_ranges(i for i in included if rows[i]["timingProvenance"]==kind)
                for kind in sorted({rows[i]["timingProvenance"] for i in included})},
            softFeatureColumns=["expected_index",*SOFT_FIELDS],
            softFeatures=[[i,*[rows[i]["soft_features"][k] for k in SOFT_FIELDS]] for i in feature_indices],
            scoreSemantics=rows[selected]["score_semantics"],
            alignmentCounts=dict(Counter(r["alignment_operation"] for r in rows)),
            expectedPhones=audio["expectedPhones"] if include_sequences else None,
            ctcHypothesis=audio["ctcHypothesis"] if include_sequences else None,
            insertions=audio["insertions"] if include_sequences else None,
            sequenceCounts={k:len(audio[k]) for k in ("expectedPhones","ctcHypothesis","insertions")},
            notices=audio["notices"]),visual=deepcopy(bundle["visual"]),scoring=score,
        coverage=dict(includedIndices=included,omittedIndexRanges=_ranges(all_indices-set(included)),
            softFeatureIndices=feature_indices,omittedSoftFeatureIndexRanges=_ranges(all_indices-set(feature_indices)),
            fullSequencesIncluded=include_sequences,
            selectionPolicy="selected_then_flagged_score_then_neighbors_then_remaining",
            sourceAccounting=bundle["accounting"]))


def fit_evidence(bundle, make_messages, count_tokens, maximum_tokens=3900):
    """Use the model tokenizer on the final chat messages, never character guesses."""
    rows=bundle["audio"]["phones"]; selected=bundle["selectedExpectedIndex"]
    flagged=sorted((i for i,r in enumerate(rows) if r["is_error"]),key=lambda i:(-rows[i]["error_score"],i))
    priority=list(dict.fromkeys([selected,*flagged,selected-1,selected+1,*range(len(rows))]))
    priority=[i for i in priority if 0<=i<len(rows)]
    # Start with every core row. Reduce only while the exact serialized chat is
    # too large. Full values stay in bundle; omissions are part of the message.
    included=list(priority); features=[selected]; sequences=True; scores=True
    while True:
        view=project_evidence(bundle,included,features,include_sequences=sequences,include_scores=scores)
        messages=make_messages(view); tokens=count_tokens(messages)
        if tokens<=maximum_tokens: break
        # Feedback needs acoustic observations before the optional 49-leaf
        # score worksheet. Keep the validated total and category scores.
        if scores and bundle['scoring'] is not None: scores=False
        elif len(included)>1: included = included[:max(1, len(included)//2)]
        elif sequences: sequences=False
        elif features: features=[]
        else: raise CoachingPayloadError("feedback_evidence_mandatory_context_exceeds_token_budget")
    low, high = len(included), len(priority)
    while low < high:
        middle = (low + high + 1)//2
        candidate=project_evidence(bundle,priority[:middle],features,include_sequences=sequences,include_scores=scores)
        candidate_messages=make_messages(candidate); candidate_tokens=count_tokens(candidate_messages)
        if candidate_tokens<=maximum_tokens:
            low=middle;included=priority[:middle];view,messages,tokens=candidate,candidate_messages,candidate_tokens
        else: high=middle-1
    for i in priority:
        if i not in included or i in features: continue
        candidate=project_evidence(bundle,included,[*features,i],include_sequences=sequences,include_scores=scores)
        candidate_messages=make_messages(candidate); candidate_tokens=count_tokens(candidate_messages)
        if candidate_tokens<=maximum_tokens:
            view,messages,tokens=candidate,candidate_messages,candidate_tokens;features.append(i)
    return messages, dict(totalPhones=len(rows),includedPhones=len(included),
        includedSoftFeatures=len(features),inputTokens=tokens,
        omittedPhoneRanges=view['coverage']['omittedIndexRanges'],
        omittedSoftFeatureRanges=view['coverage']['omittedSoftFeatureIndexRanges'],
        fullSequencesIncluded=sequences,phoneScoreDetailsIncluded=scores,
        unknownFieldCount=bundle['accounting']['unknownPhoneFieldCount']+bundle['accounting']['unknownRootFieldCount'])
