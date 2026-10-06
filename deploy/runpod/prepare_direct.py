"""Prepare direct execution scratch and a separately scoped archive environment."""
import os
from pathlib import Path

from prepare_worker import _write_private
from prepare_canonical import decode


def prepare(values):
    workspace = Path(values["AI_DIRECT_WORKSPACE_ROOT"])
    if not workspace.is_absolute() or not str(workspace).startswith("/run/intelligentai/"):
        raise ValueError("DIRECT_WORKSPACE_INVALID")
    workspace.mkdir(mode=0o700, exist_ok=True)
    if workspace.is_symlink():
        raise ValueError("DIRECT_WORKSPACE_INVALID")
    os.chown(workspace, 10001, 10001)
    workspace.chmod(0o700)
    # Root-only configuration contains no credentials. The B2 writer comes from
    # the existing encrypted envelope, never from a frontend/public environment.
    source = Path("/root/.config/intelligentai/direct-delivery-settings.env")
    settings = dict(line.split("=", 1) for line in source.read_text().splitlines()
                    if line and not line.startswith("#"))
    settings.update(decode(values))
    target = Path("/run/intelligentai/direct-delivery.env")
    _write_private(target, settings)
    os.chown(target, 0, 10001)
    target.chmod(0o640)
    values["AI_DIRECT_DELIVERY_ENV_FILE"] = str(target)
