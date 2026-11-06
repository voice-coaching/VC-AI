"""Developer-operated reference capture/review compiler. Never auto-approve.

capture runs the real CPU video extractor, without GPT or a new audio forward.
It requires a real v2 core result and PCM/preparation from that same source.
compile reads only measured data and an explicitly completed expert review.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
from pathlib import Path
import os
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from voice_coach.visual_feedback.feature_protocol import strict_json
from voice_coach.visual_feedback.reference_preparation import (
    capture_reference, compile_reviewed_catalog, encode, REVIEW_SCHEMA)
from voice_coach.visual_feedback.reference_catalog import require


def read(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum)
        raw = stream.read(maximum + 1)
        require(len(raw) == info.st_size)
        return raw


def write_new(path, raw):
    # Reference inputs remain private, never silently overwrite an expert review.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


async def capture(args):
    from jsonschema import Draft202012Validator
    from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalArtifacts
    from voice_coach.backend_analysis.canonical_audiovisual_result import visual_assets
    from voice_coach.backend_analysis.canonical_bridge import FrozenCanonicalCoaching
    from voice_coach.visual_feedback.adapters.canonical_video_features import CanonicalVideoFeatures, VideoFeatureConfiguration
    from voice_coach.visual_feedback.canonical_alignment import pcm_identity
    artifacts = FrozenCanonicalArtifacts(args.core_root, args.llm_root)
    require(artifacts.identity.audiovisual)
    artifacts.verify()
    catalog, config = visual_assets(FrozenCanonicalCoaching(artifacts))
    core_raw = read(args.core_result, 16 * 1024**2)
    core = strict_json(core_raw)
    Draft202012Validator(strict_json(read(args.llm_root / "contracts/llm/canonical_parent.schema.json", 2 * 1024**2))).validate(core)
    require(core["input_validation"]["status"] == "ACCEPT" and core["mfa_receipt"] is not None)
    receipt = strict_json(read(args.media_receipt, 256 * 1024))
    schema_path = Path(__file__).resolve().parents[2] / "docs/contracts/runpod_media_preparation_v1.schema.json"
    Draft202012Validator(strict_json(read(schema_path, 256 * 1024))).validate(receipt)
    require(receipt["sourceSha256"] == args.source_sha256 and receipt["canonicalVideoSha256"] is not None)
    extractor = CanonicalVideoFeatures(VideoFeatureConfiguration(python=args.visual_python, model=args.model,
        model_sha256=config["modelSha256"], quality=config["quality"]))
    async with asyncio.timeout(config["visualTimeoutSeconds"]):
        features = await extractor.extract(video_path=args.video, video_sha256=receipt["canonicalVideoSha256"],
                                            byte_count=args.video.stat().st_size)
    result = capture_reference(core=core, core_raw=core_raw, receipt=receipt, features=features,
        source_pcm=pcm_identity(args.source_pcm, receipt["canonicalPcmSha256"]),
        prepared_pcm=pcm_identity(args.prepared_pcm, core["prepared_wav_sha256"]), catalog=catalog,
        alignment_evidence=(strict_json(read(args.alignment_sidecar, 1024**2))
                            if args.alignment_sidecar is not None else None))
    raw = encode(result)
    require(len(raw) <= 8 * 1024**2)
    write_new(args.output, raw)
    review = dict(schemaVersion=REVIEW_SCHEMA, captureSha256=hashlib.sha256(raw).hexdigest(),
                  expertReviewId="PENDING", approvedExpectedIndices=[])
    write_new(args.review_output, encode(review))
    return dict(captureSha256=review["captureSha256"], candidatePhoneCount=len(result["phones"]), expertReviewPending=True)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    c = commands.add_parser("capture")
    for name in ("core-root", "llm-root", "core-result", "media-receipt", "source-pcm", "prepared-pcm",
                 "video", "visual-python", "model", "output", "review-output"):
        c.add_argument("--" + name, type=Path, required=True)
    c.add_argument("--source-sha256", required=True)
    c.add_argument("--alignment-sidecar", type=Path)
    b = commands.add_parser("compile")
    for name in ("capture", "review", "base-catalog", "output"):
        b.add_argument("--" + name, type=Path, required=True)
    b.add_argument("--revision", required=True)
    b.add_argument("--model-sha256", required=True)
    b.add_argument("--lip-score-policy", type=Path,
                   help="Explicit opt-in to provisional components; requires measured reviewed references")
    b.add_argument("--minimum-coverage", type=float, default=0.6)
    args = parser.parse_args()
    try:
        if args.operation == "capture":
            result = asyncio.run(capture(args))
        else:
            raw = compile_reviewed_catalog(capture_raw=read(args.capture, 8 * 1024**2),
                review_raw=read(args.review, 256 * 1024), base_raw=read(args.base_catalog, 8 * 1024**2),
                revision=args.revision, model_sha256=args.model_sha256)
            if args.lip_score_policy is not None:
                from voice_coach.visual_feedback.reference_preparation import enable_provisional_scoring
                raw = enable_provisional_scoring(raw, read(args.lip_score_policy, 16384),
                                                minimum_coverage=args.minimum_coverage)
            write_new(args.output, raw)
            result = dict(catalogSha256=hashlib.sha256(raw).hexdigest(), calibrationFitted=False, activationPerformed=False)
        print(encode(result).decode("utf-8"))
    except Exception as error:
        # Validator diagnostics can embed complete private coordinate data.
        print(encode(dict(status="FAILED", errorType=type(error).__name__)).decode("utf-8"), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
