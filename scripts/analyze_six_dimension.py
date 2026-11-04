#!/usr/bin/env python3
"""Run Seungun audio analysis (or reuse its bound result), then six-score coaching."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_coach.six_dimension.pipeline import evaluate
from voice_coach.six_dimension.policy import encode, error_code, load_policy, require, sha, strict_json
from voice_coach.six_dimension.transport import OpenAITransport


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def bounded(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(maximum+1)
    require(0 < len(raw) <= maximum, "INPUT_FILE_SIZE")
    return raw


def write_private(path: Path, value) -> None:
    raw = encode(value)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(raw)


def bound_analysis(directory: Path, script: str) -> tuple[bytes, dict]:
    source = strict_json(bounded(directory / "result.json", 32*1024*1024))
    require(source.get("schemaVersion") == "seungun-standalone-result.v1", "SEUNGUN_RESULT_CONTRACT")
    require(source.get("scriptSha256") == sha(script.encode("utf-8")), "SCRIPT_BINDING")
    raw = bounded(directory / "evidence.json", 32*1024*1024)
    require(source.get("evidenceSha256") == sha(raw), "EVIDENCE_BINDING")
    core = strict_json(raw)
    require(source.get("requestId") == core.get("request_id")
            and source.get("sourceAudioSha256") == core.get("source_audio_sha256"), "SOURCE_BINDING")
    require(file_sha(directory / "prepared.wav") == core.get("prepared_wav_sha256"), "PREPARED_BINDING")
    return raw, dict(requestId=source["requestId"], coreSha256=sha(raw),
                     scriptSha256=source["scriptSha256"], sourceAudioSha256=source["sourceAudioSha256"],
                     preparedWavSha256=core["prepared_wav_sha256"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--audio", type=Path)
    inputs.add_argument("--analysis-dir", type=Path)
    text = parser.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--script-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=ROOT / "configs/pronunciation_feedback/six_dimension_v1.json")
    parser.add_argument("--system-prompt", type=Path, default=ROOT / "configs/coaching/six_dimension_system_v1.txt")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--timeout", type=int, default=900, help="acoustic analysis timeout in seconds")
    parser.add_argument("--measurements-only", action="store_true", help="compute provisional scores without a GPT call")
    args = parser.parse_args()
    output = None
    try:
        script = args.text if args.text is not None else bounded(args.script_file, 8192).decode("utf-8").strip()
        require(1 <= len(script.strip()) <= 500, "SCRIPT_SIZE")
        require(1 <= args.timeout <= 3600, "ACOUSTIC_TIMEOUT")
        policy, policy_sha = load_policy(args.policy)
        prompt = bounded(args.system_prompt, 131072).decode("utf-8")
        destination = args.output.expanduser().resolve()
        require(not destination.exists(), "OUTPUT_MUST_BE_NEW")
        destination.mkdir(parents=True, mode=0o700)
        output = destination
        # Snapshot the exact editable policy and prompt before any inference/provider request.
        with (output / "policy.json").open("xb") as stream:
            os.chmod(output / "policy.json", 0o600)
            policy_raw = args.policy.read_bytes()
            require(sha(policy_raw) == policy_sha, "POLICY_CHANGED_DURING_RUN")
            stream.write(policy_raw)
        with (output / "system_prompt.txt").open("xb") as stream:
            os.chmod(output / "system_prompt.txt", 0o600)
            stream.write(prompt.encode("utf-8"))
        analysis = args.analysis_dir.expanduser().resolve(strict=True) if args.analysis_dir else output / "acoustic"
        if args.audio:
            launcher = ROOT / "standalone/seungun/run.py"
            require(launcher.is_file(), "SEUNGUN_LAUNCHER_MISSING")
            audio = args.audio.expanduser().resolve(strict=True)
            command = [sys.executable, str(launcher), "--device", args.device]
            if args.cache:
                command.extend(["--cache", str(args.cache.expanduser().resolve())])
            command.extend(["analyze", "--audio", str(audio), "--text", script,
                            "--output", str(analysis), "--timeout", str(args.timeout)])
            acoustic_env = {key: value for key, value in os.environ.items()
                            if key not in ("OPENAI_API_KEY", "OPENAI_API_KEY_FILE")}
            # No automatic retry. The original acoustic artifacts survive later GPT failures.
            with (output / "acoustic-launch.log").open("xb") as log:
                os.chmod(output / "acoustic-launch.log", 0o600)
                completed = subprocess.run(command, env=acoustic_env, stdout=log, stderr=subprocess.STDOUT)
            require(completed.returncode == 0, "ACOUSTIC_ANALYSIS_FAILED")
        raw, binding = bound_analysis(analysis, script)
        if args.audio:
            require(binding["sourceAudioSha256"] == file_sha(audio), "SOURCE_AUDIO_CHANGED")
        def transport(payload, *, timeout):
            # Resolve credentials only when there is a validated request to dispatch.
            from voice_coach.backend_analysis.secret_files import secret_text
            key = secret_text(os.environ, value_name="OPENAI_API_KEY", file_name="OPENAI_API_KEY_FILE", maximum_bytes=8192)
            return OpenAITransport(key)(payload, timeout=timeout)
        result = evaluate(core_bytes=raw, script=script, binding=binding, policy=policy,
                          policy_sha256=policy_sha, prompt=prompt, transport=transport,
                          measurements_only=args.measurements_only)
        schema = strict_json(bounded(ROOT / "docs/contracts/six_dimension_result_v1.schema.json", 262144))
        import jsonschema
        jsonschema.Draft202012Validator(schema).validate(result)
        write_private(output / "result.json", result)
        print(json.dumps({"result": str(output / "result.json"), "assessmentStatus": result["assessment"]["status"],
                          "generationStatus": result["generationStatus"], "dispatchAttempts": result["dispatchAttempts"]}))
        return 0 if result["assessment"]["status"] == "READY" and (
            args.measurements_only or result["generationStatus"] == "STRUCTURALLY_VALID") else 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Persist symbolic codes or the class, never provider bodies or user text.
        failure = dict(status="FAILED", errorCode=error_code(exc))
        if output is not None and output.is_dir() and not (output / "error.json").exists():
            write_private(output / "error.json", failure)
        print(json.dumps(failure), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
