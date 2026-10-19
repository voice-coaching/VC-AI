from __future__ import annotations
import argparse, dataclasses, hashlib, importlib.util, json, os, shutil, sys, time, traceback
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'source'
RECOVERY = Path('/workspace/intelligentai/validation/finish_20260916/latency_optimization_20260921T044013_2c3be9/mfa_startup_recovery_20260921T065201_ac461c')
POLICY = Path('/workspace/intelligentai/validation/p1_native_demo_refinement_20260928T1310KST/contract/refined_p1_policy_contract.json')
MODEL = Path('/workspace/intelligentai/validation/native_korean_phone_eval_20260928T1109KST/native_model/snapshot')
POLICY_SHA = 'c3c4a11927b748c4998d3cd0a5a578f9e77faaf4863780be0984edaa95a11d50'
WEIGHTS_SHA = 'b2ec663558606d651a718fa1eaa20c6d504df173725ecbc66df74c22267e2d68'
NATIVE_SRC = SOURCE / 'scripts/canonical_native'
sys.path.insert(0, str(SOURCE / 'src'))
sys.path.insert(0, str(NATIVE_SRC))
from native_infer import readwav, collapse
import evaluate_smoke as p1
from korean_pronunciation_poc import pipeline as pipeline_mod
from korean_pronunciation_poc.ai_core import AICoreConfig, build_ai_core_result
from korean_pronunciation_poc.config import PocConfig
from korean_pronunciation_poc.g2p import G2pService
from korean_pronunciation_poc.input_validation import InputValidationResultV1, classify_pipeline_run, reject_if_canonical_pcm_all_zero
from korean_pronunciation_poc.errors import ErrorCode, PipelineError, PipelineFailure, PipelineStage
from korean_pronunciation_poc.pronunciation_phone_contract import build_pronunciation_phone_contract
from korean_pronunciation_poc.schemas import AnalysisRequest
import numpy as np
import torch
from gpu_budget import configure_torch, gpu_slot
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


@lru_cache(maxsize=1)
def checked_contract():
    started = time.perf_counter()
    cpu_started = time.process_time()
    assert sha(POLICY) == POLICY_SHA, 'P1_POLICY_SHA_MISMATCH'
    weights_started, weights_cpu = time.perf_counter(), time.process_time()
    assert sha(MODEL / 'pytorch_model.bin') == WEIGHTS_SHA, 'NATIVE_WEIGHT_SHA_MISMATCH'
    print(json.dumps(dict(event='native_weight_hash', elapsedSeconds=time.perf_counter()-weights_started,
        cpuSeconds=time.process_time()-weights_cpu, fileBytes=(MODEL/'pytorch_model.bin').stat().st_size)), flush=True)
    z = json.loads(POLICY.read_text())
    assert z['selected_form'] == 'P1_UNIQUE_CONSONANT_RUN_MARGIN_V1'
    assert z['comparison_operator'] == '>='
    assert z['device'].startswith('CPU float32')
    assert z['model_revision'] == 'e26ff9dfb62169acf445d0060ef56863c018b20e'
    p1.THETA = z['theta_raw_ctc_logit_margin']
    print(json.dumps(dict(event='native_contract_prepare', elapsedSeconds=time.perf_counter()-started, cpuSeconds=time.process_time()-cpu_started)), flush=True)
    return z


def prepare_mfa_runtime(output: Path):
    adapter = SOURCE / 'scripts/canonical_mfa'
    live = output / 'mfa_runtime'
    assert not live.exists(), 'RUNTIME_PATH_EXISTS'
    shutil.copytree(adapter, live)
    local = output / 'scratch'
    for d in (live/'logs', live/'reports', live/'results', local/'guard', local/'tmp/pipeline', local/'cache'):
        d.mkdir(parents=True, exist_ok=True)
    pf = json.loads((live/'preflight.json').read_text())
    pf.update(local=str(local), source=str(SOURCE), recovery_root=str(RECOVERY), direct_candidate=True)
    (live/'preflight.json').write_text(json.dumps(pf, indent=2) + '\n')
    # Scoped to this candidate and its scratch, including subprocesses.
    guard = '''import sys,os,pathlib\nROOT=pathlib.Path(%r).resolve()\nLOCAL=pathlib.Path(os.environ["RECOVERY_LOCAL"]).resolve()\ndef audit(event,args):\n if event=="socket.connect":\n  address=args[1]\n  if isinstance(address,tuple) and address[0] not in ("127.0.0.1","::1","localhost"):raise PermissionError("offline")\n if event=="socket.getaddrinfo" and args[0] not in ("127.0.0.1","::1","localhost",None):raise PermissionError("offline DNS")\n if event=="open" and isinstance(args[0],(str,bytes,os.PathLike)):\n  mode=args[1] or "";flags=args[2];write=any(c in str(mode) for c in "wax+") or isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC)\n  if write:\n   p=pathlib.Path(os.fsdecode(args[0])).resolve()\n   if str(p) not in ("/dev/null","/dev/tty") and not (p.is_relative_to(ROOT) or p.is_relative_to(LOCAL)):raise PermissionError("write outside candidate: "+str(p))\nsys.addaudithook(audit)\nos.environ["RECOVERY_GUARD_ACTIVE"]="1"\n''' % str(output)
    (local/'guard/sitecustomize.py').write_text(guard)
    return live, local


class CaptureG2P:
    def __init__(self, state, content_config):
        self.state = state
        self.content_config = content_config
        self.real = load_g2p()
    def convert(self, script):
        pron = self.real.convert(script)
        contract = build_pronunciation_phone_contract(script=script, pronunciation=pron)
        self.state.update(script=script, pronunciation=pron, phone_contract=contract,
                          expected_phone_done=time.perf_counter())
        return pron


def native_forward(model, feature_extractor, vocab, wav: Path):
    t = time.perf_counter()
    x, n = readwav(wav)
    inp = feature_extractor(x, sampling_rate=16000, return_tensors='pt')
    with gpu_slot(), torch.inference_mode():
        arr = model(inp.input_values.to('cuda:0')).logits[0].cpu().numpy().astype(np.float32, copy=False)
    ids = arr.argmax(axis=1).tolist()
    return {'arr': arr, 'ids': ids, 'runs': collapse(ids, vocab, len(ids)), 'sample_count': n,
            'start': t, 'done': time.perf_counter()}


def position_outputs(phone_contract, word_intervals, phone_intervals, native, vocab_id):
    expected = [{**x.model_dump(mode='json'), 'expected_phone': x.phone} for x in phone_contract.detector_metadata]
    info = {'expected': expected, 'word_intervals': word_intervals, 'phone_intervals': phone_intervals}
    loc, status, words = p1.get_locations(info)
    rows = []
    for x in expected:
        if x['expected_phone'] not in p1.CONSONANTS:
            continue
        ix = int(x['expected_index']); wi = int(x['original_word_index'])
        interval = loc.get(ix)
        decision = p1.evaluate_position(x, interval, native['runs'], native['arr'], vocab_id)
        scores = p1.score_local(interval, native['arr'], x['expected_phone'], vocab_id)
        if int(decision['policy_warning']):
            state = 'CLEAR_CONTRAST'
        elif decision['decision_reason'] == 'BELOW_FROZEN_MARGIN' and decision.get('margin', 0) > 0:
            state = 'AMBIGUOUS_CONTRAST'
        elif decision['decision_reason'] in {'LOCATION_UNRESOLVED','NO_UNIQUE_CONSONANT_RUN','ONSET_TOKEN_ROLE_CONFLICT','CODA_TOKEN_ROLE_CONFLICT'}:
            state = 'INSUFFICIENT_EVIDENCE'
        else:
            state = 'NO_FEEDBACK'
        w = words.get(wi, {})
        rows.append({'expected_index': ix, 'word_index': wi, 'word': x['original_word_text'],
                     'expected_phone': x['expected_phone'], 'role': x['role'],
                     'mfa_ipa': interval['label'] if interval else None,
                     'phone_start_s': interval['start_s'] if interval else None,
                     'phone_end_s': interval['end_s'] if interval else None,
                     'word_start_s': w.get('start_s'), 'word_end_s': w.get('end_s'),
                     'localization_status': status.get(ix, 'UNRESOLVED'),
                     'presentation_state': state, 'llm_visible': state == 'CLEAR_CONTRAST',
                     **decision, **scores})
    return rows


def project_llm(rows):
    out = []
    for x in rows:
        if not x['llm_visible']:
            continue
        if x['word_start_s'] is None or x['word_end_s'] is None:
            raise RuntimeError('VISIBLE_ITEM_WITHOUT_WORD_INTERVAL')
        out.append({'word': x['word'], 'word_index': x['word_index'],
                    'start_ms': round(x['word_start_s']*1000, 3),
                    'end_ms': round(x['word_end_s']*1000, 3),
                    'feedback_type': x['presentation_state'],
                    'expected_phone': x['expected_phone'],
                    'comparison_phone': x['native_phone'],
                    'message_scope': 'PRONUNCIATION_REVIEW'})
    return out


@lru_cache(maxsize=1)
def load_g2p():
    return G2pService()


@lru_cache(maxsize=1)
def load_native():
    checked_contract()
    configure_torch(torch)
    if not torch.cuda.is_available():
        raise RuntimeError('DLPC_CUDA_UNAVAILABLE')
    vr = json.loads((MODEL/'vocab.json').read_text()); vr.update(json.loads((MODEL/'added_tokens.json').read_text()))
    vocab = {str(v): k for k, v in vr.items()}
    fe = Wav2Vec2FeatureExtractor.from_pretrained(str(MODEL), local_files_only=True)
    with gpu_slot():
        model = Wav2Vec2ForCTC.from_pretrained(str(MODEL), local_files_only=True).to('cuda:0').float().eval()
    return vr, vocab, fe, model


def run_batch(cases, output: Path, parallel: bool, blackbox_socket: str | None = None, blackbox_runtime: str | None = None, resident=None):
    if not blackbox_socket or not blackbox_runtime:
        raise RuntimeError('BLACKBOX_SIDECAR_REQUIRED_NO_INPROCESS_FALLBACK')
    output = output.resolve()
    contract = checked_contract()
    content_config_path = ROOT / 'calibration/content_gate_removed_v1.json'
    content_config = json.loads(content_config_path.read_text())
    assert content_config == {'schema': 'content_gate_removed_v1', 'enabled': False}
    content_config_sha = sha(content_config_path)
    if parallel:
        raise ValueError('PARALLEL_NATIVE_MFA_DISABLED_AFTER_PRESERVED_120S_SYSTEM_FAILURE')
    assert len(cases) <= 3 and len(cases) == len({x['id'] for x in cases})
    output.mkdir(parents=True, exist_ok=False)
    (output/'results').mkdir()
    (output/'prepared_cache').mkdir()
    if blackbox_socket:
        assert blackbox_runtime
        live = Path(blackbox_runtime).resolve()
        local = Path(json.loads((live/'preflight.json').read_text())['local'])
        from blackbox_client import BlackBoxMFAClient
    else:
        live, local = prepare_mfa_runtime(output)
        from demo_rpc import MFAClient
    sys.path.insert(0, str(live))
    from demo_common import MFA_PY, APP_PY
    assert Path(APP_PY) == Path(sys.executable)
    assert Path(MFA_PY).is_file()
    config_raw = json.loads((RECOVERY/'configs/candidate_alignment_config.json').read_text())
    config_raw['final_pronunciation'] = {'enabled': False}
    config_raw['pronunciation'] = {'enabled': False}
    config_raw['paths'].update(project_root=str(Path('/') if blackbox_socket else ROOT), work_dir=str(local/'tmp/pipeline/work'), output_dir=str(output/'scratch/output') if blackbox_socket else str(local/'output'))
    cfg = PocConfig.model_validate(config_raw)
    (output/'effective_config.json').write_text(json.dumps({'argv': sys.argv, 'cwd': os.getcwd(), 'python': sys.executable,
        'source': str(SOURCE), 'policy_sha': POLICY_SHA, 'policy_threshold_full_precision': contract['theta_raw_ctc_logit_margin'],
        'operator': contract['comparison_operator'], 'model': str(MODEL), 'weights_sha': WEIGHTS_SHA,
        'device': 'cuda:0', 'dtype': 'float32', 'parallel': parallel,
        'blackbox_socket': blackbox_socket, 'blackbox_runtime': blackbox_runtime,
        'content_gate_config':str(content_config_path), 'content_gate_config_sha256':content_config_sha,
        'mfa_config': json.loads((live/'configs/supervisor.json').read_text()),
        'pipeline_config': cfg.model_dump(mode='json')}, ensure_ascii=False, indent=2)+'\n')
    if resident is None:
        raise RuntimeError('NATIVE_RESIDENT_REQUIRED_NO_ONESHOT_FALLBACK')
    native_load_start = time.perf_counter()
    vr, vocab, fe, model = resident['native']
    native_load_done = time.perf_counter()
    client, version, startup = resident['client'], resident['version'], resident['startup']
    assert version['version'] == '3.4.1'
    assert startup['ready']['direct_candidate'] and startup['ready']['aligner_class'].endswith('DirectAligner') and startup['ready']['queue_adapter'].get('aliases')
    spec = importlib.util.spec_from_file_location('korean_pronunciation_poc.canonical_mfa', RECOVERY/'mfa_preflight_optimization_20260922T043055Z_0d7f6a/candidate/mfa.py')
    mfa_opt = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mfa_opt; spec.loader.exec_module(mfa_opt)
    active = {}
    def mfa_runner(argv, **kw):
        args = list(argv)[1:]
        if args == ['version']:
            import subprocess
            return subprocess.CompletedProcess(argv, 0, version['version'], '')
        marks = active['marks']; state = active['state']
        marks['mfa_start'] = time.perf_counter()
        if blackbox_socket:
            wav = next(Path(args[8]).glob('*.wav'))
            lab = wav.with_suffix('.lab')
            assert sha(wav) == state['prepared_sha']
            rec = client.call('resident', argv=args, request_id=active['request_id'],
                canonical_wav_path=str(state['stable_wav']), canonical_wav_sha256=state['prepared_sha'],
                normalized_transcript=lab.read_text(), transcript_sha256=sha(lab),
                audio_duration_s=state['audio'].metadata.duration_s)
        else:
            rec = client.call('resident', argv=args)
        marks['mfa_done'] = time.perf_counter()
        state['mfa_receipt'] = rec
        out = Path(args[10]); out.mkdir(parents=True, exist_ok=True)
        wavs = list(Path(args[8]).glob('*.wav'))
        assert len(wavs) == 1
        shutil.copyfile(rec['output'], out/(wavs[0].stem+'.TextGrid'))
        import subprocess
        return subprocess.CompletedProcess(argv, 0, '', '')
    preflight = mfa_opt.MfaWorkerPreflight(cfg, runner=mfa_runner, expected_version='3.4.1', runtime_paths=[Path(MFA_PY), live/'resident_worker.py', live/'mfa_sidecar.py'])
    records = []
    try:
        for case in cases:
            state = {}; marks = {'request_start': time.perf_counter()}
            request_id = case['id']; audio_path = Path(case['audio']); script_text = case['script']
            def preparer(*a):
                result = pipeline_mod.prepare_audio(*a)
                reject_if_canonical_pcm_all_zero(result)
                marks['decode_done'] = time.perf_counter(); marks['input_gate_cheap_done'] = marks['decode_done']
                state['audio'] = result
                state['prepared_sha'] = sha(result.prepared_wav)
                stable_wav = output/'prepared_cache'/(request_id+'.wav')
                shutil.copyfile(result.prepared_wav, stable_wav)
                assert sha(stable_wav) == state['prepared_sha']
                state['stable_wav'] = stable_wav
                try:
                    state['native'] = native_forward(model, fe, vocab, stable_wav)
                except Exception as exc:
                    raise PipelineFailure(PipelineError(
                        code=ErrorCode.ACOUSTIC_ANALYSIS_FAILED, stage=PipelineStage.ACOUSTIC,
                        message_ko='동결 native 음소 분석을 실행할 수 없습니다.',
                        details={'exception_type': type(exc).__name__})) from exc
                return result
            active.update(marks=marks, state=state, request_id=request_id)
            def bound_mfa(*a, **kw):
                return mfa_opt.run_mfa(*a, runner=mfa_runner, preflight=preflight, **kw)
            def capture_g2p(): return CaptureG2P(state, content_config)
            base_deps = pipeline_mod.PipelineDependencies()
            def provenance(runtime_cfg, *args, **kw):
                z = runtime_cfg.model_dump(mode='json')
                z['paths'].update(project_root=str(SOURCE), work_dir=str(SOURCE/'metadata_only_unused_work'),
                                  output_dir=str(SOURCE/'metadata_only_unused_output'))
                return base_deps.collect_provenance(PocConfig.model_validate(z), *args, **kw)
            def timed_prosody(*args, **kwargs):
                marks['prosody_start'] = time.perf_counter()
                try:
                    return base_deps.extract_prosody(*args, **kwargs)
                finally:
                    marks['prosody_end'] = time.perf_counter()
            deps = dataclasses.replace(base_deps, prepare_audio=preparer, g2p_factory=capture_g2p,
                                       run_mfa=bound_mfa, extract_prosody=timed_prosody,
                                       collect_provenance=provenance)
            run = pipeline_mod.AnalysisPipeline(cfg, platform_role='unknown', dependencies=deps).analyze(
                AnalysisRequest(script=script_text, audio_path=audio_path, source='upload'))
            marks['prosody_done'] = time.perf_counter()  # Full core analysis returned; prosody-only duration is unavailable.
            marks['expected_phone_done'] = state.get('expected_phone_done')
            native = state.get('native')
            marks['native_model_start'] = native['start'] if native else None
            marks['native_model_done'] = native['done'] if native else None
            validation = classify_pipeline_run(run, audio_path=audio_path)
            content_measure = state.get('content_measure')
            content_decision = state.get('content_decision')
            marks['content_gate_done'] = state.get('content_gate_done')
            core = build_ai_core_result(run=run, config=AICoreConfig(pipeline_config=cfg, platform_role='unknown'))
            core_json = core.model_dump(mode='json')
            legacy = run.result.model_dump(mode='json')
            rows = []
            if validation.status in {'ACCEPT','INCONCLUSIVE'} and native is not None and 'phone_contract' in state and legacy.get('word_intervals'):
                rows = position_outputs(state['phone_contract'], legacy['word_intervals'], legacy['phone_intervals'], native, vr)
            marks['pronunciation_policy_done'] = time.perf_counter()
            llm_items = project_llm(rows)
            feedback_delivery_allowed = validation.status in {'ACCEPT','INCONCLUSIVE'} and core.status == 'ok'
            if not feedback_delivery_allowed:
                llm_items = []
            marks['presentation_done'] = time.perf_counter()
            envelope = {'schema_version': 'canonical_ai_predeploy_v1', 'request_id': request_id,
                'input_validation': validation.model_dump(mode='json'),
                'content_gate': {'config_sha256':content_config_sha,'measure':content_measure,'decision':content_decision},
                'feedback_delivery_allowed':feedback_delivery_allowed,
                'core_result': core_json, 'pronunciation_feedback_source': 'REFINED_P1_ONLY',
                'pronunciation_evidence': rows,
                'llm_facing_evidence': {'schema_version': 'llm_pronunciation_evidence_v1', 'pronunciation_items': llm_items,
                                        'prosody': core_json['prosody'] if feedback_delivery_allowed else None},
                'native': {'device': 'cuda:0', 'dtype': 'float32', 'model_revision': contract['model_revision'],
                           'weights_sha256': WEIGHTS_SHA, 'ctc_runs': native['runs'] if native else None},
                'mfa_receipt': state.get('mfa_receipt'), 'timing_marks_monotonic': marks,
                'prepared_wav_sha256': state.get('prepared_sha'),
                'source_audio_sha256': sha(audio_path), 'expected_phone_count': len(state['phone_contract'].detector_metadata) if 'phone_contract' in state else None}
            from canonical_native.seungun_gop import extract as extract_gop
            marks['gop_start'] = time.perf_counter()
            if native is not None and 'phone_contract' in state:
                try:
                    envelope['phonemeAssessment'] = extract_gop(native,
                        [row.model_dump(mode='json') for row in state['phone_contract'].full_metadata], vr)
                except (ValueError, RuntimeError) as exc:
                    envelope['phonemeAssessment'] = {'revision': 'gop-ctc-af-sd-native-v1',
                        'status': 'UNAVAILABLE', 'errorCode': type(exc).__name__}
            marks['gop_done'] = time.perf_counter()
            marks['core_json_done'] = time.perf_counter()
            envelope['stage_durations_s'] = {k: (v-marks['request_start'] if isinstance(v,(float,int)) else None) for k,v in marks.items()}
            envelope['stage_durations_s']['total_llm_excluded'] = marks['core_json_done']-marks['request_start']
            dest = output/'results'/(request_id+'.json')
            assert not dest.exists()
            dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=str)+'\n')
            records.append({'id':request_id, 'status': validation.status, 'core_status':core.status,
                            'positions':len(rows), 'clear':sum(r['presentation_state']=='CLEAR_CONTRAST' for r in rows),
                            'ambiguous_internal':sum(r['presentation_state']=='AMBIGUOUS_CONTRAST' for r in rows),
                            'llm_items':len(llm_items), 'mfa_request_id':state.get('mfa_receipt',{}).get('worker',{}).get('request_id'),
                            'total_s':marks['core_json_done']-marks['request_start'], 'result_sha256':sha(dest)})
            print(json.dumps({'event':'REQUEST_DONE', **records[-1]},ensure_ascii=False),flush=True)
    finally:
        pass  # Resident owner closes the client at the generation boundary.
    report = {'status':'COMPLETED','native_model_load_s':native_load_done-native_load_start,
              'mfa_startup':startup,'requests':records,'phase3_fast_path_actual': all(
                  json.loads((output/'results'/(r['id']+'.json')).read_text())['mfa_receipt']['worker']['workflow_executed']
                  if json.loads((output/'results'/(r['id']+'.json')).read_text())['mfa_receipt'] else False for r in records)}
    (output/'batch_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cases',required=True);ap.add_argument('--output',required=True);ap.add_argument('--parallel',action='store_true');ap.add_argument('--blackbox-socket');ap.add_argument('--blackbox-runtime');args=ap.parse_args()
    cases=json.loads(Path(args.cases).read_text())
    report=run_batch(cases,Path(args.output),args.parallel,args.blackbox_socket,args.blackbox_runtime)
    print(json.dumps({'event':'BATCH_DONE','fast_path':report['phase3_fast_path_actual']},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
