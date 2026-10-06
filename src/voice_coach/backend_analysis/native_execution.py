"""Separate historical read identities from approved Native execution identities."""
from __future__ import annotations

import json

from .native_phone_assessment import policy_contract, sha256_json
from .native_phone_contract import TOKENS
from .phone_rubric import require

# Operator-authorized analysis releases. This does not approve a scoring policy
# or assert inference/quality QA. Scoring checks its own calibrated evidence.
APPROVED_NATIVE_EXECUTIONS: frozenset[tuple[str, str]] = frozenset({(
    "f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841",
    "efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428",
)})


def require_native_execution(artifacts):
    identity = artifacts.identity
    require(identity.native, "RETIRED_CORE_EXECUTION_FORBIDDEN")
    policy = json.loads((artifacts.core_root / "config/native_phone_assessment.json").read_bytes())
    policy_contract(policy)
    require((identity.core_sha256, identity.manifest_sha256) in APPROVED_NATIVE_EXECUTIONS,
            "NATIVE_RELEASE_DEVELOPER_QA_REQUIRED")


def admitted_binding(request, *, core_sha256, prepared_sha256):
    return dict(requestId=str(request.event_id), executionId=str(request.execution_id),
                sourceAudioSha256=request.audio_sha256, preparedWavSha256=prepared_sha256,
                scriptSha256=request.script_sha256, coreManifestSha256=core_sha256,
                vocabularySha256=sha256_json({token: i for i, token in enumerate(TOKENS)}))
