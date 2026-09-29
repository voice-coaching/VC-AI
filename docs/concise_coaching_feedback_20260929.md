# 짧은 발음 피드백

2026-09-29 사용자 요청: 총평은 발음 개선에 바로 사용할 수 있는 5문장 이하로 표시한다. 항목 수, 모델 표지, 정렬·판단 보류 설명은 총평에 넣지 않는다.

## 구현

- [코칭 시스템 프롬프트](../configs/pronunciation_feedback/prompts/coaching/pronunciation-coaching-v1.md)에 전체 5문장 이하, 후보별 action 한 문장·140자 이하를 명시했다. 최대 3개 후보를 유지하므로 일반 총평은 1~3문장이다. 다섯 항목을 만들기 위해 근거 없는 교정을 추가하지 않는다.
- [coaching.py](../src/voice_coach/backend_analysis/coaching.py)는 action이 마지막 마침표 하나로 끝나는 한 줄인지, UTF-16 길이가 140 이하인지, 내부 진단 용어를 포함하는지 검사한다. 실패하면 원래 후보에 연결된 짧은 기본 연습으로 대체한다. 문장 일부를 기계적으로 잘라 내지 않는다.
- `coaching.summary`는 검증된 action만 줄바꿈으로 연결한 완성 총평이다. 후보가 없으면 오류나 완벽함을 주장하지 않는 일반 연습 한 문장을 제공하고 기존 status를 유지한다.
- [service.py](../src/voice_coach/backend_analysis/service.py)는 기존 summaryFeedback 제공 조건을 유지하면서 이 총평만 전달한다. observation, practice, selfCheck를 다시 이어 붙이지 않는다. COMPLETED_NO_ISSUE의 기존 summaryFeedback=null 계약은 유지한다.
- 구조화한 items·observation·coverage·limitations·score·generation은 유지한다. UI가 직접 items를 조합한다면 이 제한이 적용된 summary를 사용해야 하며, 세부 필드를 다시 이어 붙이면 안 된다. 이 작업에서 FE는 수정하지 않았다.
- [전문가 사례](expert_feedback_rag_20260928.md), 음소 후보 선정, 최대 3개 제한, 근거 없는 개인 진단·입술 교정·속도·억양 판단 금지는 유지한다. 예시 문장을 현재 녹음의 관측으로 복사하지 않는다.
- JSON 필드와 schema version은 유지한다. 활성 프롬프트 본문이 변경되므로 다음 프로세스 로드 시 promptSha256은 변경된다.

## 반영 범위

로컬 구현이며 RunPod 배포·프로세스 재시작·기존 저장 피드백 갱신은 수행하지 않았다. 수정한 Python 두 파일의 구문 컴파일과 git diff --check를 통과했다. 실제 LLM 생성·발음 품질·브라우저·자동 회귀 QA는 수행하지 않았으며 개발자가 확인한다. 문장 형식과 길이 검증이 교육적 정확성을 보증하지는 않는다.
