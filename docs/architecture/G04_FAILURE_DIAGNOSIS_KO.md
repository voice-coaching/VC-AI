# G04 오디오 감사와 시각 sidecar의 upstream 경계

- 최종 갱신: 2026-08-27 (Asia/Seoul)
- 문서 상태: `boundary_contract_with_partial_sidecar_implementation`
- 구현 상태: visual dependency/fail-closed mapping은 구현; production G04 receipt 연동은 `not_ready`
- 문서 역할: `boundary_note`
- 파일명 상태: `legacy_compatibility_name`

G04는 `seungun`과 `evaluation`이 소유하는 오디오 정렬·감사 경로다. 이 문서는
G04의 현재 통과 여부나 metric을 다시 판정하지 않으며, 시각 sidecar가 지켜야 할
의존성 경계만 정의한다. 현재 평가 문서는
[`G04 audio proxy protocol`](../evaluation/G04_AUDIO_AI_PROXY_PROTOCOL_KO.md)과
[`G04 audio proxy result`](../evaluation/G04_AUDIO_AI_PROXY_RESULT_2026-08-09_KO.md)를
따른다.

## 시각 sidecar가 할 수 없는 일

- G04 실패, 미완료, stale 또는 불일치 상태를 성공으로 바꾸기
- 영상·입술 cue로 오디오 label, alignment 또는 audit 판정을 수리하기
- G04 threshold, 표본, 분모, reviewer 판정 또는 provenance를 재해석하기
- G04 선행 gate를 우회해 시각 feedback을 발행하기
- 시각 QC 통과를 오디오 감사 통과 근거로 사용하기

시각 sidecar는 `SeungunPronunciationEvidencePort`가 제공하는 immutable upstream
analysis envelope와 receipt만 소비한다. 필요한 G04/upstream receipt가 없거나
호환되지 않으면 [`VisualFeedbackStatus`](state.md)의 `dependency_unavailable`을
반환한다. receipt의 무결성 또는 계약 위반을 발견하면 `visual_failed_closed`로
종료한다. 어느 경우에도 독립 시각 판정으로 audio dependency를 채우지 않는다.

반대로 영상 blur, 가림, unsupported view 같은 시각 실패는 G04 상태를 변경하지
않는다. sidecar는 `visual_rerecord_required`, `visual_unsupported` 또는
`visual_uncertain` 같은 자체 상태만 반환하며, `seungun`의 오디오 결과는 upstream
소유로 보존한다.

현재 port, reliability receipt 타입과 `dependency_unavailable` mapping은 synthetic
internal source로 검사됐다. 실제 G04 상태를 포함하는 `seungun` production facade나
receipt 발급 경로는 구현되지 않았으므로 G04 통합 완료를 주장하지 않는다.

범위 수정 전 G04 실패 원인·amendment 서술은
[`historical_snapshot`](../history/legacy_voice_coach_architecture_2026-08-26/G04_FAILURE_DIAGNOSIS_KO.md)에
보존되어 있으며 현재 시각 sidecar 아키텍처 정본이 아니다.
