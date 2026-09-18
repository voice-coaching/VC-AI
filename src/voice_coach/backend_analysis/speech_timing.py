"""Offline timing observations, not pronunciation scores or verified transcripts.

The optional WebRTC dependency is loaded only by explicitly requested QA. This
module is not enabled in the production worker. Policy choices are recorded,
including the diagnostic pause minimum, so different settings are not compared.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import unicodedata
import wave
from array import array
import sys

VERSION = "webrtc-speech-timing-v1"
FRAME_MS = 20
PAUSE_MINIMUM_MS = 200


def _runs(flags, value):
    spans, start = [], None
    for index, flag in enumerate([*flags, not value]):
        if flag == value and start is None:
            start = index
        elif flag != value and start is not None:
            spans.append((start * FRAME_MS, index * FRAME_MS))
            start = None
    return spans


def measure_speech_timing(path, text, *, mode=1):
    import webrtcvad

    if type(mode) is not int or mode not in range(4):
        raise ValueError("vad_mode_invalid")
    with wave.open(str(path), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(), stream.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("timing_requires_pcm16_mono_16khz")
        pcm = stream.readframes(stream.getnframes())
    if not pcm or len(pcm) % 2 or len(pcm) > 16000 * 2 * 180:
        raise ValueError("timing_audio_size_invalid")
    frame_bytes = 16000 * FRAME_MS // 1000 * 2
    vad = webrtcvad.Vad(mode)
    flags = [vad.is_speech(pcm[i:i+frame_bytes], 16000)
             for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes)]
    speech = _runs(flags, True)
    duration_ms = len(pcm) / 32
    pauses = [(start, end) for start, end in _runs(flags, False)
              if speech and speech[0][0] < start < end < speech[-1][1]
              and end - start >= PAUSE_MINIMUM_MS]
    span_ms = speech[-1][1] - speech[0][0] if speech else None
    speech_ms = sum(end - start for start, end in speech)
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(x*x for x in samples) / len(samples)) / 32768
    normalized_text = unicodedata.normalize("NFC", text)
    syllables = sum("\uac00" <= char <= "\ud7a3" for char in normalized_text)
    return dict(schemaVersion="voice-coaching.speech-timing.v1", policyRevision=VERSION,
        method="WEBRTC_VAD", implementationVersion=importlib.metadata.version("webrtcvad"),
        vadMode=mode, frameMs=FRAME_MS, pauseMinimumMs=PAUSE_MINIMUM_MS,
        pcmSha256=hashlib.sha256(pcm).hexdigest(), scriptSha256=hashlib.sha256(normalized_text.encode()).hexdigest(),
        durationMs=duration_ms, analyzedDurationMs=len(flags)*FRAME_MS,
        unclassifiedTailMs=round(duration_ms-len(flags)*FRAME_MS, 4),
        speechIntervals=[dict(startMs=s, endMs=e) for s,e in speech],
        internalPauseIntervals=[dict(startMs=s, endMs=e) for s,e in pauses],
        speechSpanMs=span_ms, speechActiveMs=speech_ms,
        leadingNonSpeechMs=speech[0][0] if speech else None,
        # A final partial frame is unclassified, not evidence of silence.
        trailingNonSpeechMs=len(flags)*FRAME_MS-speech[-1][1] if speech else None,
        internalPauseCount=len(pauses), internalPauseMs=sum(e-s for s,e in pauses),
        promptSyllableCount=syllables,
        promptSyllablesPerSpeechSpanSecond=round(syllables*1000/span_ms,4) if span_ms else None,
        promptSyllablesPerActiveSecond=round(syllables*1000/speech_ms,4) if speech_ms else None,
        rateSemantics="EXPECTED_TEXT_LENGTH_PER_DETECTED_TIME_NOT_VERIFIED_SPOKEN_RATE",
        rmsDbfs=round(20*math.log10(rms),3) if rms else None,
        clippedSampleFraction=sum(abs(x)>=32767 for x in samples)/len(samples),
        status="OBSERVED" if speech else "NO_SPEECH_DETECTED",
        qualityJudgment=None, limitations=["VAD_BOUNDARIES_NOT_PHONEME_BOUNDARIES",
            "VAD_NONSPEECH_NOT_CONFIRMED_LINGUISTIC_PAUSE", "SPOKEN_TRANSCRIPT_NOT_VERIFIED",
            "NO_NORMATIVE_RATE_OR_PAUSE_THRESHOLD"])


def compare_timing(previous, current):
    """Compare measurements only. Caller must separately establish ownership.

    This pure offline comparison does not claim chronology, learning benefit,
    matching microphones, or equivalent pronunciation from a shared script.
    """
    keys = ("schemaVersion", "policyRevision", "implementationVersion", "scriptSha256",
            "vadMode", "frameMs", "pauseMinimumMs", "rateSemantics")
    reasons = ["INCOMPATIBLE_"+key for key in keys if previous.get(key) != current.get(key)]
    if any(item.get("status") != "OBSERVED" for item in (previous,current)):
        reasons.append("SPEECH_UNAVAILABLE")
    if previous.get("pcmSha256") == current.get("pcmSha256"):
        reasons.append("SAME_AUDIO_NOT_RERECORDING")
    names = ("speechSpanMs", "speechActiveMs", "internalPauseCount", "internalPauseMs",
             "promptSyllablesPerSpeechSpanSecond", "promptSyllablesPerActiveSecond")
    return dict(schemaVersion="voice-coaching.timing-comparison.v1",
        status="NOT_COMPARABLE" if reasons else "MEASUREMENTS_ONLY", reasonCodes=reasons,
        changes=[] if reasons else [dict(metric=name, previous=previous[name], current=current[name],
                    delta=round(current[name]-previous[name],4)) for name in names],
        improvementJudgment=None, limitations=["CHRONOLOGY_NOT_VERIFIED", "RECORDING_CONDITIONS_NOT_VERIFIED",
            "MEASUREMENT_CHANGE_IS_NOT_PRONUNCIATION_IMPROVEMENT"])
