"""Source adaptation for one Native load per bounded three-request generation."""
from release_packaging import replace_once


def resident_runner(runner):
    runner = replace_once(runner, "from concurrent.futures import ThreadPoolExecutor",
                          "from functools import lru_cache\nfrom concurrent.futures import ThreadPoolExecutor")
    runner = replace_once(runner, "def checked_contract():", "@lru_cache(maxsize=1)\ndef checked_contract():")
    runner = replace_once(runner, "def checked_contract():\n", "def checked_contract():\n    started = time.perf_counter()\n    cpu_started = time.process_time()\n")
    runner = replace_once(runner, "    return z\n", "    print(json.dumps(dict(event='native_contract_prepare', elapsedSeconds=time.perf_counter()-started, cpuSeconds=time.process_time()-cpu_started)), flush=True)\n    return z\n")
    runner = replace_once(runner,
        "    assert sha(MODEL / 'pytorch_model.bin') == WEIGHTS_SHA, 'NATIVE_WEIGHT_SHA_MISMATCH'",
        "    weights_started, weights_cpu = time.perf_counter(), time.process_time()\n"
        "    assert sha(MODEL / 'pytorch_model.bin') == WEIGHTS_SHA, 'NATIVE_WEIGHT_SHA_MISMATCH'\n"
        "    print(json.dumps(dict(event='native_weight_hash', elapsedSeconds=time.perf_counter()-weights_started,\n"
        "        cpuSeconds=time.process_time()-weights_cpu, fileBytes=(MODEL/'pytorch_model.bin').stat().st_size)), flush=True)")
    runner = replace_once(runner, "        self.real = G2pService()", "        self.real = load_g2p()")
    marker = "def run_batch(cases, output: Path, parallel: bool, blackbox_socket: str | None = None, blackbox_runtime: str | None = None):"
    loader = '''@lru_cache(maxsize=1)
def load_g2p():
    return G2pService()


@lru_cache(maxsize=1)
def load_native():
    from native_core_adapter import assessment_policy
    assessment_policy()  # Fail before loading models when calibration is missing.
    from native_scoring.native_phone_contract import NUMPY_VERSION
    if np.__version__ != NUMPY_VERSION:
        raise RuntimeError('NATIVE_NUMERIC_RUNTIME_VERSION')
    checked_contract()
    torch.set_num_threads(8)
    if not torch.backends.mkldnn.is_available():
        raise RuntimeError('MKLDNN_UNAVAILABLE')
    vr = json.loads((MODEL/'vocab.json').read_text()); vr.update(json.loads((MODEL/'added_tokens.json').read_text()))
    from native_scoring.native_phone_assessment import validate_vocabulary
    validate_vocabulary(vr)
    vocab = {str(v): k for k, v in vr.items()}
    fe = Wav2Vec2FeatureExtractor.from_pretrained(str(MODEL), local_files_only=True)
    model = Wav2Vec2ForCTC.from_pretrained(str(MODEL), local_files_only=True).to('cpu').float().eval()
    return vr, vocab, fe, model


'''
    runner = replace_once(runner, marker, loader + marker.replace("):", ", resident=None):"))
    start = runner.index("    torch.set_num_threads(8)", runner.index("def run_batch("))
    end = runner.index("    spec = importlib.util.spec_from_file_location", start)
    runner = runner[:start] + '''    if resident is None:
        raise RuntimeError('NATIVE_RESIDENT_REQUIRED_NO_ONESHOT_FALLBACK')
    native_load_start = time.perf_counter()
    vr, vocab, fe, model = resident['native']
    native_load_done = time.perf_counter()
    client, version, startup = resident['client'], resident['version'], resident['startup']
    assert version['version'] == '3.4.1'
    assert startup['ready']['direct_candidate'] and startup['ready']['aligner_class'].endswith('DirectAligner') and startup['ready']['queue_adapter'].get('aliases')
''' + runner[end:]
    runner = replace_once(runner,
        "        close = client.call('close')\n        (output/'mfa_close.json').write_text(json.dumps(close,indent=2)+'\\n')\n    finally:\n        client.close()",
        "    finally:\n        pass  # Resident owner closes the client at the generation boundary.")
    runner = replace_once(runner, "            marks['core_json_done'] = time.perf_counter()",
        "            marks['native_scoring_start'] = time.perf_counter()\n"
        "            from native_core_adapter import attach_native_evidence\n"
        "            attach_native_evidence(envelope, case=case, state=state, vocabulary=vr, legacy=legacy, output=output)\n"
        "            marks['native_scoring_done'] = time.perf_counter()\n"
        "            marks['core_json_done'] = time.perf_counter()")
    return runner
