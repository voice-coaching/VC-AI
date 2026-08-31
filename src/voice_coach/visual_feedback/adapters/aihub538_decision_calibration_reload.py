"""Subprocess boundary for strict fresh-process calibration reload."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from ..models.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationReloadReceipt,
)
from ..errors import VisualFeedbackContractError
from ..services.aihub538_decision_calibration import (
    Aihub538DecisionCalibrationError,
)


_MAXIMUM_OUTPUT_BYTES = 256 * 1024


def fresh_process_reload_aihub538_calibration(
    *,
    video_qc_policy_path: Path,
    av_sync_policy_path: Path,
    calibration_evidence_path: Path,
    repository_root: Path,
    worker_python: Path | None = None,
) -> Aihub538DecisionCalibrationReloadReceipt:
    """Reload three mode-0400 files in a distinct interpreter process."""

    descriptors: list[int] = []
    try:
        for path in (
            video_qc_policy_path,
            av_sync_policy_path,
            calibration_evidence_path,
        ):
            descriptors.append(
                os.open(
                    path,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            )
        command = [
            str((worker_python or Path(sys.executable)).absolute()),
            "-m",
            (
                "voice_coach.visual_feedback.adapters."
                "aihub538_decision_calibration_reload_worker"
            ),
            "--video-qc-fd",
            str(descriptors[0]),
            "--av-sync-fd",
            str(descriptors[1]),
            "--evidence-fd",
            str(descriptors[2]),
        ]
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30.0,
            pass_fds=tuple(descriptors),
            cwd=repository_root,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "PYTHONHASHSEED": "0",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(repository_root / "src"),
            },
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_fresh_reload_failed"
        ) from None
    finally:
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if (
        completed.returncode != 0
        or not 0 < len(completed.stdout) <= _MAXIMUM_OUTPUT_BYTES
        or len(completed.stderr) > _MAXIMUM_OUTPUT_BYTES
    ):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_fresh_reload_failed"
        )

    def reject_duplicates(pairs):  # noqa: ANN001
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise VisualFeedbackContractError("reload output has duplicate keys")
            result[key] = value
        return result

    try:
        document = json.loads(
            completed.stdout,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                VisualFeedbackContractError("reload output has non-finite value")
            ),
        )
        return Aihub538DecisionCalibrationReloadReceipt.from_json_object(document)
    except (UnicodeDecodeError, json.JSONDecodeError, VisualFeedbackContractError):
        raise Aihub538DecisionCalibrationError(
            "aihub538_calibration_fresh_reload_failed"
        ) from None


__all__ = ["fresh_process_reload_aihub538_calibration"]
