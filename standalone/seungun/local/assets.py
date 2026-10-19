"""Pinned public asset acquisition; analysis itself never downloads anything."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

PACKAGE = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def entries() -> list[dict]:
    return json.loads((PACKAGE / 'manifests/assets.json').read_bytes())['files']


def destination(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or '\\' in relative:
        raise ValueError('ASSET_PATH_INVALID')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('ASSET_PATH_INVALID')
    return path


def valid(path: Path, item: dict) -> bool:
    return path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']


def verify(root: Path) -> dict:
    missing = [item['path'] for item in entries() if not valid(destination(root, item['path']), item)]
    return {'status': 'ready' if not missing else 'not_ready', 'missingOrInvalid': missing,
        'assetManifestSha256': digest(PACKAGE / 'manifests/assets.json'),
        'inferencePerformed': False, 'networkRequiredForAnalysis': False}


def fetch(root: Path) -> dict:
    """Resume partial downloads, enforce exact size/hash, then atomically publish."""
    root.mkdir(parents=True, exist_ok=True)
    for item in entries():
        path = destination(root, item['path'])
        if valid(path, item):
            continue
        if path.exists():
            raise ValueError('ASSET_INVALID_REMOVE_OR_RELOCATE_CACHE: ' + item['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination(root, item['path'] + '.part')
        offset = temporary.stat().st_size if temporary.exists() else 0
        if offset > item['bytes']:
            raise ValueError('PARTIAL_ASSET_OVERSIZED')
        if offset != item['bytes']:
            request = urllib.request.Request(item['url'], headers={'Accept-Encoding': 'identity'})
            if offset:
                request.add_header('Range', f'bytes={offset}-')
            with urllib.request.urlopen(request, timeout=120) as response:
                append = offset > 0 and response.status == 206
                if response.status not in (200, 206):
                    raise ValueError('ASSET_HTTP_STATUS')
                if response.status == 206:
                    start = offset if append else 0
                    if response.headers.get('Content-Range') != f'bytes {start}-{item["bytes"]-1}/{item["bytes"]}':
                        raise ValueError('ASSET_RANGE_MISMATCH')
                total = offset if append else 0
                with temporary.open('ab' if append else 'wb') as stream:
                    while chunk := response.read(1024**2):
                        total += len(chunk)
                        if total > item['bytes']:
                            raise ValueError('ASSET_RESPONSE_OVERSIZED')
                        stream.write(chunk)
        if not valid(temporary, item):
            raise ValueError('ASSET_SIZE_OR_HASH_MISMATCH: ' + item['path'])
        temporary.replace(path)
    return verify(root)


def nltk_tree(cache: Path, target: Path) -> None:
    """Extract only ordinary, bounded files from the hash-verified cmudict ZIP."""
    target.mkdir(parents=True, exist_ok=False)
    total = 0
    with zipfile.ZipFile(cache / 'nltk/cmudict.zip') as archive:
        for item in archive.infolist():
            if not item.filename.startswith('cmudict/') or item.is_dir():
                continue
            total += item.file_size
            if total > 32 * 1024**2 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('NLTK_ARCHIVE_INVALID')
            path = destination(target, item.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, path.open('xb') as output:
                shutil.copyfileobj(source, output)
