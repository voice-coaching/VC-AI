"""Tiny Linux launcher that applies resource and network limits before exec.

This process never parses media.  It starts from trusted local code, installs
limits, sets ``no_new_privs``, denies network syscalls with seccomp, and only
then replaces itself with the requested decoder or ML worker.  The seccomp
filter and resource limits survive ``execve``.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import signal
import socket
import sys


_SCMP_ACT_ALLOW = 0x7FFF0000
_SCMP_ACT_ERRNO = 0x00050000
_SCMP_CMP_NE = 1
_SCMP_CMP_EQ = 4
_SCMP_CMP_MASKED_EQ = 7
_SOCKET_TYPE_MASK = 0xF
_CLONE_THREAD = 0x00010000
_PR_SET_PDEATHSIG = 1
_PR_SET_NO_NEW_PRIVS = 38
_IO_URING_SYSCALLS = (
    b"io_uring_setup",
    b"io_uring_enter",
    b"io_uring_register",
)
_NETWORK_SYSCALLS = (
    b"socket",
    b"socketpair",
    b"connect",
    b"accept",
    b"accept4",
    b"bind",
    b"listen",
    b"sendto",
    b"sendmsg",
    b"sendmmsg",
    b"recvfrom",
    b"recvmsg",
    b"recvmmsg",
    b"shutdown",
    b"getsockname",
    b"getpeername",
    b"getsockopt",
    b"setsockopt",
)
_ALWAYS_DENIED_CROSS_PROCESS_SYSCALLS = (
    b"tkill",
    b"pidfd_send_signal",
    b"pidfd_getfd",
)
_PID_FIRST_CROSS_PROCESS_SYSCALLS = (
    b"kill",
    b"tgkill",
    b"rt_sigqueueinfo",
    b"rt_tgsigqueueinfo",
    b"pidfd_open",
    b"process_vm_readv",
    b"process_vm_writev",
)


class IsolationError(RuntimeError):
    """Internal launcher failure; no target path is included."""


class _ScmpArgCompare(ctypes.Structure):
    _fields_ = (
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_uint),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    )


def set_parent_death_signal(*, expected_parent_pid: int) -> None:
    """Bind launcher lifetime to the trusted supervising process.

    Linux does not deliver ``PDEATHSIG`` retroactively.  The parent identity
    therefore has to be checked both before and immediately after ``prctl``;
    a mismatch fails closed instead of letting an already orphaned worker run.
    The seccomp policy installed later prevents the target from clearing this
    setting after ``execve``.
    """

    if expected_parent_pid <= 1 or os.getppid() != expected_parent_pid:
        raise IsolationError("launcher_parent_changed")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        raise IsolationError("parent_death_guard_unavailable")
    if os.getppid() != expected_parent_pid:
        raise IsolationError("launcher_parent_changed")


def deny_network_syscalls(*, allow_local_sockets: bool = False) -> None:
    """Install a socket-denying seccomp filter for this and future execs.

    ``allow_local_sockets`` is deliberately narrower than its legacy name.  It
    permits ``AF_UNIX`` socketpairs plus the unconnected ``SOCK_SEQPACKET``
    listener that CUDA creates for its internal UVM handle path.  ``connect``
    is forced to ``ENOENT`` (the CUDA MPS-absent fallback), while accept and
    every message-style network operation remain denied.  Other families and
    endpoint types are denied, as is io_uring, so the target cannot initiate
    or accept a proxy connection through either syscall path.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise IsolationError("no_new_privs_unavailable")

    try:
        # Resolve the fixed Linux SONAME directly.  find_library() may invoke
        # helper processes and NSS sockets before the filter is installed.
        library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError:
        raise IsolationError("seccomp_unavailable")
    library.seccomp_init.argtypes = (ctypes.c_uint32,)
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = (ctypes.c_char_p,)
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    )
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(_ScmpArgCompare),
    )
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_load.argtypes = (ctypes.c_void_p,)
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = (ctypes.c_void_p,)
    library.seccomp_release.restype = None

    context = library.seccomp_init(_SCMP_ACT_ALLOW)
    if not context:
        raise IsolationError("seccomp_unavailable")
    try:
        deny_action = _SCMP_ACT_ERRNO | errno.EPERM
        if allow_local_sockets:
            domain_filter = _ScmpArgCompare(
                arg=0,
                op=_SCMP_CMP_NE,
                datum_a=socket.AF_UNIX,
                datum_b=0,
            )
            socketpair_number = library.seccomp_syscall_resolve_name(b"socketpair")
            if socketpair_number < 0 or library.seccomp_rule_add_array(
                context,
                deny_action,
                socketpair_number,
                1,
                ctypes.byref(domain_filter),
            ) != 0:
                raise IsolationError("seccomp_unavailable")

            socket_number = library.seccomp_syscall_resolve_name(b"socket")
            if socket_number < 0 or library.seccomp_rule_add_array(
                context,
                deny_action,
                socket_number,
                1,
                ctypes.byref(domain_filter),
            ) != 0:
                raise IsolationError("seccomp_unavailable")
            # CUDA opens an AF_UNIX SOCK_SEQPACKET listener but does not need
            # a connected endpoint for this inference path.  Deny every other
            # base socket type while ignoring CLOEXEC/NONBLOCK flag bits.
            for socket_type in range(_SOCKET_TYPE_MASK + 1):
                if socket_type == socket.SOCK_SEQPACKET:
                    continue
                type_filter = _ScmpArgCompare(
                    arg=1,
                    op=_SCMP_CMP_MASKED_EQ,
                    datum_a=_SOCKET_TYPE_MASK,
                    datum_b=socket_type,
                )
                if library.seccomp_rule_add_array(
                    context,
                    deny_action,
                    socket_number,
                    1,
                    ctypes.byref(type_filter),
                ) != 0:
                    raise IsolationError("seccomp_unavailable")

            # CUDA probes /tmp/nvidia-mps/control and treats ENOENT as the
            # ordinary no-MPS path.  Never allow the connection, regardless
            # of whether a host socket exists there.
            connect_number = library.seccomp_syscall_resolve_name(b"connect")
            if connect_number < 0 or library.seccomp_rule_add(
                context,
                _SCMP_ACT_ERRNO | errno.ENOENT,
                connect_number,
                0,
            ) != 0:
                raise IsolationError("seccomp_unavailable")

            # bind/listen are required by the CUDA UVM listener.  With both
            # connection directions and message-style I/O denied, it cannot
            # exchange bytes with an external Unix-domain endpoint.  A local
            # socketpair can still use plain read/write within the isolated
            # process tree because all unrelated FDs are closed.
            for syscall_name in (
                b"accept",
                b"accept4",
                b"sendto",
                b"sendmsg",
                b"sendmmsg",
                b"recvfrom",
                b"recvmsg",
                b"recvmmsg",
                b"shutdown",
                b"getsockname",
                b"getpeername",
                b"getsockopt",
                b"setsockopt",
            ):
                syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
                if syscall_number < 0 or library.seccomp_rule_add(
                    context, deny_action, syscall_number, 0
                ) != 0:
                    raise IsolationError("seccomp_unavailable")
        else:
            for syscall_name in _NETWORK_SYSCALLS:
                syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
                if syscall_number < 0 or library.seccomp_rule_add(
                    context, deny_action, syscall_number, 0
                ) != 0:
                    raise IsolationError("seccomp_unavailable")
        # io_uring operations can otherwise perform connect/send/recv without
        # entering the correspondingly named syscalls and bypass the filter.
        for syscall_name in _IO_URING_SYSCALLS:
            syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
            if syscall_number < 0 or library.seccomp_rule_add(
                context, deny_action, syscall_number, 0
            ) != 0:
                raise IsolationError("seccomp_unavailable")
        # Every target starts as a new session.  Forbid it from spawning a
        # process that could move into another process group and survive the
        # launcher's killpg cleanup.  Native runtime threads remain available.
        for syscall_name in (
            b"fork",
            b"vfork",
            b"setsid",
            b"setpgid",
            b"unshare",
            b"setns",
        ):
            syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
            # Some architectures expose fork/vfork only through clone and
            # report a negative libseccomp pseudo-number here.
            if syscall_number < 0:
                continue
            if library.seccomp_rule_add(
                context, deny_action, syscall_number, 0
            ) != 0:
                raise IsolationError("seccomp_unavailable")
        clone3_number = library.seccomp_syscall_resolve_name(b"clone3")
        if clone3_number < 0 or library.seccomp_rule_add(
            context,
            _SCMP_ACT_ERRNO | errno.ENOSYS,
            clone3_number,
            0,
        ) != 0:
            raise IsolationError("seccomp_unavailable")
        clone_number = library.seccomp_syscall_resolve_name(b"clone")
        missing_thread_flag = _ScmpArgCompare(
            arg=0,
            op=_SCMP_CMP_MASKED_EQ,
            datum_a=_CLONE_THREAD,
            datum_b=0,
        )
        if clone_number < 0 or library.seccomp_rule_add_array(
            context,
            deny_action,
            clone_number,
            1,
            ctypes.byref(missing_thread_flag),
        ) != 0:
            raise IsolationError("seccomp_unavailable")

        # A hostile worker must not stop or kill its timeout controller, nor
        # signal another same-UID process.  Signals addressed to this process
        # (including its own native threads via tgkill) remain available for
        # ordinary runtime behaviour.  tkill lacks a tgid argument and pidfd
        # descriptors are indirect, so those routes are denied outright.
        current_process_id = os.getpid()
        cross_process_target = _ScmpArgCompare(
            arg=0,
            op=_SCMP_CMP_NE,
            datum_a=current_process_id,
            datum_b=0,
        )
        for syscall_name in _PID_FIRST_CROSS_PROCESS_SYSCALLS:
            syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
            if syscall_number < 0:
                continue
            if library.seccomp_rule_add_array(
                context,
                deny_action,
                syscall_number,
                1,
                ctypes.byref(cross_process_target),
            ) != 0:
                raise IsolationError("seccomp_unavailable")
        for syscall_name in _ALWAYS_DENIED_CROSS_PROCESS_SYSCALLS:
            syscall_number = library.seccomp_syscall_resolve_name(syscall_name)
            if syscall_number < 0:
                continue
            if library.seccomp_rule_add(
                context, deny_action, syscall_number, 0
            ) != 0:
                raise IsolationError("seccomp_unavailable")

        # ptrace's target PID is its second argument.  There is no legitimate
        # tracing requirement inside the decoder/model workers, so deny it in
        # full rather than maintain a request-specific allowlist.
        ptrace_number = library.seccomp_syscall_resolve_name(b"ptrace")
        if ptrace_number >= 0 and library.seccomp_rule_add(
            context, deny_action, ptrace_number, 0
        ) != 0:
            raise IsolationError("seccomp_unavailable")

        # Keep the parent-death binding immutable across exec.  Other prctl
        # operations remain available to native runtimes.
        parent_death_option = _ScmpArgCompare(
            arg=0,
            op=_SCMP_CMP_EQ,
            datum_a=_PR_SET_PDEATHSIG,
            datum_b=0,
        )
        prctl_number = library.seccomp_syscall_resolve_name(b"prctl")
        if prctl_number < 0 or library.seccomp_rule_add_array(
            context,
            deny_action,
            prctl_number,
            1,
            ctypes.byref(parent_death_option),
        ) != 0:
            raise IsolationError("seccomp_unavailable")
        if library.seccomp_load(context) != 0:
            raise IsolationError("seccomp_unavailable")
    finally:
        library.seccomp_release(context)


def apply_resource_limits(*, cpu_seconds: int, address_space_bytes: int) -> None:
    if not 1 <= cpu_seconds <= 3_600:
        raise IsolationError("cpu_limit_invalid")
    if not 256 * 1024 * 1024 <= address_space_bytes <= 64 * 1024 * 1024 * 1024:
        raise IsolationError("address_space_limit_invalid")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(
        resource.RLIMIT_AS,
        (address_space_bytes, address_space_bytes),
    )
    current_soft, current_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    file_limit = min(256, current_hard if current_hard >= 0 else 256)
    resource.setrlimit(resource.RLIMIT_NOFILE, (file_limit, file_limit))


def isolated_command(
    *,
    launcher_python: str,
    launcher_path: str,
    target: list[str],
    cpu_seconds: int,
    address_space_bytes: int,
    allow_local_sockets: bool = False,
) -> list[str]:
    """Return argv for a target that must start behind this launcher."""

    if not target:
        raise ValueError("target must not be empty")
    return [
        launcher_python,
        launcher_path,
        "--expected-parent-pid",
        str(os.getpid()),
        "--cpu-seconds",
        str(cpu_seconds),
        "--address-space-bytes",
        str(address_space_bytes),
        "--socket-policy",
        "local" if allow_local_sockets else "none",
        "--",
        *target,
    ]


def _parse(argv: list[str]) -> tuple[int, int, int, bool, list[str]]:
    if argv == ["--self-test"]:
        return os.getppid(), 1, 256 * 1024 * 1024, False, []
    if (
        len(argv) < 10
        or argv[0] != "--expected-parent-pid"
        or argv[2] != "--cpu-seconds"
        or argv[4] != "--address-space-bytes"
        or argv[6] != "--socket-policy"
        or argv[7] not in {"none", "local"}
        or argv[8] != "--"
    ):
        raise IsolationError("launcher_arguments_invalid")
    try:
        expected_parent_pid = int(argv[1])
        cpu_seconds = int(argv[3])
        address_space_bytes = int(argv[5])
    except ValueError:
        raise IsolationError("launcher_arguments_invalid") from None
    target = argv[9:]
    if not target or not os.path.isabs(target[0]):
        raise IsolationError("launcher_target_invalid")
    return (
        expected_parent_pid,
        cpu_seconds,
        address_space_bytes,
        argv[7] == "local",
        target,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        (
            expected_parent_pid,
            cpu_seconds,
            address_space_bytes,
            allow_local_sockets,
            target,
        ) = _parse(arguments)
        set_parent_death_signal(expected_parent_pid=expected_parent_pid)
        apply_resource_limits(
            cpu_seconds=cpu_seconds,
            address_space_bytes=address_space_bytes,
        )
        deny_network_syscalls(allow_local_sockets=allow_local_sockets)
        if os.getppid() != expected_parent_pid:
            raise IsolationError("launcher_parent_changed")
        if not target:
            try:
                socket.socket()
            except PermissionError:
                sys.stdout.write(
                    json.dumps(
                        {
                            "network_isolation": "seccomp_socket_denied",
                            "status": "passed",
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                return 0
            return 1
        os.execve(target[0], target, dict(os.environ))
    except (IsolationError, OSError, ValueError):
        return 126
    return 126


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "IsolationError",
    "apply_resource_limits",
    "deny_network_syscalls",
    "isolated_command",
    "set_parent_death_signal",
]
