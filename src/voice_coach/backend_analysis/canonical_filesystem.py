"""Fail-closed local journal mount check, not a backup/durability attestation."""
from __future__ import annotations

from pathlib import Path
import re
import sys

from .canonical_bridge import _require


def require_local_durable_mount(root: Path) -> None:
    """Reject known unsuitable storage before creating private durable state.

    A dedicated ext4/xfs/btrfs/zfs mount is necessary, not sufficient: its volume
    lifecycle and backup/restore remain deployment checks. Do not equate an
    existing /var/lib directory inside a container overlay with persistence.
    """
    _require(sys.platform == 'linux' and root.is_absolute()
             and root.resolve(strict=True) == root, 'JOURNAL_LINUX_PATH_REQUIRED')
    mounted = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields, storage = before.split(), after.split()
        location = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        if root.is_relative_to(location):
            mounted.append((len(location.parts), location, fields[5].split(','), storage[0]))
    _require(bool(mounted), 'JOURNAL_MOUNT_UNVERIFIED')
    # Nested mounts override their parents. FUSE/NFS/overlay/tmpfs and host-file
    # bind mounts cannot accidentally pass because their backing host uses ext4.
    _, location, options, filesystem = max(mounted, key=lambda item: item[0])
    _require(location != Path('/') and location.is_dir() and 'rw' in options
             and filesystem in {'ext4', 'xfs', 'btrfs', 'zfs'},
             'JOURNAL_DURABLE_LOCAL_MOUNT_REQUIRED')
