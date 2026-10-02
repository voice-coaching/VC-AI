"""Parent-side isolated canonical lane, sharing the legacy admission slot."""
from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
import time

from .canonical_codec import encode_document
from .canonical_lease import publish_lease
from .canonical_process import check_guard_python, private_directory, run_guarded
from .canonical_producer import read_private_result, write_private_new
from .http_models import parse_json, timestamp


from .canonical_settings import child_environment

class CanonicalLane:
    def __init__(self, environment):
        self.environment = child_environment(environment)
        self.python = Path(environment["AI_CANONICAL_PYTHON"])
        check_guard_python(self.python)
        self.root = private_directory(Path(environment["AI_CANONICAL_WORKSPACE_ROOT"]))
        self.app = Path(__file__).resolve().parents[3]

    @staticmethod
    def publish_lease(job):
        if job.canonical_workspace is None:
            return
        publish_lease(job.canonical_workspace, job.request.execution_id, job.lease_until)

    async def run(self, job, worker_id):
        work = self.root / str(job.request.execution_id)
        # An existing attempt is never restarted, even if memory was lost.
        work.mkdir(mode=0o700, exist_ok=False)
        job.canonical_workspace = work
        deadline = time.monotonic() + (timestamp(job.body["deadlineAt"]) - datetime.now(UTC)).total_seconds()
        write_private_new(work / "request.json", encode_document({
            "request": job.body, "workerId": worker_id, "deadline": deadline}))
        self.publish_lease(job)
        await run_guarded(guard_python=self.python,
            command=[str(self.python), "-B", "-m", "voice_coach.backend_analysis.canonical_executor",
                     str(work / "request.json")],
            cwd=self.app, environment=self.environment, log_path=work / "executor.log",
            deadline_monotonic=deadline,
            execution_is_active=lambda: not job.cancel.is_set() and time.monotonic() < job.lease_until)
        result = parse_json(read_private_result(work / "completed.json", work, 1024))
        if set(result) != {"status"} or result["status"] not in ("COMPLETED", "FAILED"):
            raise ValueError("canonical_completion_invalid")
        return result["status"]
