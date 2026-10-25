"""Bounded worker-local FST cache, keyed by complete compiler input state."""
from collections import OrderedDict
import copy
from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
from pathlib import Path
import textwrap
from typing import Any

from mfa_disambiguation import MODULE_SHA

LEXICON_SHA = '61c0715620828eeb4e496813cb0c688977d1a38ded83bc932a898e6cde7c762e'
REVISION = 'request-owned-lexicon-fst-cache-v1'
MAX_BYTES = 64 * 1024**2
MAX_ENTRIES = 1


def canonical(value):
    if value is None or type(value) in (str, bool, int, float):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return canonical(asdict(value))
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, set):
        return sorted(canonical(v) for v in value)
    raise ValueError('LEXICON_CACHE_INPUT_TYPE')


def install(aligner_class):
    import kalpy.fstext.lexicon as lexicon
    from montreal_forced_aligner.dictionary.multispeaker import MultispeakerDictionaryMixin
    original = MultispeakerDictionaryMixin.build_lexicon_compiler
    module = Path(original.__globals__['__file__'])
    if (hashlib.sha256(module.read_bytes()).hexdigest() != MODULE_SHA
            or hashlib.sha256(Path(lexicon.__file__).read_bytes()).hexdigest() != LEXICON_SHA
            or aligner_class.__module__ != 'direct_adapter'):
        raise RuntimeError('LEXICON_CACHE_DEPENDENCY_CHANGED')
    if (not hasattr(lexicon.pywrapfst.SymbolTable, '__iter__')
            or not all(hasattr(lexicon.pynini.Fst, name) for name in ('copy', 'write_to_string'))):
        raise RuntimeError('LEXICON_CACHE_COPY_UNAVAILABLE')
    cache: OrderedDict[bytes, dict[str, Any]] = OrderedDict()
    stored = [0]

    class CachedCompiler(lexicon.LexiconCompiler):
        _fst: Any
        _align_fst: Any
        def create_fsts(self, phonological_rule_fst=None):
            from mfa_trace import span
            if self._fst is not None and self._align_fst is not None:
                return
            # A caller-supplied rule FST can carry mutable state; do not cache it.
            if phonological_rule_fst is not None:
                return super().create_fsts(phonological_rule_fst)
            state = {k: canonical(v) for k, v in vars(self).items()
                     if k not in ('word_table', 'phone_table', '_fst', '_align_fst', '_align_lexicon')}
            state['word_table'] = list(self.word_table)
            state['phone_table'] = list(self.phone_table)
            key = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False).encode()).digest()
            if key in cache:
                with span('lexicon_fst_cache_hit'):
                    entry = cache[key]
                    # Never pass a cached mutable FST to graph/align operations.
                    self._fst = entry['fst'].copy()
                    self._align_fst = entry['align'].copy()
                    for name, value in entry['state'].items():
                        setattr(self, name, copy.deepcopy(value))
                    cache.move_to_end(key)
                return
            with span('lexicon_fst_cache_miss'):
                super().create_fsts(phonological_rule_fst)
            # Pinned create_fsts/add_pronunciation mutate only the FSTs and the
            # primitive cached-pronunciation set. Keep all remaining post-state.
            post = {k: copy.deepcopy(v) for k, v in vars(self).items()
                    if k not in ('word_table', 'phone_table', '_fst', '_align_fst', '_align_lexicon', 'pronunciations')}
            if state['word_table'] != list(self.word_table) or state['phone_table'] != list(self.phone_table):
                return  # Never omit symbol-table side effects on a cache hit.
            size = 4*(len(self._fst.write_to_string()) + len(self._align_fst.write_to_string()))
            size += 4*len(json.dumps(state, ensure_ascii=False).encode())
            if size > MAX_BYTES:
                return
            while cache and (len(cache) >= MAX_ENTRIES or stored[0]+size > MAX_BYTES):
                _, old = cache.popitem(last=False)
                stored[0] -= old['bytes']
            cache[key] = dict(fst=self._fst.copy(), align=self._align_fst.copy(), state=post, bytes=size)
            stored[0] += size

    # Override this DirectAligner method's compiler binding only. The original
    # ordered query, disambiguation, file writes and clear() still execute.
    source = textwrap.dedent(inspect.getsource(original))
    namespace = dict(original.__globals__, LexiconCompiler=CachedCompiler)
    exec(compile(source, 'mfa_cached_lexicon_method.py', 'exec'), namespace)
    aligner_class.build_lexicon_compiler = namespace['build_lexicon_compiler']
    return dict(revision=REVISION, maxEntries=MAX_ENTRIES, maxBytes=MAX_BYTES,
                requestOwnedFstCopies=True, originalSqlAndWrites=True)
