"""Seed the offline dynamic-module cache and start the pinned Clova server."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys


REVISION = "a6cdfd3464d1b767259cad23e164eaf39d3e3960"
MODEL_ROOT = Path(
    "/app/data/models/hyperclovax-seed-think-32b"
) / REVISION
CACHE_ROOT = Path(
    "/tmp/huggingface/hub/"
    "models--naver-hyperclovax--HyperCLOVAX-SEED-Think-32B"
)
PINNED_CODE = {
    "configuration_hyperclovax.py": (
        "c610da74018197501e230e0d47653d8e6e8407c3d69d1e7a2f2e2a1bd4654b9f"
    ),
    "modeling_hyperclovax.py": (
        "b29e8af169f443bc5d1019e79ba0d4fbbec4fdaa09ba60602a99d99456394890"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    snapshot = CACHE_ROOT / "snapshots" / REVISION
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, expected_sha256 in PINNED_CODE.items():
        source = MODEL_ROOT / name
        if not source.is_file() or source.is_symlink():
            raise SystemExit(f"pinned Clova code unavailable: {name}")
        if _sha256(source) != expected_sha256:
            raise SystemExit(f"pinned Clova code digest differs: {name}")
        shutil.copyfile(source, snapshot / name)
    refs = CACHE_ROOT / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(REVISION, encoding="ascii")
    target = [
        sys.executable,
        "/app/scripts/serve_hyperclovax_grounded_advice.py",
        *sys.argv[1:],
    ]
    os.execv(sys.executable, target)


if __name__ == "__main__":
    main()
