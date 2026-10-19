from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .aihub71557_reference import Aihub71557V2Analysis
from .pronunciation_reliability import PronunciationPresentation
POLICY_VERSION: Literal['ai_core_evidence_policy_v1'] = 'ai_core_evidence_policy_v1'

class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

class AICoreEvidence(_Model):
    code: str = Field(min_length=1)
    component: Literal['pronunciation', 'prosody']
    expected_index: int | None = Field(default=None, ge=0)
    metric_name: str | None = None

class AICoreEvidencePolicyResult(_Model):
    policy_version: Literal['ai_core_evidence_policy_v1'] = POLICY_VERSION
    status: Literal['ok', 'partial']
    evidence: tuple[AICoreEvidence, ...]
    limitations: tuple[str, ...] = Field(min_length=1)
    natural_language_feedback: tuple[str, ...] = ()

def build_ai_core_evidence_policy(*, pronunciation: None, pronunciation_presentation: PronunciationPresentation | None, aihub71557_v2: Aihub71557V2Analysis | None, pronunciation_requested: bool, aihub71557_requested: bool) -> AICoreEvidencePolicyResult:
    """Expose calibrated review evidence and observed reference bands."""
    evidence: list[AICoreEvidence] = []
    incomplete = False
    if aihub71557_requested:
        if aihub71557_v2 is None or aihub71557_v2.user_metrics.metrics.status != 'ok':
            incomplete = True
            evidence.append(AICoreEvidence(code='AIHUB71557_COMPONENT_UNAVAILABLE', component='prosody'))
        else:
            for metric in aihub71557_v2.comparison.metrics:
                if metric.user_value is None:
                    code = 'AIHUB71557_METRIC_UNAVAILABLE'
                elif metric.within_iqr:
                    code = 'AIHUB71557_WITHIN_IQR'
                else:
                    code = 'AIHUB71557_OUTSIDE_IQR'
                evidence.append(AICoreEvidence(code=code, component='prosody', metric_name=metric.metric_name))
    return AICoreEvidencePolicyResult(status='partial' if incomplete else 'ok', evidence=tuple(evidence), limitations=('이 policy는 calibrated pronunciation review와 reference comparison band만 구조화하며 자연어 coaching이나 새 품질 threshold를 만들지 않습니다.',))
