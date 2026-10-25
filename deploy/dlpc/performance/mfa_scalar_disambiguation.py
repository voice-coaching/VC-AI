"""Keep MFA's SQL and update order while avoiding per-lexicon ORM objects."""
import hashlib
import importlib.metadata
from pathlib import Path

from mfa_disambiguation import original_source, optimized_source

REVISION = 'mfa-disambiguation-core-rows-v1'
DB_SHA = '25c4bcb98da12234158d06144d32d636fb1c671a45bd7278d98bb41568bde921'


def source(raw):
    text = optimized_source(raw)
    before = '''                words = (
                    session.query(Word)
                    .filter(Word.dictionary_id == d.id)
                    .filter(Word.included == True)  # noqa
                    .options(selectinload(Word.pronunciations))
                ).all()
                tokenized = [(p, tuple(p.pronunciation.split()))
                             for w in words for p in w.pronunciations]
'''
    after = '''                word_query = (
                    session.query(Word)
                    .filter(Word.dictionary_id == d.id)
                    .filter(Word.included == True)  # noqa
                    .options(selectinload(Word.pronunciations))
                )
                # Execute the same full-column SELECT through Core. The caller
                # only reads IDs/text; hydrating mapped Word/Pronunciation
                # instances and their relationships adds no semantic value.
                if session.autoflush:
                    session.flush()
                connection = session.connection()
                words = connection.execute(word_query.statement).all()
                by_word = {w.id: [] for w in words}
                # Match the pinned selectin loader's 500-parent batches and
                # unordered relationship SELECT; flatten in parent order.
                for offset in range(0, len(words), 500):
                    ids = [w.id for w in words[offset:offset + 500]]
                    query = session.query(Pronunciation).filter(Pronunciation.word_id.in_(ids))
                    for p in connection.execute(query.statement):
                        by_word[p.word_id].append(p)
                tokenized = [(p, tuple(p.pronunciation.split()))
                             for w in words for p in by_word[w.id]]
'''
    if text.count(before) != 1:
        raise RuntimeError('MFA_SCALAR_SOURCE_CONTEXT')
    text = text.replace(before, after)
    compile(text, 'mfa_scalar_disambiguation_optimized.py', 'exec')
    return text


def install(aligner_class):
    from sqlalchemy.orm.strategies import SelectInLoader
    import montreal_forced_aligner.db as db
    if (importlib.metadata.version('Montreal-Forced-Aligner') != '3.4.1'
            or hashlib.sha256(Path(db.__file__).read_bytes()).hexdigest() != DB_SHA
            or SelectInLoader._chunksize != 500
            or db.Word.pronunciations.property.order_by
            or aligner_class.__module__ != 'direct_adapter'
            or aligner_class.__name__ != 'DirectAligner'):
        raise RuntimeError('MFA_SCALAR_DEPENDENCY_CHANGED')
    # The original module and method are pinned before any compiled override.
    # Both calls, request DB, bulk writes, phone mapping and commits remain.
    from montreal_forced_aligner.dictionary.multispeaker import MultispeakerDictionaryMixin
    original = MultispeakerDictionaryMixin.calculate_disambiguation
    raw = Path(original.__globals__['__file__']).read_bytes()
    _, line = original_source(raw)
    if original.__code__.co_firstlineno != line:
        raise RuntimeError('MFA_SCALAR_ORIGINAL_LOCATION')
    text = source(raw)
    namespace = dict(original.__globals__)
    exec(compile(text, 'mfa_scalar_disambiguation_optimized.py', 'exec'), namespace)
    method = namespace['calculate_disambiguation']
    method.__qualname__ = original.__qualname__
    aligner_class.calculate_disambiguation = method
    return dict(revision=REVISION, installed=True,
                optimizedFunctionSha256=hashlib.sha256(text.encode()).hexdigest(),
                scope='DirectAligner Core rows; full columns, 500-parent batches, original update order')
