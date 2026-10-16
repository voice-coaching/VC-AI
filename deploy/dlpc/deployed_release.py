"""Which installed release may recovery resume? Pinned baseline or a proven deployment.

Recovery originally pinned one source revision and bundle. After the Actions
controller deploys main, deployment.json carries the new revision and bundle, so
a restart must accept that release, but only with the controller's own
root-only success receipt for exactly that revision and bundle.
"""
import json
import os
from pathlib import Path
import re
import stat

BASELINE = ('791ca5496e04a10b6948c69dce97361ee4335f4c',
            'd5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443')
METADATA = Path('/opt/intelligentai/deployment.json')
TRANSACTIONS = Path('/opt/intelligentai/cicd/transactions')


def _receipt(revision):
    path = TRANSACTIONS / revision / 'state.json'
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077
            or info.st_size > 65536 or path.parent.resolve() != TRANSACTIONS / revision):
        return None
    return json.loads(path.read_bytes())


def accepted(metadata=None):
    """Return (revision, bundleSha256) or raise PRODUCTION_RELEASE_CHANGED."""
    doc = json.loads(METADATA.read_bytes()) if metadata is None else metadata
    identity = doc.get('canonicalIntegration', {})
    release = (identity.get('sourceGitRevision'), identity.get('bundleSha256'))
    if release == BASELINE:
        return release
    revision, bundle = release
    if isinstance(revision, str) and re.fullmatch('[0-9a-f]{40}', revision) and isinstance(bundle, str):
        try:
            receipt = _receipt(revision)
        except (OSError, ValueError):
            receipt = None
        if (receipt and receipt.get('status') == 'succeeded' and receipt.get('revision') == revision
                and receipt.get('bundleSha256') == bundle):
            return release
    raise ValueError('PRODUCTION_RELEASE_CHANGED')
