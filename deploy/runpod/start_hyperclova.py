"""Start the migrated A6000 INT8 service on loopback without generating text."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil

RUNTIME = Path("/opt/intelligentai")
REVISION = "a6cdfd3464d1b767259cad23e164eaf39d3e3960"
PINNED_CODE = {
    "configuration_hyperclovax.py": "c610da74018197501e230e0d47653d8e6e8407c3d69d1e7a2f2e2a1bd4654b9f",
    "modeling_hyperclovax.py": "b29e8af169f443bc5d1019e79ba0d4fbbec4fdaa09ba60602a99d99456394890",
}


def main() -> None:
    deployment_file = RUNTIME / "deployment.json"
    if deployment_file.is_file():
        deployment = json.loads(deployment_file.read_text(encoding="utf-8"))
        revision = deployment.get("sourceRevision")
        if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision):
            os.environ["VOICE_COACH_BUILD_REVISION"] = revision
    os.environ.update(
        HF_HOME="/tmp/intelligentai-huggingface",
        HF_MODULES_CACHE="/tmp/intelligentai-huggingface/modules",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        TOKENIZERS_PARALLELISM="false",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        # The host exposes more CPUs than the Pod quota; bound CPU kernels.
        OMP_NUM_THREADS="4",
        MKL_NUM_THREADS="4",
        OPENBLAS_NUM_THREADS="1",
    )
    config_file = RUNTIME / "hyperclova-runtime.json"
    config = json.loads(config_file.read_text())
    model = Path(config["localDirectory"])
    cache = Path(os.environ["HF_HOME"]) / "hub/models--naver-hyperclovax--HyperCLOVAX-SEED-Think-32B"
    snapshot = cache / "snapshots" / REVISION
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, expected in PINNED_CODE.items():
        source = model / name
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise SystemExit("Pinned Clova code checksum differs")
        shutil.copyfile(source, snapshot / name)
    (cache / "refs").mkdir(exist_ok=True)
    (cache / "refs/main").write_text(REVISION, encoding="ascii")
    python = str(RUNTIME / "envs/hyperclova/bin/python")
    os.chdir(RUNTIME / "app")
    os.execv(python, [
        python, str(RUNTIME / "app/scripts/serve_hyperclovax_grounded_advice.py"),
        "--config", str(config_file), "--host", "127.0.0.1", "--port", "8000",
        "--quantization", "bnb-int8", "--gpu-memory-gib", "40", "--cpu-memory-gib", "8",
        "--maximum-input-tokens", "4096",
    ])


if __name__ == "__main__":
    main()
