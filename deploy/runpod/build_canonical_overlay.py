"""Package explicit non-secret canonical sources, not the dirty checkout."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {"canonical_callback_client.py", "canonical_delivery.py",
            "canonical_journal.py", "canonical_filesystem.py"}
paths = [p for p in (ROOT / "src/voice_coach/backend_analysis").glob("canonical_*.py")
         if p.name not in EXCLUDED]
paths += [ROOT / p for p in [
    "src/voice_coach/backend_analysis/http_api.py",
    "src/voice_coach/backend_analysis/http_models.py",
    "src/voice_coach/backend_analysis/http_runtime.py",
    "deploy/runpod/prepare_http.py", "deploy/runpod/prepare_canonical.py",
    "deploy/runpod/canonical-requirements.lock",
    "docs/contracts/runpod_analysis_request_v2.schema.json",
    "docs/contracts/runpod_http_control_v1_2.schema.json",
    "docs/contracts/runpod_result_v4.schema.json",
]]
paths += list((ROOT / "docs/contracts").glob("runpod_canonical_*.schema.json"))
records = []
for p in sorted(paths):
    raw = p.read_bytes()
    records.append({"path": p.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content": base64.b64encode(raw).decode()})
out = ROOT / "build/canonical-runpod-overlay.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({"baseOverlaySha256":
    "87aa04793415572cda6fe708e99302f7cfe7ee8f929acfa942b654c25e573873",
    "baseRevision": "32d98e39767b56697bc7e58fb50e3e9d61432349",
    "files": records}, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"path": str(out), "files": len(records),
                  "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}))
