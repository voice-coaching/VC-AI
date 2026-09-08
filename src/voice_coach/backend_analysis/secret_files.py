"""Bounded deployment-secret loading without logging secret contents."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import stat


def secret_text(
    environment: Mapping[str, str],
    *,
    value_name: str,
    file_name: str,
    maximum_bytes: int,
) -> str:
    """Read exactly one direct or absolute regular-file secret source."""

    direct = environment.get(value_name)
    file_value = environment.get(file_name)
    if bool(direct) == bool(file_value):
        raise ValueError("deployment_secret_source_invalid")
    if direct:
        encoded = direct.encode("utf-8")
        if len(encoded) > maximum_bytes or "\x00" in direct:
            raise ValueError("deployment_secret_source_invalid")
        return direct
    assert file_value is not None
    path = Path(file_value)
    try:
        metadata = path.lstat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
        ):
            raise ValueError("deployment_secret_source_invalid")
        value = path.read_text(encoding="utf-8").removesuffix("\n")
    except (OSError, UnicodeError):
        raise ValueError("deployment_secret_source_invalid") from None
    if not value or "\x00" in value or len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError("deployment_secret_source_invalid")
    return value


__all__ = ["secret_text"]
