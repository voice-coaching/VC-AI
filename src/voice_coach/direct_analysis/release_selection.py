"""Select a server-approved direct core/H5 pair without changing internal settings."""
from pathlib import Path

from voice_coach.backend_analysis.canonical_bridge import (
    CanonicalIntegrationError, FrozenCanonicalArtifacts, _file_sha)
from voice_coach.backend_analysis.native_execution import require_native_execution


def direct_environment(environment):
    values = dict(environment)
    llm = values.get('AI_DIRECT_LLM_ROOT')
    if 'AI_DIRECT_CORE_ROOT' in values:
        core = values['AI_DIRECT_CORE_ROOT']
        if not core or not llm:
            raise ValueError('DIRECT_RELEASE_PAIR_REQUIRED')
        try:
            artifacts = FrozenCanonicalArtifacts(Path(core), Path(llm))
            identity = artifacts.identity
            require_native_execution(artifacts)
            if _file_sha(Path(core) / 'release_manifest.json') != identity.core_sha256:
                raise ValueError('DIRECT_RELEASE_PAIR_MISMATCH')
        except (OSError, ValueError, CanonicalIntegrationError):
            raise ValueError('DIRECT_RELEASE_PAIR_MISMATCH') from None
        values['AI_CANONICAL_CORE_ROOT'] = core
    if llm is not None:
        values['AI_CANONICAL_LLM_ROOT'] = llm
    return values
