"""Materialize a local derivative of the verified deployed acoustic source.

Historical absolute paths exist only inside the disposable Linux container.
The parent bytes remain untouched; changed files receive a new release identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil

from assets import PACKAGE, digest, entries, nltk_tree

FINISH = Path('/workspace/intelligentai/validation/finish_20260916')
LATENCY = FINISH / 'latency_optimization_20260921T044013_2c3be9'
RECOVERY = LATENCY / 'mfa_startup_recovery_20260921T065201_ac461c'
VALIDATION = FINISH.parent
MFA_CACHE = LATENCY / 'bench_20260921T061213_21865d/results/baseline_cpu/request_00/mfa'
APP_PY = FINISH / 'envs/mac_prod311/bin/python'
MFA_PY = FINISH / 'envs/mfa341/bin/python'


def link(target: Path, source: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def verify_source() -> dict:
    receipt = json.loads((PACKAGE / 'manifests/deployed-source.json').read_bytes())
    if digest(PACKAGE / 'vendor/core/release_manifest.original.json') != receipt['coreManifestSha256']:
        raise ValueError('PARENT_MANIFEST_HASH_MISMATCH')
    for name, expected in receipt['files'].items():
        if digest(PACKAGE / name) != expected:
            raise ValueError('DEPLOYED_SOURCE_HASH_MISMATCH: ' + name)
    for name, expected in receipt['bundledAssets'].items():
        if digest(PACKAGE / name) != expected:
            raise ValueError('BUNDLED_ASSET_HASH_MISMATCH: ' + name)
    return receipt


def materialize(cache: Path, device: str) -> tuple[Path, dict]:
    parent = verify_source()
    architecture = platform.machine()
    environment = {p.name: digest(p) for p in sorted(Path('/opt/seungun-build').glob('*')) if p.is_file()}
    if set(environment) != {'installed-app.txt', 'installed-mfa.explicit', 'system-packages.txt'}:
        raise ValueError('BUILD_ENVIRONMENT_RECEIPTS_MISSING')
    core = Path('/runtime/core')
    if core.exists():
        raise ValueError('LOCAL_RUNTIME_ALREADY_EXISTS')
    shutil.copytree(PACKAGE / 'vendor/core', core)
    runner = core / 'source/scripts/run_canonical_batch.py'
    source = runner.read_text()
    if source.count(".to('cuda:0')") != 2 or source.count('if not torch.cuda.is_available():') != 1:
        raise ValueError('PARENT_DEVICE_CONTEXT_CHANGED')
    source = source.replace(".to('cuda:0')", ".to(os.environ['SEUNGUN_DEVICE'])")
    source = source.replace('if not torch.cuda.is_available():',
        "if os.environ['SEUNGUN_DEVICE'].startswith('cuda') and not torch.cuda.is_available():")
    source = source.replace("'device': 'cuda:0'", "'device': os.environ['SEUNGUN_DEVICE']")
    runner.write_text(source)
    schema_path = core / 'contracts/canonical_ai_output.schema.json'
    schema = json.loads(schema_path.read_bytes())
    schema['properties']['native']['properties']['device'] = {
        'const': 'cuda:0' if device == 'cuda' else 'cpu'}
    write(schema_path, schema)
    # Production has no GPT dispatch in this core; no H5 is loaded or exported.
    profile = core / 'config/dlpc_execution_profile.json'
    write(profile, {'profile': 'standalone-' + architecture + '-' + device, 'device': device,
        'architecture': architecture, 'environmentReceipts': environment,
        'parentCoreSha256': parent['coreManifestSha256'], 'productionApproved': False})
    preflight = core / 'config/release_preflight_identity.json'
    doc = json.loads(preflight.read_bytes())
    doc['release_blackbox_runner_sha256'] = digest(runner)
    write(preflight, doc)
    write(core / 'config/standalone_wrapper_identity.json', {
        'files': {p.relative_to(PACKAGE).as_posix(): digest(p)
            for p in sorted((PACKAGE / 'local').glob('*.py'))},
        'assetManifestSha256': digest(PACKAGE / 'manifests/assets.json')})
    manifest = json.loads((core / 'release_manifest.original.json').read_bytes())
    # This is a local source subset, so build its complete file inventory anew.
    manifest.update(release_version='seungun-standalone-20261019-' + architecture + '-' + device,
        parent_manifest_sha256=parent['coreManifestSha256'], release_root=str(core),
        deployed=False, production_modified=False)
    manifest['file_sha256'] = {p.relative_to(core).as_posix(): digest(p)
        for p in sorted(core.rglob('*')) if p.is_file() and p.name != 'release_manifest.json'}
    write(core / 'release_manifest.json', manifest)
    link(LATENCY / 'source', core / 'source')
    config = json.loads((PACKAGE / 'vendor/recovery/alignment-config.json').read_bytes())
    write(RECOVERY / 'configs/candidate_alignment_config.json', config)
    link(RECOVERY / 'scripts/mfa_local_only_entry.py', PACKAGE / 'vendor/recovery/scripts/mfa_local_only_entry.py')
    link(RECOVERY / 'mfa_preflight_optimization_20260922T043055Z_0d7f6a/candidate/mfa.py',
        PACKAGE / 'vendor/recovery/candidate/mfa.py')
    link(VALIDATION / 'native_korean_phone_eval_20260928T1109KST/native_model/snapshot', cache / 'native')
    link(VALIDATION / 'p1_native_demo_refinement_20260928T1310KST/contract/refined_p1_policy_contract.json',
        PACKAGE / 'assets/policy.json')
    link(Path(config['aihub71557']['reference_path']), PACKAGE / 'assets/reference.json')
    revision = '0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd'
    # Use ordinary files here: the frozen resolver requires snapshot containment.
    snapshot = MFA_CACHE / 'huggingface/models--MontrealCorpusTools--korean_mfa/snapshots' / revision
    shutil.copytree(cache / 'mfa', snapshot)
    write(MFA_CACHE / 'manifests/korean_mfa.json', {'schema_version': '1.0.0',
        'repository': 'MontrealCorpusTools/korean_mfa', 'revision': revision, 'license': 'CC-BY-4.0',
        'files': [{'path': e['path'].removeprefix('mfa/'), 'size_bytes': e['bytes'], 'sha256': e['sha256']}
            for e in entries() if e['path'].startswith('mfa/')]})
    nltk_tree(cache, Path('/runtime/nltk/corpora'))
    lock = Path('/run/intelligentai/gpu.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch(mode=0o600, exist_ok=False)
    os.environ.update(SEUNGUN_DEVICE='cuda:0' if device == 'cuda' else 'cpu',
        NLTK_DATA='/runtime/nltk', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1', PYTHONDONTWRITEBYTECODE='1',
        TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
        OPENBLAS_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
    runtime_cxx = APP_PY.parent.parent / 'lib/libstdc++.so.6'
    if not runtime_cxx.is_file():
        raise ValueError('APP_CXX_RUNTIME_MISSING')
    # Load the environment's ABI before torch/ICU; never reuse host libraries.
    os.environ['LD_PRELOAD'] = str(runtime_cxx)
    return core, {'parentCoreSha256': parent['coreManifestSha256'],
        'localCoreManifestSha256': digest(core / 'release_manifest.json'),
        'profile': 'standalone-' + architecture + '-' + device, 'productionIdentity': False,
        'architecture': architecture, 'environmentReceipts': environment,
        'gptEnabled': False, 'apiAccessRequired': False}
