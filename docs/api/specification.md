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

## G04 T43/I30 로컬 검수 API

이 API는 `scripts/prepare_g04_amended_audit.py serve`가 기본
`127.0.0.1:8766`에서 제공하는 전문가용 로컬 인터페이스다. 학습자용 서비스
API가 아니며 기관 인증·TLS·다중 사용자 권한 관리를 제공하지 않는다. `/`와
`/assets/*`는 정적 shell만 반환한다. 제한 metadata와 audio를 반환하거나 state를
바꾸는 `/api/v1/*` 요청에는 실행 시 발급한 `X-Review-Token` header가 필수다.

공통 privacy 계약:

- 항목 식별자는 salt된 `item_sha256`만 반환한다.
- 원본 utterance ID, 파일명, 절대·상대 경로는 응답과 state에 반환하지 않는다.
- reviewer pseudonym은 서버에서 즉시 hash하고 원문은 저장·반환하지 않는다.
- audio locator는 dataset root 내부인지 다시 검증하고 `Cache-Control: no-store`로
  반환한다.
- 브라우저 shell에는 CSP, `no-referrer`, `nosniff`, frame 거부 header를 적용한다.

### GET /api/v1/review-session

- Response: `profile`, `expected_count`, `completed_count`, `remaining_count`,
  `completion_fraction`, `reviewer_id_sha256`, `access_control_warning`
- Status: `200`, token 누락·불일치 `403`

### GET /api/v1/review-items

- Response item: `audit_index`, `item_sha256`, `split`, `l1`, `gender`,
  `proficiency`, `error_presence`, `duration_seconds`, `target_phone_count`,
  `realized_phone_count`, `reviewed`
- 원본 ID·audio path·interval은 목록 응답에 포함하지 않는다.
- Status: `200`, token 누락·불일치 `403`

### GET /api/v1/review-items/{item_hash}

- Response: 목록 item 필드와 `target_phone_intervals`,
  `realized_phone_intervals`, `automatic_phone_alignments`, `review`, `profile`
- Status: `200`, token 누락·불일치 `403`, 선택 집합 밖 hash `404`

### GET /api/v1/review-items/{item_hash}/audio

- Response: `audio/wav`, inline, `Cache-Control: no-store`
- Status: `200`, token 누락·불일치 `403`, unknown/unavailable `404`, dataset root
  탈출 locator `400`

### POST 또는 PUT /api/v1/review-items/{item_hash}/review

공통 request는 `alignment_correct`, `maximum_boundary_error_ms`,
`incorrect_interval_count`, `notes`를 받는다. 빈 선택은 허용하지 않으며 추가
field는 거부한다.

- T43은 `annotation_fidelity`, `root_cause`, `resolution`도 필수다. 문제 후보
  원인·조치 판정이며 accuracy 분모로 사용하지 않는다.
- I30은 위 T43 전용 세 field를 반드시 `null`로 보내고 phone interval 오류 수로
  accuracy를 계산한다.
- `POST`는 최초 저장만 허용하고 기존 판정이면 `409`다. `PUT`은 기존 판정을
  의도적으로 교체한다. 두 동작 모두 state 전체를 원자 저장한다.
- Response: `item_sha256`, hash reviewer와 submission을 포함한 `review`, 갱신된
  `status`
- Status: `200`, token 누락·불일치 `403`, unknown hash `404`, 중복 POST `409`,
  profile·field·notes·interval 계약 위반 `422`
