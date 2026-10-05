"""Package pinned Git sources against a read-only observation of the deployed overlay."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def paths(revision):
    tracked = git('ls-tree', '-r', '--name-only', revision).decode().splitlines()
    fixed = {
        'src/voice_coach/backend_analysis/http_api.py',
        'src/voice_coach/backend_analysis/http_models.py',
        'src/voice_coach/backend_analysis/http_runtime.py',
        'src/voice_coach/backend_analysis/journal_http.py',
        'src/voice_coach/backend_analysis/pipeline_timing.py',
        'src/voice_coach/backend_analysis/deployment_observation.py',
        'src/voice_coach/backend_analysis/phone_rubric.py',
        'src/voice_coach/backend_analysis/gpt_rubric.py',
        'deploy/runpod/prepare_http.py', 'deploy/runpod/prepare_canonical.py',
        'deploy/runpod/canonical-requirements.lock',
        'docs/contracts/runpod_analysis_request_v2.schema.json',
        'docs/contracts/runpod_http_control_v1_2.schema.json',
        'docs/contracts/runpod_result_v4.schema.json',
        'docs/contracts/runpod_result_v5.schema.json',
        'docs/contracts/runpod_analysis_request_v3.schema.json',
    }
    selected = fixed | {p for p in tracked if
        (p.startswith(('src/voice_coach/backend_analysis/', 'src/voice_coach/direct_analysis/')) and p.endswith('.py'))
        or (p.startswith('docs/contracts/') and p.endswith('.schema.json'))}
    if not selected.issubset(set(tracked)):
        raise ValueError('commit_required_before_packaging')
    return sorted(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True, help='Full committed AI Git SHA')
    parser.add_argument('--list-paths', action='store_true')
    parser.add_argument('--base-inventory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}', args.revision):
        raise ValueError('full_revision_required')
    selected = paths(args.revision)
    if args.list_paths:
        print(json.dumps(selected)); return
    if args.base_inventory is None or args.output is None:
        parser.error('--base-inventory and --output are required')
    base = json.loads(args.base_inventory.read_bytes())
    if (not re.fullmatch('[0-9a-f]{64}', base['baseOverlaySha256'])
            or not re.fullmatch('[0-9a-f]{40}', base['baseRevision'])
            or set(base['files']) != set(selected)):
        raise ValueError('base_inventory_mismatch')
    records = []
    for name in selected:
        before = base['files'][name]
        if before is not None and not re.fullmatch('[0-9a-f]{64}', before):
            raise ValueError('base_file_digest_invalid')
        raw = git('show', args.revision + ':' + name)
        records.append(dict(path=name, beforeSha256=before,
                            sha256=hashlib.sha256(raw).hexdigest(),
                            content=base64.b64encode(raw).decode()))
    raw = json.dumps(dict(sourceGitRevision=args.revision, forbidLegacyRollback=True,
        baseOverlaySha256=base['baseOverlaySha256'], baseRevision=base['baseRevision'],
        files=records), sort_keys=True, separators=(',', ':')).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream: stream.write(raw)
    print(json.dumps(dict(path=str(args.output), files=len(records),
                          sha256=hashlib.sha256(raw).hexdigest())))


if __name__ == '__main__':
    main()
