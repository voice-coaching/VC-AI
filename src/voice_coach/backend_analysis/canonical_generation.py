"""Reuse full verification only for a parent's read-only release generation."""
from pathlib import Path
import json
import re


def readonly_identity(root):
    """A writable nested bind mount must not bypass a read-only root check."""
    root = root.resolve(strict=True)
    try:
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields = line.split()
            point = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
            mounts.append((point, fields[5].split(','), fields[0]))
        parents = [m for m in mounts if root.is_relative_to(m[0])]
        parent = max(parents, key=lambda m: len(m[0].parts))
        relevant = [parent] + [m for m in mounts if m[0] != parent[0] and m[0].is_relative_to(root)]
        if any('ro' not in opts for _, opts, _ in relevant):
            return None
        info = root.stat()
        return dict(device=info.st_dev, inode=info.st_ino,
                    mounts=sorted(m[2] for m in relevant))
    except (OSError, ValueError, IndexError):
        return None


def write_verification(artifacts, generation, before):
    """Called only by the supervisor immediately after full byte verification."""
    from .canonical_producer import write_private_new
    core, llm = artifacts.core_root.resolve(strict=True), artifacts.llm_root.resolve(strict=True)
    core_mount, llm_mount = readonly_identity(core), readonly_identity(llm)
    if core_mount is None or llm_mount is None or before != (core_mount, llm_mount):
        return False
    identity = artifacts.identity
    record = dict(schema='canonical-generation-verification-v1', coreRoot=str(core), llmRoot=str(llm),
                  coreMount=core_mount, llmMount=llm_mount, coreSha256=identity.core_sha256,
                  llmSha256=identity.manifest_sha256, lockSha256=identity.lock_sha256)
    write_private_new(generation / 'verified-artifacts.json', json.dumps(record).encode())
    return True


def generation_verified(artifacts, identity):
    generation = artifacts.verified_generation
    if generation is None:
        return False
    from .canonical_producer import read_private_result
    from .canonical_process import private_directory
    private_directory(generation)
    path = generation / 'verified-artifacts.json'
    if not path.exists():
        return False  # Mutable installation: retain the full verification path.
    record = json.loads(read_private_result(path, generation, 16384))
    core, llm = artifacts.core_root.resolve(strict=True), artifacts.llm_root.resolve(strict=True)
    cm, lm = readonly_identity(core), readonly_identity(llm)
    return cm is not None and lm is not None and record == dict(
        schema='canonical-generation-verification-v1', coreRoot=str(core), llmRoot=str(llm),
        coreMount=cm, llmMount=lm, coreSha256=identity.core_sha256,
        llmSha256=identity.manifest_sha256, lockSha256=identity.lock_sha256)
