"""Export actual MFA locations for detector phones, without changing P1/GOP.

Indices use the existing detector_metadata namespace (including its documented
compound-vowel expansion). They are not full_metadata/CTC indices. Vowels are
located only when the frozen locator establishes a complete word phone match;
its consonant-only recovery never supplies missing vowel times.
"""
SCHEMA = "voice-coaching.canonical-mfa-phone-alignment.v1"
SCOPE = "CANONICAL_MFA_DETECTOR_PHONES_V1"


def export_alignment(phone_contract, legacy, receipt, locator):
    if phone_contract is None:
        return dict(schemaVersion=SCHEMA, indexNamespace="DETECTOR_METADATA_V2", rows=[])
    expected = [dict(x.model_dump(mode="json"), expected_phone=x.phone)
                for x in phone_contract.detector_metadata]
    if len(expected) > 2048:
        raise ValueError("MFA_ALIGNMENT_PHONE_LIMIT")
    loc, statuses, words = ({}, {}, {})
    if receipt is not None and legacy.get("word_intervals"):
        loc, statuses, words = locator(dict(expected=expected,
            word_intervals=legacy["word_intervals"], phone_intervals=legacy["phone_intervals"]))
    rows = []
    for x in expected:
        ix, wi = x["expected_index"], x["original_word_index"]
        interval, word = loc.get(ix), words.get(wi, {})
        status = statuses.get(ix, "UNRESOLVED")
        # A crossing/ambiguous g2p word owner does not establish a video context.
        if x["provenance_confidence"] != "high" or x["boundary_relation"] != "within_word":
            interval, status = None, "EXPECTED_CONTEXT_AMBIGUOUS"
        if x["role"] == "nucleus" and status != "EXACT_COUNT_WORD_INDEX":
            interval, status = None, "VOWEL_LOCATION_UNRESOLVED"
        rows.append(dict(expected_index=ix, source_expected_index=x["source_expected_index"],
            expected_phone=x["expected_phone"], source_phone=x["source_phone"],
            word_index=wi, word=x["original_word_text"], role=x["role"],
            localization_status=status, mfa_ipa=interval["label"] if interval else None,
            phone_start_s=interval["start_s"] if interval else None,
            phone_end_s=interval["end_s"] if interval else None,
            word_start_s=word.get("start_s"), word_end_s=word.get("end_s")))
    return dict(schemaVersion=SCHEMA, indexNamespace="DETECTOR_METADATA_V2", rows=rows)
