"""Bounded lifetime supervisor: one active worker, one pending, no request retry."""
import time,queue,threading,json
from concurrent.futures import Future
class QueueFull(RuntimeError):pass

class BoundedSupervisor:
    def __init__(self,cfg,factory,admission,recorder=lambda x:None):
        self.cfg=cfg;self.factory=factory;self.admission=admission;self.recorder=recorder
        self.pending=queue.Queue(maxsize=cfg['pending_queue'])
        self.lock=threading.Lock();self.worker=None;self.state='STOPPED';self.generation=0;self.count=0;self.total=0;self.failed=False;self.closed=False
        self.events=[];self.generations=[];self.results=[]
        self.dispatcher=threading.Thread(target=self.loop,name='bounded-supervisor',daemon=False)
        self.dispatcher.start()
    def event(self,state,**kw):
        self.state=state;e={'state':state,'generation':self.generation,'monotonic':time.monotonic(),**kw};self.events.append(e);self.recorder(e)
    def start_worker(self):
        if self.worker:return 0.0
        if self.failed or self.generation>=self.cfg['max_generations']:raise RuntimeError('GENERATION_BUDGET_EXHAUSTED')
        self.admission(self.cfg);self.generation+=1;self.count=0;self.event('STARTING')
        t=time.monotonic()
        try:self.worker=self.factory(self.generation)
        except BaseException:
            self.failed=True;self.event('ERROR',reason='STARTUP_FAILED');raise
        self.generations.append({'generation':self.generation,'pid':self.worker.pid,'started_monotonic':t,'startup_wall_s':time.monotonic()-t,'ready':self.worker.ready,'requests':[]})
        self.event('READY');return time.monotonic()-t
    def prestart(self):
        # Only before submissions; dispatcher idle.
        with self.lock:return self.start_worker()
    def drain(self,reason,graceful=True):
        if not self.worker:return None
        self.event('DRAINING',reason=reason);w=self.worker
        close=w.close(graceful=graceful)
        g=self.generations[-1];g['close']=close;g['reason']=reason;g['elapsed_s']=time.monotonic()-g['started_monotonic'];g['elapsed_per_request_s']=g['elapsed_s']/max(1,len(g['requests']))
        self.worker=None;self.event('STOPPED')
        return close
    def submit(self,payload_factory):
        accepted=time.monotonic();f=Future()
        with self.lock:
            if self.closed or self.failed:raise RuntimeError('SUPERVISOR_NOT_AVAILABLE')
            try:self.pending.put_nowait((accepted,f,payload_factory))
            except queue.Full:raise QueueFull('PENDING_QUEUE_FULL')
        return f
    def request(self,payload_factory):
        return self.submit(payload_factory).result(timeout=self.cfg['queue_timeout_s']+self.cfg['startup_timeout_s']+self.cfg['request_timeout_s']+self.cfg['shutdown_timeout_s']+15)
    def loop(self):
        while True:
            item=self.pending.get()
            if item is None:self.pending.task_done();return
            accepted,f,make_payload=item
            try:
                if self.failed:raise RuntimeError('SUPERVISOR_FAILED')
                if time.monotonic()-accepted>self.cfg['queue_timeout_s']:raise TimeoutError('QUEUE_TIMEOUT')
                if self.total>=self.cfg['max_total_resident_requests']:raise RuntimeError('TOTAL_REQUEST_BUDGET')
                queue_wait=time.monotonic()-accepted
                admission=self.admission(self.cfg)
                startup=self.start_worker()
                if self.count>=self.cfg['max_requests_per_worker']:raise RuntimeError('COUNT_GATE_BUG')
                payload=make_payload(self.generation,self.count)
                boundary_before=self.worker.boundary()
                self.count+=1;self.total+=1 # count attempts before IPC; never retry failure
                self.event('BUSY',request_id=payload['request_id'],request_count=self.count)
                t=time.monotonic();result=self.worker.request(payload);workwall=time.monotonic()-t
                after=self.worker.boundary()
                if result['lifecycle_gate']['status']!='PASS' or not result['temp_removed'] or result['db_close_probe']!='PASS':raise RuntimeError('CLEANUP_GATE_FAILED')
                rec={'generation':self.generation,'ordinal':self.count,'input':payload['input'],'accepted_monotonic':accepted,'queue_wait_s':queue_wait,'restart_wait_s':startup,'worker_roundtrip_s':workwall,'admission':admission,'memory_before':boundary_before,'memory_after':after,'worker':result,'shutdown_s':0}
                self.generations[-1]['requests'].append(payload['request_id'])
                reason='MAX_REQUESTS' if self.count>=self.cfg['max_requests_per_worker'] else ('SOFT_MEMORY' if after['tree_rss_bytes']>=self.cfg['soft_recycle_worker_tree_rss_bytes'] else None)
                if reason:
                    rec['recycle_reason']=reason;rec['generation_shutdown']='PENDING'
                    rec['acceptance_to_response_s']=time.monotonic()-accepted
                    self.results.append(rec);f.set_result(rec)
                    self.drain(reason)
                    continue
                else:self.event('READY')
                rec['acceptance_to_response_s']=time.monotonic()-accepted
                self.results.append(rec);f.set_result(rec)
            except BaseException as e:
                self.failed=True;self.event('TIMEOUT' if isinstance(e,TimeoutError) else 'ERROR',error=type(e).__name__,detail=str(e))
                try:self.drain('ERROR_NO_REUSE',graceful=False)
                except BaseException as cleanup:e=RuntimeError(str(e)+'; cleanup='+str(cleanup))
                if not f.done():f.set_exception(e)
                while True:
                    try:
                        pending=self.pending.get_nowait()
                        if pending is not None:pending[1].set_exception(RuntimeError('SUPERVISOR_FAILED_NO_RETRY'))
                        self.pending.task_done()
                    except queue.Empty:break
            finally:self.pending.task_done()
    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
        self.pending.join()
        try:self.drain('FINAL_CLOSE',graceful=not self.failed)
        finally:
            self.pending.put(None);self.dispatcher.join(timeout=15)
            if self.dispatcher.is_alive():raise RuntimeError('DISPATCHER_DID_NOT_STOP')
