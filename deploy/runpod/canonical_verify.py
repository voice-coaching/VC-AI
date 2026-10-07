"""Bounded, offline Backend semantic verifier. Not a test or an inference runner.

Launch with pinned Python 3.12 -I. Stdin contains length-prefixed private bytes;
stdout is a small closed result with digests only. No network, core execution,
provider dispatch, temporary raw files, credentials or arbitrary input paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID

PROTOCOL = 'voice-coaching.canonical-offline-verification.v1'


def restrict_process():
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('VERIFIER_RUNTIME_INVALID')
    import resource
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    os.environ['OMP_NUM_THREADS'] = '1'
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # Kernel isolation is configured by deployment too; this prevents accidental
    # dispatch/spawning by imported Python code, not hostile native-code escape.
    def audit(event, args):
        if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                     'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty'}:
            raise RuntimeError('VERIFIER_SIDE_EFFECT_FORBIDDEN')
    sys.addaudithook(audit)


def frame(limit: int) -> bytes:
    def exact(size):
        value = bytearray()
        while len(value) < size:
            chunk = sys.stdin.buffer.read(size - len(value))
            if not chunk:
                raise ValueError('VERIFIER_TRUNCATED_FRAME')
            value.extend(chunk)
        return bytes(value)
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= limit:
        raise ValueError('VERIFIER_FRAME_SIZE')
    return exact(size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-root', required=True, type=Path)
    parser.add_argument('--llm-root', required=True, type=Path)
    parser.add_argument('--recordings-prefix', required=True)
    parser.add_argument('--check-installation', action='store_true')
    args = parser.parse_args()
    restrict_process()
    root = Path(__file__).resolve().parents[2]
    # Do not execute the production package __init__ (which imports legacy
    # orchestration). This standalone process imports only the packaged verifier
    # dependency closure, under private process-local namespaces.
    for name, directory in (
        ('voice_coach', root / 'src/voice_coach'),
        ('voice_coach.backend_analysis', root / 'src/voice_coach/backend_analysis'),
    ):
        package = ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    from importlib.metadata import version
    if version('jsonschema') != '4.25.1' or version('rfc8785') != '0.1.4':
        raise RuntimeError('VERIFIER_RUNTIME_INVALID')
    from voice_coach.backend_analysis.canonical_bridge import (
        CanonicalExpressionResult, FrozenCanonicalArtifacts, FrozenCanonicalCoaching,
    )
    from voice_coach.backend_analysis.canonical_result import (
        ArtifactReference, PreparedAudioIdentity, TrustedBackendVerifiedReceipt,
        preflight_canonical_result, validate_canonical_result_document,
    )
    from voice_coach.backend_analysis.canonical_codec import (
        read_document as _read_json, validate_document as _validate,
    )
    from voice_coach.backend_analysis.http_models import AnalysisInput, parse_json
    from voice_coach.backend_analysis.canonical_harness import (
        H5, S7, SCORED, H5_DIRECTORY, SCORED_CORE_DIRECTORY, SCORED_LLM_DIRECTORY, harness_identity,
        SCORED_V1, SCORED_V1_CORE_DIRECTORY, SCORED_V1_LLM_DIRECTORY, SCORED_V2, SCORED_V2_LLM_DIRECTORY,
        RESIDENT, RESIDENT_CORE_DIRECTORY, RESIDENT_LLM_DIRECTORY,
        NATIVE, NATIVE_CORE_DIRECTORY, NATIVE_LLM_DIRECTORY,
        NATIVE_V1, NATIVE_V1_CORE_DIRECTORY, NATIVE_V1_LLM_DIRECTORY,
        SEUNGUN_GPT, SEUNGUN_CORE_DIRECTORY, SEUNGUN_LLM_DIRECTORY,
        DLPC_GOP, DLPC_GOP_CORE_DIRECTORY, DLPC_GOP_LLM_DIRECTORY,
        DLPC_ANNOUNCER, DLPC_ANNOUNCER_LLM_DIRECTORY,
        SEUNGUN_GPT_V1, SEUNGUN_V1_CORE_DIRECTORY, SEUNGUN_V1_LLM_DIRECTORY,
    )
    frozen = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(args.core_root, args.llm_root))
    h5 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(args.core_root, args.llm_root.parent / H5_DIRECTORY))
    scored = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / SCORED_CORE_DIRECTORY, args.llm_root.parent / SCORED_LLM_DIRECTORY))
    scored_v1 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / SCORED_V1_CORE_DIRECTORY, args.llm_root.parent / SCORED_V1_LLM_DIRECTORY))
    scored_v2 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / SCORED_CORE_DIRECTORY, args.llm_root.parent / SCORED_V2_LLM_DIRECTORY))
    # Installation failure is not invalid user evidence. Validate it before reading a job.
    resident = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / RESIDENT_CORE_DIRECTORY, args.llm_root.parent / RESIDENT_LLM_DIRECTORY))
    native = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / NATIVE_CORE_DIRECTORY, args.llm_root.parent / NATIVE_LLM_DIRECTORY))
    native_v1 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / NATIVE_V1_CORE_DIRECTORY, args.llm_root.parent / NATIVE_V1_LLM_DIRECTORY))
    dlpc_gop = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / DLPC_GOP_CORE_DIRECTORY, args.llm_root.parent / DLPC_GOP_LLM_DIRECTORY))
    installed_native = None
    dlpc_announcer = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / DLPC_GOP_CORE_DIRECTORY, args.llm_root.parent / DLPC_ANNOUNCER_LLM_DIRECTORY))
    seungun = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / SEUNGUN_CORE_DIRECTORY, args.llm_root.parent / SEUNGUN_LLM_DIRECTORY))
    seungun_v1 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(
        args.core_root.parent / SEUNGUN_V1_CORE_DIRECTORY, args.llm_root.parent / SEUNGUN_V1_LLM_DIRECTORY))
    try:
        frozen._bridge()
        if frozen.artifacts.identity != S7 or h5.artifacts.identity != H5:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        h5._bridge()
        if scored.artifacts.identity != SCORED:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        scored._bridge().scoring_policy()
        if scored_v1.artifacts.identity != SCORED_V1:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        scored_v1._bridge().scoring_policy()
        if scored_v2.artifacts.identity != SCORED_V2:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        scored_v2._bridge().scoring_policy()
        if resident.artifacts.identity != RESIDENT:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        resident._bridge().scoring_policy()
        for identity, implementation in ((NATIVE_V1, native_v1), (NATIVE, native)):
            if implementation.artifacts.core_root.exists() != implementation.artifacts.llm_root.exists():
                raise RuntimeError('VERIFIER_NATIVE_PAIR_INCOMPLETE')
            if implementation.artifacts.llm_root.exists():
                if implementation.artifacts.identity != identity:
                    raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
                implementation._bridge()  # Source validation is not execution approval.
                if version('numpy') != '2.4.6':
                    raise RuntimeError('VERIFIER_NATIVE_NUMERIC_RUNTIME')
                installed_native = identity
        if seungun.artifacts.llm_root.exists():
            if seungun.artifacts.identity != SEUNGUN_GPT:
                raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
            seungun._bridge().scoring_policy()
            installed_native = SEUNGUN_GPT
        if dlpc_gop.artifacts.core_root.exists() != dlpc_gop.artifacts.llm_root.exists():
            raise RuntimeError("VERIFIER_DLPC_PAIR_INCOMPLETE")
        if dlpc_gop.artifacts.llm_root.exists():
            if dlpc_gop.artifacts.identity != DLPC_GOP:
                raise RuntimeError("VERIFIER_HARNESS_ROOT_MISMATCH")
            dlpc_gop._bridge().scoring_policy()
            installed_native = DLPC_GOP
        # Prompt-only derivative shares the existing DLPC core. Its absence is
        # compatible with installations that have not activated restoration yet.
        if dlpc_announcer.artifacts.llm_root.exists():
            if dlpc_announcer.artifacts.identity != DLPC_ANNOUNCER:
                raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
            dlpc_announcer._bridge().scoring_policy()
            installed_native = DLPC_ANNOUNCER
    except Exception:
        raise RuntimeError('VERIFIER_DEPENDENCIES_UNAVAILABLE') from None
    if args.check_installation:
        from voice_coach.backend_analysis.canonical_bridge import (
            CORE_MANIFEST_SHA256, LLM_MANIFEST_SHA256, LLM_LOCK_SHA256,
        )
        print(json.dumps(dict(protocol=PROTOCOL, status='READY', handoffContractVersion='voice-coaching.canonical-handoff.v1', coreManifestSha256=CORE_MANIFEST_SHA256,
                              llmManifestSha256=LLM_MANIFEST_SHA256, llmLockSha256=LLM_LOCK_SHA256,
                              h5ManifestSha256=H5.manifest_sha256, h5LockSha256=H5.lock_sha256,
                              scoredCoreManifestSha256=SCORED.core_sha256,
                              scoredManifestSha256=SCORED.manifest_sha256, scoredLockSha256=SCORED.lock_sha256,
                              residentCoreManifestSha256=RESIDENT.core_sha256,
                              residentManifestSha256=RESIDENT.manifest_sha256, residentLockSha256=RESIDENT.lock_sha256,
                              **(dict(nativeCoreManifestSha256=installed_native.core_sha256,
                                      nativeManifestSha256=installed_native.manifest_sha256, nativeLockSha256=installed_native.lock_sha256)
                                 if installed_native is not None else {}))))
        return
    header = _read_json(frame(1024), 1024)
    if (type(header) is not dict or set(header) != {'protocol', 'operation', 'receiptId'}
            or header['protocol'] != PROTOCOL
            or header['operation'] not in {'VERIFY_EVIDENCE', 'VERIFY_CALLBACK', 'VERIFY_PRECORE', 'VERIFY_HANDOFF'}):
        raise ValueError('VERIFIER_HEADER_INVALID')
    operation = header['operation']
    request_bytes = frame(65536)
    request = AnalysisInput.from_payload(parse_json(request_bytes), args.recordings_prefix)
    if request.analysis_profile not in {'CANONICAL_FROZEN_20260928_V4', 'CANONICAL_HANDOFF_20261004_V5'}:
        raise ValueError('VERIFIER_PROFILE_INVALID')
    response = dict(protocol=PROTOCOL, status='VALID', requestSha256=hashlib.sha256(request_bytes).hexdigest(),
                    manifestSha256=None, callbackSha256=None)
    preflight = receipt = None
    if operation != 'VERIFY_PRECORE':
        raw_manifest = frame(65536)
        manifest = _read_json(raw_manifest, 65536)
        handoff = operation == 'VERIFY_HANDOFF'
        _validate(manifest, 'handoffMetadata' if handoff else 'evidenceManifest', schema_name='handoff' if handoff else 'control')
        originals = {}
        references = []
        for reference in manifest['artifacts']:
            reference = (SimpleNamespace(kind=reference["kind"],byte_size=reference["byteSize"],sha256=reference["sha256"])
                         if handoff else ArtifactReference.from_document(reference))
            raw = frame(16 * 1024 * 1024)
            if (reference.kind in originals or len(raw) != reference.byte_size
                    or hashlib.sha256(raw).hexdigest() != reference.sha256):
                raise ValueError('VERIFIER_ORIGINAL_MISMATCH')
            originals[reference.kind] = raw
            references.append(reference)
        if originals:
            private = CanonicalExpressionResult(originals['CORE'], originals['BINDING'], originals['ASSOCIATION'],
                                                originals['BRIDGE_RESULT'], originals.get('SELECTION_PROJECTION'))
            association = _read_json(private.association_bytes)
            _validate(association, 'association')
            selected = harness_identity(association['llmManifestSha256'])
            frozen = {S7: frozen, H5: h5, SCORED_V1: scored_v1, SCORED_V2: scored_v2, SCORED: scored, RESIDENT: resident, NATIVE_V1: native_v1, NATIVE: native, SEUNGUN_GPT: seungun, SEUNGUN_GPT_V1: seungun_v1, DLPC_GOP: dlpc_gop, DLPC_ANNOUNCER: dlpc_announcer}[selected]
            metadata = association['preparedAudio']
            prepared = PreparedAudioIdentity.from_document(metadata) if metadata is not None else None
            # This verifies authenticated producer measurements and their binding,
            # not an independent re-decode of the original/prepared audio on Backend.
            preflight = preflight_canonical_result(private, request=request, frozen=frozen, prepared_audio=prepared)
            if selected.native and not selected.gpt_grading:
                from voice_coach.backend_analysis.native_execution import admitted_binding
                core = _read_json(private.core_bytes, 16 * 1024 * 1024)
                frozen._bridge().verify_archive(core, native_binding=admitted_binding(request,
                    core_sha256=selected.core_sha256, prepared_sha256=core.get('prepared_wav_sha256')))
            if not handoff and preflight.association.to_document() != manifest['association']:
                raise ValueError('VERIFIER_ASSOCIATION_MISMATCH')
        manifest_sha = hashlib.sha256(raw_manifest).hexdigest()
        response['manifestSha256'] = manifest_sha
        if operation == 'VERIFY_CALLBACK':
            receipt_id = UUID(header['receiptId'])
            if str(receipt_id) != header['receiptId']:
                raise ValueError('VERIFIER_RECEIPT_INVALID')
            receipt = TrustedBackendVerifiedReceipt(receipt_id, manifest_sha, tuple(references),
                                                    preflight.association, raw_manifest)
        elif header['receiptId'] is not None:
            raise ValueError('VERIFIER_RECEIPT_UNEXPECTED')
    elif header['receiptId'] is not None:
        raise ValueError('VERIFIER_RECEIPT_UNEXPECTED')
    if operation != 'VERIFY_EVIDENCE':
        callback_bytes = frame(1024 * 1024)
        document = _read_json(callback_bytes, 1024 * 1024)
        validate_canonical_result_document(document, preflight=preflight, receipt=receipt)
        if operation == 'VERIFY_HANDOFF':
            from voice_coach.backend_analysis.http_models import digest
            if (request.result_schema_version != 'voice-coaching.runpod-analysis-result.v5'
                    or digest(parse_json(request_bytes)) != manifest['requestSha256']
                    or digest(parse_json(callback_bytes)) != manifest['projectionSha256']
                    or any(document[k] != manifest[k] for k in ('eventId','workerInstanceId','requestId','executionId','analysisId','recordingId','contentId'))):
                raise ValueError('VERIFIER_HANDOFF_MISMATCH')
        expected = dict(requestId=str(request.event_id), executionId=str(request.execution_id),
                        analysisId=request.analysis_id, recordingId=request.recording_id, contentId=request.content_id,
                        analysisProfile=request.analysis_profile, schemaVersion=request.result_schema_version)
        if any(document[key] != value for key, value in expected.items()):
            raise ValueError('VERIFIER_REQUEST_MISMATCH')
        if (document['sourceIdentity']['scriptSha256'] != request.script_sha256
                or document['sourceIdentity']['audioSha256'] != request.audio_sha256
                or (operation == 'VERIFY_PRECORE' and document['canonical'] is not None)):
            raise ValueError('VERIFIER_SOURCE_MISMATCH')
        response['callbackSha256'] = hashlib.sha256(callback_bytes).hexdigest()
    if sys.stdin.buffer.read(1):
        raise ValueError('VERIFIER_TRAILING_DATA')
    print(json.dumps(response, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        # No exception text, frozen original_error, script, artifacts or paths on stderr/stdout.
        unavailable = isinstance(error, (RuntimeError, ImportError, OSError, MemoryError))
        print(json.dumps(dict(protocol=PROTOCOL, status='UNAVAILABLE' if unavailable else 'INVALID',
                              reasonCode='VERIFIER_UNAVAILABLE' if unavailable else 'EVIDENCE_INVALID')))
        sys.exit(2 if unavailable else 1)
