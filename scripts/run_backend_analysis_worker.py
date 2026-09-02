#!/usr/bin/env python3
"""Run the private VC-BE Redis Stream consumer.

This script has no API route and never prints connection settings or secrets.
It refuses to consume if the deployment has not supplied a reviewed worker
factory with an explicit consent/rights authorizer, restricted object store,
and Seungun executor.
"""

from __future__ import annotations

import argparse
import sys

from voice_coach.backend_analysis.adapters.redis_stream import (
    RedisStreamSettings,
    RedisStreamWorker,
    WorkerConfigurationError,
    create_redis_client,
)
from voice_coach.backend_analysis.composition import load_worker_from_environment


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
    try:
        settings = RedisStreamSettings.from_environment()
        worker = load_worker_from_environment()
        adapter = RedisStreamWorker(
            client=create_redis_client(settings),
            settings=settings,
            worker=worker,
        )
        adapter.ensure_consumer_group()
        if args.once:
            adapter.poll_once(block_ms=args.block_ms)
            return 0
        while True:
            adapter.poll_once(block_ms=args.block_ms)
    except KeyboardInterrupt:
        return 0
    except WorkerConfigurationError as error:
        print(error.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
