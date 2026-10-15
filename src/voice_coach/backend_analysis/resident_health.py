"""Read-only health of the MFA processes owned by a resident generation."""
import json
import os
from pathlib import Path
import subprocess

MFA_ADAPTER = Path('/workspace/intelligentai/validation/finish_20260916/latency_optimization_20260921T044013_2c3be9/mfa_startup_recovery_20260921T065201_ac461c/mfa_preflight_optimization_20260922T043055Z_0d7f6a/candidate/mfa.py')


def check_mfa_import(core_root, python, environment):
    """Check request-time imports as the service UID, without audio or inference."""
    code = '''import importlib.util,sys
sys.path.insert(0,sys.argv[1])
spec=importlib.util.spec_from_file_location('korean_pronunciation_poc.canonical_mfa',sys.argv[2])
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
'''
    result = subprocess.run([str(python), '-B', '-c', code,
        str(core_root / 'source/src'), str(MFA_ADAPTER)], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=20, check=False)
    if result.returncode:
        raise ValueError('MFA_ADAPTER_IMPORT_UNAVAILABLE')


def process_identity(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError('MFA_PROCESS_ID_INVALID')
    root = Path('/proc') / str(pid)
    if root.stat().st_uid != os.geteuid():
        raise ValueError('MFA_PROCESS_OWNER_CHANGED')
    fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in {'Z', 'X', 'x'}:
        raise ValueError('MFA_PROCESS_EXITED')
    # PID + kernel start time prevents a recycled PID passing a later probe.
    return pid, int(fields[19])


class MFAProcessHealth:
    def __init__(self, generation):
        from .canonical_producer import read_private_result
        receipt = json.loads(read_private_result(generation / 'host_ready.json', generation, 65536))
        if receipt.get('status') != 'READY':
            raise ValueError('MFA_HOST_NOT_READY')
        pids = (receipt['host_pid'], receipt['worker_pid'], receipt['startup']['ready']['pid'])
        self.identities = tuple(process_identity(pid) for pid in pids)

    def alive(self):
        try:
            return all(process_identity(pid) == (pid, started) for pid, started in self.identities)
        except (OSError, ValueError, IndexError):
            return False
