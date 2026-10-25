"""Read retained MFA receipts and spans; never submit or replay a recording."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics


def read(path, limit=16777216):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:return {}
    value = json.loads(path.read_bytes())
    return value if isinstance(value, dict) else {}


def valid(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def stats(values):
    values = sorted(x for x in values if valid(x))
    return dict(count=len(values), p50Seconds=statistics.median(values) if values else None,
        p95Seconds=values[math.ceil(.95*len(values))-1] if values else None)


def phase_priority(phases, trace_count):
    # Rank exclusive observations only. Parent/child wall times are not additive,
    # and a missing exclusive measurement must never become a zero-second sample.
    ranking = []
    preparation = {'cli_context_create', 'configuration_load', 'third_party_check',
                   'parameter_parse', 'dictionary_resolve', 'g2p_resolve', 'aligner_construct',
                   'prepared_dependencies_verify'}
    workflow = {'setup', 'setup_database', 'load_corpus', '_load_corpus_from_source_mp',
                'dictionary_setup', 'initialize_jobs', 'normalize_text', 'generate_features',
                'compile_train_graphs', 'align_utterances', 'alignment_workflow',
                'initialize_database', 'setup_acoustic_model', '_load_corpus', 'get_tokenizers',
                'apply_phonological_rules', 'calculate_disambiguation', 'write_lexicon_information',
                'build_lexicon_compiler', 'mfcc', 'combine_feats', 'calc_cmvn',
                'generate_final_features', '_write_feats', 'create_corpus_split'}
    for name, phase in phases.items():
        observed = phase['exclusive']
        if not observed['count']:continue
        stage = 'M1' if name in preparation else 'M2' if name in workflow else 'M0_REVIEW'
        ranking.append(dict(name=name, reviewStage=stage, exclusive=observed,
            observedRequestCount=phase['inclusive']['count'],
            missingExclusiveRequestCount=phase['inclusive']['count']-observed['count']))
    ranking.sort(key=lambda row: (-row['exclusive']['p50Seconds'], row['name']))
    return dict(status='OBSERVATION_CANDIDATES' if ranking else 'WAITING_FOR_COMPLETE_TRACE',
        completeTraceCount=trace_count, initialSampleSufficient=trace_count >= 30,
        optimizationApproved=False, rankedExclusivePhases=ranking,
        semantics='review candidates only; medians across phases are not additive; no automatic cache or thread change')


def summarize(root, since):
    cohorts = defaultdict(list)
    pending = unreadable = 0
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():continue
        try:
            job = read(folder / 'job.json')
            if not job or job.get('createdAt', 0) < since:continue
            if job.get('status') not in {'RESULT_READY', 'FAILED', 'CANCELLED'}:
                pending += 1
                continue
            core = read(folder / 'artifacts/CORE.json')
            completion = read(folder / 'worker-complete.json')
            result = read(folder / 'result.json')
            receipt = core.get('mfa_receipt') or {}
            worker = receipt.get('worker') or {}
            trace = worker.get('mfaPerformance') or {}
            config = worker.get('effective_config') or {}
            resources = trace.get('resources') or {}
            manifest = result.get('sourceIdentity', {}).get('coreManifestSha256')
            size = job.get('fileSizeBytes')
            evidence = core.get('pronunciation_evidence')
            phonemes = len(evidence) if isinstance(evidence, list) else None
            key = (manifest, receipt.get('ordinal'),
                (size // 64000)*64000 if type(size) is int else None,
                (phonemes // 10)*10 if phonemes is not None else None,
                config.get('NUM_JOBS'), config.get('BLAS_NUM_THREADS'),
                resources.get('cgroup', {}).get('cpu.max'),
                receipt.get('restart_wait_s', 0) > 0)
            ok = (job['status'] == 'RESULT_READY' and core.get('input_validation', {}).get('status') == 'ACCEPT'
                  and worker.get('status') == 'ok' and worker.get('db_close_probe') == 'PASS'
                  and worker.get('lifecycle_gate', {}).get('status') == 'PASS')
            timing = completion.get('coreTiming') or {}
            start, end = timing.get('mfa_start'), timing.get('mfa_done')
            call = end-start if valid(start) and valid(end) and end >= start else None
            cohorts[key].append((ok, call, worker, trace, receipt))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):unreadable += 1
    groups = []
    for key, rows in cohorts.items():
        manifest, ordinal, size, phonemes, jobs, blas, quota, restarted = key
        phases = defaultdict(lambda: dict(inclusive=[], exclusive=[], calls=0))
        trace_count = 0
        for ok, _, _, trace, _ in rows:
            if not ok or trace.get('complete') is not True:continue
            trace_count += 1
            per_request = defaultdict(lambda: dict(inclusive=0.0, exclusive=0.0, calls=0,
                                                 exclusive_complete=True))
            for event in trace.get('spans', []):
                name = event.get('name')
                if not isinstance(name, str) or len(name) > 80:continue
                if not valid(event.get('wallSeconds')):continue
                part = per_request[name]
                part['inclusive'] += event['wallSeconds']
                if valid(event.get('exclusiveWallSeconds')):part['exclusive'] += event['exclusiveWallSeconds']
                else:part['exclusive_complete'] = False
                part['calls'] += 1
            for name, part in per_request.items():
                phases[name]['inclusive'].append(part['inclusive'])
                if part['exclusive_complete']:phases[name]['exclusive'].append(part['exclusive'])
                phases[name]['calls'] += part['calls']
        whole = stats([call for ok, call, _, _, _ in rows if ok])
        phase_stats = {name: dict(calls=v['calls'], inclusive=stats(v['inclusive']),
                                 exclusive=stats(v['exclusive'])) for name, v in phases.items()}
        groups.append(dict(coreManifestSha256=manifest, ordinal=ordinal,
            audioFileBytesBandStart=size, audioFileBytesBandWidth=64000,
            phonemeCountBandStart=phonemes, phonemeCountBandWidth=10,
            mfaJobs=jobs, mfaBlasThreads=blas, cpuQuota=quota, restarted=restarted,
            terminalCount=len(rows), mfaSuccessfulCount=sum(ok for ok, *_ in rows),
            missingReceiptCount=sum(not w for _, _, w, _, _ in rows), completeTraceCount=trace_count,
            mfaCall=whole, initialSampleSufficient=whole['count'] >= 30,
            meetsEightSecondTarget=whole['p50Seconds'] <= 8 if whole['p50Seconds'] is not None else None,
            workerRequest=stats([w.get('request_total_s') for ok, _, w, _, _ in rows if ok]),
            alignmentWorkflow=stats([w.get('alignment_workflow_s') for ok, _, w, _, _ in rows if ok]),
            feature=stats([w.get('feature_wall_s') for ok, _, w, _, _ in rows if ok]),
            phases=phase_stats, optimizationReview=phase_priority(phase_stats, trace_count)))
    return dict(readOnly=True, analysisTriggered=False, sinceEpoch=since,
        pendingCount=pending, unreadableCount=unreadable, relativeImprovementConfirmed=False,
        optimizationReviewStatus='OBSERVATIONS_AVAILABLE' if any(
            g['optimizationReview']['rankedExclusivePhases'] for g in groups) else 'WAITING_FOR_COMPLETE_TRACE',
        semantics='phase times are per-request totals by label; nested or concurrent labels must not be summed',
        cohorts=groups)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/workspace/intelligentai/direct-spool'))
    parser.add_argument('--since', type=float, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.since) or args.since < 0:parser.error('invalid epoch')
    print(json.dumps(summarize(args.root, args.since), sort_keys=True))
