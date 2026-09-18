#!/usr/bin/env python
"""Retired universal lip calibration publisher.

A shared aperture reference and input video count are not evaluation evidence.
This entry point deliberately produces no enabled release or P8 receipt.
Use reviewed phone/context-specific calibration artifacts and independently
measured evaluation receipts with the production release packager instead.
"""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_known_args(argv)
    parser.error(
        "NOT_EVALUATED: universal same-cohort calibration cannot be promoted. "
        "No evaluation was executed and no violation count is known. "
        "Supply independently reviewed phone/context calibration and evaluation "
        "receipts before using production_visual_release.package_enabled_production_visual_release. "
        "No files have been created."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
