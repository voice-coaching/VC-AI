"""Validate pinned deployment provenance without a Git checkout at runtime."""
import hashlib
import json
from pathlib import Path
import re

from .canonical_bridge import _require

def check_provenance(core_root: Path) -> None:
    _require(core_root.is_absolute() and core_root.resolve(strict=True) == core_root,
             'CANONICAL_PROVENANCE_PATH')
    name = 'source/config/release_provenance.json'
    try:
        manifest = json.loads((core_root / 'release_manifest.json').read_bytes())
        raw = (core_root / name).read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == manifest['file_sha256'][name],
                 'CANONICAL_PROVENANCE_DIGEST')
        doc = json.loads(raw)
        _require(set(doc) == {'schema', 'source_git_commit', 'source_modified'}
                 and doc['schema'] == 'canonical-release-provenance-v1'
                 and re.fullmatch('[0-9a-f]{40}', doc['source_git_commit'])
                 and type(doc['source_modified']) is bool, 'CANONICAL_PROVENANCE_INVALID')
        identity = json.loads((core_root / 'config/release_preflight_identity.json').read_bytes())
        _require(doc['source_git_commit'] == identity['base_source_commit']
                 and doc['source_modified'] is True, 'CANONICAL_PROVENANCE_REVISION')
    except (OSError, ValueError, TypeError, KeyError):
        _require(False, 'CANONICAL_PROVENANCE_UNAVAILABLE')
