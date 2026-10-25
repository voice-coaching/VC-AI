"""Allowlisted provider metadata, independent of generated content validation."""
from __future__ import annotations

import re

from .canonical_bridge import CanonicalIntegrationError, _json


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def response_observation(raw: bytes) -> dict:
    """Missing/invalid optional metadata never invalidates a valid completion."""
    result = dict(returnedModel=None, actualServiceTier=None, finishReason=None,
                  promptTokens=None, completionTokens=None, reasoningTokens=None,
                  cachedTokens=None, cacheWriteTokens=None, outputTextTokens=None)
    try:
        body = _json(raw)
    except (CanonicalIntegrationError, ValueError, TypeError, RecursionError):
        return result
    if not isinstance(body, dict):
        return result
    model = body.get('model')
    if isinstance(model, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,100}', model):
        result['returnedModel'] = model
    tier = body.get('service_tier')
    if tier in ('default', 'flex', 'scale', 'priority', 'fast'):
        result['actualServiceTier'] = tier
    choices = body.get('choices')
    if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
        reason = choices[0].get('finish_reason')
        if reason in ('stop', 'length', 'tool_calls', 'content_filter', 'function_call'):
            result['finishReason'] = reason
    usage = body.get('usage')
    if isinstance(usage, dict):
        result['promptTokens'] = _count(usage.get('prompt_tokens'))
        result['completionTokens'] = _count(usage.get('completion_tokens'))
        for name, field, target in (
            ('prompt_tokens_details', 'cached_tokens', 'cachedTokens'),
            ('prompt_tokens_details', 'cache_write_tokens', 'cacheWriteTokens'),
            ('completion_tokens_details', 'reasoning_tokens', 'reasoningTokens'),
            ('completion_tokens_details', 'text_tokens', 'outputTextTokens'),
        ):
            details = usage.get(name)
            if isinstance(details, dict):
                result[target] = _count(details.get(field))
    return result
