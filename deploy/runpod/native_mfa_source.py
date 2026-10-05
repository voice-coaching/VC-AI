"""Adapt pinned MFA transport; keep request cleanup and three-request limits."""
import json
from release_packaging import encode, replace_once, sha


def resident_mfa(files):
    name = 'runtime_bundle/mfa_sidecar.py'
    source = files[name].decode()
    start = source.index(" if op=='version':")
    end = source.index(" if op=='control':", start)
    source = source[:start] + ''' if op=='version':
  if version is not None:return version
  # The real worker already verifies distribution version and loaded module paths.
  # Do not launch the complete MFA CLI solely to print its version.
  admission(CFG)
  S=BoundedSupervisor(CFG,lambda g:WorkerTransport(g,CFG,O,L,ENV,SOURCE,MFA_PY),admission)
  elapsed=S.prestart();ready=S.worker.ready
  if ready.get('version')!='3.4.1' or not ready.get('module_files') or not ready.get('direct_candidate'):
   raise RuntimeError('WORKER_VERSION_IDENTITY')
  version={'version':ready['version'],'source':'VERIFIED_RESIDENT_WORKER','worker_pid':S.worker.pid}
  state['version']=version
  state['startup']={'startup_s':elapsed,'ready':ready,'pid':S.worker.pid}
  save();return version
''' + source[end:]
    start = source.index(" if op=='start':")
    end = source.index(" if op=='override':", start)
    source = source[:start] + ''' if op=='start':
  if S is None or version is None:raise RuntimeError('START_SEQUENCE')
  return state['startup']
''' + source[end:]
    files[name] = source.encode()
    # Request cleanup has already passed at this point. Complete the result
    # future before generation shutdown, but do not advertise another slot.
    name = 'runtime_bundle/bounded_supervisor.py'
    source = files[name].decode()
    source = replace_once(source,
        "                    end=self.drain(reason);rec['shutdown_s']=end['elapsed_s'];rec['recycle_reason']=reason",
        "                    rec['recycle_reason']=reason;rec['generation_shutdown']='PENDING'\n"
        "                    rec['acceptance_to_response_s']=time.monotonic()-accepted\n"
        "                    self.results.append(rec);f.set_result(rec)\n"
        "                    self.drain(reason)\n"
        "                    continue")
    source = replace_once(source, "                f.set_exception(e)",
                          "                if not f.done():f.set_exception(e)")
    files[name] = source.encode()
    # prepare() verifies and copies these bytes. Update the derivative identity,
    # never mutate the preserved parent's manifest or the historical package.
    identity = json.loads(files['config/historical_runtime_identity.json'])
    for relative, record in identity['source_files'].items():
        raw = files['runtime_bundle/' + relative]
        record['sha256'] = sha(raw)
        if 'bytes' in record:
            record['bytes'] = len(raw)
    files['config/historical_runtime_identity.json'] = encode(identity)
