"""Provision the v5 Backend archive writer using the reviewed private-key supply.

No RunPod/reader credential reuse. Same exclusive intent/reconciliation behavior
as the canonical provisioner; no bucket policy or object changes.
"""
import json
import re
import sys
import provision_canonical_b2_keys as provision

provision.PREFIX = 'canonical-evidence/v5/'
provision.ROLES = {
    'backend-handoff-writer': ['readBuckets', 'readFiles', 'writeFiles', 'listAllBucketNames'],
}

if __name__ == '__main__':
    try:
        provision.main()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[A-Z0-9_]+', str(error)) else 'PROVISIONING_FAILED'
        print(json.dumps({'error': code, 'instruction': 'Inspect private supply and intents before retrying.'}))
        sys.exit(1)
