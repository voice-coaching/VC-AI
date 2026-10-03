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
from types import ModuleType
from uuid import UUID

PROTOCOL = 'voice-coaching.canonical-offline-verification.v1'


def restrict_process():
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('VERIFIER_RUNTIME_INVALID')
    import resource
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
    from voice_coach.backend_analysis.canonical_harness import H5, S7, H5_DIRECTORY, harness_identity
    frozen = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(args.core_root, args.llm_root))
    h5 = FrozenCanonicalCoaching(FrozenCanonicalArtifacts(args.core_root, args.llm_root.parent / H5_DIRECTORY))
    # Installation failure is not invalid user evidence. Validate it before reading a job.
    try:
        frozen._bridge()
        if frozen.artifacts.identity != S7 or h5.artifacts.identity != H5:
            raise RuntimeError('VERIFIER_HARNESS_ROOT_MISMATCH')
        h5._bridge()
    except Exception:
        raise RuntimeError('VERIFIER_DEPENDENCIES_UNAVAILABLE') from None
    if args.check_installation:
        from voice_coach.backend_analysis.canonical_bridge import (
            CORE_MANIFEST_SHA256, LLM_MANIFEST_SHA256, LLM_LOCK_SHA256,
        )
        print(json.dumps(dict(protocol=PROTOCOL, status='READY', coreManifestSha256=CORE_MANIFEST_SHA256,
                              llmManifestSha256=LLM_MANIFEST_SHA256, llmLockSha256=LLM_LOCK_SHA256,
                              h5ManifestSha256=H5.manifest_sha256, h5LockSha256=H5.lock_sha256)))
        return
    header = _read_json(frame(1024), 1024)
    if (type(header) is not dict or set(header) != {'protocol', 'operation', 'receiptId'}
            or header['protocol'] != PROTOCOL
            or header['operation'] not in {'VERIFY_EVIDENCE', 'VERIFY_CALLBACK', 'VERIFY_PRECORE'}):
        raise ValueError('VERIFIER_HEADER_INVALID')
    operation = header['operation']
    request_bytes = frame(65536)
    request = AnalysisInput.from_payload(parse_json(request_bytes), args.recordings_prefix)
    if request.analysis_profile != 'CANONICAL_FROZEN_20260928_V4':
        raise ValueError('VERIFIER_PROFILE_INVALID')
    response = dict(protocol=PROTOCOL, status='VALID', requestSha256=hashlib.sha256(request_bytes).hexdigest(),
                    manifestSha256=None, callbackSha256=None)
    preflight = receipt = None
    if operation != 'VERIFY_PRECORE':
        raw_manifest = frame(65536)
        manifest = _read_json(raw_manifest, 65536)
        _validate(manifest, 'evidenceManifest', schema_name='control')
        originals = {}
        references = []
        for reference in manifest['artifacts']:
            reference = ArtifactReference.from_document(reference)
            raw = frame(16 * 1024 * 1024)
            if (reference.kind in originals or len(raw) != reference.byte_size
                    or hashlib.sha256(raw).hexdigest() != reference.sha256):
                raise ValueError('VERIFIER_ORIGINAL_MISMATCH')
            originals[reference.kind] = raw
            references.append(reference)
        private = CanonicalExpressionResult(originals['CORE'], originals['BINDING'], originals['ASSOCIATION'],
                                            originals['BRIDGE_RESULT'], originals.get('SELECTION_PROJECTION'))
        association = _read_json(private.association_bytes)
        _validate(association, 'association')
        selected = harness_identity(association['llmManifestSha256'])
        frozen = h5 if selected == H5 else frozen
        metadata = association['preparedAudio']
        prepared = PreparedAudioIdentity.from_document(metadata) if metadata is not None else None
        # This verifies authenticated producer measurements and their binding,
        # not an independent re-decode of the original/prepared audio on Backend.
        preflight = preflight_canonical_result(private, request=request, frozen=frozen, prepared_audio=prepared)
        if preflight.association.to_document() != manifest['association']:
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
