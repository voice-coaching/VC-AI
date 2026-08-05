# 서비스 계층 규칙 - intelligentAI

Service/Application 계층은 비즈니스 로직의 중심이다.

[클래스 및 모듈 규칙]
- Service class 또는 module은 하나의 use-case 영역에 집중한다.
- 언어와 프레임워크 관례가 허용하면 한 파일은 하나의 주요 service class를 책임진다.
- Repository, client, adapter, store는 constructor, factory, 좁은 module boundary를 통해 명시적으로 주입한다.

[메서드 규칙]
- `create_simulation`, `update_profile`, `request_job`처럼 use-case 기반 네이밍을 사용한다.
- 하나의 메서드는 하나의 책임만 가진다.
- 의미 있는 로직이 10줄 이상 길어지면 가독성이 좋아지는 경우 private helper로 분리한다.

[필수 흐름]
- 입력과 권한을 검증한다.
- domain/entity 상태를 조회하거나 생성한다.
- 비즈니스 규칙과 상태 전이를 적용한다.
- 필요한 경우 repository/adapter 경계를 통해 저장한다.
- 결과를 response DTO, view model, service result 객체로 변환한다.

[금지]
- Service에서 controller, route handler, UI component 역할 수행
- Repository entity를 public caller에게 직접 반환
- Service method 내부에서 HTTP 응답 생성
- 외부 API 호출을 무관한 비즈니스 로직에 직접 섞기
- 복잡한 분기 로직을 하나의 메서드에 몰아넣기

[Python Service Notes]
- import 시점에 파일 생성, 네트워크 호출, 프로세스 실행 같은 부수효과가 발생하지 않게 한다.
- 실행 가능한 모듈은 `main()` 진입점을 제공하고 직접 실행 코드는 `if __name__ == "__main__"` 아래에 둔다.
- 표준 라이브러리로 충분한 문제에 불필요한 dependency를 추가하지 않는다.
- 공개 함수와 클래스에는 의미 있는 타입 힌트를 유지하고, 입력/출력 모델은 명확한 자료 구조로 표현한다.
- 순수 계산 로직과 I/O 로직을 분리해 테스트와 재사용이 가능하게 한다.

[연구 use-case 추가 규칙]
- 학습·평가 service는 실행 전 immutable config/data/model hash, split 접근 역할, label/QC/privacy allowlist, 선행 gate를 검증하고 하나라도 충족하지 않으면 fail-closed 결과를 반환하거나 명시적 예외를 낸다.
- service result는 `completed`, `failed`, `failed_closed`, `blocked`, `static_ready`처럼 관찰된 상태를 명시적으로 표현한다. static 검증 또는 artifact 생성만으로 실제 실험 완료를 반환하지 않는다.
- 장기 실행의 시작·종료·실패·artifact publish는 adapter와 registry를 통해 기록한다. 부분 산출물의 덮어쓰기, 수동 registry 수정, 성공으로 보이는 빈 결과 반환을 금지한다.
- coaching service는 evidence, confidence, content-match와 allowlist를 확인한 뒤에만 조언을 선택한다. OOD·저신뢰·미지원 입력은 자동 교정 대신 abstain/rerecord/expert-review 정책으로 귀결한다.
