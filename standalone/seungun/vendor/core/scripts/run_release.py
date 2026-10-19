"""Start preserved MFA in its own process before running the canonical AI batch."""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, signal, subprocess, sys, time, traceback
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
APP_PY = '/workspace/intelligentai/validation/finish_20260916/envs/mac_prod311/bin/python'
BUNDLE = ROOT / 'runtime_bundle'
IDENTITY = json.loads((ROOT / 'config/historical_runtime_identity.json').read_text())

def sha(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def write(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + '\n')

def prepare(out):
    runtime = out / 'mfa_runtime'
    runtime.mkdir()
    local = Path('/tmp') / ('canonical_ai_release_' + secrets.token_hex(6))
    local.mkdir()
    for rel in ('configs', 'reports', 'results', 'logs'):
        (runtime / rel).mkdir()
    for rel in ('guard', 'tmp/pipeline', 'tmp/requests', 'cache/matplotlib', 'cache/numba'):
        (local / rel).mkdir(parents=True, exist_ok=True)
    for n, record in IDENTITY['source_files'].items():
        src = BUNDLE / n
        assert sha(src) == record['sha256'], n
        dest = runtime / n
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        assert sha(dest) == record['sha256'], n
    old = json.loads((BUNDLE / 'preflight.json').read_text())
    old.update(root=str(runtime), local=str(local), fixture='canonical_ai_pre_llm_release', parallel_candidate=False)
    write(runtime / 'preflight.json', old)
    guard = BUNDLE / 'guard/sitecustomize.py'
    assert sha(guard) == IDENTITY['guard_original_sha256']
    (local / 'guard/sitecustomize.py').write_text(guard.read_text().replace(IDENTITY['selected_bundle'], str(runtime)))
    return (runtime, local)
