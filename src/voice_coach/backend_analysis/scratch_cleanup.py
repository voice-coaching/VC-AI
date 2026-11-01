"""Remove owned scratch after process reclamation; preserve transport evidence."""
import os
from pathlib import Path
import re
import shutil
import stat

from .canonical_process import private_directory
from .canonical_producer import read_private_result
from .http_models import parse_json

AUDIO_SUFFIXES = frozenset(('.wav', '.mp3', '.m4a', '.webm', '.flac', '.ogg', '.pcm'))


def remove_audio(root: Path) -> None:
    """Keep JSON/digests/journals; remove only audio in a private execution tree."""
    if not root.exists():
        return
    private_directory(root)
    for base, directories, files in os.walk(root, followlinks=False):
        directories[:] = [d for d in directories if not (Path(base) / d).is_symlink()]
        for name in files:
            path = Path(base) / name
            if path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError('AUDIO_CLEANUP_PATH_INVALID')
            path.unlink()


def remove_generation(generation: Path) -> None:
    """Caller must have reaped the guard and all MFA/native descendants first."""
    private_directory(generation)
    # The frozen launcher records its separate local scratch here. Never glob
    # across other generations, including a concurrently preparing standby.
    preflight = generation / 'mfa_runtime/preflight.json'
    if preflight.exists():
        record = parse_json(read_private_result(preflight, generation, 131072))
        local = Path(record['local'])
        if (local.parent != Path('/tmp')
                or re.fullmatch(r'canonical_ai_release_[0-9a-f]{12}', local.name) is None):
            raise ValueError('RESIDENT_CLEANUP_PATH_INVALID')
        if local.exists():
            private_directory(local)
            shutil.rmtree(local)
    shutil.rmtree(generation)
