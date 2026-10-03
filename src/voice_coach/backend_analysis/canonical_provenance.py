"""Read-only Git provenance required by the frozen core after inference."""
from pathlib import Path
import subprocess

from .canonical_bridge import _require

PARENT_COMMIT = 'f27d6b31cec30b1174372e51c6bf5d19ee359247'


def git_environment(core_root: Path) -> dict[str, str]:
    # Trust only this verified release, never every repository on the host.
    return dict(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='safe.directory',
                GIT_CONFIG_VALUE_0=str(core_root), GIT_OPTIONAL_LOCKS='0')


def check_provenance(core_root: Path, environment: dict[str, str]) -> None:
    _require(core_root.is_absolute() and core_root.resolve(strict=True) == core_root,
             'CANONICAL_PROVENANCE_PATH')
    try:
        def git(*args):
            result = subprocess.run(['git', '-C', str(core_root / 'source'), *args],
                env=environment, capture_output=True, timeout=10, check=False)
            _require(result.returncode == 0, 'CANONICAL_PROVENANCE_UNAVAILABLE')
            return result.stdout.decode().strip()
        _require(git('rev-parse', '--show-toplevel') == str(core_root),
                 'CANONICAL_PROVENANCE_PATH')
        _require(git('rev-parse', 'HEAD') == PARENT_COMMIT,
                 'CANONICAL_PROVENANCE_REVISION')
        git('status', '--porcelain')
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        _require(False, 'CANONICAL_PROVENANCE_UNAVAILABLE')
