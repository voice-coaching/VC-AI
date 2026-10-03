"""Non-secret identity captured once when the analysis process starts."""
import hashlib
import json
from pathlib import Path
import re

from .canonical_settings import schema_digests


def read_identity(environment):
    metadata = json.loads(Path('/opt/intelligentai/deployment.json').read_bytes())
    integration = metadata['canonicalIntegration']
    if (not re.fullmatch('[0-9a-f]{40}', integration['sourceGitRevision'])
            or not re.fullmatch('[0-9a-f]{64}', integration['bundleSha256'])):
        raise ValueError('deployment_identity_invalid')
    core = Path(environment['AI_CANONICAL_CORE_ROOT'])
    llm = Path(environment['AI_CANONICAL_LLM_ROOT']) / 'contracts/llm'
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
        ('coreManifestSha256', core / 'release_manifest.json'),
        ('llmManifestSha256', llm / 'llm_contract_manifest.json'),
        ('llmLockSha256', llm / 'dependency_lock.json'))}
    return dict(sourceGitRevision=integration['sourceGitRevision'],
                bundleSha256=integration['bundleSha256'], **hashes,
                schemaDigests=schema_digests())
