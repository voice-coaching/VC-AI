"""Apply a hash-guarded Korean-only frontend to the existing Melo deployment."""
import hashlib
import json
from pathlib import Path
import shutil


BASELINE = {
    '__init__.py': 'eca8c17764c4dcd57d3a71185775d1c6e4d53689d6a9a54708ec63577a2ac8ba',
    'cleaner.py': '6871dcc3fa9c86389253ef6dfc988050ac978486dbfeb0a1d6aaae98b68ec52a',
    'korean.py': '54c05e845c676f55fb8e93ed775be3581c3dc267b425c4209a5599fb1b7b8890',
    'japanese_bert.py': '9095dcf2c0725ec8f96ed167983b07e93a897c9034fb8f1544b482337eb12acf',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure(root):
    source = root / 'MeloTTS/melo/text'
    backup = root / 'source-backups/korean-runtime-v1'
    state = root / 'korean-runtime.json'
    if state.exists():
        recorded = json.loads(state.read_text())
        for name, sha in recorded['files'].items():
            if digest(source / name) != sha:
                raise RuntimeError('KOREAN_RUNTIME_SOURCE_DRIFT')
        if any((source / name).exists() for name in recorded['removed']):
            raise RuntimeError('RETIRED_FRONTEND_REINTRODUCED')
        return

    for name, sha in BASELINE.items():
        if digest(source / name) != sha:
            raise RuntimeError('MELO_BASELINE_SOURCE_DRIFT')
    names = list(BASELINE) + ['japanese.py']
    backup.mkdir(parents=True, exist_ok=False)
    for name in names:
        shutil.copy2(source / name, backup / name)

    # Preserve the pretrained model's complete symbol IDs and tone offsets.
    init = (source / '__init__.py').read_text()
    init = init[:init.index('def get_bert(')] + '''def get_bert(norm_text, word2ph, language, device):
    if language != "KR":
        raise ValueError("UNSUPPORTED_TTS_LANGUAGE")
    from .korean import get_bert_feature
    return get_bert_feature(norm_text, word2ph, device)
'''
    cleaner = (source / 'cleaner.py').read_text()
    cleaner = cleaner.replace('from . import chinese, japanese, english, chinese_mix, korean, french, spanish',
                              'from . import korean')
    start = cleaner.index('language_module_map = ')
    end = cleaner.index('\n\n', start)
    cleaner = cleaner[:start] + 'language_module_map = {"KR": korean}' + cleaner[end:]
    korean = (source / 'korean.py').read_text().replace('from . import japanese_bert', 'from . import korean_bert')
    korean = korean.replace('return japanese_bert.get_bert_feature(', 'return korean_bert.get_bert_feature(')
    bert = (source / 'japanese_bert.py').read_text().replace(
        "model_id='tohoku-nlp/bert-base-japanese-v3'", "model_id='kykim/bert-kor-base'")
    changes = {'__init__.py': init, 'cleaner.py': cleaner, 'korean.py': korean, 'korean_bert.py': bert}
    for name, text in changes.items():
        compile(text, name, 'exec')
    try:
        for name, text in changes.items():
            (source / name).write_text(text)
        removed = ['japanese.py', 'japanese_bert.py']
        for name in removed:
            (source / name).unlink()
        # Python will not load orphaned __pycache__ entries; remove only those
        # belonging to retired modules for a clean active source directory.
        cache = source / '__pycache__'
        if cache.is_dir():
            for pattern in ('japanese.*.pyc', 'japanese_bert.*.pyc'):
                for path in cache.glob(pattern):
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
        state.write_text(json.dumps({'language': 'KR', 'files': {name: digest(source / name) for name in changes},
                                     'removed': removed, 'backup': str(backup)}, indent=2))
    except Exception:
        for name in names:
            shutil.copy2(backup / name, source / name)
        raise


if __name__ == '__main__':
    configure(Path('/workspace/voice-coaching-tts'))
