"""Start only the deployed HTTP control plane with its actual release identity."""
import json
import os
from pathlib import Path
import re
import sys


def main():
    root = Path("/opt/intelligentai")
    try:
        revision = json.loads((root / "deployment.json").read_text())["sourceRevision"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError()
        if os.environ.get("AI_ANALYSIS_RELEASE_ID", revision) != revision:
            raise ValueError()
        os.environ["VOICE_COACH_BUILD_REVISION"] = os.environ["AI_ANALYSIS_RELEASE_ID"] = revision
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit("analysis_http_deployed_release_invalid") from None
    os.chdir(root / "app")
    os.execv(sys.executable, [sys.executable, str(root / "app/scripts/serve_backend_analysis_http.py")])


if __name__ == "__main__":
    main()
