# API 설계 규칙 - intelligentAI

모든 공개 API는 리소스 중심 REST 설계를 따른다.

[기본 규칙]
- 프로젝트에 별도 버전 규칙이 없으면 기본 prefix는 `/api/v1`로 둔다.
- URL은 명사형으로 작성하고 동작은 HTTP method로 표현한다.
- 구현 함수가 아니라 프로젝트 도메인 리소스를 기준으로 API를 설계한다.
- Request DTO/schema, Response DTO/schema, persistence entity/model을 분리한다.
- 공개 엔드포인트는 모두 `docs/api/endpoints.md`와 `docs/api/specification.md`에 문서화한다.
- 각 엔드포인트에는 method, path, request field, response field, status code, error case를 포함한다.
- 문서화되지 않은 응답 필드나 숨은 부수효과를 추가하지 않는다.

[리소스 흐름]
- 엔드포인트를 추가하기 전에 도메인 리소스 흐름을 먼저 정의한다.
- 하위 리소스는 실제 소유 관계나 생명주기 관계가 있을 때만 사용한다.
- pipeline, worker 같은 내부 구현 이름은 제품 계약이 아닌 한 public resource로 노출하지 않는다.

[Python]
- 공개 함수, 클래스, CLI 옵션은 외부 계약으로 보고 이름과 반환 형식을 안정적으로 관리한다.
- 외부 입력은 진입점에서 검증하고 내부 모델로 변환한 뒤 service 계층에 전달한다.
- 예외는 호출자가 처리할 수 있는 명확한 타입 또는 메시지로 표현한다.

[외부 API 경계]
- 내부 API 계약과 외부 API 계약을 분리한다.
- 외부 응답 형태는 내부 DTO로 변환한 뒤 호출자에게 반환한다.
- 계약이 확인된 경우 timeout, retry, fallback, failure mapping 규칙을 문서화한다.

[연구 API 안전 경계]
- 연구용 Oracle, static preflight, 실제 모델 추론, 자동 교정과 사람 대상 delivery는 서로 다른 목적의 계약이다. route·response·문서에서 이들을 같은 production capability처럼 표현하지 않는다.
- 진단 응답은 persistence entity, raw audio/annotation/path/raw identifier, 내부 gate 우회 정보, 비밀정보를 포함하지 않는다. evidence와 confidence의 공개 범위는 privacy·license 정책과 API 명세에 맞춘다.
- low-confidence, OOD, content mismatch, unsupported state는 명시적 안전 응답으로 계약한다. 모델 또는 코칭을 임의로 보정·생성·재시도하는 숨은 부수효과를 추가하지 않는다.
- 실제 사람 데이터, 녹음, reference delivery, closed-loop session을 받거나 내보내는 API는 해당 동의·권리·gate가 실제로 승인되기 전에는 구현 준비 계약과 실행 계약을 분리한다.
