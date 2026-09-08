"""Container healthcheck for the private analysis worker state file."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat


def main() -> int:
    try:
        path = Path(os.environ["AI_ANALYSIS_WORKER_STATE_PATH"])
        maximum_age_seconds = int(
            os.environ.get("AI_ANALYSIS_WORKER_HEALTH_MAX_AGE_SECONDS", "600")
        )
        metadata = path.lstat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > 4096
            or not 5 <= maximum_age_seconds <= 3600
        ):
            return 1
        document = json.loads(path.read_text(encoding="ascii"))
        if (
            not isinstance(document, dict)
            or document.get("schemaVersion")
            != "voice-coaching.analysis-worker-state.v1"
            or document.get("status") != "ready"
            or not isinstance(document.get("pid"), int)
            or document["pid"] <= 0
        ):
            return 1
        last_poll = datetime.fromisoformat(
            str(document["lastPollAtUtc"]).removesuffix("Z") + "+00:00"
        )
        age = (datetime.now(tz=UTC) - last_poll).total_seconds()
        if age < 0 or age > maximum_age_seconds:
            return 1
        os.kill(document["pid"], 0)
        return 0
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
