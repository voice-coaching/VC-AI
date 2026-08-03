"""Dataset parsing, alignment, quality-control, and split utilities."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .aihub71490 import load_aihub71490_annotations
from .intervals import Alignment, Interval, align_intervals
from .qc import audit_manifest_records
from .splits import stratified_group_split
from .textgrid import parse_textgrid
from .wav_qc import pcm_sha256

if TYPE_CHECKING:
    from .audio import (
        AudioChunk,
        DecodedAudio,
        PaddedAudioBatch,
        decode_audio,
        make_label_aware_chunks,
        pad_audio_batch,
        resample_audio,
    )

_AUDIO_EXPORTS = frozenset(
    {
        "AudioChunk",
        "DecodedAudio",
        "PaddedAudioBatch",
        "decode_audio",
        "make_label_aware_chunks",
        "pad_audio_batch",
        "resample_audio",
    }
)


def __getattr__(name: str) -> Any:
    """Load scorer-only audio dependencies only when their API is requested."""

    if name not in _AUDIO_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".audio", __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "Alignment",
    "AudioChunk",
    "DecodedAudio",
    "Interval",
    "PaddedAudioBatch",
    "align_intervals",
    "audit_manifest_records",
    "decode_audio",
    "load_aihub71490_annotations",
    "make_label_aware_chunks",
    "pad_audio_batch",
    "parse_textgrid",
    "pcm_sha256",
    "resample_audio",
    "stratified_group_split",
]
