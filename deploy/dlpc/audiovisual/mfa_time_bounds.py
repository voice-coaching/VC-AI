"""Reconcile six-decimal TextGrid endpoints with exact PCM sample duration.

Original TextGrid bytes are never rewritten. A maximum one-microsecond
serialization discrepancy may be clipped, with an explicit provenance record.
Larger overhangs, negative times and overlapping tiers remain invalid.
"""
from dataclasses import dataclass
import math

TOLERANCE_SECONDS = 0.000001


@dataclass(frozen=True)
class BoundedTextGrid:
    word_intervals: list
    phone_intervals: list
    tier_names: tuple
    bounds_normalization: dict


def normalize_result(parsed, *, audio_duration_s):
    if not math.isfinite(audio_duration_s) or not 0 < audio_duration_s <= 120:
        raise ValueError('MFA_PCM_DURATION_INVALID')
    audit=dict(revision='mfa-six-decimal-pcm-bounds-v1',toleranceSeconds=TOLERANCE_SECONDS,
               pcmDurationSeconds=audio_duration_s,maximumClippedSeconds=0.0,
               clippedIntervalCount=0,removedBoundaryIntervalCount=0)
    def clip(rows):
        result=[]
        previous=0.0
        for row in rows:
            start,end=row.start_s,row.end_s
            if (not all(math.isfinite(v) for v in (start,end)) or start<0 or start>end or start<previous
                    or end-audio_duration_s>TOLERANCE_SECONDS+1e-12):
                raise ValueError('MFA_TIME_BOUNDS_EXCEEDED')
            previous=end
            if end>audio_duration_s:
                audit['maximumClippedSeconds']=max(audit['maximumClippedSeconds'],end-audio_duration_s)
                audit['clippedIntervalCount']+=1
                end=audio_duration_s
                if start>=end:
                    audit['removedBoundaryIntervalCount']+=1
                    continue
                row=row.model_copy(update={'end_s':end})
            result.append(row)
        return result
    words,phones=clip(parsed.word_intervals),clip(parsed.phone_intervals)
    return BoundedTextGrid(words,phones,parsed.tier_names,audit)


def parse_textgrid(path, *, audio_duration_s):
    from korean_pronunciation_poc.textgrid import parse_textgrid as strict_parse
    parsed=strict_parse(path,audio_duration_s=audio_duration_s+TOLERANCE_SECONDS)
    return normalize_result(parsed,audio_duration_s=audio_duration_s)
