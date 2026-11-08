"""Pinned, locally verified Backend upload grants. No network/key discovery."""
import base64
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

from .http_models import HttpFailure

TYPE = 'voice-coaching.browser-video-grant.v1'
SCOPE = 'video:create upload read cancel'
SOURCE_LIMIT = 256 * 1024**2


def strict_json(raw):
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d: raise ValueError('duplicate key')
            d[k] = v
        return d
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def origin_valid(origin):
    if type(origin) is not str: return False
    u = urlsplit(origin)
    return (u.scheme == 'https' and bool(u.netloc) and u.hostname is not None
            and u.username is None and u.password is None
            and not u.path and not u.query and not u.fragment)


def source_valid(s):
    return (type(s) is dict and set(s) == {'sha256', 'fileSizeBytes', 'mimeType'}
            and type(s['sha256']) is str and re.fullmatch('[a-f0-9]{64}', s['sha256']) is not None
            and type(s['fileSizeBytes']) is int and 0 < s['fileSizeBytes'] <= SOURCE_LIMIT
            and s['mimeType'] in ('video/mp4', 'video/webm', 'video/quicktime'))


def _decode(s):
    if not s or re.fullmatch('[A-Za-z0-9_-]+', s) is None: raise ValueError()
    raw = base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b'=').decode() != s: raise ValueError()
    return raw


class GrantVerifier:
    def __init__(self, environment):
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        self.issuer = environment['AI_BROWSER_VIDEO_ISSUER']
        self.audience = environment['AI_BROWSER_VIDEO_AUDIENCE']
        self.origins = strict_json(environment['AI_BROWSER_VIDEO_ALLOWED_ORIGINS'])
        if (not self.issuer or not self.audience or type(self.origins) is not list
                or not self.origins or any(not origin_valid(o) for o in self.origins)):
            raise ValueError('invalid grant configuration')
        path = Path(environment['AI_BROWSER_VIDEO_PUBLIC_KEYS_FILE'])
        if not path.is_absolute() or path.stat().st_size > 16384: raise ValueError()
        keys = strict_json(path.read_bytes())
        if type(keys) is not dict or not 1 <= len(keys) <= 8: raise ValueError()
        self.keys = {}
        for kid, pem in keys.items():
            if re.fullmatch('[A-Za-z0-9_-]{1,64}', kid) is None: raise ValueError()
            key = load_pem_public_key(pem.encode())
            if not isinstance(key, Ed25519PublicKey): raise ValueError()
            self.keys[kid] = key

    def verify(self, authorization, origin, now=None):
        from cryptography.exceptions import InvalidSignature
        try:
            if not authorization.startswith('Bearer ') or len(authorization) > 8192: raise ValueError()
            a, b, c = authorization[7:].split('.')
            h = strict_json(_decode(a))
            if (type(h) is not dict or set(h) != {'alg', 'typ', 'kid'}
                    or h['alg'] != 'EdDSA' or h['typ'] != TYPE or h['kid'] not in self.keys):
                raise ValueError()
            self.keys[h['kid']].verify(_decode(c), (a + '.' + b).encode('ascii'))
            d = strict_json(_decode(b))
            if (type(d) is not dict or set(d) != {
                    'iss','aud','sub','jti','iat','nbf','exp','scope','origin',
                    'sessionId','clientRequestId','scriptSha256','contentId','source'}): raise ValueError()
            t = time.time() if now is None else now
            if (d['iss'] != self.issuer or d['aud'] != self.audience or d['scope'] != SCOPE
                    or any(type(d[k]) is not int for k in ('iat','nbf','exp'))
                    or not 0 < d['exp'] - d['iat'] <= 300 or d['nbf'] != d['iat']
                    or t >= d['exp'] or d['iat'] > t + 5
                    or type(d['sub']) is not str or re.fullmatch('[1-9][0-9]{0,18}',d['sub']) is None
                    or type(d['sessionId']) is not int or not 0 < d['sessionId'] < 2**63
                    or d['origin'] not in self.origins or d['origin'] != origin
                    or type(d['scriptSha256']) is not str
                    or re.fullmatch('[a-f0-9]{64}',d['scriptSha256']) is None
                    or (d['contentId'] is not None and
                        (type(d['contentId']) is not int or not 0 < d['contentId'] < 2**63))
                    or not source_valid(d['source'])): raise ValueError()
            for k in ('jti','clientRequestId'):
                if type(d[k]) is not str or str(UUID(d[k])) != d[k]: raise ValueError()
            return d
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, InvalidSignature):
            raise HttpFailure(401, 'INVALID_UPLOAD_GRANT') from None


def binding(claims):
    # Refreshing a grant for the same attempt cannot create another execution.
    return {k: claims[k] for k in ('iss','sub','sessionId','origin','clientRequestId',
                                  'scriptSha256','contentId','source')}
