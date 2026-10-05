# FE–RunPod 직접 분석 전환

2026-10-05 갱신. 사용자 최종 결정: **인증 게이트 없이 RunPod가 요청을 접수하고 결과를 직접 반환한다.** 기존 문서의 인증 게이트웨이 제안과 RunPod 소유자 인증 조건은 폐기한다.

작업 유형: 외부 연동 구현·서비스 로직·UI 경계·문서화. GLOBAL_RULES 및 아키텍처·서비스·API·외부 연동·도메인·문서 규칙을 적용하되 공개 분석 인증 여부는 현재 사용자 결정을 우선한다.

## 확정 구조

`FE → RunPod 접수·분석 → FE 결과 표시`

`RunPod → 비동기 B2 근거 보관·기존 녹음 저장소 보관·AWS 이력 반영`

직접 분석에는 사용자 JWT, 쿠키, API key, 인증 게이트웨이가 없다. AWS 사전 예약/DB/outbox/claim/heartbeat와 결과 ACK, B2 업로드를 결과 대기 경로에서 제거한다. 분석 입력·Native 근거·채점 계산 자체의 검증은 유지한다. RunPod→AWS 후처리는 기존 서버 bearer를 사용한다.

## 구현된 범위

| 구성 | 구현 |
|---|---|
| RunPod API | 무인증 raw WAV 접수, UUID 작업 조회·멱등 시도 복구·SSE·취소 |
| 실행 | 단일 supervisor, 영속 로컬 journal, Native resident, deadline·취소·재시작 회복 |
| 결과 | 로컬 확정 후 SSE에 점수·코칭·MFA 근거 포함; AWS/B2를 기다리지 않음 |
| 후처리 | 별도 archive/history lane, 영속 재시도 상태, B2 version/digest 확인, AWS 멱등 이벤트 |
| AWS | V40 direct history inbox·사용자 연결, 기본 OFF API, 기존 분석 경로와 별도 테이블 |
| FE | 직접 client·결과 UI·새로고침/응답 유실 복구·별도 이력 표시 |

API schema 이름은 `voice-coaching.direct-analysis.v1`, 저장 이벤트는 `voice-coaching.analysis-history-event.v1`이다. AWS 숫자 analysisId/recordingId/sessionId를 선발급하거나 가짜 값을 만들지 않는다.

분석에 계정 인증을 요구하지 않으므로 RunPod는 제출된 userId를 소유자로 신뢰하지 않는다. FE가 보유한 임의 historyClaim의 digest를 접수 때 저장하고, **결과 표시 뒤** 기존 AWS 로그인으로 실제 계정과 연결한다. 이는 RunPod 접수 게이트가 아니다. 브라우저 종료 시 분석·보관은 계속되며, 아직 계정 연결을 못 했다면 같은 브라우저 재진입 때 연결한다.

첫 적용 범위는 원래 계획대로 독립 음성 연습이다. 과정·시험·기존 sessionId 요청은 기존 완료 계약을 유지한다. direct 이력은 별도 표시하며 기존 과정 완료·학습일 집계를 변경하지 않는다.

## 작업본

세 저장소에 `feat/direct-ingress-20261005` 격리 브랜치를 만들었다. 기존 Native DRAFT 변경을 이어받았으며 원래 작업본을 덮어쓰지 않았다.

- AI: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/ai-direct-ingress-20261005`
- BE: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/backend-direct-ingress-20261005`
- FE: `C:/Users/Public/Documents/ESTsoft/CreatorTemp/frontend-direct-ingress-20261005`

정확한 파일·API·복구·설정 계약은 [구현 기록](direct-ingress-implementation-20261005.md)을 따른다. 현재 문서를 운영 활성화 기록으로 취급하지 않는다.

## 남은 운영 단계

1. Native 승인·보정·모음 매핑·artifact를 확정한다. 현재 정책 DRAFT와 빈 실행 allowlist는 추론 readiness를 차단한다. 구버전 Seungun fallback은 없다.
2. 접수 OFF 상태로 별도 RunPod service, HTTPS origin, POSIX 볼륨·파일 잠금·fsync·재기동 정리를 확인한다. B2 versioned write/read와 기존 녹음 bucket의 전용 PUT/HEAD 권한을 준비한다.
3. AWS V40을 검증하고 direct history 기능을 활성화한다. 이전 JAR 호환성을 확인하고 신규 데이터를 보존한다.
4. FE에 공개 HTTPS origin을 설정하여 빌드하고 실제 CORS/SSE·재접속·후처리 이력 연결을 개발자가 확인한다. 이후 direct 신규 접수를 켠다.
5. 같은 testvideo1·원고·revision과 warm/cold 조건으로 다시 측정한다. 직접 연결 구현만으로 기존 179.275초가 얼마나 줄었는지 주장하지 않는다.

현재 자동 TTL 청소/7일 tombstone·410 응답·계정 삭제에 따른 archive 정리는 구현하지 않았다. spool은 2GiB/1000건 한도에서 신규 접수를 거절하며 미보관 자료를 자동 삭제하지 않는다. 장기 운영 전 보관 수명 정책과 복구를 확인해야 한다.

컴파일·문서 정적 확인은 실제 분석/품질/브라우저 QA와 별개다. 사용자 후속 요청에 따라 세 작업본을 커밋·푸시하여 Draft PR로 인계한다(AI main, BE develop, FE dev 대상). 병합·배포·실제 추론 재측정은 수행하지 않는다. BE develop 병합 자동 배포를 유발하지 않는다.
