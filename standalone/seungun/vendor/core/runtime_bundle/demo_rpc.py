import os,time,json,selectors,subprocess,signal
from demo_common import *
class MFAClient:
 def __init__(self):
  self.log=(O/'logs/sidecar.stderr').open('xb');self.buf=b'';self.closed=False
  self.p=subprocess.Popen([APP_PY,'-B','-u',str(O/'mfa_sidecar.py')],cwd=SOURCE,env=env_for(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,start_new_session=True,bufsize=0)
  write('reports/sidecar_pid.json',{'pid':self.p.pid,'argv':self.p.args})
 def call(self,op,**kw):
  t=time.perf_counter()
  self.p.stdin.write(json.dumps({'op':op,**kw}).encode()+b'\n');self.p.stdin.flush()
  deadline=time.monotonic()+165
  with selectors.DefaultSelector() as sel:
   sel.register(self.p.stdout,selectors.EVENT_READ)
   while b'\n' not in self.buf:
    if time.monotonic()>deadline:raise TimeoutError('SIDECAR_RPC_TIMEOUT')
    if not sel.select(.25):continue
    b=os.read(self.p.stdout.fileno(),65536)
    if not b:raise RuntimeError('SIDECAR_EOF')
    self.buf+=b
    if len(self.buf)>4194304:raise RuntimeError('SIDECAR_RESPONSE_TOO_LARGE')
  line,self.buf=self.buf.split(b'\n',1);res=json.loads(line)
  if not res['ok']:raise RuntimeError(res['error'])
  out=res['result'];out['RPC_roundtrip_s']=time.perf_counter()-t
  return out
 def close(self):
  if self.closed:return
  try:
   if self.p.poll() is None:
    self.p.stdin.close();self.p.wait(timeout=20)
   if self.p.returncode:raise RuntimeError('SIDECAR_NONZERO_EXIT')
  finally:
   if self.p.poll() is None:
    os.killpg(self.p.pid,signal.SIGTERM)
    try:self.p.wait(timeout=5)
    except subprocess.TimeoutExpired:os.killpg(self.p.pid,signal.SIGKILL);self.p.wait(timeout=5)
   self.p.stdout.close();self.log.close();self.closed=True
