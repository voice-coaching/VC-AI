"""Read-only owned process memory accounting; sample watchdog, not OS hard cap."""
import os,time,json,threading
from pathlib import Path

def proc(pid,detail=False):
    p=Path('/proc')/str(pid)
    try:
        fields=(p/'stat').read_text().rsplit(')',1)[1].split()
        d={'pid':pid,'ppid':int(fields[1]),'pgid':int(fields[2]),'status':fields[0],'start_ticks':int(fields[19]),'rss_bytes':int(fields[21])*os.sysconf('SC_PAGE_SIZE')}
        if detail:
            try:
                vals={z.split(':')[0]:int(z.split()[1])*1024 for z in (p/'smaps_rollup').read_text().splitlines() if ':' in z and len(z.split())>=3 and z.split()[1].isdigit()}
                d.update(pss_bytes=vals.get('Pss'),uss_bytes=vals.get('Private_Clean',0)+vals.get('Private_Dirty',0)+vals.get('Private_Hugetlb',0))
            except (OSError,ValueError):d.update(pss_bytes=None,uss_bytes=None)
            try:d['open_fd_count']=len(list((p/'fd').iterdir()))
            except OSError:d['open_fd_count']=None
        return d
    except (OSError,ValueError):return None

def group(pgid,detail=False):
    out=[]
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            d=proc(int(p.name),False)
            if d and d['pgid']==pgid:out.append(proc(d['pid'],True) if detail else d)
    return [x for x in out if x]

def cgroup():
    from pathlib import Path

    headroom = []
    sources = []
    record = dict(current=None, max=None, failcnt=None, oom_control=None,
                  cgroup_version=None, hard_limit_observed=False)
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                value = int(line.split()[1]) * 1024
                if value >= 0:
                    headroom.append(value)
                    sources.append('proc_meminfo')
    except (OSError, ValueError, IndexError):
        pass
    for version, root, cap_name, used_name in (
        (2, '/sys/fs/cgroup', 'memory.max', 'memory.current'),
        (1, '/sys/fs/cgroup/memory', 'memory.limit_in_bytes', 'memory.usage_in_bytes'),
    ):
        try:
            cap = int((Path(root) / cap_name).read_text())
            used = int((Path(root) / used_name).read_text())
            if 0 < cap < 2**60 and used >= 0:
                headroom.append(max(0, cap - used))
                sources.append('cgroup_v' + str(version))
                record.update(current=used, max=cap, cgroup_version=version,
                              hard_limit_observed=True)
        except (OSError, ValueError):
            pass
    if not headroom:
        raise RuntimeError('MEMORY_MEASUREMENT_UNAVAILABLE')
    record.update(available_bytes=min(headroom), measurement_sources=sources)
    return record


def snapshot(pgid=None,detail=False):
    own=proc(os.getpid(),detail)
    if own is None:raise RuntimeError('MEMORY_MEASUREMENT_UNAVAILABLE')
    members=group(pgid,detail) if pgid else []
    return {'monotonic':time.monotonic(),'utc_ns':time.time_ns(),'supervisor':own,'members':members,'tree_rss_bytes':sum(p['rss_bytes'] for p in members),'cgroup':cgroup()}

def violation(s,cfg):
    cg=s['cgroup']
    if cg['available_bytes']<cfg['other_service_reserve_bytes']:return 'MEMORY_HEADROOM'
    if s['tree_rss_bytes']>cfg['abort_worker_tree_rss_bytes']:return 'WORKER_TREE_BUDGET'
    if s['supervisor']['rss_bytes']>cfg['abort_supervisor_rss_bytes']:return 'SUPERVISOR_BUDGET'
    if s['tree_rss_bytes']+s['supervisor']['rss_bytes']>cfg['abort_combined_rss_bytes']:return 'COMBINED_BUDGET'
    return None

def admission(cfg):
    s=snapshot(detail=True)
    if s['cgroup']['available_bytes']<cfg['admission_reserve_bytes']:raise RuntimeError('MEMORY_ADMISSION_BLOCKED')
    if violation(s,cfg):raise RuntimeError('MEMORY_BUDGET_BLOCKED')
    return s

class Watchdog:
    def __init__(self,pgid,cfg,path):
        self.pgid=pgid;self.cfg=cfg;self.path=Path(path);self.stop_event=threading.Event();self.error=None;self.samples=[];self.phase='STARTING'
        self.thread=threading.Thread(target=self.run,name='memory-watchdog',daemon=False);self.thread.start()
    def run(self):
        try:
            with self.path.open('x') as f:
                while not self.stop_event.is_set():
                    s=snapshot(self.pgid,detail=(len(self.samples)%4==0));s['phase']=self.phase;s['detailed_sample']=(len(self.samples)%4==0)
                    self.samples.append(s);f.write(json.dumps(s)+'\n');f.flush()
                    v=violation(s,self.cfg)
                    if v:self.error=v;return
                    self.stop_event.wait(self.cfg['sample_interval_s'])
        except BaseException as e:self.error='MEMORY_MEASUREMENT_FAILED:'+type(e).__name__
    def check(self):
        if self.error:raise RuntimeError(self.error)
    def stop(self):
        self.stop_event.set();self.thread.join(timeout=3)
        if self.thread.is_alive():raise RuntimeError('watchdog did not stop')
