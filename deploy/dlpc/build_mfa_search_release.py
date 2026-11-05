"""Derive a new immutable CORE with explicit MFA search breadth; never patch live CORE."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent/'runpod'))
from release_packaging import encode, load_files, replace_once, sha, write_release

PARENTS = {
    'direct': '9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba',
    'internal': 'f562484d436e42626114e16afa2bcaa782c1abbfc3cc3cdbd5c3b3cf030b920a',
}


def build(core, destination, lane):
    manifest, files = load_files(core, 'release_manifest.json', PARENTS[lane], 'file_sha256')
    name = 'canonical_mfa_search_'+lane+'_20261105_v1'
    policy = dict(revision='mfa-search-100-400-v1', beam=100, retryBeam=400,
                  basis='Same 6.784-second incident: 10/40 NoAlignmentsError; 100/400 actual workflow succeeded',
                  generationRetries=0, providerRetries=0)
    worker = files['runtime_bundle/resident_worker.py'].decode()
    lines = [line for line in worker.splitlines(keepends=True)
             if "extra=PretrainedAligner.parse_parameters(kw.get('config_path'),ctx.params,ctx.args)" in line]
    if len(lines) != 1:
        raise ValueError('MFA_SEARCH_PARAMETER_CONTEXT')
    line = lines[0]
    indent = line[:len(line)-len(line.lstrip())]
    worker = replace_once(worker, line, line
        + indent + '# Pinned search policy; one workflow, unchanged audio/transcript/model.\n'
        + indent + 'extra.update(beam=100, retry_beam=400)\n')
    files['runtime_bundle/resident_worker.py'] = worker.encode()
    files['config/mfa_search_policy.json'] = encode(policy)
    historical = json.loads(files['config/historical_runtime_identity.json'])
    historical['source_files']['resident_worker.py'].update(sha256=sha(worker.encode()),
        copied_sha256=sha(worker.encode()), bytes=len(worker.encode()))
    historical['dlpc_derivation'] = 'Explicit MFA beam=100/retry_beam=400; same workflow and input, no generation replay'
    files['config/historical_runtime_identity.json'] = encode(historical)
    manifest.update(release_version=name, release_root=str(destination/name),
                    parent_manifest_sha256=PARENTS[lane], deployed=False, production_modified=False,
                    mfa_search_policy=policy)
    manifest['file_sha256'] = {n:sha(raw) for n,raw in sorted(files.items())}
    raw = encode(manifest)
    files['release_manifest.json'] = raw
    for n,value in files.items():
        if n.endswith('.py'):
            compile(value,n,'exec')
    write_release(destination/name, files)
    return dict(root=str(destination/name), manifestSha256=sha(raw), lane=lane)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--lane',choices=tuple(PARENTS),required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.core,args.destination,args.lane)))
