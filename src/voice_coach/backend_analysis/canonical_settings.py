"""Credential allowlist shared by root boot and the service child launcher."""
from pathlib import Path
import hashlib


def schema_digests():
    names = (
        "runpod_http_control_v1.schema.json", "runpod_result_v1.schema.json",
        "runpod_analysis_request_v2.schema.json", "runpod_http_control_v1_2.schema.json",
        "runpod_result_v4.schema.json", "runpod_canonical_parent.schema.json",
        "runpod_canonical_input.schema.json", "runpod_canonical_output.schema.json",
        "runpod_canonical_journal_v1.schema.json")
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    return [{"file": name, "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in names]

def child_environment(environment):
    names = {
        "AI_ANALYSIS_HTTP_BOOTSTRAP_READY", "AI_ANALYSIS_API_TOKEN",
        "AI_ANALYSIS_CALLBACK_TOKEN", "AI_ANALYSIS_CALLBACK_BASE_URL",
        "AI_ANALYSIS_OBJECT_STORAGE_RECORDINGS_PREFIX", "AI_ANALYSIS_OBJECT_STORAGE_BUCKET",
        "AI_ANALYSIS_OBJECT_STORAGE_REGION", "AI_ANALYSIS_OBJECT_STORAGE_ENDPOINT",
        "AI_ANALYSIS_OBJECT_STORAGE_PATH_STYLE", "AI_ANALYSIS_READINESS_OBJECT_KEY",
        "AI_ANALYSIS_READINESS_OBJECT_SHA256", "AI_ANALYSIS_READINESS_OBJECT_SIZE_BYTES",
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "OPENAI_API_KEY", "VOICE_COACH_BUILD_REVISION",
        "AI_ANALYSIS_NLTK_DATA_ROOT",
        "AI_ANALYSIS_RESOURCE_PROFILE",
        "AI_ANALYSIS_VISUAL_LANDMARK_MODEL_PATH",
    }
    values = {k: v for k, v in environment.items()
              if k in names or k.startswith("AI_CANONICAL_")}
    values.update(PATH="/usr/bin:/bin", LANG="C.UTF-8", LC_ALL="C.UTF-8",
                  PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1",
                  PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                  AWS_EC2_METADATA_DISABLED="true")
    return values


def handoff_schema_digests():
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    names = ("runpod_http_control_v1.schema.json", "runpod_http_control_v1_2.schema.json",
        "runpod_canonical_parent.schema.json", "runpod_canonical_input.schema.json",
        "runpod_canonical_output.schema.json", "runpod_canonical_journal_v1.schema.json",
        "runpod_analysis_request_v3.schema.json", "runpod_result_v5.schema.json",
        "runpod_canonical_handoff_v1.schema.json")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def audiovisual_schema_digests():
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "runpod_analysis_request_v4.schema.json", "runpod_result_v6.schema.json",
        "runpod_canonical_handoff_v2.schema.json", "runpod_media_preparation_v1.schema.json",
        "runpod_visual_coaching_input_v1.schema.json")}
