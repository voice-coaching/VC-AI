"""Loop A candidates must arrive with their rights and synthetic status attached.

The slow real-synthesis path is exercised once behind a marker; everything else runs
against a stub worker so the contract is checked without minutes of CPU inference.
The rules under test are the ones that decide whether audio may be played to a
learner at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from voice_coach.pronunciation_feedback.adapters.reference_generator import (
    DEFAULT_INTERPRETER_RELATIVE,
    DEFAULT_VENDOR_RELATIVE,
    GENERATOR_ID,
    SPEED_BUCKETS,
    CosyVoiceReferenceGenerator,
    ReferenceGeneratorError,
    VoicePrompt,
    content_addressed_name,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[2]
INTERPRETER = ROOT / DEFAULT_INTERPRETER_RELATIVE
VENDOR = ROOT / DEFAULT_VENDOR_RELATIVE
MODEL_DIR = (
    ROOT
    / "data/cache/huggingface/hub/models--FunAudioLLM--CosyVoice2-0.5B/snapshots"
    / "eec1ae6c79877dbd9379285cf8789c9e0879293d"
)
requires_tts = pytest.mark.skipif(
    not (INTERPRETER.is_file() and VENDOR.is_dir() and MODEL_DIR.is_dir()),
    reason="the pinned TTS runtime or model is not present",
)
#: Real synthesis costs minutes of CPU, so it is opt-in rather than part of every run.
runs_real_synthesis = pytest.mark.skipif(
    os.environ.get("VOICE_COACH_RUN_SLOW_TTS") != "1",
    reason="set VOICE_COACH_RUN_SLOW_TTS=1 to run real synthesis",
)
DISCLOSURE = "이 음성은 합성된 참조음입니다."


def _prompt_audio(path: Path) -> Path:
    sample_rate = 16_000
    t = np.arange(sample_rate * 2) / sample_rate
    sf.write(path, (0.3 * np.sin(2 * np.pi * 190 * t)).astype("float32"), sample_rate)
    return path


def _voice(tmp_path: Path) -> VoicePrompt:
    return VoicePrompt(
        prompt_id="vendor-demo",
        audio_path=_prompt_audio(tmp_path / "prompt.wav"),
        license_spdx="Apache-2.0",
        rights_basis="vendor demo asset shipped with the model repository",
        speaker_axis="demo-a",
        prompt_text="프롬프트 문장입니다.",
    )


def _stub_worker(tmp_path: Path, *, duration: float = 3.0) -> Path:
    """A worker that writes real audio and answers the real protocol."""

    script = tmp_path / "stub_worker.py"
    script.write_text(
        "import json,sys\n"
        "import numpy as np, soundfile as sf\n"
        "from pathlib import Path\n"
        "req=json.loads(sys.stdin.read())\n"
        "out=Path(req['output_path'])\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        f"sr=24000; n=int(sr*{duration})\n"
        "sf.write(out, (0.2*np.sin(2*np.pi*200*np.arange(n)/sr)).astype('float32'), sr)\n"
        "json.dump({'status':'ok','protocol_version':req['protocol_version'],"
        "'generation':{'state':'observed','sample_rate':sr,"
        f"'raw_duration_seconds':{duration},'duration_seconds':{duration},"
        "'trailing_silence_trimmed':True}}, sys.stdout)\n",
        encoding="utf-8",
    )
    return script


def _generator(tmp_path: Path, **overrides) -> CosyVoiceReferenceGenerator:
    import sys as _sys

    options = {
        "repository_root": ROOT,
        "model_dir": tmp_path,
        "output_dir": tmp_path / "out",
        "disclosure": DISCLOSURE,
        "interpreter": Path(_sys.executable),
        "worker_script": _stub_worker(tmp_path),
        "vendor_dir": tmp_path,
    }
    options.update(overrides)
    return CosyVoiceReferenceGenerator(**options)


# -- rights are structural, not optional --------------------------------------


def test_a_voice_without_a_rights_basis_cannot_be_built(tmp_path: Path) -> None:
    with pytest.raises(ReferenceGeneratorError, match="rights_basis"):
        VoicePrompt(
            prompt_id="v",
            audio_path=_prompt_audio(tmp_path / "p.wav"),
            license_spdx="Apache-2.0",
            rights_basis="",
            speaker_axis="a",
            prompt_text="프롬프트",
        )


def test_a_voice_with_absent_audio_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReferenceGeneratorError, match="prompt audio is unavailable"):
        VoicePrompt(
            prompt_id="v",
            audio_path=tmp_path / "absent.wav",
            license_spdx="Apache-2.0",
            rights_basis="test",
            speaker_axis="a",
            prompt_text="프롬프트",
        )


def test_a_generator_without_a_disclosure_cannot_be_built(tmp_path: Path) -> None:
    """Synthetic audio played to a learner must say that it is synthetic."""

    with pytest.raises(ReferenceGeneratorError, match="disclosure"):
        _generator(tmp_path, disclosure="")


def test_the_exemplar_carries_the_voice_rights_not_the_generator_s(
    tmp_path: Path,
) -> None:
    exemplar, _ = _generator(tmp_path).synthesise(
        prompt_id="R00-01", text="테스트 문장입니다.", voice=_voice(tmp_path)
    )

    assert exemplar.license_spdx == "Apache-2.0"
    assert exemplar.rights_basis.startswith("vendor demo asset")
    assert exemplar.synthetic is True
    assert exemplar.disclosure == DISCLOSURE


# -- identity and reruns ------------------------------------------------------


def test_the_exemplar_names_the_generator_and_both_revisions(tmp_path: Path) -> None:
    exemplar, _ = _generator(tmp_path).synthesise(
        prompt_id="R00-01", text="테스트 문장입니다.", voice=_voice(tmp_path)
    )

    assert exemplar.generator_id == GENERATOR_ID
    # Source revision and weight revision are different things and both matter.
    assert "+" in exemplar.generator_revision


def test_the_same_request_addresses_the_same_file(tmp_path: Path) -> None:
    voice = _voice(tmp_path)
    first = content_addressed_name(
        prompt_id="R00-01", text="같은 문장", prompt=voice, speed_bucket="neutral"
    )
    second = content_addressed_name(
        prompt_id="R00-01", text="같은 문장", prompt=voice, speed_bucket="neutral"
    )

    assert first == second


@pytest.mark.parametrize(
    "changed",
    [
        {"text": "다른 문장"},
        {"speed_bucket": "fast"},
        {"prompt_id": "R00-02"},
    ],
)
def test_a_different_request_addresses_a_different_file(
    tmp_path: Path, changed: dict
) -> None:
    voice = _voice(tmp_path)
    base = dict(
        prompt_id="R00-01", text="같은 문장", prompt=voice, speed_bucket="neutral"
    )

    assert content_addressed_name(**base) != content_addressed_name(
        **{**base, **changed}
    )


def test_a_rerun_does_not_overwrite_an_existing_candidate(tmp_path: Path) -> None:
    generator = _generator(tmp_path)
    _, path = generator.synthesise(
        prompt_id="R00-01", text="테스트 문장입니다.", voice=_voice(tmp_path)
    )
    before = sha256_file(path)

    # The stub reports the real worker's refusal to overwrite.
    overwrite_stub = tmp_path / "overwrite_stub.py"
    overwrite_stub.write_text(
        "import json,sys\n"
        "req=json.loads(sys.stdin.read())\n"
        "json.dump({'status':'ok','protocol_version':req['protocol_version'],"
        "'generation':{'state':'missing','reason':'output_path_already_exists'}},"
        " sys.stdout)\n",
        encoding="utf-8",
    )
    repeat = _generator(tmp_path, worker_script=overwrite_stub)

    with pytest.raises(ReferenceGeneratorError, match="output_path_already_exists"):
        repeat.synthesise(
            prompt_id="R00-01", text="테스트 문장입니다.", voice=_voice(tmp_path)
        )
    assert sha256_file(path) == before


# -- boundary -----------------------------------------------------------------


def test_unknown_speed_bucket_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReferenceGeneratorError, match="unknown speed bucket"):
        _generator(tmp_path).synthesise(
            prompt_id="R00-01",
            text="테스트",
            voice=_voice(tmp_path),
            speed_bucket="turbo",
        )


def test_speed_buckets_are_declared_and_ordered() -> None:
    assert SPEED_BUCKETS["slow"] < SPEED_BUCKETS["neutral"] < SPEED_BUCKETS["fast"]


def test_missing_interpreter_fails_closed(tmp_path: Path) -> None:
    generator = _generator(tmp_path, interpreter=tmp_path / "no-such-python")

    with pytest.raises(ReferenceGeneratorError, match="interpreter is unavailable"):
        generator.synthesise(
            prompt_id="R00-01", text="테스트", voice=_voice(tmp_path)
        )


def test_missing_vendor_source_fails_closed(tmp_path: Path) -> None:
    generator = _generator(tmp_path, vendor_dir=tmp_path / "absent")

    with pytest.raises(ReferenceGeneratorError, match="vendored CosyVoice"):
        generator.synthesise(
            prompt_id="R00-01", text="테스트", voice=_voice(tmp_path)
        )


def test_protocol_drift_fails_closed(tmp_path: Path) -> None:
    stub = tmp_path / "drift.py"
    stub.write_text(
        "import json,sys\n"
        "json.loads(sys.stdin.read())\n"
        "json.dump({'status':'ok','protocol_version':'v0','generation':{}}, sys.stdout)\n",
        encoding="utf-8",
    )
    generator = _generator(tmp_path, worker_script=stub)

    with pytest.raises(ReferenceGeneratorError, match="protocol version differs"):
        generator.synthesise(
            prompt_id="R00-01", text="테스트", voice=_voice(tmp_path)
        )


def test_the_worker_request_passes_the_declared_speed(tmp_path: Path) -> None:
    captured = tmp_path / "request.json"
    stub = tmp_path / "capture.py"
    stub.write_text(
        "import json,sys\n"
        "import numpy as np, soundfile as sf\n"
        "from pathlib import Path\n"
        "req=json.loads(sys.stdin.read())\n"
        f"Path({str(captured)!r}).write_text(json.dumps(req))\n"
        "out=Path(req['output_path']); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "sf.write(out, np.zeros(2400, dtype='float32'), 24000)\n"
        "json.dump({'status':'ok','protocol_version':req['protocol_version'],"
        "'generation':{'state':'observed','sample_rate':24000,"
        "'raw_duration_seconds':0.1,'duration_seconds':0.1,"
        "'trailing_silence_trimmed':False}}, sys.stdout)\n",
        encoding="utf-8",
    )

    _generator(tmp_path, worker_script=stub).synthesise(
        prompt_id="R00-01",
        text="테스트",
        voice=_voice(tmp_path),
        speed_bucket="slow",
    )

    request = json.loads(captured.read_text(encoding="utf-8"))
    assert request["speed"] == SPEED_BUCKETS["slow"]
    assert request["gpu_allowed"] is False


# -- zero-shot needs the prompt's transcript ----------------------------------


def test_a_voice_without_its_transcript_cannot_be_built(tmp_path: Path) -> None:
    """Zero-shot without the prompt text is cross-lingual mode under another name.

    Measured on the R00 sentences, that mode overran a 2.6 s sentence to 14.4 s.
    """

    with pytest.raises(ReferenceGeneratorError, match="prompt_text"):
        VoicePrompt(
            prompt_id="v",
            audio_path=_prompt_audio(tmp_path / "p.wav"),
            license_spdx="Apache-2.0",
            rights_basis="test",
            speaker_axis="a",
            prompt_text="",
        )


def test_the_worker_request_carries_the_prompt_transcript(tmp_path: Path) -> None:
    captured = tmp_path / "request.json"
    stub = tmp_path / "capture_prompt_text.py"
    stub.write_text(
        "import json,sys\n"
        "import numpy as np, soundfile as sf\n"
        "from pathlib import Path\n"
        "req=json.loads(sys.stdin.read())\n"
        f"Path({str(captured)!r}).write_text(json.dumps(req, ensure_ascii=False))\n"
        "out=Path(req['output_path']); out.parent.mkdir(parents=True, exist_ok=True)\n"
        "sf.write(out, np.zeros(2400, dtype='float32'), 24000)\n"
        "json.dump({'status':'ok','protocol_version':req['protocol_version'],"
        "'generation':{'state':'observed','sample_rate':24000,"
        "'raw_duration_seconds':0.1,'duration_seconds':0.1,"
        "'trailing_silence_trimmed':False}}, sys.stdout)\n",
        encoding="utf-8",
    )

    _generator(tmp_path, worker_script=stub).synthesise(
        prompt_id="R00-01", text="테스트", voice=_voice(tmp_path)
    )

    request = json.loads(captured.read_text(encoding="utf-8"))
    assert request["prompt_text"] == "프롬프트 문장입니다."
    # The worker refuses to trim, so nothing may ask it to.
    assert request.get("trim_trailing_silence") is None


def test_two_prompts_differing_only_in_transcript_address_different_files(
    tmp_path: Path,
) -> None:
    """The transcript conditions the rendering, so it belongs in the address."""

    audio = _prompt_audio(tmp_path / "shared.wav")
    common = dict(
        prompt_id="v",
        audio_path=audio,
        license_spdx="Apache-2.0",
        rights_basis="test",
        speaker_axis="a",
    )
    first = VoicePrompt(**common, prompt_text="첫 번째 프롬프트입니다.")
    second = VoicePrompt(**common, prompt_text="두 번째 프롬프트입니다.")
    address = dict(prompt_id="R00-01", text="같은 문장", speed_bucket="neutral")

    assert content_addressed_name(
        **address, prompt=first
    ) != content_addressed_name(**address, prompt=second)


# -- what the worker inherits -------------------------------------------------


def test_the_thread_budget_reaches_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many workers run at once, so each has to be told to take fewer cores."""

    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.setenv("MKL_NUM_THREADS", "3")

    environment = _generator(tmp_path)._environment()

    assert environment["OMP_NUM_THREADS"] == "3"
    assert environment["MKL_NUM_THREADS"] == "3"


def test_the_worker_inherits_nothing_it_was_not_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allowlist, not a denylist: a secret in the parent must not travel."""

    monkeypatch.setenv("AIHUB_API_KEY", "must-not-travel")
    monkeypatch.setenv("HF_TOKEN", "must-not-travel")

    environment = _generator(tmp_path)._environment()

    assert "AIHUB_API_KEY" not in environment
    assert "HF_TOKEN" not in environment
    assert "must-not-travel" not in "".join(environment.values())


# -- the real generator -------------------------------------------------------


#: The R01 prompt set. The vendor's demo prompt is Chinese speech and this project
#: keeps no recording whose spoken language is not Korean, so the real-synthesis
#: check runs against a Korean prompt or does not run at all.
PROMPT_SET = ROOT / "data/restricted/prompt_voices/aihub542_short_v1"
requires_korean_prompt = pytest.mark.skipif(
    not (PROMPT_SET / "prompt_voices.json").is_file(),
    reason="the restricted Korean prompt set is not present",
)


@requires_tts
@requires_korean_prompt
@runs_real_synthesis
def test_real_synthesis_produces_playable_korean_audio(tmp_path: Path) -> None:
    """One real rendering, to prove the vendored runtime is wired correctly."""

    manifest = json.loads(
        (PROMPT_SET / "prompt_voices.json").read_text(encoding="utf-8")
    )
    entry = manifest["prompt_voices"][0]
    voice = VoicePrompt(
        prompt_id=entry["prompt_voice_id"],
        audio_path=PROMPT_SET / entry["audio_relative_path"],
        license_spdx=entry["license_spdx"],
        rights_basis=entry["rights_basis"],
        speaker_axis=entry["speaker_axis"],
        prompt_text=entry["prompt_transcript"],
    )
    generator = CosyVoiceReferenceGenerator(
        repository_root=ROOT,
        model_dir=MODEL_DIR,
        output_dir=tmp_path / "out",
        disclosure=DISCLOSURE,
    )

    exemplar, path = generator.synthesise(
        prompt_id="R00-01", text="쌀과 콩을 깨끗한 그릇에 담아요.", voice=voice
    )

    assert path.is_file()
    assert exemplar.sample_rate == 24_000
    assert exemplar.duration_seconds > 0.5
    assert exemplar.audio_sha256 == sha256_file(path)
