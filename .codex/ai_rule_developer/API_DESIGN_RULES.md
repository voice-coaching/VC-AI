# API 설계 규칙 - intelligentAI

## 현재 API 성격

이 저장소에는 서로 다른 두 FastAPI 경계가 있다.

1. `src/voice_coach/api/app.py`
   - `/health`
   - `/v1/oracle-attempts`
   - `/v1/closed-loop/oracle-attempts`
   - 모델 자동 진단이 아니라 정답 label로 계약을 검증하는 무상태 연구 API
2. `src/voice_coach/evaluation/g04_amended_review_app.py`
   - `/`, `/assets/*`, `/api/v1/review-*`
   - loopback 전용 전문가 검수 GUI/API

두 경계를 합치거나 모든 endpoint에 `/api/v1`을 일괄 적용하지 않는다. 기존 path는
공개 계약이며 변경 요청과 migration 계획 없이 정규화하지 않는다.

## Route 책임

- FastAPI route는 request parsing, dependency 연결, application/service 호출,
  response/error mapping만 담당한다.
- 음성/영상 decode, 모델 추론, 외부 HTTP, 파일 저장, gate 판단, 학습을 route에서
  직접 수행하지 않는다.
- Pydantic 입력 형식 검증과 service 유스케이스 검증을 구분한다.
- domain/service 예외를 안정된 HTTP status와 privacy-safe error body로 변환한다.
- 기존 local review app의 helper를 수정할 때 token 검사, path containment,
  `Cache-Control: no-store`, 보안 header와 원자 저장을 유지한다.

## Schema 규칙

- 새로 추가하거나 변경하는 public request/response는 명시적 Pydantic schema와
  `response_model`을 사용한다.
- API용 schema는 domain dataclass, artifact JSON schema, 외부 LLM payload와 분리한다.
- `ContractModel` 계열은 camelCase alias, field-name 입력 허용, `extra="forbid"`라는
  기존 계약을 유지한다.
- 모든 field에 type, required/optional, null 의미, 범위, enum/Literal을 명시한다.
- 값이 없음을 0, 빈 문자열 또는 빈 object로 위장하지 않는다.
- schema version과 기존 alias를 제거하거나 의미를 바꾸면 명시적 version/migration이
  필요하다.

## 리소스와 동작

- URL은 연구 계약의 실제 리소스와 lifecycle을 표현한다. 내부 worker, pipeline,
  checkpoint 파일명을 public resource로 노출하지 않는다.
- GET은 읽기 전용이어야 한다. state를 생성·교체하는 동작은 method와 idempotency를
  명확히 한다.
- POST/PUT의 중복·교체 정책, side effect, cleanup, timeout을 숨기지 않는다.
- 원본 경로, 파일명, token, speaker/participant ID, 내부 예외, raw model output을
  response에 포함하지 않는다.

## 연구 전용 계약

- Oracle endpoint를 실제 모델 inference 또는 production readiness endpoint로
  설명하지 않는다.
- `analysisSource="oracle"`과 모델 기반 결과를 혼동하지 않는다.
- 현재 endpoint에 audio/video upload, 사용자 session persistence, background queue,
  production auth가 있다고 가정하지 않는다.
- 이런 기능이 요청되면 API부터 만들지 말고 동의, media 제한, 보존/삭제, timeout,
  동시성, job state와 실패 복구 계약을 먼저 설계한다.

## G04 loopback API

- review token은 최소 길이와 constant-time 비교를 유지한다.
- 원본 utterance ID와 audio path 대신 salt/hash 기반 식별자만 반환한다.
- audio는 dataset root containment를 다시 확인하고 loopback에서 `no-store`로 제공한다.
- POST는 최초 저장, PUT은 명시적 교체라는 현재 의미를 유지한다.
- loopback token을 기관 인증이나 인터넷 공개 보안으로 과장하지 않는다.

## 문서 동기화

- 공개 endpoint 변경 시 `docs/api/endpoints.md`와 `docs/api/specification.md`의 endpoint
  set을 함께 갱신한다.
- `endpoints.md`에는 method/path/auth/한 줄 설명만 둔다.
- `specification.md`에는 field, status, error, auth, privacy, side effect를 기록한다.
- 외부 model endpoint는 내부 public API와 분리해 timeout, revision, fallback을
  문서화한다.

## 검증

- FastAPI `TestClient`로 성공, 422 validation, auth 실패, not-found/conflict와 privacy
  invariant를 검증한다.
- base API import가 torch/scorer를 요구하지 않는 테스트를 유지한다.
- 변경된 endpoint는 문서 목록과 실제 `app.routes`를 대조한다.
