"""Root-only B2 provisioning on the approved AWS secret-supply host.

Input: one private JSON document on stdin containing keyId/applicationKey.
Never put credentials in argv. Stdout contains metadata only. No runtime env,
bucket policy, object, or admission changes. An ambiguous create is never retried.
The durable root-only files are a provisioning supply, not service injection.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path('/etc/alpha-canonical-secrets')
BUCKET = 'speakai2026'
PREFIX = 'canonical-evidence/v1/'
ROLES = {
    'runpod-writer': ['readBuckets', 'readFiles', 'writeFiles', 'listAllBucketNames'],
    'backend-reader': ['readBuckets', 'readFiles', 'listAllBucketNames'],
}
TTL = 90 * 24 * 60 * 60


def require(condition, code):
    if not condition:
        raise ValueError(code)


def private_file(path, value):
    """Exclusive creation: leave an intent/partial file for operator recovery."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode())
        stream.flush()
        os.fsync(stream.fileno())
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def read_private(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o600, 'SECRET_FILE_PERMISSIONS')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(16385)
    require(len(raw) <= 16384, 'SECRET_FILE_SIZE')
    return json.loads(raw)


def call(url, token, document=None):
    data = None if document is None else json.dumps(document).encode()
    request = urllib.request.Request(url, data=data, headers={
        'Authorization': token, 'Content-Type': 'application/json',
        'User-Agent': 'voice-coaching-canonical-key-provisioner/1',
    })
    # No redirects, proxy, retry or insecure TLS. The authorization response is private.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=25) as response:
            raw = response.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, 'B2_RESPONSE_SIZE')
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        stage = {'b2_authorize_account': 'AUTHORIZE', 'b2_list_buckets': 'BUCKETS',
                 'b2_create_key': 'CREATE_KEY'}.get(url.rsplit('/', 1)[-1], 'UNKNOWN')
        raise ValueError('B2_' + stage + '_HTTP_' + str(error.code)) from None


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ROOT_LINUX_REQUIRED')
    os.umask(0o077)
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
    info = ROOT.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700 and ROOT.resolve() == ROOT,
            'SECRET_ROOT_PERMISSIONS')
    payload = sys.stdin.buffer.read(16385)
    require(0 < len(payload) <= 16384, 'INPUT_SIZE')
    credentials = json.loads(payload)
    require(set(credentials) == {'keyId', 'applicationKey'}, 'INPUT_FIELDS')
    require(all(isinstance(v, str) and v and not any(c.isspace() for c in v)
                for v in credentials.values()), 'INPUT_CREDENTIALS')
    basic = base64.b64encode((credentials['keyId'] + ':' + credentials['applicationKey']).encode()).decode()
    auth = call('https://api.backblazeb2.com/b2api/v4/b2_authorize_account', 'Basic ' + basic)
    credentials.clear()
    basic = payload = None
    storage = auth['apiInfo']['storageApi']
    api = storage['apiUrl']
    parsed = urllib.parse.urlsplit(api)
    require(parsed.scheme == 'https' and parsed.hostname is not None
            and re.fullmatch(r'api[0-9]+[.]backblazeb2[.]com', parsed.hostname)
            and not parsed.username and not parsed.port and parsed.path in ('', '/')
            and not parsed.query and not parsed.fragment, 'B2_API_ORIGIN')
    api = api.rstrip('/') + '/b2api/v4/'
    # Native v4 nests storage restrictions under allowed (not storageApi itself).
    allowed = storage.get('allowed')
    require(isinstance(allowed, dict)
            and isinstance(allowed.get('capabilities'), list), 'B2_ALLOWED_RESPONSE_SHAPE')
    require({'listBuckets', 'writeKeys'} <= set(allowed['capabilities']), 'B2_ADMIN_CAPABILITIES')
    token = auth['authorizationToken']
    account = auth['accountId']
    buckets = call(api + 'b2_list_buckets', token, {'accountId': account, 'bucketName': BUCKET})['buckets']
    matches = [b for b in buckets if b['bucketName'] == BUCKET]
    require(len(matches) == 1 and matches[0]['bucketType'] == 'allPrivate', 'PRIVATE_BUCKET_REQUIRED')
    bucket = matches[0]
    require(bucket.get('lifecycleRules') == [], 'LIFECYCLE_REVIEW_REQUIRED')
    require(storage['s3ApiUrl'] == 'https://s3.us-east-005.backblazeb2.com', 'B2_REGION_CHANGED')
    results = []
    for role, capabilities in ROLES.items():
        target = ROOT / (role + '.json')
        intent = ROOT / (role + '.intent.json')
        if target.exists():
            record = read_private(target)
            require(record['bucketIds'] == [bucket['bucketId']]
                    and record['namePrefix'] == PREFIX
                    and sorted(record['capabilities']) == sorted(capabilities), 'EXISTING_KEY_SCOPE_MISMATCH')
            require(record['expirationTimestamp'] > int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000),
                    'EXISTING_KEY_EXPIRED')
            results.append({'role': role, 'status': 'EXISTING_PRIVATE_SUPPLY',
                            'keyId': record['applicationKeyId'], 'expirationTimestamp': record['expirationTimestamp']})
            continue
        require(not intent.exists(), 'KEY_CREATE_OUTCOME_UNKNOWN_RECONCILE_FIRST')
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')
        name = 'canonical-prod-' + role + '-' + stamp
        private_file(intent, {'keyName': name, 'bucketIds': [bucket['bucketId']],
                              'namePrefix': PREFIX, 'capabilities': capabilities})
        record = call(api + 'b2_create_key', token, {
            'accountId': account, 'keyName': name, 'capabilities': capabilities,
            'bucketIds': [bucket['bucketId']], 'namePrefix': PREFIX, 'validDurationInSeconds': TTL,
        })
        # Preserve the one-time response privately even if subsequent scope verification fails.
        private_file(target, record)
        require(record.get('bucketIds') == [bucket['bucketId']] and record.get('namePrefix') == PREFIX
                and sorted(record.get('capabilities', [])) == sorted(capabilities)
                and record.get('applicationKey') and record.get('applicationKeyId')
                and record.get('expirationTimestamp'), 'CREATED_KEY_SCOPE_MISMATCH')
        results.append({'role': role, 'status': 'CREATED_PRIVATE_SUPPLY',
                        'keyId': record['applicationKeyId'], 'expirationTimestamp': record['expirationTimestamp']})
    print(json.dumps({'bucket': BUCKET, 'prefix': PREFIX, 'keys': results,
                      'runtimeInjected': False, 'admissionChanged': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[A-Z0-9_]+', str(error)) else 'PROVISIONING_FAILED'
        print(json.dumps({'error': code, 'instruction': 'Inspect private supply and intents; do not blindly repeat key creation.'}))
        sys.exit(1)
