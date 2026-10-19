from __future__ import annotations
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4
from .audio import AudioResult, prepare_audio
from .config import AcousticSettings, Aihub71557Settings, PocConfig
from .aihub71557_reference import Aihub71557V2Analysis, compare_from_mfa_speech_bounds
from .errors import AnalysisStatus, ErrorCode, PipelineError, PipelineFailure, PipelineStage
from .feedback import generate_feedback
from .g2p import G2pService, PronunciationResult
from .manifest import PlatformRole, collect_provenance
from .mfa import MfaRunResult, run_mfa
from .prosody import ProsodyResult, extract_prosody
from .pronunciation_reliability import PronunciationPresentation
from .pronunciation_phone_contract import PronunciationPhoneContract, build_pronunciation_phone_contract
from .robust_metrics import RobustMetricsResult, calculate_robust_metrics
from .schemas import AnalysisRequest, AnalysisResult, FeedbackItem, Provenance, TimeInterval
from .script import ScriptResult, prepare_script
from .storage import save_result
from .textgrid import TextGridParseResult, parse_textgrid
from .timing import TimingResult, calculate_timing

class G2pConverter(Protocol):

    def convert(self, script: ScriptResult) -> PronunciationResult:
        ...
AudioPreparer = Callable[[Path, Path, PocConfig], AudioResult]
G2pFactory = Callable[[], G2pConverter]
MfaRunner = Callable[[AudioResult, ScriptResult, UUID, PocConfig], MfaRunResult]
TextGridParser = Callable[..., TextGridParseResult]
TimingCalculator = Callable[..., TimingResult]
ProsodyExtractor = Callable[[Path, AcousticSettings], ProsodyResult]
RobustMetricsCalculator = Callable[..., RobustMetricsResult]
Aihub71557Runner = Callable[[Path, str, list[TimeInterval], Aihub71557Settings], Aihub71557V2Analysis]
FeedbackGenerator = Callable[[AnalysisResult], list[FeedbackItem]]
ResultSaver = Callable[[AnalysisResult, PocConfig], Path]
ProvenanceCollector = Callable[..., Provenance]

def _configured_aihub71557_reference_path(settings: Aihub71557Settings) -> Path:
    if settings.reference_path is None:
        raise ValueError('aihub71557 reference path is not configured')
    return settings.reference_path

@dataclass(frozen=True)
class PipelineDependencies:
    prepare_audio: AudioPreparer = prepare_audio
    g2p_factory: G2pFactory = G2pService
    run_mfa: MfaRunner = run_mfa
    parse_textgrid: TextGridParser = parse_textgrid
    calculate_timing: TimingCalculator = calculate_timing
    extract_prosody: ProsodyExtractor = extract_prosody
    calculate_robust_metrics: RobustMetricsCalculator = calculate_robust_metrics
    run_aihub71557: Aihub71557Runner = lambda prepared_wav, normalized_script, word_intervals, settings: compare_from_mfa_speech_bounds(prepared_wav, text=normalized_script, word_intervals=word_intervals, reference_path=_configured_aihub71557_reference_path(settings), reference_group=settings.reference_group)
    generate_feedback: FeedbackGenerator = generate_feedback
    save_result: ResultSaver = save_result
    collect_provenance: ProvenanceCollector = collect_provenance

@dataclass(frozen=True)
class PipelineRunResult:
    result: AnalysisResult
    timing: TimingResult | None
    result_path: Path
    pronunciation_ctc_baseline: None = None
    final_pronunciation: None = None
    pronunciation_presentation: PronunciationPresentation | None = None
    pronunciation_presentation_failure_reason: str | None = None
    aihub71557_v2: Aihub71557V2Analysis | None = None
    aihub71557_failure_reason: str | None = None

def _recoverable(error: PipelineError) -> PipelineError:
    return error.model_copy(update={'recoverable': True})

def _unexpected(stage: PipelineStage, exc: Exception) -> PipelineError:
    return PipelineError(code=ErrorCode.INTERNAL_ERROR, stage=stage, message_ko='분석 단계에서 예상하지 못한 오류가 발생했습니다.', recoverable=True, details={'exception_type': type(exc).__name__})

def _safe_remove_session(path: Path, config: PocConfig) -> None:
    resolved = path.resolve(strict=False)
    work_root = config.paths.work_dir.resolve(strict=False)
    if resolved.is_relative_to(work_root) and resolved != work_root:
        shutil.rmtree(resolved, ignore_errors=True)

class AnalysisPipeline:

    def __init__(self, config: PocConfig, *, platform_role: PlatformRole, dependencies: PipelineDependencies | None=None, id_factory: Callable[[], UUID]=uuid4) -> None:
        if config.pronunciation.enabled or config.final_pronunciation.enabled:
            raise ValueError('RETIRED_PRONUNCIATION_EXECUTION_FORBIDDEN')
        self._config = config
        self._platform_role = platform_role
        self._dependencies = dependencies or PipelineDependencies()
        self._id_factory = id_factory

    def analyze(self, request: AnalysisRequest) -> PipelineRunResult:
        analysis_id = self._id_factory()
        pipeline_workdir = self._config.paths.work_dir / 'pipeline' / str(analysis_id)
        mfa_workdir = self._config.paths.work_dir / 'mfa' / str(analysis_id)
        errors: list[PipelineError] = []
        script: ScriptResult | None = None
        audio: AudioResult | None = None
        pronunciation: PronunciationResult | None = None
        mfa_result: MfaRunResult | None = None
        parsed: TextGridParseResult | None = None
        timing: TimingResult | None = None
        prosody: ProsodyResult | None = None
        robust_metrics: RobustMetricsResult | None = None
        pronunciation_ctc_baseline: None = None
        final_pronunciation: None = None
        pronunciation_phone_contract: PronunciationPhoneContract | None = None
        pronunciation_presentation: PronunciationPresentation | None = None
        pronunciation_presentation_failure_reason: str | None = None
        aihub71557_v2: Aihub71557V2Analysis | None = None
        aihub71557_failure_reason: str | None = None
        terminal_failure = False
        try:
            try:
                script = prepare_script(request.script, settings=self._config.analysis)
            except PipelineFailure as exc:
                errors.append(exc.error)
                terminal_failure = True
            if not terminal_failure:
                try:
                    audio = self._dependencies.prepare_audio(request.audio_path, pipeline_workdir, self._config)
                except PipelineFailure as exc:
                    errors.append(exc.error)
                    terminal_failure = True
                except Exception as exc:
                    error = _unexpected(PipelineStage.AUDIO, exc)
                    errors.append(error.model_copy(update={'recoverable': False}))
                    terminal_failure = True
            if not terminal_failure and script is not None and (audio is not None):
                try:
                    pronunciation = self._dependencies.g2p_factory().convert(script)
                except PipelineFailure as exc:
                    errors.append(_recoverable(exc.error))
                except Exception as exc:
                    errors.append(_unexpected(PipelineStage.G2P, exc))
                try:
                    mfa_result = self._dependencies.run_mfa(audio, script, analysis_id, self._config)
                except PipelineFailure as exc:
                    errors.append(_recoverable(exc.error))
                except Exception as exc:
                    errors.append(_unexpected(PipelineStage.ALIGNMENT, exc))
                if mfa_result is not None:
                    try:
                        parsed = self._dependencies.parse_textgrid(mfa_result.textgrid_path, audio_duration_s=audio.metadata.duration_s)
                    except PipelineFailure as exc:
                        errors.append(_recoverable(exc.error))
                    except Exception as exc:
                        errors.append(_unexpected(PipelineStage.TEXTGRID, exc))
                if self._config.aihub71557.enabled:
                    if self._config.aihub71557.reference_path is None:
                        aihub71557_failure_reason = 'reference_not_configured'
                    elif parsed is None:
                        aihub71557_failure_reason = 'mfa_speech_bounds_unavailable'
                    else:
                        try:
                            aihub71557_v2 = self._dependencies.run_aihub71557(audio.prepared_wav, script.normalized, parsed.word_intervals, self._config.aihub71557)
                        except Exception as exc:
                            aihub71557_failure_reason = type(exc).__name__
                if parsed is not None:
                    try:
                        timing = self._dependencies.calculate_timing(script, word_intervals=parsed.word_intervals, audio_duration_s=audio.metadata.duration_s, min_silence_s=self._config.acoustics.min_silence_s)
                    except PipelineFailure as exc:
                        errors.append(_recoverable(exc.error))
                    except Exception as exc:
                        errors.append(_unexpected(PipelineStage.ALIGNMENT, exc))
                try:
                    prosody = self._dependencies.extract_prosody(audio.prepared_wav, self._config.acoustics)
                except PipelineFailure as exc:
                    errors.append(_recoverable(exc.error))
                except Exception as exc:
                    errors.append(_unexpected(PipelineStage.ACOUSTIC, exc))
                if timing is not None and parsed is not None:
                    try:
                        robust_metrics = self._dependencies.calculate_robust_metrics(timing, word_intervals=parsed.word_intervals, prosody=prosody)
                    except PipelineFailure as exc:
                        errors.append(_recoverable(exc.error))
                    except Exception as exc:
                        errors.append(_unexpected(PipelineStage.ACOUSTIC, exc))
            provenance = self._dependencies.collect_provenance(self._config, self._platform_role, mfa_result=mfa_result)
            status = AnalysisStatus.FAILED if terminal_failure else AnalysisStatus.PARTIAL if errors else AnalysisStatus.SUCCESS
            features = [*(timing.features if timing is not None else []), *(prosody.features if prosody is not None else []), *(robust_metrics.features if robust_metrics is not None else [])]
            draft = AnalysisResult(analysis_id=analysis_id, status=status, original_script=request.script, normalized_script=script.normalized if script is not None else None, display_pronunciation=pronunciation.display_hangul if pronunciation is not None else None, audio_metadata=audio.metadata if audio is not None else None, word_intervals=parsed.word_intervals if parsed is not None else [], phone_intervals=parsed.phone_intervals if parsed is not None else [], acoustic_features=features, feedback=[], errors=errors, provenance=provenance)
            feedback: list[FeedbackItem] = []
            if status is not AnalysisStatus.FAILED:
                try:
                    feedback = self._dependencies.generate_feedback(draft)
                except PipelineFailure as exc:
                    errors.append(_recoverable(exc.error))
                except Exception as exc:
                    errors.append(_unexpected(PipelineStage.FEEDBACK, exc))
                if len(errors) != len(draft.errors):
                    draft = AnalysisResult.model_validate({**draft.model_dump(mode='python'), 'status': AnalysisStatus.PARTIAL, 'errors': errors, 'feedback': []})
                    feedback = []
            result = AnalysisResult.model_validate({**draft.model_dump(mode='python'), 'feedback': feedback})
            result_path = self._dependencies.save_result(result, self._config)
            return PipelineRunResult(result=result, timing=timing, pronunciation_ctc_baseline=pronunciation_ctc_baseline, final_pronunciation=final_pronunciation, pronunciation_presentation=pronunciation_presentation, pronunciation_presentation_failure_reason=pronunciation_presentation_failure_reason, aihub71557_v2=aihub71557_v2, aihub71557_failure_reason=aihub71557_failure_reason, result_path=result_path)
        finally:
            _safe_remove_session(pipeline_workdir, self._config)
            _safe_remove_session(mfa_workdir, self._config)
