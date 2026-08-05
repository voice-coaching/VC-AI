# 도메인 모델 규칙 - intelligentAI

도메인 모델은 명확한 상태와 경계를 기준으로 설계한다.

[Entity 규칙]
- Entity는 저장 상태와 DB 매핑을 표현한다.
- Entity는 Request DTO, Response DTO, Schema, View model과 분리한다.
- Entity를 public API나 UI-facing service 결과로 직접 노출하지 않는다.
- Entity 변경 시 migration, repository, DB 문서를 함께 검토한다.

[DTO 및 Schema 규칙]
- Request DTO는 입력 계약을 표현한다.
- Response DTO는 출력 계약을 표현한다.
- DTO는 API 계약에 맞게 설계할 수 있지만 Entity는 persistence 중심으로 유지한다.

[Enum 및 상태 규칙]
- 상태값은 명시적 enum 또는 상수로 관리한다.
- 비즈니스 로직에서 상태 문자열을 직접 사용하지 않는다.
- 상태 전이는 Service/Application 계층에서 검증한 뒤 저장한다.
- 허용 상태와 상태 전이는 기능 명세 또는 DB 명세에 문서화한다.

[상태 전이 템플릿]
- DRAFT -> READY -> QUEUED -> RUNNING -> COMPLETED
- FAILED와 CANCELLED는 도메인에서 사용할 경우 명시적 종료 상태 또는 복구 상태로 처리한다.

[Python 도메인 메모]
- 느슨한 dictionary 대신 dataclass, typed dict, 명시적 class로 domain model을 표현한다.
- 순수 domain object는 filesystem, environment, network, process args에 의존하지 않는다.
- CLI 입력은 domain/service logic에 전달하기 전에 별도 input model로 변환한다.

[연구 도메인 추가 규칙]
- experiment, gate, artifact, review, coaching 상태는 명시적 enum과 typed model로 표현한다. `completed`, `static_ready`, `failed_closed`, `blocked`, `invalidating` 같은 상태를 boolean 하나로 축약하지 않는다.
- 기대 음소, 실현 음소, 정답 label, 모델 입력 feature, 예측, coaching evidence는 역할이 다른 모델이다. 필드가 비슷해도 한 DTO/entity에 합치거나 학습 입력에 정답·누수 필드를 넣지 않는다.
- `TBD`, 미승인 consent, 미확인 license, 미검증 checksum은 유효한 도메인 값이 아니라 gate를 차단하는 미해결 조건으로 모델링한다.
