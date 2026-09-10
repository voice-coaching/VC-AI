"""Start the deployed analysis release; never infer a release from a mutable tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys


def main() -> None:
    runtime = Path("/opt/intelligentai")
    try:
        release = json.loads((runtime / "deployment.json").read_text(encoding="utf-8"))
        revision = release["sourceRevision"]
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError
        if os.environ.get("AI_ANALYSIS_RELEASE_ID", revision) != revision:
            raise ValueError
        os.environ["VOICE_COACH_BUILD_REVISION"] = revision
        os.environ["AI_ANALYSIS_RELEASE_ID"] = revision
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("analysis_deployed_release_invalid") from None
    os.chdir(runtime / "app")
    os.execv(sys.executable, [sys.executable, str(runtime / "app/scripts/run_backend_analysis_worker.py")])


if __name__ == "__main__":
    main()
