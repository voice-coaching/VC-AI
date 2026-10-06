"""Read-only AWS/DLPC cutover checks. Never synthesize, submit jobs or print keys."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import stat
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A credential must not follow an unexpected destination.


def private_environment(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('PRIVATE_ENV_PERMISSIONS')
    values = {}
    allowed = {'AI_ANALYSIS_API_TOKEN', 'AI_ANALYSIS_CALLBACK_TOKEN',
               'EXAMPLE_TTS_APITOKEN', 'EXAMPLE_TTS_REVISION',
               'RUNPOD_ENDPOINT_URL', 'EXAMPLE_TTS_BASEURL'}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if key not in allowed:
            continue
        if not separator or key in values:
            raise ValueError('PRIVATE_ENV_FORMAT')
        values[key] = value.strip('"')
    return values


def get(url, token=None):
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            reply = opener.open(urllib.request.Request(url, headers=headers), timeout=10)
        except urllib.error.HTTPError as error:
            reply = error
        with reply:
            raw = reply.read(131073)
            if len(raw) > 131072:
                return reply.code, {}
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = {}
            return reply.code, body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return None, {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-ready', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not socket.gethostname().startswith('ip-10-20-9-222'):
        raise ValueError('EXPECTED_AWS_ROOT_REQUIRED')
    env = private_environment(Path('/etc/alpha-backend.env'))
    api_token, tts_token = env['AI_ANALYSIS_API_TOKEN'], env['EXAMPLE_TTS_APITOKEN']
    paths = {
        'analysisLiveness': ('http://127.0.0.1:18080/health', None),
        'analysisReadiness': ('http://127.0.0.1:18080/readyz', None),
        'analysisAuthenticated': ('http://127.0.0.1:18080/health/services', api_token),
        'analysisUnauthenticated': ('http://127.0.0.1:18080/health/services', None),
        'analysisDeployment': ('http://127.0.0.1:18080/health/deployment', api_token),
        'ttsReadiness': ('http://127.0.0.1:18083/health/ready', tts_token),
        'ttsUnauthenticated': ('http://127.0.0.1:18083/health/ready', None),
        'backendHandoff': ('https://api.voice-coaching.site/api/internal/ai/worker-readiness/handoff',
                           env['AI_ANALYSIS_CALLBACK_TOKEN']),
    }
    observed = {name: get(*target) for name, target in paths.items()}
    tts_revision = observed['ttsReadiness'][1].get('revision')
    deployment = observed['analysisDeployment'][1]
    reasons = []
    for name in ('analysisLiveness', 'analysisReadiness', 'analysisAuthenticated',
                 'analysisDeployment', 'ttsReadiness', 'backendHandoff'):
        if observed[name][0] != 200:
            reasons.append(name + '_not_ready')
    for name in ('analysisUnauthenticated', 'ttsUnauthenticated'):
        if observed[name][0] not in (401, 403):
            reasons.append(name + '_not_rejected')
    if tts_revision != env.get('EXAMPLE_TTS_REVISION'):
        reasons.append('backend_tts_revision_mismatch')
    if env.get('RUNPOD_ENDPOINT_URL') != 'https://ai.voice-coaching.site':
        reasons.append('unexpected_analysis_endpoint')
    if env.get('EXAMPLE_TTS_BASEURL') != 'https://ai.voice-coaching.site':
        reasons.append('unexpected_tts_endpoint')
    # Report only explicitly non-secret fields; never dump provider responses.
    report = dict(observedAt=datetime.now(timezone.utc).isoformat(),
                  observedFrom=socket.gethostname(), sshPort=22132,
                  statuses={name: response[0] for name, response in observed.items()},
                  ttsRevision=tts_revision, backendTtsRevision=env.get('EXAMPLE_TTS_REVISION'),
                  deployment={k: deployment[k] for k in (
                      'sourceGitRevision', 'bundleSha256', 'coreManifestSha256',
                      'llmManifestSha256', 'llmLockSha256', 'activeJobs') if k in deployment},
                  ready=not reasons, reasons=reasons,
                  qualityVerified=False, upstreamChanged=False)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if args.require_ready and reasons:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
