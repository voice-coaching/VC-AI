"""Validated, process-scoped settings for the HTTP transport."""
from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit
from .http_models import HttpFailure, object_key


@dataclass(frozen=True)
class HttpSettings:
    api_token: str = field(repr=False)
    callback_token: str = field(repr=False)
    callback_base: str
    prefix: str
    readiness_key: str
    readiness_sha: str
    readiness_size: int

    @classmethod
    def from_environment(cls, env):
        try:
            if env.get("AI_ANALYSIS_HTTP_BOOTSTRAP_READY") != "true":
                raise ValueError()
            api, callback = env["AI_ANALYSIS_API_TOKEN"], env["AI_ANALYSIS_CALLBACK_TOKEN"]
            if (api == callback or any(not re.fullmatch(r"[A-Za-z0-9._~+/=-]{32,512}", v)
                                      or v.startswith("rpa_") for v in (api, callback))):
                raise ValueError()
            base = env["AI_ANALYSIS_CALLBACK_BASE_URL"].rstrip("/")
            url = urlsplit(base)
            if (url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.path or url.query or url.fragment):
                raise ValueError()
            prefix = env["AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX"]
            key = env["AI_ANALYSIS_READINESS_OBJECT_KEY"]
            sha = env["AI_ANALYSIS_READINESS_OBJECT_SHA256"]
            size = int(env["AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES"])
            if not re.fullmatch(r"[0-9a-f]{64}", sha) or not 0 < size <= 65536:
                raise ValueError()
            object_key(key, prefix)
            for name in ("AI_ANALYSIS_OBJECT_STORAGE_BUCKET", "AI_ANALYSIS_OBJECT_STORAGE_REGION"):
                if not env.get(name):
                    raise ValueError()
            return cls(api, callback, base, prefix, key, sha, size)
        except (KeyError, ValueError, TypeError, HttpFailure):
            raise ValueError("http_settings_incomplete_or_invalid") from None
