"""Restore genuine parent Git metadata omitted from the scored source export.

No inference, synthetic commit, credential copying, or tracked-source mutation.
The caller closes admission and drains existing executions before installation.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

SOURCE = Path('/workspace/intelligentai/releases/canonical_ai_pre_llm_20260928_100247Z_421fda')
TARGET = Path('/workspace/intelligentai/releases/canonical_scored_core_20261003_v1')
COMMIT = 'f27d6b31cec30b1174372e51c6bf5d19ee359247'
PINS = {SOURCE: '95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2',
        TARGET: '6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776'}


def require(condition, reason):
    if not condition: raise RuntimeError(reason)


def main():
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    env = dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME='/nonexistent',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_OPTIONAL_LOCKS='0')
    def git(*args):
        result = subprocess.run(['git', *args], env=env, capture_output=True, timeout=60)
        require(result.returncode == 0, 'PROVENANCE_GIT_FAILED')
        return result.stdout.decode().strip()
    for root, pin in PINS.items():
        require(root.resolve(strict=True) == root and root.stat().st_uid == 0,
                'PROVENANCE_ROOT_INVALID')
        raw = (root / 'release_manifest.json').read_bytes()
        require(hashlib.sha256(raw).hexdigest() == pin, 'PROVENANCE_MANIFEST_MISMATCH')
        for name, sha in json.loads(raw)['file_sha256'].items():
            path = root / name
            require(path.resolve(strict=True).is_relative_to(root) and
                    hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'PROVENANCE_SOURCE_MISMATCH')
    require(git('-C', str(SOURCE), 'rev-parse', 'HEAD') == COMMIT, 'PROVENANCE_PARENT_REVISION')
    target = TARGET / '.git'
    if target.exists():
        require(not target.is_symlink() and git('-C', str(TARGET), 'rev-parse', 'HEAD') == COMMIT,
                'PROVENANCE_EXISTING_MISMATCH')
        print(json.dumps({'status': 'PROVENANCE_ALREADY_PRESENT', 'commit': COMMIT})); return
    pending = TARGET / '.git.provenance-pending'
    require(not pending.exists(), 'PROVENANCE_INSTALL_PENDING_INSPECT')
    original_mode = TARGET.stat().st_mode & 0o777
    TARGET.chmod(0o755)
    try:
        # clone creates fresh config with only a local source reference; it does
        # not copy source config, hooks, credentials, or hard-link shared objects.
        git('clone', '--bare', '--no-hardlinks', str(SOURCE), str(pending))
        git('--git-dir', str(pending), 'config', '--unset-all', 'remote.origin.url')
        git('--git-dir', str(pending), 'config', 'core.bare', 'false')
        git('--git-dir', str(pending), 'config', 'core.worktree', str(TARGET))
        git('--git-dir', str(pending), 'config', 'core.hooksPath', '/dev/null')
        git('--git-dir', str(pending), 'config', 'core.fsmonitor', 'false')
        git('--git-dir', str(pending), 'read-tree', COMMIT)
        require(git('--git-dir', str(pending), 'rev-parse', 'HEAD') == COMMIT,
                'PROVENANCE_CLONE_REVISION')
        for path in pending.rglob('*'):
            require(not path.is_symlink(), 'PROVENANCE_SYMLINK')
            path.chmod(0o555 if path.is_dir() else 0o444)
        pending.chmod(0o555)
        os.replace(pending, target)
    finally:
        TARGET.chmod(original_mode)
    require(git('-C', str(TARGET), 'rev-parse', '--show-toplevel') == str(TARGET),
            'PROVENANCE_WORKTREE_MISMATCH')
    changes = git('-C', str(TARGET), 'status', '--porcelain').splitlines()
    print(json.dumps({'status': 'PROVENANCE_INSTALLED', 'commit': COMMIT,
                      'dirty': bool(changes), 'changedFiles': len(changes),
                      'sourceFilesChanged': False}))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        import re
        code = str(error) if re.fullmatch('[A-Z_]+', str(error)) else type(error).__name__
        print(json.dumps({'status': 'PROVENANCE_INSTALL_FAILED', 'reason': code}))
        raise SystemExit(1)
