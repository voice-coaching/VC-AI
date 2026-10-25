"""Read existing direct jobs only; no requests, replay, fixtures or workload driver."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


def read(path, limit=1048576):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return {}
    value = json.loads(path.read_bytes())
    return value if isinstance(value, dict) else {}


def duration(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def summarize(root, since):
    cohorts = defaultdict(list)
    unreadable = 0
    pending = 0
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            job = read(folder / 'job.json')
            if not job or job.get('createdAt', 0) < since:
                continue
            if job.get('status') not in ('RESULT_READY', 'FAILED', 'CANCELLED'):
                pending += 1
                continue
            completion = read(folder / 'worker-complete.json')
            result = read(folder / 'result.json')
            observation = read(folder / 'provider-observation.json') or completion.get('providerTiming') or {}
            manifest = observation.get('harnessManifestSha256') or result.get('sourceIdentity', {}).get('harnessManifestSha256')
            cached = observation.get('cachedTokens')
            cache = 'unknown' if type(cached) is not int else 'hit' if cached > 0 else 'miss'
            size = observation.get('requestBytes')
            size_band = None if type(size) is not int else (size // 16384) * 16384
            key = (manifest, observation.get('requestedModel', observation.get('model')),
                   observation.get('reasoningEffort'), observation.get('actualServiceTier'), cache, size_band)
            generation = completion.get('generationStatus') or result.get('coaching', {}).get('generationStatus')
            success = (job['status'] == 'RESULT_READY' and completion.get('failed') is False
                       and generation == 'SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID')
            cohorts[key].append((success, observation, generation, completion.get('timing', {})))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            unreadable += 1
    groups = []
    for key, rows in cohorts.items():
        manifest, model, effort, tier, cache, size_band = key
        seconds = [o['totalSeconds'] for ok, o, _, _ in rows if ok and duration(o.get('totalSeconds'))]
        expression = [t['expressionFinishedAt'] - t['expressionStartedAt'] for ok, _, _, t in rows
                      if ok and duration(t.get('expressionFinishedAt')) and duration(t.get('expressionStartedAt'))
                      and t['expressionFinishedAt'] >= t['expressionStartedAt']]
        tokens = {}
        for field in ('promptTokens', 'completionTokens', 'reasoningTokens', 'outputTextTokens', 'cachedTokens', 'cacheWriteTokens'):
            values = [o[field] for _, o, _, _ in rows if type(o.get(field)) is int and o[field] >= 0]
            tokens[field] = dict(observedCount=len(values), total=sum(values) if values else None)
        groups.append(dict(harnessManifestSha256=manifest, model=model, effort=effort, actualTier=tier,
            cache=cache, requestBytesBandStart=size_band, requestBytesBandWidth=16384,
            terminalCount=len(rows), successfulCount=sum(ok for ok, _, _, _ in rows),
            fallbackCount=sum(g == 'DETERMINISTIC_FALLBACK' for _, _, g, _ in rows),
            lengthCount=sum(o.get('finishReason') == 'length' for _, o, _, _ in rows),
            providerFailedCount=sum(o.get('status') == 'FAILED' for _, o, _, _ in rows),
            transportMeasuredSuccessCount=len(seconds),
            transportP50Seconds=statistics.median(seconds) if seconds else None,
            transportP95Seconds=sorted(seconds)[math.ceil(.95 * len(seconds)) - 1] if seconds else None,
            expressionP50Seconds=statistics.median(expression) if expression else None,
            meetsAbsoluteP50Target=statistics.median(seconds) <= 3.783 if seconds else None,
            initialSampleSufficient=len(seconds) >= 30, usage=tokens))
    return dict(readOnly=True, sinceEpoch=since, pendingCount=pending, unreadableCount=unreadable,
                relativeImprovementConfirmed=False, cohorts=groups)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/workspace/intelligentai/direct-spool'))
    parser.add_argument('--since', type=float, required=True, help='Epoch lower bound for job creation')
    args = parser.parse_args()
    if not math.isfinite(args.since) or args.since < 0:
        parser.error('--since must be a finite, nonnegative epoch')
    print(json.dumps(summarize(args.root, args.since), sort_keys=True))
