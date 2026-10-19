"""Isolated resident MFA3.4.1 prototype: imports persist, request state does not."""
import time
PROCESS_ENTRY=time.perf_counter()
import os,sys,json,gc,copy,shutil,signal,traceback,contextlib,importlib.util,importlib.metadata as md
from pathlib import Path
O=Path(__file__).resolve().parent
L=Path(json.loads((O/'preflight.json').read_text())['local']);R=Path(json.loads((O/"preflight.json").read_text())["recovery_root"])
import lifecycle_debug as lifecycle
lifecycle.install(O,L)
GEN=int(os.environ.get('MFA_GENERATION','1'));assert GEN in (1,2,3,4)
PROTOCOL=None
SEEN=set()
MODEL_ID='MontrealCorpusTools/korean_mfa@0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd'

def validate(req):
    import re
    rid=req.get('request_id')
    if not isinstance(rid,str) or not re.fullmatch(rf'g{GEN}_r[0-2]_[a-f0-9]{{12}}',rid):raise ValueError('invalid request id')
    if rid in SEEN:raise ValueError('repeated request id')
    argv=req.get('argv')
    if not isinstance(argv,list) or len(argv)!=11 or argv[0]!='align_hf' or argv[1]!='--temporary_directory' or argv[3:8]!=['--single_speaker','--no_tokenization','--use_g2p','--num_jobs','1'] or argv[9]!=MODEL_ID:raise ValueError('contract mismatch')
    root=L/'tmp'/'requests'/rid
    for p in (argv[2],argv[8],argv[10]):
        path=Path(p)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.resolve()==root.resolve():raise ValueError('request path escape')
    if Path(argv[10]).exists():raise ValueError('output already exists')
    files=list(Path(argv[8]).iterdir())
    if len(files)!=2 or sorted(p.suffix for p in files)!=['.lab','.wav'] or any(p.is_symlink() for p in files):raise ValueError('one WAV/lab required')
    if len(SEEN)>=3:raise ValueError('request budget exceeded')
    return rid,root,argv

def snapshot():
    return lifecycle.snapshot()

def send(x):
    PROTOCOL.write(json.dumps(x,ensure_ascii=False,default=str)+'\n');PROTOCOL.flush()

def startup():
    global config,command,PretrainedAligner,BASE_GLOBALS,BASE_GLOBAL_CONFIG,BASE_MEMORY,recovery,IDENTITY
    assert os.environ.get('RECOVERY_GUARD_ACTIVE')=='1' and os.environ.get('HF_HUB_OFFLINE')=='1'
    spec=importlib.util.spec_from_file_location('recovery_launcher',R/'scripts/mfa_local_only_entry.py')
    recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
    it=time.perf_counter()
    import montreal_forced_aligner
    from montreal_forced_aligner.command_line import mfa
    from montreal_forced_aligner.command_line.align import align_corpus_hf_cli
    from montreal_forced_aligner.alignment import PretrainedAligner
    from montreal_forced_aligner import config
    command=align_corpus_hf_cli
    import_seconds=time.perf_counter()-it
    assert md.version('Montreal-Forced-Aligner')=='3.4.1'
    if os.environ.get('MFA_QUEUE_CANDIDATE')=='1':
        import queue_adapter
        QUEUE_ADAPTER_IDENTITY=queue_adapter.install()
    else:
        QUEUE_ADAPTER_IDENTITY={'status':'OFF'}
    expected=recovery.MFA_EXE.parent.parent/'lib/python3.14/site-packages'
    modules={n:str(Path(m.__file__).resolve()) for n,m in sys.modules.items() if n.startswith('montreal_forced_aligner') and getattr(m,'__file__',None)}
    assert all(Path(p).is_relative_to(expected.resolve()) for p in modules.values())
    IDENTITY={p:(Path(p).stat().st_ino,Path(p).stat().st_size,Path(p).stat().st_mtime_ns) for p in {str(recovery.MFA_EXE),*modules.values()}}
    if os.environ.get("DIRECT_KALPY_CANDIDATE")=="1":
        from direct_adapter import DirectAligner
        PretrainedAligner=DirectAligner
    if os.environ.get("STAGED_CAPTURE")=="1":
        import stage_capture
        stage_capture.install()
    import huggingface_hub
    huggingface_hub.snapshot_download=recovery.cached_resolver(huggingface_hub.snapshot_download)
    from montreal_forced_aligner.models import MfaAlignmentModel
    model=MfaAlignmentModel.from_pretrained(MODEL_ID)
    model_path=str(model.directory);del model
    BASE_GLOBALS={k:copy.deepcopy(v) for k,v in vars(config).items() if k.isupper() and isinstance(v,(bool,int,float,str,type(None),Path))}
    BASE_GLOBAL_CONFIG=config.GLOBAL_CONFIG;BASE_MEMORY=config.MEMORY
    # Startup identity is checked once, read-only module/executable stat identity on every request.
    result={'event':'ready','pid':os.getpid(),'version':'3.4.1','module_files':modules,'python':sys.executable,
            'startup_s':time.perf_counter()-PROCESS_ENTRY,'CLI_import_s':import_seconds,'model_cache_path':model_path,'queue_adapter':QUEUE_ADAPTER_IDENTITY,'direct_candidate':os.environ.get('DIRECT_KALPY_CANDIDATE')=='1','aligner_class':PretrainedAligner.__module__+'.'+PretrainedAligner.__name__,'snapshot':snapshot()}
    (O/'reports'/f'g{GEN}_worker_startup.json').write_text(json.dumps(result,indent=2));send(result)

def execute(req):
    rid,root,argv=validate(req);SEEN.add(rid);lifecycle.CURRENT_REQUEST=rid
    for p,identity in IDENTITY.items():
        st=Path(p).stat()
        if (st.st_ino,st.st_size,st.st_mtime_ns)!=identity:raise RuntimeError('MFA identity changed')
    before=snapshot();started=time.perf_counter();wall_start=time.time_ns();aligner=None;ctx=None
    record={'request_id':rid,'worker_pid':os.getpid(),'argv':argv,'root':str(root),'before':before,'started_ns':wall_start,'workflow_executed':False}
    if os.environ.get('STAGED_CAPTURE')=='1':
        import stage_capture,hashlib,wave
        stage_capture.begin(O/'reports'/(rid+'.stages'),os.environ.get('STAGE_ORACLE'))
        wav=next(Path(argv[8]).glob('*.wav'))
        with wave.open(str(wav),'rb') as wf:
            pcm=wf.readframes(wf.getnframes())
            audio={'bytes':wav.stat().st_size,'sha256':hashlib.sha256(wav.read_bytes()).hexdigest(),'PCM_sha256':hashlib.sha256(pcm).hexdigest(),'sample_count':wf.getnframes(),'sample_rate':wf.getframerate(),'channels':wf.getnchannels()}
        stage_capture.check('01_audio',audio)
        stage_capture.check('02_transcript',wav.with_suffix('.lab').read_text())
    old_env={k:os.environ.get(k) for k in ['MFA_ROOT_DIR','TMPDIR']}
    saved_config=None
    try:
        state=root/'state';state.mkdir()
        os.environ['MFA_ROOT_DIR']=str(state);os.environ['TMPDIR']=str(root)
        for k,v in BASE_GLOBALS.items():setattr(config,k,copy.deepcopy(v))
        config.GLOBAL_CONFIG=config.MfaConfiguration()
        config.MEMORY=type(BASE_MEMORY)(location=str(state/'joblib_cache'),verbose=0)
        with command.make_context('align_hf',argv[1:]) as ctx:
            # Same option/default parsing, HF model callback and initialization semantics,
            # without process-exit CLI history/ExitHooks (request diagnostics are explicit).
            from montreal_forced_aligner.utils import check_third_party
            import warnings
            config.load_configuration();config.update_configuration(ctx.params)
            if config.USE_POSTGRES:raise RuntimeError('prototype requires existing sqlite path; no server change allowed')
            check_third_party();warnings.simplefilter('ignore')
            assert config.NUM_JOBS==1 and config.SINGLE_SPEAKER and config.USE_MP
            kw=ctx.params
            assert kw['no_tokenization'] and kw['use_g2p'] and kw['output_format']=='long_textgrid'
            assert not kw.get('reference_directory') and not kw.get('custom_mapping_path') and not kw.get('fine_tune')
            extra=PretrainedAligner.parse_parameters(kw.get('config_path'),ctx.params,ctx.args)
            if kw['no_tokenization']:extra['language']='unknown'
            model=kw['model_id'];dictionary=model.get_dictionary_model(kw['dialect'])
            g2p_model=model.get_g2p_model(kw['dialect']) if kw['use_g2p'] else None
            aligner=PretrainedAligner(corpus_directory=kw['corpus_directory'].absolute(),acoustic_model=model.acoustic_model,dictionary=dictionary,g2p_model=g2p_model,**extra)
            record['initial_state']={'initialized':aligner.initialized,'database_initialized':aligner.database_initialized,'db_exists':Path(aligner.db_path).exists(),'db_path':str(aligner.db_path),'features_generated':aligner.features_generated,'text_normalized':aligner.text_normalized,'imported':aligner.imported,'workflow_initial_evidence':'new DB does not exist; no previous workflow'}
            assert not aligner.initialized and not aligner.database_initialized and not Path(aligner.db_path).exists()
            assert not aligner.features_generated and not aligner.text_normalized and not aligner.imported
            record['effective_config']={k:getattr(config,k) for k in ['NUM_JOBS','SINGLE_SPEAKER','USE_MP','USE_THREADING','USE_POSTGRES','CLEAN','FINAL_CLEAN','CLEANUP_TEXTGRIDS','BLAS_NUM_THREADS','SEED']}
            record['extra_parameters']=extra
            # Same bound methods and original multiprocessing. No replacement CLI subprocess.
            if os.environ.get('STAGED_CAPTURE')=='1':
                stage_capture.attach(aligner)
            feature_original=aligner.generate_features
            def timed_features(*args,**kwargs):
                t=time.perf_counter()
                try:return feature_original(*args,**kwargs)
                finally:record['feature_wall_s']=time.perf_counter()-t
            aligner.generate_features=timed_features
            t=time.perf_counter();record['workflow_executed']=True
            aligner.align();record['alignment_workflow_s']=time.perf_counter()-t
            record['after_alignment']=snapshot()
            record['final_state']={'initialized':aligner.initialized,'workflow_done':aligner.current_workflow.done,'features_generated':getattr(aligner,'features_generated',None),'text_normalized':getattr(aligner,'text_normalized',None)}
            t=time.perf_counter();aligner.analyze_alignments()
            record['analysis_s']=time.perf_counter()-t
            t=time.perf_counter();aligner.export_files(kw['output_directory'],output_format=kw['output_format'],include_original_text=kw['include_original_text'])
            record['export_s']=time.perf_counter()-t
            grids=list(Path(argv[10]).rglob('*.TextGrid'))
            assert len(grids)==1
            grid=grids[0];stat=grid.stat();assert stat.st_mtime_ns>=wall_start
            record['output']={'path':str(grid),'mtime_ns':stat.st_mtime_ns,'inode':stat.st_ino,'bytes':stat.st_size}
            # Output retained outside temp for parser/replay, only after real workflow/export.
            target=O/'results'/(rid+'.TextGrid');assert not target.exists();shutil.copyfile(grid,target)
            record['preserved_textgrid']=str(target)
    except BaseException:
        if aligner is not None:aligner.dirty=True
        record['error']=traceback.format_exc();raise
    finally:
        t=time.perf_counter()
        if aligner is not None:
            aligner.cleanup()
            try:
                import sqlite3
                con=sqlite3.connect(record['initial_state']['db_path'],timeout=0)
                try:con.execute('BEGIN IMMEDIATE');con.rollback()
                finally:con.close()
                record['db_close_probe']='PASS'
            except Exception:
                record['db_close_probe']='FAILED';record['db_close_error']=traceback.format_exc()
        if ctx is not None:ctx.params.clear()
        aligner=None;ctx=None;feature_original=None;timed_features=None;model=None;dictionary=None;g2p_model=None;kw=None
        for k,v in BASE_GLOBALS.items():setattr(config,k,copy.deepcopy(v))
        config.GLOBAL_CONFIG=BASE_GLOBAL_CONFIG;config.MEMORY=BASE_MEMORY
        for k,v in old_env.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
        gc.collect()
        record['after_close']=snapshot()
        record['lifecycle_gate']=lifecycle.gate(before,record['after_close'])
        if record['lifecycle_gate']['status']!='PASS':
            record['lifecycle_error']='RESIDENT_LIFECYCLE_UNRESOLVED'
        else:
            shutil.rmtree(root)
        record['cleanup_s']=time.perf_counter()-t
        record['temp_removed']=not root.exists()
        record['request_total_s']=time.perf_counter()-started
        record['after']=snapshot()
        (O/'reports'/(rid+'_worker.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str))
    if record.get('lifecycle_error') or record.get('db_close_probe')!='PASS':raise RuntimeError('request cleanup failed; recycle required')
    return record

def main():
    global PROTOCOL
    PROTOCOL=os.fdopen(os.dup(1),'w',buffering=1)
    with (O/'logs'/f'g{GEN}_worker_runtime.log').open('w') as log:
        os.dup2(log.fileno(),1);os.dup2(log.fileno(),2)
        startup()
    for line in sys.stdin:
        req=json.loads(line)
        if req.get('command')=='shutdown':
            shut=lifecycle.prepare_exit()
            (O/'reports'/f'g{GEN}_worker_shutdown_intent.json').write_text(json.dumps(shut,indent=2))
            send({'event':'stopped','pid':os.getpid(),'shutdown':shut});return
        try:
            rid=req.get('request_id','invalid')
            result=execute(req);send({'event':'result','status':'ok',**result})
        except BaseException:
            send({'event':'fatal','status':'error','pid':os.getpid(),'request_id':req.get('request_id'),'traceback':traceback.format_exc(),'recycle_required':True})
            return
if __name__=='__main__':main()
