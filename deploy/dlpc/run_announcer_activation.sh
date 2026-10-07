#!/bin/sh
# Run once in the DLPC platform terminal, as user 2020067.
# Keep this terminal open at waiting_for_backend_activation; the operator
# performs AWS hold/activate/resume over the existing administrator connection.
set -eu
python3 - <<'PY'
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request

home = os.path.expanduser('~')
ssh = ['ssh', '-F', '/dev/null', '-T', '-p', '22132',
       '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=15',
       '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=13.209.184.226',
       '-o', 'UserKnownHostsFile='+home+'/.ssh/intelligentai_aws_known_hosts',
       '-i', home+'/.ssh/intelligentai_aws_20261005', 'ec2-user@13.209.184.226', 'dlpc-recovery']
auth = json.loads(subprocess.check_output(ssh, input=b'{"operation":"b2"}', timeout=60))
target = urllib.parse.urlsplit(auth['downloadUrl'])
assert (target.scheme == 'https' and target.hostname.endswith('.backblazeb2.com')
        and not target.username and not target.password and target.port in (None, 443))
file_id = '4_ze5cc71977ef1880ca60b0c17_f107e77b17144395f_d20261007_m015757_c005_v0501042_t0031_u01791338277519'
url = auth['downloadUrl']+'/b2api/v4/b2_download_file_by_id?'+urllib.parse.urlencode({'fileId': file_id})
request = urllib.request.Request(url, headers={'Authorization': auth['authorizationToken']})
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
with opener.open(request, timeout=60) as reply:
    code = reply.read(11287)
assert len(code) == 11286 and hashlib.sha256(code).hexdigest() == 'ee7539f971448bf3696c979c5f5165e2576bdac351e054b81909e381e078b48c'
subprocess.run(['sudo', '-n', '/usr/bin/python3', '-',
    '--bundle-sha256', 'b7170dda1a4ec5a520f30dfa88a0f57f649f9c118060975957e7f68b27b94e78',
    '--bundle-file-id', '4_ze5cc71977ef1880ca60b0c17_f11679934c7b4394b_d20261007_m015732_c005_v0501017_t0020_u01791338252241',
    '--bundle-bytes', '14869274'], input=code, check=True)
PY
