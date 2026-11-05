"""Real isolated analysis; no archive, history callback, mock or provider retry."""
import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from voice_coach.direct_analysis.domain import Settings
from voice_coach.direct_analysis.service import AnalysisService
from voice_coach.direct_analysis.store import JobStore, atomic, encoded


def require(condition, code):
    if not condition:
        raise ValueError(code)


async def main(root):
    config = json.loads((root / 'config.json').read_bytes())
    store = JobStore(Settings(root=root / 'spool', enabled=True, deadline_seconds=240))
    service = AnalysisService(store)
    report = dict(lane=config['lane'], cases=[], passed=False)
    async def capture_mfa_failure():
        while True:
            if service.resident and service.resident.generation:
                for path in service.resident.generation.rglob('host_failure.json'):
                    try:
                        atomic(root / 'mfa-host-failure.private.json', path.read_bytes())
                    except FileNotFoundError:
                        pass
            await asyncio.sleep(.05)
    watching = asyncio.create_task(capture_mfa_failure())
    try:
        await service.start()
        for case in config['cases']:
            deadline = time.monotonic() + 300
            while not service.ready() and not service.ready_error and time.monotonic() < deadline:
                await asyncio.sleep(.25)
            require(service.ready(), service.ready_error or 'SMOKE_WARMUP_TIMEOUT')
            started = time.monotonic()
            view = await service.submit(dict(clientAttemptId=str(uuid4()), scriptText=case['scriptText'],
                contentId=None, historyClaimSha256=hashlib.sha256(uuid4().bytes).hexdigest()),
                (root / case['pcm']).read_bytes())
            job = view['jobId']
            row = dict(id=case['id'], fixtureSha256=case['sha256'], jobId=job,
                       executionId=view['executionId'], passed=False)
            report['cases'].append(row)
            deadline = time.monotonic() + 300
            while view['status'] in ('QUEUED', 'RUNNING') and time.monotonic() < deadline:
                await asyncio.sleep(.25)
                view = service.view(job)
            row.update(seconds=round(time.monotonic()-started, 3), status=view['status'])
            folder = store.directory(job)
            diag = folder / 'failure-diagnostic.json'
            if diag.exists():
                row['diagnostic'] = json.loads(diag.read_bytes())
            require(view['status'] == 'RESULT_READY', 'SMOKE_ANALYSIS_FAILED')
            result = view['result']
            row.update(decision=result['decision'], failure=result['failure'], score=result['score'])
            require(result['failure'] is None and result['decision']['status'] == 'ACCEPT', 'SMOKE_CORE_NOT_ACCEPTED')
            coaching = result['coaching']
            row['generationStatus'] = coaching.get('generationStatus')
            require(coaching.get('generationStatus') == 'SCHEMA_AND_STRUCTURAL_SEMANTICS_VALID', 'SMOKE_GPT_NOT_VALID')
            score = result['score']['overallScore']
            require(type(score) in (float, int) and math.isfinite(score) and 0 <= score <= 100, 'SMOKE_SCORE_NOT_NUMERIC')
            require(bool(coaching.get('feedback')), 'SMOKE_FEEDBACK_MISSING')
            provider = json.loads((folder / 'provider-observation.json').read_bytes())
            raw = (folder / 'artifacts/CORE.json').read_bytes()
            raw_sha = hashlib.sha256(raw).hexdigest()
            originals = list((root / 'work' / view['executionId'] / 'core').glob('*/release-output/candidate/results/*.json'))
            require(len(originals) == 1 and originals[0].read_bytes() == raw, 'SMOKE_CORE_BYTES_CHANGED')
            require(provider['postAttempts'] == 1 and provider['httpStatus'] == 200, 'SMOKE_PROVIDER_CALL_COUNT')
            require(provider['rawCoreMessageSha256'] == raw_sha, 'SMOKE_RAW_CORE_MESSAGE_CHANGED')
            row.update(coreSha256=raw_sha, postAttempts=provider['postAttempts'],
                       rawCoreMessageSha256=provider['rawCoreMessageSha256'], passed=True)
            print(json.dumps(dict(lane=config['lane'], case=case['id'], passed=True, seconds=row['seconds'], score=score)), flush=True)
        report['passed'] = True
    except Exception as error:
        text = str(error)
        import re
        report['error'] = text if re.fullmatch('[A-Z][A-Z0-9_]{0,100}', text) else type(error).__name__
        # Preserve private MFA diagnostics before resident generation cleanup.
        if service.resident and service.resident.generation:
            generation = service.resident.generation
            for path in generation.rglob('host_failure.json'):
                atomic(root / 'mfa-host-failure.private.json', path.read_bytes())
    finally:
        atomic(root / 'report.json', encoded(report))
        watching.cancel()
        await asyncio.gather(watching, return_exceptions=True)
        await service.close()
        store.close()
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(main(args.root.resolve(strict=True))) else 1)
