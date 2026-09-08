"""Deployment composition helpers for the private Stream worker.

The repository intentionally does not provide a permissive consent source or a
generic cloud-object-store implementation.  A production deployment must wire
a reviewed local factory that returns the three bounded ports required by
``BackendAnalysisWorker``.  Without it, the process fails before consuming a
single Stream message.
"""

from __future__ import annotations

from importlib import import_module
import os
from typing import Callable, Mapping

from .adapters.redis_stream import WorkerConfigurationError
from .service import BackendAnalysisWorker


def load_worker_from_environment(
    environment: Mapping[str, str] | None = None,
) -> BackendAnalysisWorker:
    """Load an operator-reviewed ``module:callable`` composition factory."""

    env = os.environ if environment is None else environment
    reference = env.get("VOICE_COACH_BACKEND_ANALYSIS_FACTORY", "")
    if reference.count(":") != 1:
        raise WorkerConfigurationError("analysis_worker_factory_unconfigured")
    module_name, callable_name = reference.split(":", maxsplit=1)
    if not module_name or not callable_name:
        raise WorkerConfigurationError("analysis_worker_factory_unconfigured")
    try:
        factory = getattr(import_module(module_name), callable_name)
    except (ImportError, AttributeError):
        raise WorkerConfigurationError("analysis_worker_factory_unavailable") from None
    if not callable(factory):
        raise WorkerConfigurationError("analysis_worker_factory_unavailable")
    try:
        worker = factory()
    except Exception:
        raise WorkerConfigurationError("analysis_worker_factory_unavailable") from None
    if not isinstance(worker, BackendAnalysisWorker):
        raise WorkerConfigurationError("analysis_worker_factory_invalid")
    return worker
