"""Owned-child provenance and fail-closed resident infrastructure classification."""
import os,sys,re,time,json,traceback,subprocess
from pathlib import Path
OWNER=os.getpid();ROOT=None;LOCAL=None;CURRENT_REQUEST=None;SPAWNS=[];REGISTRY=None
def redact(argv):
    out=[];hide=False
    for arg in argv:
        value=os.fsdecode(arg)
        if hide:out.append('<REDACTED>');hide=False;continue
        if re.match(r'^--?(?:api[-_]?key|token|secret|password|credential)(?:=|$)',value,re.I):
            if '=' in value:out.append(value.split('=',1)[0]+'=<REDACTED>')
            else:out.append(value);hide=True
        else:
            out.append(re.sub(r'(?i)(api_key|access_token|secret|password)(\s*[:=]\s*)[^\s,;]+',r'\1\2<REDACTED>',value))
    return out
def stack():
    return [{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(limit=22)[:-1]]
def install(root,local):
    global ROOT,LOCAL
    ROOT=Path(root);LOCAL=Path(local)
    import multiprocessing.util as util
    original=util.spawnv_passfds
    def observed(path,args,passfds):
        trace=stack();t=time.time_ns();pid=original(path,args,passfds)
        item={'kind':'spawnv_passfds','pid':pid,'parent_pid':os.getpid(),'request_id':CURRENT_REQUEST,'timestamp_ns':t,'executable':os.fsdecode(path),'argv':redact(args),'passfds':list(passfds),'stack':trace}
        if os.getpid()==OWNER:
            SPAWNS.append(item)
            with (ROOT/'reports/child_spawn_provenance.jsonl').open('a') as f:f.write(json.dumps(item)+'\n')
        return pid
    util.spawnv_passfds=observed
def fd_targets(pid):
    records=[]
    try:
        for f in Path('/proc',str(pid),'fd').iterdir():
            try:records.append({'fd':int(f.name),'target':os.readlink(f)})
            except (FileNotFoundError,PermissionError):pass
    except (FileNotFoundError,PermissionError):pass
    return records
def process(pid):
    p=Path('/proc',str(pid))
    try:
        fields=(p/'stat').read_text().rsplit(')',1)[1].split()
        cmd=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x]
        exe=os.readlink(p/'exe')
        btime=next(int(x.split()[1]) for x in Path('/proc/stat').read_text().splitlines() if x.startswith('btime '))
        ticks=int(fields[19]);fds=fd_targets(pid)
        return {'pid':pid,'ppid':int(fields[1]),'pgid':int(fields[2]),'status':fields[0],'executable':exe,'argv':redact(cmd),'start_ticks':ticks,'start_epoch_s':btime+ticks/os.sysconf('SC_CLK_TCK'),'fds':fds,'request_fds':[x for x in fds if str(LOCAL/'tmp/requests') in x['target']]}
    except (FileNotFoundError,ProcessLookupError,PermissionError):return None
def snapshot():
    table=[x.split() for x in subprocess.check_output(['ps','-eo','pid,ppid,pgid,stat,comm'],text=True).splitlines()[1:]]
    parents={OWNER};records=[]
    while True:
        new=[x for x in table if int(x[1]) in parents and int(x[0]) not in parents and x[4]!='ps']
        if not new:break
        for x in new:
            parents.add(int(x[0]));p=process(int(x[0]))
            if p:records.append(p)
    rss=next(int(x.split()[1])*1024 for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmRSS:'))
    fds=fd_targets(OWNER)
    return {'timestamp_ns':time.time_ns(),'rss_bytes':rss,'open_fds':len(fds),'children':records,'request_fds':[x['target'] for x in fds if str(LOCAL/'tmp/requests') in x['target']]}
def tracker_signature(p,owner,python_executable,tracker_pid,spawns):
    if p['pid']!=tracker_pid or p['ppid']!=owner or p['pgid']!=owner or p['status']=='Z':return False
    if Path(p['executable']).resolve()!=Path(python_executable).resolve() or p.get('request_fds'):return False
    args=p.get('argv',[])
    if '-c' not in args:return False
    n=args.index('-c')
    if n+1!=len(args)-1 or not re.fullmatch(r'from multiprocessing\.resource_tracker import main;main\(\d+\)',args[n+1]):return False
    own=[s for s in spawns if s['pid']==p['pid'] and s['parent_pid']==owner]
    if len(own)!=1:return False
    return any(x['file'].endswith('/multiprocessing/resource_tracker.py') and x['function']=='_launch' for x in own[0]['stack'])
def gate(before,after):
    global REGISTRY
    from multiprocessing import resource_tracker
    tracker_pid=resource_tracker._resource_tracker._pid
    initial={(p['pid'],p['start_ticks']) for p in before['children']}
    classes=[];unresolved=[]
    for p in after['children']:
        valid=tracker_signature(p,OWNER,sys.executable,tracker_pid,SPAWNS)
        identity=(p['pid'],p['start_ticks'])
        if valid and (REGISTRY is None or REGISTRY==identity):
            if REGISTRY is None:REGISTRY=identity
            classification='RESIDENT_INFRASTRUCTURE'
        else:classification='UNRESOLVED';unresolved.append(p['pid'])
        classes.append({**p,'classification':classification,'role':'CPython multiprocessing.resource_tracker' if valid else 'UNKNOWN','existed_before_request':identity in initial,'created_during_request':identity not in initial,'remains_after_cleanup':True})
    expected_missing=REGISTRY is not None and not any((p['pid'],p['start_ticks'])==REGISTRY for p in after['children'])
    return {'status':'PASS' if not unresolved and not after['request_fds'] and not expected_missing and len(classes)<=1 else 'UNRESOLVED','children':classes,'request_scoped_children':0 if not unresolved else None,'unresolved_pids':unresolved,'infrastructure_identity':REGISTRY,'zombies':sum(p['status']=='Z' for p in after['children']),'registry_missing':expected_missing,'request_open_fd_count':len(after['request_fds'])}
def prepare_exit():
    import multiprocessing as mp
    before=snapshot()
    current=gate(before,before)
    if current['status']!='PASS' or mp.active_children():
        raise RuntimeError('unsafe interpreter shutdown')
    return {'method':'normal interpreter return; CPython owns atexit/finalizer/tracker destruction; supervisor verifies wait/reap','before':before,'gate':current}
