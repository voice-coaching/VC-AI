"""Package existing paired pipeline outputs; does not run inference or QA."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA = "voice-coaching.expert-examples.v1"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source_root, media_root, feedback_path, output):
    feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    report_path = source_root / "visual-debug/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    by_name = {clip["name"]: clip for clip in report["clips"]}
    cases = []
    for expert in feedback["cases"]:
        name = expert["caseId"]
        if name not in {"testvideo1", "testvideo2"}:
            raise ValueError("unexpected_case_id")
        media = media_root / (name + ".mp4")
        clip = by_name[name]
        if digest(media) != expert["videoSha256"] or clip["inputSha256"] != expert["videoSha256"]:
            raise ValueError("case_video_digest_mismatch")
        analysis_path = source_root / "visual-debug" / (name + "-analysis.json")
        evidence_path = source_root / "visual-debug" / (name + "-evidence.json")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        analysis_sha = hashlib.sha256(encoded(analysis)).hexdigest()
        bindings = [t["binding_receipt"] for t in clip["visualTrace"] if "binding_receipt" in t]
        if not bindings or any(b["video_source_sha256"] != expert["videoSha256"]
                               or b["analysis_sha256"] != analysis_sha
                               or b["audio_source_sha256"] != clip["pcmSha256"] for b in bindings):
            raise ValueError("case_analysis_binding_mismatch")
        mappings = []
        for annotation in expert["annotations"]:
            matched = [f for f in evidence["facts"] if f["location"]["word"] in annotation["words"]]
            indices = [f["expectedIndex"] for f in matched]
            mappings.append(dict(**annotation, expectedIndices=indices,
                visualObservationIndices=[i for i, v in enumerate(clip["visualObservations"])
                                          if v["expectedIndex"] in indices],
                alignmentStatus="WORD_CONTEXT_ONLY" if matched else "UTTERANCE_OR_UNTIMED_EXPERT_JUDGMENT",
                semantics="LOCATION_LINK_NOT_DETECTOR_CONFIRMATION"))
        cases.append(dict(caseId=name, bindingStatus="VIDEO_AND_ANALYSIS_DIGEST_VERIFIED",
            media=dict(sha256=expert["videoSha256"], bytes=media.stat().st_size,
                       pcmSha256=clip["pcmSha256"]),
            provenance=dict(sourcePodId="s8hb5b0l5k4qlx", analysisObservedAt=report["startedAt"],
                pipelineRevision=clip["pipelineRevision"], analysisSha256=analysis_sha,
                sourceFiles=[dict(name=str(p.relative_to(source_root)), sha256=digest(p))
                             for p in (report_path, analysis_path, evidence_path)],
                expertSource=feedback["feedbackSource"], feedbackProvidedOn=feedback["providedOn"]),
            utterance=evidence["utterance"], seungunOutput=analysis,
            locationEvidence=evidence["facts"],
            visualPipelineOutput=dict(trace=clip["visualTrace"], observations=clip["visualObservations"],
                referenceStatus="NOT_VALIDATED", correctiveClaimsAllowed=False),
            expertFeedback=expert["expertFeedback"], feedbackMappings=mappings,
            limitations=["Historical pipeline output; no new inference performed.",
                "Expert feedback is an opinion on this recording, not calibrated detector ground truth.",
                "Analyzed script includes 깨끗한; the expert's shorter practice phrase does not.",
                "Untimed phrasing/rate/intonation guidance has no fabricated word or pitch measurement.",
                "Visual windows cover only recorded candidate indices, not every word or vowel."]))
    document = dict(schemaVersion=SCHEMA, revision=feedback["revision"],
                    retrievalPolicy="ALL_CASES_EVERY_GENERATION", cases=cases)
    raw = encoded(document)
    if len(raw) > 480_000:
        raise ValueError("expert_examples_too_large_no_truncation")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    print(json.dumps(dict(cases=[c["caseId"] for c in cases], bytes=len(raw),
                          sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--feedback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_root, args.media_root, args.feedback, args.output)
