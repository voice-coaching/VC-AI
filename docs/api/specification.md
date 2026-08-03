# API 명세 - intelligentAI

정확한 API 계약을 이 문서에 기록한다.

## GET /health

- 설명: API 프로세스의 기본 상태를 확인한다.
- 인증: 없음
- Response body: `{"status": "ok"}`
- Status codes: `200`

## POST /v1/oracle-attempts

- 설명: 모델 없이 정답 오류 라벨을 사용해 `DiagnosticProfile`과 근거 제한
  코칭 계약을 검증한다. 연구·테스트 전용이며 실제 자동 진단 endpoint가 아니다.
- 인증: 없음
- Request body:
  - `attemptId`: UUID
  - `targetText`: 목표 문장
  - `contentMatch`: 내용 일치 점수와 confidence
  - `aspects`: segmental, fluency, prosody, comprehensibility 점수
  - `goldIssues`: 시간 구간, 오류 유형, severity, confidence, evidence
  - `confidenceThreshold`: 선택, 기본값 0.75
- Response body: `DiagnosticProfile` schema version `1.0`
- Status codes:
  - `200`: 계약 생성 성공
  - `422`: 입력 schema 위반
- Error cases:
  - 역전되거나 음수인 시간 구간
  - 0–1 범위를 벗어난 점수
  - 허용되지 않은 evidence ID 또는 추가 필드

## 외부 API 매핑
- upstream 계약이 확인된 경우에만 내부 endpoint와 upstream 호출 관계를 기록한다.
- 현재 외부 API 매핑은 없다.
