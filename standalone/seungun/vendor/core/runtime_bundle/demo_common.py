import os,json,time,hashlib
from pathlib import Path
O=Path(__file__).resolve().parent
PREF=json.loads((O/'preflight.json').read_text());L=Path(PREF['local']);R=Path(PREF["recovery_root"]);W=R.parent;V=W.parent;SOURCE=W/'source'
CFG=json.loads((O/'configs/supervisor.json').read_text())
CURVE=json.loads((O/'configs/memory_curve_contract.json').read_text())
MFA_PY=str(V/'envs/mfa341/bin/python')
APP_PY=str(V/'envs/mac_prod311/bin/python')
def env_for(kind='app'):
 e=dict(os.environ)
 cache=json.loads((R/'configs/candidate_alignment_config.json').read_text())['paths']['mfa_root_dir']+'/huggingface'
 e.update(RECOVERY_LOCAL=str(L),PYTHONPATH=os.pathsep.join([str(L/'guard'),str(O),str(SOURCE/'src')]),PYTHONDONTWRITEBYTECODE='1',
   HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',HF_HOME=cache,HF_HUB_CACHE=cache,
   MFA_EXPERIMENT_VERSION='3.4.1',MFA_ROOT_DIR=str(L/'tmp/mfa_bootstrap'),TMPDIR=str(L/'tmp'),
   MPLCONFIGDIR=str(L/'cache/matplotlib'),XDG_CACHE_HOME=str(L/'cache'),NUMBA_CACHE_DIR=str(L/'cache/numba'),
   OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
   PATH=str(V/'envs/mfa341/bin')+os.pathsep+e.get('PATH',''))
 return e
def write(name,value):
 (O/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
