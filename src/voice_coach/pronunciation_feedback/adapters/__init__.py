"""Adapters for the pronunciation feedback pipeline.

Submodules are imported lazily because their dependencies differ: the catalog
and advice adapters need only the standard library, audio intake needs the
scorer extras (numpy/soundfile), and the expected-phone frontend needs the
pinned offline g2pK stack.
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AudioQcPolicy": ".audio_intake",
    "G2PKExpectedPhoneFrontend": ".expected_phones",
    "JsonThresholdStore": ".threshold_store",
    "LegacyAdviceCatalog": ".advice",
    "R00PromptCatalog": ".catalog_r00",
    "SoundfileAudioIntake": ".audio_intake",
    "UnavailableExpectedPhoneFrontend": ".expected_phones",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


__all__ = sorted(_EXPORTS)
