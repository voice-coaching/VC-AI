import time

"""Scoped official MFA CLI launcher; reuse verified recovery offline resolver."""
import sys,os,runpy,importlib.util,importlib.metadata as md
from pathlib import Path
O=Path(__file__).resolve().parent
import json
L=Path(json.loads((O/'preflight.json').read_text())['local'])
R=Path(json.loads((O/'preflight.json').read_text())['recovery_root'])
spec=importlib.util.spec_from_file_location('recovery_launcher',R/'scripts/mfa_local_only_entry.py')
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
def inside(value,root):
 p=Path(value).resolve()
 if not p.is_relative_to(root.resolve()) or p==root.resolve():raise ValueError('path outside isolated run')
 return p
def validate(args):
 if args==['version']:return
 if not args or args[0] not in ('align_hf','align_one_hf'):raise ValueError('unsupported command')
 n=11 if args[0]=='align_hf' else 12
 if len(args)!=n or args[1]!='--temporary_directory' or args[3:8]!=['--single_speaker','--no_tokenization','--use_g2p','--num_jobs','1']:raise ValueError('alignment options changed')
 inside(args[2],L/'tmp')
 if args[0]=='align_hf':
  inside(args[8],L/'tmp');model=args[9];inside(args[10],L/'tmp')
 else:
  inside(args[8],O/'scratch');inside(args[9],O/'scratch');model=args[10];inside(args[11],O/'scratch')
 if model!=recovery.REPOSITORY+'@'+recovery.REVISION:raise ValueError('model identity changed')
def main():
 args=sys.argv[1:];validate(args)
 if os.environ.get('HF_HUB_OFFLINE')!='1' or os.environ.get('RECOVERY_GUARD_ACTIVE')!='1':raise RuntimeError('offline guard required')
 version=os.environ['MFA_EXPERIMENT_VERSION']
 if version!='3.4.1':raise ValueError('profile is MFA3.4.1 only')
 if version not in ('3.4.1','3.4.2'):raise ValueError('version not authorized')
 if version=='3.4.2':sys.path.insert(0,str(O/'overlay'))
 import montreal_forced_aligner
 from montreal_forced_aligner.command_line import mfa
 if md.version('Montreal-Forced-Aligner')!=version:raise RuntimeError('MFA version mismatch')
 expected=(O/'overlay') if version=='3.4.2' else recovery.MFA_EXE.parent.parent/'lib/python3.14/site-packages'
 modules={n:str(Path(m.__file__).resolve()) for n,m in sys.modules.items() if n.startswith('montreal_forced_aligner') and getattr(m,'__file__',None)}
 if not all(Path(p).is_relative_to(expected.resolve()) for p in modules.values()):raise RuntimeError('mixed MFA modules')
 record=inside(os.environ['MFA_IMPORT_RECEIPT'],O/'reports')
 record.write_text(json.dumps({'version':version,'python':sys.executable,'pid':os.getpid(),'modules':modules},indent=2))
 import huggingface_hub
 huggingface_hub.snapshot_download=recovery.cached_resolver(huggingface_hub.snapshot_download)
 sys.argv=[str(recovery.MFA_EXE),*args]
 runpy.run_path(str(recovery.MFA_EXE),run_name='__main__')
if __name__=='__main__':main()
