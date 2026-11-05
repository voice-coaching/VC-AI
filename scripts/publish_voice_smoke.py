"""Publish the pre-merge check only for a protected receipt matching current PR HEAD.

Run on the controlled DLPC operator host. GH_TOKEN is read only from the environment.
Can create/merge only after checking the protected success receipt.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import stat
import urllib.request
import urllib.parse

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
    internal=receipt.get('internalV6',{})
    if (internal.get('passed') is not True or internal.get('status')!='COMPLETED'
            or internal.get('scope')!='REAL_INTERNAL_REQUEST_V4_TO_V6_HANDOFF_OFFLINE'
            or internal.get('postAttempts')!=1
            or internal.get('coreSha256')!=internal.get('rawCoreMessageSha256')
            or internal.get('harnessManifestSha256')!=receipt['lanes']['internal']['harnessManifestSha256']):
        raise ValueError('SMOKE_REAL_INTERNAL_V6_REQUIRED')
    if receipt.get('backendVerification',{}).get('passed') is not True:
        raise ValueError('SMOKE_BACKEND_PARSE_PROJECTION_REQUIRED')
    score = internal.get('score', {})
    if (type(score.get('overallScore')) not in (int, float)
            or not 0 <= score['overallScore'] <= 100 or score.get('validity') != 'RUBRIC_COMPUTED'):
        raise ValueError('SMOKE_INTERNAL_NUMERIC_SCORE_REQUIRED')
    for lane in receipt['lanes'].values():
        rows = lane['cases']
        if not lane['passed'] or len(rows) < 3 or any(
                row.get('passed') is not True or row.get('postAttempts') != 1
                or row.get('coreSha256') != row.get('rawCoreMessageSha256') for row in rows):
            raise ValueError('SMOKE_CASES_NOT_PASSED')
    if args.create_pr_head:
        head=api('GET','/git/ref/heads/'+urllib.parse.quote(args.create_pr_head,safe='/'))
        base=api('GET','/git/ref/heads/main')
        if head['object']['sha']!=receipt['revision'] or base['object']['sha']!=receipt['baseRevision']:
            raise ValueError('SMOKE_CREATE_PR_STALE_REVISION')
        commit=api('GET','/git/commits/'+receipt['revision'])
        if commit['tree']['sha']!=receipt['tree']:
            raise ValueError('SMOKE_CREATE_PR_STALE_TREE')
        if not args.title or args.body_file is None:
            raise ValueError('SMOKE_CREATE_PR_DESCRIPTION_REQUIRED')
        pr=api('POST','/pulls',dict(head=args.create_pr_head,base='main',
            title=args.title,body=args.body_file.read_text()))
        args.pr=pr['number']
        print(json.dumps(dict(createdPr=pr['html_url'])))
    else:
        pr = api('GET', f'/pulls/{args.pr}')
    if pr['state'] != 'open' or pr['head']['repo']['full_name'] != REPOSITORY or pr['base']['ref'] != 'main':
        raise ValueError('SMOKE_PR_TARGET_MISMATCH')
    if pr['head']['sha'] != receipt['revision']:
        raise ValueError('SMOKE_RECEIPT_STALE_HEAD')
    if pr['base']['sha'] != receipt['baseRevision']:
        raise ValueError('SMOKE_RECEIPT_STALE_BASE')
    commit = api('GET', '/git/commits/'+receipt['revision'])
    if commit['tree']['sha'] != receipt['tree']:
        raise ValueError('SMOKE_RECEIPT_STALE_TREE')
    summary = '\n'.join(f"{lane}: {len(row['cases'])} real audio analyses passed" for lane, row in receipt['lanes'].items())
    api('POST', '/issues/'+str(args.pr)+'/comments', dict(body=(
        f"Pre-merge voice test passed at `{receipt['revision']}`.\n\n{summary}\n\n"
        "Verified: real CORE/MFA/GOP, one GPT POST per analysis, unchanged raw CORE message, "
        "server score verification, numeric score and final feedback. "
        "Also passed the known incident's real internal v4 request/media receipt through v6/handoff serialization. "
        "Backend persistence, archive/history delivery and browser quality review are separate checks.\n\n"
        f"Operator receipt: `{path}`\nCompleted: "
        +datetime.datetime.fromtimestamp(receipt['finishedAt'], datetime.timezone.utc).isoformat())))
    status = api('POST', '/statuses/'+receipt['revision'], dict(state='success', context=CONTEXT,
        description='Both CORE/H5 lanes: real audio, one GPT call, score and feedback passed', target_url=pr['html_url']))
    print(json.dumps(dict(context=status['context'], state=status['state'], revision=receipt['revision'])))
    if args.merge:
        merged = api('PUT', '/pulls/'+str(args.pr)+'/merge', dict(sha=receipt['revision'],
            merge_method='merge', commit_title=pr['title']+' [skip ci]',
            commit_message='Real voice-analysis-smoke passed on the exact PR head before merge.'))
        if merged.get('merged') is not True:
            raise ValueError('SMOKE_PR_NOT_MERGED')
        print(json.dumps(dict(merged=True, revision=merged['sha'])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    target=parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--pr', type=int)
    target.add_argument('--create-pr-head', help='Create the PR only after the exact candidate tests have passed')
    parser.add_argument('--title')
    parser.add_argument('--body-file',type=Path)
    parser.add_argument('--merge', action='store_true', help='Merge with the tested head SHA only after all receipt checks pass')
    main(parser.parse_args())
