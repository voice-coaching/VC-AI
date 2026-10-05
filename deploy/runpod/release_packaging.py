"""Hash-pinned source packaging, without inference or release selection."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()

def load_files(root, name, pin, key):
    root = root.resolve(strict=True)
    raw = (root / name).read_bytes()
    if sha(raw) != pin:
        raise ValueError('DERIVATIVE_PARENT_IDENTITY')
    manifest = json.loads(raw)
    files = {}
    for relative, digest in manifest[key].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError('DERIVATIVE_SOURCE_PATH')
        content = path.read_bytes()
        if sha(content) != digest:
            raise ValueError('DERIVATIVE_SOURCE_HASH')
        files[relative] = content
    return (manifest, files)

def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('DERIVATIVE_SOURCE_CONTEXT_CHANGED')
    return text.replace(before, after)

def remove_region(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('DERIVATIVE_SOURCE_CONTEXT_CHANGED')
    a, b = (text.index(start), text.index(end))
    if b <= a:
        raise ValueError('DERIVATIVE_SOURCE_ORDER_CHANGED')
    return text[:a] + text[b:]

def write_release(target, files):
    if target.exists():
        raise ValueError('DERIVATIVE_REQUIRES_NEW_DIRECTORY')
    for name, raw in files.items():
        if name.endswith('.py'):
            compile(raw, name, 'exec')
    target.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
