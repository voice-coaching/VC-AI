from __future__ import annotations
import tomllib
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
ENGINEERING_DEFAULT = '발음 품질 기준이 아닌 엔지니어링 초기값'

class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

class PathSettings(ContractModel):
    project_root: Path = Field(default_factory=Path.cwd)
    output_dir: Path = Path('outputs')
    work_dir: Path = Path('.cache/work')
    mfa_executable: Path = Path('.conda-envs/mfa/bin/mfa')
    mfa_root_dir: Path = Path('.cache/mfa')

    @model_validator(mode='before')
    @classmethod
    def resolve_project_paths(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        project_root = Path(data.get('project_root', Path.cwd())).expanduser()
        project_root = project_root.resolve(strict=False)
        data['project_root'] = project_root
        defaults = {'output_dir': Path('outputs'), 'work_dir': Path('.cache/work'), 'mfa_executable': Path('.conda-envs/mfa/bin/mfa'), 'mfa_root_dir': Path('.cache/mfa')}
        for field_name, default in defaults.items():
            path = Path(data.get(field_name, default)).expanduser()
            explicitly_external_mfa_artifact = field_name in {'mfa_executable', 'mfa_root_dir'} and path.is_absolute()
            if not path.is_absolute():
                path = project_root / path
            path = path.resolve(strict=False)
            if not explicitly_external_mfa_artifact and (not path.is_relative_to(project_root)):
                raise ValueError(f'{field_name} must be inside project_root')
            data[field_name] = path
        return data

class AnalysisSettings(ContractModel):
    max_script_chars: int = Field(default=500, ge=1, strict=True, description=ENGINEERING_DEFAULT)
    max_audio_s: float = Field(default=60.0, gt=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)

class AudioSettings(ContractModel):
    ffmpeg_command: str = Field(default='ffmpeg', min_length=1)
    ffprobe_command: str = Field(default='ffprobe', min_length=1)
    timeout_s: int = Field(default=60, ge=1, strict=True, description=ENGINEERING_DEFAULT)

class AlignmentSettings(ContractModel):
    model_repository: Literal['MontrealCorpusTools/korean_mfa'] = 'MontrealCorpusTools/korean_mfa'
    model_license: Literal['CC-BY-4.0'] = 'CC-BY-4.0'
    model_revision: str = Field(default='0091ffa1f1ef7df380a4f799b3fb5bc80c3f65cd', pattern='^[0-9a-f]{40}$')
    mfa_timeout_s: float = Field(default=120.0, gt=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)

class AcousticSettings(ContractModel):
    pitch_floor_hz: float = Field(default=75.0, gt=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)
    pitch_ceiling_hz: float = Field(default=500.0, gt=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)
    time_step_s: float = Field(default=0.01, gt=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)
    min_silence_s: float = Field(default=0.2, ge=0, allow_inf_nan=False, description=ENGINEERING_DEFAULT)

    @model_validator(mode='after')
    def validate_pitch_range(self) -> AcousticSettings:
        if self.pitch_ceiling_hz <= self.pitch_floor_hz:
            raise ValueError('pitch_ceiling_hz must be greater than pitch_floor_hz')
        return self

class XlsrPronunciationSettings(ContractModel):
    enabled: Literal[False] = False

class FinalPronunciationSettings(ContractModel):
    enabled: Literal[False] = False

class Aihub71557Settings(ContractModel):
    """Opt-in approved 71557 prosody reference; JSON remains outside Git."""
    enabled: bool = False
    reference_path: Path | None = None
    reference_group: Literal['현직아나운서', '전직아나운서', '아나운서준비생', '전체'] = '현직아나운서'

    @model_validator(mode='before')
    @classmethod
    def expand_external_reference_path(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get('reference_path') is not None:
            data['reference_path'] = Path(data['reference_path']).expanduser()
        return data

class UiSettings(ContractModel):
    server_name: str = '127.0.0.1'
    share: bool = False

    @model_validator(mode='after')
    def keep_gradio_local(self) -> UiSettings:
        if self.server_name != '127.0.0.1' or self.share:
            raise ValueError('Gradio must remain local with sharing disabled')
        return self

class PocConfig(ContractModel):
    schema_version: Literal['1.0.0'] = '1.0.0'
    paths: PathSettings = Field(default_factory=PathSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    alignment: AlignmentSettings = Field(default_factory=AlignmentSettings)
    acoustics: AcousticSettings = Field(default_factory=AcousticSettings)
    pronunciation: XlsrPronunciationSettings = Field(default_factory=XlsrPronunciationSettings)
    final_pronunciation: FinalPronunciationSettings = Field(default_factory=FinalPronunciationSettings)
    aihub71557: Aihub71557Settings = Field(default_factory=Aihub71557Settings)
    ui: UiSettings = Field(default_factory=UiSettings)

def load_poc_config(path: Path) -> PocConfig:
    config_path = path.expanduser().resolve(strict=True)
    with config_path.open('rb') as handle:
        data = tomllib.load(handle)
    paths = dict(data.get('paths', {}))
    raw_project_root = Path(paths.get('project_root', '..'))
    if not raw_project_root.is_absolute():
        raw_project_root = config_path.parent / raw_project_root
    paths['project_root'] = raw_project_root.resolve(strict=False)
    data['paths'] = paths
    pronunciation = dict(data.get('pronunciation', {}))
    model_dir = pronunciation.get('model_dir')
    if model_dir is not None and (not Path(model_dir).expanduser().is_absolute()):
        pronunciation['model_dir'] = raw_project_root / Path(model_dir).expanduser()
    data['pronunciation'] = pronunciation
    final_pronunciation = dict(data.get('final_pronunciation', {}))
    artifact_root = final_pronunciation.get('artifact_root')
    if artifact_root is not None and (not Path(artifact_root).expanduser().is_absolute()):
        final_pronunciation['artifact_root'] = (raw_project_root / Path(artifact_root).expanduser()).resolve(strict=False)
    reliability_policy_path = final_pronunciation.get('reliability_policy_path')
    if reliability_policy_path is not None and (not Path(reliability_policy_path).expanduser().is_absolute()):
        final_pronunciation['reliability_policy_path'] = (raw_project_root / Path(reliability_policy_path).expanduser()).resolve(strict=False)
    data['final_pronunciation'] = final_pronunciation
    aihub71557 = dict(data.get('aihub71557', {}))
    reference_path = aihub71557.get('reference_path')
    if reference_path is not None and (not Path(reference_path).expanduser().is_absolute()):
        aihub71557['reference_path'] = (raw_project_root / Path(reference_path).expanduser()).resolve(strict=False)
    data['aihub71557'] = aihub71557
    return PocConfig.model_validate(data)
