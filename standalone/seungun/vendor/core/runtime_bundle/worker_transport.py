"""One ordinary OS worker process; no daemon Pool, no fork policy changes."""
import os,sys,time,json,subprocess,selectors,signal
from pathlib import Path
from memory_watchdog import Watchdog,snapshot,group,proc

class WorkerTransport:
    def __init__(self,generation,cfg,root,local,env,cwd,python):
        self.generation=generation;self.cfg=cfg;self.root=Path(root);self.closed=False;self.buffer=b'';self.close_result=None
        self.started=time.monotonic()
        env=dict(env);env['MFA_GENERATION']=str(generation)
        self.log=(self.root/'logs'/f'g{generation}_bootstrap.stderr').open('xb')
        self.p=subprocess.Popen([python,'-B',str(self.root/'resident_worker.py')],env=env,cwd=cwd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,start_new_session=True,bufsize=0)
        self.pid=self.p.pid
        self.watch=Watchdog(self.pid,cfg,self.root/'logs'/f'g{generation}_memory.jsonl')
        try:
            self.ready=self.receive(cfg['startup_timeout_s'])
            if self.ready.get('event')!='ready':raise RuntimeError('STARTUP_FAILED')
            self.startup_wall_s=time.monotonic()-self.started;self.watch.phase='READY'
        except BaseException:
            self.close(graceful=False);raise
    def receive(self,timeout):
        deadline=time.monotonic()+timeout
        with selectors.DefaultSelector() as sel:
            sel.register(self.p.stdout,selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                self.watch.check()
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('worker IPC timeout')
                if not sel.select(min(.25,remaining)):continue
                chunk=os.read(self.p.stdout.fileno(),65536)
                if not chunk:raise RuntimeError('WORKER_EOF')
                self.buffer+=chunk
                if len(self.buffer)>1024*1024:raise RuntimeError('IPC_RESPONSE_LIMIT')
        line,self.buffer=self.buffer.split(b'\n',1)
        self.watch.check()
        return json.loads(line)
    def request(self,payload):
        self.watch.phase=payload['request_id']
        self.watch.check()
        if self.p.poll() is not None:raise RuntimeError('WORKER_EXITED')
        data=json.dumps(payload).encode()+b'\n'
        if len(data)>65536:raise ValueError('IPC_REQUEST_LIMIT')
        self.p.stdin.write(data);self.p.stdin.flush()
        t=time.monotonic();result=self.receive(self.cfg['request_timeout_s'])
        result['transport_roundtrip_s']=time.monotonic()-t
        if result.get('request_id')!=payload['request_id'] or result.get('status')!='ok':raise RuntimeError('REQUEST_FAILED:'+json.dumps(result))
        self.watch.phase='READY'
        return result
    def boundary(self):
        self.watch.check();return snapshot(self.pid,True)
    def close(self,graceful=True):
        if self.closed:return self.close_result
        t=time.monotonic();fallback=False
        before=snapshot(self.pid,True);known={p['pid']:p['start_ticks'] for p in before['members']}
        try:
            if self.p.poll() is None and graceful:
                self.watch.phase='DRAINING'
                try:
                    self.p.stdin.write(b'{"command":"shutdown"}\n');self.p.stdin.flush()
                    self.p.wait(timeout=self.cfg['shutdown_timeout_s'])
                except (BrokenPipeError,subprocess.TimeoutExpired):pass
            if self.p.poll() is None:
                fallback=True
                os.killpg(self.pid,signal.SIGTERM)
                try:self.p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.pid,signal.SIGKILL);self.p.wait(timeout=5)
            # Normal interpreter exit owns tracker finalization. Verify all observed IDs.
            deadline=time.monotonic()+3
            while True:
                survivors=group(self.pid,True)
                for pid,ticks in known.items():
                    p=proc(pid,True)
                    if p and p['start_ticks']==ticks and all(x['pid']!=pid for x in survivors):survivors.append(p)
                if not survivors or time.monotonic()>=deadline:break
                time.sleep(.05)
            self.watch.stop()
            self.close_result={'pid':self.pid,'generation':self.generation,'exit_code':self.p.returncode,'reaped':True,'fallback_signal':fallback,'survivors':survivors,'elapsed_s':time.monotonic()-t,'before':before,'after':snapshot(detail=True),'watchdog_error':self.watch.error,'natural_interpreter_exit':graceful and not fallback}
            self.closed=True
            (self.root/'reports'/f'g{self.generation}_transport_close.json').write_text(json.dumps(self.close_result,indent=2))
            if survivors:raise RuntimeError('OWNED_CHILD_SURVIVOR')
            if graceful and (fallback or self.p.returncode!=0):raise RuntimeError('NORMAL_SHUTDOWN_FAILED')
            return self.close_result
        finally:
            if not self.watch.stop_event.is_set():self.watch.stop()
            for f in [self.p.stdin,self.p.stdout,self.log]:
                if f and not f.closed:f.close()
