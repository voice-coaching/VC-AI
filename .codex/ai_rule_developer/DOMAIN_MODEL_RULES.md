# 도메인·계약 모델 규칙 - intelligentAI

## 현재 모델 종류

이 저장소에는 DB entity가 없다. `Entity`라는 이름을 형식적으로 추가하지 말고 다음
모델 종류를 분리한다.

| 종류 | 대표 위치 | 용도 |
| --- | --- | --- |
| Public API contract | `voice_coach/domain/models.py` | FastAPI request/response용 Pydantic 모델 |
| Pure domain value/result | `pronunciation_feedback/domain.py` 등 | 불변 dataclass, enum, 계산·상태 계약 |
| Use-case command/result | `pronunciation_attempts/services` 등 | 신뢰 경계별 서비스 입출력 |
| Port contract | 각 package의 `ports.py` | `Protocol` 기반 외부 의존성 인터페이스 |
| Artifact/runtime contract | `visual_feedback/models`, `configs/**/*.schema.json` | versioned JSON, receipt, digest, release |
| External payload | `pronunciation_feedback/grounded_advice.py` 등 | Seungun/LLM/upstream과의 제한 계약 |

같은 field 이름이 있어도 모델을 재사용해 경계를 없애지 않는다. 명시적 mapping을
작성한다.

## Pydantic API 계약

- `ContractModel`의 camelCase alias, `populate_by_name=True`, `extra="forbid"`를
  유지한다.
- 숫자 범위, 문자열 최소 길이, 시간 구간 순서와 cross-field invariant를 schema에서
  검증한다.
- API alias와 내부 snake_case를 구분한다.
- response schema를 persistence 또는 raw upstream 객체로 대체하지 않는다.
- public schema 변경은 API 문서와 compatibility 영향을 함께 검토한다.

## 순수 domain 모델

- 기본 형태는 `@dataclass(frozen=True, slots=True)`와 `StrEnum`이다. mutation이 실제
  유스케이스 상태를 표현할 때만 mutable dataclass를 사용한다.
- `__post_init__`에서 생성 즉시 불변식을 검증한다.
- domain 모듈은 filesystem path 열기, network 요청, subprocess/model load를 하지
  않는다.
- `Path`나 민감 locator를 public repr/result에 넣지 않는다. 필요한 필드는
  `repr=False` 또는 opaque/hash identity로 제한한다.
- float는 finite/range를 검증하고 JSON 직렬화에서 NaN/Infinity를 허용하지 않는다.

## 상태와 enum

- 상태는 기존 `AttemptStatus`, `FailureCode`, `MetricState`, `VideoAttemptStatus`,
  `VisualStageStatus`, `WorkspaceCleanupStatus` 같은 enum/Literal vocabulary를 우선한다.
- 새 문자열 상태를 임의로 추가하지 않고 producer, consumer, schema, config, test를
  함께 찾는다.
- terminal success, fail-closed, unavailable, skipped, not-run을 서로 합치지 않는다.
- optional 값과 상태를 함께 가진 계약에서는 조합 불변식을 검증한다. 예를 들어
  `supported=True`이면 reliability receipt가 반드시 있어야 한다.
- legacy raw state string을 발견해도 요청 없이 전역 enum migration을 하지 않는다.

## Metric과 과학적 의미

- 측정 부재는 `0.0`이 아니다. `MetricState` 또는 명시적 null과 reason을 사용한다.
- detector ranking score, confidence, probability, severity, distance를 이름이나 문구로
  서로 바꾸지 않는다.
- expected phone, observed candidate, gold label, automatic alignment를 구분한다.
- `observedCandidate`는 현재 계약에서 ground truth transcription이 아니다.
- aggregate 결과와 row-level 결과, synthetic fixture와 actual-source evidence를
  구분한다.

## Artifact·receipt 계약

- 직렬화 가능한 artifact는 schema version, producer/source/model revision, status와
  필요한 digest를 명시한다.
- canonical JSON/digest 규칙이 있는 모델은 key, encoding, float, self-digest 규칙을
  바꾸지 않는다.
- deserializer는 추가/누락 field, duplicate key, 잘못된 enum, 비정상 숫자, digest
  mismatch를 fail-closed 한다.
- receipt는 실행 근거이지 권한 그 자체가 아니다. rights, consent, locked access,
  promotion authorization은 각각의 별도 계약으로 유지한다.
- `TBD_*` template은 미충족 전제다. 임의 digest나 status로 채우지 않는다.

## 저장 모델 도입 조건

현재 서비스는 무상태이며 `docs/database/schema.md`도 DB 미도입을 명시한다. DB가
요청될 경우 먼저 다음을 확정한다.

- 저장 목적과 owner module
- 개인정보와 음성/영상 metadata 최소화
- 동의, 보존기간, 삭제·철회, audit 요구
- transaction/idempotency/concurrency
- migration과 rollback
- API/domain/persistence 모델 간 mapping

그 다음에만 SQLAlchemy/SQLModel entity와 repository를 추가한다. entity를 public API
응답으로 직접 반환하지 않는다.

## 변경 검증

- constructor/validation, serialization round-trip, unknown field, 상태 조합, digest
  drift 테스트를 선택한다.
- schema version이나 enum 변경 시 모든 config와 consumer를 `rg`로 검색한다.
- source-pinned config가 참조하는 파일을 바꾸면 pin 검사를 실행하고, 실패를 무조건
  재계산으로 덮지 않는다.
