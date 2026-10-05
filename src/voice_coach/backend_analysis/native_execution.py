"""Separate historical read identities from approved Native execution identities."""
from __future__ import annotations

import json

from .native_phone_assessment import policy_contract, sha256_json
from .native_phone_contract import TOKENS
from .phone_rubric import require

# A source build or a self-declared APPROVED policy cannot authorize production.
# Populate only with reviewed core/H5 hashes after calibration + developer QA.
APPROVED_NATIVE_EXECUTIONS: frozenset[tuple[str, str]] = frozenset()


def require_native_execution(artifacts):
    identity = artifacts.identity
    require(identity.native, "RETIRED_CORE_EXECUTION_FORBIDDEN")
    policy = json.loads((artifacts.core_root / "config/native_phone_assessment.json").read_bytes())
    policy_contract(policy, approved=True)
    require((identity.core_sha256, identity.manifest_sha256) in APPROVED_NATIVE_EXECUTIONS,
            "NATIVE_RELEASE_DEVELOPER_QA_REQUIRED")


def admitted_binding(request, *, core_sha256, prepared_sha256):
    return dict(requestId=str(request.event_id), executionId=str(request.execution_id),
                sourceAudioSha256=request.audio_sha256, preparedWavSha256=prepared_sha256,
                scriptSha256=request.script_sha256, coreManifestSha256=core_sha256,
                vocabularySha256=sha256_json({token: i for i, token in enumerate(TOKENS)}))
