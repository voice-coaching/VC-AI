"""Version-pinned equivalent disambiguation work reduction for DirectAligner only."""
import ast
import hashlib
import importlib.metadata
from pathlib import Path

MODULE_SHA = '3cd456e52810efe9534bdaf30bb79ff2368ed6383ed3849d35b2fa2bb8fb4608'
FUNCTION_SHA = '327efe0b16638b59d93bfd4fe8220e82be7686b4b051cbc02e510e75ff9cfa36'
REVISION = 'mfa-disambiguation-prefix-v1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def original_source(raw):
    if digest(raw) != MODULE_SHA:raise RuntimeError('MFA_DISAMBIGUATION_MODULE_CHANGED')
    source = raw.decode()
    methods = [method for node in ast.walk(ast.parse(source))
               if isinstance(node,ast.ClassDef) and node.name=='MultispeakerDictionaryMixin'
               for method in node.body if isinstance(method,ast.FunctionDef)
               and method.name=='calculate_disambiguation']
    if len(methods)!=1:raise RuntimeError('MFA_DISAMBIGUATION_METHOD_SHAPE')
    text = ast.get_source_segment(source,methods[0])
    if digest(text.encode()) != FUNCTION_SHA:raise RuntimeError('MFA_DISAMBIGUATION_METHOD_CHANGED')
    return text,methods[0].lineno


def optimized_source(raw):
    text,_ = original_source(raw)
    changes = (
        ('                subsequences = set()\n',''),
        ('                for w in words:\n'
         '                    for p in w.pronunciations:\n'
         '                        pron = p.pronunciation.split()\n'
         '                        while len(pron) > 0:\n'
         '                            subsequences.add(tuple(pron))\n'
         '                            pron = pron[:-1]\n',
         '                tokenized = [(p, tuple(p.pronunciation.split()))\n'
         '                             for w in words for p in w.pronunciations]\n'),
        ('                for w in words:\n'
         '                    for p in w.pronunciations:\n'
         '                        pron = p.pronunciation\n'
         '                        pron = tuple(pron.split())\n'
         '                        if pron in subsequences:\n'
         '                            last_used[pron] += 1\n\n'
         '                            update_pron_objs.append(\n'
         '                                {"id": p.id, "disambiguation": last_used[pron]}\n'
         '                            )\n',
         '                for p, pron in tokenized:\n'
         '                    if pron:\n'
         '                        last_used[pron] += 1\n\n'
         '                        update_pron_objs.append(\n'
         '                            {"id": p.id, "disambiguation": last_used[pron]}\n'
         '                        )\n'),
    )
    # Every nonempty full pronunciation was inserted into the old prefix set.
    # Keep the initial tokenization pass, iteration order and all DB writes.
    for before,after in changes:
        if text.count(before)!=1:raise RuntimeError('MFA_DISAMBIGUATION_SOURCE_CONTEXT')
        text = text.replace(before,after)
    compile(text,'mfa_disambiguation_optimized.py','exec')
    return text


def install(aligner_class):
    if importlib.metadata.version('Montreal-Forced-Aligner')!='3.4.1':
        raise RuntimeError('MFA_DISAMBIGUATION_VERSION')
    if (aligner_class.__module__,aligner_class.__name__)!=('direct_adapter','DirectAligner'):
        raise RuntimeError('MFA_DISAMBIGUATION_ALIGNER')
    if 'calculate_disambiguation' in aligner_class.__dict__:
        raise RuntimeError('MFA_DISAMBIGUATION_ALREADY_OVERRIDDEN')
    original = aligner_class.calculate_disambiguation
    if (original.__module__,original.__qualname__) != (
            'montreal_forced_aligner.dictionary.multispeaker',
            'MultispeakerDictionaryMixin.calculate_disambiguation'):
        raise RuntimeError('MFA_DISAMBIGUATION_ORIGIN')
    raw = Path(original.__globals__['__file__']).read_bytes()
    _,line = original_source(raw)
    if original.__code__.co_firstlineno!=line:raise RuntimeError('MFA_DISAMBIGUATION_CODE_LOCATION')
    source = optimized_source(raw)
    namespace = dict(original.__globals__)
    exec(compile(source,'mfa_disambiguation_optimized.py','exec'),namespace)
    method = namespace['calculate_disambiguation']
    method.__qualname__ = original.__qualname__
    aligner_class.calculate_disambiguation = method
    return dict(revision=REVISION,installed=True,originalModuleSha256=MODULE_SHA,
        originalFunctionSha256=FUNCTION_SHA,optimizedFunctionSha256=digest(source.encode()),
        scope='DirectAligner only; both calls and request-local state preserved')
