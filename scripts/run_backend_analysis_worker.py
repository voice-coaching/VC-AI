#!/usr/bin/env python3
"""Run the private VC-BE Redis Stream consumer.

This script has no API route and never prints connection settings or secrets.
It refuses to consume if the deployment has not supplied a reviewed worker
factory with an explicit consent/rights authorizer, restricted object store,
and Seungun executor.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

from voice_coach.backend_analysis.adapters.redis_stream import (
    RedisStreamSettings,
    RedisStreamWorker,
    WorkerConfigurationError,
    create_redis_client,
)
from voice_coach.backend_analysis.composition import load_worker_from_environment
from voice_coach.backend_analysis.runtime_state import WorkerRuntimeState


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one fresh entry after pending-entry reclaim",
    )
    parser.add_argument(
        "--block-ms",
        type=int,
        default=1_000,
        help="fresh-entry read block duration (default: 1000)",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    stopped = threading.Event()
    reporter: WorkerRuntimeState | None = None
    worker = None

    def request_stop(_signal_number: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        settings = RedisStreamSettings.from_environment()
        worker = load_worker_from_environment()
        reporter = WorkerRuntimeState.from_environment()
        maximum_failures = int(
            os.environ.get("AI_ANALYSIS_MAX_CONSECUTIVE_POLL_FAILURES", "5")
        )
        retry_delay_seconds = float(
            os.environ.get("AI_ANALYSIS_POLL_FAILURE_BACKOFF_SECONDS", "5")
        )
        if not 1 <= maximum_failures <= 100 or not 0.1 <= retry_delay_seconds <= 60:
            raise WorkerConfigurationError("analysis_worker_runtime_configuration_invalid")
        adapter = RedisStreamWorker(
            client=create_redis_client(settings),
            settings=settings,
            worker=worker,
        )
        adapter.ensure_consumer_group()
        reporter.ready()
        if args.once:
            reporter.polled(adapter.poll_once(block_ms=args.block_ms))
            return 0
        consecutive_failures = 0
        while not stopped.is_set():
            try:
                reporter.polled(adapter.poll_once(block_ms=args.block_ms))
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                reporter.failed("analysis_stream_poll_failed")
                if consecutive_failures >= maximum_failures:
                    print("analysis_worker_poll_failure_limit_reached", file=sys.stderr)
                    return 1
                stopped.wait(retry_delay_seconds)
        return 0
    except WorkerConfigurationError as error:
        print(error.code, file=sys.stderr)
        return 2
    except (TypeError, ValueError):
        print("analysis_worker_runtime_configuration_invalid", file=sys.stderr)
        return 2
    except Exception:
        print("analysis_worker_startup_failed", file=sys.stderr)
        return 1
    finally:
        if worker is not None:
            worker.close()
        if reporter is not None:
            try:
                reporter.stopping()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
