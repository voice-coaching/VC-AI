"""Build a new immutable bounded resident release; never modify running roots."""
import argparse
import json
from pathlib import Path
from build_canonical_scored import load_files, replace_once, sha, encode, write_release

CORE_PARENT = '08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80'
LLM_PARENT = 'e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3'
LOCK_PARENT = 'dc3db9cc03a3355ddeb5d5819cd6fd46726935c8ca9b830f513e37163c89f4d8'
CORE_DIRECTORY = 'canonical_resident_core_20261004_v1'
LLM_DIRECTORY = 'canonical_resident_h5_20261004_v1'


def build(core_source, llm_source, destination):
    manifest, files = load_files(core_source, 'release_manifest.json', CORE_PARENT, 'file_sha256')
    runner = files['source/scripts/run_canonical_batch.py'].decode()
    runner = replace_once(runner, 'from concurrent.futures import ThreadPoolExecutor',
                          'from functools import lru_cache\nfrom concurrent.futures import ThreadPoolExecutor')
    runner = replace_once(runner, 'def checked_contract():', '@lru_cache(maxsize=1)\ndef checked_contract():')
    marker = 'def run_batch(cases, output: Path, parallel: bool, blackbox_socket: str | None = None, blackbox_runtime: str | None = None):'
    loader = '''@lru_cache(maxsize=1)
def load_native():
    checked_contract()
    torch.set_num_threads(8)
    if not torch.backends.mkldnn.is_available():
        raise RuntimeError('MKLDNN_UNAVAILABLE')
    vr = json.loads((MODEL/'vocab.json').read_text()); vr.update(json.loads((MODEL/'added_tokens.json').read_text()))
    vocab = {str(v): k for k, v in vr.items()}
    fe = Wav2Vec2FeatureExtractor.from_pretrained(str(MODEL), local_files_only=True)
    model = Wav2Vec2ForCTC.from_pretrained(str(MODEL), local_files_only=True).to('cpu').float().eval()
    return vr, vocab, fe, model


'''
    runner = replace_once(runner, marker, loader + marker.replace('):', ', resident=None):'))
    start, end = runner.index('    torch.set_num_threads(8)', runner.index('def run_batch(')), runner.index('    spec = importlib.util.spec_from_file_location')
    runner = runner[:start] + '''    native_load_start = time.perf_counter()
    vr, vocab, fe, model = resident['native'] if resident else load_native()
    native_load_done = time.perf_counter()
    client = resident['client'] if resident else BlackBoxMFAClient(blackbox_socket)
    version = resident['version'] if resident else client.call('version')
    assert version['version'] == '3.4.1'
    startup = resident['startup'] if resident else client.call('start')
    assert startup['ready']['direct_candidate'] and startup['ready']['aligner_class'].endswith('DirectAligner') and startup['ready']['queue_adapter'].get('aliases')
''' + runner[end:]
    runner = replace_once(runner, "        close = client.call('close')\n        (output/'mfa_close.json').write_text(json.dumps(close,indent=2)+'\\n')\n    finally:\n        client.close()",
        "        if resident is None:\n            close = client.call('close')\n            (output/'mfa_close.json').write_text(json.dumps(close,indent=2)+'\\n')\n    finally:\n        if resident is None:\n            client.close()")
    files['source/scripts/run_canonical_batch.py'] = runner.encode()
    files['scripts/run_resident.py'] = (Path(__file__).parent / 'harnesses/canonical_resident_server.py').read_bytes().replace(b'\r\n', b'\n')
    identity = json.loads(files['config/release_preflight_identity.json'])
    identity['release_blackbox_runner_sha256'] = sha(runner.encode())
    identity['status'] = 'DERIVED_NOT_INFERENCE_VALIDATED'
    identity['gate_config']['path'] = '/workspace/intelligentai/releases/' + CORE_DIRECTORY + '/calibration/content_gate_removed_v1.json'
    files['config/release_preflight_identity.json'] = encode(identity)
    manifest.update(release_version=CORE_DIRECTORY, parent_manifest_sha256=CORE_PARENT,
                    release_root='/workspace/intelligentai/releases/' + CORE_DIRECTORY,
                    verification_scope='source integrity and compilation only; developer inference QA required')
    manifest['file_sha256'] = {name: sha(raw) for name, raw in sorted(files.items())}
    core_raw = encode(manifest)
    files['release_manifest.json'] = core_raw
    write_release(destination / CORE_DIRECTORY, files)
    _, llm = load_files(llm_source, 'contracts/llm/dependency_lock.json', LOCK_PARENT, 'files')
    raw = (llm_source / 'contracts/llm/llm_contract_manifest.json').read_bytes()
    if sha(raw) != LLM_PARENT: raise ValueError('HARNESS_PARENT_MISMATCH')
    # Only producer identity changes. Rubric, prompts, model and scoring stay byte-identical.
    llm['bridge/adapter.py'] = replace_once(llm['bridge/adapter.py'].decode(), CORE_PARENT, sha(core_raw)).encode()
    lock = encode(dict(files={name: sha(value) for name, value in sorted(llm.items())}))
    lm = json.loads(raw)
    lm.update(contract_version=LLM_DIRECTORY, parent_llm_manifest_sha256=LLM_PARENT,
              parent_core_manifest_sha256=sha(core_raw), dependency_lock_sha256=sha(lock),
              changed_scope='bounded resident core identity only; unchanged H5 and rubric')
    llm_raw = encode(lm)
    llm['contracts/llm/dependency_lock.json'] = lock
    llm['contracts/llm/llm_contract_manifest.json'] = llm_raw
    write_release(destination / LLM_DIRECTORY, llm)
    return dict(coreManifestSha256=sha(core_raw), manifestSha256=sha(llm_raw), lockSha256=sha(lock))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--core-source', type=Path, required=True)
    p.add_argument('--llm-source', type=Path, required=True)
    p.add_argument('--destination', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(build(a.core_source, a.llm_source, a.destination)))
