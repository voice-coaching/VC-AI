from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .ai_core_policy import AICoreEvidencePolicyResult, build_ai_core_evidence_policy
from .aihub71557_reference import Aihub71557ReferenceComparison, Aihub71557UserMetricsV2
from .config import PocConfig
from .errors import AnalysisStatus
from .pipeline import AnalysisPipeline, PipelineDependencies, PipelineRunResult
from .input_validation import InputValidationResultV1, classify_pipeline_run, reject_if_canonical_pcm_all_zero
from .pronunciation_reliability import PronunciationPresentation
from .schemas import AcousticFeature, AnalysisRequest, Provenance
AI_CORE_SCHEMA_VERSION: Literal['ai_core_result_v1'] = 'ai_core_result_v1'

class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

class AICoreConfig(_Model):
    pipeline_config: PocConfig
    platform_role: Literal['mac_smoke', 'dlpc_formal', 'test', 'unknown'] = 'unknown'
    enable_input_validation_v1: bool = False

class AICoreComponentStatus(_Model):
    status: Literal['ok', 'partial', 'not_requested', 'not_run', 'error']
    failure_code: str | None = None

class AICorePhoneDecision(_Model):
    expected_index: int = Field(ge=0)
    expected_phone: str = Field(min_length=1)
    expected_phone_id: int = Field(ge=0, le=42)
    observed_candidate: str | None
    alignment_operation: Literal['correct', 'substitution', 'deletion']
    start_ms: float | None = Field(default=None, ge=0.0)
    end_ms: float | None = Field(default=None, ge=0.0)
    error_score: float = Field(allow_inf_nan=False)
    operating_threshold: float = Field(allow_inf_nan=False)
    is_error: bool
    hard_error: bool
    score_semantics: str = Field(min_length=1)

class AICoreInsertionCandidate(_Model):
    observed_phone: str = Field(min_length=1)
    start_ms: float = Field(ge=0.0)
    end_ms: float = Field(ge=0.0)

class AICorePronunciationResult(_Model):
    status: Literal['ok', 'partial', 'not_requested', 'not_run', 'error']
    failure_code: str | None = None
    detector_version: str = 'RETIRED_NOT_EXECUTED'
    expected_phone_source: str = 'g2pk_compat_jamo_v1'
    expected_phones: tuple[str, ...] = ()
    ctc_hypothesis: tuple[str, ...] = ()
    insertion_candidates: tuple[AICoreInsertionCandidate, ...] = ()
    phones: tuple[AICorePhoneDecision, ...] = ()
    logits_shape: tuple[int, int] | None = None
    layer18_shape: tuple[int, int] | None = None
    notices: tuple[str, ...] = ()
    presentation: PronunciationPresentation | None = None
    presentation_failure_code: str | None = None

class AICoreProsodyResult(_Model):
    status: Literal['ok', 'partial', 'not_requested', 'not_run', 'error']
    legacy_robust_metrics_v1: tuple[AcousticFeature, ...] = ()
    aihub71557_compatible_metrics_v2: Aihub71557UserMetricsV2 | None = None
    aihub71557_reference_comparison_v1: Aihub71557ReferenceComparison | None = None
    analysis_window_source: str | None = None
    failure_code: str | None = None

class AICoreMetadata(_Model):
    analysis_id: str = Field(min_length=1)
    legacy_analysis_schema_version: str = '1.0.0'
    detector_version: str = 'RETIRED_NOT_EXECUTED'
    stage1_model_safetensors_sha256: None = None
    expected_phone_source: str = 'g2pk_compat_jamo_v1'
    reference_version: str | None = None
    reference_group: str | None = None
    analysis_window_source: str | None = None
    reference_window_source: str | None = None
    notices: tuple[str, ...] = ()
    legacy_provenance: Provenance

class AICoreResultV1(_Model):
    schema_version: Literal['ai_core_result_v1'] = AI_CORE_SCHEMA_VERSION
    status: Literal['ok', 'partial', 'error']
    pronunciation: AICorePronunciationResult
    prosody: AICoreProsodyResult
    policy: AICoreEvidencePolicyResult
    metadata: AICoreMetadata
_LEGACY_ROBUST_FEATURE_NAMES = {'internal_pause_count', 'internal_pause_total_duration', 'speech_f0_valid_ratio', 'f0_semitone_relative_median', 'f0_semitone_percentile_range', 'speech_relative_intensity_db', 'speech_intensity_percentile_range'}

def _pronunciation_result(run: PipelineRunResult, config: AICoreConfig) -> AICorePronunciationResult:
    return AICorePronunciationResult(status='not_requested')

def _prosody_result(run: PipelineRunResult, config: AICoreConfig) -> AICoreProsodyResult:
    legacy = tuple((feature for feature in run.result.acoustic_features if feature.name in _LEGACY_ROBUST_FEATURE_NAMES))
    aihub = run.aihub71557_v2
    if not config.pipeline_config.aihub71557.enabled:
        return AICoreProsodyResult(status='not_requested', legacy_robust_metrics_v1=legacy)
    if aihub is None:
        return AICoreProsodyResult(status='error' if run.aihub71557_failure_reason else 'not_run', legacy_robust_metrics_v1=legacy, failure_code=run.aihub71557_failure_reason or 'not_run')
    if aihub.user_metrics.metrics.status != 'ok':
        return AICoreProsodyResult(status='error', legacy_robust_metrics_v1=legacy, aihub71557_compatible_metrics_v2=aihub.user_metrics, aihub71557_reference_comparison_v1=aihub.comparison, analysis_window_source=aihub.user_metrics.analysis_window_source, failure_code=aihub.user_metrics.metrics.status)
    return AICoreProsodyResult(status='ok', legacy_robust_metrics_v1=legacy, aihub71557_compatible_metrics_v2=aihub.user_metrics, aihub71557_reference_comparison_v1=aihub.comparison, analysis_window_source=aihub.user_metrics.analysis_window_source)

def _overall_status(*, legacy_status: AnalysisStatus, pronunciation: AICorePronunciationResult, prosody: AICoreProsodyResult) -> Literal['ok', 'partial', 'error']:
    if legacy_status is AnalysisStatus.FAILED:
        return 'error'
    if legacy_status is AnalysisStatus.PARTIAL:
        return 'partial'
    if pronunciation.status in {'partial', 'error', 'not_run'} or prosody.status in {'error', 'not_run'}:
        return 'partial'
    return 'ok'

def build_ai_core_result(*, run: PipelineRunResult, config: AICoreConfig) -> AICoreResultV1:
    pronunciation = _pronunciation_result(run, config)
    prosody = _prosody_result(run, config)
    aihub = run.aihub71557_v2
    policy = build_ai_core_evidence_policy(pronunciation=run.final_pronunciation, pronunciation_presentation=run.pronunciation_presentation, aihub71557_v2=aihub, pronunciation_requested=False, aihub71557_requested=config.pipeline_config.aihub71557.enabled)
    comparison = aihub.comparison if aihub is not None else None
    return AICoreResultV1(status=_overall_status(legacy_status=run.result.status, pronunciation=pronunciation, prosody=prosody), pronunciation=pronunciation, prosody=prosody, policy=policy, metadata=AICoreMetadata(analysis_id=str(run.result.analysis_id), detector_version=pronunciation.detector_version, expected_phone_source=pronunciation.expected_phone_source, reference_version=comparison.reference_version if comparison else None, reference_group=comparison.reference_group if comparison else None, analysis_window_source=comparison.analysis_window_source if comparison else None, reference_window_source=comparison.reference_window_source if comparison else None, notices=tuple((item for item in (pronunciation.failure_code, prosody.failure_code) if item is not None)), legacy_provenance=run.result.provenance))

def analyze_reading(*, text: str, audio_path: str | Path, config: AICoreConfig) -> AICoreResultV1:
    """Run the local pure-AI core without exposing model-specific runtime details."""
    pipeline = AnalysisPipeline(config.pipeline_config, platform_role=config.platform_role)
    run = pipeline.analyze(AnalysisRequest(script=text, audio_path=Path(audio_path), source='upload'))
    return build_ai_core_result(run=run, config=config)

class AICoreCandidateRunV1(_Model):
    """Candidate-only envelope; core_result keeps its original schema and values."""
    input_validation_result_v1: InputValidationResultV1 | None
    core_result: AICoreResultV1 | None

def analyze_reading_candidate(*, text: str, audio_path: str | Path, config: AICoreConfig) -> AICoreCandidateRunV1:
    if not config.enable_input_validation_v1:
        return AICoreCandidateRunV1(input_validation_result_v1=None, core_result=analyze_reading(text=text, audio_path=audio_path, config=config))
    defaults = PipelineDependencies()

    def validated_prepare_audio(*args, **kwargs):
        audio = defaults.prepare_audio(*args, **kwargs)
        reject_if_canonical_pcm_all_zero(audio)
        return audio
    pipeline = AnalysisPipeline(config.pipeline_config, platform_role=config.platform_role, dependencies=replace(defaults, prepare_audio=validated_prepare_audio))
    run = pipeline.analyze(AnalysisRequest(script=text, audio_path=Path(audio_path), source='upload'))
    validation = classify_pipeline_run(run, audio_path=Path(audio_path), ffprobe_command=config.pipeline_config.audio.ffprobe_command, ffprobe_timeout_s=config.pipeline_config.audio.timeout_s)
    core_result = build_ai_core_result(run=run, config=config) if validation.status in {'ACCEPT', 'INCONCLUSIVE'} else None
    return AICoreCandidateRunV1(input_validation_result_v1=validation, core_result=core_result)
