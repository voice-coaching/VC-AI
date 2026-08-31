"""Shared lock for process-local AI Hub 538 capability lifetimes."""

from __future__ import annotations

from threading import RLock


AIHUB538_CAPABILITY_LIFETIME_LOCK = RLock()


__all__ = ["AIHUB538_CAPABILITY_LIFETIME_LOCK"]
