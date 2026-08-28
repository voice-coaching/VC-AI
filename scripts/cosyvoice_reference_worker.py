#!/usr/bin/env python3
"""Loop A reference candidate synthesis (CosyVoice2) from the pinned TTS venv.

CosyVoice2 needs its own dependency set — transformers 4.51, hydra, diffusers,
onnxruntime — and the scorer environment is pinned by the S104/S105 execution locks.
So this worker runs under `.venv-paper-cosyvoice` with the vendored CosyVoice source
on its path, and speaks one JSON request/response over stdin/stdout.

Three things this worker refuses to decide on its own.

It does not choose the prompt voice. CosyVoice2-0.5B has no built-in speakers, so
every mode is prompt-conditioned, which means the caller must name a voice whose
rights basis it can state. A worker that silently defaulted to some voice would make
a rights decision by omission.

It synthesises zero-shot, not cross-lingual. Cross-lingual mode takes the prompt's
speech tokens and no transcript, which is right when the prompt and the target are
different languages. Korean prompt to Korean sentence is not that case, and measured
on the R00 sentences the difference is not subtle: with a Korean prompt, cross-lingual
mode ran 14.4 s for a sentence that zero-shot rendered in 2.6 s. So the request must
carry the prompt's transcript, and the worker refuses without it — the transcript is
what tells the model where the prompt ended.

It does not trim. It used to: the model sometimes appends babble after the sentence,
and an amplitude heuristic cut the recording at the first sustained quiet stretch.
Measured against the R00 bank, that heuristic removed 63% of one rendering — a slow
reading pauses between phrases for longer than any hold worth calling a tail, so the
"first gap" it found was mid-sentence, and the slowest renderings were cut earliest.
Amplitude cannot tell an intra-sentence pause from an ended sentence.

A bad rendering is therefore rejected rather than repaired: the caller measures the
speech rate against a band taken from real read speech and asks for another
rendering. That keeps every exemplar something the model actually produced.

It does not judge its own output. Whether the synthesis says the right words is R02's
hard gate, run separately with an independent ASR.

No speaker name, prompt path, or digest of the prompt is echoed back.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys

PROTOCOL_VERSION = "voice_coach.cosyvoice_reference_worker.v1"
_REQUIRED_FIELDS = (
    "protocol_version",
    "text",
    "prompt_audio_path",
    "prompt_text",
    "output_path",
    "speed",
)
#: The revision this worker was verified against. A different checkout is a different
#: generator and must be attested separately.
VENDOR_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
MODEL_REVISION = "eec1ae6c79877dbd9379285cf8789c9e0879293d"
#: CosyVoice2 renders at 24 kHz.
OUTPUT_SAMPLE_RATE = 24_000
MINIMUM_SPEED = 0.5
MAXIMUM_SPEED = 2.0


def _fail(detail: str) -> int:
    json.dump(
        {"status": "failed_closed", "error": detail},
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 1


def _patch_audio_io() -> None:
    """Give CosyVoice a working ``torchaudio.load``.

    torchaudio 2.11 routes ``load`` through TorchCodec, which is not installed and
    needs system FFmpeg. CosyVoice calls ``torchaudio.load`` internally on the prompt,
    so the read is served by soundfile instead. This changes how bytes are read, not
    what they are.
    """

    import soundfile as sf
    import torch
    import torchaudio

    def _load(uri, **_: object):
        data, sample_rate = sf.read(str(uri), dtype="float32", always_2d=True)
        return torch.from_numpy(data.T), sample_rate

    torchaudio.load = _load


def _synthesise(request: dict) -> dict:
    import soundfile as sf
    import torch

    _patch_audio_io()
    from cosyvoice.cli.cosyvoice import CosyVoice2

    model_dir = request["model_dir"]
    with contextlib.redirect_stdout(sys.stderr):
        model = CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)
        chunks = [
            item["tts_speech"]
            for item in model.inference_zero_shot(
                request["text"],
                request["prompt_text"],
                request["prompt_audio_path"],
                stream=False,
                speed=float(request["speed"]),
            )
        ]
    if not chunks:
        return {"state": "missing", "reason": "generator_returned_no_audio"}
    speech = torch.cat(chunks, dim=1).squeeze(0).cpu().numpy()
    raw_seconds = float(speech.shape[0]) / model.sample_rate

    output_path = Path(request["output_path"])
    if output_path.exists():
        # Content-addressed artifacts are never overwritten in place.
        return {"state": "missing", "reason": "output_path_already_exists"}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, speech, model.sample_rate)
    return {
        "state": "observed",
        "sample_rate": int(model.sample_rate),
        "raw_duration_seconds": raw_seconds,
        "duration_seconds": float(speech.shape[0]) / model.sample_rate,
        # Kept as a field, always false: a reader of an old receipt needs to be able
        # to tell "this worker does not trim" from "this field did not exist yet".
        "trailing_silence_trimmed": False,
        "vendor_revision": VENDOR_REVISION,
        "model_revision": MODEL_REVISION,
        "speed": float(request["speed"]),
        "inference_mode": "zero_shot",
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail("request is not valid JSON")
    if not isinstance(request, dict):
        return _fail("request must be an object")
    missing = [name for name in _REQUIRED_FIELDS if name not in request]
    if missing:
        return _fail("request is missing fields: " + ", ".join(missing))
    if request["protocol_version"] != PROTOCOL_VERSION:
        return _fail("worker protocol version differs")
    if not isinstance(request.get("text"), str) or not request["text"].strip():
        return _fail("text must be a non-empty string")
    prompt = request["prompt_audio_path"]
    if not isinstance(prompt, str) or not Path(prompt).is_file():
        return _fail("prompt audio is unavailable")
    if not isinstance(request.get("prompt_text"), str) or not request["prompt_text"].strip():
        # Zero-shot without the prompt's transcript is cross-lingual mode wearing a
        # different name, and that is what made the model overrun the sentence.
        return _fail("prompt_text must be a non-empty string")
    model_dir = request.get("model_dir")
    if not isinstance(model_dir, str) or not Path(model_dir).is_dir():
        return _fail("model directory is unavailable")
    try:
        speed = float(request["speed"])
    except (TypeError, ValueError):
        return _fail("speed must be a number")
    if not MINIMUM_SPEED <= speed <= MAXIMUM_SPEED:
        return _fail("speed is outside the declared range")
    if request.get("trim_trailing_silence", False):
        # Refused rather than ignored. A caller that asked for trimming and got an
        # untrimmed file back would report a duration it did not measure.
        return _fail("this worker does not trim; see the module docstring")

    # Keep generation off the GPU that training holds unless explicitly allowed.
    if not request.get("gpu_allowed", False):
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    try:
        measurement = _synthesise(request)
    except ImportError:
        return _fail("the pinned TTS runtime is not installed")
    except (OSError, RuntimeError, ValueError):
        return _fail("the pinned TTS runtime failed on this request")

    json.dump(
        {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "generation": measurement,
        },
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
