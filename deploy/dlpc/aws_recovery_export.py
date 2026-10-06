"""Fixed SSH forced command. Never accepts a shell command or caller path.

Install root-owned on AWS. Its only operations are exporting the approved DLPC
role settings and authorizing a bucket/prefix-restricted B2 recovery reader.
Output is secret and must go directly to the authenticated DLPC process.
"""
import base64
import json
import os
from pathlib import Path
import socket
import stat
import sys
import urllib.request

ROOT = Path('/root/.config/intelligentai/dlpc-recovery-20261006')
BUCKET = 'e5cc71977ef1880ca60b0c17'
PREFIX = 'intelligentai/recovery/dlpc-20261006/'


def private(name):
    path = ROOT / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_FILE_REQUIRED')
    if info.st_size > 131072:
        raise ValueError('PRIVATE_FILE_TOO_LARGE')
    return path.read_text()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT_REQUIRED')
    request = json.loads(sys.stdin.buffer.read(129))
    if request == {'operation': 'settings'}:
        values = {}
        for line in private('analysis.env').splitlines():
            if line and not line.startswith('#'):
                key, value = line.split('=', 1)
                if key in values:
                    raise ValueError('DUPLICATE_SETTING')
                values[key] = value
        body = dict(analysis=values, ttsToken=private('tts-token').strip())
    elif request == {'operation': 'b2'}:
        key = json.loads(private('b2-reader.json'))
        basic = base64.b64encode((key['keyId'] + ':' + key['applicationKey']).encode()).decode()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account',
                                     headers={'Authorization': 'Basic ' + basic})
        with opener.open(req, timeout=30) as reply:
            auth = json.loads(reply.read(65537))
        api = auth['apiInfo']['storageApi']
        allowed = api['allowed']
        if (set(allowed['capabilities']) != {'readFiles'}
                or allowed.get('namePrefix') != PREFIX
                or [b['id'] for b in allowed.get('buckets', [])] != [BUCKET]):
            raise ValueError('B2_READER_SCOPE_MISMATCH')
        body = dict(apiUrl=api['apiUrl'], downloadUrl=api['downloadUrl'],
                    authorizationToken=auth['authorizationToken'], accountId=auth['accountId'],
                    bucketId=BUCKET, bucketName='speakai2026')
    else:
        raise ValueError('OPERATION_NOT_ALLOWED')
    sys.stdout.write(json.dumps(body))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Provider errors can contain credentials. Never serialize them.
        sys.stderr.write('RECOVERY_EXPORT_FAILED\n')
        sys.exit(1)
