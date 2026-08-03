"""Separator-bounded protected paths and descriptor-anchored filesystem I/O."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterator


PROTECTED_NAME_TOKENS = frozenset(
    {
        "credential",
        "credentials",
        "env",
        "environment",
        "locked",
        "raw",
        "restricted",
        "secret",
        "secrets",
    }
)
_SEPARATOR_RE = re.compile(r"[._-]+")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_REQUIRED_DIR_FD_FUNCTIONS = (
    os.mkdir,
    os.open,
    os.rename,
    os.rmdir,
    os.stat,
    os.unlink,
)


class ProtectedPathError(ValueError):
    """Raised before a protected or unsafe lexical path can be opened."""


class DescriptorIOError(RuntimeError):
    """Raised when descriptor-anchored I/O invariants are not met."""


def _assert_platform_capabilities() -> None:
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(
            function not in os.supports_dir_fd
            for function in _REQUIRED_DIR_FD_FUNCTIONS
        )
    ):
        raise DescriptorIOError("platform_descriptor_capability")


def is_protected_component(component: str) -> bool:
    """Return whether one path component contains a bounded protected name.

    Protected names must be delimited by ``.``, ``_`` or ``-``. This blocks
    names such as ``raw_audio`` and ``locked-evaluation`` while intentionally
    allowing ordinary words such as ``drawer``, ``secretary``,
    ``credentialed`` and ``unlocked``. Any component beginning with ``.env``
    is always protected.
    """

    normalized = component.casefold()
    if normalized.startswith(".env"):
        return True
    return any(
        token in PROTECTED_NAME_TOKENS
        for token in _SEPARATOR_RE.split(normalized)
        if token
    )


def validate_relative_path(
    configured: str | Path,
    *,
    reject_protected: bool = True,
) -> PurePosixPath:
    """Validate a portable workspace-relative path without touching the FS."""

    text = configured.as_posix() if isinstance(configured, Path) else configured
    if not isinstance(text, str) or not text or "\\" in text or "\x00" in text:
        raise ProtectedPathError("invalid_relative_path")
    lexical_parts = text.split("/")
    if any(part in {"", ".", ".."} for part in lexical_parts):
        raise ProtectedPathError("invalid_relative_path")
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts:
        raise ProtectedPathError("invalid_relative_path")
    if reject_protected and any(is_protected_component(part) for part in path.parts):
        raise ProtectedPathError("protected_path")
    return path


def descriptor_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """Return the metadata fields used to detect descriptor mutation."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    """Return the stable device/inode identity for a directory."""

    return (metadata.st_dev, metadata.st_ino)


def fsync_directory_descriptor(descriptor: int) -> None:
    """Durably flush a directory already anchored by descriptor."""

    os.fsync(descriptor)


def open_child_directory(
    parent_descriptor: int,
    name: str,
    *,
    create: bool = False,
    mode: int = 0o750,
) -> int:
    """Open one child directory without following a symlink."""

    if "/" in name or name in {"", ".", ".."}:
        raise DescriptorIOError("directory_component")
    if create:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent_descriptor)
            fsync_directory_descriptor(parent_descriptor)
        except FileExistsError:
            pass
        except OSError as exc:
            raise DescriptorIOError("directory_create") from exc
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise DescriptorIOError("directory_open") from exc
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or not stat.S_ISDIR(path_metadata.st_mode)
            or directory_identity(metadata) != directory_identity(path_metadata)
        ):
            raise DescriptorIOError("directory_identity")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_file_at(
    parent_descriptor: int,
    name: str,
    *,
    maximum_bytes: int,
    minimum_bytes: int = 1,
    expected_mode: int | None = None,
    require_nlink_one: bool = False,
) -> tuple[bytes, os.stat_result]:
    """Read a regular file through its held parent descriptor."""

    if "/" in name or name in {"", ".", ".."}:
        raise DescriptorIOError("file_component")
    try:
        path_before = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise DescriptorIOError("file_lstat") from exc
    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise DescriptorIOError("file_not_regular")
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except OSError as exc:
        raise DescriptorIOError("file_open") from exc
    try:
        before = os.fstat(descriptor)
        if descriptor_identity(before) != descriptor_identity(path_before):
            raise DescriptorIOError("file_changed_before_open")
        if not stat.S_ISREG(before.st_mode):
            raise DescriptorIOError("file_not_regular")
        if expected_mode is not None and stat.S_IMODE(before.st_mode) != expected_mode:
            raise DescriptorIOError("file_mode")
        if require_nlink_one and before.st_nlink != 1:
            raise DescriptorIOError("file_nlink")
        if before.st_size < minimum_bytes or before.st_size > maximum_bytes:
            raise DescriptorIOError("file_size")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise DescriptorIOError("file_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise DescriptorIOError("file_grew")
        after = os.fstat(descriptor)
        if descriptor_identity(after) != descriptor_identity(before):
            raise DescriptorIOError("file_changed")
        content = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        path_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise DescriptorIOError("file_post_lstat") from exc
    if descriptor_identity(path_after) != descriptor_identity(after):
        raise DescriptorIOError("file_changed_after_read")
    return content, after


def write_new_file_at(
    parent_descriptor: int,
    name: str,
    content: bytes,
    *,
    mode: int,
) -> None:
    """Create, fsync and seal a new regular file through a directory fd."""

    if "/" in name or name in {"", ".", ".."}:
        raise DescriptorIOError("file_component")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(name, flags, mode, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        created_identity = (opened.st_dev, opened.st_ino)
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DescriptorIOError("file_write")
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        path_metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            descriptor_identity(metadata) != descriptor_identity(path_metadata)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_nlink != 1
            or metadata.st_size != len(content)
        ):
            raise DescriptorIOError("file_write_identity")
        fsync_directory_descriptor(parent_descriptor)
    except BaseException:
        if created_identity is not None:
            try:
                current = os.stat(
                    name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == created_identity:
                    os.unlink(name, dir_fd=parent_descriptor)
                    fsync_directory_descriptor(parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_replace_at(
    parent_descriptor: int,
    target_name: str,
    temporary_name: str,
    content: bytes,
    *,
    mode: int,
) -> None:
    """Atomically replace one file inside a single held directory."""

    if target_name == temporary_name:
        raise DescriptorIOError("atomic_name_collision")
    for name in (target_name, temporary_name):
        if "/" in name or name in {"", ".", ".."}:
            raise DescriptorIOError("file_component")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary_name,
            flags,
            mode,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        created_identity = (opened.st_dev, opened.st_ino)
        os.fchmod(descriptor, mode)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise DescriptorIOError("file_write")
            view = view[written:]
        os.fsync(descriptor)
        staged = os.fstat(descriptor)
        staged_path = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            descriptor_identity(staged) != descriptor_identity(staged_path)
            or not stat.S_ISREG(staged.st_mode)
            or stat.S_ISLNK(staged_path.st_mode)
            or stat.S_IMODE(staged.st_mode) != mode
            or staged.st_nlink != 1
            or staged.st_size != len(content)
        ):
            raise DescriptorIOError("atomic_staging_identity")
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        published = os.fstat(descriptor)
        published_path = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            descriptor_identity(published) != descriptor_identity(published_path)
            or not stat.S_ISREG(published.st_mode)
            or stat.S_ISLNK(published_path.st_mode)
            or stat.S_IMODE(published.st_mode) != mode
            or published.st_nlink != 1
            or published.st_size != len(content)
        ):
            raise DescriptorIOError("atomic_target_identity")
        fsync_directory_descriptor(parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary: os.stat_result | None
        try:
            temporary = os.stat(
                temporary_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            temporary = None
        except OSError:
            temporary = None
        if (
            temporary is not None
            and created_identity is not None
            and (temporary.st_dev, temporary.st_ino) == created_identity
        ):
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
                fsync_directory_descriptor(parent_descriptor)
            except OSError:
                pass


@dataclass
class RootDirectory:
    """A workspace root held open for component-by-component ``openat`` I/O."""

    path: Path
    descriptor: int
    identity: tuple[int, int]

    @classmethod
    def open(cls, workspace_root: str | Path) -> RootDirectory:
        _assert_platform_capabilities()
        path = Path(workspace_root).absolute()
        try:
            before = path.lstat()
        except OSError as exc:
            raise DescriptorIOError("workspace_lstat") from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise DescriptorIOError("workspace_not_directory")
        try:
            descriptor = os.open(path, _DIRECTORY_FLAGS)
        except OSError as exc:
            raise DescriptorIOError("workspace_open") from exc
        try:
            metadata = os.fstat(descriptor)
            identity = directory_identity(metadata)
            if not stat.S_ISDIR(metadata.st_mode) or identity != directory_identity(
                before
            ):
                raise DescriptorIOError("workspace_identity")
            return cls(path=path, descriptor=descriptor, identity=identity)
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1

    def __enter__(self) -> RootDirectory:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def assert_bound(self) -> None:
        try:
            current = self.path.lstat()
        except OSError as exc:
            raise DescriptorIOError("workspace_rebound") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or directory_identity(current) != self.identity
        ):
            raise DescriptorIOError("workspace_rebound")

    def _assert_directory_route(
        self,
        parts: tuple[str, ...],
        held_descriptor: int,
    ) -> None:
        """Confirm that a held directory is still reachable by its root route."""

        self.assert_bound()
        current = os.dup(self.descriptor)
        try:
            for part in parts:
                child = open_child_directory(current, part, create=False)
                os.close(current)
                current = child
            held_metadata = os.fstat(held_descriptor)
            current_metadata = os.fstat(current)
            if (
                not stat.S_ISDIR(held_metadata.st_mode)
                or not stat.S_ISDIR(current_metadata.st_mode)
                or directory_identity(held_metadata)
                != directory_identity(current_metadata)
            ):
                raise DescriptorIOError("directory_route_rebound")
        finally:
            os.close(current)

    @contextmanager
    def open_directory(
        self,
        relative: str | Path,
        *,
        create: bool = False,
        reject_protected: bool = False,
        mode: int = 0o750,
    ) -> Iterator[int]:
        path = validate_relative_path(
            relative,
            reject_protected=reject_protected,
        )
        descriptor = os.dup(self.descriptor)
        completed = False
        try:
            for part in path.parts:
                child = open_child_directory(
                    descriptor,
                    part,
                    create=create,
                    mode=mode,
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
            completed = True
        finally:
            try:
                if completed:
                    self._assert_directory_route(path.parts, descriptor)
            finally:
                os.close(descriptor)

    @contextmanager
    def open_parent(
        self,
        relative: str | Path,
        *,
        create: bool = False,
        reject_protected: bool = True,
        mode: int = 0o750,
    ) -> Iterator[tuple[int, str]]:
        path = validate_relative_path(
            relative,
            reject_protected=reject_protected,
        )
        descriptor = os.dup(self.descriptor)
        completed = False
        try:
            for part in path.parts[:-1]:
                child = open_child_directory(
                    descriptor,
                    part,
                    create=create,
                    mode=mode,
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor, path.name
            completed = True
        finally:
            try:
                if completed:
                    self._assert_directory_route(path.parts[:-1], descriptor)
            finally:
                os.close(descriptor)

    def read_bytes(
        self,
        relative: str | Path,
        *,
        maximum_bytes: int,
        minimum_bytes: int = 1,
        expected_mode: int | None = None,
        require_nlink_one: bool = False,
        reject_protected: bool = True,
    ) -> tuple[bytes, os.stat_result]:
        with self.open_parent(
            relative,
            create=False,
            reject_protected=reject_protected,
        ) as (parent, name):
            return read_file_at(
                parent,
                name,
                maximum_bytes=maximum_bytes,
                minimum_bytes=minimum_bytes,
                expected_mode=expected_mode,
                require_nlink_one=require_nlink_one,
            )

    def directory_identity(self, relative: str | Path) -> tuple[int, int]:
        with self.open_directory(relative) as descriptor:
            return directory_identity(os.fstat(descriptor))

    def path_exists(
        self,
        parent_descriptor: int,
        name: str,
    ) -> bool:
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return False
            raise DescriptorIOError("path_stat") from exc
        return True


__all__ = [
    "DescriptorIOError",
    "PROTECTED_NAME_TOKENS",
    "ProtectedPathError",
    "RootDirectory",
    "atomic_replace_at",
    "descriptor_identity",
    "directory_identity",
    "fsync_directory_descriptor",
    "is_protected_component",
    "open_child_directory",
    "read_file_at",
    "validate_relative_path",
    "write_new_file_at",
]
