# Backend 인계: 입술 부분 점수와 화면 요약

2026-11-08 AI 구현 명세. Backend 구현·배포는 이 작업에서 변경하지 않았다.
AI 새 정책과 H5의 운영 활성화도 아직 수행하지 않았다.

AI의 접수·영상 정규화·음성/입술 실행·결과 저장은 기존 독립 경로를 유지한다.
Backend readiness·예약·heartbeat·callback 의존성을 다시 추가하지 않는다.

## 결과 수집

서버 Bearer를 사용하는 기존 `/v2/audiovisual-analysis-jobs/{jobId}/result`와 artifact 수집·ACK
계약은 유지한다. 저장된 전체 결과 bytes와 digest도 변경하지 않는다.
Backend는 작업/시도 연결·서버 인증·전달 무결성을 확인하고 AI가 계산한 점수를 그대로 저장한다.
H5 revision을 새로 허용해야만 점수를 표시하는 gate나 공식 검산표 재검산은 추가하지 않는다.

새 30FPS 정책은 1프레임 정적 형상, 2프레임 폐쇄 대리값, 3프레임 이상 실제 동작을 구분한다.
관찰할 수 없는 값·없는 참조·자기 참조는 여전히 null이다.
`visual.reasonCodes`에 `PARTIAL_REFERENCE_COVERAGE`가 있으면 `visual.score`는 관찰·비교된
일부 음소의 임시 입술 점수다. 기존 종합점수의 전체 영상 가중치는 이 점수로 대체하지 않는다.
입술점수가 null이거나 범위가 부족해도 유효한 `score.overallScore`와 `coaching.feedback`는 전달한다.

## Frontend에 전달할 값

원본 내부 결과 전체를 화면 DTO로 그대로 전달할 필요가 없다.
`score.overallScore`, `visual.score`·`scoreKind`·점수 범위, `coaching.feedback`, 현재 시도 식별자와
실패 코드를 whitelist로 전달한다. 음소별 `phoneAssessments`, 측정값, 정렬 시각,
프레임·landmark·CORE·검산표는 사용자 화면 DTO에서 제외한다.

브라우저 직접 조회용 새 `GET /browser/v1/video-analysis-jobs/{jobId}/summary`의 명세는
[Frontend 인계서](lip-summary-frontend-handoff-20261108.md)와
[요약 Schema](../contracts/browser_video_summary_v1.schema.json)에 있다.
Backend 로그인 토큰이나 서버 Bearer를 이 브라우저 경로에 넘기지 않는다. 기존 Backend 발급
서명 권한을 사용하는 브라우저 경로다.

요약 응답 digest는 전체 결과 digest와 다르다. 기존 수집 ACK에는 **전체 결과의 digest**를
계속 사용한다. 요약 응답의 SHA로 ACK하거나 요약 JSON을 기존 전체 결과 원본에 덮어쓰지 않는다.
수집·화면 GET의 재시도를 새 분석/GPT 실행으로 연결하지 않는다.

정책·검증·남은 활성화 범위는 [30FPS 구현 기록](../video-lip-analysis/sparse-30fps-20261108.md)을 참고한다.
