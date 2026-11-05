"""Publish the pre-merge check only for a protected receipt matching current PR HEAD.

Run on the controlled DLPC operator host. GH_TOKEN is read only from the environment.
Does not merge the PR or execute untrusted PR code with credentials.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import stat
import urllib.request

REPOSITORY = 'voice-coaching/intelligentAI'
CONTEXT = 'voice-analysis-smoke'


def api(method, path, body=None):
    request = urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+path,
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={'Authorization': 'Bearer '+os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
        return json.load(response)


def main(args):
    path = args.receipt.resolve(strict=True)
    # An editable JSON assertion is not an operator test receipt.
    for part in (path, *path.parents):
        info = part.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('SMOKE_RECEIPT_NOT_OPERATOR_PROTECTED')
    receipt = json.loads(path.read_bytes())
    if receipt.get('schemaVersion') != 'voice-analysis-smoke-receipt.v1' or receipt.get('passed') is not True:
        raise ValueError('SMOKE_RECEIPT_NOT_PASSED')
    if set(receipt['lanes']) != {'direct', 'internal'}:
        raise ValueError('SMOKE_BOTH_LANES_REQUIRED')
    for lane in receipt['lanes'].values():
        rows = lane['cases']
        if not lane['passed'] or len(rows) < 3 or any(
                row.get('passed') is not True or row.get('postAttempts') != 1
                or row.get('coreSha256') != row.get('rawCoreMessageSha256') for row in rows):
            raise ValueError('SMOKE_CASES_NOT_PASSED')
    pr = api('GET', f'/pulls/{args.pr}')
    if pr['state'] != 'open' or pr['head']['repo']['full_name'] != REPOSITORY or pr['base']['ref'] != 'main':
        raise ValueError('SMOKE_PR_TARGET_MISMATCH')
    if pr['head']['sha'] != receipt['revision']:
        raise ValueError('SMOKE_RECEIPT_STALE_HEAD')
    commit = api('GET', '/git/commits/'+receipt['revision'])
    if commit['tree']['sha'] != receipt['tree']:
        raise ValueError('SMOKE_RECEIPT_STALE_TREE')
    summary = '\n'.join(f"{lane}: {len(row['cases'])} real audio analyses passed" for lane, row in receipt['lanes'].items())
    api('POST', '/issues/'+str(args.pr)+'/comments', dict(body=(
        f"Pre-merge voice test passed at `{receipt['revision']}`.\n\n{summary}\n\n"
        "Verified: real CORE/MFA/GOP, one GPT POST per analysis, unchanged raw CORE message, "
        "server score verification, numeric score and final feedback. "
        "Archive/history delivery and browser quality review are separate checks.\n\n"
        f"Operator receipt: `{path}`\nCompleted: "
        +datetime.datetime.fromtimestamp(receipt['finishedAt'], datetime.timezone.utc).isoformat())))
    status = api('POST', '/statuses/'+receipt['revision'], dict(state='success', context=CONTEXT,
        description='Both CORE/H5 lanes: real audio, one GPT call, score and feedback passed', target_url=pr['html_url']))
    print(json.dumps(dict(context=status['context'], state=status['state'], revision=receipt['revision'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--pr', type=int, required=True)
    main(parser.parse_args())
