"""Audio producer + independent video port, followed by deterministic MFA join.

This application service does not dispatch GPT or publish a result. Its caller
must bind the returned private evidence to the one frozen coaching invocation
and the versioned handoff. It never changes a core's original observations.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json

from .audiovisual_evidence import LipFrame, assess_phone, aggregate
from .canonical_alignment import anchors_from_core, pcm_identity, verified_time_map
from .feature_protocol import MAPPING, validate_features
from .parallel_analysis import analyze_parallel, MediaIntegrityFailure
from .reference_catalog import ReferenceCatalog

EVIDENCE_SCHEMA = "voice-coaching.visual-evidence.v1"


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assess_aligned(*, core: dict, request, feature_value: dict, source_pcm, prepared_pcm,
                   catalog: ReferenceCatalog):
    """Pure re-computation used both by execution and an offline verifier.

PCM measurements come from the materialized files during execution; the
verifier must additionally match them to the retained receipt/association.
"""
    video_sha = request.visual_input.sha256
    mapping = verified_time_map(source_pcm=source_pcm, prepared_pcm=prepared_pcm,
        prepared_sha256=core["prepared_wav_sha256"], video_sha256=video_sha, features=feature_value)
    located = anchors_from_core(core, request_id=str(request.event_id),
        audio_sha256=request.audio_sha256, prepared_sha256=core["prepared_wav_sha256"])
    raw_frames = feature_value["features"]["frames"]
    validate_features(feature_value["features"], [r["pts"] for r in raw_frames])
    frames = tuple(LipFrame(r["pts"], tuple(tuple(p) for p in r["landmarks"]),
                           tuple(r["features"]), r["reason"] is None) for r in raw_frames)
    rows = []
    consumed_pts = set()
    scope = _sha([str(request.execution_id), request.audio_sha256, video_sha, catalog.sha256])
    for anchor in located.anchors:
        row = assess_phone(anchor, frames=frames, pcm_sha256=core["prepared_wav_sha256"],
            video_sha256=video_sha, time_map=mapping, mapping_revision=MAPPING.revision,
            references=catalog.references, policy=catalog.policy, calibration=catalog.calibration_for(anchor))
        if row["videoStartSeconds"] is not None:
            selected = [f for f in frames if row["videoStartSeconds"] <= f.pts_s < row["videoEndSeconds"]]
            consumed_pts.update(f.pts_s for f in selected)
            reasons = sorted({r["reason"] for r in raw_frames if r["pts"] in {f.pts_s for f in selected}
                              and r["reason"] is not None})
            if reasons:
                row["reasonCodes"] = sorted(set(row["reasonCodes"]) | set(reasons))
            elif row["frameCount"] >= 3:
                # Measurements are observations, not a normal/abnormal label or
                # an instruction to change articulation. No closure threshold
                # or 3-D tongue/protrusion inference is introduced here.
                row["observations"] = [dict(observationId=f"{scope}:phone-{anchor.expected_index}:{name}",
                    code=name, minimum=min(f.features[i] for f in selected),
                    maximum=max(f.features[i] for f in selected), unit="FACE_SCALE_NORMALIZED")
                    for i, name in enumerate(("LIP_APERTURE_RANGE", "LIP_WIDTH_RANGE", "LIP_AREA_RANGE"))]
        rows.append(row)
    summary = aggregate(rows, target_count=located.target_count, visually_eligible_count=located.target_count,
                        minimum_coverage=catalog.minimum_coverage)
    reasons = sorted({r for row in rows for r in row["reasonCodes"]})
    if located.unresolved_indices:
        reasons = sorted(set(reasons) | {"ALIGNMENT_AMBIGUOUS"})
    if mapping is None:
        reasons = sorted(set(reasons) | {"SYNC_UNCERTAIN"})
    if not located.anchors:
        reasons = sorted(set(reasons) | {"ALIGNMENT_UNAVAILABLE"})
    status = "UNSCORABLE"
    if any(r["status"] == "OBSERVED" for r in rows):
        status = "OBSERVED"
    if any(r["status"] == "REFERENCE_ONLY" for r in rows):
        status = "REFERENCE_ONLY"
    if summary["score"] is not None:
        status = "SCORED"
    visual = dict(status=status, correctiveClaimsAllowed=False,
        score=summary.pop("score"), scoreKind="CALIBRATED" if status == "SCORED" else "NONE",
        coverage=summary, phoneAssessments=rows, reasonCodes=reasons,
        alignmentScope=located.scope, unresolvedExpectedIndices=list(located.unresolved_indices))
    # Retain only rows that contributed to phone assessment; not all decoded
    # frame geometry. There is no RGB, full face mesh, pathname or object key.
    evidence = dict(schemaVersion=EVIDENCE_SCHEMA, requestId=str(request.event_id),
        executionId=str(request.execution_id), audioSha256=request.audio_sha256, videoSha256=video_sha,
        preparedWavSha256=core["prepared_wav_sha256"], referenceCatalogSha256=catalog.sha256,
        mappingRevision=MAPPING.revision, distancePolicyRevision=catalog.policy.revision,
        sourcePcm=source_pcm, preparedPcm=prepared_pcm,
        timeMap=None if mapping is None else dict(scale=mapping.scale, offsetSeconds=mapping.offset_s,
                                                provenance=mapping.provenance),
        decoder={k: v for k, v in feature_value.items() if k != "features"},
        frames=[r for r in raw_frames if r["pts"] in consumed_pts], visual=visual)
    return evidence


@dataclass(frozen=True)
class AudiovisualProduction:
    audio: object
    visual_evidence: dict
    timings: dict


class CanonicalAudiovisualService:
    def __init__(self, *, video_port, catalog: ReferenceCatalog):
        self.video_port, self.catalog = video_port, catalog

    async def produce(self, *, request, audio, video, media_preparation, producer,
                      deadline: float, visual_timeout: float, execution_is_active):
        from ..backend_analysis.canonical_codec import read_document
        from ..backend_analysis.canonical_async import drain_on_cancel
        if request.visual_input is None or video is None:
            raise MediaIntegrityFailure("AUDIOVISUAL_MEDIA_REQUIRED")
        media_preparation.require_materialized(audio, video)
        # Keep the exact admitted request for the durable journal. Only the
        # producer's named audio branch passes PCM into the frozen core.
        parallel = await analyze_parallel(
            audio=lambda: producer.produce(request, audio, deadline_monotonic=deadline,
                                            execution_is_active=execution_is_active, audio_branch=True),
            video=lambda: self.video_port.extract(video_path=video.path, video_sha256=video.sha256,
                                                    byte_count=video.byte_count),
            deadline=deadline, visual_timeout=visual_timeout)
        produced = parallel.audio
        core = read_document(produced.raw, 16 * 1024**2)
        if parallel.visual.status == "OBSERVED" and produced.prepared_wav is not None:
            # Drain threaded reads before allowing the executor to release its
            # private WAV files even if the outer lease is cancelled.
            measurements = asyncio.gather(
                asyncio.to_thread(pcm_identity, audio.path, audio.sha256),
                asyncio.to_thread(pcm_identity, produced.prepared_wav, core["prepared_wav_sha256"]),
                return_exceptions=True)
            source, prepared = await drain_on_cancel(measurements)
            for item in (source, prepared):
                if isinstance(item, BaseException):
                    raise item
            evidence = assess_aligned(core=core, request=request, feature_value=parallel.visual.value,
                source_pcm=source, prepared_pcm=prepared, catalog=self.catalog)
        else:
            reason = parallel.visual.reason or "ALIGNMENT_UNAVAILABLE"
            evidence = dict(schemaVersion=EVIDENCE_SCHEMA, requestId=str(request.event_id),
                executionId=str(request.execution_id), audioSha256=request.audio_sha256,
                videoSha256=video.sha256, preparedWavSha256=core.get("prepared_wav_sha256"),
                referenceCatalogSha256=self.catalog.sha256, mappingRevision=MAPPING.revision,
                distancePolicyRevision=self.catalog.policy.revision, sourcePcm=None, preparedPcm=None,
                timeMap=None, decoder=None, frames=[], visual=dict(
                    status=parallel.visual.status if parallel.visual.status in {"FAILED", "TIMEOUT"} else "UNSCORABLE",
                    correctiveClaimsAllowed=False, score=None, scoreKind="NONE", coverage=dict(
                        targetCount=0, visuallyEligibleCount=0, observedCount=0, scoredCount=0, coverage=None),
                    phoneAssessments=[], reasonCodes=[reason],
                    alignmentScope="CANONICAL_PRONUNCIATION_EVIDENCE_CONSONANTS", unresolvedExpectedIndices=[]))
        return AudiovisualProduction(produced, evidence, dict(
            audioSeconds=parallel.audio_finished - parallel.started,
            videoSeconds=parallel.visual_finished - parallel.started,
            overlapSeconds=min(parallel.audio_finished, parallel.visual_finished) - parallel.started))
