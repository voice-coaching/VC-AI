"""Native-only scoring adapter. Never reads legacy pronunciation/error_score."""
from __future__ import annotations

from .gpt_rubric import render_worksheet
from .native_phone_contract import RUBRIC_REVISION
from .native_phone_evidence import evidence_rows
from .phone_rubric import EVIDENCE_UNITS, require, validate_response, worksheet


def native_worksheet(core, *, binding, policy):
    require(core.get("request_id") == binding["requestId"]
            and core.get("source_audio_sha256") == binding["sourceAudioSha256"]
            and core.get("prepared_wav_sha256") == binding["preparedWavSha256"], "NATIVE_CORE_BINDING")
    rows = evidence_rows(core.get("nativeScoringEvidence"), binding=binding, policy=policy)
    return worksheet(rows, evidence_units=EVIDENCE_UNITS, revision=RUBRIC_REVISION)


def score_core(core, *, binding, policy, prompt, transport):
    if core["input_validation"]["status"] != "ACCEPT":
        return dict(status="NOT_REQUESTED")
    try:
        sheet = native_worksheet(core, binding=binding, policy=policy)
        return render_worksheet(sheet, prompt=prompt, transport=transport)
    except Exception:
        # Missing/malformed/uncalibrated evidence never invokes GPT or old models.
        return dict(status="UNSCORABLE")


def verify_score_record(core, record, *, binding, policy):
    require(type(record) is dict, "SCORING_RECORD_MISSING")
    if core["input_validation"]["status"] != "ACCEPT":
        require(record == {"status": "NOT_REQUESTED"}, "SCORING_NONACCEPT_RESULT")
        return
    if record == {"status": "UNSCORABLE"}:
        return
    require(set(record) == {"status", "result"} and record["status"] == "READY", "SCORING_RECORD_INVALID")
    validate_response(record["result"], native_worksheet(core, binding=binding, policy=policy)["RUBRIC_RESULT"])
