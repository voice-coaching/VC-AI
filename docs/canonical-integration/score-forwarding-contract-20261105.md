# 기존 종합점수 필드 연결과 검산표 책임

사용자는 AI 서버의 새 종합점수를 기존 프론트·백엔드 스키마 이름으로 연결하고, 프론트·백엔드에서 공식 검산표를 다시 검증하는 게이트를 제거하도록 요청했다. 점수 계산과 공식 검산은 AI에 유지한다.

## 이름과 출력

| 경계 | 유지하는 이름 |
| --- | --- |
| AI → Backend 내부 결과 | `voice-coaching.runpod-analysis-result.v6` |
| Backend → Frontend 공개 뷰 | 기존 `voice-coaching.canonical-analysis-view.v3` 및 과거 v2 뷰 |
| AI → Frontend 직접 분석 | `voice-coaching.direct-analysis.v1`, `voice-coaching.direct-practice-result.v1` |
| 공개 종합점수 | 기존 `score.overallScore` |
| 공개 피드백 | 기존 `coaching.feedback`, direct의 기존 `practiceResult.feedback` |
| AI private 결과 | 기존 `voice-coaching.six-dimension-result.v1` |

이번 작업에서 별도로 만들었던 private result v2 이름/파일은 기존 v1에 통합했다. 계산 방식은 `policyRevision=announcer-composite-exponential-v2`로 구분한다. 서로 다른 경계의 envelope 이름을 하나로 섞지 않는다.

종합 모드에서는 AI가 검산한 private `compositeGrade.overallScore`를 기존 공개 `score.overallScore`에 복사한다. 계산 가능하면 기존 `validity=RUBRIC_COMPUTED`, `reason=EVIDENCE_BOUND_PRACTICE_SCORE`, 계산 불가이면 기존 `UNSCORABLE/SCORING_FAILED` 상태를 사용한다. `rubricRevision`이나 음소별 `criteria`가 없어도 유효한 종합점수다. 종합 모드가 아닌 과거 결과의 점수 projection은 유지한다.

공식 검산표는 private BRIDGE_RESULT에 보존한다. 공개 callback coaching에서는 `sixDimension`과 `scoring`을 제거한다. Backend/Frontend는 이 검산표를 얻거나 재구성할 필요가 없다. 기존 Backend `Score` DTO와 프론트 score parser가 읽는 종합점수 필드는 유지한다.

## 제거한 소비자 게이트

- Backend의 result v4/v5/v6 공개 `computedScore`에서 채점표 revision 허용 목록, 9행 개수, 음소 ID 순서, 단계별 level 요구를 제거했다. 오래된 coaching의 `scoringRecord`는 불투명한 AI 메타데이터로 읽는다.
- Backend 공개 DTO의 과거 `rubricRevision`/`criteria`는 선택적 불투명 메타데이터로 전달하며 음소 표로 역직렬화하지 않는다. 숫자 종합점수·기존 상태 필드는 그대로 처리한다.
- Frontend의 computed score는 종합점수와 기존 상태 필드를 읽고, 선택적 채점표 메타데이터를 공식 검산표로 검증하지 않는다. 알 수 없는 채점표 버전·없는 criteria 때문에 분석 결과 전체를 거부하지 않는다. 과거 음소별 세부 표시를 읽을 수 있으면 선택적으로 표시하되, 세부 표시 실패는 총점 수락에 영향을 주지 않는다.
- 점수 유무로 피드백의 접수 가능성을 다시 결정하던 프론트 검사를 제거했다. 전체 평가불가의 과거 `NO_PERMITTED_COACHING_CONTENT + selection=null`도 읽을 수 있다. 새 AI 패키지는 평가 가능한 항목이 전혀 없으면 기존 `FAIL_CLOSED + NO_SCORABLE_DIMENSIONS` 상태로 명시한다. GPT dispatch나 가짜 selection은 만들지 않는다.

프론트의 요청/실행/음원 식별, 기본 JSON 형식, 점수의 유한한 0~100 숫자 또는 기존 null 상태, 서버가 부여한 동작 권한 검사는 유지한다. Backend의 인증·소유 관계·파일 연결을 이 요청의 검산표 게이트로 해석해 변경하지 않는다.

## 배포 순서

1. 프론트의 새 parser를 먼저 배포한다. 기존 9항목 점수와 메타데이터 없는 종합점수를 모두 읽는다.
2. Backend를 배포한다. AI의 종합점수를 기존 DTO로 전달하며 검산표를 다시 검증하지 않는다.
3. 정확히 등록한 새 AI H5와 소스를 coordinated 배포한다. 기존 공개 필드로 종합점수를 전달한다.

Frontend 기준은 main `35be3ef8e9c137614c1eb7f61d7b7f433bd8c011`, Backend 기준은 develop/기존 운영 소스 `56d2816a613e40302e809acc4cfa34f7215f2a04`다. 폐기한 기존 6항목 확장 PR이나 터널 복구 브랜치를 재사용하지 않고 별도 작업 브랜치를 사용한다. 이번 코드 수정만으로 세 서비스의 운영 배포를 완료한 것은 아니다.

발음 정확성은 사용자가 지정한 기존 서버 계산기를 계속 따른다. AI 자체에서 필수 관측·검산표가 없는 경우의 null을 임의 점수로 바꾸는 요청은 아니며, 소비자 게이트 제거와 AI의 채점 가능 여부는 별개다.

변경 전 백업은 `/home/2020067/intelligentAI/systempromptbackup/score-forwarding-20261105T004528Z`에 세 저장소별로 보관했다. 비밀값·녹음은 포함하지 않는다. 컴파일·타입·스키마/실행 등록의 정적 확인과 실제 음성·GPT 품질 QA는 구분한다.
