"""Lightweight serial MFA supervisor; no full-pipeline model in this process."""
import os,sys,json,time,subprocess,shutil,secrets,signal,traceback
from demo_common import *
from memory_watchdog import admission,Watchdog,group,snapshot
from worker_transport import WorkerTransport
from bounded_supervisor import BoundedSupervisor
from curve_rules import enable_curve,sum_memory
ENV=env_for('mfa')
ENV.update(MFA_QUEUE_CANDIDATE='1',DIRECT_KALPY_CANDIDATE='1' if PREF['direct_candidate'] else '0',STAGED_CAPTURE='0')
S=None;version=None;control_used=False
state={'status':'STARTING','controls':[],'requests':[],'events':[]}
def save():
 if S:state.update(generations=S.generations,events=S.events)
 write('sidecar_results.json',state)
def command(args,tag):
 e=dict(ENV);e['MFA_IMPORT_RECEIPT']=str(O/'reports'/f'{tag}_imports.json');e['MFA_ROOT_DIR']=str(L/'tmp'/tag/'state')
 t=time.monotonic();p=None;mon=None
 with (O/'logs'/f'{tag}.stdout').open('xb') as fo,(O/'logs'/f'{tag}.stderr').open('xb') as fe:
  try:
   p=subprocess.Popen([MFA_PY,'-B',str(O/'mfa_entry_control.py'),*args],env=e,cwd=SOURCE,stdout=fo,stderr=fe,start_new_session=True)
   mon=Watchdog(p.pid,CFG,O/'logs'/f'{tag}_memory.jsonl');mon.phase=tag
   while p.poll() is None:
    mon.check()
    if time.monotonic()-t>CFG['request_timeout_s']:raise TimeoutError(tag)
    time.sleep(.1)
   mon.check()
   if p.returncode:raise RuntimeError('MFA_COMMAND_FAILED '+tag)
   until=time.monotonic()+3
   while group(p.pid) and time.monotonic()<until:time.sleep(.05)
   if group(p.pid):raise RuntimeError('CONTROL_CHILD_SURVIVOR')
   return {'pid':p.pid,'argv':p.args,'elapsed_s':time.monotonic()-t,'exit_code':p.returncode,'survivors':[],'peak_rss_bytes':max(x['tree_rss_bytes'] for x in mon.samples)}
  finally:
   if p and p.poll() is None:
    os.killpg(p.pid,signal.SIGTERM)
    try:p.wait(timeout=3)
    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
   if mon:mon.stop()
def stage(old,root):
 if len(old)!=11 or old[0]!='align_hf' or old[3:8]!=['--single_speaker','--no_tokenization','--use_g2p','--num_jobs','1']:raise ValueError('ARGV_CONTRACT')
 src=Path(old[8]).resolve()
 if not src.is_relative_to(L/'tmp/pipeline'):raise ValueError('INPUT_ESCAPE')
 files=list(src.iterdir())
 if len(files)!=2 or sorted(x.suffix for x in files)!=['.lab','.wav']:raise ValueError('INPUT_SHAPE')
 corpus=root/'corpus';corpus.mkdir(parents=True,exist_ok=False)
 for x in files:
  if x.is_symlink():raise ValueError('INPUT_SYMLINK')
  shutil.copyfile(x,corpus/x.name)
 argv=old.copy();argv[2]=str(root/'alignment');argv[8]=str(corpus);argv[10]=str(root/'output')
 return argv
def dispatch(req):
 global version,control_used,S
 op=req['op']
 if op=='version':
  if version is not None:return version
  # The real worker already verifies distribution version and loaded module paths.
  # Do not launch the complete MFA CLI solely to print its version.
  admission(CFG)
  S=BoundedSupervisor(CFG,lambda g:WorkerTransport(g,CFG,O,L,ENV,SOURCE,MFA_PY),admission)
  elapsed=S.prestart();ready=S.worker.ready
  if ready.get('version')!='3.4.1' or not ready.get('module_files') or not ready.get('direct_candidate'):
   raise RuntimeError('WORKER_VERSION_IDENTITY')
  version={'version':ready['version'],'source':'VERIFIED_RESIDENT_WORKER','worker_pid':S.worker.pid}
  state['version']=version
  state['startup']={'startup_s':elapsed,'ready':ready,'pid':S.worker.pid}
  save();return version
 if op=='control':
  if control_used or version is None:raise RuntimeError('CONTROL_BUDGET')
  control_used=True;admission(CFG);root=L/'tmp/control_demo';t=time.monotonic()
  argv=stage(req['argv'],root);rec=command(argv,'control_demo')
  grids=list((root/'output').rglob('*.TextGrid'))
  if len(grids)!=1:raise RuntimeError('OUTPUT_COUNT')
  out=O/'results/control_demo.TextGrid';shutil.copyfile(grids[0],out)
  rec.update(output=str(out),output_sha256=sha(out));shutil.rmtree(root)
  rec.update(service_s=time.monotonic()-t,temp_removed=not root.exists());state['controls'].append(rec);save();return rec
 if op=='start':
  if S is None or version is None:raise RuntimeError('START_SEQUENCE')
  return state['startup']
 if op=='override':
  with S.lock:enable_curve(S)
  state['curve_override']={'at_ns':time.time_ns(),'config':dict(CFG)};save();return {'status':'APPLIED'}
 if op=='resident':
  if not S or not S.worker:raise RuntimeError('NO_SAME_WORKER')
  def payload(g,n):
   rid=f'g{g}_r{n}_'+secrets.token_hex(6)
   argv=stage(req['argv'],L/'tmp/requests'/rid)
   return {'request_id':rid,'input':'DEMO','argv':argv}
  rec=S.request(payload)
  rid=rec['worker']['request_id']
  # Read recorded samples, not a result cache. Worker state/result freshly generated.
  samples=[json.loads(z) for z in (O/'logs/g1_memory.jsonl').read_text().splitlines() if z]
  active=[z for z in samples if z['phase']==rid]
  rec['sampled_peak']={k:max((sum_memory(z)[k] for z in active if sum_memory(z)[k] is not None),default=None) for k in ['rss_bytes','pss_bytes','uss_bytes']}
  rec['output']=rec['worker']['preserved_textgrid'];rec['output_sha256']=sha(rec['output'])
  state['requests'].append(rec);save();return rec
 if op=='close':
  if S:S.close()
  state['status']='CLOSED';state['final_memory']=snapshot(detail=True);save()
  return {'status':'CLOSED','generations':S.generations if S else [],'remaining_request_dirs':[str(x) for x in (L/'tmp/requests').glob('*')]}
 raise ValueError('UNKNOWN_OPERATION')
def main():
 try:
  for line in sys.stdin:
   req=json.loads(line)
   try:
    result=dispatch(req);print(json.dumps({'ok':True,'result':result},ensure_ascii=False),flush=True)
   except BaseException:
    state['error']=traceback.format_exc();state['status']='FAILED';save()
    print(json.dumps({'ok':False,'error':state['error']}),flush=True);break
   if req['op']=='close':break
 finally:
  if S:
   try:S.close()
   except BaseException:state['close_error']=traceback.format_exc()
  save()
if __name__=='__main__':main()
