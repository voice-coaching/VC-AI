"""Worker-lifetime third-party validation; no request or alignment state is cached."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

ENVIRONMENT = ('PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'CONDA_PREFIX')


def identity(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):raise RuntimeError('MFA_PREPARED_ASSET_TYPE')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_gid)


class PreparedDependencies:
    def __init__(self, check, names, use_postgres):
        started = time.perf_counter()
        self.environment = {key: os.environ.get(key) for key in ENVIRONMENT}
        self.use_postgres = use_postgres
        self.names = tuple(sorted(set(names) | {'fstcompile'} | ({'initdb'} if use_postgres else set())))
        self.executables = self.resolve()
        # Observe library directory identities as well as their existing files, so
        # adding/removing a loader candidate invalidates this worker too.
        roots = {p.parent.parent / 'lib' for p in self.executables.values()}
        roots.update(Path(p) for p in (os.environ.get('LD_LIBRARY_PATH') or '').split(os.pathsep) if p)
        preloads = set()
        for value in re.split(r'[:\s]+', os.environ.get('LD_PRELOAD') or ''):
            if not value:continue
            path = Path(value)
            if not path.is_absolute():raise RuntimeError('MFA_PREPARED_PRELOAD_PATH_REQUIRED')
            preloads.add(path.resolve(strict=True))
        roots.update(path.parent for path in preloads)
        roots.update(Path(p) for p in ('/lib', '/lib64', '/usr/lib', '/usr/lib64',
                     '/lib/x86_64-linux-gnu', '/usr/lib/x86_64-linux-gnu') if Path(p).is_dir())
        self.directories = {p.resolve(): p.resolve().stat().st_mtime_ns for p in roots if p.is_dir()}
        libraries = {p.resolve() for root in self.directories for p in root.glob('*.so*') if p.is_file()}
        libraries.update(preloads)
        # The loader cache/config can redirect a library without changing PATH.
        libraries.update(p.resolve() for p in (Path('/etc/ld.so.cache'),Path('/etc/ld.so.conf')) if p.is_file())
        loader_directory = Path('/etc/ld.so.conf.d')
        if loader_directory.is_dir():
            self.directories[loader_directory] = loader_directory.stat().st_mtime_ns
            libraries.update(p.resolve() for p in loader_directory.glob('*.conf') if p.is_file())
        paths = set(self.executables.values()) | libraries
        if len(paths) > 8192:raise RuntimeError('MFA_PREPARED_ASSET_LIMIT')
        self.identities = {p: identity(p) for p in paths}
        binaries = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in self.executables.items()}
        check()  # Exactly the installed MFA validation, once before worker READY.
        self.verify(use_postgres)
        self.receipt = dict(revision='mfa-prepared-dependencies-v1', validatedAtWorkerStartup=True,
            executableCount=len(self.executables), observedLibraryCount=len(libraries),
            binaryIdentitySha256=hashlib.sha256(json.dumps(binaries, sort_keys=True).encode()).hexdigest(),
            startupSeconds=time.perf_counter()-started)

    def resolve(self):
        result = {}
        for name in self.names:
            value = shutil.which(name)
            if value is None:raise RuntimeError('MFA_PREPARED_EXECUTABLE_UNAVAILABLE')
            result[name] = Path(value).resolve(strict=True)
        return result

    def verify(self, use_postgres):
        if self.environment != {key: os.environ.get(key) for key in ENVIRONMENT} or use_postgres != self.use_postgres:
            raise RuntimeError('MFA_PREPARED_ENVIRONMENT_CHANGED')
        if self.resolve() != self.executables:raise RuntimeError('MFA_PREPARED_EXECUTABLE_CHANGED')
        for path, expected in self.directories.items():
            if path.stat().st_mtime_ns != expected:raise RuntimeError('MFA_PREPARED_LIBRARY_DIRECTORY_CHANGED')
        for path, expected in self.identities.items():
            if identity(path) != expected:raise RuntimeError('MFA_PREPARED_ASSET_CHANGED')
